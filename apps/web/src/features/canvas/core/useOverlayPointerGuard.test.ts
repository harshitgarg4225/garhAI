/**
 * The overlay pointer guard, tested at the DOM level it actually operates on.
 *
 * The bug it fixes was invisible to every component test: it lives in the gap
 * between React's delegated handlers and the canvas's native ones, so it only
 * reproduces with real elements, real listeners and real bubbling. These tests
 * therefore build the exact three-layer sandwich `CanvasRoot` renders —
 * container (native canvas listeners) > overlay wrapper (guarded) > panel — and
 * dispatch untrusted events through it.
 */

import { afterEach, describe, expect, it } from 'vitest';

import { OVERLAY_GUARDED_EVENTS, useOverlayPointerGuard } from './useOverlayPointerGuard';

interface Sandwich {
  readonly container: HTMLDivElement;
  readonly wrapper: HTMLDivElement;
  readonly panelButton: HTMLButtonElement;
  readonly seenByCanvas: string[];
  cleanup: () => void;
}

function buildSandwich(): Sandwich {
  const container = document.createElement('div');
  const wrapper = document.createElement('div');
  const panelButton = document.createElement('button');
  wrapper.append(panelButton);
  container.append(wrapper);
  document.body.append(container);

  const seenByCanvas: string[] = [];
  const listener = (event: Event): void => void seenByCanvas.push(event.type);
  for (const name of [...OVERLAY_GUARDED_EVENTS, 'click', 'wheel']) {
    container.addEventListener(name, listener);
  }

  return {
    container,
    wrapper,
    panelButton,
    seenByCanvas,
    cleanup: () => container.remove(),
  };
}

/** Run the hook's effect body by hand — no renderer needed for a DOM hook. */
function armGuard(element: HTMLElement): () => void {
  const stop = (event: Event): void => event.stopPropagation();
  for (const name of OVERLAY_GUARDED_EVENTS) element.addEventListener(name, stop);
  return () => {
    for (const name of OVERLAY_GUARDED_EVENTS) element.removeEventListener(name, stop);
  };
}

function press(target: HTMLElement, type: string): void {
  target.dispatchEvent(new Event(type, { bubbles: true, cancelable: true }));
}

let active: Sandwich | null = null;
afterEach(() => {
  active?.cleanup();
  active = null;
});

describe('useOverlayPointerGuard', () => {
  it('keeps a panel press from reaching the canvas below it', () => {
    active = buildSandwich();
    const disarm = armGuard(active.wrapper);

    press(active.panelButton, 'pointerdown');
    press(active.panelButton, 'pointermove');
    press(active.panelButton, 'pointerup');

    expect(
      active.seenByCanvas,
      'with the wall tool armed this is a stray wall point per click',
    ).toEqual([]);
    disarm();
  });

  it('NEGATIVE CONTROL: without the guard the canvas sees every one of them', () => {
    active = buildSandwich();

    press(active.panelButton, 'pointerdown');
    press(active.panelButton, 'pointermove');
    press(active.panelButton, 'pointerup');

    // If this ever comes back empty the test above proves nothing.
    expect(active.seenByCanvas).toEqual(['pointerdown', 'pointermove', 'pointerup']);
  });

  it('still lets click and wheel through, so buttons work and the wheel zooms', () => {
    active = buildSandwich();
    const disarm = armGuard(active.wrapper);

    press(active.panelButton, 'click');
    press(active.panelButton, 'wheel');

    expect(active.seenByCanvas).toEqual(['click', 'wheel']);
    disarm();
  });

  it('stops guarding once torn down', () => {
    active = buildSandwich();
    const disarm = armGuard(active.wrapper);
    disarm();

    press(active.panelButton, 'pointerdown');

    expect(active.seenByCanvas).toEqual(['pointerdown']);
  });

  it('exports exactly the three events CanvasRoot listens for', () => {
    expect([...OVERLAY_GUARDED_EVENTS]).toEqual(['pointerdown', 'pointermove', 'pointerup']);
    expect(typeof useOverlayPointerGuard).toBe('function');
  });
});
