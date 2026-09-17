/**
 * Client-side resolution of a rule pack into "the numbers for THIS plot":
 * setbacks, FAR, coverage, height and floor caps, each carrying its rule id,
 * citation and confidence so the panel can show provenance (golden rule 4 —
 * a seeded value must never look verified).
 *
 * The `when` evaluation MIRRORS `apps/api/garh_rules/predicates.py` exactly:
 *   - six operators (`lt lte gt gte eq in`), AND only;
 *   - null facts are FALSE for every operator — a plot with no road makes the
 *     FAR-by-road-width bands drop out, it never silently picks the generous
 *     one;
 *   - `plotAreaSqm` thresholds are SCALED (×1_000_000) against the exact mm²
 *     area, never rounded, so a 120.4 m² plot falls outside an `lte: 120` band.
 * If the two ever disagree, the panel shows a number the compliance report
 * will contradict — treat a mismatch as a bug, not a display choice.
 *
 * Value OVERRIDES live in `plot.regProfile.overrides` under the `"values"`
 * key, every entry an INTEGER (the op validator enforces integral JSON):
 *
 *   { "values": { "setbackFrontMm": 1200, "farX100": 175, ... } }
 *
 * Ratios are stored ×100 (`farX100: 175` = FAR 1.75, `coveragePct: 65` = 65%)
 * for that reason. The engine's context parser routes the reserved `"values"`
 * key separately (garh_rules/context.py VALUE_OVERRIDES_KEY) from rule-
 * acknowledgement overrides (`{ruleId: {reason}}`), so both shapes coexist on
 * one object. The engine parses and audits `"values"` but does NOT yet
 * substitute them into check values — that is Phase 3; until then the
 * compliance report shows pack values, and the panel copy says so.
 */

import { z } from 'zod';

import {
  formatFixed,
  formatLength,
  type JsonObject,
  type Road,
  type UnitsDisplay,
} from '@garh/model';

import { frontEdgeIndex } from './geometry';

// ---------------------------------------------------------------------------
// Pack document schema (GET /rulepacks/{id} returns the authored JSON verbatim)
// ---------------------------------------------------------------------------

const ratioSchema = z.object({ num: z.number().int(), den: z.number().int().positive() });

/** One `when` predicate: `{ lte: 120 }`, `{ in: [...] }`. Unknown keys refused. */
const predicateSchema = z
  .object({
    lt: z.number().optional(),
    lte: z.number().optional(),
    gt: z.number().optional(),
    gte: z.number().optional(),
    eq: z.union([z.string(), z.number(), z.boolean()]).optional(),
    in: z.array(z.union([z.string(), z.number()])).optional(),
  })
  .strict();

const checkSchema = z
  .object({
    type: z.string(),
    edge: z.string().optional(),
    valueMm: z.number().int().optional(),
    value: z.number().int().optional(),
    ratio: ratioSchema.optional(),
  })
  .passthrough();

const packRuleSchema = z
  .object({
    id: z.string(),
    severity: z.string().default('fail'),
    title: z.string().default(''),
    when: z.record(predicateSchema).default({}),
    check: checkSchema,
    cite: z.string().nullable().default(null),
    confidence: z.enum(['seed', 'reviewed', 'verified']).optional(),
  })
  .passthrough();

export const rulepackDocSchema = z
  .object({
    pack: z.string(),
    version: z.string().default(''),
    title: z.string().default(''),
    extends: z.string().nullable().default(null),
    citations_base: z.string().nullable().default(null),
    confidenceDefault: z.enum(['seed', 'reviewed', 'verified']).default('seed'),
    rules: z.array(packRuleSchema).default([]),
  })
  .passthrough();

export type RulepackDoc = z.infer<typeof rulepackDocSchema>;
export type PackRule = z.infer<typeof packRuleSchema>;
export type RuleConfidence = 'seed' | 'reviewed' | 'verified';

// ---------------------------------------------------------------------------
// Facts and predicate evaluation (mirror of predicates.py)
// ---------------------------------------------------------------------------

/** The project-level facts the plot surface can bind. Everything else is null. */
export interface RegFacts {
  readonly plotAreaMm2: number | null;
  /** Width of the FRONT road (widest; ties → lowest edge index), or null. */
  readonly roadWidthMm: number | null;
  readonly zoneCategory: string;
  readonly buildingUse: string;
}

/**
 * Facts for a plot. `zoneCategory`/`buildingUse` default to the residential
 * single dwelling the MVP targets; the brief refines them later and the panel
 * says so on an assumption chip rather than hiding the default.
 */
export function buildRegFacts(input: {
  boundaryAreaMm2: number | null;
  roads: readonly Road[];
}): RegFacts {
  const front = frontEdgeIndex(input.roads);
  const frontRoad =
    front === null ? null : (input.roads.find((r) => r.edgeIndex === front) ?? null);
  return {
    plotAreaMm2: input.boundaryAreaMm2,
    roadWidthMm: frontRoad?.widthMm ?? null,
    zoneCategory: 'residential',
    buildingUse: 'dwelling-single',
  };
}

/** `plotAreaSqm` thresholds are authored in whole m²; the fact is exact mm². */
const FIELD_SCALE: Readonly<Record<string, number>> = { plotAreaSqm: 1_000_000 };

type Predicate = z.infer<typeof predicateSchema>;

function factValue(field: string, facts: RegFacts): string | number | null {
  switch (field) {
    case 'plotAreaSqm':
    case 'plotAreaMm2':
      return facts.plotAreaMm2;
    case 'roadWidthMm':
      return facts.roadWidthMm;
    case 'zoneCategory':
      return facts.zoneCategory;
    case 'buildingUse':
      return facts.buildingUse;
    default:
      // A field this surface cannot bind (storeys, roomType, …). Null → the
      // predicate is false → the rule is not applicable here. Same as engine.
      return null;
  }
}

/**
 * Lift a pack threshold into the fact's unit. Mirrors predicates.py `_scaled`
 * exactly, including its INT-ONLY rule: Python scales only `int` thresholds
 * (never floats, never bools), so a fractional `plotAreaSqm` threshold passes
 * through unscaled on both sides rather than diverging.
 */
function scaled<T>(field: string, threshold: T): T {
  const scale = FIELD_SCALE[field];
  if (scale === undefined) return threshold;
  if (typeof threshold !== 'number' || !Number.isInteger(threshold)) return threshold;
  return (threshold * scale) as T;
}

/** One field's predicate against one fact. Null facts are false, always. */
export function predicateMatches(
  field: string,
  predicate: Predicate,
  value: string | number | null,
): boolean {
  if (value === null) return false;
  // `eq` thresholds scale too — predicates.py routes every operator through
  // `_scaled`, so `{plotAreaSqm: {eq: 120}}` means 120_000_000 mm² on both sides.
  if (predicate.eq !== undefined && value !== scaled(field, predicate.eq)) return false;
  if (predicate.in !== undefined) {
    const options = predicate.in.map((item) => scaled(field, item));
    if (!options.includes(value)) return false;
  }
  if (
    predicate.lt !== undefined &&
    !(typeof value === 'number' && value < scaled(field, predicate.lt))
  ) {
    return false;
  }
  if (
    predicate.lte !== undefined &&
    !(typeof value === 'number' && value <= scaled(field, predicate.lte))
  ) {
    return false;
  }
  if (
    predicate.gt !== undefined &&
    !(typeof value === 'number' && value > scaled(field, predicate.gt))
  ) {
    return false;
  }
  if (
    predicate.gte !== undefined &&
    !(typeof value === 'number' && value >= scaled(field, predicate.gte))
  ) {
    return false;
  }
  return true;
}

/** All fields AND together, exactly like `when_matches`. */
export function whenMatches(when: PackRule['when'], facts: RegFacts): boolean {
  for (const [field, predicate] of Object.entries(when)) {
    if (!predicateMatches(field, predicate, factValue(field, facts))) return false;
  }
  return true;
}

// ---------------------------------------------------------------------------
// Resolution: pack + facts -> the binding number per aspect
// ---------------------------------------------------------------------------

export const REG_VALUE_KEYS = [
  'setbackFrontMm',
  'setbackRearMm',
  'setbackSideAMm',
  'setbackSideBMm',
  'farX100',
  'coveragePct',
  'heightMaxMm',
  'floorsMax',
] as const;
export type RegValueKey = (typeof REG_VALUE_KEYS)[number];

/**
 * Documents written before the sides were split carry ONE `setbackSideMm`.
 * The engine (`garh_rules.checks.LEGACY_SIDE_OVERRIDE_KEY`) still applies it
 * to either side whose specific key is absent; this module reads it the same
 * way and retires it the first time either side is written.
 */
export const LEGACY_SIDE_OVERRIDE_KEY = 'setbackSideMm';

export interface RegValueMeta {
  readonly label: string;
  /** 'length' formats via units.ts; the others format in `formatRegValue`. */
  readonly kind: 'length' | 'ratio-x100' | 'percent' | 'count';
}

export const REG_VALUE_META: Readonly<Record<RegValueKey, RegValueMeta>> = {
  setbackFrontMm: { label: 'Front setback', kind: 'length' },
  setbackRearMm: { label: 'Rear setback', kind: 'length' },
  setbackSideAMm: { label: 'Side A (left) setback', kind: 'length' },
  setbackSideBMm: { label: 'Side B (right) setback', kind: 'length' },
  farX100: { label: 'FAR', kind: 'ratio-x100' },
  coveragePct: { label: 'Coverage', kind: 'percent' },
  heightMaxMm: { label: 'Height cap', kind: 'length' },
  floorsMax: { label: 'Floors', kind: 'count' },
};

export interface ResolvedRegValue {
  readonly key: RegValueKey;
  /** Integer. mm for lengths, ×100 for FAR, whole % for coverage, count for floors. */
  readonly value: number;
  /** Rule the value came from; null when the value exists only as an override. */
  readonly ruleId: string | null;
  readonly title: string;
  readonly cite: string | null;
  readonly citationsBase: string | null;
  readonly confidence: RuleConfidence;
  readonly overridden: boolean;
}

export interface ResolvedRegProfile {
  readonly values: Partial<Record<RegValueKey, ResolvedRegValue>>;
  /** Aspects the pack covers that could not resolve, with the honest reason. */
  readonly missing: readonly { key: RegValueKey; reason: string }[];
}

interface Candidate {
  readonly value: number;
  readonly rule: PackRule;
}

function ratioX100(ratio: { num: number; den: number }): number {
  return Math.round((ratio.num * 100) / ratio.den);
}

/**
 * Resolve the pack against the facts. Setbacks take the MAX of matching
 * minimums (the most demanding band binds); FAR/coverage/height/floors take
 * the MIN of matching maximums. Overrides replace the resolved value but keep
 * the losing rule's citation visible via `ruleId`.
 */
export function resolveRegValues(
  pack: RulepackDoc,
  facts: RegFacts,
  overrides: JsonObject = {},
): ResolvedRegProfile {
  const buckets = new Map<RegValueKey, Candidate[]>();
  const covered = new Set<RegValueKey>();

  for (const rule of pack.rules) {
    const keys = ruleValueKeys(rule);
    for (const { key, value } of keys) {
      covered.add(key);
      if (!whenMatches(rule.when, facts)) continue;
      const list = buckets.get(key) ?? [];
      list.push({ value, rule });
      buckets.set(key, list);
    }
  }

  const overrideValues = readValueOverrides(overrides);
  const values: Partial<Record<RegValueKey, ResolvedRegValue>> = {};
  const missing: { key: RegValueKey; reason: string }[] = [];

  for (const key of REG_VALUE_KEYS) {
    const candidates = buckets.get(key) ?? [];
    const takeMax = key.startsWith('setback');
    let winner: Candidate | null = null;
    for (const c of candidates) {
      if (
        winner === null ||
        (takeMax ? c.value > winner.value : c.value < winner.value) ||
        // Deterministic tie-break so re-renders never flip the citation.
        (c.value === winner.value && c.rule.id < winner.rule.id)
      ) {
        winner = c;
      }
    }

    const override = overrideValues[key];
    if (override !== undefined) {
      values[key] = {
        key,
        value: override,
        ruleId: winner?.rule.id ?? null,
        title: winner?.rule.title ?? REG_VALUE_META[key].label,
        cite: winner?.rule.cite ?? null,
        citationsBase: pack.citations_base,
        confidence: winner?.rule.confidence ?? pack.confidenceDefault,
        overridden: true,
      };
      continue;
    }
    if (winner !== null) {
      values[key] = {
        key,
        value: winner.value,
        ruleId: winner.rule.id,
        title: winner.rule.title,
        cite: winner.rule.cite,
        citationsBase: pack.citations_base,
        confidence: winner.rule.confidence ?? pack.confidenceDefault,
        overridden: false,
      };
      continue;
    }
    if (covered.has(key)) {
      missing.push({ key, reason: missingReason(key, facts) });
    }
  }

  return { values, missing };
}

function ruleValueKeys(rule: PackRule): { key: RegValueKey; value: number }[] {
  const check = rule.check;
  switch (check.type) {
    case 'setback_min': {
      if (typeof check.valueMm !== 'number') return [];
      if (check.edge === 'front') return [{ key: 'setbackFrontMm', value: check.valueMm }];
      if (check.edge === 'rear') return [{ key: 'setbackRearMm', value: check.valueMm }];
      // A pack rule on `sides` binds both; `side-a` / `side-b` bind their own.
      if (check.edge === 'sides') {
        return [
          { key: 'setbackSideAMm', value: check.valueMm },
          { key: 'setbackSideBMm', value: check.valueMm },
        ];
      }
      if (check.edge === 'side-a') return [{ key: 'setbackSideAMm', value: check.valueMm }];
      if (check.edge === 'side-b') return [{ key: 'setbackSideBMm', value: check.valueMm }];
      return [];
    }
    case 'far_max':
      return check.ratio === undefined ? [] : [{ key: 'farX100', value: ratioX100(check.ratio) }];
    case 'coverage_max':
      return check.ratio === undefined
        ? []
        : [{ key: 'coveragePct', value: ratioX100(check.ratio) }];
    case 'height_max':
      return typeof check.valueMm === 'number'
        ? [{ key: 'heightMaxMm', value: check.valueMm }]
        : [];
    case 'floors_max':
      return typeof check.value === 'number' ? [{ key: 'floorsMax', value: check.value }] : [];
    default:
      // Room minimums, stairs, projections… evaluated by the engine against
      // the drawn model, not resolvable from the plot alone. Not this panel's job.
      return [];
  }
}

function missingReason(key: RegValueKey, facts: RegFacts): string {
  if (facts.plotAreaMm2 === null) {
    return 'Draw the plot boundary first — this value is banded by plot size.';
  }
  if (facts.roadWidthMm === null) {
    return 'Set the road width on an edge — this value is banded by the front road.';
  }
  return `No ${REG_VALUE_META[key].label.toLowerCase()} band in this pack matches the plot.`;
}

// ---------------------------------------------------------------------------
// Formatting and parsing for the chips
// ---------------------------------------------------------------------------

/** `1.5 m` / `1.75` / `65%` / `3` — the chip's value text. */
export function formatRegValue(key: RegValueKey, value: number, display: UnitsDisplay): string {
  switch (REG_VALUE_META[key].kind) {
    case 'length':
      return formatLength(value, display);
    case 'ratio-x100':
      return formatFixed(value / 100, 2);
    case 'percent':
      return `${String(value)}%`;
    case 'count':
      return String(value);
  }
}

/**
 * Parse a chip edit back to the stored integer. Lengths go through the units
 * module at the call site (they need the project display); this handles the
 * unit-less kinds. Returns null with no guess when the text is not readable.
 */
export function parseRegScalar(key: RegValueKey, raw: string): number | null {
  const text = raw.trim().replace(/%$/, '');
  if (!/^\d+(?:\.\d+)?$/.test(text)) return null;
  const n = Number(text);
  switch (REG_VALUE_META[key].kind) {
    case 'ratio-x100':
      return Math.round(n * 100);
    case 'percent': {
      const pct = Math.round(n);
      return pct >= 1 && pct <= 100 ? pct : null;
    }
    case 'count': {
      const c = Math.round(n);
      return c >= 1 && c <= 20 ? c : null;
    }
    case 'length':
      return null; // lengths are the units module's job, not this parser's
  }
}

// ---------------------------------------------------------------------------
// Override storage on plot.regProfile.overrides
// ---------------------------------------------------------------------------

const isRegValueKey = (k: string): k is RegValueKey =>
  (REG_VALUE_KEYS as readonly string[]).includes(k);

/**
 * The `values` map out of an overrides object; ignores anything malformed.
 * A legacy single `setbackSideMm` reads as BOTH sides wherever a specific
 * side key is absent — exactly how the engine applies it.
 */
export function readValueOverrides(overrides: JsonObject): Partial<Record<RegValueKey, number>> {
  const values = overrides.values;
  if (typeof values !== 'object' || values === null || Array.isArray(values)) return {};
  const out: Partial<Record<RegValueKey, number>> = {};
  for (const [k, v] of Object.entries(values)) {
    if (isRegValueKey(k) && typeof v === 'number' && Number.isSafeInteger(v)) out[k] = v;
  }
  const legacy = values[LEGACY_SIDE_OVERRIDE_KEY];
  if (typeof legacy === 'number' && Number.isSafeInteger(legacy)) {
    if (out.setbackSideAMm === undefined) out.setbackSideAMm = legacy;
    if (out.setbackSideBMm === undefined) out.setbackSideBMm = legacy;
  }
  return out;
}

/**
 * The raw integer map under `overrides.values`, with the legacy side key
 * expanded into the two side keys and dropped. Keys this module does not know
 * (the deed area, anything a later panel adds) are carried through as long
 * as they are integers — the engine's context parser requires exactly that.
 */
function integerValues(overrides: JsonObject): Record<string, number> {
  const values = overrides.values;
  const out: Record<string, number> = {};
  if (typeof values !== 'object' || values === null || Array.isArray(values)) return out;
  for (const [k, v] of Object.entries(values)) {
    if (k !== LEGACY_SIDE_OVERRIDE_KEY && typeof v === 'number' && Number.isSafeInteger(v)) {
      out[k] = v;
    }
  }
  const legacy = values[LEGACY_SIDE_OVERRIDE_KEY];
  if (typeof legacy === 'number' && Number.isSafeInteger(legacy)) {
    if (out.setbackSideAMm === undefined) out.setbackSideAMm = legacy;
    if (out.setbackSideBMm === undefined) out.setbackSideBMm = legacy;
  }
  return out;
}

function withIntegerValue(overrides: JsonObject, key: string, value: number | null): JsonObject {
  const nextValues = integerValues(overrides);
  delete nextValues[key];
  if (value !== null) {
    if (!Number.isSafeInteger(value)) {
      throw new RangeError(`Override ${key} must be an integer, got ${String(value)}`);
    }
    nextValues[key] = value;
  }
  const out: JsonObject = { ...overrides };
  if (Object.keys(nextValues).length === 0) delete out.values;
  else out.values = nextValues;
  return out;
}

/**
 * A new overrides object with `key` set to `value` (or cleared with null).
 * Everything else on the object — including any future rule-acknowledgement
 * keys — is carried through untouched. A legacy `setbackSideMm` is expanded
 * into the two side keys on the way through (so clearing one side cannot be
 * undone by a value the panel no longer shows) and dropped.
 */
export function withValueOverride(
  overrides: JsonObject,
  key: RegValueKey,
  value: number | null,
): JsonObject {
  return withIntegerValue(overrides, key, value);
}

// ---------------------------------------------------------------------------
// The registered (sale-deed) plot area
//
// Municipal forms ask for the plot area "as per document" beside the area "as
// per site", and the smaller of the two usually governs FAR and coverage. It
// is a regulatory-context fact about the plot, so it lives with the profile:
// `overrides.values.deedAreaMm2`, an integer like every other entry there
// (the engine parses `values` as {key: int} and substitutes only the keys it
// knows, so this one is carried and audited but never applied as a limit).
// ---------------------------------------------------------------------------

export const DEED_AREA_KEY = 'deedAreaMm2';

/** The registered deed area in mm², or null when none has been entered. */
export function readDeedAreaMm2(overrides: JsonObject): number | null {
  const value = integerValues(overrides)[DEED_AREA_KEY];
  return value === undefined || value <= 0 ? null : value;
}

/** A new overrides object with the deed area set (mm²) or cleared (null). */
export function withDeedAreaMm2(overrides: JsonObject, areaMm2: number | null): JsonObject {
  if (areaMm2 !== null && areaMm2 <= 0) {
    throw new RangeError('A deed area has to be a positive area.');
  }
  return withIntegerValue(overrides, DEED_AREA_KEY, areaMm2);
}

export interface DeedReconciliation {
  readonly drawnMm2: number;
  readonly deedMm2: number;
  /** drawn − deed, mm² (negative: the drawing is smaller than the document). */
  readonly differenceMm2: number;
  /** Signed percentage of the DEED area, one decimal — what a scrutiny note quotes. */
  readonly differencePct: number;
}

/** Drawn vs registered area. Pure; the caller decides what a tolerable gap is. */
export function reconcileDeedArea(drawnMm2: number, deedMm2: number): DeedReconciliation {
  const differenceMm2 = drawnMm2 - deedMm2;
  const pct = deedMm2 === 0 ? 0 : (differenceMm2 / deedMm2) * 100;
  return {
    drawnMm2,
    deedMm2,
    differenceMm2,
    differencePct: Math.round(pct * 10) / 10,
  };
}

// ---------------------------------------------------------------------------
// City preset vocabulary (kept in lockstep with pages/_contracts.ts labels)
// ---------------------------------------------------------------------------

export const CITY_PACK_OPTIONS: readonly { id: string; label: string }[] = [
  { id: 'blr', label: 'Bengaluru (BBMP)' },
  { id: 'ncr', label: 'Delhi NCR' },
  { id: 'hyd', label: 'Hyderabad (GHMC)' },
  { id: 'custom', label: 'Custom / other city' },
];

/** UI value <-> stored `regProfile.cityPack` (null means custom/none). */
export function cityPackToStored(uiValue: string): string | null {
  return uiValue === 'custom' ? null : uiValue;
}
export function cityPackFromStored(stored: string | null): string {
  return stored === null || stored === '' ? 'custom' : stored;
}
