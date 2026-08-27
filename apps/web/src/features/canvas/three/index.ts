/**
 * `features/canvas/three` — Phase 5's 3D synthesis: the plan IS the model.
 *
 * Read the modules in this order the first time:
 *   extrusion       pure profile maths — spans, footprints, opening boxes,
 *                   stair steps, parapet bands (mm in, float-mm profiles out)
 *   solids          HouseModel → typed SolidSpecs per rebuild group, with the
 *                   pick + surface-group contract stated in data
 *   dirty           signature-based dirty tracking; planRebuild is the §14
 *                   incremental-rebuild contract, pinned per op type in specs
 *   booleans        THE manifold-3d boundary — lazy WASM, honest fallback
 *   geometryBuild   SolidSpecs → merged buffers; the ONLY mm→world door in 3D
 *   materials3d     MaterialAssignment (op 29) resolution + procedural palette
 *   ThreeDScene     the component: per-group cache, PickRegistry registration
 *
 * THE THREE CORE RULES apply unchanged (see `features/canvas/core/index.ts`):
 * integer mm at the op boundary (nothing here emits ops at all), no R3F
 * pointer handlers (everything registers with the one PickRegistry), and
 * frames are asked for, never assumed.
 *
 * WHAT THIS MODULE DOES NOT DO, ON PURPOSE:
 *   - no second <Canvas>, no camera code — `CameraRig` owns projection.
 *   - no facade geometry — the facade kit generator/renderer is a separate
 *     module over the isolated facade sub-model (§8); nothing here reads
 *     `house.facade`, so facade churn can never dirty a storey group.
 *   - no binary assets — the palette is procedural flat colour.
 */

export { ThreeDScene } from './ThreeDScene';
export type { RebuildStats, ThreeDSceneProps } from './ThreeDScene';

export {
  booleanEngineStatus,
  ensureBooleanEngine,
  getPrismCutter,
  subscribeBooleanEngine,
} from './booleans';
export type { BooleanEngineStatus, CutMeshMm, PrismCutter } from './booleans';

export { groupSignatures, planRebuild, roofSignature, storeySignature } from './dirty';
export type { RebuildPlan } from './dirty';

export {
  groupKeysOf,
  roofSolids,
  ROOF_GROUP_KEY,
  solidsOfGroup,
  storeyGroupKey,
  storeySolids,
} from './solids';
export type { GroupSolids, SolidSpec } from './solids';

export { buildGroup } from './geometryBuild';
export type { BuiltBucket, GroupBuild } from './geometryBuild';

export {
  balconyRailingFootprintsF,
  fflOfIndexMm,
  MUMTY_HEIGHT_MM,
  OHT_HEIGHT_MM,
  OPENING_CUT_SLACK_MM,
  OPENING_PANEL_THICKNESS_MM,
  openingCutProfileF,
  openingPanelProfileF,
  PARAPET_THICKNESS_MM,
  parapetSegmentFootprintsF,
  stairSolidProfilesF,
  storeySpanMm,
  terraceLevelMm,
  wallFootprintF,
} from './extrusion';
export type { PrismProfileF, StoreySpanMm } from './extrusion';

export {
  colorForScope,
  DEFAULT_SURFACE_COLORS,
  disposeSolidMaterials,
  elementScopedAssignmentIds,
  getSolidMaterial,
  resolveMaterialId,
} from './materials3d';
export type { MaterialScope } from './materials3d';
