import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import {
  GOLDEN_UNIT_FAILURES,
  GOLDEN_UNIT_PAIRS,
  MM2_PER_GAJ,
  UnitParseError,
  formatArea,
  formatFixed,
  formatFtIn,
  formatGaj,
  formatIndianNumber,
  formatLength,
  formatMetres,
  formatMm,
  formatPlotArea,
  formatRupees,
  formatRupeesCompact,
  formatSqft,
  formatSqm,
  fromGaj,
  fromSqft,
  isIntMm,
  normaliseLengthInput,
  parseAreaMm2,
  parseLengthMm,
  roundHalfAwayFromZero,
  toGaj,
  toSqft,
  toSqm,
  tryParseLengthMm,
} from './units';

describe('roundHalfAwayFromZero', () => {
  it('rounds halves away from zero, not to even', () => {
    expect(roundHalfAwayFromZero(0.5)).toBe(1);
    expect(roundHalfAwayFromZero(1.5)).toBe(2);
    expect(roundHalfAwayFromZero(2.5)).toBe(3); // banker's rounding would give 2
    expect(roundHalfAwayFromZero(-0.5)).toBe(-1); // Math.round would give -0
    expect(roundHalfAwayFromZero(-1.5)).toBe(-2);
    expect(roundHalfAwayFromZero(-2.5)).toBe(-3);
    expect(roundHalfAwayFromZero(0)).toBe(0);
  });

  it('rejects non-finite input rather than producing garbage mm', () => {
    expect(() => roundHalfAwayFromZero(Number.NaN)).toThrow(RangeError);
    expect(() => roundHalfAwayFromZero(Number.POSITIVE_INFINITY)).toThrow(RangeError);
  });
});

describe('GOLDEN_UNIT_PAIRS (cross-language contract)', () => {
  it.each(GOLDEN_UNIT_PAIRS as readonly [string, number][])(
    'parses %j to %i mm',
    (input: string, mm: number) => {
      expect(parseLengthMm(input)).toBe(mm);
    },
  );

  it.each([...GOLDEN_UNIT_FAILURES])('rejects %j', (input: string) => {
    expect(() => parseLengthMm(input)).toThrow(UnitParseError);
  });

  it('matches schema/golden-unit-pairs.json byte for byte', () => {
    const url = new URL('../schema/golden-unit-pairs.json', import.meta.url);
    const data = JSON.parse(readFileSync(url, 'utf8')) as {
      pairs: [string, number][];
      failures: string[];
    };
    expect(data.pairs).toEqual(GOLDEN_UNIT_PAIRS.map(([i, m]) => [i, m]));
    expect(data.failures).toEqual([...GOLDEN_UNIT_FAILURES]);
  });

  it('every parsed value is a safe integer', () => {
    for (const [input] of GOLDEN_UNIT_PAIRS) {
      expect(isIntMm(parseLengthMm(input))).toBe(true);
    }
  });
});

describe('normaliseLengthInput', () => {
  it('folds unicode primes, dashes and spaces to ASCII', () => {
    expect(normaliseLengthInput('12′6″')).toBe('12\'6"');
    expect(normaliseLengthInput('−3800')).toBe('-3800');
    expect(normaliseLengthInput('12 ft')).toBe('12 ft');
  });

  it('drops thousands commas only between digits', () => {
    expect(normaliseLengthInput('12,45,000')).toBe('1245000');
    expect(normaliseLengthInput('3,,800')).toBe('3,800');
  });
});

describe('tryParseLengthMm', () => {
  it('reports failure without throwing', () => {
    expect(tryParseLengthMm('12\'6"')).toEqual({ ok: true, mm: 3810 });
    const bad = tryParseLengthMm('nonsense');
    expect(bad.ok).toBe(false);
  });
});

describe('default unit', () => {
  it('treats a bare number as mm, feet or metres on request', () => {
    expect(parseLengthMm('12', 'mm')).toBe(12);
    expect(parseLengthMm('12', 'ft-in')).toBe(3658);
    expect(parseLengthMm('12', 'm')).toBe(12000);
  });
});

describe('formatFtIn', () => {
  it('formats the municipal-drawing way', () => {
    expect(formatFtIn(3810)).toBe('12\'-6"');
    expect(formatFtIn(3658)).toBe('12\'-0"');
    expect(formatFtIn(0)).toBe('0\'-0"');
    expect(formatFtIn(-3810)).toBe('-12\'-6"');
  });

  it('carries 12 inches into a foot', () => {
    // 11'-11.6" rounds to 12'-0", never 11'-12"
    expect(formatFtIn(3657)).toBe('12\'-0"');
  });

  it('supports inch fractions with typographic glyphs', () => {
    expect(formatFtIn(3823, { fraction: 2 })).toBe('12\'-6½"');
    expect(formatFtIn(165, { fraction: 4 })).toBe('0\'-6½"');
    expect(formatFtIn(159, { fraction: 4 })).toBe('0\'-6¼"');
  });

  it('can drop a zero inches part', () => {
    expect(formatFtIn(3658, { dropZeroInches: true })).toBe("12'");
  });

  it('round-trips through parseLengthMm within one inch', () => {
    for (const mm of [0, 305, 914, 3658, 3810, 9144, 12192]) {
      const text = formatFtIn(mm);
      expect(Math.abs(parseLengthMm(text) - mm)).toBeLessThanOrEqual(13);
    }
  });
});

describe('formatMetres / formatMm', () => {
  it('formats metres with explicit rounding', () => {
    expect(formatMetres(3800)).toBe('3.80 m');
    expect(formatMetres(3805)).toBe('3.81 m'); // half away from zero
    expect(formatMetres(-3800)).toBe('-3.80 m');
    expect(formatMetres(3800, 0)).toBe('4 m');
    expect(formatMetres(3800, 3, false)).toBe('3.800');
  });

  it('formats mm for drawings', () => {
    expect(formatMm(3800)).toBe('3800 mm');
    expect(formatMm(3800, false)).toBe('3800');
  });

  it('follows the project display units', () => {
    expect(formatLength(3810, 'ft-in')).toBe('12\'-6"');
    expect(formatLength(3810, 'm')).toBe('3.81 m');
  });
});

describe('areas', () => {
  const oneSqft = 92_903.04;

  it('converts both ways', () => {
    expect(toSqft(fromSqft(1200))).toBeCloseTo(1200, 6);
    expect(toSqm(1_000_000)).toBe(1);
    expect(toGaj(MM2_PER_GAJ)).toBe(1);
    expect(fromGaj(1)).toBe(836_127);
  });

  it('1 gaj is 9 sq ft', () => {
    expect(toSqft(MM2_PER_GAJ)).toBeCloseTo(9, 9);
    expect(MM2_PER_GAJ / oneSqft).toBeCloseTo(9, 9);
  });

  it('formats the Indian way', () => {
    const plot = fromSqft(1200);
    expect(formatSqft(plot)).toBe('1,200.0 sq ft');
    expect(formatGaj(plot)).toBe('133 gaj');
    expect(formatPlotArea(plot)).toBe('1,200.0 sq ft · 133 gaj');
    expect(formatArea(plot, 'm')).toBe(formatSqm(plot));
  });
});

describe('Indian number and currency formatting', () => {
  it('groups lakh and crore', () => {
    expect(formatIndianNumber(0)).toBe('0');
    expect(formatIndianNumber(999)).toBe('999');
    expect(formatIndianNumber(1000)).toBe('1,000');
    expect(formatIndianNumber(100000)).toBe('1,00,000');
    expect(formatIndianNumber(1245000)).toBe('12,45,000');
    expect(formatIndianNumber(123456789)).toBe('12,34,56,789');
    expect(formatIndianNumber(-1234567)).toBe('-12,34,567');
  });

  it('formats rupees', () => {
    expect(formatRupees(1245000)).toBe('₹12,45,000');
    expect(formatRupees(1245000.5, { decimals: 2 })).toBe('₹12,45,000.50');
    expect(formatRupeesCompact(12_500_000)).toBe('₹1.25 Cr');
    expect(formatRupeesCompact(4_500_000)).toBe('₹45.0 L');
    expect(formatRupeesCompact(85_000)).toBe('₹85,000');
  });

  it('formatFixed rounds half away from zero', () => {
    expect(formatFixed(1.25, 1)).toBe('1.3');
    expect(formatFixed(-1.25, 1)).toBe('-1.3');
    expect(formatFixed(1200, 0)).toBe('1,200');
  });
});

describe('parseAreaMm2', () => {
  it('reads the ways a plot gets quoted', () => {
    expect(parseAreaMm2('1,200 sqft')).toBe(fromSqft(1200));
    expect(parseAreaMm2('1200')).toBe(fromSqft(1200));
    expect(parseAreaMm2('133 gaj')).toBe(fromGaj(133));
    expect(parseAreaMm2('111 sqm')).toBe(111_000_000);
    // 30x40 ft plot = 9144 x 12192 mm
    expect(parseAreaMm2('30x40 ft')).toBe(9144 * 12192);
    expect(parseAreaMm2('30 x 40')).toBe(9144 * 12192);
  });

  it('rejects unknown area units', () => {
    expect(() => parseAreaMm2('12 bigha')).toThrow(UnitParseError);
  });
});
