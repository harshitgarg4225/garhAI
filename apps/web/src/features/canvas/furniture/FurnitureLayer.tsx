/**
 * Furniture in the scene — ONE path, used by the 2D plan and by Phase 5's 3D.
 *
 * ## The picking rule (§12: "shared raycast hit-testing")
 *
 * This layer builds no picker. It registers its instanced mesh with the canvas
 * core's `PickRegistry` and the core's single raycaster does the rest:
 *
 * ```ts
 * core.registry.register(mesh, (intersection) => ({ kind: 'furniture', id, storeyId }));
 * ```
 *
 * REGISTRATION IS THE INTEGRATION, and it is not optional. `hitTest.pickAt`
 * raycasts `registry.objects()` — a flat list of registered objects, walked
 * non-recursively. A mesh that is merely *in the scene* is never tested. An
 * earlier version of this file tagged the mesh with `userData.garhPick` and
 * assumed a `intersectObjects(scene.children, true)` on the other side; the
 * core does no such thing, so furniture was invisible to every click, hover and
 * marquee. The tag is still written (it is a useful debugging label and Phase 5
 * may want it), but it is NOT the contract — {@link useCanvasCoreOptional} is.
 *
 * There is no plan-space hit test to throw away when the perspective camera
 * arrives — the boxes an orthographic top-down camera picks are the same boxes
 * an orbit camera picks, at the same millimetre dimensions, because the 2D view
 * is a camera choice rather than a second renderer. §12 names building a
 * 2D-only picking path as the mistake to avoid; not having one is how this
 * avoids it.
 *
 * `geometry.ts` does carry analytic overlap tests, but they answer "do these
 * two rectangles collide" for the advisory pass. They are never wired to a
 * pointer event, and they behave identically under either camera.
 *
 * ## Frame budget (§14: <16 ms during pan/zoom on the G+2 demo)
 *
 * - Placed furniture is ONE `InstancedMesh` (one draw call, one geometry, one
 *   material) plus ONE `LineSegments` carrying every outline. Both rebuild only
 *   when the furniture set, the catalogue or the axis mapping changes — never
 *   per frame, never on a pointer move.
 * - There is no `useFrame` in this file. Panning and zooming move the camera;
 *   these buffers are already on the GPU and are not touched.
 * - The preview subscribes to the placement controller's IMPERATIVE channel and
 *   writes straight into `mesh.position` / `.quaternion` / `.scale`. No React
 *   state changes while the cursor moves, so nothing re-renders.
 * - Module-scope scratch objects mean both paths allocate nothing.
 *
 * ## Materials
 *
 * Unlit (`meshBasicMaterial`) on purpose. A plan view wants flat fills, and —
 * more practically — this feature does not own the scene's lighting rig, so a
 * shaded material would render the entire catalogue black in any scene that has
 * not added lights yet. Phase 5 swaps these boxes for real assets with real
 * materials anyway (see `proxyMesh.ts`), and that is the right place for the
 * lighting conversation.
 *
 * ## Millimetres in, scene units out
 *
 * Every number crossing into Three.js goes through `sceneAxes.ts`, and nothing
 * computed here travels back towards an op.
 */

import { useEffect, useMemo, useRef } from 'react';
import { useThree } from '@react-three/fiber';
import {
  Color,
  DoubleSide,
  type InstancedMesh,
  Matrix4,
  type Mesh,
  type MeshBasicMaterial,
  Quaternion,
  Vector3,
} from 'three';

import { useSelectionStore } from '../../../stores/selection';
import { useCanvasCoreOptional, type PickTarget } from '../core';
import type { PlacementPoseState } from './placement';
import {
  buildBoxInstances,
  buildEdgePositions,
  CATEGORY_COLOR,
  CLEARANCE_COLOR,
  CLEARANCE_OPACITY,
  instanceScale,
  PREVIEW_COLOR,
  type BoxInstance,
} from './render';
import {
  DEFAULT_PLAN_AXES,
  DEFAULT_SCENE_UNITS_PER_MM,
  scenePosition,
  sceneScale,
  sceneUpAxis,
  type PlanAxes,
} from './sceneAxes';
import { useFurniturePlacement } from './useFurniturePlacement';

/**
 * The tag the shared raycaster reads. Put it on every pickable object this
 * feature adds; Phase 5 should tag walls, openings and stairs the same way so
 * ONE resolver handles every element type.
 */
export interface GarhPick {
  readonly kind: 'furniture';
  /** Instanced meshes pass `instanceId`; single meshes pass nothing. */
  readonly idAt: (instanceId?: number) => string | null;
}

// Scratch objects, reused for every matrix write.
const SCRATCH_MATRIX = new Matrix4();
const SCRATCH_POS = new Vector3();
const SCRATCH_QUAT = new Quaternion();
const SCRATCH_SCALE = new Vector3();
const SCRATCH_AXIS = new Vector3();
const SCRATCH_COLOR = new Color();

const DEG_TO_RAD = Math.PI / 180;
/** Instance capacity grows in powers of two so the mesh is rarely recreated. */
const MIN_CAPACITY = 32;
/** The clearance slab is 1 mm thick: a floor marking, not an object. */
const STRIP_THICKNESS_MM = 1;

export interface FurnitureLayerProps {
  /** Scene units per millimetre. 1 = the scene works in mm (see `sceneAxes`). */
  readonly sceneUnitsPerMm?: number | undefined;
  /** Which way is up in the shared scene. See `sceneAxes.ts`. */
  readonly axes?: PlanAxes | undefined;
  /** Draw every placed item's access strip, not only the preview's. */
  readonly showAllClearances?: boolean | undefined;
  /** Floor level of the active storey, in millimetres. */
  readonly floorLevelMm?: number | undefined;
}

export function FurnitureLayer({
  sceneUnitsPerMm = DEFAULT_SCENE_UNITS_PER_MM,
  axes = DEFAULT_PLAN_AXES,
  showAllClearances = false,
  floorLevelMm = 0,
}: FurnitureLayerProps): JSX.Element {
  const { placed, controller, activeStoreyId } = useFurniturePlacement();
  const selectedIds = useSelectionStore((s) => s.ids);
  const { invalidate } = useThree();
  // Optional so the layer still renders in a scene that has no canvas core
  // (a storybook, a future export renderer). Picking is simply off there —
  // which is honest, and is not the same thing as picking silently not working.
  const core = useCanvasCoreOptional();

  const meshRef = useRef<InstancedMesh>(null);
  const previewRef = useRef<Mesh>(null);
  const previewMaterialRef = useRef<MeshBasicMaterial>(null);
  const clearanceRef = useRef<Mesh>(null);

  const instances = useMemo(() => buildBoxInstances(placed).instances, [placed]);

  // Capacity is a render-time decision: changing `args` remounts the mesh, so
  // it must be stable across ordinary edits. Powers of two mean adding a chair
  // to a 30-item plan reuses the same GPU buffer.
  const capacity = useMemo(() => {
    let cap = MIN_CAPACITY;
    while (cap < instances.length) cap *= 2;
    return cap;
  }, [instances.length]);

  const edgePositions = useMemo(
    () => buildEdgePositions(instances, axes, sceneUnitsPerMm),
    [instances, axes, sceneUnitsPerMm],
  );

  const selected = useMemo(() => new Set(selectedIds), [selectedIds]);

  // ── upload instance matrices + colours ──────────────────────────────────
  useEffect(() => {
    const mesh = meshRef.current;
    if (mesh === null) return;

    for (let i = 0; i < instances.length; i += 1) {
      const inst = instances[i];
      if (inst === undefined) continue;
      writeInstanceMatrix(inst, axes, sceneUnitsPerMm, floorLevelMm, SCRATCH_MATRIX);
      mesh.setMatrixAt(i, SCRATCH_MATRIX);
      SCRATCH_COLOR.set(CATEGORY_COLOR[inst.category]);
      // Selection reads as "lit from within" rather than a different hue, so a
      // selected wardrobe is still recognisably a wardrobe.
      if (selected.has(inst.furnitureId)) SCRATCH_COLOR.offsetHSL(0, 0.18, 0.14);
      mesh.setColorAt(i, SCRATCH_COLOR);
    }

    // Park unused slots at zero scale rather than leaving a deleted item's
    // stale matrix on screen.
    SCRATCH_MATRIX.makeScale(0, 0, 0);
    for (let i = instances.length; i < capacity; i += 1) mesh.setMatrixAt(i, SCRATCH_MATRIX);

    mesh.count = capacity;
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor !== null) mesh.instanceColor.needsUpdate = true;
    invalidate();
  }, [instances, capacity, axes, sceneUnitsPerMm, floorLevelMm, selected, invalidate]);

  // ── the preview: imperative, never through React ────────────────────────
  useEffect(() => {
    const apply = (state: PlacementPoseState): void => {
      const preview = previewRef.current;
      const strip = clearanceRef.current;
      if (preview === null || strip === null) return;

      const item = state.item;
      if (item === null || state.phase === 'idle') {
        preview.visible = false;
        strip.visible = false;
        invalidate();
        return;
      }

      const deg = state.pose.rotationDeg;
      setUpRotation(preview.quaternion, deg, axes);
      strip.quaternion.copy(preview.quaternion);

      // The preview is the item's real volume, so it reads as a rectangle from
      // the plan camera and as a box from an orbit camera — same object.
      const [pw, ph, pd] = sceneScale(
        item.widthMm,
        item.depthMm,
        item.heightMm,
        axes,
        sceneUnitsPerMm,
      );
      preview.scale.set(pw, ph, pd);
      const [px, py, pz] = scenePosition(
        state.pose.pt.x,
        state.pose.pt.y,
        floorLevelMm + item.heightMm / 2,
        axes,
        sceneUnitsPerMm,
      );
      preview.position.set(px, py, pz);
      preview.visible = true;

      // Amber = "look at this", never "refused". Placement is not blocked.
      previewMaterialRef.current?.color.set(PREVIEW_COLOR[state.tone]);

      // Clearance strip: full width, `clearance` deep, entirely in FRONT of the
      // item (+Y local, matching the solver's `depth + clearance` packing).
      // Drawn flat on the floor — it is access space, not an object, and the
      // floor has to stay visible through it.
      if (item.clearanceMm <= 0) {
        strip.visible = false;
      } else {
        const offset = (item.depthMm + item.clearanceMm) / 2;
        const rad = deg * DEG_TO_RAD;
        const [sw, sh, sd] = sceneScale(
          item.widthMm,
          item.clearanceMm,
          STRIP_THICKNESS_MM,
          axes,
          sceneUnitsPerMm,
        );
        strip.scale.set(sw, sh, sd);
        const [sx, sy, sz] = scenePosition(
          state.pose.pt.x - Math.sin(rad) * offset,
          state.pose.pt.y + Math.cos(rad) * offset,
          floorLevelMm + STRIP_THICKNESS_MM,
          axes,
          sceneUnitsPerMm,
        );
        strip.position.set(sx, sy, sz);
        strip.visible = true;
      }

      invalidate();
    };

    apply(controller.getPoseState());
    return controller.subscribePose(apply);
  }, [controller, axes, sceneUnitsPerMm, floorLevelMm, invalidate]);

  // ── picking: registered with the core's ONE registry ────────────────────
  const pick = useMemo<GarhPick>(
    () => ({
      kind: 'furniture',
      idAt: (instanceId?: number) =>
        instanceId === undefined ? null : (instances[instanceId]?.furnitureId ?? null),
    }),
    [instances],
  );

  /**
   * The instance list is read through a ref so the registration itself is
   * stable: adding a chair must not unregister and re-register the mesh, and
   * the resolver has to see the CURRENT list rather than the one that existed
   * when the effect last ran.
   */
  const instancesRef = useRef(instances);
  instancesRef.current = instances;

  useEffect(() => {
    const mesh = meshRef.current;
    if (core === null || mesh === null) return undefined;
    return core.registry.register(mesh, (intersection): PickTarget | null => {
      const slot = intersection.instanceId;
      if (slot === undefined) return null;
      const id = instancesRef.current[slot]?.furnitureId;
      // Unused capacity is parked at zero scale; it has no element behind it.
      if (id === undefined) return null;
      return { kind: 'furniture', id, storeyId: activeStoreyId };
    });
    // `capacity` is in the list because `key={capacity}` remounts the mesh:
    // without it the registry would keep pointing at the discarded one.
  }, [core, capacity, activeStoreyId]);

  return (
    <group name="garh-furniture">
      <instancedMesh
        key={capacity}
        ref={meshRef}
        args={[undefined, undefined, capacity]}
        userData={{ garhPick: pick }}
        frustumCulled={false}
      >
        <boxGeometry args={[1, 1, 1]} />
        <meshBasicMaterial toneMapped={false} />
      </instancedMesh>

      {/* Outlines. A plan of solid fills with no edges reads as a colour blob;
          an architect checking a bedroom needs to see where the wardrobe stops
          and the bed starts. One merged buffer, one draw call. */}
      <lineSegments frustumCulled={false} raycast={NO_RAYCAST}>
        <bufferGeometry>
          <bufferAttribute
            key={edgePositions.byteLength}
            attach="attributes-position"
            args={[edgePositions, 3]}
          />
        </bufferGeometry>
        <lineBasicMaterial color="#5a6270" transparent opacity={0.65} toneMapped={false} />
      </lineSegments>

      {/* Preview objects are excluded from picking: you cannot select what you
          have not placed, and a pickable preview would swallow the very click
          meant to commit it. */}
      <mesh ref={previewRef} visible={false} raycast={NO_RAYCAST} renderOrder={10}>
        <boxGeometry args={[1, 1, 1]} />
        <meshBasicMaterial
          ref={previewMaterialRef}
          transparent
          opacity={0.55}
          depthWrite={false}
          toneMapped={false}
        />
      </mesh>

      <mesh ref={clearanceRef} visible={false} raycast={NO_RAYCAST} renderOrder={9}>
        <boxGeometry args={[1, 1, 1]} />
        <meshBasicMaterial
          color={CLEARANCE_COLOR}
          transparent
          opacity={CLEARANCE_OPACITY}
          side={DoubleSide}
          depthWrite={false}
          toneMapped={false}
        />
      </mesh>

      {showAllClearances ? (
        <PlacedClearances
          axes={axes}
          sceneUnitsPerMm={sceneUnitsPerMm}
          floorLevelMm={floorLevelMm}
        />
      ) : null}
    </group>
  );
}

/** Three's raycast hook, disabled. Cheaper and clearer than a layer mask. */
const NO_RAYCAST = (): void => undefined;

/** Compose one instance's world matrix into `out`. Allocates nothing. */
function writeInstanceMatrix(
  inst: BoxInstance,
  axes: PlanAxes,
  unitsPerMm: number,
  floorLevelMm: number,
  out: Matrix4,
): Matrix4 {
  const [x, y, z] = scenePosition(inst.px, inst.py, floorLevelMm + inst.pz, axes, unitsPerMm);
  SCRATCH_POS.set(x, y, z);
  const [sx, sy, sz] = instanceScale(inst, axes, unitsPerMm);
  SCRATCH_SCALE.set(sx, sy, sz);
  setUpRotation(SCRATCH_QUAT, inst.deg, axes);
  return out.compose(SCRATCH_POS, SCRATCH_QUAT, SCRATCH_SCALE);
}

/** A plan rotation, expressed about whichever axis the scene calls "up". */
function setUpRotation(out: Quaternion, deg: number, axes: PlanAxes): Quaternion {
  const [ax, ay, az] = sceneUpAxis(axes);
  SCRATCH_AXIS.set(ax, ay, az);
  return out.setFromAxisAngle(SCRATCH_AXIS, deg * DEG_TO_RAD);
}

interface StripPlacement {
  readonly x: number;
  readonly y: number;
  readonly deg: number;
  readonly wMm: number;
  readonly dMm: number;
}

/**
 * Every placed item's access strip — the "show me the circulation" review pass.
 *
 * Off by default: forty overlapping translucent rectangles is a mess, and the
 * strip an architect actually wants to see is the one under the cursor.
 */
function PlacedClearances({
  axes,
  sceneUnitsPerMm,
  floorLevelMm,
}: {
  readonly axes: PlanAxes;
  readonly sceneUnitsPerMm: number;
  readonly floorLevelMm: number;
}): JSX.Element | null {
  const { placed } = useFurniturePlacement();
  const meshRef = useRef<InstancedMesh>(null);

  const strips = useMemo<StripPlacement[]>(() => {
    const out: StripPlacement[] = [];
    for (const entry of placed) {
      const item = entry.item;
      if (item === null || item.clearanceMm <= 0) continue;
      const offset = (item.depthMm + item.clearanceMm) / 2;
      const rad = entry.pose.rotationDeg * DEG_TO_RAD;
      out.push({
        x: entry.pose.pt.x - Math.sin(rad) * offset,
        y: entry.pose.pt.y + Math.cos(rad) * offset,
        deg: entry.pose.rotationDeg,
        wMm: item.widthMm,
        dMm: item.clearanceMm,
      });
    }
    return out;
  }, [placed]);

  useEffect(() => {
    const mesh = meshRef.current;
    if (mesh === null) return;
    for (let i = 0; i < strips.length; i += 1) {
      const s = strips[i];
      if (s === undefined) continue;
      const [x, y, z] = scenePosition(
        s.x,
        s.y,
        floorLevelMm + STRIP_THICKNESS_MM,
        axes,
        sceneUnitsPerMm,
      );
      SCRATCH_POS.set(x, y, z);
      const [sx, sy, sz] = sceneScale(s.wMm, s.dMm, STRIP_THICKNESS_MM, axes, sceneUnitsPerMm);
      SCRATCH_SCALE.set(sx, sy, sz);
      setUpRotation(SCRATCH_QUAT, s.deg, axes);
      SCRATCH_MATRIX.compose(SCRATCH_POS, SCRATCH_QUAT, SCRATCH_SCALE);
      mesh.setMatrixAt(i, SCRATCH_MATRIX);
    }
    mesh.count = strips.length;
    mesh.instanceMatrix.needsUpdate = true;
  }, [strips, axes, sceneUnitsPerMm, floorLevelMm]);

  if (strips.length === 0) return null;

  return (
    <instancedMesh
      key={strips.length}
      ref={meshRef}
      args={[undefined, undefined, strips.length]}
      raycast={NO_RAYCAST}
      frustumCulled={false}
    >
      <boxGeometry args={[1, 1, 1]} />
      <meshBasicMaterial
        color={CLEARANCE_COLOR}
        transparent
        opacity={CLEARANCE_OPACITY}
        side={DoubleSide}
        depthWrite={false}
        toneMapped={false}
      />
    </instancedMesh>
  );
}
