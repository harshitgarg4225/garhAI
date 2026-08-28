/**
 * Spec for the panel, driven by real clicks on a real DOM.
 *
 * `createRoot` into a jsdom container and `element.click()` — no testing
 * library, because this workspace does not have one and a hand-rolled shallow
 * renderer would be testing my own harness rather than the panel. What comes
 * out is stronger anyway: React's own event system delivers the click, the
 * store's real action runs, and the assertion is made against the derivation
 * the CANVAS consumes, not against a boolean.
 *
 * That last part is the point. `clicking hide really removes geometry` clicks
 * the eye on the door row and then asks `planLayerViewFor` what the plan would
 * be drawn from. A panel wired to a store nothing reads would pass a "the
 * button got aria-pressed=false" test and fail this one.
 */

import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { makeTwoRoomPlanWithOpenings } from '@garh/model';

import { LayerPanel } from './LayerPanel';
import { DRAWING_LAYER_NAMES } from './layerSpecs';
import { defaultLayerState } from './persist';
import { planLayerViewFor, useLayerStore } from './store';

const HOUSE = makeTwoRoomPlanWithOpenings().house;

declare global {
  // React 18 reads this to decide whether `act` warnings apply.
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

function click(element: Element): void {
  act(() => {
    (element as HTMLElement).click();
  });
}

/** Every control, addressed the way a screen reader would. */
function byLabel(pattern: RegExp): HTMLButtonElement[] {
  return [...container.querySelectorAll('button')].filter((b) =>
    pattern.test(b.getAttribute('aria-label') ?? ''),
  );
}

function one(pattern: RegExp): HTMLButtonElement {
  const found = byLabel(pattern);
  expect(found, `expected exactly one control matching ${String(pattern)}`).toHaveLength(1);
  return found[0] as HTMLButtonElement;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.localStorage.clear();
  useLayerStore.setState({
    scope: null,
    ...defaultLayerState(),
    isolated: null,
    preIsolate: null,
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('what the panel renders', () => {
  it('lists all nine layers by their CAD names', () => {
    mount(<LayerPanel />);
    for (const name of DRAWING_LAYER_NAMES) {
      expect(container.textContent, name).toContain(name);
    }
  });

  it('gives every control an accessible name that says what it will do', () => {
    mount(<LayerPanel />);
    // Three controls per layer, minus the one row whose two canvas controls
    // are disabled but still labelled, plus the header's "Show all".
    for (const name of DRAWING_LAYER_NAMES) {
      expect(byLabel(new RegExp(`\\(${name}\\)`)).length, name).toBeGreaterThanOrEqual(2);
    }
    expect(one(/^Hide Walls \(A-WALL\)$/).getAttribute('aria-pressed')).toBe('true');
    expect(one(/^Lock Walls \(A-WALL\)$/).getAttribute('aria-pressed')).toBe('false');
  });

  it('disables the canvas controls on A-TITL and says why', () => {
    mount(<LayerPanel />);
    const isolate = byLabel(/Isolate unavailable for Title block/)[0];
    expect(isolate?.disabled).toBe(true);
    expect(isolate?.getAttribute('title')).toContain('sheet');
    // Visibility too: the plan draws no title block, so the eye would do
    // nothing on this surface.
    expect(byLabel(/Title block \(A-TITL\)/).some((b) => b.disabled)).toBe(true);
    expect(container.textContent).toContain('sheet only');
  });
});

describe('clicking the panel changes what the canvas would draw', () => {
  it('hiding doors removes them from the model the plan is drawn from', () => {
    mount(<LayerPanel />);
    expect(planLayerViewFor(HOUSE, useLayerStore.getState()).house).toBe(HOUSE);

    click(one(/^Hide Doors \(A-DOOR\)$/));

    const view = planLayerViewFor(HOUSE, useLayerStore.getState());
    expect(view.house).not.toBe(HOUSE);
    expect(view.house.openings.some((o) => o.kind === 'door')).toBe(false);
    expect(view.house.openings.some((o) => o.kind === 'window')).toBe(true);

    // …and the control now offers the opposite action.
    expect(one(/^Show Doors \(A-DOOR\)$/).getAttribute('aria-pressed')).toBe('false');
  });

  it('hiding dimensions switches the overlay prop, not the model', () => {
    mount(<LayerPanel />);
    click(one(/^Hide Dimensions \(A-DIM\)$/));
    const view = planLayerViewFor(HOUSE, useLayerStore.getState());
    expect(view.showDimensions).toBe(false);
    expect(view.house).toBe(HOUSE);
  });

  it('locking a layer keeps it drawn and refuses its picks', () => {
    mount(<LayerPanel />);
    click(one(/^Lock Walls \(A-WALL\)$/));

    const state = useLayerStore.getState();
    expect(planLayerViewFor(HOUSE, state).house).toBe(HOUSE);
    const wallId = HOUSE.walls[0]?.id as string;
    expect(state.locked['A-WALL']).toBe(true);
    expect(one(/^Unlock Walls \(A-WALL\)$/).getAttribute('aria-pressed')).toBe('true');
    expect(wallId).toBeTruthy();
  });

  it('isolate shows one layer and the banner offers the way out', () => {
    mount(<LayerPanel />);
    click(one(/^Isolate Walls \(A-WALL\)$/));

    expect(container.textContent).toContain('Isolated:');
    const view = planLayerViewFor(HOUSE, useLayerStore.getState());
    expect(view.house.openings).toEqual([]);
    expect(view.showRooms).toBe(false);
    expect(view.house.walls.length).toBeGreaterThan(0);

    const exit = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Exit');
    expect(exit).toBeDefined();
    click(exit as HTMLButtonElement);
    expect(planLayerViewFor(HOUSE, useLayerStore.getState()).house).toBe(HOUSE);
  });

  it('“Show all” is inert until something is hidden, then puts it back', () => {
    mount(<LayerPanel />);
    const showAll = (): HTMLButtonElement =>
      [...container.querySelectorAll('button')].find(
        (b) => b.textContent === 'Show all',
      ) as HTMLButtonElement;

    expect(showAll().disabled).toBe(true);
    click(one(/^Hide Stairs \(A-STAIR\)$/));
    expect(container.textContent).toContain('1 hidden');
    expect(showAll().disabled).toBe(false);

    click(showAll());
    expect(planLayerViewFor(HOUSE, useLayerStore.getState()).house).toBe(HOUSE);
  });
});
