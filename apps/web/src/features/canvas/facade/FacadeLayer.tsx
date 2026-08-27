/**
 * FacadeLayer.tsx — the facade sub-model in the shared R3F scene.
 *
 * ## Picking (inherited fact 1 — the FurnitureLayer lesson)
 *
 * Every component mesh REGISTERS with the canvas core's one `PickRegistry`:
 *
 * ```ts
 * core.registry.register(mesh, { kind: FACADE_PICK_KIND, id: component.id, storeyId });
 * ```
 *
 * Registration is the contract — a mesh that is merely in the scene is never
 * raycast (`pickAt` walks `registry.objects()` non-recursively). Each
 * component is its own `Mesh` (§8: "separate meshes tagged
 * `facadeComponentId`"), so registration is per-component and the resolver is
 * a constant target, no instanceId indirection. The `userData.garhFacade` tag
 * is a debugging label, NOT the integration.
 *
 * ## §14 — how the <100 ms incremental rebuild is met
 *
 * Nothing here rebuilds "the facade". Geometry is rebuilt per COMPONENT, and a
 * component rebuilds only when a React memo says its inputs moved:
 *
 *  - `fold()` preserves object identity for untouched elements and for
 *    `facade.components` entries an op-28 patch did not hit — so `component`,
 *    `wall`, `opening` props are reference-stable across unrelated ops;
 *  - the per-storey model slices (`useStoreySlices`) reuse the PREVIOUS array
 *    object when a storey's walls/openings/balconies are element-wise
 *    identical, so an edit on storey 2 leaves storey 0/1 slices `===` and
 *    every mesh on those storeys skips both React render and geometry build;
 *  - building-scoped components (cladding zone, parapet profile — two meshes)
 *    key on the full wall list and rebuild on any wall edit, which is the
 *    honest dependency: both read building height and the storey centroid.
 *
 * Worst case for a G+2 edit is therefore: the dirty storey's components (a
 * handful of 4-box trims and 1-box chajjas) plus two building-scoped meshes —
 * a few thousand triangles of Float32Array writes, nowhere near 100 ms.
 *
 * ## No new canvas, no lights
 *
 * This layer mounts inside the ONE existing `<Canvas>` (`CanvasRoot`) next to
 * the plan layers. Materials are unlit `MeshBasicMaterial` with Lambert
 * shading pre-baked into vertex colours (`geometry3d.ts`) — the layer renders
 * correctly in a scene with no lighting rig and needs nothing from the sun
 * widget.
 */

import { memo, useEffect, useMemo, useRef, type MutableRefObject } from 'react';
import { useThree } from '@react-three/fiber';
import { BufferAttribute, BufferGeometry, type Mesh, MeshBasicMaterial } from 'three';

import type {
  Balcony,
  FacadeComponent,
  HouseModel,
  Levels,
  Opening,
  Storey,
  Wall,
} from '@garh/model';

import { selectHouse, useModelStore } from '../../../stores/model';
import { useSelectionStore } from '../../../stores/selection';
import { useCanvasCoreOptional } from '../core';
import { boxesForComponent } from './componentBoxes';
import { buildBoxTriangles, SELECTION_BOOST } from './geometry3d';
import { FACADE_PICK_KIND } from './types';

// ---------------------------------------------------------------------------
// Stable-slice hooks — the identity discipline the memoisation relies on
// ---------------------------------------------------------------------------

function shallowArrayEqual<T>(a: readonly T[], b: readonly T[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return false;
  return true;
}

/** Returns the previous array instance while its elements are `===`-equal. */
function useShallowStableArray<T>(next: readonly T[]): readonly T[] {
  const ref = useRef(next);
  if (!shallowArrayEqual(ref.current, next)) ref.current = next;
  return ref.current;
}

/** `Levels` compared by value — it is 4 ints and a small int array. */
function useStableLevels(next: Levels): Levels {
  const ref = useRef(next);
  const prev = ref.current;
  const equal =
    prev === next ||
    (prev.plinthMm === next.plinthMm &&
      prev.sillDefaultMm === next.sillDefaultMm &&
      prev.lintelDefaultMm === next.lintelDefaultMm &&
      prev.parapetMm === next.parapetMm &&
      shallowArrayEqual(prev.fflPerStoreyMm, next.fflPerStoreyMm));
  if (!equal) ref.current = next;
  return ref.current;
}

interface StoreySlice {
  readonly walls: readonly Wall[];
  readonly openings: readonly Opening[];
  readonly balconies: readonly Balcony[];
}

const EMPTY_SLICE: StoreySlice = { walls: [], openings: [], balconies: [] };

/**
 * Per-storey slices of the model, each reusing its previous identity while its
 * contents are unchanged. THIS is the "keyed by storey" of the §14 note above.
 */
function useStoreySlices(
  walls: readonly Wall[],
  openings: readonly Opening[],
  balconies: readonly Balcony[],
): ReadonlyMap<string, StoreySlice> {
  const prevRef = useRef<ReadonlyMap<string, StoreySlice>>(new Map());
  return useMemo(() => {
    const wallsBy = new Map<string, Wall[]>();
    for (const w of walls) {
      const list = wallsBy.get(w.storeyId);
      if (list === undefined) wallsBy.set(w.storeyId, [w]);
      else list.push(w);
    }
    const wallStorey = new Map<string, string>();
    for (const w of walls) wallStorey.set(w.id, w.storeyId);
    const openingsBy = new Map<string, Opening[]>();
    for (const o of openings) {
      const sid = wallStorey.get(o.wallId);
      if (sid === undefined) continue;
      const list = openingsBy.get(sid);
      if (list === undefined) openingsBy.set(sid, [o]);
      else list.push(o);
    }
    const balconiesBy = new Map<string, Balcony[]>();
    for (const b of balconies) {
      const list = balconiesBy.get(b.storeyId);
      if (list === undefined) balconiesBy.set(b.storeyId, [b]);
      else list.push(b);
    }

    const prev = prevRef.current;
    const next = new Map<string, StoreySlice>();
    const storeyIds = new Set<string>([
      ...wallsBy.keys(),
      ...openingsBy.keys(),
      ...balconiesBy.keys(),
    ]);
    for (const sid of storeyIds) {
      const fresh: StoreySlice = {
        walls: wallsBy.get(sid) ?? [],
        openings: openingsBy.get(sid) ?? [],
        balconies: balconiesBy.get(sid) ?? [],
      };
      const old = prev.get(sid);
      const reuse =
        old !== undefined &&
        shallowArrayEqual(old.walls, fresh.walls) &&
        shallowArrayEqual(old.openings, fresh.openings) &&
        shallowArrayEqual(old.balconies, fresh.balconies);
      next.set(sid, reuse ? old : fresh);
    }
    prevRef.current = next;
    return next;
  }, [walls, openings, balconies]);
}

// ---------------------------------------------------------------------------
// The layer
// ---------------------------------------------------------------------------

export interface FacadeLayerProps {
  /** Model override for tests/stories; defaults to the model store. */
  readonly house?: HouseModel | undefined;
}

export function FacadeLayer({ house: houseProp }: FacadeLayerProps): JSX.Element | null {
  const storeHouse = useModelStore(selectHouse);
  const house = houseProp ?? storeHouse;
  const selectedIds = useSelectionStore((s) => s.ids);

  // One unlit material for every facade mesh; colour lives in vertex colours.
  const material = useMemo(
    () => new MeshBasicMaterial({ vertexColors: true, toneMapped: false }),
    [],
  );
  useEffect(() => () => material.dispose(), [material]);

  // Stable views of the model, so unrelated ops do not invalidate memos.
  const storeys = useShallowStableArray(house.storeys);
  const walls = useShallowStableArray(house.walls);
  const openings = useShallowStableArray(house.openings);
  const balconies = useShallowStableArray(house.balconies);
  const levels = useStableLevels(house.levels);
  const slices = useStoreySlices(walls, openings, balconies);
  const components = useShallowStableArray(house.facade.components);

  // Builders read the live model through a ref; the memo DEPS above are what
  // guarantee the read happens exactly when an input actually moved.
  const houseRef = useRef(house);
  houseRef.current = house;

  const selected = useMemo(() => new Set(selectedIds), [selectedIds]);

  if (components.length === 0) return null;

  return (
    <group name="garh-facade">
      {components.map((component) => {
        const buildingScoped =
          component.kind === 'cladding_zone' || component.kind === 'parapet_profile';
        // A railing's anchor storey is its balcony's; others carry their own.
        const sliceKey = component.storeyId ?? '';
        const slice = buildingScoped ? EMPTY_SLICE : (slices.get(sliceKey) ?? EMPTY_SLICE);
        return (
          <FacadeComponentMesh
            key={component.id}
            component={component}
            houseRef={houseRef}
            material={material}
            slice={slice}
            allWalls={buildingScoped ? walls : EMPTY_WALLS}
            storeys={storeys}
            levels={levels}
            selected={selected.has(component.id)}
          />
        );
      })}
    </group>
  );
}

const EMPTY_WALLS: readonly Wall[] = [];

// ---------------------------------------------------------------------------
// One component = one mesh = one registration
// ---------------------------------------------------------------------------

interface FacadeComponentMeshProps {
  readonly component: FacadeComponent;
  readonly houseRef: MutableRefObject<HouseModel>;
  readonly material: MeshBasicMaterial;
  /** The component's storey slice (empty for building-scoped kinds). */
  readonly slice: StoreySlice;
  /** All walls — non-empty only for building-scoped kinds. */
  readonly allWalls: readonly Wall[];
  readonly storeys: readonly Storey[];
  readonly levels: Levels;
  readonly selected: boolean;
}

const FacadeComponentMesh = memo(function FacadeComponentMesh({
  component,
  houseRef,
  material,
  slice,
  allWalls,
  storeys,
  levels,
  selected,
}: FacadeComponentMeshProps): JSX.Element | null {
  const core = useCanvasCoreOptional();
  const { invalidate } = useThree();
  const meshRef = useRef<Mesh>(null);

  // The dep list is the honest read-set of `boxesForComponent` for this kind:
  // its params (via `component`), its storey's elements (via `slice` /
  // `allWalls`), and the building's vertical datums (`storeys`, `levels`).
  const geometry = useMemo(() => {
    const boxes = boxesForComponent(houseRef.current, component);
    if (boxes.length === 0) return null;
    const data = buildBoxTriangles(boxes, selected ? SELECTION_BOOST : 1);
    const g = new BufferGeometry();
    g.setAttribute('position', new BufferAttribute(data.positions, 3));
    g.setAttribute('color', new BufferAttribute(data.colors, 3));
    return g;
    // slice/allWalls/storeys/levels are not referenced in the closure — they
    // ARE the tracked read-set of `boxesForComponent`, which reads the live
    // model through `houseRef` (see the module header for why that is sound).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [component, slice, allWalls, storeys, levels, selected, houseRef]);

  useEffect(() => () => geometry?.dispose(), [geometry]);

  // frameloop="demand": a rebuilt buffer is invisible until a frame is asked for.
  useEffect(() => {
    invalidate();
  }, [geometry, invalidate]);

  // THE pick registration (inherited fact 1). Unregister rides the cleanup.
  useEffect(() => {
    const mesh = meshRef.current;
    if (core === null || mesh === null || geometry === null) return undefined;
    return core.registry.register(mesh, {
      kind: FACADE_PICK_KIND,
      id: component.id,
      storeyId: component.storeyId,
    });
  }, [core, component.id, component.storeyId, geometry]);

  if (geometry === null) return null; // orphaned component: nothing, honestly

  return (
    <mesh
      ref={meshRef}
      geometry={geometry}
      material={material}
      userData={{ garhFacade: component.id, garhFacadeKind: component.kind }}
    />
  );
});
