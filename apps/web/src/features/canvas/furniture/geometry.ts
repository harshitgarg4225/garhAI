/**
 * Footprint, clearance and overlap geometry — pure, integer, no renderer.
 *
 * ## Why doubled millimetres
 *
 * A queen bed is 1525 mm wide. Half of that is 762.5 mm, so the moment you
 * express a footprint as "centre ± half-extent" in millimetres you are either
 * rounding (and losing a millimetre of width) or carrying a float (and losing
 * the integer-mm guarantee the whole model core is built on).
 *
 * Every function here works in DOUBLED millimetres — 1 unit = 0.5 mm — where
 * the half-extent of any integer dimension is itself an integer. Corners are
 * exact, the separating-axis test is exact integer arithmetic with no epsilon,
 * and nothing needs a tolerance constant. `cornersToMm` converts back for
 * display; `ops.ts` never sees a doubled coordinate at all.
 *
 * Magnitudes: a 100 m plot is 200 000 doubled units, and the SAT projections
 * multiply two such numbers — about 4·10^10, comfortably inside the 2^53 range
 * where JavaScript integers are exact.
 *
 * ## The conversion boundary
 *
 * IN: integer mm from the model, integer degrees from the tool.
 * OUT: doubled-mm integers (geometry) and integer mm (display).
 * Screen pixels never enter this file, and no value produced here becomes an op
 * payload — `ops.ts` builds payloads from the pose, not from these corners.
 */

import { pointInPolygon, roundHalfAwayFromZero, type Pt } from '@garh/model';

import type { Bounds2x, CatalogueItem, Obstacle, Pose, Quad2x, RoomLike } from './types';

/** Doubled-mm units per millimetre. Named so call sites read as intent. */
export const MM_2X = 2;

// ---------------------------------------------------------------------------
// Rotation — integer degrees, always
// ---------------------------------------------------------------------------

/**
 * Any degree value → the op contract: an integer in [0, 360).
 *
 * A free-rotate drag produces a float and a shift-R produces a negative; both
 * land here before they can reach a payload. `furniture.set` validates
 * `rotationDeg` as an int in [-359, 359] (`packages/model/src/validate.ts`), so
 * [0, 360) is inside the contract with room to spare.
 */
export function normaliseRotationDeg(deg: number): number {
  if (!Number.isFinite(deg)) return 0;
  const i = roundHalfAwayFromZero(deg);
  return ((i % 360) + 360) % 360;
}

/** Rotate a pose by a whole number of degrees (R = +90, Shift-R = -90). */
export function rotateBy(currentDeg: number, deltaDeg: number): number {
  return normaliseRotationDeg(currentDeg + deltaDeg);
}

/**
 * The angle from a drag: the bearing from `centre` to `pointer`, CCW from +X,
 * snapped to `stepDeg` whole degrees. `stepDeg` of 1 is genuinely free rotation
 * that still cannot produce a fractional payload.
 */
export function angleFromDrag(centre: Pt, pointer: Pt, stepDeg = 1): number {
  const dx = pointer.x - centre.x;
  const dy = pointer.y - centre.y;
  if (dx === 0 && dy === 0) return 0;
  const raw = (Math.atan2(dy, dx) * 180) / Math.PI;
  const step = Math.max(1, Math.trunc(stepDeg));
  return normaliseRotationDeg(roundHalfAwayFromZero(raw / step) * step);
}

// ---------------------------------------------------------------------------
// Snapping
// ---------------------------------------------------------------------------

/**
 * Snap a plan point onto the module grid. `stepMm` of 0 (snap off) still
 * rounds to whole millimetres — "off" means "no grid", never "float".
 */
export function snapPtMm(p: Pt, stepMm: number): Pt {
  if (stepMm <= 0) {
    return { x: roundHalfAwayFromZero(p.x), y: roundHalfAwayFromZero(p.y) };
  }
  return {
    x: roundHalfAwayFromZero(p.x / stepMm) * stepMm,
    y: roundHalfAwayFromZero(p.y / stepMm) * stepMm,
  };
}

// ---------------------------------------------------------------------------
// Quads
// ---------------------------------------------------------------------------

/**
 * Rotate a local doubled-mm offset by `deg` CCW and translate to a centre.
 *
 * Multiples of 90° take an exact integer path — no trigonometry, no rounding —
 * because those are the rotations 95% of placements use and an architect will
 * notice a wardrobe that is one millimetre off the wall. Other angles round to
 * the nearest doubled unit, i.e. to half a millimetre.
 */
function place2x(lx: number, ly: number, deg: number, cx2: number, cy2: number): Pt {
  switch (deg) {
    case 0:
      return { x: cx2 + lx, y: cy2 + ly };
    case 90:
      return { x: cx2 - ly, y: cy2 + lx };
    case 180:
      return { x: cx2 - lx, y: cy2 - ly };
    case 270:
      return { x: cx2 + ly, y: cy2 - lx };
    default: {
      const rad = (deg * Math.PI) / 180;
      const c = Math.cos(rad);
      const s = Math.sin(rad);
      return {
        x: cx2 + roundHalfAwayFromZero(lx * c - ly * s),
        y: cy2 + roundHalfAwayFromZero(lx * s + ly * c),
      };
    }
  }
}

/**
 * A rectangle as a doubled-mm quad, CCW, corners in local order
 * (−X−Y, +X−Y, +X+Y, −X+Y) before rotation.
 *
 * `halfW2x` and `halfD2x` are half-extents in DOUBLED mm, which is to say they
 * equal the width and depth in millimetres. That identity is the whole trick.
 */
export function rectQuad2x(
  centreMm: Pt,
  halfW2x: number,
  halfD2x: number,
  rotationDeg: number,
  frontOffset2x = 0,
): Quad2x {
  const deg = normaliseRotationDeg(rotationDeg);
  const cx2 = centreMm.x * MM_2X;
  const cy2 = centreMm.y * MM_2X;
  const y0 = frontOffset2x - halfD2x;
  const y1 = frontOffset2x + halfD2x;
  return [
    place2x(-halfW2x, y0, deg, cx2, cy2),
    place2x(halfW2x, y0, deg, cx2, cy2),
    place2x(halfW2x, y1, deg, cx2, cy2),
    place2x(-halfW2x, y1, deg, cx2, cy2),
  ];
}

/** The item's own footprint — what it occupies, clearance excluded. */
export function footprintQuad2x(item: CatalogueItem, pose: Pose): Quad2x {
  return rectQuad2x(pose.pt, item.widthMm, item.depthMm, pose.rotationDeg);
}

/**
 * The access strip in front of the item (+Y local), or `null` when the item
 * needs none.
 *
 * The strip spans the item's full width and sits entirely OUTSIDE the
 * footprint: from `+depth/2` to `+depth/2 + clearance`. Its centre is therefore
 * `(depth + clearance) / 2` ahead of the item centre, which in doubled units is
 * `depthMm + clearanceMm` — integer again.
 */
export function clearanceQuad2x(item: CatalogueItem, pose: Pose): Quad2x | null {
  if (item.clearanceMm <= 0) return null;
  return rectQuad2x(
    pose.pt,
    item.widthMm,
    item.clearanceMm,
    pose.rotationDeg,
    item.depthMm + item.clearanceMm,
  );
}

/**
 * Footprint + clearance as one rectangle — the shape the solver's fit test
 * packs (`width × (depth + clearance)`, `services/solver/furniture_fit.py`).
 * Exported so a future "does this room take its standard set?" check in the
 * editor can use the same rectangle the gate used.
 */
export function occupancyQuad2x(item: CatalogueItem, pose: Pose): Quad2x {
  return rectQuad2x(
    pose.pt,
    item.widthMm,
    item.depthMm + item.clearanceMm,
    pose.rotationDeg,
    item.clearanceMm,
  );
}

/**
 * Doubled-mm corners → integer millimetres, for drawing and for tests.
 *
 * Rounds OUTWARD (away from zero), so an odd-width item's ring can read half a
 * millimetre large but never half a millimetre small. That direction is chosen
 * on purpose: a footprint that under-reports its size is the one that produces
 * a drawing where two things secretly overlap.
 */
export function cornersToMm(quad: Quad2x): Pt[] {
  return quad.map((p) => ({
    x: roundHalfAwayFromZero(p.x / MM_2X),
    y: roundHalfAwayFromZero(p.y / MM_2X),
  }));
}

/**
 * The same corners rounded INWARD, toward the quad's own centre.
 *
 * Used only for "is this inside the room?". With outward rounding, a wardrobe
 * of odd width sitting flush in a room it exactly fits would be reported as
 * sticking out by half a millimetre — a warning about nothing, which is worse
 * than no warning at all.
 */
export function cornersToMmInward(quad: Quad2x): Pt[] {
  let sx = 0;
  let sy = 0;
  for (const p of quad) {
    sx += p.x;
    sy += p.y;
  }
  const cx = sx / 4;
  const cy = sy / 4;
  return quad.map((p) => ({
    x: p.x >= cx ? Math.floor(p.x / MM_2X) : Math.ceil(p.x / MM_2X),
    y: p.y >= cy ? Math.floor(p.y / MM_2X) : Math.ceil(p.y / MM_2X),
  }));
}

// ---------------------------------------------------------------------------
// Overlap — broad phase then exact SAT
// ---------------------------------------------------------------------------

export function bounds2x(quad: Quad2x): Bounds2x {
  const [a, b, c, d] = quad;
  return {
    minX: Math.min(a.x, b.x, c.x, d.x),
    minY: Math.min(a.y, b.y, c.y, d.y),
    maxX: Math.max(a.x, b.x, c.x, d.x),
    maxY: Math.max(a.y, b.y, c.y, d.y),
  };
}

/** Strict: rectangles that share only an edge are NOT overlapping. */
export function boundsOverlap(a: Bounds2x, b: Bounds2x): boolean {
  return a.minX < b.maxX && b.minX < a.maxX && a.minY < b.maxY && b.minY < a.maxY;
}

/**
 * Separating-axis test for two convex quads, exact in integer arithmetic.
 *
 * Touching is not overlapping: a wardrobe pushed flush against a wall is
 * correct practice, not a collision, and a test that flagged it would train
 * architects to ignore the warning colour within an hour.
 */
export function quadsOverlap(a: Quad2x, b: Quad2x): boolean {
  return !hasSeparatingAxis(a, b) && !hasSeparatingAxis(b, a);
}

function hasSeparatingAxis(from: Quad2x, other: Quad2x): boolean {
  const [p0, p1, p2, p3] = from;
  return (
    axisSeparates(p0, p1, from, other) ||
    axisSeparates(p1, p2, from, other) ||
    axisSeparates(p2, p3, from, other) ||
    axisSeparates(p3, p0, from, other)
  );
}

/**
 * Does the normal of edge p→q separate the two quads?
 *
 * Unrolled over the four corners rather than looped: it runs once per obstacle
 * that survives the bounds test, on every pointer move, and it allocates
 * nothing — no arrays, no closures, no intermediate points.
 */
function axisSeparates(p: Pt, q: Pt, a: Quad2x, b: Quad2x): boolean {
  // Outward normal of edge p→q for a CCW ring. (Sign is irrelevant to SAT.)
  const nx = q.y - p.y;
  const ny = p.x - q.x;
  if (nx === 0 && ny === 0) return false;

  const [a0, a1, a2, a3] = a;
  const a0d = a0.x * nx + a0.y * ny;
  const a1d = a1.x * nx + a1.y * ny;
  const a2d = a2.x * nx + a2.y * ny;
  const a3d = a3.x * nx + a3.y * ny;
  const minA = Math.min(a0d, a1d, a2d, a3d);
  const maxA = Math.max(a0d, a1d, a2d, a3d);

  const [b0, b1, b2, b3] = b;
  const b0d = b0.x * nx + b0.y * ny;
  const b1d = b1.x * nx + b1.y * ny;
  const b2d = b2.x * nx + b2.y * ny;
  const b3d = b3.x * nx + b3.y * ny;
  const minB = Math.min(b0d, b1d, b2d, b3d);
  const maxB = Math.max(b0d, b1d, b2d, b3d);

  return maxA <= minB || maxB <= minA;
}

// ---------------------------------------------------------------------------
// Model geometry → obstacles
// ---------------------------------------------------------------------------

/** The subset of `Wall` this feature reads. */
export interface WallLike {
  readonly id: string;
  readonly storeyId: string;
  readonly a: Pt;
  readonly b: Pt;
  readonly thicknessMm: number;
}

/**
 * A wall as a doubled-mm quad: the centreline rectangle, width = thickness.
 *
 * Deliberately NOT extended by half a thickness at each end. Ends are where
 * walls meet, and extending them would make every junction claim territory
 * twice; the cost is that an item tucked exactly into an internal corner can
 * miss a millimetre of the joint, which no architect will ever notice and no
 * drawing will ever show.
 */
export function wallQuad2x(wall: WallLike): Quad2x {
  const ax = wall.a.x * MM_2X;
  const ay = wall.a.y * MM_2X;
  const bx = wall.b.x * MM_2X;
  const by = wall.b.y * MM_2X;
  const dx = bx - ax;
  const dy = by - ay;
  const len = Math.sqrt(dx * dx + dy * dy);
  if (len === 0) {
    const h = wall.thicknessMm;
    return [
      { x: ax - h, y: ay - h },
      { x: ax + h, y: ay - h },
      { x: ax + h, y: ay + h },
      { x: ax - h, y: ay + h },
    ];
  }
  // Half-thickness in doubled units == thicknessMm. Axis-aligned walls (all but
  // a handful in practice) give exact ±1/0 normals and therefore exact corners.
  const nx = (-dy / len) * wall.thicknessMm;
  const ny = (dx / len) * wall.thicknessMm;
  const r = roundHalfAwayFromZero;
  return [
    { x: r(ax + nx), y: r(ay + ny) },
    { x: r(bx + nx), y: r(by + ny) },
    { x: r(bx - nx), y: r(by - ny) },
    { x: r(ax - nx), y: r(ay - ny) },
  ];
}

/** Build one obstacle record (quad + cached bounds) from a quad. */
export function toObstacle(
  id: string,
  kind: Obstacle['kind'],
  label: string,
  quad: Quad2x,
): Obstacle {
  return { id, kind, label, quad, bounds: bounds2x(quad) };
}

// ---------------------------------------------------------------------------
// Rooms
// ---------------------------------------------------------------------------

/** The room whose clear polygon contains this point, or null. */
export function roomAtPt(rooms: readonly RoomLike[], p: Pt): RoomLike | null {
  for (const room of rooms) {
    if (pointInPolygon(p, room.polygon) !== 'outside') return room;
  }
  return null;
}

/**
 * True when every corner of the footprint is inside (or on) the room.
 *
 * Corners round inward — see {@link cornersToMmInward} — so an item that
 * exactly fits its room is reported as fitting.
 */
export function quadInsideRoom(quad: Quad2x, room: RoomLike): boolean {
  for (const corner of cornersToMmInward(quad)) {
    if (pointInPolygon(corner, room.polygon) === 'outside') return false;
  }
  return true;
}
