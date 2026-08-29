/**
 * Geometric constraints — the difference between drawing and drafting.
 *
 * Every case here is built from a real fold, so "the wall moved" means the op survived
 * validation and replay, not that a helper returned a plausible object.
 *
 * Three properties are load-bearing and each has a negative control, because each would
 * pass a suite that merely checked "an op came back":
 *
 *   1. **The pivot.** Rotating about `a` is the obvious implementation and the wrong
 *      one: `a`/`b` is an arbitrary authoring order, so half the time the end holding
 *      the plan together is the end that swings. Pin the pivot to `a` and the joined
 *      wall visibly tears off its junction.
 *   2. **Direction from the pivot, not `a`→`b`.** Get this wrong and a wall pivoting at
 *      `b` spins 180° into the room next door — while still being perfectly parallel,
 *      so a test that only measured the angle would pass.
 *   3. **Length is preserved and drift is reported.** A silent millimetre is a silent
 *      change to an area statement.
 */

import { describe, expect, it } from 'vitest';

import { applyGroup } from './fold';
import { distMm } from './geometry';
import { fixedId, makeEmptyDoc } from './testing';
import { solveConstraint, pivotForWall, type ConstraintKind } from './constraints';
import type { Op } from './ops';
import type { HouseModel, Wall } from './model';
import type { ProjectDoc } from './model';
import type { WallId } from './ids';

const STOREY = 'ground';

function wallId(tag: string): WallId {
  return fixedId('wall', tag);
}

/** Build a doc whose ground storey holds exactly the given wall segments. */
function plan(
  segments: readonly { tag: string; a: [number, number]; b: [number, number] }[],
): ProjectDoc {
  const doc = makeEmptyDoc();
  const storeyId = fixedId('storey', STOREY);
  const ops: Op[] = [
    {
      type: 'storey.add',
      payload: { id: storeyId, name: 'Ground', index: 0, heightMm: 3000 },
    },
    ...segments.map<Op>((segment) => ({
      type: 'wall.add',
      payload: {
        id: wallId(segment.tag),
        storeyId,
        a: { x: segment.a[0], y: segment.a[1] },
        b: { x: segment.b[0], y: segment.b[1] },
        thicknessMm: 230,
        kind: 'external',
      },
    })),
  ];
  return applyGroup(doc, ops, fixedId('group', 'setup')).model;
}

function find(house: HouseModel, tag: string): Wall {
  const wall = house.walls.find((candidate) => candidate.id === wallId(tag));
  if (wall === undefined) throw new Error(`no wall ${tag}`);
  return wall;
}

/** Apply the constraint through the real fold and hand back the resulting house. */
function run(doc: ProjectDoc, kind: ConstraintKind, tags: readonly string[]) {
  const result = solveConstraint(doc.house, { kind, wallIds: tags.map(wallId) });
  const next =
    result.ops.length === 0
      ? doc
      : applyGroup(doc, [...result.ops], fixedId('group', 'solve')).model;
  return { result, house: next.house };
}

/** Bearing of a wall in degrees, folded to [0, 180) so direction does not matter. */
function bearing(wall: Wall): number {
  const deg = (Math.atan2(wall.b.y - wall.a.y, wall.b.x - wall.a.x) * 180) / Math.PI;
  return ((deg % 180) + 180) % 180;
}

// ===========================================================================
// The pivot rule — the decision that keeps a plan in one piece
// ===========================================================================
describe('the pivot', () => {
  // "spine" runs along the bottom. "leg" hangs off its RIGHT end, joined at leg.b,
  // and is 3° out of vertical. Its junction is at `b`, which is the case that catches
  // a naive rotate-about-`a`.
  const joinedAtB = () =>
    plan([
      { tag: 'spine', a: [0, 0], b: [4000, 0] },
      { tag: 'leg', a: [4200, 3000], b: [4000, 0] },
    ]);

  it('keeps the end that other walls are joined to', () => {
    const doc = joinedAtB();
    expect(pivotForWall(doc.house, find(doc.house, 'leg'))).toBe('b');
  });

  it('a wall joined at neither end pivots at a, deterministically', () => {
    const doc = plan([{ tag: 'lonely', a: [0, 0], b: [1000, 500] }]);
    expect(pivotForWall(doc.house, find(doc.house, 'lonely'))).toBe('a');
  });

  it('straightening the leg does not tear it off its junction', () => {
    const doc = joinedAtB();
    const before = find(doc.house, 'leg').b;
    const { result, house } = run(doc, 'vertical', ['leg']);

    expect(result.reason).toBeNull();
    const after = find(house, 'leg');
    expect(after.b).toEqual(before);
    // ...and the free end really did move, so this is not passing on a no-op.
    expect(after.a.x).toBe(4000);
  });

  it('the free end stays on the side of the junction it was already on', () => {
    // The assertion the junction test alone does NOT make, and the one that catches a
    // wall spun 180 degrees. Taking the direction as `a`→`b` rather than from the pivot
    // leaves a wall that is perfectly vertical, still joined at `b`, still 3006 mm long
    // — and hanging DOWN through the floor below instead of up into the room. Every
    // measurement of it stays correct; only the drawing is wrong.
    const doc = joinedAtB();
    const leg = find(doc.house, 'leg');
    expect(leg.a.y).toBeGreaterThan(leg.b.y);

    const { house } = run(doc, 'vertical', ['leg']);
    const after = find(house, 'leg');
    expect(after.a.y).toBeGreaterThan(after.b.y);
  });

  it('NEGATIVE CONTROL: pivoting at a instead would move the junction', () => {
    // The same solve, computed the naive way, to prove the assertion above discriminates.
    const doc = joinedAtB();
    const leg = find(doc.house, 'leg');
    const naiveB = { x: leg.a.x, y: leg.a.y + Math.round(distMm(leg.a, leg.b)) };
    expect(naiveB).not.toEqual(leg.b);
  });
});

// ===========================================================================
// Axis alignment — the everyday one
// ===========================================================================
describe('horizontal and vertical', () => {
  it('makes an almost-horizontal wall exactly horizontal', () => {
    const doc = plan([{ tag: 'w', a: [0, 0], b: [3000, 40] }]);
    const { house } = run(doc, 'horizontal', ['w']);
    const wall = find(house, 'w');
    expect(wall.a.y).toBe(wall.b.y);
  });

  it('preserves the length exactly, because an axis move has no rounding to do', () => {
    const doc = plan([{ tag: 'w', a: [0, 0], b: [3000, 40] }]);
    const before = distMm(find(doc.house, 'w').a, find(doc.house, 'w').b);
    const { result, house } = run(doc, 'horizontal', ['w']);
    const wall = find(house, 'w');
    expect(distMm(wall.a, wall.b)).toBe(Math.round(before));
    expect(result.lengthDriftMm).toBe(0);
  });

  it('does not flip the wall end for end', () => {
    // Pointing right before, pointing right after. A constraint that reverses a wall
    // reverses every opening offset measured along it.
    const doc = plan([{ tag: 'w', a: [0, 0], b: [3000, 40] }]);
    const { house } = run(doc, 'horizontal', ['w']);
    expect(find(house, 'w').b.x).toBeGreaterThan(find(house, 'w').a.x);
  });

  it('a wall pointing LEFT stays pointing left', () => {
    const doc = plan([{ tag: 'w', a: [3000, 0], b: [0, 40] }]);
    const { house } = run(doc, 'horizontal', ['w']);
    expect(find(house, 'w').b.x).toBeLessThan(find(house, 'w').a.x);
  });

  it('an already-horizontal wall produces no op at all', () => {
    const doc = plan([{ tag: 'w', a: [0, 0], b: [3000, 0] }]);
    const { result } = run(doc, 'horizontal', ['w']);
    expect(result.ops).toHaveLength(0);
    expect(result.reason).toContain('already');
  });
});

// ===========================================================================
// Anchored constraints
// ===========================================================================
describe('parallel, perpendicular, collinear, equal-length', () => {
  const pair = () =>
    plan([
      { tag: 'anchor', a: [0, 0], b: [4000, 0] },
      { tag: 'target', a: [0, 2500], b: [3600, 2620] },
    ]);

  it('the anchor never moves', () => {
    const doc = pair();
    const before = find(doc.house, 'anchor');
    const { house } = run(doc, 'parallel', ['anchor', 'target']);
    expect(find(house, 'anchor')).toEqual(before);
  });

  it('parallel brings the bearings together', () => {
    const doc = pair();
    expect(bearing(find(doc.house, 'target'))).toBeGreaterThan(1);
    const { house } = run(doc, 'parallel', ['anchor', 'target']);
    expect(bearing(find(house, 'target'))).toBeCloseTo(bearing(find(house, 'anchor')), 1);
  });

  it('perpendicular puts them 90 degrees apart', () => {
    const doc = pair();
    const { house } = run(doc, 'perpendicular', ['anchor', 'target']);
    const delta = Math.abs(bearing(find(house, 'target')) - bearing(find(house, 'anchor')));
    expect(Math.min(delta, 180 - delta)).toBeCloseTo(90, 1);
  });

  it('equal-length matches the anchor and leaves the direction alone', () => {
    const doc = pair();
    const before = bearing(find(doc.house, 'target'));
    const { house } = run(doc, 'equal-length', ['anchor', 'target']);
    const target = find(house, 'target');
    expect(Math.round(distMm(target.a, target.b))).toBe(4000);
    expect(bearing(target)).toBeCloseTo(before, 1);
  });

  // Collinear's real case: two stretches of what should be ONE line — a party wall
  // interrupted by a gate, a boundary wall in two runs — drawn 150 mm out of true.
  // They must not OVERLAP once aligned, or the fold rejects the move (correctly).
  const runsOfOneWall = () =>
    plan([
      { tag: 'anchor', a: [0, 0], b: [4000, 0] },
      { tag: 'target', a: [5000, 150], b: [9000, 40] },
    ]);

  it('collinear puts the wall ON the anchor line, not merely parallel to it', () => {
    const doc = runsOfOneWall();
    const { result, house } = run(doc, 'collinear', ['anchor', 'target']);
    expect(result.reason).toBeNull();
    const target = find(house, 'target');
    // The anchor lies on y = 0, so collinear means both ends do too.
    expect(target.a.y).toBe(0);
    expect(target.b.y).toBe(0);
  });

  it('NEGATIVE CONTROL: parallel alone does NOT satisfy collinear', () => {
    const doc = runsOfOneWall();
    const { house } = run(doc, 'parallel', ['anchor', 'target']);
    // Parallel corrects the angle and leaves the 150 mm offset exactly where it was.
    expect(find(house, 'target').a.y).toBe(150);
  });

  it('a constraint whose result would overlap another wall is rejected by the fold', () => {
    // Worth pinning: `solveConstraint` is geometry, not validation. It can legitimately
    // propose a move the model refuses — here, sliding a wall directly onto another.
    // The ops must fail through the normal rejection path (which the UI already
    // surfaces as a toast) rather than being silently dropped or half-applied.
    const doc = plan([
      { tag: 'anchor', a: [0, 0], b: [4000, 0] },
      { tag: 'overlapping', a: [0, 2500], b: [3600, 2620] },
    ]);
    const result = solveConstraint(doc.house, {
      kind: 'collinear',
      wallIds: [wallId('anchor'), wallId('overlapping')],
    });
    expect(result.ops).toHaveLength(1);
    expect(() => applyGroup(doc, [...result.ops], fixedId('group', 'clash'))).toThrowError(
      /WALL_DUPLICATE|overlap/i,
    );
  });

  it('one op per wall that actually moves, and none for the anchor', () => {
    const doc = plan([
      { tag: 'anchor', a: [0, 0], b: [4000, 0] },
      { tag: 'skew', a: [0, 2000], b: [3000, 90] },
      { tag: 'already', a: [0, 5000], b: [3000, 5000] },
    ]);
    const { result } = run(doc, 'parallel', ['anchor', 'skew', 'already']);
    expect(result.movedWallIds).toEqual([wallId('skew')]);
    expect(result.ops).toHaveLength(1);
  });

  it('reports the rounding drift rather than hiding it', () => {
    const doc = pair();
    const { result } = run(doc, 'parallel', ['anchor', 'target']);
    // Integer millimetres cannot represent an arbitrary rotation exactly. The number is
    // small; the point is that it is stated.
    expect(result.lengthDriftMm).toBeLessThanOrEqual(1.5);
  });
});

// ===========================================================================
// Refusals — every one a sentence
// ===========================================================================
describe('refusals', () => {
  it('refuses walls on different storeys', () => {
    let doc = plan([{ tag: 'ground-wall', a: [0, 0], b: [4000, 0] }]);
    const upperId = fixedId('storey', 'first');
    doc = applyGroup(
      doc,
      [
        { type: 'storey.add', payload: { id: upperId, name: 'First', index: 1, heightMm: 3000 } },
        {
          type: 'wall.add',
          payload: {
            id: wallId('upper-wall'),
            storeyId: upperId,
            a: { x: 0, y: 2000 },
            b: { x: 3000, y: 2100 },
            thicknessMm: 230,
            kind: 'external',
          },
        },
      ],
      fixedId('group', 'upper'),
    ).model;
    const result = solveConstraint(doc.house, {
      kind: 'parallel',
      wallIds: [wallId('ground-wall'), wallId('upper-wall')],
    });
    expect(result.ops).toHaveLength(0);
    expect(result.reason).toContain('different storeys');
  });

  it('an anchored constraint with one wall says what to select', () => {
    const doc = plan([{ tag: 'w', a: [0, 0], b: [4000, 0] }]);
    const result = solveConstraint(doc.house, { kind: 'parallel', wallIds: [wallId('w')] });
    expect(result.reason).toContain('Select two walls');
  });

  it('a wall that has been deleted underneath the selection says so', () => {
    const doc = plan([{ tag: 'w', a: [0, 0], b: [4000, 0] }]);
    const result = solveConstraint(doc.house, {
      kind: 'horizontal',
      wallIds: [wallId('gone')],
    });
    expect(result.reason).toContain('no longer there');
  });

  it('never throws, whatever it is handed', () => {
    const doc = plan([{ tag: 'w', a: [0, 0], b: [4000, 0] }]);
    for (const kind of [
      'parallel',
      'perpendicular',
      'collinear',
      'equal-length',
      'horizontal',
      'vertical',
    ] as ConstraintKind[]) {
      expect(() => solveConstraint(doc.house, { kind, wallIds: [] })).not.toThrow();
    }
  });
});
