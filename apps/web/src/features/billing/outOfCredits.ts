/**
 * What to do with a 402 — the one status whose plain meaning is "a billing decision,
 * not a bug": the plan has no more of that, the budget is spent, every seat is taken.
 *
 * A toast with "Try again" is the wrong answer to all three (it 402s again). The right
 * answer is the Billing page, opened on the reason, with the plans catalogue right
 * there. `billingRouteFor` decides whether an error is that kind of error and where to
 * send it; `describeOutOfCredits` writes the banner the page shows on arrival.
 */

import type { Plan, Usage } from '../../lib/api';
import type { AppError } from '../../lib/errors';
import { inrFor } from './money';
import { kindLabel, lineFor } from './usage';

/** The problem codes the billing package answers 402 with. */
export const BILLING_REFUSAL_CODES = new Set([
  'quota_exceeded',
  'spend_cap_exceeded',
  'seat_limit_reached',
]);

export interface OutOfCredits {
  readonly reason: string;
  readonly kind: string | null;
}

/**
 * `/billing?reason=quota_exceeded&kind=solver` for a billing refusal, else null.
 * Branches on the status as well as the code: a 402 with an unfamiliar body is still
 * a billing answer, and the page is the only place that can act on it.
 */
export function billingRouteFor(error: AppError): string | null {
  const isRefusal = error.status === 402 || BILLING_REFUSAL_CODES.has(error.code);
  if (!isRefusal) return null;
  const params = new URLSearchParams();
  params.set('reason', BILLING_REFUSAL_CODES.has(error.code) ? error.code : 'payment_required');
  const kind = error.data.kind;
  if (typeof kind === 'string' && kind !== '') params.set('kind', kind);
  return `/billing?${params.toString()}`;
}

/** Read the arrival reason back off the page's search params. */
export function readOutOfCredits(search: URLSearchParams): OutOfCredits | null {
  const reason = search.get('reason');
  if (reason === null || reason === '') return null;
  const kind = search.get('kind');
  return { reason, kind: kind === null || kind === '' ? null : kind };
}

function formatPeriodEnd(iso: string): string | null {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const d = new Date(t);
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  return `${dd}-${mm}-${d.getFullYear()}`;
}

/** The cheapest plan above the current one that includes more of `kind`, if any. */
export function firstPlanWithMore(
  plans: readonly Plan[],
  currentCode: string,
  kind: string,
  currentAllowance: number | null,
): Plan | null {
  const current = plans.find((plan) => plan.code === currentCode);
  const floor = current?.priceInrPerMonth ?? 0;
  const better = plans
    .filter((plan) => plan.code !== currentCode && plan.priceInrPerMonth > floor)
    .filter((plan) => {
      const allowance = plan.allowances.find((a) => a.kind === kind)?.allowance ?? null;
      if (allowance === null) return true; // unmetered beats any number
      if (currentAllowance === null) return false;
      return allowance > currentAllowance;
    })
    .sort((a, b) => a.priceInrPerMonth - b.priceInrPerMonth);
  return better[0] ?? null;
}

/**
 * The banner: what ran out, what the numbers are, when it resets, and which plan
 * would lift it. Every sentence is a fact from the usage or plans response; nothing
 * is guessed.
 */
export function describeOutOfCredits(
  arrival: OutOfCredits,
  usage: Usage | null,
  plans: readonly Plan[],
): { title: string; detail: string } {
  if (arrival.reason === 'spend_cap_exceeded') {
    const spend = usage?.spend ?? null;
    const cap = spend === null ? null : (inrFor(spend.capMicros, spend.usdInrRate) ?? spend.capUsd);
    return {
      title: 'Your generation budget is spent.',
      detail:
        (cap === null ? 'The budget' : `The ${cap} budget`) +
        ' is a one-off allowance, not a monthly one. Ask your administrator to raise it; a failed or cancelled generation is refunded automatically.',
    };
  }
  if (arrival.reason === 'seat_limit_reached') {
    return {
      title: 'Every editor seat on this plan is taken.',
      detail: 'Release a seat, buy an extra one, or move to a larger plan below.',
    };
  }
  const kind = arrival.kind ?? 'solver';
  const label = kindLabel(kind).toLowerCase();
  const line = usage === null ? null : lineFor(usage, kind);
  const plan = usage?.effectivePlanCode ?? usage?.planCode ?? 'free';
  const upgrade = firstPlanWithMore(plans, plan, kind, line?.allowance ?? null);
  const numbers =
    line === null
      ? ''
      : line.allowance === 0
        ? ` The ${plan} plan does not include ${label}.`
        : ` ${line.used} of ${line.allowance ?? line.used} used this period.`;
  const reset = usage === null ? null : formatPeriodEnd(usage.periodEnd);
  const resetText =
    line !== null && line.allowance !== 0 && reset !== null
      ? ` The allowance resets on ${reset}.`
      : '';
  const upgradeText =
    upgrade === null
      ? ''
      : ` ${upgrade.name} includes ${describeAllowance(upgrade, kind, label)} for ₹${upgrade.priceInrPerMonth.toLocaleString('en-IN')} a month.`;
  return {
    title:
      line !== null && line.allowance === 0
        ? `Your plan doesn't include ${label}.`
        : `You're out of ${label} for this period.`,
    detail: `${numbers}${resetText}${upgradeText}`.trim(),
  };
}

function describeAllowance(plan: Plan, kind: string, label: string): string {
  const allowance = plan.allowances.find((a) => a.kind === kind)?.allowance ?? null;
  return allowance === null ? `unmetered ${label}` : `${allowance} ${label}`;
}
