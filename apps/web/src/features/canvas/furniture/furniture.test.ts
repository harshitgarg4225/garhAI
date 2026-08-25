/**
 * Pure-logic spec for the furniture feature.
 *
 * The op tests fold through the REAL model core (`applyGroup` from
 * `@garh/model`), not a stand-in: if `furniture.set`'s payload contract or its
 * inverse ever changes, this fails here rather than in a demo. The geometry
 * tests pin the two numbers that would be invisible until a drawing is wrong —
 * that rotation stays an integer, and that a clearance rectangle sits exactly
 * in front of its item with no gap and no overlap.
 *
 * No DOM, no renderer, no network. Everything under test is a function.
 */

import { describe, expect, it } from 'vitest';

import {
  applyGroup,
  FIXTURE_IDS,
  makeTwoRoomPlan,
  polygonAreaMm2,
  type Op,
  type Pt,
} from '@garh/model';

import {
  CLEARANCE_FALLBACK_MM,
  filterByRoomType,
  groupByCategory,
  searchItems,
  toCatalogueItem,
  toCatalogue,
} from './catalogue';
import {
  buildPlacementContext,
  evaluatePlacement,
  issueTone,
  type PlacementContext,
} from './collision';
import {
  angleFromDrag,
  bounds2x,
  clearanceQuad2x,
  cornersToMm,
  footprintQuad2x,
  normaliseRotationDeg,
  occupancyQuad2x,
  quadsOverlap,
  rotateBy,
  snapPtMm,
  wallQuad2x,
  type WallLike,
} from './geometry';
import {
  deleteFurnitureOp,
  placeFurnitureOp,
  transformFurnitureOp,
} from './ops';
import { PlacementController, suggestRotationDeg } from './placement';
import { boxProxyFor } from './proxyMesh';
import { buildBoxInstances, clearanceRingMm, footprintRingMm } from './render';
import type { CatalogueItem, PlacedFurniture, RoomLike } from './types';

// ---------------------------------------------------------------------------
// Fixtures — real dimensions from fixtures/catalog/furniture.json
// ---------------------------------------------------------------------------

const QUEEN_BED: CatalogueItem = toCatalogueItem({
  id: 'bed-queen',
  name: 'Queen bed',
  category: 'bed',
  widthMm: 1525,
  depthMm: 1900,
  heightMm: 600,
  roomTypes: ['bedroom', 'bedroom_master', 'guest_bedroom'],
  clearanceMm: 600,
});

const WARDROBE: CatalogueItem = toCatalogueItem({
  id: 'wardrobe-2door',
  name: 'Wardrobe (2 door)',
  category: 'storage',
  widthMm: 1200,
  depthMm: 600,
  heightMm: 2100,
  roomTypes: ['bedroom', 'guest_bedroom', 'dress'],
  clearanceMm: 750,
});

const WALL_SHELF: CatalogueItem = toCatalogueItem({
  id: 'wall-shelf',
  name: 'Wall shelf',
  category: 'storage',
  widthMm: 750,
  depthMm: 250,
  heightMm: 300,
  roomTypes: ['living', 'study'],
  clearanceMm: 0,
});

const FURNITURE_ID = FIXTURE_IDS.sofa;
const STOREY_ID = FIXTURE_IDS.groundStorey;

function at(x: number, y: number): Pt {
  return { x, y };
}

/** Op payloads are a union across 32 op types; read them as plain records. */
function payloadOf(op: Op | undefined): Record<string, unknown> {
  return (op?.payload ?? {}) as Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// 1. Op payloads — folded through the real model core
// ---------------------------------------------------------------------------

describe('furniture op payloads', () => {
  it('places an instance the fold accepts, with integer mm and integer degrees', () => {
    const doc = makeTwoRoomPlan();
    const op = placeFurnitureOp({
      id: FURNITURE_ID,
      storeyId: STOREY_ID,
      catalogId: QUEEN_BED.id,
      pose: { pt: at(1495, 2300), rotationDeg: 90 },
    });

    const result = applyGroup(doc, [op]);
    const placed = result.model.house.furniture;

    expect(placed).toHaveLength(1);
    expect(placed[0]).toMatchObject({
      id: FURNITURE_ID,
      storeyId: STOREY_ID,
      catalogId: 'bed-queen',
      pt: { x: 1495, y: 2300 },
      rotationDeg: 90,
    });
  });

  it('rounds a float pose to whole millimetres before it can reach a payload', () => {
    const op = placeFurnitureOp({
      id: FURNITURE_ID,
      storeyId: STOREY_ID,
      catalogId: QUEEN_BED.id,
      // A raw pointer position, exactly as a camera unprojection produces it.
      pose: { pt: at(1494.6, 2300.4), rotationDeg: 90.7 },
    });
    const payload = payloadOf(op);
    const pt = payload.pt as Pt;

    expect(Number.isInteger(pt.x)).toBe(true);
    expect(Number.isInteger(pt.y)).toBe(true);
    expect(pt).toEqual({ x: 1495, y: 2300 });
    expect(payload.rotationDeg).toBe(91);
  });

  it('normalises a negative or over-turned angle into the op contract [0, 360)', () => {
    const back = transformFurnitureOp(FURNITURE_ID, { pt: at(0, 0), rotationDeg: -90 });
    const round = transformFurnitureOp(FURNITURE_ID, { pt: at(0, 0), rotationDeg: 450 });

    expect(payloadOf(back).rotationDeg).toBe(270);
    expect(payloadOf(round).rotationDeg).toBe(90);
  });

  it('transforms an existing instance and leaves everything else alone', () => {
    const base = applyGroup(makeTwoRoomPlan(), [
      placeFurnitureOp({
        id: FURNITURE_ID,
        storeyId: STOREY_ID,
        catalogId: WARDROBE.id,
        pose: { pt: at(1000, 1000), rotationDeg: 0 },
      }),
    ]).model;

    const moved = applyGroup(base, [
      transformFurnitureOp(FURNITURE_ID, { pt: at(2150, 1495), rotationDeg: 180 }),
    ]).model;

    expect(moved.house.furniture[0]).toMatchObject({
      pt: { x: 2150, y: 1495 },
      rotationDeg: 180,
      catalogId: 'wardrobe-2door',
      storeyId: STOREY_ID,
    });
    expect(moved.house.walls).toHaveLength(base.house.walls.length);
  });

  it('deletes, and the fold-supplied inverse puts the item back unchanged', () => {
    const base = applyGroup(makeTwoRoomPlan(), [
      placeFurnitureOp({
        id: FURNITURE_ID,
        storeyId: STOREY_ID,
        catalogId: WARDROBE.id,
        pose: { pt: at(1000, 1000), rotationDeg: 270 },
      }),
    ]).model;

    const removal = applyGroup(base, [deleteFurnitureOp(FURNITURE_ID)]);
    expect(removal.model.house.furniture).toHaveLength(0);

    const undone = applyGroup(removal.model, removal.inverse).model;
    expect(undone.house.furniture[0]).toMatchObject({
      id: FURNITURE_ID,
      pt: { x: 1000, y: 1000 },
      rotationDeg: 270,
      catalogId: 'wardrobe-2door',
    });
  });
});

// ---------------------------------------------------------------------------
// 2. Rotation math stays integer
// ---------------------------------------------------------------------------

describe('rotation math', () => {
  it('normalises anything to an integer in [0, 360)', () => {
    for (const [input, expected] of [
      [0, 0],
      [90, 90],
      [360, 0],
      [-90, 270],
      [-450, 270],
      [720, 0],
      [44.6, 45],
      [-0.4, 0],
    ] as const) {
      const out = normaliseRotationDeg(input);
      expect(Number.isInteger(out)).toBe(true);
      expect(out).toBe(expected);
      expect(out).toBeGreaterThanOrEqual(0);
      expect(out).toBeLessThan(360);
    }
  });

  it('returns 0 rather than NaN for a degenerate angle', () => {
    expect(normaliseRotationDeg(Number.NaN)).toBe(0);
    expect(normaliseRotationDeg(Number.POSITIVE_INFINITY)).toBe(0);
  });

  it('R four times is a full turn, in either direction', () => {
    let deg = 0;
    for (let i = 0; i < 4; i += 1) deg = rotateBy(deg, 90);
    expect(deg).toBe(0);

    let back = 0;
    for (let i = 0; i < 4; i += 1) back = rotateBy(back, -90);
    expect(back).toBe(0);
  });

  it('free rotation from a drag is an integer, and honours a coarse step', () => {
    const centre = at(0, 0);
    expect(angleFromDrag(centre, at(1000, 0))).toBe(0);
    expect(angleFromDrag(centre, at(0, 1000))).toBe(90);
    expect(angleFromDrag(centre, at(-1000, 0))).toBe(180);
    expect(angleFromDrag(centre, at(0, -1000))).toBe(270);

    const odd = angleFromDrag(centre, at(1000, 371));
    expect(Number.isInteger(odd)).toBe(true);
    expect(odd).toBe(20);

    // Shift → 15° steps, still integer.
    expect(angleFromDrag(centre, at(1000, 371), 15)).toBe(15);
    expect(angleFromDrag(centre, centre)).toBe(0);
  });

  it('suggests a right angle when a wall is nearby, never an odd one', () => {
    const room: Pt[] = [at(0, 0), at(4000, 0), at(4000, 3000), at(0, 3000)];
    for (const p of [at(2000, 200), at(3800, 1500), at(2000, 2800), at(200, 1500)]) {
      const deg = suggestRotationDeg(p, room);
      expect(deg % 90).toBe(0);
      expect(Number.isInteger(deg)).toBe(true);
    }
    expect(suggestRotationDeg(at(0, 0), null)).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// 3. Footprint and clearance rectangles
// ---------------------------------------------------------------------------

describe('footprint and clearance geometry', () => {
  it('holds an odd-width item exactly, in the doubled-mm space', () => {
    // 1525 mm halves to 762.5 — the case that forces the doubled-mm space.
    const b = bounds2x(footprintQuad2x(QUEEN_BED, { pt: at(0, 0), rotationDeg: 0 }));
    expect(b.maxX - b.minX).toBe(QUEEN_BED.widthMm * 2);
    expect(b.maxY - b.minY).toBe(QUEEN_BED.depthMm * 2);
    // Exact, so collision needs no tolerance constant anywhere.
    expect(b.minX).toBe(-QUEEN_BED.widthMm);
    expect(b.maxX).toBe(QUEEN_BED.widthMm);
  });

  it('rounds an odd half-millimetre OUTWARD when converting corners to mm', () => {
    // Documented behaviour, not an accident: the millimetre ring is for drawing
    // and for tests, and rounding outward keeps it conservative — it can look a
    // hair large, never a hair small.
    const mm = cornersToMm(footprintQuad2x(QUEEN_BED, { pt: at(0, 0), rotationDeg: 0 }));
    const xs = mm.map((p) => p.x);
    expect(Math.min(...xs)).toBe(-763);
    expect(Math.max(...xs)).toBe(763);
    expect(Math.max(...xs) - Math.min(...xs) - QUEEN_BED.widthMm).toBe(1);
  });

  it('rotates the footprint exactly at every right angle', () => {
    const upright = bounds2x(footprintQuad2x(QUEEN_BED, { pt: at(5000, 5000), rotationDeg: 0 }));
    const turned = bounds2x(footprintQuad2x(QUEEN_BED, { pt: at(5000, 5000), rotationDeg: 90 }));

    expect(upright.maxX - upright.minX).toBe(QUEEN_BED.widthMm * 2);
    expect(upright.maxY - upright.minY).toBe(QUEEN_BED.depthMm * 2);
    // 90° swaps the extents, with no half-millimetre left behind.
    expect(turned.maxX - turned.minX).toBe(QUEEN_BED.depthMm * 2);
    expect(turned.maxY - turned.minY).toBe(QUEEN_BED.widthMm * 2);
  });

  it('puts the clearance strip in front, touching the footprint but not inside it', () => {
    const pose = { pt: at(0, 0), rotationDeg: 0 };
    const foot = footprintQuad2x(WARDROBE, pose);
    const strip = clearanceQuad2x(WARDROBE, pose);
    expect(strip).not.toBeNull();
    if (strip === null) return;

    const footB = bounds2x(foot);
    const stripB = bounds2x(strip);

    // Same width, sitting on the +Y (front) edge, exactly `clearance` deep.
    expect(stripB.minX).toBe(footB.minX);
    expect(stripB.maxX).toBe(footB.maxX);
    expect(stripB.minY).toBe(footB.maxY);
    expect(stripB.maxY - stripB.minY).toBe(WARDROBE.clearanceMm * 2);

    // Touching is not overlapping — a wardrobe against a wall is correct work.
    expect(quadsOverlap(foot, strip)).toBe(false);
  });

  it('rotates the clearance strip with the item', () => {
    const pose = { pt: at(0, 0), rotationDeg: 90 };
    const strip = clearanceQuad2x(WARDROBE, pose);
    if (strip === null) throw new Error('expected a clearance strip');
    const b = bounds2x(strip);

    // Front is now −X: 90° CCW turns +Y into −X.
    expect(b.maxX).toBe(-WARDROBE.depthMm);
    expect(b.maxX - b.minX).toBe(WARDROBE.clearanceMm * 2);
    expect(b.maxY - b.minY).toBe(WARDROBE.widthMm * 2);
  });

  it('has no strip for an item that needs no access space', () => {
    expect(clearanceQuad2x(WALL_SHELF, { pt: at(0, 0), rotationDeg: 0 })).toBeNull();
    expect(clearanceRingMm(WALL_SHELF, { pt: at(0, 0), rotationDeg: 0 })).toBeNull();
  });

  it('matches the solver: occupancy is width × (depth + clearance)', () => {
    // services/solver/furniture_fit.py packs exactly this rectangle.
    const b = bounds2x(occupancyQuad2x(WARDROBE, { pt: at(0, 0), rotationDeg: 0 }));
    expect(b.maxX - b.minX).toBe(WARDROBE.widthMm * 2);
    expect(b.maxY - b.minY).toBe((WARDROBE.depthMm + WARDROBE.clearanceMm) * 2);
  });

  it('draws the same ring the renderer uses, at the right area', () => {
    const ring = footprintRingMm(WARDROBE, { pt: at(3000, 4000), rotationDeg: 0 });
    expect(ring).toHaveLength(4);
    expect(polygonAreaMm2(ring)).toBe(WARDROBE.widthMm * WARDROBE.depthMm);
  });

  it('snaps to the 115 mm module, and to whole mm when snap is off', () => {
    expect(snapPtMm(at(120, 60), 115)).toEqual({ x: 115, y: 115 });
    expect(snapPtMm(at(0, 0), 115)).toEqual({ x: 0, y: 0 });
    expect(snapPtMm(at(1494.6, 2300.4), 0)).toEqual({ x: 1495, y: 2300 });
    expect(snapPtMm(at(-60, -58), 115)).toEqual({ x: -115, y: -115 });
  });
});

// ---------------------------------------------------------------------------
// 4. Collision — informs, never blocks
// ---------------------------------------------------------------------------

describe('collision feedback', () => {
  const room: RoomLike = {
    id: 'room_test',
    type: 'bedroom_master',
    name: 'Master Bedroom',
    polygon: [at(0, 0), at(4000, 0), at(4000, 4000), at(0, 4000)],
  };

  const placedWardrobe: PlacedFurniture = {
    id: 'furniture_a',
    storeyId: STOREY_ID,
    catalogId: WARDROBE.id,
    pose: { pt: at(1000, 1000), rotationDeg: 0 },
    item: WARDROBE,
  };

  function context(
    overrides: {
      walls?: readonly WallLike[];
      furniture?: readonly PlacedFurniture[];
      rooms?: readonly RoomLike[];
      excludeFurnitureId?: string | null;
    } = {},
  ): PlacementContext {
    return buildPlacementContext({
      storeyId: STOREY_ID,
      snapStepMm: 115,
      walls: overrides.walls ?? [],
      furniture: overrides.furniture ?? [placedWardrobe],
      rooms: overrides.rooms ?? [room],
      excludeFurnitureId: overrides.excludeFurnitureId ?? null,
    });
  }

  it('flags an overlap with another item and names it', () => {
    const issues = evaluatePlacement(QUEEN_BED, { pt: at(1000, 1000), rotationDeg: 0 }, context());
    const overlap = issues.find((i) => i.code === 'overlaps-furniture');
    expect(overlap).toBeDefined();
    expect(overlap?.message).toContain('Wardrobe');
    expect(overlap?.severity).toBe('warn');
    expect(overlap?.fixHint.length).toBeGreaterThan(0);
    expect(overlap?.targetIds).toContain('furniture_a');
    expect(issueTone(issues)).toBe('warn');
  });

  it('does not flag two items that merely touch', () => {
    // The placed wardrobe occupies x 400..1600, y 700..1300. Sit a second one
    // exactly against its left face — x −800..400, sharing the edge at x = 400.
    // Flush against a neighbour is correct work, and a warning here would train
    // architects to ignore the colour within an hour.
    const issues = evaluatePlacement(WARDROBE, { pt: at(-200, 1000), rotationDeg: 0 }, context());
    expect(issues.some((i) => i.code === 'overlaps-furniture')).toBe(false);
  });

  it('flags a wall overlap using the wall thickness', () => {
    const ctx = context({
      walls: [
        { id: 'wall_x', storeyId: STOREY_ID, a: at(0, 2000), b: at(4000, 2000), thicknessMm: 230 },
      ],
    });
    const issues = evaluatePlacement(WARDROBE, { pt: at(2000, 2000), rotationDeg: 0 }, ctx);
    expect(issues.some((i) => i.code === 'overlaps-wall')).toBe(true);
  });

  it('reports a blocked access strip separately, and only as info', () => {
    // Bed centred at y = −300: its body runs y −1250..650, clear of the placed
    // wardrobe at y 700..1300 — but its 600 mm walk-past strip runs y 650..1250
    // and lands on the wardrobe. That is a comfort note, not a collision.
    const issues = evaluatePlacement(QUEEN_BED, { pt: at(1000, -300), rotationDeg: 0 }, context());

    expect(issues.some((i) => i.code === 'overlaps-furniture')).toBe(false);
    const clearance = issues.find((i) => i.code === 'clearance-blocked');
    expect(clearance).toBeDefined();
    expect(clearance?.severity).toBe('info');
    expect(clearance?.basis).toContain('600');
    expect(clearance?.message).toContain('Wardrobe');
  });

  it('never removes an item from the obstacle set except the one being dragged', () => {
    const withSelf = context();
    const dragging = context({ excludeFurnitureId: 'furniture_a' });
    expect(withSelf.obstacles).toHaveLength(1);
    expect(dragging.obstacles).toHaveLength(0);
  });

  it('says an item is outside the room without calling it an error', () => {
    const issues = evaluatePlacement(QUEEN_BED, { pt: at(9000, 9000), rotationDeg: 0 }, context());
    const outside = issues.find((i) => i.code === 'outside-room');
    expect(outside?.severity).toBe('info');
    expect(issueTone(issues)).not.toBe('warn');
  });

  it('does not warn about a well-placed item at all', () => {
    const issues = evaluatePlacement(QUEEN_BED, { pt: at(2800, 2800), rotationDeg: 180 }, context());
    expect(issues.filter((i) => i.severity === 'warn')).toHaveLength(0);
  });

  it('ignores furniture and walls belonging to another storey', () => {
    const ctx = buildPlacementContext({
      storeyId: STOREY_ID,
      snapStepMm: 115,
      walls: [
        { id: 'wall_up', storeyId: FIXTURE_IDS.firstStorey, a: at(0, 0), b: at(4000, 0), thicknessMm: 230 },
      ],
      furniture: [{ ...placedWardrobe, storeyId: FIXTURE_IDS.firstStorey }],
      rooms: [],
    });
    expect(ctx.obstacles).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// 5. The tool state machine
// ---------------------------------------------------------------------------

describe('placement state machine', () => {
  function controllerWithStorey(): PlacementController {
    const c = new PlacementController();
    c.setContext(
      buildPlacementContext({
        storeyId: STOREY_ID,
        snapStepMm: 115,
        walls: [],
        furniture: [],
        rooms: [],
      }),
    );
    return c;
  }

  it('idles until armed, and commits nothing from idle', () => {
    const c = controllerWithStorey();
    expect(c.getCoarseState().phase).toBe('idle');
    expect(c.commit()).toBeNull();
  });

  it('arms, snaps the preview to the 115 mm module, and commits a place op', () => {
    const c = controllerWithStorey();
    c.arm(QUEEN_BED);
    c.pointerMove(at(1204, 2287));

    expect(c.getPoseState().pose.pt).toEqual({ x: 1150, y: 2300 });

    const result = c.commit();
    expect(result).not.toBeNull();
    expect(result?.ops).toHaveLength(1);
    const payload = payloadOf(result?.ops[0]);
    expect(payload.action).toBe('place');
    expect(payload.catalogId).toBe('bed-queen');
    expect(payload.pt).toEqual({ x: 1150, y: 2300 });
    expect(result?.label).toBe('Queen bed placed');
  });

  it('stays armed after a placement so a set of chairs is one trip to the browser', () => {
    const c = controllerWithStorey();
    c.arm(QUEEN_BED);
    c.pointerMove(at(1000, 1000));
    expect(c.commit()).not.toBeNull();
    expect(c.getCoarseState().phase).toBe('placing');
    c.pointerMove(at(3000, 1000));
    expect(c.commit()).not.toBeNull();
  });

  it('refuses to place when there is no storey rather than emitting a half-op', () => {
    const c = new PlacementController();
    c.arm(QUEEN_BED);
    c.pointerMove(at(1000, 1000));
    expect(c.commit()).toBeNull();
  });

  it('Esc cancels; Enter commits', () => {
    const c = controllerWithStorey();
    c.arm(QUEEN_BED);
    expect(c.handleKey({ key: 'Escape' })).toEqual({ handled: true });
    expect(c.getCoarseState().phase).toBe('idle');

    c.arm(QUEEN_BED);
    c.pointerMove(at(1000, 1000));
    const outcome = c.handleKey({ key: 'Enter' });
    expect(outcome.handled).toBe(true);
    expect(outcome.commit?.ops).toHaveLength(1);
  });

  it('R rotates 90°, Shift-R the other way', () => {
    const c = controllerWithStorey();
    c.arm(QUEEN_BED);
    c.handleKey({ key: 'r' });
    expect(c.getPoseState().pose.rotationDeg).toBe(90);
    c.handleKey({ key: 'R', shift: true });
    expect(c.getPoseState().pose.rotationDeg).toBe(0);
  });

  it('typing a number overrides the mouse for the angle', () => {
    const c = controllerWithStorey();
    c.arm(QUEEN_BED);
    c.handleKey({ key: '4' });
    c.handleKey({ key: '5' });
    expect(c.getCoarseState().entry).toMatchObject({ target: 'rotation', buffer: '45' });
    expect(c.getPoseState().pose.rotationDeg).toBe(45);

    const committed = c.handleKey({ key: 'Enter' }).commit;
    expect(payloadOf(committed?.ops[0]).rotationDeg).toBe(45);
  });

  it('X and Y type an exact position in the project’s units, and pin that axis', () => {
    const c = controllerWithStorey();
    c.arm(QUEEN_BED);
    c.handleKey({ key: 'x' });
    for (const ch of '3450') c.handleKey({ key: ch });
    expect(c.getPoseState().pose.pt.x).toBe(3450);

    // The pointer may keep moving; the typed axis holds.
    c.pointerMove(at(9999, 1150));
    expect(c.getPoseState().pose.pt).toEqual({ x: 3450, y: 1150 });
  });

  it('parses a feet-and-inches entry through the shared parser', () => {
    const c = controllerWithStorey();
    c.arm(QUEEN_BED);
    c.handleKey({ key: 'y' });
    for (const ch of "12'6\"") c.handleKey({ key: ch });
    // 12'6" = 3810 mm exactly.
    expect(c.getPoseState().pose.pt.y).toBe(3810);
  });

  it('Esc clears a mistyped number before it cancels the placement', () => {
    const c = controllerWithStorey();
    c.arm(QUEEN_BED);
    c.handleKey({ key: '9' });
    expect(c.handleKey({ key: 'Escape' })).toEqual({ handled: true });
    expect(c.getCoarseState().phase).toBe('placing');
    expect(c.getCoarseState().entry).toBeNull();
    c.handleKey({ key: 'Escape' });
    expect(c.getCoarseState().phase).toBe('idle');
  });

  it('lets a tool key through instead of swallowing it into a number', () => {
    const c = controllerWithStorey();
    c.arm(QUEEN_BED);
    // 'm' is the measure tool. With no digits typed it is NOT entry input.
    expect(c.handleKey({ key: 'm' }).handled).toBe(false);
    // After a digit, it can complete "3800mm".
    c.handleKey({ key: '3' });
    expect(c.handleKey({ key: 'm' }).handled).toBe(true);
  });

  it('commits over an overlap — advisories inform, they do not block', () => {
    const occupied: PlacedFurniture = {
      id: 'furniture_x',
      storeyId: STOREY_ID,
      catalogId: WARDROBE.id,
      pose: { pt: at(1150, 1150), rotationDeg: 0 },
      item: WARDROBE,
    };
    const c = new PlacementController();
    c.setContext(
      buildPlacementContext({
        storeyId: STOREY_ID,
        snapStepMm: 115,
        walls: [],
        furniture: [occupied],
        rooms: [],
      }),
    );
    c.arm(QUEEN_BED);
    c.pointerMove(at(1150, 1150));

    expect(c.getPoseState().tone).toBe('warn');
    expect(c.commit()).not.toBeNull();
  });

  it('moving an existing instance emits a transform, then returns to idle', () => {
    const c = controllerWithStorey();
    c.beginMove('furniture_a', WARDROBE, { pt: at(0, 0), rotationDeg: 0 });
    c.pointerMove(at(2300, 1150));
    const result = c.commit();

    const payload = payloadOf(result?.ops[0]);
    expect(payload.action).toBe('transform');
    expect(payload.id).toBe('furniture_a');
    expect(payload.pt).toEqual({ x: 2300, y: 1150 });
    expect(result?.label).toBe('Wardrobe (2 door) moved');
    expect(c.getCoarseState().phase).toBe('idle');
  });

  it('publishes coarse changes rarely and pose changes often', () => {
    const c = controllerWithStorey();
    let coarse = 0;
    let pose = 0;
    c.subscribe(() => {
      coarse += 1;
    });
    c.subscribePose(() => {
      pose += 1;
    });

    c.arm(QUEEN_BED);
    for (let i = 0; i < 20; i += 1) c.pointerMove(at(1000 + i * 30, 1000));

    // One arm event; twenty moves must not become twenty React renders.
    expect(coarse).toBe(1);
    expect(pose).toBe(21);
  });

  it('hands out a stable coarse snapshot (useSyncExternalStore would loop otherwise)', () => {
    const c = controllerWithStorey();
    expect(c.getCoarseState()).toBe(c.getCoarseState());
    c.arm(QUEEN_BED);
    const armed = c.getCoarseState();
    c.pointerMove(at(500, 500));
    expect(c.getCoarseState()).toBe(armed);
  });

  it('deletes a selection as one group with a plain-English label', () => {
    const c = controllerWithStorey();
    const one = c.deleteOps(['furniture_a'], [WARDROBE]);
    expect(one?.label).toBe('Wardrobe (2 door) deleted');
    expect(one?.ops).toHaveLength(1);

    const many = c.deleteOps(['a', 'b', 'c'], [WARDROBE, QUEEN_BED, null]);
    expect(many?.label).toBe('3 items deleted');
    expect(many?.ops).toHaveLength(3);

    expect(c.deleteOps([], [])).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 6. Catalogue transforms
// ---------------------------------------------------------------------------

describe('catalogue', () => {
  it('uses the served clearance when there is one', () => {
    expect(QUEEN_BED.clearanceMm).toBe(600);
    expect(QUEEN_BED.clearanceAssumed).toBe(false);
  });

  it('falls back per category, and says the number was assumed', () => {
    const stripped = toCatalogueItem({
      id: 'dining-6',
      name: 'Dining table (6)',
      category: 'table',
      widthMm: 1500,
      depthMm: 900,
      heightMm: 750,
      roomTypes: ['dining'],
      // clearanceMm absent — what today's zod schema leaves us with.
    });
    expect(stripped.clearanceMm).toBe(CLEARANCE_FALLBACK_MM.table);
    expect(stripped.clearanceAssumed).toBe(true);
  });

  it('files an unknown category under "other" instead of dropping the item', () => {
    const odd = toCatalogueItem({
      id: 'mystery',
      name: 'Mystery object',
      category: 'furniture-of-the-future',
      widthMm: 500,
      depthMm: 500,
      heightMm: 500,
    });
    expect(odd.category).toBe('other');
    expect(odd.rawCategory).toBe('furniture-of-the-future');
  });

  it('filters to a room type using the catalogue’s own list', () => {
    const items = [QUEEN_BED, WARDROBE, WALL_SHELF];
    expect(filterByRoomType(items, 'bedroom_master').map((i) => i.id)).toEqual(['bed-queen']);
    expect(filterByRoomType(items, 'study').map((i) => i.id)).toEqual(['wall-shelf']);
    expect(filterByRoomType(items, null)).toHaveLength(3);
  });

  it('ranks an exact name match above a room-type match', () => {
    const found = searchItems([QUEEN_BED, WARDROBE, WALL_SHELF], 'wardrobe');
    expect(found[0]?.id).toBe('wardrobe-2door');
  });

  it('requires every search term to match something', () => {
    expect(searchItems([QUEEN_BED, WARDROBE], 'queen bed')).toHaveLength(1);
    expect(searchItems([QUEEN_BED, WARDROBE], 'queen wardrobe')).toHaveLength(0);
  });

  it('groups in catalogue order and drops empty sections', () => {
    const groups = groupByCategory([WARDROBE, QUEEN_BED]);
    expect(groups.map((g) => g.category)).toEqual(['bed', 'storage']);
    expect(groups[0]?.items).toHaveLength(1);
  });

  it('normalises a whole page in one call', () => {
    const items = toCatalogue([
      { id: 'a', name: 'A', category: 'bed', widthMm: 900, depthMm: 1900, heightMm: 600 },
      { id: 'b', name: 'B', category: 'seating', widthMm: 800, depthMm: 850, heightMm: 800 },
    ]);
    expect(items).toHaveLength(2);
    expect(items.every((i) => i.clearanceAssumed)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 7. Box proxies — honest placeholders, deterministically built
// ---------------------------------------------------------------------------

describe('box proxies', () => {
  it('is tagged for the Phase 5/7 asset swap and says what it is', () => {
    const proxy = boxProxyFor(QUEEN_BED);
    expect(proxy.catalogId).toBe('bed-queen');
    expect(proxy.source).toBe('parametric-box-proxy');
    expect(proxy.boxes.length).toBeGreaterThan(0);
  });

  it('never exceeds the catalogue footprint — that is the number collision uses', () => {
    for (const item of [QUEEN_BED, WARDROBE, WALL_SHELF]) {
      const proxy = boxProxyFor(item);
      expect(proxy.widthMm).toBe(item.widthMm);
      expect(proxy.depthMm).toBe(item.depthMm);
      for (const box of proxy.boxes) {
        expect(box.wMm).toBeLessThanOrEqual(item.widthMm);
        expect(box.dMm).toBeLessThanOrEqual(item.depthMm);
      }
    }
  });

  it('reports its real height, which a headboard may push past the catalogue’s', () => {
    // The catalogue's 600 mm is the mattress top, not the top of the bed.
    const bed = boxProxyFor(QUEEN_BED);
    expect(bed.heightMm).toBeGreaterThan(QUEEN_BED.heightMm);
    // A plain box item measures exactly what the catalogue says.
    expect(boxProxyFor(WARDROBE).heightMm).toBe(WARDROBE.heightMm);
  });

  it('keeps every dimension an integer', () => {
    for (const box of boxProxyFor(QUEEN_BED).boxes) {
      for (const n of [box.cx, box.cy, box.cz, box.wMm, box.dMm, box.hMm]) {
        expect(Number.isInteger(n)).toBe(true);
      }
    }
  });

  it('puts a bed’s headboard behind it, away from the access strip', () => {
    const headboard = boxProxyFor(QUEEN_BED).boxes.find((b) => b.key === 'headboard');
    expect(headboard).toBeDefined();
    // Front is +Y, so the headboard must be at negative Y.
    expect(headboard?.cy ?? 0).toBeLessThan(0);
  });

  it('builds identical boxes on two runs', () => {
    expect(boxProxyFor(WARDROBE)).toEqual(boxProxyFor(WARDROBE));
  });
});

// ---------------------------------------------------------------------------
// 8. Render data
// ---------------------------------------------------------------------------

describe('render instances', () => {
  it('flattens placed items into per-box instances carrying their ids', () => {
    const placed: PlacedFurniture[] = [
      {
        id: 'furniture_a',
        storeyId: STOREY_ID,
        catalogId: WARDROBE.id,
        pose: { pt: at(1000, 2000), rotationDeg: 90 },
        item: WARDROBE,
      },
    ];
    const { instances, unknownCatalogIds } = buildBoxInstances(placed);
    expect(unknownCatalogIds).toHaveLength(0);
    expect(instances.length).toBe(boxProxyFor(WARDROBE).boxes.length);
    expect(instances.every((i) => i.furnitureId === 'furniture_a')).toBe(true);
    expect(instances.every((i) => i.deg === 90)).toBe(true);
  });

  it('reports an instance whose catalogue entry is missing instead of hiding it', () => {
    const { instances, unknownCatalogIds } = buildBoxInstances([
      {
        id: 'furniture_ghost',
        storeyId: STOREY_ID,
        catalogId: 'deleted-from-catalogue',
        pose: { pt: at(0, 0), rotationDeg: 0 },
        item: null,
      },
    ]);
    expect(instances).toHaveLength(0);
    expect(unknownCatalogIds).toEqual(['deleted-from-catalogue']);
  });

  it('places a wall obstacle at its built thickness', () => {
    const quad = wallQuad2x({
      id: 'w',
      storeyId: STOREY_ID,
      a: at(0, 0),
      b: at(3000, 0),
      thicknessMm: 230,
    });
    const b = bounds2x(quad);
    expect(b.maxY - b.minY).toBe(230 * 2);
    expect(b.maxX - b.minX).toBe(3000 * 2);
  });
});
