/**
 * geometry.ts — the browser port of `hatch_families()` from
 * `services/drawings/render/hatch_patterns.py`.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY A PORT EXISTS, AND WHAT KEEPS IT HONEST
 * ════════════════════════════════════════════════════════════════════════════
 * A swatch has to draw the pattern's REAL line families. Faking one with a PNG
 * or a CSS gradient is how a picker comes to show a swatch that is not what
 * the sheet prints — the same "two things that should be one" failure the
 * pattern table itself was consolidated to end. The renderer's generator is
 * Python and runs in a worker; the swatch runs in a browser with no round
 * trip. So the ALGORITHM is ported here.
 *
 * The DATA is not: `patterns.ts` mirrors the definitions and
 * `patterns.drift.test.ts` pins that mirror to the Python file. What this
 * module adds is arithmetic, and `geometry.test.ts` checks that arithmetic
 * against expectations computed from the PYTHON defs (angles, spacings, dash
 * cycles) rather than from the TS table this file reads — so an error here
 * cannot be hidden by a matching error there.
 *
 * The port is line-for-line, including two things that look like bugs and are
 * not:
 *
 *  * ACAD writes gaps as negative dash lengths, and the cycle is emitted as
 *    positive on/off lengths in SVG order. A family whose ACAD list STARTS
 *    with a gap therefore begins with an "on" segment in SVG. That is what the
 *    Python does and what the sheets already show; matching it is the point.
 *  * A zero-length ACAD dash is a dot. SVG has no dot, so it becomes a very
 *    short dash (2% of the cycle) drawn with a round cap.
 *
 * Everything here is pure and in integer millimetres at the boundary, like
 * every other coordinate this repo lets near an output file.
 */

import { roundHalfAwayFromZero } from '../../lib/units';
import {
  hatchPattern,
  type HatchLine,
  type HatchPatternDef,
  type HatchPatternKey,
} from './patterns';

/**
 * Generation stops at this many lines per family. A pathological spacing over
 * a plot-sized region would otherwise emit unbounded geometry; the caller gets
 * what fits. Mirrors `MAX_HATCH_LINES` in the Python.
 */
export const MAX_HATCH_LINES = 4000;

/** A zero-length ACAD dash is a dot, drawn as this fraction of the cycle. */
export const DOT_FRACTION = 0.02;

/** `[[x0, y0], [x1, y1]]`, integer model mm. */
export type Segment = readonly [readonly [number, number], readonly [number, number]];

/** `[x0, y0, x1, y1]`, integer model mm. */
export type BBox = readonly [number, number, number, number];

/** Drawable lines sharing one dash cycle and one phase — one SVG `<path>`. */
export interface HatchFamily {
  readonly segments: readonly Segment[];
  /** Positive on/off lengths in model mm, `stroke-dasharray` order. Empty = solid. */
  readonly dashes: readonly number[];
  /** Where in the cycle each segment starts — `stroke-dashoffset`. */
  readonly dashOffset: number;
  /** True when the cycle is dots, so the caller rounds the line cap. */
  readonly dotted: boolean;
}

/**
 * Distance between adjacent lines of one family, in pattern units.
 *
 * `offset` steps from one line to the next in PATTERN space; only its
 * component normal to the line direction actually moves the line, so that
 * component — not the vector's length — is the spacing. ANSI31 offsets by
 * (-2.245, 2.245) at 45 deg, which is 3.175 mm of real separation. Getting
 * this wrong by using the vector length is how a hatch ends up 41% too coarse.
 */
export function perpSpacing(line: HatchLine): number {
  const theta = (line.angleDeg * Math.PI) / 180;
  return Math.abs(line.offset[0] * -Math.sin(theta) + line.offset[1] * Math.cos(theta));
}

/**
 * The pattern's characteristic spacing: the first family's, by CAD convention.
 * Scaling so this equals the authored spacing is what makes an authored
 * "150 mm hatch" measure 150 mm on the drawing.
 */
export function baseSpacing(definition: HatchPatternDef): number {
  const first = definition.lines[0];
  return first === undefined ? 0 : perpSpacing(first);
}

/**
 * The angle the pattern is DEFINED at, which the author never sets. Subtracted
 * from the authored angle before rotating, so `angleDeg: 45` on a pattern
 * already drawn at 45 deg yields 45 deg and not 90 — the third of the three
 * hatch defects this library was written to end.
 */
export function baseAngleDeg(definition: HatchPatternDef): number {
  return definition.lines[0]?.angleDeg ?? 0;
}

export interface HatchFamiliesOptions {
  /** Distance between adjacent lines of the FIRST family, model mm. */
  readonly spacing: number;
  /** The angle that first family should end up at, degrees. */
  readonly angleDeg: number;
  readonly bbox: BBox;
  readonly maxLines?: number;
}

/**
 * Generate a pattern's line geometry across `bbox`, in model millimetres.
 *
 * Lines are anchored at the model ORIGIN, not at `bbox`, so two hatches that
 * meet along a wall line up instead of stepping. The caller clips to the real
 * outline — for a swatch that is the SVG viewport, which clips by default.
 */
export function hatchFamilies(
  key: HatchPatternKey,
  { spacing, angleDeg, bbox, maxLines = MAX_HATCH_LINES }: HatchFamiliesOptions,
): HatchFamily[] {
  const definition = hatchPattern(key);
  const base = baseSpacing(definition);
  if (definition.lines.length === 0 || base <= 0 || spacing <= 0) return [];

  const scale = spacing / base;
  const rotation = ((angleDeg - baseAngleDeg(definition)) * Math.PI) / 180;
  const cosR = Math.cos(rotation);
  const sinR = Math.sin(rotation);
  const rotated = (x: number, y: number): [number, number] => [
    x * cosR - y * sinR,
    x * sinR + y * cosR,
  ];

  const [x0, y0, x1, y1] = bbox;
  const corners: readonly (readonly [number, number])[] = [
    [x0, y0],
    [x1, y0],
    [x1, y1],
    [x0, y1],
  ];

  const out: HatchFamily[] = [];
  for (const family of definition.lines) {
    const angle = (family.angleDeg * Math.PI) / 180 + rotation;
    const along: [number, number] = [Math.cos(angle), Math.sin(angle)];
    const normal: [number, number] = [-along[1], along[0]];
    const anchor = rotated(family.base[0] * scale, family.base[1] * scale);
    const stepVec = rotated(family.offset[0] * scale, family.offset[1] * scale);
    const step = stepVec[0] * normal[0] + stepVec[1] * normal[1];
    // Offset parallel to the family's own lines: every line would land on top
    // of the last. ACAD treats such a family as degenerate too.
    if (Math.abs(step) < 1e-9) continue;

    const drift = stepVec[0] * along[0] + stepVec[1] * along[1];
    const anchorV = anchor[0] * normal[0] + anchor[1] * normal[1];
    const anchorU = anchor[0] * along[0] + anchor[1] * along[1];
    const vs = corners.map((c) => c[0] * normal[0] + c[1] * normal[1]);
    const us = corners.map((c) => c[0] * along[0] + c[1] * along[1]);
    const lo = (Math.min(...vs) - anchorV) / step;
    const hi = (Math.max(...vs) - anchorV) / step;
    const first = Math.floor(Math.min(lo, hi));
    let last = Math.ceil(Math.max(lo, hi));
    if (last - first + 1 > maxLines) last = first + maxLines - 1;
    const uStart = Math.min(...us);
    const uEnd = Math.max(...us);
    const { dashes, period, dotted } = dashCycle(family.dashes, scale);

    const byPhase = new Map<number, Segment[]>();
    for (let index = first; index <= last; index += 1) {
      const v = anchorV + index * step;
      const segment: Segment = [
        [
          roundHalfAwayFromZero(normal[0] * v + along[0] * uStart),
          roundHalfAwayFromZero(normal[1] * v + along[1] * uStart),
        ],
        [
          roundHalfAwayFromZero(normal[0] * v + along[0] * uEnd),
          roundHalfAwayFromZero(normal[1] * v + along[1] * uEnd),
        ],
      ];
      let phase = 0;
      if (period > 0) {
        // Python's `%` is always non-negative for a positive modulus and JS's
        // is not; a negative dash offset would shift half the family's dashes
        // the wrong way. `((x % p) + p) % p` restores Python's answer.
        const raw = roundHalfAwayFromZero(uStart - (anchorU + index * drift));
        phase = ((raw % period) + period) % period;
      }
      const bucket = byPhase.get(phase);
      if (bucket === undefined) byPhase.set(phase, [segment]);
      else bucket.push(segment);
    }

    for (const phase of [...byPhase.keys()].sort((a, b) => a - b)) {
      out.push({
        segments: byPhase.get(phase) ?? [],
        dashes,
        dashOffset: phase,
        dotted,
      });
    }
  }
  return out;
}

interface DashCycle {
  readonly dashes: readonly number[];
  readonly period: number;
  readonly dotted: boolean;
}

/**
 * Scale an ACAD dash list into positive on/off lengths in model mm.
 *
 * ACAD writes gaps as negatives and dots as zeros; SVG wants positive lengths
 * alternating draw/skip.
 */
function dashCycle(dashes: readonly number[], scale: number): DashCycle {
  if (dashes.length === 0) return { dashes: [], period: 0, dotted: false };
  const span = dashes.reduce((sum, value) => sum + Math.abs(value), 0) * scale;
  if (span <= 0) return { dashes: [], period: 0, dotted: false };
  const dot = Math.max(1, span * DOT_FRACTION);
  const lengths = dashes.map((value) => {
    const scaled = Math.abs(value) * scale;
    return Math.max(1, roundHalfAwayFromZero(scaled === 0 ? dot : scaled));
  });
  // `dashes[::2]` — the DRAWN entries. All zero means the cycle is dots.
  const dotted = dashes.filter((_, i) => i % 2 === 0).every((value) => value === 0);
  return {
    dashes: lengths,
    period: lengths.reduce((sum, value) => sum + value, 0),
    dotted,
  };
}
