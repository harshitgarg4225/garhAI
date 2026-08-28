/**
 * patterns.ts — the TypeScript MIRROR of `services/drawings/render/hatch_patterns.py`.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY A MIRROR AT ALL, GIVEN THIS REPO'S HISTORY WITH HAND-KEPT TABLES
 * ════════════════════════════════════════════════════════════════════════════
 * Two hand-kept copies of one table is exactly how the three hatch defects of
 * 2026-08 happened (earth drawn as cross, hatches 31x too dense, the angle
 * applied twice) — the Python module's own docstring says so. So this file is
 * not "a TS version of the pattern library"; it is a copy that a test refuses
 * to let drift.
 *
 * The picker cannot avoid needing the numbers on the client: a swatch has to
 * draw the REAL line families in SVG, in the browser, with no round trip. What
 * it can avoid is being a second source of truth, and that is what
 * `patterns.drift.test.ts` enforces — it parses the Python file at test time
 * and deep-equals it against this array, ORDER INCLUDED. Add a pattern in
 * Python and forget this file: red. Edit an angle here: red. Edit a label
 * there: red.
 *
 * Every value below was GENERATED from the Python defs, not retyped, and the
 * float literals are Python's shortest round-trip reprs, which parse to the
 * identical IEEE-754 double in JS. That is why the drift test can compare with
 * `toEqual` and not a tolerance — an exact match is achievable here, and a
 * tolerance would be a gate that cannot go red for small drifts.
 *
 * Reading a definition (restated from the Python, because a caller here never
 * sees that file): each line family is one ACAD pattern line — an angle, a
 * base point, an offset to the next line of the family, and a dash pattern,
 * all in pattern units (millimetres, ISO measurement). `offset` is in pattern
 * space, NOT the family's rotated frame, so the perpendicular spacing is the
 * component of `offset` normal to the line direction (`perpSpacing` in
 * `geometry.ts`). An empty `dashes` array is a continuous line; in ACAD dash
 * lists a negative is a gap and a zero is a dot.
 */

/** One family of parallel lines within a pattern. */
export interface HatchLine {
  readonly angleDeg: number;
  readonly base: readonly [number, number];
  readonly offset: readonly [number, number];
  /** ACAD dash list: positive draws, negative skips, zero is a dot. */
  readonly dashes: readonly number[];
}

/** A named pattern: what DXF calls it, and the geometry both writers draw. */
export interface HatchPatternDef {
  readonly key: HatchPatternKey;
  readonly acadName: string;
  readonly label: string;
  readonly lines: readonly HatchLine[];
}

/**
 * The table, in the Python module's own insertion order — which is the order
 * the picker shows, so "solid, then the generic sections, then the material
 * patterns" survives from the library to the UI.
 */
export const HATCH_PATTERNS: readonly HatchPatternDef[] = [
  {
    key: 'solid',
    acadName: 'SOLID',
    label: 'Solid fill',
    lines: [],
  },
  {
    key: 'diagonal',
    acadName: 'ANSI31',
    label: 'Diagonal / generic section',
    lines: [{ angleDeg: 45, base: [0, 0], offset: [-2.2450640303, 2.2450640303], dashes: [] }],
  },
  {
    key: 'cross',
    acadName: 'ANSI37',
    label: 'Cross hatch',
    lines: [
      { angleDeg: 45, base: [0, 0], offset: [-2.2450640303, 2.2450640303], dashes: [] },
      { angleDeg: 135, base: [0, 0], offset: [-2.2450640303, -2.2450640303], dashes: [] },
    ],
  },
  {
    key: 'earth',
    acadName: 'EARTH',
    label: 'Earth / soil',
    lines: [
      { angleDeg: 0, base: [0, 0], offset: [6.35, 6.35], dashes: [6.35, -6.35] },
      { angleDeg: 0, base: [0, 2.38125], offset: [6.35, 6.35], dashes: [6.35, -6.35] },
      { angleDeg: 0, base: [0, 4.7625], offset: [6.35, 6.35], dashes: [6.35, -6.35] },
      { angleDeg: 90, base: [0.79375, 5.55625], offset: [-6.35, 6.35], dashes: [6.35, -6.35] },
      { angleDeg: 90, base: [3.175, 5.55625], offset: [-6.35, 6.35], dashes: [6.35, -6.35] },
      { angleDeg: 90, base: [5.55625, 5.55625], offset: [-6.35, 6.35], dashes: [6.35, -6.35] },
    ],
  },
  {
    key: 'brick',
    acadName: 'BRICK',
    label: 'Brick masonry',
    lines: [
      { angleDeg: 0, base: [0, 0], offset: [0, 6.35], dashes: [] },
      { angleDeg: 90, base: [0, 0], offset: [-12.7, 0], dashes: [6.35, -6.35] },
      { angleDeg: 90, base: [6.35, 0], offset: [-12.7, 0], dashes: [-6.35, 6.35] },
    ],
  },
  {
    key: 'concrete',
    acadName: 'AR-CONC',
    label: 'Concrete (RCC)',
    lines: [
      {
        angleDeg: 50,
        base: [0, 0],
        offset: [182.184668996, -15.9390855389],
        dashes: [19.05, -209.55],
      },
      {
        angleDeg: 355,
        base: [0, 0],
        offset: [-35.243421425, 191.056845239],
        dashes: [15.24, -167.64058417],
      },
      {
        angleDeg: 100.4514447,
        base: [15.182007, -1.3282535],
        offset: [146.9412470904, 175.1177519122],
        dashes: [16.1900088, -178.0902446],
      },
      {
        angleDeg: 46.1842,
        base: [0, 50.8],
        offset: [271.0790408921, -42.0423279327],
        dashes: [28.575, -314.325],
      },
      {
        angleDeg: 96.63555761,
        base: [22.5899, 47.2965],
        offset: [237.4041340515, 247.4261245977],
        dashes: [24.28502314, -267.13560816],
      },
      {
        angleDeg: 351.18415117,
        base: [0, 50.8],
        offset: [237.404134065, 247.4261245855],
        dashes: [22.85996707, -251.45973192],
      },
      {
        angleDeg: 21,
        base: [25.4, 38.1],
        offset: [151.6143912691, -102.2651981871],
        dashes: [19.05, -209.55],
      },
      {
        angleDeg: 326,
        base: [25.4, 38.1],
        offset: [61.8020283476, 184.1879661223],
        dashes: [15.24, -167.64],
      },
      {
        angleDeg: 71.451445,
        base: [38.0345326, 29.5779001],
        offset: [213.4164192787, 81.9227688859],
        dashes: [16.1900088, -178.0899376],
      },
      {
        angleDeg: 37.5,
        base: [0, 0],
        offset: [3.0886032506, 84.5550388732],
        dashes: [0, -165.608, 0, -170.18, 0, -168.275],
      },
      {
        angleDeg: 7.5,
        base: [0, 0],
        offset: [66.8196625103, 100.1805748181],
        dashes: [0, -97.028, 0, -161.798, 0, -64.135],
      },
      {
        angleDeg: -32.5,
        base: [-56.642, 0],
        offset: [135.5905951669, -5.7287439927],
        dashes: [0, -63.5, 0, -198.12, 0, -262.89],
      },
      {
        angleDeg: -42.5,
        base: [-82.042, 0],
        offset: [148.129181386, 25.4264910333],
        dashes: [0, -82.55, 0, -131.572, 0, -186.69],
      },
    ],
  },
  {
    key: 'insulation',
    acadName: 'INSUL',
    label: 'Insulation',
    lines: [
      { angleDeg: 0, base: [0, 0], offset: [0, 9.525], dashes: [] },
      { angleDeg: 0, base: [0, 3.175], offset: [0, 9.525], dashes: [3.175, -3.175] },
      { angleDeg: 0, base: [0, 6.35], offset: [0, 9.525], dashes: [3.175, -3.175] },
    ],
  },
  {
    key: 'plaster',
    acadName: 'PLAST',
    label: 'Plaster',
    lines: [
      { angleDeg: 0, base: [0, 0], offset: [0, 6.35], dashes: [] },
      { angleDeg: 0, base: [0, 0.79375], offset: [0, 6.35], dashes: [] },
      { angleDeg: 0, base: [0, 1.5875], offset: [0, 6.35], dashes: [] },
    ],
  },
  {
    key: 'stone',
    acadName: 'BRSTONE',
    label: 'Stone masonry',
    lines: [
      { angleDeg: 0, base: [0, 0], offset: [0, 8.382], dashes: [] },
      { angleDeg: 90, base: [22.86, 0], offset: [-12.7, 8.382], dashes: [8.382, -8.382] },
      { angleDeg: 90, base: [20.32, 0], offset: [-12.7, 8.382], dashes: [8.382, -8.382] },
      { angleDeg: 0, base: [22.86, 1.397], offset: [12.7, 8.382], dashes: [-22.86, 2.54] },
      { angleDeg: 0, base: [22.86, 2.794], offset: [12.7, 8.382], dashes: [-22.86, 2.54] },
      { angleDeg: 0, base: [22.86, 4.191], offset: [12.7, 8.382], dashes: [-22.86, 2.54] },
      { angleDeg: 0, base: [22.86, 5.588], offset: [12.7, 8.382], dashes: [-22.86, 2.54] },
      { angleDeg: 0, base: [22.86, 6.985], offset: [12.7, 8.382], dashes: [-22.86, 2.54] },
    ],
  },
  {
    key: 'steel',
    acadName: 'STEEL',
    label: 'Steel',
    lines: [
      { angleDeg: 45, base: [0, 0], offset: [-5.3881536726, 5.3881536726], dashes: [] },
      { angleDeg: 45, base: [4.318, 0], offset: [-5.3881536726, 5.3881536726], dashes: [] },
      { angleDeg: 45, base: [4.572, 0], offset: [-5.3881536726, 5.3881536726], dashes: [] },
      { angleDeg: 45, base: [4.826, 0], offset: [-5.3881536726, 5.3881536726], dashes: [] },
      { angleDeg: 45, base: [5.08, 0], offset: [-5.3881536726, 5.3881536726], dashes: [] },
      { angleDeg: 45, base: [5.334, 0], offset: [-5.3881536726, 5.3881536726], dashes: [] },
      { angleDeg: 45, base: [5.588, 0], offset: [-5.3881536726, 5.3881536726], dashes: [] },
      { angleDeg: 45, base: [5.842, 0], offset: [-5.3881536726, 5.3881536726], dashes: [] },
    ],
  },
  {
    key: 'glass',
    acadName: 'GOST_GLASS',
    label: 'Glazing',
    lines: [
      { angleDeg: 45, base: [0, 0], offset: [8.4852813742, 0], dashes: [5, -7] },
      { angleDeg: 45, base: [2.12132, 0], offset: [8.4852813742, 0], dashes: [2, -10] },
      { angleDeg: 45, base: [0, 2.12132], offset: [8.4852813742, 0], dashes: [2, -10] },
    ],
  },
  {
    key: 'sand',
    acadName: 'AR-SAND',
    label: 'Sand filling',
    lines: [
      {
        angleDeg: 37.5,
        base: [0, 0],
        offset: [-1.600031296, 48.9413237329],
        dashes: [0, -38.608, 0, -43.18, 0, -41.275],
      },
      {
        angleDeg: 7.5,
        base: [0, 0],
        offset: [44.9523283138, 71.6825100568],
        dashes: [0, -20.828, 0, -34.798, 0, -13.335],
      },
      {
        angleDeg: -32.5,
        base: [-31.242, 0],
        offset: [79.0992370241, 0.1437184679],
        dashes: [0, -12.7, 0, -45.72, 0, -59.69],
      },
      {
        angleDeg: -42.5,
        base: [-31.242, 0],
        offset: [76.3556452472, 22.2929323257],
        dashes: [0, -6.35, 0, -29.972, 0, -34.29],
      },
    ],
  },
  {
    key: 'timber',
    acadName: 'WOOD1',
    label: 'Timber',
    lines: [
      {
        angleDeg: 216.8699,
        base: [8.128, 20.32],
        offset: [60.9599983302, 40.6400025047],
        dashes: [10.16, -91.44],
      },
      {
        angleDeg: 206.5651,
        base: [20.32, 14.224],
        offset: [-20.3199828894, -20.3200266069],
        dashes: [22.71845088, -22.71845088],
      },
      {
        angleDeg: 198.4349,
        base: [20.32, 4.064],
        offset: [-40.6400088309, -20.3199675535],
        dashes: [12.85150592, -51.40598304],
      },
    ],
  },
  {
    key: 'tile',
    acadName: 'AR-HBONE',
    label: 'Tile flooring (herringbone)',
    lines: [
      { angleDeg: 45, base: [0, 0], offset: [0, 143.6840979371], dashes: [304.8, -101.6] },
      {
        angleDeg: 135,
        base: [71.842, 71.842],
        offset: [0, 143.6840979371],
        dashes: [304.8, -101.6],
      },
    ],
  },
  {
    key: 'grass',
    acadName: 'GRASS',
    label: 'Grass / soft landscape',
    lines: [
      {
        angleDeg: 90,
        base: [0, 0],
        offset: [-17.96051224, 17.96051224],
        dashes: [4.7625, -31.15852448],
      },
      {
        angleDeg: 45,
        base: [0, 0],
        offset: [-17.9605122421, 17.9605122421],
        dashes: [4.7625, -20.6375],
      },
      {
        angleDeg: 135,
        base: [0, 0],
        offset: [-17.9605122421, -17.9605122421],
        dashes: [4.7625, -20.6375],
      },
    ],
  },
];

/**
 * The key union, spelled out rather than derived from `HATCH_PATTERNS`.
 *
 * Deriving it (`(typeof HATCH_PATTERNS)[number]['key']`) would widen to
 * `string` the moment the array is typed as `HatchPatternDef[]`, and typing
 * the array `as const` instead would make every numeric literal a literal
 * type — 400 of them. Spelling the union out costs one line per pattern and
 * buys real compile-time checking at every call site; the drift test proves
 * the two lists agree, so this cannot silently fall behind either.
 */
export const HATCH_PATTERN_KEYS = [
  'solid',
  'diagonal',
  'cross',
  'earth',
  'brick',
  'concrete',
  'insulation',
  'plaster',
  'stone',
  'steel',
  'glass',
  'sand',
  'timber',
  'tile',
  'grass',
] as const;

export type HatchPatternKey = (typeof HATCH_PATTERN_KEYS)[number];

const BY_KEY: ReadonlyMap<string, HatchPatternDef> = new Map(
  HATCH_PATTERNS.map((definition) => [definition.key, definition]),
);

/** True for a key the pattern library carries. Narrows for callers off the wire. */
export function isHatchPatternKey(value: unknown): value is HatchPatternKey {
  return typeof value === 'string' && BY_KEY.has(value);
}

/**
 * The definition for a key.
 *
 * Throws rather than returning a default: a silently substituted pattern is
 * the exact failure `render/adapt.py` refuses too ("a section's concrete
 * prints as brick"), and every caller here holds a `HatchPatternKey`, so a
 * miss means the table and the union have come apart.
 */
export function hatchPattern(key: HatchPatternKey): HatchPatternDef {
  const found = BY_KEY.get(key);
  if (found === undefined) {
    throw new Error(`unknown hatch pattern ${key} — patterns.ts and its key union disagree`);
  }
  return found;
}

/**
 * `solid` is a fill, not a line family: it has no geometry to generate, and
 * every consumer branches on that before asking for lines.
 */
export function isSolidPattern(key: HatchPatternKey): boolean {
  return hatchPattern(key).lines.length === 0;
}
