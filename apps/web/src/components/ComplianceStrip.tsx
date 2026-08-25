/**
 * ComplianceStrip — the bottom chip strip of the project shell (§12).
 *
 * Golden rule 5: "compliance never blocks, it informs". This strip is a
 * horizontal scroller, never a modal, never a gate. Failures sort first,
 * warnings next, and passes collapse into a single count so a clean project
 * shows "23 checks passed" rather than 23 green chips pushing the failures off
 * screen.
 *
 * §15: "cite on hover, 'Fix it' applies the suggested op diff where
 * computable" — both live in `ComplianceChip`, which this only arranges.
 *
 * Honesty about the packs: every seeded rule value carries
 * `"confidence": "seed"` until an empanelled local architect reviews it, and
 * this strip says so in its footer note rather than implying the numbers are
 * gospel. The product is advisory, not an approval.
 */

import { useMemo, useState } from 'react';
import { Badge, Button, ComplianceChip, Icon, Skeleton, Tooltip, cn } from '@garh/ui';
import { complianceIssueKey } from './types';
import type { ComplianceIssueVM, ComplianceResultStatus } from './types';

const STATUS_ORDER: Readonly<Record<ComplianceResultStatus, number>> = {
  fail: 0,
  warn: 1,
  pass: 2,
  not_applicable: 3,
};

export interface ComplianceStripProps {
  issues: readonly ComplianceIssueVM[];
  /** True while the debounced re-check is in flight (budget: ≤500ms, §14). */
  checking?: boolean | undefined;
  /** Nothing has been checked yet (no plot / no plan). */
  notRun?: boolean | undefined;
  /** Highlight the offending elements on the canvas. */
  onSelectElements?: ((elementIds: readonly string[]) => void) | undefined;
  /** Apply the pack's auto-fix op group. Shown only when `fixAvailable`. */
  onApplyFix?: ((issue: ComplianceIssueVM) => void) | undefined;
  /** Open the full compliance tab. */
  onOpenAll?: (() => void) | undefined;
  className?: string | undefined;
}

export function ComplianceStrip({
  issues,
  checking = false,
  notRun = false,
  onSelectElements,
  onApplyFix,
  onOpenAll,
  className,
}: ComplianceStripProps): JSX.Element {
  const [showPasses, setShowPasses] = useState(false);

  const { failures, warnings, passCount, naCount, hasSeed } = useMemo(() => {
    let pass = 0;
    let na = 0;
    let seed = false;
    const fail: ComplianceIssueVM[] = [];
    const warn: ComplianceIssueVM[] = [];
    for (const issue of issues) {
      if (issue.confidence === 'seed') seed = true;
      if (issue.status === 'fail') fail.push(issue);
      else if (issue.status === 'warn') warn.push(issue);
      else if (issue.status === 'pass') pass += 1;
      else na += 1;
    }
    const bySeverity = (a: ComplianceIssueVM, b: ComplianceIssueVM): number =>
      STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || a.ruleId.localeCompare(b.ruleId);
    return {
      failures: fail.sort(bySeverity),
      warnings: warn.sort(bySeverity),
      passCount: pass,
      naCount: na,
      hasSeed: seed,
    };
  }, [issues]);

  const visible = [...failures, ...warnings, ...(showPasses ? issues.filter((i) => i.status === 'pass') : [])];

  return (
    <div
      className={cn(
        'flex h-strip w-full items-center gap-3 border-t border-line bg-surface px-3',
        className,
      )}
      // Compliance results change on every edit; polite so it does not
      // interrupt the architect mid-drag.
      role="region"
      aria-label="Compliance"
    >
      <span className="flex shrink-0 items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-ink-subtle">
        <Icon name="shield-check" size={14} />
        Checks
      </span>

      {notRun ? (
        <span className="text-xs text-ink-muted">
          Nothing to check yet. Draw the plot and we will start checking as you design.
        </span>
      ) : checking && issues.length === 0 ? (
        <span className="flex items-center gap-2" role="status" aria-live="polite">
          <span className="sr-only">Running the compliance checks</span>
          <Skeleton className="h-6 w-40" shape="block" />
          <Skeleton className="h-6 w-32" shape="block" />
          <Skeleton className="h-6 w-36" shape="block" />
        </span>
      ) : failures.length === 0 && warnings.length === 0 ? (
        <span className="flex items-center gap-2 text-xs text-pass-ink">
          <Icon name="check-circle" size={14} />
          All {passCount} checks passed.
          {naCount > 0 ? (
            <span className="text-ink-subtle">{naCount} did not apply to this plot.</span>
          ) : null}
        </span>
      ) : (
        <ul
          className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto py-1"
          aria-live="polite"
          aria-relevant="additions text"
        >
          {visible.map((issue, index) => (
            <li key={`${complianceIssueKey(issue)}#${index}`} className="shrink-0">
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
                onFix={
                  issue.fixAvailable && onApplyFix !== undefined ? () => onApplyFix(issue) : undefined
                }
              />
            </li>
          ))}
          {passCount > 0 ? (
            <li className="shrink-0">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setShowPasses((v) => !v)}
                aria-expanded={showPasses}
              >
                {showPasses ? 'Hide' : 'Show'} {passCount} passed
              </Button>
            </li>
          ) : null}
        </ul>
      )}

      <span className="ml-auto flex shrink-0 items-center gap-2">
        {checking && issues.length > 0 ? (
          <span className="text-2xs text-ink-subtle" role="status">
            Re-checking…
          </span>
        ) : null}
        {hasSeed ? (
          <Tooltip
            delayMs={150}
            content="Some bye-law values in this city pack are seeded from published tables and have not yet been reviewed by a local architect. Garh AI is advisory — confirm against the current bye-law before submission."
          >
            <Badge tone="warn" icon="info">
              Seed values
            </Badge>
          </Tooltip>
        ) : null}
        {failures.length > 0 ? (
          <Badge tone="fail">
            {failures.length} to fix
          </Badge>
        ) : null}
        {onOpenAll === undefined ? null : (
          <Button size="sm" variant="ghost" iconRight="chevron-right" onClick={onOpenAll}>
            All checks
          </Button>
        )}
      </span>
    </div>
  );
}
