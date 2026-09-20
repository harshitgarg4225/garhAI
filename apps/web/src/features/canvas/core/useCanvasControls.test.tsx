/**
 * The pointer path, driven with synthetic DOM events in jsdom.
 *
 * What is under test is the DEVICE semantics the reader found missing: a
 * trackpad's two-finger scroll must pan and its pinch must zoom, a mouse
 * notch must still zoom, two fingers on a touch screen must navigate and
 * never reach the tools, and the element must refuse the browser's own
 * touch gestures. Each has a negative control beside it.
 *
 * jsdom has no `PointerEvent` and no pointer capture; both are polyfilled
 * below with exactly the fields the hook reads. The camera is null (no rig),
 * which is fine — the viewport controller is what every gesture mutates.
 */

import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CanvasCore } from './context';
import { useCanvasControls, type CanvasControlsCallbacks } from './useCanvasControls';

// ---------------------------------------------------------------------------
// Polyfills
// ---------------------------------------------------------------------------

interface PointerInit extends MouseEventInit {
  pointerId?: number;
  pointerType?: string;
}

class FakePointerEvent extends MouseEvent {
  readonly pointerId: number;
  readonly pointerType: string;
  constructor(type: string, init: PointerInit = {}) {
    super(type, { bubbles: true, cancelable: true, ...init });
    this.pointerId = init.pointerId ?? 1;
    this.pointerType = init.pointerType ?? 'mouse';
  }
}

const captured = new Set<number>();
beforeEach(() => {
  vi.stubGlobal('PointerEvent', FakePointerEvent);
  Object.assign(HTMLElement.prototype, {
    setPointerCapture: (id: number) => {
      captured.add(id);
    },
    releasePointerCapture: (id: number) => {
      captured.delete(id);
    },
    hasPointerCapture: (id: number) => captured.has(id),
  });
  captured.clear();
});

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

function Host({
  core,
  callbacks,
  onElement,
}: {
  core: CanvasCore;
  callbacks: CanvasControlsCallbacks;
  onElement: (el: HTMLDivElement | null) => void;
}): JSX.Element {
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  useCanvasControls(el, { core, ...callbacks });
  return (
    <div
      ref={(node) => {
        setEl(node);
        onElement(node);
      }}
      style={{ width: 800, height: 600 }}
    />
  );
}

interface Mounted {
  core: CanvasCore;
  element: HTMLDivElement;
  calls: { down: number; move: number; up: number; click: number };
  unmount: () => void;
}

let roots: Root[] = [];
afterEach(() => {
  for (const root of roots) act(() => root.unmount());
  roots = [];
  vi.unstubAllGlobals();
});

function mount(): Mounted {
  const core = new CanvasCore();
  core.viewport.setSize(800, 600);
  const calls = { down: 0, move: 0, up: 0, click: 0 };
  let element: HTMLDivElement | null = null;
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);
  act(() => {
    root.render(
      <Host
        core={core}
        callbacks={{
          onPointerDown: () => {
            calls.down += 1;
          },
          onPointerMove: () => {
            calls.move += 1;
          },
          onPointerUp: () => {
            calls.up += 1;
          },
          onClick: () => {
            calls.click += 1;
          },
        }}
        onElement={(el) => {
          element = el;
        }}
      />,
    );
  });
  if (element === null) throw new Error('host did not mount');
  const el: HTMLDivElement = element;
  // jsdom has no layout: give the hook the rect the viewport was sized to.
  el.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 800, height: 600, right: 800, bottom: 600, x: 0, y: 0 }) as DOMRect;
  return {
    core,
    element: el,
    calls,
    unmount: () => act(() => root.unmount()),
  };
}

function wheel(el: HTMLElement, init: WheelEventInit & { wheelDeltaY?: number }): void {
  const event = new WheelEvent('wheel', { bubbles: true, cancelable: true, ...init });
  if (init.wheelDeltaY !== undefined) {
    Object.defineProperty(event, 'wheelDeltaY', { value: init.wheelDeltaY });
  }
  el.dispatchEvent(event);
}

function pointer(el: HTMLElement, type: string, init: PointerInit): void {
  el.dispatchEvent(new FakePointerEvent(type, init));
}

const nextFrame = (): Promise<void> =>
  new Promise((resolve) => {
    requestAnimationFrame(() => resolve());
  });

// ---------------------------------------------------------------------------
// Wheel
// ---------------------------------------------------------------------------

describe('wheel', () => {
  it('a mouse notch zooms and does not pan (the CAD convention)', () => {
    const { core, element } = mount();
    const before = core.viewport.view2d;
    wheel(element, { deltaY: -100, wheelDeltaY: 120, clientX: 400, clientY: 300 });
    const after = core.viewport.view2d;
    expect(after.mmPerPx).toBeLessThan(before.mmPerPx);
    // Zoom is about the cursor at the centre, so the centre stays put.
    expect(after.centreMm).toEqual(before.centreMm);
  });

  it('a two-finger scroll pans the view and leaves the zoom alone', () => {
    const { core, element } = mount();
    const before = core.viewport.view2d;
    wheel(element, { deltaX: 12, deltaY: 8, wheelDeltaY: -24, clientX: 400, clientY: 300 });
    const after = core.viewport.view2d;
    expect(after.mmPerPx).toBe(before.mmPerPx);
    // Scrolling right/down moves the content left/up: the centre goes +x, −y.
    expect(after.centreMm.x).toBeCloseTo(before.centreMm.x + 12 * before.mmPerPx, 9);
    expect(after.centreMm.y).toBeCloseTo(before.centreMm.y - 8 * before.mmPerPx, 9);
  });

  it('a pinch (ctrl+wheel) zooms towards the cursor', () => {
    const { core, element } = mount();
    const before = core.viewport.view2d;
    wheel(element, { deltaY: -10, ctrlKey: true, clientX: 600, clientY: 300 });
    const after = core.viewport.view2d;
    expect(after.mmPerPx).toBeLessThan(before.mmPerPx);
    // Zooming towards a point right of centre pulls the centre right.
    expect(after.centreMm.x).toBeGreaterThan(before.centreMm.x);
  });

  it('prevents the page from scrolling behind the canvas', () => {
    const { element } = mount();
    const event = new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 8 });
    element.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Touch
// ---------------------------------------------------------------------------

describe('touch', () => {
  it('sets touch-action: none so the browser never pans the page instead', () => {
    const { element, unmount } = mount();
    expect(element.style.touchAction).toBe('none');
    unmount();
    // Restored to what it was: '' in a browser, undefined in jsdom (which has
    // no `touchAction` on its style declaration). Either way, not ours.
    expect(element.style.touchAction).not.toBe('none');
  });

  it('one finger is an ordinary pointer: the tools hear it', () => {
    const { element, calls } = mount();
    pointer(element, 'pointerdown', {
      pointerId: 7,
      pointerType: 'touch',
      clientX: 100,
      clientY: 100,
    });
    pointer(element, 'pointerup', {
      pointerId: 7,
      pointerType: 'touch',
      clientX: 100,
      clientY: 100,
    });
    expect(calls.down).toBe(1);
    expect(calls.up).toBe(1);
    expect(calls.click).toBe(1);
  });

  it('a stylus is a mouse', () => {
    const { element, calls } = mount();
    pointer(element, 'pointerdown', {
      pointerId: 3,
      pointerType: 'pen',
      clientX: 100,
      clientY: 100,
    });
    pointer(element, 'pointerup', { pointerId: 3, pointerType: 'pen', clientX: 100, clientY: 100 });
    expect(calls.click).toBe(1);
  });

  it('a second finger turns the gesture into pan + pinch and silences the tools', async () => {
    const { core, element, calls } = mount();
    const before = core.viewport.view2d;
    pointer(element, 'pointerdown', {
      pointerId: 1,
      pointerType: 'touch',
      clientX: 300,
      clientY: 300,
    });
    pointer(element, 'pointerdown', {
      pointerId: 2,
      pointerType: 'touch',
      clientX: 500,
      clientY: 300,
    });
    // Fingers spread and the pair drifts right.
    pointer(element, 'pointermove', {
      pointerId: 1,
      pointerType: 'touch',
      clientX: 300,
      clientY: 300,
    });
    pointer(element, 'pointermove', {
      pointerId: 2,
      pointerType: 'touch',
      clientX: 600,
      clientY: 300,
    });
    await nextFrame();
    const after = core.viewport.view2d;
    expect(after.mmPerPx).toBeLessThan(before.mmPerPx);
    expect(after.centreMm.x).not.toBe(before.centreMm.x);

    pointer(element, 'pointerup', {
      pointerId: 2,
      pointerType: 'touch',
      clientX: 600,
      clientY: 300,
    });
    pointer(element, 'pointerup', {
      pointerId: 1,
      pointerType: 'touch',
      clientX: 300,
      clientY: 300,
    });
    // The first finger's press was delivered (the browser cannot know a pinch
    // is coming); nothing after the second finger landed reached the tools,
    // and the gesture did not end in a click.
    expect(calls.down).toBe(1);
    expect(calls.move).toBe(0);
    expect(calls.up).toBe(0);
    expect(calls.click).toBe(0);
  });

  it('negative control: two fingers that neither move nor spread change nothing', () => {
    const { core, element } = mount();
    const before = core.viewport.view2d;
    pointer(element, 'pointerdown', {
      pointerId: 1,
      pointerType: 'touch',
      clientX: 300,
      clientY: 300,
    });
    pointer(element, 'pointerdown', {
      pointerId: 2,
      pointerType: 'touch',
      clientX: 500,
      clientY: 300,
    });
    pointer(element, 'pointermove', {
      pointerId: 1,
      pointerType: 'touch',
      clientX: 300,
      clientY: 300,
    });
    expect(core.viewport.view2d).toEqual(before);
  });

  it('the tools hear a single finger again once every finger has lifted', () => {
    const { element, calls } = mount();
    pointer(element, 'pointerdown', {
      pointerId: 1,
      pointerType: 'touch',
      clientX: 300,
      clientY: 300,
    });
    pointer(element, 'pointerdown', {
      pointerId: 2,
      pointerType: 'touch',
      clientX: 500,
      clientY: 300,
    });
    pointer(element, 'pointerup', {
      pointerId: 1,
      pointerType: 'touch',
      clientX: 300,
      clientY: 300,
    });
    pointer(element, 'pointerup', {
      pointerId: 2,
      pointerType: 'touch',
      clientX: 500,
      clientY: 300,
    });
    pointer(element, 'pointerdown', {
      pointerId: 9,
      pointerType: 'touch',
      clientX: 100,
      clientY: 100,
    });
    pointer(element, 'pointerup', {
      pointerId: 9,
      pointerType: 'touch',
      clientX: 100,
      clientY: 100,
    });
    expect(calls.down).toBe(2);
    expect(calls.click).toBe(1);
  });
});
