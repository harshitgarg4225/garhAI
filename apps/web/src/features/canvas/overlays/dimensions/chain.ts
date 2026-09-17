/**
 * chain.ts — dimension chains from a wall set. PURE: no React, no three, no
 * store. Everything here is integer millimetres in and integer millimetres out,
 * which is why it is the module the specs hammer.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHAT A CHAIN IS
 * ────────────────────────────────────────────────────────────────────────────
 * A chain is one dimension *string*: a baseline parallel to an axis, a sorted
 * list of ticks along it, and the segments between them. Three levels are built
 * per side, in the order a drafted sheet stacks them outward from the building:
 *
 *   level 0  `opening`  door and window widths, and the piers between them
 *   level 1  `wall`     wall-to-wall — one segment per structural bay
 *   level 2  `overall`  a single segment across the whole extent
 *
 * The baseline OFFSET is not computed here. It is a screen-space distance (the
 * strings must sit the same distance from the building at 1:20 and at 1:500),
 * so the layer resolves it per zoom via {@link chainBaselineMm}. What this file
 * fixes is the geometry that does not depend on zoom: which ticks exist, what
 * each segment measures, and — the part that matters — what editing a segment
 * is supposed to DO.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * CENTRELINES, NOT FACES
 * ────────────────────────────────────────────────────────────────────────────
 * Every wall tick is the wall's CENTRELINE coordinate. `wall.move` takes
 * centreline endpoints (§4 op 10), so a centreline dimension maps to the op
 * with no half-thickness fudge in between: what you typed is what the payload
 * says. Face-to-face dimensioning (which municipal sheets also want) is a
 * §7 sheet-engine concern and is deliberately NOT smuggled in here, because a
 * canvas that dims to faces and an op that moves centrelines disagree by
 * 115 mm and nobody can see which one is wrong.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * SKEW WALLS: ALIGNED CHAINS
 * ────────────────────────────────────────────────────────────────────────────
 * The axis strings above can only hold a coordinate along X or Y, and a
 * diagonal wall has neither. Shift-inverted ortho lets an architect draw one,
 * and a drawing with an undimensioned wall on it is a drawing that goes back
 * from the site. So every skew wall gets an ALIGNED chain of its own: a frame
 * (`origin`, unit direction, outward normal) in which "along" is distance from
 * the wall's `a` end, one level-1 segment for its length (editable — it moves
 * the `b` end along the wall, `wall-length`), and a level-0 opening string in
 * the same along-wall space the opening targets already speak. The chain hangs
 * off the side of the wall that faces away from the building.
 *
 * {@link DimensionChainSet.skewWallIds} still lists those walls: they are not
 * in the axis strings, and a caller that wants to say so can.
 */

import type { Opening, Pt, Room, Wall } from '@garh/model';
import { bbox, type Bbox } from '@garh/model';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Which model axis a chain measures along. */
export type DimAxis = 'x' | 'y';

/** A chain's axis: one of the model's, or its own frame (a skew wall). */
export type DimChainAxis = DimAxis | 'aligned';

/**
 * The frame of an aligned chain: `along` coordinates run from `origin` in the
 * unit direction `(ux, uy)`, and the baseline is pushed out along the unit
 * normal `(nx, ny)`, which points away from the building. Float, render-only —
 * the integer targets carry what an edit needs.
 */
export interface AlignedFrame {
  readonly origin: Pt;
  readonly ux: number;
  readonly uy: number;
  readonly nx: number;
  readonly ny: number;
}

/** Which side of the building the chain sits on. */
export type DimSide = 'S' | 'N' | 'W' | 'E';

export type DimChainKind = 'opening' | 'wall' | 'overall' | 'room';

/** Chain level, outward from the building. Drives the baseline offset. */
export const DIM_LEVEL: Readonly<Record<DimChainKind, number>> = {
  opening: 0,
  wall: 1,
  overall: 2,
  room: 0,
};

/**
 * What committing a new value on a segment does. Consumed by
 * `applyDimensionEdit` in `edit.ts`, which is the only place that turns one of
 * these into ops.
 */
export type DimensionEditTarget =
  | {
      /**
       * The gap between two parallel walls. `anchorWallIds` stay put and
       * `movingWallIds` slide along `axis` — plus whatever is joined to them
       * (see `edit.ts`). `anchorAtMm`/`movingAtMm` are the centreline
       * coordinates the chain was built from; `edit.ts` re-reads the live ones
       * so a stale chain cannot move a wall to a coordinate that no longer
       * means anything.
       */
      readonly kind: 'wall-gap';
      readonly axis: DimAxis;
      readonly anchorWallIds: readonly string[];
      readonly movingWallIds: readonly string[];
      readonly anchorAtMm: number;
      readonly movingAtMm: number;
    }
  | {
      /** A pier: the clear masonry between a wall end (or another opening) and
       *  an opening. Editing it SLIDES the opening; nothing else moves. */
      readonly kind: 'opening-gap';
      readonly openingId: string;
      readonly wallId: string;
      /** Along-wall coordinate of the fixed end of the pier. */
      readonly anchorAlongMm: number;
      /** `before` — the pier is on the wall.a side of the opening. */
      readonly side: 'before' | 'after';
    }
  | {
      /** The opening itself. Editing it resizes, keeping the centre put. */
      readonly kind: 'opening-width';
      readonly openingId: string;
      readonly wallId: string;
    }
  | {
      /**
       * A skew wall's own length. Editing it holds `a` and slides `b` along
       * the wall's existing direction — the same edit the inspector's Length
       * field and `setWallLengthOps` make.
       */
      readonly kind: 'wall-length';
      readonly wallId: string;
    };

export interface DimTick {
  /** Coordinate along the chain axis, integer mm. */
  readonly atMm: number;
  /** Walls whose centreline sits here. Empty for opening-derived ticks. */
  readonly wallIds: readonly string[];
}

export interface DimSegment {
  /** Stable across rebuilds of the same plan — used as a React key and as the
   *  pick handle id. Derived from the chain and the two tick coordinates, so a
   *  segment that has not moved keeps its identity across an unrelated edit. */
  readonly id: string;
  readonly startMm: number;
  readonly endMm: number;
  /** `endMm - startMm`, always > 0. */
  readonly valueMm: number;
  /**
   * `null` when the segment is measurable but not editable — a bay whose far
   * tick has no wall behind it (an open-ended plan). The layer renders it as
   * plain text with no hit target rather than pretending it is editable and
   * failing on commit. §15 says no dead text; it also does not say to lie.
   */
  readonly target: DimensionEditTarget | null;
}

export interface DimChain {
  readonly id: string;
  readonly kind: DimChainKind;
  readonly side: DimSide;
  readonly axis: DimChainAxis;
  /** Present exactly when `axis === 'aligned'`. */
  readonly frame?: AlignedFrame | undefined;
  /**
   * The building-edge coordinate the chain is measured from, perpendicular to
   * `axis`. The baseline is this pushed outward by a screen-space offset.
   */
  readonly edgeMm: number;
  /** +1 when the chain sits at increasing perpendicular coordinate. */
  readonly outward: 1 | -1;
  readonly level: number;
  readonly segments: readonly DimSegment[];
  /** Perpendicular coordinates the witness lines run back to, per tick. */
  readonly ticks: readonly DimTick[];
}

export interface DimensionChainSet {
  readonly chains: readonly DimChain[];
  /** Bounding box of the wall centrelines, or null when there are no walls. */
  readonly extentMm: Bbox | null;
  /**
   * Walls that are neither horizontal nor vertical. They are not in the axis
   * strings; each carries its own aligned chain instead.
   */
  readonly skewWallIds: readonly string[];
}

export interface DimensionChainOptions {
  /** Sides to build. Default all four. */
  readonly sides?: readonly DimSide[] | undefined;
  /** Build the level-0 opening string. Default true. */
  readonly includeOpenings?: boolean | undefined;
  /** Build the level-2 overall string. Default true. */
  readonly includeOverall?: boolean | undefined;
  /**
   * Segments below this are dropped from the wall string. Two walls 40 mm
   * apart are a modelling artefact, and a 40 mm dimension is unreadable and
   * unhelpful. Default 100 mm.
   */
  readonly minSegmentMm?: number | undefined;
  /**
   * How close a wall's centreline must be to the building edge to count as
   * "on that side" for the opening string. Default 600 mm — wide enough to
   * catch a 230 mm external wall drawn slightly inboard, tight enough not to
   * pick up the first internal partition.
   */
  readonly edgeToleranceMm?: number | undefined;
}

const DEFAULT_SIDES: readonly DimSide[] = ['S', 'E', 'N', 'W'];
const DEFAULT_MIN_SEGMENT_MM = 100;
const DEFAULT_EDGE_TOLERANCE_MM = 600;

// ---------------------------------------------------------------------------
// Wall classification
// ---------------------------------------------------------------------------

/** An axis-aligned wall, reduced to the two numbers a chain cares about. */
interface AxisWall {
  readonly wall: Wall;
  /** The axis the wall RUNS along. */
  readonly runAxis: DimAxis;
  /** The constant coordinate on the other axis — where a tick goes. */
  readonly constMm: number;
  /** Extent along `runAxis`. */
  readonly minMm: number;
  readonly maxMm: number;
}

function classify(walls: readonly Wall[]): { axis: AxisWall[]; skew: Wall[] } {
  const axis: AxisWall[] = [];
  const skew: Wall[] = [];
  for (const wall of walls) {
    if (wall.a.x === wall.b.x && wall.a.y === wall.b.y) continue; // degenerate
    if (wall.a.y === wall.b.y) {
      axis.push({
        wall,
        runAxis: 'x',
        constMm: wall.a.y,
        minMm: Math.min(wall.a.x, wall.b.x),
        maxMm: Math.max(wall.a.x, wall.b.x),
      });
    } else if (wall.a.x === wall.b.x) {
      axis.push({
        wall,
        runAxis: 'y',
        constMm: wall.a.x,
        minMm: Math.min(wall.a.y, wall.b.y),
        maxMm: Math.max(wall.a.y, wall.b.y),
      });
    } else {
      skew.push(wall);
    }
  }
  return { axis, skew };
}

/** Chain axis per side, and which perpendicular extreme the chain hangs off. */
const SIDE_AXIS: Readonly<Record<DimSide, DimAxis>> = { S: 'x', N: 'x', W: 'y', E: 'y' };
const SIDE_OUTWARD: Readonly<Record<DimSide, 1 | -1>> = { S: -1, N: 1, W: -1, E: 1 };

/** The wall run-axis that produces ticks for a chain measuring along `axis`. */
function tickRunAxis(axis: DimAxis): DimAxis {
  return axis === 'x' ? 'y' : 'x';
}

function edgeCoordinate(box: Bbox, side: DimSide): number {
  switch (side) {
    case 'S':
      return box.minY;
    case 'N':
      return box.maxY;
    case 'W':
      return box.minX;
    case 'E':
      return box.maxX;
  }
}

// ---------------------------------------------------------------------------
// Ticks
// ---------------------------------------------------------------------------

/** Group axis walls into ticks by their constant coordinate. Sorted ascending. */
function ticksFromWalls(walls: readonly AxisWall[], axis: DimAxis): DimTick[] {
  const want = tickRunAxis(axis);
  const byCoord = new Map<number, string[]>();
  for (const w of walls) {
    if (w.runAxis !== want) continue;
    const list = byCoord.get(w.constMm);
    if (list === undefined) byCoord.set(w.constMm, [w.wall.id]);
    else list.push(w.wall.id);
  }
  return Array.from(byCoord.entries())
    .map(([atMm, ids]) => ({ atMm, wallIds: ids.slice().sort() }))
    .sort((a, b) => a.atMm - b.atMm);
}

// ---------------------------------------------------------------------------
// Segment construction
// ---------------------------------------------------------------------------

function segmentId(chainId: string, startMm: number, endMm: number): string {
  return `${chainId}:${String(startMm)}:${String(endMm)}`;
}

/**
 * Consecutive ticks → segments.
 *
 * The LOWER tick anchors and the HIGHER tick moves. That is the behaviour a
 * CAD user expects from a dimension override: the thing you referenced moves,
 * everything on the far side of the anchor stays where it is, and the
 * neighbouring bay absorbs the difference. The alternative — pushing the whole
 * plan — changes dimensions the user did not touch, which is much harder to
 * undo mentally than one wall in the wrong place.
 */
function wallSegments(
  chainId: string,
  axis: DimAxis,
  ticks: readonly DimTick[],
  minMm: number,
): DimSegment[] {
  const out: DimSegment[] = [];
  for (let i = 0; i + 1 < ticks.length; i++) {
    const a = ticks[i];
    const b = ticks[i + 1];
    if (a === undefined || b === undefined) continue;
    const valueMm = b.atMm - a.atMm;
    if (valueMm < minMm) continue;
    const editable = a.wallIds.length > 0 && b.wallIds.length > 0;
    out.push({
      id: segmentId(chainId, a.atMm, b.atMm),
      startMm: a.atMm,
      endMm: b.atMm,
      valueMm,
      target: editable
        ? {
            kind: 'wall-gap',
            axis,
            anchorWallIds: a.wallIds,
            movingWallIds: b.wallIds,
            anchorAtMm: a.atMm,
            movingAtMm: b.atMm,
          }
        : null,
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Opening strings
// ---------------------------------------------------------------------------

/**
 * Map an along-wall distance to a chain-axis coordinate.
 *
 * `Opening.offsetMm` is measured from `wall.a`, which may be the high-coordinate
 * end — a wall drawn right-to-left has `a.x > b.x`. Getting this backwards
 * mirrors every window on that wall, and it is invisible on a symmetric plan,
 * which is exactly why it is a named function with a spec rather than an
 * expression inlined twice.
 */
function alongToAxis(w: AxisWall, alongMm: number): number {
  const forward = w.runAxis === 'x' ? w.wall.b.x >= w.wall.a.x : w.wall.b.y >= w.wall.a.y;
  const start = w.runAxis === 'x' ? w.wall.a.x : w.wall.a.y;
  return forward ? start + alongMm : start - alongMm;
}

function wallLengthMm(w: AxisWall): number {
  return w.maxMm - w.minMm;
}

interface OpeningRun {
  readonly opening: Opening;
  /** Along-wall coordinates of the two jambs. */
  readonly startAlongMm: number;
  readonly endAlongMm: number;
}

/**
 * The opening string for ONE wall: piers and openings, alternating, in
 * along-wall order. Returned in chain-axis coordinates but with along-wall
 * anchors carried in the targets — the edit maths belongs in along-wall space
 * (that is what `opening.move` speaks) and the rendering belongs in axis space.
 */
function openingSegmentsForWall(
  parentChainId: string,
  w: AxisWall,
  openings: readonly Opening[],
  minMm: number,
): { segments: DimSegment[]; ticks: DimTick[] } {
  return openingSegmentsAlong(parentChainId, w.wall, wallLengthMm(w), openings, minMm, (along) =>
    alongToAxis(w, along),
  );
}

/**
 * The opening string for ONE wall, in whatever chain coordinate `toChain`
 * maps along-wall distance to: the axis coordinate for an axis wall, the
 * along-wall distance itself for an aligned chain. The targets carry
 * along-wall anchors either way — that is the space `opening.move` speaks.
 */
function openingSegmentsAlong(
  parentChainId: string,
  wall: Wall,
  length: number,
  openings: readonly Opening[],
  minMm: number,
  toChain: (alongMm: number) => number,
): { segments: DimSegment[]; ticks: DimTick[] } {
  /**
   * Segment ids are namespaced by the HOST WALL, not just the chain.
   *
   * One opening string aggregates every wall facing that side of the building.
   * Two collinear walls covering the same span — a wall split in two, which
   * `wall.split` produces routinely — would otherwise mint identical segment
   * ids, and a segment id is both a React key and a pick handle. Duplicate
   * React keys make React reuse the wrong node; a duplicate pick handle makes
   * `lookup` return whichever one was inserted last, so you edit a dimension
   * you are not pointing at.
   */
  const chainId = `${parentChainId}:${wall.id}`;
  const runs: OpeningRun[] = openings
    .filter((o) => o.wallId === wall.id)
    .map((o) => ({
      opening: o,
      startAlongMm: o.offsetMm - Math.floor(o.widthMm / 2),
      endAlongMm: o.offsetMm - Math.floor(o.widthMm / 2) + o.widthMm,
    }))
    .sort((p, q) => p.startAlongMm - q.startAlongMm || (p.opening.id < q.opening.id ? -1 : 1));

  if (runs.length === 0) return { segments: [], ticks: [] };

  const segments: DimSegment[] = [];
  const tickAlong = new Set<number>([0, length]);

  let cursor = 0;
  for (const run of runs) {
    tickAlong.add(run.startAlongMm);
    tickAlong.add(run.endAlongMm);

    // Pier before the opening.
    const pier = run.startAlongMm - cursor;
    if (pier >= minMm) {
      const s = toChain(cursor);
      const e = toChain(run.startAlongMm);
      segments.push({
        id: segmentId(chainId, Math.min(s, e), Math.max(s, e)),
        startMm: Math.min(s, e),
        endMm: Math.max(s, e),
        valueMm: pier,
        target: {
          kind: 'opening-gap',
          openingId: run.opening.id,
          wallId: wall.id,
          anchorAlongMm: cursor,
          side: 'before',
        },
      });
    }

    // The opening itself.
    const os = toChain(run.startAlongMm);
    const oe = toChain(run.endAlongMm);
    segments.push({
      id: segmentId(chainId, Math.min(os, oe), Math.max(os, oe)),
      startMm: Math.min(os, oe),
      endMm: Math.max(os, oe),
      valueMm: run.opening.widthMm,
      target: { kind: 'opening-width', openingId: run.opening.id, wallId: wall.id },
    });

    cursor = run.endAlongMm;
  }

  // Trailing pier. Anchored at the far wall end, so editing it slides the LAST
  // opening rather than the first — which is what "this gap should be 600" means
  // when you are pointing at the gap next to the wall end.
  const last = runs[runs.length - 1];
  if (last !== undefined) {
    const tail = length - last.endAlongMm;
    if (tail >= minMm) {
      const s = toChain(last.endAlongMm);
      const e = toChain(length);
      segments.push({
        id: segmentId(chainId, Math.min(s, e), Math.max(s, e)),
        startMm: Math.min(s, e),
        endMm: Math.max(s, e),
        valueMm: tail,
        target: {
          kind: 'opening-gap',
          openingId: last.opening.id,
          wallId: wall.id,
          anchorAlongMm: length,
          side: 'after',
        },
      });
    }
  }

  const ticks: DimTick[] = Array.from(tickAlong)
    .map((along) => ({ atMm: toChain(along), wallIds: [] as readonly string[] }))
    .sort((a, b) => a.atMm - b.atMm);

  segments.sort((a, b) => a.startMm - b.startMm);
  return { segments, ticks };
}

// ---------------------------------------------------------------------------
// The entry point
// ---------------------------------------------------------------------------

/**
 * Build every dimension chain for one storey's walls.
 *
 * COST: O(w log w + o log o) — one classification pass, one sort per side. On
 * the G+2 demo (about 180 walls per storey) this is well under a millisecond,
 * which is what lets the layer rebuild it synchronously on every document
 * change instead of maintaining an incremental cache that can go stale.
 */
export function buildDimensionChains(
  walls: readonly Wall[],
  openings: readonly Opening[],
  options: DimensionChainOptions = {},
): DimensionChainSet {
  const sides = options.sides ?? DEFAULT_SIDES;
  const includeOpenings = options.includeOpenings ?? true;
  const includeOverall = options.includeOverall ?? true;
  const minSegmentMm = options.minSegmentMm ?? DEFAULT_MIN_SEGMENT_MM;
  const edgeToleranceMm = options.edgeToleranceMm ?? DEFAULT_EDGE_TOLERANCE_MM;

  const { axis: axisWalls, skew } = classify(walls);
  const skewWallIds = skew.map((w) => w.id);

  const allPoints: Pt[] = [];
  for (const w of walls) {
    if (w.a.x === w.b.x && w.a.y === w.b.y) continue;
    allPoints.push(w.a, w.b);
  }
  const chains: DimChain[] = [];

  // ── aligned chains, one per skew wall ────────────────────────────────
  if (skew.length > 0) {
    const all = bbox(allPoints);
    const centre = { x: (all.minX + all.maxX) / 2, y: (all.minY + all.maxY) / 2 };
    for (const wall of skew) {
      chains.push(...alignedChainsForWall(wall, centre, openings, includeOpenings, minSegmentMm));
    }
  }

  if (axisWalls.length === 0) {
    return { chains, extentMm: allPoints.length > 0 ? bbox(allPoints) : null, skewWallIds };
  }

  const points: Pt[] = [];
  for (const w of axisWalls) {
    points.push(w.wall.a, w.wall.b);
  }
  const extentMm = bbox(points);

  for (const side of sides) {
    const axis = SIDE_AXIS[side];
    const outward = SIDE_OUTWARD[side];
    const edgeMm = edgeCoordinate(extentMm, side);

    // ── level 1: wall-to-wall ────────────────────────────────────────────
    const wallChainId = `dim:${side}:wall`;
    const ticks = ticksFromWalls(axisWalls, axis);
    const segments = wallSegments(wallChainId, axis, ticks, minSegmentMm);
    if (segments.length > 0) {
      chains.push({
        id: wallChainId,
        kind: 'wall',
        side,
        axis,
        edgeMm,
        outward,
        level: DIM_LEVEL.wall,
        segments,
        ticks,
      });
    }

    // ── level 2: overall ─────────────────────────────────────────────────
    const first = ticks[0];
    const last = ticks[ticks.length - 1];
    if (includeOverall && first !== undefined && last !== undefined && last.atMm > first.atMm) {
      // Only worth a second string when it says something the wall string does
      // not: a two-tick plan's overall IS its only bay.
      if (ticks.length > 2) {
        const overallId = `dim:${side}:overall`;
        chains.push({
          id: overallId,
          kind: 'overall',
          side,
          axis,
          edgeMm,
          outward,
          level: DIM_LEVEL.overall,
          segments: [
            {
              id: segmentId(overallId, first.atMm, last.atMm),
              startMm: first.atMm,
              endMm: last.atMm,
              valueMm: last.atMm - first.atMm,
              target: {
                kind: 'wall-gap',
                axis,
                anchorWallIds: first.wallIds,
                movingWallIds: last.wallIds,
                anchorAtMm: first.atMm,
                movingAtMm: last.atMm,
              },
            },
          ],
          ticks: [first, last],
        });
      }
    }

    // ── level 0: openings on the walls that face this side ───────────────
    if (includeOpenings) {
      const runAxisForSide = axis; // a wall parallel to the chain carries the openings
      const facing = axisWalls.filter(
        (w) => w.runAxis === runAxisForSide && Math.abs(w.constMm - edgeMm) <= edgeToleranceMm,
      );
      const openingChainId = `dim:${side}:opening`;
      const allSegments: DimSegment[] = [];
      const allTicks: DimTick[] = [];
      for (const w of facing) {
        const built = openingSegmentsForWall(openingChainId, w, openings, minSegmentMm);
        allSegments.push(...built.segments);
        allTicks.push(...built.ticks);
      }
      if (allSegments.length > 0) {
        allSegments.sort((a, b) => a.startMm - b.startMm);
        allTicks.sort((a, b) => a.atMm - b.atMm);
        chains.push({
          id: openingChainId,
          kind: 'opening',
          side,
          axis,
          edgeMm,
          outward,
          level: DIM_LEVEL.opening,
          segments: allSegments,
          ticks: allTicks,
        });
      }
    }
  }

  return { chains, extentMm, skewWallIds };
}

// ---------------------------------------------------------------------------
// Aligned chains — a skew wall's own frame
// ---------------------------------------------------------------------------

/** The compass side the outward normal faces, for the chain's `side`. */
function sideOfNormal(nx: number, ny: number): DimSide {
  if (Math.abs(nx) >= Math.abs(ny)) return nx >= 0 ? 'E' : 'W';
  return ny >= 0 ? 'N' : 'S';
}

/**
 * The frame of a skew wall: along from `a`, normal pointing away from the
 * building's centre so the strings hang outside the plan like the axis ones.
 */
export function alignedFrame(wall: Wall, centre: { x: number; y: number }): AlignedFrame | null {
  const dx = wall.b.x - wall.a.x;
  const dy = wall.b.y - wall.a.y;
  const len = Math.hypot(dx, dy);
  if (len === 0) return null;
  const ux = dx / len;
  const uy = dy / len;
  let nx = -uy;
  let ny = ux;
  const midX = (wall.a.x + wall.b.x) / 2;
  const midY = (wall.a.y + wall.b.y) / 2;
  // The left normal points at the building's centre: use the other one.
  if (nx * (centre.x - midX) + ny * (centre.y - midY) > 0) {
    nx = -nx;
    ny = -ny;
  }
  return { origin: wall.a, ux, uy, nx, ny };
}

function alignedChainsForWall(
  wall: Wall,
  centre: { x: number; y: number },
  openings: readonly Opening[],
  includeOpenings: boolean,
  minSegmentMm: number,
): DimChain[] {
  const frame = alignedFrame(wall, centre);
  if (frame === null) return [];
  const lengthMm = Math.round(Math.hypot(wall.b.x - wall.a.x, wall.b.y - wall.a.y));
  const side = sideOfNormal(frame.nx, frame.ny);
  const out: DimChain[] = [];

  const wallChainId = `dim:aligned:${wall.id}:wall`;
  if (lengthMm >= minSegmentMm) {
    out.push({
      id: wallChainId,
      kind: 'wall',
      side,
      axis: 'aligned',
      frame,
      edgeMm: 0,
      outward: 1,
      level: DIM_LEVEL.wall,
      segments: [
        {
          id: segmentId(wallChainId, 0, lengthMm),
          startMm: 0,
          endMm: lengthMm,
          valueMm: lengthMm,
          target: { kind: 'wall-length', wallId: wall.id },
        },
      ],
      ticks: [
        { atMm: 0, wallIds: [wall.id] },
        { atMm: lengthMm, wallIds: [wall.id] },
      ],
    });
  }

  if (includeOpenings) {
    const openingChainId = `dim:aligned:${wall.id}:opening`;
    const built = openingSegmentsAlong(
      openingChainId,
      wall,
      lengthMm,
      openings,
      minSegmentMm,
      (along) => along,
    );
    if (built.segments.length > 0) {
      out.push({
        id: openingChainId,
        kind: 'opening',
        side,
        axis: 'aligned',
        frame,
        edgeMm: 0,
        outward: 1,
        level: DIM_LEVEL.opening,
        segments: built.segments,
        ticks: built.ticks,
      });
    }
  }

  return out;
}

// ---------------------------------------------------------------------------
// Room span chains — "live dimensions while editing"
// ---------------------------------------------------------------------------

/**
 * The two chains that appear across a room while it is selected: its clear
 * width and its clear depth.
 *
 * These are the dimensions an architect actually retypes ("make this bedroom
 * 3600 wide"), so they must be editable, and editing must move a wall. The
 * bounding walls are found by matching the room's clear-polygon extremes back
 * to wall centrelines: a room edge at x = 2943 with a 115 mm wall is the
 * centreline x = 3000 wall, half a thickness away. Any wall whose centreline is
 * within `thickness/2 + 1` of the room edge qualifies — the +1 absorbs the
 * `floor(thickness / 2)` the room inset uses for odd thicknesses (§rooms.ts).
 */
export function buildRoomSpanChains(
  room: Room,
  walls: readonly Wall[],
  options: DimensionChainOptions = {},
): DimChain[] {
  if (room.polygon.length < 3) return [];
  const minSegmentMm = options.minSegmentMm ?? DEFAULT_MIN_SEGMENT_MM;
  const box = bbox(room.polygon);
  const { axis: axisWalls } = classify(walls);

  const chains: DimChain[] = [];

  const build = (axis: DimAxis, side: DimSide, lowMm: number, highMm: number): void => {
    const valueMm = highMm - lowMm;
    if (valueMm < minSegmentMm) return;
    const lowWalls = wallsBounding(axisWalls, axis, lowMm, 'low');
    const highWalls = wallsBounding(axisWalls, axis, highMm, 'high');
    const chainId = `dim:room:${room.id}:${axis}`;
    chains.push({
      id: chainId,
      kind: 'room',
      side,
      axis,
      // A room chain draws THROUGH the room, not outside the building: the edge
      // it hangs off is the room's own far side.
      edgeMm: axis === 'x' ? (box.minY + box.maxY) / 2 : (box.minX + box.maxX) / 2,
      outward: 1,
      level: DIM_LEVEL.room,
      ticks: [
        { atMm: lowMm, wallIds: lowWalls },
        { atMm: highMm, wallIds: highWalls },
      ],
      segments: [
        {
          id: segmentId(chainId, lowMm, highMm),
          startMm: lowMm,
          endMm: highMm,
          valueMm,
          target:
            lowWalls.length > 0 && highWalls.length > 0
              ? {
                  kind: 'wall-gap',
                  axis,
                  anchorWallIds: lowWalls,
                  movingWallIds: highWalls,
                  // Centreline coordinates, not the room's clear edge — the op
                  // moves centrelines, so the arithmetic must be done in them.
                  anchorAtMm: centrelineOf(axisWalls, lowWalls) ?? lowMm,
                  movingAtMm: centrelineOf(axisWalls, highWalls) ?? highMm,
                }
              : null,
        },
      ],
    });
  };

  build('x', 'S', box.minX, box.maxX);
  build('y', 'W', box.minY, box.maxY);
  return chains;
}

/**
 * Walls whose centreline could be the one that produced a room edge at
 * `edgeMm`. `side` says which way the wall lies from the clear face.
 */
function wallsBounding(
  axisWalls: readonly AxisWall[],
  axis: DimAxis,
  edgeMm: number,
  side: 'low' | 'high',
): string[] {
  const want = tickRunAxis(axis);
  const out: string[] = [];
  for (const w of axisWalls) {
    if (w.runAxis !== want) continue;
    const half = Math.floor(w.wall.thicknessMm / 2);
    const expected = side === 'low' ? edgeMm - half : edgeMm + half;
    if (Math.abs(w.constMm - expected) <= 1) out.push(w.wall.id);
  }
  return out.sort();
}

function centrelineOf(axisWalls: readonly AxisWall[], ids: readonly string[]): number | null {
  for (const w of axisWalls) {
    if (ids.includes(w.wall.id)) return w.constMm;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Baselines — where a chain is drawn, resolved at render time
// ---------------------------------------------------------------------------

/**
 * The perpendicular coordinate a chain's dimension line is drawn at.
 *
 * `offsetMm` and `stepMm` come from the layer as `pixels × mmPerPx`, which is
 * what keeps the strings a constant distance from the building at every zoom.
 * A room chain draws through the room, so it ignores both.
 */
export function chainBaselineMm(chain: DimChain, offsetMm: number, stepMm: number): number {
  if (chain.kind === 'room') return chain.edgeMm;
  return chain.edgeMm + chain.outward * (offsetMm + chain.level * stepMm);
}

/**
 * A RENDER coordinate: float millimetres. Baselines are derived from zoom, so
 * they are not integers and must never become an op payload — the target
 * carries the integers that do.
 */
export interface DimPointF {
  readonly x: number;
  readonly y: number;
}

/** The plan point of a position along a chain, at a given baseline. */
export function chainPointMm(chain: DimChain, alongMm: number, baselineMm: number): DimPointF {
  if (chain.axis === 'aligned') {
    const f = chain.frame;
    if (f === undefined) return { x: alongMm, y: baselineMm };
    return {
      x: f.origin.x + f.ux * alongMm + f.nx * baselineMm,
      y: f.origin.y + f.uy * alongMm + f.ny * baselineMm,
    };
  }
  return chain.axis === 'x' ? { x: alongMm, y: baselineMm } : { x: baselineMm, y: alongMm };
}

/** Midpoint of a segment, where its label goes. */
export function segmentMidMm(chain: DimChain, segment: DimSegment, baselineMm: number): DimPointF {
  return chainPointMm(chain, (segment.startMm + segment.endMm) / 2, baselineMm);
}

/** Every editable segment, flattened — the layer's hit-target index. */
export function editableSegments(
  chains: readonly DimChain[],
): { chain: DimChain; segment: DimSegment; target: DimensionEditTarget }[] {
  const out: { chain: DimChain; segment: DimSegment; target: DimensionEditTarget }[] = [];
  for (const chain of chains) {
    for (const segment of chain.segments) {
      if (segment.target !== null) out.push({ chain, segment, target: segment.target });
    }
  }
  return out;
}
