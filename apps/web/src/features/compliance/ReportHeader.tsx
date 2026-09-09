/**
 * ReportHeader — what this report IS, before what it says.
 *
 * Live run vs frozen report (a version's, the one the sheets quote), when it
 * ran, the counts, which packs at which versions and where each stands against
 * its review date, the Vastu score when that pack is on, and — expanded on
 * demand — the engine's warnings (a room type no rule reaches, an override key
 * nothing reads: each one is a check that did NOT happen) and the projection's
 * approximations. Hiding those would make the report look more certain than
 * the engine said it was.
 */

import { useState } from 'react';
import { Badge, Button, Card, ProgressRing, Tooltip, cn } from '@garh/ui';

import { formatDateTime } from '../../lib/units';
import { packReviewState } from './filters';
import type { ComplianceReportVM } from './report';

export interface ReportHeaderProps {
  report: ComplianceReportVM;
  checking?: boolean | undefined;
  today: Date;
}

export function ReportHeader({ report, checking = false, today }: ReportHeaderProps): JSX.Element {
  const [showNotes, setShowNotes] = useState(false);
  const packIds = Object.keys(report.packVersions);
  const noteCount = report.warnings.length + report.notes.length;
  // What the API's presentability check counts: fails the architect has NOT
  // accepted. Generate's own gate is stricter (every fail, acknowledged or not).
  const blocking = report.issues.filter((i) => i.status === 'fail' && i.overridden !== true).length;

  return (
    <Card className="p-4" data-testid="report-header">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-ink">
              {report.live
                ? 'Live check of the working design'
                : 'Frozen report for a saved version'}
            </h2>
            {report.live ? (
              <Tooltip
                delayMs={150}
                content="Run just now against the working state; re-run after every confirmed edit. Not stored — save a version to freeze it."
              >
                <Badge tone="info">Live</Badge>
              </Tooltip>
            ) : (
              <Tooltip
                delayMs={150}
                content="Stored with the version it was run on. This is what the sheets and any share link quote."
              >
                <Badge tone="neutral">Frozen</Badge>
              </Tooltip>
            )}
            {checking ? (
              <span className="text-2xs text-ink-subtle" role="status">
                Re-checking…
              </span>
            ) : null}
          </div>
          <p className="text-xs text-ink-muted">
            {report.createdAt === null ? null : `Run ${formatDateTime(report.createdAt)} · `}
            <span data-testid="report-counts">
              {report.counts.fail} failing
              {report.counts.fail > 0 && blocking !== report.counts.fail
                ? ` (${blocking} not yet accepted)`
                : ''}{' '}
              · {report.counts.warn} advisory · {report.counts.pass} passing ·{' '}
              {report.counts.not_applicable} not applicable
              {report.counts.overridden > 0 ? ` · ${report.counts.overridden} overridden` : ''}
            </span>
          </p>
        </div>

        {report.vastuScore !== null ? (
          <div className="flex items-center gap-2" data-testid="vastu-score">
            <ProgressRing value={report.vastuScore} size={44} label="Vastu score" />
            <div className="text-xs">
              <div className="font-semibold text-ink">Vastu {report.vastuScore}/100</div>
              <div className="text-ink-subtle">
                {report.scores.find((s) => s.packId === 'vastu')?.mode ?? 'advisory'} mode
              </div>
            </div>
          </div>
        ) : null}
      </div>

      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs" aria-label="Rule packs">
        {packIds.map((packId) => {
          const state = packReviewState(report.packReview[packId], today);
          return (
            <li key={packId} className="flex items-center gap-1.5">
              <span className="font-medium text-ink">{packId}</span>
              <span className="text-ink-subtle">v{report.packVersions[packId]}</span>
              {state.kind === 'unreviewed' ? (
                <Badge tone="warn">seed</Badge>
              ) : state.kind === 'overdue' ? (
                <Badge tone="fail">review overdue {state.due}</Badge>
              ) : state.kind === 'due' ? (
                <Badge tone="warn">review due {state.due}</Badge>
              ) : (
                <Badge tone="pass">reviewed</Badge>
              )}
            </li>
          );
        })}
      </ul>

      {noteCount > 0 ? (
        <div className="mt-3">
          <Button
            size="sm"
            variant="link"
            onClick={() => setShowNotes((v) => !v)}
            aria-expanded={showNotes}
            className="print:hidden"
          >
            {showNotes ? 'Hide' : 'Show'} {report.warnings.length} engine warning
            {report.warnings.length === 1 ? '' : 's'} and {report.notes.length} approximation
            {report.notes.length === 1 ? '' : 's'}
          </Button>
          <div className={cn(!showNotes && 'hidden print:block')} data-testid="report-notes">
            {report.warnings.length > 0 ? (
              <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-warn-ink">
                {report.warnings.map((w, i) => (
                  <li key={`w${i}`}>{w}</li>
                ))}
              </ul>
            ) : null}
            {report.notes.length > 0 ? (
              <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-ink-muted">
                {report.notes.map((n, i) => (
                  <li key={`n${i}`}>{n}</li>
                ))}
              </ul>
            ) : null}
          </div>
        </div>
      ) : null}

      {report.disclaimers.length > 0 ? (
        <details className="mt-3 text-2xs text-ink-subtle print:open">
          <summary className="cursor-pointer">Pack disclaimers</summary>
          <ul className="mt-1 space-y-1">
            {report.disclaimers.map((d) => (
              <li key={d.packId}>
                <span className="font-medium">{d.packId}:</span> {d.text}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </Card>
  );
}

export default ReportHeader;
