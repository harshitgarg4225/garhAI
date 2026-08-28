/**
 * materialHatch.ts — A-10: which hatch a material implies.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT THIS IS FOR
 * ════════════════════════════════════════════════════════════════════════════
 * An architect assigns "Exposed brick" to the external walls. The section must
 * then poché those walls as BRICK, concrete as concrete, timber as timber —
 * without anyone opening a pattern picker. That is the whole of A-10: a
 * material carries its hatch, and the picker (A-9) exists for the cases where
 * the implication is wrong.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE AWKWARD FACT ABOUT THE CATALOGUE, STATED PLAINLY
 * ════════════════════════════════════════════════════════════════════════════
 * A catalogue row may carry a curated `texture` — ten values (tile, plaster,
 * wood, metal, speckle, stone, concrete, vein, glass, brick) that map onto
 * hatches almost one to one, and the ideal input for this mapping. Two things
 * are true about it: it is OPTIONAL in the file (`fixtures/catalog/index.json`
 * does not require it), and it never reaches the browser even when present —
 * `MaterialOut` (apps/api/garh_api/routers/catalog.py) does not serve it and
 * `materialItemSchema` (apps/web/src/lib/schemas.ts) would strip it if it did.
 * What a client actually holds is `id`, `name`, `category`, `colorHex` and
 * `surfaceGroups`.
 *
 * `finish` is served and is deliberately NOT used as a signal: it describes a
 * surface treatment, not a material. "matte" spans tile, plaster and concrete;
 * "polished" spans granite, marble and steel. Reading it as evidence of a
 * material would be a rule that fires confidently and wrongly.
 *
 * A binding that only worked from `texture` would therefore be a module that
 * believes it is wired up and is not — the fourth bug class in CLAUDE.md, and
 * exactly what "the furniture layer tagged its meshes and never called the
 * registry" looked like. So the resolution has two roads to the same answer:
 *
 *   1. `MATERIAL_HATCH_OVERRIDES` — four materials the catalogue's own fields
 *      get wrong. Consulted first on BOTH roads, so they cannot disagree.
 *   2. `TEXTURE_HATCH` — used when a caller has a `texture` (fixtures, tests,
 *      and the client the moment the schema handoff below lands).
 *   3. `TOKEN_HATCH` — an ordered read of the material's id and name, which is
 *      what the client actually has today.
 *   4. `CATEGORY_HATCH` — the six catalogue categories, for a material whose
 *      words say nothing.
 *   5. `FALLBACK_PATTERN` — deliberate, named, and reported as a fallback so
 *      the UI can say "we guessed" instead of quietly drawing masonry.
 *
 * `materialHatch.test.ts` walks every row of the catalogue of record down road
 * 3 — the one a browser takes — and holds it to two things: a table of hatches
 * an architect would recognise, and (for every row that carries one) the
 * curated `texture`. A new material whose name reads differently from its
 * texture turns the suite red and asks a human which one is right, which is
 * the correct outcome and not a silent guess.
 *
 * HANDOFF (see index.ts): adding `texture` to `MaterialOut` and to
 * `materialItemSchema` upgrades every client from road 3 to road 2 with no
 * change here.
 */

import type { HatchPatternKey } from './patterns';

/** What a caller knows about a material. Only `id` is guaranteed. */
export interface MaterialLike {
  readonly id: string;
  readonly name?: string | undefined;
  readonly category?: string | undefined;
  /** Catalogue-only today — see the header. */
  readonly texture?: string | undefined;
}

/** Which road produced the answer. Shown in the UI; asserted in the specs. */
export type HatchBindingSource = 'override' | 'texture' | 'token' | 'category' | 'fallback';

export interface MaterialHatch {
  readonly pattern: HatchPatternKey;
  readonly source: HatchBindingSource;
  /** One line an architect can read, naming the evidence. */
  readonly why: string;
}

/**
 * Materials whose own catalogue fields point at the wrong hatch. Four, each a
 * judgement rather than a typo, and each applied on both roads so the token
 * and texture answers stay identical.
 */
export const MATERIAL_HATCH_OVERRIDES: ReadonlyMap<
  string,
  { pattern: HatchPatternKey; why: string }
> = new Map([
  [
    'artificial-grass-mat',
    {
      pattern: 'grass' as HatchPatternKey,
      // Filed with the speckled floors, beside granite and terrazzo, which
      // would hatch it as stone. It is soft landscape, and GRASS is the
      // pattern a site plan needs for it.
      why: 'Soft landscape — drawn with the grass pattern, not the stone its catalogue neighbours imply.',
    },
  ],
  [
    'frame-teak',
    {
      pattern: 'timber' as HatchPatternKey,
      // Filed with the other window frames, which are metal. A teak frame is
      // timber in section whatever the frame family says.
      why: 'A teak window frame is timber in section, though it is catalogued with the metal frames.',
    },
  ],
  [
    'railing-glass-ss',
    {
      pattern: 'glass' as HatchPatternKey,
      // Filed with the metal railings, which describes the stainless posts;
      // the surface a section cuts through is the glass panel.
      why: 'The panel is glass; the stainless steel is only the posts.',
    },
  ],
  [
    'roof-metal-standing-seam',
    {
      pattern: 'steel' as HatchPatternKey,
      // Filed among the roof coverings, which are tiles. A standing-seam
      // roof is a metal sheet.
      why: 'A standing-seam roof is sheet metal, though it is catalogued with the roof tiles.',
    },
  ],
]);

/**
 * The catalogue's ten curated textures → the pattern library. The most direct
 * road, and the one the token road is measured against.
 *
 * `speckle` (granite, terrazzo) and `vein` (marble) both land on STONE: a
 * municipal section distinguishes stone from concrete from brick, and does not
 * distinguish granite from marble.
 */
export const TEXTURE_HATCH: Readonly<Record<string, HatchPatternKey>> = {
  brick: 'brick',
  concrete: 'concrete',
  glass: 'glass',
  metal: 'steel',
  plaster: 'plaster',
  speckle: 'stone',
  stone: 'stone',
  tile: 'tile',
  vein: 'stone',
  wood: 'timber',
};

/**
 * Words in a material's id and name, in PRIORITY ORDER — the first token that
 * matches wins.
 *
 * Order is the whole design here, and it is not alphabetical or arbitrary:
 *
 *  * Stone first, because granite and marble names borrow other materials'
 *    words ("Steel grey granite" is stone, not steel).
 *  * Glazing before metal, so "Toughened glass railing" is glass rather than
 *    the steel of its posts.
 *  * Paints and renders before the colour words they borrow ("Exterior
 *    emulsion sand", "Interior emulsion terracotta").
 *  * The engineered boards — vitrified, ceramic, WPC — before the look they
 *    imitate: "Wood-look vitrified plank" is a ceramic tile in section, and
 *    "WPC deck tile" is a timber composite.
 *  * `coat` last, because "Colour-coated metal roof sheet" and "powder-coated
 *    aluminium" both carry it and neither is a coating in the hatch sense.
 *
 * Matching is on SLUG SEGMENTS, prefix-wise — `id` and `name` are split on
 * non-alphanumerics and a token matches a segment that starts with it. Plain
 * substring matching is what made "kitchen-tile-gloss-white" resolve to steel:
 * "gloss-white" contains "ss-". `materialHatch.test.ts` proves no token is
 * shadowed by an earlier one.
 */
export const TOKEN_HATCH: readonly (readonly [string, HatchPatternKey])[] = [
  // Stone, and the Indian stones by name.
  ['granite', 'stone'],
  ['marble', 'stone'],
  ['terrazzo', 'stone'],
  ['kota', 'stone'],
  ['sandstone', 'stone'],
  ['shahabad', 'stone'],
  ['tandur', 'stone'],
  ['slate', 'stone'],
  ['cudappah', 'stone'],
  ['dholpur', 'stone'],
  ['jaisalmer', 'stone'],
  ['stone', 'stone'],
  // Soft landscape.
  ['grass', 'grass'],
  ['lawn', 'grass'],
  ['turf', 'grass'],
  // Glazing before the metal that frames it.
  ['glazing', 'glass'],
  ['glass', 'glass'],
  // Metal. `upvc` precedes `pvc` so a uPVC frame is not read as a PVC panel.
  ['upvc', 'steel'],
  ['aluminium', 'steel'],
  ['acp', 'steel'],
  ['ms', 'steel'],
  ['ss', 'steel'],
  ['stainless', 'steel'],
  ['wrought', 'steel'],
  ['iron', 'steel'],
  ['metal', 'steel'],
  ['steel', 'steel'],
  // Paints and renders.
  ['emulsion', 'plaster'],
  ['paint', 'plaster'],
  ['distemper', 'plaster'],
  ['plaster', 'plaster'],
  ['putty', 'plaster'],
  ['membrane', 'plaster'],
  ['texture', 'plaster'],
  // Engineered boards, before the material they imitate.
  ['vitrified', 'tile'],
  ['ceramic', 'tile'],
  ['mosaic', 'tile'],
  ['paver', 'tile'],
  ['hpl', 'tile'],
  ['pvc', 'tile'],
  // Timber and its composites.
  ['wpc', 'timber'],
  ['bamboo', 'timber'],
  ['teak', 'timber'],
  ['wood', 'timber'],
  ['laminate', 'timber'],
  ['veneer', 'timber'],
  ['flush', 'timber'],
  ['louver', 'timber'],
  ['handrail', 'timber'],
  // Fired clay. `coba` (brick bat coba, a lime-and-brick terrace finish) is
  // laid as a tiled screed, so it is caught before `brick`.
  ['coba', 'tile'],
  ['brick', 'brick'],
  ['tile', 'tile'],
  // Cementitious.
  ['epoxy', 'concrete'],
  ['ips', 'concrete'],
  ['oxide', 'concrete'],
  ['grc', 'concrete'],
  ['concrete', 'concrete'],
  ['cement', 'concrete'],
  // Joinery left-overs, after the material words above have had their say.
  ['door', 'timber'],
  // A terracotta that is not a tile is fired clay: jaali screens, pots.
  ['terracotta', 'brick'],
  ['coat', 'plaster'],
];

/** The six categories `fixtures/catalog/materials.json` uses. */
export const CATEGORY_HATCH: Readonly<Record<string, HatchPatternKey>> = {
  floor: 'tile',
  wall: 'plaster',
  joinery: 'timber',
  roof: 'tile',
  glazing: 'glass',
  railing: 'steel',
};

/**
 * When nothing else fires. ANSI31 is what `projection/primitives.py` calls
 * PATTERN_MASONRY and what the plan already poachés walls with, so an unknown
 * material draws as the drawing's generic section rather than as a guess at
 * some specific material.
 */
export const FALLBACK_PATTERN: HatchPatternKey = 'diagonal';

/** `id` + `name`, lowercased and split on non-alphanumerics. */
export function materialSegments(material: MaterialLike): string[] {
  return `${material.id} ${material.name ?? ''}`
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((segment) => segment.length > 0);
}

/** The token road on its own. Exported so the specs can compare the two roads. */
export function hatchFromTokens(material: MaterialLike): MaterialHatch | null {
  const segments = materialSegments(material);
  for (const [token, pattern] of TOKEN_HATCH) {
    if (segments.some((segment) => segment.startsWith(token))) {
      return { pattern, source: 'token', why: `Named "${token}" — hatched as ${pattern}.` };
    }
  }
  return null;
}

export interface HatchForMaterialOptions {
  /**
   * Ignore `material.texture` even when it is present. The specs use this to
   * walk the whole catalogue down the road a browser actually takes.
   */
  readonly ignoreTexture?: boolean;
}

/** The hatch a material implies. Total: always answers, always says why. */
export function hatchForMaterial(
  material: MaterialLike,
  { ignoreTexture = false }: HatchForMaterialOptions = {},
): MaterialHatch {
  const override = MATERIAL_HATCH_OVERRIDES.get(material.id);
  if (override !== undefined) {
    return { pattern: override.pattern, source: 'override', why: override.why };
  }

  const texture = material.texture;
  if (!ignoreTexture && texture !== undefined) {
    const fromTexture = TEXTURE_HATCH[texture];
    if (fromTexture !== undefined) {
      return {
        pattern: fromTexture,
        source: 'texture',
        why: `Catalogue texture "${texture}" — hatched as ${fromTexture}.`,
      };
    }
  }

  const fromTokens = hatchFromTokens(material);
  if (fromTokens !== null) return fromTokens;

  const category = material.category;
  if (category !== undefined) {
    const fromCategory = CATEGORY_HATCH[category];
    if (fromCategory !== undefined) {
      return {
        pattern: fromCategory,
        source: 'category',
        why: `No material word to go on — hatched as the ${category} default, ${fromCategory}.`,
      };
    }
  }

  return {
    pattern: FALLBACK_PATTERN,
    source: 'fallback',
    why: `Nothing in this material names a material — falling back to the generic section hatch. Pick a pattern if that is wrong.`,
  };
}
