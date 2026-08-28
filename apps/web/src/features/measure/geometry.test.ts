/**
 * Spec for the measure arithmetic.
 *
 * The whole feature is worth exactly as much as these numbers are, so the
 * assertions are about EXACTNESS, not approximation: a 3-4-5 triangle's
 * hypotenuse is 5000 mm and not 4999.7, the total of a chain is the sum of the
 * legs printed beside it, and a midpoint rounds half AWAY from zero so that a
 * wall measured westwards agrees with the same wall measured eastwards.
 *
 * The last block is a source gate rather than a behaviour test, and it is here
 * because of the bug class this repository keeps paying for: `Math.round` and
 * `toFixed` both LOOK right and both round differently from the drawing set.
 * Grepping our own source is the only check that fires before the divergence
 * reaches a sheet.
 */

import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { distMm, roundHalfAwayFromZero, type Pt } from '@garh/model';

import {
  closesRing,
  draftPolyline,
  interiorAngleDeg,
  measurementAngleDeg,
  midpointMm,
  ringAreaMm2,
  ringCentroidMm,
  ringPerimeterMm,
  segmentLengthsMm,
  totalLengthMm,
} from './geometry';

const P = (x: number, y: number): Pt => ({ x, y });

describe('lengths', () => {
  it('gets a 3-4-5 triangle exactly right: 3000 + 4000 → 5000 mm', () => {
    // The number an architect checks the tool against on day one. `Math.hypot`
    // on these operands is exactly 5000 in IEEE754, and `distMm` rounds it —
    // but the point of the assertion is that nothing in the chain introduces a
    // 4999.7 through a metre round-trip or a float scale factor.
    expect(distMm(P(0, 0), P(3000, 4000))).toBe(5000);
    expect(segmentLengthsMm([P(0, 0), P(3000, 4000)])).toEqual([5000]);
  });

  it('is symmetric: the same triangle measured backwards is still 5000', () => {
    expect(distMm(P(0, 0), P(-3000, -4000))).toBe(5000);
    expect(distMm(P(3000, 4000), P(0, 0))).toBe(5000);
  });

  it('rounds a non-integer diagonal half away from zero', () => {
    // √2 × 1000 = 1414.2135…
    expect(distMm(P(0, 0), P(1000, 1000))).toBe(1414);
    // √2 × 500 = 707.106…  — the .5 cases live in `midpointMm` below, where a
    // rounded value can actually land on one.
    expect(distMm(P(0, 0), P(500, 500))).toBe(707);
  });

  it('totals a chain as the sum of the legs it prints', () => {
    const chain = [P(0, 0), P(3000, 4000), P(3000, 9000), P(0, 9000)];
    const legs = segmentLengthsMm(chain);
    expect(legs).toEqual([5000, 5000, 3000]);
    expect(totalLengthMm(chain)).toBe(13_000);
    expect(totalLengthMm(chain)).toBe(legs.reduce((a, b) => a + b, 0));
  });

  it('has no legs and no total below two points', () => {
    expect(segmentLengthsMm([])).toEqual([]);
    expect(segmentLengthsMm([P(1, 1)])).toEqual([]);
    expect(totalLengthMm([P(1, 1)])).toBe(0);
  });
});

describe('midpointMm — the rounding rule, not a formatting detail', () => {
  it('rounds an odd span to the higher magnitude on BOTH sides of zero', () => {
    // +500.5 and −500.5. Half away from zero sends both outward.
    expect(midpointMm(P(0, 0), P(1001, 1001))).toEqual(P(501, 501));
    expect(midpointMm(P(0, 0), P(-1001, -1001))).toEqual(P(-501, -501));
  });

  it('differs from Math.round on the negative half — which is the whole point', () => {
    // Math.round is half-UP: it answers −500 here. If this ever passes with
    // −500 the module has stopped using the model's rounding and every label on
    // a westward measurement is one millimetre off from its eastward twin.
    expect(Math.round(-500.5)).toBe(-500);
    expect(roundHalfAwayFromZero(-500.5)).toBe(-501);
    expect(midpointMm(P(0, 0), P(-1001, 0)).x).toBe(-501);
  });

  it('is exact for an even span', () => {
    expect(midpointMm(P(0, 0), P(4000, 2000))).toEqual(P(2000, 1000));
  });
});

describe('interiorAngleDeg', () => {
  it('is EXACTLY 90 for perpendicular arms', () => {
    // Not 89.99999999999999: `atan2(|cross|, 0)` is exactly π/2, where the
    // `acos(dot/|u||v|)` form would come back a float epsilon short.
    expect(interiorAngleDeg(P(1000, 0), P(0, 0), P(0, 1000))).toBe(90);
    expect(interiorAngleDeg(P(0, 3000), P(0, 0), P(4000, 0))).toBe(90);
  });

  it('is 180 for a straight line and 0 for doubled-back arms', () => {
    expect(interiorAngleDeg(P(-1000, 0), P(0, 0), P(1000, 0))).toBe(180);
    expect(interiorAngleDeg(P(1000, 0), P(0, 0), P(2000, 0))).toBe(0);
  });

  it('is 45 for a unit diagonal', () => {
    expect(interiorAngleDeg(P(1000, 0), P(0, 0), P(1000, 1000))).toBeCloseTo(45, 10);
  });

  it('never reports 0° for a degenerate arm — it reports nothing', () => {
    // A confident wrong answer is the failure mode this codebase keeps finding.
    expect(interiorAngleDeg(P(0, 0), P(0, 0), P(1000, 0))).toBeNull();
    expect(interiorAngleDeg(P(1000, 0), P(0, 0), P(0, 0))).toBeNull();
  });

  it('reads the middle point of a three-point measurement as the corner', () => {
    expect(measurementAngleDeg([P(3000, 0), P(0, 0), P(0, 4000)])).toBe(90);
    expect(measurementAngleDeg([P(0, 0), P(1000, 0)])).toBeNull();
    expect(measurementAngleDeg([P(0, 0), P(1000, 0), P(1, 1), P(2, 2)])).toBeNull();
  });
});

describe('area', () => {
  const RECT = [P(0, 0), P(3000, 0), P(3000, 4000), P(0, 4000)];

  it('computes an open ring the way the model does', () => {
    expect(ringAreaMm2(RECT)).toBe(12_000_000);
    expect(ringPerimeterMm(RECT)).toBe(14_000);
    expect(ringCentroidMm(RECT)).toEqual(P(1500, 2000));
  });

  it('is orientation-independent — clicking clockwise is not negative area', () => {
    expect(ringAreaMm2([...RECT].reverse())).toBe(12_000_000);
  });

  it('is zero below three points and for collinear corners', () => {
    expect(ringAreaMm2([])).toBe(0);
    expect(ringAreaMm2([P(0, 0), P(1000, 0)])).toBe(0);
    expect(ringAreaMm2([P(0, 0), P(1000, 0), P(2000, 0)])).toBe(0);
  });

  it('handles an L-shaped (concave) region, not just rectangles', () => {
    //  6×6 metres with a 3×3 bite out of the north-east corner = 27 m².
    const ell = [P(0, 0), P(6000, 0), P(6000, 3000), P(3000, 3000), P(3000, 6000), P(0, 6000)];
    expect(ringAreaMm2(ell)).toBe(27_000_000);
  });
});

describe('closing a ring', () => {
  const ring = [P(0, 0), P(3000, 0), P(3000, 3000)];

  it('closes on a click within the snap tolerance of the first corner', () => {
    expect(closesRing(ring, P(0, 0), 12)).toBe(true);
    expect(closesRing(ring, P(8, 8), 12)).toBe(true); // 11.3 mm away
  });

  it('does not close outside it, or below three corners', () => {
    expect(closesRing(ring, P(40, 0), 12)).toBe(false);
    expect(closesRing([P(0, 0), P(3000, 0)], P(0, 0), 12)).toBe(false);
  });
});

describe('draftPolyline', () => {
  it('appends the rubber-band cursor', () => {
    expect(draftPolyline([P(0, 0)], P(1000, 0))).toEqual([P(0, 0), P(1000, 0)]);
  });

  it('does not append a cursor sitting on the last clicked point', () => {
    // Otherwise the frame a click lands pushes a zero-length leg into the
    // readouts, and the chain briefly reports one more leg than it has.
    expect(draftPolyline([P(0, 0), P(1000, 0)], P(1000, 0))).toEqual([P(0, 0), P(1000, 0)]);
  });

  it('is just the points when there is no cursor', () => {
    expect(draftPolyline([P(0, 0)], null)).toEqual([P(0, 0)]);
  });
});

// ---------------------------------------------------------------------------
// Source gate
// ---------------------------------------------------------------------------

describe('rounding convention (source gate)', () => {
  // `dirname(fileURLToPath(import.meta.url))`, NOT `new URL('.', import.meta.url)`:
  // Vite statically rewrites the latter idiom and hands the spec a URL that is
  // not a file: URL, as the two other source-reading specs in this app note.
  const dir = dirname(fileURLToPath(import.meta.url));
  const sources = readdirSync(dir).filter(
    (f) => (f.endsWith('.ts') || f.endsWith('.tsx')) && !f.endsWith('.test.ts'),
  );

  it('scans a non-empty set of files (a gate over nothing is not a gate)', () => {
    expect(sources.length).toBeGreaterThan(5);
  });

  it.each([
    ['Math.round(', 'use roundMm/ptRound from @garh/model — half away from zero, not half up'],
    ['.toFixed(', 'use formatFixed from lib/units — toFixed does not round half away from zero'],
    ['304.8', 'use MM_PER_FOOT / formatFtIn — a hand-rolled foot conversion drifts from the sheet'],
    ['25.4', 'use MM_PER_INCH — same reason'],
  ])('no source file contains %s (%s)', (needle) => {
    const offenders = sources.filter((f) => readFileSync(join(dir, f), 'utf8').includes(needle));
    expect(offenders).toEqual([]);
  });
});
