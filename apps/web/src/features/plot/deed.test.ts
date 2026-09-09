/**
 * Deed entry: sides + diagonals, bearing traverses, bearing parsing. Every
 * case is checked against numbers an architect can verify by hand (3-4-5
 * triangles, a square walked by its bearings), and every refusal names what
 * was wrong — never a silent guess.
 */

import { describe, expect, it } from 'vitest';

import { polygonAreaMm2, polygonDoubledAreaMm2 } from '@garh/model';

import {
  TRAVERSE_MAX_MISCLOSURE_RATIO,
  edgeBearingDeg,
  formatBearingDms,
  parseBearingDeg,
  ringFromSidesAndDiagonals,
  ringFromTraverse,
} from './deed';
import { edgeLengthsMm, isRectilinear } from './geometry';

// ---------------------------------------------------------------------------
// Sides + diagonals
// ---------------------------------------------------------------------------

describe('ringFromSidesAndDiagonals', () => {
  it('rebuilds the 30 × 40 ft rectangle exactly from four sides and the 50 ft diagonal', () => {
    // 9144 : 12192 : 15240 is 3 : 4 : 5 — the diagonal is exact, so is the ring.
    const result = ringFromSidesAndDiagonals([9144, 12192, 9144, 12192], [15240]);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.polygon).toEqual([
      { x: 0, y: 0 },
      { x: 9144, y: 0 },
      { x: 9144, y: 12192 },
      { x: 0, y: 12192 },
    ]);
    expect(polygonDoubledAreaMm2(result.polygon)).toBeGreaterThan(0); // CCW
    expect(result.areaMm2).toBe(9144 * 12192);
    expect(result.perimeterMm).toBe(2 * (9144 + 12192));
    expect(result.sides.every((s) => s.requestedMm === s.achievedMm)).toBe(true);
    // Both diagonals are reported; the second is the deed's cross-check figure.
    expect(result.diagonals).toEqual([
      { from: 0, to: 2, lengthMm: 15240 },
      { from: 1, to: 3, lengthMm: 15240 },
    ]);
  });

  it('builds a skewed quadrilateral and reports rounding honestly (≤1 mm per side)', () => {
    // A deed-style irregular plot: 12.0 m, 9.5 m, 11.2 m, 9.0 m with A–C 15.1 m.
    const sides = [12000, 9500, 11200, 9000];
    const result = ringFromSidesAndDiagonals(sides, [15100]);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.polygon).toHaveLength(4);
    expect(isRectilinear(result.polygon)).toBe(false);
    for (const p of result.polygon) {
      expect(Number.isSafeInteger(p.x)).toBe(true);
      expect(Number.isSafeInteger(p.y)).toBe(true);
    }
    for (const s of result.sides) {
      expect(Math.abs(s.achievedMm - s.requestedMm)).toBeLessThanOrEqual(1);
    }
    const ac = result.diagonals.find((d) => d.from === 0 && d.to === 2);
    expect(Math.abs((ac?.lengthMm ?? 0) - 15100)).toBeLessThanOrEqual(1);
    // bbox at the origin (plot-local frame), CCW.
    expect(Math.min(...result.polygon.map((p) => p.x))).toBe(0);
    expect(Math.min(...result.polygon.map((p) => p.y))).toBe(0);
    expect(polygonDoubledAreaMm2(result.polygon)).toBeGreaterThan(0);
  });

  it('builds a pentagon from five sides and two diagonals from A', () => {
    // Regular-ish pentagon: sides 6000, diagonals 9708 (φ × 6000 rounded).
    const result = ringFromSidesAndDiagonals([6000, 6000, 6000, 6000, 6000], [9708, 9708]);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.polygon).toHaveLength(5);
    expect(result.diagonals).toHaveLength(5);
    for (const s of result.sides) {
      expect(Math.abs(s.achievedMm - s.requestedMm)).toBeLessThanOrEqual(1);
    }
  });

  it('refuses inconsistent figures and names the triangle', () => {
    // 3 + 4 < 10: triangle A–B–C cannot close.
    const result = ringFromSidesAndDiagonals([3000, 4000, 9144, 12192], [10000]);
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.reason).toMatch(/triangle A–B–C/);
    expect(result.reason).toMatch(/mis-read/);
  });

  it('refuses the wrong number of diagonals, zero lengths and fractions', () => {
    expect(ringFromSidesAndDiagonals([1, 2, 3, 4], []).ok).toBe(false);
    expect(ringFromSidesAndDiagonals([9144, 12192, 9144, 12192], [15240, 1]).ok).toBe(false);
    expect(ringFromSidesAndDiagonals([9144, 0, 9144, 12192], [15240]).ok).toBe(false);
    expect(ringFromSidesAndDiagonals([9144.5, 12192, 9144, 12192], [15240]).ok).toBe(false);
    expect(ringFromSidesAndDiagonals([1, 2], []).ok).toBe(false);
  });

  it('a triangle needs no diagonal', () => {
    const result = ringFromSidesAndDiagonals([10000, 8000, 6000], []);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(edgeLengthsMm(result.polygon)).toEqual([10000, 8000, 6000]);
    expect(result.diagonals).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Bearing traverse
// ---------------------------------------------------------------------------

describe('ringFromTraverse', () => {
  it('walks a square by its bearings, closes exactly, and lands CCW at the origin', () => {
    // East 10 m, North 10 m, West 10 m, South 10 m — read anticlockwise.
    const result = ringFromTraverse([
      { bearingDeg: 90, lengthMm: 10000 },
      { bearingDeg: 0, lengthMm: 10000 },
      { bearingDeg: 270, lengthMm: 10000 },
      { bearingDeg: 180, lengthMm: 10000 },
    ]);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.closure).toEqual({
      misclosureMm: 0,
      precisionDenominator: null,
      adjusted: false,
      reversed: false,
    });
    expect(result.polygon).toEqual([
      { x: 0, y: 0 },
      { x: 10000, y: 0 },
      { x: 10000, y: 10000 },
      { x: 0, y: 10000 },
    ]);
    expect(polygonAreaMm2(result.polygon)).toBe(100_000_000);
  });

  it('reverses a clockwise reading to the CCW the model stores, keeping corner A first', () => {
    const result = ringFromTraverse([
      { bearingDeg: 0, lengthMm: 10000 },
      { bearingDeg: 90, lengthMm: 10000 },
      { bearingDeg: 180, lengthMm: 10000 },
      { bearingDeg: 270, lengthMm: 10000 },
    ]);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.closure.reversed).toBe(true);
    expect(polygonDoubledAreaMm2(result.polygon)).toBeGreaterThan(0);
    expect(result.polygon[0]).toEqual({ x: 0, y: 0 });
    expect(result.polygon).toHaveLength(4);
  });

  it('reports the misclosure as "1 in N" and closes it by the compass rule', () => {
    // The last leg is 100 mm short: a 1-in-399 closure.
    const result = ringFromTraverse([
      { bearingDeg: 90, lengthMm: 10000 },
      { bearingDeg: 0, lengthMm: 10000 },
      { bearingDeg: 270, lengthMm: 10000 },
      { bearingDeg: 180, lengthMm: 9900 },
    ]);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.closure.misclosureMm).toBe(100);
    expect(result.closure.adjusted).toBe(true);
    expect(result.closure.precisionDenominator).toBe(399);
    // The ring closed: four corners, still a simple CCW ring, error spread around.
    expect(result.polygon).toHaveLength(4);
    expect(polygonDoubledAreaMm2(result.polygon)).toBeGreaterThan(0);
    const lengths = edgeLengthsMm(result.polygon);
    // Nothing moved by more than the misclosure; the short leg absorbed most of it.
    for (const [i, len] of lengths.entries()) {
      const typed = [10000, 10000, 10000, 9900][i] ?? 0;
      expect(Math.abs(len - typed)).toBeLessThanOrEqual(100);
    }
  });

  it('refuses a gross misclosure instead of hiding a mistyped leg', () => {
    const result = ringFromTraverse([
      { bearingDeg: 90, lengthMm: 10000 },
      { bearingDeg: 0, lengthMm: 10000 },
      { bearingDeg: 270, lengthMm: 10000 },
      { bearingDeg: 180, lengthMm: 5000 }, // 5 m short: 1 in 7
    ]);
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.reason).toMatch(/5000 mm/);
    expect(result.reason).toMatch(/1 in 7\b/);
    expect(result.closure?.misclosureMm).toBe(5000);
    expect(5000 / 35000).toBeGreaterThan(TRAVERSE_MAX_MISCLOSURE_RATIO);
  });

  it('refuses fewer than three legs, zero lengths and non-finite bearings', () => {
    expect(ringFromTraverse([{ bearingDeg: 0, lengthMm: 1 }]).ok).toBe(false);
    expect(
      ringFromTraverse([
        { bearingDeg: 90, lengthMm: 0 },
        { bearingDeg: 0, lengthMm: 10 },
        { bearingDeg: 225, lengthMm: 14 },
      ]).ok,
    ).toBe(false);
    expect(
      ringFromTraverse([
        { bearingDeg: Number.NaN, lengthMm: 10 },
        { bearingDeg: 0, lengthMm: 10 },
        { bearingDeg: 225, lengthMm: 14 },
      ]).ok,
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Bearings as a surveyor writes them
// ---------------------------------------------------------------------------

describe('parseBearingDeg', () => {
  it.each([
    ['135', 135],
    ['135.5', 135.5],
    ['135°', 135],
    ["135°30'", 135.5],
    ['135°30\'15"', 135 + 30 / 60 + 15 / 3600],
    ['135 30 15', 135 + 30 / 60 + 15 / 3600],
    ['135d30m15s', 135 + 30 / 60 + 15 / 3600],
    ['135-30-15', 135 + 30 / 60 + 15 / 3600],
    ['N45E', 45],
    ["N45°30'E", 45.5],
    ['S 12 W', 192],
    ['S45E', 135],
    ['N 30 W', 330],
    ['360', 0],
    ['0', 0],
  ])('%s → %s', (raw, expected) => {
    const deg = parseBearingDeg(raw);
    expect(deg).not.toBeNull();
    expect(Math.abs((deg ?? 0) - expected)).toBeLessThan(1e-9);
  });

  it.each(['', 'north', "12°75'", 'N95E', '1 2 3 4', '45x'])('rejects %s', (raw) => {
    expect(parseBearingDeg(raw)).toBeNull();
  });
});

describe('formatBearingDms / edgeBearingDeg', () => {
  it('formats minutes and seconds the way a field book does', () => {
    expect(formatBearingDms(135.5)).toBe("135°30'");
    expect(formatBearingDms(135 + 30 / 60 + 15 / 3600)).toBe('135°30\'15"');
    expect(formatBearingDms(0)).toBe("0°00'");
    expect(formatBearingDms(359.99999)).toBe("0°00'");
  });

  it('reads an edge bearing back out of a ring, corrected for the plot north', () => {
    const square = [
      { x: 0, y: 0 },
      { x: 10000, y: 0 },
      { x: 10000, y: 10000 },
      { x: 0, y: 10000 },
    ];
    expect(edgeBearingDeg(square, 0, 0)).toBe(90);
    expect(edgeBearingDeg(square, 1, 0)).toBe(0);
    expect(edgeBearingDeg(square, 2, 0)).toBe(270);
    // North rotated 30° clockwise from +Y: the same east edge reads 60°.
    expect(edgeBearingDeg(square, 0, 30)).toBe(60);
    expect(edgeBearingDeg([], 0, 0)).toBeNull();
  });
});
