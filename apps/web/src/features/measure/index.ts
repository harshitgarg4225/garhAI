/**
 * `features/measure` — "how far is that?", answered without leaving the canvas.
 *
 * Four readings, one snapping rule, and nothing that touches the model:
 *
 *   distance   two points, or a chain — per-leg lengths and a running total
 *   angle      three points: arm, corner, arm
 *   area       a closed region, in m² AND ft² (Indian practice quotes both)
 *   (all)      persisted until dismissed, in the project's display units
 *
 * WHAT MAKES THE NUMBER TRUSTWORTHY, in order of importance:
 *
 *  1. Every point resolves through `canvas/tools/snapping.resolveSnap` — the
 *     SAME function the wall tool uses. A measure tool that snapped differently
 *     from the drawing tools would report a number the building does not have.
 *  2. Every length, area, centroid and rounding call delegates to `@garh/model`
 *     (golden-tested against the Python twin that dimensions the sheets), and
 *     every conversion to text goes through `lib/units.ts`.
 *  3. Measurements are pick targets registered with the ONE `PickRegistry`
 *     (`scene.ts`), proven by a spec that raycasts through the core's real
 *     `pickAt` — bug pattern 4 has no second chance here.
 *
 * MOUNTING (three places, all outside this directory — see the handoff):
 *   · `<MeasureLayer …/>`  inside `PlanScene`, in the R3F tree
 *   · `<MeasurePanel …/>`  in `<CanvasRoot overlay={…}>`
 *   · `useMeasureController({ core, enabled: activeTool === 'measure' })` in
 *     `PlanPage`, its `canvasHandlers` merged into the ones already passed.
 */

export { MeasureLayer, MEASURE_LABEL_PX } from './MeasureLayer';
export type { MeasureLayerProps } from './MeasureLayer';
export { MeasurePanel } from './MeasurePanel';
export type { MeasurePanelProps } from './MeasurePanel';
export { useMeasureController } from './useMeasureController';
export type { MeasureController, MeasureControllerOptions } from './useMeasureController';

export {
  MeasureSession,
  createMeasureIdFactory,
  measureBlockReason,
  MEASURE_HINTS,
} from './session';
export type { MeasureResponse, MeasureSessionOptions } from './session';

export {
  MeasureScene,
  measurementSegments,
  MEASURE_PICK_KIND,
  MEASURE_PICK_WIDTH_PX,
  MEASURE_TICK_HALF_PX,
} from './scene';
export type { MeasureSceneInput, MeasureSceneMaterials, MeasureSegment } from './scene';

export { useMeasureStore, measurementsForStorey, resetMeasureStore } from './store';
export type { MeasureState } from './store';

export { measurementLabels, draftLabels } from './labels';
export type { MeasureLabel } from './labels';

export {
  formatAngle,
  formatAreaBoth,
  formatDelta,
  formatLengthDetail,
  formatMeasureLength,
  measureReadouts,
  measurementReadouts,
  measurementLabel,
  NO_VALUE,
} from './format';

export {
  closesRing,
  draftPolyline,
  interiorAngleDeg,
  measurementAngleDeg,
  midpointMm,
  ringAreaMm2,
  ringCentroidMm,
  ringPerimeterMm,
  segmentLengthsMm,
  totalLengthMm,
} from './geometry';

export { MEASURE_ID_PREFIX, MEASURE_KINDS, isMeasureId } from './types';
export type { MeasureDraft, MeasureKind, Measurement } from './types';
