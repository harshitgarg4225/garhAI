/**
 * `features/canvas/core` — the shared canvas foundation for Phase 4 (2D) and
 * Phase 5 (3D).
 *
 * Read the modules in this order the first time:
 *   constants          world scale, layer order, pick priorities, budget knobs
 *   coords             THE mm ↔ world ↔ screen boundary, and the rounding rule
 *   cameraMath         pure pan/zoom/fit/orbit maths (no three, no React)
 *   viewport           camera state that lives outside React, + invalidate
 *   pickRegistry       object → element mapping; why no R3F pointer handlers
 *   hitTest            ONE picker, both views, with the priority rules
 *   CanvasRoot         the single <Canvas>, and how the §14 budget is met
 *   CameraRig          orthographic ↔ perspective without rebuilding anything
 *   Grid               the drafting grid, as one quad and one shader
 *   materials/outline  the shared selection treatment
 *
 * THREE RULES FOR EVERY MODULE THAT BUILDS ON THIS
 *
 *  1. **Integer millimetres at the boundary.** Screen-space floats are fine
 *     inside a renderer; anything that becomes an op payload goes through
 *     `pointerToMm` / `snapPtMm` and comes out an integer. There is no other
 *     sanctioned float→mm door.
 *
 *  2. **No react-three-fiber pointer handlers.** Register with `usePickable`
 *     and let `CanvasRoot` deliver a `CanvasPointerEvent`. This is the §12
 *     "one hit-testing system" requirement, and it is also why hover costs one
 *     raycast per animation frame instead of one per mouse move.
 *
 *  3. **Ask for frames, never assume them.** `frameloop="demand"`. Mutating a
 *     mesh outside React must be followed by `core.invalidate()`; mutating the
 *     camera goes through `ViewportController`, which invalidates for you.
 */

// Constants, layers, kinds
export {
  applyLayer,
  applyLayerToMaterial,
  CANVAS_LAYERS,
  depthTestForMode,
  DEPTH_EPSILON_WORLD_3D,
  DPR_CAP,
  DPR_FLOOR,
  FIT_PADDING_PX,
  GRID_EMPHASIS_MM,
  GRID_FINE_MM,
  GRID_MODULE_MM,
  LAYER_RENDER_ORDER,
  MAX_MM_PER_PX,
  MIN_MM_PER_PX,
  MM_PER_WORLD_UNIT,
  ORTHO_EYE_HEIGHT_MM,
  PERSP_FOV_DEG,
  PICK_KINDS,
  PICK_PRIORITY,
  PICK_TOLERANCE_PX,
  SNAP_COARSE_MM,
  SNAP_FINE_MM,
  WORLD_UNITS_PER_MM,
} from './constants';
export type { CanvasLayer, CanvasMode, PickKind } from './constants';

// The conversion boundary
export {
  bboxCentreMm,
  bboxIsEmpty,
  bboxOfMm,
  bboxUnion,
  constrainOrtho,
  mmToPx,
  mmToWorld,
  mmToWorldScalar,
  mmToWorldXYZ,
  ndcFromPixel,
  ndcFromPointer,
  pickToleranceMm,
  pixelFromNdc,
  pointAtLengthMm,
  pointerToMm,
  pointerToMmRaw,
  pxToMm,
  raycasterFromNdc,
  roundMm,
  snapMm,
  snapPtMm,
  snapPtRelativeMm,
  worldToElevationMm,
  worldToMm,
  worldToMmF,
  worldToMmScalar,
  worldToMmScalarF,
} from './coords';
export type {
  Ndc,
  PixelPoint,
  PointerToMmOptions,
  PtF,
  ReadonlyVec3,
  Vec3Like,
  ViewportSizePx,
} from './coords';

// Camera maths
export {
  clampMmPerPx,
  clampOrbit,
  CSS_MM_PER_PX,
  DEFAULT_ORBIT_3D,
  DEFAULT_VIEW_2D,
  dollyOrbit,
  fitBboxToViewport,
  fitDistanceMm,
  fitOrbitToBbox,
  FIT_MIN_EXTENT_MM,
  mmPerPxAtDistance,
  mmToPixel,
  normaliseAzimuthDeg,
  orbitByPx,
  orbitEyeMm,
  orthoFrustumWorld,
  panByMm,
  panByPx,
  pixelToMmF,
  scaleLabel,
  wheelZoomFactor,
  zoomAtCentre,
  zoomAtPixel,
} from './cameraMath';
export type { Orbit3D, PtF3, View2D } from './cameraMath';

// Viewport controller
export { ViewportController } from './viewport';
export type { ViewportListener } from './viewport';

// Picking
export { isEffectivelyVisible, PickRegistry } from './pickRegistry';
export type { InstanceIdLookup, PickResolver, PickTarget } from './pickRegistry';
export {
  comparePickCandidates,
  depthEpsilonForMode,
  emptyHit,
  pickAt,
  pickPriority,
  resolveHit,
  sameHitTarget,
} from './hitTest';
export type { PickCandidate, PickHit, PickOptions, ResolveHitOptions } from './hitTest';

// React surface
export { CanvasRoot } from './CanvasRoot';
export type { CanvasRootProps } from './CanvasRoot';
export { CameraRig } from './CameraRig';
export type { CameraRigProps } from './CameraRig';
export { Grid } from './Grid';
export type { GridProps } from './Grid';
export {
  CanvasCore,
  CanvasCoreContext,
  useCanvasCore,
  useCanvasCoreOptional,
  useMmPerPx,
  usePickable,
  usePickableInstances,
  usePickableResolver,
  useViewportValue,
} from './context';
export type { CorePickOptions } from './context';
export { useCanvasControls } from './useCanvasControls';
export type {
  CanvasControlsCallbacks,
  CanvasControlsOptions,
  CanvasPointerEvent,
} from './useCanvasControls';

// Selection rendering
export {
  disposeCanvasMaterials,
  getCanvasMaterials,
  getCanvasThemeColors,
  readTokenColor,
  refreshCanvasTheme,
  watchCanvasTheme,
} from './materials';
export type { CanvasMaterials, CanvasThemeColors } from './materials';
export { OutlineBox, OutlineFill, OutlinePolygon, OutlinePolyline } from './outline';
export { bboxRingMm, pointsMmToWorld, polygonFillGeometry } from './outlineGeometry';
export type {
  OutlineBoxProps,
  OutlineFillProps,
  OutlinePolygonProps,
  OutlinePolylineProps,
  OutlineTone,
} from './outline';

// Grid shader (Phase 5 may want its own grid instance with different opacity)
export { createGridMaterial, refreshGridMaterialTheme, updateGridMaterial } from './gridShader';
export type { GridUniformValues } from './gridShader';
