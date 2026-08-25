/**
 * `pages/project/plan` — the plan drawing itself, plus the two hooks the Plan
 * page needs that no canvas module owns.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY THIS FOLDER EXISTS
 * ════════════════════════════════════════════════════════════════════════════
 * Phase 4 landed as four canvas modules with clean edges:
 *
 *   core       the scene, the camera, ONE picker
 *   tools      the eight state machines and the keyboard map
 *   overlays   what is drawn ON TOP of the plan
 *   furniture  the catalogue, placement and the box proxies
 *
 * Between them there was a hole: nothing rendered the plan. `tools` disclaims
 * "the rendered walls, rooms"; `overlays` disclaims everything that is not an
 * overlay. This folder fills it, and lives under `pages/` because that is what
 * the integrator owns.
 *
 * Nothing in here knows it is inside a page. If a `features/canvas/scene/`
 * module is ever created, these files move there unchanged and the Plan page
 * changes one import path.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE THREE RULES, INHERITED FROM `core`
 * ════════════════════════════════════════════════════════════════════════════
 * 1. Integer millimetres at any boundary that becomes an op. Nothing in this
 *    folder produces an op except `RoomTagEditor`, and it converts through the
 *    overlays' `parseAreaInput`.
 * 2. No react-three-fiber pointer handlers. Picking is `usePickableResolver`
 *    over merged geometry — see `PlanScene.tsx`.
 * 3. `frameloop="demand"`. Anything mutated outside React is followed by
 *    `core.invalidate()`.
 */

export { PlanScene } from './PlanScene';
export type { PlanSceneProps } from './PlanScene';

export { PreviewLayer } from './PreviewLayer';
export type { PreviewLayerProps } from './PreviewLayer';

export { SelectionLayer, ringForId } from './SelectionLayer';
export type { SelectionLayerProps } from './SelectionLayer';

export { RoomTagEditor } from './RoomTagEditor';
export type { RoomTagEditorProps, RoomTagEditSession, RoomTagPart } from './RoomTagEditor';

export {
  getPlanMaterials,
  refreshPlanMaterials,
  disposePlanMaterials,
} from './planMaterials';
export type { PlanMaterials } from './planMaterials';

export {
  balconiesOfStorey,
  columnRingMm,
  columnsOfStorey,
  directionVector,
  elementsExtentMm,
  openingSymbol,
  openingsOfStorey,
  planExtentMm,
  roomCentreMm,
  roomsOfStorey,
  stairSymbol,
  stairsOfStorey,
  storeyFflMm,
  storeyIndex,
  triangleVerticesMm,
  wallPointMm,
  wallQuadF,
  wallRingMm,
  wallRuns,
  wallSpanQuadF,
  wallsOfStorey,
} from './planGeometry';
export type { OpeningSymbol, PtF, QuadF, StairSymbol, WallRun } from './planGeometry';

export { useSetbackContext } from './useSetbackContext';
export { useFurnitureItems, resetFurnitureItemsCache } from './useFurnitureItems';
export type { FurnitureItems } from './useFurnitureItems';
