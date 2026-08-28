/**
 * Filtering, faceting, and the one thing an empty result list owes the user:
 * a reason.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE DEPTH FILTER SPENDS THE ACCESS STRIP, AND SAYS SO
 * ════════════════════════════════════════════════════════════════════════════
 * "Fits in 900 mm depth" has two honest readings. A wardrobe carcase 600 mm
 * deep fits a 900 mm alcove; the same wardrobe needs 600 + 750 = 1350 mm before
 * anyone can open it. The solver already picked a side —
 * `services/solver/furniture_fit.py` packs each item as
 * `(width, depth + clearance)` — and a browser that answered the other question
 * would offer items the solver then refuses to place.
 *
 * So the default is "body + access strip", matching the solver, and the other
 * reading is a visible switch rather than a constant nobody can see. Turn the
 * switch off and the filter answers the carcase question instead.
 *
 * Against the seeded corpus the two readings are 371 items and 111 items at
 * 900 mm. A filter whose two settings differ by 260 items is not a detail.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * A MATERIAL HAS NO DEPTH, AND IS THEREFORE EXCLUDED, NOT WAVED THROUGH
 * ════════════════════════════════════════════════════════════════════════════
 * `AssetRecord.depthMm` is `null` on a material. The tempting implementation —
 * "records without dimensions skip the dimensional filters" — makes "fits in
 * 900 mm" return all 184 materials, i.e. a filter that cannot narrow. The
 * opposite mistake, treating null as 0, makes every material fit everything.
 * Both are the same failure the circulation cap had: a comparison whose result
 * was never in doubt.
 *
 * A dimensional filter is a placement question, so it keeps only records that
 * can answer it. The chip on screen says "furniture only" while one is on, and
 * {@link explainEmpty} names it when it is the reason the list is empty.
 */

import { formatLengthDisplay, type UnitsDisplay } from '../../lib/units';
import { parseQuery, scoreEntry, type SearchEntry } from './search';
import {
  DEFAULT_FILTERS,
  categoryRank,
  hasFootprint,
  roomTypeLabel,
  type AssetFilters,
  type AssetRecord,
} from './types';

/** The per-user state the scopes read. Passed in, never imported — see `store.ts`. */
export interface FilterContext {
  readonly favourites: ReadonlySet<string>;
  /** `key -> position`, 0 = most recently used. Absent means never used. */
  readonly recentOrder: ReadonlyMap<string, number>;
}

export const EMPTY_CONTEXT: FilterContext = {
  favourites: new Set<string>(),
  recentOrder: new Map<string, number>(),
};

/** True when `record` survives every active filter. */
export function passesFilters(
  record: AssetRecord,
  filters: AssetFilters,
  ctx: FilterContext,
): boolean {
  if (filters.kind !== 'all' && record.kind !== filters.kind) return false;
  if (filters.scope === 'favourites' && !ctx.favourites.has(record.key)) return false;
  if (filters.scope === 'recent' && !ctx.recentOrder.has(record.key)) return false;
  if (filters.categoryKey !== null && record.categoryKey !== filters.categoryKey) return false;
  if (filters.roomType !== null && !record.roomTypes.includes(filters.roomType)) return false;

  if (filters.maxDepthMm !== null || filters.maxWidthMm !== null) {
    // The placement question. A record with no footprint cannot answer it.
    if (!hasFootprint(record)) return false;
    if (filters.maxDepthMm !== null) {
      const strip = filters.includeClearance ? record.clearanceMm : 0;
      if (record.depthMm + strip > filters.maxDepthMm) return false;
    }
    if (filters.maxWidthMm !== null && record.widthMm > filters.maxWidthMm) return false;
  }

  return true;
}

/**
 * Apply the filters, preserving the index's browsing order.
 *
 * Runs BEFORE the search on purpose: the filters are integer compares and set
 * lookups, the search is string work. Narrowing first means a room-scoped
 * search scores 40 entries, not 653.
 */
export function applyFilters(
  entries: readonly SearchEntry[],
  filters: AssetFilters,
  ctx: FilterContext,
): readonly SearchEntry[] {
  return entries.filter((entry) => passesFilters(entry.record, filters, ctx));
}

/**
 * Most-recently-used first.
 *
 * Applied after the search in the `recent` scope, because recency IS the
 * ranking there — "the thing I placed ten minutes ago" is the query, and a
 * relevance sort would bury it under a better name match.
 */
export function orderByRecency(
  entries: readonly SearchEntry[],
  recentOrder: ReadonlyMap<string, number>,
): readonly SearchEntry[] {
  const missing = recentOrder.size;
  return [...entries].sort(
    (a, b) =>
      (recentOrder.get(a.record.key) ?? missing) - (recentOrder.get(b.record.key) ?? missing),
  );
}

// ---------------------------------------------------------------------------
// Facets
// ---------------------------------------------------------------------------

export interface Facet {
  readonly value: string;
  readonly label: string;
  readonly count: number;
}

export interface Facets {
  /** Values are `AssetRecord.categoryKey`. */
  readonly categories: readonly Facet[];
  /** Values are room-type slugs as the catalogue serves them. */
  readonly roomTypes: readonly Facet[];
}

/**
 * The filter options, derived from the data rather than declared.
 *
 * Declaring them is how 83 rules went inert: a hard-coded list whose members
 * are not the ones the served data actually uses produces controls that select
 * nothing, and nothing about that is visible on screen. Every option here came
 * from a record, so every option matches at least one.
 */
export function facetsFor(entries: readonly SearchEntry[]): Facets {
  const categories = new Map<string, Facet>();
  const roomTypes = new Map<string, Facet>();

  for (const { record } of entries) {
    const cat = categories.get(record.categoryKey);
    categories.set(record.categoryKey, {
      value: record.categoryKey,
      label: record.categoryLabel,
      count: (cat?.count ?? 0) + 1,
    });
    // Deduped per record. The seeded catalogue repeats a slug on 11 items —
    // six of them list `living_dining` twice (the sideboards and the split
    // ACs) — while the room filter matches with `includes`, once. Counting
    // occurrences instead of records made this dropdown promise 110 items and
    // the list deliver 104. A count that disagrees with its own filter is the
    // sort of number an architect stops trusting the whole panel over.
    for (const slug of new Set(record.roomTypes)) {
      const seen = roomTypes.get(slug);
      roomTypes.set(slug, {
        value: slug,
        label: roomTypeLabel(slug),
        count: (seen?.count ?? 0) + 1,
      });
    }
  }

  const categoryList = [...categories.values()].sort((a, b) => {
    const [kindA = '', catA = ''] = a.value.split(':');
    const [kindB = '', catB = ''] = b.value.split(':');
    const ra = categoryRank(kindA === 'material' ? 'material' : 'furniture', catA);
    const rb = categoryRank(kindB === 'material' ? 'material' : 'furniture', catB);
    return ra !== rb ? ra - rb : a.label.localeCompare(b.label);
  });
  const roomList = [...roomTypes.values()].sort((a, b) => a.label.localeCompare(b.label));

  return { categories: categoryList, roomTypes: roomList };
}

// ---------------------------------------------------------------------------
// The empty state's reason
// ---------------------------------------------------------------------------

export interface EmptyAdvice {
  /** One sentence naming the constraint that emptied the list. */
  readonly reason: string;
  /** Label for the one-click fix, or null when there is nothing left to relax. */
  readonly fixLabel: string | null;
  /** The filters with that one constraint relaxed. */
  readonly fixFilters: AssetFilters;
  /** True when applying the fix also clears the search box. */
  readonly fixClearsQuery: boolean;
}

function countMatches(
  entries: readonly SearchEntry[],
  filters: AssetFilters,
  ctx: FilterContext,
  query: string,
): number {
  const terms = parseQuery(query);
  let n = 0;
  for (const entry of entries) {
    if (!passesFilters(entry.record, filters, ctx)) continue;
    if (terms.length > 0 && scoreEntry(entry, terms) < 0) continue;
    n += 1;
  }
  return n;
}

/**
 * Why the list is empty, and the single change that would fix it.
 *
 * Found by RELAXING one constraint at a time and re-counting — not by guessing
 * from the shape of the filter state. That distinction is the whole value: the
 * advice is only offered when the relaxed query genuinely returns rows, so the
 * button can never say "clear the depth filter" and then still show nothing.
 *
 * Ordered most-specific-first, because when two constraints are both fatal the
 * narrow one is nearly always the one the user just touched.
 */
export function explainEmpty(
  entries: readonly SearchEntry[],
  filters: AssetFilters,
  query: string,
  ctx: FilterContext,
  display: UnitsDisplay,
): EmptyAdvice {
  // The two scopes can be empty for a reason no relaxation would explain well.
  if (filters.scope === 'favourites' && ctx.favourites.size === 0) {
    return {
      reason: 'You have not pinned anything yet. The pin on any row adds it here.',
      fixLabel: 'Browse everything',
      fixFilters: { ...filters, scope: 'all' },
      fixClearsQuery: false,
    };
  }
  if (filters.scope === 'recent' && ctx.recentOrder.size === 0) {
    return {
      reason: 'Nothing used yet in this browser. Items you place show up here.',
      fixLabel: 'Browse everything',
      fixFilters: { ...filters, scope: 'all' },
      fixClearsQuery: false,
    };
  }

  const depthText =
    filters.maxDepthMm === null ? '' : formatLengthDisplay(filters.maxDepthMm, display);
  const widthText =
    filters.maxWidthMm === null ? '' : formatLengthDisplay(filters.maxWidthMm, display);

  const candidates: readonly {
    active: boolean;
    reason: string;
    fixLabel: string;
    filters: AssetFilters;
    query: string;
  }[] = [
    {
      active: filters.maxDepthMm !== null,
      reason: filters.includeClearance
        ? `Nothing fits ${depthText} deep once its access strip is counted.`
        : `Nothing is ${depthText} or less, front to back.`,
      fixLabel: 'Clear the depth limit',
      filters: { ...filters, maxDepthMm: null },
      query,
    },
    {
      active: filters.maxWidthMm !== null,
      reason: `Nothing is ${widthText} or less along the wall.`,
      fixLabel: 'Clear the width limit',
      filters: { ...filters, maxWidthMm: null },
      query,
    },
    {
      active: filters.roomType !== null,
      reason: `Nothing here belongs in a ${roomTypeLabel(filters.roomType ?? '')}.`,
      fixLabel: 'Show every room',
      filters: { ...filters, roomType: null },
      query,
    },
    {
      active: filters.categoryKey !== null,
      reason: 'Nothing in this category matches the rest of the filters.',
      fixLabel: 'Show every category',
      filters: { ...filters, categoryKey: null },
      query,
    },
    {
      active: filters.kind !== 'all',
      reason:
        filters.kind === 'furniture'
          ? 'No furniture matches — but a material does.'
          : 'No material matches — but a furniture item does.',
      fixLabel: 'Search both catalogues',
      filters: { ...filters, kind: 'all' },
      query,
    },
    {
      active: filters.scope !== 'all',
      reason:
        filters.scope === 'favourites'
          ? 'Nothing you have pinned matches this search.'
          : 'Nothing you have used recently matches this search.',
      fixLabel: 'Browse everything',
      filters: { ...filters, scope: 'all' },
      query,
    },
    {
      active: query.trim() !== '',
      reason: `Nothing matches “${query.trim()}”.`,
      fixLabel: 'Clear the search',
      filters,
      query: '',
    },
  ];

  for (const candidate of candidates) {
    if (!candidate.active) continue;
    if (countMatches(entries, candidate.filters, ctx, candidate.query) === 0) continue;
    return {
      reason: candidate.reason,
      fixLabel: candidate.fixLabel,
      fixFilters: candidate.filters,
      fixClearsQuery: candidate.query !== query,
    };
  }

  // Nothing relaxed alone is enough — several constraints are fatal together.
  return {
    reason:
      query.trim() === ''
        ? 'These filters have no items in common.'
        : `Nothing matches “${query.trim()}” with these filters. More than one of them would have to change.`,
    fixLabel: 'Reset every filter',
    fixFilters: DEFAULT_FILTERS,
    fixClearsQuery: true,
  };
}
