/**
 * CompliancePage — the full check list (F8, playbook Phase 2).
 *
 * The bottom strip in the shell shows the failures; this tab shows everything,
 * grouped by status, with the citation and the confidence of each rule.
 *
 * Two honesty requirements shape this page:
 *
 *  1. Golden rule 5 — compliance informs, never blocks. There is no "cannot
 *     export until clean" anywhere. Architects override rules for good reasons
 *     and the override is logged, not prevented.
 *
 *  2. The packs ship with `"confidence": "seed"` values taken from published
 *     tables and NOT yet reviewed by an empanelled local architect. The banner
 *     at the top of this page says that plainly. Garh AI is advisory; it is not
 *     an approval, and pretending otherwise is the one failure mode that would
 *     genuinely hurt a user.
 *
 * `compliance` arrives through the outlet context, kept live by the shell's
 * `useLiveCompliance` hook (debounced re-fetch on every confirmed op group).
 * `null` means "nothing evaluated yet" — a different fact from "checked and
 * clean", and the two must never look the same (§15).
 */

import { useMemo } from 'react';
import { Badge, Card, ComplianceChip, EmptyState, Icon } from '@garh/ui';
import { PageBody, complianceIssueKey } from '../../components';
import type { ComplianceIssueVM, ComplianceResultStatus } from '../../components';
import { useProjectOutlet } from '../ProjectShell';

const GROUP_TITLE: Readonly<Record<ComplianceResultStatus, string>> = {
  fail: 'Needs fixing',
  warn: 'Worth a look',
  pass: 'Passing',
  not_applicable: "Doesn't apply to this plot",
};

const GROUP_ORDER: readonly ComplianceResultStatus[] = ['fail', 'warn', 'pass', 'not_applicable'];

export function CompliancePage(): JSX.Element {
  const { compliance } = useProjectOutlet();

  const grouped = useMemo(() => {
    const map = new Map<ComplianceResultStatus, ComplianceIssueVM[]>();
    for (const status of GROUP_ORDER) map.set(status, []);
    for (const issue of compliance ?? []) map.get(issue.status)?.push(issue);
    return map;
  }, [compliance]);

  return (
    <PageBody className="max-w-4xl">
      <AdvisoryBanner />

      {compliance === null ? (
        <EmptyState
          className="mt-5"
          icon="shield-check"
          title="Nothing checked yet"
          description="The rules need a plot boundary to measure against — setbacks, coverage and FAR are all ratios of it. Draw or import the plot on the Brief tab and every rule in your city pack runs automatically, re-checked on each edit."
          demoAction={{
            notApplicable:
              'You are already inside a project; the demo offer lives on the dashboard empty state.',
          }}
        />
      ) : compliance.length === 0 ? (
        <EmptyState
          className="mt-5"
          icon="shield-check"
          title="Nothing to check yet"
          description="Once there is a plot boundary and some geometry, every rule in your city pack runs automatically on each edit."
          demoAction={{
            notApplicable: 'You are already inside a project.',
          }}
        />
      ) : (
        <div className="mt-5 flex flex-col gap-4">
          {GROUP_ORDER.map((status) => {
            const issues = grouped.get(status) ?? [];
            if (issues.length === 0) return null;
            return (
              <Card key={status}>
                <div className="flex items-center justify-between border-b border-line px-4 py-3">
                  <h2 className="text-sm font-semibold text-ink">{GROUP_TITLE[status]}</h2>
                  <Badge tone={status === 'fail' ? 'fail' : status === 'warn' ? 'warn' : 'neutral'}>
                    {issues.length}
                  </Badge>
                </div>
                <ul className="flex flex-col gap-2 p-4">
                  {issues.map((issue, index) => (
                    <li key={`${complianceIssueKey(issue)}#${index}`} className="flex flex-col gap-1">
                      <ComplianceChip
                        status={issue.status}
                        message={issue.message}
                        cite={issue.cite}
                        ruleId={issue.ruleId}
                        confidence={issue.confidence}
                      />
                      {issue.fixHint === undefined ? null : (
                        <p className="pl-1 text-xs text-ink-muted">{issue.fixHint}</p>
                      )}
                    </li>
                  ))}
                </ul>
              </Card>
            );
          })}
        </div>
      )}
    </PageBody>
  );
}

function AdvisoryBanner(): JSX.Element {
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-warn-line bg-warn-soft p-3.5">
      <Icon name="info" size={16} className="mt-0.5 shrink-0 text-warn-ink" />
      <div className="text-xs leading-5 text-warn-ink">
        <p className="font-semibold">These checks are advisory, not an approval.</p>
        <p className="mt-0.5">
          City rule packs are seeded from published bye-law tables and are being reviewed city by
          city with local architects. Every value carries its source and a confidence marker — hover
          any chip to see both. Confirm against the current bye-law before you submit, and keep an
          architect of record on the drawings.
        </p>
      </div>
    </div>
  );
}

export default CompliancePage;
