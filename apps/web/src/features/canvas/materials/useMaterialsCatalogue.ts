/**
 * useMaterialsCatalogue.ts — `GET /catalog/materials`, once per session.
 *
 * Same shape and same reasoning as `furniture/useFurnitureCatalogue.ts`: the
 * catalogue is static product data behind an ETag'd, hour-cached endpoint, so
 * the cache holds a module-level PROMISE (two panels mounting in one tick
 * share one request) and a failed load clears it so retry really retries.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import { api } from '../../../lib/api';
import { AppError } from '../../../lib/errors';
import type { MaterialItem } from '../../../lib/schemas';

export type MaterialsLoadable =
  | { readonly state: 'loading' }
  | { readonly state: 'error'; readonly error: AppError }
  | { readonly state: 'ready'; readonly data: readonly MaterialItem[] };

let cached: Promise<readonly MaterialItem[]> | null = null;

/** Fetch (or reuse) the material catalogue. Exported for tests and prefetch. */
export function loadMaterialsCatalogue(): Promise<readonly MaterialItem[]> {
  if (cached === null) {
    cached = api.catalog
      .materials()
      .then((page) => page.items)
      .catch((err: unknown) => {
        cached = null;
        throw err;
      });
  }
  return cached;
}

/** Drop the cache — after sign-out, or from a "reload" action. */
export function resetMaterialsCatalogueCache(): void {
  cached = null;
}

export interface MaterialsCatalogue {
  readonly loadable: MaterialsLoadable;
  /** `materialId -> item`. Empty until loaded; never null. */
  readonly index: ReadonlyMap<string, MaterialItem>;
  readonly reload: () => void;
}

export function useMaterialsCatalogue(): MaterialsCatalogue {
  const [loadable, setLoadable] = useState<MaterialsLoadable>({ state: 'loading' });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let live = true;
    setLoadable({ state: 'loading' });

    loadMaterialsCatalogue().then(
      (items) => {
        if (live) setLoadable({ state: 'ready', data: items });
      },
      (err: unknown) => {
        const error = AppError.from(err);
        if (live && !error.isAborted) setLoadable({ state: 'error', error });
      },
    );

    return () => {
      live = false;
    };
  }, [nonce]);

  const reload = useCallback(() => {
    resetMaterialsCatalogueCache();
    setNonce((n) => n + 1);
  }, []);

  const index = useMemo(() => {
    if (loadable.state !== 'ready') return EMPTY_INDEX;
    const map = new Map<string, MaterialItem>();
    for (const item of loadable.data) map.set(item.id, item);
    return map as ReadonlyMap<string, MaterialItem>;
  }, [loadable]);

  return useMemo(() => ({ loadable, index, reload }), [loadable, index, reload]);
}

const EMPTY_INDEX: ReadonlyMap<string, MaterialItem> = new Map();
