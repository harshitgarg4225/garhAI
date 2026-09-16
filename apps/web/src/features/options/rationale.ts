/**
 * "Why this plan" as prose — a deterministic template renderer over the solver's
 * rationale facts. No LLM: every sentence here is a fixed template filled with a
 * number the worker measured (`services/solver/pipeline._rationale_facts`), so
 * what an architect reads cannot be a plausible invention. A fact kind this file
 * does not know is NOT rendered as prose; it stays available as a chip behind
 * "details" (OptionCard), never dropped and never guessed at.
 *
 * Facts are `kind:value` strings in the model's own units — integer mm² for
 * areas, 0–100 scores, compass zones — and the Vastu verdict for a room comes
 * from the option's own compliance rows, so "as Vastu asks" is the rules engine's
 * word, not this file's.
 */

import { formatSqft } from '@garh/model';

import { labelForRoomType } from './planGeometry';
import { SCORE_LABELS } from './stats';
import type { OptionComplianceRow, PlanOption } from './types';

export interface RationaleLine {
  /** The fact kind that produced the sentence — what a test keys on. */
  readonly kind: string;
  readonly text: string;
}

/** Fact kinds this renderer turns into prose; anything else is a chip only. */
export const PROSE_FACT_KINDS: readonly string[] = [
  'composite',
  'bedrooms',
  'baths',
  'storeys',
  'builtUp',
  'footprint',
  'plotArea',
  'coverage',
  'far',
  'mainDoor',
  'zone',
  'stair',
  'circulationPercent',
  'doors',
  'windows',
  'ventilators',
  'vastu',
  'vastuMode',
  'daylight',
  'rules',
  'warnings',
  'parking',
  'stairAnchor',
];

const ZONE_NAMES: Readonly<Record<string, string>> = {
  N: 'north',
  NE: 'north-east',
  E: 'east',
  SE: 'south-east',
  S: 'south',
  SW: 'south-west',
  W: 'west',
  NW: 'north-west',
  C: 'centre',
};

/** Which Vastu rule speaks for a room type's zone (rulepacks/vastu.json). */
const VASTU_RULE_FOR_TYPE: Readonly<Record<string, string>> = {
  kitchen: 'vastu.kitchen.zone',
  kitchen_dining: 'vastu.kitchen.zone',
  bedroom_master: 'vastu.master.zone',
  pooja: 'vastu.pooja.zone',
  bath: 'vastu.toilet.zone',
  bath_wc: 'vastu.toilet.zone',
  wc: 'vastu.toilet.zone',
  staircase: 'vastu.stair.zone',
};

/** Display order for the zone sentences: the rooms Vastu cares about first. */
const ZONE_ORDER: readonly string[] = [
  'kitchen',
  'kitchen_dining',
  'bedroom_master',
  'pooja',
  'living',
  'living_dining',
  'dining',
  'bedroom',
  'guest_bedroom',
  'study',
  'bath',
  'bath_wc',
  'wc',
];

const COMPONENT_KEYS: readonly (keyof PlanOption['scores'])[] = [
  'targetAreaFit',
  'adjacency',
  'circulation',
  'daylight',
  'vastu',
  'furnitureFit',
  'plumbingStack',
  'privacy',
  'compactness',
];

/** `kind:value` strings → kind → every value carried under it, in order. */
export function parseFacts(facts: readonly string[]): ReadonlyMap<string, readonly string[]> {
  const out = new Map<string, string[]>();
  for (const fact of facts) {
    const at = fact.indexOf(':');
    if (at <= 0) continue;
    const kind = fact.slice(0, at);
    const value = fact.slice(at + 1);
    const list = out.get(kind);
    if (list === undefined) out.set(kind, [value]);
    else list.push(value);
  }
  return out;
}

export function zoneName(zone: string): string {
  return ZONE_NAMES[zone] ?? zone.toLowerCase();
}

function firstInt(values: readonly string[] | undefined): number | null {
  const raw = values?.[0];
  if (raw === undefined) return null;
  const n = Number(raw);
  return Number.isInteger(n) ? n : null;
}

/** "41000000/194400000" → [41000000, 194400000]. */
function pair(values: readonly string[] | undefined): readonly [number, number] | null {
  const raw = values?.[0];
  if (raw === undefined) return null;
  const [a, b] = raw.split('/');
  const x = Number(a);
  const y = Number(b);
  return Number.isInteger(x) && Number.isInteger(y) ? [x, y] : null;
}

/** Integer percent of `part` in `whole`, rounded half-up. */
export function percentOf(part: number, whole: number): number {
  if (whole <= 0) return 0;
  return Math.floor((part * 200 + whole) / (2 * whole));
}

/** A ratio to two decimals as text ("1.75"), rounded half-up, integer arithmetic. */
export function ratioText(numerator: number, denominator: number): string {
  if (denominator <= 0) return '0.00';
  const x100 = Math.floor((numerator * 200 + denominator) / (2 * denominator));
  const whole = Math.floor(x100 / 100);
  const frac = x100 - whole * 100;
  return `${whole}.${frac < 10 ? '0' : ''}${frac}`;
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

function joinList(items: readonly string[]): string {
  if (items.length <= 1) return items[0] ?? '';
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1] ?? ''}`;
}

/** The zones a Vastu rule allows, from its row's `{allow}` limit. */
function allowedZones(row: OptionComplianceRow | undefined): readonly string[] {
  const limit: unknown = row?.limit;
  if (limit !== null && typeof limit === 'object' && 'allow' in limit) {
    const allow = (limit as { allow?: unknown }).allow;
    if (Array.isArray(allow)) return allow.filter((z): z is string => typeof z === 'string');
  }
  return [];
}

function vastuRow(option: PlanOption, ruleId: string): OptionComplianceRow | undefined {
  return option.compliance.find((row) => row.ruleId === ruleId && row.status !== 'not_applicable');
}

// ---------------------------------------------------------------------------
// The sentences, one builder per fact kind
// ---------------------------------------------------------------------------

function overallLine(option: PlanOption, facts: ReadonlyMap<string, readonly string[]>): string {
  const composite = firstInt(facts.get('composite')) ?? option.scores.composite;
  const ranked = COMPONENT_KEYS.map((key) => ({ key, value: option.scores[key] })).sort(
    (a, b) => b.value - a.value,
  );
  const strongest = ranked
    .slice(0, 2)
    .map((s) => `${SCORE_LABELS[s.key].toLowerCase()} (${s.value})`);
  const weakest = ranked[ranked.length - 1];
  const tail =
    weakest !== undefined && weakest.value < (ranked[0]?.value ?? 0)
      ? `, weakest on ${SCORE_LABELS[weakest.key].toLowerCase()} (${weakest.value})`
      : '';
  return `Overall ${composite} of 100 — strongest on ${joinList(strongest)}${tail}.`;
}

function programmeLine(facts: ReadonlyMap<string, readonly string[]>): string | null {
  const bedrooms = firstInt(facts.get('bedrooms'));
  const baths = firstInt(facts.get('baths'));
  const storeys = firstInt(facts.get('storeys'));
  const builtUp = firstInt(facts.get('builtUp'));
  const footprint = firstInt(facts.get('footprint'));
  if (bedrooms === null && builtUp === null) return null;
  const parts: string[] = [];
  if (bedrooms !== null) {
    let rooms = plural(bedrooms, 'bedroom', 'bedrooms');
    if (baths !== null) rooms += ` and ${plural(baths, 'bath', 'baths')}`;
    if (storeys !== null) rooms += ` over ${plural(storeys, 'floor', 'floors')}`;
    parts.push(rooms);
  }
  if (builtUp !== null) {
    let area = `${formatSqft(builtUp, 0)} built up`;
    if (footprint !== null) area += ` on a ${formatSqft(footprint, 0)} ground-floor footprint`;
    parts.push(area);
  }
  return `${parts.join(' — ')}.`;
}

function coverageLine(facts: ReadonlyMap<string, readonly string[]>): string | null {
  const coverage = pair(facts.get('coverage'));
  const plot = firstInt(facts.get('plotArea'));
  if (coverage === null || plot === null || plot <= 0) return null;
  const [actual, limit] = coverage;
  return `Ground coverage ${percentOf(actual, plot)}% of the plot, against the ${percentOf(limit, plot)}% the pack allows.`;
}

function farLine(facts: ReadonlyMap<string, readonly string[]>): string | null {
  const far = pair(facts.get('far'));
  const plot = firstInt(facts.get('plotArea'));
  if (far === null || plot === null || plot <= 0) return null;
  const [actual, limit] = far;
  return `FAR ${ratioText(actual, plot)} against ${ratioText(limit, plot)} allowed.`;
}

function entranceLine(
  option: PlanOption,
  facts: ReadonlyMap<string, readonly string[]>,
): string | null {
  const side = facts.get('mainDoor')?.[0];
  if (side === undefined) return null;
  const row = vastuRow(option, 'vastu.entrance.edge');
  let verdict = '';
  if (row !== undefined) {
    if (row.status === 'pass') verdict = ', where Vastu prefers it';
    else {
      const allowed = allowedZones(row);
      verdict =
        allowed.length > 0
          ? ` — Vastu prefers an entrance on the ${joinList(allowed.map(zoneName))}`
          : ' — not where Vastu prefers it';
    }
  }
  return `Main door on the ${zoneName(side)} side${verdict}.`;
}

function zoneLines(
  option: PlanOption,
  facts: ReadonlyMap<string, readonly string[]>,
): RationaleLine[] {
  const zones = facts.get('zone') ?? [];
  const entries = zones
    .map((token) => {
      const [type, zone] = token.split('@');
      return type !== undefined && zone !== undefined && zone !== '' ? { type, zone } : null;
    })
    .filter((e): e is { type: string; zone: string } => e !== null && e.type !== 'staircase');
  const rank = (type: string): number => {
    const at = ZONE_ORDER.indexOf(type);
    return at === -1 ? ZONE_ORDER.length : at;
  };
  entries.sort((a, b) => rank(a.type) - rank(b.type) || a.type.localeCompare(b.type));
  return entries.map(({ type, zone }) => {
    const label = labelForRoomType(type);
    const ruleId = VASTU_RULE_FOR_TYPE[type];
    const row = ruleId !== undefined ? vastuRow(option, ruleId) : undefined;
    let text = `${label} in the ${zoneName(zone)}`;
    if (row !== undefined) {
      if (row.status === 'pass') text += ', as Vastu asks';
      else if (row.status === 'warn') text += " — Vastu's accepted fallback";
      else {
        const allowed = allowedZones(row);
        text +=
          allowed.length > 0
            ? `; Vastu places it in the ${joinList(allowed.map(zoneName))}`
            : '; not where Vastu places it';
      }
    }
    return { kind: 'zone', text: `${text}.` };
  });
}

function stairLine(
  option: PlanOption,
  facts: ReadonlyMap<string, readonly string[]>,
): string | null {
  const zone = (facts.get('zone') ?? [])
    .map((token) => token.split('@'))
    .find(([type]) => type === 'staircase')?.[1];
  const spec = facts.get('stair')?.[0];
  if (zone === undefined && spec === undefined) return null;
  let text = zone !== undefined ? `Stair in the ${zoneName(zone)}` : 'Stair';
  const row = vastuRow(option, 'vastu.stair.zone');
  if (zone !== undefined && row !== undefined) {
    text += row.status === 'pass' ? ', where Vastu allows it' : ' — not where Vastu puts it';
  }
  if (spec !== undefined) {
    // "dogleg@storey0:18x167mm"
    const match = /^([a-zA-Z]+)@storey(\d+):(\d+)x(\d+)mm$/.exec(spec);
    if (match !== null) {
      const [, kind, , risers, riser] = match;
      text += ` — a ${kind ?? 'straight'} flight of ${risers ?? '?'} risers at ${riser ?? '?'} mm`;
    }
  }
  return `${text}.`;
}

function circulationLine(
  option: PlanOption,
  facts: ReadonlyMap<string, readonly string[]>,
): string {
  const percent = firstInt(facts.get('circulationPercent')) ?? option.scores.circulationPercent;
  return `Circulation takes ${percent}% of the floor area; the cap is 18%.`;
}

function openingsLine(facts: ReadonlyMap<string, readonly string[]>): string | null {
  const doors = firstInt(facts.get('doors'));
  const windows = firstInt(facts.get('windows'));
  const ventilators = firstInt(facts.get('ventilators'));
  if (doors === null && windows === null) return null;
  const parts: string[] = [];
  if (doors !== null) parts.push(plural(doors, 'door', 'doors'));
  if (windows !== null) parts.push(plural(windows, 'window', 'windows'));
  if (ventilators !== null && ventilators > 0)
    parts.push(plural(ventilators, 'ventilator', 'ventilators'));
  return `${joinList(parts)} placed.`;
}

function vastuLine(
  option: PlanOption,
  facts: ReadonlyMap<string, readonly string[]>,
): string | null {
  const mode = facts.get('vastuMode')?.[0];
  if (mode === 'off') return 'Vastu is off for this brief.';
  const score = firstInt(facts.get('vastu')) ?? option.scores.vastu;
  const modeText = mode !== undefined ? ` in ${mode} mode` : '';
  return `Vastu ${score} of 100${modeText}.`;
}

function rulesLine(facts: ReadonlyMap<string, readonly string[]>): string | null {
  const tally = pair(facts.get('rules'));
  if (tally === null) return null;
  const [passed, applicable] = tally;
  const warnings = firstInt(facts.get('warnings')) ?? 0;
  const fails = Math.max(0, applicable - passed - warnings);
  let text = `Passes ${passed} of ${applicable} applicable rule checks`;
  if (fails > 0) text += ` (${plural(fails, 'fails', 'fail')})`;
  text += '.';
  if (warnings > 0)
    text += ` ${plural(warnings, 'advisory warning', 'advisory warnings')} to review.`;
  return text;
}

function parkingLine(facts: ReadonlyMap<string, readonly string[]>): string | null {
  const raw = facts.get('parking')?.[0];
  if (raw === undefined) return null;
  // "1@front:2500x5000" — count, where, bay size.
  const match = /^(\d+)@([a-z-]+)(?::(\d+)x(\d+))?$/.exec(raw);
  if (match === null) return null;
  const count = Number(match[1]);
  const where = match[2] ?? 'front';
  const size =
    match[3] !== undefined && match[4] !== undefined
      ? ` of ${Number(match[3]) / 1000} × ${Number(match[4]) / 1000} m`
      : '';
  if (count === 0) return 'No car bay could be placed in the front setback.';
  return `${plural(count, 'car bay', 'car bays')}${size} in the ${where} setback, reachable from the road.`;
}

/**
 * The prose for one option, in reading order. Every line names a real number
 * from the facts or the scores; a fact the renderer does not know produces no
 * line and stays a chip (see `detailChips`).
 */
export function rationaleLines(option: PlanOption): RationaleLine[] {
  const facts = parseFacts(option.rationaleFacts);
  const lines: RationaleLine[] = [];
  const push = (kind: string, text: string | null): void => {
    if (text !== null) lines.push({ kind, text });
  };
  push('composite', overallLine(option, facts));
  push('programme', programmeLine(facts));
  push('coverage', coverageLine(facts));
  push('far', farLine(facts));
  push('mainDoor', entranceLine(option, facts));
  lines.push(...zoneLines(option, facts));
  push('stair', stairLine(option, facts));
  push('circulationPercent', circulationLine(option, facts));
  push('openings', openingsLine(facts));
  push('vastu', vastuLine(option, facts));
  push('rules', rulesLine(facts));
  push('parking', parkingLine(facts));
  return lines;
}

/**
 * The raw facts, for the "details" disclosure: every chip the worker sent, in
 * its order — including kinds the prose does not know, which is exactly why the
 * chips stay reachable.
 */
export function detailChips(option: PlanOption): readonly string[] {
  return option.rationaleFacts;
}

/** Facts the prose did not consume — a future fact kind is visible, never lost. */
export function unrenderedFacts(option: PlanOption): readonly string[] {
  return option.rationaleFacts.filter((fact) => {
    const kind = fact.slice(0, Math.max(0, fact.indexOf(':')));
    return !PROSE_FACT_KINDS.includes(kind);
  });
}
