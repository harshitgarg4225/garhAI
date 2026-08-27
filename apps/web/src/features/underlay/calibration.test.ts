/**
 * The two-point calibration arithmetic.
 *
 * This is the one piece of the underlay feature that can be silently, plausibly
 * wrong: a scale that is 8% out produces a plan that looks completely normal
 * and measures completely wrong, and it would reach a municipal drawing set
 * before anybody noticed. So the cases below are not "does it run" — they pin
 * the ACTUAL NUMBERS, including a `12'6"` input parsed by the shared unit
 * parser, and there is a deliberate negative test proving the two formulas that
 * look right and are not would fail.
 */

import { describe, expect, it } from 'vitest';

import { parseLengthMm } from '../../lib/units';
import {
  MAX_UNDERLAY_MM_PER_PX,
  markDistanceMm,
  recalibrate,
  underlayExtentMm,
  type UnderlayCalibration,
} from './calibration';

/** A scan imported at the 1 mm/px default, origin at the model origin. */
const AT_DEFAULT: UnderlayCalibration = { mmPerPx: 1, originXMm: 0, originYMm: 0 };

describe('recalibrate', () => {
  it('turns "these 1000 mm-as-drawn are really 3810 mm" into a 3.81 mm/px scale', () => {
    // 1000 mm apart under a 1 mm/px scale is 1000 px on the scan. Told those
    // 1000 px are 3810 mm, each pixel must be 3.81 mm.
    const result = recalibrate({
      a: { x: 0, y: 0 },
      b: { x: 1000, y: 0 },
      current: AT_DEFAULT,
      knownMm: 3810,
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.spanPx).toBeCloseTo(1000, 9);
    expect(result.measuredMm).toBeCloseTo(1000, 9);
    expect(result.next.mmPerPx).toBeCloseTo(3.81, 9);
    expect(result.factor).toBeCloseTo(3.81, 9);
  });

  it('accepts 12\'6" through the shared parser and lands on the same number', () => {
    // The panel hands `recalibrate` integer millimetres from `LengthInput`,
    // which is `parseLengthMm` — the golden-tested boundary. Going through it
    // here is the point: if the parser and this module ever disagree about
    // what 12'6" is, the drawing is wrong and this test says so.
    const knownMm = parseLengthMm(`12'6"`, 'ft-in');
    expect(knownMm).toBe(3810);

    const result = recalibrate({
      a: { x: 0, y: 0 },
      b: { x: 1000, y: 0 },
      current: AT_DEFAULT,
      knownMm,
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.next.mmPerPx).toBeCloseTo(3.81, 9);
  });

  it('works from a scale that is already non-trivial, and on a diagonal', () => {
    // 3-4-5: the marks are 5000 mm apart under a 4 mm/px scale, so 1250 px.
    // Told they are really 6000 mm: 6000 / 1250 = 4.8 mm/px.
    const current: UnderlayCalibration = { mmPerPx: 4, originXMm: 0, originYMm: 0 };
    const result = recalibrate({
      a: { x: 1000, y: 2000 },
      b: { x: 4000, y: 6000 },
      current,
      knownMm: 6000,
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.measuredMm).toBeCloseTo(5000, 9);
    expect(result.spanPx).toBeCloseTo(1250, 9);
    expect(result.next.mmPerPx).toBeCloseTo(4.8, 9);
  });

  it('is the identity when the typed distance matches what is already drawn', () => {
    const current: UnderlayCalibration = { mmPerPx: 8.5, originXMm: -12000, originYMm: 18000 };
    const result = recalibrate({
      a: { x: 0, y: 0 },
      b: { x: 3000, y: 0 },
      current,
      knownMm: 3000,
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.factor).toBeCloseTo(1, 12);
    expect(result.next.mmPerPx).toBeCloseTo(8.5, 12);
    // Nothing moves either — a no-op calibration must not shove the scan.
    expect(result.next.originXMm).toBe(-12000);
    expect(result.next.originYMm).toBe(18000);
  });

  // ── the origin, which is the half that is easy to forget ────────────────

  it('scales about the FIRST mark, so the point the user clicked stays put', () => {
    // Origin 1000 mm west and 1000 mm north of mark A; doubling the scale must
    // push it to 2000 mm away in each direction, leaving A itself untouched.
    const current: UnderlayCalibration = { mmPerPx: 1, originXMm: 0, originYMm: 2000 };
    const result = recalibrate({
      a: { x: 1000, y: 1000 },
      b: { x: 2000, y: 1000 },
      current,
      knownMm: 2000,
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.factor).toBeCloseTo(2, 12);
    expect(result.next.originXMm).toBe(-1000);
    expect(result.next.originYMm).toBe(3000);

    // And the invariant that matters, stated directly: the model point of the
    // image pixel under mark A is mark A, before and after.
    const pixelOfA = {
      x: (1000 - current.originXMm) / current.mmPerPx,
      y: (current.originYMm - 1000) / current.mmPerPx,
    };
    const afterX = result.next.originXMm + pixelOfA.x * result.next.mmPerPx;
    const afterY = result.next.originYMm - pixelOfA.y * result.next.mmPerPx;
    expect(afterX).toBeCloseTo(1000, 9);
    expect(afterY).toBeCloseTo(1000, 9);
  });

  it('answers an INTEGER-mm origin — the wire type is StrictInt', () => {
    const result = recalibrate({
      a: { x: 137, y: -41 },
      b: { x: 1103, y: 892 },
      current: { mmPerPx: 2.7, originXMm: -333, originYMm: 777 },
      knownMm: 4321,
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(Number.isInteger(result.next.originXMm)).toBe(true);
    expect(Number.isInteger(result.next.originYMm)).toBe(true);
  });

  // ── refusals: it must fail loudly rather than clamp ─────────────────────

  it('refuses two marks on top of each other instead of dividing by ~zero', () => {
    const result = recalibrate({
      a: { x: 500, y: 500 },
      b: { x: 500.2, y: 500.1 },
      current: AT_DEFAULT,
      knownMm: 3000,
    });

    expect(result).toEqual({ ok: false, reason: 'marks-too-close' });
  });

  it('refuses a zero or negative distance', () => {
    for (const knownMm of [0, -100, Number.NaN]) {
      const result = recalibrate({
        a: { x: 0, y: 0 },
        b: { x: 1000, y: 0 },
        current: AT_DEFAULT,
        knownMm,
      });
      expect(result).toEqual({ ok: false, reason: 'distance-not-positive' });
    }
  });

  it('refuses an implausible scale rather than clamping it into range', () => {
    // 1000 px told to be 100 km: 100 mm/px per pixel × 1000 — far past the band.
    const result = recalibrate({
      a: { x: 0, y: 0 },
      b: { x: 1000, y: 0 },
      current: AT_DEFAULT,
      knownMm: 100_000_000,
    });

    expect(result).toEqual({ ok: false, reason: 'scale-out-of-range' });
    // Stated as a property too, so the constant and the guard cannot drift.
    const wouldHaveBeen = 100_000_000 / 1000;
    expect(wouldHaveBeen).toBeGreaterThan(MAX_UNDERLAY_MM_PER_PX);
  });

  // ── NEGATIVE TEST: the formulas that look right and are not ─────────────

  it('does NOT use the inverted ratio, nor forget the current scale', () => {
    // A case chosen so all three candidate formulas give different answers.
    const current: UnderlayCalibration = { mmPerPx: 4, originXMm: 0, originYMm: 0 };
    const measuredMm = 5000;
    const knownMm = 6000;

    const result = recalibrate({
      a: { x: 0, y: 0 },
      b: { x: measuredMm, y: 0 },
      current,
      knownMm,
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;

    const correct = knownMm / (measuredMm / current.mmPerPx); // 4.8
    // (a) ratio the wrong way up — the classic, and it is plausible: it even
    //     moves in the right direction when the correction is small.
    const inverted = current.mmPerPx * (measuredMm / knownMm); // 3.333…
    // (b) forgetting that the marks were measured under the CURRENT scale,
    //     i.e. treating model mm as if they were image pixels.
    const scaleForgotten = knownMm / measuredMm; // 1.2

    expect(result.next.mmPerPx).toBeCloseTo(correct, 12);
    expect(result.next.mmPerPx).not.toBeCloseTo(inverted, 6);
    expect(result.next.mmPerPx).not.toBeCloseTo(scaleForgotten, 6);
    // And the three really are distinguishable on this input, or the two
    // assertions above would pass for a broken implementation.
    expect(Math.abs(correct - inverted)).toBeGreaterThan(1);
    expect(Math.abs(correct - scaleForgotten)).toBeGreaterThan(1);
  });
});

describe('markDistanceMm', () => {
  it('is the plain euclidean distance, in the model plane', () => {
    expect(markDistanceMm({ x: 0, y: 0 }, { x: 3000, y: 4000 })).toBeCloseTo(5000, 9);
    expect(markDistanceMm({ x: -1000, y: 500 }, { x: -1000, y: 500 })).toBe(0);
  });
});

describe('underlayExtentMm', () => {
  it('is the sanity check the panel shows: pixels × scale', () => {
    // A 300-dpi A1 scan at roughly 1:100 covers tens of metres, not hundreds.
    const extent = underlayExtentMm(2480, 3508, 8.4677);
    expect(extent.widthMm).toBeCloseTo(20_999.9, 1);
    expect(extent.heightMm).toBeCloseTo(29_704.7, 1);
  });
});
