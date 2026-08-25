/**
 * constants.ts — the numbers every canvas module agrees on.
 *
 * Nothing here is a preference. Each value is either a contract (the world
 * scale, the layer order, the pick priorities) or a §14 budget knob (the DPR
 * cap, the zoom clamps). Changing one changes behaviour in both the 2D and the
 * 3D view, which is the point: there is one scene graph, so there is one set of
 * numbers describing it.
 *
 * WHY 1 WORLD UNIT = 1 METRE
 * --------------------------
 * The model is integer millimetres; Three.js is float32 on the GPU. If world
 * units were millimetres, a 30 m plot would span 30 000 units, and float32's
 * ~7 significant digits would leave roughly 0.002 unit (2 µm) of resolution at
 * the far corner — survivable, but shadow bias, camera near/far ratios and
 * physically-based lights in Phase 5 all assume metres, and fighting that costs
 * more than the conversion does. One metre per unit keeps a G+2 house inside a
 * ~30-unit box where float32 has ~1 µm of headroom, and lets Phase 5 use
 * Three's defaults for lights and shadows without a scale fudge factor.
 */

import type { Material, Object3D } from 'three';

import { SNAP_COARSE_MM, SNAP_FINE_MM } from '../../../lib/units';

export { SNAP_COARSE_MM, SNAP_FINE_MM };

// ---------------------------------------------------------------------------
// World scale
// ---------------------------------------------------------------------------

/** Multiply millimetres by this to get world units. 1 unit = 1 metre. */
export const WORLD_UNITS_PER_MM = 0.001;

/** Multiply world units by this to get millimetres. */
export const MM_PER_WORLD_UNIT = 1000;

// ---------------------------------------------------------------------------
// View modes
// ---------------------------------------------------------------------------

/**
 * Structurally identical to `ViewMode` in `stores/ui.ts`, declared here so the
 * canvas core has no dependency on app chrome. `CanvasRoot` takes the mode as a
 * prop; the Plan page is what wires the store to it.
 */
export type CanvasMode = '2d' | '3d';

// ---------------------------------------------------------------------------
// Renderer / performance (§14: <16 ms per frame during pan-zoom on a G+2)
// ---------------------------------------------------------------------------

/**
 * Device-pixel-ratio ceiling. A 3× phone or a 2× Retina at 2560 wide asks for
 * 4× the fragments of a 1× buffer for a drafting view whose content is mostly
 * hairlines; capping at 2 is the single cheapest way to stay inside the frame
 * budget on the machines Indian studios actually own.
 */
export const DPR_CAP = 2;

/** Floor for the DPR range — never render below native 1×. */
export const DPR_FLOOR = 1;

/**
 * How long a resize is allowed to settle before the drawing buffer is resized.
 * Resizing the buffer reallocates GPU memory; doing it on every mousemove of a
 * panel drag is the classic way to drop 200 ms in the middle of an interaction.
 */
export const RESIZE_DEBOUNCE_MS = 32;

// ---------------------------------------------------------------------------
// Zoom limits, expressed as millimetres per CSS pixel
// ---------------------------------------------------------------------------

/**
 * `mmPerPx` is the zoom scalar for the whole canvas because it is the number an
 * architect already thinks in: at 96 CSS-dpi, 1:100 is 3.78 mm/px and 1:50 is
 * 1.89 mm/px. Storing zoom this way means "fit the plot" and "print at 1:100"
 * are the same kind of statement.
 */
export const MIN_MM_PER_PX = 0.25;

/** Zoomed all the way out: ~1 px per 40 cm — a 100 m site fits a laptop screen. */
export const MAX_MM_PER_PX = 400;

/** Opening zoom when nothing has been fitted yet (~1:300). */
export const DEFAULT_MM_PER_PX = 12;

/** Padding left around a `zoomToFit` result, in CSS pixels. */
export const FIT_PADDING_PX = 48;

/**
 * Wheel sensitivity: `factor = exp(deltaY * WHEEL_ZOOM_RATE)`. Exponential
 * rather than linear so that zooming out and back in returns to the same
 * scale — a linear step is not invertible and drifts over a long session.
 */
export const WHEEL_ZOOM_RATE = 0.0015;

/** One notch of a line-mode wheel (Firefox) in pixel-equivalents. */
export const WHEEL_LINE_HEIGHT_PX = 16;

/** One notch of a page-mode wheel in pixel-equivalents. */
export const WHEEL_PAGE_HEIGHT_PX = 400;

// ---------------------------------------------------------------------------
// Cameras
// ---------------------------------------------------------------------------

/**
 * How far above the datum the orthographic eye sits, in mm. An orthographic
 * projection does not care about distance, only about what falls between near
 * and far — 100 m of headroom clears a G+2 plus its water tank with room for
 * the Phase 5 sun widget's shadow casters.
 */
export const ORTHO_EYE_HEIGHT_MM = 100_000;

/** Orthographic near plane, world units. */
export const ORTHO_NEAR = 0.01;

/** Orthographic far plane, world units (400 m). */
export const ORTHO_FAR = 400;

/** Perspective vertical field of view, degrees. 50° reads as "architectural". */
export const PERSP_FOV_DEG = 50;

/** Perspective near plane, world units (5 cm — close enough for a walkthrough). */
export const PERSP_NEAR = 0.05;

/** Perspective far plane, world units (2 km — the site plus context). */
export const PERSP_FAR = 2000;

/** Opening orbit distance for the 3D camera, mm. */
export const DEFAULT_ORBIT_DISTANCE_MM = 25_000;

/** Opening orbit azimuth, degrees CCW from +X (east). 225° looks from the SW. */
export const DEFAULT_ORBIT_AZIMUTH_DEG = 225;

/** Opening orbit polar angle, degrees from straight down. 60° is eye-level-ish. */
export const DEFAULT_ORBIT_POLAR_DEG = 60;

/** Orbit polar clamp — never pass through the poles, never go under the ground. */
export const MIN_ORBIT_POLAR_DEG = 1;
export const MAX_ORBIT_POLAR_DEG = 89;

// ---------------------------------------------------------------------------
// Picking
// ---------------------------------------------------------------------------

/**
 * Click slop in CSS pixels. Fitts' law, not laziness: a 115 mm wall at 1:100 is
 * about 1 px wide, and a hit test that demands the pointer be inside those
 * pixels is a hit test nobody can use.
 */
export const PICK_TOLERANCE_PX = 6;

/**
 * The 3D depth window inside which priority outranks distance, in world units.
 * A door leaf and its host wall are coplanar to within the wall thickness, so
 * 50 mm lets the opening win; a room floor two metres behind a wall does not
 * make it into the window and loses on distance, as it should.
 *
 * In 2D this is `Infinity` — every 2D element is drawn on the same plane, so
 * ray distance carries no information at all and priority decides outright.
 */
export const DEPTH_EPSILON_WORLD_3D = 0.05;

/**
 * What a pick can return. A superset of the seven kinds §12 names, by design:
 * the Phase 4 keyboard map already has a balcony tool (B), and columns are in
 * the model. Leaving them out would force those two features to grow a second
 * picking path — the exact mistake §12 tells us not to make.
 *
 * `'facade'` (Phase 5): a facade kit component — chajja, trim, railing, porch,
 * cladding band (§8's isolated sub-model, op 28). Added here by the Phase-5
 * integrator because `features/canvas/facade/types.ts` deliberately fails to
 * compile until the kind is first-class: a cast would have compiled and lost
 * every pick tie silently, which is the Phase-4 FurnitureLayer bug shape.
 */
export const PICK_KINDS = [
  'wall',
  'opening',
  'room',
  'stair',
  'furniture',
  'facade',
  'balcony',
  'column',
  'dimension',
] as const;

export type PickKind = (typeof PICK_KINDS)[number];

/**
 * Which element wins when two are under the same pixel. Higher wins.
 *
 * The two rules §12 states explicitly fall out of the table: an opening (70)
 * beats its host wall (40), and a dimension (90) beats a room (10). The rest
 * follows one principle — the smaller and more deliberately placed a thing is,
 * the more likely you meant to click it.
 */
export const PICK_PRIORITY: Readonly<Record<PickKind, number>> = {
  dimension: 90,
  opening: 70,
  // Facade components sit ON walls (a chajja hugs its window, cladding hugs
  // its wall), so they must beat the host wall (40) and the furniture behind
  // glass (60), but an opening's leaf (70) is smaller and more deliberate
  // still. 65 is the facade module's own suggested slot.
  facade: 65,
  furniture: 60,
  stair: 55,
  column: 52,
  balcony: 45,
  wall: 40,
  room: 10,
};

// ---------------------------------------------------------------------------
// Layers — the one draw-order table
// ---------------------------------------------------------------------------

/**
 * Draw order for the shared scene graph, back to front.
 *
 * In 2D everything is coplanar, so depth testing cannot order it: overlay
 * layers set `depthTest: false` and rely on `renderOrder` alone (see
 * {@link depthTestForMode}). In 3D real geometry orders itself and
 * `renderOrder` only breaks ties between transparent surfaces. One table,
 * both modes — no 2D-only ordering hack for Phase 5 to unpick.
 */
export const CANVAS_LAYERS = [
  'grid',
  'slab',
  'roomFill',
  'balcony',
  'wall',
  'opening',
  'stair',
  'furniture',
  'column',
  'roomLabel',
  'annotation',
  'dimension',
  'selection',
  'preview',
  'handle',
] as const;

export type CanvasLayer = (typeof CANVAS_LAYERS)[number];

/** `renderOrder` per layer, spaced by 10 so a module can slot between two. */
export const LAYER_RENDER_ORDER: Readonly<Record<CanvasLayer, number>> = (() => {
  const out = {} as Record<CanvasLayer, number>;
  CANVAS_LAYERS.forEach((layer, i) => {
    out[layer] = i * 10;
  });
  return out;
})();

/** Layers that float above the drawing rather than living inside it. */
const OVERLAY_LAYERS: ReadonlySet<CanvasLayer> = new Set<CanvasLayer>([
  'roomLabel',
  'annotation',
  'dimension',
  'selection',
  'preview',
  'handle',
]);

/**
 * Whether a material on `layer` should depth-test in `mode`.
 *
 * 2D: overlays do not, so a selection outline is never eaten by the wall it
 * outlines. 3D: everything does, because in a perspective view an annotation
 * that ignores depth is a annotation floating through the building.
 */
export function depthTestForMode(mode: CanvasMode, layer: CanvasLayer): boolean {
  if (mode === '3d') return true;
  return !OVERLAY_LAYERS.has(layer);
}

/** Stamp an object's draw order. Call once at build time, not per frame. */
export function applyLayer<T extends Object3D>(object: T, layer: CanvasLayer): T {
  object.renderOrder = LAYER_RENDER_ORDER[layer];
  return object;
}

/** Configure one material for a layer in a mode. Idempotent; safe to re-call. */
export function applyLayerToMaterial(
  material: Material,
  mode: CanvasMode,
  layer: CanvasLayer,
): void {
  const wanted = depthTestForMode(mode, layer);
  if (material.depthTest !== wanted) {
    material.depthTest = wanted;
    material.needsUpdate = true;
  }
}

// ---------------------------------------------------------------------------
// Grid
// ---------------------------------------------------------------------------

/** The drafting module: the 115 mm half-brick the solver snaps to (§5.3). */
export const GRID_MODULE_MM = SNAP_COARSE_MM;

/** The heavier every-metre line. Reads as the 1 m squares on drafting paper. */
export const GRID_EMPHASIS_MM = 1000;

/** The fine grid, shown only when the fine-grid toggle (G) is on. */
export const GRID_FINE_MM = SNAP_FINE_MM;

/**
 * A grid level fades out once its spacing falls below this many CSS pixels.
 * Below ~4 px apart, lines stop being a grid and become a grey wash that costs
 * fill rate and hides the drawing.
 */
export const GRID_MIN_SPACING_PX = 4;

/** …and is fully solid by this spacing. */
export const GRID_FULL_SPACING_PX = 12;

/** Grid line width in CSS pixels. Hairline, like a drafted sheet. */
export const GRID_LINE_WIDTH_PX = 1;
