/**
 * The compliance report must survive every row shape the engine emits. The
 * vastu zone rules report `actual` as a list of zones and `limit` as an
 * `{allow}` object; a scalar-only schema rejected the WHOLE report, the hook's
 * catch kept the strip on "nothing to check yet", and a fully evaluated plan
 * looked unchecked.
 */
import { describe, expect, it } from 'vitest';

import { complianceResultSchema, complianceSchema } from './schemas';

const zoneRow = {
  ruleId: 'vastu.pooja.zone',
  packId: 'vastu',
  status: 'warn',
  actual: ['S'],
  limit: { allow: ['NE'], fallback: { allow: ['N', 'E'], scoreRatio: { num: 1, den: 2 } } },
  unit: 'zone',
  message: 'Pooja sits in S; vastu prefers NE.',
};

describe('compliance result values', () => {
  it('accepts a zone rule row with a list actual and an object limit', () => {
    const parsed = complianceResultSchema.parse(zoneRow);
    expect(parsed.actual).toEqual(['S']);
    expect(parsed.limit).toEqual(zoneRow.limit);
  });

  it('keeps an evaluated report evaluated when a zone row is in it', () => {
    const report = complianceSchema.parse({
      evaluated: true,
      projectId: '2f9e3b1a-1c4d-4e5f-8a6b-7c8d9e0f1a2b',
      results: [
        { ruleId: 'nbc.room.habitable.area.min', status: 'pass', actual: 9792969, limit: 9500000 },
        zoneRow,
      ],
    });
    expect(report.evaluated).toBe(true);
    expect(report.results).toHaveLength(2);
  });

  it('still takes the scalar shapes every other rule uses', () => {
    for (const value of [12, 'front', true, null]) {
      expect(
        complianceResultSchema.parse({ ruleId: 'r', status: 'pass', actual: value, limit: value })
          .actual,
      ).toBe(value);
    }
  });
});
