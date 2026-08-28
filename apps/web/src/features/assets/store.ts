/**
 * store.ts — the asset browser's state: who is browsing, what they pinned, what
 * they used, and how the list is currently narrowed.
 *
 * Zustand and a module store rather than component state, for two reasons that
 * both bite in practice:
 *
 *   1. The browser is mounted and unmounted constantly — it is a tool panel,
 *      and switching tools tears it down. Component state would lose the query
 *      and the filters every time, and would reload the pins from storage on
 *      every mount.
 *   2. More than one surface wants this. A side rail in the plan tab and a
 *      full-page library are the same list; so is a command-palette entry that
 *      sets `scope: 'favourites'` and opens the panel.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY {@link FilterContext} IS A STORED FIELD AND NOT A SELECTOR
 * ════════════════════════════════════════════════════════════════════════════
 * The filter functions want a `Set` and a `Map`. Building them in a selector
 * would hand every render a NEW object, which defeats the `useMemo` that keeps
 * 653 records from being re-filtered on every keystroke — the memo would see a
 * changed dependency every time and the deliberate memoisation in
 * `AssetBrowser.tsx` would quietly become a no-op. So the context is built once
 * per mutation, stored, and handed out by identity.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * BINDING
 * ════════════════════════════════════════════════════════════════════════════
 * `bind(userId)` is called by the component when the signed-in user is known.
 * It loads that user's lists from storage, or empty ones when storage is
 * unavailable. `bind(null)` — a share-link viewer, or before sign-in — leaves
 * the store fully working and in-memory only: pins still toggle for the session
 * and are simply never written. That is the same promise the layer store makes,
 * and it is what keeps the panel usable in a browser with storage switched off.
 */

import { create } from 'zustand';

import { EMPTY_CONTEXT, type FilterContext } from './filters';
import {
  pushRecent,
  readFavourites,
  readRecents,
  toggleFavourite as toggleFavouriteList,
  writeFavourites,
  writeRecents,
} from './persist';
import { DEFAULT_FILTERS, type AssetFilters } from './types';

function contextFor(favourites: readonly string[], recents: readonly string[]): FilterContext {
  const recentOrder = new Map<string, number>();
  recents.forEach((key, index) => recentOrder.set(key, index));
  return { favourites: new Set(favourites), recentOrder };
}

export interface AssetBrowserState {
  /** Whose lists are loaded. `null` = in-memory only; nothing is persisted. */
  readonly userId: string | null;
  /** Pinned asset keys, most recently pinned first. */
  readonly favourites: readonly string[];
  /** Used asset keys, most recently used first. */
  readonly recents: readonly string[];
  /** Stable derived view of the two lists above. See the header. */
  readonly context: FilterContext;
  readonly query: string;
  readonly filters: AssetFilters;

  // ── actions ──────────────────────────────────────────────────────────────
  /** Load a user's lists. Idempotent: re-binding the same id is a no-op. */
  bind: (userId: string | null) => void;
  setQuery: (query: string) => void;
  setFilters: (filters: AssetFilters) => void;
  patchFilters: (patch: Partial<AssetFilters>) => void;
  resetFilters: () => void;
  /** Pin or unpin. Writes through to storage when a user is bound. */
  toggleFavourite: (key: string) => void;
  /** Record a placement. Writes through to storage when a user is bound. */
  noteUsed: (key: string) => void;
}

export const useAssetBrowserStore = create<AssetBrowserState>()((set, get) => ({
  userId: null,
  favourites: [],
  recents: [],
  context: EMPTY_CONTEXT,
  query: '',
  filters: DEFAULT_FILTERS,

  bind: (userId) => {
    if (get().userId === userId) return;
    const favourites = userId === null ? [] : readFavourites(userId);
    const recents = userId === null ? [] : readRecents(userId);
    set({ userId, favourites, recents, context: contextFor(favourites, recents) });
  },

  setQuery: (query) => {
    set({ query });
  },

  setFilters: (filters) => {
    set({ filters });
  },

  patchFilters: (patch) => {
    set({ filters: { ...get().filters, ...patch } });
  },

  resetFilters: () => {
    set({ filters: DEFAULT_FILTERS, query: '' });
  },

  toggleFavourite: (key) => {
    const { userId, favourites, recents } = get();
    const next = toggleFavouriteList(favourites, key);
    set({ favourites: next, context: contextFor(next, recents) });
    if (userId !== null) writeFavourites(userId, next);
  },

  noteUsed: (key) => {
    const { userId, favourites, recents } = get();
    const next = pushRecent(recents, key);
    set({ recents: next, context: contextFor(favourites, next) });
    if (userId !== null) writeRecents(userId, next);
  },
}));

/**
 * Reset to a pristine store. Test support, and the sign-out path — a shared
 * studio machine must not show the previous user's pins after a switch.
 */
export function resetAssetBrowserStore(): void {
  useAssetBrowserStore.setState({
    userId: null,
    favourites: [],
    recents: [],
    context: EMPTY_CONTEXT,
    query: '',
    filters: DEFAULT_FILTERS,
  });
}
