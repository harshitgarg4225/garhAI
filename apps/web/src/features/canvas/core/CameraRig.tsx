/**
 * CameraRig.tsx — two cameras, one scene graph.
 *
 * §12 asks for an orthographic 2D view and a perspective 3D view over the same
 * geometry, and Phase 5's Tab key has to switch between them without a rebuild.
 * So both cameras are constructed once, live for the lifetime of the canvas,
 * and switching modes swaps which one R3F renders through. Nothing unmounts,
 * no geometry is rebuilt, no material recompiles (except the one-off tone
 * mapping change), and the plan view you come back to is exactly the plan view
 * you left.
 *
 * `camera.manual = true` on both is load-bearing. R3F otherwise "helpfully"
 * rewrites an orthographic camera's frustum to pixel units on every resize,
 * which would silently discard the `mmPerPx` zoom this whole module is built
 * on. With `manual`, the frustum is ours.
 *
 * ORIENTATION. The plan camera looks straight down (−Y) with `up = (0, 0, −1)`,
 * which puts model +X (east) to screen right and model +Y (north) to screen up.
 * That is the pairing the world mapping in `coords.ts` was chosen for; change
 * one without the other and every plan renders mirrored.
 *
 * PERF. The rig subscribes to the viewport controller directly and writes to
 * the camera objects. It never sets React state, so a pan is: pointer event →
 * controller mutation → this callback → `invalidate()` → one frame. No
 * reconciliation anywhere in that chain.
 */

import { useEffect, useMemo } from 'react';
import { useThree } from '@react-three/fiber';
import {
  ACESFilmicToneMapping,
  NoToneMapping,
  OrthographicCamera,
  PerspectiveCamera,
  Vector3,
} from 'three';

import { useIsomorphicLayoutEffect } from '@garh/ui';

import {
  ORTHO_EYE_HEIGHT_MM,
  ORTHO_FAR,
  ORTHO_NEAR,
  PERSP_FAR,
  PERSP_FOV_DEG,
  PERSP_NEAR,
  type CanvasMode,
} from './constants';
import { orbitEyeMm, orthoFrustumWorld } from './cameraMath';
import { mmToWorldXYZ } from './coords';
import type { CanvasCore } from './context';

export interface CameraRigProps {
  core: CanvasCore;
  mode: CanvasMode;
}

/** Scratch targets — module scope so a pan allocates nothing. */
const lookTarget = /* @__PURE__ */ new Vector3();

/**
 * `manual` is react-three-fiber's flag, not Three's: with it set, R3F stops
 * rewriting the camera's frustum/aspect on resize. It is not in `@types/three`,
 * hence the intersection type rather than a bare `as any`.
 */
type Manual<T> = T & { manual: boolean };

export function CameraRig({ core, mode }: CameraRigProps): null {
  const set = useThree((s) => s.set);
  const gl = useThree((s) => s.gl);
  const invalidate = useThree((s) => s.invalidate);
  const size = useThree((s) => s.size);

  const cameras = useMemo(() => {
    const ortho = new OrthographicCamera(
      -1,
      1,
      1,
      -1,
      ORTHO_NEAR,
      ORTHO_FAR,
    ) as Manual<OrthographicCamera>;
    // Plan view: north is screen-up. See the orientation note above.
    ortho.up.set(0, 0, -1);
    ortho.manual = true;

    const persp = new PerspectiveCamera(
      PERSP_FOV_DEG,
      1,
      PERSP_NEAR,
      PERSP_FAR,
    ) as Manual<PerspectiveCamera>;
    persp.up.set(0, 1, 0);
    persp.manual = true;

    return { ortho, persp };
  }, []);

  // R3F owns `invalidate`; the viewport controller is what everything else
  // holds, so the two are introduced here.
  useEffect(() => {
    core.invalidate = invalidate;
    core.viewport.attachInvalidate(invalidate);
    return () => {
      core.viewport.attachInvalidate(null);
      core.invalidate = () => undefined;
    };
  }, [core, invalidate]);

  // The canvas element's size is R3F's to measure and the controller's to know.
  useEffect(() => {
    core.viewport.setSize(size.width, size.height);
  }, [core, size.width, size.height]);

  // Push camera state. Called on every viewport commit — i.e. on every pan
  // step — so it must stay allocation-free. A layout effect, so the first
  // frame is drawn through a correctly-framed camera rather than R3F's
  // placeholder.
  useIsomorphicLayoutEffect(() => {
    const { ortho, persp } = cameras;
    const viewport = core.viewport;

    const sync = (): void => {
      if (viewport.mode === '2d') {
        const frustum = orthoFrustumWorld(viewport.view2d, viewport.sizePx);
        ortho.left = frustum.left;
        ortho.right = frustum.right;
        ortho.top = frustum.top;
        ortho.bottom = frustum.bottom;
        mmToWorldXYZ(
          viewport.view2d.centreMm.x,
          viewport.view2d.centreMm.y,
          ORTHO_EYE_HEIGHT_MM,
          ortho.position,
        );
        mmToWorldXYZ(
          viewport.view2d.centreMm.x,
          viewport.view2d.centreMm.y,
          viewport.planeElevationMm,
          lookTarget,
        );
        ortho.lookAt(lookTarget);
        ortho.updateProjectionMatrix();
        ortho.updateMatrixWorld();
      } else {
        const eye = orbitEyeMm(viewport.orbit);
        persp.aspect = viewport.aspect;
        mmToWorldXYZ(eye.x, eye.y, eye.z, persp.position);
        mmToWorldXYZ(
          viewport.orbit.targetMm.x,
          viewport.orbit.targetMm.y,
          viewport.orbit.targetMm.z,
          lookTarget,
        );
        persp.lookAt(lookTarget);
        persp.updateProjectionMatrix();
        persp.updateMatrixWorld();
      }
    };

    sync();
    return viewport.subscribe(sync);
  }, [cameras, core]);

  // Mode switch: swap which camera R3F renders through, and move the renderer
  // between "drafting sheet" and "building" settings. Layout effect for the
  // same reason as the sync above — no perspective flash on mount.
  useIsomorphicLayoutEffect(() => {
    const camera = mode === '2d' ? cameras.ortho : cameras.persp;
    core.viewport.setMode(mode);
    core.camera = camera;
    set({ camera });

    // Shadow maps and tone mapping cost nothing in a flat plan view and cost
    // real time in one. Changing `toneMapping` recompiles programs, so it
    // happens here (once per Tab press) and never during interaction.
    gl.shadowMap.enabled = mode === '3d';
    gl.toneMapping = mode === '3d' ? ACESFilmicToneMapping : NoToneMapping;

    core.viewport.commit();
  }, [cameras, core, gl, mode, set]);

  return null;
}
