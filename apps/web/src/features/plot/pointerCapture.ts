/**
 * Pointer capture, feature-detected.
 *
 * Capturing the pointer lets a drag survive the pointer leaving the handle —
 * a browser nicety, not a requirement: the drag works without it. jsdom and
 * some SVG implementations do not provide the methods, and a drag that throws
 * on its first event is worse than one that loses the handle at the edge.
 */

export function capturePointer(el: Element, pointerId: number): void {
  const target = el as Element & { setPointerCapture?: (id: number) => void };
  if (typeof target.setPointerCapture === 'function') {
    try {
      target.setPointerCapture(pointerId);
    } catch {
      /* not capturable — fine */
    }
  }
}

export function releasePointer(el: Element, pointerId: number): void {
  const target = el as Element & { releasePointerCapture?: (id: number) => void };
  if (typeof target.releasePointerCapture === 'function') {
    try {
      target.releasePointerCapture(pointerId);
    } catch {
      /* not captured — fine */
    }
  }
}
