/**
 * Plan millimetres → scene units. The ONE place this feature converts, and a
 * contract Phase 5 shares rather than re-derives.
 *
 * The canvas core owns the camera, so it also owns the answer to two questions
 * this module must be told rather than assume:
 *
 *   **Which way is up?** `'z-up'` (plan X→scene X, plan Y→scene Y, height→scene
 *   Z) is the default because it is the natural frame for an orthographic plan
 *   camera looking straight down at model coordinates. `'y-up'` is Three.js's
 *   own default and is what a walk-through camera usually wants, so both are
 *   implemented and the mapping is a prop, not a rewrite.
 *
 *   **How big is a unit?** `sceneUnitsPerMm` defaults to 1 — the scene works
 *   directly in millimetres, which keeps the numbers on screen identical to the
 *   numbers in the op log while debugging. Pass 0.001 for a metre-based scene.
 *
 * INTEGRATOR: if the canvas core's rig disagrees with these defaults, pass the
 * right values to `<FurnitureLayer>` once. Nothing else in this feature needs
 * to change, because nothing else knows the scene exists.
 *
 * Everything above this boundary is integer mm. Everything below it is
 * floating-point scene space, and never travels back.
 */

export type PlanAxes = 'z-up' | 'y-up';

export const DEFAULT_PLAN_AXES: PlanAxes = 'z-up';
export const DEFAULT_SCENE_UNITS_PER_MM = 1;

/** A plan point + height, in scene coordinates. */
export function scenePosition(
  xMm: number,
  yMm: number,
  heightMm: number,
  axes: PlanAxes,
  unitsPerMm: number,
): [number, number, number] {
  return axes === 'z-up'
    ? [xMm * unitsPerMm, yMm * unitsPerMm, heightMm * unitsPerMm]
    : [xMm * unitsPerMm, heightMm * unitsPerMm, -yMm * unitsPerMm];
}

/** Box extents (width along local X, depth along Y, height along Z) in scene axes. */
export function sceneScale(
  wMm: number,
  dMm: number,
  hMm: number,
  axes: PlanAxes,
  unitsPerMm: number,
): [number, number, number] {
  return axes === 'z-up'
    ? [wMm * unitsPerMm, dMm * unitsPerMm, hMm * unitsPerMm]
    : [wMm * unitsPerMm, hMm * unitsPerMm, dMm * unitsPerMm];
}

/**
 * The axis a plan rotation turns about, and its sign.
 *
 * In `z-up` a CCW plan rotation is a positive rotation about +Z. In `y-up`,
 * plan Y maps to −Z, and working the trigonometry through gives a positive
 * rotation about +Y by the SAME angle — no sign flip. Stated here once so the
 * renderer does not have to re-derive it and get it wrong at 3 a.m.
 */
export function sceneUpAxis(axes: PlanAxes): [number, number, number] {
  return axes === 'z-up' ? [0, 0, 1] : [0, 1, 0];
}

/**
 * Write one scene-space vertex into a packed Float32Array.
 *
 * Used by the wireframe builder, which writes thousands of vertices per rebuild
 * and must not allocate a tuple for each one.
 */
export function writeScenePosition(
  target: Float32Array,
  offset: number,
  xMm: number,
  yMm: number,
  heightMm: number,
  axes: PlanAxes,
  unitsPerMm: number,
): void {
  if (axes === 'z-up') {
    target[offset] = xMm * unitsPerMm;
    target[offset + 1] = yMm * unitsPerMm;
    target[offset + 2] = heightMm * unitsPerMm;
  } else {
    target[offset] = xMm * unitsPerMm;
    target[offset + 1] = heightMm * unitsPerMm;
    target[offset + 2] = -yMm * unitsPerMm;
  }
}
