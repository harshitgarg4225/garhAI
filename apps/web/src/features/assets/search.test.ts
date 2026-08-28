/**
 * Search, against the real 469 + 184 corpus.
 *
 * Every assertion here names a specific catalogue id, so a change in the
 * ranking that "still returns something sensible" fails rather than passing on
 * a loose `toBeGreaterThan(0)`. The corpus is loaded from
 * `fixtures/catalog/*.json` — the same files the API serves.
 */

import { describe, expect, it } from 'vitest';

import { INDEX, RECORDS } from './catalog.fixture';
import {
  DIM_MIN_MM,
  buildIndex,
  isAdjacentSwap,
  normaliseHaystack,
  parseQuery,
  searchEntries,
  termScore,
  withinOneEdit,
  type SearchEntry,
} from './search';

function ids(query: string, limit = 10): string[] {
  return searchEntries(INDEX, query)
    .slice(0, limit)
    .map((entry) => entry.record.id);
}

function allIds(query: string): string[] {
  return searchEntries(INDEX, query).map((entry) => entry.record.id);
}

function entry(id: string): SearchEntry {
  const found = INDEX.find((candidate) => candidate.record.id === id);
  if (found === undefined) throw new Error(`no index entry for "${id}"`);
  return found;
}

describe('the corpus itself', () => {
  it('is the real one, not a stub', () => {
    expect(RECORDS.filter((r) => r.kind === 'furniture')).toHaveLength(469);
    expect(RECORDS.filter((r) => r.kind === 'material')).toHaveLength(184);
    expect(INDEX).toHaveLength(653);
  });

  it('gives every record a key unique across both catalogues', () => {
    expect(new Set(RECORDS.map((r) => r.key)).size).toBe(RECORDS.length);
  });

  it('never invents a footprint for a material', () => {
    for (const record of RECORDS) {
      if (record.kind !== 'material') continue;
      expect(record.widthMm).toBeNull();
      expect(record.depthMm).toBeNull();
      expect(record.clearanceMm).toBeNull();
    }
  });
});

describe('search by name', () => {
  it('finds an item by a word in its name', () => {
    expect(ids('kota')).toContain('kota-stone');
  });

  it('ranks a whole-name prefix above a mid-name match', () => {
    const ranked = allIds('wardrobe');
    const prefix = ranked.indexOf('wardrobe-hinged-1800');
    const midName = ranked.indexOf('loft-unit-1800');
    expect(prefix).toBeGreaterThanOrEqual(0);
    // "Loft above wardrobe" contains the word but does not start with it.
    expect(ranked.indexOf('wardrobe-loft')).toBeGreaterThan(prefix);
    expect(midName).toBe(-1);
  });

  it('is insensitive to the punctuation in a name', () => {
    // "Wardrobe (2 door)" — the brackets must not have to be typed.
    expect(ids('wardrobe 2 door')).toContain('wardrobe-2door');
  });

  it('ANDs its terms rather than ORing them', () => {
    // Both halves match plenty on their own; nothing matches both.
    expect(allIds('wardrobe')).not.toHaveLength(0);
    expect(allIds('granite')).not.toHaveLength(0);
    expect(allIds('wardrobe granite')).toHaveLength(0);
  });

  it('lets a room type act as one of the ANDed terms', () => {
    // "bed store" is not nonsense: it is "a bed that goes in a store room",
    // and the room-type haystack is what makes that reachable. The result must
    // be exactly the intersection of the two single-term searches — an item
    // dropped by the AND that both halves found is a scoring bug.
    const both = new Set(allIds('bed store'));
    const beds = new Set(allIds('bed'));
    const stores = new Set(allIds('store'));
    const intersection = [...beds].filter((id) => stores.has(id)).sort();
    expect([...both].sort()).toEqual(intersection);
    expect(intersection.length).toBeGreaterThan(0);
    expect(intersection).toContain('bed-folding');
  });

  it('searches the category and the room type as well as the name', () => {
    const sanitary = searchEntries(INDEX, 'sanitary');
    expect(sanitary.length).toBeGreaterThan(0);
    expect(sanitary.every((entry) => entry.record.categoryKey === 'furniture:sanitary')).toBe(true);

    const pooja = searchEntries(INDEX, 'pooja');
    expect(pooja.length).toBeGreaterThan(0);
    expect(
      pooja.some(
        (entry) => entry.record.roomTypes.includes('pooja') && entry.record.id !== 'pooja',
      ),
    ).toBe(true);
  });
});

describe('search by dimension — the headline case', () => {
  it('"wardrobe 1800" finds the 1800 mm hinged wardrobe', () => {
    expect(ids('wardrobe 1800')).toContain('wardrobe-hinged-1800');
  });

  it('finds an item whose NAME does not carry the number at all', () => {
    // "Wardrobe (3 door)" is 1800 mm wide; nothing in its name says so, so a
    // name-only search cannot reach it. This is the assertion that fails if the
    // dimension path is deleted while the name path keeps the test green.
    const nameOnly = normaliseHaystack('Wardrobe (3 door)');
    expect(nameOnly).not.toContain('1800');
    expect(allIds('wardrobe 1800')).toContain('wardrobe-3door');
  });

  it('accepts the same length typed any of the ways a length is typed', () => {
    const inMm = allIds('wardrobe 1800');
    expect(allIds('wardrobe 1.8m')).toEqual(inMm);
    expect(allIds('wardrobe 180cm')).toEqual(inMm);
    expect(allIds('wardrobe 1,800')).toEqual(inMm);
  });

  it('tolerates 25 mm of slop but not a whole size step', () => {
    // The queen bed is 1525 mm wide. "1500" is 25 mm off — inside tolerance.
    expect(allIds('bed 1500')).toContain('bed-queen');
    // 1400 is a whole step away, and nothing named "1400" is a bed.
    expect(allIds('bed 1400')).not.toContain('bed-queen');
  });

  it('scores an exact dimension hit above a near one', () => {
    // The queen bed is 1525 × 1900. Same item, two terms: the one that names
    // its width exactly must score higher than the one 25 mm off, or the
    // tolerance band would flatten the ranking it exists to widen.
    const queen = entry('bed-queen');
    const exactTerm = parseQuery('1525')[0];
    const nearTerm = parseQuery('1500')[0];
    expect(exactTerm).toBeDefined();
    expect(nearTerm).toBeDefined();
    if (exactTerm === undefined || nearTerm === undefined) return;
    expect(termScore(queen, exactTerm)).toBeGreaterThan(termScore(queen, nearTerm));
    expect(termScore(queen, nearTerm)).toBeGreaterThan(0);
  });

  it('does not treat one catalogue size as a typo of another', () => {
    // "1200" is one substitution from "1800". Before the letter guard in
    // fuzzyScore, searching for an 1800 mm wardrobe returned the 1200 mm and
    // 1500 mm ones as well.
    const ranked = allIds('wardrobe 1800');
    expect(ranked).toContain('wardrobe-hinged-1800');
    expect(ranked).not.toContain('wardrobe-hinged-1200');
    expect(ranked).not.toContain('wardrobe-hinged-1500');
  });

  it('reads a bare number below the smallest real dimension as text, not a size', () => {
    // "2" in "2 door" is a word. Treating it as 2 mm would make the term match
    // nothing on the dimension path and quietly drop every 2-door item.
    expect(parseQuery('2')[0]?.mm).toBeNull();
    expect(parseQuery(String(DIM_MIN_MM))[0]?.mm).toBe(DIM_MIN_MM);
    expect(ids('wardrobe 2 door')).toContain('wardrobe-2door');
  });

  it('splits a 600x600 style dimension pair into two terms', () => {
    expect(parseQuery('600x600').map((t) => t.mm)).toEqual([600, 600]);
    expect(parseQuery('600 × 600').map((t) => t.mm)).toEqual([600, 600]);
    expect(ids('tile 600x600')).toContain('vitrified-tile-600');
  });

  it('matches a material by name even though it has no dimensions to match', () => {
    const hits = searchEntries(INDEX, '600');
    expect(hits.some((entry) => entry.record.id === 'vitrified-tile-600')).toBe(true);
    // …and never because of a phantom zero footprint.
    for (const entry of hits) {
      if (entry.record.kind === 'material') expect(entry.name).toContain('600');
    }
  });
});

describe('typing tolerance', () => {
  it('forgives one wrong letter', () => {
    expect(allIds('wardrobbe')).toContain('wardrobe-hinged-1800');
  });

  it('forgives two transposed letters', () => {
    expect(allIds('wardorbe')).toContain('wardrobe-hinged-1800');
  });

  it('ranks a literal match above a forgiven one', () => {
    const ranked = allIds('sofa');
    expect(ranked[0]).toBeDefined();
    const exactFirst = searchEntries(INDEX, 'sofa')[0];
    expect(exactFirst?.name.startsWith('sofa')).toBe(true);
  });

  it('drops a trailing plural as a last resort', () => {
    expect(allIds('wardrobes')).toContain('wardrobe-hinged-1800');
  });

  it('does not forgive a typo in a short word — that is noise, not tolerance', () => {
    // "bex" is one edit from "bed" and appears nowhere in the corpus as text.
    // Forgiving it would mean every three-letter slip returns half the beds.
    expect(allIds('bex')).toHaveLength(0);
    // Four letters is where tolerance starts paying: "sofe" → "sofa".
    expect(allIds('sofe').length).toBeGreaterThan(0);
  });

  it('withinOneEdit and isAdjacentSwap agree with their names', () => {
    expect(withinOneEdit('wardrobe', 'wardrobbe')).toBe(true);
    expect(withinOneEdit('wardrobe', 'wardrbe')).toBe(true);
    expect(withinOneEdit('wardrobe', 'wardrobf')).toBe(true);
    expect(withinOneEdit('wardrobe', 'wardorbe')).toBe(false);
    expect(withinOneEdit('wardrobe', 'wrdrbe')).toBe(false);
    expect(isAdjacentSwap('wardrobe', 'wardorbe')).toBe(true);
    expect(isAdjacentSwap('wardrobe', 'wardrobe')).toBe(false);
    expect(isAdjacentSwap('wardrobe', 'wadrrobe')).toBe(true);
    expect(isAdjacentSwap('abc', 'cba')).toBe(false);
  });
});

describe('determinism and identity', () => {
  it('returns the SAME ARRAY for an empty query, so downstream memos hold', () => {
    expect(searchEntries(INDEX, '')).toBe(INDEX);
    expect(searchEntries(INDEX, '   ')).toBe(INDEX);
  });

  it('produces the same order twice', () => {
    expect(allIds('wardrobe 1800')).toEqual(allIds('wardrobe 1800'));
  });

  it('browses in category order with no query', () => {
    const first = INDEX[0]?.record;
    const last = INDEX[INDEX.length - 1]?.record;
    expect(first?.kind).toBe('furniture');
    expect(first?.category).toBe('bed');
    expect(last?.kind).toBe('material');
  });

  it('builds an index of exactly one entry per record', () => {
    expect(buildIndex(RECORDS)).toHaveLength(RECORDS.length);
  });
});
