/**
 * wheelGesture.ts — what a `wheel` event MEANS: pan, or zoom.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE PROBLEM
 * ════════════════════════════════════════════════════════════════════════════
 * A mouse wheel and a trackpad deliver the same DOM event. CAD muscle memory
 * says the wheel ZOOMS (AutoCAD, SketchUp, Revit); a laptop trackpad's
 * two-finger scroll means PAN everywhere else the architect uses it, and its
 * pinch arrives as a wheel with `ctrlKey` set. Treating every wheel as a zoom
 * — what this canvas did — makes a MacBook two-finger scroll zoom instead of
 * pan and makes pinch indistinguishable from scroll. The browser does not say
 * which device sent the event, so this file decides from what the event
 * carries, and states the heuristic so the next reader can see where it is
 * wrong rather than discover it.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE RULE, IN ORDER
 * ════════════════════════════════════════════════════════════════════════════
 *   1. `ctrlKey` or `metaKey`      → ZOOM. A pinch arrives as ctrl+wheel in
 *                                     every browser; ⌘/Ctrl+wheel is a
 *                                     deliberate zoom on any device.
 *   2. `deltaMode` lines or pages  → ZOOM. Only a wheel (Firefox, or a
 *                                     keyboard) reports line mode.
 *   3. `deltaX !== 0`              → PAN. A wheel is one-axis; a horizontal
 *                                     component is a trackpad or a tilt wheel,
 *                                     both of which mean "move the view".
 *   4. `shiftKey`                  → PAN sideways. The shift-scroll idiom.
 *   5. `wheelDeltaY` a multiple of 120 (Chrome/Safari set it for a wheel
 *      notch and something else for a trackpad)         → ZOOM.
 *   6. otherwise a whole-number `deltaY` of at least a notch (Chrome on
 *      Windows without `wheelDeltaY`)                    → ZOOM,
 *      and any small or fractional vertical delta        → PAN.
 *
 * WHERE THIS IS WRONG, STATED: a Chrome trackpad flick whose legacy
 * `wheelDeltaY` happens to land on a multiple of 120 zooms once; a mouse that
 * reports fractional deltas with no `wheelDeltaY` pans. Both are rare, both
 * are one event, and ctrl/⌘+wheel always zooms whatever the device.
 *
 * Pure: no DOM, no three. Exercised by `wheelGesture.test.ts` with synthetic
 * events for each device, including the negative controls.
 */

/** The fields of a `WheelEvent` the rule reads. `wheelDeltaY` is the legacy Chrome/Safari field. */
export interface WheelLike {
  readonly deltaX: number;
  readonly deltaY: number;
  /** 0 pixels, 1 lines, 2 pages. */
  readonly deltaMode: number;
  readonly ctrlKey: boolean;
  readonly metaKey: boolean;
  readonly shiftKey: boolean;
  readonly wheelDeltaY?: number | undefined;
}

export type WheelGesture =
  | {
      readonly kind: 'zoom';
      /** True when the event is a pinch (ctrl-wheel), which needs its own gain. */
      readonly pinch: boolean;
      readonly deltaY: number;
      readonly deltaMode: number;
    }
  | {
      readonly kind: 'pan';
      /** CSS pixels the CONTENT should move by — the negative of the scroll. */
      readonly dxPx: number;
      readonly dyPx: number;
    };

/** One notch of a classic wheel, in `deltaY` pixels. Chrome reports 100; some mice 120. */
export const WHEEL_NOTCH_MIN_PX = 40;

/**
 * Zoom rate for a pinch. Chrome delivers a pinch as ctrl-wheel events with
 * `deltaY` of a few units each, so the wheel's 0.0015 per unit would need
 * hundreds of them; 0.01 makes a comfortable two-finger spread roughly one
 * doubling, which is what the same gesture does in a map.
 */
export const PINCH_ZOOM_RATE = 0.01;

/** Cap on one pinch event's factor: a jittery trackpad must not jump the view. */
export const MAX_PINCH_FACTOR = 1.5;

/** Pixels per line / page for a line-mode pan (Firefox wheels with shift). */
const LINE_PX = 16;
const PAGE_PX = 400;

function unitPx(deltaMode: number): number {
  return deltaMode === 1 ? LINE_PX : deltaMode === 2 ? PAGE_PX : 1;
}

export function classifyWheel(event: WheelLike): WheelGesture {
  if (event.ctrlKey || event.metaKey) {
    return {
      kind: 'zoom',
      pinch: event.ctrlKey && !event.metaKey,
      deltaY: event.deltaY,
      deltaMode: event.deltaMode,
    };
  }
  if (event.deltaMode !== 0) {
    if (event.shiftKey) {
      const px = unitPx(event.deltaMode);
      return { kind: 'pan', dxPx: -event.deltaY * px, dyPx: 0 };
    }
    return { kind: 'zoom', pinch: false, deltaY: event.deltaY, deltaMode: event.deltaMode };
  }
  if (event.deltaX !== 0) {
    return { kind: 'pan', dxPx: -event.deltaX, dyPx: -event.deltaY };
  }
  if (event.shiftKey) {
    return { kind: 'pan', dxPx: -event.deltaY, dyPx: 0 };
  }
  const legacy = event.wheelDeltaY;
  if (legacy !== undefined && legacy !== 0) {
    const notch = Math.abs(legacy) % 120 === 0;
    return notch
      ? { kind: 'zoom', pinch: false, deltaY: event.deltaY, deltaMode: 0 }
      : { kind: 'pan', dxPx: 0, dyPx: -event.deltaY };
  }
  const notch = Number.isInteger(event.deltaY) && Math.abs(event.deltaY) >= WHEEL_NOTCH_MIN_PX;
  return notch
    ? { kind: 'zoom', pinch: false, deltaY: event.deltaY, deltaMode: 0 }
    : { kind: 'pan', dxPx: 0, dyPx: -event.deltaY };
}

/**
 * The zoom factor for one pinch event. `exp` so that spreading and pinching
 * back return to the same scale (the same reason `wheelZoomFactor` is
 * exponential), clamped per event.
 */
export function pinchZoomFactor(deltaY: number): number {
  const factor = Math.exp(deltaY * PINCH_ZOOM_RATE);
  return Math.min(MAX_PINCH_FACTOR, Math.max(1 / MAX_PINCH_FACTOR, factor));
}

// ---------------------------------------------------------------------------
// Two-finger touch: pan by the centroid, zoom by the spread
// ---------------------------------------------------------------------------

export interface TouchPoint {
  readonly x: number;
  readonly y: number;
}

export interface PinchState {
  readonly centre: TouchPoint;
  readonly spreadPx: number;
}

/** Centroid and finger distance of two touches. */
export function pinchState(a: TouchPoint, b: TouchPoint): PinchState {
  return {
    centre: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 },
    spreadPx: Math.hypot(b.x - a.x, b.y - a.y),
  };
}

/**
 * What a two-finger move does to the view: pan by how far the centroid moved,
 * and scale `mmPerPx` by `before / after` of the spread (fingers apart = zoom
 * in = fewer millimetres per pixel). A degenerate spread leaves the zoom alone
 * rather than dividing by zero.
 */
export function pinchDelta(
  before: PinchState,
  after: PinchState,
): { readonly dxPx: number; readonly dyPx: number; readonly factor: number } {
  const factor = before.spreadPx > 1 && after.spreadPx > 1 ? before.spreadPx / after.spreadPx : 1;
  return {
    dxPx: after.centre.x - before.centre.x,
    dyPx: after.centre.y - before.centre.y,
    factor: Math.min(MAX_PINCH_FACTOR, Math.max(1 / MAX_PINCH_FACTOR, factor)),
  };
}
