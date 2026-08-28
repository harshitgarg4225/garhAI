/**
 * ink.ts — how much line a family actually DRAWS inside a box.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY THIS EXISTS
 * ════════════════════════════════════════════════════════════════════════════
 * "The swatch renders non-empty geometry" is easy to assert and easy to get
 * wrong: `hatchFamilies` happily returns twenty segments that all lie outside
 * the box, or twenty dashed segments whose dash phase parks every gap over the
 * visible area. Both produce a blank swatch and a green test — the "gate that
 * cannot go red" this repo has shipped before.
 *
 * So the specs measure the thing a human would: the total drawn length inside
 * the swatch's own box, after clipping to it AND after applying the dash cycle
 * exactly as SVG will. `AR-SAND` is the pattern that proves this is not
 * paranoia — it is a stipple of dots whose ink is a hundredth of what a solid
 * hatch draws, and a naive "are there segments?" check says nothing about
 * whether any of it lands where a person can see it.
 *
 * The app does not import this; SVG does the dashing on screen. It is a
 * measuring instrument for the specs, kept beside the code it measures.
 */

import type { BBox, HatchFamily, Segment } from './geometry';

/** Clip a segment to `box`, as the arclength interval `[from, to]`, or null. */
function clip(segment: Segment, box: BBox): { from: number; to: number; length: number } | null {
  const [[ax, ay], [bx, by]] = segment;
  const dx = bx - ax;
  const dy = by - ay;
  const length = Math.hypot(dx, dy);
  if (length === 0) return null;

  // Liang–Barsky: walk the four half-planes, narrowing the parameter window.
  let t0 = 0;
  let t1 = 1;
  const limits: readonly (readonly [number, number])[] = [
    [-dx, ax - box[0]],
    [dx, box[2] - ax],
    [-dy, ay - box[1]],
    [dy, box[3] - ay],
  ];
  for (const [p, q] of limits) {
    if (p === 0) {
      if (q < 0) return null; // parallel to this edge and outside it
      continue;
    }
    const r = q / p;
    if (p < 0) {
      if (r > t1) return null;
      if (r > t0) t0 = r;
    } else {
      if (r < t0) return null;
      if (r < t1) t1 = r;
    }
  }
  return { from: t0 * length, to: t1 * length, length };
}

/**
 * Total drawn length inside `box`, in the same units as the geometry.
 *
 * Dash semantics are SVG's: the cycle starts at `-dashOffset` along the path,
 * even entries draw and odd entries skip.
 */
export function drawnLengthInside(families: readonly HatchFamily[], box: BBox): number {
  let total = 0;
  for (const family of families) {
    const period = family.dashes.reduce((sum, value) => sum + value, 0);
    for (const segment of family.segments) {
      const visible = clip(segment, box);
      if (visible === null) continue;
      if (period <= 0) {
        total += visible.to - visible.from;
        continue;
      }
      const firstCycle = Math.floor((visible.from + family.dashOffset) / period);
      for (let cycle = firstCycle; cycle * period - family.dashOffset < visible.to; cycle += 1) {
        let position = cycle * period - family.dashOffset;
        for (let i = 0; i < family.dashes.length; i += 1) {
          const dash = family.dashes[i] ?? 0;
          if (i % 2 === 0) {
            const lo = Math.max(visible.from, position);
            const hi = Math.min(visible.to, position + dash);
            if (hi > lo) total += hi - lo;
          }
          position += dash;
        }
      }
    }
  }
  return total;
}
