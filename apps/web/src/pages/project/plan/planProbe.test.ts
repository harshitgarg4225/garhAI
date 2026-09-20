/**
 * The dev probe, tested — because a probe that lies is worse than no probe.
 *
 * Two specs read these numbers in a real browser (`plan-hatch.spec.ts` and the
 * §14 budgets in `performance.spec.ts`), and a probe that returns a constant
 * would make both of them green forever. So: the rebuild count must MOVE when
 * geometry is rebuilt (the §14 pan assertion is "this integer did not change",
 * which is worthless if it can never change), the stats must be the ones last
 * published, and the picker must go away when the page unpublishes it.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import {
  planGeometryStats,
  planPickAt,
  publishPlanGeometryStats,
  publishPlanPicker,
  resetPlanGeometryStats,
} from './planProbe';

const GEOMETRY = {
  hatchLineVertices: 12,
  wallFaceVertices: 240,
  hatchedWallCount: 1,
} as const;

describe('planProbe', () => {
  beforeEach(() => {
    resetPlanGeometryStats();
    publishPlanPicker(null);
  });

  it('starts at zero, so a stale page cannot report a count it never built', () => {
    expect(planGeometryStats()).toEqual({
      hatchLineVertices: 0,
      wallFaceVertices: 0,
      hatchedWallCount: 0,
      rebuildCount: 0,
    });
  });

  it('reports what was last published', () => {
    publishPlanGeometryStats(GEOMETRY);
    expect(planGeometryStats()).toMatchObject(GEOMETRY);
  });

  it('counts every rebuild — the §14 pan assertion depends on this moving', () => {
    publishPlanGeometryStats(GEOMETRY);
    expect(planGeometryStats().rebuildCount).toBe(1);
    publishPlanGeometryStats(GEOMETRY);
    publishPlanGeometryStats({ ...GEOMETRY, hatchLineVertices: 0 });
    expect(planGeometryStats().rebuildCount).toBe(3);
    // …and the same publish with the same numbers still counts: the assertion
    // is "was geometry rebuilt", not "did the result differ".
    expect(planGeometryStats().hatchLineVertices).toBe(0);
  });

  it('forgets everything on reset, count included', () => {
    publishPlanGeometryStats(GEOMETRY);
    resetPlanGeometryStats();
    expect(planGeometryStats().rebuildCount).toBe(0);
    expect(planGeometryStats().wallFaceVertices).toBe(0);
  });

  it('answers "unavailable" until a page publishes a picker, and again after', () => {
    expect(planPickAt(10, 10)).toEqual({ kind: 'unavailable', id: null });

    publishPlanPicker((x, y) => ({ kind: 'wall', id: `w:${x},${y}` }));
    expect(planPickAt(4, 7)).toEqual({ kind: 'wall', id: 'w:4,7' });

    // Unmount. A stale picker holding a disposed core would answer with
    // yesterday's scene — "unavailable" is the honest answer.
    publishPlanPicker(null);
    expect(planPickAt(4, 7)).toEqual({ kind: 'unavailable', id: null });
  });
});
