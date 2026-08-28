/**
 * The library, as React state: both catalogues, joined and indexed once.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ONE FETCH PATH, NOT TWO
 * ════════════════════════════════════════════════════════════════════════════
 * This hook does no fetching. It composes the two hooks that already own it —
 * `features/canvas/furniture/useFurnitureCatalogue` and
 * `features/canvas/materials/useMaterialsCatalogue` — each of which caches a
 * module-level PROMISE so the catalogue is fetched once per session no matter
 * how many panels mount. Adding a third `fetch` here would defeat the ETag'd,
 * hour-cached endpoint those two exist to honour, and would give the canvas and
 * this browser two copies of the same list that could disagree.
 *
 * A consequence worth knowing: mounting the asset browser warms the catalogue
 * the furniture tool later needs, and vice versa. They share the cache.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHERE THE MEMOISATION ACTUALLY MATTERS
 * ════════════════════════════════════════════════════════════════════════════
 * `buildIndex` lower-cases, strips punctuation and splits 653 names into words.
 * That is the expensive part of searching, and it depends only on the two
 * catalogue arrays — which, given the promise caches above, change once per
 * session. Both memos below key on the `loadable` objects, not on their
 * contents, so a re-render caused by typing does not rebuild anything.
 *
 * The keystroke path is therefore: filter (integer compares) → score
 * (pre-lowercased strings) → slice. No allocation of the index, ever again.
 */

import { useCallback, useMemo } from 'react';

import { useMaterialsCatalogue } from '../canvas/materials/useMaterialsCatalogue';
import type { CatalogueItem } from '../canvas/furniture/types';
import { useFurnitureCatalogue } from '../canvas/furniture/useFurnitureCatalogue';
import type { MaterialItem } from '../../lib/schemas';
import { toAssetRecords } from './normalise';
import { buildIndex, type SearchEntry } from './search';

/**
 * `loading` until BOTH catalogues answer; `error` if either fails.
 *
 * Deliberately not a partial-success state. A library that silently shows 469
 * furniture items and no materials, with nothing on screen to say the materials
 * request failed, is the "green check that cannot go red" shape: the user reads
 * an empty material category as "there are none".
 */
export type AssetLibraryStatus = 'loading' | 'error' | 'ready';

export interface AssetLibrary {
  readonly status: AssetLibraryStatus;
  /** Empty until `status === 'ready'`; never null, so callers skip a check. */
  readonly index: readonly SearchEntry[];
  /** What to do about the failure, from `AppError.action`. Null unless in error. */
  readonly errorAction: string | null;
  /** Drop both caches and refetch. */
  readonly reload: () => void;
}

const EMPTY_FURNITURE: readonly CatalogueItem[] = [];
const EMPTY_MATERIALS: readonly MaterialItem[] = [];

export function useAssetLibrary(): AssetLibrary {
  const furniture = useFurnitureCatalogue();
  const materials = useMaterialsCatalogue();

  const furnitureLoadable = furniture.loadable;
  const materialsLoadable = materials.loadable;

  const index = useMemo(() => {
    const f = furnitureLoadable.state === 'ready' ? furnitureLoadable.data : EMPTY_FURNITURE;
    const m = materialsLoadable.state === 'ready' ? materialsLoadable.data : EMPTY_MATERIALS;
    return buildIndex(toAssetRecords(f, m));
  }, [furnitureLoadable, materialsLoadable]);

  const status: AssetLibraryStatus =
    furnitureLoadable.state === 'error' || materialsLoadable.state === 'error'
      ? 'error'
      : furnitureLoadable.state === 'loading' || materialsLoadable.state === 'loading'
        ? 'loading'
        : 'ready';

  const errorAction =
    furnitureLoadable.state === 'error'
      ? furnitureLoadable.error.action
      : materialsLoadable.state === 'error'
        ? materialsLoadable.error.action
        : null;

  const furnitureReload = furniture.reload;
  const materialsReload = materials.reload;
  const reload = useCallback(() => {
    furnitureReload();
    materialsReload();
  }, [furnitureReload, materialsReload]);

  // A stable object: consumers put this in dependency arrays, and a fresh
  // literal every render would quietly defeat every memo downstream.
  return useMemo(
    () => ({ status, index, errorAction, reload }),
    [status, index, errorAction, reload],
  );
}
