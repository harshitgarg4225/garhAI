/**
 * useFurnitureItems — `GET /catalog/furniture`, in the shape the TOOL layer
 * wants.
 *
 * There are two consumers of the same endpoint and they want two different
 * types, which is why this exists alongside `useFurnitureCatalogue`:
 *
 *   `features/canvas/furniture` normalises the wire rows into `CatalogueItem`
 *   (category narrowed to its own union, clearance resolved, readonly arrays)
 *   for the browser panel, collision and the box proxies.
 *
 *   `features/canvas/tools` — `useToolController` and `ToolOptionsBar` — takes
 *   the raw `FurnitureItem` from `lib/schemas`. `CatalogueItem` is *nearly*
 *   assignable to it and deliberately is not: `roomTypes` is `readonly
 *   string[]` there and `string[]` here, so passing one for the other is a
 *   type error rather than a silent widening.
 *
 * Rather than cast between them (which would hide the day the two genuinely
 * diverge), this hook keeps the parsed rows exactly as `lib/api` returned them.
 * One extra request per session at worst — the response is a small, cacheable
 * reference list served with cache headers, and both hooks hit the same URL.
 *
 * Loading is not an error and an error is not an empty catalogue: the tool
 * options bar shows "Loading catalogue…" for the first, and the furniture tool
 * simply declines to place anything for the second, rather than placing a
 * zero-sized box.
 */

import { useEffect, useMemo, useState } from 'react';

import { api } from '../../../lib/api';
import { AppError } from '../../../lib/errors';
import type { FurnitureItem } from '../../../lib/schemas';

export interface FurnitureItems {
  readonly items: readonly FurnitureItem[];
  readonly itemsById: ReadonlyMap<string, FurnitureItem>;
  readonly loading: boolean;
  readonly error: AppError | null;
}

const EMPTY_ITEMS: readonly FurnitureItem[] = [];

/** Session cache. The catalogue is immutable within a session (§11). */
let cached: Promise<FurnitureItem[]> | null = null;

function load(): Promise<FurnitureItem[]> {
  if (cached === null) {
    cached = api.catalog
      .furniture()
      .then((page) => page.items)
      .catch((err: unknown) => {
        // Do not cache a failure: a flaky network on first load must not leave
        // the furniture tool permanently empty for the rest of the session.
        cached = null;
        throw err;
      });
  }
  return cached;
}

/** Drop the cache. Exported for tests and for a "retry" affordance. */
export function resetFurnitureItemsCache(): void {
  cached = null;
}

export function useFurnitureItems(): FurnitureItems {
  const [items, setItems] = useState<readonly FurnitureItem[]>(EMPTY_ITEMS);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<AppError | null>(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    load().then(
      (rows) => {
        if (!live) return;
        setItems(rows);
        setError(null);
        setLoading(false);
      },
      (err: unknown) => {
        if (!live) return;
        setError(AppError.from(err));
        setLoading(false);
      },
    );
    return () => {
      live = false;
    };
  }, []);

  const itemsById = useMemo(() => new Map(items.map((item) => [item.id, item])), [items]);

  return { items, itemsById, loading, error };
}
