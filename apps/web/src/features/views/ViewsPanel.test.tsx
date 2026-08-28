/**
 * Spec for the panel, driven by real clicks on a real DOM.
 *
 * `createRoot` into a jsdom container and `element.click()` — no testing
 * library, because this workspace does not have one, following the pattern
 * `features/layers/LayerPanel.test.tsx` set.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT THESE ASSERTIONS ARE MADE AGAINST, AND WHY IT MATTERS
 * ════════════════════════════════════════════════════════════════════════════
 * Not the store. The `ViewportController` — the object that actually decides
 * what is on screen. A panel wired to a store nothing reads would pass a "the
 * row appeared" test and fail every one of these, which is exactly the shape of
 * the furniture-layer bug this repo shipped: a module that documented itself as
 * integrated and never called the thing it claimed to.
 *
 * Restores run with `reducedMotion: true`, so a click lands the camera in the
 * same tick. The flight itself is specced frame by frame in `restore.test.ts`;
 * what is being checked here is that the click reaches it at all.
 */

import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { FIXTURE_IDS, makeTwoRoomPlanWithOpenings } from '@garh/model';

import { useModelStore } from '../../stores/model';
import { useSelectionStore } from '../../stores/selection';
import { useSessionStore } from '../../stores/session';
import { useUiStore } from '../../stores/ui';
import { CanvasCore } from '../canvas/core/context';
import { captureCamera, sameCamera } from './camera';
import { readViews } from './persist';
import { useViewsStore } from './store';
import type { Saved2dCamera, SavedCamera } from './types';
import { ANONYMOUS_USER_ID } from './useViews';
import { ViewsPanel } from './ViewsPanel';

const DOC = makeTwoRoomPlanWithOpenings();
const PROJECT_ID = 'proj_views';
const SCOPE = { userId: ANONYMOUS_USER_ID, projectId: PROJECT_ID };

declare global {
  // React 18 reads this to decide whether `act` warnings apply.
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let core: CanvasCore;

function mount(element: ReactElement): void {
  act(() => {
    root.render(element);
  });
}

function panel(): ReactElement {
  return <ViewsPanel projectId={PROJECT_ID} core={core} restoreOptions={{ reducedMotion: true }} />;
}

function click(element: Element): void {
  act(() => {
    (element as HTMLElement).click();
  });
}

function buttons(pattern: RegExp): HTMLButtonElement[] {
  return [...container.querySelectorAll('button')].filter((button) =>
    pattern.test(button.getAttribute('aria-label') ?? ''),
  );
}

function one(pattern: RegExp): HTMLButtonElement {
  const found = buttons(pattern);
  expect(found, `expected exactly one control matching ${String(pattern)}`).toHaveLength(1);
  const first = found[0];
  if (first === undefined) throw new Error('unreachable');
  return first;
}

function builtIn(label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll('button')].filter(
    (button) => button.textContent?.trim() === label,
  );
  expect(found, `expected one built-in button labelled ${label}`).toHaveLength(1);
  const first = found[0];
  if (first === undefined) throw new Error('unreachable');
  return first;
}

function nameInput(): HTMLInputElement {
  const input = container.querySelector<HTMLInputElement>(
    'input[aria-label="Name for the current view"]',
  );
  if (input === null) throw new Error('no name input');
  return input;
}

/**
 * Type into a controlled React input the way a user would.
 *
 * The native setter, not `input.value = x`. React installs a value tracker on
 * the element that swallows a plain assignment — it sees the new value already
 * in place when the event arrives, decides nothing changed, and never calls
 * `onChange`. Going through `HTMLInputElement.prototype`'s own setter writes
 * past the tracker, which is why every React testing library does the same.
 */
function typeInto(input: HTMLInputElement, value: string): void {
  // A property descriptor's setter is unbound by definition; `.call(input, …)`
  // supplies the receiver two lines down, which is the whole point of it.
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  act(() => {
    setter?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

function pressKey(element: Element, key: string): void {
  act(() => {
    element.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
  });
}

function rowNames(): string[] {
  return [...container.querySelectorAll('li button[aria-label^="Restore "]')].map((button) =>
    (button.textContent ?? '').trim(),
  );
}

function saveCurrentAs(name: string): void {
  typeInto(nameInput(), name);
  click(one(/^Save the current view$/));
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.localStorage.clear();

  core = new CanvasCore();
  core.viewport.setSize(1280, 840);

  useViewsStore.setState({ scope: null, views: [] });
  useModelStore.setState({ doc: DOC });
  useSessionStore.setState({ user: null });
  useUiStore.setState({ viewMode: '2d', activeStoreyId: FIXTURE_IDS.groundStorey });
  useSelectionStore.setState({ ids: [], kinds: {} });

  container = document.createElement('div');
  document.body.appendChild(container);
  act(() => {
    root = createRoot(container);
  });
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

// ---------------------------------------------------------------------------

describe('saving and restoring, through the panel', () => {
  it('lands the real camera back on exactly the saved numbers', () => {
    mount(panel());

    // Frame something specific, the way an architect would.
    core.viewport.panPx(-231, 87);
    core.viewport.wheel(-197, 0, { x: 517, y: 233 });
    const saved = captureCamera(core.viewport) as Saved2dCamera;

    saveCurrentAs('Kitchen detail');
    expect(rowNames()).toEqual(['Kitchen detail']);

    // Wander away, and prove we really did.
    core.viewport.panPx(600, -400);
    core.viewport.wheel(400, 0, { x: 20, y: 800 });
    expect(sameCamera(captureCamera(core.viewport), saved)).toBe(false);

    click(one(/^Restore Kitchen detail$/));

    const landed = captureCamera(core.viewport) as Saved2dCamera;
    expect(landed).toEqual(saved);
    expect(landed.centreMm.x).toBe(saved.centreMm.x);
    expect(landed.centreMm.y).toBe(saved.centreMm.y);
    expect(landed.mmPerPx).toBe(saved.mmPerPx);
  });

  it('NEGATIVE CONTROL: a single-millimetre change of the saved camera is caught', () => {
    mount(panel());
    core.viewport.panPx(-231, 87);
    const saved = captureCamera(core.viewport) as Saved2dCamera;
    saveCurrentAs('Kitchen detail');
    click(one(/^Restore Kitchen detail$/));

    const landed = captureCamera(core.viewport) as Saved2dCamera;
    const perturbed: SavedCamera = {
      ...saved,
      centreMm: { x: saved.centreMm.x + 1e-9, y: saved.centreMm.y },
    };
    expect(sameCamera(landed, saved)).toBe(true);
    expect(sameCamera(landed, perturbed)).toBe(false);
  });

  it('persists the view, so it is there on the next visit', () => {
    mount(panel());
    core.viewport.panPx(10, 20);
    saveCurrentAs('Street elevation');

    const stored = readViews(SCOPE) ?? [];
    expect(stored.map((view) => view.name)).toEqual(['Street elevation']);
    expect(stored[0]?.camera).toEqual(captureCamera(core.viewport));
  });

  it('names an unnamed save after the placeholder it showed', () => {
    mount(panel());
    const suggestion = nameInput().placeholder;
    click(one(/^Save the current view$/));
    expect(rowNames()).toEqual([suggestion]);
  });
});

describe('a 2D view restored while the user is in 3D', () => {
  it('frames the plan, asks the app to switch, and leaves the orbit alone', () => {
    mount(panel());

    // Save a plan view…
    core.viewport.panPx(-120, 60);
    const plan = captureCamera(core.viewport) as Saved2dCamera;
    saveCurrentAs('Ground floor');

    // …then go to 3D, as the app would (the ui store drives `CameraRig`, which
    // drives the controller; here the two are set together).
    act(() => {
      useUiStore.getState().setViewMode('3d');
    });
    core.viewport.setMode('3d');
    const orbitBefore = captureCamera(core.viewport);

    click(one(/^Restore Ground floor$/));

    // 1. the app was asked to go back to the plan — the visible transition
    expect(useUiStore.getState().viewMode).toBe('2d');
    // 2. the plan camera is already exactly framed for when the rig swaps
    expect(core.viewport.view2d.centreMm).toEqual(plan.centreMm);
    expect(core.viewport.view2d.mmPerPx).toBe(plan.mmPerPx);
    // 3. the 3D camera the user is still looking at was not disturbed
    expect(sameCamera(captureCamera(core.viewport), orbitBefore)).toBe(true);
    // 4. the panel did NOT set the controller's mode itself — `CameraRig` owns
    //    that, and doing it here would leave R3F on the other camera.
    expect(core.viewport.mode).toBe('3d');
  });

  it('saves a 3D view as a 3D view, and labels it as one', () => {
    core.viewport.setMode('3d');
    mount(panel());
    saveCurrentAs('From the street');

    const stored = readViews(SCOPE) ?? [];
    expect(stored[0]?.camera.mode).toBe('3d');
    expect(container.textContent).toContain('3d');
  });
});

describe('the built-in views', () => {
  it('fits the selection only when there is one, and frames it when clicked', () => {
    mount(panel());
    expect(builtIn('Fit selection').disabled).toBe(true);

    act(() => {
      useSelectionStore.getState().selectMany([FIXTURE_IDS.wallSpine]);
    });
    expect(builtIn('Fit selection').disabled).toBe(false);

    click(builtIn('Fit selection'));

    // The spine wall runs (3000,0)–(3000,4000): the camera centre lands on it,
    // to within the half-millimetre its 115 mm ring rounds to.
    const camera = captureCamera(core.viewport) as Saved2dCamera;
    expect(Math.abs(camera.centreMm.x - 3000)).toBeLessThan(2);
    expect(Math.abs(camera.centreMm.y - 2000)).toBeLessThan(2);
  });

  it('fits the whole project, plot included', () => {
    mount(panel());
    click(builtIn('Fit all'));
    const camera = captureCamera(core.viewport) as Saved2dCamera;
    // Plot 9144 × 12192 unioned with the building's 115 mm overhang.
    expect(camera.centreMm.x).toBeCloseTo((-115 + 9144) / 2, 6);
    expect(camera.centreMm.y).toBeCloseTo((-115 + 12_192) / 2, 6);
    // …and it is zoomed out far enough to actually contain it.
    expect(camera.mmPerPx * 840).toBeGreaterThanOrEqual(12_192 + 115);
  });

  it('fits the active storey, which is a different view from fit all', () => {
    mount(panel());
    click(builtIn('Fit all'));
    const all = captureCamera(core.viewport) as Saved2dCamera;
    click(builtIn('Fit storey'));
    const storey = captureCamera(core.viewport) as Saved2dCamera;

    expect(sameCamera(all, storey)).toBe(false);
    expect(storey.centreMm).toEqual({ x: 3000, y: 2000 });
    // Closer in: the storey is smaller than the plot.
    expect(storey.mmPerPx).toBeLessThan(all.mmPerPx);
  });
});

describe('managing the list', () => {
  it('renames a view and remembers the new name', () => {
    mount(panel());
    saveCurrentAs('Kitchn');

    click(one(/^Rename Kitchn$/));
    const input = container.querySelector<HTMLInputElement>('input[aria-label="Rename Kitchn"]');
    expect(input).not.toBe(null);
    if (input === null) return;
    typeInto(input, 'Kitchen detail');
    pressKey(input, 'Enter');

    expect(rowNames()).toEqual(['Kitchen detail']);
    expect((readViews(SCOPE) ?? []).map((view) => view.name)).toEqual(['Kitchen detail']);
  });

  it('abandons a rename on Escape', () => {
    mount(panel());
    saveCurrentAs('Kitchen');
    click(one(/^Rename Kitchen$/));
    const input = container.querySelector<HTMLInputElement>('input[aria-label="Rename Kitchen"]');
    if (input === null) throw new Error('no rename input');
    typeInto(input, 'Something else');
    pressKey(input, 'Escape');
    expect(rowNames()).toEqual(['Kitchen']);
  });

  it('reorders with the arrows, and persists the order', () => {
    mount(panel());
    saveCurrentAs('A');
    saveCurrentAs('B');
    saveCurrentAs('C');
    expect(rowNames()).toEqual(['A', 'B', 'C']);

    click(one(/^Move C up$/));
    expect(rowNames()).toEqual(['A', 'C', 'B']);
    click(one(/^Move A down$/));
    expect(rowNames()).toEqual(['C', 'A', 'B']);
    expect((readViews(SCOPE) ?? []).map((view) => view.name)).toEqual(['C', 'A', 'B']);

    // The ends are dead: the first cannot go up, the last cannot go down.
    expect(one(/^Move C up$/).disabled).toBe(true);
    expect(one(/^Move B down$/).disabled).toBe(true);
  });

  it('takes two clicks to delete, so a mis-click costs nothing', () => {
    mount(panel());
    saveCurrentAs('Kitchen');

    click(one(/^Delete Kitchen$/));
    expect(rowNames()).toEqual(['Kitchen']); // armed, not gone

    click(one(/^Confirm delete Kitchen$/));
    expect(rowNames()).toEqual([]);
    expect(readViews(SCOPE)).toEqual([]);
  });

  it('disarms a primed delete when anything else is clicked', () => {
    mount(panel());
    saveCurrentAs('A');
    saveCurrentAs('B');

    click(one(/^Delete A$/));
    expect(buttons(/^Confirm delete A$/)).toHaveLength(1);

    click(one(/^Restore B$/));
    expect(buttons(/^Confirm delete A$/)).toHaveLength(0);
    expect(rowNames()).toEqual(['A', 'B']);
  });
});

describe('before the canvas exists', () => {
  it('disables every control instead of offering one that cannot work', () => {
    act(() => {
      root.render(<ViewsPanel projectId={PROJECT_ID} core={null} />);
    });
    expect(one(/^Save the current view$/).disabled).toBe(true);
    expect(builtIn('Fit all').disabled).toBe(true);
  });
});
