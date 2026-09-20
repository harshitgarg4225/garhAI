/**
 * planHatch — the hatch a wall will PRINT with, drawn on the plan.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY
 * ════════════════════════════════════════════════════════════════════════════
 * `HatchBindingPanel` binds a surface (or a material) to a pattern for the
 * drawings service, and the plan drew every wall as one flat ink fill — the
 * architect could not see what the sheet would hatch until the sheet came
 * back. This module derives, per wall on the storey, the SAME line families
 * the drawings service generates (`hatchpicker/geometry.ts` is the port of
 * `hatch_patterns.py`, pinned by its drift test), at the SAME density the
 * sheet uses (2.5 paper mm, which is 250 model mm at 1:100 —
 * `services/drawings/projection/style.py`), at the pattern's own definition
 * angle, anchored at the model origin exactly as the sheet anchors them —
 * then clips them to the wall's solid runs (between the openings) and bakes
 * the dash cycles into short solid segments so the whole storey's hatching is
 * ONE `LineSegments`. Same primitives, one more consumer.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHICH WALLS
 * ════════════════════════════════════════════════════════════════════════════
 * A wall is hatched when `resolveHatch` answers from a MATERIAL assignment or
 * a hand-picked OVERRIDE — i.e. when the architect bound something. A wall on
 * its surface default keeps the flat poché: the sheet does hatch it (ANSI31),
 * but drawing forty diagonal walls on every new project would bury the plan
 * under the one pattern that carries no information, and the change should
 * appear exactly where a binding was made. `solid` is a fill, not a pattern,
 * and stays flat too.
 *
 * Pure. `planHatch.test.ts` asserts a bound wall yields lines inside its own
 * quad, an unbound wall yields none, and removing the binding removes them.
 */

import type { HouseModel, MaterialAssignment, Opening } from '@garh/model';

import { surfaceGroupOf } from '../../../features/canvas/materials';
import {
  baseAngleDeg,
  hatchFamilies,
  hatchPattern,
  isSolidPattern,
  resolveHatch,
  type HatchFamily,
  type HatchOverrides,
  type HatchPatternKey,
} from '../../../features/hatchpicker';
import type { MaterialItem } from '../../../lib/schemas';
import { packSegments } from './planBuffers';
import { wallRuns, wallSpanQuadF, wallsOfStorey, type PtF, type QuadF } from './planGeometry';

/**
 * The scale the canvas hatches at. A plan is read at 1:100 on screen far more
 * often than at 1:50, and ONE density everywhere is what keeps the canvas
 * honest about the sheet rather than pretty at every zoom — the lines do not
 * re-space as you zoom, exactly as the printed poché does not.
 */
export const CANVAS_HATCH_SCALE_DENOMINATOR = 100;

/**
 * Distance between adjacent hatch lines, MODEL millimetres.
 *
 * `HATCH_SPACING_MM = 2.5` PAPER mm in
 * `services/drawings/projection/style.py`, through that module's own
 * `paper_to_model_mm` at the scale above — the same arithmetic the sheet
 * does, so the canvas draws the density the drawing will print. The repo has
 * already shipped a hatch 31× too dense from a spacing passed in the wrong
 * unit; `planHatch.test.ts` reads that Python constant and refuses to let the
 * two drift.
 */
export const CANVAS_HATCH_SPACING_MM = 2.5 * CANVAS_HATCH_SCALE_DENOMINATOR;

export interface WallHatchInput {
  readonly house: HouseModel;
  readonly storeyId: string;
  readonly catalog: ReadonlyMap<string, MaterialItem>;
  readonly overrides: HatchOverrides;
}

export interface WallHatch {
  /** Walls drawn with a pattern (and therefore a paper fill, not ink). */
  readonly wallIds: ReadonlySet<string>;
  /** The pattern each hatched wall wears. */
  readonly patternByWall: ReadonlyMap<string, HatchPatternKey>;
  /** Every drawn line, float mm, clipped to the walls and dash-cut. */
  readonly segments: readonly (readonly [PtF, PtF])[];
  /** The `LineSegments` buffer, at a storey's elevation. */
  linePositions(elevationMm: number): Float32Array;
}

export const EMPTY_HATCH: WallHatch = {
  wallIds: new Set(),
  patternByWall: new Map(),
  segments: [],
  linePositions: () => new Float32Array(0),
};

/** The pattern a wall prints with, or null when it keeps the flat poché. */
export function wallHatchPattern(
  materials: readonly MaterialAssignment[],
  catalog: ReadonlyMap<string, MaterialItem>,
  overrides: HatchOverrides,
  wall: {
    readonly id: string;
    readonly storeyId: string;
    readonly kind: 'external' | 'internal' | 'parapet';
  },
): HatchPatternKey | null {
  const resolved = resolveHatch({
    materials,
    catalog,
    overrides,
    group: surfaceGroupOf({ kind: 'wall', wallKind: wall.kind }),
    ctx: { storeyId: wall.storeyId, elementId: wall.id },
  });
  if (resolved.source === 'surface-default') return null;
  if (isSolidPattern(resolved.pattern)) return null;
  return resolved.pattern;
}

export function buildWallHatch(input: WallHatchInput): WallHatch {
  const walls = wallsOfStorey(input.house, input.storeyId);
  if (walls.length === 0) return EMPTY_HATCH;

  const byWall = new Map<string, Opening[]>();
  for (const opening of input.house.openings) {
    const list = byWall.get(opening.wallId);
    if (list) list.push(opening);
    else byWall.set(opening.wallId, [opening]);
  }

  const wallIds = new Set<string>();
  const patternByWall = new Map<string, HatchPatternKey>();
  const segments: (readonly [PtF, PtF])[] = [];

  for (const wall of walls) {
    const pattern = wallHatchPattern(input.house.materials, input.catalog, input.overrides, wall);
    if (pattern === null) continue;
    wallIds.add(wall.id);
    patternByWall.set(wall.id, pattern);

    const quads: QuadF[] = [];
    for (const run of wallRuns(wall, byWall.get(wall.id) ?? [])) {
      const quad = wallSpanQuadF(wall, run.startMm, run.endMm);
      if (quad !== null) quads.push(quad);
    }
    if (quads.length === 0) continue;

    // One family set per wall over its bounding box; anchored at the origin
    // by `hatchFamilies`, so two walls that meet share a pattern with no step.
    const xs = quads.flatMap((q) => q.map((p) => p.x));
    const ys = quads.flatMap((q) => q.map((p) => p.y));
    const families = hatchFamilies(pattern, {
      spacing: CANVAS_HATCH_SPACING_MM,
      angleDeg: baseAngleDeg(hatchPattern(pattern)),
      bbox: [
        Math.floor(Math.min(...xs)) - 1,
        Math.floor(Math.min(...ys)) - 1,
        Math.ceil(Math.max(...xs)) + 1,
        Math.ceil(Math.max(...ys)) + 1,
      ],
    });

    for (const family of families) {
      for (const [from, to] of family.segments) {
        for (const quad of quads) {
          const clipped = clipToConvexQuad(
            { x: from[0], y: from[1] },
            { x: to[0], y: to[1] },
            quad,
          );
          if (clipped === null) continue;
          pushDashed(segments, family, { x: from[0], y: from[1] }, clipped.a, clipped.b);
        }
      }
    }
  }

  return {
    wallIds,
    patternByWall,
    segments,
    linePositions: (elevationMm) => packSegments(segments, elevationMm),
  };
}

// ---------------------------------------------------------------------------
// Clipping — Cyrus–Beck against one convex quad
// ---------------------------------------------------------------------------

/**
 * The part of `a→b` inside `quad`, or null. Works for either winding: the
 * quad's orientation is read once and every edge normal is turned inward.
 */
export function clipToConvexQuad(
  a: PtF,
  b: PtF,
  quad: readonly PtF[],
): { readonly a: PtF; readonly b: PtF } | null {
  const n = quad.length;
  if (n < 3) return null;
  let area2 = 0;
  for (let i = 0; i < n; i += 1) {
    const p = quad[i] as PtF;
    const q = quad[(i + 1) % n] as PtF;
    area2 += p.x * q.y - q.x * p.y;
  }
  if (area2 === 0) return null;
  const ccw = area2 > 0;

  let t0 = 0;
  let t1 = 1;
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  for (let i = 0; i < n; i += 1) {
    const p = quad[i] as PtF;
    const q = quad[(i + 1) % n] as PtF;
    const ex = q.x - p.x;
    const ey = q.y - p.y;
    // Inward normal of the edge.
    const nx = ccw ? -ey : ey;
    const ny = ccw ? ex : -ex;
    const numer = nx * (a.x - p.x) + ny * (a.y - p.y);
    const denom = nx * dx + ny * dy;
    if (denom === 0) {
      if (numer < 0) return null;
      continue;
    }
    const t = -numer / denom;
    if (denom < 0) {
      if (t < t0) return null;
      if (t < t1) t1 = t;
    } else {
      if (t > t1) return null;
      if (t > t0) t0 = t;
    }
  }
  if (t1 <= t0) return null;
  return {
    a: { x: a.x + dx * t0, y: a.y + dy * t0 },
    b: { x: a.x + dx * t1, y: a.y + dy * t1 },
  };
}

// ---------------------------------------------------------------------------
// Dashes — baked into solid pieces
// ---------------------------------------------------------------------------

/**
 * Emit the "on" pieces of `a→b` under the family's dash cycle.
 *
 * `family.dashOffset` is the phase at the family segment's own start
 * (`familyStart`); the clipped piece starts further along, so the phase is
 * advanced by that distance — which is what keeps a dash continuous across a
 * door opening, and identical to what the SVG writer's `stroke-dashoffset`
 * produces on the sheet.
 */
export function pushDashed(
  out: (readonly [PtF, PtF])[],
  family: Pick<HatchFamily, 'dashes' | 'dashOffset'>,
  familyStart: PtF,
  a: PtF,
  b: PtF,
): void {
  const length = Math.hypot(b.x - a.x, b.y - a.y);
  if (length <= 0) return;
  if (family.dashes.length === 0) {
    out.push([a, b] as const);
    return;
  }
  const period = family.dashes.reduce((sum, d) => sum + d, 0);
  if (period <= 0) {
    out.push([a, b] as const);
    return;
  }
  const ux = (b.x - a.x) / length;
  const uy = (b.y - a.y) / length;
  const advanced = family.dashOffset + Math.hypot(a.x - familyStart.x, a.y - familyStart.y);
  let phase = ((advanced % period) + period) % period;

  // Find where in the cycle the piece starts.
  let index = 0;
  while (index < family.dashes.length && phase >= (family.dashes[index] ?? 0)) {
    phase -= family.dashes[index] ?? 0;
    index += 1;
  }
  let t = 0;
  let remaining = (family.dashes[index] ?? 0) - phase;
  while (t < length) {
    const end = Math.min(length, t + remaining);
    if (index % 2 === 0 && end > t) {
      out.push([
        { x: a.x + ux * t, y: a.y + uy * t },
        { x: a.x + ux * end, y: a.y + uy * end },
      ] as const);
    }
    t = end;
    index = (index + 1) % family.dashes.length;
    remaining = family.dashes[index] ?? 0;
    if (remaining <= 0) remaining = 1;
  }
}
