/**
 * THE "IT ACTUALLY DRAWS LESS" GATE.
 *
 * A layer panel whose switches only move booleans is the exact failure this
 * repository has shipped before — a gate that silently never fires. So nothing
 * here asserts on the store. Every assertion goes through the plan's OWN
 * geometry selectors, the ones `PlanScene` calls to decide what to put in its
 * merged buffers:
 *
 *     wallsOfStorey · openingsOfStorey · stairsOfStorey
 *     columnsOfStorey · balconiesOfStorey
 *
 * imported from `pages/project/plan` — not reimplemented here. If those change,
 * this test changes with them, because it is measuring the real thing.
 *
 * `drawSet()` below is therefore a faithful summary of what the canvas would
 * put on screen for a given layer state: the element ids that reach the merged
 * geometry builders, plus the three `visible` props `PlanPage` passes to the
 * room fill, the dimension layer and the room-tag layer.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE COVERAGE LOOP IS THE POINT
 * ════════════════════════════════════════════════════════════════════════════
 * `every layer that claims canvas presence changes the draw set` walks all nine
 * specs. A layer marked `onCanvas: true` whose hiding does not change the draw
 * set fails. A layer marked `onCanvas: false` whose hiding DOES change it also
 * fails. So a tenth layer added to `layers.py` and mirrored here cannot be
 * left unwired without going red, and A-TITL cannot quietly acquire a canvas
 * effect the panel says it does not have.
 *
 * The fixture is asserted to contain at least one element on every canvas layer
 * BEFORE the loop runs. Without that, "hiding A-STAIR changed nothing" would
 * pass on a plan with no stairs — a test that cannot fail.
 */

import { describe, expect, it } from 'vitest';

import {
  applyGroup,
  makeTwoRoomPlanWithOpenings,
  DEFAULTS,
  FIXTURE_IDS,
  fixedId,
  type HouseModel,
  type ProjectDoc,
} from '@garh/model';

import {
  balconiesOfStorey,
  columnsOfStorey,
  openingsOfStorey,
  stairsOfStorey,
  wallsOfStorey,
  // The module itself, not the barrel: the barrel re-exports `PlanScene`,
  // which drags three.js and react-three-fiber into a test that needs neither.
} from '../../pages/project/plan/planGeometry';
import { DRAWING_LAYER_NAMES, DRAWING_LAYER_SPECS, type DrawingLayerName } from './layerSpecs';
import {
  blockedPicks,
  filterHouseByLayers,
  layerOfOpening,
  layerOfWall,
  resolvePlanLayerView,
  type LayerFlags,
} from './mapping';
import { allLayers } from './persist';

// ---------------------------------------------------------------------------
// A fixture with something on every canvas layer
// ---------------------------------------------------------------------------

const STOREY = FIXTURE_IDS.groundStorey;
const PARAPET = fixedId('wall', 'WPAR');

/**
 * The shared two-room plan (5 walls, a door, a window, 2 rooms) plus the four
 * things it lacks: a parapet wall, a stair, a column and a balcony. Every one
 * of the eight canvas layers therefore has at least one element, which the
 * first test below verifies rather than assumes.
 */
function fixture(): HouseModel {
  const base: ProjectDoc = makeTwoRoomPlanWithOpenings();
  return applyGroup(base, [
    {
      type: 'wall.add',
      payload: {
        id: PARAPET,
        storeyId: STOREY,
        // Clear of the two-room block (which ends at y = 4000) — a parapet
        // along the same line as the north wall is a WALL_DUPLICATE.
        a: { x: 0, y: 6000 },
        b: { x: 6000, y: 6000 },
        thicknessMm: 115,
        kind: 'parapet',
      },
    },
    {
      type: 'stair.add',
      payload: {
        id: FIXTURE_IDS.stair,
        storeyId: STOREY,
        kind: 'straight',
        origin: { x: 1000, y: 1000 },
        direction: 'N',
        riserMm: 150,
        treadMm: 275,
        widthMm: 1000,
        risersCount: Math.round(DEFAULTS.storeyHeightMm / 150),
        landing: null,
      },
    },
    {
      type: 'column.set',
      payload: {
        action: 'add',
        id: FIXTURE_IDS.column,
        storeyId: STOREY,
        pt: { x: 3000, y: 2000 },
        sizeMm: { xMm: 230, yMm: 230 },
      },
    },
    {
      type: 'balcony.set',
      payload: {
        action: 'add',
        id: FIXTURE_IDS.balcony,
        storeyId: STOREY,
        polygon: [
          { x: 0, y: 4000 },
          { x: 2400, y: 4000 },
          { x: 2400, y: 4900 },
          { x: 0, y: 4900 },
        ],
        railingKind: 'ms',
        railingHeightMm: 1000,
        projectionMm: 900,
      },
    },
  ]).model.house;
}

const HOUSE = fixture();

// ---------------------------------------------------------------------------
// What the canvas would draw
// ---------------------------------------------------------------------------

interface DrawSet {
  readonly wallIds: readonly string[];
  readonly openingIds: readonly string[];
  readonly stairIds: readonly string[];
  readonly columnIds: readonly string[];
  readonly balconyIds: readonly string[];
  readonly showRooms: boolean;
  readonly showDimensions: boolean;
  readonly showRoomTags: boolean;
}

/**
 * Run the layer state through `resolvePlanLayerView` and then through the
 * plan's real selectors — i.e. do exactly what `PlanScene` does before it
 * packs a triangle.
 */
function drawSet(visible: LayerFlags): DrawSet {
  const view = resolvePlanLayerView(HOUSE, visible);
  return {
    wallIds: wallsOfStorey(view.house, STOREY).map((w) => w.id),
    openingIds: openingsOfStorey(view.house, STOREY).map((p) => p.opening.id),
    stairIds: stairsOfStorey(view.house, STOREY).map((s) => s.id),
    columnIds: columnsOfStorey(view.house, STOREY).map((c) => c.id),
    balconyIds: balconiesOfStorey(view.house, STOREY).map((b) => b.id),
    showRooms: view.showRooms,
    showDimensions: view.showDimensions,
    showRoomTags: view.showRoomTags,
  };
}

const ALL_ON = allLayers(true);
const BASELINE = drawSet(ALL_ON);

function hidingOnly(layer: DrawingLayerName): LayerFlags {
  return { ...ALL_ON, [layer]: false };
}

// ---------------------------------------------------------------------------
// The fixture must be able to fail
// ---------------------------------------------------------------------------

describe('the fixture (so no assertion below can pass vacuously)', () => {
  it('has at least one element on every layer the plan draws', () => {
    const present: Record<string, number> = {
      'A-WALL': HOUSE.walls.filter((w) => layerOfWall(w) === 'A-WALL').length,
      'A-WALL-PART':
        HOUSE.walls.filter((w) => layerOfWall(w) === 'A-WALL-PART').length +
        HOUSE.columns.length +
        HOUSE.balconies.length,
      'A-DOOR': HOUSE.openings.filter((o) => layerOfOpening(o) === 'A-DOOR').length,
      'A-WIND': HOUSE.openings.filter((o) => layerOfOpening(o) === 'A-WIND').length,
      'A-STAIR': HOUSE.stairs.length,
      // A-AREA / A-TEXT / A-DIM are overlay props rather than model elements,
      // but they still need something to be about: rooms, and walls to dimension.
      'A-AREA': HOUSE.rooms.length,
      'A-TEXT': HOUSE.rooms.length,
      'A-DIM': HOUSE.walls.length,
    };
    for (const [layer, count] of Object.entries(present)) {
      expect(count, `fixture has nothing on ${layer}`).toBeGreaterThan(0);
    }
  });

  it('has a parapet, a column, a balcony and a stair — the four the shared plan lacks', () => {
    expect(HOUSE.walls.some((w) => w.kind === 'parapet')).toBe(true);
    expect(HOUSE.columns).toHaveLength(1);
    expect(HOUSE.balconies).toHaveLength(1);
    expect(HOUSE.stairs).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// THE COVERAGE GATE
// ---------------------------------------------------------------------------

describe('hiding a layer changes what the canvas would draw', () => {
  it('every layer that claims canvas presence changes the draw set', () => {
    for (const spec of DRAWING_LAYER_SPECS) {
      const after = drawSet(hidingOnly(spec.name));
      if (spec.onCanvas) {
        expect(
          after,
          `${spec.name} is declared onCanvas but hiding it draws exactly the same plan — ` +
            'either wire it up or mark it sheet-only in layerSpecs.ts',
        ).not.toEqual(BASELINE);
      } else {
        expect(after, `${spec.name} is declared sheet-only but hiding it changed the plan`).toEqual(
          BASELINE,
        );
      }
    }
  });

  it('covers every one of the nine layers', () => {
    // Guards the loop above against a spec list that quietly shrank.
    expect(DRAWING_LAYER_SPECS.map((s) => s.name)).toEqual([...DRAWING_LAYER_NAMES]);
    expect(DRAWING_LAYER_SPECS.length).toBe(9);
  });
});

describe('hiding a layer removes exactly that layer', () => {
  it('A-WALL drops full-height walls and keeps the parapet', () => {
    const after = drawSet(hidingOnly('A-WALL'));
    expect(after.wallIds).toEqual([PARAPET]);
  });

  it('A-WALL-PART drops the parapet, the column and the balcony, keeping the walls', () => {
    const after = drawSet(hidingOnly('A-WALL-PART'));
    expect(after.wallIds).not.toContain(PARAPET);
    expect(after.wallIds).toContain(FIXTURE_IDS.wallSouth);
    expect(after.columnIds).toEqual([]);
    expect(after.balconyIds).toEqual([]);
  });

  it('A-DOOR drops the door and keeps the window', () => {
    const after = drawSet(hidingOnly('A-DOOR'));
    expect(after.openingIds).toEqual([FIXTURE_IDS.windowWest]);
  });

  it('A-WIND drops the window and keeps the door', () => {
    const after = drawSet(hidingOnly('A-WIND'));
    expect(after.openingIds).toEqual([FIXTURE_IDS.doorMain]);
  });

  it('A-STAIR drops the stair only', () => {
    const after = drawSet(hidingOnly('A-STAIR'));
    expect(after.stairIds).toEqual([]);
    expect(after.wallIds).toEqual(BASELINE.wallIds);
  });

  it('A-AREA, A-TEXT and A-DIM switch their overlay props and nothing else', () => {
    expect(drawSet(hidingOnly('A-AREA'))).toEqual({ ...BASELINE, showRooms: false });
    expect(drawSet(hidingOnly('A-TEXT'))).toEqual({ ...BASELINE, showRoomTags: false });
    expect(drawSet(hidingOnly('A-DIM'))).toEqual({ ...BASELINE, showDimensions: false });
  });

  it('hiding a wall takes its openings with it — no orphan door in mid-air', () => {
    // The main door is hosted by the south wall. Hiding only A-WALL must not
    // leave a door drawn where its host is not.
    const after = drawSet(hidingOnly('A-WALL'));
    expect(after.openingIds).toEqual([]);
  });
});

describe('the default state costs nothing (§14)', () => {
  it('returns the same model object when every layer is on', () => {
    // PlanScene memoises every merged buffer on `house` identity. A copy here
    // would rebuild every vertex buffer on every render.
    expect(filterHouseByLayers(HOUSE, ALL_ON)).toBe(HOUSE);
    expect(resolvePlanLayerView(HOUSE, ALL_ON).house).toBe(HOUSE);
  });

  it('returns a different model as soon as something is hidden', () => {
    expect(filterHouseByLayers(HOUSE, hidingOnly('A-STAIR'))).not.toBe(HOUSE);
  });
});

// ---------------------------------------------------------------------------
// Element → layer, mirroring the Python projection rules
// ---------------------------------------------------------------------------

describe('element → layer', () => {
  it('puts parapets on A-WALL-PART and every other wall on A-WALL (walls.py:351)', () => {
    for (const wall of HOUSE.walls) {
      expect(layerOfWall(wall)).toBe(wall.kind === 'parapet' ? 'A-WALL-PART' : 'A-WALL');
    }
  });

  it('puts doors on A-DOOR and everything else glazed on A-WIND (walls.py:490)', () => {
    for (const opening of HOUSE.openings) {
      expect(layerOfOpening(opening)).toBe(opening.kind === 'door' ? 'A-DOOR' : 'A-WIND');
    }
    // A ventilator is the third OpeningKind and must land on A-WIND, not on a
    // hole in a lookup table.
    const ventilator = { ...(HOUSE.openings[0] as (typeof HOUSE.openings)[number]) };
    expect(layerOfOpening({ ...ventilator, kind: 'ventilator' })).toBe('A-WIND');
  });
});

// ---------------------------------------------------------------------------
// blockedPicks — what the gate will refuse
// ---------------------------------------------------------------------------

describe('blockedPicks', () => {
  const NONE = allLayers(false);

  it('blocks nothing when everything is visible and unlocked', () => {
    const block = blockedPicks(HOUSE, ALL_ON, NONE);
    expect(block.ids.size).toBe(0);
    expect(block.kinds.size).toBe(0);
  });

  it('blocks a locked layer element by element', () => {
    const block = blockedPicks(HOUSE, ALL_ON, { ...NONE, 'A-WALL': true });
    const fullHeight = HOUSE.walls.filter((w) => layerOfWall(w) === 'A-WALL');
    for (const wall of fullHeight) expect(block.ids.has(wall.id)).toBe(true);
    expect(block.ids.has(PARAPET)).toBe(false);
  });

  it('blocks a hidden layer too, so nothing invisible stays clickable', () => {
    const block = blockedPicks(HOUSE, hidingOnly('A-STAIR'), NONE);
    expect(block.ids.has(FIXTURE_IDS.stair)).toBe(true);
  });

  it('takes columns and balconies with the parapets on A-WALL-PART', () => {
    const block = blockedPicks(HOUSE, ALL_ON, { ...NONE, 'A-WALL-PART': true });
    expect(block.ids.has(PARAPET)).toBe(true);
    expect(block.ids.has(FIXTURE_IDS.column)).toBe(true);
    expect(block.ids.has(FIXTURE_IDS.balcony)).toBe(true);
  });

  it('blocks dimension picks by kind — a dimension segment id is synthetic', () => {
    expect(blockedPicks(HOUSE, ALL_ON, { ...NONE, 'A-DIM': true }).kinds.has('dimension')).toBe(
      true,
    );
  });

  it('blocks room picks when EITHER A-AREA or A-TEXT is locked', () => {
    // RoomTagLayer resolves its labels to the same `{ kind: "room", id }` the
    // room wash does, so the two layers cannot be told apart at the registry.
    // Over-refusing is the safe direction for a lock.
    expect(blockedPicks(HOUSE, ALL_ON, { ...NONE, 'A-AREA': true }).kinds.has('room')).toBe(true);
    expect(blockedPicks(HOUSE, ALL_ON, { ...NONE, 'A-TEXT': true }).kinds.has('room')).toBe(true);
    expect(blockedPicks(HOUSE, ALL_ON, NONE).kinds.has('room')).toBe(false);
  });

  it('never blocks furniture — it has no §7 layer and this panel does not own it', () => {
    const block = blockedPicks(HOUSE, allLayers(false), allLayers(true));
    for (const item of HOUSE.furniture) expect(block.ids.has(item.id)).toBe(false);
    expect(block.kinds.has('furniture')).toBe(false);
  });
});
