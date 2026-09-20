/**
 * useTourAnchor — measuring the thing a step points at.
 *
 * The case that matters is the one a real browser found: every project tab is a
 * lazy chunk, so the first step's anchor is NOT in the DOM when the tour opens.
 * A 250 ms poll left the card with no highlight for a visible moment; the hook
 * now watches the DOM and measures the instant the node arrives. The test adds
 * the anchor after the hook is mounted and asserts the rect appears without any
 * timer running — with "never added" as the negative control.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { useTourAnchor, type AnchorRect } from './useTourAnchor';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let seen: AnchorRect | null = null;

function Probe({ selector, active }: { selector: string | null; active: boolean }): null {
  seen = useTourAnchor(selector, active);
  return null;
}

/** jsdom gives every element a zero box; fake a real one for the nodes we add. */
function withBox(el: HTMLElement, box: AnchorRect): HTMLElement {
  el.getBoundingClientRect = () =>
    ({
      ...box,
      right: box.left + box.width,
      bottom: box.top + box.height,
      x: box.left,
      y: box.top,
      toJSON: () => ({}),
    }) as DOMRect;
  return el;
}

function anchorNode(box: AnchorRect = { top: 40, left: 20, width: 300, height: 120 }): HTMLElement {
  const el = withBox(document.createElement('section'), box);
  el.setAttribute('data-tour', 'plot');
  return el;
}

async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  seen = null;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  document.querySelectorAll('[data-tour]').forEach((el) => el.remove());
});

describe('useTourAnchor', () => {
  it('measures an anchor that is already on screen', async () => {
    document.body.appendChild(anchorNode());
    act(() => {
      root.render(<Probe selector='[data-tour="plot"]' active />);
    });
    await settle();
    expect(seen).toEqual({ top: 40, left: 20, width: 300, height: 120 });
  });

  it('measures an anchor that arrives AFTER the tour opened (the lazy tab)', async () => {
    act(() => {
      root.render(<Probe selector='[data-tour="plot"]' active />);
    });
    await settle();
    expect(seen).toBeNull();

    // The tab's chunk mounts its section; the observer must catch it without
    // waiting for the 250 ms backstop.
    await act(async () => {
      document.body.appendChild(anchorNode({ top: 10, left: 5, width: 200, height: 80 }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(seen).toEqual({ top: 10, left: 5, width: 200, height: 80 });
  });

  it('stays null for an anchor that never arrives, and while inactive', async () => {
    act(() => {
      root.render(<Probe selector='[data-tour="nothing-here"]' active />);
    });
    await settle();
    await act(async () => {
      document.body.appendChild(anchorNode());
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(seen).toBeNull();

    act(() => {
      root.render(<Probe selector='[data-tour="plot"]' active={false} />);
    });
    await settle();
    expect(seen).toBeNull();
  });

  it('ignores a node with no box (rendered but collapsed)', async () => {
    const el = document.createElement('section');
    el.setAttribute('data-tour', 'plot');
    document.body.appendChild(el); // jsdom: all-zero rect
    act(() => {
      root.render(<Probe selector='[data-tour="plot"]' active />);
    });
    await settle();
    expect(seen).toBeNull();
  });
});
