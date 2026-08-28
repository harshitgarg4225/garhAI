/**
 * camera.ts — capture the live camera, put it back, and prove it is the same
 * camera.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * EXACT, NOT APPROXIMATE — AND WHY THAT IS NOT PEDANTRY
 * ════════════════════════════════════════════════════════════════════════════
 * "Kitchen detail" is a place an architect returns to twenty times in a day.
 * If each return lands a hair off, the drawing creeps: the view drifts, and the
 * user starts re-fitting by hand, which is the manual pan-and-zoom this feature
 * exists to abolish. So a restore must reproduce the captured numbers bit for
 * bit, not to within a pixel.
 *
 * Two things make that possible, and both are deliberate:
 *
 *  1. **Capture copies fields; it does not derive them.** No round-tripping
 *     through a matrix, no mm→world→mm, no re-rounding. `SavedCamera` is
 *     structurally `View2D` / `Orbit3D` precisely so that this file has nothing
 *     to compute.
 *
 *  2. **Capture does not clamp.** `clampMmPerPx` and `clampOrbit` are
 *     idempotent on anything the controller is already holding, so clamping
 *     here would usually be a no-op — but "usually" is how a silent one-ulp
 *     drift gets in. Validation belongs at the STORAGE boundary, where the
 *     bytes are untrusted (`persist.ts`), not on a value we just read out of
 *     the live controller.
 *
 * The one thing that could still break exactness is `ViewportController`:
 * `setView2d` clamps what it is given. That is harmless as long as every live
 * `view2d.mmPerPx` is already inside the clamp — which it is, because every
 * mutator on the controller clamps or preserves it. {@link isStorableCamera}
 * states that as a checkable property rather than a hope, and `camera.test.ts`
 * drives the real controller through pan / zoom / wheel / fit and asserts it.
 * If someone later adds a mutator that writes an unclamped zoom, that gate goes
 * red instead of a saved view quietly landing somewhere else.
 */

// Deep imports rather than the `canvas/core` barrel, which re-exports
// `CanvasRoot`, `CameraRig` and `Grid`. This module is pure maths over the
// controller's numbers; pulling react-three-fiber in behind it would make
// every spec that touches a saved camera boot a renderer. Same leaf discipline
// `stores/selection.ts` applies for the same reason.
import {
  clampMmPerPx,
  clampOrbit,
  fitBboxToViewport,
  fitOrbitToBbox,
  scaleLabel,
} from '../canvas/core/cameraMath';
import { FIT_PADDING_PX } from '../canvas/core/constants';
import type { ViewportController } from '../canvas/core/viewport';
import type { SavedCamera, ViewExtent } from './types';

/**
 * The camera as it is right now, in whichever projection is live.
 *
 * Reads `viewport.mode`, so a capture taken while the plan is on screen is a 2D
 * view and one taken in the 3D view is a 3D view — the architect saves what
 * they are looking at, which is the only rule that needs no explaining.
 */
export function captureCamera(viewport: ViewportController): SavedCamera {
  if (viewport.mode === '2d') {
    return {
      mode: '2d',
      centreMm: { x: viewport.view2d.centreMm.x, y: viewport.view2d.centreMm.y },
      mmPerPx: viewport.view2d.mmPerPx,
    };
  }
  const orbit = viewport.orbit;
  return {
    mode: '3d',
    targetMm: { x: orbit.targetMm.x, y: orbit.targetMm.y, z: orbit.targetMm.z },
    distanceMm: orbit.distanceMm,
    azimuthDeg: orbit.azimuthDeg,
    polarDeg: orbit.polarDeg,
  };
}

/**
 * Write a camera into the controller, instantly and exactly.
 *
 * NOTE WHAT THIS DOES NOT DO: it does not change the mode. `view2d` and `orbit`
 * both exist at all times (the controller's "one controller, both cameras"
 * design), so writing the 2D half while the perspective camera is on screen is
 * legal, lands nothing on screen yet, and is exactly what a cross-mode restore
 * wants — the plan is already framed when the rig swaps cameras, so the switch
 * itself IS the transition and there is no second jump. `restore.ts` owns that
 * decision; this function stays honest and narrow.
 */
export function applyCamera(viewport: ViewportController, camera: SavedCamera): void {
  if (camera.mode === '2d') {
    viewport.setView2d({ centreMm: camera.centreMm, mmPerPx: camera.mmPerPx });
    return;
  }
  viewport.setOrbit({
    targetMm: camera.targetMm,
    distanceMm: camera.distanceMm,
    azimuthDeg: camera.azimuthDeg,
    polarDeg: camera.polarDeg,
  });
}

/**
 * Are these the same camera, to the last bit?
 *
 * `Object.is`, not `===`, on every number. The difference that matters here is
 * ±0: a centre of `-0` and a centre of `0` frame the identical view, but they
 * are different bits, and a round-trip check that quietly accepted one for the
 * other would be a check that cannot fail in the one direction we care about.
 * `Object.is` also reports NaN equal to NaN — which would be a lie about a
 * usable camera, so NaN is refused at the storage boundary instead
 * ({@link isStorableCamera}) and can never reach here from a stored view.
 */
export function sameCamera(a: SavedCamera, b: SavedCamera): boolean {
  if (a.mode !== b.mode) return false;
  if (a.mode === '2d' && b.mode === '2d') {
    return (
      Object.is(a.centreMm.x, b.centreMm.x) &&
      Object.is(a.centreMm.y, b.centreMm.y) &&
      Object.is(a.mmPerPx, b.mmPerPx)
    );
  }
  if (a.mode === '3d' && b.mode === '3d') {
    return (
      Object.is(a.targetMm.x, b.targetMm.x) &&
      Object.is(a.targetMm.y, b.targetMm.y) &&
      Object.is(a.targetMm.z, b.targetMm.z) &&
      Object.is(a.distanceMm, b.distanceMm) &&
      Object.is(a.azimuthDeg, b.azimuthDeg) &&
      Object.is(a.polarDeg, b.polarDeg)
    );
  }
  return false;
}

/**
 * Is this camera one the controller will take back unchanged?
 *
 * The property `applyCamera(vp, c); sameCamera(captureCamera(vp), c)` — stated
 * without needing a viewport, so it can be asserted on a stored payload before
 * it is ever restored. `setView2d` clamps, so a 2D camera is storable only if
 * its zoom is already a fixed point of the clamp; `setOrbit` does not clamp, so
 * a 3D camera only has to be finite.
 *
 * This is the gate that would catch a future controller mutator writing an
 * unclamped zoom, and it is what `persist.ts` normalises stored payloads
 * against.
 */
export function isStorableCamera(camera: SavedCamera): boolean {
  if (camera.mode === '2d') {
    return (
      isFiniteNumber(camera.centreMm.x) &&
      isFiniteNumber(camera.centreMm.y) &&
      isFiniteNumber(camera.mmPerPx) &&
      Object.is(clampMmPerPx(camera.mmPerPx), camera.mmPerPx)
    );
  }
  return (
    isFiniteNumber(camera.targetMm.x) &&
    isFiniteNumber(camera.targetMm.y) &&
    isFiniteNumber(camera.targetMm.z) &&
    isFiniteNumber(camera.distanceMm) &&
    isFiniteNumber(camera.azimuthDeg) &&
    isFiniteNumber(camera.polarDeg)
  );
}

/**
 * Force an untrusted camera into a storable one.
 *
 * Only ever applied to bytes read back from `localStorage` — a payload written
 * by an older build, by a different clamp table, or by a curious user with the
 * devtools open. Clamping there means every camera the store holds is one the
 * controller will accept verbatim, which is what keeps the exactness promise
 * true for restored-from-disk views and not only for freshly captured ones.
 */
export function normaliseCamera(camera: SavedCamera): SavedCamera {
  if (camera.mode === '2d') {
    return { mode: '2d', centreMm: camera.centreMm, mmPerPx: clampMmPerPx(camera.mmPerPx) };
  }
  return { mode: '3d', ...clampOrbit(camera) };
}

/**
 * The camera that frames `extent` in whichever projection is live — how a
 * built-in view becomes an ordinary `SavedCamera`.
 *
 * Both branches delegate to the canvas core's own fit maths, so "fit" means the
 * same thing here as it does on the F key and on the compliance chips. A second
 * implementation of fitting would drift from that one within a release.
 *
 * The 3D branch keeps the current viewing angles — a fit moves the camera, it
 * does not spin the building — and takes the height from the extent rather than
 * from `viewport.fitHeightMm`, because that field is shared state owned by the
 * 3D scene and writing it here to frame one storey would silently change what
 * the F key frames afterwards.
 */
export function cameraForExtent(viewport: ViewportController, extent: ViewExtent): SavedCamera {
  if (viewport.mode === '2d') {
    const view = fitBboxToViewport(extent.box, viewport.sizePx, FIT_PADDING_PX);
    return { mode: '2d', centreMm: view.centreMm, mmPerPx: view.mmPerPx };
  }
  const orbit = fitOrbitToBbox(viewport.orbit, extent.box, extent.heightMm, viewport.aspect);
  return { mode: '3d', ...orbit };
}

/**
 * A one-line description of what a saved camera is looking at, for the row's
 * tooltip.
 *
 * 2D gets the printed scale from the canvas core's own `scaleLabel` — the same
 * string the status bar shows, because an architect reads "1:100" instantly and
 * `mmPerPx` never. 3D gets the compass bearing, since "which side of the house
 * is this" is the only question a 3D bookmark's title has to answer.
 */
export function describeCamera(camera: SavedCamera): string {
  if (camera.mode === '2d') return `Plan, about ${scaleLabel(camera.mmPerPx)}`;
  return `3D, ${Math.round(camera.azimuthDeg).toString()}° azimuth`;
}

function isFiniteNumber(value: number): boolean {
  return typeof value === 'number' && Number.isFinite(value);
}
