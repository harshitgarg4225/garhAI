/**
 * Pure derivations over PlanOption JSON: the three key stats, the compliance
 * badge counts, the Vastu wheel breakdown, the compare-two diff, the §5.6
 * honest banner fallback, and the solve-request builders for every regenerate
 * control. No React, no stores — all of it unit-testable here and now.
 */

import { formatFtIn, formatSqft, parseAreaMm2, parseLengthMm } from '@garh/model';

import { isCompassSector, type CompassSector, type VastuZone } from './planGeometry';
import type { OptionComplianceRow, Placement, PlanOption, SolveOutcome } from './types';

// ---------------------------------------------------------------------------
// Key stats (§15: built-up, bedrooms fit, circulation %)
// ---------------------------------------------------------------------------

const BEDROOM_TYPES: readonly string[] = ['bedroom_master', 'bedroom', 'guest_bedroom'];

export interface KeyStats {
  /** "1,450 sq ft" — Indian primary display per §15. */
  readonly builtUpLabel: string;
  readonly builtUpMm2: number;
  readonly bedrooms: number;
  /** §5.6: presentable options always passed furniture-fit for habitable rooms. */
  readonly furnitureFits: boolean;
  readonly bedroomsLabel: string;
  readonly circulationPercent: number;
  readonly circulationLabel: string;
}

export function bedroomCount(placements: readonly Placement[] | undefined): number {
  if (!placements) return 0;
  return placements.filter((p) => BEDROOM_TYPES.includes(p.roomType)).length;
}

export function keyStats(option: PlanOption): KeyStats {
  const bedrooms = bedroomCount(option.placements);
  // Furniture-fit is a §5.6 gate: every option the server returns has passed
  // it, so this is a rendered fact, not a recomputation — the catalog packing
  // ran in the critic (§5.4).
  const furnitureFits = true;
  return {
    builtUpLabel: formatSqft(option.builtUpMm2, 0),
    builtUpMm2: option.builtUpMm2,
    bedrooms,
    furnitureFits,
    bedroomsLabel:
      bedrooms > 0
        ? `${bedrooms} ${bedrooms === 1 ? 'bedroom' : 'bedrooms'} · furniture fits`
        : 'Furniture fits',
    circulationPercent: option.scores.circulationPercent,
    circulationLabel: `${option.scores.circulationPercent}% circulation`,
  };
}

// ---------------------------------------------------------------------------
// Compliance badge
// ---------------------------------------------------------------------------

export interface ComplianceSummary {
  readonly pass: number;
  readonly warn: number;
  readonly fail: number;
  /** §5.6: hard rules all pass on a presentable option; this proves it. */
  readonly hardFails: number;
}

export function complianceSummary(rows: readonly OptionComplianceRow[]): ComplianceSummary {
  let pass = 0;
  let warn = 0;
  let fail = 0;
  let hardFails = 0;
  for (const row of rows) {
    if (row.status === 'pass') pass += 1;
    else if (row.status === 'warn') warn += 1;
    else if (row.status === 'fail') {
      fail += 1;
      if (row.hard) hardFails += 1;
    }
  }
  return { pass, warn, fail, hardFails };
}

// ---------------------------------------------------------------------------
// Vastu wheel breakdown
// ---------------------------------------------------------------------------

/**
 * Where each seeded Vastu rule lives on the 3×3 zone wheel. Static because the
 * pack's `allow` lists are not on the result rows; the row's `status` supplies
 * the colour and its `actual`/`message` the tooltip. Unknown vastu rules fall
 * into `unplaced` and render as chips under the wheel instead of vanishing.
 * (Rule ids from rulepacks/vastu.json — 9 rules, weights sum to 100.)
 */
const VASTU_RULE_ZONES: Readonly<Record<string, readonly VastuZone[]>> = {
  'vastu.entrance.edge': ['N', 'NE', 'E'],
  'vastu.pooja.zone': ['NE'],
  'vastu.kitchen.zone': ['SE'],
  'vastu.master.zone': ['SW'],
  'vastu.toilet.zone': ['W', 'NW'],
  'vastu.toilet.never_ne': ['NE'],
  'vastu.stair.zone': ['S', 'SW', 'W'],
  'vastu.brahmasthan.open': ['C'],
  'vastu.water_tank.zone': ['NE'],
};

export type WheelStatus = 'pass' | 'warn' | 'fail' | 'none';

export interface VastuWheelRule {
  readonly ruleId: string;
  readonly title: string;
  readonly status: Exclude<WheelStatus, 'none'>;
  readonly zones: readonly VastuZone[];
  /** "The kitchen sits in NW. Vastu places the fire zone in SE." */
  readonly message: string | null;
  readonly actual: string | null;
}

export interface VastuWheel {
  /** Worst status per sector — what the wheel paints. */
  readonly sectors: Readonly<Record<CompassSector, WheelStatus>>;
  readonly center: WheelStatus;
  readonly rules: readonly VastuWheelRule[];
  /** Rules the wheel could not place; render as chips, never drop. */
  readonly unplaced: readonly VastuWheelRule[];
  /** The critic's 0–100 Vastu score for the caption. */
  readonly score: number;
  /** False when the pack was off / nothing applied — hide the wheel. */
  readonly applicable: boolean;
}

const STATUS_RANK: Readonly<Record<WheelStatus, number>> = {
  none: 0,
  pass: 1,
  warn: 2,
  fail: 3,
};

function worse(a: WheelStatus, b: WheelStatus): WheelStatus {
  return STATUS_RANK[b] > STATUS_RANK[a] ? b : a;
}

export function isVastuRow(row: OptionComplianceRow): boolean {
  return row.packId === 'vastu' || row.ruleId.startsWith('vastu.');
}

export function vastuWheel(option: PlanOption): VastuWheel {
  const sectors: Record<CompassSector, WheelStatus> = {
    N: 'none',
    NE: 'none',
    E: 'none',
    SE: 'none',
    S: 'none',
    SW: 'none',
    W: 'none',
    NW: 'none',
  };
  let center: WheelStatus = 'none';
  const rules: VastuWheelRule[] = [];
  const unplaced: VastuWheelRule[] = [];

  for (const row of option.compliance) {
    if (!isVastuRow(row) || row.status === 'not_applicable') continue;
    const zones = VASTU_RULE_ZONES[row.ruleId] ?? [];
    const rule: VastuWheelRule = {
      ruleId: row.ruleId,
      title: row.title ?? row.ruleId,
      status: row.status,
      zones,
      message: row.message ?? null,
      actual: typeof row.actual === 'string' ? row.actual : null,
    };
    if (zones.length === 0) {
      unplaced.push(rule);
      continue;
    }
    rules.push(rule);
    for (const zone of zones) {
      if (zone === 'C') center = worse(center, row.status);
      else sectors[zone] = worse(sectors[zone], row.status);
    }
    // Where the room ACTUALLY sits also colours its sector on a violation, so
    // "kitchen in NW" shows on NW, not only on the empty SE preference.
    if (rule.actual !== null && row.status !== 'pass' && isCompassSector(rule.actual)) {
      sectors[rule.actual] = worse(sectors[rule.actual], row.status);
    }
  }

  return {
    sectors,
    center,
    rules,
    unplaced,
    score: option.scores.vastu,
    applicable: rules.length > 0 || unplaced.length > 0,
  };
}

// ---------------------------------------------------------------------------
// §5.6 honest banner (fallback when the job row lost the worker's banner)
// ---------------------------------------------------------------------------

export function bannerFor(optionCount: number, target = 3): string | null {
  if (optionCount >= target) return null;
  if (optionCount === 0) return null; // that is a failure state, not a banner
  return optionCount === 1
    ? '1 strong option found for this plot'
    : `${optionCount} strong options found for this plot`;
}

/** The banner the screen shows: the worker's own words when present. */
export function effectiveBanner(outcome: SolveOutcome, target = 3): string | null {
  return outcome.banner ?? bannerFor(outcome.options.length, target);
}

// ---------------------------------------------------------------------------
// Compare two
// ---------------------------------------------------------------------------

export interface ScoreDelta {
  readonly key: keyof PlanOption['scores'];
  readonly label: string;
  readonly a: number;
  readonly b: number;
  /** b − a: positive means B scores higher. */
  readonly delta: number;
}

export interface RoomDiff {
  /** Room-type labels present in A only / B only (multiset difference). */
  readonly onlyA: readonly string[];
  readonly onlyB: readonly string[];
  readonly shared: readonly string[];
}

export interface OptionComparison {
  readonly scores: readonly ScoreDelta[];
  readonly rooms: RoomDiff;
  readonly builtUpDeltaMm2: number;
  readonly circulationDelta: number;
  /** True when the two options share a §5.5 signature entry set (same family). */
  readonly sameStairAnchor: boolean;
}

export const SCORE_LABELS: Readonly<Record<keyof PlanOption['scores'], string>> = {
  targetAreaFit: 'Room sizes vs brief',
  adjacency: 'Adjacencies',
  circulation: 'Circulation',
  daylight: 'Daylight',
  vastu: 'Vastu',
  furnitureFit: 'Furniture fit',
  plumbingStack: 'Plumbing stacks',
  privacy: 'Privacy',
  compactness: 'Compactness',
  composite: 'Overall',
  circulationPercent: 'Circulation %',
};

/** Multiset of room-type counts, e.g. { bedroom: 3, bath: 2 }. */
export function roomMultiset(placements: readonly Placement[] | undefined): Map<string, number> {
  const counts = new Map<string, number>();
  for (const p of placements ?? []) {
    counts.set(p.roomType, (counts.get(p.roomType) ?? 0) + 1);
  }
  return counts;
}

function multisetOnly(a: Map<string, number>, b: Map<string, number>): string[] {
  const out: string[] = [];
  for (const [type, countA] of a) {
    const extra = countA - (b.get(type) ?? 0);
    for (let i = 0; i < extra; i += 1) out.push(type);
  }
  return out.sort();
}

export function diffRooms(
  a: readonly Placement[] | undefined,
  b: readonly Placement[] | undefined,
): RoomDiff {
  const setA = roomMultiset(a);
  const setB = roomMultiset(b);
  const shared: string[] = [];
  for (const [type, countA] of setA) {
    const both = Math.min(countA, setB.get(type) ?? 0);
    for (let i = 0; i < both; i += 1) shared.push(type);
  }
  return {
    onlyA: multisetOnly(setA, setB),
    onlyB: multisetOnly(setB, setA),
    shared: shared.sort(),
  };
}

/** Score keys the compare table shows, in display order. Composite leads. */
const COMPARE_KEYS: readonly (keyof PlanOption['scores'])[] = [
  'composite',
  'targetAreaFit',
  'adjacency',
  'daylight',
  'vastu',
  'furnitureFit',
  'privacy',
  'plumbingStack',
  'compactness',
];

export function compareOptions(a: PlanOption, b: PlanOption): OptionComparison {
  const scores: ScoreDelta[] = COMPARE_KEYS.map((key) => ({
    key,
    label: SCORE_LABELS[key],
    a: a.scores[key],
    b: b.scores[key],
    delta: b.scores[key] - a.scores[key],
  }));
  return {
    scores,
    rooms: diffRooms(a.placements, b.placements),
    builtUpDeltaMm2: b.builtUpMm2 - a.builtUpMm2,
    circulationDelta: b.scores.circulationPercent - a.scores.circulationPercent,
    sameStairAnchor: a.stairAnchorId !== '' && a.stairAnchorId === b.stairAnchorId,
  };
}

// ---------------------------------------------------------------------------
// Solve-request builders (the §F3 controls)
// ---------------------------------------------------------------------------

/**
 * Params for `POST /projects/:id/solve`. Sent through the jobs store, which
 * wraps them as the request's `params` object — the server merges them into
 * the worker payload (`SolveIn.params` → `params.update`). Keys mirror
 * `SolveIn` so lifting them to the top-level body later is a rename-free move.
 */
export interface SolveRequestParams extends Record<string, unknown> {
  readonly lockedRoomIds?: readonly string[];
  readonly seed?: number;
  readonly optionCount?: number;
  readonly storeys?: number;
  /** Restrict the re-solve to one floor (per-floor regen). */
  readonly storeyIndex?: number;
}

function readSeed(params: Readonly<Record<string, unknown>>): number | null {
  const seed = params.seed;
  return typeof seed === 'number' && Number.isInteger(seed) ? seed : null;
}

/** Lock the given rooms, re-solve everything else (§5.7). */
export function regenerateOthersParams(lockedRoomIds: readonly string[]): SolveRequestParams {
  return { lockedRoomIds: [...lockedRoomIds] };
}

/** Re-solve one floor; every room on other floors is locked by the caller. */
export function perFloorParams(
  storeyIndex: number,
  lockedRoomIds: readonly string[],
): SolveRequestParams {
  return { storeyIndex, lockedRoomIds: [...lockedRoomIds] };
}

/**
 * "More like this": same seed family. Deterministic solver (§5) means the same
 * seed + params reproduces the family; a small fixed offset inside the family
 * explores neighbours without leaving it. The offset is derived from the
 * option's rank so two "more like this" clicks on different cards differ.
 */
export function moreLikeThisParams(
  jobParams: Readonly<Record<string, unknown>>,
  option: PlanOption,
): SolveRequestParams {
  const base = readSeed(jobParams) ?? 0;
  return { seed: base + 1 + option.rank, likeOptionId: option.id };
}

/** New-seed variation: an unrelated family, honestly random. */
export function newSeedParams(random: () => number = Math.random): SolveRequestParams {
  // 2^31-safe positive int; the API's StrictInt accepts it.
  return { seed: Math.floor(random() * 2_147_483_647) };
}

// ---------------------------------------------------------------------------
// Assumption chips → ops (the locked golden rule: edits dispatch ops)
// ---------------------------------------------------------------------------

/**
 * Build the `brief.update` merge-patch op for an edited assumption chip.
 *
 * Only `brief.*` assumptions are editable this way — they round-trip into the
 * next solve. Anything else (envelope facts, solver internals) returns null
 * and renders read-only, because inventing an op target for it would fabricate
 * a mutation the model core never defined.
 *
 * The raw string is parsed by the field's unit suffix: `…Mm2` fields take
 * areas ("120 sqft", "11 sqm"), `…Mm` fields take lengths ("12'6\"", "3.8m"),
 * anything else must be a plain integer. Returns null on unparseable input —
 * the chip shows its old value again rather than dispatching a NaN.
 */
export function assumptionEditOp(
  field: string,
  raw: string,
): { type: 'brief.update'; payload: { patch: Record<string, unknown> } } | null {
  if (!field.startsWith('brief.')) return null;
  const path = field
    .slice('brief.'.length)
    .split('.')
    .filter((s) => s !== '');
  if (path.length === 0) return null;

  const leaf = path[path.length - 1] ?? '';
  let value: number;
  try {
    if (leaf.endsWith('Mm2')) value = parseAreaMm2(raw);
    else if (leaf.endsWith('Mm')) value = parseLengthMm(raw);
    else {
      const n = Number(raw.replace(/[,\s]/g, ''));
      if (!Number.isFinite(n) || !Number.isInteger(n)) return null;
      value = n;
    }
  } catch {
    return null;
  }

  // Nest the leaf value: "rooms.bedroom2.targetAreaMm2" → {rooms:{bedroom2:{…}}}
  const patch: Record<string, unknown> = {};
  let cursor = patch;
  for (let i = 0; i < path.length - 1; i += 1) {
    const next: Record<string, unknown> = {};
    cursor[path[i] as string] = next;
    cursor = next;
  }
  cursor[leaf] = value;

  return { type: 'brief.update', payload: { patch } };
}

/**
 * Display text for an assumption chip's value, keyed off the field's unit
 * suffix — the same convention `assumptionEditOp` parses back. Non-numeric
 * values pass through as text.
 */
export function assumptionValueText(field: string, value: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value)) {
    const leaf = field.split('.').pop() ?? '';
    if (leaf.endsWith('Mm2')) return formatSqft(value, 0);
    if (leaf.endsWith('Mm')) return formatFtIn(value);
    return new Intl.NumberFormat('en-IN').format(value);
  }
  if (typeof value === 'string') return value;
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return String(value ?? '—');
}

/** "Bedroom 2 · Target Area" from "brief.rooms.bedroom2.targetAreaMm2". */
export function assumptionLabel(field: string): string {
  const parts = field.split('.').filter((p) => p !== '');
  // The namespace prefix ("brief", "envelope") is plumbing, not label copy.
  const scoped = parts.length > 1 ? parts.slice(1) : parts;
  const tail = scoped.slice(-2); // ["bedroom2", "targetAreaMm2"] reads best
  return tail
    .map((part) =>
      part
        .replace(/Mm2$|Mm$/, '')
        .replace(/([a-z])([A-Z0-9])/g, '$1 $2')
        .replace(/[_-]+/g, ' ')
        .trim(),
    )
    .filter((p) => p !== '')
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join(' · ');
}
