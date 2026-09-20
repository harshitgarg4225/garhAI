/**
 * Spec: dimension chains from a wall set, and the ops a committed value becomes.
 *
 * The fixture is the model core's own two-room plan (`@garh/model`'s
 * `testing.ts`), folded through the real `fold()` — not a hand-written wall
 * list. That matters: the rooms in it were produced by the real planar
 * subdivision, so a room-span chain here is measured against the same clear
 * polygons the compliance engine and the sheet engine see.
 *
 *     (0,4000) +-----------+-----------+ (6000,4000)
 *              |           |           |
 *              |  room A   |  room B   |   external 230mm
 *              |           |           |   spine    115mm
 *        (0,0) +-----------+-----------+ (6000,0)
 *                      x = 3000
 */

import { describe, expect, it } from 'vitest';

import {
  applyGroup,
  FIXTURE_IDS,
  makeTwoRoomPlan,
  makeTwoRoomPlanWithOpenings,
  type Op,
  type Opening,
  type Wall,
} from '@garh/model';

import {
  buildDimensionChains,
  buildRoomSpanChains,
  chainBaselineMm,
  chainPointMm,
  editableSegments,
  segmentMidMm,
  type DimChain,
  type DimensionEditTarget,
} from './chain';
import { applyDimensionEdit, roomTargetAreaOp } from './edit';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function chainOf(chains: readonly DimChain[], side: string, kind: string): DimChain {
  const found = chains.find((c) => c.side === side && c.kind === kind);
  if (found === undefined) throw new Error(`no ${side}/${kind} chain`);
  return found;
}

function values(chain: DimChain): number[] {
  return chain.segments.map((s) => s.valueMm);
}

function wallMoveOps(
  ops: readonly Op[],
): { wallId: string; a: { x: number; y: number }; b: { x: number; y: number } }[] {
  const out: { wallId: string; a: { x: number; y: number }; b: { x: number; y: number } }[] = [];
  for (const op of ops) {
    if (op.type === 'wall.move')
      out.push({ wallId: op.payload.wallId, a: op.payload.a, b: op.payload.b });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Chain construction
// ---------------------------------------------------------------------------

describe('buildDimensionChains', () => {
  const doc = makeTwoRoomPlan();
  const walls = doc.house.walls;
  const set = buildDimensionChains(walls, doc.house.openings);

  it('ticks the south string on every wall that runs north-south', () => {
    const south = chainOf(set.chains, 'S', 'wall');
    expect(south.axis).toBe('x');
    expect(south.ticks.map((t) => t.atMm)).toEqual([0, 3000, 6000]);
    // west wall, spine, east wall — one tick each, and the tick carries the id
    // so an edit knows which wall to move.
    expect(south.ticks[1]?.wallIds).toEqual([FIXTURE_IDS.wallSpine]);
    expect(values(south)).toEqual([3000, 3000]);
  });

  it('ticks the west string on every wall that runs east-west', () => {
    const west = chainOf(set.chains, 'W', 'wall');
    expect(west.axis).toBe('y');
    expect(west.ticks.map((t) => t.atMm)).toEqual([0, 4000]);
    expect(values(west)).toEqual([4000]);
  });

  it('adds an overall string only when it says more than the wall string', () => {
    // South has three ticks, so the overall (6000) is new information.
    expect(values(chainOf(set.chains, 'S', 'overall'))).toEqual([6000]);
    // West has two: its single bay IS the overall, and printing it twice is
    // noise on a drawing.
    expect(set.chains.some((c) => c.side === 'W' && c.kind === 'overall')).toBe(false);
  });

  it('measures centrelines, because that is what wall.move moves', () => {
    // 6000, not 6230: the outer FACES are 6230 apart, and a dimension that
    // says 6230 cannot be typed back into a centreline op without a fudge.
    expect(values(chainOf(set.chains, 'S', 'overall'))).toEqual([6000]);
  });

  it('reports skew walls rather than silently dropping them', () => {
    const skew: Wall = {
      id: 'wall_01J0000000000000000000SKW',
      storeyId: FIXTURE_IDS.groundStorey,
      a: { x: 0, y: 0 },
      b: { x: 1000, y: 1000 },
      thicknessMm: 115,
      kind: 'internal',
      loadBearing: false,
    };
    const withSkew = buildDimensionChains([...walls, skew], []);
    expect(withSkew.skewWallIds).toEqual([skew.id]);
    // …and the orthogonal walls are still dimensioned normally.
    expect(values(chainOf(withSkew.chains, 'S', 'wall'))).toEqual([3000, 3000]);
  });

  it('drops hairline segments that no one can read', () => {
    const nudged: Wall = {
      id: 'wall_01J0000000000000000000NDG',
      storeyId: FIXTURE_IDS.groundStorey,
      a: { x: 3040, y: 0 },
      b: { x: 3040, y: 4000 },
      thicknessMm: 115,
      kind: 'internal',
      loadBearing: false,
    };
    const set2 = buildDimensionChains([...walls, nudged], []);
    const south = chainOf(set2.chains, 'S', 'wall');
    // The 40 mm gap between x=3000 and x=3040 is a modelling artefact, not a bay.
    expect(values(south)).toEqual([3000, 2960]);
  });

  it('is deterministic — same walls in a different order, same chains', () => {
    const reversed = buildDimensionChains(walls.slice().reverse(), doc.house.openings);
    expect(values(chainOf(reversed.chains, 'S', 'wall'))).toEqual(
      values(chainOf(set.chains, 'S', 'wall')),
    );
    expect(chainOf(reversed.chains, 'S', 'wall').segments.map((s) => s.id)).toEqual(
      chainOf(set.chains, 'S', 'wall').segments.map((s) => s.id),
    );
  });
});

describe('opening strings', () => {
  const doc = makeTwoRoomPlanWithOpenings();
  const set = buildDimensionChains(doc.house.walls, doc.house.openings);

  it('alternates pier, opening, pier along the wall that faces the side', () => {
    // Main door: 900 wide, centred 1500 along a 6000 wall running west→east.
    const chain = chainOf(set.chains, 'S', 'opening');
    expect(values(chain)).toEqual([1050, 900, 4050]);
    expect(chain.segments.map((s) => s.target?.kind)).toEqual([
      'opening-gap',
      'opening-width',
      'opening-gap',
    ]);
  });

  it('anchors the trailing pier at the far wall end, not at the near one', () => {
    const chain = chainOf(set.chains, 'S', 'opening');
    const tail = chain.segments[2]?.target;
    expect(tail).toMatchObject({ kind: 'opening-gap', side: 'after', anchorAlongMm: 6000 });
  });

  it('places jambs correctly on a wall drawn in the negative direction', () => {
    // The north wall runs east→west (a.x 6000 → b.x 0), so `offsetMm` counts
    // backwards along +X. A window 1000 along it sits at x = 5000, not 1000.
    const north = doc.house.walls.find((w) => w.id === FIXTURE_IDS.wallNorth);
    expect(north?.a.x).toBe(6000);
    const set2 = buildDimensionChains(doc.house.walls, [
      {
        id: 'opening_01J000000000000000000WN',
        wallId: FIXTURE_IDS.wallNorth,
        kind: 'window',
        widthMm: 1200,
        heightMm: 1200,
        sillMm: 900,
        offsetMm: 1000,
        swing: 'in-left',
        tag: null,
      },
    ]);
    const chain = chainOf(set2.chains, 'N', 'opening');
    const opening = chain.segments.find((s) => s.target?.kind === 'opening-width');
    expect(opening?.startMm).toBe(4400);
    expect(opening?.endMm).toBe(5600);
  });
});

// ---------------------------------------------------------------------------
// Baselines
// ---------------------------------------------------------------------------

describe('chainBaselineMm', () => {
  const doc = makeTwoRoomPlan();
  const set = buildDimensionChains(doc.house.walls, []);

  it('stacks strings outward from the building, level by level', () => {
    const south = chainOf(set.chains, 'S', 'wall');
    const overall = chainOf(set.chains, 'S', 'overall');
    // South is at y = 0 and hangs downwards, so the baselines are negative and
    // the overall string sits further out than the wall string.
    expect(chainBaselineMm(south, 100, 50)).toBe(-150);
    expect(chainBaselineMm(overall, 100, 50)).toBe(-200);
  });

  it('puts the label at the midpoint of its segment', () => {
    const south = chainOf(set.chains, 'S', 'wall');
    const segment = south.segments[0];
    expect(segment).toBeDefined();
    if (segment === undefined) return;
    expect(segmentMidMm(south, segment, -150)).toEqual({ x: 1500, y: -150 });
  });
});

// ---------------------------------------------------------------------------
// Room span chains
// ---------------------------------------------------------------------------

describe('buildRoomSpanChains', () => {
  const doc = makeTwoRoomPlan();

  it('measures a room and points its edit at the walls that made it', () => {
    // The WEST room specifically — `house.rooms` is in canonical polygon order,
    // which is an implementation detail this spec should not depend on.
    const room = doc.house.rooms
      .slice()
      .sort(
        (a, b) => Math.min(...a.polygon.map((p) => p.x)) - Math.min(...b.polygon.map((p) => p.x)),
      )[0];
    expect(room).toBeDefined();
    if (room === undefined) return;

    const chains = buildRoomSpanChains(room, doc.house.walls);
    expect(chains).toHaveLength(2);

    const xChain = chains.find((c) => c.axis === 'x');
    expect(xChain).toBeDefined();
    // The clear width is 2828 (2943 − 115): centreline 3000 minus half of the
    // 230 external and half of the 115 spine.
    expect(xChain?.segments[0]?.valueMm).toBe(2828);

    // …but the EDIT is expressed in centrelines, which is what the op moves.
    const target = xChain?.segments[0]?.target;
    expect(target?.kind).toBe('wall-gap');
    if (target?.kind !== 'wall-gap') return;
    expect(target.anchorAtMm).toBe(0);
    expect(target.movingAtMm).toBe(3000);
  });
});

// ---------------------------------------------------------------------------
// Editing
// ---------------------------------------------------------------------------

describe('applyDimensionEdit — wall gaps', () => {
  const doc = makeTwoRoomPlan();
  const house = doc.house;
  const set = buildDimensionChains(house.walls, house.openings);

  it('moves the far wall and leaves the anchor where it is', () => {
    const south = chainOf(set.chains, 'S', 'wall');
    const target = south.segments[0]?.target;
    expect(target?.kind).toBe('wall-gap');
    if (target === undefined || target === null) return;

    const result = applyDimensionEdit(house, target, 3600);
    expect(result.ok).toBe(true);
    if (!result.ok) return;

    const moves = wallMoveOps(result.ops);
    // Only the spine moves: nothing else has an endpoint ON the spine.
    expect(moves).toHaveLength(1);
    expect(moves[0]).toMatchObject({
      wallId: FIXTURE_IDS.wallSpine,
      a: { x: 3600, y: 0 },
      b: { x: 3600, y: 4000 },
    });
  });

  it('drags every wall joined to the one it moves — as ONE group', () => {
    const overall = chainOf(set.chains, 'S', 'overall');
    const target = overall.segments[0]?.target;
    if (target === undefined || target === null) return;

    const result = applyDimensionEdit(house, target, 6500);
    expect(result.ok).toBe(true);
    if (!result.ok) return;

    const moves = wallMoveOps(result.ops);
    const byId = new Map(moves.map((m) => [m.wallId, m]));

    // The east wall slides 500 east…
    expect(byId.get(FIXTURE_IDS.wallEast)).toMatchObject({
      a: { x: 6500, y: 0 },
      b: { x: 6500, y: 4000 },
    });
    // …and the south and north walls stretch to follow it, at their east ends
    // only. Without this the plan tears open and every room id on the storey
    // dies with the face.
    expect(byId.get(FIXTURE_IDS.wallSouth)).toMatchObject({
      a: { x: 0, y: 0 },
      b: { x: 6500, y: 0 },
    });
    expect(byId.get(FIXTURE_IDS.wallNorth)).toMatchObject({
      a: { x: 6500, y: 4000 },
      b: { x: 0, y: 4000 },
    });
    // The west wall is untouched: it is the anchor.
    expect(byId.has(FIXTURE_IDS.wallWest)).toBe(false);
    expect(moves).toHaveLength(3);
  });

  it('folds cleanly through the real model core', () => {
    const south = chainOf(set.chains, 'S', 'wall');
    const target = south.segments[0]?.target;
    if (target === undefined || target === null) return;

    const result = applyDimensionEdit(house, target, 3600);
    expect(result.ok).toBe(true);
    if (!result.ok) return;

    const next = applyGroup(doc, result.ops, 'g1').model;
    const spine = next.house.walls.find((w) => w.id === FIXTURE_IDS.wallSpine);
    expect(spine?.a.x).toBe(3600);

    // And the rooms re-detect with their ids intact — the whole reason joins
    // travel with the wall.
    expect(next.house.rooms).toHaveLength(2);
    expect(new Set(next.house.rooms.map((r) => r.id))).toEqual(
      new Set(doc.house.rooms.map((r) => r.id)),
    );
  });

  it('refuses an edit whose walls have gone, with a sentence not a throw', () => {
    const target: DimensionEditTarget = {
      kind: 'wall-gap',
      axis: 'x',
      anchorWallIds: ['wall_01J0000000000000000000GON'],
      movingWallIds: [FIXTURE_IDS.wallSpine],
      anchorAtMm: 0,
      movingAtMm: 3000,
    };
    const result = applyDimensionEdit(house, target, 3600);
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.reason).toMatch(/gone/i);
  });

  it('refuses zero, negative and fractional values', () => {
    const south = chainOf(set.chains, 'S', 'wall');
    const target = south.segments[0]?.target;
    if (target === undefined || target === null) return;
    for (const bad of [0, -100, 1200.5]) {
      expect(applyDimensionEdit(house, target, bad).ok).toBe(false);
    }
  });

  it('never returns a partial group', () => {
    // Every editable segment either produces a complete set of ops or none.
    for (const { target } of editableSegments(set.chains)) {
      const result = applyDimensionEdit(house, target, 2000);
      if (result.ok) expect(result.ops.length).toBeGreaterThan(0);
      else expect(result.reason.length).toBeGreaterThan(0);
    }
  });
});

describe('applyDimensionEdit — openings', () => {
  const doc = makeTwoRoomPlanWithOpenings();
  const house = doc.house;
  const set = buildDimensionChains(house.walls, house.openings);
  const chain = chainOf(set.chains, 'S', 'opening');

  it('slides the opening when its leading pier is retyped', () => {
    const target = chain.segments[0]?.target;
    if (target === undefined || target === null) return;
    const result = applyDimensionEdit(house, target, 1500);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    // pier 1500 + half of 900 ⇒ centre at 1950
    expect(result.ops).toEqual([
      { type: 'opening.move', payload: { openingId: FIXTURE_IDS.doorMain, offsetMm: 1950 } },
    ]);
  });

  it('measures the trailing pier back from the far end of the wall', () => {
    const target = chain.segments[2]?.target;
    if (target === undefined || target === null) return;
    const result = applyDimensionEdit(house, target, 1000);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    // 6000 − 1000 − 450 ⇒ 4550
    expect(result.ops).toEqual([
      { type: 'opening.move', payload: { openingId: FIXTURE_IDS.doorMain, offsetMm: 4550 } },
    ]);
  });

  it('resizes rather than moves when the opening itself is retyped', () => {
    const target = chain.segments[1]?.target;
    if (target === undefined || target === null) return;
    const result = applyDimensionEdit(house, target, 1200);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.ops).toEqual([
      { type: 'opening.resize', payload: { openingId: FIXTURE_IDS.doorMain, widthMm: 1200 } },
    ]);
  });

  it('refuses to push an opening off the end of its wall', () => {
    const target = chain.segments[0]?.target;
    if (target === undefined || target === null) return;
    const result = applyDimensionEdit(house, target, 9000);
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.reason).toMatch(/past the end/i);
  });

  it('does not snap a typed length to the 115 mm module', () => {
    const target = chain.segments[1]?.target;
    if (target === undefined || target === null) return;
    // 2390 is exactly the kind of value the module cannot express, and typing
    // it is the whole reason the field exists.
    const result = applyDimensionEdit(house, target, 2390);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.ops[0]).toMatchObject({ payload: { widthMm: 2390 } });
  });
});

describe('roomTargetAreaOp', () => {
  it('edits the target, never the geometry', () => {
    const op = roomTargetAreaOp('room_01J0000000000000000000R1', 12_000_000);
    expect(op.type).toBe('room.set_target');
    expect(op).toMatchObject({ payload: { targetAreaMm2: 12_000_000 } });
  });

  it('clears the target with null rather than zero', () => {
    expect(roomTargetAreaOp('room_01J0000000000000000000R1', null)).toMatchObject({
      payload: { targetAreaMm2: null },
    });
  });
});

// ---------------------------------------------------------------------------
// Aligned chains — a skew wall is dimensioned in its own frame
// ---------------------------------------------------------------------------

describe('aligned chains for skew walls', () => {
  const doc = makeTwoRoomPlan();
  const skew: Wall = {
    id: 'wall_01J0000000000000000000SKW',
    storeyId: FIXTURE_IDS.groundStorey,
    // A 3-4-5 diagonal: the length is exactly 5000, so nothing here rounds.
    a: { x: 6000, y: 4000 },
    b: { x: 9000, y: 8000 },
    thicknessMm: 115,
    kind: 'internal',
    loadBearing: false,
  };
  const walls = [...doc.house.walls, skew];
  const alignedOf = (chains: readonly DimChain[], kind: string): DimChain => {
    const found = chains.find((c) => c.axis === 'aligned' && c.kind === kind);
    if (found === undefined) throw new Error(`no aligned ${kind} chain`);
    return found;
  };

  it('gives the skew wall a chain of its own length, in along-wall millimetres', () => {
    const set = buildDimensionChains(walls, []);
    expect(set.skewWallIds).toEqual([skew.id]);
    const chain = alignedOf(set.chains, 'wall');
    expect(chain.segments.map((s) => s.valueMm)).toEqual([5000]);
    expect(chain.segments[0]?.target).toEqual({ kind: 'wall-length', wallId: skew.id });
    expect(chain.frame?.origin).toEqual(skew.a);
    expect(chain.frame?.ux).toBeCloseTo(0.6, 12);
    expect(chain.frame?.uy).toBeCloseTo(0.8, 12);
  });

  it('hangs the string on the side away from the building', () => {
    const set = buildDimensionChains(walls, []);
    const frame = alignedOf(set.chains, 'wall').frame;
    if (frame === undefined) throw new Error('no frame');
    // The building's centre is south-west of this wall; the normal points
    // the other way (positive x, negative y, i.e. south-east).
    expect(frame.nx).toBeGreaterThan(0);
    expect(frame.ny).toBeLessThan(0);
    // …and the baseline point is genuinely off the wall, on that side.
    const chain = alignedOf(set.chains, 'wall');
    const p = chainPointMm(chain, 2500, chainBaselineMm(chain, 300, 200));
    expect(p.x).toBeCloseTo(7500 + frame.nx * 500, 9);
    expect(p.y).toBeCloseTo(6000 + frame.ny * 500, 9);
  });

  it('does not put a skew wall into the axis strings', () => {
    const set = buildDimensionChains(walls, []);
    for (const chain of set.chains) {
      if (chain.axis === 'aligned') continue;
      for (const tick of chain.ticks) expect(tick.wallIds).not.toContain(skew.id);
    }
  });

  it('strings the openings on a skew wall along it, with the usual targets', () => {
    const door: Opening = {
      id: 'opening_01J000000000000000000SKD',
      wallId: skew.id,
      kind: 'door',
      widthMm: 900,
      heightMm: 2100,
      sillMm: 0,
      offsetMm: 2000,
      swing: 'in-left',
      tag: null,
    };
    const set = buildDimensionChains(walls, [door]);
    const chain = alignedOf(set.chains, 'opening');
    expect(chain.segments.map((s) => s.valueMm)).toEqual([1550, 900, 2550]);
    expect(chain.segments.map((s) => s.target?.kind)).toEqual([
      'opening-gap',
      'opening-width',
      'opening-gap',
    ]);
    // Along-wall coordinates: the door runs 1550..2450 from `a`.
    expect(chain.segments[1]?.startMm).toBe(1550);
    expect(chain.segments[1]?.endMm).toBe(2450);
  });

  it('typing a length on the aligned chain slides `b` along the wall, integer mm', () => {
    const set = buildDimensionChains(walls, []);
    const target = alignedOf(set.chains, 'wall').segments[0]?.target;
    if (target === undefined || target === null) throw new Error('no target');
    const house = { ...doc.house, walls };
    const result = applyDimensionEdit(house, target, 4000);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.label).toBe('Wall resized');
    expect(wallMoveOps(result.ops)).toEqual([
      { wallId: skew.id, a: { x: 6000, y: 4000 }, b: { x: 8400, y: 7200 } },
    ]);
    // Negative control: the length it already has is not an edit.
    expect(applyDimensionEdit(house, target, 5000).ok).toBe(false);
  });

  it('still dimensions a plan that is ALL skew walls', () => {
    const set = buildDimensionChains([skew], []);
    expect(set.chains.some((c) => c.axis === 'aligned')).toBe(true);
    expect(set.extentMm).not.toBeNull();
  });
});
