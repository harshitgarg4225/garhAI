/**
 * TrialUsageCard / UsageInline — what the architect has left.
 *
 * Two facts, both enforced server-side: generations used against the period's
 * allowance (the count quota) and money left against the trial budget (the spend
 * cap). Pure presentation: the numbers come in as props so this renders the same
 * on the dashboard, in the Plan options header, and in a test with a fixture.
 *
 * Copy rule (§15): say what happens next, not just the number — "2 of 10 used
 * this period" tells the architect how many more tries the trial holds.
 */

import type { JSX } from 'react';

import type { Usage } from '../../lib/api';
import { describeLine, describeSpend, lineFor } from './usage';

function formatPeriodEnd(iso: string): string | null {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const d = new Date(t);
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  return `${dd}-${mm}-${d.getFullYear()}`;
}

export interface TrialUsageCardProps {
  readonly usage: Usage | null;
  readonly loading?: boolean | undefined;
  readonly error?: string | null | undefined;
}

export function TrialUsageCard({ usage, loading, error }: TrialUsageCardProps): JSX.Element {
  if (usage === null) {
    return (
      <section
        aria-label="Trial usage"
        aria-busy={loading === true}
        className="rounded-md border border-line bg-surface px-4 py-3 text-sm text-ink-muted"
      >
        {error !== null && error !== undefined
          ? `Usage isn't available right now — ${error}`
          : 'Loading your usage…'}
      </section>
    );
  }
  const solver = lineFor(usage, 'solver');
  const render = lineFor(usage, 'render');
  const spend = describeSpend(usage);
  const exhausted =
    (solver !== null && solver.remaining === 0) ||
    (usage.spend !== null && usage.spend.enforced && usage.spend.remainingMicros === 0);
  const periodEnd = formatPeriodEnd(usage.periodEnd);
  return (
    <section
      aria-label="Trial usage"
      className={
        'rounded-md border px-4 py-3 text-sm ' +
        (exhausted ? 'border-fail-line bg-fail-soft' : 'border-line bg-surface')
      }
    >
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <h2 className="font-semibold text-ink">Your trial</h2>
        {solver !== null ? <span className="text-ink">{describeLine(solver)}</span> : null}
        {render !== null ? <span className="text-ink-muted">{describeLine(render)}</span> : null}
        {spend !== null ? <span className="text-ink-muted">{spend}</span> : null}
      </div>
      <p className="mt-1 text-xs text-ink-muted">
        {exhausted
          ? 'Your allowance is used up for this period. A failed or cancelled generation is refunded automatically.'
          : periodEnd !== null
            ? `Allowance resets on ${periodEnd}. A failed or cancelled generation is refunded automatically.`
            : 'A failed or cancelled generation is refunded automatically.'}
      </p>
    </section>
  );
}

export interface UsageInlineProps {
  readonly usage: Usage | null;
}

/** One line for a toolbar: "Generations: 2 of 10 used · Budget: $4.96 of $5.00 left". */
export function UsageInline({ usage }: UsageInlineProps): JSX.Element | null {
  if (usage === null) return null;
  const solver = lineFor(usage, 'solver');
  const spend = describeSpend(usage);
  const parts = [solver !== null ? describeLine(solver) : null, spend].filter(
    (part): part is string => part !== null,
  );
  if (parts.length === 0) return null;
  return (
    <span className="text-xs text-ink-muted" aria-label="Trial usage">
      {parts.join(' · ')}
    </span>
  );
}
