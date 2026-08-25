/**
 * geometry.ts — 2D primitives in INTEGER MILLIMETRES.
 *
 * EXACTNESS CONTRACT (read before using any of this):
 *
 *   EXACT (integer arithmetic only, no rounding anywhere):
 *     - `polygonDoubledAreaMm2`, `polygonAreaMm2` for even doubled areas
 *     - `orientation`, `cross`, `dot`, `pointOnSegment`, `pointInPolygon`
 *     - `bbox`, `polygonIsSimple`, `segmentsOverlapCollinear`
 *     - `unionAxisAlignedRects` (grid decomposition of axis-aligned input)
 *     - `dedupeCollinear`, `polygonsCongruent`
 *
 *   ROUNDED (rational result, rounded to whole mm with round-half-away-from-zero;
 *   deterministic and identical in the Python mirror because the arithmetic is
 *   +,-,*,/ on IEEE-754 doubles only — no transcendental functions):
 *     - `segmentIntersection` (the intersection point)
 *     - `offsetPolygon` (the offset vertices)
 *     - `polygonCentroid`
 *     - `segmentLengthMm`, `polygonPerimeterMm`
 *
 *   APPROXIMATE (double-precision area; used only for ranking/matching, never
 *   for stored geometry or compliance numbers):
 *     - `polygonIntersectionAreaMm2`, `polygonUnionAreaMm2`, `jaccard`
 *
 * Angles are never used for ordering: `compareAngleAround` is an exact integer
 * comparator, so half-edge traversal (rooms.ts) has no floating-point input.
 */

import { roundHalfAwayFromZero } from './units';

/** A point in plot-local mm. Origin = plot SW corner, +X east, +Y north. */
export interface Pt {
  readonly x: number;
  readonly y: number;
}

/** A directed segment. */
export interface Seg {
  readonly a: Pt;
  readonly b: Pt;
}

/**
 * A simple polygon: implicitly closed, NO repeated last vertex, at least 3
 * vertices. Stored counter-clockwise by convention (see `ensureCcw`).
 */
export type Polygon = readonly Pt[];

/** Axis-aligned bounding box in mm. */
export interface Bbox {
  readonly minX: number;
  readonly minY: number;
  readonly maxX: number;
  readonly maxY: number;
}

export type Orientation = 'ccw' | 'cw' | 'degenerate';

/** Construct a point, asserting integer mm. */
export function pt(x: number, y: number): Pt {
  if (!Number.isSafeInteger(x) || !Number.isSafeInteger(y)) {
    throw new RangeError(`Pt must be integer mm, got (${String(x)}, ${String(y)})`);
  }
  return { x, y };
}

/** Round a float pair into an integer Pt (the ONLY sanctioned float->Pt door). */
export function ptRound(x: number, y: number): Pt {
  return { x: roundHalfAwayFromZero(x), y: roundHalfAwayFromZero(y) };
}

export function ptEq(a: Pt, b: Pt): boolean {
  return a.x === b.x && a.y === b.y;
}

export function ptAdd(a: Pt, b: Pt): Pt {
  return { x: a.x + b.x, y: a.y + b.y };
}

export function ptSub(a: Pt, b: Pt): Pt {
  return { x: a.x - b.x, y: a.y - b.y };
}

/** Stable string key for maps/sets. */
export function ptKey(p: Pt): string {
  return `${p.x},${p.y}`;
}

/** Inverse of {@link ptKey}. */
export function ptFromKey(key: string): Pt {
  const i = key.indexOf(',');
  return { x: Number(key.slice(0, i)), y: Number(key.slice(i + 1)) };
}

/** Lexicographic (x, then y) comparison — the canonical vertex order. */
export function comparePt(a: Pt, b: Pt): number {
  if (a.x !== b.x) return a.x < b.x ? -1 : 1;
  if (a.y !== b.y) return a.y < b.y ? -1 : 1;
  return 0;
}

/** EXACT cross product (b-a) × (c-a). Sign gives turn direction. */
export function cross(a: Pt, b: Pt, c: Pt): number {
  return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
}

/** EXACT dot product (b-a) · (c-a). */
export function dot(a: Pt, b: Pt, c: Pt): number {
  return (b.x - a.x) * (c.x - a.x) + (b.y - a.y) * (c.y - a.y);
}

/** EXACT squared distance in mm². */
export function distSqMm2(a: Pt, b: Pt): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  return dx * dx + dy * dy;
}

/** ROUNDED distance in mm (exact for axis-aligned pairs). */
export function distMm(a: Pt, b: Pt): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  if (dx === 0) return Math.abs(dy);
  if (dy === 0) return Math.abs(dx);
  return roundHalfAwayFromZero(Math.sqrt(dx * dx + dy * dy));
}

/** ROUNDED segment length in mm. */
export function segmentLengthMm(s: Seg): number {
  return distMm(s.a, s.b);
}

/** EXACT squared segment length. */
export function segmentLengthSqMm2(s: Seg): number {
  return distSqMm2(s.a, s.b);
}

/** True when the segment has zero length (the `WALL_ZERO_LENGTH` invariant). */
export function isDegenerateSeg(s: Seg): boolean {
  return ptEq(s.a, s.b);
}

/**
 * Point at `alongMm` from `s.a` towards `s.b`. ROUNDED. Exact for axis-aligned
 * segments, which is every wall in the MVP (orthogonal-only).
 */
export function pointAlongSeg(s: Seg, alongMm: number): Pt {
  const len = segmentLengthMm(s);
  if (len === 0) return { x: s.a.x, y: s.a.y };
  const t = alongMm / len;
  return ptRound(s.a.x + (s.b.x - s.a.x) * t, s.a.y + (s.b.y - s.a.y) * t);
}

/** Unit-ish normal (left side of a->b) scaled to `lengthMm`. ROUNDED. */
export function segNormalOffset(s: Seg, lengthMm: number): Pt {
  const dx = s.b.x - s.a.x;
  const dy = s.b.y - s.a.y;
  const len = Math.sqrt(dx * dx + dy * dy);
  if (len === 0) return { x: 0, y: 0 };
  return ptRound((-dy / len) * lengthMm, (dx / len) * lengthMm);
}

// ---------------------------------------------------------------------------
// Bounding boxes
// ---------------------------------------------------------------------------

export function bbox(points: readonly Pt[]): Bbox {
  if (points.length === 0) throw new RangeError('bbox of empty point list');
  let minX = points[0].x;
  let minY = points[0].y;
  let maxX = minX;
  let maxY = minY;
  for (let i = 1; i < points.length; i++) {
    const p = points[i];
    if (p.x < minX) minX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.x > maxX) maxX = p.x;
    if (p.y > maxY) maxY = p.y;
  }
  return { minX, minY, maxX, maxY };
}

export function bboxIntersects(a: Bbox, b: Bbox): boolean {
  return a.minX <= b.maxX && b.minX <= a.maxX && a.minY <= b.maxY && b.minY <= a.maxY;
}

export function bboxContainsPt(b: Bbox, p: Pt): boolean {
  return p.x >= b.minX && p.x <= b.maxX && p.y >= b.minY && p.y <= b.maxY;
}

/** EXACT bbox area in mm². */
export function bboxAreaMm2(b: Bbox): number {
  return (b.maxX - b.minX) * (b.maxY - b.minY);
}

export function bboxWidthMm(b: Bbox): number {
  return b.maxX - b.minX;
}

export function bboxHeightMm(b: Bbox): number {
  return b.maxY - b.minY;
}

/** Grow a bbox by `mm` on every side. */
export function bboxInflate(b: Bbox, mm: number): Bbox {
  return { minX: b.minX - mm, minY: b.minY - mm, maxX: b.maxX + mm, maxY: b.maxY + mm };
}

// ---------------------------------------------------------------------------
// Polygon basics
// ---------------------------------------------------------------------------

/**
 * EXACT signed doubled area (shoelace sum). Positive = CCW.
 * Doubled so the result stays an exact integer.
 *
 * Magnitude check: coordinates are ≤ ~1e6 mm (1 km), so each term is ≤ 1e12 and
 * a 100-vertex polygon sums to ≤ 1e14 — comfortably inside Number.MAX_SAFE_INTEGER.
 */
export function polygonDoubledAreaMm2(poly: Polygon): number {
  const n = poly.length;
  if (n < 3) return 0;
  let sum = 0;
  for (let i = 0; i < n; i++) {
    const p = poly[i];
    const q = poly[(i + 1) % n];
    sum += p.x * q.y - q.x * p.y;
  }
  return sum;
}

/** EXACT signed area in mm² when the doubled area is even; otherwise ROUNDED. */
export function polygonSignedAreaMm2(poly: Polygon): number {
  const doubled = polygonDoubledAreaMm2(poly);
  return doubled % 2 === 0 ? doubled / 2 : roundHalfAwayFromZero(doubled / 2);
}

/** Absolute area in mm² (see {@link polygonSignedAreaMm2} for exactness). */
export function polygonAreaMm2(poly: Polygon): number {
  return Math.abs(polygonSignedAreaMm2(poly));
}

export function polygonOrientation(poly: Polygon): Orientation {
  const doubled = polygonDoubledAreaMm2(poly);
  if (doubled === 0) return 'degenerate';
  return doubled > 0 ? 'ccw' : 'cw';
}

export function reversePolygon(poly: Polygon): Pt[] {
  return poly.slice().reverse();
}

/** Return the polygon CCW-oriented (identity if already CCW). */
export function ensureCcw(poly: Polygon): Pt[] {
  return polygonOrientation(poly) === 'cw' ? reversePolygon(poly) : poly.slice();
}

/** ROUNDED centroid of the polygon AREA (not of its vertices). */
export function polygonCentroid(poly: Polygon): Pt {
  const n = poly.length;
  if (n === 0) throw new RangeError('centroid of empty polygon');
  if (n === 1) return { x: poly[0].x, y: poly[0].y };
  const doubled = polygonDoubledAreaMm2(poly);
  if (doubled === 0) {
    // degenerate (collinear) — fall back to the vertex mean
    let sx = 0;
    let sy = 0;
    for (const p of poly) {
      sx += p.x;
      sy += p.y;
    }
    return ptRound(sx / n, sy / n);
  }
  let cx = 0;
  let cy = 0;
  for (let i = 0; i < n; i++) {
    const p = poly[i];
    const q = poly[(i + 1) % n];
    const f = p.x * q.y - q.x * p.y;
    cx += (p.x + q.x) * f;
    cy += (p.y + q.y) * f;
  }
  return ptRound(cx / (3 * doubled), cy / (3 * doubled));
}

/** ROUNDED perimeter in mm (exact for rectilinear polygons). */
export function polygonPerimeterMm(poly: Polygon): number {
  let total = 0;
  for (let i = 0; i < poly.length; i++) {
    total += distMm(poly[i], poly[(i + 1) % poly.length]);
  }
  return total;
}

/** Edges of a polygon as segments, in vertex order. */
export function polygonEdges(poly: Polygon): Seg[] {
  const out: Seg[] = [];
  for (let i = 0; i < poly.length; i++) {
    out.push({ a: poly[i], b: poly[(i + 1) % poly.length] });
  }
  return out;
}

/** EXACT: does `p` lie on segment `s` (endpoints included)? */
export function pointOnSegment(p: Pt, s: Seg): boolean {
  if (cross(s.a, s.b, p) !== 0) return false;
  return (
    Math.min(s.a.x, s.b.x) <= p.x &&
    p.x <= Math.max(s.a.x, s.b.x) &&
    Math.min(s.a.y, s.b.y) <= p.y &&
    p.y <= Math.max(s.a.y, s.b.y)
  );
}

export type PointInPolygon = 'inside' | 'outside' | 'boundary';

/**
 * EXACT point-in-polygon (crossing number with integer predicates).
 * Boundary points are reported as `'boundary'`, never guessed.
 */
export function pointInPolygon(p: Pt, poly: Polygon): PointInPolygon {
  const n = poly.length;
  if (n < 3) return 'outside';
  let inside = false;
  for (let i = 0; i < n; i++) {
    const a = poly[i];
    const b = poly[(i + 1) % n];
    if (pointOnSegment(p, { a, b })) return 'boundary';
    // upward/downward crossing of the horizontal ray y = p.y going +X
    if (a.y <= p.y ? b.y > p.y : b.y <= p.y) {
      const side = cross(a, b, p);
      // `side > 0` means p is left of a->b
      if (b.y > a.y ? side > 0 : side < 0) inside = !inside;
    }
  }
  return inside ? 'inside' : 'outside';
}

/** Convenience: inside OR on the boundary. */
export function polygonContains(poly: Polygon, p: Pt): boolean {
  return pointInPolygon(p, poly) !== 'outside';
}

/** EXACT: does the polygon have any self-intersection or repeated vertex? */
export function polygonIsSimple(poly: Polygon): boolean {
  const n = poly.length;
  if (n < 3) return false;
  const seen = new Set<string>();
  for (const p of poly) {
    const k = ptKey(p);
    if (seen.has(k)) return false;
    seen.add(k);
  }
  const edges = polygonEdges(poly);
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const adjacent = j === i + 1 || (i === 0 && j === n - 1);
      const r = segmentIntersection(edges[i], edges[j]);
      if (adjacent) {
        // Adjacent edges legitimately touch at their shared vertex, and three
        // collinear vertices (a redundant point on a straight run) are legal.
        // Only a real overlap of non-zero length is a self-intersection.
        if (r.kind === 'collinear' && !ptEq(r.overlap.a, r.overlap.b)) return false;
      } else if (r.kind !== 'none') {
        return false;
      }
    }
  }
  return true;
}

/**
 * A closed room/plot polygon per the fold invariant "rooms closed": at least 3
 * vertices, non-zero area, no self-intersections, no duplicate vertices.
 */
export function polygonIsClosedRing(poly: Polygon): boolean {
  return poly.length >= 3 && polygonDoubledAreaMm2(poly) !== 0 && polygonIsSimple(poly);
}

/** Remove duplicate consecutive vertices and vertices that are exactly collinear. */
export function dedupeCollinear(poly: Polygon): Pt[] {
  const pts: Pt[] = [];
  for (const p of poly) {
    if (pts.length === 0 || !ptEq(pts[pts.length - 1], p)) pts.push(p);
  }
  while (pts.length > 1 && ptEq(pts[0], pts[pts.length - 1])) pts.pop();
  if (pts.length < 3) return pts;
  const out: Pt[] = [];
  const n = pts.length;
  for (let i = 0; i < n; i++) {
    const prev = pts[(i - 1 + n) % n];
    const cur = pts[i];
    const next = pts[(i + 1) % n];
    if (cross(prev, cur, next) !== 0) out.push(cur);
  }
  // fully collinear input collapses to nothing — return the deduped points
  return out.length >= 3 ? out : pts;
}

/**
 * Remove "spurs" — vertex triples (v, w, v) produced when a planar face walk
 * runs out and back along a dangling wall. Repeats until stable.
 */
export function removeSpurs(ring: Polygon): Pt[] {
  let pts = ring.slice();
  let changed = true;
  while (changed && pts.length >= 3) {
    changed = false;
    for (let i = 0; i < pts.length; i++) {
      const prev = pts[(i - 1 + pts.length) % pts.length];
      const next = pts[(i + 1) % pts.length];
      if (ptEq(prev, next)) {
        // drop pts[i] and one of the duplicates
        const dropA = i;
        const dropB = (i + 1) % pts.length;
        pts = pts.filter((_, idx) => idx !== dropA && idx !== dropB);
        changed = true;
        break;
      }
    }
  }
  return pts;
}

/** Rotation/reflection-insensitive equality of two rings (same vertex set+cycle). */
export function polygonsCongruent(a: Polygon, b: Polygon): boolean {
  if (a.length !== b.length) return false;
  const n = a.length;
  if (n === 0) return true;
  for (const dir of [1, -1] as const) {
    for (let off = 0; off < n; off++) {
      let ok = true;
      for (let i = 0; i < n; i++) {
        const j = dir === 1 ? (off + i) % n : (((off - i) % n) + n) % n;
        if (!ptEq(a[i], b[j])) {
          ok = false;
          break;
        }
      }
      if (ok) return true;
    }
  }
  return false;
}

/**
 * CANONICAL FORM of a ring, and part of the state-hash contract: collinear
 * vertices removed, counter-clockwise, rotated to start at the lexicographically
 * smallest vertex. Two rings describing the same area always come out identical,
 * so a room polygon does not change (and the hash does not move) just because
 * the face walk started somewhere else.
 */
export function canonicalRing(poly: Polygon): Pt[] {
  const ccw = ensureCcw(dedupeCollinear(poly));
  if (ccw.length === 0) return [];
  let best = 0;
  for (let i = 1; i < ccw.length; i++) {
    if (comparePt(ccw[i], ccw[best]) < 0) best = i;
  }
  const rotated: Pt[] = [];
  for (let i = 0; i < ccw.length; i++) rotated.push(ccw[(best + i) % ccw.length]);
  return rotated;
}

/** Canonical, hash-stable key for a polygon (see {@link canonicalRing}). */
export function polygonKey(poly: Polygon): string {
  return canonicalRing(poly).map(ptKey).join(' ');
}

// ---------------------------------------------------------------------------
// Segment intersection
// ---------------------------------------------------------------------------

export type SegIntersection =
  | { kind: 'none' }
  /** Single crossing point. `exact` is false when the point had to be rounded. */
  | { kind: 'point'; point: Pt; exact: boolean; onEndpoint: boolean }
  /** Collinear overlap. `overlap` is the shared sub-segment (possibly a point). */
  | { kind: 'collinear'; overlap: Seg };

/**
 * Segment/segment intersection. Classification is EXACT (integer predicates);
 * the crossing point is ROUNDED to whole mm when it is not integral, and
 * `exact: false` says so.
 */
export function segmentIntersection(s1: Seg, s2: Seg): SegIntersection {
  const { a: p1, b: p2 } = s1;
  const { a: p3, b: p4 } = s2;
  const d1 = cross(p3, p4, p1);
  const d2 = cross(p3, p4, p2);
  const d3 = cross(p1, p2, p3);
  const d4 = cross(p1, p2, p4);

  if (d1 === 0 && d2 === 0) {
    // collinear (or one/both degenerate)
    const overlap = collinearOverlap(s1, s2);
    return overlap ? { kind: 'collinear', overlap } : { kind: 'none' };
  }

  const straddle1 = (d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0);
  const straddle2 = (d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0);

  if (straddle1 && straddle2) {
    const den = (p2.x - p1.x) * (p4.y - p3.y) - (p2.y - p1.y) * (p4.x - p3.x);
    const t = d3 / den;
    const xNum = p1.x + t * (p2.x - p1.x);
    const yNum = p1.y + t * (p2.y - p1.y);
    const point = ptRound(xNum, yNum);
    const exact = Number.isInteger(xNum) && Number.isInteger(yNum);
    return { kind: 'point', point, exact, onEndpoint: false };
  }

  // touching: an endpoint of one lies on the other
  if (d1 === 0 && pointOnSegment(p1, s2)) {
    return { kind: 'point', point: p1, exact: true, onEndpoint: true };
  }
  if (d2 === 0 && pointOnSegment(p2, s2)) {
    return { kind: 'point', point: p2, exact: true, onEndpoint: true };
  }
  if (d3 === 0 && pointOnSegment(p3, s1)) {
    return { kind: 'point', point: p3, exact: true, onEndpoint: true };
  }
  if (d4 === 0 && pointOnSegment(p4, s1)) {
    return { kind: 'point', point: p4, exact: true, onEndpoint: true };
  }
  return { kind: 'none' };
}

/** EXACT shared sub-segment of two collinear segments, or null. */
export function collinearOverlap(s1: Seg, s2: Seg): Seg | null {
  const dx = s1.b.x - s1.a.x;
  const dy = s1.b.y - s1.a.y;
  if (dx === 0 && dy === 0) return pointOnSegment(s1.a, s2) ? { a: s1.a, b: s1.a } : null;
  // project all four points onto the dominant axis of s1
  const useX = Math.abs(dx) >= Math.abs(dy);
  const key = (p: Pt): number => (useX ? p.x : p.y);
  const lo1 = Math.min(key(s1.a), key(s1.b));
  const hi1 = Math.max(key(s1.a), key(s1.b));
  const lo2 = Math.min(key(s2.a), key(s2.b));
  const hi2 = Math.max(key(s2.a), key(s2.b));
  const lo = Math.max(lo1, lo2);
  const hi = Math.min(hi1, hi2);
  if (lo > hi) return null;
  const at = (v: number): Pt => {
    if (useX) {
      const t = dx === 0 ? 0 : (v - s1.a.x) / dx;
      return ptRound(s1.a.x + t * dx, s1.a.y + t * dy);
    }
    const t = dy === 0 ? 0 : (v - s1.a.y) / dy;
    return ptRound(s1.a.x + t * dx, s1.a.y + t * dy);
  };
  return { a: at(lo), b: at(hi) };
}

/** True when the two segments cross at a single interior point. */
export function segmentsProperlyCross(s1: Seg, s2: Seg): boolean {
  const r = segmentIntersection(s1, s2);
  return r.kind === 'point' && !r.onEndpoint;
}

/** True when two segments describe the same line segment (either direction). */
export function segmentsIdentical(s1: Seg, s2: Seg): boolean {
  return (
    (ptEq(s1.a, s2.a) && ptEq(s1.b, s2.b)) || (ptEq(s1.a, s2.b) && ptEq(s1.b, s2.a))
  );
}

/**
 * True when two segments overlap along a non-zero length (the
 * `WALL_DUPLICATE` / "no two walls exactly overlapping" invariant uses this).
 */
export function segmentsOverlapCollinear(s1: Seg, s2: Seg): boolean {
  const r = segmentIntersection(s1, s2);
  return r.kind === 'collinear' && !ptEq(r.overlap.a, r.overlap.b);
}

/**
 * EXACT angular comparator for the half-edge graph: orders directions
 * `a - origin` and `b - origin` counter-clockwise starting at +X, using only
 * integer arithmetic (quadrant, then cross product). No `atan2`, so no
 * floating-point ordering instability between TS and Python.
 */
export function compareAngleAround(origin: Pt, a: Pt, b: Pt): number {
  const qa = halfPlane(a.x - origin.x, a.y - origin.y);
  const qb = halfPlane(b.x - origin.x, b.y - origin.y);
  if (qa !== qb) return qa < qb ? -1 : 1;
  const c = (a.x - origin.x) * (b.y - origin.y) - (a.y - origin.y) * (b.x - origin.x);
  if (c > 0) return -1; // a is clockwise of b => smaller angle
  if (c < 0) return 1;
  return 0;
}

/** 0 for angles in [0, 180), 1 for [180, 360). */
function halfPlane(dx: number, dy: number): number {
  if (dy > 0) return 0;
  if (dy < 0) return 1;
  return dx >= 0 ? 0 : 1;
}

// ---------------------------------------------------------------------------
// Offsetting (setback envelopes, wall-thickness insets)
// ---------------------------------------------------------------------------

/**
 * Offset every edge of a CCW polygon inward by its own distance and intersect
 * consecutive edge lines to get the new vertices.
 *
 * `distancesMm[i]` applies to edge i (poly[i] -> poly[i+1]). Positive = inward
 * for a CCW polygon (the left normal points into the polygon), negative =
 * outward. This is exactly what setback envelopes need: front 1500, sides 900,
 * rear 1200, per edge.
 *
 * ROUNDED vertices. Returns `null` when the offset collapses the polygon (all
 * setbacks larger than the plot, or an edge pair becomes parallel-degenerate) —
 * callers must treat null as "no buildable envelope", never as an empty polygon.
 *
 * Limitation (documented, MVP-acceptable): this is a naive line-offset that does
 * not resolve self-intersections created by deep offsets of reflex corners. It
 * is exact for convex and for rectilinear rect/L/T shapes, which are the only
 * plot shapes the MVP accepts (§5.1). The result is checked with
 * `polygonIsSimple` and rejected (null) when it self-intersects.
 */
export function offsetPolygon(poly: Polygon, distancesMm: readonly number[]): Pt[] | null {
  const n = poly.length;
  if (n < 3) return null;
  if (distancesMm.length !== n) {
    throw new RangeError(
      `offsetPolygon: ${String(distancesMm.length)} distances for ${String(n)} edges — ` +
        'distancesMm[i] must correspond to edge poly[i]->poly[i+1]',
    );
  }
  for (let i = 0; i < n; i++) {
    if (ptEq(poly[i], poly[(i + 1) % n])) return null; // zero-length edge
  }
  // Work CCW (inward = left normal). Reversing the ring reverses the edge order
  // too: edge i of the reversed ring is edge (n-1-i) of the original.
  const ccw = polygonOrientation(poly) === 'ccw';
  const source = ccw ? poly.slice() : reversePolygon(poly);
  const dists = ccw ? distancesMm.slice() : reverseEdgeDistances(distancesMm);

  interface Line {
    ax: number;
    ay: number;
    bx: number;
    by: number;
  }
  const lines: Line[] = [];
  for (let i = 0; i < n; i++) {
    const a = source[i];
    const b = source[(i + 1) % n];
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len = Math.sqrt(dx * dx + dy * dy);
    if (len === 0) return null;
    // inward normal for CCW = (-dy, dx)/len
    const d = dists[i] ?? 0;
    const nx = (-dy / len) * d;
    const ny = (dx / len) * d;
    lines.push({ ax: a.x + nx, ay: a.y + ny, bx: b.x + nx, by: b.y + ny });
  }

  const out: Pt[] = [];
  for (let i = 0; i < n; i++) {
    const l1 = lines[(i - 1 + n) % n];
    const l2 = lines[i];
    const d1x = l1.bx - l1.ax;
    const d1y = l1.by - l1.ay;
    const d2x = l2.bx - l2.ax;
    const d2y = l2.by - l2.ay;
    const den = d1x * d2y - d1y * d2x;
    if (den === 0) return null; // consecutive edges parallel after dedupeCollinear => degenerate
    const t = ((l2.ax - l1.ax) * d2y - (l2.ay - l1.ay) * d2x) / den;
    out.push(ptRound(l1.ax + d1x * t, l1.ay + d1y * t));
  }

  const cleaned = dedupeCollinear(out);
  if (cleaned.length < 3) return null;
  if (polygonOrientation(cleaned) !== 'ccw') return null; // flipped inside-out
  if (!polygonIsSimple(cleaned)) return null;
  return ccw ? cleaned : reversePolygon(cleaned);
}

/**
 * Reversing a ring reverses its edge order: edge `i` of `reversePolygon(poly)`
 * is edge `(n-2-i) mod n` of `poly`.
 */
function reverseEdgeDistances(distancesMm: readonly number[]): number[] {
  const n = distancesMm.length;
  const out: number[] = [];
  for (let i = 0; i < n; i++) out.push(distancesMm[(((n - 2 - i) % n) + n) % n]);
  return out;
}

/** Uniform inward offset — the common case (wall half-thickness inset). */
export function offsetPolygonUniform(poly: Polygon, distanceMm: number): Pt[] | null {
  return offsetPolygon(poly, new Array<number>(poly.length).fill(distanceMm));
}

// ---------------------------------------------------------------------------
// Rect / L / T helpers and union
// ---------------------------------------------------------------------------

/** CCW rectangle polygon from an inclusive bbox. */
export function rectPolygon(minX: number, minY: number, maxX: number, maxY: number): Pt[] {
  return [pt(minX, minY), pt(maxX, minY), pt(maxX, maxY), pt(minX, maxY)];
}

/** CCW rectangle polygon from a bbox. */
export function bboxPolygon(b: Bbox): Pt[] {
  return rectPolygon(b.minX, b.minY, b.maxX, b.maxY);
}

/**
 * EXACT union of axis-aligned rectangles, returned as CCW outer rings.
 *
 * Algorithm: build the coordinate grid from all distinct x/y values, mark cells
 * covered by any rect, then trace the boundary of the covered region. Entirely
 * integer; no tolerance anywhere. This is how rect/L/T plot shapes and
 * per-storey slab outlines get built (§5.1: "L/T = union of ≤3 rects").
 *
 * Holes are NOT returned (a doughnut union yields only its outer ring) — the
 * MVP never produces one; `unionAxisAlignedRectsHasHoles` reports the case so
 * callers can fail loudly instead of silently filling a courtyard.
 */
export function unionAxisAlignedRects(rects: readonly Bbox[]): Pt[][] {
  const grid = buildCoverageGrid(rects);
  if (!grid) return [];
  return traceCoverageRings(grid).rings;
}

/** True when the union of `rects` encloses an uncovered hole. */
export function unionAxisAlignedRectsHasHoles(rects: readonly Bbox[]): boolean {
  const grid = buildCoverageGrid(rects);
  if (!grid) return false;
  return traceCoverageRings(grid).holes > 0;
}

interface CoverageGrid {
  xs: number[];
  ys: number[];
  covered: boolean[][]; // [ix][iy] for cell xs[ix]..xs[ix+1] × ys[iy]..ys[iy+1]
}

function buildCoverageGrid(rects: readonly Bbox[]): CoverageGrid | null {
  const valid = rects.filter((r) => r.maxX > r.minX && r.maxY > r.minY);
  if (valid.length === 0) return null;
  const xsSet = new Set<number>();
  const ysSet = new Set<number>();
  for (const r of valid) {
    xsSet.add(r.minX);
    xsSet.add(r.maxX);
    ysSet.add(r.minY);
    ysSet.add(r.maxY);
  }
  const xs = Array.from(xsSet).sort((a, b) => a - b);
  const ys = Array.from(ysSet).sort((a, b) => a - b);
  const covered: boolean[][] = [];
  for (let ix = 0; ix < xs.length - 1; ix++) {
    const col: boolean[] = new Array<boolean>(Math.max(0, ys.length - 1)).fill(false);
    for (let iy = 0; iy < ys.length - 1; iy++) {
      const cx = xs[ix];
      const cy = ys[iy];
      for (const r of valid) {
        if (r.minX <= cx && xs[ix + 1] <= r.maxX && r.minY <= cy && ys[iy + 1] <= r.maxY) {
          col[iy] = true;
          break;
        }
      }
    }
    covered.push(col);
  }
  return { xs, ys, covered };
}

function traceCoverageRings(grid: CoverageGrid): { rings: Pt[][]; holes: number } {
  const { xs, ys, covered } = grid;
  const nx = xs.length - 1;
  const ny = ys.length - 1;
  const isCovered = (ix: number, iy: number): boolean =>
    ix >= 0 && iy >= 0 && ix < nx && iy < ny && covered[ix][iy];

  // Collect boundary edges as directed segments so the covered region is on the
  // LEFT of each edge (=> CCW outer rings, CW hole rings).
  const edges = new Map<string, Pt[]>(); // fromKey -> list of to-points
  const pushEdge = (a: Pt, b: Pt): void => {
    const k = ptKey(a);
    const list = edges.get(k);
    if (list) list.push(b);
    else edges.set(k, [b]);
  };
  for (let ix = 0; ix < nx; ix++) {
    for (let iy = 0; iy < ny; iy++) {
      if (!isCovered(ix, iy)) continue;
      const x0 = xs[ix];
      const x1 = xs[ix + 1];
      const y0 = ys[iy];
      const y1 = ys[iy + 1];
      if (!isCovered(ix, iy - 1)) pushEdge(pt(x0, y0), pt(x1, y0)); // bottom, →
      if (!isCovered(ix + 1, iy)) pushEdge(pt(x1, y0), pt(x1, y1)); // right, ↑
      if (!isCovered(ix, iy + 1)) pushEdge(pt(x1, y1), pt(x0, y1)); // top, ←
      if (!isCovered(ix - 1, iy)) pushEdge(pt(x0, y1), pt(x0, y0)); // left, ↓
    }
  }

  const rings: Pt[][] = [];
  let holes = 0;
  while (edges.size > 0) {
    const firstKey = edges.keys().next().value as string;
    let current = ptFromKey(firstKey);
    const ring: Pt[] = [current];
    for (;;) {
      const list = edges.get(ptKey(current));
      if (!list || list.length === 0) break;
      const next = list.shift() as Pt;
      if (list.length === 0) edges.delete(ptKey(current));
      if (ptEq(next, ring[0])) {
        current = next;
        break;
      }
      ring.push(next);
      current = next;
    }
    const cleaned = dedupeCollinear(ring);
    if (cleaned.length >= 3) {
      if (polygonOrientation(cleaned) === 'ccw') rings.push(cleaned);
      else holes += 1;
    }
  }
  rings.sort((a, b) => polygonAreaMm2(b) - polygonAreaMm2(a));
  return { rings, holes };
}

// ---------------------------------------------------------------------------
// Triangulation, intersection area, Jaccard
// ---------------------------------------------------------------------------

export type Triangle = readonly [Pt, Pt, Pt];

/**
 * Ear-clipping triangulation of a simple polygon. Deterministic (always clips
 * the first valid ear in index order). Returns [] for degenerate input.
 */
export function triangulate(poly: Polygon): Triangle[] {
  const ring = ensureCcw(dedupeCollinear(poly));
  const n = ring.length;
  if (n < 3) return [];
  if (n === 3) return [[ring[0], ring[1], ring[2]]];
  const idx: number[] = [];
  for (let i = 0; i < n; i++) idx.push(i);
  const out: Triangle[] = [];
  let guard = 0;
  while (idx.length > 3 && guard < n * n + 16) {
    guard += 1;
    let clipped = false;
    for (let i = 0; i < idx.length; i++) {
      const iPrev = idx[(i - 1 + idx.length) % idx.length];
      const iCur = idx[i];
      const iNext = idx[(i + 1) % idx.length];
      const a = ring[iPrev];
      const b = ring[iCur];
      const c = ring[iNext];
      if (cross(a, b, c) <= 0) continue; // reflex or collinear for CCW ring
      let contains = false;
      for (const j of idx) {
        if (j === iPrev || j === iCur || j === iNext) continue;
        if (pointInTriangle(ring[j], a, b, c)) {
          contains = true;
          break;
        }
      }
      if (contains) continue;
      out.push([a, b, c]);
      idx.splice(i, 1);
      clipped = true;
      break;
    }
    if (!clipped) break; // non-simple input; bail with what we have
  }
  if (idx.length === 3) out.push([ring[idx[0]], ring[idx[1]], ring[idx[2]]]);
  return out;
}

/** EXACT: is `p` inside (or on) triangle abc? */
export function pointInTriangle(p: Pt, a: Pt, b: Pt, c: Pt): boolean {
  const d1 = cross(a, b, p);
  const d2 = cross(b, c, p);
  const d3 = cross(c, a, p);
  const hasNeg = d1 < 0 || d2 < 0 || d3 < 0;
  const hasPos = d1 > 0 || d2 > 0 || d3 > 0;
  return !(hasNeg && hasPos);
}

interface Ptf {
  x: number;
  y: number;
}

/** Sutherland–Hodgman: clip a convex polygon by the half-plane left of a->b. */
function clipHalfPlane(poly: readonly Ptf[], a: Pt, b: Pt): Ptf[] {
  const side = (p: Ptf): number => (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x);
  const out: Ptf[] = [];
  const n = poly.length;
  for (let i = 0; i < n; i++) {
    const cur = poly[i];
    const next = poly[(i + 1) % n];
    const sc = side(cur);
    const sn = side(next);
    if (sc >= 0) out.push(cur);
    if ((sc > 0 && sn < 0) || (sc < 0 && sn > 0)) {
      const t = sc / (sc - sn);
      out.push({ x: cur.x + (next.x - cur.x) * t, y: cur.y + (next.y - cur.y) * t });
    }
  }
  return out;
}

function shoelaceAbs(poly: readonly Ptf[]): number {
  const n = poly.length;
  if (n < 3) return 0;
  let sum = 0;
  for (let i = 0; i < n; i++) {
    const p = poly[i];
    const q = poly[(i + 1) % n];
    sum += p.x * q.y - q.x * p.y;
  }
  return Math.abs(sum) / 2;
}

/**
 * APPROXIMATE intersection area of two simple polygons, in mm².
 *
 * Implementation: triangulate both, clip every A-triangle against every
 * B-triangle's three half-planes, sum the clipped areas. Arithmetic is
 * +,-,*,/ on doubles only, so the result is bit-identical in the Python mirror,
 * but it is NOT exact integer mm² — do not store it, do not put it in a
 * compliance number. Its only job is ranking room matches.
 */
export function polygonIntersectionAreaMm2(a: Polygon, b: Polygon): number {
  if (a.length < 3 || b.length < 3) return 0;
  if (!bboxIntersects(bbox(a), bbox(b))) return 0;
  const ta = triangulate(a);
  const tb = triangulate(b);
  let total = 0;
  for (const t1 of ta) {
    const base: Ptf[] = [
      { x: t1[0].x, y: t1[0].y },
      { x: t1[1].x, y: t1[1].y },
      { x: t1[2].x, y: t1[2].y },
    ];
    for (const t2 of tb) {
      // ensure t2 is CCW so "left of each edge" is its interior
      const ccw = cross(t2[0], t2[1], t2[2]) > 0 ? t2 : ([t2[2], t2[1], t2[0]] as Triangle);
      let poly: Ptf[] = base;
      poly = clipHalfPlane(poly, ccw[0], ccw[1]);
      if (poly.length === 0) continue;
      poly = clipHalfPlane(poly, ccw[1], ccw[2]);
      if (poly.length === 0) continue;
      poly = clipHalfPlane(poly, ccw[2], ccw[0]);
      if (poly.length === 0) continue;
      total += shoelaceAbs(poly);
    }
  }
  return roundHalfAwayFromZero(total);
}

/** APPROXIMATE union area = |A| + |B| - |A∩B|. */
export function polygonUnionAreaMm2(a: Polygon, b: Polygon): number {
  return polygonAreaMm2(a) + polygonAreaMm2(b) - polygonIntersectionAreaMm2(a, b);
}

/**
 * Jaccard overlap |A∩B| / |A∪B| in [0, 1]. LOAD-BEARING: this is how room ids
 * survive edits (§3 "match new faces to existing rooms by max-overlap").
 * Returns 0 when either polygon is degenerate.
 */
export function jaccard(a: Polygon, b: Polygon): number {
  const inter = polygonIntersectionAreaMm2(a, b);
  if (inter <= 0) return 0;
  const union = polygonAreaMm2(a) + polygonAreaMm2(b) - inter;
  if (union <= 0) return 0;
  return inter / union;
}

/** Ratio of A covered by B — useful when A shrank a lot but is "the same room". */
export function containmentRatio(a: Polygon, b: Polygon): number {
  const areaA = polygonAreaMm2(a);
  if (areaA <= 0) return 0;
  return polygonIntersectionAreaMm2(a, b) / areaA;
}
