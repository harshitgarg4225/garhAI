/**
 * Spec for the readouts.
 *
 * Two properties matter more than the strings themselves:
 *
 *  1. **The project's units are the readout's units.** An architect who set the
 *     project to metres must never be shown feet by the measure tool while the
 *     dimension strings beside it are metric.
 *  2. **m² and ft² describe the same area.** They are printed together, from
 *     one integer mm², so the only way they can disagree is a bug — and the
 *     ratio between the two printed numbers is asserted, not assumed.
 */

import { describe, expect, it } from 'vitest';

import { MM2_PER_SQFT, MM2_PER_SQM, type Pt } from '@garh/model';

import {
  formatAngle,
  formatAreaBoth,
  formatDelta,
  formatLengthDetail,
  formatMeasureLength,
  measureReadouts,
  measurementLabel,
  NO_VALUE,
} from './format';
import type { Readout } from '../canvas/tools/types';

const P = (x: number, y: number): Pt => ({ x, y });

/** The number out of a formatted string like `1,076.4 sq ft`. */
function numberIn(text: string): number {
  const match = /-?[\d,]+(?:\.\d+)?/.exec(text);
  if (match === null) throw new Error(`no number in ${text}`);
  return Number(match[0].replace(/,/g, ''));
}

function byId(readouts: readonly Readout[], id: string): Readout | undefined {
  return readouts.find((r) => r.id === id);
}

describe('lengths follow the project display units', () => {
  it('prints ft-in for an ft-in project and metres for a metric one', () => {
    expect(formatMeasureLength(3660, 'ft-in')).toBe(`12'-0"`);
    expect(formatMeasureLength(5000, 'm')).toBe('5.00 m');
    // The same millimetres, two projects, two answers — and neither is the mm.
    expect(formatMeasureLength(5000, 'ft-in')).toBe(`16'-5"`);
  });

  it('carries the millimetres alongside, grouped the Indian way', () => {
    expect(formatLengthDetail(13_000, 'ft-in')).toBe(`42'-8" · 13,000 mm`);
    expect(formatLengthDetail(13_000, 'm')).toBe('13.00 m · 13,000 mm');
  });

  it('formats a signed run and rise', () => {
    expect(formatDelta(1200, -450)).toBe('1,200 , -450 mm');
  });
});

describe('area is printed in both systems, from one mm²', () => {
  it('prints metric first, imperial second', () => {
    expect(formatAreaBoth(12_000_000)).toBe('12.00 m² · 129.2 sq ft');
  });

  it('the two printed numbers agree to the precision they are printed at', () => {
    // 100 m² → 1,076.39… sq ft. The conversion constant, independently: one
    // square metre is MM2_PER_SQM/MM2_PER_SQFT = 10.7639 square feet.
    const text = formatAreaBoth(100_000_000);
    const [metric, imperial] = text.split(' · ');
    expect(metric).toBeDefined();
    expect(imperial).toBeDefined();
    const sqm = numberIn(metric ?? '');
    const sqft = numberIn(imperial ?? '');
    expect(sqm).toBe(100);
    expect(sqft / sqm).toBeCloseTo(MM2_PER_SQM / MM2_PER_SQFT, 3);
  });

  it.each([7_531_942, 1, 999_999_999])(
    'prints two views of the SAME %d mm², each within half its printed unit',
    (mm2) => {
      // The ratio test above is blunt at small areas, where one printed decimal
      // is a large slice of the value. The sharp statement is per number: each
      // printed figure is the exact conversion of this mm², rounded to the
      // decimals it is printed at (2 for m², 1 for sq ft). If either side ever
      // converted from the OTHER unit instead of from the millimetres, the
      // error would compound past half a unit and this fires.
      const [metric, imperial] = formatAreaBoth(mm2).split(' · ');
      expect(numberIn(metric ?? '')).toBeCloseTo(mm2 / MM2_PER_SQM, 2);
      expect(numberIn(imperial ?? '')).toBeCloseTo(mm2 / MM2_PER_SQFT, 1);
    },
  );
});

describe('angles', () => {
  it('prints one decimal', () => {
    expect(formatAngle(90)).toBe('90.0°');
    expect(formatAngle(45.36)).toBe('45.4°');
  });

  it('prints an em dash, never 0°, for an undefined angle', () => {
    expect(formatAngle(null)).toBe(NO_VALUE);
  });
});

describe('measureReadouts — distance', () => {
  it('gives a single leg one emphasised length and its Δ', () => {
    const readouts = measureReadouts('distance', [P(0, 0), P(3000, 4000)], 'ft-in');
    expect(byId(readouts, 'length')?.label).toBe('Length');
    expect(byId(readouts, 'length')?.value).toBe(`16'-5" · 5,000 mm`);
    expect(byId(readouts, 'length')?.emphasis).toBe(true);
    expect(byId(readouts, 'delta')?.value).toBe('3,000 , 4,000 mm');
    expect(byId(readouts, 'legs')).toBeUndefined();
  });

  it('gives a chain a total, the leg list, and no misleading Δ', () => {
    const chain = [P(0, 0), P(3000, 4000), P(3000, 9000)];
    const readouts = measureReadouts('distance', chain, 'm');
    expect(byId(readouts, 'length')?.label).toBe('Total (2 legs)');
    expect(byId(readouts, 'length')?.value).toBe('10.00 m · 10,000 mm');
    expect(byId(readouts, 'legs')?.value).toBe('5.00 m + 5.00 m');
    // Δx/Δy under a total would read as describing the whole chain.
    expect(byId(readouts, 'delta')).toBeUndefined();
  });

  it('says nothing at all with one point', () => {
    expect(measureReadouts('distance', [P(0, 0)], 'm')).toEqual([]);
  });

  it('has exactly one emphasised readout, whatever the kind', () => {
    const cases: readonly Readout[][] = [
      measureReadouts('distance', [P(0, 0), P(1000, 0), P(1000, 1000)], 'm'),
      measureReadouts('angle', [P(1000, 0), P(0, 0), P(0, 1000)], 'm'),
      measureReadouts('area', [P(0, 0), P(3000, 0), P(3000, 4000)], 'm'),
    ];
    for (const readouts of cases) {
      expect(readouts.filter((r) => r.emphasis === true)).toHaveLength(1);
    }
  });
});

describe('measureReadouts — angle and area', () => {
  it('reports the corner angle and both arms', () => {
    const readouts = measureReadouts('angle', [P(3000, 0), P(0, 0), P(0, 4000)], 'm');
    expect(byId(readouts, 'angle')?.value).toBe('90.0°');
    expect(byId(readouts, 'arms')?.value).toBe('3.00 m · 4.00 m');
  });

  it('reports area, perimeter and vertex count for a closed region', () => {
    const rect = [P(0, 0), P(3000, 0), P(3000, 4000), P(0, 4000)];
    const readouts = measureReadouts('area', rect, 'ft-in');
    expect(byId(readouts, 'area')?.value).toBe('12.00 m² · 129.2 sq ft');
    expect(byId(readouts, 'perimeter')?.value).toBe(`45'-11" · 14,000 mm`);
    expect(byId(readouts, 'vertices')?.value).toBe('4');
  });

  it('refuses to print an area for an unclosed region', () => {
    // "0.00 m²" is a number, and a number gets read as an answer.
    const readouts = measureReadouts('area', [P(0, 0), P(3000, 0)], 'm');
    expect(byId(readouts, 'area')?.value).toBe(NO_VALUE);
    expect(byId(readouts, 'perimeter')?.label).toBe('Run so far');
  });
});

describe('measurementLabel — the one line drawn on the canvas', () => {
  it('is the headline number for each kind', () => {
    expect(measurementLabel('distance', [P(0, 0), P(3000, 4000)], 'ft-in')).toBe(`16'-5"`);
    expect(measurementLabel('angle', [P(3000, 0), P(0, 0), P(0, 4000)], 'm')).toBe('90.0°');
    expect(measurementLabel('area', [P(0, 0), P(3000, 0), P(3000, 4000), P(0, 4000)], 'm')).toBe(
      '12.00 m² · 129.2 sq ft',
    );
  });
});
