/**
 * terrace.ts — the terrace over a SET-BACK lower storey, derived purely.
 *
 * THE MASSING THIS SERVES: a G+1 whose first floor is smaller than the
 * ground floor — the commonest Indian house shape (the rear or a side set
 * back for a terrace, a bedroom's open-to-sky deck, the front porch bay). The
 * model derives one floor slab per storey (its wall outline), so the storey
 * ABOVE roofs only what it stands on; the exposed portion of the lower
 * storey used to be open to the sky with nothing to lean on. `roofSolids`
 * still owns the top storey's roof; this module owns every other exposed
 * roof, one per storey, and hands `solids.ts` the polygons to extrude.
 *
 * HOW THE EXPOSED REGION IS FOUND — no polygon-boolean library. Both
 * outlines' edges go into the model core's own planar half-edge graph
 * (`buildHalfEdgeGraph`, the room detector's engine, exact in integer mm),
 * `planarFaces` walks every bounded cycle, and each face is classified by a
 * point strictly inside it: inside the lower outline and outside the upper
 * one ⇒ terrace. Shared edges and crossings are the arrangement's ordinary
 * business, which is why the room detector was the right tool: those are
 * exactly the degenerate cases a hand-rolled clipper gets wrong.
 *
 * THE ONE CASE THE ARRANGEMENT CANNOT SEE: an upper outline strictly INSIDE
 * the lower one, touching nothing. Two rings that never meet form two
 * components, and a cycle walk cannot know the inner ring is a hole of the
 * outer face. That case is detected up front and returned as the whole
 * lower ring with the upper ring as a `hole` — `solids.ts` turns the hole
 * into a boolean cut, the same mechanism the roof slab uses for stair wells.
 *
 * PARAPET EDGES: a terrace edge that lies along the upper outline is where
 * the upper storey's wall stands — that wall IS the parapet there. Every
 * other edge is the building's perimeter and gets a parapet band.
 */

import {
  buildHalfEdgeGraph,
  ensureCcw,
  planarFaces,
  pointInPolygon,
  triangulate,
  type Polygon,
  type Pt,
} from '@garh/model';

import type { PtF } from '../core/coords';

/** One exposed roof region of a storey. */
export interface TerraceFace {
  /** CCW ring, integer mm (arrangement nodes are integers). */
  readonly ring: readonly Pt[];
  /** Index-aligned with `ring`'s edges (i → i+1): true = perimeter, parapet. */
  readonly exposedEdges: readonly boolean[];
  /** An upper outline entirely inside this face, to subtract. Usually null. */
  readonly hole: readonly Pt[] | null;
}

/** A point strictly inside a simple ring: the centroid of its first ear. */
function interiorPoint(ring: readonly Pt[]): PtF | null {
  const tris = triangulate(ring as Polygon);
  const first = tris[0];
  if (first === undefined) return null;
  const [a, b, c] = first;
  return { x: (a.x + b.x + c.x) / 3, y: (a.y + b.y + c.y) / 3 };
}

/** Is the whole edge p→q on the boundary of `ring`? Both ends and the middle. */
function edgeOnBoundary(p: Pt, q: Pt, ring: Polygon): boolean {
  if (pointInPolygon(p, ring) !== 'boundary') return false;
  if (pointInPolygon(q, ring) !== 'boundary') return false;
  // Midpoints of integer endpoints are exact halves — no rounding drift.
  const mid = { x: (p.x + q.x) / 2, y: (p.y + q.y) / 2 };
  return pointInPolygon(mid as Pt, ring) === 'boundary';
}

/**
 * The exposed roof regions of a storey whose outline is `lower`, under a
 * storey whose outline is `upper`. Empty when nothing is exposed (identical
 * outlines, or an upper storey that overhangs everything). An absent upper
 * outline (no walls drawn above yet) exposes the whole lower outline.
 */
export function exposedTerraceFaces(lower: Polygon, upper: Polygon): TerraceFace[] {
  if (lower.length < 3) return [];
  const lowerRing = ensureCcw(lower);

  if (upper.length < 3) {
    return [{ ring: lowerRing, exposedEdges: lowerRing.map(() => true), hole: null }];
  }
  const upperRing = ensureCcw(upper);

  // The hole case: every upper vertex strictly inside, every lower vertex
  // strictly outside — the rings cannot meet, so the arrangement would see
  // two unrelated cycles.
  const upperInside = upperRing.every((p) => pointInPolygon(p, lowerRing) === 'inside');
  const lowerOutside = lowerRing.every((p) => pointInPolygon(p, upperRing) === 'outside');
  if (upperInside && lowerOutside) {
    return [{ ring: lowerRing, exposedEdges: lowerRing.map(() => true), hole: upperRing }];
  }

  // The general case: one planar arrangement of both rings' edges.
  const edgesOf = (ring: readonly Pt[], tag: string) =>
    ring.map((a, i) => ({
      // `Id<'wall'>`'s brand is optional (ids.ts): a plain label assigns.
      id: `${tag}${String(i)}` as never,
      a,
      b: ring[(i + 1) % ring.length] ?? a,
      thicknessMm: 0,
    }));
  const graph = buildHalfEdgeGraph([...edgesOf(lowerRing, 'L'), ...edgesOf(upperRing, 'U')]);

  const out: TerraceFace[] = [];
  for (const face of planarFaces(graph)) {
    if (face.doubledAreaMm2 <= 0) continue; // the unbounded outer cycle
    const ring = ensureCcw(face.ring);
    const inside = interiorPoint(ring);
    if (inside === null) continue;
    const probe = inside as Pt;
    if (pointInPolygon(probe, lowerRing) !== 'inside') continue;
    if (pointInPolygon(probe, upperRing) !== 'outside') continue;
    const exposedEdges = ring.map((p, i) => {
      const q = ring[(i + 1) % ring.length] ?? p;
      return !edgeOnBoundary(p, q, upperRing);
    });
    out.push({ ring, exposedEdges, hole: null });
  }
  return out;
}
