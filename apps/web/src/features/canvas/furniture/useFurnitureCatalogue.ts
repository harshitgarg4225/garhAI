/**
 * Loading the catalogue: one fetch per session, shared by every consumer.
 *
 * `GET /catalog/furniture` is static product data — the router caches it for an
 * hour and tags it with an ETag precisely so "a firm's browser fetches the
 * furniture catalogue once a session, not once a tool switch"
 * (`apps/api/garh_api/routers/catalog.py`). Honouring that means a
 * module-level promise, not a per-component `useEffect` that re-fires whenever
 * the browser panel mounts.
 *
 * The cache holds the PROMISE, not the result, so two components mounting in
 * the same tick share one request instead of racing. A failed load clears the
 * cache, so retry actually retries rather than re-reading a rejected promise.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import { api } from '../../../lib/api';
import { AppError } from '../../../lib/errors';
import { catalogueIndex, toCatalogue } from './catalogue';
import type { CatalogueItem } from './types';

export type Loadable<T> =
  | { readonly state: 'loading' }
  | { readonly state: 'error'; readonly error: AppError }
  | { readonly state: 'ready'; readonly data: T };

let cached: Promise<CatalogueItem[]> | null = null;

/** Fetch (or reuse) the catalogue. Exported for tests and for a prefetch call. */
export function loadFurnitureCatalogue(): Promise<CatalogueItem[]> {
  if (cached === null) {
    cached = api.catalog
      .furniture()
      .then((page) => toCatalogue(page.items))
      .catch((err: unknown) => {
        cached = null;
        throw err;
      });
  }
  return cached;
}

/** Drop the cache — after a sign-out, or from a "reload catalogue" action. */
export function resetFurnitureCatalogueCache(): void {
  cached = null;
}

export interface FurnitureCatalogue {
  readonly loadable: Loadable<CatalogueItem[]>;
  /** `catalogId -> item`. Empty until loaded; never null, so callers skip a check. */
  readonly index: ReadonlyMap<string, CatalogueItem>;
  readonly reload: () => void;
}

/**
 * The catalogue, as React state.
 *
 * Deliberately does NOT suspend. A furniture browser that suspends takes the
 * whole plan tab down with it while a reference list loads; §15 wants a
 * skeleton in the panel and a working canvas behind it.
 */
export function useFurnitureCatalogue(): FurnitureCatalogue {
  const [loadable, setLoadable] = useState<Loadable<CatalogueItem[]>>({ state: 'loading' });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let live = true;
    setLoadable({ state: 'loading' });

    loadFurnitureCatalogue().then(
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
    resetFurnitureCatalogueCache();
    setNonce((n) => n + 1);
  }, []);

  // Rebuilt only when the item list identity changes — which, given the module
  // cache above, is once per session.
  const index = useMemo(
    () => (loadable.state === 'ready' ? catalogueIndex(loadable.data) : EMPTY_INDEX),
    [loadable],
  );

  // A stable object: consumers put this in dependency arrays, and a fresh
  // literal every render would quietly defeat every memo downstream.
  return useMemo(() => ({ loadable, index, reload }), [loadable, index, reload]);
}

const EMPTY_INDEX: ReadonlyMap<string, CatalogueItem> = new Map();
