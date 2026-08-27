/**
 * The copilot store — the Phase-6 spec's behavioural guarantees:
 *
 *   - client dry-fold correctness: the after-document shown in the diff is
 *     byte-identical (state hash) to folding the fixture op set with the
 *     model core directly;
 *   - Apply dispatches EXACTLY the returned ops, as ONE group, through the
 *     real model store (same path as a hand edit — asserted via the store's
 *     own undo history and the sequencer call);
 *   - Reject dispatches nothing and leaves no trace;
 *   - the fail-closed 429 renders its specific, calm problem;
 *   - cannotDo / needsClarification never reach dispatch.
 *
 * `./api` (the LLM route) and `lib/api` (the op sequencer transport) are
 * mocked; the fold, the model store, the ui store are all real — the
 * behaviour under test lives in how they combine.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  DEFAULTS,
  FIXTURE_IDS,
  applyGroup,
  fixedId,
  makeTwoRoomPlanWithOpenings,
  stateHash,
  type Op,
  type ProjectDoc,
} from '@garh/model';

import { AppError } from '../../lib/errors';
import { useModelStore } from '../../stores/model';
import { useUiStore } from '../../stores/ui';

import { toModelOps, useCopilotStore } from './useCopilot';
import type { CopilotProposal, CopilotWireOp } from './types';

// ---------------------------------------------------------------------------
// Mocks — the copilot route and the op sequencer transport
// ---------------------------------------------------------------------------

const routeMock = vi.hoisted(() => ({ propose: vi.fn() }));
vi.mock('./api', () => ({ proposeCopilot: routeMock.propose }));

const opsMock = vi.hoisted(() => ({
  model: vi.fn(),
  append: vi.fn(),
  since: vi.fn(),
}));
vi.mock('../../lib/api', () => ({ api: { ops: opsMock } }));

const PROJECT_ID = 'proj_01J0000000000000000000CP';

interface AppendInput {
  readonly ops: readonly Op[];
  readonly baseIdx: number;
}

function appendOk(input: AppendInput): Record<string, unknown> {
  const firstIdx = input.baseIdx + 1;
  const lastIdx = firstIdx + input.ops.length - 1;
  return {
    applied: [],
    firstIdx,
    lastIdx,
    headIdx: lastIdx,
    versionBranch: null,
    alreadyApplied: false,
    stateHash: null,
    snapshotVersionId: null,
    rendersMarkedStale: 0,
  };
}

// ---------------------------------------------------------------------------
// Fixtures — the op set mirrors services/llm/fixtures/copilot-commands.json's
// "widen the kitchen door to 900", retargeted at the shared FIXTURE_IDS plan.
// ---------------------------------------------------------------------------

const WIRE_OPS: readonly CopilotWireOp[] = [
  { type: 'opening.resize', payload: { openingId: FIXTURE_IDS.doorMain, widthMm: 1050 } },
  {
    type: 'wall.move',
    payload: {
      wallId: FIXTURE_IDS.wallSpine,
      a: { x: 3450, y: 0 },
      b: { x: 3450, y: 4000 },
    },
  },
];

function proposal(overrides: Partial<CopilotProposal> = {}): CopilotProposal {
  return {
    applicable: true,
    intent: 'Widen the main door to 1050 mm and shift the spine wall east.',
    ops: WIRE_OPS,
    plainLanguage: ['Widen the main door to 1050 mm.', 'Shift the spine wall 450 mm east.'],
    cannotDo: null,
    needsClarification: null,
    issues: [],
    selfCorrected: false,
    ...overrides,
  };
}

function seedModel(doc: ProjectDoc): void {
  useModelStore.setState({
    projectId: PROJECT_ID,
    versionBranch: null,
    status: 'ready',
    loadError: null,
    doc,
    serverDoc: doc,
    baseIdx: 9,
    headIdx: 9,
    pending: [],
    flushing: false,
    saveState: 'saved',
    lastSavedAt: Date.now(),
    syncError: null,
    divergedAt: null,
    undoStack: [],
    redoStack: [],
  });
}

async function flushAsync(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  vi.restoreAllMocks();
  routeMock.propose.mockReset();
  opsMock.append.mockReset();
  opsMock.append.mockImplementation((input: AppendInput) => Promise.resolve(appendOk(input)));
  useCopilotStore.setState({ turns: [], busy: false, history: [] });
  useUiStore.setState({ toasts: [] });
  seedModel(makeTwoRoomPlanWithOpenings());
});

// ---------------------------------------------------------------------------
// Thinking state
// ---------------------------------------------------------------------------

describe('send', () => {
  it('is "thinking" exactly while the request is live — no longer', async () => {
    let resolve!: (value: CopilotProposal) => void;
    routeMock.propose.mockReturnValue(
      new Promise<CopilotProposal>((r) => {
        resolve = r;
      }),
    );

    const sending = useCopilotStore.getState().send('widen the kitchen door to 900');
    expect(useCopilotStore.getState().busy).toBe(true);
    expect(useCopilotStore.getState().turns[0]?.status).toBe('thinking');

    resolve(proposal());
    await sending;

    expect(useCopilotStore.getState().busy).toBe(false);
    expect(useCopilotStore.getState().turns[0]?.status).toBe('ready');
  });

  it('records the command in the input history', async () => {
    routeMock.propose.mockResolvedValue(proposal());
    await useCopilotStore.getState().send('widen the kitchen door to 900');
    expect(useCopilotStore.getState().history).toEqual(['widen the kitchen door to 900']);
  });
});

// ---------------------------------------------------------------------------
// Dry-fold correctness
// ---------------------------------------------------------------------------

describe('client dry-run fold', () => {
  it('produces the exact document the model core produces for the same ops', async () => {
    routeMock.propose.mockResolvedValue(proposal());
    const before = useModelStore.getState().doc;

    await useCopilotStore.getState().send('widen the kitchen door to 900');

    const turn = useCopilotStore.getState().turns[0];
    expect(turn?.status).toBe('ready');
    expect(turn?.afterDoc).not.toBeNull();

    // The oracle: fold the same fixture ops with the model core directly.
    const expected = applyGroup(before, toModelOps(WIRE_OPS), 'grp_oracle').model;
    expect(stateHash(turn?.afterDoc)).toBe(stateHash(expected));

    // Spot-check the physics: door is 900 wide, spine wall moved to x=3450.
    const door = turn?.afterDoc?.house.openings.find((o) => o.id === FIXTURE_IDS.doorMain);
    expect(door?.widthMm).toBe(1050);
    const spine = turn?.afterDoc?.house.walls.find((w) => w.id === FIXTURE_IDS.wallSpine);
    expect(spine?.a.x).toBe(3450);

    // The preview is a fork: the live document did not move, nothing queued.
    expect(stateHash(useModelStore.getState().doc)).toBe(stateHash(before));
    expect(useModelStore.getState().pending).toEqual([]);
    expect(turn?.beforeDoc).toBe(before);

    // Diff rows carry the server's sentences and the touched elements.
    expect(turn?.diff?.source).toBe('copilot');
    expect(turn?.diff?.ops.map((o) => o.text)).toEqual([
      'Widen the main door to 1050 mm.',
      'Shift the spine wall 450 mm east.',
    ]);
    expect(turn?.diff?.ops[0]?.elementIds).toContain(FIXTURE_IDS.doorMain);
  });

  it('reports honestly when the ops no longer fold on the current doc', async () => {
    routeMock.propose.mockResolvedValue(
      proposal({
        ops: [
          {
            type: 'opening.resize',
            payload: { openingId: fixedId('opening', 'GONE'), widthMm: 900 },
          },
        ],
        plainLanguage: [],
      }),
    );

    await useCopilotStore.getState().send('widen the vanished door');

    const turn = useCopilotStore.getState().turns[0];
    expect(turn?.status).toBe('error');
    expect(turn?.problem?.code).toBe('copilot_stale_fold');
    expect(turn?.problem?.action).toBeTruthy();
    expect(turn?.issues.length).toBeGreaterThan(0);
    // And absolutely nothing was dispatched.
    expect(useModelStore.getState().pending).toEqual([]);
    expect(useModelStore.getState().undoStack).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Apply — one group, exactly the returned ops, the real dispatch path
// ---------------------------------------------------------------------------

describe('apply', () => {
  it('dispatches exactly the returned ops as ONE undo group', async () => {
    routeMock.propose.mockResolvedValue(proposal());
    await useCopilotStore.getState().send('widen the kitchen door to 900');

    const turn = useCopilotStore.getState().turns[0];
    expect(turn?.status).toBe('ready');
    const groupId = turn?.groupId;
    expect(groupId).toBeTruthy();

    useCopilotStore.getState().apply(turn?.id ?? '');
    await flushAsync();

    // ONE history entry, under the pre-allocated group id.
    const history = useModelStore.getState().undoStack;
    expect(history).toHaveLength(1);
    expect(history[0]?.groupId).toBe(groupId);
    expect(history[0]?.label).toBe('Copilot edit');

    // EXACTLY the returned ops: same count, same types, same payloads, and
    // every op stamped with the same single group id.
    const dispatched = history[0]?.ops ?? [];
    expect(dispatched.map((op) => ({ type: op.type, payload: op.payload }))).toEqual(
      WIRE_OPS.map((op) => ({ type: op.type, payload: op.payload })),
    );
    const groupIds = new Set(dispatched.map((op) => (op as { groupId?: string }).groupId));
    expect([...groupIds]).toEqual([groupId]);

    // It went to the sequencer through the normal append, source 'copilot'.
    expect(opsMock.append).toHaveBeenCalledTimes(1);
    const call = opsMock.append.mock.calls[0]?.[0] as {
      groupId?: string;
      source?: string;
      ops: readonly Op[];
    };
    expect(call.groupId).toBe(groupId);
    expect(call.source).toBe('copilot');
    expect(call.ops).toHaveLength(WIRE_OPS.length);

    // The live document now matches the preview the user approved.
    const doc = useModelStore.getState().doc;
    expect(doc.house.openings.find((o) => o.id === FIXTURE_IDS.doorMain)?.widthMm).toBe(1050);

    // Turn settled and dropped its forked documents.
    const applied = useCopilotStore.getState().turns[0];
    expect(applied?.status).toBe('applied');
    expect(applied?.afterDoc).toBeNull();

    // §15: "Copilot edit applied — Undo".
    const toast = useUiStore.getState().toasts.find((t) => t.title === 'Copilot edit applied');
    expect(toast).toBeDefined();
    expect(toast?.action?.label).toBe('Undo');
  });

  it('one undo reverses the whole applied group', async () => {
    routeMock.propose.mockResolvedValue(proposal());
    const before = useModelStore.getState().doc;

    await useCopilotStore.getState().send('widen the kitchen door to 900');
    const turn = useCopilotStore.getState().turns[0];
    useCopilotStore.getState().apply(turn?.id ?? '');
    await flushAsync();

    expect(useModelStore.getState().undo()).toBe(true);
    await flushAsync();

    const doc = useModelStore.getState().doc;
    expect(stateHash(doc)).toBe(stateHash(before));
    expect(doc.house.openings.find((o) => o.id === FIXTURE_IDS.doorMain)?.widthMm).toBe(
      DEFAULTS.doorWidthMm,
    );
  });
});

// ---------------------------------------------------------------------------
// Reject — nothing happened
// ---------------------------------------------------------------------------

describe('reject', () => {
  it('dispatches nothing and leaves no trace', async () => {
    routeMock.propose.mockResolvedValue(proposal());
    const before = useModelStore.getState().doc;

    await useCopilotStore.getState().send('widen the kitchen door to 900');
    const turn = useCopilotStore.getState().turns[0];
    expect(turn?.status).toBe('ready');

    useCopilotStore.getState().reject(turn?.id ?? '');
    await flushAsync();

    const model = useModelStore.getState();
    expect(stateHash(model.doc)).toBe(stateHash(before));
    expect(model.pending).toEqual([]);
    expect(model.undoStack).toEqual([]);
    expect(opsMock.append).not.toHaveBeenCalled();

    const rejected = useCopilotStore.getState().turns[0];
    expect(rejected?.status).toBe('rejected');
    expect(rejected?.ops).toEqual([]);
    expect(rejected?.diff).toBeNull();
    expect(rejected?.afterDoc).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Honest non-answers and failures
// ---------------------------------------------------------------------------

describe('non-answers', () => {
  it('cannotDo renders as a refusal and never reaches dispatch', async () => {
    routeMock.propose.mockResolvedValue(
      proposal({
        applicable: false,
        ops: [],
        plainLanguage: [],
        cannotDo: "I can't do curved walls yet.",
      }),
    );

    await useCopilotStore.getState().send('make the front wall a sweeping curve');

    const turn = useCopilotStore.getState().turns[0];
    expect(turn?.status).toBe('cannot');
    expect(turn?.proposal?.cannotDo).toContain('curved walls');
    expect(useModelStore.getState().undoStack).toEqual([]);
    expect(opsMock.append).not.toHaveBeenCalled();
  });

  it('needsClarification renders the question, ops stay empty', async () => {
    routeMock.propose.mockResolvedValue(
      proposal({
        applicable: false,
        ops: [],
        plainLanguage: [],
        needsClarification:
          'Which bedroom should I enlarge, and should I take the space from the passage or the adjoining room?',
      }),
    );

    await useCopilotStore.getState().send('make the bedroom bigger');

    const turn = useCopilotStore.getState().turns[0];
    expect(turn?.status).toBe('clarify');
    expect(turn?.ops).toEqual([]);
  });

  it('the fail-closed 429 gets the specific, calm message', async () => {
    routeMock.propose.mockRejectedValue(
      new AppError({
        code: 'rate_limited',
        message: 'Too many requests.',
        action: 'Retry after a while.',
        status: 429,
        retryAfterSeconds: 30,
      }),
    );

    await useCopilotStore.getState().send('widen the kitchen door to 900');

    const turn = useCopilotStore.getState().turns[0];
    expect(turn?.status).toBe('error');
    expect(turn?.problem?.code).toBe('rate_limited');
    expect(turn?.problem?.status).toBe(429);
    // Calm and specific: names the limit, promises the design is untouched,
    // gives a concrete wait — no raw server text, no alarm.
    expect(turn?.problem?.message).toContain('usage limit');
    expect(turn?.problem?.message).toContain('untouched');
    expect(turn?.problem?.action).toMatch(/\b30 seconds\b/);
    expect(turn?.problem?.message).not.toContain('Too many requests');
  });

  it('other transport failures keep the problem+json contract', async () => {
    routeMock.propose.mockRejectedValue(
      new AppError({
        code: 'service_unavailable',
        message: 'The copilot is warming up.',
        action: 'Try again in a few seconds.',
        status: 503,
      }),
    );

    await useCopilotStore.getState().send('add a window to the master bedroom');

    const turn = useCopilotStore.getState().turns[0];
    expect(turn?.status).toBe('error');
    expect(turn?.problem?.message).toBe('The copilot is warming up.');
    expect(turn?.problem?.action).toBe('Try again in a few seconds.');
  });
});
