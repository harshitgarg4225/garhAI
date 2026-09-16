/**
 * planBuffers — the merged vertex buffers `PlanScene` draws, as PURE functions.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY THIS IS NOT INSIDE `PlanScene.tsx`
 * ════════════════════════════════════════════════════════════════════════════
 * CLAUDE.md's fourth bug: a layer that tagged its meshes for hit-testing,
 * documented itself as integrated, and never called the registry — every
 * placed item invisible to clicks, with no compile-time signal. The only
 * defence is a test that CLICKS: build the exact buffer the scene draws, hand
 * it to the one `PickRegistry` with the exact resolver the scene registers,
 * cast the exact ray `useCanvasControls` would, and assert the element id
 * comes back. That test cannot mount a WebGL canvas in vitest, so the buffer
 * builders and the resolver live here, and `PlanScene` is a thin component
 * over them. `planBuffers.test.ts` is that click test; a builder that packs
 * the wrong id per face, or a resolver that reads the wrong index, goes red
 * there rather than in front of an architect.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE CONVERSION BOUNDARY
 * ════════════════════════════════════════════════════════════════════════════
 * `packTriangles` and `packSegments` are the only two places in the plan
 * scene where millimetres become world units: a multiply by
 * `WORLD_UNITS_PER_MM`, with the model's Y going to world −Z exactly as
 * `coords.ts` defines it. Everything upstream is float millimetres from
 * `planGeometry.ts`; nothing here produces an op.
 */

import type { Intersection } from 'three';

import type { HouseModel, Opening, Pt } from '@garh/model';

import { WORLD_UNITS_PER_MM, type PickKind, type PickTarget } from '../../../features/canvas/core';
import {
  balconiesOfStorey,
  columnRingMm,
  columnsOfStorey,
  openingSymbol,
  openingsOfStorey,
  roomsOfStorey,
  stairSymbol,
  stairsOfStorey,
  triangleVerticesMm,
  wallRuns,
  wallSpanQuadF,
  wallsOfStorey,
  type PtF,
} from './planGeometry';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** A merged mesh: interleaved world-space vertices plus one id per triangle. */
export interface MergedFaces {
  readonly positions: Float32Array;
  /** `faceIds[faceIndex]` — the element the raycast landed on. */
  readonly faceIds: readonly string[];
}

export const EMPTY_FACES: MergedFaces = { positions: new Float32Array(0), faceIds: [] };

/** A flat mm triangle list for one element. */
export interface FaceItem {
  readonly id: string;
  /** `[x,y, x,y, x,y, …]`, three points per triangle, float mm. */
  readonly tris: readonly number[];
}

// ---------------------------------------------------------------------------
// Packing — the one mm → world door
// ---------------------------------------------------------------------------

/**
 * Pack triangles into a world-space position buffer.
 *
 * `tris` is a flat list of float MILLIMETRES, three points per triangle. This
 * is where millimetres become world units, and it is a multiply by
 * `WORLD_UNITS_PER_MM` with the model's Y going to world −Z.
 */
export function packTriangles(items: readonly FaceItem[], elevationMm: number): MergedFaces {
  let vertexCount = 0;
  for (const item of items) vertexCount += item.tris.length / 2;
  if (vertexCount === 0) return EMPTY_FACES;

  const positions = new Float32Array(vertexCount * 3);
  const faceIds: string[] = new Array<string>(vertexCount / 3);
  const worldY = elevationMm * WORLD_UNITS_PER_MM;

  let v = 0;
  let f = 0;
  for (const item of items) {
    for (let i = 0; i + 1 < item.tris.length; i += 2) {
      positions[v] = (item.tris[i] as number) * WORLD_UNITS_PER_MM;
      positions[v + 1] = worldY;
      positions[v + 2] = -(item.tris[i + 1] as number) * WORLD_UNITS_PER_MM;
      v += 3;
    }
    const faces = item.tris.length / 6;
    for (let i = 0; i < faces; i += 1) {
      faceIds[f] = item.id;
      f += 1;
    }
  }
  return { positions, faceIds };
}

/** Quad → two triangles, as a flat mm list. Ring order, not strip order. */
export function quadTris(quad: readonly PtF[]): number[] {
  const [a, b, c, d] = quad;
  if (a === undefined || b === undefined || c === undefined || d === undefined) return [];
  return [a.x, a.y, b.x, b.y, c.x, c.y, a.x, a.y, c.x, c.y, d.x, d.y];
}

/** Pack `[from, to]` segments into a `LineSegments` position buffer. */
export function packSegments(
  segments: readonly (readonly [PtF, PtF])[],
  elevationMm: number,
): Float32Array {
  const positions = new Float32Array(segments.length * 6);
  const worldY = elevationMm * WORLD_UNITS_PER_MM;
  let v = 0;
  for (const [from, to] of segments) {
    positions[v] = from.x * WORLD_UNITS_PER_MM;
    positions[v + 1] = worldY;
    positions[v + 2] = -from.y * WORLD_UNITS_PER_MM;
    positions[v + 3] = to.x * WORLD_UNITS_PER_MM;
    positions[v + 4] = worldY;
    positions[v + 5] = -to.y * WORLD_UNITS_PER_MM;
    v += 6;
  }
  return positions;
}

/** Polyline → the segment pairs a `LineSegments` wants. */
export function polylineSegments(points: readonly PtF[], closed: boolean): (readonly [PtF, PtF])[] {
  const out: (readonly [PtF, PtF])[] = [];
  for (let i = 0; i + 1 < points.length; i += 1) {
    out.push([points[i] as PtF, points[i + 1] as PtF] as const);
  }
  if (closed && points.length > 2) {
    out.push([points[points.length - 1] as PtF, points[0] as PtF] as const);
  }
  return out;
}

// ---------------------------------------------------------------------------
// The resolver — what the registry runs
// ---------------------------------------------------------------------------

/**
 * The pick resolver for one merged layer.
 *
 * Two triangles per quad and one id per triangle, so `faceIndex` IS the
 * lookup — no arithmetic to get wrong when a wall with three openings
 * contributes four quads and the next wall contributes one. `PlanScene`
 * registers exactly this function; the click test runs exactly this function.
 */
export function faceResolver(
  faces: MergedFaces,
  kind: PickKind,
  storeyId: string | null,
): (intersection: Intersection) => PickTarget | null {
  const ids = faces.faceIds;
  return (intersection: Intersection): PickTarget | null => {
    const faceIndex = intersection.faceIndex;
    if (faceIndex === undefined || faceIndex === null) return null;
    const id = ids[faceIndex];
    return id === undefined ? null : { kind, id, storeyId };
  };
}

// ---------------------------------------------------------------------------
// Builders — one per element family, each a pure function of the document
// ---------------------------------------------------------------------------

/** Walls: poché with the openings genuinely cut out. */
export function buildWallFaces(
  house: HouseModel,
  storeyId: string | null,
  elevationMm: number,
): MergedFaces {
  const walls = wallsOfStorey(house, storeyId);
  if (walls.length === 0) return EMPTY_FACES;
  const byWall = new Map<string, Opening[]>();
  for (const opening of house.openings) {
    const list = byWall.get(opening.wallId);
    if (list) list.push(opening);
    else byWall.set(opening.wallId, [opening]);
  }
  const items: FaceItem[] = [];
  for (const wall of walls) {
    const tris: number[] = [];
    for (const run of wallRuns(wall, byWall.get(wall.id) ?? [])) {
      const quad = wallSpanQuadF(wall, run.startMm, run.endMm);
      if (quad !== null) tris.push(...quadTris(quad));
    }
    if (tris.length > 0) items.push({ id: wall.id, tris });
  }
  return packTriangles(items, elevationMm);
}

/** Walls again, as outlines, so joints and thin partitions read. */
export function buildWallOutlines(
  house: HouseModel,
  storeyId: string | null,
  elevationMm: number,
): Float32Array {
  const segments: (readonly [PtF, PtF])[] = [];
  for (const wall of wallsOfStorey(house, storeyId)) {
    const quad = wallSpanQuadF(wall, 0, Math.hypot(wall.b.x - wall.a.x, wall.b.y - wall.a.y));
    if (quad !== null) segments.push(...polylineSegments(quad, true));
  }
  return packSegments(segments, elevationMm);
}

/** Rooms: the wash that makes a plan readable at a glance. */
export function buildRoomFaces(
  house: HouseModel,
  storeyId: string | null,
  elevationMm: number,
): MergedFaces {
  const items = roomsOfStorey(house, storeyId)
    .map((room) => ({ id: room.id, tris: triangleVerticesMm(room.polygon) }))
    .filter((item) => item.tris.length > 0);
  return packTriangles(items, elevationMm);
}

/** Openings: the reveal is the pick target, the symbol is the drawing. */
export function buildOpeningBuffers(
  house: HouseModel,
  storeyId: string | null,
  elevationMm: number,
): { readonly faces: MergedFaces; readonly symbols: Float32Array } {
  const pairs = openingsOfStorey(house, storeyId);
  const reveals: FaceItem[] = [];
  const segments: (readonly [PtF, PtF])[] = [];

  for (const { opening, wall } of pairs) {
    const symbol = openingSymbol(wall, opening);
    if (symbol === null) continue;
    const ring: readonly Pt[] = symbol.ringMm;
    if (ring.length === 4) reveals.push({ id: opening.id, tris: quadTris(ring) });
    segments.push(...symbol.lines);
    segments.push(...polylineSegments(symbol.arc, false));
  }

  return {
    faces: packTriangles(reveals, elevationMm),
    symbols: packSegments(segments, elevationMm),
  };
}

/** Stairs: footprint (pickable) plus riser lines and the UP arrow. */
export function buildStairBuffers(
  house: HouseModel,
  storeyId: string | null,
  elevationMm: number,
): { readonly faces: MergedFaces; readonly lines: Float32Array } {
  const faces: FaceItem[] = [];
  const segments: (readonly [PtF, PtF])[] = [];
  for (const stair of stairsOfStorey(house, storeyId)) {
    const symbol = stairSymbol(stair);
    const tris = triangleVerticesMm(symbol.ringMm);
    if (tris.length > 0) faces.push({ id: stair.id, tris });
    segments.push(...polylineSegments(symbol.ringMm, true));
    for (const tread of symbol.treads) segments.push(tread);
    for (const edge of symbol.edges) segments.push(edge);
    segments.push(...polylineSegments(symbol.arrow, false));
    for (const barb of symbol.arrowHead) segments.push(barb);
  }
  return { faces: packTriangles(faces, elevationMm), lines: packSegments(segments, elevationMm) };
}

/** Balconies: slab fill (pickable) plus the ring. */
export function buildBalconyBuffers(
  house: HouseModel,
  storeyId: string | null,
  elevationMm: number,
): { readonly faces: MergedFaces; readonly lines: Float32Array } {
  const faces: FaceItem[] = [];
  const segments: (readonly [PtF, PtF])[] = [];
  for (const balcony of balconiesOfStorey(house, storeyId)) {
    const tris = triangleVerticesMm(balcony.polygon);
    if (tris.length > 0) faces.push({ id: balcony.id, tris });
    segments.push(...polylineSegments(balcony.polygon, true));
  }
  return { faces: packTriangles(faces, elevationMm), lines: packSegments(segments, elevationMm) };
}

/**
 * Columns: the footprint (pickable) plus the drafting cross that reads
 * "column" rather than "small room" at 1:100.
 */
export function buildColumnBuffers(
  house: HouseModel,
  storeyId: string | null,
  elevationMm: number,
): { readonly faces: MergedFaces; readonly lines: Float32Array } {
  const faces: FaceItem[] = [];
  const segments: (readonly [PtF, PtF])[] = [];
  for (const column of columnsOfStorey(house, storeyId)) {
    const ring = columnRingMm(column);
    const tris = triangleVerticesMm(ring);
    if (tris.length > 0) faces.push({ id: column.id, tris });
    const [a, b, c, d] = ring;
    if (a !== undefined && b !== undefined && c !== undefined && d !== undefined) {
      segments.push([a, c] as const, [b, d] as const);
    }
  }
  return { faces: packTriangles(faces, elevationMm), lines: packSegments(segments, elevationMm) };
}
