/**
 * Op construction for the plot surface (F1). Pure functions from "what the
 * editor wants" to the exact `@garh/model` ops the store dispatches — kept out
 * of the components so `plot.test.ts` can fold them through the real model
 * core and assert on the resulting document.
 *
 * Golden rule 1: these BUILD ops; they never mutate state. The model store is
 * the only writer.
 */

import {
  roundHalfAwayFromZero,
  type JsonObject,
  type Op,
  type Polygon,
  type Road,
} from '@garh/model';

/** How a boundary got here — the model's `plot.source` vocabulary. */
export type BoundarySource = 'manual' | 'dxf' | 'seed';

export function boundaryOp(polygon: Polygon, source: BoundarySource = 'manual'): Op {
  return {
    type: 'plot.set_boundary',
    payload: { polygon: polygon.map((p) => ({ x: p.x, y: p.y })), source },
  };
}

/**
 * Normalize any degree value (a compass drag produces floats, a typed value
 * can be negative or ≥360) into the op contract: integer, 0–359, clockwise
 * from +Y.
 */
export function normalizeNorthDeg(deg: number): number {
  const i = roundHalfAwayFromZero(deg);
  return ((i % 360) + 360) % 360;
}

export function northOp(deg: number): Op {
  return { type: 'plot.set_north', payload: { deg: normalizeNorthDeg(deg) } };
}

export function roadOp(edgeIndex: number, widthMm: number | null, name?: string | null): Op {
  return {
    type: 'plot.set_road',
    payload: { edgeIndex, widthMm, name: name ?? null },
  };
}

export function regProfileOp(cityPack: string | null, overrides: JsonObject): Op {
  return { type: 'plot.set_reg_profile', payload: { cityPack, overrides } };
}

/**
 * The ops for a boundary change that also has to carry the roads across.
 *
 * `fold`'s own behaviour for `plot.set_boundary` is to KEEP every road whose
 * edge index still exists and drop the rest — it cannot know how the editor's
 * renumbering maps old edges to new ones. So this builder emits the boundary
 * op first, then exactly the `plot.set_road` diffs that turn the fold's kept
 * set into `nextRoads`. Dispatched as ONE group: one undo step, and the
 * document never exists in a state where a road points at the wrong edge.
 */
export function boundaryGroupOps(
  prevRoads: readonly Road[],
  nextPolygon: Polygon,
  nextRoads: readonly Road[],
  source: BoundarySource = 'manual',
): Op[] {
  const ops: Op[] = [boundaryOp(nextPolygon, source)];
  const edgeCount = nextPolygon.length;

  // What the document will hold immediately after the boundary op folds.
  const kept = new Map<number, Road>();
  for (const r of prevRoads) {
    if (r.edgeIndex >= 0 && r.edgeIndex < edgeCount) kept.set(r.edgeIndex, r);
  }

  const desired = new Map<number, Road>();
  for (const r of nextRoads) {
    if (r.edgeIndex >= 0 && r.edgeIndex < edgeCount && r.widthMm !== null) {
      desired.set(r.edgeIndex, r);
    }
  }

  const touched = new Set<number>([...kept.keys(), ...desired.keys()]);
  const indices = Array.from(touched).sort((a, b) => a - b);
  for (const idx of indices) {
    const have = kept.get(idx) ?? null;
    const want = desired.get(idx) ?? null;
    if (want === null) {
      if (have !== null) ops.push(roadOp(idx, null, null));
      continue;
    }
    const sameWidth = have !== null && have.widthMm === want.widthMm;
    const sameName = have !== null && (have.name ?? null) === (want.name ?? null);
    if (!sameWidth || !sameName) ops.push(roadOp(idx, want.widthMm, want.name ?? null));
  }
  return ops;
}
