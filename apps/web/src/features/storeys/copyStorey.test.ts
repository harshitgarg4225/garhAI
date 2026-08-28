/**
 * Storey copy: the ops, their order, and the one property that is easy to lose
 * and expensive to lose — ONE GESTURE IS ONE UNDO.
 *
 * Everything here folds through the REAL model core (`applyGroup`, `stateHash`
 * from `@garh/model`) and, for the undo tests, through the REAL model store
 * with only `lib/api` mocked. A storey copy that folded in a stand-in would
 * prove nothing about the fold that ships, and it is the fold that decides
 * whether an op group is valid.
 *
 * The tests are grouped by what they would catch:
 *
 *   op taxonomy    an invented op type — which would fold here and be rejected
 *                  by the Python twin, breaking the byte-identical state hash.
 *   op order       openings hosted on the walls of the floor below; deletes
 *                  landing after the adds they were supposed to make room for.
 *   fidelity       a "copy" that quietly loses a stair, a room name, an opening.
 *   refusals       a copy that folds into a broken document instead of saying no.
 *   undo grouping  fifty ops that need fifty presses of ⌘Z to take back.
 *
 * NEGATIVE CONTROL (executed, not asserted here): change the single
 * `model.dispatch(plan.ops, …)` in `actions.ts` into a loop that dispatches
 * each op on its own and the four tests under "one gesture, one undo" go red —
 * the undo stack grows by 55 instead of 1 and one `undo()` leaves the document
 * mid-copy. Restoring the single dispatch makes them green again.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  applyGroup,
  DEFAULTS,
  FIXTURE_IDS,
  OP_CATALOG,
  fixedId,
  emptyProjectDoc,
  stateHash,
  twoRoomPlanOps,
  type Op,
  type OpType,
  type ProjectDoc,
} from '@garh/model';

import type { OpsAppendResult } from '../../lib/schemas';
import { useModelStore } from '../../stores/model';
import { useSelectionStore } from '../../stores/selection';
import { useUiStore } from '../../stores/ui';
import { runAddStorey, runStoreyCopy } from './actions';
import {
  describeCounts,
  isStoreyEmpty,
  planStoreyCopy,
  storeyContentCounts,
  type StoreyCopyPlan,
} from './copyStorey';

// ---------------------------------------------------------------------------
// The mocked API (same shape as stores/model.test.ts — the store is real)
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
// Fixtures — a G+1 with something on every element family
// ---------------------------------------------------------------------------

const GF = FIXTURE_IDS.groundStorey;
const FF = FIXTURE_IDS.firstStorey;

/** The first floor's own content, so the destructive case has something to lose. */
const FF_OWN_WALL = fixedId('wall', 'FFW');
const FF_OWN_STAIR = fixedId('stair', 'FFS');

/** 20 risers × 150 mm = 3000 mm, which is exactly the storey height. */
const STAIR_RISERS = 20;
const STAIR_RISER_MM = 150;

function firstStoreyOp(heightMm: number = DEFAULTS.storeyHeightMm): Op {
  return {
    type: 'storey.add',
    payload: { id: FF, index: 1, name: 'First Floor', heightMm },
  };
}

/** The two-room plan plus a door, a window, a stair, a column, a sofa and a
 *  balcony — one of every family the copy has to carry. */
function groundFloorOps(): Op[] {
  return [
    ...twoRoomPlanOps(),
    {
      type: 'opening.add',
      payload: {
        id: FIXTURE_IDS.doorMain,
        wallId: FIXTURE_IDS.wallSouth,
        kind: 'door',
        widthMm: DEFAULTS.doorWidthMm,
        heightMm: DEFAULTS.doorHeightMm,
        sillMm: 0,
        offsetMm: 1500,
        swing: 'in-left',
      },
    },
    {
      type: 'opening.add',
      payload: {
        id: FIXTURE_IDS.windowWest,
        wallId: FIXTURE_IDS.wallWest,
        kind: 'window',
        widthMm: DEFAULTS.windowWidthMm,
        heightMm: DEFAULTS.windowHeightMm,
        sillMm: DEFAULTS.sillDefaultMm,
        offsetMm: 2000,
        swing: 'in-left',
      },
    },
    {
      type: 'stair.add',
      payload: {
        id: FIXTURE_IDS.stair,
        storeyId: GF,
        kind: 'straight',
        origin: { x: 4000, y: 500 },
        direction: 'N',
        riserMm: STAIR_RISER_MM,
        treadMm: DEFAULTS.treadMm,
        widthMm: DEFAULTS.stairWidthMm,
        risersCount: STAIR_RISERS,
        landing: null,
      },
    },
    {
      type: 'column.set',
      payload: {
        action: 'add',
        id: FIXTURE_IDS.column,
        storeyId: GF,
        pt: { x: 3000, y: 2000 },
        sizeMm: DEFAULTS.columnSizeMm,
      },
    },
    {
      type: 'furniture.set',
      payload: {
        action: 'place',
        id: FIXTURE_IDS.sofa,
        storeyId: GF,
        catalogId: 'sofa-3seat',
        pt: { x: 1500, y: 2000 },
        rotationDeg: 90,
      },
    },
    {
      type: 'balcony.set',
      payload: {
        action: 'add',
        id: FIXTURE_IDS.balcony,
        storeyId: GF,
        polygon: [
          { x: 0, y: 4000 },
          { x: 2000, y: 4000 },
          { x: 2000, y: 5000 },
          { x: 0, y: 5000 },
        ],
        railingKind: 'ms',
        railingHeightMm: DEFAULTS.railingHeightMm,
        projectionMm: DEFAULTS.balconyProjectionMm,
        slabThicknessMm: DEFAULTS.slabThicknessMm,
      },
    },
  ];
}

/** The ground floor, with the west room named — so the copy has a name to carry. */
function makeGroundFloor(): ProjectDoc {
  const doc = applyGroup(emptyProjectDoc(), groundFloorOps()).model;
  const room = doc.house.rooms.find((r) => r.storeyId === GF);
  if (room === undefined) throw new Error('fixture has no rooms — the plan did not close');
  return applyGroup(doc, [
    {
      type: 'room.assign',
      payload: {
        roomId: room.id,
        type: 'bedroom_master',
        name: 'Master Bedroom',
        tags: ['vastu-sw'],
        locked: true,
      },
    },
    {
      type: 'room.set_target',
      payload: { roomId: room.id, targetAreaMm2: 12_000_000, mustFace: 'SW' },
    },
  ]).model;
}

/** G+1 where the first floor exists and is empty. */
function makeG1(firstHeightMm: number = DEFAULTS.storeyHeightMm): ProjectDoc {
  return applyGroup(makeGroundFloor(), [firstStoreyOp(firstHeightMm)]).model;
}

/** G+1 where the first floor already has a wall of its own — the destructive case. */
function makeG1WithContent(): ProjectDoc {
  return applyGroup(makeG1(), [
    {
      type: 'wall.add',
      payload: {
        id: FF_OWN_WALL,
        storeyId: FF,
        a: { x: 0, y: 8000 },
        b: { x: 6000, y: 8000 },
        thicknessMm: 230,
        kind: 'external',
      },
    },
    {
      type: 'stair.add',
      payload: {
        id: FF_OWN_STAIR,
        storeyId: FF,
        kind: 'straight',
        origin: { x: 100, y: 9000 },
        direction: 'N',
        riserMm: STAIR_RISER_MM,
        treadMm: DEFAULTS.treadMm,
        widthMm: DEFAULTS.stairWidthMm,
        risersCount: STAIR_RISERS,
        landing: null,
      },
    },
  ]).model;
}

/** The plan, or a failure that names the refusal rather than "undefined". */
function planOrThrow(doc: ProjectDoc, input: Parameters<typeof planStoreyCopy>[1]): StoreyCopyPlan {
  const result = planStoreyCopy(doc, input);
  if (!result.ok)
    throw new Error(`plan refused: ${result.refusal.reason} — ${result.refusal.message}`);
  return result.plan;
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
// The op taxonomy
// ---------------------------------------------------------------------------

describe('the ops a copy emits', () => {
  const KNOWN: ReadonlySet<string> = new Set(OP_CATALOG.map((spec) => spec.type));

  it('uses only op types that already exist in the §4 taxonomy', () => {
    const plan = planOrThrow(makeG1WithContent(), {
      sourceStoreyId: GF,
      target: { kind: 'existing', storeyId: FF },
    });
    const types: ReadonlySet<OpType> = new Set(plan.ops.map((op) => op.type));
    expect(types.size).toBeGreaterThan(4);
    for (const type of types) expect(KNOWN.has(type)).toBe(true);
  });

  it('emits exactly the families this feature documents, and nothing else', () => {
    const plan = planOrThrow(makeG1WithContent(), {
      sourceStoreyId: GF,
      target: { kind: 'existing', storeyId: FF },
      matchHeight: true,
    });
    const types = [...new Set(plan.ops.map((op) => op.type))].sort();
    expect(types).toEqual([
      'balcony.set',
      'column.set',
      'furniture.set',
      'opening.add',
      'room.assign',
      'room.set_target',
      'stair.add',
      'stair.delete',
      'wall.add',
      'wall.delete',
    ]);
  });

  it('adds the storey first when copying onto a new one', () => {
    const plan = planOrThrow(makeGroundFloor(), {
      sourceStoreyId: GF,
      target: { kind: 'new' },
    });
    expect(plan.ops[0]?.type).toBe('storey.add');
    // The fold names it, not us — one source of truth with the Python twin.
    expect(plan.targetName).toBe('First Floor');
  });
});

describe('op order', () => {
  it('clears the target before it copies, and deletes walls last', () => {
    const plan = planOrThrow(makeG1WithContent(), {
      sourceStoreyId: GF,
      target: { kind: 'existing', storeyId: FF },
    });
    const types = plan.ops.map((op) => op.type);
    const lastDelete = Math.max(
      types.lastIndexOf('wall.delete'),
      types.lastIndexOf('stair.delete'),
    );
    const firstAdd = types.indexOf('wall.add');

    expect(lastDelete).toBeGreaterThanOrEqual(0);
    expect(firstAdd).toBeGreaterThan(lastDelete);
    // Walls last among the deletes: the group's inverse is reversed, so this is
    // what brings the walls back BEFORE the openings that need them.
    expect(types.lastIndexOf('wall.delete')).toBeGreaterThan(types.lastIndexOf('stair.delete'));
  });

  it('hosts every copied opening on a copied wall, never on the floor below', () => {
    const plan = planOrThrow(makeG1(), {
      sourceStoreyId: GF,
      target: { kind: 'existing', storeyId: FF },
    });
    const newWallIds = new Set(
      plan.ops
        .filter((op) => op.type === 'wall.add')
        .map((op) => (op.payload as { id: string }).id),
    );
    const openings = plan.ops.filter((op) => op.type === 'opening.add');

    expect(openings).toHaveLength(2);
    for (const op of openings) {
      const wallId = (op.payload as { wallId: string }).wallId;
      expect(newWallIds.has(wallId)).toBe(true);
      // …and specifically NOT the ground floor's own walls.
      expect(wallId).not.toBe(FIXTURE_IDS.wallSouth);
      expect(wallId).not.toBe(FIXTURE_IDS.wallWest);
    }
  });

  it('mints a fresh id for every created element', () => {
    const doc = makeG1();
    const existing = new Set<string>([
      ...doc.house.walls.map((w) => w.id),
      ...doc.house.openings.map((o) => o.id),
      ...doc.house.stairs.map((s) => s.id),
      ...doc.house.columns.map((c) => c.id),
      ...doc.house.furniture.map((f) => f.id),
      ...doc.house.balconies.map((b) => b.id),
    ]);
    const plan = planOrThrow(doc, {
      sourceStoreyId: GF,
      target: { kind: 'existing', storeyId: FF },
    });

    const created = plan.ops
      .filter((op) =>
        [
          'wall.add',
          'opening.add',
          'stair.add',
          'column.set',
          'furniture.set',
          'balcony.set',
        ].includes(op.type),
      )
      .map((op) => (op.payload as { id: string }).id);

    expect(created.length).toBeGreaterThan(5);
    expect(new Set(created).size).toBe(created.length);
    for (const id of created) expect(existing.has(id)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Fidelity — a copy that loses something is not a copy
// ---------------------------------------------------------------------------

describe('what the copy reproduces', () => {
  it('gives the target the same walls, openings, stairs, columns, furniture and balconies', () => {
    const doc = makeG1();
    const plan = planOrThrow(doc, {
      sourceStoreyId: GF,
      target: { kind: 'existing', storeyId: FF },
    });
    const after = applyGroup(doc, plan.ops).model;

    const source = storeyContentCounts(after.house, GF);
    const target = storeyContentCounts(after.house, FF);
    expect(target).toEqual(source);
    expect(plan.copied).toEqual(source);
  });

  it('reproduces the geometry, not just the counts', () => {
    const doc = makeG1();
    const after = applyGroup(
      doc,
      planOrThrow(doc, { sourceStoreyId: GF, target: { kind: 'existing', storeyId: FF } }).ops,
    ).model;

    const key = (storeyId: string): string =>
      after.house.walls
        .filter((w) => w.storeyId === storeyId)
        .map(
          (w) =>
            `${String(w.a.x)},${String(w.a.y)}→${String(w.b.x)},${String(w.b.y)}@${String(w.thicknessMm)}`,
        )
        .sort()
        .join(' | ');
    expect(key(FF)).toBe(key(GF));

    // Rooms are DERIVED, so this is the real proof the walls landed correctly:
    // the detector found the same rooms, with the same clear areas, upstairs.
    const areas = (storeyId: string): number[] =>
      after.house.rooms
        .filter((r) => r.storeyId === storeyId)
        .map((r) => r.areaMm2)
        .sort((a, b) => a - b);
    expect(areas(FF)).toEqual(areas(GF));
    expect(areas(FF).length).toBe(2);
  });

  it('carries room names, types, locks and solver targets onto the copies', () => {
    const doc = makeG1();
    const plan = planOrThrow(doc, {
      sourceStoreyId: GF,
      target: { kind: 'existing', storeyId: FF },
    });
    expect(plan.roomsCarried).toBe(1);

    const after = applyGroup(doc, plan.ops).model;
    const copied = after.house.rooms.find((r) => r.storeyId === FF && r.name === 'Master Bedroom');
    expect(copied).toBeDefined();
    expect(copied?.type).toBe('bedroom_master');
    expect(copied?.locked).toBe(true);
    expect(copied?.tags).toEqual(['vastu-sw']);
    expect(copied?.targetAreaMm2).toBe(12_000_000);
    expect(copied?.mustFace).toBe('SW');
    // And the room it was copied from is untouched.
    expect(after.house.rooms.filter((r) => r.name === 'Master Bedroom')).toHaveLength(2);
  });

  it('replaces what was on the target and reports it', () => {
    const doc = makeG1WithContent();
    const before = storeyContentCounts(doc.house, FF);
    expect(isStoreyEmpty(before)).toBe(false);

    const plan = planOrThrow(doc, {
      sourceStoreyId: GF,
      target: { kind: 'existing', storeyId: FF },
    });
    expect(plan.replaced).toEqual(before);
    expect(describeCounts(plan.replaced)).toBe('1 wall and 1 stair');

    const after = applyGroup(doc, plan.ops).model;
    // The first floor's own wall (at y = 8000) is gone, not merged with the copy.
    expect(after.house.walls.filter((w) => w.storeyId === FF && w.a.y === 8000)).toHaveLength(0);
    expect(storeyContentCounts(after.house, FF)).toEqual(storeyContentCounts(after.house, GF));
  });

  it('leaves the source storey completely untouched', () => {
    const doc = makeG1();
    const beforeHash = stateHash(doc);
    const plan = planOrThrow(doc, {
      sourceStoreyId: GF,
      target: { kind: 'existing', storeyId: FF },
    });
    const after = applyGroup(doc, plan.ops).model;

    // Planning is pure: the document it was planned against did not move.
    expect(stateHash(doc)).toBe(beforeHash);
    const sourceIds = (d: ProjectDoc): string[] =>
      d.house.walls
        .filter((w) => w.storeyId === GF)
        .map((w) => w.id)
        .sort();
    expect(sourceIds(after)).toEqual(sourceIds(doc));
  });
});

// ---------------------------------------------------------------------------
// Refusals — say no, do not fold something broken
// ---------------------------------------------------------------------------

describe('refusals', () => {
  it('refuses to copy a storey onto itself', () => {
    const result = planStoreyCopy(makeG1(), {
      sourceStoreyId: GF,
      target: { kind: 'existing', storeyId: GF },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.refusal.reason).toBe('same-storey');
  });

  it('refuses an empty source', () => {
    const result = planStoreyCopy(makeG1(), {
      sourceStoreyId: FF,
      target: { kind: 'existing', storeyId: GF },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.refusal.reason).toBe('empty-source');
      expect(result.refusal.message).toContain('nothing to copy');
    }
  });

  it('refuses a storey that is not in the document', () => {
    const result = planStoreyCopy(makeG1(), {
      sourceStoreyId: 'storey_01J0000000000000000000ZZ',
      target: { kind: 'new' },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.refusal.reason).toBe('unknown-storey');
  });

  it('refuses a stair that does not fit a shorter target, and says why', () => {
    // 20 × 150 = 3000 mm of rise on a 2700 mm storey: the fold's own ±10 mm
    // invariant. This is the trap that would otherwise reject the whole group
    // AFTER the confirm dialog promised it would work.
    const result = planStoreyCopy(makeG1(2700), {
      sourceStoreyId: GF,
      target: { kind: 'existing', storeyId: FF },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.refusal.reason).toBe('rejected');
      expect(result.refusal.issues.length).toBeGreaterThan(0);
      expect(result.refusal.message.length).toBeGreaterThan(10);
    }
  });

  it('matchHeight makes that same copy possible, height op first', () => {
    const doc = makeG1(2700);
    const plan = planOrThrow(doc, {
      sourceStoreyId: GF,
      target: { kind: 'existing', storeyId: FF },
      matchHeight: true,
    });
    expect(plan.ops[0]?.type).toBe('storey.set_height');
    expect(plan.heightChangeMm).toEqual([2700, 3000]);

    const after = applyGroup(doc, plan.ops).model;
    expect(after.house.storeys.find((s) => s.id === FF)?.heightMm).toBe(3000);
    expect(after.house.stairs.filter((s) => s.storeyId === FF)).toHaveLength(1);
  });

  it('does not emit a height op when the heights already agree', () => {
    const plan = planOrThrow(makeG1(), {
      sourceStoreyId: GF,
      target: { kind: 'existing', storeyId: FF },
      matchHeight: true,
    });
    expect(plan.ops.some((op) => op.type === 'storey.set_height')).toBe(false);
    expect(plan.heightChangeMm).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// ONE GESTURE, ONE UNDO — the property this feature is most likely to lose
// ---------------------------------------------------------------------------

describe('one gesture, one undo', () => {
  it('applies a whole copy as a single history entry', async () => {
    await hydrate(makeG1());
    useUiStore.getState().setActiveStorey(GF);

    const before = useModelStore.getState();
    expect(before.undoStack).toHaveLength(0);

    const outcome = runStoreyCopy({
      sourceStoreyId: GF,
      target: { kind: 'existing', storeyId: FF },
    });
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;

    const after = useModelStore.getState();
    // Many ops…
    expect(outcome.plan.ops.length).toBeGreaterThan(10);
    // …one undo step, and one pending group on the wire.
    expect(after.undoStack).toHaveLength(1);
    expect(after.pending).toHaveLength(1);
    expect(after.undoStack[0]?.ops).toHaveLength(outcome.plan.ops.length);

    // Every op carries the SAME groupId — that is what makes it one group for
    // the server, the op log and every other client.
    const groupIds = new Set((after.pending[0]?.ops ?? []).map((op) => op.groupId));
    expect(groupIds.size).toBe(1);

    await useModelStore.getState().flush();
    expect(mocks.append).toHaveBeenCalledTimes(1);
    const sent = mocks.append.mock.calls[0]?.[0] as AppendInput;
    expect(sent.ops).toHaveLength(outcome.plan.ops.length);
  });

  it('one undo puts the document back exactly, byte for byte', async () => {
    const doc = makeG1();
    await hydrate(doc);
    const hashBefore = stateHash(useModelStore.getState().doc);

    const outcome = runStoreyCopy({
      sourceStoreyId: GF,
      target: { kind: 'existing', storeyId: FF },
    });
    expect(outcome.ok).toBe(true);
    expect(stateHash(useModelStore.getState().doc)).not.toBe(hashBefore);

    expect(useModelStore.getState().undo()).toBe(true);

    // The state hash is the whole document, canonicalised — the same number the
    // server checks. Equal means nothing was left behind and nothing was lost.
    expect(stateHash(useModelStore.getState().doc)).toBe(hashBefore);
    expect(useModelStore.getState().undoStack).toHaveLength(0);
  });

  it('one undo restores a storey that the copy replaced', async () => {
    const doc = makeG1WithContent();
    await hydrate(doc);
    const hashBefore = stateHash(useModelStore.getState().doc);
    const roomsBefore = useModelStore.getState().doc.house.rooms.length;

    const outcome = runStoreyCopy({
      sourceStoreyId: GF,
      target: { kind: 'existing', storeyId: FF },
    });
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;
    expect(isStoreyEmpty(outcome.plan.replaced)).toBe(false);

    expect(useModelStore.getState().undo()).toBe(true);

    const restored = useModelStore.getState().doc;
    expect(stateHash(restored)).toBe(hashBefore);
    expect(restored.house.rooms).toHaveLength(roomsBefore);
    // The first floor's own wall is back, with its own id.
    expect(restored.house.walls.some((w) => w.id === FF_OWN_WALL)).toBe(true);
    expect(restored.house.stairs.some((s) => s.id === FF_OWN_STAIR)).toBe(true);
  });

  it('adding a storey is also one undo, and undoing it takes the storey away', async () => {
    await hydrate(makeGroundFloor());
    const hashBefore = stateHash(useModelStore.getState().doc);

    const outcome = runAddStorey();
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;
    expect(outcome.name).toBe('First Floor');
    expect(useModelStore.getState().undoStack).toHaveLength(1);
    expect(useModelStore.getState().doc.house.storeys).toHaveLength(2);

    expect(useModelStore.getState().undo()).toBe(true);
    expect(useModelStore.getState().doc.house.storeys).toHaveLength(1);
    expect(stateHash(useModelStore.getState().doc)).toBe(hashBefore);
  });

  it('follows the copy to the storey it landed on, and offers the undo', async () => {
    await hydrate(makeGroundFloor());
    useUiStore.getState().setActiveStorey(GF);

    const outcome = runStoreyCopy({ sourceStoreyId: GF, target: { kind: 'new' } });
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;

    expect(useUiStore.getState().activeStoreyId).toBe(outcome.plan.targetStoreyId);

    // §15: an edit this large offers to take itself back.
    const toast = useUiStore.getState().toasts.at(-1);
    expect(toast?.title).toBe('Ground Floor copied to First Floor');
    expect(toast?.action?.label).toBe('Undo');

    toast?.action?.run();
    expect(useModelStore.getState().doc.house.storeys).toHaveLength(1);
  });
});
