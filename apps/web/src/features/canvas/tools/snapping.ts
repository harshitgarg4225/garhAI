/**
 * snapping.ts — where the pointer actually lands.
 *
 * The grid snap in `core/coords.ts` is the floor, not the ceiling: an architect
 * drawing a partition expects it to meet the wall it abuts EXACTLY, not to the
 * nearest 115 mm. So every tool that places a point runs the raw (unsnapped,
 * already-integer) pointer position through {@link resolveSnap}, which offers,
 * in priority order:
 *
 *   endpoint      a wall centreline end on this storey          rank 100
 *   midpoint      the middle of a wall centreline               rank  80
 *   plot-corner   a vertex of the plot boundary                 rank  75
 *   wall-line     the nearest point ON a wall centreline        rank  60
 *   plot-edge     the nearest point on a plot boundary edge     rank  55
 *   grid          the active module (115 mm / 25 mm / off)      rank   0
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE CONVERSION BOUNDARY
 * ────────────────────────────────────────────────────────────────────────────
 * In: integer mm (`rawPointMm`, produced by `core/coords.pointerToMmRaw`) plus
 * a float `mmPerPx` used only to size the tolerance. Out: integer mm, always.
 * The only float arithmetic in this file is the parametric projection onto a
 * segment, and its result goes through `ptRound` (half away from zero) before
 * it leaves. No tool receives a float from here.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * ORTHO
 * ────────────────────────────────────────────────────────────────────────────
 * MVP walls are orthogonal (§5), so drawing tools pass `ortho: true` with the
 * chain anchor. Ortho and object snap genuinely conflict — the endpoint you
 * want is rarely exactly north of where you started — and the CAD resolution is
 * to keep the constraint and PROJECT the snap onto it. The result is reported
 * as an `'extension'` snap ("Aligned with wall end"), which is honest: the
 * point is not on the feature, it is aligned with it.
 *
 * When ortho survives without a candidate, only the FREE axis is snapped to the
 * grid; the locked axis keeps the anchor's coordinate exactly. Snapping both
 * would drag a chain that started off-grid back onto it, one segment at a time.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * PERFORMANCE (§14)
 * ────────────────────────────────────────────────────────────────────────────
 * This runs at most once per animation frame (pointer moves are coalesced by
 * `useCanvasControls`), and costs O(walls on the active storey). A G+2 demo
 * storey is ~120 walls → ~360 candidate points, each a handful of integer
 * operations, with an early bounding-box reject. No allocation survives the
 * call except the returned candidate.
 */

import { distMm, polygonEdges, ptEq, ptRound, type Polygon, type Pt, type Wall } from '@garh/model';

import { snapMm } from '../../../lib/units';
// From the module rather than the `../core` barrel — the barrel drags in
// react-three-fiber, and this file has to run in a spec with no renderer.
import { constrainOrtho, snapPtMm } from '../core/coords';
import { SNAP_TOLERANCE_PX } from './constants';
import type { SnapView, ToolContext } from './types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type SnapKind =
  | 'grid'
  | 'endpoint'
  | 'midpoint'
  | 'wall-line'
  | 'plot-corner'
  | 'plot-edge'
  | 'extension';

export interface SnapCandidate {
  readonly kind: SnapKind;
  readonly pointMm: Pt;
  /** One short human phrase for the snap marker: "Wall end". */
  readonly label: string;
  /** Element the snap came from, when there is one. */
  readonly refId: string | null;
  readonly distanceMm: number;
  readonly rank: number;
}

export interface SnapResolution {
  /** Integer mm, ready to be an op payload. */
  readonly pointMm: Pt;
  /** What it snapped to. `null` means "the grid, or nothing at all". */
  readonly candidate: SnapCandidate | null;
}

export interface SnapOptions {
  /** Chain anchor for ortho and for the length readout. */
  readonly anchor?: Pt | null | undefined;
  /** Constrain to the anchor's axes. Ignored without an anchor. */
  readonly ortho?: boolean | undefined;
  /** Wall ids to ignore — the one being dragged snaps to others, not itself. */
  readonly excludeIds?: ReadonlySet<string> | undefined;
  /** Turn object snap off and use the grid alone. Default: on. */
  readonly objectSnap?: boolean | undefined;
  /** Override the tolerance in CSS pixels (the select tool wants it tighter). */
  readonly tolerancePx?: number | undefined;
}

const RANK: Readonly<Record<SnapKind, number>> = {
  endpoint: 100,
  midpoint: 80,
  'plot-corner': 75,
  'wall-line': 60,
  'plot-edge': 55,
  extension: 50,
  grid: 0,
};

const LABEL: Readonly<Record<SnapKind, string>> = {
  endpoint: 'Wall end',
  midpoint: 'Wall middle',
  'plot-corner': 'Plot corner',
  'wall-line': 'On wall',
  'plot-edge': 'Plot edge',
  extension: 'Aligned',
  grid: 'Grid',
};

// ---------------------------------------------------------------------------
// Geometry helpers (shared with the opening tool)
// ---------------------------------------------------------------------------

export interface SegmentProjection {
  /** The projected point, clamped to the segment, integer mm. */
  readonly pointMm: Pt;
  /** Distance from `a` to the projection along the segment, integer mm. */
  readonly alongMm: number;
  /** Perpendicular distance from the query point, integer mm. */
  readonly distanceMm: number;
  /** True when the unclamped projection fell inside the segment. */
  readonly inside: boolean;
}

/**
 * Project `p` onto segment `a`→`b`, clamped to its ends.
 *
 * Used by the wall-line snap and — load-bearing — by the opening tools, which
 * turn a pointer position over a wall into an `offsetMm` along it.
 */
export function projectOnSegment(p: Pt, a: Pt, b: Pt): SegmentProjection {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) {
    return { pointMm: a, alongMm: 0, distanceMm: distMm(p, a), inside: false };
  }
  const t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2;
  const clamped = t < 0 ? 0 : t > 1 ? 1 : t;
  const point = ptRound(a.x + dx * clamped, a.y + dy * clamped);
  return {
    pointMm: point,
    alongMm: distMm(a, point),
    distanceMm: distMm(p, point),
    inside: t >= 0 && t <= 1,
  };
}

/** Snap tolerance in mm — a constant number of screen pixels at any zoom. */
export function snapToleranceMm(mmPerPx: number, px: number = SNAP_TOLERANCE_PX): number {
  return Math.max(1, Math.round(px * mmPerPx));
}

/** The walls object snap considers: this storey's, minus the excluded ones. */
export function snapWalls(ctx: ToolContext, excludeIds?: ReadonlySet<string>): Wall[] {
  const storeyId = ctx.storeyId;
  if (storeyId === null) return [];
  return ctx.doc.house.walls.filter(
    (w) => w.storeyId === storeyId && !(excludeIds?.has(w.id) ?? false),
  );
}

// ---------------------------------------------------------------------------
// Candidates
// ---------------------------------------------------------------------------

function candidate(
  kind: SnapKind,
  pointMm: Pt,
  refId: string | null,
  distanceMm: number,
): SnapCandidate {
  return { kind, pointMm, label: LABEL[kind], refId, distanceMm, rank: RANK[kind] };
}

/**
 * Every object snap within `toleranceMm` of `raw`, unsorted.
 *
 * Exported for the specs and for the overlay's "all available snaps" affordance;
 * tools normally want {@link resolveSnap}, which also applies ortho and the
 * grid fallback.
 */
export function collectSnapCandidates(
  ctx: ToolContext,
  raw: Pt,
  options: SnapOptions = {},
): SnapCandidate[] {
  const toleranceMm = snapToleranceMm(ctx.mmPerPx, options.tolerancePx);
  const out: SnapCandidate[] = [];

  for (const wall of snapWalls(ctx, options.excludeIds)) {
    // Cheap reject: the wall's bounding box, grown by the tolerance.
    const minX = Math.min(wall.a.x, wall.b.x) - toleranceMm;
    const maxX = Math.max(wall.a.x, wall.b.x) + toleranceMm;
    const minY = Math.min(wall.a.y, wall.b.y) - toleranceMm;
    const maxY = Math.max(wall.a.y, wall.b.y) + toleranceMm;
    if (raw.x < minX || raw.x > maxX || raw.y < minY || raw.y > maxY) continue;

    const da = distMm(raw, wall.a);
    if (da <= toleranceMm) out.push(candidate('endpoint', wall.a, wall.id, da));
    const db = distMm(raw, wall.b);
    if (db <= toleranceMm) out.push(candidate('endpoint', wall.b, wall.id, db));

    const mid = ptRound((wall.a.x + wall.b.x) / 2, (wall.a.y + wall.b.y) / 2);
    const dm = distMm(raw, mid);
    if (dm <= toleranceMm) out.push(candidate('midpoint', mid, wall.id, dm));

    const proj = projectOnSegment(raw, wall.a, wall.b);
    if (proj.inside && proj.distanceMm <= toleranceMm) {
      out.push(candidate('wall-line', proj.pointMm, wall.id, proj.distanceMm));
    }
  }

  const boundary: Polygon = ctx.doc.plot.boundary;
  if (boundary.length >= 3) {
    for (const vertex of boundary) {
      const d = distMm(raw, vertex);
      if (d <= toleranceMm) out.push(candidate('plot-corner', vertex, null, d));
    }
    for (const edge of polygonEdges(boundary)) {
      const proj = projectOnSegment(raw, edge.a, edge.b);
      if (proj.inside && proj.distanceMm <= toleranceMm) {
        out.push(candidate('plot-edge', proj.pointMm, null, proj.distanceMm));
      }
    }
  }

  return out;
}

/**
 * Total order over candidates: rank, then nearest, then position, then id.
 *
 * Position and id are in there for determinism, not taste: two walls that share
 * an endpoint produce two identical-distance candidates, and a snap marker that
 * flickers between them under a still mouse is a bug nobody enjoys finding.
 */
export function compareSnapCandidates(a: SnapCandidate, b: SnapCandidate): number {
  if (a.rank !== b.rank) return b.rank - a.rank;
  if (a.distanceMm !== b.distanceMm) return a.distanceMm - b.distanceMm;
  if (a.pointMm.x !== b.pointMm.x) return a.pointMm.x - b.pointMm.x;
  if (a.pointMm.y !== b.pointMm.y) return a.pointMm.y - b.pointMm.y;
  return (a.refId ?? '').localeCompare(b.refId ?? '');
}

// ---------------------------------------------------------------------------
// Resolution
// ---------------------------------------------------------------------------

/**
 * THE tool-facing entry point: raw pointer millimetres in, the point to use out.
 *
 * Always returns integer mm. `candidate` is null when the grid (or nothing)
 * decided it, which is what the overlay uses to decide whether to draw a snap
 * marker at all.
 */
export function resolveSnap(ctx: ToolContext, raw: Pt, options: SnapOptions = {}): SnapResolution {
  const anchor = options.anchor ?? null;
  const ortho = (options.ortho ?? false) && anchor !== null;

  if (options.objectSnap !== false) {
    const candidates = collectSnapCandidates(ctx, raw, options);
    if (candidates.length > 0) {
      candidates.sort(compareSnapCandidates);
      const best = candidates[0];
      if (best !== undefined) {
        if (!ortho || anchor === null) return { pointMm: best.pointMm, candidate: best };
        const constrained = constrainOrtho(anchor, best.pointMm);
        if (ptEq(constrained, best.pointMm)) return { pointMm: best.pointMm, candidate: best };
        // The snap is off-axis: keep the constraint, report the alignment.
        return {
          pointMm: constrained,
          candidate: {
            ...best,
            kind: 'extension',
            label: `Aligned with ${best.label.toLowerCase()}`,
            pointMm: constrained,
            rank: RANK.extension,
          },
        };
      }
    }
  }

  if (ortho && anchor !== null) {
    // Snap the FREE axis only; the locked one keeps the anchor's coordinate
    // exactly. `constrainOrtho` picks the same axis by the same rule (|dx| wins
    // ties), so the two cannot disagree about which one is free.
    const horizontal = Math.abs(raw.x - anchor.x) >= Math.abs(raw.y - anchor.y);
    const point: Pt = horizontal
      ? { x: snapMm(raw.x, ctx.snapModuleMm), y: anchor.y }
      : { x: anchor.x, y: snapMm(raw.y, ctx.snapModuleMm) };
    return { pointMm: point, candidate: null };
  }

  return { pointMm: snapPtMm(raw, ctx.snapModuleMm), candidate: null };
}

/** Candidate → the overlay's snap marker. */
export function toSnapView(candidate: SnapCandidate | null): SnapView | null {
  if (candidate === null) return null;
  return {
    kind: candidate.kind,
    label: candidate.label,
    pointMm: candidate.pointMm,
    refId: candidate.refId,
  };
}
