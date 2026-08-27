/**
 * features/canvas/furniture — placement + the catalogue browser (Phase 4, §F4).
 *
 * INTEGRATOR CONTRACT
 * ===================
 *
 * ## 1. Wrap the plan surface once
 *
 * ```tsx
 * <FurniturePlacementProvider>
 *   <Canvas>…<FurnitureLayer /></Canvas>
 *   <FurnitureBrowser />          // side rail
 *   <FurniturePlacementHud />     // floating readout, position it yourself
 * </FurniturePlacementProvider>
 * ```
 *
 * The provider owns the single tool state machine. The browser, the layer and
 * the HUD all read it; none of them needs props to work.
 *
 * ## 2. Feed the canvas core's four events in
 *
 * The core owns the camera, so it owns screen→plan projection. Everything here
 * takes PLOT-LOCAL INTEGER MILLIMETRES:
 *
 * ```ts
 * const f = useFurniturePlacement();
 * onPointerMove: f.pointerMove(ptMm, { alt: e.altKey, shift: e.shiftKey })
 * onPointerDown: if (f.phase !== 'idle') f.pointerDown(ptMm)
 * onDrop:        const id = readFurnitureDragPayload(e.dataTransfer);
 *                if (id !== null) f.dropAt(id, ptMm);
 * onKeyDown:     if (f.handleKey({ key: e.key, shift: e.shiftKey }).handled)
 *                  e.preventDefault();
 * ```
 *
 * `dragover` on the canvas element must call `preventDefault()` or the browser
 * refuses the drop. Delete on a selection: `f.deleteSelected()`.
 *
 * ## 3. Picking goes through the shared raycaster
 *
 * This feature adds NO picking path. It tags its meshes; the core's one
 * raycaster resolves them:
 *
 * ```ts
 * const pick = hit.object.userData.garhPick as GarhPick | undefined;
 * const furnitureId = pick?.idAt(hit.instanceId) ?? null;
 * ```
 *
 * Phase 5 gets furniture picking in the perspective view for free, because the
 * meshes and the tag are the same objects.
 *
 * ## 4. Two things to check against your scene
 *
 * `<FurnitureLayer>` defaults to `axes="z-up"` and `sceneUnitsPerMm={1}`. If
 * the shared camera rig uses a different frame, pass the right values once —
 * see `sceneAxes.ts`. Nothing else in the feature knows a scene exists.
 *
 * ## 5. One ask outside this folder
 *
 * `furnitureItemSchema` in `apps/web/src/lib/schemas.ts` does not list
 * `clearanceMm`, so zod strips a field the API really does send. Until it is
 * added, every clearance is a per-category assumption, flagged in the UI as
 * one. Adding `clearanceMm: intMm.default(0)` to that schema is the whole fix.
 *
 * WHAT IS NOT REAL
 * ================
 *
 * The 3D "assets" are parametric box proxies generated from each catalogue
 * item's three dimensions — no furniture has been modelled. They are tagged
 * with `catalogId` and `source: 'parametric-box-proxy'` so Phase 5/7 can swap
 * real meshes in without touching placement, collision or ops. Read the header
 * of `proxyMesh.ts` before shipping anything that implies otherwise.
 */

// ── React surfaces ─────────────────────────────────────────────────────────
export { FurniturePlacementProvider, useFurniturePlacement } from './useFurniturePlacement';
export type { FurniturePlacementValue } from './useFurniturePlacement';

export { FurnitureBrowser } from './FurnitureBrowser';
export type { FurnitureBrowserProps } from './FurnitureBrowser';

export { FurnitureLayer } from './FurnitureLayer';
export type { FurnitureLayerProps, GarhPick } from './FurnitureLayer';

export { FurniturePlacementHud } from './FurniturePlacementHud';
export type { FurniturePlacementHudProps } from './FurniturePlacementHud';

// ── Drag and drop (no React import needed to read a drop) ──────────────────
export {
  FURNITURE_DND_MIME,
  isFurnitureDrag,
  readFurnitureDragPayload,
  setFurnitureDragPayload,
} from './dnd';

// ── The tool state machine ─────────────────────────────────────────────────
export { PlacementController, suggestRotationDeg } from './placement';
export type {
  CommitResult,
  EntryTarget,
  FurnitureKeyEvent,
  KeyOutcome,
  NumericEntry,
  PlacementCoarseState,
  PlacementPhase,
  PlacementPoseState,
  PointerModifiers,
} from './placement';

// ── Ops (the integer-mm boundary) ──────────────────────────────────────────
export {
  deleteFurnitureOp,
  deleteFurnitureOps,
  deleteLabel,
  moveLabel,
  newFurnitureId,
  placeFurnitureOp,
  placeLabel,
  transformFurnitureOp,
} from './ops';
export type { PlaceFurnitureInput } from './ops';

// ── Catalogue ──────────────────────────────────────────────────────────────
export {
  CLEARANCE_FALLBACK_MM,
  catalogueIndex,
  filterByRoomType,
  footprintMm2,
  formatItemClearance,
  formatItemFootprint,
  groupByCategory,
  searchItems,
  searchScore,
  toCatalogue,
  toCatalogueItem,
  toCategory,
} from './catalogue';
export type { CatalogueGroup, RawFurnitureItem } from './catalogue';

export {
  loadFurnitureCatalogue,
  resetFurnitureCatalogueCache,
  useFurnitureCatalogue,
} from './useFurnitureCatalogue';
export type { FurnitureCatalogue, Loadable } from './useFurnitureCatalogue';

// ── Geometry & collision (pure, integer, renderer-free) ────────────────────
export {
  MM_2X,
  angleFromDrag,
  bounds2x,
  boundsOverlap,
  clearanceQuad2x,
  cornersToMm,
  cornersToMmInward,
  footprintQuad2x,
  normaliseRotationDeg,
  occupancyQuad2x,
  quadInsideRoom,
  quadsOverlap,
  rectQuad2x,
  roomAtPt,
  rotateBy,
  snapPtMm,
  toObstacle,
  wallQuad2x,
} from './geometry';
export type { WallLike } from './geometry';

export {
  EMPTY_CONTEXT,
  buildPlacementContext,
  evaluatePlacement,
  furnitureAdvisoryChips,
  issueTone,
  roomTypeAt,
} from './collision';
export type { BuildContextInput, FurnitureAdvisoryChip, PlacementContext } from './collision';

// ── Render data + the honest box proxies ───────────────────────────────────
export {
  CATEGORY_COLOR,
  CLEARANCE_COLOR,
  CLEARANCE_OPACITY,
  PREVIEW_COLOR,
  buildBoxInstances,
  buildEdgePositions,
  clearanceRingMm,
  footprintRingMm,
  instanceScale,
} from './render';
export type { BoxInstance } from './render';

export { boxProxyFor, proxyBoxCount, proxyCache } from './proxyMesh';
export type { BoxProxy, ProxyBox } from './proxyMesh';

export {
  DEFAULT_PLAN_AXES,
  DEFAULT_SCENE_UNITS_PER_MM,
  scenePosition,
  sceneScale,
  sceneUpAxis,
  writeScenePosition,
} from './sceneAxes';
export type { PlanAxes } from './sceneAxes';

// ── Vocabulary ─────────────────────────────────────────────────────────────
export { FURNITURE_CATEGORIES, FURNITURE_CATEGORY_LABELS } from './types';
export type {
  Bounds2x,
  CatalogueItem,
  FurnitureCategory,
  Obstacle,
  PlacedFurniture,
  PlacementIssue,
  PlacementIssueCode,
  PlacementIssueSeverity,
  Pose,
  Quad2x,
  RoomLike,
} from './types';
