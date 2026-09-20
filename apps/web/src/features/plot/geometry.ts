/**
 * Pure plot-boundary editing math (F1). No React, no store — everything here
 * takes a polygon in and returns a polygon (or a refusal with a human reason)
 * out, which is what makes `plot.test.ts` able to pin the whole feature's
 * behaviour without a DOM.
 *
 * INTEGER MILLIMETRES THROUGHOUT (golden rule: geometry is int mm). The only
 * float arithmetic is inside `ptRound`-guarded projections, exactly as
 * `@garh/model`'s own geometry module does it.
 *
 * Edge-length editing has TWO semantics, chosen by the ring's shape and always
 * named in the result:
 *
 *   - `stretch` (rectilinear rings only): a cut plane perpendicular to the edge
 *     through its midpoint; every vertex strictly beyond the plane translates
 *     along the edge direction by the length delta. Exact for rect/L/T plots
 *     and keeps rectangles rectangular — typing a new width on a 30×40 moves
 *     the whole far side, which is what an architect means.
 *   - `end` / `start` (any ring): only the edge's far (or near) corner slides
 *     along the edge's own line. Exactly one neighbouring edge changes as a
 *     consequence, and the result says which and by how much. On a skewed ring
 *     the stretch would silently shear every neighbour, so it is never chosen
 *     automatically there.
 *
 * On a skewed edge the translation rounds to whole mm and the achieved length
 * can differ from the request by ≤1 mm — the committed polygon, not the
 * request, is what the labels re-display.
 */

import {
  distMm,
  polygonContains,
  polygonDoubledAreaMm2,
  polygonIsSimple,
  ptEq,
  ptRound,
  roundHalfAwayFromZero,
  type Polygon,
  type Pt,
  type Road,
} from '@garh/model';

// ---------------------------------------------------------------------------
// Small utilities
// ---------------------------------------------------------------------------

/** Ring-indexed vertex access. Throws on an empty polygon, never returns undefined. */
export function ringAt(poly: Polygon, i: number): Pt {
  const n = poly.length;
  if (n === 0) throw new RangeError('ringAt on an empty polygon');
  const p = poly[((i % n) + n) % n];
  if (p === undefined) throw new RangeError(`ringAt: index ${String(i)} out of range`);
  return p;
}

/** Length of edge i (poly[i] -> poly[i+1]) in mm. */
export function edgeLengthMm(poly: Polygon, edgeIndex: number): number {
  return distMm(ringAt(poly, edgeIndex), ringAt(poly, edgeIndex + 1));
}

/** All edge lengths, in edge order. */
export function edgeLengthsMm(poly: Polygon): number[] {
  return poly.map((_, i) => edgeLengthMm(poly, i));
}

/** Midpoint of edge i, rounded to integer mm. */
export function edgeMidpoint(poly: Polygon, edgeIndex: number): Pt {
  const a = ringAt(poly, edgeIndex);
  const b = ringAt(poly, edgeIndex + 1);
  return ptRound((a.x + b.x) / 2, (a.y + b.y) / 2);
}

/**
 * CCW rectangle with its SW corner at the origin — the quick-start boundary.
 * (0,0) is the model's plot-local origin by contract (`PlotDoc.boundary`).
 */
export function rectBoundaryMm(widthMm: number, depthMm: number): Pt[] {
  if (!Number.isSafeInteger(widthMm) || !Number.isSafeInteger(depthMm)) {
    throw new RangeError('rectBoundaryMm needs integer millimetres');
  }
  if (widthMm <= 0 || depthMm <= 0) {
    throw new RangeError('rectBoundaryMm needs positive width and depth');
  }
  return [
    { x: 0, y: 0 },
    { x: widthMm, y: 0 },
    { x: widthMm, y: depthMm },
    { x: 0, y: depthMm },
  ];
}

// ---------------------------------------------------------------------------
// Validation with honest reasons
// ---------------------------------------------------------------------------

export type BoundaryCheck = { readonly ok: true } | { readonly ok: false; readonly reason: string };

/**
 * Why a candidate boundary is not acceptable, in words the editor can show
 * verbatim (§15: every error has a next action). Mirrors the fold invariant
 * (`polygonIsClosedRing`) but names WHICH condition failed.
 */
export function checkBoundary(poly: Polygon): BoundaryCheck {
  if (poly.length < 3) {
    return { ok: false, reason: 'A plot boundary needs at least 3 corners.' };
  }
  for (const p of poly) {
    if (!Number.isSafeInteger(p.x) || !Number.isSafeInteger(p.y)) {
      return { ok: false, reason: 'Every corner must be a whole number of millimetres.' };
    }
  }
  if (polygonDoubledAreaMm2(poly) === 0) {
    return {
      ok: false,
      reason: 'That boundary has no area — pull the corners apart so it encloses the plot.',
    };
  }
  if (!polygonIsSimple(poly)) {
    return {
      ok: false,
      reason:
        "That would make the boundary cross itself — move the corner so the edges don't overlap.",
    };
  }
  return { ok: true };
}

export type PolygonEditResult =
  | { readonly ok: true; readonly polygon: Pt[] }
  | { readonly ok: false; readonly reason: string };

// ---------------------------------------------------------------------------
// Edge-length editing (click a dimension, type a value — §15 "no dead text")
// ---------------------------------------------------------------------------

/** True when every edge is axis-aligned (the rect/L/T family). */
export function isRectilinear(poly: Polygon): boolean {
  if (poly.length < 3) return false;
  for (let i = 0; i < poly.length; i += 1) {
    const a = ringAt(poly, i);
    const b = ringAt(poly, i + 1);
    if (a.x !== b.x && a.y !== b.y) return false;
  }
  return true;
}

export type EdgeLengthMode = 'stretch' | 'end' | 'start';

/** An edge other than the one edited whose length changed as a consequence. */
export interface EdgeSideEffect {
  readonly edgeIndex: number;
  readonly fromMm: number;
  readonly toMm: number;
}

export type EdgeLengthResult =
  | {
      readonly ok: true;
      readonly polygon: Pt[];
      readonly mode: EdgeLengthMode;
      /** Every OTHER edge whose length changed (a rectangle's opposite side, a slid corner's neighbour). */
      readonly sideEffects: readonly EdgeSideEffect[];
    }
  | { readonly ok: false; readonly reason: string };

/** Which mode `'auto'` picks for this ring — the UI names it before the edit. */
export function defaultEdgeLengthMode(poly: Polygon): EdgeLengthMode {
  return isRectilinear(poly) ? 'stretch' : 'end';
}

function sideEffectsOf(before: Polygon, after: Polygon, edited: number): EdgeSideEffect[] {
  const out: EdgeSideEffect[] = [];
  for (let i = 0; i < before.length; i += 1) {
    if (i === edited) continue;
    const fromMm = edgeLengthMm(before, i);
    const toMm = edgeLengthMm(after, i);
    if (fromMm !== toMm) out.push({ edgeIndex: i, fromMm, toMm });
  }
  return out;
}

/**
 * Set edge `edgeIndex` to `newLengthMm`. `mode` defaults to `'auto'`: stretch
 * on a rectilinear ring, otherwise slide the far corner (see module docstring).
 * Rejects non-positive lengths, a neighbour collapsing to nothing, and any
 * result that stops being a simple ring — and never distorts silently: the
 * neighbours that changed are listed in `sideEffects`.
 */
export function setEdgeLengthMm(
  poly: Polygon,
  edgeIndex: number,
  newLengthMm: number,
  mode: EdgeLengthMode | 'auto' = 'auto',
): EdgeLengthResult {
  if (poly.length < 3) return { ok: false, reason: 'Draw the boundary first.' };
  if (!Number.isSafeInteger(newLengthMm) || newLengthMm <= 0) {
    return { ok: false, reason: 'An edge length has to be a positive distance.' };
  }
  const n = poly.length;
  const i = ((edgeIndex % n) + n) % n;
  const a = ringAt(poly, i);
  const b = ringAt(poly, i + 1);
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return { ok: false, reason: 'This edge has no length to change.' };

  const chosen: EdgeLengthMode = mode === 'auto' ? defaultEdgeLengthMode(poly) : mode;
  if (chosen === 'stretch' && !isRectilinear(poly)) {
    return {
      ok: false,
      reason:
        'Stretching only works on a boundary whose edges all run north–south or east–west; ' +
        'on this plot it would shear the neighbouring edges. Move one corner instead.',
    };
  }

  const currentLen = distMm(a, b);
  const delta = newLengthMm - currentLen;
  if (delta === 0) return { ok: true, polygon: poly.slice(), mode: chosen, sideEffects: [] };

  const len = Math.sqrt(lenSq);
  const t = ptRound((dx / len) * delta, (dy / len) * delta);
  if (t.x === 0 && t.y === 0) {
    // Sub-millimetre request on a skewed edge rounded away to nothing.
    return { ok: true, polygon: poly.slice(), mode: chosen, sideEffects: [] };
  }

  let next: Pt[];
  if (chosen === 'stretch') {
    // Vertices strictly beyond the edge's midpoint (measured along the edge
    // direction) translate. Exact integer predicate: 2·((v−a)·d) > |d|².
    next = poly.map((v) => {
      const proj2 = 2 * ((v.x - a.x) * dx + (v.y - a.y) * dy);
      return proj2 > lenSq ? { x: v.x + t.x, y: v.y + t.y } : { x: v.x, y: v.y };
    });
  } else {
    const moving = chosen === 'end' ? (i + 1) % n : i;
    const sign = chosen === 'end' ? 1 : -1;
    next = poly.map((v, k) =>
      k === moving ? { x: v.x + sign * t.x, y: v.y + sign * t.y } : { x: v.x, y: v.y },
    );
    const neighbour = chosen === 'end' ? (i + 1) % n : (i - 1 + n) % n;
    if (edgeLengthMm(next, neighbour) === 0) {
      return {
        ok: false,
        reason: `That length would collapse edge ${String(neighbour + 1)} to nothing — move its far corner first.`,
      };
    }
  }

  const check = checkBoundary(next);
  if (!check.ok) return { ok: false, reason: check.reason };
  return { ok: true, polygon: next, mode: chosen, sideEffects: sideEffectsOf(poly, next, i) };
}

// ---------------------------------------------------------------------------
// Vertex editing
// ---------------------------------------------------------------------------

/** Move one vertex. Validates the result; a refusal leaves the input untouched. */
export function moveVertex(poly: Polygon, vertexIndex: number, to: Pt): PolygonEditResult {
  if (vertexIndex < 0 || vertexIndex >= poly.length) {
    return { ok: false, reason: 'That corner does not exist any more.' };
  }
  const next = poly.map((v, i) => (i === vertexIndex ? { x: to.x, y: to.y } : { x: v.x, y: v.y }));
  const check = checkBoundary(next);
  if (!check.ok) return { ok: false, reason: check.reason };
  return { ok: true, polygon: next };
}

/**
 * Insert a vertex at the midpoint of edge `edgeIndex` (poly[i] -> poly[i+1]).
 * The midpoint of an integer segment can be a half — rounded, like every other
 * float→Pt door in the model core.
 */
export function insertVertexOnEdge(poly: Polygon, edgeIndex: number): PolygonEditResult {
  if (poly.length < 3) return { ok: false, reason: 'Draw the boundary first.' };
  const n = poly.length;
  const i = ((edgeIndex % n) + n) % n;
  const mid = edgeMidpoint(poly, i);
  if (ptEq(mid, ringAt(poly, i)) || ptEq(mid, ringAt(poly, i + 1))) {
    return { ok: false, reason: 'This edge is too short to split.' };
  }
  const next = [...poly.slice(0, i + 1), mid, ...poly.slice(i + 1)].map((p) => ({
    x: p.x,
    y: p.y,
  }));
  const check = checkBoundary(next);
  if (!check.ok) return { ok: false, reason: check.reason };
  return { ok: true, polygon: next };
}

/** Remove a vertex, merging its two edges. Refuses to go below a triangle. */
export function removeVertex(poly: Polygon, vertexIndex: number): PolygonEditResult {
  if (poly.length <= 3) {
    return {
      ok: false,
      reason: 'A boundary needs at least 3 corners — this one cannot be removed.',
    };
  }
  if (vertexIndex < 0 || vertexIndex >= poly.length) {
    return { ok: false, reason: 'That corner does not exist any more.' };
  }
  const next = poly.filter((_, i) => i !== vertexIndex).map((p) => ({ x: p.x, y: p.y }));
  const check = checkBoundary(next);
  if (!check.ok) return { ok: false, reason: check.reason };
  return { ok: true, polygon: next };
}

// ---------------------------------------------------------------------------
// Road ↔ edge index bookkeeping
//
// Roads are stored per EDGE INDEX (`plot.set_road`), and the fold's only
// protection when a boundary changes is dropping roads whose index no longer
// exists. Inserting or removing a vertex renumbers the edges, so the editor
// must carry the roads across itself — these two functions compute where each
// road lands, and `ops.ts` turns the difference into `plot.set_road` ops.
// ---------------------------------------------------------------------------

/**
 * Where the roads live after inserting a vertex on edge `edgeIndex`.
 * The split edge keeps its road on BOTH halves: physically the road still runs
 * along the whole original stretch of the boundary.
 */
export function remapRoadsAfterInsert(roads: readonly Road[], edgeIndex: number): Road[] {
  const out: Road[] = [];
  for (const r of roads) {
    if (r.edgeIndex < edgeIndex) {
      out.push({ ...r });
    } else if (r.edgeIndex === edgeIndex) {
      out.push({ ...r, edgeIndex });
      out.push({ ...r, edgeIndex: edgeIndex + 1 });
    } else {
      out.push({ ...r, edgeIndex: r.edgeIndex + 1 });
    }
  }
  out.sort((x, y) => x.edgeIndex - y.edgeIndex);
  return out;
}

/**
 * Where the roads live after removing vertex `vertexIndex` from a ring of
 * `oldVertexCount` vertices. The two edges meeting at the vertex merge; when
 * both carried a road the wider one wins (that is also how the compliance
 * mapper picks the "front" edge, so the two layers agree).
 */
export function remapRoadsAfterRemove(
  roads: readonly Road[],
  vertexIndex: number,
  oldVertexCount: number,
): Road[] {
  const n = oldVertexCount;
  if (n <= 3) return roads.map((r) => ({ ...r }));
  const newN = n - 1;

  /** Old edge index -> new edge index (or the merged edge). */
  const mergedNew = vertexIndex === 0 ? newN - 1 : vertexIndex - 1;
  const mapIndex = (e: number): number => {
    if (vertexIndex === 0) {
      // old edges 0 and n-1 merge into new edge newN-1; old e in [1, n-2] -> e-1
      if (e === 0 || e === n - 1) return mergedNew;
      return e - 1;
    }
    if (e === vertexIndex - 1 || e === vertexIndex) return mergedNew;
    return e > vertexIndex ? e - 1 : e;
  };

  const byNew = new Map<number, Road>();
  for (const r of roads) {
    if (r.edgeIndex < 0 || r.edgeIndex >= n) continue;
    const target = mapIndex(r.edgeIndex);
    const existing = byNew.get(target);
    if (existing === undefined || (r.widthMm ?? 0) > (existing.widthMm ?? 0)) {
      byNew.set(target, { ...r, edgeIndex: target });
    }
  }
  return Array.from(byNew.values()).sort((x, y) => x.edgeIndex - y.edgeIndex);
}

/**
 * Which edge the rules engine will treat as the FRONT: the widest road, ties
 * broken by the lowest edge index. Mirrors `_edge_roles` in
 * `apps/api/garh_api/compliance.py` — the solver's entry edge and the setback
 * tables both hang off this, so the editor must not disagree with the engine.
 */
export function frontEdgeIndex(roads: readonly Road[]): number | null {
  let best: Road | null = null;
  for (const r of roads) {
    if (r.widthMm === null) continue;
    if (
      best === null ||
      r.widthMm > (best.widthMm ?? 0) ||
      (r.widthMm === best.widthMm && r.edgeIndex < best.edgeIndex)
    ) {
      best = r;
    }
  }
  return best === null ? null : best.edgeIndex;
}

// ---------------------------------------------------------------------------
// Edge roles — the ONE definition the rules engine and the editor share
//
// MIRRORS `_edge_roles` in `apps/api/garh_api/compliance.py` exactly, and
// `fixtures/model/edge-roles.json` is asserted by both. Pure integer
// arithmetic (BigInt where a product can pass 2^53) so the two languages
// cannot round differently.
// ---------------------------------------------------------------------------

export type EdgeRole = 'front' | 'rear' | 'side-a' | 'side-b' | 'other';

export const EDGE_ROLE_LABELS: Readonly<Record<EdgeRole, string>> = {
  front: 'Front',
  rear: 'Rear',
  'side-a': 'Side A',
  'side-b': 'Side B',
  other: '—',
};

/**
 * Assign front/rear/side-a/side-b to every edge from GEOMETRY, not index
 * parity:
 *   - front  = the edge with the widest road (ties → lowest index);
 *   - rear   = every other edge whose outward normal points within 45° of
 *              directly away from the front (an L-plot's two back faces are
 *              both rear, which is what the rear setback governs);
 *   - side-a / side-b = the rest, split by which half of the plot they sit
 *              in as seen FROM THE ROAD looking into the plot: A is the left
 *              half, B the right (the engine's copy calls them "left side" and
 *              "right side"). On a rectangle with the road at the bottom, A is
 *              the west edge.
 * With no road at all every edge is `other`, so road-banded rules go
 * `not_applicable` rather than silently passing.
 */
export function edgeRoles(boundary: Polygon, roads: readonly Road[]): EdgeRole[] {
  const n = boundary.length;
  const roles: EdgeRole[] = new Array<EdgeRole>(n).fill('other');
  if (n < 3) return roles;
  const front = frontEdgeIndex(roads.filter((r) => r.edgeIndex >= 0 && r.edgeIndex < n));
  if (front === null) return roles;
  roles[front] = 'front';

  // CW rings can exist (a triangle's corner dragged across its opposite edge),
  // so the outward normal is orientation-aware on both sides of the mirror.
  const orient = polygonDoubledAreaMm2(boundary) < 0 ? -1 : 1;
  const fa = ringAt(boundary, front);
  const fb = ringAt(boundary, front + 1);
  const fdx = fb.x - fa.x;
  const fdy = fb.y - fa.y;
  // Outward normal of a→b for a CCW ring is (dy, −dx).
  const fnx = orient * fdy;
  const fny = orient * -fdx;
  const fLenSq = BigInt(fdx * fdx + fdy * fdy);
  const fmx2 = fa.x + fb.x; // doubled midpoint of the front edge
  const fmy2 = fa.y + fb.y;
  // "Left" for someone standing on the road looking into the plot: the inward
  // direction (−f) rotated 90° anticlockwise = (f.y, −f.x). Orientation-free.
  const leftX = fny;
  const leftY = -fnx;

  for (let i = 0; i < n; i += 1) {
    if (i === front) continue;
    const a = ringAt(boundary, i);
    const b = ringAt(boundary, i + 1);
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const nx = orient * dy;
    const ny = orient * -dx;
    const dotp = nx * fnx + ny * fny;
    if (dotp < 0) {
      // cos θ ≤ −cos 45°  ⇔  2·dot² ≥ |n|²·|f|²  (with dot < 0)
      const dotB = BigInt(dotp);
      const lenSq = BigInt(dx * dx + dy * dy);
      if (2n * dotB * dotB >= lenSq * fLenSq) {
        roles[i] = 'rear';
        continue;
      }
    }
    // Which half of the plot the edge's midpoint sits in, left or right of the
    // front edge's midpoint as seen from the road.
    const toLeft = (a.x + b.x - fmx2) * leftX + (a.y + b.y - fmy2) * leftY;
    roles[i] = toLeft > 0 ? 'side-a' : 'side-b';
  }
  return roles;
}

// ---------------------------------------------------------------------------
// The plan after a plot edit
// ---------------------------------------------------------------------------

/**
 * How many walls of the house now have an end outside the plot boundary. A
 * cheap, exact (integer point-in-polygon) signal for the "you changed the plot
 * after a plan was applied" banner — it is NOT the compliance check (setbacks
 * are the engine's job), only the part that needs no rule pack to be certain.
 */
export function wallsOutsideBoundary(
  boundary: Polygon,
  walls: readonly { readonly a: Pt; readonly b: Pt }[],
): number {
  if (boundary.length < 3) return 0;
  let count = 0;
  for (const wall of walls) {
    if (!polygonContains(boundary, wall.a) || !polygonContains(boundary, wall.b)) count += 1;
  }
  return count;
}

// ---------------------------------------------------------------------------
// Bearings (display only — never stored)
// ---------------------------------------------------------------------------

export const DIRECTION_LABELS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'] as const;
export type DirectionLabel = (typeof DIRECTION_LABELS)[number];

/**
 * Compass bearing (0–359, clockwise from true north) of a plot-local vector,
 * given the plot's `northDeg`. DISPLAY ONLY: uses `atan2`, so it never feeds
 * geometry. `null` for the zero vector.
 */
export function bearingDeg(v: Pt, northDeg: number): number | null {
  if (v.x === 0 && v.y === 0) return null;
  const cwFromPlusY = (Math.atan2(v.x, v.y) * 180) / Math.PI;
  const deg = roundHalfAwayFromZero(cwFromPlusY - northDeg);
  return ((deg % 360) + 360) % 360;
}

/** Quantize a bearing to one of the 8 compass labels. */
export function directionLabel(bearing: number): DirectionLabel {
  const idx = Math.floor((((bearing % 360) + 360) % 360) / 45 + 0.5) % 8;
  return DIRECTION_LABELS[idx] ?? 'N';
}

/**
 * Which way edge i FACES (its outward normal), as a compass label. For a CCW
 * ring the outward normal is the RIGHT side of a→b.
 */
export function edgeFacing(
  poly: Polygon,
  edgeIndex: number,
  northDeg: number,
): DirectionLabel | null {
  if (poly.length < 3) return null;
  const a = ringAt(poly, edgeIndex);
  const b = ringAt(poly, edgeIndex + 1);
  const ccw = polygonDoubledAreaMm2(poly) > 0;
  // Right normal of a->b is (dy, -dx); flip when the ring is stored CW.
  const nx = ccw ? b.y - a.y : a.y - b.y;
  const ny = ccw ? a.x - b.x : b.x - a.x;
  const bearing = bearingDeg({ x: nx, y: ny }, northDeg);
  return bearing === null ? null : directionLabel(bearing);
}
