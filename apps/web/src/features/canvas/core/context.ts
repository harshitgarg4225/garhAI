/**
 * context.ts — the `CanvasCore`: everything a canvas module shares, in one
 * object, handed through React context.
 *
 * NOTE (provenance): this file was reconstructed from its call sites after the
 * original blob was lost from the repository archive. Every member below is
 * pinned by a consumer — `CanvasRoot` (construction, snap/storey mirroring,
 * dispose), `useCanvasControls` (`pointMm` / `rawPointMm` / `pick`),
 * `CameraRig` (`camera` / `invalidate` assignment), the overlay layers
 * (`registry`, the pickable hooks) and `screenScale` (`useViewportValue`-style
 * subscriptions). If a member looks unused, check those files before deleting.
 *
 * WHAT LIVES HERE AND WHY
 *
 *   viewport   camera state outside React — see `viewport.ts` for the §14 case
 *   registry   the one pick registry — see `pickRegistry.ts` for the §12 case
 *   camera     whichever camera is live; written by `CameraRig` on mode swap
 *   invalidate R3F's demand-frameloop trigger; written by `CameraRig` on mount
 *
 * The core is deliberately a plain mutable class, not a store. Pointer
 * handlers read `snapModuleMm` and `activeStoreyId` on every event; nothing
 * should re-render because the snap module changed (`CanvasRoot` mirrors the
 * props straight onto the instance).
 *
 * THE PICK BOUNDARY. `pick`, `pointMm` and `rawPointMm` are the only places
 * the rest of the product asks "what is under the pointer". They fill in the
 * registry, the live camera, the mode, the reference plane and the zoom so
 * that no tool ever assembles a `PickOptions` by hand — that is how the 2D and
 * 3D views stay on one set of priority rules.
 */

import { createContext, useCallback, useContext, useRef, useSyncExternalStore } from 'react';
import type { Camera, Object3D } from 'three';

import type { Pt } from '@garh/model';

import { SNAP_COARSE_MM, type PickKind } from './constants';
import { pointerToMm, pointerToMmRaw, type Ndc } from './coords';
import { emptyHit, pickAt, type PickHit, type PickOptions } from './hitTest';
import {
  PickRegistry,
  type InstanceIdLookup,
  type PickResolver,
  type PickTarget,
} from './pickRegistry';
import { ViewportController } from './viewport';

// ---------------------------------------------------------------------------
// The core
// ---------------------------------------------------------------------------

/**
 * Per-call pick constraints. Everything environmental (registry, camera, mode,
 * plane, zoom) is the core's to supply; a caller only narrows the search.
 * `storeyId` defaults to the active storey — pass `null` explicitly to pick
 * across every storey.
 */
export type CorePickOptions = Omit<
  PickOptions,
  'registry' | 'camera' | 'ndc' | 'mode' | 'planeElevationMm' | 'mmPerPx'
>;

export class CanvasCore {
  readonly viewport = new ViewportController();

  readonly registry = new PickRegistry();

  /**
   * The live camera. `CameraRig` writes it on every mode swap; before the rig
   * mounts it is null and every pick reports empty. Never construct a camera
   * here — the rig owns both of them.
   */
  camera: Camera | null = null;

  /**
   * R3F's `invalidate`, once the renderer exists (`CameraRig` introduces
   * them). Mutating a mesh outside React must be followed by a call to this —
   * the canvas is `frameloop="demand"` and will not notice otherwise.
   */
  invalidate: () => void = () => undefined;

  /** Snap module in mm. Mirrored from props by `CanvasRoot`; 0 disables. */
  snapModuleMm: number = SNAP_COARSE_MM;

  /** Storey being edited; picks are filtered to it. Mirrored by `CanvasRoot`. */
  activeStoreyId: string | null = null;

  /**
   * Model point under the pointer on the reference plane, snapped to the
   * active module — the value an op payload wants. `null` when the ray misses
   * the plane (3D, pointer above the horizon) or before the rig has mounted.
   */
  pointMm(ndc: Ndc): Pt | null {
    if (this.camera === null) return null;
    return pointerToMm(ndc, this.camera, {
      planeElevationMm: this.viewport.planeElevationMm,
      snapModuleMm: this.snapModuleMm,
    });
  }

  /** The same point, unsnapped (still integer mm). Readouts and hover maths. */
  rawPointMm(ndc: Ndc): Pt | null {
    if (this.camera === null) return null;
    return pointerToMmRaw(ndc, this.camera, this.viewport.planeElevationMm);
  }

  /** ONE picker, both views — every click, hover and marquee goes through here. */
  pick(ndc: Ndc, options: CorePickOptions = {}): PickHit {
    if (this.camera === null) {
      return emptyHit(null, this.viewport.planeElevationMm);
    }
    return pickAt({
      registry: this.registry,
      camera: this.camera,
      ndc,
      mode: this.viewport.mode,
      planeElevationMm: this.viewport.planeElevationMm,
      mmPerPx: this.viewport.mmPerPx,
      kinds: options.kinds,
      excludeIds: options.excludeIds,
      storeyId: options.storeyId === undefined ? this.activeStoreyId : options.storeyId,
      tolerancePx: options.tolerancePx,
      depthEpsilonWorld: options.depthEpsilonWorld,
    });
  }

  /** Called on `CanvasRoot` unmount. The core must not outlive its canvas. */
  dispose(): void {
    this.viewport.dispose();
    this.registry.clear();
    this.camera = null;
    this.invalidate = () => undefined;
  }
}

// ---------------------------------------------------------------------------
// Context + hooks
// ---------------------------------------------------------------------------

/**
 * Provided by `CanvasRoot` *inside* the `<Canvas>` (R3F reconciles its
 * children with a separate React root, and context does not cross that
 * boundary — the provider placement in `CanvasRoot` is what bridges it).
 */
export const CanvasCoreContext = createContext<CanvasCore | null>(null);

export function useCanvasCore(): CanvasCore {
  const core = useContext(CanvasCoreContext);
  if (core === null) {
    throw new Error('useCanvasCore must be used inside <CanvasRoot>');
  }
  return core;
}

/**
 * For components that also render outside the canvas (panel previews reusing
 * scene components). Callers must handle the null.
 */
export function useCanvasCoreOptional(): CanvasCore | null {
  return useContext(CanvasCoreContext);
}

/**
 * Read a viewport-derived value in React, rAF-coalesced (§14: a drag commits
 * several times between two frames; React sees at most one). Selectors must
 * return primitives — `getFrame()` is what keeps `useSyncExternalStore`
 * snapshots comparable, and an object selector would re-render every frame.
 */
export function useViewportValue<T>(selector: (viewport: ViewportController) => T): T {
  const core = useCanvasCore();
  const subscribe = useCallback(
    (onStoreChange: () => void) => core.viewport.subscribeAnimationFrame(onStoreChange),
    [core],
  );
  const getSnapshot = (): T => selector(core.viewport);
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

/** Millimetres per CSS pixel, live. The "1:100" readout and HUD sizing. */
export function useMmPerPx(): number {
  return useViewportValue((viewport) => viewport.mmPerPx);
}

// ---------------------------------------------------------------------------
// Pickable registration — callback refs over `useEffect`
// ---------------------------------------------------------------------------
//
// All three hooks return a **callback ref**: bind it to the mesh and the
// registration exactly tracks the mesh's lifetime — React hands the ref null
// on detach, which is the unregister. The registered resolver is a stable
// delegate reading the latest arguments through a ref, so re-renders (a new
// resolver closure, a changed storey filter) never re-register the object and
// never touch the registry's cached object list mid-interaction.

function usePickableDelegate(
  resolverRef: React.MutableRefObject<PickResolver | null>,
): (object: Object3D | null) => void {
  const core = useCanvasCore();
  const unregister = useRef<(() => void) | null>(null);

  const delegate = useCallback<PickResolver>(
    (intersection) => {
      const resolver = resolverRef.current;
      return resolver === null ? null : resolver(intersection);
    },
    [resolverRef],
  );

  return useCallback(
    (object: Object3D | null) => {
      unregister.current?.();
      unregister.current = null;
      if (object !== null) {
        unregister.current = core.registry.register(object, delegate);
      }
    },
    [core, delegate],
  );
}

/**
 * Make a one-element mesh pickable. Pass `null` to keep the mesh on the
 * registry but never a selection (see the ground plane in `ThreeDScene`).
 */
export function usePickable(target: PickTarget | null): (object: Object3D | null) => void {
  const latest = useRef<PickTarget | null>(target);
  latest.current = target;

  const resolverRef = useRef<PickResolver | null>(null);
  resolverRef.current = () => latest.current;

  return usePickableDelegate(resolverRef);
}

/**
 * Make a mesh pickable through a resolver — anything instanced, per-face, or
 * conditional. `null` keeps the mesh registered but unpickable (a hit on it
 * still occludes nothing and resolves to empty), which is how layers toggle
 * pickability without churning the registry.
 */
export function usePickableResolver(
  resolver: PickResolver | null,
): (object: Object3D | null) => void {
  const resolverRef = useRef<PickResolver | null>(resolver);
  resolverRef.current = resolver;
  return usePickableDelegate(resolverRef);
}

/**
 * Make an `InstancedMesh` pickable, mapping `instanceId` → element id. The
 * array form is read live on every resolve — a layer may rewrite its id array
 * in place as instances are recycled (see `DimensionLayer`'s live-ids note)
 * without re-registering.
 */
export function usePickableInstances(
  kind: PickKind,
  ids: InstanceIdLookup,
  storeyId: string | null = null,
): (object: Object3D | null) => void {
  const latest = useRef({ kind, ids, storeyId });
  latest.current = { kind, ids, storeyId };

  const resolverRef = useRef<PickResolver | null>(null);
  resolverRef.current = (intersection) => {
    const current = latest.current;
    const instanceId = intersection.instanceId;
    if (instanceId === undefined) return null;
    const id =
      typeof current.ids === 'function'
        ? current.ids(instanceId)
        : (current.ids[instanceId] ?? null);
    return id === null ? null : { kind: current.kind, id, storeyId: current.storeyId };
  };

  return usePickableDelegate(resolverRef);
}
