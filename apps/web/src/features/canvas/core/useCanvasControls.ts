/**
 * useCanvasControls.ts — pointer input, once, for everybody.
 *
 * Every tool in Phase 4 needs the same four things from a pointer event: where
 * it is in snapped millimetres, where it is unsnapped, what is under it, and
 * whether the user is navigating rather than drawing. Computing that in each
 * tool would mean each tool caching a `DOMRect`, doing its own NDC flip, and
 * running its own raycast — three chances per tool to get the coordinate
 * boundary subtly wrong.
 *
 * So it happens here, and tools receive a {@link CanvasPointerEvent}.
 *
 * THREE PERFORMANCE DECISIONS, all §14:
 *
 *  1. **Moves are coalesced to one per animation frame.** A high-polling mouse
 *     fires 500 `pointermove`s a second; nobody can draw finer than a frame,
 *     and 8 raycasts per frame is 8× the cost of the one that gets seen.
 *
 *  2. **The hit test is lazy.** `event.hit()` raycasts on first call and caches.
 *     A tool that only wants `pointMm` (the wall tool, mid-drag) pays nothing.
 *
 *  3. **The bounding rect is cached**, refreshed on resize and scroll.
 *     `getBoundingClientRect()` inside a move handler forces layout, and layout
 *     inside the input path is how a 16 ms budget becomes a 30 ms one.
 *
 * NAVIGATION GESTURES (CAD conventions, so muscle memory transfers):
 *   wheel / pinch      zoom to cursor (2D) · dolly (3D)
 *   middle-drag        pan (2D) · orbit (3D)
 *   space + left-drag  pan, for trackpads with no middle button
 *   shift + middle     pan in 3D instead of orbit
 *
 * Right-drag is deliberately NOT a pan. `contextmenu` fires on mouse-*down* in
 * Chrome, before any movement exists to distinguish a drag from a click, so a
 * right-drag pan can only be built by suppressing the context menu always and
 * guessing afterwards. The canvas needs a right-click (it is where the element
 * context menu will live), so the right button stays a click.
 */

import { useEffect, useRef } from 'react';

import type { Pt } from '@garh/model';

import type { CanvasCore, CorePickOptions } from './context';
import { ndcFromPixel, type Ndc, type PixelPoint } from './coords';
import { sameHitTarget, type PickHit } from './hitTest';

// ---------------------------------------------------------------------------
// The event tools see
// ---------------------------------------------------------------------------

export interface CanvasPointerEvent {
  /** `PointerEvent` for pointer callbacks; `MouseEvent` for dblclick/contextmenu. */
  readonly nativeEvent: PointerEvent | MouseEvent;
  /** Canvas-relative CSS pixels. */
  readonly pixel: PixelPoint;
  readonly ndc: Ndc;
  readonly button: number;
  readonly buttons: number;
  readonly shiftKey: boolean;
  readonly altKey: boolean;
  readonly ctrlKey: boolean;
  readonly metaKey: boolean;
  /**
   * Model point on the reference plane, snapped to the active module — the
   * value an op payload wants. `null` only when the ray misses the plane
   * (3D, pointer above the horizon).
   */
  readonly pointMm: Pt | null;
  /** The same point, unsnapped. Measurement readouts and hover maths. */
  readonly rawPointMm: Pt | null;
  /** Raycast on demand, memoised for this event. */
  hit: () => PickHit;
}

export interface CanvasControlsCallbacks {
  onPointerDown?: ((event: CanvasPointerEvent) => void) | undefined;
  onPointerMove?: ((event: CanvasPointerEvent) => void) | undefined;
  onPointerUp?: ((event: CanvasPointerEvent) => void) | undefined;
  /** Fired on pointer-up that did not move or linger — a real click. */
  onClick?: ((event: CanvasPointerEvent) => void) | undefined;
  onDoubleClick?: ((event: CanvasPointerEvent) => void) | undefined;
  onContextMenu?: ((event: CanvasPointerEvent) => void) | undefined;
  /** Fires only when the hovered element changes, never per move. */
  onHoverChange?: ((hit: PickHit | null) => void) | undefined;
  onPointerLeave?: (() => void) | undefined;
  /** Navigation started/stopped, so a tool can suspend its preview. */
  onNavigatingChange?: ((navigating: boolean) => void) | undefined;
}

export interface CanvasControlsOptions extends CanvasControlsCallbacks {
  core: CanvasCore;
  /** Turn the whole thing off (a modal has the keyboard and the pointer). */
  enabled?: boolean | undefined;
  /** Built-in pan/zoom. Default true. */
  navigation?: boolean | undefined;
  /** Emit hover picks. Default true. */
  hover?: boolean | undefined;
  /** Extra pick constraints for hover — usually a `kinds` filter per tool. */
  hoverPickOptions?: CorePickOptions | undefined;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** A click is a press and release in the same place, quickly. */
const CLICK_SLOP_PX = 4;
const CLICK_MAX_MS = 600;

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return (
    tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable === true
  );
}

// ---------------------------------------------------------------------------
// The hook
// ---------------------------------------------------------------------------

/**
 * Attach input handling to the canvas container. `element` is the wrapper
 * `<div>`, not the `<canvas>`: the wrapper is what has a stable size and what
 * still receives the pointer during a capture.
 */
export function useCanvasControls(
  element: HTMLElement | null,
  options: CanvasControlsOptions,
): void {
  // Callbacks change every render (inline arrow functions in the tool layer).
  // Reading them through a ref keeps the listener set stable — re-attaching
  // `wheel` with `passive: false` on every render is both wasteful and a source
  // of dropped events mid-gesture.
  const latest = useRef(options);
  latest.current = options;

  useEffect(() => {
    if (element === null) return;
    if (options.enabled === false) return;

    const core = latest.current.core;
    const viewport = core.viewport;

    // ── cached geometry ──────────────────────────────────────────────────
    let rect = element.getBoundingClientRect();
    const refreshRect = (): void => {
      rect = element.getBoundingClientRect();
    };
    const resizeObserver =
      typeof ResizeObserver === 'function' ? new ResizeObserver(refreshRect) : null;
    resizeObserver?.observe(element);
    window.addEventListener('scroll', refreshRect, { passive: true, capture: true });
    window.addEventListener('resize', refreshRect, { passive: true });

    const pixelOf = (event: { clientX: number; clientY: number }): PixelPoint => ({
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
    });

    const build = (event: PointerEvent | MouseEvent): CanvasPointerEvent => {
      const pixel = pixelOf(event);
      const ndc = ndcFromPixel(pixel, { width: rect.width, height: rect.height });
      let cached: PickHit | null = null;
      return {
        nativeEvent: event,
        pixel,
        ndc,
        button: event.button,
        buttons: event.buttons,
        shiftKey: event.shiftKey,
        altKey: event.altKey,
        ctrlKey: event.ctrlKey,
        metaKey: event.metaKey,
        pointMm: core.pointMm(ndc),
        rawPointMm: core.rawPointMm(ndc),
        hit: () => {
          if (cached === null) cached = core.pick(ndc);
          return cached;
        },
      };
    };

    // ── navigation state ─────────────────────────────────────────────────
    let spaceHeld = false;
    let navigating = false;
    let navPointerId: number | null = null;
    let lastNavX = 0;
    let lastNavY = 0;

    const setNavigating = (value: boolean): void => {
      if (navigating === value) return;
      navigating = value;
      element.style.cursor = value ? 'grabbing' : '';
      latest.current.onNavigatingChange?.(value);
    };

    // ── move coalescing ──────────────────────────────────────────────────
    let pendingMove: PointerEvent | null = null;
    let moveFrame = 0;
    let lastHover: PickHit | null = null;

    const flushMove = (): void => {
      moveFrame = 0;
      const event = pendingMove;
      pendingMove = null;
      if (event === null) return;

      const canvasEvent = build(event);
      latest.current.onPointerMove?.(canvasEvent);

      if (latest.current.hover !== false && !navigating) {
        const hit = core.pick(canvasEvent.ndc, latest.current.hoverPickOptions ?? {});
        if (!sameHitTarget(hit, lastHover)) {
          lastHover = hit;
          // Only on change: this is what keeps a Zustand write off the
          // per-move path (§14) and stops the inspector re-rendering
          // 500 times a second.
          latest.current.onHoverChange?.(hit);
        }
      }
    };

    const queueMove = (event: PointerEvent): void => {
      pendingMove = event;
      if (moveFrame === 0) moveFrame = requestAnimationFrame(flushMove);
    };

    // ── press bookkeeping (click vs drag) ─────────────────────────────────
    let downPixel: PixelPoint | null = null;
    let downAt = 0;

    // ── handlers ─────────────────────────────────────────────────────────

    const isNavigationStart = (event: PointerEvent): boolean => {
      if (latest.current.navigation === false) return false;
      // Middle button, or space held with the left button. Not the right
      // button — see the note at the top of the file.
      if (event.button === 1) return true;
      return event.button === 0 && spaceHeld;
    };

    const onPointerDown = (event: PointerEvent): void => {
      refreshRect();
      if (isNavigationStart(event)) {
        navPointerId = event.pointerId;
        lastNavX = event.clientX;
        lastNavY = event.clientY;
        setNavigating(true);
        element.setPointerCapture(event.pointerId);
        event.preventDefault();
        return;
      }
      downPixel = pixelOf(event);
      downAt = event.timeStamp;
      latest.current.onPointerDown?.(build(event));
    };

    const onPointerMove = (event: PointerEvent): void => {
      if (navPointerId === event.pointerId && navigating) {
        const dx = event.clientX - lastNavX;
        const dy = event.clientY - lastNavY;
        lastNavX = event.clientX;
        lastNavY = event.clientY;
        if (viewport.mode === '2d' || event.shiftKey) {
          panViewport(core, dx, dy, event.shiftKey);
        } else {
          orbitViewport(core, dx, dy);
        }
        return;
      }
      queueMove(event);
    };

    const onPointerUp = (event: PointerEvent): void => {
      if (navPointerId === event.pointerId) {
        navPointerId = null;
        setNavigating(false);
        if (element.hasPointerCapture(event.pointerId)) {
          element.releasePointerCapture(event.pointerId);
        }
        return;
      }

      const canvasEvent = build(event);
      latest.current.onPointerUp?.(canvasEvent);

      if (downPixel !== null) {
        const moved = Math.hypot(
          canvasEvent.pixel.x - downPixel.x,
          canvasEvent.pixel.y - downPixel.y,
        );
        if (moved <= CLICK_SLOP_PX && event.timeStamp - downAt <= CLICK_MAX_MS) {
          latest.current.onClick?.(canvasEvent);
        }
        downPixel = null;
      }
    };

    const onPointerLeave = (): void => {
      if (lastHover !== null) {
        lastHover = null;
        latest.current.onHoverChange?.(null);
      }
      latest.current.onPointerLeave?.();
    };

    const onDoubleClick = (event: MouseEvent): void => {
      latest.current.onDoubleClick?.(build(event));
    };

    const onContextMenu = (event: MouseEvent): void => {
      // The browser's menu is never the right answer over a drawing; the
      // element context menu is ours to render.
      event.preventDefault();
      latest.current.onContextMenu?.(build(event));
    };

    const onWheel = (event: WheelEvent): void => {
      if (latest.current.navigation === false) return;
      // Without this the page scrolls behind the canvas, which on a laptop
      // trackpad means the drawing zooms *and* the whole app slides.
      event.preventDefault();
      refreshRect();
      viewport.wheel(event.deltaY, event.deltaMode, pixelOf(event));
    };

    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.code !== 'Space' || event.repeat) return;
      if (isEditableTarget(event.target)) return;
      spaceHeld = true;
      element.style.cursor = 'grab';
    };

    const onKeyUp = (event: KeyboardEvent): void => {
      if (event.code !== 'Space') return;
      spaceHeld = false;
      if (!navigating) element.style.cursor = '';
    };

    element.addEventListener('pointerdown', onPointerDown);
    element.addEventListener('pointermove', onPointerMove);
    element.addEventListener('pointerup', onPointerUp);
    element.addEventListener('pointercancel', onPointerUp);
    element.addEventListener('pointerleave', onPointerLeave);
    element.addEventListener('dblclick', onDoubleClick);
    element.addEventListener('contextmenu', onContextMenu);
    element.addEventListener('wheel', onWheel, { passive: false });
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);

    return () => {
      if (moveFrame !== 0) cancelAnimationFrame(moveFrame);
      resizeObserver?.disconnect();
      window.removeEventListener('scroll', refreshRect, true);
      window.removeEventListener('resize', refreshRect);
      element.removeEventListener('pointerdown', onPointerDown);
      element.removeEventListener('pointermove', onPointerMove);
      element.removeEventListener('pointerup', onPointerUp);
      element.removeEventListener('pointercancel', onPointerUp);
      element.removeEventListener('pointerleave', onPointerLeave);
      element.removeEventListener('dblclick', onDoubleClick);
      element.removeEventListener('contextmenu', onContextMenu);
      element.removeEventListener('wheel', onWheel);
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
      element.style.cursor = '';
    };
    // `options` is read through `latest`; only the element and the on/off
    // switch may re-attach listeners.
  }, [element, options.enabled]);
}

// ---------------------------------------------------------------------------
// Navigation verbs
// ---------------------------------------------------------------------------

function panViewport(core: CanvasCore, dxPx: number, dyPx: number, lateral3d: boolean): void {
  const viewport = core.viewport;
  if (viewport.mode === '2d') {
    viewport.panPx(dxPx, dyPx);
    return;
  }
  if (!lateral3d) return;
  // Slide the orbit target in the ground plane, along the camera's own axes.
  // Right of the camera in model space is (−sin a, cos a); "up the screen"
  // projects onto the ground as (cos a, sin a).
  const mmPerPx = viewport.mmPerPx;
  const a = (viewport.orbit.azimuthDeg * Math.PI) / 180;
  const rightX = -Math.sin(a);
  const rightY = Math.cos(a);
  const forwardX = Math.cos(a);
  const forwardY = Math.sin(a);
  const target = viewport.orbit.targetMm;
  viewport.setOrbit({
    ...viewport.orbit,
    targetMm: {
      x: target.x - (dxPx * rightX + dyPx * forwardX) * mmPerPx,
      y: target.y - (dxPx * rightY + dyPx * forwardY) * mmPerPx,
      z: target.z,
    },
  });
}

function orbitViewport(core: CanvasCore, dxPx: number, dyPx: number): void {
  const viewport = core.viewport;
  viewport.setOrbit({
    ...viewport.orbit,
    azimuthDeg: viewport.orbit.azimuthDeg - dxPx * 0.4,
    polarDeg: Math.min(89, Math.max(1, viewport.orbit.polarDeg - dyPx * 0.4)),
  });
}
