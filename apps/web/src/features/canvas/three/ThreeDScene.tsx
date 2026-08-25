/**
 * ThreeDScene — the building, extruded from the model document. Phase 5's
 * counterpart to `PlanScene`, mounted INSIDE the same `<CanvasRoot>` (§12:
 * ONE `<Canvas>`, one scene graph, one picker — the `CameraRig` swaps the
 * projection, this component never touches a camera).
 *
 * ════════════════════════════════════════════════════════════════════════════
 * INCREMENTAL REBUILD (§14: <100ms after an edit, dirty storeys only)
 * ════════════════════════════════════════════════════════════════════════════
 * A per-group cache keyed by the group's model-slice SIGNATURE (`dirty.ts`):
 * on every document change the signatures are recomputed (string building
 * over already-sorted arrays — microseconds), and only groups whose signature
 * moved are re-synthesised and re-packed. Unchanged groups return the SAME
 * buffer arrays, so their leaf components see identical props and React never
 * touches their GPU geometry. A wall drag re-meshes one storey; a facade op
 * or a room rename re-meshes nothing.
 *
 * The cache holds plain Float32Arrays, never BufferGeometry: GPU objects are
 * created and disposed by the leaf components' effects, which makes the cache
 * StrictMode-safe and keeps `geometryBuild.ts` runnable in node for the specs.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * PICKING (inherited fact 1 — the FurnitureLayer lesson)
 * ════════════════════════════════════════════════════════════════════════════
 * EVERY mesh this component renders registers with the core's PickRegistry:
 * element buckets through a faceIndex→target resolver (same pattern as the
 * 2D plan's merged meshes), floor slabs through a point→room resolver (the
 * floor of a room selects the room, matching the 2D room-wash click), and
 * derived structure (roof, parapet, plinth, ground) through an explicit
 * null-resolver — visibly registered, deliberately not selectable, because
 * no model element exists for them. There is no R3F pointer handler anywhere
 * in this file. 2D and 3D picks return the same element ids through the same
 * `pickAt`.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * HONEST DEGRADATION
 * ════════════════════════════════════════════════════════════════════════════
 * Opening holes need the Manifold WASM (`booleans.ts`, lazy-loaded on first
 * mount of this component — the app pays for it only when 3D opens). Until
 * it is ready — or if it never becomes ready — walls render WITHOUT holes,
 * opening panels still render and pick, and `onEngineStatus` /
 * `onRebuildStats.holesApplied` tell the page so it can say so in words.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  BufferAttribute,
  BufferGeometry,
  DirectionalLight,
  type Intersection,
} from 'three';

import {
  bbox,
  buildingHeightMm,
  pointInPolygon,
  type Bbox,
  type HouseModel,
  type Room,
} from '@garh/model';

import {
  LAYER_RENDER_ORDER,
  WORLD_UNITS_PER_MM,
  useCanvasCore,
  usePickableResolver,
  worldToMmF,
  type CanvasLayer,
  type PickResolver,
  type PickTarget,
} from '../core';
import {
  booleanEngineStatus,
  ensureBooleanEngine,
  getPrismCutter,
  subscribeBooleanEngine,
  type BooleanEngineStatus,
} from './booleans';
import { groupSignatures } from './dirty';
import { buildGroup, type BuiltBucket, type GroupBuild } from './geometryBuild';
import {
  colorForScope,
  elementScopedAssignmentIds,
  getGroundMaterial,
  getSolidMaterial,
} from './materials3d';

// ---------------------------------------------------------------------------
// Props & reporting
// ---------------------------------------------------------------------------

export interface RebuildStats {
  /** Group keys re-synthesised by the last document change. */
  readonly rebuiltGroups: readonly string[];
  readonly totalGroups: number;
  /** Wall-clock milliseconds of the incremental rebuild (§14 evidence). */
  readonly ms: number;
  /** False while any group renders walls without their opening holes. */
  readonly holesApplied: boolean;
}

export interface ThreeDSceneProps {
  readonly house: HouseModel;
  /** materialId → colorHex, from `GET /catalog/materials`. Optional — the
   * procedural palette covers every surface group without it. */
  readonly materialColors?: Readonly<Record<string, string>> | undefined;
  /** Render the built-in hemisphere + sun lights. Pass false when a sun
   * widget module owns the lighting. */
  readonly lights?: boolean | undefined;
  /** Boolean-engine state, for the honest status chip (§15). */
  readonly onEngineStatus?: ((status: BooleanEngineStatus) => void) | undefined;
  /** Rebuild telemetry after each document change. */
  readonly onRebuildStats?: ((stats: RebuildStats) => void) | undefined;
  /**
   * Rebuild-group keys to SHOW (`storeyGroupKey(id)` / `ROOF_GROUP_KEY`);
   * `undefined` shows everything. Visibility only — geometry caches, pick
   * registration and the §14 signatures are untouched, and the shared picker
   * skips hidden groups itself (`isEffectivelyVisible` walks ancestors), so a
   * hidden storey can never be clicked. Added by the Phase-5 integrator for
   * the "see one storey / all" toggle.
   */
  readonly visibleGroupKeys?: ReadonlySet<string> | undefined;
}

// ---------------------------------------------------------------------------
// Surface → draw-order layer (one table, mirroring CANVAS_LAYERS semantics)
// ---------------------------------------------------------------------------

const SURFACE_LAYER: Readonly<Record<string, CanvasLayer>> = {
  external_wall: 'wall',
  internal_wall: 'wall',
  cladding: 'wall',
  parapet: 'wall',
  plinth: 'slab',
  floor: 'slab',
  ceiling: 'slab',
  roof: 'slab',
  door: 'opening',
  window: 'opening',
  railing: 'balcony',
  staircase: 'stair',
};

function renderOrderOf(bucket: BuiltBucket): number {
  return LAYER_RENDER_ORDER[SURFACE_LAYER[bucket.surface] ?? 'wall'];
}

// ---------------------------------------------------------------------------
// Geometry lifecycle (same discipline as PlanScene.useGeometry)
// ---------------------------------------------------------------------------

function useSolidGeometry(positions: Float32Array, normals: Float32Array): BufferGeometry {
  const geometry = useMemo(() => {
    const g = new BufferGeometry();
    g.setAttribute('position', new BufferAttribute(positions, 3));
    g.setAttribute('normal', new BufferAttribute(normals, 3));
    // Raycaster and frustum culling both want the sphere; guard the empty
    // buffer to avoid three's NaN-radius warning on empty layers.
    if (positions.length > 0) g.computeBoundingSphere();
    return g;
  }, [positions, normals]);

  useEffect(() => () => geometry.dispose(), [geometry]);
  return geometry;
}

// ---------------------------------------------------------------------------
// One merged, registered bucket
// ---------------------------------------------------------------------------

interface BucketMeshProps {
  readonly bucket: BuiltBucket;
  readonly house: HouseModel;
  readonly rooms: readonly Room[];
  readonly materialColors: Readonly<Record<string, string>> | undefined;
}

function BucketMesh({ bucket, house, rooms, materialColors }: BucketMeshProps): JSX.Element | null {
  const geometry = useSolidGeometry(bucket.positions, bucket.normals);

  const resolver = useMemo<PickResolver>(() => {
    if (bucket.pickRoomByPoint) {
      // The floor of a room selects the room — the same id a 2D room-wash
      // click produces, so 2D and 3D selection agree (§12).
      return (intersection: Intersection): PickTarget | null => {
        const pointMm = worldToMmF(intersection.point);
        for (const room of rooms) {
          if (room.polygon.length < 3) continue;
          if (pointInPolygon(pointMm, room.polygon) !== 'outside') {
            return { kind: 'room', id: room.id, storeyId: room.storeyId };
          }
        }
        return null;
      };
    }
    const targets = bucket.faceTargets;
    return (intersection: Intersection): PickTarget | null => {
      const faceIndex = intersection.faceIndex;
      if (faceIndex === undefined) return null;
      return targets[faceIndex] ?? null;
    };
  }, [bucket, rooms]);

  const pickRef = usePickableResolver(resolver);

  const color = colorForScope(
    house.materials,
    { surface: bucket.surface, storeyId: bucket.storeyId, elementId: bucket.elementId },
    materialColors,
    bucket.overrideColor,
  );
  const material = getSolidMaterial(color, bucket.glass);

  if (bucket.positions.length === 0) return null;
  return (
    <mesh
      ref={pickRef}
      geometry={geometry}
      material={material}
      renderOrder={renderOrderOf(bucket)}
      castShadow={!bucket.glass}
      receiveShadow
    />
  );
}

// ---------------------------------------------------------------------------
// Ground plane — registered (null resolver), receives shadows
// ---------------------------------------------------------------------------

const NULL_RESOLVER: PickResolver = () => null;

function GroundPlane({ boxMm }: { readonly boxMm: Bbox }): JSX.Element {
  // Registered with an explicit null resolver: on the registry (fact 1's
  // discipline — no mesh silently off the picker), deliberately never a
  // selection. A click on open ground resolves 'empty' at the reference
  // plane, which is what clears the selection.
  const pickRef = usePickableResolver(NULL_RESOLVER);

  const pad = 3000;
  const k = WORLD_UNITS_PER_MM;
  const width = (boxMm.maxX - boxMm.minX + pad * 2) * k;
  const depth = (boxMm.maxY - boxMm.minY + pad * 2) * k;
  const cx = ((boxMm.minX + boxMm.maxX) / 2) * k;
  const cz = -((boxMm.minY + boxMm.maxY) / 2) * k;

  return (
    <mesh
      ref={pickRef}
      position={[cx, -0.02, cz]}
      rotation={[-Math.PI / 2, 0, 0]}
      material={getGroundMaterial()}
      receiveShadow
      renderOrder={LAYER_RENDER_ORDER.grid}
    >
      <planeGeometry args={[width, depth]} />
    </mesh>
  );
}

// ---------------------------------------------------------------------------
// Lights — a fixed, honest sun (a real date/time scrubber is the sun widget
// module's job; it mounts with `lights={false}` here and owns its own light)
// ---------------------------------------------------------------------------

function SceneLights({ boxMm, heightMm }: { readonly boxMm: Bbox; readonly heightMm: number }): JSX.Element {
  const sun = useMemo(() => new DirectionalLight(0xffffff, 1.15), []);
  useEffect(
    () => () => {
      sun.dispose();
    },
    [sun],
  );

  const k = WORLD_UNITS_PER_MM;
  const cx = ((boxMm.minX + boxMm.maxX) / 2) * k;
  const cz = -((boxMm.minY + boxMm.maxY) / 2) * k;
  const radius =
    Math.max(boxMm.maxX - boxMm.minX, boxMm.maxY - boxMm.minY, heightMm, 6000) * k * 0.75;

  useEffect(() => {
    // From the south-west, high — the default 3D orbit looks from the SW too,
    // so the lit faces are the ones on screen.
    sun.position.set(cx - radius, radius * 1.8, cz + radius);
    sun.target.position.set(cx, 0, cz);
    sun.castShadow = true;
    sun.shadow.mapSize.set(1024, 1024);
    const cam = sun.shadow.camera;
    cam.left = -radius * 2;
    cam.right = radius * 2;
    cam.top = radius * 2;
    cam.bottom = -radius * 2;
    cam.near = 0.1;
    cam.far = radius * 6;
    cam.updateProjectionMatrix();
  }, [sun, cx, cz, radius]);

  return (
    <>
      <hemisphereLight args={[0xffffff, 0xb8b2a6, 0.65]} />
      <primitive object={sun} />
      <primitive object={sun.target} />
    </>
  );
}

// ---------------------------------------------------------------------------
// The scene
// ---------------------------------------------------------------------------

interface CachedGroup {
  readonly signature: string;
  readonly build: GroupBuild;
}

const NO_ROOMS: readonly Room[] = [];

/** Bbox of everything the 3D view draws, mm. Null on an empty document. */
function houseExtentMm(house: HouseModel): Bbox | null {
  const points: { x: number; y: number }[] = [];
  for (const wall of house.walls) points.push(wall.a, wall.b);
  for (const slab of house.slabs) points.push(...slab.polygon);
  for (const balcony of house.balconies) points.push(...balcony.polygon);
  if (points.length === 0) return null;
  return bbox(points);
}

export function ThreeDScene({
  house,
  materialColors,
  lights = true,
  onEngineStatus,
  onRebuildStats,
  visibleGroupKeys,
}: ThreeDSceneProps): JSX.Element {
  const core = useCanvasCore();

  // ── boolean engine: lazy-load on first mount of the 3D view ─────────────
  const [engineStatus, setEngineStatus] = useState<BooleanEngineStatus>(booleanEngineStatus);
  useEffect(() => {
    const unsubscribe = subscribeBooleanEngine(setEngineStatus);
    void ensureBooleanEngine();
    return unsubscribe;
  }, []);
  useEffect(() => {
    onEngineStatus?.(engineStatus);
  }, [engineStatus, onEngineStatus]);
  const engineReady = engineStatus.state === 'ready';

  // ── the incremental rebuild ──────────────────────────────────────────────
  const cacheRef = useRef<Map<string, CachedGroup>>(new Map());
  const statsRef = useRef<RebuildStats | null>(null);

  const groups = useMemo<readonly GroupBuild[]>(() => {
    const t0 = performance.now();
    const cutter = engineReady ? getPrismCutter() : null;
    // The engine generation salts the signature: when WASM arrives, every
    // group that wanted cuts is stale by definition and rebuilds once.
    const salt = cutter === null ? 'M0|' : 'M1|';
    const elementScoped = elementScopedAssignmentIds(house.materials);
    const signatures = groupSignatures(house);
    const cache = cacheRef.current;

    const out: GroupBuild[] = [];
    const rebuilt: string[] = [];
    let holesApplied = true;
    for (const [key, signature] of signatures) {
      const salted = salt + signature;
      const hit = cache.get(key);
      if (hit !== undefined && hit.signature === salted) {
        out.push(hit.build);
        if (!hit.build.holesApplied) holesApplied = false;
        continue;
      }
      const build = buildGroup(house, key, cutter, elementScoped);
      cache.set(key, { signature: salted, build });
      rebuilt.push(key);
      out.push(build);
      if (!build.holesApplied) holesApplied = false;
    }
    for (const key of [...cache.keys()]) {
      if (!signatures.has(key)) cache.delete(key);
    }

    statsRef.current = {
      rebuiltGroups: rebuilt,
      totalGroups: signatures.size,
      ms: performance.now() - t0,
      holesApplied,
    };
    return out;
  }, [house, engineReady]);

  useEffect(() => {
    const stats = statsRef.current;
    if (stats !== null) onRebuildStats?.(stats);
  }, [groups, onRebuildStats]);

  // ── camera plumbing: fit height + demand-frameloop invalidation ─────────
  useEffect(() => {
    core.viewport.setFitHeightMm(buildingHeightMm(house) + house.levels.parapetMm);
  }, [core, house]);

  useEffect(() => {
    core.invalidate();
  }, [core, groups]);

  // ── room lookup tables for the floor-slab pick resolvers ────────────────
  const roomsByStorey = useMemo(() => {
    const out = new Map<string, Room[]>();
    for (const room of house.rooms) {
      const list = out.get(room.storeyId);
      if (list) list.push(room);
      else out.set(room.storeyId, [room]);
    }
    return out;
  }, [house.rooms]);

  const extentMm = useMemo(() => houseExtentMm(house), [house]);

  return (
    <group name="three-d">
      {lights && extentMm !== null ? (
        <SceneLights boxMm={extentMm} heightMm={buildingHeightMm(house)} />
      ) : null}
      {extentMm !== null ? <GroundPlane boxMm={extentMm} /> : null}
      {groups.map((group) => (
        <group
          key={group.key}
          name={`three-d:${group.key}`}
          visible={visibleGroupKeys === undefined || visibleGroupKeys.has(group.key)}
        >
          {group.buckets.map((bucket) => (
            <BucketMesh
              key={bucket.key}
              bucket={bucket}
              house={house}
              rooms={
                bucket.storeyId === null ? NO_ROOMS : (roomsByStorey.get(bucket.storeyId) ?? NO_ROOMS)
              }
              materialColors={materialColors}
            />
          ))}
        </group>
      ))}
    </group>
  );
}
