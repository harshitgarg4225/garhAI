/**
 * planProbe — what the plan actually BUILT, for the dev test handle.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY THIS EXISTS AND WHY IT IS THIS SMALL
 * ════════════════════════════════════════════════════════════════════════════
 * A WebGL canvas has no accessible structure, so a Playwright spec cannot ask
 * "did the hatch actually get drawn?" — and CLAUDE.md's bug 4 is precisely a
 * layer that renders (or does not) with nothing able to tell. `lib/testHooks`
 * is the sanctioned answer to that, and its rule is: READ-ONLY probes, DEV
 * builds only, never a way to produce an op.
 *
 * This module is one such probe. `PlanScene` records how many vertices each
 * geometry family came out with, once per GEOMETRY REBUILD — not per frame:
 * the write happens inside the same `useEffect` that already calls
 * `core.invalidate()`, which fires when the document, the storey or the theme
 * changes. A pan or a zoom touches none of it, so the §14 budget is untouched.
 *
 * A count, not a screenshot: it rises only if geometry was genuinely built and
 * handed to a mesh, which is the assertion a "the renderer drew nothing" bug
 * would fail. It says nothing about what the pixels look like — the visual
 * suite owns that, and this file does not pretend otherwise.
 */

export interface PlanGeometryStats {
  /** Vertices in the hatch `LineSegments` — 0 when nothing is bound. */
  readonly hatchLineVertices: number;
  /** Triangle-list vertices across the flat and hatched wall meshes. */
  readonly wallFaceVertices: number;
  /** Walls drawn with a pattern rather than the flat poché. */
  readonly hatchedWallCount: number;
}

const EMPTY: PlanGeometryStats = {
  hatchLineVertices: 0,
  wallFaceVertices: 0,
  hatchedWallCount: 0,
};

let current: PlanGeometryStats = EMPTY;

/** Called by `PlanScene` on each geometry rebuild. Never per frame. */
export function publishPlanGeometryStats(stats: PlanGeometryStats): void {
  current = stats;
}

/** The last published stats. Read by `lib/testHooks` in DEV builds. */
export function planGeometryStats(): PlanGeometryStats {
  return current;
}

/** Forget the stats — the plan page unmounting, so a stale count cannot lie. */
export function resetPlanGeometryStats(): void {
  current = EMPTY;
}
