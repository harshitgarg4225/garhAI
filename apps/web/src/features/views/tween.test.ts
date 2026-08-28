/**
 * Spec for the flight's shape.
 *
 * The load-bearing assertion in this file is the smallest one: at `k = 1` the
 * interpolator returns the TARGET OBJECT, not a computed copy of it. Everything
 * else here is feel; that one is correctness, and the "naive endpoint" test
 * below shows it is not a free win — the arithmetic really does miss.
 */

import { describe, expect, it } from 'vitest';

import { easeOutCubic, interpolateCamera, shortestAngleDeltaDeg } from './tween';
import type { Saved2dCamera, Saved3dCamera, SavedCamera } from './types';

const PLAN_A: Saved2dCamera = { mode: '2d', centreMm: { x: 0, y: 0 }, mmPerPx: 1.5 };
const PLAN_B: Saved2dCamera = { mode: '2d', centreMm: { x: 8000, y: -3200 }, mmPerPx: 10.9644 };

const ORBIT_A: Saved3dCamera = {
  mode: '3d',
  targetMm: { x: 0, y: 0, z: 0 },
  distanceMm: 25_000,
  azimuthDeg: 350,
  polarDeg: 60,
};
const ORBIT_B: Saved3dCamera = {
  mode: '3d',
  targetMm: { x: 4000, y: 2000, z: 1500 },
  distanceMm: 18_733.61,
  azimuthDeg: 10,
  polarDeg: 30,
};

describe('the endpoint', () => {
  it('is the target object itself, by reference', () => {
    expect(interpolateCamera(PLAN_A, PLAN_B, 1)).toBe(PLAN_B);
    expect(interpolateCamera(ORBIT_A, ORBIT_B, 1)).toBe(ORBIT_B);
    // Overshoot (a late frame after a long GC pause) lands, it does not sail past.
    expect(interpolateCamera(PLAN_A, PLAN_B, 1.7)).toBe(PLAN_B);
  });

  it('and that matters: the arithmetic endpoint really does miss', () => {
    // What a tween without the identity return would write on its last frame.
    const naiveZoom = Math.exp(
      Math.log(PLAN_A.mmPerPx) + (Math.log(PLAN_B.mmPerPx) - Math.log(PLAN_A.mmPerPx)) * 1,
    );
    expect(naiveZoom).not.toBe(PLAN_B.mmPerPx);
    expect(Math.abs(naiveZoom - PLAN_B.mmPerPx)).toBeLessThan(1e-9); // invisible…

    const naiveDistance = Math.exp(
      Math.log(ORBIT_A.distanceMm) +
        (Math.log(ORBIT_B.distanceMm) - Math.log(ORBIT_A.distanceMm)) * 1,
    );
    expect(naiveDistance).not.toBe(ORBIT_B.distanceMm); // …and still not equal

    // The real interpolator does not go near that arithmetic at k = 1.
    expect((interpolateCamera(PLAN_A, PLAN_B, 1) as Saved2dCamera).mmPerPx).toBe(PLAN_B.mmPerPx);
    expect((interpolateCamera(ORBIT_A, ORBIT_B, 1) as Saved3dCamera).distanceMm).toBe(
      ORBIT_B.distanceMm,
    );
  });

  it('returns the source at k = 0 and below', () => {
    expect(interpolateCamera(PLAN_A, PLAN_B, 0)).toBe(PLAN_A);
    expect(interpolateCamera(PLAN_A, PLAN_B, -0.3)).toBe(PLAN_A);
  });
});

describe('what moves how', () => {
  it('moves the plan centre linearly and the zoom in log space', () => {
    const mid = interpolateCamera(PLAN_A, PLAN_B, 0.5) as Saved2dCamera;
    expect(mid.centreMm.x).toBeCloseTo(4000, 9);
    expect(mid.centreMm.y).toBeCloseTo(-1600, 9);
    // Geometric mean, not arithmetic: √(1.5 × 10.9644) ≈ 4.055, well below the
    // arithmetic midpoint of 6.23. A linear zoom ramp reads as a snap then a
    // crawl, which is why the controller's own fit tween does the same thing.
    expect(mid.mmPerPx).toBeCloseTo(Math.sqrt(1.5 * 10.9644), 9);
    expect(mid.mmPerPx).toBeLessThan((1.5 + 10.9644) / 2);
  });

  it('takes the short way round the compass', () => {
    // 350° → 10° is +20°, so the quarter-way point is 355°, not somewhere on
    // the far side of the building.
    const quarter = interpolateCamera(ORBIT_A, ORBIT_B, 0.25) as Saved3dCamera;
    expect(quarter.azimuthDeg).toBeCloseTo(355, 9);

    const back = interpolateCamera(ORBIT_B, ORBIT_A, 0.25) as Saved3dCamera;
    expect(back.azimuthDeg).toBeCloseTo(5, 9);
  });

  it('NEGATIVE CONTROL: a plain linear angle blend would go the long way', () => {
    // What the naive version would produce for the same quarter step. If
    // `interpolateCamera` ever loses its shortest-arc call, it lands here.
    const naive = ORBIT_A.azimuthDeg + (ORBIT_B.azimuthDeg - ORBIT_A.azimuthDeg) * 0.25;
    expect(naive).toBeCloseTo(265, 9);
    expect((interpolateCamera(ORBIT_A, ORBIT_B, 0.25) as Saved3dCamera).azimuthDeg).not.toBeCloseTo(
      naive,
      3,
    );
  });

  it('dollies and tilts through sensible intermediate values', () => {
    const mid = interpolateCamera(ORBIT_A, ORBIT_B, 0.5) as Saved3dCamera;
    expect(mid.distanceMm).toBeCloseTo(Math.sqrt(25_000 * 18_733.61), 6);
    expect(mid.polarDeg).toBeCloseTo(45, 9);
    expect(mid.targetMm).toEqual({ x: 2000, y: 1000, z: 750 });
  });

  it('does not pretend a projection change is a flight', () => {
    const crossed = interpolateCamera(PLAN_A as SavedCamera, ORBIT_B as SavedCamera, 0.5);
    expect(crossed).toBe(ORBIT_B);
  });

  it('falls back to a linear ramp rather than emitting NaN on a non-positive zoom', () => {
    const broken: Saved2dCamera = { mode: '2d', centreMm: { x: 0, y: 0 }, mmPerPx: 0 };
    const mid = interpolateCamera(broken, PLAN_B, 0.5) as Saved2dCamera;
    expect(Number.isFinite(mid.mmPerPx)).toBe(true);
    expect(mid.mmPerPx).toBeCloseTo(PLAN_B.mmPerPx / 2, 9);
  });
});

describe('shortestAngleDeltaDeg', () => {
  it('answers in [-180, 180) whatever it is given', () => {
    expect(shortestAngleDeltaDeg(350, 10)).toBeCloseTo(20, 12);
    expect(shortestAngleDeltaDeg(10, 350)).toBeCloseTo(-20, 12);
    expect(shortestAngleDeltaDeg(0, 0)).toBe(0);
    expect(shortestAngleDeltaDeg(0, 180)).toBe(-180);
    expect(shortestAngleDeltaDeg(-720, 45)).toBeCloseTo(45, 12);
    expect(shortestAngleDeltaDeg(45, 45 + 3600)).toBeCloseTo(0, 12);
  });
});

describe('easeOutCubic', () => {
  it('is pinned at both ends and clamped outside them', () => {
    expect(easeOutCubic(0)).toBe(0);
    expect(easeOutCubic(1)).toBe(1);
    expect(easeOutCubic(-5)).toBe(0);
    expect(easeOutCubic(5)).toBe(1);
  });

  it('front-loads the movement, which is what "ease out" means', () => {
    expect(easeOutCubic(0.5)).toBeGreaterThan(0.5);
    expect(easeOutCubic(0.25)).toBeGreaterThan(0.25);
  });
});
