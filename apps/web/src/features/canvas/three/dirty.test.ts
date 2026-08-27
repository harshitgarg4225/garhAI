/**
 * dirty.test.ts — the §14 incremental-rebuild contract, pinned PER OP TYPE.
 *
 * `dirty.ts` is signature-based (it reads the folded document, not the op),
 * so the op-level behaviour the spec asks for — "an op dirties only its
 * storey" — is an emergent property. This file is where that property is
 * pinned: every geometry-touching op in the §4 taxonomy is folded through the
 * REAL `applyGroup` onto a real G+1 document, and the resulting `planRebuild`
 * is asserted list-for-list (rebuild + keep + drop — all three, so a test
 * cannot pass by accident of a partition).
 *
 * The cross-storey edges asserted here are the ones fold actually derives:
 *   - a stair cuts its well out of the slab ABOVE (fold's
 *     `markStoreyAboveDirty`), so stair ops dirty the host storey AND the one
 *     above — the "openings on shared boundaries dirty both touching
 *     storeys" rule, materialised where the model actually shares geometry;
 *   - the top storey's envelope feeds the roof group (terrace slab, parapet,
 *     mumty, OHT), so top-storey edits dirty `roof` and lower-storey edits
 *     do not;
 *   - `storey.set_height`/`levels.set plinth` shift FFLs, which every group
 *     reads.
 *
 * And the §8 isolation is pinned from the other side: `facade.apply_kit` and
 * `facade.edit_component` rebuild NOTHING — a facade change must never dirty
 * the plan.
 */

import { describe, expect, it } from 'vitest';

import {
  applyGroup,
  fixedId,
  makeTwoRoomPlanWithOpenings,
  type Op,
  type ProjectDoc,
  type RoomId,
  type WallId,
} from '@garh/model';

import { groupSignatures, planRebuild, storeySignature } from './dirty';
import { ROOF_GROUP_KEY, storeyGroupKey } from './solids';

// ---------------------------------------------------------------------------
// Fixture: a real G+1 — the two-room GF plan plus a walled first floor
// ---------------------------------------------------------------------------

const GF = fixedId('storey', 'GF');
const FF = fixedId('storey', 'FF');
const SF = fixedId('storey', 'SF');

const GF_KEY = storeyGroupKey(GF);
const FF_KEY = storeyGroupKey(FF);
const SF_KEY = storeyGroupKey(SF);

const WALL_SOUTH = fixedId('wall', 'WS');
const WALL_NORTH = fixedId('wall', 'WN');
const WALL_SPINE = fixedId('wall', 'WSP');
const DOOR_MAIN = fixedId('opening', 'D1');
const WINDOW_WEST = fixedId('opening', 'W1');

const FF_SOUTH = fixedId('wall', 'F1');
const FF_EAST = fixedId('wall', 'F2');
const FF_NORTH = fixedId('wall', 'F3');
const FF_WEST = fixedId('wall', 'F4');

function withOps(doc: ProjectDoc, ops: readonly Op[]): ProjectDoc {
  return applyGroup(doc, ops).model;
}

/** GF (two rooms, door, window) + FF with a closed 6000×4000 envelope. */
function gPlusOneDoc(): ProjectDoc {
  const ffWall = (id: WallId, ax: number, ay: number, bx: number, by: number): Op => ({
    type: 'wall.add',
    payload: {
      id,
      storeyId: FF,
      a: { x: ax, y: ay },
      b: { x: bx, y: by },
      thicknessMm: 230,
      kind: 'external',
    },
  });
  return withOps(makeTwoRoomPlanWithOpenings(), [
    { type: 'storey.add', payload: { id: FF, index: 1, name: 'First Floor', heightMm: 3000 } },
    ffWall(FF_SOUTH, 0, 0, 6000, 0),
    ffWall(FF_EAST, 6000, 0, 6000, 4000),
    ffWall(FF_NORTH, 6000, 4000, 0, 4000),
    ffWall(FF_WEST, 0, 4000, 0, 0),
  ]);
}

/** The first derived room on a storey — for room.assign / room.set_target. */
function roomOn(doc: ProjectDoc, storeyId: string): RoomId {
  const room = doc.house.rooms.find((r) => r.storeyId === storeyId);
  if (room === undefined) throw new Error(`fixture has no room on ${storeyId}`);
  return room.id;
}

/**
 * Fold `ops` onto `before` and assert the exact rebuild plan. `keep` is
 * asserted as the complement, so all three lists are pinned every time.
 */
function expectPlan(
  before: ProjectDoc,
  ops: readonly Op[],
  expected: { readonly rebuild: readonly string[]; readonly drop?: readonly string[] },
): ProjectDoc {
  const after = withOps(before, ops);
  const prev = groupSignatures(before.house);
  const next = groupSignatures(after.house);
  const plan = planRebuild(prev, next);

  expect([...plan.rebuild].sort()).toEqual([...expected.rebuild].sort());
  expect([...plan.drop].sort()).toEqual([...(expected.drop ?? [])].sort());
  const expectedKeep = [...next.keys()].filter((k) => !expected.rebuild.includes(k)).sort();
  expect([...plan.keep].sort()).toEqual(expectedKeep);
  return after;
}

// ---------------------------------------------------------------------------
// planRebuild mechanics
// ---------------------------------------------------------------------------

describe('planRebuild mechanics', () => {
  it('first render (no previous signatures) rebuilds every group', () => {
    const doc = gPlusOneDoc();
    const plan = planRebuild(null, groupSignatures(doc.house));
    expect([...plan.rebuild].sort()).toEqual([GF_KEY, FF_KEY, ROOF_GROUP_KEY].sort());
    expect(plan.keep).toEqual([]);
    expect(plan.drop).toEqual([]);
  });

  it('an unchanged document keeps every group', () => {
    const doc = gPlusOneDoc();
    const sigs = groupSignatures(doc.house);
    const plan = planRebuild(sigs, groupSignatures(doc.house));
    expect(plan.rebuild).toEqual([]);
    expect([...plan.keep].sort()).toEqual([GF_KEY, FF_KEY, ROOF_GROUP_KEY].sort());
  });

  it('signatures are deterministic across independent folds of the same ops', () => {
    expect(groupSignatures(gPlusOneDoc().house)).toEqual(groupSignatures(gPlusOneDoc().house));
  });

  it('an unknown storey signs as missing (never collides with a real slice)', () => {
    expect(storeySignature(gPlusOneDoc().house, 'storey_nope')).toBe('missing');
  });
});

// ---------------------------------------------------------------------------
// Wall ops
// ---------------------------------------------------------------------------

describe('wall ops dirty exactly the host storey', () => {
  it('wall.add on GF rebuilds GF only', () => {
    expectPlan(
      gPlusOneDoc(),
      [
        {
          type: 'wall.add',
          payload: {
            id: fixedId('wall', 'WX'),
            storeyId: GF,
            a: { x: 1500, y: 0 },
            b: { x: 1500, y: 4000 },
            thicknessMm: 115,
            kind: 'internal',
          },
        },
      ],
      { rebuild: [GF_KEY] },
    );
  });

  it('wall.move on GF rebuilds GF only — FF and roof keep their meshes', () => {
    expectPlan(
      gPlusOneDoc(),
      [
        {
          type: 'wall.move',
          payload: { wallId: WALL_SPINE, a: { x: 3200, y: 0 }, b: { x: 3200, y: 4000 } },
        },
      ],
      { rebuild: [GF_KEY] },
    );
  });

  it('wall.set_thickness on GF rebuilds GF only', () => {
    expectPlan(
      gPlusOneDoc(),
      [{ type: 'wall.set_thickness', payload: { wallId: WALL_SPINE, thicknessMm: 230 } }],
      { rebuild: [GF_KEY] },
    );
  });

  it('wall.split on GF rebuilds GF only', () => {
    expectPlan(
      gPlusOneDoc(),
      [
        {
          type: 'wall.split',
          payload: { wallId: WALL_NORTH, atMm: 3000, newWallId: fixedId('wall', 'WY') },
        },
      ],
      { rebuild: [GF_KEY] },
    );
  });

  it('wall.delete on GF rebuilds GF only', () => {
    expectPlan(gPlusOneDoc(), [{ type: 'wall.delete', payload: { wallId: WALL_SPINE } }], {
      rebuild: [GF_KEY],
    });
  });

  it('moving the TOP storey envelope rebuilds FF and the roof, never GF', () => {
    // Deepen the FF envelope 4000 → 4500 keeping it closed: the derived FF
    // slab changes, and the roof reads that envelope for its terrace slab,
    // parapet ring and mumty placement.
    expectPlan(
      gPlusOneDoc(),
      [
        {
          type: 'wall.move',
          payload: { wallId: FF_EAST, a: { x: 6000, y: 0 }, b: { x: 6000, y: 4500 } },
        },
        {
          type: 'wall.move',
          payload: { wallId: FF_NORTH, a: { x: 6000, y: 4500 }, b: { x: 0, y: 4500 } },
        },
        {
          type: 'wall.move',
          payload: { wallId: FF_WEST, a: { x: 0, y: 4500 }, b: { x: 0, y: 0 } },
        },
      ],
      { rebuild: [FF_KEY, ROOF_GROUP_KEY] },
    );
  });
});

// ---------------------------------------------------------------------------
// Opening ops
// ---------------------------------------------------------------------------

describe('opening ops dirty the host wall’s storey', () => {
  it('opening.add rebuilds the host storey only', () => {
    expectPlan(
      gPlusOneDoc(),
      [
        {
          type: 'opening.add',
          payload: {
            id: fixedId('opening', 'W2'),
            wallId: WALL_NORTH,
            kind: 'window',
            widthMm: 1200,
            heightMm: 1200,
            sillMm: 900,
            offsetMm: 3000,
            swing: 'in-left',
          },
        },
      ],
      { rebuild: [GF_KEY] },
    );
  });

  it('opening.move rebuilds the host storey only', () => {
    expectPlan(
      gPlusOneDoc(),
      [{ type: 'opening.move', payload: { openingId: DOOR_MAIN, offsetMm: 2000 } }],
      { rebuild: [GF_KEY] },
    );
  });

  it('opening.resize rebuilds the host storey only', () => {
    expectPlan(
      gPlusOneDoc(),
      [{ type: 'opening.resize', payload: { openingId: WINDOW_WEST, widthMm: 1500 } }],
      { rebuild: [GF_KEY] },
    );
  });

  it('opening.delete rebuilds the host storey only', () => {
    expectPlan(gPlusOneDoc(), [{ type: 'opening.delete', payload: { openingId: WINDOW_WEST } }], {
      rebuild: [GF_KEY],
    });
  });

  it('opening.flip rebuilds NOTHING — swing is a 2D symbol, not 3D geometry', () => {
    expectPlan(
      gPlusOneDoc(),
      [{ type: 'opening.flip', payload: { openingId: DOOR_MAIN, swing: 'in-right' } }],
      { rebuild: [] },
    );
  });
});

// ---------------------------------------------------------------------------
// Stairs — the documented cross-storey edge (the well is cut from ABOVE)
// ---------------------------------------------------------------------------

const GF_STAIR: Op = {
  type: 'stair.add',
  payload: {
    id: fixedId('stair', 'ST1'),
    storeyId: GF,
    kind: 'straight',
    origin: { x: 3300, y: 500 },
    direction: 'N',
    riserMm: 150,
    treadMm: 250,
    widthMm: 900,
    risersCount: 20,
    landing: null,
  },
};

describe('stair ops dirty the host storey AND the storey above', () => {
  it('stair.add on GF rebuilds GF (the flight) and FF (its slab gains the well)', () => {
    expectPlan(gPlusOneDoc(), [GF_STAIR], { rebuild: [GF_KEY, FF_KEY] });
  });

  it('stair.edit on a GF stair rebuilds GF and FF', () => {
    const before = withOps(gPlusOneDoc(), [GF_STAIR]);
    expectPlan(
      before,
      [
        {
          type: 'stair.edit',
          payload: { stairId: fixedId('stair', 'ST1'), patch: { widthMm: 1050 } },
        },
      ],
      { rebuild: [GF_KEY, FF_KEY] },
    );
  });

  it('stair.delete on a GF stair rebuilds GF and FF', () => {
    const before = withOps(gPlusOneDoc(), [GF_STAIR]);
    expectPlan(before, [{ type: 'stair.delete', payload: { stairId: fixedId('stair', 'ST1') } }], {
      rebuild: [GF_KEY, FF_KEY],
    });
  });

  it('stair.add on the TOP storey rebuilds FF and the roof (well + mumty)', () => {
    expectPlan(
      gPlusOneDoc(),
      [
        {
          type: 'stair.add',
          payload: {
            id: fixedId('stair', 'ST2'),
            storeyId: FF,
            kind: 'straight',
            origin: { x: 3300, y: 500 },
            direction: 'N',
            riserMm: 150,
            treadMm: 250,
            widthMm: 900,
            risersCount: 20,
            landing: null,
          },
        },
      ],
      { rebuild: [FF_KEY, ROOF_GROUP_KEY] },
    );
  });
});

// ---------------------------------------------------------------------------
// Balconies and columns — single-group elements
// ---------------------------------------------------------------------------

describe('balcony and column ops dirty their one storey', () => {
  it('balcony.set add on GF rebuilds GF only', () => {
    expectPlan(
      gPlusOneDoc(),
      [
        {
          type: 'balcony.set',
          payload: {
            action: 'add',
            id: fixedId('balcony', 'B1'),
            storeyId: GF,
            polygon: [
              { x: 1000, y: -900 },
              { x: 2500, y: -900 },
              { x: 2500, y: 0 },
              { x: 1000, y: 0 },
            ],
            railingKind: 'glass',
            railingHeightMm: 1000,
            projectionMm: 900,
            slabThicknessMm: 150,
          },
        },
      ],
      { rebuild: [GF_KEY] },
    );
  });

  it('column.set add on GF rebuilds GF only', () => {
    expectPlan(
      gPlusOneDoc(),
      [
        {
          type: 'column.set',
          payload: {
            action: 'add',
            id: fixedId('column', 'C1'),
            storeyId: GF,
            pt: { x: 4500, y: 2000 },
            sizeMm: { xMm: 230, yMm: 230 },
          },
        },
      ],
      { rebuild: [GF_KEY] },
    );
  });
});

// ---------------------------------------------------------------------------
// Storey and levels ops — the FFL cascade
// ---------------------------------------------------------------------------

describe('storey and levels ops', () => {
  it('storey.set_height on GF shifts every FFL above it: all groups rebuild', () => {
    expectPlan(
      gPlusOneDoc(),
      [{ type: 'storey.set_height', payload: { storeyId: GF, heightMm: 3200 } }],
      {
        rebuild: [GF_KEY, FF_KEY, ROOF_GROUP_KEY],
      },
    );
  });

  it('storey.add on top creates its group, retops the roof, retops FF — GF keeps', () => {
    expectPlan(
      gPlusOneDoc(),
      [{ type: 'storey.add', payload: { id: SF, index: 2, name: 'Second Floor', heightMm: 3000 } }],
      { rebuild: [FF_KEY, SF_KEY, ROOF_GROUP_KEY] },
    );
  });

  it('storey.remove drops its cached group and rebuilds FF + roof', () => {
    const withSecond = withOps(gPlusOneDoc(), [
      { type: 'storey.add', payload: { id: SF, index: 2, name: 'Second Floor', heightMm: 3000 } },
    ]);
    expectPlan(withSecond, [{ type: 'storey.remove', payload: { index: 2 } }], {
      rebuild: [FF_KEY, ROOF_GROUP_KEY],
      drop: [SF_KEY],
    });
  });

  it('levels.set plinthMm re-derives every FFL: all groups rebuild', () => {
    expectPlan(gPlusOneDoc(), [{ type: 'levels.set', payload: { plinthMm: 750 } }], {
      rebuild: [GF_KEY, FF_KEY, ROOF_GROUP_KEY],
    });
  });

  it('levels.set parapetMm rebuilds the roof only', () => {
    expectPlan(gPlusOneDoc(), [{ type: 'levels.set', payload: { parapetMm: 1200 } }], {
      rebuild: [ROOF_GROUP_KEY],
    });
  });

  it('levels.set sillDefaultMm rebuilds nothing — openings carry their own sill', () => {
    expectPlan(gPlusOneDoc(), [{ type: 'levels.set', payload: { sillDefaultMm: 1050 } }], {
      rebuild: [],
    });
  });
});

// ---------------------------------------------------------------------------
// §8 facade isolation — pinned from the dirty-tracking side
// ---------------------------------------------------------------------------

describe('facade ops never dirty the plan (§8 isolation)', () => {
  const APPLY_KIT: Op = {
    type: 'facade.apply_kit',
    payload: {
      kitId: 'contemporary',
      seed: 7,
      colorwayId: null,
      components: [
        {
          id: fixedId('facadecomp', 'FC1'),
          kind: 'chajja',
          storeyId: GF,
          wallId: WALL_SOUTH,
          openingId: DOOR_MAIN,
          params: { projectionMm: 600 },
        },
      ],
    },
  };

  it('facade.apply_kit rebuilds NOTHING', () => {
    expectPlan(gPlusOneDoc(), [APPLY_KIT], { rebuild: [] });
  });

  it('facade.edit_component rebuilds NOTHING', () => {
    const before = withOps(gPlusOneDoc(), [APPLY_KIT]);
    expectPlan(
      before,
      [
        {
          type: 'facade.edit_component',
          payload: { componentId: fixedId('facadecomp', 'FC1'), patch: { projectionMm: 750 } },
        },
      ],
      { rebuild: [] },
    );
  });
});

// ---------------------------------------------------------------------------
// material.assign (op 29) — element scope splits buckets, wider scopes do not
// ---------------------------------------------------------------------------

describe('material.assign', () => {
  it('a building-wide assignment rebuilds nothing (colour resolves at render time)', () => {
    expectPlan(
      gPlusOneDoc(),
      [
        {
          type: 'material.assign',
          payload: {
            id: fixedId('material', 'M1'),
            target: { group: 'external_wall', storeyId: null, elementId: null },
            materialId: 'texture-paint-grey',
          },
        },
      ],
      { rebuild: [] },
    );
  });

  it('a storey-scoped assignment rebuilds nothing', () => {
    expectPlan(
      gPlusOneDoc(),
      [
        {
          type: 'material.assign',
          payload: {
            id: fixedId('material', 'M2'),
            target: { group: 'external_wall', storeyId: GF, elementId: null },
            materialId: 'texture-paint-grey',
          },
        },
      ],
      { rebuild: [] },
    );
  });

  it('an ELEMENT-scoped assignment rebuilds the host group only (bucket split)', () => {
    expectPlan(
      gPlusOneDoc(),
      [
        {
          type: 'material.assign',
          payload: {
            id: fixedId('material', 'M3'),
            target: { group: 'external_wall', storeyId: null, elementId: WALL_SOUTH },
            materialId: 'exposed-brick',
          },
        },
      ],
      { rebuild: [GF_KEY] },
    );
  });
});

// ---------------------------------------------------------------------------
// Non-geometry ops — and the one honest exception (the OHT reads shaft rooms)
// ---------------------------------------------------------------------------

describe('non-geometry ops rebuild nothing', () => {
  it('room.assign (name/type) rebuilds nothing', () => {
    const doc = gPlusOneDoc();
    expectPlan(
      doc,
      [
        {
          type: 'room.assign',
          payload: { roomId: roomOn(doc, GF), type: 'bedroom', name: 'Bed 1' },
        },
      ],
      { rebuild: [] },
    );
  });

  it('room.assign to shaft on a LOWER storey still rebuilds nothing', () => {
    const doc = gPlusOneDoc();
    expectPlan(
      doc,
      [{ type: 'room.assign', payload: { roomId: roomOn(doc, GF), type: 'shaft' } }],
      {
        rebuild: [],
      },
    );
  });

  it('room.assign to shaft on the TOP storey rebuilds the roof — the OHT appears', () => {
    const doc = gPlusOneDoc();
    expectPlan(
      doc,
      [{ type: 'room.assign', payload: { roomId: roomOn(doc, FF), type: 'shaft' } }],
      {
        rebuild: [ROOF_GROUP_KEY],
      },
    );
  });

  it('room.set_target rebuilds nothing', () => {
    const doc = gPlusOneDoc();
    expectPlan(
      doc,
      [
        {
          type: 'room.set_target',
          payload: { roomId: roomOn(doc, GF), targetAreaMm2: 12_000_000 },
        },
      ],
      { rebuild: [] },
    );
  });

  it('furniture.set rebuilds nothing', () => {
    expectPlan(
      gPlusOneDoc(),
      [
        {
          type: 'furniture.set',
          payload: {
            action: 'place',
            id: fixedId('furniture', 'FS1'),
            storeyId: GF,
            catalogId: 'bed-queen-1900x1525',
            pt: { x: 2000, y: 2000 },
            rotationDeg: 90,
          },
        },
      ],
      { rebuild: [] },
    );
  });

  it('plot ops rebuild nothing (the house document is untouched)', () => {
    expectPlan(gPlusOneDoc(), [{ type: 'plot.set_north', payload: { deg: 90 } }], { rebuild: [] });
  });
});
