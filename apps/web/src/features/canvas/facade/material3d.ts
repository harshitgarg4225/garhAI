/**
 * material3d.ts — the ONE material every facade mesh wears, and the shadow
 * contract that goes with it.
 *
 * Facade components are the elements whose whole purpose is to shade —
 * a chajja over a window, a porch over the door, a cladding band catching
 * the afternoon sun. Until 2026-09-16 they were unlit `MeshBasicMaterial`
 * with a Lambert term baked against one fixed direction, so they cast no
 * shadow, received none, and did not move with the sun scrubber: the sun
 * study lied on exactly the elements it exists for.
 *
 * Now: a `MeshStandardMaterial` with vertex colours (the kit colorway rides
 * in the geometry, so one material serves every component and every kit —
 * no per-colour cache), lit by the scene's `SunLight`, casting AND receiving
 * shadows. Pinned by `material3d.test.ts`; the sun-angle pixel check lives
 * in `e2e/tests/three-d.spec.ts`.
 */

import { FrontSide, MeshStandardMaterial } from 'three';

/** Shadow flags every facade mesh must carry. Spread onto the `<mesh>`. */
export const FACADE_MESH_SHADOW = { castShadow: true, receiveShadow: true } as const;

/**
 * A fresh facade material. Callers own its lifetime (dispose on unmount).
 * FrontSide: the boxes are closed and wound CCW seen from outside, so the
 * back faces are never visible and culling them halves the shadow-map cost.
 */
export function createFacadeMaterial(): MeshStandardMaterial {
  return new MeshStandardMaterial({
    vertexColors: true,
    roughness: 0.85,
    metalness: 0,
    side: FrontSide,
  });
}
