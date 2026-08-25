/**
 * The catalogue: normalising what `GET /catalog/furniture` returns into
 * {@link CatalogueItem}, and the pure search / group / filter functions the
 * browser panel renders.
 *
 * No React, no network — `useFurnitureCatalogue.ts` does the fetching. Keeping
 * the transforms here is what lets `furniture.test.ts` pin the room filter and
 * the search ranking without a DOM.
 *
 * ## The clearance gap, stated plainly
 *
 * The API serves `clearanceMm` (`CatalogItemOut` in
 * `apps/api/garh_api/routers/catalog.py`) and the seed file carries a real
 * value for all 45 items. The client's zod schema
 * (`apps/web/src/lib/schemas.ts`, `furnitureItemSchema`) does not yet list the
 * field, and zod strips unknown keys — so today the number arrives at the
 * server boundary and is dropped before the editor sees it.
 *
 * {@link toCatalogueItem} therefore reads `clearanceMm` when it is present and
 * otherwise falls back to a per-category default, flagging the item with
 * `clearanceAssumed: true` so the UI can say so instead of pretending. Adding
 * `clearanceMm: intMm.default(0)` to `furnitureItemSchema` turns every fallback
 * off with no change here — that file belongs to the integrator, so it is an
 * ask, not an edit.
 */

import type { RoomType, UnitsDisplay } from '@garh/model';

import { formatDimensionPair, formatLengthDisplay } from '../../../lib/units';
import {
  FURNITURE_CATEGORIES,
  FURNITURE_CATEGORY_LABELS,
  type CatalogueItem,
  type FurnitureCategory,
} from './types';

/**
 * Fallback access strip per category, used only when the server did not send
 * one. Each number is the modal value of that category in
 * `fixtures/catalog/furniture.json`, so a fallback matches the real data for
 * the overwhelming majority of items rather than inventing a house number.
 *
 *   bed 600 · seating 600 · table 750 · storage 600 · kitchen 1050 ·
 *   sanitary 600 · appliance 750 · vehicle 600 · service 450
 *
 * 1050 mm for kitchen is not a typo: it is the standard Indian galley working
 * aisle, and it is what the seeded counter, sink and hob all carry.
 */
export const CLEARANCE_FALLBACK_MM: Readonly<Record<FurnitureCategory, number>> = {
  bed: 600,
  seating: 600,
  table: 750,
  storage: 600,
  kitchen: 1050,
  sanitary: 600,
  appliance: 750,
  vehicle: 600,
  service: 450,
  other: 600,
};

const KNOWN_CATEGORIES = new Set<string>(FURNITURE_CATEGORIES);

/** Narrow a served category string to the closed list; anything else is `other`. */
export function toCategory(raw: string): FurnitureCategory {
  const key = raw.trim().toLowerCase();
  return KNOWN_CATEGORIES.has(key) && key !== 'other' ? (key as FurnitureCategory) : 'other';
}

/** What the API can hand us. Deliberately loose — this IS the boundary. */
export interface RawFurnitureItem {
  readonly id: string;
  readonly name: string;
  readonly category?: string | undefined;
  readonly widthMm: number;
  readonly depthMm: number;
  readonly heightMm: number;
  readonly roomTypes?: readonly string[] | undefined;
  readonly assetUrl?: string | null | undefined;
  /** Present on the wire and in the fixture; see the module note. */
  readonly clearanceMm?: unknown;
}

function readClearance(raw: RawFurnitureItem, category: FurnitureCategory): {
  clearanceMm: number;
  clearanceAssumed: boolean;
} {
  const value = raw.clearanceMm;
  if (typeof value === 'number' && Number.isInteger(value) && value >= 0) {
    return { clearanceMm: value, clearanceAssumed: false };
  }
  return { clearanceMm: CLEARANCE_FALLBACK_MM[category], clearanceAssumed: true };
}

/**
 * Normalise one served item. Dimensions are trusted as integers (the API's
 * `StrictInt` rejects `1524.0` at the source and the client's `intMm` rejects
 * it again) but clamped to ≥1 mm so a zero-width entry cannot produce a
 * degenerate footprint that the SAT test would answer nonsense for.
 */
export function toCatalogueItem(raw: RawFurnitureItem): CatalogueItem {
  const rawCategory = raw.category ?? '';
  const category = toCategory(rawCategory);
  const { clearanceMm, clearanceAssumed } = readClearance(raw, category);
  return {
    id: raw.id,
    name: raw.name,
    category,
    rawCategory,
    widthMm: Math.max(1, Math.trunc(raw.widthMm)),
    depthMm: Math.max(1, Math.trunc(raw.depthMm)),
    heightMm: Math.max(1, Math.trunc(raw.heightMm)),
    clearanceMm,
    clearanceAssumed,
    roomTypes: raw.roomTypes ?? [],
    assetUrl: raw.assetUrl ?? null,
  };
}

export function toCatalogue(raws: readonly RawFurnitureItem[]): CatalogueItem[] {
  return raws.map(toCatalogueItem);
}

/** `id -> item`, for joining `FurnitureInstance.catalogId` back to dimensions. */
export function catalogueIndex(items: readonly CatalogueItem[]): ReadonlyMap<string, CatalogueItem> {
  const map = new Map<string, CatalogueItem>();
  for (const item of items) map.set(item.id, item);
  return map;
}

// ---------------------------------------------------------------------------
// Filtering and search
// ---------------------------------------------------------------------------

/** Items a room of this type would normally contain. `null` disables the filter. */
export function filterByRoomType(
  items: readonly CatalogueItem[],
  roomType: RoomType | null,
): CatalogueItem[] {
  if (roomType === null) return [...items];
  return items.filter((item) => item.roomTypes.includes(roomType));
}

/**
 * Search score for one item, or -1 when it does not match at all.
 *
 * Ranking, best first: exact id · name starts with the query · a name word
 * starts with it · name contains it · category or room type contains it. Every
 * whitespace-separated term must match something, so "dining 6" finds the
 * six-seater and "bed store" finds nothing — which is the honest answer.
 */
export function searchScore(item: CatalogueItem, query: string): number {
  const q = query.trim().toLowerCase();
  if (q === '') return 0;

  const name = item.name.toLowerCase();
  const id = item.id.toLowerCase();
  const haystacks = [name, id, item.category, item.rawCategory.toLowerCase(), ...item.roomTypes];

  let total = 0;
  for (const term of q.split(/\s+/)) {
    let best = -1;
    if (id === term) best = 100;
    else if (name.startsWith(term)) best = 80;
    else if (name.split(/[\s(-]+/).some((word) => word.startsWith(term))) best = 60;
    else if (name.includes(term)) best = 40;
    else if (id.includes(term)) best = 30;
    else if (haystacks.some((h) => h.includes(term))) best = 10;
    if (best < 0) return -1;
    total += best;
  }
  return total;
}

/**
 * Search, best match first. Ties break on name so the list never reshuffles
 * between renders for reasons a user cannot see.
 */
export function searchItems(items: readonly CatalogueItem[], query: string): CatalogueItem[] {
  if (query.trim() === '') return [...items];
  const scored: Array<{ item: CatalogueItem; score: number }> = [];
  for (const item of items) {
    const score = searchScore(item, query);
    if (score >= 0) scored.push({ item, score });
  }
  scored.sort((a, b) => b.score - a.score || a.item.name.localeCompare(b.item.name));
  return scored.map((s) => s.item);
}

export interface CatalogueGroup {
  readonly category: FurnitureCategory;
  readonly label: string;
  readonly items: readonly CatalogueItem[];
}

/**
 * Group into the browser's sections, in {@link FURNITURE_CATEGORIES} order.
 * Empty groups are dropped — a section header with nothing under it is noise,
 * and after a room filter most of them are empty.
 */
export function groupByCategory(items: readonly CatalogueItem[]): CatalogueGroup[] {
  const buckets = new Map<FurnitureCategory, CatalogueItem[]>();
  for (const item of items) {
    const list = buckets.get(item.category);
    if (list === undefined) buckets.set(item.category, [item]);
    else list.push(item);
  }

  const out: CatalogueGroup[] = [];
  for (const category of FURNITURE_CATEGORIES) {
    const list = buckets.get(category);
    if (list === undefined || list.length === 0) continue;
    out.push({
      category,
      label: FURNITURE_CATEGORY_LABELS[category],
      items: list,
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Display — every number a human reads goes through lib/units
// ---------------------------------------------------------------------------

/** `5'-0" × 6'-3"` or `1.53 × 1.90 m`, per the project's units. */
export function formatItemFootprint(item: CatalogueItem, display: UnitsDisplay): string {
  return formatDimensionPair(item.widthMm, item.depthMm, display);
}

/** `750 mm access` — the clearance strip, in the project's units. */
export function formatItemClearance(item: CatalogueItem, display: UnitsDisplay): string {
  return `${formatLengthDisplay(item.clearanceMm, display)} access`;
}

/** Plan footprint in mm². Display-only; the solver has its own copy of this. */
export function footprintMm2(item: CatalogueItem): number {
  return item.widthMm * item.depthMm;
}
