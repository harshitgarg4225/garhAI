/**
 * features/assets — the asset browser.
 *
 * ONE registration point. Mount `<AssetBrowser />` anywhere inside the app
 * shell; it needs no provider, no props and no context. It reads the two
 * catalogue hooks that already own the fetch (`useFurnitureCatalogue`,
 * `useMaterialsCatalogue`) and the session store for the user id.
 *
 * ```tsx
 * <AssetBrowser
 *   className="…"
 *   onUse={(record) => {
 *     if (record.kind === 'furniture') arm(record.id);
 *   }}
 * />
 * ```
 *
 * Furniture rows are draggable and write the payload
 * `features/canvas/furniture/dnd.ts` already defines, so a drag onto the plan
 * canvas lands as a real placement with nothing further to wire.
 */

export { AssetBrowser } from './AssetBrowser';
export type { AssetBrowserProps } from './AssetBrowser';
export { AssetBrowserView, PAGE } from './AssetBrowserView';
export type { AssetBrowserViewProps } from './AssetBrowserView';
export { AssetRow } from './AssetRow';
export type { AssetRowProps } from './AssetRow';

export { useAssetLibrary } from './useAssetLibrary';
export type { AssetLibrary, AssetLibraryStatus } from './useAssetLibrary';

export { useAssetBrowserStore, resetAssetBrowserStore } from './store';
export type { AssetBrowserState } from './store';

export { buildIndex, searchEntries, parseQuery } from './search';
export type { SearchEntry, QueryTerm } from './search';

export {
  applyFilters,
  passesFilters,
  orderByRecency,
  facetsFor,
  explainEmpty,
  EMPTY_CONTEXT,
} from './filters';
export type { FilterContext, Facet, Facets, EmptyAdvice } from './filters';

export { toAssetRecords, furnitureRecord, materialRecord } from './normalise';

export {
  DEFAULT_FILTERS,
  hasFootprint,
  hasNarrowingFilters,
  roomTypeLabel,
  MATERIAL_CATEGORIES,
  MATERIAL_CATEGORY_LABELS,
} from './types';
export type { AssetRecord, AssetKind, AssetScope, AssetFilters } from './types';

export {
  readFavourites,
  writeFavourites,
  readRecents,
  writeRecents,
  clearAssetPrefs,
  toggleFavourite,
  pushRecent,
  FAVOURITES_MAX,
  RECENTS_MAX,
} from './persist';
