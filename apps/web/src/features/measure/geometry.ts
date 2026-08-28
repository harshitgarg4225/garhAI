/**
 * geometry.ts — the arithmetic behind every number this feature shows.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * NOTHING IS COMPUTED HERE THAT THE MODEL CORE ALREADY COMPUTES
 * ────────────────────────────────────────────────────────────────────────────
 * `distMm`, `polygonAreaMm2`, `polygonPerimeterMm`, `polygonCentroid` and
 * `ptRound` all come from `@garh/model`, which is golden-tested against its
 * Python twin. A measure tool that hand-rolled `Math.hypot` would eventually
 * report a length the drawing set disagrees with, and the whole point of a
 * measure tool is that its number IS the drawing's number. The only arithmetic
 * this file owns is the angle (nobody else needs it) and the two aggregation
 * rules below, which are decisions rather than formulas.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * DECISION 1 — THE TOTAL IS THE SUM OF THE PARTS AS DISPLAYED
 * ────────────────────────────────────────────────────────────────────────────
 * `distMm` rounds each leg to whole millimetres (half away from zero). The
 * total of a chain therefore sums the ROUNDED legs, not the exact ones. Summing
 * the exact lengths and rounding once would be marginally more accurate and
 * visibly wrong: an architect who adds up the four numbers on screen would get
 * a fifth number that disagrees with the total we printed beside them, and
 * would be right to distrust the tool. Worst case here is ±0.5 mm per leg.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * DECISION 2 — HALF AWAY FROM ZERO, NEVER `Math.round`
 * ────────────────────────────────────────────────────────────────────────────
 * Every float→integer step goes through `ptRound`/`roundMm`. `Math.round` is
 * half-UP, so it rounds −500.5 to −500 while +500.5 goes to +501: a midpoint
 * label on a wall drawn westwards would sit one millimetre off from the same
 * wall drawn eastwards. `geometry.test.ts` asserts the difference on a real
 * midpoint rather than trusting the import.
 */

import {
  distMm,
  polygonAreaMm2,
  polygonCentroid,
  polygonPerimeterMm,
  ptRound,
  type Polygon,
  type Pt,
} from '@garh/model';

// ---------------------------------------------------------------------------
// Lengths
// ---------------------------------------------------------------------------

/**
 * Per-leg lengths of a polyline, in click order. `[]` for fewer than 2 points.
 *
 * Exact for axis-aligned legs (`distMm` short-circuits those), which is most of
 * what gets measured in a rectilinear plan.
 */
export function segmentLengthsMm(points: readonly Pt[]): number[] {
  const out: number[] = [];
  for (let i = 0; i + 1 < points.length; i++) {
    const a = points[i];
    const b = points[i + 1];
    if (a === undefined || b === undefined) continue;
    out.push(distMm(a, b));
  }
  return out;
}

/** Running total of a chain — the sum of {@link segmentLengthsMm}. */
export function totalLengthMm(points: readonly Pt[]): number {
  let total = 0;
  for (const mm of segmentLengthsMm(points)) total += mm;
  return total;
}

/**
 * Midpoint, integer mm, half away from zero.
 *
 * Not a formatting detail: this is where labels are anchored, and two points
 * an odd number of millimetres apart land the midpoint on exactly `.5`.
 */
export function midpointMm(a: Pt, b: Pt): Pt {
  return ptRound((a.x + b.x) / 2, (a.y + b.y) / 2);
}

// ---------------------------------------------------------------------------
// Angle
// ---------------------------------------------------------------------------

/**
 * The interior angle at `vertex` between the arms to `a` and `b`, in degrees
 * in [0, 180]. `null` when either arm has zero length — an angle between a
 * point and itself is not 0°, it is undefined, and reporting 0 would be a
 * confident wrong answer of exactly the kind this codebase keeps finding.
 *
 * `atan2(|cross|, dot)` rather than `acos(dot / (|u||v|))`: the acos form loses
 * all precision near 0° and 180° (its argument saturates at ±1) and can escape
 * the domain by a float epsilon and produce NaN. The atan2 form is
 * well-conditioned everywhere, and — the property the spec pins — returns
 * EXACTLY 90 for perpendicular integer arms, because `dot` is then exactly 0
 * and `atan2(positive, 0)` is exactly π/2.
 */
export function interiorAngleDeg(a: Pt, vertex: Pt, b: Pt): number | null {
  const ux = a.x - vertex.x;
  const uy = a.y - vertex.y;
  const vx = b.x - vertex.x;
  const vy = b.y - vertex.y;
  if ((ux === 0 && uy === 0) || (vx === 0 && vy === 0)) return null;
  const cross = ux * vy - uy * vx;
  const dot = ux * vx + uy * vy;
  return (Math.atan2(Math.abs(cross), dot) * 180) / Math.PI;
}

/**
 * The angle a three-point measurement reports: the corner at `points[1]`.
 * `null` for a list that is not three points, or for a degenerate arm.
 */
export function measurementAngleDeg(points: readonly Pt[]): number | null {
  if (points.length !== 3) return null;
  const a = points[0];
  const v = points[1];
  const b = points[2];
  if (a === undefined || v === undefined || b === undefined) return null;
  return interiorAngleDeg(a, v, b);
}

// ---------------------------------------------------------------------------
// Area
// ---------------------------------------------------------------------------

/**
 * Area of the ring in mm², via the model's shoelace. Zero below three points.
 *
 * The ring is OPEN (see `Measurement.points`): the closing edge is implied.
 * `polygonAreaMm2` treats its input as a closed ring already, so passing a
 * duplicated first vertex would add a zero-length edge — harmless for area, but
 * it would inflate {@link ringPerimeterMm}, so the invariant is stated once
 * here and enforced by the session rather than being re-checked everywhere.
 */
export function ringAreaMm2(ring: readonly Pt[]): number {
  if (ring.length < 3) return 0;
  return polygonAreaMm2(ring as Polygon);
}

/** Perimeter of the closed ring, including the implied closing edge. */
export function ringPerimeterMm(ring: readonly Pt[]): number {
  if (ring.length < 2) return 0;
  if (ring.length === 2) return totalLengthMm(ring);
  return polygonPerimeterMm(ring as Polygon);
}

/**
 * Where an area label goes: the centroid of the AREA, not of the vertices.
 * `null` for an empty ring, since there is nowhere to put the label.
 */
export function ringCentroidMm(ring: readonly Pt[]): Pt | null {
  if (ring.length === 0) return null;
  return polygonCentroid(ring as Polygon);
}

/**
 * Would a click at `candidate` close the ring?
 *
 * `toleranceMm` is the SNAP tolerance (`snapToleranceMm(mmPerPx)`), not a
 * constant: the click that closes a polygon has to be as forgiving as the click
 * that started it, or the architect chases a vertex they cannot land on at a
 * zoomed-out scale.
 */
export function closesRing(points: readonly Pt[], candidate: Pt, toleranceMm: number): boolean {
  if (points.length < 3) return false;
  const first = points[0];
  if (first === undefined) return false;
  return distMm(first, candidate) <= toleranceMm;
}

// ---------------------------------------------------------------------------
// Draft → drawable points
// ---------------------------------------------------------------------------

/**
 * The polyline a draft draws: the clicked points plus the rubber-band cursor,
 * unless the cursor is already the last clicked point (which happens on the
 * frame a click lands and would otherwise push a zero-length leg into the
 * readouts).
 */
export function draftPolyline(points: readonly Pt[], cursor: Pt | null): Pt[] {
  if (cursor === null) return [...points];
  const last = points[points.length - 1];
  if (last !== undefined && last.x === cursor.x && last.y === cursor.y) return [...points];
  return [...points, cursor];
}
