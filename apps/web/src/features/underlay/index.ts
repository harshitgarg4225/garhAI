/**
 * features/underlay — trace over a scanned plan (Rayon parity).
 *
 * Three pieces, in the order they matter:
 *
 *   calibration.ts    the two-point → mmPerPx arithmetic. Pure, unit-tested,
 *                     and the only place the algebra lives.
 *   UnderlayLayer     the textured quad, mounted INSIDE the one `<Canvas>`,
 *                     below the grid and outside `PickRegistry` entirely.
 *   UnderlayPanel     the DOM card, plus the armed capture layer that the
 *                     calibrate and move gestures borrow the pointer through.
 *
 * `store.ts` is what lets those first two talk: react-three-fiber reconciles
 * its children with a separate React root, so context does not cross the canvas
 * boundary and a module store is the only shared channel.
 *
 * The underlay is NOT model state. No op, no undo, no state hash, no export —
 * it is a view aid attached to a project, persisted by four plain CRUD routes.
 */

export { UnderlayLayer } from './UnderlayLayer';
export type { UnderlayLayerProps } from './UnderlayLayer';
export { UnderlayPanel } from './UnderlayPanel';
export type { UnderlayPanelProps } from './UnderlayPanel';
export { useUnderlayStore } from './store';
export type { UnderlayMode, UnderlayState } from './store';
export {
  calibrationRefusalText,
  markDistanceMm,
  recalibrate,
  underlayExtentMm,
  MAX_UNDERLAY_MM_PER_PX,
  MIN_MARK_SEPARATION_MM,
  MIN_UNDERLAY_MM_PER_PX,
} from './calibration';
export type {
  CalibrationInput,
  CalibrationRefusal,
  CalibrationResult,
  MarkMm,
  UnderlayCalibration,
} from './calibration';
export { createPatchQueue, PATCH_DEBOUNCE_MS } from './patchQueue';
export type { PatchQueue, PatchQueueOptions } from './patchQueue';
