/**
 * Filters, facets and the empty state's reason — against the real corpus.
 *
 * The counts below (91 storage items, 26 of them usable in 900 mm, 371 vs 111
 * across the whole catalogue) were computed independently from
 * `fixtures/catalog/furniture.json`, not derived from the code under test. That
 * is the point: a test that recomputes the expectation with the same predicate
 * it is checking passes no matter what the predicate says.
 */

import { describe, expect, it } from 'vitest';

import { INDEX, RECORDS } from './catalog.fixture';
import {
  EMPTY_CONTEXT,
  applyFilters,
  explainEmpty,
  facetsFor,
  orderByRecency,
  passesFilters,
  type FilterContext,
} from './filters';
import { searchEntries } from './search';
import { DEFAULT_FILTERS, hasFootprint, type AssetFilters } from './types';

function withFilters(patch: Partial<AssetFilters>): AssetFilters {
  return { ...DEFAULT_FILTERS, ...patch };
}

function count(patch: Partial<AssetFilters>, ctx: FilterContext = EMPTY_CONTEXT): number {
  return applyFilters(INDEX, withFilters(patch), ctx).length;
}

function contextOf(favourites: readonly string[], recents: readonly string[]): FilterContext {
  const recentOrder = new Map<string, number>();
  recents.forEach((key, index) => recentOrder.set(key, index));
  return { favourites: new Set(favourites), recentOrder };
}

describe('the dimensional filter', () => {
  it('counts the access strip by default, and that is not a rounding detail', () => {
    // 371 furniture items are 900 mm or less front to back. Only 111 of them
    // are still usable in a 900 mm slot once the door has to open.
    expect(count({ maxDepthMm: 900, includeClearance: false })).toBe(371);
    expect(count({ maxDepthMm: 900, includeClearance: true })).toBe(111);
  });

  it('excludes materials rather than waving them through', () => {
    const withDepth = applyFilters(INDEX, withFilters({ maxDepthMm: 900 }), EMPTY_CONTEXT);
    expect(withDepth.length).toBeGreaterThan(0);
    expect(withDepth.every((entry) => entry.record.kind === 'furniture')).toBe(true);
    // …and the exclusion is a real narrowing, not "everything happens to be
    // furniture": there are 184 materials in the unfiltered index.
    expect(INDEX.filter((entry) => entry.record.kind === 'material')).toHaveLength(184);
  });

  it('filters on width independently of depth', () => {
    expect(count({ maxWidthMm: 600 })).toBe(138);
  });

  it('is a real gate at every setting — no limit means no exclusions', () => {
    expect(count({})).toBe(653);
    // 80 mm is the smallest depth in the corpus and those eight items need no
    // access strip, so 80 keeps exactly them and 79 keeps none.
    expect(count({ maxDepthMm: 80 })).toBe(8);
    expect(count({ maxDepthMm: 79 })).toBe(0);
  });

  it('counts a room type once per item even when the catalogue repeats it', () => {
    // Six seeded items list `living_dining` twice. The facet count must agree
    // with what the filter returns, or the dropdown advertises rows that are
    // not there.
    const facet = facetsFor(INDEX).roomTypes.find((f) => f.value === 'living_dining');
    expect(facet).toBeDefined();
    expect(facet?.count).toBe(count({ roomType: 'living_dining' }));
    const repeated = RECORDS.filter(
      (r) => r.roomTypes.filter((slug) => slug === 'living_dining').length > 1,
    );
    expect(repeated.length).toBe(6);
  });
});

describe('filters compose', () => {
  it('category AND fits-depth is the intersection of the two', () => {
    const storage = count({ categoryKey: 'furniture:storage' });
    const fits = count({ maxDepthMm: 900, includeClearance: true });
    const both = count({
      categoryKey: 'furniture:storage',
      maxDepthMm: 900,
      includeClearance: true,
    });

    expect(storage).toBe(91);
    expect(fits).toBe(111);
    expect(both).toBe(26);

    // Both constraints must actually bite: the pair is strictly smaller than
    // either half. A composition that equalled one of its halves would be a
    // filter that never fires.
    expect(both).toBeLessThan(storage);
    expect(both).toBeLessThan(fits);

    const composed = applyFilters(
      INDEX,
      withFilters({ categoryKey: 'furniture:storage', maxDepthMm: 900, includeClearance: true }),
      EMPTY_CONTEXT,
    ).map((entry) => entry.record.id);
    const byHand = RECORDS.filter(
      (record) =>
        record.categoryKey === 'furniture:storage' &&
        hasFootprint(record) &&
        record.depthMm + record.clearanceMm <= 900,
    ).map((record) => record.id);
    expect([...composed].sort()).toEqual([...byHand].sort());
  });

  it('composes with the room filter as well', () => {
    expect(
      count({
        categoryKey: 'furniture:storage',
        roomType: 'bedroom',
        maxDepthMm: 900,
        includeClearance: true,
      }),
    ).toBe(19);
  });

  it('composes with the search, filters first', () => {
    const filtered = applyFilters(
      INDEX,
      withFilters({ categoryKey: 'furniture:storage', maxDepthMm: 900, includeClearance: true }),
      EMPTY_CONTEXT,
    );
    const searched = searchEntries(filtered, 'wardrobe');
    expect(searched.length).toBeGreaterThan(0);
    for (const entry of searched) {
      expect(entry.record.categoryKey).toBe('furniture:storage');
      expect(hasFootprint(entry.record)).toBe(true);
      if (hasFootprint(entry.record)) {
        expect(entry.record.depthMm + entry.record.clearanceMm).toBeLessThanOrEqual(900);
      }
    }
  });

  it('narrows by kind', () => {
    expect(count({ kind: 'furniture' })).toBe(469);
    expect(count({ kind: 'material' })).toBe(184);
  });
});

describe('scopes', () => {
  const ctx = contextOf(['furniture:bed-queen'], ['furniture:bed-king', 'material:kota-stone']);

  it('favourites shows exactly what was pinned', () => {
    const rows = applyFilters(INDEX, withFilters({ scope: 'favourites' }), ctx);
    expect(rows.map((entry) => entry.record.key)).toEqual(['furniture:bed-queen']);
  });

  it('recent shows exactly what was used, most recent first', () => {
    const rows = orderByRecency(
      applyFilters(INDEX, withFilters({ scope: 'recent' }), ctx),
      ctx.recentOrder,
    );
    expect(rows.map((entry) => entry.record.key)).toEqual([
      'furniture:bed-king',
      'material:kota-stone',
    ]);
  });

  it('an empty context empties both scopes', () => {
    expect(count({ scope: 'favourites' })).toBe(0);
    expect(count({ scope: 'recent' })).toBe(0);
  });
});

describe('facets', () => {
  const facets = facetsFor(INDEX);

  it('offers only options that actually match something', () => {
    for (const facet of facets.categories) {
      expect(facet.count).toBeGreaterThan(0);
      expect(count({ categoryKey: facet.value })).toBe(facet.count);
    }
    for (const facet of facets.roomTypes) {
      expect(facet.count).toBeGreaterThan(0);
      expect(count({ roomType: facet.value })).toBe(facet.count);
    }
  });

  it('partitions the catalogue by category', () => {
    const total = facets.categories.reduce((sum, facet) => sum + facet.count, 0);
    expect(total).toBe(INDEX.length);
  });

  it('labels a room type the way the rest of the app does', () => {
    const master = facets.roomTypes.find((facet) => facet.value === 'bedroom_master');
    expect(master?.label).toBe('Master Bedroom');
  });

  it('lists furniture categories before material ones', () => {
    const kinds = facets.categories.map((facet) => facet.value.split(':')[0]);
    expect(kinds.indexOf('material')).toBeGreaterThan(kinds.lastIndexOf('furniture'));
  });
});

describe('the empty state names a reason and offers a fix that works', () => {
  it('blames the depth limit when the depth limit is what emptied the list', () => {
    const filters = withFilters({
      categoryKey: 'furniture:vehicle',
      maxDepthMm: 900,
      includeClearance: true,
    });
    expect(applyFilters(INDEX, filters, EMPTY_CONTEXT)).toHaveLength(0);

    const advice = explainEmpty(INDEX, filters, '', EMPTY_CONTEXT, 'm');
    expect(advice.reason).toContain('access strip');
    expect(advice.fixLabel).toBe('Clear the depth limit');
    // The fix must genuinely produce rows — that is the difference between
    // advice and a guess.
    expect(applyFilters(INDEX, advice.fixFilters, EMPTY_CONTEXT).length).toBeGreaterThan(0);
    expect(advice.fixFilters.maxDepthMm).toBeNull();
    expect(advice.fixFilters.categoryKey).toBe('furniture:vehicle');
  });

  it('says so plainly when the depth limit is a carcase limit', () => {
    const filters = withFilters({
      categoryKey: 'furniture:vehicle',
      maxDepthMm: 900,
      includeClearance: false,
    });
    const advice = explainEmpty(INDEX, filters, '', EMPTY_CONTEXT, 'm');
    expect(advice.reason).toContain('front to back');
    expect(advice.reason).not.toContain('access strip');
  });

  it('quotes the query when the query is what emptied the list', () => {
    const advice = explainEmpty(INDEX, DEFAULT_FILTERS, 'zzzzzz', EMPTY_CONTEXT, 'm');
    expect(advice.reason).toContain('zzzzzz');
    expect(advice.fixLabel).toBe('Clear the search');
    expect(advice.fixClearsQuery).toBe(true);
  });

  it('explains an empty favourites list instead of blaming a filter', () => {
    const advice = explainEmpty(
      INDEX,
      withFilters({ scope: 'favourites' }),
      '',
      EMPTY_CONTEXT,
      'm',
    );
    expect(advice.reason).toContain('pinned anything yet');
    expect(advice.fixFilters.scope).toBe('all');
  });

  it('names the room when the room is the culprit', () => {
    const filters = withFilters({ categoryKey: 'furniture:vehicle', roomType: 'pooja' });
    expect(applyFilters(INDEX, filters, EMPTY_CONTEXT)).toHaveLength(0);
    const advice = explainEmpty(INDEX, filters, '', EMPTY_CONTEXT, 'm');
    expect(advice.reason).toContain('Pooja');
    expect(applyFilters(INDEX, advice.fixFilters, EMPTY_CONTEXT).length).toBeGreaterThan(0);
  });

  it('falls back to a full reset when no single change is enough', () => {
    const filters = withFilters({ categoryKey: 'furniture:vehicle', maxDepthMm: 900 });
    const advice = explainEmpty(INDEX, filters, 'zzzzzz', EMPTY_CONTEXT, 'm');
    expect(advice.fixLabel).toBe('Reset every filter');
    expect(advice.fixClearsQuery).toBe(true);
    expect(advice.fixFilters).toEqual(DEFAULT_FILTERS);
  });

  it('reports the depth limit in the project units', () => {
    const filters = withFilters({ categoryKey: 'furniture:vehicle', maxDepthMm: 900 });
    expect(explainEmpty(INDEX, filters, '', EMPTY_CONTEXT, 'm').reason).toContain('0.90 m');
    expect(explainEmpty(INDEX, filters, '', EMPTY_CONTEXT, 'ft-in').reason).toContain('"');
  });
});

describe('passesFilters is total', () => {
  it('accepts every record under the default filters', () => {
    for (const record of RECORDS) {
      expect(passesFilters(record, DEFAULT_FILTERS, EMPTY_CONTEXT)).toBe(true);
    }
  });
});
