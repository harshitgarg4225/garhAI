/**
 * overlayProjection.ts — model millimetres → canvas CSS pixels, for the DOM
 * chrome that floats over the drawing.
 *
 * This is NOT a second coordinate system. Every step delegates to
 * `core/coords.ts`, which the canvas core declares to be the one conversion
 * boundary in the product:
 *
 *     mm → world      `mmToWorld`      (the +Y-north → −Z flip lives there)
 *     world → NDC     `Vector3.project(camera)`   (three's own projection)
 *     NDC  → pixels   `pixelFromNdc`   (the +Y-up → +Y-down flip lives there)
 *
 * The only thing added here is the composition and the "is it worth drawing"
 * answer, and the reason to write it down once is the reason `coords.ts` gives
 * for existing at all: a module that does its own `/ 304.8` is a module that
 * will eventually disagree with the drawing it is annotating.
 *
 * PERF: one module-scoped scratch `Vector3`, reused. This runs once per pin and
 * once per remote cursor per camera commit, and allocating a vector per call
 * would put garbage collection inside a pan.
 */

import { Vector3 } from 'three';
import type { Camera } from 'three';

import type { Pt } from '@garh/model';

import { mmToWorld, pixelFromNdc, type PixelPoint, type PtF, type ViewportSizePx } from '../core';

const scratch = /* @__PURE__ */ new Vector3();

/** A projected overlay position, plus whether it is worth drawing. */
export interface OverlayPoint extends PixelPoint {
  /**
   * False when the point is behind the camera or well outside the canvas.
   * Callers SKIP those rather than clamping them to the edge: a pin clamped to
   * the viewport border is a pin claiming to mark something it is not marking.
   */
  readonly onScreen: boolean;
}

/**
 * Project a plot-local millimetre point onto the canvas, in CSS pixels measured
 * from the canvas element's top-left.
 *
 * `elevationMm` is the plane the point sits on — the active storey's FFL — so
 * that in an orbited view a pin stays glued to the floor it belongs to rather
 * than to the datum. In the orthographic top view it makes no difference to
 * x/y, which is exactly why it is easy to forget and worth naming here.
 *
 * `marginPx` widens the on-screen band. A cursor arrow is ~14px and a pin ~22px
 * and both are anchored at a tip, so a strict viewport test would blink them
 * out several pixels before they visually left the canvas; a margin lets them
 * slide off the edge instead. Depth is NOT subject to the margin: a point
 * behind a perspective camera projects to a plausible-looking pixel and must
 * stay rejected however close to the viewport that pixel lands.
 */
export function projectMmToOverlay(
  ptMm: Pt | PtF,
  elevationMm: number,
  camera: Camera,
  sizePx: ViewportSizePx,
  marginPx = 0,
): OverlayPoint {
  mmToWorld(ptMm, elevationMm, scratch);
  scratch.project(camera);

  // `project` leaves NDC depth in z; outside [-1, 1] is beyond a clip plane,
  // which for a perspective camera includes "behind you".
  const depthOk = scratch.z >= -1 && scratch.z <= 1;
  const px = pixelFromNdc({ x: scratch.x, y: scratch.y }, sizePx);
  const insideViewport =
    px.x >= -marginPx &&
    px.y >= -marginPx &&
    px.x <= sizePx.width + marginPx &&
    px.y <= sizePx.height + marginPx;

  return { x: px.x, y: px.y, onScreen: depthOk && insideViewport };
}
