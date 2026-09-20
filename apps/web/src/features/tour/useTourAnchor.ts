/**
 * useTourAnchor — where the thing a tour step points at is on screen right now.
 *
 * Measures the first element matching `selector`, scrolls it into view once, and
 * re-measures on resize and scroll so the highlight follows the page. `null` when
 * nothing matches (the tab has not rendered it, or the selector is wrong): the
 * tour then shows the step centred rather than pointing at empty space — a step
 * that cannot find its anchor is still a step, not a crash.
 */

import { useEffect, useState } from 'react';

export interface AnchorRect {
  readonly top: number;
  readonly left: number;
  readonly width: number;
  readonly height: number;
}

function measure(selector: string): AnchorRect | null {
  if (typeof document === 'undefined') return null;
  const element = document.querySelector(selector);
  if (!(element instanceof HTMLElement)) return null;
  const rect = element.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) return null;
  return { top: rect.top, left: rect.left, width: rect.width, height: rect.height };
}

export function useTourAnchor(selector: string | null, active: boolean): AnchorRect | null {
  const [rect, setRect] = useState<AnchorRect | null>(null);

  useEffect(() => {
    if (!active || selector === null) {
      setRect(null);
      return undefined;
    }
    const element = document.querySelector(selector);
    if (element instanceof HTMLElement && typeof element.scrollIntoView === 'function') {
      element.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
    const update = (): void => setRect(measure(selector));
    update();

    // The anchor usually is not there yet: every tab is a lazy chunk, so the
    // first step's `[data-tour="plot"]` mounts a moment after the tour opens. A
    // poll alone showed a card with no highlight for the first quarter second in
    // a real browser (the run in docs/ops-runbook.md caught it), so watch the DOM
    // and measure the instant it appears; the interval stays as the backstop for
    // a node that resizes without mutating (a font or an image landing).
    let observer: MutationObserver | null = null;
    if (typeof MutationObserver !== 'undefined') {
      observer = new MutationObserver(() => {
        if (document.querySelector(selector) !== null) update();
      });
      observer.observe(document.body, { childList: true, subtree: true });
    }
    const retry = setInterval(update, 250);
    const stopRetrying = setTimeout(() => {
      clearInterval(retry);
      observer?.disconnect();
      observer = null;
    }, 5_000);
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      clearInterval(retry);
      clearTimeout(stopRetrying);
      observer?.disconnect();
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [selector, active]);

  return rect;
}
