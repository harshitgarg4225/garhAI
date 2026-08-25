/**
 * Spec: unit-aware parse/format round-trips for the overlay display boundary.
 *
 * The property under test is that the overlay can read back what it wrote. A
 * click-to-edit label seeds a field with its own formatted value; if parsing
 * that value does not return the number it came from, then opening a dimension
 * and pressing Enter without touching anything MOVES A WALL. That is the bug
 * this file exists to make impossible.
 *
 * Where a round trip cannot be exact — ft-in rounds to ⅛ inch, areas to one
 * decimal — the tolerance is asserted explicitly and derived from the format,
 * never fudged upward until it passes.
 */

import { describe, expect, it } from 'vitest';

import { MM_PER_INCH, MM2_PER_SQFT, MM2_PER_SQM } from '../../../lib/units';

import {
  AREA_DECIMALS,
  DIMENSION_BARE_UNIT,
  areaEditSeed,
  areaHint,
  dimensionEditSeed,
  dimensionHint,
  dimensionText,
  dimensionTextMm,
  expandFractionGlyphs,
  parseAreaInput,
  parseDimensionInput,
  roomAreaText,
} from './format';

// ---------------------------------------------------------------------------
// The bare-unit rule
// ---------------------------------------------------------------------------

describe('a bare number in a dimension box', () => {
  it('is millimetres, not feet, whatever the project displays in', () => {
    expect(DIMENSION_BARE_UNIT).toBe('mm');
    expect(parseDimensionInput('3600')).toEqual({ ok: true, mm: 3600 });
    expect(parseDimensionInput('12')).toEqual({ ok: true, mm: 12 });
  });

  it('says so, in both unit systems, so nobody has to guess', () => {
    expect(dimensionHint('ft-in')).toMatch(/plain number is millimetres/i);
    expect(dimensionHint('m')).toMatch(/plain number is millimetres/i);
  });

  it('still understands every qualified form an architect types', () => {
    expect(parseDimensionInput(`12'6"`)).toEqual({ ok: true, mm: 3810 });
    expect(parseDimensionInput('12-6')).toEqual({ ok: true, mm: 3810 });
    expect(parseDimensionInput('3.6m')).toEqual({ ok: true, mm: 3600 });
    expect(parseDimensionInput('360cm')).toEqual({ ok: true, mm: 3600 });
    expect(parseDimensionInput('12 ft')).toEqual({ ok: true, mm: 3658 });
    // Curly quotes, straight off WhatsApp.
    expect(parseDimensionInput('12’6”')).toEqual({ ok: true, mm: 3810 });
  });

  it('refuses what it cannot read, with the formats that do work', () => {
    const bad = parseDimensionInput('about three metres');
    expect(bad.ok).toBe(false);
    if (bad.ok) return;
    expect(bad.error).toMatch(/3600/);
  });

  it('refuses zero and negatives at the field, not three hundred ms later', () => {
    expect(parseDimensionInput('0').ok).toBe(false);
    expect(parseDimensionInput('-500').ok).toBe(false);
  });
});

describe('fraction glyphs', () => {
  it('expands the glyphs formatFtIn prints into the form the parser reads', () => {
    expect(expandFractionGlyphs(`12'-6½"`)).toBe(`12'-6 1/2"`);
    expect(expandFractionGlyphs(`15'-⅛"`)).toBe(`15'- 1/8"`);
    expect(expandFractionGlyphs('3600')).toBe('3600');
  });

  it('closes the round trip that would otherwise break click-to-edit', () => {
    // Without the expansion this throws: the app prints ⅛ and cannot read it.
    expect(parseDimensionInput(`12'-6½"`)).toEqual({ ok: true, mm: 3810 + 13 });
    expect(parseDimensionInput(`7'-10⅛"`)).toEqual({ ok: true, mm: 2391 });
  });
});

// ---------------------------------------------------------------------------
// Length round-trips
// ---------------------------------------------------------------------------

const LENGTHS_MM = [1, 115, 900, 2390, 3600, 3810, 4575, 9144, 12_192, 30_000];

/**
 * The same list without the 1 mm case. `formatFtIn(1, { fraction: 8 })` is
 * `0'-0"` — one millimetre is below ⅛ of an inch, so ft-in has no way to write
 * it and the round trip cannot exist. That is not a defect to widen a tolerance
 * around; it is asserted on its own below. A real dimension never gets there:
 * `buildDimensionChains` drops segments under `minSegmentMm` (100 mm) and the
 * layer hides anything under 18 screen pixels.
 */
const FT_IN_LENGTHS_MM = LENGTHS_MM.filter((mm) => mm >= 100);

describe('dimension round-trips', () => {
  it('seeds the edit field with a value that parses back to itself, exactly', () => {
    for (const mm of LENGTHS_MM) {
      expect(parseDimensionInput(dimensionEditSeed(mm))).toEqual({ ok: true, mm });
    }
  });

  it('round-trips its metric display exactly — millimetre resolution', () => {
    for (const mm of LENGTHS_MM) {
      const shown = dimensionText(mm, 'm');
      const parsed = parseDimensionInput(shown);
      expect(parsed, shown).toEqual({ ok: true, mm });
    }
  });

  it('round-trips its ft-in display to the eighth of an inch it prints', () => {
    // ⅛" is 3.175 mm, and the format rounds half away from zero, so the worst
    // case is half of that. Anything larger means the formatter and the parser
    // disagree about something other than resolution.
    const tolerance = MM_PER_INCH / 8 / 2 + 0.5;
    for (const mm of FT_IN_LENGTHS_MM) {
      const shown = dimensionText(mm, 'ft-in');
      const parsed = parseDimensionInput(shown);
      expect(parsed.ok, shown).toBe(true);
      if (!parsed.ok) continue;
      expect(Math.abs(parsed.mm - mm), `${shown} ⇄ ${String(mm)}`).toBeLessThanOrEqual(tolerance);
    }
  });

  it('cannot round-trip a length below its own ft-in resolution — and says so', () => {
    // 1 mm formats as 0'-0", which parses to zero, which the field rejects
    // because a zero dimension is not a thing you can commit. The failure is
    // loud and at the field, not a silent wall move to 0.
    expect(dimensionText(1, 'ft-in')).toBe(`0'-0"`);
    expect(parseDimensionInput(dimensionText(1, 'ft-in')).ok).toBe(false);
    // Metric keeps millimetre resolution, so the same value survives there.
    expect(parseDimensionInput(dimensionText(1, 'm'))).toEqual({ ok: true, mm: 1 });
  });

  it('is exact in ft-in for values that land on the inch grid', () => {
    for (const inches of [6, 12, 90, 150, 480]) {
      const mm = Math.round(inches * MM_PER_INCH);
      const parsed = parseDimensionInput(dimensionText(mm, 'ft-in'));
      expect(parsed).toEqual({ ok: true, mm });
    }
  });

  it('prints the millimetre value too, because that is what the sheet says', () => {
    expect(dimensionTextMm(3810)).toBe('3,810 mm');
    // Indian digit grouping, not thousands-comma grouping.
    expect(dimensionTextMm(1_245_000)).toBe('12,45,000 mm');
  });

  it('formats ft-in the way a municipal drawing does', () => {
    // 12'-0", never 12'. The canvas and the sheet must not use two conventions.
    expect(dimensionText(3658, 'ft-in')).toMatch(/^12'-0/);
    expect(dimensionText(3810, 'ft-in')).toBe(`12'-6"`);
  });
});

// ---------------------------------------------------------------------------
// Area round-trips
// ---------------------------------------------------------------------------

const AREAS_MM2 = [
  1_100_000, // 1.1 m² — a WC
  5_000_000, // 5.0 m² — the NBC kitchen minimum
  9_500_000, // 9.5 m² — the NBC habitable minimum
  10_661_560, // the demo plan's room
  111_483_648, // a 30x40 ft plot
];

describe('area round-trips', () => {
  it('reads back its own metric label, including the superscript', () => {
    for (const mm2 of AREAS_MM2) {
      const shown = roomAreaText(mm2, 'm');
      const parsed = parseAreaInput(shown, 'm');
      expect(parsed.ok, shown).toBe(true);
      if (!parsed.ok) continue;
      // One printed decimal of m² is 0.05 m² of resolution either way.
      expect(Math.abs(parsed.mm2 - mm2), shown).toBeLessThanOrEqual(0.05 * MM2_PER_SQM + 1);
    }
  });

  it('reads back its own imperial label, commas and all', () => {
    for (const mm2 of AREAS_MM2) {
      const shown = roomAreaText(mm2, 'ft-in');
      const parsed = parseAreaInput(shown, 'ft-in');
      expect(parsed.ok, shown).toBe(true);
      if (!parsed.ok) continue;
      expect(Math.abs(parsed.mm2 - mm2), shown).toBeLessThanOrEqual(0.05 * MM2_PER_SQFT + 1);
    }
  });

  it('reads back the seed the edit field is opened with', () => {
    for (const mm2 of AREAS_MM2) {
      for (const display of ['ft-in', 'm'] as const) {
        const seed = areaEditSeed(mm2, display);
        const parsed = parseAreaInput(seed, display);
        expect(parsed.ok, `${display}: ${seed}`).toBe(true);
      }
    }
  });

  it('shows one decimal in both systems, matching the sheet convention', () => {
    expect(AREA_DECIMALS).toBe(1);
    expect(roomAreaText(10_661_560, 'ft-in')).toBe('114.8 sq ft');
    expect(roomAreaText(10_661_560, 'm')).toBe('10.7 m²');
  });

  it('groups big areas the Indian way', () => {
    // 111 483 648 mm² ≈ 1,200 sq ft. Not "1,200" by accident — by lakh grouping.
    expect(roomAreaText(111_483_648, 'ft-in')).toBe('1,200.0 sq ft');
  });

  it('accepts every area form the brief and the plot editor use', () => {
    expect(parseAreaInput('150 sq ft', 'ft-in').ok).toBe(true);
    expect(parseAreaInput('14 m2', 'm').ok).toBe(true);
    expect(parseAreaInput('14 m²', 'm').ok).toBe(true);
    expect(parseAreaInput('133 gaj', 'ft-in').ok).toBe(true);
    // A rectangle, which is how an Indian plot is quoted.
    const rect = parseAreaInput('30x40 ft', 'ft-in');
    expect(rect.ok).toBe(true);
    if (!rect.ok) return;
    expect(rect.mm2).toBe(9144 * 12_192);
  });

  it('takes a bare number as the project unit, not as millimetres', () => {
    // The opposite of the dimension rule, on purpose: nobody writes a bedroom
    // as 13 000 000 mm².
    const imperial = parseAreaInput('150', 'ft-in');
    const metric = parseAreaInput('150', 'm');
    expect(imperial.ok && metric.ok).toBe(true);
    if (!imperial.ok || !metric.ok) return;
    expect(imperial.mm2).toBeLessThan(metric.mm2);
    expect(imperial.mm2).toBe(Math.round(150 * MM2_PER_SQFT));
    expect(metric.mm2).toBe(150 * MM2_PER_SQM);
  });

  it('refuses garbage and zero, and says what does work', () => {
    const bad = parseAreaInput('big', 'ft-in');
    expect(bad.ok).toBe(false);
    if (bad.ok) return;
    expect(bad.error).toMatch(/sq ft/);
    expect(parseAreaInput('0', 'ft-in').ok).toBe(false);
  });

  it('explains the bare-number rule per unit system', () => {
    expect(areaHint('ft-in')).toMatch(/square feet/i);
    expect(areaHint('m')).toMatch(/square metres/i);
  });
});
