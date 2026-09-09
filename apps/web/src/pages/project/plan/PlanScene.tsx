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
 * a parallel array built at the same time as the vertices. That resolver is
 * `faceResolver` in `planBuffers.ts`, and so are the buffer builders — pure
 * functions, so `planBuffers.test.ts` can build what this component draws,
 * register it with the real `PickRegistry`, and CLICK it (CLAUDE.md bug 4: a
 * layer that believes it is registered has no compile-time tell).
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
 *
 */

import { useEffect, useMemo } from 'react';
import { BufferAttribute, BufferGeometry, type Material } from 'three';

import type { HouseModel } from '@garh/model';

import {
  LAYER_RENDER_ORDER,
  useCanvasCore,
  usePickableResolver,
  type CanvasLayer,
  type PickKind,
} from '../../../features/canvas/core';
import { CoPresenceLayer } from '../../../features/canvas/copresence';
import {
  buildBalconyBuffers,
  buildColumnBuffers,
  buildOpeningBuffers,
  buildRoomFaces,
  buildStairBuffers,
  buildWallFaces,
  buildWallOutlines,
  faceResolver,
  type MergedFaces,
} from './planBuffers';
import { getPlanMaterials } from './planMaterials';

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

  const resolver = useMemo(() => faceResolver(faces, kind, storeyId), [faces, kind, storeyId]);

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
  const wallFaces = useMemo(
    () => buildWallFaces(house, storeyId, elevationMm),
    [house, storeyId, elevationMm],
  );

  // ── rooms: the wash that makes a plan readable at a glance ───────────────
  const roomFaces = useMemo(
    () => buildRoomFaces(house, storeyId, elevationMm),
    [house, storeyId, elevationMm],
  );

  // ── openings: the reveal is the pick target, the symbol is the drawing ───
  const openings = useMemo(
    () => buildOpeningBuffers(house, storeyId, elevationMm),
    [house, storeyId, elevationMm],
  );

  // ── walls again, as outlines, so joints and thin partitions read ─────────
  const wallOutlinePositions = useMemo(
    () => buildWallOutlines(house, storeyId, elevationMm),
    [house, storeyId, elevationMm],
  );

  // ── stairs, balconies, columns ───────────────────────────────────────────
  const stairs = useMemo(
    () => buildStairBuffers(house, storeyId, elevationMm),
    [house, storeyId, elevationMm],
  );
  const balconies = useMemo(
    () => buildBalconyBuffers(house, storeyId, elevationMm),
    [house, storeyId, elevationMm],
  );
  const columns = useMemo(
    () => buildColumnBuffers(house, storeyId, elevationMm),
    [house, storeyId, elevationMm],
  );

  // `frameloop="demand"`: geometry that changed is not geometry that was
  // drawn. R3F invalidates on its own commit, but the storey switch path can
  // change only the memo inputs, so ask explicitly.
  useEffect(() => {
    core.invalidate();
  }, [core, wallFaces, roomFaces, openings, stairs, balconies, columns]);

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
        faces={balconies.faces}
        kind="balcony"
        storeyId={storeyId}
        layer="balcony"
        material={materials.balconyFill}
      />
      <LineLayer positions={balconies.lines} layer="balcony" material={materials.symbolLine} />
      <MergedLayer
        faces={wallFaces}
        kind="wall"
        storeyId={storeyId}
        layer="wall"
        material={materials.wallFill}
      />
      <LineLayer positions={wallOutlinePositions} layer="wall" material={materials.wallLine} />
      <MergedLayer
        faces={openings.faces}
        kind="opening"
        storeyId={storeyId}
        layer="opening"
        material={materials.openingFill}
      />
      <LineLayer positions={openings.symbols} layer="opening" material={materials.symbolLine} />
      <MergedLayer
        faces={stairs.faces}
        kind="stair"
        storeyId={storeyId}
        layer="stair"
        material={materials.structureFill}
      />
      <LineLayer positions={stairs.lines} layer="stair" material={materials.symbolLine} />
      <MergedLayer
        faces={columns.faces}
        kind="column"
        storeyId={storeyId}
        layer="column"
        material={materials.structureFill}
      />
      <LineLayer positions={columns.lines} layer="column" material={materials.symbolLine} />

      {/* ── co-presence ────────────────────────────────────────────────────
          Live collaborator cursors and canvas-pinned comments. Contributes no
          geometry and returns null into this tree: it mounts a DOM overlay
          beside the canvas, because a comment pin is chrome (clickable, with a
          tooltip and a link into the comments panel) and a cursor must never be
          a pick target at all. See `features/canvas/copresence` for the full
          picking argument — neither layer touches the PickRegistry, and both
          say so on purpose. */}
      <CoPresenceLayer house={house} storeyId={storeyId} elevationMm={elevationMm} />
    </group>
  );
}
