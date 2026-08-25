/**
 * `features/canvas/facade` — the facade kit system (Phase 5, playbook §8).
 *
 * WHAT THIS MODULE IS: a pure kit generator (`generator.ts`), the op builders
 * for ops 27/28 (`ops.ts`), one scene layer that renders every facade
 * component as its own picked mesh (`FacadeLayer`), the kit-card panel with
 * generator-drawn previews (`FacadeKitPanel`), and the per-element inspector
 * (`FacadeComponentPanel`).
 *
 * WHAT THIS MODULE NEVER DOES: touch walls, rooms, openings or areas. The
 * facade is an isolated sub-model (§8); its only writes are `facade.apply_kit`
 * and `facade.edit_component`, and `generator.test.ts` proves folding them
 * leaves the rest of the house deep-equal.
 *
 * INTEGRATION (owner: the integrator, not this module):
 *  1. Mount `<FacadeLayer />` inside the existing `CanvasRoot` scene — the 3D
 *     view's layer stack. NO second <Canvas>.
 *  2. Add `'facade'` to `PICK_KINDS` + `PICK_PRIORITY` (suggested 65) in
 *     `features/canvas/core/constants.ts`. `types.ts` here fails to compile
 *     until that lands, by design — see `FACADE_PICK_KIND`.
 *  3. Show `<FacadeKitPanel />` on the 3D tab's side panel.
 *  4. Route the right inspector to `<FacadeComponentPanel componentId=…/>`
 *     when `tryParseId(primaryId)?.type === 'facadecomp'`.
 */

export { FACADE_KITS, CONTEMPORARY_KIT, MODERN_MINIMAL_KIT, kitById, colorwayById } from './kits';
export {
  generateFacadeComponents,
  resolveColorway,
  kitFitIssues,
  findEntryDoor,
  findCladdingWall,
  externalWallsOf,
  CHAJJA_SIDE_OVERHANG_MM,
  PORCH_SIDE_MARGIN_MM,
} from './generator';
export type { GenerateOptions, KitFitIssue } from './generator';
export { applyKitOp, clearFacadeOp, editComponentOp } from './ops';
export { boxesForComponent, balconyOpenEdges, externalCentroid, wallFrame } from './componentBoxes';
export type { OrientedBoxMm, WallFrame } from './componentBoxes';
export { buildBoxTriangles, hexToRgb, SELECTION_BOOST, WORLD_PER_MM } from './geometry3d';
export type { BoxTriangleData } from './geometry3d';
export { FacadeLayer } from './FacadeLayer';
export type { FacadeLayerProps } from './FacadeLayer';
export { FacadeKitPanel } from './FacadeKitPanel';
export type { FacadeKitPanelProps } from './FacadeKitPanel';
export { FacadeComponentPanel } from './FacadeComponentPanel';
export type { FacadeComponentPanelProps } from './FacadeComponentPanel';
export { KitThumbnail } from './KitThumbnail';
export type { KitThumbnailProps } from './KitThumbnail';
export {
  elevationSpec,
  hasFrontage,
  kitThumbnailSpec,
  pickFrontage,
  sampleHouseForThumbnails,
} from './thumbnail';
export type { ElevationRect, ThumbnailSpec } from './thumbnail';
export { fnv1a32, nextSeed, pickVariant, variantIndex } from './variation';
export { FACADE_PICK_KIND, intParam, strParam, enumParam, RAILING_STYLES } from './types';
export type {
  FacadeKitDef,
  KitColorway,
  KitRules,
  RailingStyle,
  WindowTrimStyle,
  ChajjaStyle,
  ParapetStyle,
  PorchStyle,
} from './types';
