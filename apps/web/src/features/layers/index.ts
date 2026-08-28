/**
 * `features/layers` — the layer manager: visibility, lock and isolate for the
 * nine §7 drawing layers.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * READ THESE THREE FILES IN THIS ORDER
 * ════════════════════════════════════════════════════════════════════════════
 *   layerSpecs   the nine layers, MIRRORED from services/drawings/layers.py
 *   mapping      which element is on which layer, and what the plan then draws
 *   pickGate     how a locked layer stops being clickable, in one place
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT MAKES THIS FEATURE HONEST
 * ════════════════════════════════════════════════════════════════════════════
 * A layer panel is trivial to fake: nine switches, nine booleans, a green test
 * that a boolean flipped, and a control that does nothing on the canvas. This
 * repository has shipped that shape more than once. So:
 *
 *  · `layerSpecs.test.ts` PARSES `services/drawings/layers.py` and fails if the
 *    TS mirror disagrees on a name, colour, linetype, lineweight or wording.
 *    One list of layers, in Python, with the browser held to it.
 *  · `mapping.test.ts` runs every layer that claims canvas presence through the
 *    plan's OWN geometry selectors (`wallsOfStorey`, `openingsOfStorey`, …) and
 *    fails unless hiding it changes what those selectors return. A toggle that
 *    stops having an effect goes red.
 *  · `pickGate.test.ts` raycasts a real mesh through the real `pickAt` and the
 *    real `PickRegistry`, and fails unless a locked element becomes unpickable.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WIRING (three calls, all in the Plan page)
 * ════════════════════════════════════════════════════════════════════════════
 *   useLayerScope(user.id, project.id)         remember per project + user
 *   const view = usePlanLayerView(house)       what the plan draws
 *   useLayerPickGate(core, house)              what the picker refuses
 *
 * then hand `view.house` to `<PlanScene>` and `view.showRooms` /
 * `view.showDimensions` / `view.showRoomTags` to the props that already exist
 * on the room fill, the dimension layer and the room-tag layer. The panel goes
 * in the DOM overlay beside the others.
 */

export { LayerPanel } from './LayerPanel';
export type { LayerPanelProps } from './LayerPanel';

export {
  DRAWING_LAYER_NAMES,
  DRAWING_LAYER_SPECS,
  CANVAS_DRAWING_LAYERS,
  aciSwatchHex,
  isDrawingLayerName,
  layerSpec,
} from './layerSpecs';
export type { DrawingLayerName, DrawingLayerSpec } from './layerSpecs';

export {
  EMPTY_PICK_BLOCK,
  blockedPicks,
  filterHouseByLayers,
  layerOfOpening,
  layerOfWall,
  resolvePlanLayerView,
} from './mapping';
export type { LayerFlags, LayerPickBlock, PlanLayerView } from './mapping';

export {
  allLayers,
  clearLayerState,
  defaultLayerState,
  readLayerState,
  storageKey,
  writeLayerState,
} from './persist';
export type { LayerScope, PersistedLayerState } from './persist';

export {
  blockedPicksFor,
  layerRows,
  planLayerViewFor,
  selectHiddenCount,
  selectIsolatedLayer,
  selectLayerLocks,
  selectLayerVisibility,
  selectLockedCount,
  useLayerStore,
} from './store';
export type { LayerRow, LayerState } from './store';

export { hasLayerPickGate, installLayerPickGate } from './pickGate';

export { useLayerPickGate, useLayerRows, useLayerScope, usePlanLayerView } from './useLayerView';
