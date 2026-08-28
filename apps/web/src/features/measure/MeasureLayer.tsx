/**
 * MeasureLayer.tsx — the measurements, in the shared scene graph.
 *
 * This component owns LIFECYCLE and nothing else. The geometry, the pick
 * proxies and the registry call all live in `MeasureScene`, a plain class that
 * a spec can construct without a renderer — see its header for why that split
 * is the answer to bug pattern 4 rather than a comment claiming it is.
 *
 * So there is exactly one thing to get right here: `scene.attach(core.registry)`
 * on mount, its returned detach on unmount. Everything else is a `<primitive>`.
 *
 * §14. The line geometry is rewritten imperatively inside `useViewportEffect`
 * (measurements are ticked and click-targeted in SCREEN pixels, so their world
 * geometry genuinely changes with zoom) — no React reconciliation there. The
 * only React work per pointer frame is the LABEL subtree, which is a handful of
 * `<Text>` nodes whose positions are plain model millimetres; the alternative,
 * placing them imperatively like `DimensionLayer` does, buys nothing because
 * ours do not depend on zoom.
 */

import { Suspense, useEffect, useMemo, useRef, type JSX } from 'react';
import { Text } from '@react-three/drei';

import type { UnitsDisplay } from '@garh/model';

import { useCanvasCore, WORLD_UNITS_PER_MM } from '../canvas/core';
import {
  DIMENSION_RENDER_ORDER,
  getOverlayMaterials,
  LABEL_FONT_SIZE_LOCAL,
  LABEL_FONT_URL,
} from '../canvas/overlays/render/overlayMaterials';
import { useScreenScale, useViewportEffect } from '../canvas/overlays/render/screenScale';
import { draftLabels, measurementLabels, type MeasureLabel } from './labels';
import { MeasureScene } from './scene';
import { measurementsForStorey, useMeasureStore } from './store';

/** Leg-label height in CSS pixels. Small — a plan is read at arm's length. */
export const MEASURE_LABEL_PX = 11;

/** The headline number is drawn 20% larger than its legs. */
const HEADLINE_SCALE = 1.2;

export interface MeasureLayerProps {
  /** The storey being drawn; measurements taken elsewhere are not shown. */
  readonly storeyId: string | null;
  /** FFL of that storey — the plane measurements are drawn on. */
  readonly elevationMm?: number | undefined;
  readonly display: UnitsDisplay;
  /** Self-hosted font. See `LABEL_FONT_URL` before changing this. */
  readonly fontUrl?: string | undefined;
}

export function MeasureLayer({
  storeyId,
  elevationMm = 0,
  display,
  fontUrl = LABEL_FONT_URL,
}: MeasureLayerProps): JSX.Element | null {
  const core = useCanvasCore();
  const materials = getOverlayMaterials();
  const scale = useScreenScale(MEASURE_LABEL_PX);

  const all = useMeasureStore((s) => s.measurements);
  const draft = useMeasureStore((s) => s.draft);
  const selectedId = useMeasureStore((s) => s.selectedId);
  const visible = useMeasureStore((s) => s.visible);

  // Lazily, not `useRef(new MeasureScene())`: the eager form builds (and leaks)
  // a scene, two geometries and an instanced mesh on every render.
  const sceneRef = useRef<MeasureScene | null>(null);
  // The shared overlay materials, handed to the scene rather than imported by
  // it — see `MeasureSceneMaterials`. Measurements are annotation, so they are
  // drawn in the dimension ink and highlight in the brand colour, which is what
  // keeps a measurement and a dimension string looking like the same family.
  sceneRef.current ??= new MeasureScene({
    ink: materials.dimensionLine,
    active: materials.dimensionActive,
    pickProxy: materials.pickProxy,
  });
  const scene = sceneRef.current;

  // THE REGISTRATION. Nothing else in this file makes a measurement clickable.
  useEffect(() => {
    const detach = scene.attach(core.registry);
    return () => {
      detach();
    };
  }, [scene, core]);

  // STRICT MODE. React 18 mounts effects twice in development, so this runs
  // dispose → attach → (later) dispose. That is survivable and is the same
  // pattern `DimensionLayer` uses: three's `dispose()` releases GPU resources
  // and leaves the JS objects intact, so the renderer re-uploads on the next
  // frame, and `attach()` re-registers the very same object. It is only safe
  // because nothing here frees JS state — do not add teardown that does.
  useEffect(() => () => scene.dispose(), [scene]);

  const measurements = useMemo(() => measurementsForStorey(all, storeyId), [all, storeyId]);

  useViewportEffect(() => {
    scene.update({
      measurements,
      draft,
      mmPerPx: core.viewport.mmPerPx,
      elevationMm,
      selectedId,
      visible,
    });
    // `measurements`/`draft` are inputs as much as the camera is: without them
    // the layer would keep drawing the previous state until the user panned.
  }, [measurements, draft, selectedId, visible, elevationMm]);

  const labels = useMemo(() => {
    const out: MeasureLabel[] = [];
    for (const m of measurements) out.push(...measurementLabels(m, display));
    if (draft !== null) out.push(...draftLabels(draft, display));
    return out;
  }, [measurements, draft, display]);

  // Labels mounted by this render have not been through a camera commit yet, so
  // their scale is still 1 — sync now rather than letting them render one frame
  // at world size, which reads as a flash of enormous text.
  useEffect(() => {
    scale.sync();
  }, [labels, scale]);

  return (
    <group name="measure-overlay">
      <primitive object={scene.root} />

      {/*
        THE SUSPENSE BOUNDARY IS LOAD-BEARING — the same trap `DimensionLayer`
        documents: drei's `<Text>` suspends until troika has the font, an
        uncaught suspension is re-thrown by `<Canvas>` into the DOM tree, and
        the route-level boundary then hides the whole plan tab. Caught here, a
        broken font costs label TEXT only; the lines and the click targets are
        outside the boundary and stay live.
      */}
      <group ref={scale.ref} visible={visible}>
        <Suspense fallback={null}>
          {labels.map((label) => (
            <group
              key={label.id}
              position={[
                label.atMm.x * WORLD_UNITS_PER_MM,
                elevationMm * WORLD_UNITS_PER_MM,
                -label.atMm.y * WORLD_UNITS_PER_MM,
              ]}
            >
              <Text
                font={fontUrl}
                fontSize={LABEL_FONT_SIZE_LOCAL * (label.emphasis ? HEADLINE_SCALE : 1)}
                anchorX="center"
                anchorY={label.emphasis ? 'middle' : 'bottom'}
                // Flat on the plan, reading west-to-east like every other label.
                rotation={[-Math.PI / 2, 0, 0]}
                color={
                  label.emphasis
                    ? materials.dimensionActive.color.getStyle()
                    : materials.dimensionLine.color.getStyle()
                }
                renderOrder={DIMENSION_RENDER_ORDER + 1}
                // Coplanar with the plan in an orthographic top view, so depth
                // testing can only z-fight; `renderOrder` does the ordering.
                material-depthTest={false}
                material-depthWrite={false}
              >
                {label.text}
              </Text>
            </group>
          ))}
        </Suspense>
      </group>
    </group>
  );
}
