/**
 * The panel, driven by real clicks and real keystrokes on a real DOM.
 *
 * `createRoot` into jsdom and `element.click()` / a native value setter plus a
 * dispatched `input` event — no testing library, because this workspace does
 * not have one and a hand-rolled shallow renderer would be testing my own
 * harness. What comes out is stronger anyway: React's own event system delivers
 * the event, the store's real action runs, and the assertion is made against
 * the rows the browser would actually show.
 *
 * The corpus is the real one (`fixtures/catalog/*.json`, 469 + 184), so "search
 * finds it" means "found among 653", not "found among the six I wrote".
 *
 * ════════════════════════════════════════════════════════════════════════════
 * NEGATIVE CONTROLS RUN FOR THE WHOLE FEATURE
 * ════════════════════════════════════════════════════════════════════════════
 * Each break below was applied, `vitest run src/features/assets/` was run, the
 * failures were observed, and the change was reverted. The A–E counts were
 * taken at an 86-test baseline, before the four wiring cases at the bottom of
 * this file were added; the suite is 90 green now.
 *
 *   A. `search.ts` `termScore` — delete the `term.mm` dimension branch
 *        Tests  5 failed | 63 passed   (the four dimension cases in
 *        search.test.ts, plus "search by dimension finds the 1800 mm wardrobe
 *        among 653" here)
 *
 *   B. `filters.ts` `passesFilters` — delete the depth comparison
 *        Tests  12 failed | 74 passed  (every compose case, both empty-state
 *        cases that blame the depth limit, and the four DOM filter cases)
 *
 *   C. `persist.ts` `writeFavourites` — make it a no-op
 *        Tests  4 failed | 82 passed   (including "a pin survives a remount
 *        and a fresh store" here, which is the one that matters: the in-memory
 *        store alone would have kept it)
 *
 *   D. `filters.ts` `explainEmpty` — return a fixed "No results." reason
 *        Tests  7 failed | 79 passed   (both empty-state cases here)
 *
 *   E. `persist.ts` `safeStorage` — delete the try/catch
 *        Tests  4 failed | 82 passed   (both throwing-storage cases here)
 *
 *   F. `AssetRow.tsx` `onDragStart` — stop calling `setFurnitureDragPayload`
 *        Tests  1 failed | 18 passed (19, this file alone)
 *        This is the bug-class-4 control: the row still LOOKS registered —
 *        `draggable` is set, the handler is attached, the panel renders — and
 *        only reading the payload back through the canvas's own
 *        `readFurnitureDragPayload` catches it.
 */

import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../../lib/api';
import { readFurnitureDragPayload } from '../canvas/furniture/dnd';
import { resetFurnitureCatalogueCache } from '../canvas/furniture/useFurnitureCatalogue';
import { resetMaterialsCatalogueCache } from '../canvas/materials/useMaterialsCatalogue';
import { AssetBrowser } from './AssetBrowser';
import { AssetBrowserView } from './AssetBrowserView';
import { FURNITURE, FURNITURE_ITEMS, INDEX, MATERIALS } from './catalog.fixture';
import { favouritesKey } from './persist';
import { useSessionStore } from '../../stores/session';
import { resetAssetBrowserStore, useAssetBrowserStore } from './store';

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

function unmount(): void {
  act(() => {
    root.unmount();
  });
}

/**
 * Type into a controlled input the way a user does.
 *
 * React installs its own `value` setter on the element, so assigning
 * `input.value` directly is swallowed and no `input` event carries the new
 * text. Reaching for the PROTOTYPE setter and calling it with the element as
 * the receiver is the documented way round that; `unbound-method` is disabled
 * for exactly that line, because an explicit receiver is the whole point.
 */
function type(input: HTMLInputElement, value: string): void {
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  act(() => {
    setter?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

function choose(select: HTMLSelectElement, value: string): void {
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
  act(() => {
    setter?.call(select, value);
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

function click(element: Element): void {
  act(() => {
    (element as HTMLElement).click();
  });
}

function byLabel<T extends Element>(label: string): T {
  const found = container.querySelector<T>(`[aria-label="${label}"]`);
  if (found === null) throw new Error(`no element labelled "${label}"`);
  return found;
}

/** The names in the rendered list, in order. */
function rowNames(): string[] {
  return [...container.querySelectorAll('li > button:first-child')].map((button) =>
    (button.querySelector('span > span')?.textContent ?? '').trim(),
  );
}

function countText(): string {
  return container.querySelector('[aria-live="polite"]')?.textContent ?? '';
}

function pinButtons(): HTMLButtonElement[] {
  return [...container.querySelectorAll<HTMLButtonElement>('button[aria-label^="Pin "]')];
}

/**
 * The empty state's DESCRIPTION, scoped to the EmptyState block.
 *
 * Deliberately not `container.textContent`: the access-strip checkbox label
 * also says "access strip", so a whole-container match would let
 * `toContain('access strip')` pass without any empty state on screen at all.
 */
function emptyStateText(): string {
  const heading = [...container.querySelectorAll('h2')].find(
    (h) => h.textContent === 'Nothing matches' || h.textContent === 'Could not load the library',
  );
  return heading?.parentElement?.querySelector('p')?.textContent ?? '';
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.localStorage.clear();
  resetAssetBrowserStore();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  try {
    unmount();
  } catch {
    // Already unmounted by the test.
  }
  // After the unmount, so a store write cannot re-render a live component and
  // trip React's act warning.
  useSessionStore.setState({ status: 'anonymous', user: null });
  container.remove();
  globalThis.localStorage.clear();
  resetAssetBrowserStore();
  resetFurnitureCatalogueCache();
  resetMaterialsCatalogueCache();
});

// ---------------------------------------------------------------------------

describe('search, through the real input', () => {
  it('finds an item by name', () => {
    mount(<AssetBrowserView index={INDEX} status="ready" />);
    type(byLabel<HTMLInputElement>('Search the asset library'), 'kota');
    expect(rowNames()).toContain('Kota stone');
  });

  it('search by dimension finds the 1800 mm wardrobe among 653', () => {
    mount(<AssetBrowserView index={INDEX} status="ready" />);
    expect(countText()).toBe('653 of 653 items');

    type(byLabel<HTMLInputElement>('Search the asset library'), 'wardrobe 1800');

    const names = rowNames();
    expect(names).toContain('Wardrobe, hinged 1800 mm');
    // The one whose NAME does not carry 1800 — only its width does. This is the
    // row that disappears if the dimension path is removed.
    expect(names).toContain('Wardrobe (3 door)');
    expect(names).not.toContain('Wardrobe, hinged 1200 mm');
    expect(countText()).toBe(`${String(names.length)} of 653 items`);
  });

  it('reports the true match count, not the number of rows it mounted', () => {
    mount(<AssetBrowserView index={INDEX} status="ready" />);
    // 653 matches, capped rendering. The count line must still say 653.
    expect(countText()).toBe('653 of 653 items');
    expect(rowNames().length).toBeLessThan(653);
    const more = [...container.querySelectorAll('button')].find((b) =>
      (b.textContent ?? '').startsWith('Show '),
    );
    expect(more).toBeDefined();
    if (more !== undefined) {
      const before = rowNames().length;
      click(more);
      expect(rowNames().length).toBeGreaterThan(before);
    }
  });
});

describe('filters compose, through the real controls', () => {
  it('category AND fits-in-900 narrows further than either alone', () => {
    mount(<AssetBrowserView index={INDEX} status="ready" />);

    choose(byLabel<HTMLSelectElement>('Filter by category'), 'furniture:storage');
    expect(countText()).toBe('91 of 653 items');

    type(byLabel<HTMLInputElement>('Maximum depth, front to back'), '900');
    expect(countText()).toBe('26 of 653 items');

    // Every rendered row really does fit, access strip included.
    for (const name of rowNames()) {
      const item = FURNITURE.find((candidate) => candidate.name === name);
      expect(item).toBeDefined();
      if (item !== undefined) expect(item.depthMm + item.clearanceMm).toBeLessThanOrEqual(900);
    }
  });

  it('the depth filter is a real gate — the access strip switch moves the number', () => {
    mount(<AssetBrowserView index={INDEX} status="ready" />);
    type(byLabel<HTMLInputElement>('Maximum depth, front to back'), '900');
    expect(countText()).toBe('111 of 653 items');

    click(byLabel<HTMLInputElement>('Count the access strip in front'));
    expect(countText()).toBe('371 of 653 items');
  });

  it('accepts a depth typed in metres, because lib/units parses it', () => {
    mount(<AssetBrowserView index={INDEX} status="ready" />);
    type(byLabel<HTMLInputElement>('Maximum depth, front to back'), '0.9m');
    expect(countText()).toBe('111 of 653 items');
  });

  it('ignores a number below the smallest real dimension, and applies the rest', () => {
    mount(<AssetBrowserView index={INDEX} status="ready" />);
    const depth = byLabel<HTMLInputElement>('Maximum depth, front to back');
    // 9 mm is below anything in the catalogue: applying it would blank the list
    // on the way to typing "900", so it does not apply at all.
    type(depth, '9');
    expect(countText()).toBe('653 of 653 items');
    // 90 mm is a real limit and is applied as one — eight wall-mounted items.
    type(depth, '90');
    expect(countText()).toBe('8 of 653 items');
    type(depth, '900');
    expect(countText()).toBe('111 of 653 items');
    // Emptying the box turns the filter off again.
    type(depth, '');
    expect(countText()).toBe('653 of 653 items');
  });

  it('composes the search with the filters', () => {
    mount(<AssetBrowserView index={INDEX} status="ready" />);
    choose(byLabel<HTMLSelectElement>('Filter by category'), 'furniture:storage');
    type(byLabel<HTMLInputElement>('Search the asset library'), 'wardrobe');
    const names = rowNames();
    expect(names.length).toBeGreaterThan(0);
    for (const name of names) {
      const item = FURNITURE.find((candidate) => candidate.name === name);
      expect(item?.category).toBe('storage');
    }
  });
});

describe('favourites', () => {
  it('a pin survives a remount and a fresh store', () => {
    act(() => {
      useAssetBrowserStore.getState().bind('user_a');
    });
    mount(<AssetBrowserView index={INDEX} status="ready" />);
    type(byLabel<HTMLInputElement>('Search the asset library'), 'kota stone');

    const pin = pinButtons()[0];
    expect(pin).toBeDefined();
    if (pin === undefined) return;
    const label = pin.getAttribute('aria-label');
    click(pin);
    expect(container.querySelector(`[aria-label="${String(label)}"]`)).toBeNull();

    // Simulate a page reload: throw the store away entirely, then rebind.
    unmount();
    resetAssetBrowserStore();
    expect(useAssetBrowserStore.getState().favourites).toEqual([]);
    act(() => {
      useAssetBrowserStore.getState().bind('user_a');
    });
    root = createRoot(container);
    mount(<AssetBrowserView index={INDEX} status="ready" />);

    act(() => {
      useAssetBrowserStore.getState().patchFilters({ scope: 'favourites' });
    });
    expect(rowNames()).toEqual(['Kota stone']);
    expect(globalThis.localStorage.getItem(favouritesKey('user_a'))).toContain('kota-stone');
  });

  it('keeps one user pins out of another user session', () => {
    act(() => {
      useAssetBrowserStore.getState().bind('user_a');
    });
    mount(<AssetBrowserView index={INDEX} status="ready" />);
    type(byLabel<HTMLInputElement>('Search the asset library'), 'kota stone');
    const pin = pinButtons()[0];
    if (pin === undefined) throw new Error('no pin button rendered');
    click(pin);

    act(() => {
      useAssetBrowserStore.getState().bind('user_b');
      useAssetBrowserStore.getState().patchFilters({ scope: 'favourites' });
    });
    expect(rowNames()).toEqual([]);
    expect(emptyStateText()).toContain('not pinned anything yet');
  });

  it('recently used records what was activated, most recent first', () => {
    mount(<AssetBrowserView index={INDEX} status="ready" />);
    type(byLabel<HTMLInputElement>('Search the asset library'), 'kota stone');
    const firstRow = container.querySelector('li > button:first-child');
    expect(firstRow).not.toBeNull();
    if (firstRow !== null) click(firstRow);

    type(byLabel<HTMLInputElement>('Search the asset library'), 'italian marble');
    const secondRow = container.querySelector('li > button:first-child');
    if (secondRow !== null) click(secondRow);

    type(byLabel<HTMLInputElement>('Search the asset library'), '');
    act(() => {
      useAssetBrowserStore.getState().patchFilters({ scope: 'recent' });
    });
    expect(rowNames()).toEqual(['Italian marble', 'Kota stone']);
  });
});

describe('a localStorage that throws', () => {
  /** Shadow `globalThis.localStorage` with an accessor that throws, as framed Chrome does. */
  function withThrowingStorage(fn: () => void): void {
    const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      get(): Storage {
        throw new Error('SecurityError: storage is disabled');
      },
    });
    try {
      fn();
    } finally {
      if (original === undefined) delete (globalThis as { localStorage?: Storage }).localStorage;
      else Object.defineProperty(globalThis, 'localStorage', original);
    }
  }

  it('renders and pins with a localStorage that throws on access', () => {
    withThrowingStorage(() => {
      act(() => {
        useAssetBrowserStore.getState().bind('user_a');
      });
      mount(<AssetBrowserView index={INDEX} status="ready" />);
      expect(countText()).toBe('653 of 653 items');

      type(byLabel<HTMLInputElement>('Search the asset library'), 'kota stone');
      const pin = pinButtons()[0];
      expect(pin).toBeDefined();
      if (pin === undefined) return;

      // The click must not throw out of the handler and unmount the panel.
      click(pin);
      expect(rowNames()).toContain('Kota stone');
      expect(useAssetBrowserStore.getState().favourites).toEqual(['material:kota-stone']);

      // …and it still works for the rest of the session, in memory.
      act(() => {
        useAssetBrowserStore.getState().patchFilters({ scope: 'favourites' });
      });
      type(byLabel<HTMLInputElement>('Search the asset library'), '');
      expect(rowNames()).toEqual(['Kota stone']);
    });
  });

  it('starts from nothing when storage returns nothing', () => {
    withThrowingStorage(() => {
      act(() => {
        useAssetBrowserStore.getState().bind('user_a');
      });
      expect(useAssetBrowserStore.getState().favourites).toEqual([]);
      expect(useAssetBrowserStore.getState().recents).toEqual([]);
      mount(<AssetBrowserView index={INDEX} status="ready" />);
      expect(countText()).toBe('653 of 653 items');
    });
  });
});

describe('the empty state', () => {
  it('says which filter to change, and the fix works', () => {
    mount(<AssetBrowserView index={INDEX} status="ready" />);
    choose(byLabel<HTMLSelectElement>('Filter by category'), 'furniture:vehicle');
    type(byLabel<HTMLInputElement>('Maximum depth, front to back'), '900');

    expect(countText()).toBe('0 of 653 items');
    expect(emptyStateText()).toContain('access strip');

    const fix = [...container.querySelectorAll('button')].find((button) =>
      (button.textContent ?? '').includes('Clear the depth limit'),
    );
    expect(fix).toBeDefined();
    if (fix === undefined) return;
    click(fix);

    expect(countText()).not.toBe('0 of 653 items');
    expect(rowNames().length).toBeGreaterThan(0);
    // The fix cleared the field as well as the filter — a number left sitting
    // in a box that no longer filters is its own bug.
    expect(byLabel<HTMLInputElement>('Maximum depth, front to back').value).toBe('');
  });

  it('quotes the search text when the search is the culprit', () => {
    mount(<AssetBrowserView index={INDEX} status="ready" />);
    type(byLabel<HTMLInputElement>('Search the asset library'), 'zzzzzz');
    expect(countText()).toBe('0 of 653 items');
    expect(emptyStateText()).toContain('zzzzzz');
  });
});

describe('the row actually calls the things it claims to', () => {
  it('hands the integrator the record it activated', () => {
    const used: string[] = [];
    mount(
      <AssetBrowserView
        index={INDEX}
        status="ready"
        onUse={(record) => {
          used.push(record.key);
        }}
      />,
    );
    type(byLabel<HTMLInputElement>('Search the asset library'), 'kota stone');
    const firstRow = container.querySelector('li > button:first-child');
    expect(firstRow).not.toBeNull();
    if (firstRow !== null) click(firstRow);
    expect(used).toEqual(['material:kota-stone']);
  });

  it('writes the canvas drag payload the drop handler already reads', () => {
    mount(<AssetBrowserView index={INDEX} status="ready" />);
    type(byLabel<HTMLInputElement>('Search the asset library'), 'wardrobe hinged 1800');

    const row = container.querySelector<HTMLButtonElement>('li > button:first-child');
    expect(row).not.toBeNull();
    if (row === null) return;
    expect(row.draggable).toBe(true);

    // jsdom has no DataTransfer, so stand one up with the three members the
    // payload writer touches, and read it back through the CANVAS's own
    // `readFurnitureDragPayload`. This is the assertion that a module which
    // merely believes it is registered cannot pass.
    const data = new Map<string, string>();
    const transfer = {
      setData: (format: string, value: string) => data.set(format, value),
      getData: (format: string) => data.get(format) ?? '',
      types: [] as string[],
      effectAllowed: 'none',
    };
    const event = new Event('dragstart', { bubbles: true });
    Object.defineProperty(event, 'dataTransfer', { value: transfer });
    act(() => {
      row.dispatchEvent(event);
    });

    expect(transfer.effectAllowed).toBe('copy');
    expect(readFurnitureDragPayload(transfer as unknown as DataTransfer)).toBe(
      'wardrobe-hinged-1800',
    );
  });

  it('does not offer a material as a canvas drag — there is nothing to place', () => {
    mount(<AssetBrowserView index={INDEX} status="ready" />);
    type(byLabel<HTMLInputElement>('Search the asset library'), 'kota stone');
    const row = container.querySelector<HTMLButtonElement>('li > button:first-child');
    expect(row?.draggable).toBe(false);
  });
});

describe('the connected browser', () => {
  it('reads the catalogues through the existing api client, once each', async () => {
    const furniture = vi
      .spyOn(api.catalog, 'furniture')
      .mockResolvedValue({ items: [...FURNITURE_ITEMS], nextCursor: null, hasMore: false });
    const materials = vi
      .spyOn(api.catalog, 'materials')
      .mockResolvedValue({ items: [...MATERIALS], nextCursor: null, hasMore: false });

    act(() => {
      root.render(<AssetBrowser userId="user_a" />);
    });
    // Two turns: one for each catalogue promise to settle into state.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(furniture).toHaveBeenCalledTimes(1);
    expect(materials).toHaveBeenCalledTimes(1);
    expect(countText()).toBe('653 of 653 items');
    expect(rowNames().length).toBeGreaterThan(0);
    expect(useAssetBrowserStore.getState().userId).toBe('user_a');
  });

  it('binds the signed-in user when no id is passed', async () => {
    vi.spyOn(api.catalog, 'furniture').mockResolvedValue({
      items: [...FURNITURE_ITEMS],
      nextCursor: null,
      hasMore: false,
    });
    vi.spyOn(api.catalog, 'materials').mockResolvedValue({
      items: [...MATERIALS],
      nextCursor: null,
      hasMore: false,
    });
    useSessionStore.setState({
      status: 'authenticated',
      user: {
        id: 'user_signed_in',
        email: 'a@example.com',
        name: 'A',
        role: 'member',
        coaNumber: null,
      },
    });

    act(() => {
      root.render(<AssetBrowser />);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(useAssetBrowserStore.getState().userId).toBe('user_signed_in');
  });
});
