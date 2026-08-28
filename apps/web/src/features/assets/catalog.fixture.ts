/**
 * The REAL catalogue, loaded from disk, for the tests in this folder.
 *
 * `fixtures/catalog/{furniture,materials}.json` is not a convenient stand-in —
 * it is the data `GET /catalog/furniture` and `GET /catalog/materials` actually
 * serve (`apps/api/garh_api/routers/catalog.py` reads that very directory, and
 * `apps/api/tests/test_catalog_fixtures.py` pins it). Testing the search
 * against a six-item hand-written list would prove the ranking works on a list
 * that does not exist; the interesting failures — a term that matches 40 items,
 * a size step that repeats across seven wardrobes — only appear at 469.
 *
 * Materials go through `materialItemSchema` rather than being cast, because
 * that is the boundary the app puts them through: zod strips `finish`,
 * `texture` and `priceInrPerSqm`, and a test that saw those fields would be
 * testing a shape the browser never receives.
 *
 * Node's `fs` here is deliberate and safe: nothing in the runtime graph imports
 * this module, so it never reaches a browser bundle.
 */

import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

import {
  furnitureItemSchema,
  materialItemSchema,
  type FurnitureItem,
  type MaterialItem,
} from '../../lib/schemas';
import { toCatalogue } from '../canvas/furniture/catalogue';
import type { CatalogueItem } from '../canvas/furniture/types';
import { toAssetRecords } from './normalise';
import { buildIndex, type SearchEntry } from './search';
import type { AssetRecord } from './types';

/**
 * Walk up from the working directory to the repo's `fixtures/catalog`.
 *
 * Not `import.meta.url`: Vite serves this module under an `/@fs/` prefix, so a
 * module-relative resolve produces a path that does not exist on disk. Walking
 * up works whether vitest is run from `apps/web` or from the repo root.
 *
 * It THROWS when the directory is missing rather than returning an empty list.
 * A loader that quietly yielded no items would make every assertion below pass
 * vacuously — the precise shape of a test that cannot fail.
 */
function catalogDir(): string {
  let dir = process.cwd();
  for (let depth = 0; depth < 8; depth += 1) {
    const candidate = join(dir, 'fixtures', 'catalog');
    if (existsSync(join(candidate, 'furniture.json'))) return candidate;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error(`fixtures/catalog not found above ${process.cwd()}`);
}

function readJson(name: string): unknown {
  return JSON.parse(readFileSync(join(catalogDir(), name), 'utf8'));
}

/**
 * The two catalogues exactly as the client receives them: through the zod
 * schemas `pageParser` applies at the boundary, so the extra keys the seed file
 * carries (`finish`, `texture`, `priceInrPerSqm`) are stripped here as well.
 * Handed to the api-client spy in `AssetBrowser.test.tsx`.
 */
export const FURNITURE_ITEMS: readonly FurnitureItem[] = (
  readJson('furniture.json') as unknown[]
).map((raw) => furnitureItemSchema.parse(raw));

export const MATERIALS: readonly MaterialItem[] = (readJson('materials.json') as unknown[]).map(
  (raw) => materialItemSchema.parse(raw),
);

export const FURNITURE: readonly CatalogueItem[] = toCatalogue(FURNITURE_ITEMS);

export const RECORDS: readonly AssetRecord[] = toAssetRecords(FURNITURE, MATERIALS);

export const INDEX: readonly SearchEntry[] = buildIndex(RECORDS);

/** One record by catalogue id, for assertions that name a specific item. */
export function recordById(id: string): AssetRecord {
  const found = RECORDS.find((record) => record.id === id);
  if (found === undefined) throw new Error(`no catalogue record with id "${id}"`);
  return found;
}
