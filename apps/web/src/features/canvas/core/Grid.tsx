/**
 * Grid.tsx — the drafting paper.
 *
 * One `<mesh>`: a unit quad, rotated flat, scaled to cover the view and moved
 * to follow the camera. It is never rebuilt, its geometry never changes, and
 * the shader (see `gridShader.ts`) draws all three grid levels plus the plot
 * axes in a single pass. Panning the grid is two vector writes, which is the
 * only version of this that fits in the §14 frame budget.
 *
 * The quad follows the camera rather than covering the whole site because the
 * grid is defined in model coordinates inside the shader — moving the quad
 * does not move the grid, it only changes which part of an infinite grid is
 * being rasterised.
 */

import { useEffect, useMemo, useRef } from 'react';
import { PlaneGeometry, type Mesh } from 'three';

import { LAYER_RENDER_ORDER } from './constants';
import { useCanvasCore } from './context';
import { mmToWorldXYZ } from './coords';
import { createGridMaterial, refreshGridMaterialTheme, updateGridMaterial } from './gridShader';
import { watchCanvasTheme } from './materials';

export interface GridProps {
  /** Show the 25 mm fine grid — the G toggle's `snapMode === 'fine'`. */
  fine?: boolean | undefined;
  /** Overall strength. 3D wants a quieter grid than a plan sheet does. */
  opacity?: number | undefined;
  visible?: boolean | undefined;
}

/** How much bigger than the viewport the quad is, so a pan never shows an edge. */
const COVER_FACTOR_2D = 1.6;

/** Minimum ground coverage in the 3D view, world units (metres). */
const MIN_COVER_3D = 200;

export function Grid({ fine = false, opacity, visible = true }: GridProps): JSX.Element {
  const core = useCanvasCore();
  const meshRef = useRef<Mesh | null>(null);

  const geometry = useMemo(() => new PlaneGeometry(1, 1), []);
  // Created once. `updateGridMaterial` mutates uniforms in place afterwards, so
  // toggling the fine grid never recompiles the program.
  const material = useMemo(
    () => createGridMaterial(opacity === undefined ? { fine } : { fine, opacity }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- created once on purpose
    [],
  );

  useEffect(() => {
    updateGridMaterial(material, opacity === undefined ? { fine } : { fine, opacity });
    core.invalidate();
  }, [core, fine, material, opacity]);

  useEffect(
    () =>
      watchCanvasTheme(() => {
        refreshGridMaterialTheme(material);
        core.invalidate();
      }),
    [core, material],
  );

  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
    },
    [geometry, material],
  );

  // Follow the camera. Subscribed imperatively: this runs on every pan step and
  // must not go anywhere near React.
  useEffect(() => {
    const viewport = core.viewport;

    const sync = (): void => {
      const mesh = meshRef.current;
      if (mesh === null) return;

      if (viewport.mode === '2d') {
        const widthWorld = (viewport.sizePx.width * viewport.view2d.mmPerPx) / 1000;
        const heightWorld = (viewport.sizePx.height * viewport.view2d.mmPerPx) / 1000;
        const cover = Math.max(widthWorld, heightWorld) * COVER_FACTOR_2D;
        mesh.scale.set(cover, cover, 1);
        mmToWorldXYZ(
          viewport.view2d.centreMm.x,
          viewport.view2d.centreMm.y,
          viewport.planeElevationMm,
          mesh.position,
        );
      } else {
        const cover = Math.max(MIN_COVER_3D, (viewport.orbit.distanceMm / 1000) * 8);
        mesh.scale.set(cover, cover, 1);
        mmToWorldXYZ(
          viewport.orbit.targetMm.x,
          viewport.orbit.targetMm.y,
          viewport.planeElevationMm,
          mesh.position,
        );
      }
    };

    sync();
    return viewport.subscribe(sync);
  }, [core]);

  return (
    <mesh
      ref={meshRef}
      geometry={geometry}
      material={material}
      rotation-x={-Math.PI / 2}
      renderOrder={LAYER_RENDER_ORDER.grid}
      // The quad is deliberately larger than the frustum and moves with the
      // camera; three's culling test on a stale bounding sphere would blink it.
      frustumCulled={false}
      visible={visible}
    />
  );
}
