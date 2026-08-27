/**
 * edit.ts — a typed dimension value becomes ops. PURE.
 *
 * §15: "any dimension or area label on canvas is click-to-edit. No dead text."
 * This is the half of that sentence that has consequences. Everything upstream
 * is presentation; this is where a number an architect typed turns into
 * `wall.move` / `opening.move` / `opening.resize` and gets folded.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THREE RULES
 * ────────────────────────────────────────────────────────────────────────────
 *
 * 1. **The live document decides, not the chain.** A target carries the
 *    coordinates it was built from, but this module re-reads every wall from
 *    the document before computing a delta. Between the chain being built and
 *    Enter being pressed there may have been an undo, a rebase, or a copilot
 *    edit; moving a wall by a delta derived from a coordinate that is no longer
 *    true is how a plan quietly gains a 300 mm error.
 *
 * 2. **Joins travel with the wall.** Moving a wall without moving what is
 *    attached to it tears the plan apart: the room detector (planar
 *    subdivision, `rooms.ts`) loses the face, the room id dies, and every
 *    annotation anchored to it orphans. So the returned group also moves the
 *    endpoint of every wall that touches a moving wall — a T-junction stretches,
 *    a corner follows. One group, one undo step.
 *
 * 3. **A typed length is never snapped.** The 115 mm module governs the MOUSE.
 *    The whole reason to type a number is to say one the grid cannot: a 2390 mm
 *    corridor. Snapping it to 2415 and saying nothing would be a lie about what
 *    was asked for. (Ops still carry integer mm — the parse guarantees that.)
 */

import type { HouseModel, Op, Pt, RoomType, Wall } from '@garh/model';
import { pointOnSegment } from '@garh/model';

import type { DimAxis, DimensionEditTarget } from './chain';

// ---------------------------------------------------------------------------
// Result
// ---------------------------------------------------------------------------

export type DimensionEditResult =
  | { readonly ok: true; readonly ops: readonly Op[]; readonly label: string }
  | { readonly ok: false; readonly reason: string };

/** Undo-toast copy per target kind. Sentence case, no trailing period (§15). */
const LABELS: Readonly<Record<DimensionEditTarget['kind'], string>> = {
  'wall-gap': 'Wall moved',
  'opening-gap': 'Opening moved',
  'opening-width': 'Opening resized',
};

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

/**
 * Turn a committed dimension value into the op group that realises it.
 *
 * Returns `{ ok: false }` with a human sentence when the edit cannot be
 * expressed — never a partial group. A half-applied dimension edit is worse
 * than a refused one, and the model store's `applyGroup` is atomic anyway, so
 * this stays honest with it.
 */
export function applyDimensionEdit(
  house: HouseModel,
  target: DimensionEditTarget,
  valueMm: number,
): DimensionEditResult {
  if (!Number.isSafeInteger(valueMm) || valueMm <= 0) {
    return { ok: false, reason: 'A dimension has to be a whole number of millimetres above zero.' };
  }

  switch (target.kind) {
    case 'wall-gap':
      return editWallGap(house, target, valueMm);
    case 'opening-gap':
      return editOpeningGap(house, target, valueMm);
    case 'opening-width':
      return editOpeningWidth(house, target, valueMm);
  }
}

// ---------------------------------------------------------------------------
// wall-gap
// ---------------------------------------------------------------------------

/** The centreline coordinate of an axis-aligned wall on the given axis. */
function wallCoordinate(wall: Wall, axis: DimAxis): number | null {
  // A chain measuring along X ticks on walls that RUN along Y, whose constant
  // coordinate is x. Hence the cross-over: axis 'x' reads `wall.a.x`.
  if (axis === 'x') return wall.a.x === wall.b.x ? wall.a.x : null;
  return wall.a.y === wall.b.y ? wall.a.y : null;
}

function shiftPt(p: Pt, axis: DimAxis, deltaMm: number): Pt {
  return axis === 'x' ? { x: p.x + deltaMm, y: p.y } : { x: p.x, y: p.y + deltaMm };
}

function editWallGap(
  house: HouseModel,
  target: Extract<DimensionEditTarget, { kind: 'wall-gap' }>,
  valueMm: number,
): DimensionEditResult {
  const byId = new Map(house.walls.map((w) => [w.id, w]));

  const anchorWalls = target.anchorWallIds
    .map((id) => byId.get(id))
    .filter((w): w is Wall => w !== undefined);
  const movingWalls = target.movingWallIds
    .map((id) => byId.get(id))
    .filter((w): w is Wall => w !== undefined);

  if (anchorWalls.length === 0 || movingWalls.length === 0) {
    return {
      ok: false,
      reason: 'One of the walls this dimension refers to is gone. Re-select it.',
    };
  }

  const anchorAt = firstCoordinate(anchorWalls, target.axis);
  const movingAt = firstCoordinate(movingWalls, target.axis);
  if (anchorAt === null || movingAt === null) {
    return { ok: false, reason: 'This dimension spans a wall that is no longer straight.' };
  }

  // Direction matters: the chain was built with the anchor at the lower
  // coordinate, but an undo could have swapped them. Preserve the sign that is
  // true NOW, so "3600" always means "3600 apart" and never "3600 the other way".
  const sign = movingAt >= anchorAt ? 1 : -1;
  const currentMm = Math.abs(movingAt - anchorAt);
  const deltaMm = (valueMm - currentMm) * sign;
  if (deltaMm === 0) return { ok: false, reason: 'That is already the dimension.' };

  const movingIds = new Set(movingWalls.map((w) => w.id));
  const ops: Op[] = [];

  // 1. The referenced walls slide.
  for (const wall of movingWalls) {
    ops.push({
      type: 'wall.move',
      payload: {
        wallId: wall.id,
        a: shiftPt(wall.a, target.axis, deltaMm),
        b: shiftPt(wall.b, target.axis, deltaMm),
      },
    });
  }

  // 2. Anything joined to them follows, endpoint by endpoint. Same storey only:
  //    a wall on the floor above that happens to share a coordinate is a
  //    different wall, and dragging it would be a silent cross-storey edit.
  const storeys = new Set(movingWalls.map((w) => w.storeyId));
  for (const wall of house.walls) {
    if (movingIds.has(wall.id)) continue;
    if (!storeys.has(wall.storeyId)) continue;

    const aTouches = touchesAny(wall.a, movingWalls);
    const bTouches = touchesAny(wall.b, movingWalls);
    if (!aTouches && !bTouches) continue;

    ops.push({
      type: 'wall.move',
      payload: {
        wallId: wall.id,
        a: aTouches ? shiftPt(wall.a, target.axis, deltaMm) : wall.a,
        b: bTouches ? shiftPt(wall.b, target.axis, deltaMm) : wall.b,
      },
    });
  }

  return { ok: true, ops, label: LABELS['wall-gap'] };
}

/** The first readable centreline coordinate in a tick's wall list. */
function firstCoordinate(walls: readonly Wall[], axis: DimAxis): number | null {
  for (const wall of walls) {
    const c = wallCoordinate(wall, axis);
    if (c !== null) return c;
  }
  return null;
}

/**
 * Does `p` lie on any of these walls' centrelines?
 *
 * `pointOnSegment` from `@garh/model` is exact integer arithmetic (cross product
 * zero plus a bounding-box test), so a T-junction is detected when it is
 * genuinely a T-junction and not when it is 1 mm away. A tolerant version here
 * would drag walls that merely pass close by.
 */
function touchesAny(p: Pt, walls: readonly Wall[]): boolean {
  for (const wall of walls) {
    if (pointOnSegment(p, { a: wall.a, b: wall.b })) return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// opening-gap / opening-width
// ---------------------------------------------------------------------------

/**
 * Clear length of the host wall. Openings are positioned along the centreline
 * from `wall.a` (§4 op 14), so this is the space `offsetMm` lives in.
 */
function wallLengthMm(wall: Wall): number {
  return Math.round(Math.hypot(wall.b.x - wall.a.x, wall.b.y - wall.a.y));
}

function editOpeningGap(
  house: HouseModel,
  target: Extract<DimensionEditTarget, { kind: 'opening-gap' }>,
  valueMm: number,
): DimensionEditResult {
  const opening = house.openings.find((o) => o.id === target.openingId);
  if (opening === undefined) {
    return { ok: false, reason: 'That opening is gone. Re-select the dimension.' };
  }
  const wall = house.walls.find((w) => w.id === opening.wallId);
  if (wall === undefined) {
    return { ok: false, reason: 'The wall this opening sits in is gone.' };
  }

  const half = Math.floor(opening.widthMm / 2);
  // `anchorAlongMm` is a fixed point on the wall — a wall end or the far jamb
  // of the neighbouring opening. `before` measures forward from it, `after`
  // measures backward towards it.
  const offsetMm =
    target.side === 'before'
      ? target.anchorAlongMm + valueMm + half
      : target.anchorAlongMm - valueMm - half;

  const lengthMm = wallLengthMm(wall);
  if (offsetMm - half < 0 || offsetMm + (opening.widthMm - half) > lengthMm) {
    return {
      ok: false,
      reason: 'That would push the opening past the end of its wall. Widen the wall first.',
    };
  }

  return {
    ok: true,
    ops: [{ type: 'opening.move', payload: { openingId: opening.id, offsetMm } }],
    label: LABELS['opening-gap'],
  };
}

function editOpeningWidth(
  house: HouseModel,
  target: Extract<DimensionEditTarget, { kind: 'opening-width' }>,
  valueMm: number,
): DimensionEditResult {
  const opening = house.openings.find((o) => o.id === target.openingId);
  if (opening === undefined) {
    return { ok: false, reason: 'That opening is gone. Re-select the dimension.' };
  }
  if (opening.widthMm === valueMm) return { ok: false, reason: 'That is already the width.' };

  return {
    ok: true,
    // Width only. Growing an opening about its stored centre is what an
    // architect means by "make this window 1500" — `fold` re-validates the end
    // margins and rejects the op if the new width no longer fits, which is the
    // right place for that judgement (it owns the invariant).
    ops: [{ type: 'opening.resize', payload: { openingId: opening.id, widthMm: valueMm } }],
    label: LABELS['opening-width'],
  };
}

// ---------------------------------------------------------------------------
// Room area — the other half of "no dead text"
// ---------------------------------------------------------------------------

/**
 * Clicking a room's AREA sets its target area (§4 op 20), not its geometry.
 *
 * This is a real distinction and the UI must not blur it: the solver owns
 * geometry, and a room's actual area is a consequence of where its walls are.
 * `room.set_target` records the intent — "this bedroom should be 12 m²" — which
 * the solver honours on the next partial re-solve and the compliance strip can
 * report against. Editing the area label and watching walls jump would be a
 * geometry edit disguised as a label edit.
 */
export function roomTargetAreaOp(roomId: string, targetAreaMm2: number | null): Op {
  return { type: 'room.set_target', payload: { roomId, targetAreaMm2 } };
}

/**
 * Clicking a room's NAME assigns it (§4 op 19).
 *
 * `room.assign` carries the type as well as the name because the op replaces
 * both; passing the room's CURRENT type is what makes a rename a rename rather
 * than a silent reclassification back to `unassigned`.
 */
export function roomNameOp(roomId: string, type: RoomType, name: string): Op {
  return { type: 'room.assign', payload: { roomId, type, name } };
}
