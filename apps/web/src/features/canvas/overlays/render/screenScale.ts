/**
 * screenScale.ts — keeping overlay text and handles a CONSTANT SIZE ON SCREEN
 * without re-rendering React.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE §14 PROBLEM THIS SOLVES
 * ────────────────────────────────────────────────────────────────────────────
 * A dimension label must be ~12 px tall at 1:20 and at 1:500. Its size in world
 * units is therefore `px × mmPerPx`, which changes on every frame of a zoom.
 * The obvious implementation — `const mmPerPx = useMmPerPx()` and pass it down
 * — re-renders the whole overlay tree once per animation frame while the user
 * scrolls the wheel. On a G+2 demo that is a few hundred components of
 * reconciliation inside a 16 ms budget, for values only the GPU reads.
 *
 * So nothing here is React state. Objects register themselves in a `Set`, the
 * hook subscribes ONCE to `ViewportController`, and each commit walks the set
 * writing `object.scale` in place. No allocation, no reconciliation, no
 * `useFrame` (the canvas is `frameloop="demand"`, and the controller has
 * already asked for the frame by the time our listener runs).
 *
 * The cost is one scalar multiply and three float writes per registered object
 * per committed camera change: for 300 labels that is under 0.05 ms, measured
 * as arithmetic rather than guessed.
 */

import { useCallback, useEffect, useMemo, useRef, type DependencyList } from 'react';
import type { Object3D } from 'three';

import { useCanvasCore, WORLD_UNITS_PER_MM } from '../../core';

/**
 * World units per screen pixel at a given zoom.
 *
 * `mmPerPx` is the canvas's zoom scalar (see `core/constants.ts`), and world
 * units are metres, so this is the single conversion every screen-constant
 * overlay needs.
 */
export function worldPerPx(mmPerPx: number): number {
  return mmPerPx * WORLD_UNITS_PER_MM;
}

export interface ScreenScaleHandle {
  /**
   * Ref callback for the CONTAINER group. Every direct child of it is scaled so
   * that one of the child's local units renders as `pxPerUnit` CSS pixels.
   */
  readonly ref: (object: Object3D | null) => void;
  /** Apply the current scale now. Call after adding children outside React. */
  readonly sync: () => void;
  /** Current world-units-per-pixel, for callers doing their own maths. */
  readonly current: () => number;
}

/**
 * Keep a group of labels at a constant apparent size.
 *
 * ```tsx
 * const scale = useScreenScale(12);          // 1 child unit = 12 CSS px
 * …
 * <group ref={scale.ref}>
 *   {labels.map(l => (
 *     <group key={l.id} position={[x, y, z]}>  // world mm, unscaled
 *       <Text fontSize={1} …/>                 // renders 12 px tall at any zoom
 *     </group>
 *   ))}
 * </group>
 * ```
 *
 * ONE CONTAINER, NOT ONE REGISTRATION PER LABEL. The obvious design — every
 * label group registering itself in a `Set` — leaks: React 18 hands a callback
 * ref `null` on detach without saying WHICH object it is detaching, so a set
 * cannot remove the right entry. Labels churn on every document edit (their
 * keys are segment ids), so the set would grow without bound across a session
 * and the per-frame loop would walk hundreds of orphaned objects.
 *
 * Iterating `container.children` instead makes React's own reconciliation the
 * lifecycle: a label that unmounts is gone from the list, with nothing to
 * clean up.
 *
 * THE CHILD is scaled, never the container — the children carry world-space
 * positions, and scaling their parent would scale those positions too, sliding
 * every label towards the origin as you zoomed out.
 */
export function useScreenScale(pxPerUnit: number): ScreenScaleHandle {
  const core = useCanvasCore();
  const container = useRef<Object3D | null>(null);
  const pxRef = useRef(pxPerUnit);
  pxRef.current = pxPerUnit;

  const apply = useCallback((): void => {
    const parent = container.current;
    if (parent === null) return;
    const k = worldPerPx(core.viewport.mmPerPx) * pxRef.current;
    const children = parent.children;
    // Indexed loop, no iterator allocation — this runs on every camera commit.
    // eslint-disable-next-line @typescript-eslint/prefer-for-of -- see above
    for (let i = 0; i < children.length; i++) {
      children[i]?.scale.setScalar(k);
    }
  }, [core]);

  useEffect(() => {
    apply();
    // Synchronous, not rAF-coalesced: `CameraRig` reads the same commit to move
    // the camera, and a label that lags the camera by a frame visibly swims.
    return core.viewport.subscribe(apply);
  }, [core, apply]);

  const ref = useCallback(
    (object: Object3D | null) => {
      container.current = object;
      if (object !== null) apply();
    },
    [apply],
  );

  return useMemo(
    () => ({ ref, sync: apply, current: () => worldPerPx(core.viewport.mmPerPx) }),
    [ref, apply, core],
  );
}

/**
 * Subscribe to camera commits with a plain callback — for layers that rewrite
 * GEOMETRY rather than scale an object. Dimension baselines hang a constant
 * number of pixels off the building, so their world coordinates change with
 * zoom; there is no way to express that as a scale.
 *
 * `deps` is not optional in practice. The effect must also run when the thing
 * being drawn changes, not only when the camera moves — a layer subscribed to
 * the camera alone would keep drawing last edit's dimensions until the user
 * happened to pan.
 *
 * Every run ends in `core.invalidate()`. The canvas is `frameloop="demand"`
 * (see `core/CanvasRoot.tsx`): mutating a buffer without asking for a frame
 * means the change appears the next time something else happens to render.
 */
export function useViewportEffect(effect: () => void, deps: DependencyList = []): void {
  const core = useCanvasCore();
  const latest = useRef(effect);
  latest.current = effect;

  useEffect(() => {
    const run = (): void => {
      latest.current();
      core.invalidate();
    };
    run();
    return core.viewport.subscribe(run);
    // `deps` is spread so a layer can re-run on its own inputs. The lint rule
    // cannot see through the spread; the alternative is every layer writing
    // this subscription by hand.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [core, ...deps]);
}
