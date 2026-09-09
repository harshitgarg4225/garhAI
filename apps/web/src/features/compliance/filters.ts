/**
 * Pure helpers behind the Compliance tab: which storey a result belongs to,
 * what an architect wrote when accepting a rule, filtering and grouping a
 * 65-row report, and whether a pack is past its review date.
 *
 * Nothing here touches React or the network, so every branch has a unit test
 * and the page stays a thin composition.
 */

import type { JsonObject, ProjectDoc } from '@garh/model';
import type { CompliancePackReview } from '../../lib/schemas';
import type { ComplianceIssueVM, ComplianceResultStatus } from '../../components';

// ---------------------------------------------------------------------------
// Storeys
// ---------------------------------------------------------------------------

export interface StoreyRef {
  readonly id: string;
  readonly name: string;
  readonly index: number;
}

/** Element id → storey id, for every element kind a rule can point at. */
export function storeyOfElements(doc: ProjectDoc): ReadonlyMap<string, string> {
  const map = new Map<string, string>();
  const wallStorey = new Map<string, string>();
  for (const wall of doc.house.walls) {
    wallStorey.set(wall.id, wall.storeyId);
    map.set(wall.id, wall.storeyId);
  }
  for (const room of doc.house.rooms) map.set(room.id, room.storeyId);
  for (const stair of doc.house.stairs) map.set(stair.id, stair.storeyId);
  for (const balcony of doc.house.balconies) map.set(balcony.id, balcony.storeyId);
  for (const opening of doc.house.openings) {
    const storeyId = wallStorey.get(opening.wallId);
    if (storeyId !== undefined) map.set(opening.id, storeyId);
  }
  for (const storey of doc.house.storeys) map.set(storey.id, storey.id);
  return map;
}

/** The storeys, lowest first (by finished floor level), as filter options. */
export function storeyRefs(doc: ProjectDoc): readonly StoreyRef[] {
  return [...doc.house.storeys]
    .sort((a, b) => a.level.fflMm - b.level.fflMm)
    .map((s, index) => ({ id: s.id, name: s.name, index }));
}

/**
 * The storey a result belongs to, or `null` for plot-level rules (setbacks,
 * FAR, coverage) and rules whose elements span storeys.
 */
export function storeyOfIssue(
  issue: ComplianceIssueVM,
  storeyOf: ReadonlyMap<string, string>,
): string | null {
  let found: string | null = null;
  for (const elementId of issue.elementIds) {
    const storeyId = storeyOf.get(elementId);
    if (storeyId === undefined) continue;
    if (found !== null && found !== storeyId) return null;
    found = storeyId;
  }
  return found;
}

// ---------------------------------------------------------------------------
// Overrides written to the profile
// ---------------------------------------------------------------------------

/** What the override route stored for a rule (`regProfile.overrides[ruleId]`). */
export interface RuleOverrideRecord {
  readonly reason: string;
  readonly byUserId: string | null;
  readonly byName: string | null;
  /** ISO 8601, server-stamped. */
  readonly at: string | null;
}

/** Reserved key inside `overrides`: the plot panel's VALUE overrides, not a rule. */
const VALUE_OVERRIDES_KEY = 'values';

/** The acknowledgements in a profile, ignoring the `values` map and malformed entries. */
export function readRuleOverrides(
  overrides: JsonObject,
): Readonly<Record<string, RuleOverrideRecord>> {
  const out: Record<string, RuleOverrideRecord> = {};
  for (const [ruleId, value] of Object.entries(overrides)) {
    if (ruleId === VALUE_OVERRIDES_KEY) continue;
    if (typeof value !== 'object' || value === null || Array.isArray(value)) continue;
    const record = value as Record<string, unknown>;
    if (typeof record.reason !== 'string') continue;
    out[ruleId] = {
      reason: record.reason,
      byUserId: typeof record.byUserId === 'string' ? record.byUserId : null,
      byName: typeof record.byName === 'string' ? record.byName : null,
      at: typeof record.at === 'string' ? record.at : null,
    };
  }
  return out;
}

// ---------------------------------------------------------------------------
// Filtering, searching, grouping
// ---------------------------------------------------------------------------

export type SeverityFilter = 'all' | 'fail' | 'warn' | 'pass' | 'not_applicable' | 'overridden';

export interface ComplianceFilters {
  /** Pack id, or 'all'. */
  readonly pack: string;
  /** Storey id, 'all', or 'plot' for rules not tied to one storey. */
  readonly storey: string;
  readonly severity: SeverityFilter;
  readonly query: string;
}

export const DEFAULT_FILTERS: ComplianceFilters = {
  pack: 'all',
  storey: 'all',
  severity: 'all',
  query: '',
};

function matchesQuery(issue: ComplianceIssueVM, query: string): boolean {
  if (query === '') return true;
  const q = query.toLowerCase();
  const haystack = [
    issue.ruleId,
    issue.title ?? '',
    issue.message,
    issue.cite ?? '',
    issue.packId ?? '',
    ...(issue.instances ?? []).map((i) => i.label),
  ]
    .join(' ')
    .toLowerCase();
  return haystack.includes(q);
}

export function filterIssues(
  issues: readonly ComplianceIssueVM[],
  filters: ComplianceFilters,
  storeyOf: ReadonlyMap<string, string>,
): ComplianceIssueVM[] {
  return issues.filter((issue) => {
    if (filters.pack !== 'all' && (issue.packId ?? '') !== filters.pack) return false;
    if (filters.severity === 'overridden') {
      if (issue.overridden !== true) return false;
    } else if (filters.severity !== 'all' && issue.status !== filters.severity) {
      return false;
    }
    if (filters.storey !== 'all') {
      const storey = storeyOfIssue(issue, storeyOf);
      if (filters.storey === 'plot' ? storey !== null : storey !== filters.storey) return false;
    }
    return matchesQuery(issue, filters.query);
  });
}

export interface StoreyGroup {
  /** Storey id, or null for the plot-level group. */
  readonly storeyId: string | null;
  readonly label: string;
  readonly issues: readonly ComplianceIssueVM[];
}

/**
 * Group results by storey, storeys in index order, plot-level rules last.
 * A group with nothing in it is omitted — an empty "First floor" heading is noise.
 */
export function groupByStorey(
  issues: readonly ComplianceIssueVM[],
  storeys: readonly StoreyRef[],
  storeyOf: ReadonlyMap<string, string>,
): StoreyGroup[] {
  const buckets = new Map<string | null, ComplianceIssueVM[]>();
  for (const storey of storeys) buckets.set(storey.id, []);
  buckets.set(null, []);
  for (const issue of issues) {
    const key = storeyOfIssue(issue, storeyOf);
    const bucket = buckets.get(key);
    if (bucket === undefined) buckets.get(null)?.push(issue);
    else bucket.push(issue);
  }
  const out: StoreyGroup[] = [];
  for (const storey of storeys) {
    const list = buckets.get(storey.id) ?? [];
    if (list.length > 0) out.push({ storeyId: storey.id, label: storey.name, issues: list });
  }
  const plot = buckets.get(null) ?? [];
  if (plot.length > 0) out.push({ storeyId: null, label: 'Plot and whole building', issues: plot });
  return out;
}

const STATUS_ORDER: Readonly<Record<ComplianceResultStatus, number>> = {
  fail: 0,
  warn: 1,
  pass: 2,
  not_applicable: 3,
};

/** Failures first, then warnings, passes, not-applicable; stable within a status. */
export function sortBySeverity(issues: readonly ComplianceIssueVM[]): ComplianceIssueVM[] {
  return [...issues].sort(
    (a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || a.ruleId.localeCompare(b.ruleId),
  );
}

/** The pack ids present in a report, in first-seen order. */
export function packIdsOf(issues: readonly ComplianceIssueVM[]): string[] {
  const seen: string[] = [];
  for (const issue of issues) {
    const id = issue.packId;
    if (id !== undefined && id !== '' && !seen.includes(id)) seen.push(id);
  }
  return seen;
}

// ---------------------------------------------------------------------------
// Pack review staleness
// ---------------------------------------------------------------------------

export type ReviewState =
  | { readonly kind: 'unreviewed' }
  | { readonly kind: 'overdue'; readonly due: string; readonly daysOver: number }
  | { readonly kind: 'due'; readonly due: string; readonly daysLeft: number }
  | { readonly kind: 'current'; readonly due: string | null };

const DAY_MS = 86_400_000;

/**
 * Where a pack stands against its own review date. `unreviewed` is every seed
 * pack today; a reviewed pack with `nextReviewDue` in the past is `overdue`,
 * within 30 days is `due`, otherwise `current`. A reviewed pack with no due
 * date is `current` with `due: null` — it cannot be stale by a date it never set.
 */
export function packReviewState(
  review: CompliancePackReview | undefined,
  today: Date,
): ReviewState {
  const status = review?.status ?? null;
  if (review === undefined || status === null || status === 'unreviewed') {
    return { kind: 'unreviewed' };
  }
  const due = review.nextReviewDue;
  if (due === null) return { kind: 'current', due: null };
  const dueMs = Date.parse(due);
  if (Number.isNaN(dueMs)) return { kind: 'current', due };
  const days = Math.floor((dueMs - today.getTime()) / DAY_MS);
  if (days < 0) return { kind: 'overdue', due, daysOver: -days };
  if (days <= 30) return { kind: 'due', due, daysLeft: days };
  return { kind: 'current', due };
}
