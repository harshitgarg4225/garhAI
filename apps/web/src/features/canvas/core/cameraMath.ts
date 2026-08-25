/**
 * cameraMath.ts — camera state and the pure functions that move it.
 *
 * Zero Three.js imports, zero React, zero DOM. `CameraRig` is the only thing
 * that turns these numbers into a `THREE.Camera`; everything else — the wheel
 * handler, the fit buttons, the zoom readout, the specs — works on the plain
 * data. That separation is what makes pan/zoom testable without a GPU and what
 * keeps the 2D and 3D views describing their cameras in the same vocabulary.
 *
 * ZOOM IS `mmPerPx`, NOT A MULTIPLIER. It is a physical statement — "one screen
 * pixel is this many millimetres of building" — so it composes with print
 * scales (1:100 is 3.78 mm/px at 96 dpi), with pick tolerance ("6 px of slop is
 * this many mm"), and with the grid's fade thresholds ("hide a level once its
 * lines are 4 px apart"). A dimensionless `zoom` scalar composes with none of
 * those and has to be translated at every call site.
 *
 * VIEW STATE IS FLOAT MILLIMETRES, on purpose. A camera centre is not geometry;
 * rounding it to the 115 mm module would make panning visibly notchy. Nothing
 * here ever becomes an op — see the boundary note at the top of `coords.ts`.
 */

import type { Bbox } from '@garh/model';

import {
  DEFAULT_MM_PER_PX,
  DEFAULT_ORBIT_AZIMUTH_DEG,
  DEFAULT_ORBIT_DISTANCE_MM,
  DEFAULT_ORBIT_POLAR_DEG,
  FIT_PADDING_PX,
  MAX_MM_PER_PX,
  MAX_ORBIT_POLAR_DEG,
  MIN_MM_PER_PX,
  MIN_ORBIT_POLAR_DEG,
  PERSP_FOV_DEG,
  WHEEL_LINE_HEIGHT_PX,
  WHEEL_PAGE_HEIGHT_PX,
  WHEEL_ZOOM_RATE,
} from './constants';
import type { PixelPoint, PtF, ViewportSizePx } from './coords';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

/** The orthographic plan camera: where it looks and how far in it is. */
export interface View2D {
  /** Model point at the centre of the viewport, float mm. */
  readonly centreMm: PtF;
  /** Millimetres of building per CSS pixel. */
  readonly mmPerPx: number;
}

/** A model-space 3D point, float mm. `z` is elevation above the plot datum. */
export interface PtF3 {
  readonly x: number;
  readonly y: number;
  readonly z: number;
}

/**
 * The perspective camera, as an orbit. Angles are model-space, so they mean the
 * same thing as the north arrow: azimuth is degrees CCW from +X (east), polar
 * is degrees from straight up (0 = plan view, 90 = eye level).
 */
export interface Orbit3D {
  readonly targetMm: PtF3;
  readonly distanceMm: number;
  readonly azimuthDeg: number;
  readonly polarDeg: number;
}

export const DEFAULT_VIEW_2D: View2D = {
  centreMm: { x: 0, y: 0 },
  mmPerPx: DEFAULT_MM_PER_PX,
};

export const DEFAULT_ORBIT_3D: Orbit3D = {
  targetMm: { x: 0, y: 0, z: 0 },
  distanceMm: DEFAULT_ORBIT_DISTANCE_MM,
  azimuthDeg: DEFAULT_ORBIT_AZIMUTH_DEG,
  polarDeg: DEFAULT_ORBIT_POLAR_DEG,
};

/**
 * How much of a fit a degenerate bbox gets. Zoom-to-selection on a single
 * column has a zero-size box; without a floor the fit would divide by zero and
 * land at maximum magnification with the column filling the screen.
 */
export const FIT_MIN_EXTENT_MM = 2000;

/** One CSS pixel in millimetres of *screen*, at the CSS-defined 96 dpi. */
export const CSS_MM_PER_PX = 25.4 / 96;

/** Hard cap on how much one wheel event may change the zoom. */
const MAX_WHEEL_FACTOR = 4;

// ---------------------------------------------------------------------------
// Clamps
// ---------------------------------------------------------------------------

export function clampMmPerPx(mmPerPx: number): number {
  if (!Number.isFinite(mmPerPx) || mmPerPx <= 0) return DEFAULT_MM_PER_PX;
  return Math.min(MAX_MM_PER_PX, Math.max(MIN_MM_PER_PX, mmPerPx));
}

export function clampPolarDeg(polarDeg: number): number {
  return Math.min(MAX_ORBIT_POLAR_DEG, Math.max(MIN_ORBIT_POLAR_DEG, polarDeg));
}

/** Normalise an azimuth into [0, 360) so the readout never shows −450°. */
export function normaliseAzimuthDeg(deg: number): number {
  const wrapped = deg % 360;
  return wrapped < 0 ? wrapped + 360 : wrapped;
}

export function clampOrbit(orbit: Orbit3D): Orbit3D {
  return {
    targetMm: orbit.targetMm,
    distanceMm: Math.max(500, orbit.distanceMm),
    azimuthDeg: normaliseAzimuthDeg(orbit.azimuthDeg),
    polarDeg: clampPolarDeg(orbit.polarDeg),
  };
}

// ---------------------------------------------------------------------------
// 2D: pixels ↔ millimetres
// ---------------------------------------------------------------------------

/**
 * Canvas pixel → model point (float mm) under the orthographic camera.
 *
 * This mapping and the frustum {@link orthoFrustumWorld} builds are two
 * statements of one fact; if they ever disagree, clicking lands somewhere other
 * than the cursor. `cameraMath.test.ts` pins the round trip.
 */
export function pixelToMmF(view: View2D, px: PixelPoint, size: ViewportSizePx): PtF {
  return {
    x: view.centreMm.x + (px.x - size.width / 2) * view.mmPerPx,
    // Screen Y grows downwards; north grows upwards.
    y: view.centreMm.y - (px.y - size.height / 2) * view.mmPerPx,
  };
}

/** Model point → canvas pixel. Exact inverse of {@link pixelToMmF}. */
export function mmToPixel(view: View2D, mm: PtF, size: ViewportSizePx): PixelPoint {
  return {
    x: size.width / 2 + (mm.x - view.centreMm.x) / view.mmPerPx,
    y: size.height / 2 - (mm.y - view.centreMm.y) / view.mmPerPx,
  };
}

/**
 * The orthographic frustum in **world units**, ready for
 * `OrthographicCamera.left/right/top/bottom`. The camera sits at the view
 * centre, so the frustum is symmetric.
 */
export function orthoFrustumWorld(
  view: View2D,
  size: ViewportSizePx,
): { left: number; right: number; top: number; bottom: number } {
  // (px × mm/px) → mm → world units.
  const halfW = ((size.width / 2) * view.mmPerPx) / 1000;
  const halfH = ((size.height / 2) * view.mmPerPx) / 1000;
  return { left: -halfW, right: halfW, top: halfH, bottom: -halfH };
}

// ---------------------------------------------------------------------------
// 2D: pan, zoom, fit
// ---------------------------------------------------------------------------

/**
 * Drag-pan by a pixel delta. The content follows the cursor, so the centre
 * moves against it — drag right, the plan goes right, the camera goes left.
 */
export function panByPx(view: View2D, dxPx: number, dyPx: number): View2D {
  return {
    centreMm: {
      x: view.centreMm.x - dxPx * view.mmPerPx,
      y: view.centreMm.y + dyPx * view.mmPerPx,
    },
    mmPerPx: view.mmPerPx,
  };
}

/** Pan by a model-space delta — used by "nudge the view" and by fit tweens. */
export function panByMm(view: View2D, dxMm: number, dyMm: number): View2D {
  return {
    centreMm: { x: view.centreMm.x + dxMm, y: view.centreMm.y + dyMm },
    mmPerPx: view.mmPerPx,
  };
}

/**
 * Zoom about a cursor position: the model point under `cursorPx` is the same
 * point under `cursorPx` afterwards. `factor > 1` zooms **out** (more mm per
 * pixel), which is what a positive `deltaY` means.
 *
 * Anchoring on the cursor rather than the centre is not polish — it is the
 * difference between navigating a drawing and chasing it around the screen.
 */
export function zoomAtPixel(
  view: View2D,
  cursorPx: PixelPoint,
  size: ViewportSizePx,
  factor: number,
): View2D {
  const next = clampMmPerPx(view.mmPerPx * factor);
  if (next === view.mmPerPx) return view;
  const anchor = pixelToMmF(view, cursorPx, size);
  return {
    mmPerPx: next,
    centreMm: {
      x: anchor.x - (cursorPx.x - size.width / 2) * next,
      y: anchor.y + (cursorPx.y - size.height / 2) * next,
    },
  };
}

/** Zoom about the centre — the keyboard `+`/`−` path, where there is no cursor. */
export function zoomAtCentre(view: View2D, factor: number): View2D {
  return { centreMm: view.centreMm, mmPerPx: clampMmPerPx(view.mmPerPx * factor) };
}

/**
 * Wheel delta → zoom factor. Exponential, so that N notches out and N notches
 * back returns to exactly the scale you started at; a linear step does not, and
 * the drift is visible within a minute of use.
 *
 * `deltaMode` normalises Firefox's line units and the rare page units against
 * Chrome/Safari's pixels.
 */
export function wheelZoomFactor(deltaY: number, deltaMode = 0): number {
  const unit =
    deltaMode === 1 ? WHEEL_LINE_HEIGHT_PX : deltaMode === 2 ? WHEEL_PAGE_HEIGHT_PX : 1;
  const factor = Math.exp(deltaY * unit * WHEEL_ZOOM_RATE);
  // A trackpad flick can deliver a 600-unit delta in one event; without a cap
  // that is a 2.5× jump and the drawing disappears.
  return Math.min(MAX_WHEEL_FACTOR, Math.max(1 / MAX_WHEEL_FACTOR, factor));
}

/**
 * Fit a model-space box to the viewport with pixel padding. Used by
 * zoom-to-fit-plot and zoom-to-selection — the same function, because "fit the
 * plot" and "fit these three walls" differ only in which box you hand it.
 */
export function fitBboxToViewport(
  box: Bbox,
  size: ViewportSizePx,
  paddingPx: number = FIT_PADDING_PX,
): View2D {
  const centreMm = { x: (box.minX + box.maxX) / 2, y: (box.minY + box.maxY) / 2 };
  const widthMm = Math.max(box.maxX - box.minX, FIT_MIN_EXTENT_MM);
  const heightMm = Math.max(box.maxY - box.minY, FIT_MIN_EXTENT_MM);
  // Guard against a viewport smaller than its own padding (a collapsed panel).
  const availW = Math.max(size.width - paddingPx * 2, 1);
  const availH = Math.max(size.height - paddingPx * 2, 1);
  const mmPerPx = clampMmPerPx(Math.max(widthMm / availW, heightMm / availH));
  return { centreMm, mmPerPx };
}

// ---------------------------------------------------------------------------
// 3D
// ---------------------------------------------------------------------------

const DEG = Math.PI / 180;

/**
 * Where the perspective eye sits, in model space (mm, +Z up). `CameraRig`
 * converts to world; keeping the orbit maths in model space means the sun
 * widget and the north arrow in Phase 5 can share these angles directly.
 */
export function orbitEyeMm(orbit: Orbit3D): PtF3 {
  const polar = clampPolarDeg(orbit.polarDeg) * DEG;
  const azimuth = orbit.azimuthDeg * DEG;
  const horizontal = orbit.distanceMm * Math.sin(polar);
  return {
    x: orbit.targetMm.x + horizontal * Math.cos(azimuth),
    y: orbit.targetMm.y + horizontal * Math.sin(azimuth),
    z: orbit.targetMm.z + orbit.distanceMm * Math.cos(polar),
  };
}

/** Orbit by pixel drag. 0.4°/px is the rate that reads as "attached to the mouse". */
export function orbitByPx(orbit: Orbit3D, dxPx: number, dyPx: number): Orbit3D {
  return clampOrbit({
    ...orbit,
    azimuthDeg: orbit.azimuthDeg - dxPx * 0.4,
    polarDeg: orbit.polarDeg - dyPx * 0.4,
  });
}

/** Dolly in/out. `factor > 1` moves away, matching {@link wheelZoomFactor}. */
export function dollyOrbit(orbit: Orbit3D, factor: number): Orbit3D {
  return clampOrbit({ ...orbit, distanceMm: orbit.distanceMm * factor });
}

/**
 * The 2D-equivalent zoom of a perspective camera at the orbit target, so that
 * everything keyed to `mmPerPx` — pick tolerance, grid fading, label
 * culling — keeps working in 3D instead of needing a parallel implementation.
 */
export function mmPerPxAtDistance(
  distanceMm: number,
  viewportHeightPx: number,
  fovDeg: number = PERSP_FOV_DEG,
): number {
  if (viewportHeightPx <= 0) return DEFAULT_MM_PER_PX;
  const visibleMm = 2 * distanceMm * Math.tan((fovDeg * DEG) / 2);
  return visibleMm / viewportHeightPx;
}

/**
 * Distance at which a box of this size fits the perspective frustum, honouring
 * both axes. `heightMm` is the building's vertical extent — a G+2 is as much a
 * fitting constraint as the plot footprint.
 */
export function fitDistanceMm(
  box: Bbox,
  heightMm: number,
  aspect: number,
  fovDeg: number = PERSP_FOV_DEG,
  paddingFactor = 1.15,
): number {
  const w = Math.max(box.maxX - box.minX, FIT_MIN_EXTENT_MM);
  const d = Math.max(box.maxY - box.minY, FIT_MIN_EXTENT_MM);
  const h = Math.max(heightMm, FIT_MIN_EXTENT_MM);
  const radius = 0.5 * Math.hypot(w, d, h);
  const vFov = fovDeg * DEG;
  const hFov = 2 * Math.atan(Math.tan(vFov / 2) * Math.max(aspect, 0.1));
  const dist = Math.max(radius / Math.sin(vFov / 2), radius / Math.sin(hFov / 2));
  return dist * paddingFactor;
}

/** Fit a box in the 3D view, keeping the current viewing angles. */
export function fitOrbitToBbox(
  orbit: Orbit3D,
  box: Bbox,
  heightMm: number,
  aspect: number,
): Orbit3D {
  return clampOrbit({
    ...orbit,
    targetMm: {
      x: (box.minX + box.maxX) / 2,
      y: (box.minY + box.maxY) / 2,
      z: heightMm / 2,
    },
    distanceMm: fitDistanceMm(box, heightMm, aspect),
  });
}

// ---------------------------------------------------------------------------
// The zoom readout
// ---------------------------------------------------------------------------

/** Scale denominators an architect recognises. Anything else reads as noise. */
const SCALE_LADDER = [1, 2, 5, 10, 20, 25, 50, 75, 100, 150, 200, 250, 500, 1000, 2000];

/**
 * `"1:100"` for the status bar. Snapped to the nearest ladder rung, because the
 * honest answer ("1:103.7") tells an architect nothing they can use, and the
 * rung tells them what the screen is roughly showing.
 */
export function scaleLabel(mmPerPx: number): string {
  const denom = mmPerPx / CSS_MM_PER_PX;
  let best = SCALE_LADDER[0] ?? 1;
  let bestErr = Infinity;
  for (const rung of SCALE_LADDER) {
    // Compare in log space: 1:100 vs 1:150 is the same perceptual step as
    // 1:10 vs 1:15, and a linear comparison would always prefer the big rungs.
    const err = Math.abs(Math.log(denom / rung));
    if (err < bestErr) {
      bestErr = err;
      best = rung;
    }
  }
  return `1:${best}`;
}
