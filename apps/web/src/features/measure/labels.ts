/**
 * labels.ts — where each number sits on the drawing.
 *
 * Pure: points and a display-unit setting in, anchors and strings out. The
 * layer only positions what this returns, which is what lets a spec assert
 * "the total is drawn at the end of the chain, the area at the centroid"
 * without a renderer.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHAT IS DRAWN, AND WHAT IS DELIBERATELY NOT
 * ────────────────────────────────────────────────────────────────────────────
 * One HEADLINE per measurement — the number that was asked for — plus a leg
 * label per segment when a chain or a region has more than one. A single
 * two-point distance gets its headline at the midpoint of its own leg and no
 * leg label, because printing the same number twice on top of itself is how a
 * plan turns into a grey wash of digits.
 *
 * The headline for an area carries BOTH units (m² and ft²), which is a long
 * string; it goes at the centroid, where there is room, rather than on an edge.
 */

import { distMm, type Pt, type UnitsDisplay } from '@garh/model';

import { formatAngle, formatAreaBoth, formatMeasureLength } from './format';
import {
  draftPolyline,
  measurementAngleDeg,
  midpointMm,
  ringAreaMm2,
  ringCentroidMm,
  segmentLengthsMm,
  totalLengthMm,
} from './geometry';
import { measurementSegments } from './scene';
import type { MeasureDraft, MeasureKind, Measurement } from './types';

export interface MeasureLabel {
  /** Stable across frames — it is the React key AND the scene-graph identity. */
  readonly id: string;
  readonly text: string;
  readonly atMm: Pt;
  /** The headline number. Drawn heavier; exactly one per measurement. */
  readonly emphasis: boolean;
}

/** Labels for one committed measurement. */
export function measurementLabels(m: Measurement, display: UnitsDisplay): MeasureLabel[] {
  return labelsFor(m.id, m.kind, m.points, display);
}

/**
 * Labels for the in-progress draft, including the rubber-band leg — the number
 * has to move with the pointer or the tool is a form, not a measurement.
 */
export function draftLabels(draft: MeasureDraft, display: UnitsDisplay): MeasureLabel[] {
  return labelsFor('measure:draft', draft.kind, draftPolyline(draft.points, draft.cursor), display);
}

function labelsFor(
  key: string,
  kind: MeasureKind,
  points: readonly Pt[],
  display: UnitsDisplay,
): MeasureLabel[] {
  const out: MeasureLabel[] = [];
  const segments = measurementSegments(kind, points);
  const legs = segmentLengthsMm(points);

  // Leg labels: only when there is more than one number to tell apart.
  if (segments.length > 1) {
    segments.forEach((seg, i) => {
      // The area ring's closing edge is not in `segmentLengthsMm` (which walks
      // the open point list), so its length comes from the model's `distMm` —
      // the same rounding rule (half away from zero) as every other leg.
      const mm = legs[i] ?? distMm(seg.a, seg.b);
      out.push({
        id: `${key}:leg:${String(i)}`,
        text: formatMeasureLength(mm, display),
        atMm: midpointMm(seg.a, seg.b),
        emphasis: false,
      });
    });
  }

  const headline = headlineFor(kind, points, display);
  if (headline !== null) out.push({ id: `${key}:headline`, ...headline, emphasis: true });
  return out;
}

function headlineFor(
  kind: MeasureKind,
  points: readonly Pt[],
  display: UnitsDisplay,
): { text: string; atMm: Pt } | null {
  switch (kind) {
    case 'distance': {
      if (points.length < 2) return null;
      const total = totalLengthMm(points);
      const first = points[0];
      const last = points[points.length - 1];
      if (first === undefined || last === undefined) return null;
      // Two points: on the leg. A chain: at the far end, clear of the leg
      // labels that already cover the middle.
      const at = points.length === 2 ? midpointMm(first, last) : last;
      return { text: formatMeasureLength(total, display), atMm: at };
    }
    case 'angle': {
      const vertex = points[1];
      if (vertex === undefined) return null;
      const deg = measurementAngleDeg(points);
      if (deg === null) return null;
      return { text: formatAngle(deg), atMm: vertex };
    }
    case 'area': {
      if (points.length < 3) return null;
      const centroid = ringCentroidMm(points);
      if (centroid === null) return null;
      return { text: formatAreaBoth(ringAreaMm2(points)), atMm: centroid };
    }
  }
}
