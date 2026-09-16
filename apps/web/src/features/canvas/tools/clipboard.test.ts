/**
 * Copy / paste / duplicate / mirror / array — each ONE undo entry, through
 * the model's own planners, against the real store with a mocked API.
 *
 * The planners are pinned by `packages/model/src/transform.test.ts` and the
 * Python twin; what this spec owns is the wiring the reader found missing —
 * that the gestures reach the planners at all, land where the pointer is,
 * select what they created, and undo as one.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  FIXTURE_IDS,
  fixedId,
  makeTwoRoomPlanWithOpenings,
  stateHash,
  type Op,
  type ProjectDoc,
} from '@garh/model';

import type { OpsAppendResult } from '../../../lib/schemas';
import { useModelStore } from '../../../stores/model';
import { useSelectionStore } from '../../../stores/selection';
import { useUiStore } from '../../../stores/ui';
import {
  DUPLICATE_OFFSET_MM,
  createdIds,
  pasteDeltaMm,
  previewArray,
  runArray,
  runCopy,
  runDuplicate,
  runMirror,
  runPaste,
  selectionAnchorMm,
  useClipboardStore,
} from './clipboard';

// ---------------------------------------------------------------------------
// The mocked API — the store is real
// ---------------------------------------------------------------------------

const mocks = vi.hoisted(() => ({
  model: vi.fn(),
  append: vi.fn(),
  since: vi.fn(),
}));

vi.mock('../../../lib/api', () => ({ api: { ops: mocks } }));

const PROJECT_ID = 'proj_01J0000000000000000000P1';
const BRANCH = 'ver_01J0000000000000000000B1';

interface AppendInput {
  projectId: string;
  ops: readonly Op[];
  baseIdx: number;
  groupId?: string;
}

function appendResult(input: AppendInput): OpsAppendResult {
  const firstIdx = input.baseIdx + 1;
  const lastIdx = firstIdx + input.ops.length - 1;
  return {
    applied: [],
    firstIdx,
    lastIdx,
    headIdx: lastIdx,
    versionBranch: BRANCH,
    alreadyApplied: false,
    stateHash: null,
    snapshotVersionId: null,
    rendersMarkedStale: 0,
  };
}

async function hydrate(doc: ProjectDoc): Promise<void> {
  mocks.model.mockResolvedValue({
    projectId: PROJECT_ID,
    versionBranch: BRANCH,
    designVersionId: null,
    schemaVersion: doc.schemaVersion,
    snapshot: JSON.parse(JSON.stringify(doc)) as unknown,
    snapshotHash: null,
    baseIdx: 4,
    headIdx: 4,
    ops: [],
    stateHash: null,
    truncated: false,
  });
  await useModelStore.getState().hydrate(PROJECT_ID);
}

const GF = FIXTURE_IDS.groundStorey;
const SPINE = FIXTURE_IDS.wallSpine;
const SOUTH = FIXTURE_IDS.wallSouth;

function house() {
  return useModelStore.getState().doc.house;
}

beforeEach(async () => {
  vi.clearAllMocks();
  useModelStore.getState().reset();
  useSelectionStore.getState().clear();
  useUiStore.getState().clearToasts();
  useUiStore.getState().setSnapMode('module');
  useClipboardStore.setState({ entry: null, cursorMm: null });
  mocks.append.mockImplementation((input: AppendInput) => Promise.resolve(appendResult(input)));
  mocks.since.mockResolvedValue({
    ops: [],
    sinceIdx: -1,
    headIdx: 4,
    versionBranch: BRANCH,
    hasMore: false,
  });
  await hydrate(makeTwoRoomPlanWithOpenings());
  useUiStore.getState().setActiveStorey(GF);
});

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

describe('anchors and deltas', () => {
  it('anchors a selection at its bounding-box corner', () => {
    expect(selectionAnchorMm(house(), [SPINE])).toEqual({ x: 3000, y: 0 });
    expect(selectionAnchorMm(house(), [SOUTH, SPINE])).toEqual({ x: 0, y: 0 });
    expect(selectionAnchorMm(house(), [])).toBeNull();
    // A room is derived: it anchors nothing.
    expect(selectionAnchorMm(house(), [fixedId('room', 'R1')])).toBeNull();
  });

  it('snaps the paste delta to the module, not the pointer', () => {
    expect(pasteDeltaMm({ x: 3000, y: 0 }, { x: 4490, y: 60 }, 115)).toEqual({ x: 1495, y: 115 });
    expect(pasteDeltaMm({ x: 3000, y: 0 }, { x: 4490, y: 60 }, 0)).toEqual({ x: 1490, y: 60 });
  });

  it('lists the ids a plan creates, family by family', () => {
    expect(
      createdIds([
        {
          type: 'wall.add',
          payload: {
            id: fixedId('wall', 'A'),
            storeyId: GF,
            a: { x: 0, y: 0 },
            b: { x: 1, y: 0 },
            thicknessMm: 115,
            kind: 'internal',
          },
        },
        {
          type: 'column.set',
          payload: { action: 'add', id: fixedId('column', 'C'), storeyId: GF, pt: { x: 0, y: 0 } },
        },
        { type: 'column.set', payload: { action: 'delete', id: fixedId('column', 'D') } },
        {
          type: 'room.assign',
          payload: {
            roomId: fixedId('room', 'R'),
            type: 'bedroom',
            name: '',
            tags: [],
            locked: false,
          },
        },
      ]),
    ).toEqual([fixedId('wall', 'A'), fixedId('column', 'C')]);
  });
});

// ---------------------------------------------------------------------------
// Copy / paste
// ---------------------------------------------------------------------------

describe('copy and paste', () => {
  it('copies ids and an anchor, dispatching nothing', () => {
    useSelectionStore.getState().selectMany([SPINE]);
    const entry = runCopy();
    expect(entry).toEqual({ elementIds: [SPINE], sourceStoreyId: GF, anchorMm: { x: 3000, y: 0 } });
    expect(useClipboardStore.getState().entry).toEqual(entry);
    expect(useModelStore.getState().undoStack).toHaveLength(0);
  });

  it('refuses to copy nothing, with a toast and no clipboard write', () => {
    expect(runCopy()).toBeNull();
    expect(useClipboardStore.getState().entry).toBeNull();
    expect(useUiStore.getState().toasts[0]?.title).toBe('Nothing to copy.');
  });

  it('pastes at the noted pointer, as ONE undo entry, and selects the copy', () => {
    useSelectionStore.getState().selectMany([SPINE]);
    runCopy();
    // The pointer is over the next bay: the anchor (3000, 0) should land there.
    useClipboardStore.getState().noteCursor({ x: 4495, y: 0 });
    const hashBefore = stateHash(useModelStore.getState().doc);

    const outcome = runPaste();
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;
    expect(outcome.plan.kind).toBe('paste');

    const walls = house().walls;
    expect(walls).toHaveLength(6);
    const pasted = walls.find((w) => w.id !== SPINE && w.a.x === 4495 && w.b.x === 4495);
    expect(pasted).toBeDefined();
    expect(useSelectionStore.getState().ids).toEqual([pasted?.id]);

    const undo = useModelStore.getState().undoStack;
    expect(undo).toHaveLength(1);
    // The undo entry carries the plan's own group id — the pasted ids were
    // derived from it.
    expect(undo[0]?.groupId).toBe(outcome.plan.groupId);
    expect(useModelStore.getState().undo()).toBe(true);
    expect(stateHash(useModelStore.getState().doc)).toBe(hashBefore);
  });

  it('pastes one offset over when no pointer position is known', () => {
    useSelectionStore.getState().selectMany([SPINE]);
    runCopy();
    const outcome = runPaste();
    expect(outcome.ok).toBe(true);
    const pasted = house().walls.find(
      (w) => w.id !== SPINE && w.a.x === 3000 + DUPLICATE_OFFSET_MM.x,
    );
    expect(pasted?.a.y).toBe(0 + DUPLICATE_OFFSET_MM.y);
  });

  it('carries a door with its wall', () => {
    useSelectionStore.getState().selectMany([SOUTH]);
    runCopy();
    const outcome = runPaste({ x: 0, y: -1150 });
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;
    expect(outcome.plan.created.openings).toBe(1);
    expect(house().openings).toHaveLength(3);
  });

  it('refuses a paste with an empty clipboard, and after the copy was deleted', () => {
    expect(runPaste().ok).toBe(false);

    useSelectionStore.getState().selectMany([SPINE]);
    runCopy();
    useModelStore.getState().dispatch([{ type: 'wall.delete', payload: { wallId: SPINE } }]);
    const outcome = runPaste({ x: 4495, y: 0 });
    expect(outcome.ok).toBe(false);
    if (outcome.ok) return;
    expect(outcome.refusal.reason).toBe('unknown-element');
    expect(useUiStore.getState().toasts.at(-1)?.tone).toBe('error');
  });

  it('pastes onto the ACTIVE storey when it differs from the source', () => {
    const FF = FIXTURE_IDS.firstStorey;
    useModelStore
      .getState()
      .dispatch([
        { type: 'storey.add', payload: { id: FF, index: 1, name: 'First Floor', heightMm: 3000 } },
      ]);
    useSelectionStore.getState().selectMany([SPINE]);
    runCopy();
    useUiStore.getState().setActiveStorey(FF);
    const outcome = runPaste({ x: 3000, y: 0 });
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;
    expect(outcome.plan.targetStoreyId).toBe(FF);
    // Zero delta is fine across storeys: the copy sits exactly above.
    const upstairs = house().walls.filter((w) => w.storeyId === FF);
    expect(upstairs).toHaveLength(1);
    expect(upstairs[0]?.a).toEqual({ x: 3000, y: 0 });
  });
});

// ---------------------------------------------------------------------------
// Duplicate / mirror / array
// ---------------------------------------------------------------------------

describe('duplicate', () => {
  it('copies the selection one offset over without touching the clipboard', () => {
    useSelectionStore.getState().selectMany([SPINE]);
    const outcome = runDuplicate();
    expect(outcome.ok).toBe(true);
    expect(useClipboardStore.getState().entry).toBeNull();
    expect(house().walls).toHaveLength(6);
    expect(useModelStore.getState().undoStack).toHaveLength(1);
  });

  it('refuses with nothing selected', () => {
    expect(runDuplicate().ok).toBe(false);
    expect(useModelStore.getState().undoStack).toHaveLength(0);
  });
});

describe('mirror', () => {
  it('mirrors across a picked vertical line as one undo, keeping the original', () => {
    useSelectionStore.getState().selectMany([SPINE]);
    const outcome = runMirror({ axis: 'vertical', atMm: 4000 });
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;
    const mirrored = house().walls.find((w) => w.id !== SPINE && w.a.x === 5000);
    expect(mirrored).toBeDefined();
    expect(house().walls.some((w) => w.id === SPINE)).toBe(true);
    expect(useModelStore.getState().undoStack).toHaveLength(1);
    expect(useSelectionStore.getState().ids).toEqual([mirrored?.id]);
  });

  it('flips in place when the original is not kept', () => {
    useSelectionStore.getState().selectMany([SPINE]);
    const outcome = runMirror({ axis: 'vertical', atMm: 4000, keepOriginal: false });
    expect(outcome.ok).toBe(true);
    expect(house().walls).toHaveLength(5);
    expect(house().walls.find((w) => w.id === SPINE)?.a.x).toBe(5000);
  });

  it('refuses a symmetric selection mirrored onto itself, and says so', () => {
    useSelectionStore.getState().selectMany([SPINE]);
    const outcome = runMirror({ axis: 'horizontal' });
    expect(outcome.ok).toBe(false);
    if (outcome.ok) return;
    expect(outcome.refusal.reason).toBe('zero-offset');
    expect(useModelStore.getState().undoStack).toHaveLength(0);
  });
});

describe('array', () => {
  it('describes the plan before it runs, and runs it as one undo', () => {
    useSelectionStore.getState().selectMany([SPINE]);
    const preview = previewArray({ countX: 3, countY: 1, spacingXMm: -1000, spacingYMm: 0 });
    expect(preview.ok && preview.plan.created.walls).toBe(2);

    const outcome = runArray({ countX: 3, countY: 1, spacingXMm: -1000, spacingYMm: 0 });
    expect(outcome.ok).toBe(true);
    expect(house().walls).toHaveLength(7);
    expect(
      house()
        .walls.map((w) => w.a.x)
        .sort((a, b) => a - b),
    ).toEqual([0, 0, 1000, 2000, 3000, 6000, 6000]);
    expect(useModelStore.getState().undoStack).toHaveLength(1);
  });

  it('refuses a zero spacing, with the model’s own sentence', () => {
    useSelectionStore.getState().selectMany([SPINE]);
    const outcome = runArray({ countX: 3, countY: 1, spacingXMm: 0, spacingYMm: 0 });
    expect(outcome.ok).toBe(false);
    if (outcome.ok) return;
    expect(outcome.refusal.reason).toBe('zero-offset');
    expect(useUiStore.getState().toasts.at(-1)?.title).toBe(outcome.refusal.message);
  });
});
