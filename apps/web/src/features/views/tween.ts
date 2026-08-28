/**
 * tween.ts — the shape of the flight between two cameras. Pure: no rAF, no
 * controller, no DOM. `restore.ts` is what drives it.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE ENDPOINT IS THE TARGET OBJECT ITSELF
 * ════════════════════════════════════════════════════════════════════════════
 * {@link interpolateCamera} returns `to` BY REFERENCE at `k >= 1`, before any
 * arithmetic runs. That single early return is what makes an animated restore
 * land exactly where a saved view says, and it is not a micro-optimisation:
 *
 *     Math.exp(Math.log(12))            // 12.000000000000002
 *     from + (to - from) * 1            // one ulp off `to`, routinely
 *
 * `ViewportController.animateTo` interpolates zoom in log space for good
 * reasons (a linear `mmPerPx` ramp reads as a snap followed by a crawl), and
 * its final frame therefore lands an ulp away from the value it was aiming at.
 * That is invisible for a one-shot zoom-to-fit, which is all it was written
 * for. It is NOT invisible for a named view: the error is re-captured on the
 * next save and compounds, and "the kitchen detail" slowly stops being the
 * kitchen detail. So this module tweens towards the target and then lands ON
 * it, and `tween.test.ts` pins the identity rather than trusting the float.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT IS INTERPOLATED IN WHAT SPACE
 * ════════════════════════════════════════════════════════════════════════════
 *   centre, target   linear — it is a position
 *   mmPerPx          log    — see above; the same choice `animateTo` makes
 *   distance         log    — a dolly is the 3D twin of a zoom
 *   polar            linear — clamped to (0, 90), so it cannot wrap
 *   azimuth          SHORTEST ARC — 350° → 10° is +20°, not −340°. Going the
 *                    long way is a full spin round the building on a restore
 *                    that should have nudged; the wrap is the whole reason this
 *                    one angle gets its own function.
 */

import type { SavedCamera } from './types';

/** Ease-out cubic — the same curve the viewport's own fit tween uses. */
export function easeOutCubic(t: number): number {
  const clamped = t <= 0 ? 0 : t >= 1 ? 1 : t;
  return 1 - Math.pow(1 - clamped, 3);
}

/**
 * Signed shortest angular step from `from` to `to`, in [−180, 180).
 *
 * The `+ 540` before the modulo is the standard trick for making JavaScript's
 * sign-preserving `%` behave like a true modulo over a full turn; it costs
 * nothing and removes the branch on negative input.
 *
 * Two views exactly half a turn apart resolve to −180 (anticlockwise). Either
 * direction is equally short, so the choice is arbitrary — but it is fixed, and
 * a fixed answer is what keeps a restore reproducible.
 */
export function shortestAngleDeltaDeg(fromDeg: number, toDeg: number): number {
  return ((((toDeg - fromDeg) % 360) + 540) % 360) - 180;
}

/**
 * The camera `k` of the way from `from` to `to`, `k` in [0, 1].
 *
 * `k >= 1` returns `to` itself — see the header. Cross-mode pairs also return
 * `to`: there is no continuous path between an orthographic frustum and a
 * perspective one, and pretending otherwise would animate a camera nobody is
 * looking at (`restore.ts` handles that case by not animating at all).
 */
export function interpolateCamera(from: SavedCamera, to: SavedCamera, k: number): SavedCamera {
  if (k >= 1) return to;
  if (from.mode !== to.mode) return to;
  if (k <= 0) return from;

  if (from.mode === '2d' && to.mode === '2d') {
    return {
      mode: '2d',
      centreMm: {
        x: lerp(from.centreMm.x, to.centreMm.x, k),
        y: lerp(from.centreMm.y, to.centreMm.y, k),
      },
      mmPerPx: lerpLog(from.mmPerPx, to.mmPerPx, k),
    };
  }
  if (from.mode === '3d' && to.mode === '3d') {
    return {
      mode: '3d',
      targetMm: {
        x: lerp(from.targetMm.x, to.targetMm.x, k),
        y: lerp(from.targetMm.y, to.targetMm.y, k),
        z: lerp(from.targetMm.z, to.targetMm.z, k),
      },
      distanceMm: lerpLog(from.distanceMm, to.distanceMm, k),
      azimuthDeg: from.azimuthDeg + shortestAngleDeltaDeg(from.azimuthDeg, to.azimuthDeg) * k,
      polarDeg: lerp(from.polarDeg, to.polarDeg, k),
    };
  }
  return to;
}

function lerp(a: number, b: number, k: number): number {
  return a + (b - a) * k;
}

/**
 * Interpolate in log space. Both inputs are strictly positive by construction
 * (`clampMmPerPx` floors at 0.25; `clampOrbit` floors the distance at 500), but
 * a non-positive value would produce NaN and a camera that renders nothing, so
 * it falls back to a linear ramp rather than poisoning the flight.
 */
function lerpLog(a: number, b: number, k: number): number {
  if (a <= 0 || b <= 0) return lerp(a, b, k);
  return Math.exp(lerp(Math.log(a), Math.log(b), k));
}
