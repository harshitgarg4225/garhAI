/**
 * The display boundary. The conversions themselves are golden-tested in
 * `@garh/model` against their Python twin — what is tested here is the part
 * this app adds: the snap modules, the Indian display defaults (§15), and the
 * autosave badge's version label.
 */

import { describe, expect, it } from 'vitest';

import {
  SNAP_COARSE_MM,
  SNAP_FINE_MM,
  formatDate,
  formatDateTime,
  formatDimensionPair,
  formatGaj,
  formatPhoneIn,
  formatRelative,
  formatRupees,
  formatSqft,
  formatVersionLabel,
  isIntMm,
  parseLengthMm,
  snapMm,
} from './units';

describe('snapping', () => {
  it('defaults to the 115mm half-brick module', () => {
    expect(SNAP_COARSE_MM).toBe(115);
    expect(SNAP_FINE_MM).toBe(25);
  });

  it('always returns integer millimetres', () => {
    for (const value of [0, 57, 58, 114, 115, 116, -57, -58, 3457]) {
      const snapped = snapMm(value, SNAP_COARSE_MM);
      expect(isIntMm(snapped), `${value} -> ${snapped}`).toBe(true);
      // Math.abs: IEEE gives `-115 % 115 === -0`, which Object.is separates
      // from +0. The model contract has no signed zero (the Python mirror's
      // int 0 is unsigned), so the sign of a zero remainder is noise.
      expect(Math.abs(snapped % SNAP_COARSE_MM)).toBe(0);
    }
  });

  it('rounds to the nearest module, half away from zero', () => {
    expect(snapMm(0, 115)).toBe(0);
    expect(snapMm(57, 115)).toBe(0);
    expect(snapMm(58, 115)).toBe(115);
    expect(snapMm(3450, 115)).toBe(3450);
  });

  it('degrades to plain rounding when snapping is off', () => {
    expect(snapMm(2399.6, 0)).toBe(2400);
  });
});

describe('Indian display defaults (§15)', () => {
  it('formats plot dimensions as feet and inches', () => {
    // 30 x 40 ft, the demo plot.
    expect(formatDimensionPair(9144, 12192)).toBe("30' × 40'");
  });

  it('groups rupees the Indian way', () => {
    expect(formatRupees(1245000)).toBe('₹12,45,000');
  });

  it('reports plot area in both sqft and gaj', () => {
    const thirtyByForty = 9144 * 12192;
    expect(formatSqft(thirtyByForty)).toMatch(/^1,200/);
    expect(formatGaj(thirtyByForty)).toMatch(/^133/);
  });

  it('writes dates DD-MM-YYYY', () => {
    expect(formatDate(new Date(2026, 7, 5))).toBe('05-08-2026');
    expect(formatDateTime(new Date(2026, 7, 5, 14, 32))).toBe('05-08-2026, 14:32');
    // Never "Invalid Date" in a UI.
    expect(formatDate(null)).toBe('');
    expect(formatDate('not a date')).toBe('');
  });

  it('formats +91 numbers and leaves anything else alone', () => {
    expect(formatPhoneIn('9876543210')).toBe('+91 98765 43210');
    expect(formatPhoneIn('+91 98765 43210')).toBe('+91 98765 43210');
    expect(formatPhoneIn('12345')).toBe('12345');
  });

  it('parses what an architect actually types', () => {
    expect(parseLengthMm('12\'6"')).toBe(3810);
    expect(parseLengthMm('3.8m')).toBe(3800);
    expect(parseLengthMm('2400')).toBe(2400);
  });
});

describe('formatRelative', () => {
  const now = Date.UTC(2026, 7, 5, 12, 0, 0);

  it('says something useful at every scale', () => {
    expect(formatRelative(new Date(now - 3_000), now)).toBe('just now');
    expect(formatRelative(new Date(now - 45_000), now)).toBe('45s ago');
    expect(formatRelative(new Date(now - 4 * 60_000), now)).toBe('4 min ago');
    expect(formatRelative(new Date(now - 3 * 3_600_000), now)).toBe('3 hr ago');
    expect(formatRelative(new Date(now - 26 * 3_600_000), now)).toBe('yesterday');
    expect(formatRelative(null, now)).toBe('');
  });
});

describe('formatVersionLabel', () => {
  it('counts ops, not indices — an untouched project is v0', () => {
    expect(formatVersionLabel(-1)).toBe('v0');
    expect(formatVersionLabel(0)).toBe('v1');
    expect(formatVersionLabel(213)).toBe('v214');
  });
});
