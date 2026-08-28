/**
 * Search over the merged catalogue: 653 records, re-scored on every keystroke.
 *
 * No React, no network — so `search.test.ts` pins the ranking against the real
 * fixture without a DOM, and the component is free to decide *when* to call it.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY AN INDEX AND NOT `items.filter(...)`
 * ════════════════════════════════════════════════════════════════════════════
 * Every string operation a naive search does — lower-casing, splitting a name
 * into words, stripping punctuation — depends only on the CATALOGUE, which
 * changes once per session (`useFurnitureCatalogue` caches a module-level
 * promise). Doing that work inside the keystroke loop means 653 × ~5 allocations
 * per character typed, which is exactly how a search box drops frames.
 *
 * {@link buildIndex} does it once. What is left per keystroke is a scan of
 * pre-lowercased strings and integer compares.
 *
 * Measured on this corpus (node 22, jsdom, 50 iterations of the twelve
 * keystrokes that spell "wardrobe 1800"): `buildIndex` 2.4 ms ONCE, then
 * 0.17 ms per keystroke with no filters on and 0.018 ms with a category and a
 * depth limit applied. A 16 ms frame has room for ninety of the former.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * "TOLERANT OF THE WAY PEOPLE ACTUALLY TYPE"
 * ════════════════════════════════════════════════════════════════════════════
 * Four concessions, each earning its place against the seeded corpus:
 *
 * 1. **Dimensions are searchable.** `wardrobe 1800` finds `Wardrobe, hinged
 *    1800 mm` — and would still find it if the name did not carry the number,
 *    because a term that parses as a length is also matched against the item's
 *    width, depth and height. That is the difference between a catalogue you
 *    search by name and one you search by size, and size is how an architect
 *    thinks when a wall is 1830 mm long.
 *
 *    The term goes through `lib/units.tryParseLengthMm`, so `1800`, `1.8m`,
 *    `180cm` and `6'` are the same query. There is no `parseFloat` here; the
 *    rounding is the model's half-away-from-zero, once, at the parse.
 *
 * 2. **`×` and `x` between digits are a separator.** `600x600` and `600 × 600`
 *    both reach the scorer as two terms.
 *
 * 3. **A trailing plural is dropped as a fallback.** `chairs` finds `Chair` at
 *    a small penalty, so the ranking still prefers a literal match.
 *
 * 4. **One typo is forgiven, transpositions included.** `wardrobbe` and
 *    `wardorbe` both find wardrobes, at the lowest score in the table so a
 *    real match always outranks a guess. Only for terms of 4+ characters —
 *    below that, one edit is most of the word and the results become noise.
 *
 * Terms are ANDed. `bed store` finding nothing is the honest answer, and it is
 * what makes `wardrobe 1800` mean "a wardrobe, and 1800" rather than "anything
 * matching either".
 */

import { tryParseLengthMm } from '../../lib/units';
import { categoryRank, type AssetRecord } from './types';

/**
 * One record with every derived string it will ever be searched by, computed
 * once. `dims` holds only the non-null lengths, so a material contributes an
 * empty array and can never match a dimension term by accident.
 */
export interface SearchEntry {
  readonly record: AssetRecord;
  /** Haystack-normalised name: lower case, no punctuation, no diacritics. */
  readonly name: string;
  /** The name split into words, for prefix matching. */
  readonly nameWords: readonly string[];
  /** Haystack-normalised id. */
  readonly id: string;
  /** Category slug + label + room types + surface groups, space-joined. */
  readonly extra: string;
  /** Width, depth, height in integer mm — the ones this record actually has. */
  readonly dims: readonly number[];
}

// ---------------------------------------------------------------------------
// Normalisation
// ---------------------------------------------------------------------------

const COMBINING_MARKS = /[\u0300-\u036f]/g;
const DIGIT_X_DIGIT = /(\d)\s*[x\u00d7]\s*(\d)/g;
const NON_ALNUM = /[^a-z0-9]+/g;

/**
 * The form everything is compared in: lower case, unaccented, punctuation
 * collapsed to single spaces.
 *
 * `Wardrobe (2 door)` and `wardrobe 2 door` therefore look identical, which is
 * what lets a user type either.
 */
export function normaliseHaystack(raw: string): string {
  return raw
    .normalize('NFD')
    .replace(COMBINING_MARKS, '')
    .toLowerCase()
    .replace(DIGIT_X_DIGIT, '$1 $2')
    .replace(NON_ALNUM, ' ')
    .trim();
}

/**
 * The query, kept in a form the LENGTH parser can still read.
 *
 * Deliberately not {@link normaliseHaystack}: that would eat the `'` and `"` of
 * `12'6"` and the `.` of `1.8m`, and the dimension search would silently stop
 * working for everyone who types in feet. Punctuation is stripped per-term,
 * after the length parse has had its look.
 */
export function normaliseQuery(raw: string): string {
  return raw
    .normalize('NFD')
    .replace(COMBINING_MARKS, '')
    .toLowerCase()
    .replace(/[\u2032\u2019\u02b9\u00b4`]/g, "'")
    .replace(/[\u2033\u201d\u02ba]/g, '"')
    .replace(/(?<=[\d,]),(?=\d)/g, '')
    .replace(DIGIT_X_DIGIT, '$1 $2')
    .replace(/\s+/g, ' ')
    .trim();
}

// ---------------------------------------------------------------------------
// Index
// ---------------------------------------------------------------------------

function entryFor(record: AssetRecord): SearchEntry {
  const name = normaliseHaystack(record.name);
  const dims: number[] = [];
  if (record.widthMm !== null) dims.push(record.widthMm);
  if (record.depthMm !== null) dims.push(record.depthMm);
  if (record.heightMm !== null) dims.push(record.heightMm);
  const extra = normaliseHaystack(
    [record.category, record.categoryLabel, ...record.roomTypes, ...record.surfaceGroups].join(' '),
  );
  return {
    record,
    name,
    nameWords: name === '' ? [] : name.split(' '),
    id: normaliseHaystack(record.id),
    extra,
    dims,
  };
}

/**
 * Build the searchable index, sorted into browsing order.
 *
 * The sort is the order the list shows with an EMPTY query — furniture by
 * category then name, materials after — so "no query" is a browsable catalogue
 * rather than whatever order two `fetch` calls happened to resolve in.
 */
export function buildIndex(records: readonly AssetRecord[]): readonly SearchEntry[] {
  const entries = records.map(entryFor);
  entries.sort(compareForBrowsing);
  return entries;
}

function compareForBrowsing(a: SearchEntry, b: SearchEntry): number {
  const ra = categoryRank(a.record.kind, a.record.category);
  const rb = categoryRank(b.record.kind, b.record.category);
  if (ra !== rb) return ra - rb;
  const byName = a.record.name.localeCompare(b.record.name);
  return byName !== 0 ? byName : a.record.key.localeCompare(b.record.key);
}

// ---------------------------------------------------------------------------
// Query terms
// ---------------------------------------------------------------------------

/**
 * The smallest length worth reading as a dimension.
 *
 * 80 mm is the smallest depth in `fixtures/catalog/furniture.json` (a set of
 * wall-mounted service items). Below it a bare number in a query — "2 door",
 * "3 seater" — is a word, not a size, and treating "2" as 2 mm would make every
 * such query match nothing on the dimension path and mislead the ranking.
 */
export const DIM_MIN_MM = 80;

/**
 * How far off a typed dimension may be and still count as a match, in mm.
 *
 * 25 mm ≈ 1 inch: enough to absorb "bed 1500" finding the 1525 mm queen, not
 * enough to blur the catalogue's own 1500 / 1800 / 2100 size steps into each
 * other. Exact hits always outrank near ones (75 vs 45 below).
 */
export const DIM_TOLERANCE_MM = 25;

export interface QueryTerm {
  /** Haystack-normalised text form. May be '' when the term was only a number. */
  readonly text: string;
  /** Integer mm when the term parses as a length of at least {@link DIM_MIN_MM}. */
  readonly mm: number | null;
}

function parseTermMm(token: string): number | null {
  if (!/\d/.test(token)) return null;
  const parsed = tryParseLengthMm(token, 'mm');
  if (!parsed.ok) return null;
  return parsed.mm >= DIM_MIN_MM ? parsed.mm : null;
}

/**
 * Split a raw query into terms. A token that carries neither text nor a length
 * (a stray dash, say) is dropped rather than made to match everything.
 */
export function parseQuery(raw: string): readonly QueryTerm[] {
  const normalised = normaliseQuery(raw);
  if (normalised === '') return [];
  const terms: QueryTerm[] = [];
  for (const token of normalised.split(' ')) {
    const mm = parseTermMm(token);
    const text = normaliseHaystack(token);
    if (text === '' && mm === null) continue;
    terms.push({ text, mm });
  }
  return terms;
}

// ---------------------------------------------------------------------------
// Scoring
// ---------------------------------------------------------------------------

const SCORE_ID_EXACT = 100;
const SCORE_NAME_PREFIX = 80;
const SCORE_DIM_EXACT = 75;
const SCORE_WORD_PREFIX = 60;
const SCORE_DIM_NEAR = 45;
const SCORE_NAME_CONTAINS = 40;
const SCORE_ID_CONTAINS = 30;
const SCORE_EXTRA_CONTAINS = 15;
const SCORE_FUZZY = 8;
/** Penalty for matching only after a trailing plural was dropped. */
const PLURAL_PENALTY = 5;
/** Below this length a one-edit match is noise, not tolerance. */
const FUZZY_MIN_LEN = 4;

/** Text-only score for one term, or -1 when the term does not appear at all. */
function textScore(entry: SearchEntry, term: string): number {
  if (term === '') return -1;
  if (entry.id === term) return SCORE_ID_EXACT;
  if (entry.name.startsWith(term)) return SCORE_NAME_PREFIX;
  for (const word of entry.nameWords) {
    if (word.startsWith(term)) return SCORE_WORD_PREFIX;
  }
  if (entry.name.includes(term)) return SCORE_NAME_CONTAINS;
  if (entry.id.includes(term)) return SCORE_ID_CONTAINS;
  if (entry.extra.includes(term)) return SCORE_EXTRA_CONTAINS;
  return -1;
}

/**
 * True when `a` and `b` differ by at most one insertion, deletion or
 * substitution. Linear, with no DP table — this runs per word per item and a
 * matrix allocation here would be the frame budget.
 */
export function withinOneEdit(a: string, b: string): boolean {
  const la = a.length;
  const lb = b.length;
  if (la - lb > 1 || lb - la > 1) return false;
  let i = 0;
  let j = 0;
  let edits = 0;
  while (i < la && j < lb) {
    if (a[i] === b[j]) {
      i += 1;
      j += 1;
      continue;
    }
    edits += 1;
    if (edits > 1) return false;
    if (la === lb) {
      i += 1;
      j += 1;
    } else if (la > lb) {
      i += 1;
    } else {
      j += 1;
    }
  }
  return edits + (la - i) + (lb - j) <= 1;
}

/**
 * True when `a` and `b` differ only by one swap of adjacent characters.
 *
 * Separate from {@link withinOneEdit} because a transposition costs TWO edits
 * under plain Levenshtein — so `wardorbe` would not reach `wardrobe` without
 * this, and transposition is the single most common keyboard slip there is.
 */
export function isAdjacentSwap(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  const diffs: number[] = [];
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) {
      diffs.push(i);
      if (diffs.length > 2) return false;
    }
  }
  if (diffs.length !== 2) return false;
  const [p, q] = diffs as [number, number];
  return q === p + 1 && a[p] === b[q] && a[q] === b[p];
}

const HAS_LETTER = /[a-z]/;

/**
 * A one-edit match against a name word, or -1.
 *
 * BOTH sides must contain a letter, and that guard is not cosmetic. Without it
 * `1200` is one substitution from `1800`, so a search for a 1800 mm wardrobe
 * quietly returned the 1200 mm and 1500 mm ones too — the real corpus caught it
 * on the first run. Catalogue sizes are not typos of each other: a number is
 * either the size you meant or a different size, and the dimension path above
 * already answers that question exactly.
 */
function fuzzyScore(entry: SearchEntry, term: string): number {
  if (term.length < FUZZY_MIN_LEN || !HAS_LETTER.test(term)) return -1;
  for (const word of entry.nameWords) {
    if (word.length < FUZZY_MIN_LEN || !HAS_LETTER.test(word)) continue;
    if (withinOneEdit(word, term) || isAdjacentSwap(word, term)) return SCORE_FUZZY;
  }
  return -1;
}

function dimensionScore(entry: SearchEntry, mm: number): number {
  let best = -1;
  for (const dim of entry.dims) {
    if (dim === mm) return SCORE_DIM_EXACT;
    const delta = dim > mm ? dim - mm : mm - dim;
    if (delta <= DIM_TOLERANCE_MM) best = SCORE_DIM_NEAR;
  }
  return best;
}

/** Best score for one term across every path, or -1 when nothing matched. */
export function termScore(entry: SearchEntry, term: QueryTerm): number {
  let best = textScore(entry, term.text);

  if (best < 0 && term.text.endsWith('s') && term.text.length > FUZZY_MIN_LEN) {
    const singular = textScore(entry, term.text.slice(0, -1));
    if (singular >= 0) best = singular - PLURAL_PENALTY;
  }

  if (term.mm !== null) {
    const dim = dimensionScore(entry, term.mm);
    if (dim > best) best = dim;
  }

  if (best < 0) best = fuzzyScore(entry, term.text);

  return best;
}

/** Total score for a record, or -1 when any term fails to match (AND). */
export function scoreEntry(entry: SearchEntry, terms: readonly QueryTerm[]): number {
  let total = 0;
  for (const term of terms) {
    const score = termScore(entry, term);
    if (score < 0) return -1;
    total += score;
  }
  return total;
}

/**
 * Rank `entries` against `query`, best first.
 *
 * An empty query returns the input untouched — same array identity, so a
 * `useMemo` downstream keeping the un-searched list does no work and the row
 * components do not remount when the user clears the box.
 *
 * Ties break on category order, then name, then key. Fully deterministic: the
 * list must not reshuffle between renders for reasons a user cannot see.
 */
export function searchEntries(
  entries: readonly SearchEntry[],
  query: string,
): readonly SearchEntry[] {
  const terms = parseQuery(query);
  if (terms.length === 0) return entries;

  const hits: { entry: SearchEntry; score: number }[] = [];
  for (const entry of entries) {
    const score = scoreEntry(entry, terms);
    if (score >= 0) hits.push({ entry, score });
  }
  hits.sort((a, b) =>
    b.score !== a.score ? b.score - a.score : compareForBrowsing(a.entry, b.entry),
  );
  return hits.map((hit) => hit.entry);
}
