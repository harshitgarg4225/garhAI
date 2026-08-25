/**
 * PlanScene — the drawing itself.
 *
 * Walls, openings, room washes, stairs, balconies and columns for ONE storey,
 * rendered from the folded document. Dimensions, room tags, compliance markers
 * and furniture are other people's layers and are composed alongside this one
 * by `PlanPage`.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * BATCHING, AND WHY PICKING STILL WORKS (§12 + §14)
 * ════════════════════════════════════════════════════════════════════════════
 * Every element family is ONE merged, non-indexed `BufferGeometry`: one draw
 * call for all the walls, one for all the room washes, one for the opening
 * reveals, one for the symbol linework. A G+2 storey is therefore about eight
 * draw calls for the plan, not eight hundred.
 *
 * Batching normally costs you picking, because a merged mesh has one identity.
 * The canvas core's `usePickableResolver` is the way out: the resolver is handed
 * the raycast `Intersection`, reads `faceIndex`, and looks the element id up in
 * a parallel array built at the same time as the vertices. Two triangles per
 * quad, so `faceIds[faceIndex]` is the answer with no arithmetic to get wrong.
 *
 * This is the §12 "one hit-testing system" rule taken seriously: there is no
 * react-three-fiber pointer handler anywhere in this file, and Phase 5 inherits
 * the same resolvers when it points a perspective camera at the same meshes.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE FRAME BUDGET
 * ════════════════════════════════════════════════════════════════════════════
 * Nothing here runs during a pan or a zoom. Geometry is rebuilt only when the
 * document, the storey or the theme changes — `useMemo` on `house` object
 * identity, which the model store replaces exactly once per op group. Buffers
 * are allocated in the memo and disposed when it is superseded; there is no
 * per-frame allocation and no `useFrame` in this file at all.
 *
 * Storey switching is a re-memo, not a re-mount, and the meshes for the storeys
 * you are not looking at are simply not built — which is the honest version of
 * §15's "switching storeys instant". Pre-building every storey's meshes would
 * be faster still and is the obvious next step if a G+3 ever feels slow.
 */

import { useEffect, useMemo } from 'react';
import { BufferAttribute, BufferGeometry, type Intersection, type Material } from 'three';

import type { HouseModel, Opening, Pt } from '@garh/model';

import {
  LAYER_RENDER_ORDER,
  WORLD_UNITS_PER_MM,
  useCanvasCore,
  usePickableResolver,
  type CanvasLayer,
  type PickKind,
  type PickTarget,
} from '../../../features/canvas/core';
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
import { getPlanMaterials } from './planMaterials';

// ---------------------------------------------------------------------------
// Buffer builders — pure, allocate once, no three.js types in the maths
// ---------------------------------------------------------------------------

/** A merged mesh: interleaved world-space vertices plus one id per triangle. */
interface MergedFaces {
  readonly positions: Float32Array;
  /** `faceIds[faceIndex]` — the element the raycast landed on. */
  readonly faceIds: readonly string[];
}

const EMPTY_FACES: MergedFaces = { positions: new Float32Array(0), faceIds: [] };

/**
 * Pack triangles into a world-space position buffer.
 *
 * `tris` is a flat list of float MILLIMETRES, `[x,y, x,y, x,y, …]`, three
 * points per triangle. This function is the only place in the plan scene where
 * millimetres become world units, and it is a multiply by `WORLD_UNITS_PER_MM`
 * with the model's Y going to world −Z, exactly as `coords.ts` defines it.
 */
function packTriangles(
  items: readonly { readonly id: string; readonly tris: readonly number[] }[],
  elevationMm: number,
): MergedFaces {
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
function quadTris(quad: readonly PtF[]): number[] {
  const [a, b, c, d] = quad;
  if (a === undefined || b === undefined || c === undefined || d === undefined) return [];
  return [a.x, a.y, b.x, b.y, c.x, c.y, a.x, a.y, c.x, c.y, d.x, d.y];
}

/** Pack `[from, to]` segments into a `LineSegments` position buffer. */
function packSegments(
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
function polylineSegments(points: readonly PtF[], closed: boolean): (readonly [PtF, PtF])[] {
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
// Geometry lifecycle
// ---------------------------------------------------------------------------

/**
 * A `BufferGeometry` that lives exactly as long as the buffer it wraps.
 *
 * The disposal is the point. `useMemo` alone would leak one GPU buffer per
 * edit — on a session with a few hundred ops that is real memory, and the
 * symptom (a tab that gets slower the longer you draw) is miserable to
 * diagnose after the fact.
 */
function useGeometry(positions: Float32Array, itemSize = 3): BufferGeometry {
  const geometry = useMemo(() => {
    const g = new BufferGeometry();
    g.setAttribute('position', new BufferAttribute(positions, itemSize));
    // Guarded: `computeBoundingSphere` on a zero-vertex attribute produces a
    // NaN radius and three logs a warning for every empty layer — which is
    // every layer on a brand-new project.
    if (positions.length > 0) g.computeBoundingSphere();
    return g;
  }, [positions, itemSize]);

  useEffect(() => () => geometry.dispose(), [geometry]);
  return geometry;
}

// ---------------------------------------------------------------------------
// One batched, pickable layer
// ---------------------------------------------------------------------------

interface MergedLayerProps {
  readonly faces: MergedFaces;
  readonly kind: PickKind;
  readonly storeyId: string | null;
  readonly layer: CanvasLayer;
  readonly material: Material;
  readonly visible?: boolean | undefined;
}

function MergedLayer({
  faces,
  kind,
  storeyId,
  layer,
  material,
  visible = true,
}: MergedLayerProps): JSX.Element | null {
  const geometry = useGeometry(faces.positions);

  const resolver = useMemo(() => {
    const ids = faces.faceIds;
    return (intersection: Intersection): PickTarget | null => {
      // Two triangles per quad and one id per triangle, so `faceIndex` IS the
      // lookup — no arithmetic to get wrong when a wall with three openings
      // contributes four quads and the next wall contributes one.
      const faceIndex = intersection.faceIndex;
      if (faceIndex === undefined) return null;
      const id = ids[faceIndex];
      return id === undefined ? null : { kind, id, storeyId };
    };
  }, [faces.faceIds, kind, storeyId]);

  const pickRef = usePickableResolver(visible ? resolver : null);

  if (faces.faceIds.length === 0) return null;
  return (
    <mesh
      ref={pickRef}
      geometry={geometry}
      material={material}
      renderOrder={LAYER_RENDER_ORDER[layer]}
      visible={visible}
      frustumCulled={false}
    />
  );
}

/** Non-pickable linework: symbols, hairlines. One draw call per layer. */
function LineLayer({
  positions,
  layer,
  material,
  visible = true,
}: {
  readonly positions: Float32Array;
  readonly layer: CanvasLayer;
  readonly material: Material;
  readonly visible?: boolean | undefined;
}): JSX.Element | null {
  const geometry = useGeometry(positions);
  if (positions.length === 0) return null;
  return (
    <lineSegments
      geometry={geometry}
      material={material}
      renderOrder={LAYER_RENDER_ORDER[layer]}
      visible={visible}
      frustumCulled={false}
    />
  );
}

// ---------------------------------------------------------------------------
// The scene
// ---------------------------------------------------------------------------

export interface PlanSceneProps {
  readonly house: HouseModel;
  readonly storeyId: string | null;
  /** Finished floor level of that storey, mm. Everything is drawn on it. */
  readonly elevationMm: number;
  /** Draw the room washes. Off with the room-tag layer. */
  readonly showRooms?: boolean | undefined;
}

export function PlanScene({
  house,
  storeyId,
  elevationMm,
  showRooms = true,
}: PlanSceneProps): JSX.Element {
  const core = useCanvasCore();
  const materials = getPlanMaterials();

  // ── walls: poché with the openings genuinely cut out ─────────────────────
  const wallFaces = useMemo<MergedFaces>(() => {
    const walls = wallsOfStorey(house, storeyId);
    if (walls.length === 0) return EMPTY_FACES;
    const byWall = new Map<string, Opening[]>();
    for (const opening of house.openings) {
      const list = byWall.get(opening.wallId);
      if (list) list.push(opening);
      else byWall.set(opening.wallId, [opening]);
    }
    const items: { id: string; tris: number[] }[] = [];
    for (const wall of walls) {
      const tris: number[] = [];
      for (const run of wallRuns(wall, byWall.get(wall.id) ?? [])) {
        const quad = wallSpanQuadF(wall, run.startMm, run.endMm);
        if (quad !== null) tris.push(...quadTris(quad));
      }
      if (tris.length > 0) items.push({ id: wall.id, tris });
    }
    return packTriangles(items, elevationMm);
  }, [house, storeyId, elevationMm]);

  // ── rooms: the wash that makes a plan readable at a glance ───────────────
  const roomFaces = useMemo<MergedFaces>(() => {
    const items = roomsOfStorey(house, storeyId)
      .map((room) => ({ id: room.id, tris: triangleVerticesMm(room.polygon) }))
      .filter((item) => item.tris.length > 0);
    return packTriangles(items, elevationMm);
  }, [house, storeyId, elevationMm]);

  // ── openings: the reveal is the pick target, the symbol is the drawing ───
  const { openingFaces, symbolPositions } = useMemo(() => {
    const pairs = openingsOfStorey(house, storeyId);
    const reveals: { id: string; tris: number[] }[] = [];
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
      openingFaces: packTriangles(reveals, elevationMm),
      symbolPositions: packSegments(segments, elevationMm),
    };
  }, [house, storeyId, elevationMm]);

  // ── walls again, as outlines, so joints and thin partitions read ─────────
  const wallOutlinePositions = useMemo(() => {
    const segments: (readonly [PtF, PtF])[] = [];
    for (const wall of wallsOfStorey(house, storeyId)) {
      const quad = wallSpanQuadF(wall, 0, Math.hypot(wall.b.x - wall.a.x, wall.b.y - wall.a.y));
      if (quad !== null) segments.push(...polylineSegments(quad, true));
    }
    return packSegments(segments, elevationMm);
  }, [house, storeyId, elevationMm]);

  // ── stairs ───────────────────────────────────────────────────────────────
  const { stairFaces, stairLinePositions } = useMemo(() => {
    const faces: { id: string; tris: number[] }[] = [];
    const segments: (readonly [PtF, PtF])[] = [];
    for (const stair of stairsOfStorey(house, storeyId)) {
      const symbol = stairSymbol(stair);
      const tris = triangleVerticesMm(symbol.ringMm);
      if (tris.length > 0) faces.push({ id: stair.id, tris });
      segments.push(...polylineSegments(symbol.ringMm, true));
      for (const tread of symbol.treads) segments.push(tread);
      segments.push(symbol.arrow);
    }
    return {
      stairFaces: packTriangles(faces, elevationMm),
      stairLinePositions: packSegments(segments, elevationMm),
    };
  }, [house, storeyId, elevationMm]);

  // ── balconies ────────────────────────────────────────────────────────────
  const { balconyFaces, balconyLinePositions } = useMemo(() => {
    const faces: { id: string; tris: number[] }[] = [];
    const segments: (readonly [PtF, PtF])[] = [];
    for (const balcony of balconiesOfStorey(house, storeyId)) {
      const tris = triangleVerticesMm(balcony.polygon);
      if (tris.length > 0) faces.push({ id: balcony.id, tris });
      segments.push(...polylineSegments(balcony.polygon, true));
    }
    return {
      balconyFaces: packTriangles(faces, elevationMm),
      balconyLinePositions: packSegments(segments, elevationMm),
    };
  }, [house, storeyId, elevationMm]);

  // ── columns ──────────────────────────────────────────────────────────────
  const columnFaces = useMemo<MergedFaces>(() => {
    const items = columnsOfStorey(house, storeyId)
      .map((column) => ({ id: column.id, tris: triangleVerticesMm(columnRingMm(column)) }))
      .filter((item) => item.tris.length > 0);
    return packTriangles(items, elevationMm);
  }, [house, storeyId, elevationMm]);

  // `frameloop="demand"`: geometry that changed is not geometry that was
  // drawn. R3F invalidates on its own commit, but the storey switch path can
  // change only the memo inputs, so ask explicitly.
  useEffect(() => {
    core.invalidate();
  }, [
    core,
    wallFaces,
    roomFaces,
    openingFaces,
    stairFaces,
    balconyFaces,
    columnFaces,
    symbolPositions,
  ]);

  return (
    <group name="plan">
      <MergedLayer
        faces={roomFaces}
        kind="room"
        storeyId={storeyId}
        layer="roomFill"
        material={materials.roomFill}
        visible={showRooms}
      />
      <MergedLayer
        faces={balconyFaces}
        kind="balcony"
        storeyId={storeyId}
        layer="balcony"
        material={materials.balconyFill}
      />
      <LineLayer
        positions={balconyLinePositions}
        layer="balcony"
        material={materials.symbolLine}
      />
      <MergedLayer
        faces={wallFaces}
        kind="wall"
        storeyId={storeyId}
        layer="wall"
        material={materials.wallFill}
      />
      <LineLayer positions={wallOutlinePositions} layer="wall" material={materials.wallLine} />
      <MergedLayer
        faces={openingFaces}
        kind="opening"
        storeyId={storeyId}
        layer="opening"
        material={materials.openingFill}
      />
      <LineLayer positions={symbolPositions} layer="opening" material={materials.symbolLine} />
      <MergedLayer
        faces={stairFaces}
        kind="stair"
        storeyId={storeyId}
        layer="stair"
        material={materials.structureFill}
      />
      <LineLayer positions={stairLinePositions} layer="stair" material={materials.symbolLine} />
      <MergedLayer
        faces={columnFaces}
        kind="column"
        storeyId={storeyId}
        layer="column"
        material={materials.structureFill}
      />
    </group>
  );
}
