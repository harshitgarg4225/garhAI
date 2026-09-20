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
 * ════════════════════════════════════════════════════════════════════════════
 * HATCHES
 * ════════════════════════════════════════════════════════════════════════════
 * A wall whose surface is bound to a material (op 29) or hand-picked in the
 * hatch panel is drawn the way the SHEET will poché it: the same line families
 * the drawings service generates, at the sheet's own density, clipped to the
 * wall's solid runs. `planHatch.ts` owns that derivation; an unbound wall
 * keeps the flat ink poché, so the pattern appears exactly where a binding was
 * made. Hatched walls are a second merged mesh with the same pick resolver.
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
import type { HatchOverrides } from '../../../features/hatchpicker';
import type { MaterialItem } from '../../../lib/schemas';
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
import { buildWallHatch, EMPTY_HATCH } from './planHatch';
import { getPlanMaterials } from './planMaterials';
import { publishPlanGeometryStats } from './planProbe';

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
  /**
   * What the hatch panel decided, so a bound wall is drawn the way the sheet
   * will poché it. Omitted (a preview, a share view) = every wall flat.
   */
  readonly hatch?:
    | {
        readonly catalog: ReadonlyMap<string, MaterialItem>;
        readonly overrides: HatchOverrides;
      }
    | undefined;
}

export function PlanScene({
  house,
  storeyId,
  elevationMm,
  showRooms = true,
  hatch,
}: PlanSceneProps): JSX.Element {
  const core = useCanvasCore();
  const materials = getPlanMaterials();

  // ── hatches: which walls the sheet will pattern, and the lines it draws ──
  const hatched = useMemo(() => {
    if (hatch === undefined || storeyId === null) return EMPTY_HATCH;
    return buildWallHatch({ house, storeyId, catalog: hatch.catalog, overrides: hatch.overrides });
  }, [house, storeyId, hatch]);

  // ── walls: poché with the openings genuinely cut out ─────────────────────
  // Two merged meshes when anything is hatched: the patterned walls wear a
  // faint wash under their linework, the rest keep the ink poché. Both come
  // from the same builder and register the same resolver, so a hatched wall
  // is exactly as clickable as a flat one.
  const { wallFaces, hatchedWallFaces } = useMemo(() => {
    if (hatched.wallIds.size === 0) {
      return { wallFaces: buildWallFaces(house, storeyId, elevationMm), hatchedWallFaces: null };
    }
    const flat = { ...house, walls: house.walls.filter((w) => !hatched.wallIds.has(w.id)) };
    const patterned = { ...house, walls: house.walls.filter((w) => hatched.wallIds.has(w.id)) };
    return {
      wallFaces: buildWallFaces(flat, storeyId, elevationMm),
      hatchedWallFaces: buildWallFaces(patterned, storeyId, elevationMm),
    };
  }, [house, storeyId, elevationMm, hatched]);

  const hatchLinePositions = useMemo(
    () => hatched.linePositions(elevationMm),
    [hatched, elevationMm],
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
    // What was built, for the dev test handle. Once per geometry rebuild, in
    // the effect that already runs then — never per frame. See `planProbe`.
    publishPlanGeometryStats({
      hatchLineVertices: hatchLinePositions.length / 3,
      wallFaceVertices:
        wallFaces.positions.length / 3 + (hatchedWallFaces?.positions.length ?? 0) / 3,
      hatchedWallCount: hatched.wallIds.size,
    });
  }, [
    core,
    wallFaces,
    hatchedWallFaces,
    hatchLinePositions,
    hatched,
    roomFaces,
    openings,
    stairs,
    balconies,
    columns,
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
      {hatchedWallFaces === null ? null : (
        <MergedLayer
          faces={hatchedWallFaces}
          kind="wall"
          storeyId={storeyId}
          layer="wall"
          material={materials.hatchedWallFill}
        />
      )}
      <LineLayer positions={hatchLinePositions} layer="wall" material={materials.hatchLine} />
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
