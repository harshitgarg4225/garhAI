/**
 * ghostGeometry.ts — the storey below, as two buffers and nothing else.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY THIS IS NOT `PlanScene` WITH A DIFFERENT MATERIAL
 * ════════════════════════════════════════════════════════════════════════════
 * `PlanScene` is the drawing, and every one of its merged meshes registers a
 * pick resolver — that is what makes a wall clickable. Rendering it twice, once
 * for the storey below, would put a full set of pick candidates for the floor
 * downstairs under every pixel of the floor you are editing. `PICK_PRIORITY`
 * would not save it either: a ghost wall and a real wall are both `kind:
 * 'wall'`, so the tie would be broken by depth, and clicking an empty room on
 * the first floor would select a ground-floor wall.
 *
 * `features/underlay` reached the same conclusion for the scanned image and
 * said it best: a tracing aid that steals clicks from the walls being traced
 * over it is worse than no tracing aid. This module therefore produces PLAIN
 * BUFFERS — no ids, no resolvers, nothing the picker could ever be handed.
 * `StoreyGhostLayer` draws them and registers nothing.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ONE SOURCE FOR THE GEOMETRY
 * ════════════════════════════════════════════════════════════════════════════
 * Every shape here comes from `pages/project/plan/planGeometry` — the same
 * `wallRuns`, `wallSpanQuadF`, `openingSymbol`, `stairSymbol` and
 * `columnRingMm` the real plan is built from. A ghost drawn from its own idea
 * of where a wall's faces are would drift from the plan the moment either
 * changed, and the drift would be invisible: it is a faint grey line, and
 * "faintly wrong" is the hardest kind of wrong to notice. Only the packing into
 * typed arrays is local, because `PlanScene`'s packers are module-private and
 * carry the per-triangle element ids this layer must not have.
 *
 * The mm → world conversion is `coords.ts`'s, spelled out identically to
 * `PlanScene.packTriangles`: `(x, elevation, −y) × WORLD_UNITS_PER_MM`.
 */

import type { HouseModel, Opening, Pt, Storey } from '@garh/model';

import { WORLD_UNITS_PER_MM } from '../canvas/core';
import {
  balconiesOfStorey,
  columnRingMm,
  columnsOfStorey,
  openingSymbol,
  openingsOfStorey,
  stairSymbol,
  stairsOfStorey,
  triangleVerticesMm,
  wallRuns,
  wallSpanQuadF,
  wallsOfStorey,
  type PtF,
} from '../../pages/project/plan/planGeometry';

/** Two buffers in world units, ready for a `Mesh` and a `LineSegments`. */
export interface StoreyGhostGeometry {
  /** Triangle soup: wall poché with openings cut out, plus column footprints. */
  readonly fillPositions: Float32Array;
  /** Segment pairs: wall outlines, opening symbols, stair treads, balconies. */
  readonly linePositions: Float32Array;
  /** Triangles in `fillPositions`. Zero means "draw nothing". */
  readonly triangleCount: number;
  /** Segments in `linePositions`. */
  readonly segmentCount: number;
}

const EMPTY: StoreyGhostGeometry = {
  fillPositions: new Float32Array(0),
  linePositions: new Float32Array(0),
  triangleCount: 0,
  segmentCount: 0,
};

/**
 * The storey immediately below `storeyId`, or null when there is none.
 *
 * Exported because the panel labels what is being shown and the layer draws it:
 * two answers to "which storey is the ghost?" is exactly how a UI ends up
 * saying "Ground Floor" over a picture of the terrace.
 */
export function storeyBelow(house: HouseModel, storeyId: string | null): Storey | null {
  if (storeyId === null) return null;
  const index = house.storeys.findIndex((s) => s.id === storeyId);
  if (index <= 0) return null;
  return house.storeys[index - 1] ?? null;
}

/** Push one float-mm triangle fan-free quad/triangle list into world floats. */
function pushTrianglesMm(out: number[], trisMm: readonly number[], elevationMm: number): void {
  const worldY = elevationMm * WORLD_UNITS_PER_MM;
  for (let i = 0; i + 1 < trisMm.length; i += 2) {
    out.push(
      (trisMm[i] as number) * WORLD_UNITS_PER_MM,
      worldY,
      -(trisMm[i + 1] as number) * WORLD_UNITS_PER_MM,
    );
  }
}

/** A ring (quad or polygon) as a flat float-mm triangle list, fan-triangulated. */
function ringToTrianglesMm(ring: readonly PtF[]): number[] {
  const out: number[] = [];
  const first = ring[0];
  if (first === undefined) return out;
  for (let i = 1; i + 1 < ring.length; i += 1) {
    const b = ring[i];
    const c = ring[i + 1];
    if (b === undefined || c === undefined) continue;
    out.push(first.x, first.y, b.x, b.y, c.x, c.y);
  }
  return out;
}

function pushSegment(out: number[], from: PtF, to: PtF, elevationMm: number): void {
  const worldY = elevationMm * WORLD_UNITS_PER_MM;
  out.push(
    from.x * WORLD_UNITS_PER_MM,
    worldY,
    -from.y * WORLD_UNITS_PER_MM,
    to.x * WORLD_UNITS_PER_MM,
    worldY,
    -to.y * WORLD_UNITS_PER_MM,
  );
}

/** Every edge of a ring, closed. Used for wall outlines and balcony edges. */
function pushRing(out: number[], ring: readonly PtF[], elevationMm: number): void {
  for (let i = 0; i < ring.length; i += 1) {
    const from = ring[i];
    const to = ring[(i + 1) % ring.length];
    if (from === undefined || to === undefined) continue;
    pushSegment(out, from, to, elevationMm);
  }
}

/** An open polyline (the door swing arc, already flattened). */
function pushPolyline(out: number[], points: readonly PtF[], elevationMm: number): void {
  for (let i = 0; i + 1 < points.length; i += 1) {
    const from = points[i];
    const to = points[i + 1];
    if (from === undefined || to === undefined) continue;
    pushSegment(out, from, to, elevationMm);
  }
}

/**
 * Build the ghost for one storey at one elevation.
 *
 * Rooms are deliberately not washed: the storey below is context, and a second
 * tinted floor under the one you are drawing makes both harder to read. Walls,
 * openings, stairs, columns and balconies are what an architect actually aligns
 * to.
 */
export function buildStoreyGhost(
  house: HouseModel,
  storeyId: string | null,
  elevationMm: number,
): StoreyGhostGeometry {
  if (storeyId === null) return EMPTY;

  const walls = wallsOfStorey(house, storeyId);
  const columns = columnsOfStorey(house, storeyId);
  const stairs = stairsOfStorey(house, storeyId);
  const balconies = balconiesOfStorey(house, storeyId);
  const openings = openingsOfStorey(house, storeyId);
  if (walls.length === 0 && columns.length === 0 && stairs.length === 0 && balconies.length === 0) {
    return EMPTY;
  }

  const fill: number[] = [];
  const lines: number[] = [];

  // ── walls: poché with the openings genuinely cut out, plus the outline ────
  const openingsByWall = new Map<string, Opening[]>();
  for (const { opening } of openings) {
    const list = openingsByWall.get(opening.wallId);
    if (list === undefined) openingsByWall.set(opening.wallId, [opening]);
    else list.push(opening);
  }
  for (const wall of walls) {
    const hosted = openingsByWall.get(wall.id) ?? [];
    for (const run of wallRuns(wall, hosted)) {
      const quad = wallSpanQuadF(wall, run.startMm, run.endMm);
      if (quad !== null) pushTrianglesMm(fill, ringToTrianglesMm(quad), elevationMm);
    }
    const whole = wallSpanQuadF(wall, 0, Math.hypot(wall.b.x - wall.a.x, wall.b.y - wall.a.y));
    if (whole !== null) pushRing(lines, whole, elevationMm);
  }

  // ── openings: jambs, leaves and swing arcs ────────────────────────────────
  for (const { opening, wall } of openings) {
    const symbol = openingSymbol(wall, opening);
    if (symbol === null) continue;
    for (const [from, to] of symbol.lines) pushSegment(lines, from, to, elevationMm);
    pushPolyline(lines, symbol.arc, elevationMm);
  }

  // ── stairs: the flight outline, its treads and the up-arrow ───────────────
  for (const stair of stairs) {
    const symbol = stairSymbol(stair);
    pushRing(lines, symbol.ringMm, elevationMm);
    for (const [from, to] of symbol.treads) pushSegment(lines, from, to, elevationMm);
    pushSegment(lines, symbol.arrow[0], symbol.arrow[1], elevationMm);
  }

  // ── columns: solid, because a column you cannot see is a column you build
  //    a wall through on the floor above ──────────────────────────────────────
  for (const column of columns) {
    const ring: readonly Pt[] = columnRingMm(column);
    pushTrianglesMm(fill, triangleVerticesMm(ring), elevationMm);
    pushRing(lines, ring, elevationMm);
  }

  // ── balconies: outline only; the slab below is not what you align to ──────
  for (const balcony of balconies) {
    pushRing(lines, balcony.polygon, elevationMm);
  }

  return {
    fillPositions: Float32Array.from(fill),
    linePositions: Float32Array.from(lines),
    triangleCount: fill.length / 9,
    segmentCount: lines.length / 6,
  };
}
