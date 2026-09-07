/**
 * The options boundary must accept what the solver actually sends.
 *
 * The delivered options in the first browser UAT carried seven vastu rows per plan
 * whose `actual` is a list of zones and whose `limit` is an `{allow}` object. The
 * client schema admitted scalars only, `readSolveOutcome` dropped each option
 * silently, and three solved plans rendered as "No plan cleared the quality
 * checks". The fixture below is one of those rows verbatim; the negative control
 * is an option that really is unreadable, which must be COUNTED, not hidden.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import { planOptionSchema, readSolveOutcome, solverJobDetailSchema } from './types';

const VASTU_ROW = {
  ruleId: 'vastu.entrance.edge',
  packId: 'vastu',
  status: 'warn',
  title: 'Main entrance - facing',
  message: 'The main entrance faces S. Vastu prefers an entrance on the N, NE or E.',
  actual: ['S'],
  limit: { allow: ['N', 'NE', 'E'] },
  cite: 'Vastu Shastra - common North/South Indian practice Entrance (dwara) placement',
  hard: false,
  unit: 'zone',
  elements: ['door-main'],
};

const SCALAR_ROW = {
  ruleId: 'nbc.room.bedroom.min_area',
  packId: 'nbc-core',
  status: 'pass',
  actual: 12_600_000,
  limit: 9_500_000,
  hard: true,
};

function option(id: string, rank: number, compliance: unknown[]): Record<string, unknown> {
  return {
    id,
    rank,
    scores: { composite: 71, circulationPercent: 14 },
    ops: [{ type: 'wall.add', payload: { a: { x: 0, y: 0 }, b: { x: 3000, y: 0 } } }],
    signature: ['living:N'],
    stairAnchorId: 'st-s0-start',
    builtUpMm2: 100_980_000,
    footprintMm2: 100_980_000,
    rationaleFacts: ['Kitchen in the SE.'],
    assumptions: [{ field: 'plinthMm', value: 450, reason: 'NBC default', source: 'system' }],
    compliance,
  };
}

describe('planOptionSchema', () => {
  it('accepts a vastu row whose actual is a list and whose limit is an object', () => {
    const parsed = planOptionSchema.safeParse(option('plan_a', 0, [VASTU_ROW, SCALAR_ROW]));
    expect(parsed.success).toBe(true);
    if (!parsed.success) return;
    expect(parsed.data.compliance[0]?.actual).toEqual(['S']);
    expect(parsed.data.compliance[0]?.limit).toEqual({ allow: ['N', 'NE', 'E'] });
  });
});

describe('readSolveOutcome', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders every delivered option when their rule rows carry zone lists', () => {
    const row = solverJobDetailSchema.parse({
      id: 'job-1',
      status: 'succeeded',
      options: [
        option('plan_b', 1, [VASTU_ROW]),
        option('plan_a', 0, [VASTU_ROW, SCALAR_ROW]),
        option('plan_c', 2, [SCALAR_ROW]),
      ],
      optionCount: 3,
      banner: 'Generated 3 plan options.',
    });
    const outcome = readSolveOutcome(row);
    expect(outcome.options.map((o) => o.id)).toEqual(['plan_a', 'plan_b', 'plan_c']);
    expect(outcome.unreadable).toBe(0);
    expect(outcome.banner).toBe('Generated 3 plan options.');
  });

  it('counts an option it truly cannot read instead of dropping it silently', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const broken = { ...option('plan_x', 0, []), ops: 'not-a-list' };
    const row = solverJobDetailSchema.parse({
      id: 'job-2',
      status: 'succeeded',
      options: [broken, option('plan_ok', 1, [SCALAR_ROW])],
      optionCount: 2,
    });
    const outcome = readSolveOutcome(row);
    expect(outcome.options.map((o) => o.id)).toEqual(['plan_ok']);
    expect(outcome.unreadable).toBe(1);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0]?.[1])).toContain('ops');
  });
});
