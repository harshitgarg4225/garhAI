/**
 * Pure readers over `GET /billing/usage` — the numbers the trial cards show.
 * Kept out of the component file so React Fast Refresh sees only components there,
 * and so a test can pin the copy without rendering.
 *
 * Money reads in rupees, with the dollar source beside it. The ledger is micro-USD
 * (the providers bill in dollars); the rupee is derived here at the dated rate the
 * response carries (`money.ts`). With no usable rate — an older api — the copy falls
 * back to dollars rather than inventing a conversion.
 */

import type { SpendBudget, Usage, UsageLine } from '../../lib/api';
import { describeRate, formatInr, formatUsd, inrFor, microsToPaise, parseRate } from './money';

const KIND_LABEL: Record<string, string> = {
  solver: 'Generations',
  render: 'Renders',
  export: 'Exports',
  llm: 'Copilot calls',
};

/** The human label for a metered kind: "solver" → "Generations". */
export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind;
}

/** The line for one metered kind, or null when the plan does not meter it. */
export function lineFor(usage: Usage, kind: string): UsageLine | null {
  return usage.lines.find((line) => line.kind === kind) ?? null;
}

export function describeLine(line: UsageLine): string {
  const label = kindLabel(line.kind);
  if (line.allowance === null) return `${label}: ${line.used} used this period`;
  return `${label}: ${line.used} of ${line.allowance} used this period`;
}

/** Rupees when the response carries a rate, else the dollar string. */
function amount(micros: number, spend: SpendBudget): string {
  return inrFor(micros, spend.usdInrRate) ?? formatUsd(micros);
}

/** True when the response carries a rate the UI can convert at. */
export function hasRate(spend: SpendBudget | null): spend is SpendBudget {
  return spend !== null && parseRate(spend.usdInrRate) !== null;
}

/**
 * "Budget: ₹417.60 of ₹420.00 left (5% platform fee)" — or the dollar form when
 * there is no rate. Null when no budget is enforced.
 */
export function describeSpend(usage: Usage): string | null {
  const spend = usage.spend;
  if (!spend?.enforced) return null;
  const fee = describeFee(usage);
  const left = amount(spend.remainingMicros, spend);
  const cap = amount(spend.capMicros, spend);
  return `Budget: ${left} of ${cap} left${fee ? ` (${fee})` : ''}`;
}

/**
 * The dollar source of {@link describeSpend}, for a hover or a toggle:
 * "$4.96 of $5.00 at ₹84.00 per $ (rate of 2026-09-01)". Null when the copy is
 * already in dollars (no rate) or no budget is enforced.
 */
export function describeSpendSource(usage: Usage): string | null {
  const spend = usage.spend;
  if (!spend?.enforced || !hasRate(spend)) return null;
  const rate = describeRate(spend.usdInrRate, spend.usdInrRateAsOf);
  return `${formatUsd(spend.remainingMicros)} of ${formatUsd(spend.capMicros)} at ${rate ?? ''}`.trim();
}

/**
 * The platform fee, in words, or null when there is none. "5% platform fee" — every
 * charge on the budget carries it, so an architect reading "₹417 left" knows what
 * a rupee of generation actually buys.
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
 * Cost, fee and charge as three strings that ADD UP on screen. The fee is derived as
 * the difference of the two converted totals, not converted on its own, so rounding
 * each leg separately can never leave the line a paisa short.
 */
export function splitCharge(
  costMicros: number,
  chargedMicros: number,
  spend: SpendBudget,
): { cost: string; fee: string; charged: string } {
  const feeMicros = Math.max(0, chargedMicros - costMicros);
  const rate = parseRate(spend.usdInrRate);
  if (rate === null) {
    return {
      cost: formatUsd(costMicros),
      fee: formatUsd(feeMicros),
      charged: formatUsd(chargedMicros),
    };
  }
  const costPaise = microsToPaise(costMicros, rate);
  const chargedPaise = microsToPaise(chargedMicros, rate);
  return {
    cost: formatInr(costPaise),
    fee: formatInr(Math.max(0, chargedPaise - costPaise)),
    charged: formatInr(chargedPaise),
  };
}

/**
 * The fee separated from the cost, for one architect's lifetime charges:
 * "Provider cost ₹84.00 + platform fee ₹4.20 = ₹88.20 charged". Null with no budget
 * object at all (an older api); shown even when nothing is enforced, because the
 * split is the point — an owner reading it can see the fee is applied, and how much.
 */
export function describeSpendBreakdown(usage: Usage): string | null {
  const spend = usage.spend;
  if (!spend) return null;
  const parts = splitCharge(spend.providerCostMicros, spend.spentMicros, spend);
  return `Provider cost ${parts.cost} + platform fee ${parts.fee} = ${parts.charged} charged`;
}

/** The dollar source of {@link describeSpendBreakdown}, or null when already in dollars. */
export function describeSpendBreakdownSource(usage: Usage): string | null {
  const spend = usage.spend;
  if (!spend || !hasRate(spend)) return null;
  const feeMicros = Math.max(0, spend.spentMicros - spend.providerCostMicros);
  const rate = describeRate(spend.usdInrRate, spend.usdInrRateAsOf);
  return `${formatUsd(spend.providerCostMicros)} + ${formatUsd(feeMicros)} = ${formatUsd(spend.spentMicros)} at ${rate ?? ''}`.trim();
}
