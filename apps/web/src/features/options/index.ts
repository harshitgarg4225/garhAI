/**
 * features/options — the F3 options experience (playbook §15: generation
 * theater, options screen; §5.6 gates; §5.7 partial re-solve controls).
 *
 * Self-contained: everything here reads the jobs/model stores and dispatches
 * ops through the model store (golden rule 1). NOTHING here touches routes,
 * pages/ or the components barrel — wiring belongs to the integrator.
 *
 * Suggested mounting (Plan tab, or its own Options tab):
 *
 *   <OptionsPanel
 *     projectId={project.id}
 *     plotOutline={plot.boundary}      // optional, faint under mini plans
 *     briefReady={briefCompleteEnough} // gates the "Generate" empty state
 *   />
 *
 * OptionsPanel owns the whole lifecycle: the teach-to-generate empty state,
 * the live GenerationTheater while a job runs, the options grid with the §5.6
 * honest banner, compare-two, apply-with-confirm, lock-rooms-then-regenerate,
 * per-floor regen, more-like-this and new-seed variation. The smaller
 * components are exported for bespoke layouts (e.g. a read-only share view
 * rendering OptionCard alone).
 *
 * Prerequisites the mounting shell provides (both already true when mounted
 * inside the project shell): `useProjectStore.open(projectId)` has run — it
 * calls `useJobsStore.watchProject`, which is how useSolverJob sees jobs —
 * and `useModelStore.hydrate(projectId)` has run, so apply/lock dispatch onto
 * the real document.
 *
 * Wire contracts this feature consumes (coordinate changes with their owners):
 *   - solver job SSE events        lib/sse.ts + lib/schemas.progressEventSchema
 *   - solver job row               types.solverJobDetailSchema (own parser;
 *                                  lib/schemas.jobSchema strips `options`)
 *   - PlanOption JSON              types.planOptionSchema — mirrors
 *                                  services/solver/types.py PlanOption.to_json()
 *   - silhouette event payload     types.miniPlanSchema (data.miniPlan on
 *                                  'plan-option' artifact events; optional)
 */

export { OptionsPanel } from './OptionsPanel';
export type { OptionsPanelProps } from './OptionsPanel';

export { GenerationTheater } from './GenerationTheater';
export type { GenerationTheaterProps } from './GenerationTheater';

export { OptionCard, floorName } from './OptionCard';
export type { OptionCardProps } from './OptionCard';

export { CompareTwo } from './CompareTwo';
export type { CompareTwoProps } from './CompareTwo';

export { MiniPlanSvg } from './MiniPlanSvg';
export type { MiniPlanSvgProps } from './MiniPlanSvg';

export { VastuWheel } from './VastuWheel';
export type { VastuWheelProps } from './VastuWheel';

export {
  useOptionActions,
  useSolveOutcome,
  useSolverJob,
  useTheater,
} from './useOptions';
export type { LockableRoom, UseOptionActions, UseSolveOutcome, UseSolverJob } from './useOptions';

export {
  INITIAL_THEATER,
  reduceTheater,
  theaterFromJob,
} from './theater';
export type { TheaterSilhouette, TheaterStage, TheaterState } from './theater';

export {
  assumptionEditOp,
  bannerFor,
  compareOptions,
  complianceSummary,
  effectiveBanner,
  keyStats,
  moreLikeThisParams,
  newSeedParams,
  perFloorParams,
  regenerateOthersParams,
  vastuWheel,
} from './stats';
export type { ComplianceSummary, KeyStats, OptionComparison, SolveRequestParams } from './stats';

export { miniPlanFromOption, planViewBox } from './planGeometry';
export type { MiniPlanGeometry, PlanViewBox } from './planGeometry';

export {
  miniPlanSchema,
  planOptionSchema,
  readSolveOutcome,
  solveResultSchema,
  solverJobDetailSchema,
} from './types';
export type { MiniPlan, PlanOption, SolveOutcome, SolverJobDetail } from './types';
