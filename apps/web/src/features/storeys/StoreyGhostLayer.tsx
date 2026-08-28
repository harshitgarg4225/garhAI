/**
 * StoreyGhostLayer.tsx — the storey below, faded, under the one being drawn.
 *
 * This is how architects actually work: you draw the first floor over a ghost
 * of the ground floor so the walls stack, the shafts line up and the stair
 * lands where it left. Without it a G+2 is possible but not usable.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * IT IS NOT A PICK TARGET, AND THAT IS THE POINT
 * ════════════════════════════════════════════════════════════════════════════
 * This layer never touches `PickRegistry`. Not `usePickable`, not
 * `usePickableResolver`, not `usePickableInstances`. The argument is
 * `features/underlay/UnderlayLayer.tsx`'s, one step sharper: the ghost is a
 * full set of WALLS sitting under the walls you are drawing, and a ghost wall
 * and a real wall are the same `PICK_PRIORITY`. Register it and a click meant
 * for the first floor would sometimes select a ground-floor wall — which the
 * tools would then move, on a storey you are not looking at.
 *
 * `ghost.test.ts` measures this rather than asserting it: it builds a real
 * `Mesh` from this layer's real buffers, points a real camera at it, and calls
 * the real `pickAt` — the same function every click in the product goes
 * through — and requires the answer to be `empty`. The repository has already
 * shipped a layer that believed it was registered and was not (the furniture
 * bug); this is the mirror of it, and a mirror needs the same measurement.
 *
 * There are also no react-three-fiber pointer handlers here, per the §12 rule
 * in `pickRegistry.ts` — which follows for free once nothing is pickable.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHERE IT SITS
 * ════════════════════════════════════════════════════════════════════════════
 * Drawn 2 mm BELOW the active storey's FFL — under the image underlay's own
 * 1 mm drop, so the two never fight for a fragment — and with a render order
 * between the grid and the plan's own layers. So: the scan is at the bottom,
 * then the drafting grid, then the ghost, then everything on the storey you are
 * editing.
 *
 * The ORDER is what does that work, not the depth buffer. Every 2D material in
 * this app — the grid's, the plan's, the underlay's and both of this layer's —
 * sets `depthWrite: false`, because in a top-down orthographic view the whole
 * drawing is coplanar to within a few millimetres and depth cannot sort it. The
 * drop is therefore a statement about which plane a thing belongs to, and the
 * ghost can never occlude the storey being edited.
 *
 * 2D only. In 3D every storey is already visible in its own right and a faded
 * copy of one of them, floating 2 mm under a slab, would be a bug report.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE FRAME BUDGET (§14)
 * ════════════════════════════════════════════════════════════════════════════
 * Buffers are rebuilt only when the document, the storey or the elevation
 * changes — `useMemo` on `house` object identity, which the model store
 * replaces exactly once per op group. Opacity is a material mutation, not a
 * rebuild, so dragging the slider costs one uniform write and one frame.
 * `frameloop="demand"` means each of those has to ask for its frame explicitly.
 */

import { useEffect, useMemo } from 'react';
import {
  BufferAttribute,
  BufferGeometry,
  DoubleSide,
  LineBasicMaterial,
  MeshBasicMaterial,
} from 'three';

import type { HouseModel } from '@garh/model';

import {
  LAYER_RENDER_ORDER,
  readTokenColor,
  useCanvasCore,
  watchCanvasTheme,
} from '../canvas/core';
import { buildStoreyGhost, storeyBelow } from './ghostGeometry';
import { useStoreysStore } from './store';

/**
 * How far below the active FFL the ghost sits, in mm.
 *
 * Below the image underlay's 1 mm so a project using both does not have two
 * coplanar aids; small enough that the orthographic depth range (400 m) treats
 * it as the same plane for every practical purpose.
 */
const GHOST_DROP_MM = 2;

/** Between the grid (0) and the plan's slab/room layers (10). */
const GHOST_RENDER_ORDER = LAYER_RENDER_ORDER.slab - 5;

/**
 * The outline is drawn stronger than the poché.
 *
 * A flat wash at the fill's opacity reads as a smudge at 1:100; the edges are
 * what tells you where the wall below actually is, which is the only reason
 * this layer exists. Capped so the ghost can never approach the drawing's own
 * line weight — "which floor am I on" must stay answerable at a glance.
 */
const LINE_OPACITY_FACTOR = 1.6;
const MAX_LINE_OPACITY = 0.55;

export interface StoreyGhostLayerProps {
  readonly house: HouseModel;
  /** The storey being edited. The ghost is the one directly below it. */
  readonly activeStoreyId: string | null;
  /** Finished floor level of the ACTIVE storey, mm. The drop is applied here. */
  readonly elevationMm: number;
}

export function StoreyGhostLayer({
  house,
  activeStoreyId,
  elevationMm,
}: StoreyGhostLayerProps): JSX.Element | null {
  const core = useCanvasCore();
  const visible = useStoreysStore((s) => s.ghostVisible);
  const opacity = useStoreysStore((s) => s.ghostOpacity);

  const below = storeyBelow(house, activeStoreyId);
  const belowId = below?.id ?? null;

  const ghost = useMemo(
    () => buildStoreyGhost(house, belowId, elevationMm - GHOST_DROP_MM),
    [house, belowId, elevationMm],
  );

  // One material each for the life of the layer; both are mutated in place
  // afterwards. A new material is a shader compile, and a shader compile in the
  // middle of an opacity drag is a dropped frame you cannot get back.
  const fillMaterial = useMemo(
    () =>
      new MeshBasicMaterial({
        transparent: true,
        // No depth writes: the storey being edited must never be occluded by
        // the ghost of the one under it, whatever the render order ends up as.
        depthWrite: false,
        toneMapped: false,
        // DoubleSide, exactly as `planMaterials.wallFill` is: a wall quad's
        // winding follows the direction the wall was drawn in, so under
        // FrontSide half the ghost — every wall drawn "the other way" — would
        // simply not be there, and nobody would know which half.
        side: DoubleSide,
      }),
    [],
  );
  const lineMaterial = useMemo(
    () => new LineBasicMaterial({ transparent: true, depthWrite: false, toneMapped: false }),
    [],
  );

  useEffect(
    () => () => {
      fillMaterial.dispose();
      lineMaterial.dispose();
    },
    [fillMaterial, lineMaterial],
  );

  // Colour comes from the design tokens, and is re-read when the theme flips —
  // a hard-coded grey would be invisible in dark mode and wrong in light.
  useEffect(() => {
    const paint = (): void => {
      readTokenColor('--garh-ink-subtle', fillMaterial.color);
      readTokenColor('--garh-ink-subtle', lineMaterial.color);
      fillMaterial.needsUpdate = true;
      lineMaterial.needsUpdate = true;
      core.invalidate();
    };
    paint();
    return watchCanvasTheme(paint);
  }, [core, fillMaterial, lineMaterial]);

  useEffect(() => {
    fillMaterial.opacity = opacity;
    lineMaterial.opacity = Math.min(MAX_LINE_OPACITY, opacity * LINE_OPACITY_FACTOR);
    core.invalidate();
  }, [core, fillMaterial, lineMaterial, opacity]);

  // The buffers, wrapped for exactly as long as they are the current ones. The
  // disposal is the point: without it every op leaks two GPU buffers, and the
  // symptom — a tab that gets slower the longer you draw — is miserable to
  // diagnose later. (`PlanScene.useGeometry` makes the same argument.)
  const fillGeometry = useMemo(() => {
    const g = new BufferGeometry();
    g.setAttribute('position', new BufferAttribute(ghost.fillPositions, 3));
    if (ghost.fillPositions.length > 0) g.computeBoundingSphere();
    return g;
  }, [ghost.fillPositions]);
  const lineGeometry = useMemo(() => {
    const g = new BufferGeometry();
    g.setAttribute('position', new BufferAttribute(ghost.linePositions, 3));
    if (ghost.linePositions.length > 0) g.computeBoundingSphere();
    return g;
  }, [ghost.linePositions]);

  useEffect(() => () => fillGeometry.dispose(), [fillGeometry]);
  useEffect(() => () => lineGeometry.dispose(), [lineGeometry]);

  useEffect(() => {
    core.invalidate();
  }, [core, ghost, visible]);

  if (!visible || below === null) return null;
  if (ghost.triangleCount === 0 && ghost.segmentCount === 0) return null;

  return (
    <group name="storey-ghost">
      {ghost.triangleCount === 0 ? null : (
        <mesh
          geometry={fillGeometry}
          material={fillMaterial}
          renderOrder={GHOST_RENDER_ORDER}
          /* One merged buffer covering the whole floor plate; three's culling
             test against its bounding sphere is exactly the case that blinks
             geometry at the frustum edge during a pan. */
          frustumCulled={false}
        />
      )}
      {ghost.segmentCount === 0 ? null : (
        <lineSegments
          geometry={lineGeometry}
          material={lineMaterial}
          renderOrder={GHOST_RENDER_ORDER + 1}
          frustumCulled={false}
        />
      )}
    </group>
  );
}

export default StoreyGhostLayer;
