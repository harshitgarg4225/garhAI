/**
 * The compliance REPORT view model — everything `GET /compliance` says beyond
 * the row list: the area statement, scores, engine warnings, projection notes,
 * pack versions and review status, and whether the run was live or frozen.
 *
 * One mapping, used by the live hook and by anything that later reads a frozen
 * report, so the tab and the strip cannot disagree about what a report holds.
 * Rows go through `toComplianceIssue` (pages/_contracts.ts) — the same function
 * the strip's chips come from.
 */

import { formatLength, formatSqft, formatSqm, type UnitsDisplay } from '@garh/model';
import type {
  ComplianceAreas,
  CompliancePackReview,
  ComplianceReport,
  ComplianceScore,
} from '../../lib/schemas';
import { toComplianceIssue } from '../../pages/_contracts';
import type {
  ComplianceIssueVM,
  ComplianceResultStatus,
  ComplianceValueVM,
} from '../../components';

export interface ComplianceCountsVM {
  readonly pass: number;
  readonly warn: number;
  readonly fail: number;
  readonly not_applicable: number;
  /** Rows an architect accepted with a logged reason (still counted above). */
  readonly overridden: number;
}

export interface ComplianceReportVM {
  readonly evaluated: boolean;
  /** True: run just now, unpersisted. False: the frozen report a version quotes. */
  readonly live: boolean;
  readonly reportId: string | null;
  readonly designVersionId: string | null;
  readonly createdAt: string | null;
  /** Why the rules could not run, when `evaluated` is false. */
  readonly reason: string | null;
  readonly worstStatus: ComplianceResultStatus | null;
  readonly counts: ComplianceCountsVM;
  readonly issues: readonly ComplianceIssueVM[];
  readonly packVersions: Readonly<Record<string, string>>;
  readonly packReview: Readonly<Record<string, CompliancePackReview>>;
  /** Approximations the projection made ("edge roles are derived…"). */
  readonly notes: readonly string[];
  /** Engine warnings — a room type no rule reaches, an override key nothing reads. */
  readonly warnings: readonly string[];
  readonly disclaimers: readonly { readonly packId: string; readonly text: string }[];
  readonly areas: ComplianceAreas | null;
  readonly scores: readonly ComplianceScore[];
  readonly vastuScore: number | null;
}

function countOf(counts: Readonly<Record<string, number>>, key: string): number {
  const n = counts[key];
  return typeof n === 'number' && Number.isFinite(n) ? n : 0;
}

/** Wire report → view model. Row mapping is shared with the strip. */
export function toComplianceReport(report: ComplianceReport): ComplianceReportVM {
  const issues = report.results.map((r) =>
    toComplianceIssue({
      ruleId: r.ruleId,
      status: r.status,
      // The engine writes `message`; `title` is the fallback for rules that have
      // not produced a sentence, and the id is the last honest resort.
      message: r.message ?? r.title ?? r.ruleId,
      cite: r.citeShort ?? r.cite,
      confidence: asConfidence(r.confidence),
      elements: r.elements,
      fixHint: r.fixHint,
      fixAvailable: r.fixAvailable,
      autofix: r.autofix,
      checkType: r.checkType,
      packId: r.packId,
      title: r.title,
      severity: r.severity,
      declaredSeverity: r.declaredSeverity,
      hard: r.hard,
      actual: r.actual,
      limit: r.limit,
      unit: r.unit,
      originalLimit: r.originalLimit,
      valueOverridden: r.valueOverridden,
      overrideValueKeys: r.overrideValueKeys,
      overridden: r.overridden,
      overrideReason: r.overrideReason,
      relaxedToWarn: r.relaxedToWarn,
      citeUrl: r.citeUrl,
      note: r.note,
      notApplicableReason: r.notApplicableReason,
      instances: r.instances,
    }),
  );
  const packVersions: Record<string, string> = {};
  for (const [packId, version] of Object.entries(report.packVersions)) {
    if (typeof version === 'string') packVersions[packId] = version;
  }
  const overridden = issues.reduce((n, i) => (i.overridden === true ? n + 1 : n), 0);
  return {
    evaluated: report.evaluated,
    live: report.live,
    reportId: report.reportId,
    designVersionId: report.designVersionId,
    createdAt: report.createdAt,
    reason: report.reason,
    worstStatus: report.worstStatus,
    counts: {
      pass: countOf(report.counts, 'pass'),
      warn: countOf(report.counts, 'warn'),
      fail: countOf(report.counts, 'fail'),
      not_applicable: countOf(report.counts, 'not_applicable'),
      overridden: Math.max(countOf(report.counts, 'overridden'), overridden),
    },
    issues,
    packVersions,
    packReview: report.packReview,
    notes: report.notes,
    warnings: report.warnings,
    disclaimers: report.disclaimers,
    areas: report.areas,
    scores: report.scores,
    vastuScore: report.vastuScore,
  };
}

const CONFIDENCES = ['seed', 'reviewed', 'verified'] as const;
type Confidence = (typeof CONFIDENCES)[number];

function asConfidence(value: string | null): Confidence | null {
  return (CONFIDENCES as readonly string[]).includes(value ?? '') ? (value as Confidence) : null;
}

// ---------------------------------------------------------------------------
// Formatting what a rule measured
// ---------------------------------------------------------------------------

function isRatioObject(value: unknown): value is { num: number; den: number } {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { num?: unknown }).num === 'number' &&
    typeof (value as { den?: unknown }).den === 'number' &&
    (value as { den: number }).den !== 0
  );
}

function fmtNumber(n: number, decimals: number): string {
  return n.toLocaleString('en-IN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimals,
  });
}

/**
 * "9.5 m²", "1'-6"", "1.75", "2 spaces", "Yes", "NE, E". The engine's `unit`
 * names the measure; the project's display units decide metric vs ft-in for
 * lengths and areas. Anything unrecognised is shown verbatim rather than
 * hidden — a number an architect cannot see is a number they cannot dispute.
 */
export function formatComplianceValue(
  value: ComplianceValueVM | undefined,
  unit: string | undefined,
  units: UnitsDisplay,
): string {
  if (value === undefined || value === null) return '—';
  if (isRatioObject(value)) return fmtNumber(value.num / value.den, 2);
  if (Array.isArray(value)) return value.length === 0 ? '—' : value.map(String).join(', ');
  if (typeof value === 'object') {
    const allow = (value as { allow?: unknown }).allow;
    if (Array.isArray(allow)) return `allowed: ${allow.map(String).join(', ')}`;
    return JSON.stringify(value);
  }
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'string') return value;

  switch (unit) {
    case 'mm':
      return formatLength(value, units, { dropZeroInches: true });
    case 'mm2':
      return units === 'm' ? formatSqm(value) : formatSqft(value);
    case 'ratio':
      return fmtNumber(value, 2);
    case 'percent':
      return `${fmtNumber(value, 1)}%`;
    case 'count':
      return fmtNumber(value, 0);
    case 'deg':
      return `${fmtNumber(value, 1)}°`;
    default:
      return unit === undefined || unit === ''
        ? fmtNumber(value, 2)
        : `${fmtNumber(value, 2)} ${unit}`;
  }
}

/** "8.9 m² of 9.5 m²" — actual against limit, in one glance. */
export function formatActualVsLimit(issue: ComplianceIssueVM, units: UnitsDisplay): string | null {
  if (issue.actual === undefined || issue.limit === undefined) return null;
  if (issue.actual === null && issue.limit === null) return null;
  const actual = formatComplianceValue(issue.actual, issue.unit, units);
  const limit = formatComplianceValue(issue.limit, issue.unit, units);
  return `${actual} of ${limit}`;
}
