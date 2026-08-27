/**
 * useCursorBroadcast — publish MY pointer to everyone else on the project.
 *
 * `POST /projects/:id/collab/cursor` with `{x, y, storeyIndex}` in plot-local
 * integer millimetres; the server stamps identity and fans it out on the collab
 * stream. Fire-and-forget: nothing is stored, nothing comes back, and a failure
 * is never surfaced (see `api.collab.cursor`).
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE FOUR GATES, AND WHY EACH ONE IS HERE
 * ────────────────────────────────────────────────────────────────────────────
 *  1. **Rate.** A high-polling mouse fires ~500 `pointermove`s a second.
 *     {@link createTrailingThrottle} cuts that to ~10/s with a trailing send so
 *     the final resting position still lands.
 *  2. **Off the canvas.** `pointerleave` cancels the pending trailing send.
 *     Without it, moving the mouse away to the inspector would publish one last
 *     frame up to 100ms later, parking a colleague's cursor on the edge of the
 *     drawing right after you stopped pointing at anything.
 *  3. **Hidden tab.** `document.visibilityState` is checked on every move and
 *     on `visibilitychange`. A backgrounded tab still receives pointer events
 *     in some window arrangements, and publishing from one means your cursor
 *     sits on a colleague's plan while you are reading email.
 *  4. **No movement, no traffic.** A repeated position is dropped before it
 *     reaches the throttle. At the zoom levels an architect works at, a pointer
 *     drifting one screen pixel can land on the same millimetre, and a stream
 *     of identical frames is pure cost.
 *
 * WHAT IT DOES NOT DO: convert coordinates. `toMm` is supplied by the caller
 * and goes through the canvas core's own screen→plan projection. Inventing a
 * second conversion here is the one thing `core/coords.ts` asks every module
 * not to do, and a cursor that lands on a different millimetre than the tools
 * would is worse than no cursor.
 */

import { useEffect, useRef } from 'react';

import type { Pt } from '@garh/model';

import { api } from '../../../lib/api';
import { createTrailingThrottle, type TrailingThrottle } from './cursorThrottle';

/** ~10 posts a second while moving — the rate the endpoint is designed for. */
export const CURSOR_POST_INTERVAL_MS = 100;

interface CursorPayload {
  readonly x: number;
  readonly y: number;
  readonly storeyIndex: number | null;
}

export interface CursorBroadcastOptions {
  readonly projectId: string;
  /** The element whose pointer moves are published — the `<canvas>` itself. */
  readonly element: HTMLElement | null;
  /** Which storey the pointer is over, or null when not storey-bound. */
  readonly storeyIndex: number | null;
  /** Client pixels → plot-local integer mm, via the canvas core. */
  readonly toMm: (clientX: number, clientY: number) => Pt | null;
  /** Off for a solo project, a share-link viewer, or while a modal owns input. */
  readonly enabled: boolean;
}

export function useCursorBroadcast(options: CursorBroadcastOptions): void {
  // Everything that changes per render is read through this ref so the listener
  // set is attached once per element and never re-attached mid-gesture — the
  // same discipline `useCanvasControls` follows, and for the same reason:
  // re-binding `pointermove` while the pointer is moving drops events.
  const latest = useRef(options);
  latest.current = options;

  const throttleRef = useRef<TrailingThrottle<CursorPayload> | null>(null);

  useEffect(() => {
    const throttle = createTrailingThrottle<CursorPayload>({
      intervalMs: CURSOR_POST_INTERVAL_MS,
      send: (payload) => {
        const { projectId } = latest.current;
        if (projectId === '') return;
        // `void` + `catch`: a dropped cursor is a dropped cursor. An unhandled
        // rejection here would reach the error boundary and take down the plan
        // someone is drawing, over a mouse position.
        void api.collab.cursor(projectId, payload).catch(() => undefined);
      },
    });
    throttleRef.current = throttle;
    return () => {
      throttleRef.current = null;
      throttle.cancel();
    };
  }, []);

  useEffect(() => {
    const element = options.element;
    if (element === null) return undefined;

    /** Last position actually offered, so an unmoved pointer costs nothing. */
    let lastSent: CursorPayload | null = null;

    const stop = (): void => {
      throttleRef.current?.cancel();
      lastSent = null;
    };

    const onPointerMove = (event: PointerEvent): void => {
      const current = latest.current;
      if (!current.enabled) return;
      // Gate 3. Checked per move rather than only on `visibilitychange`,
      // because a tab can be hidden by a compositor without firing one.
      if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;

      const ptMm = current.toMm(event.clientX, event.clientY);
      // Null means the ray missed the reference plane — in 3D, a pointer above
      // the horizon. There is no plan position to publish, so publish nothing
      // rather than a fabricated one.
      if (ptMm === null) return;

      const payload: CursorPayload = {
        x: ptMm.x,
        y: ptMm.y,
        storeyIndex: current.storeyIndex,
      };
      // Gate 4.
      if (
        lastSent !== null &&
        lastSent.x === payload.x &&
        lastSent.y === payload.y &&
        lastSent.storeyIndex === payload.storeyIndex
      ) {
        return;
      }
      lastSent = payload;
      throttleRef.current?.push(payload);
    };

    const onVisibilityChange = (): void => {
      if (document.visibilityState !== 'visible') stop();
    };

    element.addEventListener('pointermove', onPointerMove, { passive: true });
    // Gate 2. `pointercancel` too: a touch lifted mid-drag never fires `leave`.
    element.addEventListener('pointerleave', stop);
    element.addEventListener('pointercancel', stop);
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      element.removeEventListener('pointermove', onPointerMove);
      element.removeEventListener('pointerleave', stop);
      element.removeEventListener('pointercancel', stop);
      document.removeEventListener('visibilitychange', onVisibilityChange);
      stop();
    };
  }, [options.element]);
}
