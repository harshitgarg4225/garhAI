/**
 * Storey removal: the plan says what goes, the action is one undo, and undo
 * puts the state hash back.
 *
 * The store is REAL and the API is mocked, exactly as `copyStorey.test.ts`
 * does it — the property under test ("one gesture is one undo entry") lives
 * in the dispatch, not in a plan object.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  applyGroup,
  DEFAULTS,
  FIXTURE_IDS,
  fixedId,
  emptyProjectDoc,
  stateHash,
  twoRoomPlanOps,
  type Op,
  type ProjectDoc,
} from '@garh/model';

import type { OpsAppendResult } from '../../lib/schemas';
import { useModelStore } from '../../stores/model';
import { useSelectionStore } from '../../stores/selection';
import { useUiStore } from '../../stores/ui';
import { runRemoveStorey } from './actions';
import { planStoreyRemove } from './removeStorey';

// ---------------------------------------------------------------------------
// The mocked API
// ---------------------------------------------------------------------------

const mocks = vi.hoisted(() => ({
  model: vi.fn(),
  append: vi.fn(),
  since: vi.fn(),
}));

vi.mock('../../lib/api', () => ({ api: { ops: mocks } }));

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

// ---------------------------------------------------------------------------
// Fixture — a G+1 with a wall, a door, a column and a stair upstairs
// ---------------------------------------------------------------------------

const GF = FIXTURE_IDS.groundStorey;
const FF = FIXTURE_IDS.firstStorey;
const FF_WALL = fixedId('wall', 'FFW');
const FF_WALL2 = fixedId('wall', 'FFW2');
const FF_DOOR = fixedId('opening', 'FFD');
const FF_COLUMN = fixedId('column', 'FFC');

function makeG1(): ProjectDoc {
  return applyGroup(emptyProjectDoc('ft-in'), [
    ...twoRoomPlanOps(),
    {
      type: 'storey.add',
      payload: { id: FF, index: 1, name: 'First Floor', heightMm: DEFAULTS.storeyHeightMm },
    },
    {
      type: 'wall.add',
      payload: {
        id: FF_WALL,
        storeyId: FF,
        a: { x: 0, y: 0 },
        b: { x: 6000, y: 0 },
        thicknessMm: 230,
        kind: 'external',
      },
    },
    {
      type: 'wall.add',
      payload: {
        id: FF_WALL2,
        storeyId: FF,
        a: { x: 6000, y: 0 },
        b: { x: 6000, y: 4000 },
        thicknessMm: 230,
        kind: 'external',
      },
    },
    {
      type: 'opening.add',
      payload: {
        id: FF_DOOR,
        wallId: FF_WALL,
        kind: 'door',
        widthMm: 900,
        heightMm: 2100,
        sillMm: 0,
        offsetMm: 1500,
        swing: 'in-left',
      },
    },
    {
      type: 'column.set',
      payload: { action: 'add', id: FF_COLUMN, storeyId: FF, pt: { x: 3000, y: 2000 } },
    },
  ]).model;
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

beforeEach(() => {
  vi.clearAllMocks();
  useModelStore.getState().reset();
  useSelectionStore.getState().clear();
  useUiStore.getState().clearToasts();
  useUiStore.getState().setActiveStorey(null);
  mocks.append.mockImplementation((input: AppendInput) => Promise.resolve(appendResult(input)));
  mocks.since.mockResolvedValue({
    ops: [],
    sinceIdx: -1,
    headIdx: 4,
    versionBranch: BRANCH,
    hasMore: false,
  });
});

// ---------------------------------------------------------------------------
// The plan
// ---------------------------------------------------------------------------

describe('planStoreyRemove', () => {
  it('resolves the index, counts the cascade and names the storey below', () => {
    const planned = planStoreyRemove(makeG1(), FF);
    expect(planned.ok).toBe(true);
    if (!planned.ok) return;
    const plan = planned.plan;
    expect(plan.index).toBe(1);
    expect(plan.op).toEqual({ type: 'storey.remove', payload: { index: 1 } });
    expect(plan.removed.walls).toBe(2);
    expect(plan.removed.openings).toBe(1);
    expect(plan.removed.columns).toBe(1);
    expect(plan.empty).toBe(false);
    expect(plan.last).toBe(false);
    expect(plan.nextActiveStoreyId).toBe(GF);
    expect(plan.label).toBe('First Floor removed');
  });

  it('sends the architect UP when the ground floor goes', () => {
    const planned = planStoreyRemove(makeG1(), GF);
    expect(planned.ok && planned.plan.nextActiveStoreyId).toBe(FF);
    expect(planned.ok && planned.plan.index).toBe(0);
  });

  it('allows the last storey, and says the design will be empty', () => {
    const doc = applyGroup(makeG1(), [{ type: 'storey.remove', payload: { index: 1 } }]).model;
    const planned = planStoreyRemove(doc, GF);
    expect(planned.ok).toBe(true);
    if (!planned.ok) return;
    expect(planned.plan.last).toBe(true);
    expect(planned.plan.nextActiveStoreyId).toBeNull();
  });

  it('refuses a storey that is not in the design', () => {
    const planned = planStoreyRemove(makeG1(), fixedId('storey', 'NOPE'));
    expect(planned.ok).toBe(false);
    if (planned.ok) return;
    expect(planned.refusal.reason).toBe('unknown-storey');
  });
});

// ---------------------------------------------------------------------------
// One gesture, one undo
// ---------------------------------------------------------------------------

describe('runRemoveStorey', () => {
  it('removes the storey and everything on it as ONE undo entry', async () => {
    await hydrate(makeG1());
    useUiStore.getState().setActiveStorey(FF);
    const before = useModelStore.getState();
    const hashBefore = stateHash(before.doc);
    expect(before.undoStack).toHaveLength(0);

    const outcome = runRemoveStorey(FF);
    expect(outcome.ok).toBe(true);

    const after = useModelStore.getState();
    expect(after.doc.house.storeys.map((s) => s.id)).toEqual([GF]);
    expect(after.doc.house.walls.some((w) => w.storeyId === FF)).toBe(false);
    expect(after.doc.house.openings.some((o) => o.id === FF_DOOR)).toBe(false);
    expect(after.doc.house.columns.some((c) => c.id === FF_COLUMN)).toBe(false);
    expect(after.undoStack).toHaveLength(1);

    // The active storey followed the plan: down to the ground floor.
    expect(useUiStore.getState().activeStoreyId).toBe(GF);

    // One undo restores the whole storey, hash-exact.
    expect(useModelStore.getState().undo()).toBe(true);
    expect(stateHash(useModelStore.getState().doc)).toBe(hashBefore);
    expect(useModelStore.getState().doc.house.columns.some((c) => c.id === FF_COLUMN)).toBe(true);
  });

  it('leaves the active storey alone when another storey is removed', async () => {
    await hydrate(makeG1());
    useUiStore.getState().setActiveStorey(GF);
    runRemoveStorey(FF);
    expect(useUiStore.getState().activeStoreyId).toBe(GF);
  });

  it('returns the refusal for an unknown storey and dispatches nothing', async () => {
    await hydrate(makeG1());
    const outcome = runRemoveStorey(fixedId('storey', 'NOPE'));
    expect(outcome.ok).toBe(false);
    expect(useModelStore.getState().undoStack).toHaveLength(0);
  });
});
