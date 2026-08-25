/**
 * hooks.ts — the small behavioural pieces the primitives share.
 *
 * These are the bits that make a component keyboard-operable rather than
 * merely clickable (§15 accessibility). Written by hand rather than pulled from
 * a headless-UI package: the surface we need is ~150 lines and a dependency
 * here would land in the initial bundle for every route.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';

/**
 * `useLayoutEffect` that does not warn during SSR / node test environments.
 * Focus management genuinely needs to run before paint, so we cannot simply
 * downgrade it to `useEffect` in the browser.
 */
export const useIsomorphicLayoutEffect =
  typeof window === 'undefined' ? useEffect : useLayoutEffect;

/** Elements that can hold focus inside a dialog. */
const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
  '[contenteditable="true"]',
].join(',');

export function focusableWithin(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (el) => el.offsetParent !== null || el === document.activeElement,
  );
}

/**
 * Trap Tab focus inside `ref` while `active`, restoring focus to whatever was
 * focused before on deactivate. This is the whole reason a modal is usable with
 * a keyboard, so it is not optional decoration.
 */
export function useFocusTrap(ref: RefObject<HTMLElement>, active: boolean): void {
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useIsomorphicLayoutEffect(() => {
    if (!active) return;
    const root = ref.current;
    if (!root) return;

    previouslyFocused.current = document.activeElement as HTMLElement | null;

    const initial = focusableWithin(root)[0] ?? root;
    // A container without focusable children still needs to receive focus so
    // that Escape and screen-reader reading order start inside the dialog.
    if (initial === root && !root.hasAttribute('tabindex')) root.setAttribute('tabindex', '-1');
    initial.focus({ preventScroll: true });

    function onKeyDown(event: KeyboardEvent): void {
      if (event.key !== 'Tab') return;
      const container = ref.current;
      if (!container) return;
      const items = focusableWithin(container);
      if (items.length === 0) {
        event.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (!first || !last) return;
      const activeEl = document.activeElement;
      if (event.shiftKey && (activeEl === first || activeEl === container)) {
        event.preventDefault();
        last.focus({ preventScroll: true });
      } else if (!event.shiftKey && activeEl === last) {
        event.preventDefault();
        first.focus({ preventScroll: true });
      }
    }

    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      previouslyFocused.current?.focus({ preventScroll: true });
    };
  }, [ref, active]);
}

/** Call `handler` on Escape while `active`. Registered on the document so it
 *  works no matter where focus currently is inside the overlay. */
export function useOnEscape(active: boolean, handler: () => void): void {
  const saved = useRef(handler);
  saved.current = handler;
  useEffect(() => {
    if (!active) return;
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === 'Escape') {
        event.stopPropagation();
        saved.current();
      }
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [active]);
}

/** Call `handler` on a pointer press outside `ref` while `active`. */
export function useOnOutsidePointerDown(
  ref: RefObject<HTMLElement>,
  active: boolean,
  handler: () => void,
): void {
  const saved = useRef(handler);
  saved.current = handler;
  useEffect(() => {
    if (!active) return;
    function onPointerDown(event: MouseEvent | TouchEvent): void {
      const node = ref.current;
      if (!node) return;
      if (event.target instanceof Node && node.contains(event.target)) return;
      saved.current();
    }
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('touchstart', onPointerDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('touchstart', onPointerDown);
    };
  }, [ref, active]);
}

/** Lock body scroll while a modal is open, restoring the previous value. */
export function useBodyScrollLock(active: boolean): void {
  useIsomorphicLayoutEffect(() => {
    if (!active) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, [active]);
}

/** True when the OS asks for reduced motion. */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReduced(mq.matches);
    const onChange = (e: MediaQueryListEvent): void => setReduced(e.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
  return reduced;
}

/**
 * Controlled-or-uncontrolled state. Lets `<Tabs value>` and `<Tabs
 * defaultValue>` both work without every caller wiring a useState.
 */
export function useControllableState<T>(
  controlled: T | undefined,
  defaultValue: T,
  onChange?: ((value: T) => void) | undefined,
): [T, (value: T) => void] {
  const [internal, setInternal] = useState<T>(defaultValue);
  const isControlled = controlled !== undefined;
  const value = isControlled ? controlled : internal;
  const set = useCallback(
    (next: T) => {
      if (!isControlled) setInternal(next);
      onChange?.(next);
    },
    [isControlled, onChange],
  );
  return [value, set];
}
