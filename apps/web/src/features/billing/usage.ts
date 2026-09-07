/**
 * Pure readers over `GET /billing/usage` — the numbers the trial cards show.
 * Kept out of the component file so React Fast Refresh sees only components there,
 * and so a test can pin the copy without rendering.
 */

import type { Usage, UsageLine } from '../../lib/api';
import { formatUsd } from './money';

const KIND_LABEL: Record<string, string> = {
  solver: 'Generations',
  render: 'Renders',
  export: 'Exports',
  llm: 'Copilot calls',
};

/** The line for one metered kind, or null when the plan does not meter it. */
export function lineFor(usage: Usage, kind: string): UsageLine | null {
  return usage.lines.find((line) => line.kind === kind) ?? null;
}

export function describeLine(line: UsageLine): string {
  const label = KIND_LABEL[line.kind] ?? line.kind;
  if (line.allowance === null) return `${label}: ${line.used} used this period`;
  return `${label}: ${line.used} of ${line.allowance} used this period`;
}

export function describeSpend(usage: Usage): string | null {
  const spend = usage.spend;
  if (!spend?.enforced) return null;
  const fee = describeFee(usage);
  return `Budget: ${spend.remainingUsd} of ${spend.capUsd} left${fee ? ` (${fee})` : ''}`;
}

/**
 * The platform fee, in words, or null when there is none. "5% platform fee" — every
 * charge on the budget carries it, so an architect reading "$4.20 left" knows what
 * a dollar of generation actually buys.
 */
export function describeFee(usage: Usage): string | null {
  const spend = usage.spend;
  if (!spend) return null;
  // Tolerate an api that predates the fee (a rolling deploy, a stale dev bundle):
  // no field means no fee, never a crash on the dashboard.
  const percent = (spend.markupPercent ?? '').trim();
  if (percent === '' || percent === '0') return null;
  return `${percent}% platform fee`;
}

/**
 * The fee separated from the cost, for one architect's lifetime charges:
 * "Provider cost $0.04 + platform fee $0.00 = $0.04 charged". Null with no budget
 * object at all (an older api); shown even when nothing is enforced, because the
 * split is the point — an owner reading it can see the fee is applied, and how much.
 */
export function describeSpendBreakdown(usage: Usage): string | null {
  const spend = usage.spend;
  if (!spend) return null;
  const feeMicros = Math.max(0, spend.spentMicros - spend.providerCostMicros);
  return `Provider cost ${formatUsd(spend.providerCostMicros)} + platform fee ${formatUsd(feeMicros)} = ${formatUsd(spend.spentMicros)} charged`;
}
