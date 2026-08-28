/**
 * format.ts — measurements, in the units the project is drawn in.
 *
 * "mm in, pretty out" (golden rule 6): every conversion here delegates to
 * `lib/units.ts`, which delegates to `@garh/model`, which is golden-tested
 * against the Python twin that dimensions the drawing set. There is no
 * hand-rolled millimetres-per-foot divisor and no `toFixed` anywhere in this
 * directory — `formatFixed` rounds half away from zero, `toFixed` does not, and
 * a measure tool whose numbers round differently from the sheet is a measure
 * tool nobody can cite. `geometry.test.ts` greps the source for both.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHY AREA IS ALWAYS PRINTED TWICE
 * ────────────────────────────────────────────────────────────────────────────
 * Indian residential practice quotes rooms and plots in square feet and files
 * the municipal drawing in square metres — the same architect uses both within
 * a sentence. So an area readout gives both, in one string, derived from ONE
 * integer mm² through the model's own converters. Printing one and letting the
 * reader convert is how the two numbers drift apart.
 *
 * Lengths follow the project's `unitsDisplay` (ft-in by default, §15) with the
 * millimetres alongside, for the same reason the ephemeral measure tool does
 * it: the drawing dimensions in mm and the conversation happens in feet.
 */

import type { Pt, UnitsDisplay } from '@garh/model';

import {
  formatFixed,
  formatIndianNumber,
  formatLength,
  formatSqft,
  formatSqm,
} from '../../lib/units';
// Type-only, and deliberately the tool layer's own readout shape: the tool HUD
// already renders `Readout[]`, so a measurement's numbers can be shown by the
// same component without a second, subtly different type to keep in step.
import type { Readout } from '../canvas/tools/types';
import {
  measurementAngleDeg,
  ringAreaMm2,
  ringPerimeterMm,
  segmentLengthsMm,
  totalLengthMm,
} from './geometry';
import type { MeasureKind, Measurement } from './types';

/** What a readout shows when the value is genuinely undefined. Never "0". */
export const NO_VALUE = '—';

/** `12'-0"` / `3.66 m` — the project's units, nothing else. */
export function formatMeasureLength(mm: number, display: UnitsDisplay): string {
  return formatLength(mm, display);
}

/** `12'-0" · 3,660 mm` — display units first, the drawing's mm alongside. */
export function formatLengthDetail(mm: number, display: UnitsDisplay): string {
  return `${formatLength(mm, display)} · ${formatIndianNumber(mm)} mm`;
}

/**
 * `11.15 m² · 120.0 sq ft` — both, from one mm², always in this order (metric
 * first because that is what the submission set carries).
 */
export function formatAreaBoth(mm2: number): string {
  return `${formatSqm(mm2)} · ${formatSqft(mm2)}`;
}

/** `90.0°`, or `—` for an undefined angle (a zero-length arm). */
export function formatAngle(deg: number | null): string {
  return deg === null ? NO_VALUE : `${formatFixed(deg, 1)}°`;
}

/** `+1,200 , −450 mm` — the run and rise of a two-point measurement. */
export function formatDelta(dxMm: number, dyMm: number): string {
  return `${formatIndianNumber(dxMm)} , ${formatIndianNumber(dyMm)} mm`;
}

/**
 * The one-line label drawn ON the canvas beside a measurement: the headline
 * number only. Everything else lives in the panel — a plan covered in
 * four-line readouts is a plan you cannot read.
 */
export function measurementLabel(
  kind: MeasureKind,
  points: readonly Pt[],
  display: UnitsDisplay,
): string {
  switch (kind) {
    case 'distance':
      return formatMeasureLength(totalLengthMm(points), display);
    case 'angle':
      return formatAngle(measurementAngleDeg(points));
    case 'area':
      return formatAreaBoth(ringAreaMm2(points));
  }
}

/**
 * Every number a measurement (or an in-progress draft) has to offer, in the
 * order the panel shows them. Exactly one carries `emphasis`.
 *
 * Shared by the live draft and the committed list on purpose: the number that
 * appears while dragging is the number that persists, computed by one function.
 * Two code paths here would be the classic way for the rubber band to promise
 * 3,600 and the saved measurement to say 3,599.
 */
export function measureReadouts(
  kind: MeasureKind,
  points: readonly Pt[],
  display: UnitsDisplay,
): Readout[] {
  switch (kind) {
    case 'distance':
      return distanceReadouts(points, display);
    case 'angle':
      return angleReadouts(points, display);
    case 'area':
      return areaReadouts(points, display);
  }
}

/** The same, for a committed measurement. */
export function measurementReadouts(m: Measurement, display: UnitsDisplay): Readout[] {
  return measureReadouts(m.kind, m.points, display);
}

// ---------------------------------------------------------------------------
// Per-kind
// ---------------------------------------------------------------------------

function distanceReadouts(points: readonly Pt[], display: UnitsDisplay): Readout[] {
  const legs = segmentLengthsMm(points);
  if (legs.length === 0) return [];
  const total = totalLengthMm(points);
  const out: Readout[] = [
    {
      id: 'length',
      label: legs.length > 1 ? `Total (${String(legs.length)} legs)` : 'Length',
      value: formatLengthDetail(total, display),
      emphasis: true,
    },
  ];

  if (legs.length > 1) {
    out.push({
      id: 'legs',
      label: 'Legs',
      value: legs.map((mm) => formatMeasureLength(mm, display)).join(' + '),
    });
  }

  // Δx/Δy is only meaningful for a single leg; on a chain it would describe the
  // last leg while sitting under a total, which reads as if it described both.
  if (points.length === 2) {
    const a = points[0];
    const b = points[1];
    if (a !== undefined && b !== undefined) {
      out.push({ id: 'delta', label: 'Δx, Δy', value: formatDelta(b.x - a.x, b.y - a.y) });
    }
  }
  return out;
}

function angleReadouts(points: readonly Pt[], display: UnitsDisplay): Readout[] {
  const a = points[0];
  const v = points[1];
  const b = points[2];
  if (a === undefined || v === undefined) return [];
  const out: Readout[] = [
    {
      id: 'angle',
      label: 'Angle',
      value: formatAngle(measurementAngleDeg(points)),
      emphasis: true,
    },
  ];
  const legs = segmentLengthsMm(points);
  const first = legs[0];
  const second = legs[1];
  if (first !== undefined) {
    out.push({
      id: 'arms',
      label: b === undefined ? 'Arm' : 'Arms',
      value:
        second === undefined
          ? formatMeasureLength(first, display)
          : `${formatMeasureLength(first, display)} · ${formatMeasureLength(second, display)}`,
    });
  }
  return out;
}

function areaReadouts(points: readonly Pt[], display: UnitsDisplay): Readout[] {
  if (points.length < 3) {
    // Below three points there is no area — say so rather than printing 0.00 m²,
    // which is a number and would be read as one.
    const legs = segmentLengthsMm(points);
    if (legs.length === 0) return [];
    return [
      { id: 'area', label: 'Area', value: NO_VALUE, emphasis: true },
      {
        id: 'perimeter',
        label: 'Run so far',
        value: formatLengthDetail(totalLengthMm(points), display),
      },
    ];
  }
  return [
    { id: 'area', label: 'Area', value: formatAreaBoth(ringAreaMm2(points)), emphasis: true },
    {
      id: 'perimeter',
      label: 'Perimeter',
      value: formatLengthDetail(ringPerimeterMm(points), display),
    },
    { id: 'vertices', label: 'Vertices', value: String(points.length) },
  ];
}
