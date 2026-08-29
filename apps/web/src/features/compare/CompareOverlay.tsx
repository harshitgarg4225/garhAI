/**
 * CompareOverlay.tsx — the change boxes from a version compare, on the plan (C-8).
 *
 * The visual half of "version branches with visual compare". A list of 41 changed
 * elements is a list; a rectangle around each of them on the drawing is an answer.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * IT IS NOT A PICK TARGET, AND THAT IS DELIBERATE
 * ════════════════════════════════════════════════════════════════════════════
 * This layer never touches `PickRegistry` — not `usePickable`, not a react-three-fiber
 * pointer handler. The boxes sit directly on top of the very elements they describe, so
 * registering them would mean a click aimed at a changed wall selected the annotation
 * ABOUT the wall instead. The whole point of the overlay is to let an architect look at
 * what changed and then go and edit it.
 *
 * `StoreyGhostLayer` made the same call for the same reason and measured it rather than
 * asserting it; this layer's geometry is line segments with no mesh at all, so there is
 * nothing for `pickAt` to hit even in principle. That is a stronger guarantee than a
 * registration check, and it is why there is no mesh here.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHERE IT SITS
 * ════════════════════════════════════════════════════════════════════════════
 * Above the drawing, with the other overlays, and with `depthWrite: false` like every
 * other 2D material in this app — in a top-down orthographic view the whole drawing is
 * coplanar and the depth buffer cannot sort it, so render ORDER is what decides.
 *
 * 2D only. In 3D a set of flat rectangles floating at floor level reads as geometry.
 */

import { useEffect, useMemo } from 'react';
import { BufferAttribute, BufferGeometry, LineBasicMaterial } from 'three';

import {
  LAYER_RENDER_ORDER,
  readTokenColor,
  useCanvasCore,
  watchCanvasTheme,
} from '../canvas/core';
import { compareBoxesForStorey, useCompareStore } from './store';

/** Just above the plan's own lines, with the selection overlays. */
const COMPARE_RENDER_ORDER = LAYER_RENDER_ORDER.selection - 1;

/** A hair above the floor, so the boxes never z-fight the walls they surround. */
const LIFT_MM = 3;

export interface CompareOverlayProps {
  /** The storey on screen. Boxes for other storeys are not drawn. */
  readonly storeyId: string | null;
  /** Finished floor level of that storey, mm. */
  readonly elevationMm: number;
}

export function CompareOverlay({ storeyId, elevationMm }: CompareOverlayProps): JSX.Element | null {
  const core = useCanvasCore();
  // Subscribed to the whole store rather than a slice: the boxes depend on the result,
  // the visibility flag AND the storey, and three separate selectors recomputing
  // independently is how a layer ends up one frame out of date.
  const state = useCompareStore();
  const boxes = compareBoxesForStorey(state, storeyId);

  const geometry = useMemo(() => {
    if (boxes.length === 0) return null;
    // Four segments per box, two vertices each, three floats each.
    const positions = new Float32Array(boxes.length * 4 * 2 * 3);
    const z = elevationMm + LIFT_MM;
    let i = 0;
    const push = (x: number, y: number): void => {
      positions[i++] = x;
      positions[i++] = y;
      positions[i++] = z;
    };
    for (const box of boxes) {
      const [minX, minY, maxX, maxY] = box as [number, number, number, number];
      push(minX, minY);
      push(maxX, minY);
      push(maxX, minY);
      push(maxX, maxY);
      push(maxX, maxY);
      push(minX, maxY);
      push(minX, maxY);
      push(minX, minY);
    }
    const next = new BufferGeometry();
    next.setAttribute('position', new BufferAttribute(positions, 3));
    return next;
  }, [boxes, elevationMm]);

  const material = useMemo(
    () => new LineBasicMaterial({ transparent: true, depthWrite: false, toneMapped: false }),
    [],
  );

  // The colour is a theme token read at runtime, not a literal, so the overlay follows
  // a theme switch like every other layer rather than staying the light-mode colour.
  useEffect(
    () =>
      watchCanvasTheme(() => {
        readTokenColor('--garh-warn', material.color);
        material.opacity = 0.9;
        material.needsUpdate = true;
        core.invalidate();
      }),
    [core, material],
  );

  useEffect(() => {
    core.invalidate();
  }, [core, geometry]);

  // Free the buffers when the compare changes, rather than leaving one set per compare
  // alive for the life of the session.
  useEffect(() => () => geometry?.dispose(), [geometry]);
  useEffect(() => () => material.dispose(), [material]);

  if (geometry === null) return null;

  return (
    <lineSegments
      geometry={geometry}
      material={material}
      renderOrder={COMPARE_RENDER_ORDER}
      frustumCulled={false}
    />
  );
}

export default CompareOverlay;
