/**
 * A 402 goes to the Billing page, on the reason, and never to "Try again".
 *
 * `billingRouteFor` decides from the status AND the code (a 402 with a body the client
 * has never seen is still a billing answer); the negative controls are the 409s and
 * 500s that must keep their own actions. `describeOutOfCredits` is the banner: every
 * sentence is a fact from usage or the plan list.
 */

import { describe, expect, it } from 'vitest';

import { planListSchema, usageSchema } from '../../lib/api';
import { AppError } from '../../lib/errors';
import {
  billingRouteFor,
  describeOutOfCredits,
  firstPlanWithMore,
  readOutOfCredits,
} from './outOfCredits';

function error(init: { status: number; code: string; data?: Record<string, unknown> }): AppError {
  return new AppError({
    status: init.status,
    code: init.code,
    message: 'm',
    action: 'a',
    ...(init.data === undefined ? {} : { data: init.data }),
  });
}

const PLANS = planListSchema.parse({
  currentPlanCode: 'free',
  plans: [
    {
      code: 'free',
      name: 'Free',
      priceInrPerMonth: 0,
      includedEditorSeats: 1,
      extraSeatInrPerMonth: null,
      summary: 'One architect.',
      allowances: [
        { kind: 'solver', allowance: 10 },
        { kind: 'render', allowance: 5 },
        { kind: 'llm', allowance: 100 },
        { kind: 'export', allowance: 0 },
      ],
    },
    {
      code: 'studio',
      name: 'Studio',
      priceInrPerMonth: 4999,
      includedEditorSeats: 3,
      extraSeatInrPerMonth: 1499,
      summary: 'A small practice.',
      allowances: [
        { kind: 'solver', allowance: 150 },
        { kind: 'render', allowance: 100 },
        { kind: 'llm', allowance: 1500 },
        { kind: 'export', allowance: 100 },
      ],
    },
    {
      code: 'enterprise',
      name: 'Enterprise',
      priceInrPerMonth: 49999,
      includedEditorSeats: 40,
      extraSeatInrPerMonth: 999,
      summary: 'Unmetered.',
      allowances: [
        { kind: 'solver', allowance: null },
        { kind: 'render', allowance: null },
        { kind: 'llm', allowance: null },
        { kind: 'export', allowance: null },
      ],
    },
  ],
});

const USAGE = usageSchema.parse({
  planCode: 'free',
  effectivePlanCode: 'free',
  periodStart: '2026-09-01T00:00:00+00:00',
  periodEnd: '2026-10-01T00:00:00+00:00',
  lines: [
    { kind: 'solver', used: 10, allowance: 10, remaining: 0 },
    { kind: 'export', used: 0, allowance: 0, remaining: 0 },
  ],
  spend: {
    capUsd: '$5.00',
    spentUsd: '$5.00',
    remainingUsd: '$0.00',
    capMicros: 5_000_000,
    spentMicros: 5_000_000,
    remainingMicros: 0,
    enforced: true,
    usdInrRate: '84.00',
    usdInrRateAsOf: '2026-09-01',
  },
});

describe('billingRouteFor', () => {
  it('sends a quota 402 to the Billing page on that reason and kind', () => {
    const route = billingRouteFor(
      error({ status: 402, code: 'quota_exceeded', data: { kind: 'solver', used: 10 } }),
    );
    expect(route).toBe('/billing?reason=quota_exceeded&kind=solver');
  });

  it('sends a spend-cap 402 and a seat-limit 402 too', () => {
    expect(billingRouteFor(error({ status: 402, code: 'spend_cap_exceeded' }))).toBe(
      '/billing?reason=spend_cap_exceeded',
    );
    expect(billingRouteFor(error({ status: 402, code: 'seat_limit_reached' }))).toBe(
      '/billing?reason=seat_limit_reached',
    );
  });

  it('treats any 402 as a billing answer even with an unfamiliar code', () => {
    expect(billingRouteFor(error({ status: 402, code: 'payment_required' }))).toBe(
      '/billing?reason=payment_required',
    );
  });

  it('branches on the code when the status was lost (AppError.from a bare Error)', () => {
    expect(billingRouteFor(error({ status: 0, code: 'quota_exceeded' }))).toBe(
      '/billing?reason=quota_exceeded',
    );
  });

  it('leaves every other error to its own action — the negative control', () => {
    expect(billingRouteFor(error({ status: 409, code: 'no_brief_rooms' }))).toBeNull();
    expect(billingRouteFor(error({ status: 409, code: 'no_plot_boundary' }))).toBeNull();
    expect(billingRouteFor(error({ status: 500, code: 'internal_error' }))).toBeNull();
    expect(billingRouteFor(error({ status: 429, code: 'rate_limited' }))).toBeNull();
  });
});

describe('readOutOfCredits', () => {
  it('round-trips what billingRouteFor wrote', () => {
    const route = billingRouteFor(
      error({ status: 402, code: 'quota_exceeded', data: { kind: 'render' } }),
    );
    const params = new URLSearchParams(route?.split('?')[1] ?? '');
    expect(readOutOfCredits(params)).toEqual({ reason: 'quota_exceeded', kind: 'render' });
    expect(readOutOfCredits(new URLSearchParams(''))).toBeNull();
    expect(readOutOfCredits(new URLSearchParams('reason=spend_cap_exceeded'))).toEqual({
      reason: 'spend_cap_exceeded',
      kind: null,
    });
  });
});

describe('describeOutOfCredits', () => {
  it('names what ran out, the numbers, the reset, and the plan that lifts it', () => {
    const banner = describeOutOfCredits(
      { reason: 'quota_exceeded', kind: 'solver' },
      USAGE,
      PLANS.plans,
    );
    expect(banner.title).toBe("You're out of generations for this period.");
    expect(banner.detail).toContain('10 of 10 used this period.');
    expect(banner.detail).toContain('resets on 01-10-2026');
    expect(banner.detail).toContain('Studio includes 150 generations for ₹4,999 a month.');
  });

  it('says a kind the plan does not include is not included, with no reset date', () => {
    const banner = describeOutOfCredits(
      { reason: 'quota_exceeded', kind: 'export' },
      USAGE,
      PLANS.plans,
    );
    expect(banner.title).toBe("Your plan doesn't include exports.");
    expect(banner.detail).toContain('The free plan does not include exports.');
    expect(banner.detail).not.toContain('resets');
    expect(banner.detail).toContain('Studio includes 100 exports');
  });

  it('describes a spent budget in rupees and does not promise a monthly reset', () => {
    const banner = describeOutOfCredits({ reason: 'spend_cap_exceeded', kind: null }, USAGE, []);
    expect(banner.title).toBe('Your generation budget is spent.');
    expect(banner.detail).toContain('The ₹420.00 budget is a one-off allowance');
    expect(banner.detail).not.toContain('resets');
  });

  it('still reads sensibly before usage has loaded', () => {
    const banner = describeOutOfCredits({ reason: 'quota_exceeded', kind: 'solver' }, null, []);
    expect(banner.title).toBe("You're out of generations for this period.");
    expect(banner.detail).toBe('');
  });

  it('picks the cheapest plan with more, and prefers unmetered over any number', () => {
    expect(firstPlanWithMore(PLANS.plans, 'free', 'solver', 10)?.code).toBe('studio');
    expect(firstPlanWithMore(PLANS.plans, 'studio', 'solver', 150)?.code).toBe('enterprise');
    expect(firstPlanWithMore(PLANS.plans, 'enterprise', 'solver', null)).toBeNull();
  });
});
