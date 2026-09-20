/**
 * jsdom harness for the plot feature's component specs.
 *
 * Real store, real components, real DOM events — no testing library (this
 * workspace has none) and no mocks of the model store: `dispatch` folds through
 * the actual model core and `flush` is a no-op while `projectId` is null, so
 * what a test asserts is the folded document the whole app reads.
 *
 * Two things jsdom lacks are supplied here, and the second is why this file
 * exists: `PointerEvent` (React's `onPointerDown` listens for the native
 * `pointerdown` type, so a MouseEvent subclass carrying `pointerId` is enough),
 * and an SVG bounding box (jsdom lays nothing out, so `getBoundingClientRect`
 * is zeros — the editor would divide by a scale of 0). The box is stubbed at
 * exactly the viewBox size, which makes the SVG user-unit ↔ client-pixel
 * mapping the identity and lets a test aim the pointer in model space via
 * `toSvg` alone.
 */

import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';

import { emptyProjectDoc, fold, type Op, type ProjectDoc } from '@garh/model';

import { useModelStore } from '../../stores/model';
import { usePlotEditSession } from './usePlot';

declare global {
  // React 18 reads this to decide whether `act` warnings apply.
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

// ---------------------------------------------------------------------------
// PointerEvent polyfill
// ---------------------------------------------------------------------------

interface PointerEventInitLike extends MouseEventInit {
  pointerId?: number;
  pointerType?: string;
}

class PolyfilledPointerEvent extends MouseEvent {
  readonly pointerId: number;
  readonly pointerType: string;
  constructor(type: string, init: PointerEventInitLike = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 1;
    this.pointerType = init.pointerType ?? 'mouse';
  }
}

export function installPointerEvents(): void {
  if (typeof globalThis.PointerEvent === 'undefined') {
    (globalThis as unknown as { PointerEvent: typeof PolyfilledPointerEvent }).PointerEvent =
      PolyfilledPointerEvent;
  }
}

// ---------------------------------------------------------------------------
// SVG bounding box
// ---------------------------------------------------------------------------

let svgBox = { width: 0, height: 0 };

/** Make every `<svg>` report this box (left/top 0). Call before mounting. */
export function setSvgBox(width: number, height: number): void {
  svgBox = { width, height };
}

export function installSvgBox(): void {
  const proto = globalThis.SVGSVGElement?.prototype as
    | { getBoundingClientRect: () => DOMRect }
    | undefined;
  if (proto === undefined) return;
  proto.getBoundingClientRect = function (): DOMRect {
    const { width, height } = svgBox;
    return {
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: width,
      bottom: height,
      width,
      height,
      toJSON: () => ({}),
    } as DOMRect;
  };
}

// ---------------------------------------------------------------------------
// Store seeding
// ---------------------------------------------------------------------------

export function foldAll(ops: readonly Op[]): ProjectDoc {
  let doc = emptyProjectDoc();
  for (const op of ops) doc = fold(doc, op, { computeInverse: false }).model;
  return doc;
}

/** A ready, unsynced project holding `ops` folded — `flush` no-ops without a project id. */
export function seedStore(ops: readonly Op[]): ProjectDoc {
  const doc = foldAll(ops);
  useModelStore.setState({
    projectId: null,
    versionBranch: null,
    status: 'ready',
    loadError: null,
    doc,
    serverDoc: doc,
    baseIdx: -1,
    headIdx: -1,
    pending: [],
    flushing: false,
    saveState: 'idle',
    syncError: null,
    divergedAt: null,
    undoStack: [],
    redoStack: [],
  });
  usePlotEditSession.setState({ planTouchedAt: null });
  return doc;
}

export function currentDoc(): ProjectDoc {
  return useModelStore.getState().doc;
}

// ---------------------------------------------------------------------------
// Mounting and events
// ---------------------------------------------------------------------------

export interface Mounted {
  readonly container: HTMLDivElement;
  readonly root: Root;
  readonly unmount: () => void;
}

export function mount(element: ReactElement): Mounted {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(element);
  });
  return {
    container,
    root,
    unmount: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
}

export function rerender(m: Mounted, element: ReactElement): void {
  act(() => {
    m.root.render(element);
  });
}

export function byTestId<T extends Element = HTMLElement>(container: Element, id: string): T {
  const el = container.querySelector<T>(`[data-testid="${id}"]`);
  if (el === null) throw new Error(`no element with data-testid="${id}"`);
  return el;
}

export function byAriaLabel<T extends Element = HTMLElement>(container: Element, label: string): T {
  const el = container.querySelector<T>(`[aria-label="${label.replace(/"/g, '\\"')}"]`);
  if (el === null) throw new Error(`no element with aria-label="${label}"`);
  return el;
}

/** The control a `<label>` starting with `text` points at (Field's htmlFor). */
export function inputByLabel(container: Element, text: string): HTMLInputElement {
  const label = Array.from(container.querySelectorAll('label')).find((l) =>
    (l.textContent ?? '').trim().startsWith(text),
  );
  if (label === undefined) throw new Error(`no label starting with "${text}"`);
  const control =
    label.htmlFor !== ''
      ? (document.getElementById(label.htmlFor) as HTMLInputElement | null)
      : label.querySelector('input');
  if (control === null) throw new Error(`no control for label "${text}"`);
  return control;
}

export function click(el: Element): void {
  act(() => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  });
}

export function keyDown(el: Element, key: string, init: KeyboardEventInit = {}): void {
  act(() => {
    el.dispatchEvent(
      new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init }),
    );
  });
}

export function pointer(
  el: Element,
  type: 'pointerdown' | 'pointermove' | 'pointerup' | 'pointercancel',
  init: PointerEventInitLike,
): void {
  act(() => {
    el.dispatchEvent(
      new globalThis.PointerEvent(type, { bubbles: true, cancelable: true, pointerId: 1, ...init }),
    );
  });
}

export function focus(el: Element): void {
  act(() => {
    (el as HTMLElement).focus?.();
    el.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
  });
}

/**
 * Blur like a browser: when the element really holds focus, `blur()` makes
 * jsdom fire the focusout itself (so focusing the NEXT field later cannot fire
 * a second one); otherwise dispatch the event directly.
 */
export function blur(el: Element): void {
  act(() => {
    if (document.activeElement === el && typeof (el as HTMLElement).blur === 'function') {
      (el as HTMLElement).blur();
    } else {
      el.dispatchEvent(new FocusEvent('focusout', { bubbles: true }));
    }
  });
}

/** Type into a React-controlled input (native setter + input event). */
export function setValue(el: HTMLInputElement | HTMLSelectElement, value: string): void {
  const proto =
    el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
  act(() => {
    setter?.call(el, value);
    el.dispatchEvent(
      new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }),
    );
  });
}

/** Type a value and commit it the way a person does: blur (or Enter). */
export function commitValue(
  el: HTMLInputElement,
  value: string,
  how: 'blur' | 'enter' = 'blur',
): void {
  focus(el);
  setValue(el, value);
  if (how === 'enter') keyDown(el, 'Enter');
  else blur(el);
}
