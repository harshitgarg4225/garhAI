/**
 * features/canvas/tools — the §12 drawing tools, as state machines.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * INTEGRATOR CONTRACT — three things to wire, in this order
 * ════════════════════════════════════════════════════════════════════════════
 *
 * 1. **The controller.** One hook, on the page that owns the canvas:
 *
 * ```tsx
 * const [core, setCore] = useState<CanvasCore | null>(null);
 * const tools = useToolController({
 *   core,
 *   setback,            // optional: { envelope, maxProjectionMm, cite }
 *   furnitureCatalog,   // optional: Map<id, FurnitureItem> from /catalog/furniture
 * });
 *
 * <CanvasRoot
 *   onCoreReady={setCore}
 *   mode={viewMode}
 *   snapModuleMm={snapStepMm}
 *   activeStoreyId={activeStoreyId}
 *   overlay={<><ToolOptionsBar /><ToolHud /></>}
 *   {...tools.canvasHandlers}
 * >
 *   …scene…
 * </CanvasRoot>
 * ```
 *
 *    `useToolController` installs the WHOLE §12 keyboard map (V/W/D/N/S/B/M/F,
 *    ⌘Z/⌘Y, 1/2/3, Tab, G, Esc, Enter) plus the tool-first capture layer that
 *    makes "type a length while drawing" work without stealing those letters
 *    when nothing is being drawn. Do not also call `useKeyboardMap` for those
 *    commands elsewhere; two owners of one key is how a tool rail and a canvas
 *    start disagreeing.
 *
 * 2. **The preview.** The scene layer reads `toolPreviewBus.get()` inside its
 *    frame callback and draws `preview.shape` — a discriminated union with no
 *    tool-specific knowledge in it. Do NOT subscribe the scene with React; the
 *    §14 budget is the reason this bus exists.
 *
 * 3. **The commit path.** The dimension-first overlay must dispatch through the
 *    builders in `editOps`, above all `setWallLengthOps`. Dragging a wall and
 *    typing into its dimension label are the same edit and must produce the
 *    same op.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT IS OWNED HERE, AND WHAT IS NOT
 * ════════════════════════════════════════════════════════════════════════════
 * Owned:      the eight tools, snapping, numeric entry, tool settings, the op
 *             builders, the HUD and the options bar.
 * Not owned:  the scene graph and picking (`../core`), the rendered walls,
 *             rooms, dimensions and chips (the overlays layer), the tool rail
 *             (app chrome — use `TOOL_META` for its labels and shortcuts).
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE CONVERSION BOUNDARY
 * ════════════════════════════════════════════════════════════════════════════
 * Pointer positions arrive as integer millimetres from `core/coords`. Every
 * number that becomes an op payload is snapped and integral before it leaves
 * `editOps`. Screen-space floats never enter this directory; `mmPerPx` is used
 * only to size tolerances, never to place geometry.
 */

// ── The contract types ────────────────────────────────────────────────────
export type {
  MarqueeRectMm,
  NumericEntryView,
  OpeningParams,
  PreviewShape,
  PreviewWall,
  Readout,
  SelectionIntent,
  SetbackContext,
  SnapView,
  Tool,
  ToolBlock,
  ToolChip,
  ToolCommit,
  ToolContext,
  ToolId,
  ToolKeyInput,
  ToolPhase,
  ToolPointerInput,
  ToolPreview,
  ToolResponse,
  ToolSettings,
} from './types';
export { TOOL_RESPONSE_NONE, handled } from './types';

// ── React surface ─────────────────────────────────────────────────────────
export { useToolController } from './useToolController';
export type { ToolController, ToolControllerOptions } from './useToolController';
export { ToolHud } from './ToolHud';
export type { ToolHudProps } from './ToolHud';
export { ToolOptionsBar } from './ToolOptionsBar';
export type { ToolOptionsBarProps } from './ToolOptionsBar';
export { toolPreviewBus, useToolPreview, ToolPreviewBus } from './previewBus';
export type { PreviewListener } from './previewBus';

// ── Settings ──────────────────────────────────────────────────────────────
export {
  DEFAULT_TOOL_SETTINGS,
  readToolSettings,
  useToolSettings,
  WALL_THICKNESS_PRESETS,
} from './useToolSettings';
export type { ToolSettingsState } from './useToolSettings';

// ── Registry (the tool rail's source of truth) ────────────────────────────
export { TOOL_META, TOOL_IDS, TOOL_SHORTCUT, createTool } from './registry';
export type { ToolMeta } from './registry';

// ── The machines, for specs and for direct use ────────────────────────────
export { BaseTool } from './baseTool';
export type { PreviewParts } from './baseTool';
export { WallTool } from './wallTool';
export { OpeningTool } from './openingTool';
export { StairTool } from './stairTool';
export { BalconyTool, maxDistanceOutside } from './balconyTool';
export { MeasureTool } from './measureTool';
export { FurnitureTool, normaliseRotationDeg } from './furnitureTool';
export { SelectTool, pointInsidePolygon } from './selectTool';

// ── THE COMMIT PATH (shared with the dimension-first overlay) ─────────────
export {
  angleDeg,
  balconyAddOp,
  clampOpeningOffset,
  defaultOpeningParams,
  deleteLabel,
  deleteOps,
  dryRun,
  furnitureFootprintMm,
  furniturePlaceOp,
  furnitureTransformOp,
  nextSwing,
  openingAddOp,
  openingFlipOp,
  openingMoveOp,
  openingOffsetWindow,
  openingResizeOp,
  previewWall,
  ringAreaMm2,
  setWallLengthOps,
  stairAddOp,
  SWING_CYCLE,
  toBlock,
  translateWallsOps,
  validateCommit,
  wallAddOp,
  wallMoveOp,
  wallThicknessOp,
} from './editOps';
export type {
  BalconyAddInput,
  OpeningAddInput,
  StairAddInput,
  WallAddInput,
  WallAnchorEnd,
} from './editOps';

// ── Snapping (the overlay draws the marker this returns) ──────────────────
export {
  collectSnapCandidates,
  compareSnapCandidates,
  projectOnSegment,
  resolveSnap,
  snapToleranceMm,
  snapWalls,
  toSnapView,
} from './snapping';
export type { SegmentProjection, SnapCandidate, SnapKind, SnapOptions, SnapResolution } from './snapping';

// ── Numeric entry ─────────────────────────────────────────────────────────
export {
  activeField,
  clearEntry,
  createEntry,
  entryError,
  entryValueFor,
  entryView,
  feedKey,
  formatEcho,
  isEntryActive,
  isEntryApplicable,
  parseEntry,
  resetBuffer,
  wantsKey,
} from './numericEntry';
export type { EntryAction, EntryStep, NumericEntryState, NumericField, NumericUnit } from './numericEntry';

// ── Stair maths ───────────────────────────────────────────────────────────
export { comfortTreadMm, flightIssues, landingFor, solveFlight, STAIR_WELL_GAP_MM } from './stairFlight';
export type { FlightInput, FlightIssue, FlightResult, FlightSolution } from './stairFlight';

// ── Constants worth sharing ───────────────────────────────────────────────
export {
  DEFAULT_WALL_THICKNESS_MM,
  DRAG_THRESHOLD_PX,
  HINTS,
  MIN_WALL_LENGTH_MM,
  NBC_CITE,
  NBC_HEADROOM_MIN_MM,
  NBC_RISER_MAX_MM,
  NBC_STAIR_WIDTH_MIN_MM,
  NBC_TREAD_MIN_MM,
  ROTATE_STEP_DEG,
  SNAP_TOLERANCE_PX,
  WALL_END_MARGIN_MM,
} from './constants';
