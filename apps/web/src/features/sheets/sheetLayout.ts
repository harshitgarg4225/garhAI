/**
 * sheetLayout.ts — paper sizes and the "does this layout leave anywhere to draw" check.
 *
 * The dimensions mirror `services/drawings/sheets/model.py::PAPER_SIZES`, and the fit
 * rule mirrors `SheetLayout.validate`. The server is authoritative — it refuses a bad
 * layout whatever the browser thinks — and this exists so the architect finds out while
 * they are typing rather than after a save round trip.
 *
 * A mirrored table is a table that can drift, so `sheetLayout.test.ts` asserts these
 * against the same numbers the Python side uses. A UI that quietly disagreed with the
 * renderer about the size of A2 would be worse than one with no preview at all.
 */

export interface PaperSize {
  readonly name: string;
  /** Landscape dimensions. Portrait swaps them. */
  readonly widthMm: number;
  readonly heightMm: number;
}

/** ISO A sizes, landscape. Mirrors the Python `PAPER_SIZES`. */
export const PAPER_SIZES: readonly PaperSize[] = [
  { name: 'A0', widthMm: 1189, heightMm: 841 },
  { name: 'A1', widthMm: 841, heightMm: 594 },
  { name: 'A2', widthMm: 594, heightMm: 420 },
  { name: 'A3', widthMm: 420, heightMm: 297 },
  { name: 'A4', widthMm: 297, heightMm: 210 },
];

export interface LayoutLike {
  readonly paper: string;
  readonly orientation: 'landscape' | 'portrait';
  readonly marginLeftMm: number;
  readonly marginRightMm: number;
  readonly marginTopMm: number;
  readonly marginBottomMm: number;
  readonly titleBlockWidthMm: number;
  readonly titleBlockHeightMm: number;
}

export interface DrawableArea {
  readonly widthMm: number;
  readonly heightMm: number;
  /** Null when the layout works. A sentence when it does not. */
  readonly problem: string | null;
}

/**
 * The drawable area this layout leaves, and what is wrong with it if anything.
 *
 * The problem cases matter more than the numbers: a title block wider than the drawable
 * area produces a technically valid frame with nowhere for the building, and the
 * renderer will happily scale a plan down to nothing to fit what is left. Saying so
 * here means the architect sees it before they plot.
 */
export function drawableArea(layout: LayoutLike): DrawableArea {
  const size = PAPER_SIZES.find((entry) => entry.name === layout.paper);
  if (size === undefined) {
    return { widthMm: 0, heightMm: 0, problem: `${layout.paper} is not a paper size we draw.` };
  }
  const portrait = layout.orientation === 'portrait';
  const paperWidth = portrait ? size.heightMm : size.widthMm;
  const paperHeight = portrait ? size.widthMm : size.heightMm;

  const widthMm = paperWidth - layout.marginLeftMm - layout.marginRightMm;
  const heightMm = paperHeight - layout.marginTopMm - layout.marginBottomMm;

  if (widthMm <= 0 || heightMm <= 0) {
    return {
      widthMm,
      heightMm,
      problem: `Those margins leave no room to draw on ${layout.paper}.`,
    };
  }
  if (layout.titleBlockWidthMm > widthMm) {
    return {
      widthMm,
      heightMm,
      problem: `A ${layout.titleBlockWidthMm} mm title block does not fit in ${widthMm} mm.`,
    };
  }
  if (layout.titleBlockHeightMm > heightMm) {
    return {
      widthMm,
      heightMm,
      problem: `A ${layout.titleBlockHeightMm} mm title block does not fit in ${heightMm} mm.`,
    };
  }
  return { widthMm, heightMm, problem: null };
}
