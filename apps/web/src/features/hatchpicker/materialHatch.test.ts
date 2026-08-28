/**
 * materialHatch.test.ts — A-10, checked against all 184 materials of record.
 *
 * The spec reads `fixtures/catalog/materials.json` itself rather than a
 * hand-copied sample, because the interesting failures are the ones a sample
 * would not contain: "Steel grey granite" is stone, "Wood-look vitrified
 * plank" is a tile, "Railing glass with SS" is glass, and each of those is a
 * real row in that file.
 *
 * THE ROAD UNDER TEST IS THE ONE A BROWSER TAKES. `texture` is optional in
 * that file and is not served to clients (see `materialHatch.ts`), so every
 * assertion here resolves from `id`, `name` and `category` — exactly what the
 * app holds. `EXPECTED_HATCHES` is the hand-authored answer key: it names a
 * material and the hatch an architect would expect on the section, and it is
 * written down here rather than derived, so it cannot quietly agree with a
 * mistake in the table it is testing.
 *
 * Where a row DOES carry a curated `texture`, the last block cross-checks the
 * two roads against each other. That block is deliberately allowed to compare
 * nothing when the field is absent — and the answer key above it is what stops
 * this file from being a suite that passes no matter what.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
  CATEGORY_HATCH,
  FALLBACK_PATTERN,
  MATERIAL_HATCH_OVERRIDES,
  TEXTURE_HATCH,
  TOKEN_HATCH,
  hatchForMaterial,
  hatchFromTokens,
  materialSegments,
  type MaterialLike,
} from './materialHatch';
import { isHatchPatternKey, type HatchPatternKey } from './patterns';

// See `pythonDefs.ts` for why this is `resolve(dirname(fileURLToPath(...)))`
// and not `new URL(rel, import.meta.url)`.
const CATALOGUE_PATH = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '../../../../../fixtures/catalog/materials.json',
);

interface CatalogueRow extends MaterialLike {
  readonly id: string;
  readonly name: string;
  readonly category: string;
  /** Optional in the file, never served to a client. */
  readonly texture?: string;
}

const CATALOGUE: readonly CatalogueRow[] = JSON.parse(
  readFileSync(CATALOGUE_PATH, 'utf8'),
) as readonly CatalogueRow[];

/** What a browser sees: no `texture`, whatever the file happens to carry. */
const asClientSees = (row: CatalogueRow): MaterialLike => ({
  id: row.id,
  name: row.name,
  category: row.category,
});

const row = (id: string): CatalogueRow => {
  const found = CATALOGUE.find((m) => m.id === id);
  if (found === undefined) throw new Error(`${id} is no longer in the catalogue`);
  return found;
};

const hatchOf = (id: string): HatchPatternKey => hatchForMaterial(asClientSees(row(id))).pattern;

/**
 * The answer key: real catalogue ids, and the hatch a municipal section wants
 * for each. Every one of the nine patterns the catalogue can reach appears,
 * and the second half is the traps — colour words, look-alikes and coatings
 * that a naive reading gets wrong.
 */
const EXPECTED_HATCHES: readonly (readonly [string, HatchPatternKey])[] = [
  ['exposed-brick', 'brick'],
  ['brick-exposed-wirecut', 'brick'],
  ['terracotta-jaali', 'brick'],
  ['exposed-concrete', 'concrete'],
  ['concrete-wall-board-formed', 'concrete'],
  ['cement-ips', 'concrete'],
  ['ips-red-oxide', 'concrete'],
  ['epoxy-floor-coat', 'concrete'],
  ['grc-jaali', 'concrete'],
  ['kota-stone', 'stone'],
  ['shahabad-stone', 'stone'],
  ['sandstone-red', 'stone'],
  ['marble-makrana', 'stone'],
  ['granite-black-galaxy', 'stone'],
  ['terrazzo-grey', 'stone'],
  ['slate-cladding-black', 'stone'],
  ['teak-door', 'timber'],
  ['flush-door', 'timber'],
  ['bamboo-flooring', 'timber'],
  ['wood-panel-teak-veneer', 'timber'],
  ['wooden-handrail', 'timber'],
  ['wpc-cladding', 'timber'],
  ['glass-clear', 'glass'],
  ['glazing-float-dgu-24', 'glass'],
  ['glass-railing', 'glass'],
  ['ms-railing', 'steel'],
  ['aluminium-window', 'steel'],
  ['acp-panel', 'steel'],
  ['railing-wrought-iron', 'steel'],
  ['metal-roof-sheet', 'steel'],
  ['interior-emulsion', 'plaster'],
  ['lime-plaster-araish', 'plaster'],
  ['distemper-economy', 'plaster'],
  ['waterproof-membrane', 'plaster'],
  ['cool-roof-coat', 'plaster'],
  ['vitrified-tile-600', 'tile'],
  ['ceramic-wall-tile', 'tile'],
  ['clay-roof-tile', 'tile'],
  ['china-mosaic', 'tile'],
  ['paver-cobble-80', 'tile'],
  ['artificial-grass-mat', 'grass'],
  // The traps.
  ['granite-steel-grey', 'stone'], // "Steel grey granite" is not steel
  ['vitrified-plank-wood', 'tile'], // a wood-LOOK ceramic is a ceramic
  ['vitrified-concrete-800', 'tile'],
  ['wpc-deck-tile', 'timber'], // …and a WPC deck tile is not a ceramic
  ['paint-exterior-sand', 'plaster'], // "sand" is the colour, not a fill
  ['paint-interior-terracotta', 'plaster'],
  ['kitchen-tile-gloss-white', 'tile'], // "gloss-white" is not stainless steel
  ['roof-brick-bat-coba', 'tile'], // brick bat coba is a terrace screed
  ['railing-glass-ss', 'glass'], // the panel, not the posts
  ['frame-teak', 'timber'], // filed with the metal frames; still timber
  ['roof-metal-standing-seam', 'steel'], // filed with the roof tiles; still metal
  ['cement-plaster-natural', 'plaster'], // "cement" plaster is a render
  ['pvc-wall-panel', 'tile'],
  ['upvc-window', 'steel'], // uPVC frames sit with the metal joinery
  ['hpl-cladding', 'tile'],
];

describe('the catalogue this mapping is authored against', () => {
  it('is the real 184-row file, with the fields the mapping reads', () => {
    // If this is ever not true, every assertion below is measuring nothing.
    expect(CATALOGUE).toHaveLength(184);
    for (const item of CATALOGUE) {
      expect(typeof item.id, item.id).toBe('string');
      expect(typeof item.name, item.id).toBe('string');
      expect(typeof item.category, item.id).toBe('string');
    }
  });
});

describe('every material binds to a real pattern', () => {
  it('matches the answer key, material by material', () => {
    const wrong = EXPECTED_HATCHES.filter(([id, expected]) => hatchOf(id) !== expected).map(
      ([id, expected]) => `${id}: expected ${expected}, got ${hatchOf(id)}`,
    );
    expect(wrong).toEqual([]);
    // Every pattern the catalogue can reach is exercised, so a table that
    // collapsed everything onto one hatch could not pass this file.
    expect(new Set(EXPECTED_HATCHES.map(([, pattern]) => pattern)).size).toBe(9);
  });

  it('answers with a key the pattern library carries, and says why', () => {
    for (const item of CATALOGUE) {
      const hatch = hatchForMaterial(asClientSees(item));
      expect(isHatchPatternKey(hatch.pattern), item.id).toBe(true);
      expect(hatch.why.length, `${item.id} gave no reason`).toBeGreaterThan(0);
    }
  });

  it('nothing falls through to the generic hatch — the fallback is for the unknown', () => {
    const weak = CATALOGUE.map((item) => ({ item, hatch: hatchForMaterial(asClientSees(item)) }))
      .filter(({ hatch }) => hatch.source === 'category' || hatch.source === 'fallback')
      .map(({ item, hatch }) => `${item.id} (${hatch.source} → ${hatch.pattern})`);
    expect(
      weak,
      'This material names no material a browser can recognise, so it hatches as its ' +
        'category default or the generic section. Add a TOKEN_HATCH row for the word ' +
        'that identifies it.',
    ).toEqual([]);
  });
});

describe('the tables themselves', () => {
  it('covers every category the catalogue uses', () => {
    const categories = [...new Set(CATALOGUE.map((item) => item.category))].sort();
    expect(categories).toEqual(['floor', 'glazing', 'joinery', 'railing', 'roof', 'wall']);
    for (const category of categories) {
      expect(
        CATEGORY_HATCH[category],
        `no default hatch for the "${category}" category`,
      ).toBeDefined();
    }
    // And no row for a category the catalogue does not have — a default that
    // can never fire is the "83 rules went inert" failure in miniature.
    expect(Object.keys(CATEGORY_HATCH).sort()).toEqual(categories);
  });

  it('maps all ten curated textures, to real patterns', () => {
    expect(Object.keys(TEXTURE_HATCH).sort()).toEqual([
      'brick',
      'concrete',
      'glass',
      'metal',
      'plaster',
      'speckle',
      'stone',
      'tile',
      'vein',
      'wood',
    ]);
    for (const pattern of Object.values(TEXTURE_HATCH)) {
      expect(isHatchPatternKey(pattern)).toBe(true);
    }
    // …and any texture the file actually carries is one of them.
    for (const item of CATALOGUE) {
      if (item.texture === undefined) continue;
      expect(TEXTURE_HATCH[item.texture], `unmapped texture "${item.texture}"`).toBeDefined();
    }
  });

  it('maps every token to a real pattern, and lets every token fire', () => {
    // Shadowing check: an earlier token that also matches this one's own word
    // would make the later rule dead code — which is how "gloss-white"
    // resolving to steel through the "ss" rule got caught in the first place.
    for (const [token, pattern] of TOKEN_HATCH) {
      expect(isHatchPatternKey(pattern), token).toBe(true);
      const probe = hatchFromTokens({ id: token, name: token });
      expect(probe?.pattern, `the "${token}" rule is shadowed by an earlier token`).toBe(pattern);
    }
  });

  it('overrides name materials that exist, and win on both roads', () => {
    for (const [id, override] of MATERIAL_HATCH_OVERRIDES) {
      const item = CATALOGUE.find((m) => m.id === id);
      expect(item, `${id} is overridden but is not in the catalogue`).toBeDefined();
      expect(isHatchPatternKey(override.pattern), id).toBe(true);
      expect(override.why.length, `${id} overrides without a reason`).toBeGreaterThan(20);
      if (item === undefined) continue;
      expect(hatchForMaterial(item).source).toBe('override');
      expect(hatchForMaterial(item).pattern).toBe(override.pattern);
      expect(hatchForMaterial(asClientSees(item)).pattern).toBe(override.pattern);
    }
    expect(MATERIAL_HATCH_OVERRIDES.size).toBeGreaterThan(0);
  });
});

describe('resolution order and the deliberate fallback', () => {
  it('override beats texture beats token beats category beats fallback', () => {
    // One material, five answers, by removing one input at a time.
    expect(hatchForMaterial({ id: 'frame-teak', texture: 'metal' }).source).toBe('override');
    expect(hatchForMaterial({ id: 'anything', texture: 'brick' }).pattern).toBe('brick');
    expect(hatchForMaterial({ id: 'teak-shutter', texture: 'brick' }).pattern).toBe('brick');
    expect(hatchForMaterial({ id: 'teak-shutter' }).pattern).toBe('timber');
    expect(hatchForMaterial({ id: 'mystery-panel', category: 'roof' })).toEqual({
      pattern: 'tile',
      source: 'category',
      why: expect.stringContaining('roof') as unknown as string,
    });
    const unknown = hatchForMaterial({ id: 'zzz-9', name: 'Something new', category: 'sculpture' });
    expect(unknown.pattern).toBe(FALLBACK_PATTERN);
    expect(unknown.source).toBe('fallback');
    expect(unknown.why).toMatch(/fall/i);
  });

  it('ignores a texture it does not know rather than trusting it', () => {
    const odd = hatchForMaterial({ id: 'teak-shutter', texture: 'holographic' });
    expect(odd.pattern).toBe('timber');
    expect(odd.source).toBe('token');
  });

  it('splits ids and names into segments, not substrings', () => {
    expect(materialSegments({ id: 'wall-tile-300x600', name: 'Wall tile 300 × 600' })).toEqual([
      'wall',
      'tile',
      '300x600',
      'wall',
      'tile',
      '300',
      '600',
    ]);
    // The bug this replaced: "gloss-white" contains "ss-".
    expect(hatchFromTokens({ id: 'kitchen-tile-gloss-white', name: '' })?.pattern).toBe('tile');
  });
});

describe('the curated texture, where the catalogue carries one', () => {
  it('agrees with the reading a browser makes from the name', () => {
    const carried = CATALOGUE.filter((item) => typeof item.texture === 'string');
    const disagreements = carried
      .filter(
        (item) => hatchForMaterial(item).pattern !== hatchForMaterial(asClientSees(item)).pattern,
      )
      .map(
        (item) =>
          `${item.id} (texture "${String(item.texture)}" → ${hatchForMaterial(item).pattern}, ` +
          `name → ${hatchForMaterial(asClientSees(item)).pattern})`,
      );
    expect(
      disagreements,
      'A material whose name implies a different hatch from its catalogue texture. ' +
        'Decide which is right: reorder TOKEN_HATCH, or add a MATERIAL_HATCH_OVERRIDES row ' +
        'with the reason — an override applies on both roads, so it settles the conflict.',
    ).toEqual([]);
    // Not an assertion about the file, a note in the output: this block
    // compares nothing when the field is absent, and the answer key above is
    // what carries the suite in that case.
    expect(carried.length === 0 || carried.length === CATALOGUE.length).toBe(true);
  });
});
