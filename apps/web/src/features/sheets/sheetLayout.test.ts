/**
 * The sheet-layout mirror table and its fit rule (D-3).
 *
 * The paper dimensions here mirror `services/drawings/sheets/model.py::PAPER_SIZES` and
 * the fit rule mirrors `SheetLayout.validate`. A mirrored table is a table that can
 * drift, and a UI quietly disagreeing with the renderer about the size of A2 would be
 * worse than one with no preview at all — so the numbers are asserted, not assumed.
 *
 * The problem cases matter more than the arithmetic. A title block wider than the
 * drawable area produces a technically valid frame with nowhere for the building, and
 * the renderer will scale a plan down to nothing rather than complain.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * NEGATIVE CONTROLS — each applied, the suite run, the failure observed, reverted.
 * ════════════════════════════════════════════════════════════════════════════
 *   A. `drawableArea` ignores the orientation swap  → the portrait test fails
 *   B. the title-block width check is dropped        → the overflow test fails
 *   C. margins are not subtracted                    → the arithmetic test fails
 */

import { describe, expect, it } from 'vitest';

import { PAPER_SIZES, drawableArea, type LayoutLike } from './sheetLayout';

const HOUSE_STYLE: LayoutLike = {
  paper: 'A2',
  orientation: 'landscape',
  marginLeftMm: 20,
  marginRightMm: 10,
  marginTopMm: 10,
  marginBottomMm: 10,
  titleBlockWidthMm: 180,
  titleBlockHeightMm: 60,
};

describe('the paper table', () => {
  it('carries the ISO A sizes the renderer knows, in landscape', () => {
    // Straight from services/drawings/sheets/model.py. If these ever disagree, the
    // preview lies about the sheet the architect is about to plot.
    expect(PAPER_SIZES.map((size) => [size.name, size.widthMm, size.heightMm])).toEqual([
      ['A0', 1189, 841],
      ['A1', 841, 594],
      ['A2', 594, 420],
      ['A3', 420, 297],
      ['A4', 297, 210],
    ]);
  });

  it('every size is wider than it is tall, because the table is landscape', () => {
    for (const size of PAPER_SIZES) expect(size.widthMm).toBeGreaterThan(size.heightMm);
  });
});

describe('the drawable area', () => {
  it('is the paper less the margins', () => {
    const area = drawableArea(HOUSE_STYLE);
    expect(area.widthMm).toBe(594 - 20 - 10);
    expect(area.heightMm).toBe(420 - 10 - 10);
    expect(area.problem).toBeNull();
  });

  it('swaps the paper when the sheet is portrait', () => {
    const area = drawableArea({ ...HOUSE_STYLE, orientation: 'portrait' });
    expect(area.widthMm).toBe(420 - 20 - 10);
    expect(area.heightMm).toBe(594 - 10 - 10);
  });

  it('refuses a title block wider than the room left for it', () => {
    const area = drawableArea({ ...HOUSE_STYLE, paper: 'A4', titleBlockWidthMm: 400 });
    expect(area.problem).toContain('does not fit');
  });

  it('refuses a title block taller than the room left for it', () => {
    const area = drawableArea({ ...HOUSE_STYLE, paper: 'A4', titleBlockHeightMm: 300 });
    expect(area.problem).toContain('does not fit');
  });

  it('refuses margins that eat the sheet', () => {
    const area = drawableArea({
      ...HOUSE_STYLE,
      paper: 'A4',
      marginLeftMm: 200,
      marginRightMm: 200,
    });
    expect(area.problem).toContain('no room to draw');
  });

  it('accepts a cramped but workable layout', () => {
    // Negative control on the three refusals: they must discriminate rather than
    // rejecting anything unusual. A4 landscape, 10 mm all round, a 270x55 block.
    const area = drawableArea({
      paper: 'A4',
      orientation: 'landscape',
      marginLeftMm: 10,
      marginRightMm: 10,
      marginTopMm: 10,
      marginBottomMm: 10,
      titleBlockWidthMm: 270,
      titleBlockHeightMm: 55,
    });
    expect(area.problem).toBeNull();
    expect(area.widthMm).toBe(277);
  });

  it('names a paper size it does not know rather than guessing one', () => {
    expect(drawableArea({ ...HOUSE_STYLE, paper: 'Letter' }).problem).toContain('not a paper size');
  });
});
