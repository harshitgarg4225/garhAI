/**
 * ComplianceRow — one rule on the Compliance tab, with the numbers.
 *
 * The chip carries the sentence; this row carries what a professional has to be
 * able to dispute: what was measured against what (with units), the pack's own
 * limit when a value override moved it, which of three bedrooms failed, the
 * severity and whether the pack marks the rule `hard`, the citation with the
 * pack's review standing, and — for a failing rule — the two ways out: "Fix it"
 * when the client can compute the op group, "Accept with reason" always.
 *
 * An accepted rule stays visibly failing (its status never changes) and shows
 * who accepted it, when, and why, with Revoke beside it. Golden rule 5.
 */

import { Badge, Button, ComplianceChip, Icon, Tooltip, cn } from '@garh/ui';
import type { UnitsDisplay } from '@garh/model';

import { formatDateTime } from '../../lib/units';
import type { RuleOverrideRecord, ReviewState } from './filters';
import { formatComplianceValue } from './report';
import type { ComplianceIssueVM } from '../../components';

export interface ComplianceRowProps {
  issue: ComplianceIssueVM;
  units: UnitsDisplay;
  /** What the profile holds for this rule, when the architect accepted it. */
  override?: RuleOverrideRecord | undefined;
  /** The signed-in user; "you" instead of a name on their own overrides. */
  currentUserId?: string | null | undefined;
  /** Where the rule's pack stands against its review date. */
  reviewState?: ReviewState | undefined;
  /** Present only when a computed fix exists for this rule (§15). */
  onFix?: (() => void) | undefined;
  onSelectElements?: ((elementIds: readonly string[]) => void) | undefined;
  /** Open the accept-with-reason dialog. Absent for a read-only viewer. */
  onAccept?: (() => void) | undefined;
  /** Revoke the acknowledgement. Absent for a read-only viewer. */
  onRevoke?: (() => void) | undefined;
  busy?: boolean | undefined;
}

const SEVERITY_LABEL: Readonly<Record<string, string>> = {
  fail: 'Fails',
  warn: 'Advisory',
  info: 'Note',
};

export function ComplianceRow({
  issue,
  units,
  override,
  currentUserId,
  reviewState,
  onFix,
  onSelectElements,
  onAccept,
  onRevoke,
  busy = false,
}: ComplianceRowProps): JSX.Element {
  const violated = issue.status === 'fail' || issue.status === 'warn';
  const hasNumbers =
    issue.actual !== undefined && issue.actual !== null && issue.limit !== undefined;
  const actual = formatComplianceValue(issue.actual, issue.unit, units);
  const limit = formatComplianceValue(issue.limit, issue.unit, units);
  const original =
    issue.valueOverridden === true
      ? formatComplianceValue(issue.originalLimit, issue.unit, units)
      : null;
  const instances = (issue.instances ?? []).filter((i) => i.status !== 'not_applicable');
  const overriddenBy =
    override === undefined
      ? null
      : override.byUserId !== null && override.byUserId === currentUserId
        ? 'you'
        : (override.byName ?? override.byUserId ?? 'an architect');

  return (
    <li
      className={cn(
        'flex flex-col gap-2 rounded-lg border border-line p-3 print:break-inside-avoid',
        issue.overridden === true && 'bg-surface-muted',
      )}
      data-testid="compliance-row"
      data-rule-id={issue.ruleId}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <ComplianceChip
          status={issue.status}
          message={issue.message}
          cite={issue.cite}
          ruleId={issue.ruleId}
          confidence={issue.confidence}
          onSelect={
            onSelectElements === undefined || issue.elementIds.length === 0
              ? undefined
              : () => onSelectElements(issue.elementIds)
          }
          onFix={onFix}
        />
        <div className="flex shrink-0 flex-wrap items-center gap-1.5 print:hidden">
          {issue.severity === undefined ? null : (
            <Badge
              tone={
                issue.severity === 'fail' ? 'fail' : issue.severity === 'warn' ? 'warn' : 'neutral'
              }
            >
              {SEVERITY_LABEL[issue.severity] ?? issue.severity}
            </Badge>
          )}
          {issue.hard === true ? (
            <Tooltip
              delayMs={150}
              content="The pack marks this rule hard: no mode can soften it to an advisory. Every un-overridden fail blocks Generate; this one cannot be relaxed."
            >
              <Badge tone="fail" icon="alert-triangle">
                Hard
              </Badge>
            </Tooltip>
          ) : null}
          {issue.relaxedToWarn === true ? (
            <Tooltip
              delayMs={150}
              content="Vastu is in advisory mode, so this rule reports a warning where the pack says fail."
            >
              <Badge tone="outline">Relaxed</Badge>
            </Tooltip>
          ) : null}
          {issue.overridden === true ? (
            <Badge tone="info" icon="check-circle">
              Overridden
            </Badge>
          ) : null}
        </div>
      </div>

      {/* The numbers. */}
      {hasNumbers || original !== null || issue.note !== undefined ? (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
          {hasNumbers ? (
            <>
              <dt className="text-ink-subtle">Measured</dt>
              <dd className="font-mono text-ink" data-testid="actual-vs-limit">
                {actual} <span className="text-ink-subtle">of</span> {limit}
              </dd>
            </>
          ) : null}
          {original !== null ? (
            <>
              <dt className="text-ink-subtle">Pack limit</dt>
              <dd className="font-mono text-ink" data-testid="original-limit">
                {original}{' '}
                <span className="font-sans text-ink-subtle">
                  (your override: {(issue.overrideValueKeys ?? []).join(', ') || 'value'})
                </span>
              </dd>
            </>
          ) : null}
          {issue.note === undefined ? null : (
            <>
              <dt className="text-ink-subtle">Note</dt>
              <dd className="text-ink-muted">{issue.note}</dd>
            </>
          )}
        </dl>
      ) : null}

      {/* Per-element instances: which bedroom of three. */}
      {instances.length > 1 || (instances.length === 1 && instances[0]?.label !== '') ? (
        <ul className="flex flex-col gap-0.5 text-xs" aria-label="Elements checked">
          {instances.map((inst, index) => (
            <li key={`${inst.elementId ?? 'x'}#${index}`} className="flex items-center gap-2">
              <Icon
                name={inst.status === 'pass' ? 'check-circle' : 'alert-circle'}
                size={12}
                className={inst.status === 'pass' ? 'text-pass-ink' : 'text-fail-ink'}
              />
              <span className="text-ink">{inst.label || inst.elementId}</span>
              <span className="font-mono text-ink-muted">
                {formatComplianceValue(inst.actual, issue.unit, units)} of{' '}
                {formatComplianceValue(inst.limit, issue.unit, units)}
              </span>
              {inst.elementId !== null && onSelectElements !== undefined ? (
                <Button
                  size="sm"
                  variant="link"
                  className="print:hidden"
                  onClick={() => onSelectElements([inst.elementId ?? ''])}
                >
                  Show
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {/* Citation with the pack's review standing. */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-2xs text-ink-subtle">
        {issue.packId === undefined ? null : (
          <span className="uppercase tracking-wider">{issue.packId}</span>
        )}
        {issue.cite === undefined ? null : <span>{issue.cite}</span>}
        {issue.citeUrl === undefined ? null : (
          <a
            href={issue.citeUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-0.5 text-brand-ink underline-offset-2 hover:underline print:hidden"
          >
            Source <Icon name="external-link" size={10} />
          </a>
        )}
        {reviewState === undefined ? null : <ReviewBadge state={reviewState} />}
      </div>

      {/* The architect's decision. */}
      {issue.overridden === true && override !== undefined ? (
        <div
          className="flex flex-wrap items-start justify-between gap-2 rounded-md border border-info-line bg-info-soft p-2 text-xs"
          data-testid="override-record"
        >
          <div className="text-ink">
            <span className="font-semibold">Accepted by {overriddenBy}</span>
            {override.at === null ? null : (
              <span className="text-ink-muted"> · {formatDateTime(override.at)}</span>
            )}
            <p className="mt-0.5 whitespace-pre-wrap">{override.reason}</p>
          </div>
          {onRevoke === undefined ? null : (
            <Button
              size="sm"
              variant="ghost"
              className="print:hidden"
              onClick={onRevoke}
              loading={busy}
            >
              Revoke
            </Button>
          )}
        </div>
      ) : issue.overridden === true && issue.overrideReason !== undefined ? (
        <p className="text-xs text-ink-muted" data-testid="override-record">
          Accepted with reason: {issue.overrideReason}
        </p>
      ) : null}

      {violated && issue.overridden !== true ? (
        <div className="flex flex-wrap items-center gap-2 print:hidden">
          {issue.fixHint === undefined ? null : (
            <p className="text-xs text-ink-muted">{issue.fixHint}</p>
          )}
          {onAccept === undefined ? null : (
            <Button
              size="sm"
              variant="subtle"
              className="ml-auto"
              onClick={onAccept}
              loading={busy}
            >
              Accept with reason
            </Button>
          )}
        </div>
      ) : issue.fixHint !== undefined && violated ? (
        <p className="text-xs text-ink-muted print:hidden">{issue.fixHint}</p>
      ) : null}
    </li>
  );
}

function ReviewBadge({ state }: { state: ReviewState }): JSX.Element {
  switch (state.kind) {
    case 'unreviewed':
      return (
        <Tooltip
          delayMs={150}
          content="Seed pack: not yet reviewed by an empanelled local architect."
        >
          <Badge tone="warn">Seed · unreviewed</Badge>
        </Tooltip>
      );
    case 'overdue':
      return (
        <Badge tone="fail" icon="alert-triangle">
          Review overdue since {state.due}
        </Badge>
      );
    case 'due':
      return <Badge tone="warn">Review due {state.due}</Badge>;
    default:
      return <Badge tone="pass">Reviewed{state.due === null ? '' : ` · next ${state.due}`}</Badge>;
  }
}

export default ComplianceRow;
