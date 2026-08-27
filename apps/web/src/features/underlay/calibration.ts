/**
 * calibration.ts — "these two marks are 12'6" apart", turned into a scale.
 *
 * Pure arithmetic, no React, no three, no store. It is a module of its own
 * because it is the one place in the underlay feature where getting the
 * algebra subtly wrong produces a plan that LOOKS plausible and is 8% out —
 * the failure mode a municipal drawing set cannot survive, and one that no
 * type checker and no rendering test would catch.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE MATHS, IN THE ORDER THE USER PERFORMS IT
 * ────────────────────────────────────────────────────────────────────────────
 * The architect clicks two points ON THE UNDERLAY that they know the real
 * distance between (a door width, a dimension already printed on the scan) and
 * types that distance.
 *
 * Their two clicks land in MODEL millimetres, because the canvas resolves a
 * pointer against the drawing plane — but those millimetres were produced by
 * the CURRENT (probably wrong, probably the 1.0 default) `mmPerPx`. So:
 *
 *     measuredMm  = |b − a|                    ← mm under the CURRENT scale
 *     spanPx      = measuredMm / currentMmPerPx ← back to image pixels, which
 *                                                 is the one quantity that is
 *                                                 actually a fact about the
 *                                                 image and not about our guess
 *     newMmPerPx  = knownMm / spanPx
 *
 * The middle step is the point of the whole exercise: pixels are invariant
 * under recalibration, millimetres are not. Collapsing it to
 * `knownMm * currentMmPerPx / measuredMm` is algebraically identical, and it
 * is deliberately NOT written that way here — `spanPx` is a number the panel
 * shows the user ("you marked 412 px"), and a reviewer can check the two short
 * steps against the sentence above without doing any algebra.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHY THE ORIGIN MOVES TOO
 * ────────────────────────────────────────────────────────────────────────────
 * Rescaling an image pinned at its top-left corner slides everything else
 * across the sheet: correct a 1:100 scan by 8% and the far corner of the plan
 * jumps a metre and a half, and the architect has to hunt for their drawing
 * again. So the scale change is applied ABOUT THE FIRST MARK — the point they
 * just told us they care about stays exactly where it is under the cursor.
 *
 * A pixel `p` of the image sits at model point
 *
 *     P = O + s·(p.x, −p.y)      (image y grows down, model Y grows north)
 *
 * so for the mark `a` we have `s·(pa.x, −pa.y) = a − O`, and holding `a` fixed
 * while `s → s'` gives
 *
 *     O' = a − (s'/s)·(a − O) = a + k·(O − a),   k = s'/s
 *
 * — the same expression in both components, which is why the sign flip on Y
 * never appears in the code below.
 */

import { roundMm } from '../../lib/units';

/** A float model point in millimetres — a raw (unsnapped) canvas click. */
export interface MarkMm {
  readonly x: number;
  readonly y: number;
}

/** The three numbers a calibration owns. Origin is integer mm, scale is not. */
export interface UnderlayCalibration {
  /** Model millimetres per image pixel. A raster display scale, so a float. */
  readonly mmPerPx: number;
  /** Model position of image pixel (0,0) — the scan's top-left corner. */
  readonly originXMm: number;
  readonly originYMm: number;
}

export interface CalibrationInput {
  /** The two marks, in model mm under {@link CalibrationInput.current}'s scale. */
  readonly a: MarkMm;
  readonly b: MarkMm;
  /** The calibration in force when the marks were made. */
  readonly current: UnderlayCalibration;
  /** The real-world distance the architect typed, in integer millimetres. */
  readonly knownMm: number;
}

export type CalibrationRefusal =
  /** The two marks are the same point (or within a hair of it). */
  | 'marks-too-close'
  /** The typed distance was zero, negative, or not a number. */
  | 'distance-not-positive'
  /** The implied scale is outside anything a scan could plausibly be. */
  | 'scale-out-of-range';

export type CalibrationResult =
  | {
      readonly ok: true;
      readonly next: UnderlayCalibration;
      /** How far apart the marks are under the CURRENT scale, mm. */
      readonly measuredMm: number;
      /** The same span in image pixels — the invariant, shown to the user. */
      readonly spanPx: number;
      /** `new / old`. 1 means the scan was already calibrated. */
      readonly factor: number;
    }
  | { readonly ok: false; readonly reason: CalibrationRefusal };

/**
 * Two marks closer together than this are treated as one click.
 *
 * A calibration is a division by the span, so a near-zero span is a near-
 * infinite scale: 0.5 mm at a typical zoom is well under one screen pixel, and
 * refusing there is how a fumbled double-click fails loudly instead of
 * silently blowing the drawing up by four orders of magnitude.
 */
export const MIN_MARK_SEPARATION_MM = 0.5;

/**
 * Plausibility band for `mmPerPx`, chosen from what a scan can actually be.
 *
 * A 300-dpi A1 scan of a 1:100 plan is about 8.5 mm/px; a phone photo of a
 * printed A4 at 1:200 is nearer 25; a 12 000 px drum scan of a site plan can
 * reach the low hundreds. 0.01 and 5 000 are three orders of magnitude clear
 * on either side — wide enough never to refuse honest work, tight enough that
 * a mistyped "3" for 3 metres (which asks for a 1000× correction) is caught.
 */
export const MIN_UNDERLAY_MM_PER_PX = 0.01;
export const MAX_UNDERLAY_MM_PER_PX = 5000;

/** Straight-line distance between two float model points, in mm. */
export function markDistanceMm(a: MarkMm, b: MarkMm): number {
  return Math.hypot(b.x - a.x, b.y - a.y);
}

/**
 * Recalibrate from two marks and a known distance.
 *
 * Refuses rather than clamps: a clamped scale is a wrong scale that looks
 * deliberate, and this number ends up under a drawing that gets submitted.
 */
export function recalibrate(input: CalibrationInput): CalibrationResult {
  const { a, b, current, knownMm } = input;

  if (!Number.isFinite(knownMm) || knownMm <= 0) {
    return { ok: false, reason: 'distance-not-positive' };
  }
  if (!Number.isFinite(current.mmPerPx) || current.mmPerPx <= 0) {
    return { ok: false, reason: 'scale-out-of-range' };
  }

  const measuredMm = markDistanceMm(a, b);
  if (!Number.isFinite(measuredMm) || measuredMm < MIN_MARK_SEPARATION_MM) {
    return { ok: false, reason: 'marks-too-close' };
  }

  // The two steps from the header, in that order and on purpose.
  const spanPx = measuredMm / current.mmPerPx;
  const mmPerPx = knownMm / spanPx;

  if (
    !Number.isFinite(mmPerPx) ||
    mmPerPx < MIN_UNDERLAY_MM_PER_PX ||
    mmPerPx > MAX_UNDERLAY_MM_PER_PX
  ) {
    return { ok: false, reason: 'scale-out-of-range' };
  }

  // Scale about the first mark so it stays under the cursor (see header).
  const factor = mmPerPx / current.mmPerPx;
  const originXMm = roundMm(a.x + (current.originXMm - a.x) * factor);
  const originYMm = roundMm(a.y + (current.originYMm - a.y) * factor);

  return {
    ok: true,
    next: { mmPerPx, originXMm, originYMm },
    measuredMm,
    spanPx,
    factor,
  };
}

/**
 * Human copy for a refusal. Golden rule 9: what happened, then what to do.
 */
export function calibrationRefusalText(reason: CalibrationRefusal): string {
  switch (reason) {
    case 'marks-too-close':
      return 'Those two marks are on top of each other. Click two points that are far apart on the scan — the longer the run, the more accurate the scale.';
    case 'distance-not-positive':
      return "That distance didn't read as a length. Try 12'6\", 3.8m or 3810.";
    case 'scale-out-of-range':
      return 'That gives a scale no scan could plausibly have. Check the distance you typed and the units it is in, then mark the two points again.';
  }
}

/**
 * The real-world size of the whole image under a calibration, in mm.
 *
 * The panel shows this next to the scale because it is the sanity check an
 * architect can actually make at a glance: "my A1 sheet is 24 m wide" is
 * obviously wrong in a way that "8.43 mm/px" is not.
 */
export function underlayExtentMm(
  widthPx: number,
  heightPx: number,
  mmPerPx: number,
): { readonly widthMm: number; readonly heightMm: number } {
  return { widthMm: widthPx * mmPerPx, heightMm: heightPx * mmPerPx };
}
