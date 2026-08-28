/**
 * HatchBindingPanel.test.tsx — the wiring, end to end, in a real DOM.
 *
 * This is the spec that answers "is the feature actually connected?", so it
 * uses the REAL model store, the REAL override store and the REAL resolver,
 * and mocks exactly one thing: `useMaterialsCatalogue`, which is a network
 * fetch. Everything else is the product.
 *
 * What it walks is the A-10 promise and the A-9 escape hatch, in the order an
 * architect meets them:
 *   1. no material            → the surface's own default
 *   2. brick assigned (op 29) → BRICK, with the material named
 *   3. a pattern picked       → the override wins over the material
 *   4. the panel re-renders   → the override is still there
 *   5. "Follow the material"  → back to BRICK
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  makeTwoRoomPlanWithOpenings,
  type MaterialAssignment,
  type MaterialAssignmentId,
  type ProjectDoc,
} from '@garh/model';

import type { MaterialItem } from '../../lib/schemas';

const ITEMS: readonly MaterialItem[] = [
  {
    id: 'exposed-brick',
    name: 'Exposed brick',
    category: 'wall',
    colorHex: '#9C5B3C',
    textureUrl: null,
    surfaceGroups: ['wall.exterior'],
  },
];
const INDEX: ReadonlyMap<string, MaterialItem> = new Map(ITEMS.map((item) => [item.id, item]));

// The one seam: the catalogue arrives over the network. Everything else in
// this file is the real thing, on purpose.
vi.mock('../canvas/materials/useMaterialsCatalogue', () => ({
  useMaterialsCatalogue: () => ({
    loadable: { state: 'ready', data: ITEMS },
    index: INDEX,
    reload: () => undefined,
  }),
  loadMaterialsCatalogue: () => Promise.resolve(ITEMS),
  resetMaterialsCatalogueCache: () => undefined,
}));

const { HatchBindingPanel } = await import('./HatchBindingPanel');
const { useModelStore } = await import('../../stores/model');
const { useUiStore } = await import('../../stores/ui');
const { useHatchOverrideStore } = await import('./store');
const { hatchTargetKey } = await import('./resolve');

declare global {
  // React 18 reads this to decide whether `act` warnings apply.
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const BASE = makeTwoRoomPlanWithOpenings();

const BRICK_ON_WALLS: MaterialAssignment = {
  id: 'mat_01J00000000000000000000B' as MaterialAssignmentId,
  target: { group: 'external_wall', storeyId: null, elementId: null },
  materialId: 'exposed-brick',
};

function docWith(materials: readonly MaterialAssignment[]): ProjectDoc {
  return { ...BASE, house: { ...BASE.house, materials } };
}

let container: HTMLDivElement;
let root: Root;

function mount(): void {
  act(() => {
    root.render(<HatchBindingPanel />);
  });
}

function click(element: Element | null): void {
  expect(element).not.toBeNull();
  act(() => {
    (element as HTMLElement).click();
  });
}

const text = (testid: string): string =>
  container.querySelector(`[data-testid="${testid}"]`)?.textContent ?? '';

const tile = (pattern: string): Element | null =>
  container.querySelector(`button[data-pattern="${pattern}"]`);

const byLabel = (pattern: RegExp): HTMLButtonElement[] =>
  [...container.querySelectorAll('button')].filter((b) => pattern.test(b.textContent ?? ''));

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  useModelStore.setState({ doc: docWith([]), projectId: 'proj_hatch' });
  useUiStore.setState({ activeStoreyId: null });
  useHatchOverrideStore.setState({ projectId: null, overrides: new Map() });
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

describe('the panel binds materials to hatches', () => {
  it('starts on the surface default when nothing is assigned', () => {
    mount();
    expect(text('resolved-pattern')).toBe('Diagonal / generic section');
    expect(text('resolved-why')).toContain('No material assigned');
    expect(tile('diagonal')?.getAttribute('aria-pressed')).toBe('true');
  });

  it('follows the material assigned by op 29, and names it', () => {
    useModelStore.setState({ doc: docWith([BRICK_ON_WALLS]) });
    mount();
    expect(text('resolved-pattern')).toBe('Brick masonry');
    expect(text('resolved-why')).toContain('Exposed brick');
    expect(tile('brick')?.getAttribute('aria-pressed')).toBe('true');
    // The tile is also marked as the material's own choice.
    expect(tile('brick')?.getAttribute('aria-label')).toContain('from the material');
  });

  it('lets a picked pattern override the material, and survives a re-render', () => {
    useModelStore.setState({ doc: docWith([BRICK_ON_WALLS]) });
    mount();
    expect(text('resolved-pattern')).toBe('Brick masonry');

    click(tile('stone'));
    expect(text('resolved-pattern')).toBe('Stone masonry');
    expect(text('resolved-why')).toContain('by hand');
    // It reached the store under the same target a material assignment uses.
    expect(
      useHatchOverrideStore
        .getState()
        .overrides.get(hatchTargetKey({ group: 'external_wall', storeyId: null, elementId: null }))
        ?.pattern,
    ).toBe('stone');

    // Re-render the whole panel — a parent re-render, a tab switch back, a
    // store update elsewhere. The choice must still be the architect's.
    mount();
    expect(text('resolved-pattern')).toBe('Stone masonry');
    expect(tile('stone')?.getAttribute('aria-pressed')).toBe('true');
    // …and the material's own implication is still shown as the alternative.
    expect(tile('brick')?.getAttribute('aria-label')).toContain('from the material');
  });

  it('goes back to the material when the override is cleared', () => {
    useModelStore.setState({ doc: docWith([BRICK_ON_WALLS]) });
    mount();
    click(tile('grass'));
    expect(text('resolved-pattern')).toBe('Grass / soft landscape');

    const follow = byLabel(/Follow the material/);
    expect(follow).toHaveLength(1);
    click(follow[0] ?? null);
    expect(text('resolved-pattern')).toBe('Brick masonry');
    expect(useHatchOverrideStore.getState().overrides.size).toBe(0);
  });

  it('scopes an override to the active storey when asked', () => {
    const storeyId = BASE.house.storeys[0]?.id ?? null;
    expect(storeyId).not.toBeNull();
    useUiStore.setState({ activeStoreyId: storeyId });
    useModelStore.setState({ doc: docWith([BRICK_ON_WALLS]) });
    mount();

    click(byLabel(/This storey/)[0] ?? null);
    click(tile('stone'));

    const overrides = useHatchOverrideStore.getState().overrides;
    expect(overrides.size).toBe(1);
    expect([...overrides.values()][0]?.target.storeyId).toBe(storeyId);

    // Back to the building scope: the storey override must NOT apply there.
    click(byLabel(/Whole building/)[0] ?? null);
    expect(text('resolved-pattern')).toBe('Brick masonry');
  });

  it('offers every surface group, and switching group switches the answer', () => {
    useModelStore.setState({ doc: docWith([BRICK_ON_WALLS]) });
    mount();
    const tabs = [...container.querySelectorAll('button[role="tab"]')];
    expect(tabs).toHaveLength(12);
    expect(text('resolved-pattern')).toBe('Brick masonry');

    const floors = tabs.find((t) => (t.textContent ?? '') === 'Floors');
    click(floors ?? null);
    // The brick assignment is on the walls; floors keep the slab default.
    expect(text('resolved-pattern')).toBe('Cross hatch');
    expect(text('resolved-why')).toContain('No material assigned');
  });

  it('drops overrides when the project changes underneath it', () => {
    mount();
    click(tile('stone'));
    expect(useHatchOverrideStore.getState().overrides.size).toBe(1);

    act(() => {
      useModelStore.setState({ projectId: 'proj_other' });
    });
    expect(useHatchOverrideStore.getState().overrides.size).toBe(0);
    expect(text('resolved-pattern')).toBe('Diagonal / generic section');
  });
});
