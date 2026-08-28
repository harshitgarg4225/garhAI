/**
 * Spec for capture/apply — the trap this whole feature turns on.
 *
 * A saved camera that does not round-trip is worse than no saved camera: the
 * architect trusts it, returns twenty times a day, and the view creeps. So the
 * assertions here are EQUALITY on the captured state, not "close enough", and
 * every one of them is negative-controlled by perturbing a single field by an
 * amount no screen could show (1e-12 mm) to prove the check would notice.
 *
 * Everything runs against the REAL `ViewportController` driven through its real
 * pan / zoom / wheel / fit / orbit methods. A hand-built stub would let this
 * file pass while the controller clamped, re-rounded or normalised the value on
 * the way back in — which is precisely the failure being guarded against.
 */

import { describe, expect, it } from 'vitest';

import { DEFAULT_MM_PER_PX, MAX_MM_PER_PX, MIN_MM_PER_PX } from '../canvas/core/constants';
import { ViewportController } from '../canvas/core/viewport';
import {
  applyCamera,
  cameraForExtent,
  captureCamera,
  isStorableCamera,
  normaliseCamera,
  sameCamera,
} from './camera';
import type { Saved2dCamera, Saved3dCamera, SavedCamera } from './types';

function viewport2d(): ViewportController {
  const viewport = new ViewportController();
  viewport.setSize(1280, 840);
  return viewport;
}

function viewport3d(): ViewportController {
  const viewport = viewport2d();
  viewport.setMode('3d');
  return viewport;
}

/** Drive the plan camera somewhere awkward — deliberately not a round number. */
function wanderIn2d(viewport: ViewportController): void {
  viewport.panPx(37, -119);
  viewport.wheel(-213, 0, { x: 613, y: 291 });
  viewport.panPx(-8, 44);
  viewport.zoomAtPixel({ x: 17, y: 803 }, 0.9137);
}

function wanderIn3d(viewport: ViewportController): void {
  viewport.setOrbit({
    targetMm: { x: 4321.5, y: -987.25, z: 1500.125 },
    distanceMm: 18_733.61,
    azimuthDeg: 217.37,
    polarDeg: 63.19,
  });
  viewport.wheel(-97, 0, { x: 400, y: 400 });
}

describe('captureCamera / applyCamera round trip', () => {
  it('puts the 2D camera back on exactly the same numbers', () => {
    const viewport = viewport2d();
    wanderIn2d(viewport);
    const saved = captureCamera(viewport);

    // Go somewhere else, and prove we really went somewhere else — otherwise
    // "it restored" would be indistinguishable from "it never moved".
    viewport.panPx(-540, 260);
    viewport.wheel(320, 0, { x: 100, y: 100 });
    expect(sameCamera(captureCamera(viewport), saved)).toBe(false);

    applyCamera(viewport, saved);

    const restored = captureCamera(viewport);
    expect(restored).toEqual(saved);
    expect(sameCamera(restored, saved)).toBe(true);
    // Structural equality is not enough on its own: assert the actual floats.
    const back = restored as Saved2dCamera;
    const before = saved as Saved2dCamera;
    expect(back.centreMm.x).toBe(before.centreMm.x);
    expect(back.centreMm.y).toBe(before.centreMm.y);
    expect(back.mmPerPx).toBe(before.mmPerPx);
  });

  it('NEGATIVE CONTROL: a 1e-12 mm perturbation of one field is caught', () => {
    const viewport = viewport2d();
    wanderIn2d(viewport);
    const saved = captureCamera(viewport) as Saved2dCamera;

    const nudgedCentre: Saved2dCamera = {
      ...saved,
      centreMm: { x: saved.centreMm.x + 1e-12, y: saved.centreMm.y },
    };
    const nudgedZoom: Saved2dCamera = { ...saved, mmPerPx: saved.mmPerPx * (1 + Number.EPSILON) };

    expect(sameCamera(nudgedCentre, saved)).toBe(false);
    expect(sameCamera(nudgedZoom, saved)).toBe(false);

    // …and the perturbation is genuinely below anything a user could see: at
    // this zoom it is a millionth of a pixel. The check is stricter than the
    // eye, which is the point.
    expect(Math.abs(1e-12 / saved.mmPerPx)).toBeLessThan(1e-6);
  });

  it('puts the 3D camera back on exactly the same numbers', () => {
    const viewport = viewport3d();
    wanderIn3d(viewport);
    const saved = captureCamera(viewport);

    viewport.setOrbit({
      targetMm: { x: 0, y: 0, z: 0 },
      distanceMm: 40_000,
      azimuthDeg: 12,
      polarDeg: 20,
    });
    expect(sameCamera(captureCamera(viewport), saved)).toBe(false);

    applyCamera(viewport, saved);

    const restored = captureCamera(viewport);
    expect(restored).toEqual(saved);
    const back = restored as Saved3dCamera;
    const before = saved as Saved3dCamera;
    expect(back.targetMm).toEqual(before.targetMm);
    expect(back.distanceMm).toBe(before.distanceMm);
    expect(back.azimuthDeg).toBe(before.azimuthDeg);
    expect(back.polarDeg).toBe(before.polarDeg);
  });

  it('NEGATIVE CONTROL: a 1e-12° perturbation of the azimuth is caught', () => {
    const viewport = viewport3d();
    wanderIn3d(viewport);
    const saved = captureCamera(viewport) as Saved3dCamera;
    expect(sameCamera({ ...saved, azimuthDeg: saved.azimuthDeg + 1e-12 }, saved)).toBe(false);
    expect(sameCamera({ ...saved, distanceMm: saved.distanceMm + 1e-9 }, saved)).toBe(false);
    expect(
      sameCamera({ ...saved, targetMm: { ...saved.targetMm, z: saved.targetMm.z + 1e-12 } }, saved),
    ).toBe(false);
  });

  it('captures whichever projection is live, and only that one', () => {
    const viewport = viewport2d();
    expect(captureCamera(viewport).mode).toBe('2d');
    viewport.setMode('3d');
    expect(captureCamera(viewport).mode).toBe('3d');
  });

  it('writes the 2D half while the 3D camera is on screen, leaving the orbit alone', () => {
    const viewport = viewport3d();
    const orbitBefore = captureCamera(viewport);
    const plan: Saved2dCamera = { mode: '2d', centreMm: { x: 1234, y: -567 }, mmPerPx: 4.25 };

    applyCamera(viewport, plan);

    // The orbit is untouched — a cross-mode restore must not disturb the view
    // the user is actually looking at until the mode itself changes.
    expect(sameCamera(captureCamera(viewport), orbitBefore)).toBe(true);
    // …and the plan camera is already framed, ready for the swap.
    expect(viewport.view2d.centreMm).toEqual({ x: 1234, y: -567 });
    expect(viewport.view2d.mmPerPx).toBe(4.25);
  });
});

describe('sameCamera', () => {
  it('refuses to call two projections the same view', () => {
    const plan: SavedCamera = { mode: '2d', centreMm: { x: 0, y: 0 }, mmPerPx: 12 };
    const orbit: SavedCamera = {
      mode: '3d',
      targetMm: { x: 0, y: 0, z: 0 },
      distanceMm: 25_000,
      azimuthDeg: 225,
      polarDeg: 60,
    };
    expect(sameCamera(plan, orbit)).toBe(false);
    expect(sameCamera(orbit, plan)).toBe(false);
  });

  it('distinguishes -0 from 0, which `===` would not', () => {
    const a: SavedCamera = { mode: '2d', centreMm: { x: 0, y: 0 }, mmPerPx: 12 };
    const b: SavedCamera = { mode: '2d', centreMm: { x: -0, y: 0 }, mmPerPx: 12 };
    expect(a.centreMm.x === b.centreMm.x).toBe(true); // `===` says yes…
    expect(sameCamera(a, b)).toBe(false); // …and this says what it really is
  });
});

describe('isStorableCamera — the gate on the controller staying clamped', () => {
  /**
   * THE GATE. Every camera the live controller can hold must be one it takes
   * back unchanged. It is true today because every mutator clamps or preserves
   * `mmPerPx`. If someone adds one that does not, a saved view would silently
   * land somewhere else — and this goes red instead.
   */
  it('holds for every camera the real controller produces', () => {
    const viewport = viewport2d();
    const cameras: SavedCamera[] = [captureCamera(viewport)];

    wanderIn2d(viewport);
    cameras.push(captureCamera(viewport));

    // Zoom hard against both clamps, from both directions.
    for (let i = 0; i < 40; i++) viewport.wheel(600, 0, { x: 640, y: 420 });
    cameras.push(captureCamera(viewport));
    for (let i = 0; i < 80; i++) viewport.wheel(-600, 0, { x: 12, y: 830 });
    cameras.push(captureCamera(viewport));

    viewport.fitBbox({ minX: 0, minY: 0, maxX: 9144, maxY: 12_192 }, { animate: false });
    cameras.push(captureCamera(viewport));

    viewport.setMode('3d');
    wanderIn3d(viewport);
    cameras.push(captureCamera(viewport));
    viewport.fitBbox({ minX: 0, minY: 0, maxX: 9144, maxY: 12_192 }, { animate: false });
    cameras.push(captureCamera(viewport));

    for (const camera of cameras) {
      expect(isStorableCamera(camera)).toBe(true);
      // The property spelled out: apply it, capture it, it is the same camera.
      const fresh = camera.mode === '2d' ? viewport2d() : viewport3d();
      applyCamera(fresh, camera);
      expect(sameCamera(captureCamera(fresh), camera)).toBe(true);
    }
  });

  it('NEGATIVE CONTROL: an out-of-clamp zoom is not storable, and does not round-trip', () => {
    const tooFar: Saved2dCamera = {
      mode: '2d',
      centreMm: { x: 0, y: 0 },
      mmPerPx: MAX_MM_PER_PX * 10,
    };
    expect(isStorableCamera(tooFar)).toBe(false);

    const viewport = viewport2d();
    applyCamera(viewport, tooFar);
    expect(sameCamera(captureCamera(viewport), tooFar)).toBe(false);
    expect(viewport.view2d.mmPerPx).toBe(MAX_MM_PER_PX);
  });

  it('refuses NaN and infinities in either projection', () => {
    expect(isStorableCamera({ mode: '2d', centreMm: { x: Number.NaN, y: 0 }, mmPerPx: 12 })).toBe(
      false,
    );
    expect(
      isStorableCamera({
        mode: '3d',
        targetMm: { x: 0, y: 0, z: 0 },
        distanceMm: Number.POSITIVE_INFINITY,
        azimuthDeg: 0,
        polarDeg: 45,
      }),
    ).toBe(false);
  });
});

describe('normaliseCamera', () => {
  it('drags an untrusted payload into the range the controller accepts', () => {
    const wild = normaliseCamera({
      mode: '2d',
      centreMm: { x: 10, y: 20 },
      mmPerPx: MIN_MM_PER_PX / 100,
    });
    expect(isStorableCamera(wild)).toBe(true);
    expect((wild as Saved2dCamera).mmPerPx).toBe(MIN_MM_PER_PX);

    const orbit = normaliseCamera({
      mode: '3d',
      targetMm: { x: 0, y: 0, z: 0 },
      distanceMm: 10,
      azimuthDeg: -450,
      polarDeg: 179,
    }) as Saved3dCamera;
    expect(orbit.distanceMm).toBe(500);
    expect(orbit.azimuthDeg).toBe(270);
    expect(orbit.polarDeg).toBeLessThanOrEqual(89);
  });

  it('leaves a live camera untouched — it is already a fixed point', () => {
    const viewport = viewport2d();
    wanderIn2d(viewport);
    const live = captureCamera(viewport);
    expect(sameCamera(normaliseCamera(live), live)).toBe(true);
  });

  it('is idempotent, so a re-read payload never drifts', () => {
    const once = normaliseCamera({ mode: '2d', centreMm: { x: 1, y: 2 }, mmPerPx: 1e9 });
    expect(sameCamera(normaliseCamera(once), once)).toBe(true);
  });
});

describe('cameraForExtent', () => {
  const BOX = { minX: 0, minY: 0, maxX: 9144, maxY: 12_192 };

  it('frames the box in 2D, centred and inside the zoom clamps', () => {
    const viewport = viewport2d();
    const camera = cameraForExtent(viewport, { box: BOX, heightMm: 9000 }) as Saved2dCamera;
    expect(camera.mode).toBe('2d');
    expect(camera.centreMm).toEqual({ x: 4572, y: 6096 });
    expect(camera.mmPerPx).toBeGreaterThanOrEqual(MIN_MM_PER_PX);
    expect(camera.mmPerPx).toBeLessThanOrEqual(MAX_MM_PER_PX);
    // The box must actually fit: 12.192 m of depth inside 840 px of viewport.
    expect(camera.mmPerPx * 840).toBeGreaterThanOrEqual(12_192);
  });

  it('frames the box in 3D without spinning the camera', () => {
    const viewport = viewport3d();
    wanderIn3d(viewport);
    const before = captureCamera(viewport) as Saved3dCamera;
    const camera = cameraForExtent(viewport, { box: BOX, heightMm: 9000 }) as Saved3dCamera;

    expect(camera.mode).toBe('3d');
    expect(camera.azimuthDeg).toBe(before.azimuthDeg);
    expect(camera.polarDeg).toBe(before.polarDeg);
    expect(camera.targetMm.x).toBe(4572);
    expect(camera.targetMm.y).toBe(6096);
    expect(camera.distanceMm).toBeGreaterThan(0);
  });

  it('does not touch the shared 3D fit height', () => {
    const viewport = viewport3d();
    viewport.setFitHeightMm(7777);
    cameraForExtent(viewport, { box: BOX, heightMm: 3000 });
    // The 3D scene owns this field; framing one storey must not change what
    // the F key frames afterwards.
    expect(viewport.fitHeightMm).toBe(7777);
  });

  it('gives a degenerate box a usable frame instead of infinite magnification', () => {
    const viewport = viewport2d();
    const point = { minX: 5000, minY: 5000, maxX: 5000, maxY: 5000 };
    const camera = cameraForExtent(viewport, { box: point, heightMm: 3000 }) as Saved2dCamera;
    expect(Number.isFinite(camera.mmPerPx)).toBe(true);
    expect(camera.mmPerPx).toBeGreaterThan(0);
    expect(camera.mmPerPx).not.toBe(DEFAULT_MM_PER_PX);
  });
});
