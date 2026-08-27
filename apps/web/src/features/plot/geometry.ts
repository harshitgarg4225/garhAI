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
 * Edge-length editing uses the CAD "stretch" semantic: a cut plane
 * perpendicular to the edge through its midpoint; every vertex strictly beyond
 * the plane translates along the edge direction by the length delta. For the
 * axis-aligned rect/L/T plots the MVP accepts (§5.1) this is exact and keeps
 * rectangles rectangular; for a skewed edge the translation rounds to whole mm
 * and the achieved length can differ from the request by ≤1 mm — the committed
 * polygon, not the request, is what the labels re-display.
 */

import {
  distMm,
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

/**
 * Set edge `edgeIndex` to `newLengthMm` by stretching the polygon along the
 * edge's own direction (see module docstring for the semantics). Rejects
 * non-positive lengths and any result that stops being a simple ring.
 */
export function setEdgeLengthMm(
  poly: Polygon,
  edgeIndex: number,
  newLengthMm: number,
): PolygonEditResult {
  if (poly.length < 3) return { ok: false, reason: 'Draw the boundary first.' };
  if (!Number.isSafeInteger(newLengthMm) || newLengthMm <= 0) {
    return { ok: false, reason: 'An edge length has to be a positive distance.' };
  }
  const a = ringAt(poly, edgeIndex);
  const b = ringAt(poly, edgeIndex + 1);
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return { ok: false, reason: 'This edge has no length to change.' };

  const currentLen = distMm(a, b);
  const delta = newLengthMm - currentLen;
  if (delta === 0) return { ok: true, polygon: poly.slice() };

  const len = Math.sqrt(lenSq);
  const t = ptRound((dx / len) * delta, (dy / len) * delta);
  if (t.x === 0 && t.y === 0) {
    // Sub-millimetre request on a skewed edge rounded away to nothing.
    return { ok: true, polygon: poly.slice() };
  }

  // Vertices strictly beyond the edge's midpoint (measured along the edge
  // direction) translate. Exact integer predicate: 2·((v−a)·d) > |d|².
  const next = poly.map((v) => {
    const proj2 = 2 * ((v.x - a.x) * dx + (v.y - a.y) * dy);
    return proj2 > lenSq ? { x: v.x + t.x, y: v.y + t.y } : { x: v.x, y: v.y };
  });

  const check = checkBoundary(next);
  if (!check.ok) return { ok: false, reason: check.reason };
  return { ok: true, polygon: next };
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
