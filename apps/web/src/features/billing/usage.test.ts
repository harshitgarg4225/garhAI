/**
 * The trial numbers, from the wire to the words.
 *
 * `usageSchema` is the contract with `GET /billing/usage` (camelCase, nullable
 * spend, `allowance: null` = unlimited); the describers are the copy the dashboard
 * card and the Plan options header show. Pinned here so a renamed field or a
 * reworded line cannot silently turn "2 of 10 used" into nothing.
 */

import { describe, expect, it } from 'vitest';

import { usageSchema } from '../../lib/api';
import { describeLine, describeSpend, lineFor } from './usage';

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
  },
};

describe('usage schema', () => {
  it('parses the billing usage body', () => {
    const usage = usageSchema.parse(WIRE);
    expect(usage.lines).toHaveLength(3);
    expect(usage.spend?.remainingUsd).toBe('$4.96');
  });

  it('tolerates a stack with no spend budget configured', () => {
    const usage = usageSchema.parse({ ...WIRE, spend: null });
    expect(usage.spend).toBeNull();
    expect(describeSpend(usage)).toBeNull();
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

  it('says how much money is left of the cap', () => {
    expect(describeSpend(usage)).toBe('Budget: $4.96 of $5.00 left');
  });

  it('stays silent about a budget that is not enforced', () => {
    const relaxed = usageSchema.parse({ ...WIRE, spend: { ...WIRE.spend, enforced: false } });
    expect(describeSpend(relaxed)).toBeNull();
  });

  it('returns null for a kind the plan does not meter', () => {
    expect(lineFor(usage, 'export')).toBeNull();
  });
});
