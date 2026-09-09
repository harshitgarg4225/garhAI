/**
 * The trial numbers, from the wire to the words.
 *
 * `usageSchema` is the contract with `GET /billing/usage` (camelCase, nullable
 * spend, `allowance: null` = unlimited); the describers are the copy the dashboard
 * card and the Plan options header show. Pinned here so a renamed field or a
 * reworded line cannot silently turn "2 of 10 used" into nothing.
 *
 * Money: rupees on screen at the dated rate the response carries, the dollar source
 * one call away, and the dollar form when the api sends no rate.
 */

import { describe, expect, it } from 'vitest';

import { usageSchema } from '../../lib/api';
import {
  describeFee,
  describeLine,
  describeSpend,
  describeSpendBreakdown,
  describeSpendBreakdownSource,
  describeSpendSource,
  hasRate,
  kindLabel,
  lineFor,
  splitCharge,
} from './usage';

const WIRE = {
  planCode: 'free',
  effectivePlanCode: 'free',
  periodStart: '2026-09-01T00:00:00+00:00',
  periodEnd: '2026-10-01T00:00:00+00:00',
  lines: [
    { kind: 'solver', used: 2, allowance: 10, remaining: 8 },
    { kind: 'render', used: 0, allowance: 20, remaining: 20 },
    { kind: 'llm', used: 3, allowance: null, remaining: null },
  ],
  spend: {
    capUsd: '$5.00',
    spentUsd: '$0.04',
    remainingUsd: '$4.96',
    capMicros: 5_000_000,
    spentMicros: 40_000,
    remainingMicros: 4_960_000,
    enforced: true,
    markupPercent: '5',
    providerCostUsd: '$0.03',
    providerCostMicros: 38_095,
    usdInrRate: '84.00',
    usdInrRateAsOf: '2026-09-01',
  },
};

/** The same body from an api that predates the rate: no `usdInrRate*` fields. */
const WIRE_NO_RATE = {
  ...WIRE,
  spend: {
    capUsd: '$5.00',
    spentUsd: '$0.04',
    remainingUsd: '$4.96',
    capMicros: 5_000_000,
    spentMicros: 40_000,
    remainingMicros: 4_960_000,
    enforced: true,
  },
};

describe('usage schema', () => {
  it('parses the billing usage body', () => {
    const usage = usageSchema.parse(WIRE);
    expect(usage.lines).toHaveLength(3);
    expect(usage.spend?.remainingUsd).toBe('$4.96');
    expect(usage.spend?.usdInrRate).toBe('84.00');
  });

  it('tolerates a stack with no spend budget configured', () => {
    const usage = usageSchema.parse({ ...WIRE, spend: null });
    expect(usage.spend).toBeNull();
    expect(describeSpend(usage)).toBeNull();
    expect(describeSpendBreakdown(usage)).toBeNull();
  });

  it('defaults the rate to empty for an older api, and that means dollars', () => {
    const usage = usageSchema.parse(WIRE_NO_RATE);
    expect(usage.spend?.usdInrRate).toBe('');
    expect(hasRate(usage.spend)).toBe(false);
  });

  it('reads `enforced` per line, and assumes enforced when an older api omits it', () => {
    const usage = usageSchema.parse({
      ...WIRE,
      lines: [
        { kind: 'solver', used: 2, allowance: 10, remaining: 8 },
        { kind: 'export', used: 1, allowance: 0, remaining: 0, enforced: false },
      ],
    });
    expect(lineFor(usage, 'solver')?.enforced).toBe(true);
    expect(lineFor(usage, 'export')?.enforced).toBe(false);
  });

  it('refuses a body that dropped the lines', () => {
    expect(() => usageSchema.parse({ ...WIRE, lines: 'nope' })).toThrow();
  });
});

describe('usage copy', () => {
  const usage = usageSchema.parse(WIRE);

  it('says how many generations are used of how many', () => {
    const solver = lineFor(usage, 'solver');
    expect(solver).not.toBeNull();
    expect(describeLine(solver!)).toBe('Generations: 2 of 10 used this period');
  });

  it('does not invent a ceiling for an unlimited kind', () => {
    expect(describeLine(lineFor(usage, 'llm')!)).toBe('Copilot calls: 3 used this period');
  });

  it('labels every metered kind in words an architect uses', () => {
    expect(kindLabel('solver')).toBe('Generations');
    expect(kindLabel('export')).toBe('Exports');
    expect(kindLabel('mystery')).toBe('mystery');
  });

  it('returns null for a kind the plan does not meter', () => {
    expect(lineFor(usage, 'export')).toBeNull();
  });
});

describe('money in rupees, with the dollar source', () => {
  const usage = usageSchema.parse(WIRE);

  it('says how much is left of the cap in rupees, naming the fee', () => {
    expect(describeSpend(usage)).toBe('Budget: ₹416.64 of ₹420.00 left (5% platform fee)');
  });

  it('keeps the dollar figures and the dated rate one hover away', () => {
    expect(describeSpendSource(usage)).toBe('$4.96 of $5.00 at ₹84.00 per $ (rate of 2026-09-01)');
  });

  it('separates the fee from the provider cost, and the line adds up', () => {
    // $0.038095 cost → ₹3.20; $0.04 charged → ₹3.36; fee shown as the difference.
    expect(describeSpendBreakdown(usage)).toBe(
      'Provider cost ₹3.20 + platform fee ₹0.16 = ₹3.36 charged',
    );
    expect(describeSpendBreakdownSource(usage)).toBe(
      '$0.03 + $0.00 = $0.04 at ₹84.00 per $ (rate of 2026-09-01)',
    );
  });

  it('derives the fee as the difference so separate rounding cannot leave it a paisa short', () => {
    const spend = usage.spend!;
    // 1 µUSD cost and 2 µUSD charged each round to ₹0.00; a separately converted fee
    // would too, and the identity would still hold — but with 59 and 120 it would not.
    const parts = splitCharge(59, 120, spend);
    expect(parts).toEqual({ cost: '₹0.00', fee: '₹0.01', charged: '₹0.01' });
  });

  it('falls back to dollars, with no source line, when the api sends no rate', () => {
    const old = usageSchema.parse(WIRE_NO_RATE);
    expect(describeSpend(old)).toBe('Budget: $4.96 of $5.00 left');
    expect(describeSpendSource(old)).toBeNull();
    expect(describeSpendBreakdown(old)).toBe(
      'Provider cost $0.00 + platform fee $0.04 = $0.04 charged',
    );
    expect(describeSpendBreakdownSource(old)).toBeNull();
  });

  it('stays silent about a budget that is not enforced', () => {
    const relaxed = usageSchema.parse({ ...WIRE, spend: { ...WIRE.spend, enforced: false } });
    expect(describeSpend(relaxed)).toBeNull();
    expect(describeSpendSource(relaxed)).toBeNull();
  });
});

describe('the platform fee', () => {
  it('is absent from the wire until the api sends it, and then reads as words', () => {
    // An older api (no fee fields) parses: the schema defaults to no fee.
    expect(describeFee(usageSchema.parse(WIRE_NO_RATE))).toBeNull();
    expect(describeFee(usageSchema.parse(WIRE))).toBe('5% platform fee');
    expect(describeSpend(usageSchema.parse(WIRE_NO_RATE))).toBe('Budget: $4.96 of $5.00 left');
  });

  it('says nothing for a zero fee', () => {
    const zero = usageSchema.parse({ ...WIRE, spend: { ...WIRE.spend, markupPercent: '0' } });
    expect(describeFee(zero)).toBeNull();
    expect(describeSpend(zero)).toBe('Budget: ₹416.64 of ₹420.00 left');
  });
});
