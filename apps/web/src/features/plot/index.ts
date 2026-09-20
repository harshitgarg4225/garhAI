/**
 * features/plot — the F1 plot surface (Phase 2).
 *
 * INTEGRATOR CONTRACT. Mount points:
 *   <PlotEditor />       the SVG boundary editor (self-contained: quick-start
 *                        empty state, drag handles, click-to-edit lengths,
 *                        north compass overlay, live area readout)
 *   <RoadEdges />        per-edge road toggle + width panel (side rail)
 *   <RegProfilePanel />  city preset + resolved setbacks/FAR/coverage/height
 *   <NorthCompass />     standalone compass (already embedded in PlotEditor)
 *   <RectQuickStart />   width × depth starter (already the editor's empty state)
 *   <AreaReadout />      "1,200 sq ft · 133 gaj" chip for a header slot
 *
 * All of them read the model store and write ONLY via op dispatch — mount them
 * anywhere inside the project shell; they need no props to function.
 *
 * The pure logic (geometry, op builders, rulepack resolution) is exported for
 * reuse and is what `plot.test.ts` pins.
 */

export { PlotEditor } from './PlotEditor';
export type { PlotEditorProps } from './PlotEditor';
export { RoadEdges } from './RoadEdges';
export type { RoadEdgesProps } from './RoadEdges';
export { RegProfilePanel } from './RegProfilePanel';
export type { RegProfilePanelProps } from './RegProfilePanel';
export { NorthCompass } from './NorthCompass';
export type { NorthCompassProps } from './NorthCompass';
export { RectQuickStart } from './RectQuickStart';
export type { RectQuickStartProps } from './RectQuickStart';
export { AreaReadout } from './AreaReadout';
export type { AreaReadoutProps } from './AreaReadout';
export { DeedEntry } from './DeedEntry';
export type { DeedEntryMode, DeedEntryProps } from './DeedEntry';
export { PlotReadouts, DEED_AREA_TOLERANCE_PCT } from './PlotReadouts';
export type { PlotReadoutsProps } from './PlotReadouts';
export { PlanEnvelopeBanner } from './PlanEnvelopeBanner';
export type { PlanEnvelopeBannerProps } from './PlanEnvelopeBanner';

export {
  EDGE_ROLE_LABELS,
  checkBoundary,
  defaultEdgeLengthMode,
  edgeFacing,
  edgeLengthMm,
  edgeLengthsMm,
  edgeMidpoint,
  edgeRoles,
  frontEdgeIndex,
  insertVertexOnEdge,
  isRectilinear,
  moveVertex,
  rectBoundaryMm,
  remapRoadsAfterInsert,
  remapRoadsAfterRemove,
  removeVertex,
  setEdgeLengthMm,
} from './geometry';
export type {
  BoundaryCheck,
  EdgeLengthMode,
  EdgeLengthResult,
  EdgeRole,
  EdgeSideEffect,
  PolygonEditResult,
} from './geometry';

export {
  TRAVERSE_MAX_MISCLOSURE_RATIO,
  cornerLabel,
  edgeBearingDeg,
  formatBearingDms,
  parseBearingDeg,
  ringDiagonals,
  ringFromSidesAndDiagonals,
  ringFromTraverse,
} from './deed';
export type {
  DeedResult,
  DiagonalMm,
  SideCheck,
  TraverseClosure,
  TraverseLeg,
  TraverseResult,
} from './deed';

export {
  boundaryGroupOps,
  boundaryOp,
  normalizeNorthDeg,
  northOp,
  regProfileOp,
  roadOp,
} from './ops';
export type { BoundarySource } from './ops';

export {
  CITY_PACK_OPTIONS,
  DEED_AREA_KEY,
  LEGACY_SIDE_OVERRIDE_KEY,
  REG_VALUE_KEYS,
  readDeedAreaMm2,
  reconcileDeedArea,
  withDeedAreaMm2,
  REG_VALUE_META,
  buildRegFacts,
  cityPackFromStored,
  cityPackToStored,
  formatRegValue,
  parseRegScalar,
  readValueOverrides,
  resolveRegValues,
  rulepackDocSchema,
  whenMatches,
  withValueOverride,
} from './rules';
export type {
  DeedReconciliation,
  RegFacts,
  RegValueKey,
  ResolvedRegProfile,
  ResolvedRegValue,
  RulepackDoc,
} from './rules';

export {
  useHouseWalls,
  useModelReady,
  usePlotActions,
  usePlotDoc,
  usePlotEditSession,
  useRulepack,
  useRulepackList,
  useUnitsDisplay,
} from './usePlot';
export type { PlotActions, PlotEditSession } from './usePlot';
