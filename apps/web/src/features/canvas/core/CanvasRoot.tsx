/**
 * CanvasRoot.tsx — the one `<Canvas>` in the product.
 *
 * Both the Phase 4 plan editor and the Phase 5 3D view mount inside this, over
 * the same scene graph, with the same picker and the same selection. There is
 * no second canvas and no second renderer; switching views is a camera swap
 * (see `CameraRig`).
 *
 * WHAT THIS COMPONENT IS RESPONSIBLE FOR
 *   · creating the `CanvasCore` and handing it to the tree through context
 *   · renderer settings that are §14 budget decisions, not taste
 *   · resize
 *   · routing pointer input through one normalised path (`useCanvasControls`)
 *   · keeping the drawing on the design system's paper colour in both themes
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 *   · own tools, geometry, or selection state. Tools are state machines in
 *     `features/canvas/tools`; the model store is the only writer of model
 *     state. This component never dispatches an op.
 *   · use react-three-fiber's pointer events. See `pickRegistry.ts` for why.
 *
 * §14 — HOW THE FRAME BUDGET IS MET
 *   1. `frameloop="demand"`: an idle canvas renders zero frames. Every render
 *      is asked for explicitly, by `ViewportController.commit()` or by a module
 *      calling `core.invalidate()` after mutating something.
 *   2. Camera state lives outside React (`viewport.ts`), so a pan is a vector
 *      write and a draw — no reconciliation, no component re-render.
 *   3. Pointer moves are coalesced to one per animation frame, and the hit test
 *      inside them is lazy (`useCanvasControls`).
 *   4. `dpr` is capped at 2 (`DPR_CAP`), which roughly halves fragment work on
 *      a 3× display for a drawing made of hairlines.
 *   5. The grid is one quad and one shader — one draw call, never rebuilt.
 *   6. Feature modules are expected to batch: instanced meshes for furniture
 *      and openings, one merged geometry per wall layer. The pick registry is
 *      built for that (`registerInstanced`), so batching costs no interactivity.
 */

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Canvas } from '@react-three/fiber';

import { cn } from '@garh/ui';

import { CameraRig } from './CameraRig';
import {
  DPR_CAP,
  DPR_FLOOR,
  RESIZE_DEBOUNCE_MS,
  SNAP_COARSE_MM,
  type CanvasMode,
} from './constants';
import { CanvasCore, CanvasCoreContext } from './context';
import { refreshCanvasTheme, watchCanvasTheme } from './materials';
import { useCanvasControls, type CanvasControlsCallbacks } from './useCanvasControls';

export interface CanvasRootProps extends CanvasControlsCallbacks {
  /** `'2d'` orthographic plan, `'3d'` perspective. Wire to `ui.viewMode`. */
  mode?: CanvasMode | undefined;
  /** Snap module in mm. Wire to `selectSnapStepMm` — 115, 25, or 0 for off. */
  snapModuleMm?: number | undefined;
  /** Storey being edited; picks are filtered to it. Wire to `ui.activeStoreyId`. */
  activeStoreyId?: string | null | undefined;
  /** FFL of that storey — the plane the grid draws on and empty picks land on. */
  planeElevationMm?: number | undefined;
  /** Building height, used when fitting the 3D camera. */
  fitHeightMm?: number | undefined;

  /** Suspend input (a modal owns the pointer). */
  enabled?: boolean | undefined;
  /** Built-in pan/zoom gestures. Default true. */
  navigation?: boolean | undefined;
  /** Hover picking. Default true. */
  hover?: boolean | undefined;

  /**
   * Handed the core once, on mount. The page keeps it so the tool rail, the
   * keyboard map and the storey tabs can call `core.viewport.fitBbox(...)`
   * without threading refs through the scene.
   */
  onCoreReady?: ((core: CanvasCore) => void) | undefined;

  /** Scene contents: walls, rooms, dimensions, tool previews. */
  children?: ReactNode;
  /** DOM overlay above the canvas — HUD chips, the scale readout, tooltips. */
  overlay?: ReactNode;

  className?: string | undefined;
  /** Screen-reader name. §15 wants the canvas to have a keyboard equivalent. */
  ariaLabel?: string | undefined;
}

export function CanvasRoot({
  mode = '2d',
  snapModuleMm = SNAP_COARSE_MM,
  activeStoreyId = null,
  planeElevationMm = 0,
  fitHeightMm,
  enabled = true,
  navigation = true,
  hover = true,
  onCoreReady,
  children,
  overlay,
  className,
  ariaLabel = 'Drawing canvas',
  ...callbacks
}: CanvasRootProps): JSX.Element {
  const core = useMemo(() => new CanvasCore(), []);
  const [container, setContainer] = useState<HTMLDivElement | null>(null);

  // Props the core reads on every pointer event. Mirrored onto the mutable core
  // rather than held in state: nothing should re-render because the snap module
  // changed, and the pointer path reads them directly.
  useEffect(() => {
    core.snapModuleMm = snapModuleMm;
    core.activeStoreyId = activeStoreyId;
  }, [core, snapModuleMm, activeStoreyId]);

  useEffect(() => {
    core.viewport.setPlaneElevationMm(planeElevationMm);
  }, [core, planeElevationMm]);

  useEffect(() => {
    if (fitHeightMm !== undefined) core.viewport.setFitHeightMm(fitHeightMm);
  }, [core, fitHeightMm]);

  useEffect(() => {
    onCoreReady?.(core);
  }, [core, onCoreReady]);

  useEffect(() => () => core.dispose(), [core]);

  // Theme: recolour the shared materials in place and ask for one frame. The
  // canvas is `frameloop="demand"`, so without the invalidate a theme toggle
  // would not show until the next time you touched the drawing.
  useEffect(() => {
    refreshCanvasTheme();
    return watchCanvasTheme(() => {
      core.invalidate();
    });
  }, [core]);

  useCanvasControls(container, {
    core,
    enabled,
    navigation,
    hover,
    ...callbacks,
  });

  return (
    <div
      ref={setContainer}
      // `bg-surface-sunken` is the drawing well from the design tokens, and the
      // WebGL context is alpha-blended over it — one source of truth for the
      // paper colour in light and dark, with no colour-space conversion to get
      // subtly wrong on the GL side.
      className={cn(
        'relative h-full w-full touch-none overflow-hidden bg-surface-sunken',
        className,
      )}
      role="application"
      aria-label={ariaLabel}
      tabIndex={0}
    >
      <Canvas
        // §14. Every one of these four is a budget decision.
        frameloop="demand"
        dpr={[DPR_FLOOR, DPR_CAP]}
        resize={{ scroll: false, debounce: { scroll: 0, resize: RESIZE_DEBOUNCE_MS } }}
        gl={{
          antialias: true,
          // Transparent so the container's themed background shows through.
          alpha: true,
          powerPreference: 'high-performance',
          // Nothing in the drawing set needs a stencil buffer, and asking for
          // one costs memory bandwidth on every clear.
          stencil: false,
          preserveDrawingBuffer: false,
        }}
        // The default camera is replaced by the rig in a layout effect, before
        // the first frame; declaring it orthographic here avoids a one-frame
        // perspective flash on mount.
        orthographic
        camera={{ position: [0, 100, 0], up: [0, 0, -1], near: 0.01, far: 400 }}
        style={{ position: 'absolute', inset: 0 }}
      >
        {/* Inside the <Canvas>: R3F reconciles its children with a separate
            React root, and context does not cross that boundary. */}
        <CanvasCoreContext.Provider value={core}>
          <CameraRig core={core} mode={mode} />
          {children}
        </CanvasCoreContext.Provider>
      </Canvas>

      {/* DOM overlay: HUD, chips, readouts. Outside the GL context on purpose —
          text in the DOM is accessible, selectable and free. */}
      {overlay === undefined ? null : (
        <div className="pointer-events-none absolute inset-0">{overlay}</div>
      )}
    </div>
  );
}
