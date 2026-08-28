/**
 * swatch.test.ts — does every one of the fifteen swatches actually show
 * something, and is it the right pattern?
 *
 * "Renders non-empty geometry" is the assertion this file is asked for, and
 * taken literally it is nearly worthless: `hatchFamilies` will hand back a
 * dozen segments that all sit outside the box, or dashed ones whose gaps land
 * squarely over it, and a picker full of blank tiles passes. So the measure is
 * `drawnLengthInside` — real drawn ink, clipped to the swatch box, with the
 * dash cycle applied the way SVG will apply it.
 *
 * The instrument is calibrated first (a measure that always reads "plenty" is
 * the same failure one level down), then pointed at all fifteen patterns.
 */

import { describe, expect, it } from 'vitest';

import type { BBox, HatchFamily } from './geometry';
import { drawnLengthInside } from './ink';
import { HATCH_PATTERN_KEYS, hatchPattern, isSolidPattern } from './patterns';
import {
  familyPath,
  swatchGeometry,
  swatchSpacing,
  SWATCH_TARGET_LINES,
  SWATCH_UNITS,
} from './swatch';

const BOX: BBox = [0, 0, SWATCH_UNITS, SWATCH_UNITS];

function family(partial: Partial<HatchFamily> & Pick<HatchFamily, 'segments'>): HatchFamily {
  return { dashes: [], dashOffset: 0, dotted: false, ...partial };
}

describe('the ink measure itself', () => {
  it('reads a continuous line as its clipped length', () => {
    const inside = family({
      segments: [
        [
          [-500, 512],
          [2000, 512],
        ],
      ],
    });
    expect(drawnLengthInside([inside], BOX)).toBeCloseTo(SWATCH_UNITS, 6);
  });

  it('reads zero for geometry that misses the box entirely', () => {
    const outside = family({
      segments: [
        [
          [-900, -900],
          [-100, -100],
        ],
      ],
    });
    expect(drawnLengthInside([outside], BOX)).toBe(0);
  });

  it('reads zero when the dash cycle parks its gap over the whole box', () => {
    // The blank-swatch failure mode, made concrete: 100 units of ink then
    // 4000 of gap, phased so the gap covers the box.
    const phased = family({
      segments: [
        [
          [0, 512],
          [SWATCH_UNITS, 512],
        ],
      ],
      dashes: [100, 4000],
      dashOffset: 100,
    });
    expect(drawnLengthInside([phased], BOX)).toBe(0);
  });

  it('reads half for a 50/50 dash cycle', () => {
    const dashed = family({
      segments: [
        [
          [0, 512],
          [SWATCH_UNITS, 512],
        ],
      ],
      dashes: [128, 128],
    });
    expect(drawnLengthInside([dashed], BOX)).toBeCloseTo(SWATCH_UNITS / 2, 6);
  });
});

describe('every pattern gets a swatch with real ink in it', () => {
  it('draws at least a box-side of line for all fourteen line patterns', () => {
    for (const key of HATCH_PATTERN_KEYS) {
      const swatch = swatchGeometry(key);
      if (isSolidPattern(key)) {
        expect(swatch.solid).toBe(true);
        expect(swatch.families).toEqual([]);
        continue;
      }
      expect(swatch.solid).toBe(false);
      expect(swatch.families.length, `${key} has no families`).toBeGreaterThan(0);
      const ink = drawnLengthInside(swatch.families, [0, 0, swatch.units, swatch.units]);
      // One box-side of ink is the floor a stipple like AR-SAND clears (it
      // draws ~1.1) while a blank tile cannot. A generous threshold here would
      // be met by geometry nobody can see.
      expect(ink / swatch.units, `${key} draws almost nothing`).toBeGreaterThan(1);
    }
  });

  it('gives every pattern its own picture — no two swatches are the same', () => {
    // `earth` rendering as `cross` is a defect this repo shipped. In a picker
    // it is worse: two tiles that look alike teach an architect the wrong
    // thing about the sheet.
    const drawings = new Map<string, string>();
    for (const key of HATCH_PATTERN_KEYS) {
      if (isSolidPattern(key)) continue;
      const svg = swatchGeometry(key)
        .families.map((f) => `${familyPath(f)}|${f.dashes.join(',')}|${String(f.dashOffset)}`)
        .join(';');
      const clash = [...drawings.entries()].find(([, other]) => other === svg);
      expect(clash, `${key} draws exactly what ${String(clash?.[0])} draws`).toBeUndefined();
      drawings.set(key, svg);
    }
    expect(drawings.size).toBe(14);
  });

  it('fits each pattern to a legible density rather than one global spacing', () => {
    // The reason `swatchSpacing` exists: ANSI31's families sit 3.175 apart and
    // AR-CONC's 149.8. Both must land in the same readable band.
    for (const key of HATCH_PATTERN_KEYS) {
      if (isSolidPattern(key)) continue;
      const swatch = swatchGeometry(key);
      expect(swatch.spacing, `${key}`).toBeGreaterThan(0);
      const perDirection = new Map<number, number>();
      for (const f of swatch.families) {
        const s = f.segments[0];
        if (s === undefined) continue;
        const deg = Math.round(
          ((Math.atan2(s[1][1] - s[0][1], s[1][0] - s[0][0]) * 180) / Math.PI) % 180,
        );
        perDirection.set(deg, (perDirection.get(deg) ?? 0) + f.segments.length);
      }
      const busiest = Math.max(...perDirection.values());
      // The band is wide on purpose. The fit predicts a direction's line
      // count from the definition; a pattern like STEEL, whose eight families
      // are nearly coincident, overshoots because every family rounds its
      // first and last line outward. Both ends still exclude what the fit
      // exists to prevent: a single line, and a solid grey block.
      expect(
        busiest,
        `${key} draws ${String(busiest)} lines in one direction`,
      ).toBeGreaterThanOrEqual(8);
      expect(busiest, `${key} draws ${String(busiest)} lines in one direction`).toBeLessThanOrEqual(
        40,
      );
    }
  });

  it('the fit follows the definition — a coarser target draws fewer lines', () => {
    const fitted = swatchGeometry('diagonal');
    const coarse = swatchGeometry('diagonal', { targetLines: 4 });
    expect(coarse.spacing).toBeGreaterThan(fitted.spacing);
    expect(coarse.families[0]!.segments.length).toBeLessThan(fitted.families[0]!.segments.length);
    // SWATCH_TARGET_LINES across, give or take the two the generator rounds
    // outward at each end.
    expect(fitted.families[0]!.segments.length).toBeGreaterThanOrEqual(SWATCH_TARGET_LINES);
    expect(fitted.families[0]!.segments.length).toBeLessThanOrEqual(SWATCH_TARGET_LINES + 3);
  });

  it('draws each pattern at its own definition angle unless asked otherwise', () => {
    expect(swatchGeometry('diagonal').angleDeg).toBe(45);
    expect(swatchGeometry('brick').angleDeg).toBe(0);
    expect(swatchGeometry('diagonal', { angleDeg: 0 }).angleDeg).toBe(0);
    expect(swatchSpacing('solid')).toBe(0);
    expect(swatchGeometry('solid').families).toEqual([]);
  });

  it('familyPath emits one move-line per segment', () => {
    const path = familyPath(
      family({
        segments: [
          [
            [0, 1],
            [2, 3],
          ],
          [
            [4, 5],
            [6, 7],
          ],
        ],
      }),
    );
    expect(path).toBe('M 0 1 L 2 3 M 4 5 L 6 7');
    expect(familyPath(family({ segments: [] }))).toBe('');
  });

  it('is deterministic — the same pattern fits the same way every time', () => {
    for (const key of HATCH_PATTERN_KEYS) {
      expect(swatchGeometry(key)).toEqual(swatchGeometry(key));
      expect(hatchPattern(key).key).toBe(key);
    }
  });
});
