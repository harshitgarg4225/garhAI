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
 * MVP LIMIT, STATED
 * ────────────────────────────────────────────────────────────────────────────
 * Only axis-aligned walls are dimensioned. §5/§7 make MVP walls orthogonal, and
 * a diagonal wall's "position along X" is not a number a chain can hold. Skew
 * walls are counted in {@link DimensionChainSet.skewWallIds} rather than
 * silently dropped, so the layer can say "3 walls are not dimensioned" instead
 * of quietly under-reporting the plan.
 */

import type { Opening, Pt, Room, Wall } from '@garh/model';
import { bbox, type Bbox } from '@garh/model';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Which model axis a chain measures along. */
export type DimAxis = 'x' | 'y';

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
  readonly axis: DimAxis;
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
  /** Walls that are neither horizontal nor vertical, and so are not dimensioned. */
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

function classify(walls: readonly Wall[]): { axis: AxisWall[]; skew: string[] } {
  const axis: AxisWall[] = [];
  const skew: string[] = [];
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
      skew.push(wall.id);
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
  const chainId = `${parentChainId}:${w.wall.id}`;
  const runs: OpeningRun[] = openings
    .filter((o) => o.wallId === w.wall.id)
    .map((o) => ({
      opening: o,
      startAlongMm: o.offsetMm - Math.floor(o.widthMm / 2),
      endAlongMm: o.offsetMm - Math.floor(o.widthMm / 2) + o.widthMm,
    }))
    .sort((p, q) => p.startAlongMm - q.startAlongMm || (p.opening.id < q.opening.id ? -1 : 1));

  if (runs.length === 0) return { segments: [], ticks: [] };

  const length = wallLengthMm(w);
  const segments: DimSegment[] = [];
  const tickAlong = new Set<number>([0, length]);

  let cursor = 0;
  for (const run of runs) {
    tickAlong.add(run.startAlongMm);
    tickAlong.add(run.endAlongMm);

    // Pier before the opening.
    const pier = run.startAlongMm - cursor;
    if (pier >= minMm) {
      const s = alongToAxis(w, cursor);
      const e = alongToAxis(w, run.startAlongMm);
      segments.push({
        id: segmentId(chainId, Math.min(s, e), Math.max(s, e)),
        startMm: Math.min(s, e),
        endMm: Math.max(s, e),
        valueMm: pier,
        target: {
          kind: 'opening-gap',
          openingId: run.opening.id,
          wallId: w.wall.id,
          anchorAlongMm: cursor,
          side: 'before',
        },
      });
    }

    // The opening itself.
    const os = alongToAxis(w, run.startAlongMm);
    const oe = alongToAxis(w, run.endAlongMm);
    segments.push({
      id: segmentId(chainId, Math.min(os, oe), Math.max(os, oe)),
      startMm: Math.min(os, oe),
      endMm: Math.max(os, oe),
      valueMm: run.opening.widthMm,
      target: { kind: 'opening-width', openingId: run.opening.id, wallId: w.wall.id },
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
      const s = alongToAxis(w, last.endAlongMm);
      const e = alongToAxis(w, length);
      segments.push({
        id: segmentId(chainId, Math.min(s, e), Math.max(s, e)),
        startMm: Math.min(s, e),
        endMm: Math.max(s, e),
        valueMm: tail,
        target: {
          kind: 'opening-gap',
          openingId: last.opening.id,
          wallId: w.wall.id,
          anchorAlongMm: length,
          side: 'after',
        },
      });
    }
  }

  const ticks: DimTick[] = Array.from(tickAlong)
    .map((along) => ({ atMm: alongToAxis(w, along), wallIds: [] as readonly string[] }))
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
  if (axisWalls.length === 0) {
    return { chains: [], extentMm: null, skewWallIds: skew };
  }

  const points: Pt[] = [];
  for (const w of axisWalls) {
    points.push(w.wall.a, w.wall.b);
  }
  const extentMm = bbox(points);

  const chains: DimChain[] = [];

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

  return { chains, extentMm, skewWallIds: skew };
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
