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

export {
  checkBoundary,
  edgeFacing,
  edgeLengthMm,
  edgeLengthsMm,
  edgeMidpoint,
  frontEdgeIndex,
  insertVertexOnEdge,
  moveVertex,
  rectBoundaryMm,
  remapRoadsAfterInsert,
  remapRoadsAfterRemove,
  removeVertex,
  setEdgeLengthMm,
} from './geometry';
export type { BoundaryCheck, PolygonEditResult } from './geometry';

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
  REG_VALUE_KEYS,
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
  RegFacts,
  RegValueKey,
  ResolvedRegProfile,
  ResolvedRegValue,
  RulepackDoc,
} from './rules';

export {
  useModelReady,
  usePlotActions,
  usePlotDoc,
  useRulepack,
  useRulepackList,
  useUnitsDisplay,
} from './usePlot';
export type { PlotActions } from './usePlot';
