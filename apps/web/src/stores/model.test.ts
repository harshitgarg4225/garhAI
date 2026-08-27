/**
 * The optimistic-op pipeline (§12) and its §14 budget.
 *
 * These are the tests that matter most in this package, because every one of
 * them covers a failure the user would experience as "the app lost my edit":
 *
 *   - apply locally, queue, confirm       → the happy path, and the <10 ms budget
 *   - 409  → rebase and re-apply          → two tabs, or a solver job landing
 *   - 422  → roll back and say why        → the server refused the op
 *   - offline → keep the queue, say so    → the badge must not claim "Saved"
 *   - undo/redo                           → inverse ops, recomputed on rebase
 *
 * `lib/api` is mocked; everything else — the fold, the stores, the toasts —
 * is the real thing, because the interesting behaviour lives in how they
 * combine.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  DEMO_PLOT_POLYGON,
  FIXTURE_IDS,
  emptyProjectDoc,
  stateHash,
  twoRoomPlanOps,
  type Op,
} from '@garh/model';

import { OpConflictError, OpRejectionError, networkError } from '../lib/errors';
import type { OpsAppendResult, OpsSince, PersistedOp } from '../lib/schemas';
import { selectSaveBadge, useModelStore } from './model';
import { useSelectionStore } from './selection';
import { useUiStore } from './ui';

// ---------------------------------------------------------------------------
// The mocked API
// ---------------------------------------------------------------------------

const mocks = vi.hoisted(() => ({
  model: vi.fn(),
  append: vi.fn(),
  since: vi.fn(),
}));

vi.mock('../lib/api', () => ({ api: { ops: mocks } }));

const PROJECT_ID = 'proj_01J0000000000000000000P1';
const BRANCH = 'ver_01J0000000000000000000B1';

function emptyModelState(): Record<string, unknown> {
  return {
    projectId: PROJECT_ID,
    versionBranch: BRANCH,
    designVersionId: null,
    schemaVersion: emptyProjectDoc().schemaVersion,
    snapshot: null,
    snapshotHash: null,
    baseIdx: -1,
    headIdx: -1,
    ops: [],
    stateHash: null,
    truncated: false,
  };
}

interface AppendInput {
  projectId: string;
  ops: readonly Op[];
  baseIdx: number;
  groupId?: string;
}

/** The server's answer to a clean append: indices advance, nothing else moves. */
function appendResult(input: AppendInput, stateHashValue: string | null = null): OpsAppendResult {
  const firstIdx = input.baseIdx + 1;
  const lastIdx = firstIdx + input.ops.length - 1;
  return {
    applied: [],
    firstIdx,
    lastIdx,
    headIdx: lastIdx,
    versionBranch: BRANCH,
    alreadyApplied: false,
    stateHash: stateHashValue,
    snapshotVersionId: null,
    rendersMarkedStale: 0,
  };
}

/** A row as `GET /projects/:id/ops` returns it. */
function persisted(idx: number, op: Op): PersistedOp {
  return {
    seq: idx + 1,
    idx,
    type: op.type,
    payload: op.payload as unknown as Record<string, unknown>,
    inverse: null,
    source: 'manual',
    actor: null,
    clientOpId: null,
    groupId: null,
    createdAt: '2026-08-01T10:00:00Z',
  };
}

function sincePage(ops: readonly PersistedOp[], headIdx: number): OpsSince {
  return { ops: [...ops], sinceIdx: -1, headIdx, versionBranch: BRANCH, hasMore: false };
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const groundStorey: Op = {
  type: 'storey.add',
  payload: {
    id: FIXTURE_IDS.groundStorey,
    index: 0,
    name: 'Ground Floor',
    heightMm: 3000,
  },
};

function wallAdd(id: string, x: number): Op {
  return {
    type: 'wall.add',
    payload: {
      id,
      storeyId: FIXTURE_IDS.groundStorey,
      a: { x, y: 0 },
      b: { x, y: 4000 },
      thicknessMm: 230,
      kind: 'external',
    },
  } as Op;
}

/** A remote edit that cannot collide with anything the client is doing. */
const remoteBriefUpdate: Op = {
  type: 'brief.update',
  payload: { patch: { budgetInr: 5_000_000 } },
};

async function hydrate(): Promise<void> {
  mocks.model.mockResolvedValue(emptyModelState());
  await useModelStore.getState().hydrate(PROJECT_ID);
}

beforeEach(() => {
  vi.clearAllMocks();
  useModelStore.getState().reset();
  useSelectionStore.getState().clear();
  useUiStore.getState().clearToasts();
  mocks.append.mockImplementation((input: AppendInput) => Promise.resolve(appendResult(input)));
  mocks.since.mockResolvedValue(sincePage([], -1));
});

// ---------------------------------------------------------------------------

describe('hydrate', () => {
  it('loads an empty project and reports it as saved at v0', async () => {
    await hydrate();
    const s = useModelStore.getState();
    expect(s.status).toBe('ready');
    expect(s.baseIdx).toBe(-1);
    expect(s.doc.house.walls).toHaveLength(0);
    expect(s.pending).toHaveLength(0);
    // §15: the badge counts ops, so an untouched project is honestly v0.
    expect(selectSaveBadge(s)).toEqual({ state: 'saved', label: 'Saved · v0', detail: null });
  });

  it('replays the inlined tail onto the snapshot', async () => {
    mocks.model.mockResolvedValue({
      ...emptyModelState(),
      ops: [persisted(0, groundStorey), persisted(1, wallAdd(FIXTURE_IDS.wallSouth, 0))],
      baseIdx: -1,
      headIdx: 1,
    });
    await useModelStore.getState().hydrate(PROJECT_ID);

    const s = useModelStore.getState();
    expect(s.status).toBe('ready');
    expect(s.baseIdx).toBe(1);
    expect(s.doc.house.storeys).toHaveLength(1);
    expect(s.doc.house.walls).toHaveLength(1);
  });

  it('refuses to trust a document whose hash disagrees with the server', async () => {
    mocks.model.mockResolvedValue({ ...emptyModelState(), stateHash: 'deadbeef'.repeat(8) });
    await useModelStore.getState().hydrate(PROJECT_ID);

    const s = useModelStore.getState();
    expect(s.divergedAt).not.toBeNull();
    // And it stops accepting edits rather than compounding the divergence.
    const result = s.dispatch([groundStorey]);
    expect(result.ok).toBe(false);
  });
});

describe('dispatch', () => {
  it('applies locally, queues for the server, then confirms', async () => {
    await hydrate();
    const result = useModelStore.getState().dispatch([groundStorey], { label: 'Add storey' });

    expect(result.ok).toBe(true);
    // Local first: the canvas already has the storey.
    expect(useModelStore.getState().doc.house.storeys).toHaveLength(1);
    expect(useModelStore.getState().pending).toHaveLength(1);
    // …and the server has not confirmed it yet.
    expect(useModelStore.getState().serverDoc.house.storeys).toHaveLength(0);

    await useModelStore.getState().flush();

    const s = useModelStore.getState();
    expect(s.pending).toHaveLength(0);
    expect(s.serverDoc.house.storeys).toHaveLength(1);
    expect(s.baseIdx).toBe(0);
    expect(s.saveState).toBe('saved');
    expect(s.doc).toStrictEqual(s.serverDoc);
  });

  it('stamps every op with a group id and a client op id', async () => {
    await hydrate();
    useModelStore.getState().dispatch([groundStorey, wallAdd(FIXTURE_IDS.wallSouth, 0)]);
    await useModelStore.getState().flush();

    const input = mocks.append.mock.calls[0]?.[0] as AppendInput;
    expect(input.baseIdx).toBe(-1);
    expect(input.ops).toHaveLength(2);
    for (const op of input.ops) {
      const meta = op as { clientOpId?: string; groupId?: string };
      expect(meta.clientOpId).toMatch(/^op_[0-9A-HJKMNP-TV-Z]{26}$/);
      expect(meta.groupId).toBe(input.groupId);
    }
  });

  it('rejects an invalid op without touching the document', async () => {
    await hydrate();
    // A wall on a storey that does not exist.
    const result = useModelStore.getState().dispatch([wallAdd(FIXTURE_IDS.wallSouth, 0)]);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.issues.length).toBeGreaterThan(0);
    expect(useModelStore.getState().doc.house.walls).toHaveLength(0);
    expect(useModelStore.getState().pending).toHaveLength(0);
    expect(mocks.append).not.toHaveBeenCalled();
  });

  it('dryRun folds without applying (the copilot/solver diff path)', async () => {
    await hydrate();
    const before = useModelStore.getState().doc;
    const preview = useModelStore.getState().dryRun([groundStorey]);

    expect(preview.ok).toBe(true);
    if (preview.ok) expect(preview.doc.house.storeys).toHaveLength(1);
    expect(useModelStore.getState().doc).toBe(before);
    expect(useModelStore.getState().pending).toHaveLength(0);
  });
});

describe('§14 budget: optimistic op apply', () => {
  it('folds a wall move in under 10 ms on the two-room plan', async () => {
    await hydrate();
    const built = useModelStore.getState().dispatch(twoRoomPlanOps(), { label: 'Draw plan' });
    expect(built.ok).toBe(true);
    await useModelStore.getState().flush();

    const samples: number[] = [];
    for (let i = 0; i < 25; i++) {
      const x = 3000 + (i % 2 === 0 ? 115 : 0);
      const op: Op = {
        type: 'wall.move',
        payload: { wallId: FIXTURE_IDS.wallSpine, a: { x, y: 0 }, b: { x, y: 4000 } },
      } as Op;

      const started = performance.now();
      const result = useModelStore.getState().dispatch([op], { label: 'Move wall' });
      samples.push(performance.now() - started);

      // A rejected op would make this a measurement of nothing.
      expect(result.ok).toBe(true);
    }

    samples.sort((a, b) => a - b);
    const median = samples[Math.floor(samples.length / 2)] ?? Number.POSITIVE_INFINITY;
    expect(median).toBeLessThan(10);

    await useModelStore.getState().flush();
  });
});

describe('409 — rebase', () => {
  it('replays remote ops, re-applies the queue, and retries the append', async () => {
    await hydrate();

    let attempt = 0;
    mocks.append.mockImplementation((input: AppendInput) => {
      attempt += 1;
      if (attempt === 1) {
        return Promise.reject(
          new OpConflictError({
            code: 'ignored',
            message: 'This design moved on while you were editing.',
            action: 'Rebasing…',
            headIdx: 0,
            baseIdx: input.baseIdx,
          }),
        );
      }
      return Promise.resolve(appendResult(input));
    });
    mocks.since.mockResolvedValue(sincePage([persisted(0, remoteBriefUpdate)], 0));

    useModelStore.getState().dispatch([groundStorey], { label: 'Add storey' });
    await useModelStore.getState().flush();

    const s = useModelStore.getState();
    expect(mocks.since).toHaveBeenCalledTimes(1);
    // The remote edit survived…
    expect(s.doc.brief.data.budgetInr).toBe(5_000_000);
    expect(s.serverDoc.brief.data.budgetInr).toBe(5_000_000);
    // …and so did ours, re-applied on top of it.
    expect(s.doc.house.storeys).toHaveLength(1);
    expect(s.pending).toHaveLength(0);
    expect(s.baseIdx).toBe(1);
    expect(s.saveState).toBe('saved');
    // The retry was based on the rebased index, not the stale one.
    const retried = mocks.append.mock.calls[1]?.[0] as AppendInput;
    expect(retried.baseIdx).toBe(0);
  });

  it('drops a queued edit the remote change invalidated, and says so', async () => {
    await hydrate();
    useModelStore.getState().dispatch([groundStorey]);
    await useModelStore.getState().flush();
    useUiStore.getState().clearToasts();

    // Local: a wall on the ground storey. Remote: that storey is gone — so the
    // queued wall cannot be replayed onto the rebased document.
    let attempt = 0;
    mocks.append.mockImplementation((input: AppendInput) => {
      attempt += 1;
      if (attempt === 1) {
        return Promise.reject(
          new OpConflictError({
            code: 'ignored',
            message: 'This design moved on.',
            action: 'Rebasing…',
            headIdx: 1,
          }),
        );
      }
      return Promise.resolve(appendResult(input));
    });
    // `storey.remove` is indexed, not id'd (playbook §4) — index 0 is the ground floor.
    const remoteRemove: Op = { type: 'storey.remove', payload: { index: 0 } };
    mocks.since.mockResolvedValue(sincePage([persisted(1, remoteRemove)], 1));

    useModelStore.getState().dispatch([wallAdd(FIXTURE_IDS.wallSouth, 0)], { label: 'Add wall' });
    await useModelStore.getState().flush();

    const s = useModelStore.getState();
    expect(s.doc.house.storeys).toHaveLength(0);
    expect(s.doc.house.walls).toHaveLength(0);
    expect(s.pending).toHaveLength(0);
    // The dropped edit is gone from history too, so undo cannot resurrect it.
    expect(s.undoStack.map((e) => e.label)).not.toContain('Add wall');
    // Never silent: §15 and golden rule 9.
    expect(useUiStore.getState().toasts.some((t) => t.tone === 'warning')).toBe(true);
    // Only one append was attempted: after the rebase there was nothing to send.
    expect(attempt).toBe(1);
  });
});

describe('422 — rollback', () => {
  it('restores the document and explains why', async () => {
    await hydrate();
    useModelStore.getState().dispatch([groundStorey]);
    await useModelStore.getState().flush();
    useUiStore.getState().clearToasts();

    mocks.append.mockRejectedValue(
      new OpRejectionError({
        code: 'ignored',
        message: 'That change is not valid.',
        action: 'Adjust it and try again.',
        issues: [
          {
            code: 'ROOM_TOO_SMALL',
            message: 'Bedroom 2 is 8.9 m² — NBC needs 9.5 m².',
            severity: 'error',
            elementIds: [],
            fix: 'Widen the room by 300mm.',
          },
        ],
      }),
    );

    useModelStore.getState().dispatch([wallAdd(FIXTURE_IDS.wallSouth, 0)], { label: 'Add wall' });
    expect(useModelStore.getState().doc.house.walls).toHaveLength(1);

    await useModelStore.getState().flush();

    const s = useModelStore.getState();
    expect(s.doc.house.walls).toHaveLength(0);
    expect(s.doc).toStrictEqual(s.serverDoc);
    expect(s.pending).toHaveLength(0);
    expect(s.undoStack.map((e) => e.label)).not.toContain('Add wall');

    const toast = useUiStore.getState().toasts.at(-1);
    expect(toast?.tone).toBe('error');
    expect(toast?.title).toBe('Bedroom 2 is 8.9 m² — NBC needs 9.5 m².');
    expect(toast?.description).toBe('Widen the room by 300mm.');
  });
});

describe('offline', () => {
  it('keeps the queue and stops claiming the work is saved', async () => {
    await hydrate();
    mocks.append.mockRejectedValue(networkError('POST /projects/p/ops', new TypeError('offline')));

    useModelStore.getState().dispatch([groundStorey], { label: 'Add storey' });
    await useModelStore.getState().flush();

    const s = useModelStore.getState();
    expect(s.pending).toHaveLength(1);
    expect(s.saveState).toBe('offline');
    // The edit is still on screen — losing it would be the worse failure.
    expect(s.doc.house.storeys).toHaveLength(1);

    // Cancels the scheduled retry so the test does not leave a timer running.
    useModelStore.getState().reset();
  });
});

describe('undo / redo', () => {
  it('round-trips a group through its inverse', async () => {
    await hydrate();
    useModelStore.getState().dispatch([groundStorey], { label: 'Add storey' });
    await useModelStore.getState().flush();
    useModelStore.getState().dispatch([wallAdd(FIXTURE_IDS.wallSouth, 0)], { label: 'Add wall' });
    await useModelStore.getState().flush();

    const withWall = stateHash(useModelStore.getState().doc);

    expect(useModelStore.getState().undo()).toBe(true);
    await useModelStore.getState().flush();
    expect(useModelStore.getState().doc.house.walls).toHaveLength(0);

    expect(useModelStore.getState().redo()).toBe(true);
    await useModelStore.getState().flush();
    expect(useModelStore.getState().doc.house.walls).toHaveLength(1);
    // Identical state, not merely a similar one.
    expect(stateHash(useModelStore.getState().doc)).toBe(withWall);
  });

  it('appends the inverse as new ops rather than rewriting history', async () => {
    await hydrate();
    useModelStore.getState().dispatch([groundStorey], { label: 'Add storey' });
    await useModelStore.getState().flush();
    useModelStore.getState().undo();
    await useModelStore.getState().flush();

    // Two appends: the original and the undo. The op log is append-only (§4).
    expect(mocks.append).toHaveBeenCalledTimes(2);
    const undoInput = mocks.append.mock.calls[1]?.[0] as AppendInput;
    expect(undoInput.ops[0]?.type).toBe('storey.remove');
    expect(useModelStore.getState().undoStack).toHaveLength(0);
    expect(useModelStore.getState().redoStack).toHaveLength(1);
  });

  it('reports honestly when an undo no longer applies', async () => {
    await hydrate();
    useModelStore.getState().dispatch([groundStorey, wallAdd(FIXTURE_IDS.wallSouth, 0)]);
    await useModelStore.getState().flush();

    const moved = useModelStore.getState().dispatch(
      [
        {
          type: 'wall.move',
          payload: { wallId: FIXTURE_IDS.wallSouth, a: { x: 230, y: 0 }, b: { x: 230, y: 4000 } },
        } as Op,
      ],
      { label: 'Move wall' },
    );
    expect(moved.ok).toBe(true);
    await useModelStore.getState().flush();

    // Someone else deleted that wall. The inverse of our move now refers to
    // an element that is gone.
    mocks.since.mockResolvedValue(
      sincePage(
        [persisted(3, { type: 'wall.delete', payload: { wallId: FIXTURE_IDS.wallSouth } } as Op)],
        3,
      ),
    );
    await useModelStore.getState().pull();
    useUiStore.getState().clearToasts();

    expect(useModelStore.getState().doc.house.walls).toHaveLength(0);
    expect(useModelStore.getState().undo()).toBe(false);
    expect(useUiStore.getState().toasts.at(-1)?.tone).toBe('warning');
  });

  it('forgets the redo stack once a new edit forks history', async () => {
    await hydrate();
    useModelStore.getState().dispatch([groundStorey], { label: 'Add storey' });
    await useModelStore.getState().flush();
    useModelStore.getState().undo();
    await useModelStore.getState().flush();
    expect(useModelStore.getState().redoStack).toHaveLength(1);

    useModelStore.getState().dispatch([groundStorey], { label: 'Add storey again' });
    expect(useModelStore.getState().redoStack).toHaveLength(0);
    await useModelStore.getState().flush();
  });
});

describe('selection hygiene', () => {
  it('drops a selected element that an edit deleted', async () => {
    await hydrate();
    useModelStore.getState().dispatch([groundStorey, wallAdd(FIXTURE_IDS.wallSouth, 0)]);
    await useModelStore.getState().flush();

    useSelectionStore.getState().select(FIXTURE_IDS.wallSouth);
    expect(useSelectionStore.getState().ids).toEqual([FIXTURE_IDS.wallSouth]);

    useModelStore
      .getState()
      .dispatch([{ type: 'wall.delete', payload: { wallId: FIXTURE_IDS.wallSouth } } as Op]);
    expect(useSelectionStore.getState().ids).toEqual([]);
    await useModelStore.getState().flush();
  });
});

describe('the plot lives in the same document', () => {
  it('folds plot ops through the same pipeline', async () => {
    await hydrate();
    const result = useModelStore.getState().dispatch([
      {
        type: 'plot.set_boundary',
        payload: { polygon: DEMO_PLOT_POLYGON, source: 'manual' },
      } as Op,
      { type: 'plot.set_north', payload: { deg: 0 } } as Op,
    ]);
    expect(result.ok).toBe(true);
    expect(useModelStore.getState().doc.plot.boundary).toHaveLength(4);
    await useModelStore.getState().flush();
  });
});
