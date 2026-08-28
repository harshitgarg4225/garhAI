/**
 * `features/storeys` — multi-storey navigation, the storey-below underlay, and
 * storey copy (C-9).
 *
 * G+2 is the default Indian residential job, and the model has always supported
 * storeys. What was missing was the working surface around them: a way to see
 * the stack, a way to draw the first floor over a ghost of the ground floor,
 * and the one action every architect performs on the second day of a job —
 * "make the first floor the same as the ground floor, then change three
 * things".
 *
 * Five pieces, in the order they matter:
 *
 *   copyStorey.ts       the PURE planner: document + intent → a list of ops.
 *                       No store, no dispatch, no mutation. It folds its own
 *                       output on a fork before returning it, which is both how
 *                       it learns the derived room ids and how it knows the
 *                       copy is valid.
 *   actions.ts          the only place that dispatches. ONE dispatch per
 *                       gesture, so a fifty-op copy is ONE undo step.
 *   ghostGeometry.ts    the storey below as two plain buffers — no element ids,
 *                       nothing the picker could be handed.
 *   StoreyGhostLayer    draws those buffers inside the ONE `<Canvas>`, between
 *                       the grid and the plan, and registers NOTHING with
 *                       `PickRegistry`. Measured, not asserted: see
 *                       `ghost.test.ts`.
 *   StoreyPanel         the DOM card — the stack, the ghost controls, add
 *                       storey, and the copy dialog with its destructive guard.
 *
 * `store.ts` is what lets the layer and the panel talk: react-three-fiber
 * reconciles its children in a separate React root, so context does not cross
 * the canvas boundary and a module store is the only shared channel. It holds
 * VIEW state only — the storeys themselves are the document, and the active
 * storey is `ui.activeStoreyId`, exactly as it was before this feature existed.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE OPS THIS FEATURE EMITS
 * ════════════════════════════════════════════════════════════════════════════
 * Twelve op types, every one already in the §4 taxonomy — nothing new, because
 * an op that folds in TypeScript and not in the Python twin would break the
 * byte-identical state hash the whole product rests on:
 *
 *   storey.add · storey.set_height · wall.add · wall.delete · opening.add
 *   stair.add · stair.delete · column.set · furniture.set · balcony.set
 *   room.assign · room.set_target
 */

export { StoreyGhostLayer } from './StoreyGhostLayer';
export type { StoreyGhostLayerProps } from './StoreyGhostLayer';
export { StoreyPanel } from './StoreyPanel';
export type { StoreyPanelProps } from './StoreyPanel';

export { buildStoreyGhost, storeyBelow } from './ghostGeometry';
export type { StoreyGhostGeometry } from './ghostGeometry';

export {
  addStoreyOp,
  describeCounts,
  isStoreyEmpty,
  planStoreyCopy,
  storeyContentCounts,
  totalElements,
} from './copyStorey';
export type {
  StoreyContentCounts,
  StoreyCopyInput,
  StoreyCopyPlan,
  StoreyCopyPlanResult,
  StoreyCopyRefusal,
  StoreyCopyRefusalReason,
  StoreyCopyTarget,
} from './copyStorey';

export { runAddStorey, runStoreyCopy } from './actions';
export type { AddStoreyOutcome, StoreyCopyOutcome } from './actions';

export {
  DEFAULT_GHOST_OPACITY,
  MAX_GHOST_OPACITY,
  MIN_GHOST_OPACITY,
  useStoreysStore,
} from './store';
export type { StoreysState } from './store';
