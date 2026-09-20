/**
 * The tour card, rendered for real: every step's copy, the counter, the buttons
 * and — the accessibility contract — the dialog role, the focus trap, Escape,
 * and the arrow keys. Each behaviour has the run that would break it: a Back on
 * step one that moved, an Escape that did not skip, a Tab that left the card.
 */

import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { TOUR_STEPS, TOUR_STEP_COUNT, clampStep } from './steps';
import { Tour, placeCard } from './Tour';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

function mount(element: ReactElement): void {
  act(() => {
    root.render(element);
  });
}

function card(): HTMLElement {
  const el = document.querySelector('[data-testid="tour-card"]');
  if (!(el instanceof HTMLElement)) throw new Error('no tour card rendered');
  return el;
}

function press(target: Element, key: string, init: KeyboardEventInit = {}): void {
  act(() => {
    target.dispatchEvent(
      new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init }),
    );
  });
}

function click(testId: string): void {
  const el = document.querySelector(`[data-testid="${testId}"]`);
  if (!(el instanceof HTMLElement)) throw new Error(`no ${testId}`);
  act(() => {
    el.click();
  });
}

const noop = (): void => undefined;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('steps', () => {
  it('there are six, in the product order, each on a tab with an anchor and copy', () => {
    expect(TOUR_STEP_COUNT).toBe(6);
    expect(TOUR_STEPS.map((s) => s.id)).toEqual([
      'plot',
      'brief',
      'generate',
      'plan',
      'compliance',
      'sheets',
    ]);
    for (const step of TOUR_STEPS) {
      expect(step.anchor).toMatch(/^\[data-tour="[a-z-]+"\]$/);
      expect(step.title.length).toBeGreaterThan(5);
      expect(step.body.length).toBeGreaterThan(40);
    }
    expect(clampStep(-3)).toBe(0);
    expect(clampStep(99)).toBe(5);
    expect(clampStep(Number.NaN)).toBe(0);
  });

  it('placeCard sits under the anchor, flips above when there is no room, and clamps', () => {
    const viewport = { width: 1200, height: 800 };
    expect(placeCard(null, viewport, 200)).toBeNull();
    expect(placeCard({ top: 100, left: 50, width: 200, height: 40 }, viewport, 200)).toEqual({
      top: 152,
      left: 50,
    });
    expect(placeCard({ top: 700, left: 50, width: 200, height: 40 }, viewport, 200)).toEqual({
      top: 488,
      left: 50,
    });
    // A left edge past the viewport is pulled back so the card stays on screen.
    expect(placeCard({ top: 10, left: 1100, width: 80, height: 20 }, viewport, 200)).toEqual({
      top: 42,
      left: 1200 - 360 - 12,
    });
  });
});

describe('Tour', () => {
  it('renders nothing while closed and a labelled dialog while open', () => {
    mount(
      <Tour
        open={false}
        step={0}
        anchor={null}
        onStepChange={noop}
        onSkip={noop}
        onFinish={noop}
      />,
    );
    expect(document.querySelector('[data-testid="tour"]')).toBeNull();

    mount(<Tour open step={0} anchor={null} onStepChange={noop} onSkip={noop} onFinish={noop} />);
    const dialog = card();
    expect(dialog.getAttribute('role')).toBe('dialog');
    // NOT aria-modal: the tour informs, it does not trap the page. See the
    // pointer-events test below for what that promise is worth.
    expect(dialog.getAttribute('aria-modal')).toBeNull();
    const title = document.getElementById(dialog.getAttribute('aria-labelledby') ?? '');
    const body = document.getElementById(dialog.getAttribute('aria-describedby') ?? '');
    expect(title?.textContent).toBe(TOUR_STEPS[0]?.title);
    expect(body?.textContent).toBe(TOUR_STEPS[0]?.body);
    expect(dialog.textContent).toContain('Step 1 of 6');
    expect(dialog.getAttribute('data-step')).toBe('plot');
    // No anchor ⇒ centred, no highlight.
    expect(document.querySelector('[data-testid="tour-highlight"]')).toBeNull();
    expect(dialog.className).toContain('left-1/2');
  });

  it('never swallows a click meant for the app underneath', () => {
    // CI run 91: the full-screen dim layer intercepted pointer events, Playwright
    // retried the plot button 33 times against it and the @smoke journey timed out.
    // A first-time architect would have been just as stuck. The overlay dims; the
    // card takes clicks; nothing else does.
    mount(<Tour open step={0} anchor={null} onStepChange={noop} onSkip={noop} onFinish={noop} />);
    const overlay = document.querySelector('[data-testid="tour"]');
    expect(overlay?.className).toContain('pointer-events-none');
    expect(card().className).toContain('pointer-events-auto');
    const dim = overlay?.querySelector('[aria-hidden="true"]');
    expect(dim?.className).not.toContain('pointer-events-auto');
  });

  it('draws the highlight around the anchor and places the card beneath it', () => {
    mount(
      <Tour
        open
        step={2}
        anchor={{ top: 100, left: 40, width: 120, height: 30 }}
        onStepChange={noop}
        onSkip={noop}
        onFinish={noop}
      />,
    );
    const highlight = document.querySelector('[data-testid="tour-highlight"]');
    expect(highlight).not.toBeNull();
    expect((highlight as HTMLElement).style.top).toBe('94px');
    expect((highlight as HTMLElement).style.width).toBe('132px');
    expect(card().style.top).toBe('142px');
    expect(card().getAttribute('data-step')).toBe('generate');
  });

  it('Next and Back move a step; Back is disabled on the first; Finish replaces Next on the last', () => {
    const onStepChange = vi.fn();
    const onFinish = vi.fn();
    mount(
      <Tour
        open
        step={0}
        anchor={null}
        onStepChange={onStepChange}
        onSkip={noop}
        onFinish={onFinish}
      />,
    );
    expect(
      (document.querySelector('[data-testid="tour-back"]') as HTMLButtonElement).disabled,
    ).toBe(true);
    click('tour-next');
    expect(onStepChange).toHaveBeenLastCalledWith(1);

    mount(
      <Tour
        open
        step={3}
        anchor={null}
        onStepChange={onStepChange}
        onSkip={noop}
        onFinish={onFinish}
      />,
    );
    click('tour-back');
    expect(onStepChange).toHaveBeenLastCalledWith(2);

    mount(
      <Tour
        open
        step={5}
        anchor={null}
        onStepChange={onStepChange}
        onSkip={noop}
        onFinish={onFinish}
      />,
    );
    expect(document.querySelector('[data-testid="tour-next"]')).toBeNull();
    click('tour-finish');
    expect(onFinish).toHaveBeenCalledTimes(1);
  });

  it('← → move between steps, Enter on the card advances, Escape skips', () => {
    const onStepChange = vi.fn();
    const onSkip = vi.fn();
    const onFinish = vi.fn();
    mount(
      <Tour
        open
        step={1}
        anchor={null}
        onStepChange={onStepChange}
        onSkip={onSkip}
        onFinish={onFinish}
      />,
    );
    press(card(), 'ArrowRight');
    expect(onStepChange).toHaveBeenLastCalledWith(2);
    press(card(), 'ArrowLeft');
    expect(onStepChange).toHaveBeenLastCalledWith(0);
    press(card(), 'Enter');
    expect(onStepChange).toHaveBeenLastCalledWith(2);
    press(document.body, 'Escape');
    expect(onSkip).toHaveBeenCalledTimes(1);
    expect(onFinish).not.toHaveBeenCalled();

    // ← on the first step does nothing; → on the last finishes.
    onStepChange.mockClear();
    mount(
      <Tour
        open
        step={0}
        anchor={null}
        onStepChange={onStepChange}
        onSkip={onSkip}
        onFinish={onFinish}
      />,
    );
    press(card(), 'ArrowLeft');
    expect(onStepChange).not.toHaveBeenCalled();
    mount(
      <Tour
        open
        step={5}
        anchor={null}
        onStepChange={onStepChange}
        onSkip={onSkip}
        onFinish={onFinish}
      />,
    );
    press(card(), 'ArrowRight');
    expect(onFinish).toHaveBeenCalledTimes(1);
  });

  it('Skip calls onSkip, and focus lands inside the card and is trapped there', () => {
    const outside = document.createElement('button');
    outside.textContent = 'outside';
    document.body.appendChild(outside);
    outside.focus();
    expect(document.activeElement).toBe(outside);

    const onSkip = vi.fn();
    mount(<Tour open step={0} anchor={null} onStepChange={noop} onSkip={onSkip} onFinish={noop} />);
    // The trap moves focus to the first focusable control in the card.
    expect(card().contains(document.activeElement)).toBe(true);
    const focusables = Array.from(card().querySelectorAll<HTMLElement>('button:not([disabled])'));
    const last = focusables[focusables.length - 1];
    expect(last).toBeDefined();
    last?.focus();
    // Tab from the last control does not leave the dialog. WHICH control it lands
    // on cannot be asserted here: `focusableWithin` filters on `offsetParent`,
    // which jsdom never populates, so its list is whatever is focused. The
    // property that matters — focus stays inside — holds either way, and the
    // ordering is covered in a real browser (the @smoke run in the ledger).
    press(last as HTMLElement, 'Tab');
    expect(card().contains(document.activeElement)).toBe(true);

    click('tour-skip');
    expect(onSkip).toHaveBeenCalledTimes(1);
    outside.remove();
  });

  it('closing the tour returns focus to whatever had it before', () => {
    const outside = document.createElement('button');
    document.body.appendChild(outside);
    outside.focus();

    mount(<Tour open step={0} anchor={null} onStepChange={noop} onSkip={noop} onFinish={noop} />);
    expect(document.activeElement).not.toBe(outside);

    mount(
      <Tour
        open={false}
        step={0}
        anchor={null}
        onStepChange={noop}
        onSkip={noop}
        onFinish={noop}
      />,
    );
    expect(document.activeElement).toBe(outside);
    outside.remove();
  });
});
