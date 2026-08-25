/**
 * Spec for the camera maths.
 *
 * The load-bearing property is that the pixel↔mm mapping and the orthographic
 * frustum are two statements of the same fact. If they drift, clicks land
 * somewhere other than the cursor — a bug that looks like a hit-testing problem
 * and is actually a camera problem, and costs an afternoon to find.
 */

import { describe, expect, it } from 'vitest';

import {
  clampMmPerPx,
  CSS_MM_PER_PX,
  fitBboxToViewport,
  fitDistanceMm,
  mmPerPxAtDistance,
  mmToPixel,
  normaliseAzimuthDeg,
  orbitEyeMm,
  orthoFrustumWorld,
  panByPx,
  pixelToMmF,
  scaleLabel,
  wheelZoomFactor,
  zoomAtPixel,
  type View2D,
} from './cameraMath';
import { MAX_MM_PER_PX, MIN_MM_PER_PX } from './constants';

const SIZE = { width: 800, height: 600 };
const VIEW: View2D = { centreMm: { x: 0, y: 0 }, mmPerPx: 10 };

describe('pixel ↔ mm', () => {
  it('puts the view centre at the viewport centre', () => {
    expect(pixelToMmF(VIEW, { x: 400, y: 300 }, SIZE)).toEqual({ x: 0, y: 0 });
  });

  it('grows +X to the right and +Y upwards', () => {
    expect(pixelToMmF(VIEW, { x: 500, y: 300 }, SIZE)).toEqual({ x: 1000, y: 0 });
    // 100 px *up* the screen is +1000 mm north.
    expect(pixelToMmF(VIEW, { x: 400, y: 200 }, SIZE)).toEqual({ x: 0, y: 1000 });
  });

  it('round-trips', () => {
    for (const px of [
      { x: 0, y: 0 },
      { x: 800, y: 600 },
      { x: 137, y: 42 },
    ]) {
      const back = mmToPixel(VIEW, pixelToMmF(VIEW, px, SIZE), SIZE);
      expect(back.x).toBeCloseTo(px.x, 9);
      expect(back.y).toBeCloseTo(px.y, 9);
    }
  });

  it('agrees with the frustum the rig hands the camera', () => {
    const frustum = orthoFrustumWorld(VIEW, SIZE);
    // Right edge of the viewport, in mm, must equal the frustum's right edge
    // converted from world units. 400 px × 10 mm/px = 4000 mm = 4 world units.
    expect(frustum.right).toBeCloseTo(4, 12);
    expect(frustum.top).toBeCloseTo(3, 12);
    expect(pixelToMmF(VIEW, { x: SIZE.width, y: 0 }, SIZE)).toEqual({ x: 4000, y: 3000 });
    expect(frustum.left).toBe(-frustum.right);
    expect(frustum.bottom).toBe(-frustum.top);
  });
});

describe('zoom', () => {
  it('keeps the model point under the cursor fixed', () => {
    const cursor = { x: 600, y: 200 };
    const before = pixelToMmF(VIEW, cursor, SIZE);
    const zoomed = zoomAtPixel(VIEW, cursor, SIZE, 0.5);
    const after = pixelToMmF(zoomed, cursor, SIZE);
    expect(after.x).toBeCloseTo(before.x, 9);
    expect(after.y).toBeCloseTo(before.y, 9);
    expect(zoomed.mmPerPx).toBe(5);
  });

  it('keeps it fixed at the corners too', () => {
    for (const cursor of [
      { x: 0, y: 0 },
      { x: 800, y: 600 },
      { x: 0, y: 600 },
    ]) {
      const before = pixelToMmF(VIEW, cursor, SIZE);
      const after = pixelToMmF(zoomAtPixel(VIEW, cursor, SIZE, 2.5), cursor, SIZE);
      expect(after.x).toBeCloseTo(before.x, 9);
      expect(after.y).toBeCloseTo(before.y, 9);
    }
  });

  it('clamps rather than letting the drawing vanish', () => {
    expect(clampMmPerPx(0)).toBeGreaterThan(0);
    expect(clampMmPerPx(-5)).toBeGreaterThan(0);
    expect(clampMmPerPx(Number.NaN)).toBeGreaterThan(0);
    expect(clampMmPerPx(1e9)).toBe(MAX_MM_PER_PX);
    expect(clampMmPerPx(1e-9)).toBe(MIN_MM_PER_PX);
  });

  it('returns the same view when the clamp bites, so no drift accumulates', () => {
    const atMax: View2D = { centreMm: { x: 10, y: 20 }, mmPerPx: MAX_MM_PER_PX };
    expect(zoomAtPixel(atMax, { x: 10, y: 10 }, SIZE, 2)).toBe(atMax);
  });

  it('is invertible: N notches out then N back is the same scale', () => {
    const out = wheelZoomFactor(120);
    const back = wheelZoomFactor(-120);
    expect(out * back).toBeCloseTo(1, 12);
    expect(out).toBeGreaterThan(1); // positive deltaY = zoom out
  });

  it('normalises Firefox line-mode and page-mode wheels', () => {
    expect(wheelZoomFactor(3, 1)).toBeGreaterThan(wheelZoomFactor(3, 0));
    expect(wheelZoomFactor(1, 2)).toBeGreaterThan(wheelZoomFactor(1, 1));
  });

  it('caps a violent trackpad flick', () => {
    expect(wheelZoomFactor(100_000)).toBeLessThanOrEqual(4);
    expect(wheelZoomFactor(-100_000)).toBeGreaterThanOrEqual(0.25);
  });
});

describe('pan', () => {
  it('moves the content with the cursor', () => {
    // Drag 100 px to the right: the plan goes right, so the camera goes left.
    const panned = panByPx(VIEW, 100, 0);
    expect(panned.centreMm.x).toBe(-1000);
    // Drag 100 px down: the plan goes down, so the camera goes north.
    expect(panByPx(VIEW, 0, 100).centreMm.y).toBe(1000);
  });

  it('does not change the zoom', () => {
    expect(panByPx(VIEW, 33, -77).mmPerPx).toBe(VIEW.mmPerPx);
  });
});

describe('fit', () => {
  const plot = { minX: 0, minY: 0, maxX: 30_480, maxY: 12_192 }; // 100 ft × 40 ft

  it('centres the box', () => {
    const view = fitBboxToViewport(plot, SIZE, 48);
    expect(view.centreMm).toEqual({ x: 15_240, y: 6_096 });
  });

  it('fits inside the padded viewport on both axes', () => {
    const view = fitBboxToViewport(plot, SIZE, 48);
    const widthPx = (plot.maxX - plot.minX) / view.mmPerPx;
    const heightPx = (plot.maxY - plot.minY) / view.mmPerPx;
    expect(widthPx).toBeLessThanOrEqual(SIZE.width - 96 + 1e-6);
    expect(heightPx).toBeLessThanOrEqual(SIZE.height - 96 + 1e-6);
    // …and touches one of them, or it is not a fit.
    expect(
      Math.abs(widthPx - (SIZE.width - 96)) < 1e-6 ||
        Math.abs(heightPx - (SIZE.height - 96)) < 1e-6,
    ).toBe(true);
  });

  it('survives a degenerate box — zoom-to-selection on one column', () => {
    const point = { minX: 5000, minY: 5000, maxX: 5000, maxY: 5000 };
    const view = fitBboxToViewport(point, SIZE, 48);
    expect(view.centreMm).toEqual({ x: 5000, y: 5000 });
    expect(view.mmPerPx).toBeGreaterThan(0);
    expect(Number.isFinite(view.mmPerPx)).toBe(true);
  });

  it('survives a viewport smaller than its own padding', () => {
    const view = fitBboxToViewport(plot, { width: 40, height: 30 }, 48);
    expect(Number.isFinite(view.mmPerPx)).toBe(true);
    expect(view.mmPerPx).toBeLessThanOrEqual(MAX_MM_PER_PX);
  });
});

describe('3D', () => {
  it('places the eye by azimuth and polar, in model space', () => {
    const eye = orbitEyeMm({
      targetMm: { x: 0, y: 0, z: 0 },
      distanceMm: 10_000,
      azimuthDeg: 225,
      polarDeg: 60,
    });
    // 225° is south-west of the target, and polar 60° is above the ground.
    expect(eye.x).toBeLessThan(0);
    expect(eye.y).toBeLessThan(0);
    expect(eye.z).toBeGreaterThan(0);
    expect(Math.hypot(eye.x, eye.y, eye.z)).toBeCloseTo(10_000, 6);
  });

  it('puts azimuth 0 due east', () => {
    const eye = orbitEyeMm({
      targetMm: { x: 0, y: 0, z: 0 },
      distanceMm: 10_000,
      azimuthDeg: 0,
      polarDeg: 89,
    });
    expect(eye.x).toBeGreaterThan(9_000);
    expect(Math.abs(eye.y)).toBeLessThan(1e-6);
  });

  it('clamps the polar angle away from the poles and below ground', () => {
    const overhead = orbitEyeMm({
      targetMm: { x: 0, y: 0, z: 0 },
      distanceMm: 10_000,
      azimuthDeg: 0,
      polarDeg: 200,
    });
    expect(overhead.z).toBeGreaterThan(0);
  });

  it('normalises azimuth into [0, 360)', () => {
    expect(normaliseAzimuthDeg(-90)).toBe(270);
    expect(normaliseAzimuthDeg(450)).toBe(90);
    expect(normaliseAzimuthDeg(0)).toBe(0);
  });

  it('reports an equivalent mmPerPx so shared rules keep working in 3D', () => {
    // 10 m away, 50° vertical fov, 600 px tall.
    const mmPerPx = mmPerPxAtDistance(10_000, 600, 50);
    const visible = 2 * 10_000 * Math.tan((50 * Math.PI) / 180 / 2);
    expect(mmPerPx).toBeCloseTo(visible / 600, 9);
  });

  it('fits further away for a bigger box', () => {
    const small = fitDistanceMm({ minX: 0, minY: 0, maxX: 10_000, maxY: 10_000 }, 6_000, 4 / 3);
    const large = fitDistanceMm({ minX: 0, minY: 0, maxX: 40_000, maxY: 40_000 }, 9_000, 4 / 3);
    expect(large).toBeGreaterThan(small);
    expect(small).toBeGreaterThan(0);
  });
});

describe('scale readout', () => {
  it('reports the drawing scale an architect recognises', () => {
    expect(scaleLabel(CSS_MM_PER_PX * 100)).toBe('1:100');
    expect(scaleLabel(CSS_MM_PER_PX * 50)).toBe('1:50');
    expect(scaleLabel(CSS_MM_PER_PX)).toBe('1:1');
  });

  it('snaps to the nearest rung rather than inventing one', () => {
    expect(scaleLabel(CSS_MM_PER_PX * 103.7)).toBe('1:100');
  });
});
