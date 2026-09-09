/**
 * CompliancePage — the full check list (F8, playbook Phase 2), with the numbers.
 *
 * The bottom strip in the shell shows the failures; this tab shows everything a
 * professional needs to defend the design: what each rule measured against
 * what (with units), the pack's own limit when a value override moved it, which
 * element failed, severity and the pack's `hard` flag, the citation with the
 * pack's review standing, the area statement the sheet will print (FAR consumed
 * vs allowed, coverage, per-storey built-up, setbacks), the Vastu score when the
 * pack is on, and every warning and approximation the engine reported.
 *
 * Two honesty requirements shape this page:
 *
 *  1. Golden rule 5 — compliance informs, never blocks. There is no "cannot
 *     export until clean" anywhere. Architects override rules for good reasons
 *     and the override is logged, not prevented: "Accept with reason" records
 *     who/when/why on the project, the row stays red, and Revoke undoes it.
 *
 *  2. The packs ship with `"confidence": "seed"` values taken from published
 *     tables and NOT yet reviewed by an empanelled local architect. The banner
 *     at the top of this page says that plainly, and each row carries its
 *     pack's review standing. Garh AI is advisory; it is not an approval.
 *
 * `compliance` arrives through the outlet context, kept live by the shell's
 * `useLiveCompliance` hook (debounced re-fetch on every confirmed op group).
 * `null` means "nothing evaluated yet" — a different fact from "checked and
 * clean", and the two must never look the same (§15). A failed re-check is a
 * third state: the list shown is the last good one, and the banner says so.
 */

import { useMemo, useState } from 'react';
import { Badge, Button, Card, EmptyState, Icon, Select, cn } from '@garh/ui';
import { PageBody, complianceIssueKey } from '../../components';
import type { ComplianceIssueVM } from '../../components';
import { AreaStatementCard } from '../../features/compliance/AreaStatementCard';
import { ComplianceRow } from '../../features/compliance/ComplianceRow';
import {
  DEFAULT_FILTERS,
  filterIssues,
  groupByStorey,
  packIdsOf,
  packReviewState,
  readRuleOverrides,
  sortBySeverity,
  storeyOfElements,
  storeyRefs,
} from '../../features/compliance/filters';
import type { ComplianceFilters, SeverityFilter } from '../../features/compliance/filters';
import { OverrideDialog } from '../../features/compliance/OverrideDialog';
import { ReportHeader } from '../../features/compliance/ReportHeader';
import { useRuleOverrides } from '../../features/compliance/useRuleOverrides';
import { useModelStore } from '../../stores/model';
import { useSessionStore } from '../../stores/session';
import { useUiStore } from '../../stores/ui';
import { useProjectOutlet } from '../ProjectShell';

const SEVERITY_OPTIONS: readonly { value: SeverityFilter; label: string }[] = [
  { value: 'all', label: 'All results' },
  { value: 'fail', label: 'Needs fixing' },
  { value: 'warn', label: 'Worth a look' },
  { value: 'overridden', label: 'Overridden' },
  { value: 'pass', label: 'Passing' },
  { value: 'not_applicable', label: "Doesn't apply" },
];

export function CompliancePage(): JSX.Element {
  const {
    project,
    units,
    compliance,
    complianceReport,
    complianceChecking,
    complianceError,
    recheckCompliance,
    applyFix,
  } = useProjectOutlet();
  const currentUserId = useSessionStore((s) => s.user?.id ?? null);
  const doc = useModelStore((s) => s.doc);
  const canWrite = useModelStore((s) => s.status === 'ready');
  const overrides = useRuleOverrides(project.id);

  const [filters, setFilters] = useState<ComplianceFilters>(DEFAULT_FILTERS);
  const [byStorey, setByStorey] = useState(false);
  const [accepting, setAccepting] = useState<ComplianceIssueVM | null>(null);

  const storeyOf = useMemo(() => storeyOfElements(doc), [doc]);
  const storeys = useMemo(() => storeyRefs(doc), [doc]);
  const ruleOverrides = useMemo(
    () => readRuleOverrides(doc.plot.regProfile.overrides),
    [doc.plot.regProfile.overrides],
  );
  const packs = useMemo(() => packIdsOf(compliance ?? []), [compliance]);
  const today = useMemo(() => new Date(), []);

  const visible = useMemo(
    () => sortBySeverity(filterIssues(compliance ?? [], filters, storeyOf)),
    [compliance, filters, storeyOf],
  );
  const groups = useMemo(
    () => (byStorey ? groupByStorey(visible, storeys, storeyOf) : null),
    [byStorey, visible, storeys, storeyOf],
  );
  const filtered = filters !== DEFAULT_FILTERS && visible.length !== (compliance ?? []).length;

  function selectElements(elementIds: readonly string[]): void {
    useUiStore.getState().requestCanvasFocus(elementIds);
  }

  function renderRow(issue: ComplianceIssueVM, index: number): JSX.Element {
    const packId = issue.packId ?? '';
    return (
      <ComplianceRow
        key={`${complianceIssueKey(issue)}#${index}`}
        issue={issue}
        units={units}
        override={ruleOverrides[issue.ruleId]}
        currentUserId={currentUserId}
        reviewState={
          packId === '' ? undefined : packReviewState(complianceReport?.packReview[packId], today)
        }
        onFix={issue.fixAvailable ? () => applyFix(issue) : undefined}
        onSelectElements={selectElements}
        onAccept={
          canWrite && (issue.status === 'fail' || issue.status === 'warn')
            ? () => setAccepting(issue)
            : undefined
        }
        onRevoke={canWrite ? () => void overrides.revoke(issue.ruleId) : undefined}
        busy={overrides.busyRuleId === issue.ruleId}
      />
    );
  }

  return (
    <PageBody className="max-w-4xl print:max-w-none">
      <AdvisoryBanner />

      {complianceError !== null ? (
        <div
          className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-fail-line bg-fail-soft p-3 text-xs text-fail-ink print:hidden"
          role="alert"
          data-testid="compliance-error-banner"
        >
          <Icon name="alert-triangle" size={16} className="shrink-0" />
          <span className="font-semibold">The last check failed.</span>
          <span className="text-fail-ink/80">
            {compliance === null
              ? 'Nothing below is current.'
              : 'The results below are from the last successful check and may not describe the current design.'}{' '}
            {complianceError.message}
          </span>
          <Button
            size="sm"
            variant="secondary"
            className="ml-auto"
            onClick={recheckCompliance}
            loading={complianceChecking}
            loadingLabel="Re-checking…"
          >
            Re-check now
          </Button>
        </div>
      ) : null}

      {compliance === null && complianceError === null ? (
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
      ) : compliance !== null && compliance.length === 0 ? (
        <EmptyState
          className="mt-5"
          icon="shield-check"
          title="Nothing to check yet"
          description="Once there is a plot boundary and some geometry, every rule in your city pack runs automatically on each edit."
          demoAction={{
            notApplicable: 'You are already inside a project.',
          }}
        />
      ) : compliance === null ? null : (
        <div className="mt-5 flex flex-col gap-4">
          {complianceReport === null ? null : (
            <ReportHeader report={complianceReport} checking={complianceChecking} today={today} />
          )}

          {/* Filters — screen only; the print annexure is the whole list. */}
          <div
            className="flex flex-wrap items-center gap-2 print:hidden"
            role="search"
            aria-label="Filter the checks"
          >
            <input
              type="search"
              value={filters.query}
              onChange={(event) => setFilters({ ...filters, query: event.target.value })}
              placeholder="Search rule, message or citation…"
              aria-label="Search checks"
              className="garh-focus-ring h-9 min-w-[12rem] flex-1 rounded-md border border-line-strong bg-surface px-2.5 text-sm text-ink placeholder:text-ink-subtle"
            />
            <Select
              value={filters.severity}
              onValueChange={(severity) => setFilters({ ...filters, severity })}
              options={SEVERITY_OPTIONS}
              aria-label="Status"
            />
            {packs.length > 1 ? (
              <Select
                value={filters.pack}
                onValueChange={(pack) => setFilters({ ...filters, pack })}
                options={[
                  { value: 'all', label: 'All packs' },
                  ...packs.map((p) => ({ value: p, label: p })),
                ]}
                aria-label="Rule pack"
              />
            ) : null}
            {storeys.length > 0 ? (
              <Select
                value={filters.storey}
                onValueChange={(storey) => setFilters({ ...filters, storey })}
                options={[
                  { value: 'all', label: 'All storeys' },
                  ...storeys.map((s) => ({ value: s.id, label: s.name })),
                  { value: 'plot', label: 'Plot and whole building' },
                ]}
                aria-label="Storey"
              />
            ) : null}
            <label className="flex items-center gap-1.5 text-xs text-ink-muted">
              <input
                type="checkbox"
                checked={byStorey}
                onChange={(event) => setByStorey(event.target.checked)}
              />
              Group by storey
            </label>
            {filtered ? (
              <Button size="sm" variant="ghost" onClick={() => setFilters(DEFAULT_FILTERS)}>
                Clear filters
              </Button>
            ) : null}
            <Button
              size="sm"
              variant="secondary"
              className="ml-auto"
              onClick={() => window.print()}
              title="Print the compliance annexure — every result with its citation, plus the area statement"
            >
              Print annexure
            </Button>
          </div>

          <p className="text-xs text-ink-subtle" aria-live="polite">
            {filtered
              ? `${visible.length} of ${compliance.length} results shown.`
              : `${compliance.length} results.`}
          </p>

          {visible.length === 0 ? (
            <Card className="p-6 text-center text-sm text-ink-muted">
              No results match these filters.
            </Card>
          ) : groups === null ? (
            <Card>
              <ul className="flex flex-col gap-2 p-3">{visible.map(renderRow)}</ul>
            </Card>
          ) : (
            groups.map((group) => (
              <Card key={group.storeyId ?? 'plot'}>
                <div className="flex items-center justify-between border-b border-line px-4 py-3">
                  <h2 className="text-sm font-semibold text-ink">{group.label}</h2>
                  <Badge tone="neutral">{group.issues.length}</Badge>
                </div>
                <ul className="flex flex-col gap-2 p-3">{group.issues.map(renderRow)}</ul>
              </Card>
            ))
          )}

          {complianceReport?.areas === null || complianceReport === null ? null : (
            <AreaStatementCard
              areas={complianceReport.areas}
              units={units}
              overriddenRuleIds={complianceReport.areas.overriddenRuleIds}
            />
          )}
        </div>
      )}

      <OverrideDialog
        issue={accepting}
        onOpenChange={(open) => {
          if (!open) setAccepting(null);
        }}
        onSubmit={overrides.accept}
        busy={accepting !== null && overrides.busyRuleId === accepting.ruleId}
      />
    </PageBody>
  );
}

function AdvisoryBanner(): JSX.Element {
  return (
    <div
      className={cn(
        'flex items-start gap-2.5 rounded-lg border border-warn-line bg-warn-soft p-3.5',
        'print:border-line print:bg-transparent',
      )}
    >
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
