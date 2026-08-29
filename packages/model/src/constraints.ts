/**
 * constraints.ts — parallel, perpendicular, collinear, equal-length, axis-align.
 *
 * The difference between drawing and drafting. An architect sketching a plan produces
 * walls that are 0.4° off parallel and 12 mm apart in length, and every one of those
 * errors ends up on a dimension chain, in an area statement, and on a sheet a
 * municipality reads. Nudging them true by hand is the work this exists to delete.
 *
 * ## What this is, and what it deliberately is not
 *
 * These are **one-shot** constraints: solving "make B parallel to A" emits the ops that
 * make it so, right now, and then the relationship is over. Move A afterwards and B
 * stays where it was put. A maintained constraint graph — the kind that re-solves on
 * every drag, with a numerical solver and cycle detection behind it — is a much larger
 * piece of machinery, and pretending a one-shot solve is one would be worse than not
 * having it: an architect who believes their plan is holding itself square would stop
 * checking.
 *
 * What this does buy is real. It emits nothing but `wall.move`, an op that already
 * exists, so every constraint is undoable, replayable, rules-checked and byte-identical
 * across the TS and Python folds without either twin learning a new verb.
 *
 * ## The decision that matters: which end stays put
 *
 * "Make these two walls parallel" has infinitely many answers, and picking the wrong one
 * quietly destroys the plan. Rotating a wall about its `a` endpoint is the obvious
 * implementation and the wrong default — `a` and `b` are an arbitrary authoring order,
 * invisible to the person clicking, so half the time the rotation swings the end that
 * was joined to three other walls out into the middle of a room.
 *
 * So the pivot is chosen: **the end shared with more other walls stays put**, because
 * that is the junction, and a junction torn open is a hole in the plan. Ties go to `a`.
 * {@link pivotForWall} is that rule, and it has a negative control — pin the pivot to
 * `a` and a wall joined at `b` visibly tears off.
 *
 * ## Rounding
 *
 * Every coordinate is integer millimetres, so a rotation cannot be exact. Endpoints are
 * rounded half-away-from-zero like everything else in this model, and the resulting
 * length change — at most about a millimetre, exactly zero for the axis-aligned cases —
 * is measured and REPORTED in {@link ConstraintResult.lengthDriftMm} rather than hidden.
 * A drafting aid that silently changes a wall's length by a millimetre is a drafting aid
 * that silently changes an area statement.
 */

import { distMm, ptRound, type Pt } from './geometry';
import type { WallId } from './ids';
import type { HouseModel, Wall } from './model';
import type { Op } from './ops';

/** The relationships an architect asks for by name. */
export type ConstraintKind =
  | 'parallel'
  | 'perpendicular'
  | 'collinear'
  | 'equal-length'
  | 'horizontal'
  | 'vertical';

/** Kinds that measure one wall against another, so the first selection is the anchor. */
export const ANCHORED_KINDS: readonly ConstraintKind[] = [
  'parallel',
  'perpendicular',
  'collinear',
  'equal-length',
];

/** Kinds that need only one wall, because the reference is the world. */
export const ABSOLUTE_KINDS: readonly ConstraintKind[] = ['horizontal', 'vertical'];

export interface ConstraintRequest {
  readonly kind: ConstraintKind;
  /**
   * The walls to constrain, in selection order.
   *
   * For an anchored kind the FIRST wall is the anchor and never moves — which is the
   * behaviour an architect expects from every CAD package and the reason selection
   * order is preserved rather than sorted.
   */
  readonly wallIds: readonly WallId[];
}

export interface ConstraintResult {
  /** `wall.move` ops, in the order the walls were given. Empty when nothing to do. */
  readonly ops: readonly Op[];
  readonly movedWallIds: readonly WallId[];
  /** Why nothing happened. A sentence for a person, never a code. `null` on success. */
  readonly reason: string | null;
  /** The largest length change integer rounding forced, in mm. 0 for axis-aligned. */
  readonly lengthDriftMm: number;
}

const OK = (
  ops: readonly Op[],
  movedWallIds: readonly WallId[],
  lengthDriftMm: number,
): ConstraintResult => ({ ops, movedWallIds, reason: null, lengthDriftMm });

const NOTHING = (reason: string): ConstraintResult => ({
  ops: [],
  movedWallIds: [],
  reason,
  lengthDriftMm: 0,
});

// ---------------------------------------------------------------------------
// The pivot rule
// ---------------------------------------------------------------------------

/** How many OTHER walls on this storey touch `point`. */
function junctionDegree(house: HouseModel, wall: Wall, point: Pt): number {
  let count = 0;
  for (const other of house.walls) {
    if (other.id === wall.id || other.storeyId !== wall.storeyId) continue;
    if (
      (other.a.x === point.x && other.a.y === point.y) ||
      (other.b.x === point.x && other.b.y === point.y)
    ) {
      count += 1;
    }
  }
  return count;
}

/**
 * Which end of `wall` should stay put: the one joined to more other walls.
 *
 * `a` and `b` are an arbitrary authoring order that nobody clicking on a wall can see.
 * Rotating about `a` unconditionally means that half the time the end holding the plan
 * together is the end that swings — and the result looks like the tool broke the
 * drawing, because it did. Ties go to `a` so the answer is deterministic.
 */
export function pivotForWall(house: HouseModel, wall: Wall): 'a' | 'b' {
  return junctionDegree(house, wall, wall.b) > junctionDegree(house, wall, wall.a) ? 'b' : 'a';
}

// ---------------------------------------------------------------------------
// Small geometric helpers, all integer-mm in and out
// ---------------------------------------------------------------------------

interface Dir {
  readonly ux: number;
  readonly uy: number;
}

function direction(wall: Wall): Dir | null {
  const dx = wall.b.x - wall.a.x;
  const dy = wall.b.y - wall.a.y;
  const len = Math.hypot(dx, dy);
  return len === 0 ? null : { ux: dx / len, uy: dy / len };
}

/** Flip `dir` when it points away from `along`, so a constraint never reverses a wall. */
function alignedWith(dir: Dir, along: Dir): Dir {
  return dir.ux * along.ux + dir.uy * along.uy < 0 ? { ux: -dir.ux, uy: -dir.uy } : dir;
}

/**
 * One `wall.move` placing the wall's free end `lengthMm` from `pivot` along `dir`.
 *
 * Returns `null` when nothing would change, so a constraint already satisfied emits no
 * op at all — an undo entry for a no-op is noise in a history an architect reads.
 */
function moveTo(
  wall: Wall,
  pivot: Pt,
  dir: Dir,
  lengthMm: number,
  pivotEnd: 'a' | 'b',
): { op: Op; driftMm: number } | null {
  const free = ptRound(pivot.x + dir.ux * lengthMm, pivot.y + dir.uy * lengthMm);
  const a = pivotEnd === 'a' ? pivot : free;
  const b = pivotEnd === 'a' ? free : pivot;
  if (a.x === wall.a.x && a.y === wall.a.y && b.x === wall.b.x && b.y === wall.b.y) return null;
  return {
    op: { type: 'wall.move', payload: { wallId: wall.id, a, b } },
    driftMm: Math.abs(distMm(a, b) - lengthMm),
  };
}

function byId(house: HouseModel, id: WallId): Wall | undefined {
  return house.walls.find((wall) => wall.id === id);
}

// ---------------------------------------------------------------------------
// The solve
// ---------------------------------------------------------------------------

/**
 * Emit the ops that make `request` true, or explain why nothing can be done.
 *
 * Pure and total: it never throws, never mutates, and every refusal carries a sentence.
 * A drafting aid that fails silently on a selection the architect thought was fine is
 * one they stop trusting after the second time.
 */
export function solveConstraint(house: HouseModel, request: ConstraintRequest): ConstraintResult {
  const anchored = ANCHORED_KINDS.includes(request.kind);
  const walls: Wall[] = [];
  for (const id of request.wallIds) {
    const wall = byId(house, id);
    if (wall === undefined) return NOTHING('One of the selected walls is no longer there.');
    walls.push(wall);
  }

  if (walls.length < (anchored ? 2 : 1)) {
    return NOTHING(
      anchored
        ? 'Select two walls: the first one stays put and the rest move to match it.'
        : 'Select a wall first.',
    );
  }

  // A wall on the ground floor and a wall on the first floor have no geometric
  // relationship worth constraining, and letting it through would move a wall on a
  // storey the architect is not even looking at.
  const storey = walls[0]!.storeyId;
  if (walls.some((wall) => wall.storeyId !== storey)) {
    return NOTHING('Those walls are on different storeys. Constrain walls on one storey.');
  }

  const anchor = walls[0]!;
  const targets = anchored ? walls.slice(1) : walls;
  const anchorDir = direction(anchor);
  if (anchored && anchorDir === null) {
    return NOTHING('The first wall has no length, so there is nothing to match it to.');
  }

  const ops: Op[] = [];
  const moved: WallId[] = [];
  let drift = 0;

  for (const wall of targets) {
    const current = direction(wall);
    if (current === null) continue; // A zero-length wall has no direction to correct.
    const pivotEnd = pivotForWall(house, wall);
    const pivot = pivotEnd === 'a' ? wall.a : wall.b;
    const lengthMm = distMm(wall.a, wall.b);
    // The direction the free end currently lies in, FROM the pivot — not `a`→`b`, which
    // would spin the wall 180° whenever the pivot happened to be `b`.
    const fromPivot: Dir = pivotEnd === 'a' ? current : { ux: -current.ux, uy: -current.uy };

    let result: { op: Op; driftMm: number } | null = null;
    switch (request.kind) {
      case 'horizontal':
        result = moveTo(wall, pivot, alignedWith({ ux: 1, uy: 0 }, fromPivot), lengthMm, pivotEnd);
        break;
      case 'vertical':
        result = moveTo(wall, pivot, alignedWith({ ux: 0, uy: 1 }, fromPivot), lengthMm, pivotEnd);
        break;
      case 'parallel':
        result = moveTo(wall, pivot, alignedWith(anchorDir!, fromPivot), lengthMm, pivotEnd);
        break;
      case 'perpendicular': {
        const normal: Dir = { ux: -anchorDir!.uy, uy: anchorDir!.ux };
        result = moveTo(wall, pivot, alignedWith(normal, fromPivot), lengthMm, pivotEnd);
        break;
      }
      case 'equal-length': {
        const target = distMm(anchor.a, anchor.b);
        result = moveTo(wall, pivot, fromPivot, target, pivotEnd);
        break;
      }
      case 'collinear': {
        // Onto the anchor's infinite line: project the pivot perpendicular, then lay the
        // wall out along the anchor's own direction. Both halves are needed — a wall
        // that is parallel but 300 mm off the line is not collinear, and a wall on the
        // line at 3° is not either.
        const dir = alignedWith(anchorDir!, fromPivot);
        const t = (pivot.x - anchor.a.x) * anchorDir!.ux + (pivot.y - anchor.a.y) * anchorDir!.uy;
        const onLine = ptRound(anchor.a.x + anchorDir!.ux * t, anchor.a.y + anchorDir!.uy * t);
        result = moveTo(wall, onLine, dir, lengthMm, pivotEnd);
        break;
      }
    }

    if (result !== null) {
      ops.push(result.op);
      moved.push(wall.id);
      drift = Math.max(drift, result.driftMm);
    }
  }

  if (ops.length === 0) {
    return NOTHING(
      targets.length === 0
        ? 'Select a wall to move as well as the one to match.'
        : 'Those walls already satisfy that — nothing to change.',
    );
  }
  return OK(ops, moved, Math.round(drift * 100) / 100);
}

/** Label for the undo entry and the toast. Written as the architect would say it. */
export function constraintLabel(kind: ConstraintKind): string {
  switch (kind) {
    case 'parallel':
      return 'Made parallel';
    case 'perpendicular':
      return 'Made perpendicular';
    case 'collinear':
      return 'Made collinear';
    case 'equal-length':
      return 'Lengths matched';
    case 'horizontal':
      return 'Straightened horizontal';
    case 'vertical':
      return 'Straightened vertical';
  }
}
