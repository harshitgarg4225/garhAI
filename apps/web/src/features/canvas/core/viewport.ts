/**
 * viewport.ts — camera state that lives *outside* React.
 *
 * §14 gives the canvas 16 ms per frame during a pan. A `useState` holding the
 * camera centre spends a chunk of that re-rendering a React tree on every
 * `pointermove`, for a value only the renderer reads. So the camera lives in
 * this controller: pointer handlers mutate it, `CameraRig` reads it, and React
 * finds out only if it asked to (`useViewportValue`, rAF-coalesced, for things
 * like the "1:100" readout).
 *
 * The other half of the budget is `frameloop="demand"`. Nothing renders unless
 * something changed, so an idle canvas costs zero — and every mutation here
 * ends in {@link ViewportController.commit}, which is the single place that
 * asks for a frame. If you change camera state without committing, the screen
 * does not update; that is the design, not a bug to work around with a
 * `useFrame`.
 *
 * ONE CONTROLLER, BOTH CAMERAS. `view2d` and `orbit` both exist at all times
 * and neither is destroyed by switching modes, so Tab (2D↔3D) is a projection
 * change, not a rebuild — you come back to the plan exactly where you left it.
 */

import type { Bbox } from '@garh/model';

import { DEFAULT_MM_PER_PX, PERSP_FOV_DEG, type CanvasMode } from './constants';
import {
  clampMmPerPx,
  fitBboxToViewport,
  fitOrbitToBbox,
  mmPerPxAtDistance,
  panByPx,
  pixelToMmF,
  wheelZoomFactor,
  zoomAtPixel,
  DEFAULT_ORBIT_3D,
  DEFAULT_VIEW_2D,
  type Orbit3D,
  type View2D,
} from './cameraMath';
import type { PixelPoint, PtF, ViewportSizePx } from './coords';

export type ViewportListener = () => void;

/** Default fit animation. Short enough to feel instant, long enough to follow. */
const FIT_TWEEN_MS = 180;

function prefersReducedMotion(): boolean {
  if (typeof matchMedia !== 'function') return false;
  return matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Ease-out cubic. */
function ease(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

export class ViewportController {
  mode: CanvasMode = '2d';

  view2d: View2D = DEFAULT_VIEW_2D;

  orbit: Orbit3D = DEFAULT_ORBIT_3D;

  sizePx: ViewportSizePx = { width: 1, height: 1 };

  /**
   * Elevation of the plane picks fall back to and the grid is drawn on — the
   * active storey's FFL. Set by the Plan page when the storey tab changes.
   */
  planeElevationMm = 0;

  /** Vertical extent used when fitting the 3D camera (building height). */
  fitHeightMm = 3000;

  private readonly listeners = new Set<ViewportListener>();

  private invalidateFn: (() => void) | null = null;

  private frame = 0;

  private tween: number | null = null;

  // ── plumbing ────────────────────────────────────────────────────────────

  /** `CanvasRoot` hands us R3F's `invalidate` once the renderer exists. */
  attachInvalidate(fn: (() => void) | null): void {
    this.invalidateFn = fn;
  }

  /**
   * Fires after every committed change, synchronously. `CameraRig` subscribes
   * this way; React components should use {@link subscribeAnimationFrame}.
   */
  subscribe(listener: ViewportListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /**
   * Coalesced to one call per animation frame. A drag can deliver several
   * `pointermove` events between two frames; a React subscriber that reacted to
   * each would render work nobody can see.
   */
  subscribeAnimationFrame(listener: ViewportListener): () => void {
    let queued = false;
    const wrapped = (): void => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => {
        queued = false;
        listener();
      });
    };
    return this.subscribe(wrapped);
  }

  /**
   * Monotonic counter, bumped on every commit. `useSyncExternalStore` snapshots
   * derive from it, so a selector that returns a primitive stays stable.
   */
  getFrame(): number {
    return this.frame;
  }

  /** Publish the current state and ask for a render. Every mutator ends here. */
  commit(): void {
    this.frame += 1;
    for (const listener of this.listeners) listener();
    this.invalidateFn?.();
  }

  // ── mode / size ─────────────────────────────────────────────────────────

  setMode(mode: CanvasMode): void {
    if (this.mode === mode) return;
    this.mode = mode;
    this.commit();
  }

  setSize(width: number, height: number): void {
    const w = Math.max(1, Math.floor(width));
    const h = Math.max(1, Math.floor(height));
    if (this.sizePx.width === w && this.sizePx.height === h) return;
    this.sizePx = { width: w, height: h };
    this.commit();
  }

  setPlaneElevationMm(elevationMm: number): void {
    if (this.planeElevationMm === elevationMm) return;
    this.planeElevationMm = elevationMm;
    this.commit();
  }

  setFitHeightMm(heightMm: number): void {
    this.fitHeightMm = Math.max(1, heightMm);
  }

  // ── reads ───────────────────────────────────────────────────────────────

  /** Aspect ratio for the perspective camera. */
  get aspect(): number {
    return this.sizePx.width / Math.max(1, this.sizePx.height);
  }

  /**
   * Millimetres per CSS pixel in whichever mode is live. In 3D this is the
   * value at the orbit target, so pick tolerance and grid fading behave the
   * same in both views instead of each growing its own rule.
   */
  get mmPerPx(): number {
    if (this.mode === '2d') return this.view2d.mmPerPx;
    return mmPerPxAtDistance(this.orbit.distanceMm, this.sizePx.height, PERSP_FOV_DEG);
  }

  /** Canvas pixel → model point (float mm). 2D only; returns null in 3D. */
  pixelToMmF(px: PixelPoint): PtF | null {
    if (this.mode !== '2d') return null;
    return pixelToMmF(this.view2d, px, this.sizePx);
  }

  // ── 2D navigation ───────────────────────────────────────────────────────

  setView2d(view: View2D): void {
    this.view2d = { centreMm: view.centreMm, mmPerPx: clampMmPerPx(view.mmPerPx) };
    this.commit();
  }

  panPx(dxPx: number, dyPx: number): void {
    this.cancelTween();
    this.view2d = panByPx(this.view2d, dxPx, dyPx);
    this.commit();
  }

  zoomAtPixel(cursorPx: PixelPoint, factor: number): void {
    this.cancelTween();
    this.view2d = zoomAtPixel(this.view2d, cursorPx, this.sizePx, factor);
    this.commit();
  }

  /**
   * The wheel handler's whole behaviour: zoom towards the cursor in 2D, dolly
   * in 3D. One entry point so the two modes cannot drift in feel.
   */
  wheel(deltaY: number, deltaMode: number, cursorPx: PixelPoint): void {
    const factor = wheelZoomFactor(deltaY, deltaMode);
    if (this.mode === '2d') {
      this.zoomAtPixel(cursorPx, factor);
    } else {
      this.cancelTween();
      this.orbit = { ...this.orbit, distanceMm: Math.max(500, this.orbit.distanceMm * factor) };
      this.commit();
    }
  }

  // ── 3D navigation ───────────────────────────────────────────────────────

  setOrbit(orbit: Orbit3D): void {
    this.orbit = orbit;
    this.commit();
  }

  // ── fitting ─────────────────────────────────────────────────────────────

  /**
   * Zoom-to-fit-plot and zoom-to-selection are the same call with a different
   * box. `animate: false` for anything driven by a continuous input.
   */
  fitBbox(box: Bbox, options: { paddingPx?: number; animate?: boolean } = {}): void {
    if (this.mode === '3d') {
      this.cancelTween();
      this.orbit = fitOrbitToBbox(this.orbit, box, this.fitHeightMm, this.aspect);
      this.commit();
      return;
    }
    const target =
      options.paddingPx === undefined
        ? fitBboxToViewport(box, this.sizePx)
        : fitBboxToViewport(box, this.sizePx, options.paddingPx);
    const animate = options.animate ?? true;
    if (!animate || prefersReducedMotion()) {
      this.setView2d(target);
      return;
    }
    this.animateTo(target);
  }

  /**
   * Tween the 2D view. Zoom is interpolated in **log** space: linear
   * interpolation of `mmPerPx` from 0.5 to 50 spends most of the animation
   * already zoomed out, which reads as a snap followed by a crawl.
   */
  animateTo(target: View2D, durationMs = FIT_TWEEN_MS): void {
    this.cancelTween();
    const from = this.view2d;
    const to = { centreMm: target.centreMm, mmPerPx: clampMmPerPx(target.mmPerPx) };
    const logFrom = Math.log(from.mmPerPx);
    const logTo = Math.log(to.mmPerPx);
    const start = performance.now();

    const step = (now: number): void => {
      const t = durationMs <= 0 ? 1 : Math.min(1, (now - start) / durationMs);
      const k = ease(t);
      this.view2d = {
        centreMm: {
          x: from.centreMm.x + (to.centreMm.x - from.centreMm.x) * k,
          y: from.centreMm.y + (to.centreMm.y - from.centreMm.y) * k,
        },
        mmPerPx: Math.exp(logFrom + (logTo - logFrom) * k),
      };
      this.commit();
      if (t < 1) {
        this.tween = requestAnimationFrame(step);
      } else {
        this.tween = null;
      }
    };
    this.tween = requestAnimationFrame(step);
  }

  /** Any direct input cancels a running fit — the user is in charge. */
  cancelTween(): void {
    if (this.tween !== null) {
      cancelAnimationFrame(this.tween);
      this.tween = null;
    }
  }

  /** Drop listeners and stop any animation. Called on `CanvasRoot` unmount. */
  dispose(): void {
    this.cancelTween();
    this.listeners.clear();
    this.invalidateFn = null;
  }

  /** Back to the opening view. Used by "reset view" and by the specs. */
  reset(): void {
    this.cancelTween();
    this.view2d = { centreMm: { x: 0, y: 0 }, mmPerPx: DEFAULT_MM_PER_PX };
    this.orbit = DEFAULT_ORBIT_3D;
    this.commit();
  }
}
