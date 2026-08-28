/**
 * restore.ts — putting the camera back, with a flight rather than a jump.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY NOT `ViewportController.animateTo`
 * ════════════════════════════════════════════════════════════════════════════
 * The controller already tweens the 2D view, and reusing it was the first
 * thing tried. Two things rule it out, and both are properties of a NAMED view
 * rather than of a one-shot fit:
 *
 *  1. It lands an ulp short of its target (log-space zoom; see `tween.ts`).
 *     For "zoom to fit" that is nothing. For a view you return to twenty times
 *     a day, re-saved from where you landed, it is a slow drift.
 *  2. It only exists for 2D. The 3D camera has no tween at all, and "the street
 *     elevation" is exactly the kind of view an architect saves.
 *
 * So the flight lives here — but the controller's rules are respected: the
 * flight cancels the controller's own tween before it starts, and it gives up
 * the moment the user touches the camera, which is `cancelTween`'s stated rule
 * ("any direct input cancels a running fit — the user is in charge") enforced
 * from the outside.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * HOW A USER INTERRUPTS A FLIGHT, WITH NO CHANGES TO THE CONTROLLER
 * ════════════════════════════════════════════════════════════════════════════
 * `panPx`, `wheel`, `zoomAtPixel` and `fitBbox` all call the controller's OWN
 * `cancelTween`, which knows nothing about this module. Rather than reach into
 * the controller, each frame here re-reads the live camera and compares it with
 * what this flight wrote last frame. If they differ, something else moved the
 * camera — a pan, a wheel, a Tab into 3D, a second restore — and the flight
 * stops where it is instead of dragging the user back onto its own path.
 *
 * That check is cheap (six numbers), needs no coordination, and cannot go stale
 * the way a subscription flag could.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE DECISION: RESTORING A 2D VIEW WHILE THE USER IS IN 3D
 * ════════════════════════════════════════════════════════════════════════════
 * A saved view carries its projection. Restoring across projections:
 *
 *   1. writes the camera into the matching half of the controller — instantly
 *      and exactly, no tween;
 *   2. asks the app to switch to that projection (`requestMode`, wired to
 *      `ui.setViewMode` by `useViews`);
 *   3. reports both facts back in the {@link RestoreOutcome}.
 *
 * Order matters: because the camera is written BEFORE the switch is requested,
 * the target camera is already framed when `CameraRig` swaps which one R3F
 * renders through. The switch itself is the transition, and it lands directly
 * on the view — no flash of the old plan position, no second animation.
 *
 * The alternatives, and why each was rejected:
 *
 *   · **Throw, or do nothing.** A control that silently does nothing is the bug
 *     class this repo has shipped before; and a saved view the architect can
 *     see in a list but cannot click is worse than not listing it.
 *   · **Project one camera into the other and stay in the current mode.** A
 *     view named "street elevation" means the perspective view. Landing on a
 *     plan camera "roughly there" answers a question nobody asked, and the
 *     product would be lying about what it restored.
 *   · **Animate across the switch.** There is no continuous path between an
 *     orthographic frustum and a perspective one, and R3F swaps cameras in a
 *     single commit. Animating the off-screen camera animates something nobody
 *     is looking at, and the swap is still a cut.
 *
 * `requestMode` is optional — a caller that has no way to change the mode (a
 * spec, a preview) still gets the camera written and is TOLD the switch did not
 * happen, rather than being left to guess.
 */

// Deep import: see the note on the same import in `camera.ts`.
import type { ViewportController } from '../canvas/core/viewport';
import { applyCamera, captureCamera, sameCamera } from './camera';
import { easeOutCubic, interpolateCamera } from './tween';
import type { CanvasMode, RestoreOutcome, SavedCamera } from './types';

/**
 * Flight time. Longer than the controller's 180 ms fit because a named view
 * usually travels further — across the plan, or round the building — and the
 * point of animating at all is that the eye can follow where it went.
 */
export const RESTORE_DURATION_MS = 260;

/** The three clock calls a flight makes. Injected so specs can drive it. */
export interface TransitionClock {
  now: () => number;
  request: (callback: () => void) => number;
  cancel: (handle: number) => void;
}

export interface RestoreOptions {
  /** Flight time in ms. `0` lands immediately. */
  readonly durationMs?: number | undefined;
  /** Caller override. Reduced motion still wins over `true`. */
  readonly animate?: boolean | undefined;
  /** Ask the app to switch projection. See the decision note above. */
  readonly requestMode?: ((mode: CanvasMode) => void) | undefined;
  /** Defaults to the browser's. Injected by the specs. */
  readonly clock?: TransitionClock | undefined;
  /** Defaults to {@link prefersReducedMotion}. Injected by the specs. */
  readonly reducedMotion?: boolean | undefined;
}

/**
 * The OS "reduce motion" setting.
 *
 * A duplicate of the controller's own private helper, deliberately: that one is
 * module-scoped in `viewport.ts` and not ours to export, and a feature that
 * silently stopped honouring the setting because an import moved would be a
 * §15 accessibility regression with no test to catch it. Nine lines is the
 * cheaper half of that trade.
 *
 * Read at restore time, not at mount: a user who turns the setting on mid
 * session gets it on the next restore rather than after a reload.
 */
export function prefersReducedMotion(): boolean {
  if (typeof matchMedia !== 'function') return false;
  try {
    return matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    // jsdom and some embedded webviews implement `matchMedia` as a thrower.
    // Motion is the safe default here — the animation is a nicety, and
    // refusing to restore the view at all would not be.
    return false;
  }
}

function browserClock(): TransitionClock {
  return {
    now: () => (typeof performance === 'object' ? performance.now() : Date.now()),
    request: (callback) => requestAnimationFrame(() => callback()),
    cancel: (handle) => {
      cancelAnimationFrame(handle);
    },
  };
}

/**
 * Put `target` on screen. Returns what it did — see {@link RestoreOutcome}.
 *
 * Every exit path ends with the camera EXACTLY equal to `target` for that
 * projection: the instant paths assign it, and the animated path's last frame
 * is `target` itself by identity (`interpolateCamera` at `k >= 1`).
 */
export function restoreCamera(
  viewport: ViewportController,
  target: SavedCamera,
  options: RestoreOptions = {},
): RestoreOutcome {
  // The controller's own fit tween would keep writing the camera underneath
  // this one; two writers means the last frame wins at random.
  viewport.cancelTween();

  // Cross-projection: write the other half of the controller and ask for the
  // switch. See the decision note in the header.
  if (target.mode !== viewport.mode) {
    applyCamera(viewport, target);
    options.requestMode?.(target.mode);
    return {
      camera: target,
      modeRequested: target.mode,
      animated: false,
      cancel: () => undefined,
    };
  }

  const durationMs = options.durationMs ?? RESTORE_DURATION_MS;
  const reduced = options.reducedMotion ?? prefersReducedMotion();
  const from = captureCamera(viewport);
  const wanted = options.animate ?? true;
  const animate = wanted && !reduced && durationMs > 0 && !sameCamera(from, target);

  if (!animate) {
    applyCamera(viewport, target);
    return { camera: target, modeRequested: null, animated: false, cancel: () => undefined };
  }

  const clock = options.clock ?? browserClock();
  const start = clock.now();
  let handle: number | null = null;
  let done = false;
  // What this flight wrote last — the baseline the interruption check compares
  // against. Seeded with the starting camera so a pan BEFORE the first frame
  // is caught too.
  let lastWritten: SavedCamera = from;

  const stop = (): void => {
    done = true;
    if (handle !== null) {
      clock.cancel(handle);
      handle = null;
    }
  };

  const step = (): void => {
    handle = null;
    if (done) return;

    // Somebody else moved the camera (pan, wheel, Tab, another restore). The
    // user is in charge; leave them where they are.
    if (!sameCamera(captureCamera(viewport), lastWritten)) {
      done = true;
      return;
    }

    const t = Math.min(1, (clock.now() - start) / durationMs);
    const next = interpolateCamera(from, target, easeOutCubic(t));
    applyCamera(viewport, next);
    lastWritten = captureCamera(viewport);

    if (t >= 1) {
      done = true;
      return;
    }
    handle = clock.request(step);
  };

  handle = clock.request(step);

  return { camera: target, modeRequested: null, animated: true, cancel: stop };
}
