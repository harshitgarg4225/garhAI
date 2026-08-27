/**
 * useOverlayPointerGuard — stop the DOM overlay's pointer events reaching the
 * canvas tools underneath it.
 *
 * ## The bug this exists to kill
 *
 * `CanvasRoot` attaches its pointer handling as NATIVE listeners on the canvas
 * container. Every overlay panel — the tool options bar, the furniture browser,
 * the sun panel, the underlay controls — renders inside that container, so a
 * press on one of their buttons bubbles straight into the canvas. With the wall
 * tool armed, clicking "Furniture" also dropped a wall point behind the panel.
 *
 * React's own `onPointerDown` + `stopPropagation` cannot fix it: React delegates
 * to the app root, which is an ANCESTOR of the container, so the synthetic
 * handler runs strictly after the canvas has already seen the press. The guard
 * therefore has to be a native listener attached to the overlay wrapper itself,
 * which sits between the panels and the container.
 *
 * ## Why the wrapper is the right single place
 *
 * The wrapper is `pointer-events: none`, so it is never itself a hit target and
 * a click on the drawing does not pass through it at all. The only events it
 * ever sees are ones that bubbled up from a child that opted into
 * `pointer-events: auto` — i.e. exactly the interactive chrome, and nothing
 * else. One listener fixes every present and future panel, instead of asking
 * each one to remember.
 *
 * ## What is deliberately NOT stopped
 *
 * * `click` — React's delegation carries it, so every `onClick` in every panel
 *   keeps working. The canvas synthesises its own click from pointerdown/up
 *   rather than reading this one, so letting it through costs nothing.
 * * `wheel` — the wheel should still zoom the drawing under the cursor even
 *   when the cursor happens to be over a panel.
 *
 * Nothing calls `preventDefault`, so focus, text selection, caret placement and
 * slider drags inside the panels all behave exactly as before.
 */

import { useEffect } from 'react';

/** The three events `CanvasRoot`'s container listens for. */
const GUARDED_EVENTS = ['pointerdown', 'pointermove', 'pointerup'] as const;

export function useOverlayPointerGuard(element: HTMLElement | null): void {
  useEffect(() => {
    if (element === null) return undefined;
    const stop = (event: Event): void => event.stopPropagation();
    for (const name of GUARDED_EVENTS) element.addEventListener(name, stop);
    return () => {
      for (const name of GUARDED_EVENTS) element.removeEventListener(name, stop);
    };
  }, [element]);
}

export { GUARDED_EVENTS as OVERLAY_GUARDED_EVENTS };
