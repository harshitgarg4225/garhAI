/**
 * transform.test.ts — copy / paste / array / mirror.
 *
 * The heart of this file is the golden corpus at the bottom: every row of
 * `fixtures/model/golden-transforms.json` is asserted here AND in
 * `apps/api/garh_model/tests/test_transform.py`. These transforms add no op
 * type, so a divergence between the two planners would never be caught by a
 * fold — it would surface as the browser and the server disagreeing about a
 * document the architect is still editing.
 *
 * Everything above it is the unit-level reasoning the corpus depends on: the
 * plane map's algebra, the door-hand rule, the stair's origin corner, and the
 * guards. Each guard is written so that deleting the thing it guards turns this
 * file red — a green check that cannot go red is worse than no check.
 */

import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { applyGroup, docHash, replay, stateHash, UndoStack } from './fold';
import { emptyProjectDoc } from './model';
import type { ProjectDoc } from './model';
import type { Op } from './ops';
import {
  FIXTURE_IDS,
  fixedId,
  makeTwoRoomPlan,
  makeTwoRoomPlanWithOpenings,
  twoRoomPlanOps,
} from './testing';
import {
  IDENTITY_MAP,
  MAX_ARRAY_INSTANCES,
  describeSelection,
  isReflection,
  mapDirection,
  mapPolygon,
  mapPt,
  mapRotationDeg,
  mapStairPlacement,
  mapSwing,
  planArray,
  planMirror,
  planPaste,
  MAX_ARRAY_ELEMENTS,
  reflectionMap,
  roomMetadataOps,
  translationMap,
} from './transform';
import type {
  ArrayRequest,
  MirrorRequest,
  PasteRequest,
  PlaneMap,
  TransformPlan,
  TransformPlanResult,
} from './transform';
import type { OpeningSwing } from './model';
import type { Id, StoreyId } from './ids';
import type { Pt } from './geometry';

const GROUP = fixedId('group', 'GTEST');
const GF = FIXTURE_IDS.groundStorey;
const FF = FIXTURE_IDS.firstStorey;
const WALLS: string[] = [
  FIXTURE_IDS.wallSouth,
  FIXTURE_IDS.wallEast,
  FIXTURE_IDS.wallNorth,
  FIXTURE_IDS.wallWest,
  FIXTURE_IDS.wallSpine,
];

function expectPlan(result: TransformPlanResult): TransformPlan {
  if (!result.ok) {
    throw new Error(
      `expected a plan, got refusal ${result.refusal.reason}: ${result.refusal.message}`,
    );
  }
  return result.plan;
}

function refusalReason(result: TransformPlanResult): string {
  return result.ok ? 'ok' : result.refusal.reason;
}

/**
 * Everything a transform is responsible for putting back, EXCEPT derived room
 * identity.
 *
 * A mirror in place has to delete and re-add its walls (see the module
 * docstring), and the fold cannot restore a derived room's ID through that —
 * `wall.delete` × n followed by `wall.add` × n at IDENTICAL coordinates loses it
 * too, in both languages, with no transform involved at all. Room ids are
 * history and no op sets one. So the round-trip assertion is written against the
 * geometry, which IS fully restorable and IS this module's job: every wall,
 * opening, stair, column, furniture item, balcony, and every room POLYGON.
 */
function geometrySignature(doc: ProjectDoc): string {
  const h = doc.house;
  return JSON.stringify({
    walls: h.walls.map((w) => [w.id, w.storeyId, w.a, w.b, w.thicknessMm, w.kind, w.loadBearing]),
    openings: h.openings.map((o) => [
      o.id,
      o.wallId,
      o.kind,
      o.widthMm,
      o.heightMm,
      o.sillMm,
      o.offsetMm,
      o.swing,
    ]),
    stairs: h.stairs.map((s) => [s.id, s.storeyId, s.kind, s.origin, s.direction, s.risersCount]),
    columns: h.columns.map((c) => [c.id, c.storeyId, c.pt, c.sizeMm]),
    furniture: h.furniture.map((f) => [f.id, f.storeyId, f.catalogId, f.pt, f.rotationDeg]),
    balconies: h.balconies.map((b) => [b.id, b.storeyId, b.polygon]),
    roomPolygons: h.rooms.map((r) => [r.storeyId, r.polygon, r.areaMm2]),
  });
}

// ---------------------------------------------------------------------------
// The plane map
// ---------------------------------------------------------------------------

describe('plane map', () => {
  it('translates exactly', () => {
    expect(mapPt(translationMap(1500, -250), { x: 100, y: 100 })).toEqual({ x: 1600, y: -150 });
  });

  it('reflects about a vertical line and is an exact involution', () => {
    // `twiceAt` is 2·9000; the axis is x = 9000.
    const m = reflectionMap('vertical', 18000);
    const p: Pt = { x: 2345, y: 678 };
    expect(mapPt(m, p)).toEqual({ x: 15655, y: 678 });
    expect(mapPt(m, mapPt(m, p))).toEqual(p);
  });

  it('reflects about a HALF-millimetre axis without drifting', () => {
    // The selection-centre default: an extent of 0..4001 has its centre at
    // 2000.5, which is why the map carries 2·at as an integer. Rounding the
    // axis instead would move every mirrored point by half a millimetre and
    // would not be an involution.
    const m = reflectionMap('vertical', 4001);
    expect(mapPt(m, { x: 0, y: 0 })).toEqual({ x: 4001, y: 0 });
    expect(mapPt(m, { x: 4001, y: 0 })).toEqual({ x: 0, y: 0 });
    expect(mapPt(m, mapPt(m, { x: 1234, y: 0 }))).toEqual({ x: 1234, y: 0 });
  });

  it('knows which maps reverse orientation', () => {
    expect(isReflection(IDENTITY_MAP)).toBe(false);
    expect(isReflection(translationMap(10, 20))).toBe(false);
    expect(isReflection(reflectionMap('vertical', 0))).toBe(true);
    expect(isReflection(reflectionMap('horizontal', 0))).toBe(true);
    // Two reflections compose to a 180° rotation, which preserves orientation.
    expect(isReflection({ sx: -1, sy: -1, tx: 0, ty: 0 })).toBe(false);
  });

  it('re-winds a ring under a reflection and leaves it alone otherwise', () => {
    const ccw: Pt[] = [
      { x: 0, y: 0 },
      { x: 100, y: 0 },
      { x: 100, y: 100 },
      { x: 0, y: 100 },
    ];
    expect(mapPolygon(translationMap(10, 10), ccw)).toEqual([
      { x: 10, y: 10 },
      { x: 110, y: 10 },
      { x: 110, y: 110 },
      { x: 10, y: 110 },
    ]);
    // Reflected about x = 0, then reversed so the ring is CCW again.
    expect(mapPolygon(reflectionMap('vertical', 0), ccw)).toEqual([
      { x: 0, y: 100 },
      { x: -100, y: 100 },
      { x: -100, y: 0 },
      { x: 0, y: 0 },
    ]);
  });

  it('maps directions of travel', () => {
    const v = reflectionMap('vertical', 0);
    const h = reflectionMap('horizontal', 0);
    expect([
      mapDirection(v, 'N'),
      mapDirection(v, 'E'),
      mapDirection(v, 'S'),
      mapDirection(v, 'W'),
    ]).toEqual(['N', 'W', 'S', 'E']);
    expect([
      mapDirection(h, 'N'),
      mapDirection(h, 'E'),
      mapDirection(h, 'S'),
      mapDirection(h, 'W'),
    ]).toEqual(['S', 'E', 'N', 'W']);
  });
});

// ---------------------------------------------------------------------------
// The door hand — the geometry claim most likely to be quietly wrong
// ---------------------------------------------------------------------------

describe('door hand under a mirror', () => {
  const v = reflectionMap('vertical', 0);
  const h = reflectionMap('horizontal', 0);

  it('flips IN/OUT and keeps LEFT/RIGHT', () => {
    // LEFT/RIGHT is the hinge END along the wall's a→b parameter; a reflection
    // that maps a↦M(a), b↦M(b) preserves it. IN/OUT is which side of that line
    // the leaf sweeps into, and a reflection reverses exactly that.
    for (const m of [v, h]) {
      expect(mapSwing(m, 'in-left')).toBe('out-left');
      expect(mapSwing(m, 'in-right')).toBe('out-right');
      expect(mapSwing(m, 'out-left')).toBe('in-left');
      expect(mapSwing(m, 'out-right')).toBe('in-right');
    }
  });

  it('leaves the hand alone under a translation', () => {
    expect(mapSwing(translationMap(9000, 0), 'in-left')).toBe('in-left');
    expect(mapSwing(IDENTITY_MAP, 'out-right')).toBe('out-right');
  });

  it('refuses a swing that is not in the enum rather than returning nothing', () => {
    // Bug class 2 in CLAUDE.md: a value outside the enum going quietly inert. A
    // switch with no default, or a mapped-type lookup, would hand back
    // `undefined` here — a door with no hand — and the Python mirror would raise
    // instead. Both now fail, loudly and the same way.
    expect(() => mapSwing(v, 'sliding' as OpeningSwing)).toThrow(RangeError);
    // ...but only when the map actually reflects: a translation is a pass-through
    // by definition and must not start validating its input.
    expect(mapSwing(translationMap(1, 0), 'sliding' as OpeningSwing)).toBe('sliding');
  });

  it('is an involution — mirroring twice restores the original hand', () => {
    for (const swing of ['in-left', 'in-right', 'out-left', 'out-right'] as const) {
      expect(mapSwing(v, mapSwing(v, swing))).toBe(swing);
    }
  });

  it('mirrors a real door on a real wall, offset intact', () => {
    const doc = makeTwoRoomPlanWithOpenings();
    const plan = expectPlan(
      planMirror(doc, { elementIds: WALLS, groupId: GROUP, axis: 'vertical', atMm: 9000 }),
    );
    const doors = plan.ops.filter((op) => op.type === 'opening.add');
    expect(doors).toHaveLength(2);
    for (const op of doors) {
      if (op.type !== 'opening.add') continue;
      // The offset is a distance along the wall; a reflection is an isometry, so
      // it cannot change. If the wall were re-normalised left-to-right instead
      // of keeping a↦M(a), this would be `length − offset` and the door would
      // move.
      expect(op.payload.swing.startsWith('out-')).toBe(true);
    }
    const door = doors.find((op) => op.type === 'opening.add' && op.payload.kind === 'door');
    expect(door?.type).toBe('opening.add');
    if (door?.type === 'opening.add') {
      expect(door.payload.offsetMm).toBe(1500);
      expect(door.payload.swing).toBe('out-left');
    }
  });
});

// ---------------------------------------------------------------------------
// Furniture rotation: a rotation, never a reflection
// ---------------------------------------------------------------------------

describe('furniture rotation under a mirror', () => {
  it('reflects the facing axis without ever reflecting the item', () => {
    const v = reflectionMap('vertical', 0);
    const h = reflectionMap('horizontal', 0);
    expect(mapRotationDeg(v, 0)).toBe(180);
    expect(mapRotationDeg(v, 30)).toBe(150);
    expect(mapRotationDeg(v, 90)).toBe(90);
    expect(mapRotationDeg(h, 0)).toBe(0);
    expect(mapRotationDeg(h, 30)).toBe(330);
    expect(mapRotationDeg(h, 90)).toBe(270);
  });

  it('refuses a map it does not know rather than silently not rotating', () => {
    // No `?? [1, 0]` fallback: an unknown map must not leave every mirrored item
    // facing the way it already faced while everything else moves.
    expect(() => mapRotationDeg({ sx: 2, sy: 1, tx: 0, ty: 0 } as unknown as PlaneMap, 30)).toThrow(
      RangeError,
    );
  });

  it('always lands in [0, 360)', () => {
    const v = reflectionMap('vertical', 0);
    const h = reflectionMap('horizontal', 0);
    for (let deg = 0; deg < 360; deg++) {
      for (const m of [v, h, translationMap(1, 1)]) {
        const out = mapRotationDeg(m, deg);
        expect(Number.isInteger(out)).toBe(true);
        expect(out).toBeGreaterThanOrEqual(0);
        expect(out).toBeLessThan(360);
      }
      // …and mirroring twice is the identity, on every whole degree.
      expect(mapRotationDeg(v, mapRotationDeg(v, deg))).toBe(deg);
      expect(mapRotationDeg(h, mapRotationDeg(h, deg))).toBe(deg);
    }
  });
});

// ---------------------------------------------------------------------------
// Stair origin: a direction-dependent corner, not a mappable point
// ---------------------------------------------------------------------------

describe('stair placement under a map', () => {
  const stair = {
    id: FIXTURE_IDS.stair,
    storeyId: GF,
    kind: 'dogleg' as const,
    origin: { x: 1000, y: 500 },
    direction: 'N' as const,
    riserMm: 167,
    treadMm: 275,
    widthMm: 1000,
    risersCount: 18,
    landing: { widthMm: 2100, depthMm: 1000 },
  };

  it('is the identity under the identity map', () => {
    // The round trip is the real assertion: it proves the corner rule agrees
    // with `stairFootprintPolygon`, which is where the extents actually live.
    expect(mapStairPlacement(IDENTITY_MAP, stair)).toEqual({
      origin: stair.origin,
      direction: 'N',
    });
  });

  it('keeps the travel direction under a mirror in the same axis', () => {
    // Footprint is x 1000..3100, y 500..3700. Mirrored about x = 12000 the x
    // range becomes 20900..23000, and N travel is unchanged, so the origin
    // corner (minX, minY) is (20900, 500).
    expect(mapStairPlacement(reflectionMap('vertical', 24000), stair)).toEqual({
      origin: { x: 20900, y: 500 },
      direction: 'N',
    });
  });

  it('reverses the travel direction under a mirror across it', () => {
    // Mirrored about y = 0: y range becomes −3700..−500, travel becomes S, and
    // an S stair's origin corner is (maxX, maxY) = (3100, −500).
    expect(mapStairPlacement(reflectionMap('horizontal', 0), stair)).toEqual({
      origin: { x: 3100, y: -500 },
      direction: 'S',
    });
  });

  it('round-trips through two mirrors', () => {
    const m = reflectionMap('vertical', 24000);
    const once = mapStairPlacement(m, stair);
    const twice = mapStairPlacement(m, { ...stair, ...once });
    expect(twice).toEqual({ origin: stair.origin, direction: stair.direction });
  });
});

// ---------------------------------------------------------------------------
// The guards
// ---------------------------------------------------------------------------

describe('guards', () => {
  it('refuses a selection that spans two storeys', () => {
    // A transform has ONE target storey. Flattening a G+1 selection onto it
    // folds cleanly and is wrong, which is exactly the shape of bug this repo
    // keeps shipping — so it is a refusal, not a best effort.
    const doc = replay(
      [
        ...twoRoomPlanOps(),
        { type: 'storey.add', payload: { id: FF, index: 1, name: 'First Floor', heightMm: 3000 } },
        {
          type: 'wall.add',
          payload: {
            id: fixedId('wall', 'FFS'),
            storeyId: FF,
            a: { x: 0, y: 0 },
            b: { x: 6000, y: 0 },
            thicknessMm: 230,
            kind: 'external',
          },
        },
      ],
      emptyProjectDoc('ft-in'),
    );
    const result = planPaste(doc, {
      elementIds: [FIXTURE_IDS.wallSouth, fixedId('wall', 'FFS')],
      groupId: GROUP,
      deltaMm: { x: 0, y: 8000 },
    });
    expect(refusalReason(result)).toBe('mixed-storeys');
  });

  it('refuses a zero-delta paste onto the same storey — the fold would NOT', () => {
    // This is the guard's whole justification. Walls are protected by
    // WALL_DUPLICATE, but columns are not: prove the fold accepts a second
    // column on the same point, so the reader can see the planner is the only
    // thing standing between the user and a doubled structural count.
    const doc = replay(
      [
        ...twoRoomPlanOps(),
        {
          type: 'column.set',
          payload: {
            action: 'add',
            id: FIXTURE_IDS.column,
            storeyId: GF,
            pt: { x: 3000, y: 2000 },
          },
        },
      ],
      emptyProjectDoc('ft-in'),
    );
    const stacked = replay(
      [
        {
          type: 'column.set',
          payload: {
            action: 'add',
            id: fixedId('column', 'C2'),
            storeyId: GF,
            pt: { x: 3000, y: 2000 },
          },
        },
      ],
      doc,
    );
    expect(stacked.house.columns).toHaveLength(2);

    const result = planPaste(doc, {
      elementIds: [FIXTURE_IDS.column],
      groupId: GROUP,
      deltaMm: { x: 0, y: 0 },
    });
    expect(refusalReason(result)).toBe('zero-offset');
  });

  it('allows a zero-delta paste onto a DIFFERENT storey', () => {
    const doc = replay(
      [
        ...twoRoomPlanOps(),
        { type: 'storey.add', payload: { id: FF, index: 1, name: 'First Floor', heightMm: 3000 } },
      ],
      emptyProjectDoc('ft-in'),
    );
    const plan = expectPlan(
      planPaste(doc, {
        elementIds: WALLS,
        groupId: GROUP,
        deltaMm: { x: 0, y: 0 },
        targetStoreyId: FF as StoreyId,
      }),
    );
    expect(plan.created.walls).toBe(5);
    expect(plan.targetStoreyId).toBe(FF);
  });

  it('refuses an array with no spacing in a direction it repeats', () => {
    const doc = replay(
      [
        ...twoRoomPlanOps(),
        {
          type: 'column.set',
          payload: {
            action: 'add',
            id: FIXTURE_IDS.column,
            storeyId: GF,
            pt: { x: 3000, y: 2000 },
          },
        },
      ],
      emptyProjectDoc('ft-in'),
    );
    expect(
      refusalReason(
        planArray(doc, {
          elementIds: [FIXTURE_IDS.column],
          groupId: GROUP,
          countX: 3,
          countY: 2,
          spacingXMm: 0,
          spacingYMm: 1500,
        }),
      ),
    ).toBe('zero-offset');
    // …but a zero spacing in a direction with count 1 is meaningless, not wrong.
    expect(
      expectPlan(
        planArray(doc, {
          elementIds: [FIXTURE_IDS.column],
          groupId: GROUP,
          countX: 3,
          countY: 1,
          spacingXMm: 2000,
          spacingYMm: 0,
        }),
      ).instances,
    ).toBe(2);
  });

  it('refuses counts outside the range', () => {
    const doc = makeTwoRoomPlan();
    const base = {
      elementIds: [FIXTURE_IDS.wallSpine],
      groupId: GROUP,
      spacingXMm: 1000,
      spacingYMm: 1000,
    };
    expect(refusalReason(planArray(doc, { ...base, countX: 0, countY: 3 }))).toBe(
      'count-out-of-range',
    );
    expect(refusalReason(planArray(doc, { ...base, countX: 1, countY: 1 }))).toBe(
      'count-out-of-range',
    );
    expect(refusalReason(planArray(doc, { ...base, countX: MAX_ARRAY_INSTANCES, countY: 2 }))).toBe(
      'count-out-of-range',
    );
  });

  it('refuses an opening whose host wall is not in the selection', () => {
    const doc = makeTwoRoomPlanWithOpenings();
    expect(
      refusalReason(
        planPaste(doc, {
          elementIds: [FIXTURE_IDS.doorMain],
          groupId: GROUP,
          deltaMm: { x: 0, y: 8000 },
        }),
      ),
    ).toBe('opening-without-wall');
  });

  it('carries an unselected opening WITH its selected wall', () => {
    const doc = makeTwoRoomPlanWithOpenings();
    const plan = expectPlan(
      planPaste(doc, {
        elementIds: [FIXTURE_IDS.wallSouth],
        groupId: GROUP,
        deltaMm: { x: 0, y: 8000 },
      }),
    );
    expect(plan.selected.openings).toBe(1);
    expect(plan.ops.filter((op) => op.type === 'opening.add')).toHaveLength(1);
  });

  it('refuses an id that is no longer in the document', () => {
    expect(
      refusalReason(
        planPaste(makeTwoRoomPlan(), {
          elementIds: [fixedId('wall', 'GHOST')],
          groupId: GROUP,
          deltaMm: { x: 1000, y: 0 },
        }),
      ),
    ).toBe('unknown-element');
  });

  it('refuses a family it cannot honestly duplicate', () => {
    expect(
      refusalReason(
        planPaste(makeTwoRoomPlan(), {
          elementIds: [FIXTURE_IDS.annotation],
          groupId: GROUP,
          deltaMm: { x: 1000, y: 0 },
        }),
      ),
    ).toBe('unsupported-element');
  });

  it('skips derived rooms in a mixed selection instead of refusing it', () => {
    const doc = makeTwoRoomPlan();
    const roomId = doc.house.rooms[0]?.id ?? '';
    expect(roomId).not.toBe('');
    const plan = expectPlan(
      planPaste(doc, {
        elementIds: [FIXTURE_IDS.wallSpine, roomId],
        groupId: GROUP,
        deltaMm: { x: 0, y: 9000 },
      }),
    );
    expect(plan.derivedSkipped).toBe(1);
    expect(plan.selected.walls).toBe(1);
    // …but a selection of NOTHING but rooms has no geometry to transform.
    expect(
      refusalReason(
        planPaste(doc, { elementIds: [roomId], groupId: GROUP, deltaMm: { x: 0, y: 9000 } }),
      ),
    ).toBe('empty-selection');
  });

  it('refuses when the fold refuses, and says why', () => {
    // A copy landing exactly on an existing wall is WALL_DUPLICATE. The planner
    // does not re-implement that rule; it folds on a fork and reports what the
    // fold said, so there is one source of truth for what a legal wall is.
    const doc = makeTwoRoomPlan();
    const result = planPaste(doc, {
      elementIds: [FIXTURE_IDS.wallSouth],
      groupId: GROUP,
      deltaMm: { x: 0, y: 4000 },
    });
    expect(refusalReason(result)).toBe('rejected');
    if (!result.ok) {
      expect(result.refusal.issues.map((i) => i.code)).toContain('WALL_DUPLICATE');
    }
  });

  it('refuses an unknown target storey', () => {
    expect(
      refusalReason(
        planPaste(makeTwoRoomPlan(), {
          elementIds: [FIXTURE_IDS.wallSpine],
          groupId: GROUP,
          deltaMm: { x: 0, y: 0 },
          targetStoreyId: fixedId('storey', 'NOPE') as StoreyId,
        }),
      ),
    ).toBe('unknown-storey');
  });
});

// ---------------------------------------------------------------------------
// One gesture, one undo
// ---------------------------------------------------------------------------

describe('one gesture, one undo', () => {
  /** `exactHash` is false only where the pre-existing room-id gap applies. */
  const cases: readonly (readonly [string, (doc: ProjectDoc) => TransformPlanResult, boolean])[] = [
    [
      'paste',
      (doc) => planPaste(doc, { elementIds: WALLS, groupId: GROUP, deltaMm: { x: 0, y: 9000 } }),
      true,
    ],
    [
      'array',
      (doc) =>
        planArray(doc, {
          elementIds: [FIXTURE_IDS.wallSpine],
          groupId: GROUP,
          countX: 4,
          countY: 1,
          spacingXMm: -4000,
          spacingYMm: 0,
        }),
      true,
    ],
    [
      'mirror copy',
      (doc) => planMirror(doc, { elementIds: WALLS, groupId: GROUP, axis: 'vertical', atMm: 9000 }),
      true,
    ],
    [
      // Additive transforms restore the hash exactly. A mirror in place has to
      // delete and re-add its walls, and the fold cannot carry a derived room's
      // ID through that — see `geometrySignature` above for why that is a
      // property of the fold and not of this module.
      'mirror in place',
      (doc) =>
        planMirror(doc, {
          elementIds: WALLS,
          groupId: GROUP,
          axis: 'horizontal',
          keepOriginal: false,
        }),
      false,
    ],
  ];

  it.each(cases)('%s is a single undoable group', (_name, plan, exactHash) => {
    const doc = makeTwoRoomPlanWithOpenings();
    const before = stateHash(doc);
    const beforeGeometry = geometrySignature(doc);
    const result = plan(doc);
    const built = expectPlan(result);
    expect(built.ops.length).toBeGreaterThan(1);

    const group = applyGroup(doc, built.ops, built.groupId);
    expect(stateHash(group.model)).not.toBe(before);
    // Every op in the group carries the SAME groupId — that is what makes the
    // whole paste one row in the undo stack rather than twelve.
    for (const op of group.ops) expect(op.groupId).toBe(built.groupId);

    const stack = new UndoStack();
    stack.push({ groupId: built.groupId, ops: group.ops, inverse: group.inverse });
    const undone = stack.undo(group.model);
    expect(undone).not.toBeNull();
    // ONE undo, not one per op: the stack is empty after a single step.
    expect(stack.toJSON().undo).toHaveLength(0);
    if (undone !== null) {
      expect(geometrySignature(undone.model)).toBe(beforeGeometry);
      if (exactHash) expect(stateHash(undone.model)).toBe(before);
      // …and redo of that ONE entry puts it all back.
      const redone = stack.redo(undone.model);
      expect(redone).not.toBeNull();
      if (redone !== null) {
        expect(geometrySignature(redone.model)).toBe(geometrySignature(group.model));
      }
    }
  });
});

// ---------------------------------------------------------------------------
// Behaviour that only shows up once the ops are folded
// ---------------------------------------------------------------------------

describe('folded behaviour', () => {
  it('mirroring a plan in place cannot be done with wall.move — and is done anyway', () => {
    // South and north swap positions under a mirror about the plan's own
    // horizontal centre, so `wall.move` on either one first trips
    // WALL_DUPLICATE. The plan therefore deletes every selected wall before
    // re-adding any of them, at the ORIGINAL ids.
    const doc = makeTwoRoomPlanWithOpenings();
    const plan = expectPlan(
      planMirror(doc, {
        elementIds: WALLS,
        groupId: GROUP,
        axis: 'horizontal',
        keepOriginal: false,
      }),
    );
    const types = plan.ops.map((op) => op.type);
    expect(types.filter((t) => t === 'wall.delete')).toHaveLength(5);
    expect(types.filter((t) => t === 'wall.add')).toHaveLength(5);
    expect(types).not.toContain('wall.move');
    // The last delete comes before the first add: that is what breaks the cycle.
    expect(types.lastIndexOf('wall.delete')).toBeLessThan(types.indexOf('wall.add'));

    const after = applyGroup(doc, plan.ops, plan.groupId).model;
    const ids = new Set(after.house.walls.map((w) => w.id));
    for (const id of WALLS) expect(ids.has(id)).toBe(true);
    expect(after.house.walls).toHaveLength(5);
    expect(after.house.openings).toHaveLength(2);
    // Mirroring about the plan's own centre is an involution on the geometry, so
    // doing it twice restores the document exactly.
    const twice = applyGroup(
      after,
      expectPlan(
        planMirror(after, {
          elementIds: WALLS,
          groupId: GROUP,
          axis: 'horizontal',
          keepOriginal: false,
        }),
      ).ops,
      GROUP,
    ).model;
    expect(stateHash(twice)).toBe(stateHash(doc));
  });

  it('carries room names onto the copies', () => {
    const doc = makeTwoRoomPlan();
    const rooms = doc.house.rooms;
    expect(rooms).toHaveLength(2);
    const named = replay(
      rooms.map((room, i) => ({
        type: 'room.assign' as const,
        payload: { roomId: room.id, type: i === 0 ? ('living' as const) : ('kitchen' as const) },
      })),
      doc,
    );

    const plan = expectPlan(
      planMirror(named, { elementIds: WALLS, groupId: GROUP, axis: 'vertical', atMm: 9000 }),
    );
    expect(plan.roomsCarried).toBe(2);
    const after = applyGroup(named, plan.ops, plan.groupId).model;
    expect(after.house.rooms).toHaveLength(4);
    const types = after.house.rooms.map((r) => r.type).sort();
    expect(types).toEqual(['kitchen', 'kitchen', 'living', 'living']);
  });

  it("leaves the target storey's named rooms alone", () => {
    // The carry-over only touches rooms that come out of the fold BLANK. Give
    // the target storey a named room first and prove the paste leaves it alone.
    const doc = makeTwoRoomPlan();
    const named = replay(
      [
        {
          type: 'room.assign',
          payload: { roomId: doc.house.rooms[0]?.id ?? '', type: 'living', name: 'Living' },
        },
        {
          type: 'room.assign',
          payload: { roomId: doc.house.rooms[1]?.id ?? '', type: 'kitchen', name: 'Kitchen' },
        },
      ],
      doc,
    );
    const plan = expectPlan(
      planPaste(named, { elementIds: WALLS, groupId: GROUP, deltaMm: { x: 0, y: 9000 } }),
    );
    const after = applyGroup(named, plan.ops, plan.groupId).model;
    const byName = after.house.rooms.filter((r) => r.name === 'Living');
    expect(byName).toHaveLength(2);
    // The originals still carry exactly what they had.
    for (const id of [named.house.rooms[0]?.id, named.house.rooms[1]?.id]) {
      const room = after.house.rooms.find((r) => r.id === id);
      expect(room?.name).not.toBe('');
    }
  });

  it('is idempotent for a given group id, and different for a different one', () => {
    const doc = makeTwoRoomPlan();
    const req: PasteRequest = {
      elementIds: [FIXTURE_IDS.wallSpine],
      groupId: GROUP,
      deltaMm: { x: 0, y: 9000 },
    };
    const a = expectPlan(planPaste(doc, req));
    const b = expectPlan(planPaste(doc, req));
    expect(b.ops).toEqual(a.ops);
    const c = expectPlan(planPaste(doc, { ...req, groupId: fixedId('group', 'OTHER') }));
    expect(c.ops).not.toEqual(a.ops);
  });

  it('mints ids that do not collide with the document', () => {
    const doc = makeTwoRoomPlanWithOpenings();
    const plan = expectPlan(
      planArray(doc, {
        elementIds: [FIXTURE_IDS.wallSpine],
        groupId: GROUP,
        countX: 4,
        countY: 1,
        spacingXMm: -4000,
        spacingYMm: 0,
      }),
    );
    const minted = plan.ops.flatMap((op) => (op.type === 'wall.add' ? [op.payload.id] : []));
    expect(new Set(minted).size).toBe(minted.length);
    const existing = new Set(doc.house.walls.map((w) => w.id));
    for (const id of minted) expect(existing.has(id)).toBe(false);
  });

  it('describes a selection the way the confirm copy reads', () => {
    expect(
      describeSelection({
        walls: 0,
        openings: 0,
        stairs: 0,
        columns: 0,
        furniture: 0,
        balconies: 0,
      }),
    ).toBe('nothing');
    expect(
      describeSelection({
        walls: 1,
        openings: 0,
        stairs: 0,
        columns: 0,
        furniture: 0,
        balconies: 0,
      }),
    ).toBe('1 wall');
    expect(
      describeSelection({
        walls: 5,
        openings: 2,
        stairs: 1,
        columns: 0,
        furniture: 0,
        balconies: 0,
      }),
    ).toBe('5 walls, 2 openings and 1 stair');
  });
});

// ---------------------------------------------------------------------------
// THE cross-language check
// ---------------------------------------------------------------------------
//
// fixtures/model/golden-transforms.json is generated by
// fixtures/model/_tools/generate_golden_transforms.py from the PYTHON planner.
// This block is the TypeScript half of that contract, and it is the only thing
// in the repo that can catch the two planners disagreeing about what a paste IS.
//
// A failure here is never fixed by pasting the new value. These transforms emit
// only ops both folds already agree on, so a divergence would be accepted by
// both sides and would show up as two different documents with the same op log
// in front of them. Find out which side moved, then regenerate with
//     PYTHONPATH=apps/api python3 fixtures/model/_tools/generate_golden_transforms.py
// in the same commit, with a note.
// ---------------------------------------------------------------------------

interface GoldenCounts {
  readonly walls: number;
  readonly openings: number;
  readonly stairs: number;
  readonly columns: number;
  readonly furniture: number;
  readonly balconies: number;
}

interface GoldenRequest {
  readonly kind: 'paste' | 'array' | 'mirror';
  readonly elementIds: readonly string[];
  readonly groupId: string;
  readonly deltaMm?: Pt;
  readonly targetStoreyId?: string | null;
  readonly countX?: number;
  readonly countY?: number;
  readonly spacingXMm?: number;
  readonly spacingYMm?: number;
  readonly axis?: 'vertical' | 'horizontal';
  readonly atMm?: number | null;
  readonly keepOriginal?: boolean;
}

interface GoldenTransformCase {
  readonly name: string;
  readonly description: string;
  readonly unitsDisplay: 'ft-in' | 'm';
  readonly baseOps: readonly Op[];
  readonly request: GoldenRequest;
  readonly expectedOps?: readonly Op[];
  readonly expectedPlan?: {
    readonly kind: string;
    readonly sourceStoreyId: string;
    readonly targetStoreyId: string;
    readonly instances: number;
    readonly selected: GoldenCounts;
    readonly created: GoldenCounts;
    readonly derivedSkipped: number;
    readonly roomsCarried: number;
    readonly label: string;
  };
  readonly expectedStateHash?: string;
  readonly expectedRefusal?: { readonly reason: string; readonly message: string };
}

interface GoldenTransformsFile {
  readonly schemaVersion: number;
  readonly canonicalJsonSpec: string;
  readonly hashAlgorithm: string;
  readonly cases: readonly GoldenTransformCase[];
}

function loadGoldenTransforms(): GoldenTransformsFile {
  const url = new URL('../../../fixtures/model/golden-transforms.json', import.meta.url);
  return JSON.parse(readFileSync(url, 'utf8')) as GoldenTransformsFile;
}

/** Run one language-neutral request through the planner it names. */
function runGolden(doc: ProjectDoc, request: GoldenRequest): TransformPlanResult {
  const groupId = request.groupId as Id<'group'>;
  const elementIds = request.elementIds;
  if (request.kind === 'paste') {
    const req: PasteRequest = {
      elementIds,
      groupId,
      deltaMm: request.deltaMm ?? { x: 0, y: 0 },
      targetStoreyId: (request.targetStoreyId ?? null) as StoreyId | null,
    };
    return planPaste(doc, req);
  }
  if (request.kind === 'array') {
    const req: ArrayRequest = {
      elementIds,
      groupId,
      countX: request.countX ?? 1,
      countY: request.countY ?? 1,
      spacingXMm: request.spacingXMm ?? 0,
      spacingYMm: request.spacingYMm ?? 0,
    };
    return planArray(doc, req);
  }
  const req: MirrorRequest = {
    elementIds,
    groupId,
    axis: request.axis ?? 'vertical',
    atMm: request.atMm ?? null,
    keepOriginal: request.keepOriginal ?? true,
    targetStoreyId: (request.targetStoreyId ?? null) as StoreyId | null,
  };
  return planMirror(doc, req);
}

describe('golden transforms (fixtures/model/golden-transforms.json)', () => {
  const golden = loadGoldenTransforms();

  it('declares the canonical form this package implements', () => {
    expect(golden.canonicalJsonSpec).toBe('garh-canonical-json/v1');
    expect(golden.hashAlgorithm).toBe('sha256(garh-canonical-json/v1)');
    expect(golden.cases.length).toBeGreaterThanOrEqual(16);
    // Both halves of the contract must actually be present, or a corpus of
    // nothing but successes would quietly stop testing the guards.
    expect(
      golden.cases.filter((c) => c.expectedRefusal !== undefined).length,
    ).toBeGreaterThanOrEqual(8);
    expect(golden.cases.filter((c) => c.expectedOps !== undefined).length).toBeGreaterThanOrEqual(
      7,
    );
  });

  it.each(golden.cases.map((c) => [c.name, c] as const))(
    'plans %s exactly as the Python mirror did',
    (_name: string, testCase: GoldenTransformCase) => {
      const doc = replay(testCase.baseOps, emptyProjectDoc(testCase.unitsDisplay));
      const result = runGolden(doc, testCase.request);

      if (testCase.expectedRefusal !== undefined) {
        expect(result.ok).toBe(false);
        if (result.ok) return;
        expect(result.refusal.reason).toBe(testCase.expectedRefusal.reason);
        expect(result.refusal.message).toBe(testCase.expectedRefusal.message);
        return;
      }

      expect(result.ok).toBe(true);
      if (!result.ok) return;
      const plan = result.plan;

      // The op list, key for key. `JSON.parse(JSON.stringify(...))` normalises
      // the TypeScript objects to the wire form the fixture stores, so an extra
      // `undefined`-valued key on either side is a mismatch, not a pass.
      expect(JSON.parse(JSON.stringify(plan.ops)) as unknown).toEqual(testCase.expectedOps);

      const expected = testCase.expectedPlan;
      expect(expected).toBeDefined();
      if (expected !== undefined) {
        expect(plan.kind).toBe(expected.kind);
        expect(plan.sourceStoreyId).toBe(expected.sourceStoreyId);
        expect(plan.targetStoreyId).toBe(expected.targetStoreyId);
        expect(plan.instances).toBe(expected.instances);
        expect(plan.selected).toEqual(expected.selected);
        expect(plan.created).toEqual(expected.created);
        expect(plan.derivedSkipped).toBe(expected.derivedSkipped);
        expect(plan.roomsCarried).toBe(expected.roomsCarried);
        expect(plan.label).toBe(expected.label);
      }

      const after = applyGroup(doc, plan.ops, plan.groupId).model;
      expect(stateHash(after)).toBe(testCase.expectedStateHash);
      expect(docHash(after)).toBe(testCase.expectedStateHash);
    },
  );

  it('every golden hash is 64 lowercase hex characters', () => {
    for (const testCase of golden.cases) {
      if (testCase.expectedStateHash === undefined) continue;
      expect(testCase.expectedStateHash).toMatch(/^[0-9a-f]{64}$/);
    }
  });

  it('undoes every golden transform back to the geometry it started from', () => {
    for (const testCase of golden.cases) {
      if (testCase.expectedOps === undefined) continue;
      const doc = replay(testCase.baseOps, emptyProjectDoc(testCase.unitsDisplay));
      const beforeHash = stateHash(doc);
      const beforeGeometry = geometrySignature(doc);
      const result = runGolden(doc, testCase.request);
      expect(result.ok).toBe(true);
      if (!result.ok) continue;
      const group = applyGroup(doc, result.plan.ops, result.plan.groupId);
      const stack = new UndoStack();
      stack.push({ groupId: result.plan.groupId, ops: group.ops, inverse: group.inverse });
      const undone = stack.undo(group.model);
      expect(undone).not.toBeNull();
      if (undone === null) continue;
      expect(geometrySignature(undone.model)).toBe(beforeGeometry);
      // Additive plans restore the hash exactly; the in-place mirror hits the
      // fold's pre-existing derived-room-id gap (see `geometrySignature`).
      if (result.plan.instances > 0) expect(stateHash(undone.model)).toBe(beforeHash);
    }
  });

  it('rests on the door the corpus says it rests on', () => {
    // Guards the corpus against a silent change of the shared fixture: if the
    // demo door stops being `in-left` at 1500, the mirror rows stop proving
    // anything about the hand and would still be green.
    const mirrorRow = golden.cases.find((c) => c.name === 'mirror-copy-vertical-with-doors');
    expect(mirrorRow).toBeDefined();
    const door = mirrorRow?.baseOps.find(
      (op) => op.type === 'opening.add' && op.payload.kind === 'door',
    );
    expect(door?.type).toBe('opening.add');
    if (door?.type === 'opening.add') {
      expect(door.payload.swing).toBe('in-left');
      expect(door.payload.offsetMm).toBe(1500);
    }
    const mirrored = mirrorRow?.expectedOps?.find(
      (op) => op.type === 'opening.add' && op.payload.kind === 'door',
    );
    if (mirrored?.type === 'opening.add') {
      expect(mirrored.payload.swing).toBe('out-left');
      expect(mirrored.payload.offsetMm).toBe(1500);
    }
  });
});

// ===========================================================================
// Mirroring a symmetric selection onto itself — the Python twin's
// test_mirroring_a_symmetric_selection_about_its_own_centre_is_refused
// ===========================================================================
describe('mirror onto itself', () => {
  it('refuses a symmetric selection mirrored about its own centre', () => {
    // The default axis runs through the selection's centre, which is the trap. A
    // reflection is never the identity map, so `isIdentityMap` cannot see this —
    // but a symmetric selection is carried onto its own point set, and with
    // `keepOriginal` the copy lands exactly on the original. The fold rejects a
    // duplicate wall; nothing at all forbids two columns at one point.
    const doc = makeTwoRoomPlanWithOpenings();
    for (const axis of ['vertical', 'horizontal'] as const) {
      const result = planMirror(doc, { elementIds: WALLS, groupId: GROUP, axis });
      expect(result.ok).toBe(false);
      if (result.ok) continue;
      expect(result.refusal.reason).toBe('zero-offset');
    }
  });

  it('still allows the same selection mirrored off-centre', () => {
    // The negative control: the guard must refuse the stacking case and ONLY the
    // stacking case. One that refused every mirror would pass the test above
    // while making the feature useless.
    const result = planMirror(makeTwoRoomPlanWithOpenings(), {
      elementIds: WALLS,
      groupId: GROUP,
      axis: 'vertical',
      atMm: 99_000,
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.plan.ops.length).toBeGreaterThan(0);
  });

  it('leaves mirror-in-place alone, symmetric or not', () => {
    // `keepOriginal: false` MOVES the originals — there is no copy to stack, so a
    // symmetric selection must still flip. Guarding it would break the Vastu
    // "flip the plan" gesture.
    const result = planMirror(makeTwoRoomPlanWithOpenings(), {
      elementIds: WALLS,
      groupId: GROUP,
      axis: 'vertical',
      keepOriginal: false,
    });
    expect(result.ok).toBe(true);
  });
});

// The Python twin's test_the_carry_over_refuses_to_overwrite_a_room_that_already_has_a_name.
describe('room metadata carry-over', () => {
  it('refuses to overwrite a room that already has a name', () => {
    // Tested against the internal, because the collision the guard exists for
    // cannot be built through the public API: a paste whose copy lands on an
    // existing room needs walls at the same coordinates, and the fold rejects
    // those as WALL_DUPLICATE first. The guard is defensive; this is the level at
    // which it can actually go red, and it does.
    const ground = fixedId('storey', 'GF2');
    const first = fixedId('storey', 'FF2');
    const square = [
      { x: 0, y: 0 },
      { x: 4000, y: 0 },
      { x: 4000, y: 4000 },
      { x: 0, y: 4000 },
    ] as const;

    const room = (id: string, storeyId: string, name: string, type: string) =>
      ({
        id,
        storeyId,
        type,
        name,
        polygon: square,
        areaMm2: 4000 * 4000,
        tags: [],
        locked: false,
        targetAreaMm2: null,
        mustFace: null,
      }) as never;

    const source = room(fixedId('room', 'SRC2'), ground, 'Living', 'living');
    // Same polygon on the target storey, and ALREADY NAMED by the architect.
    const target = room(fixedId('room', 'TGT2'), first, 'Pooja', 'pooja');

    const base = makeTwoRoomPlan().house;
    const before = { ...base, rooms: [source] } as never;
    const after = { ...base, rooms: [source, target] } as never;

    const result = roomMetadataOps(before, after, ground, first, [IDENTITY_MAP]);
    expect(result.carried).toBe(0);
    expect(result.ops).toEqual([]);
  });
});

// The Python twin's test_the_array_cap_bounds_the_WORK_not_the_instance_count.
describe('array work cap', () => {
  it('bounds the work, not the instance count', () => {
    // MAX_ARRAY_INSTANCES alone let a frozen tab straight through: `buildPlan`
    // folds every emitted op serially on a fork and each `wall.add` re-runs room
    // detection over a growing house, so the cost tracks ELEMENTS. A 20x20 array
    // of a four-wall selection is ~1,600 folds and sat comfortably inside the
    // 400-instance cap — measured at 59.6 s for 396 ops.
    const doc = makeTwoRoomPlanWithOpenings();
    const array = (count: number) =>
      planArray(doc, {
        elementIds: WALLS,
        groupId: GROUP,
        countX: count,
        countY: count,
        spacingXMm: 12_000,
        spacingYMm: 12_000,
      });

    expect(array(3).ok).toBe(true);

    const big = array(20);
    expect(big.ok).toBe(false);
    if (big.ok) return;
    expect(big.refusal.reason).toBe('count-out-of-range');
    expect(big.refusal.message).toContain('elements');
    // The point of the fix: the instance cap alone would NOT have caught it.
    expect(20 * 20).toBeLessThanOrEqual(MAX_ARRAY_INSTANCES);
    expect(MAX_ARRAY_ELEMENTS).toBeLessThan(20 * 20);
  });
});
