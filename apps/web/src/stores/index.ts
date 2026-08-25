/**
 * The six Zustand stores, named exactly as playbook §12 names them — plus the
 * Phase-5 `three` slice.
 *
 *   session    who is signed in, and what they may do
 *   project    the project list, and the one project that is open
 *   model      the folded design document — THE ONLY WRITER of design state
 *   selection  what is selected or hovered on the canvas
 *   jobs       solver / render / sheet / export work on a worker
 *   ui         chrome: active tool, storey, view mode, snap, toasts, theme
 *   three      the 3D view: storey visibility, the applied-facade mirror,
 *              rebuild/engine telemetry (camera mode stays `ui.viewMode`;
 *              the sun scrubber's store is re-exported from the sun feature —
 *              `stores/three.ts` explains both)
 *
 * ## The one rule
 *
 * **Components dispatch ops; they never mutate the design.** `model.dispatch()`
 * is the only way a wall moves. Everything else — the optimistic local fold,
 * the server queue, rebasing on a 409, rolling back on a rejection, the undo
 * stacks, the autosave badge — follows from that single writer, and stops
 * following from it the moment a second one appears.
 *
 * ## Import style
 *
 * Selectors are exported next to their store and are plain functions, so they
 * compose with `useXStore(selectFoo)` and stay usable in tests without React:
 *
 * ```ts
 * const canUndo = useModelStore(selectCanUndo);
 * const tool    = useUiStore(selectActiveTool);
 * ```
 *
 * Prefer a narrow selector over destructuring the whole store — Zustand
 * re-renders on reference change, and `const s = useModelStore()` re-renders
 * every canvas component on every keystroke of a room name.
 */

// ── session ────────────────────────────────────────────────────────────────
export {
  useSessionStore,
  installSessionWatcher,
  selectStatus as selectSessionStatus,
  selectUser,
  selectFirm,
  selectIsAuthenticated,
  selectIsResolvingSession,
  selectIsAdmin,
  selectCanWrite,
  selectShareContext,
  selectAuthError,
  selectIsAuthBusy,
  selectOtpChallenge,
} from './session';
export type {
  SessionState,
  SessionStatus,
  OtpChallenge,
  OtpRequestResult,
  ShareContext,
} from './session';

// ── project ────────────────────────────────────────────────────────────────
export {
  useProjectStore,
  toProjectDTO,
  toProjectStatus,
  selectProjects,
  selectCurrentProject,
  selectProjectError,
  selectIsLoadingProjects,
  selectPlot,
  selectBrief,
  selectServerHeadIdx,
  selectVersionBranch,
  selectUnits,
  selectDemoProject,
} from './project';
export type { ProjectState, ProjectDTO, CreateProjectPayload } from './project';

// ── model ──────────────────────────────────────────────────────────────────
export {
  useModelStore,
  selectDoc,
  selectHouse,
  selectPlot as selectModelPlot,
  selectBrief as selectModelBrief,
  selectStatus as selectModelStatus,
  selectIsReady,
  selectCanUndo,
  selectCanRedo,
  selectNextUndoLabel,
  selectPendingCount,
  selectHasUnsavedWork,
  /* The design's version counter. Read by the autosave badge and — since
     Phase 7 — by the render gallery, which re-lists when it moves and lets the
     SERVER's `stale` flag drive the §9 banner. See the selector's own note. */
  selectHeadIdx,
  selectVersionBranch,
  selectDiverged,
  selectStoreys,
  selectSaveBadge,
} from './model';
export type {
  ModelState,
  ModelStatus,
  SaveState,
  SaveBadge,
  PendingGroup,
  HistoryEntry,
  DispatchOptions,
  DispatchResult,
} from './model';

// ── selection ──────────────────────────────────────────────────────────────
export {
  useSelectionStore,
  selectSelectedIds,
  selectPrimaryId,
  selectHoverId,
  selectHoverHit,
  selectSelectionCount,
  selectIsSelected,
  selectIdsOfType,
  selectKindOf,
  selectPrimaryKind,
  selectPrimaryType,
} from './selection';
export type { SelectionState, SelectionHit, SelectMode, MarqueeRect } from './selection';

// ── jobs ───────────────────────────────────────────────────────────────────
export {
  useJobsStore,
  isTerminal,
  toJobDTO,
  toUiKind,
  selectJobsFor,
  selectActiveJobsFor,
  selectHasActiveJob,
  selectJobsError,
} from './jobs';
export type { JobsState, JobDTO } from './jobs';

// ── ui ─────────────────────────────────────────────────────────────────────
export {
  useUiStore,
  snapStepMm,
  CANVAS_TOGGLES,
  selectActiveTool,
  selectViewMode,
  selectActiveStoreyId,
  selectSnapMode,
  selectSnapStepMm,
  selectCanvasLayer,
  selectCanvasLayers,
  selectCanvasFocus,
  selectScaleLabel,
  selectToasts,
  selectModal,
  selectTheme,
  selectTourStep,
  selectTourDone,
  selectKeyboardEnabled,
} from './ui';
export type {
  UiState,
  ViewMode,
  SnapMode,
  CanvasToggle,
  CanvasFocusRequest,
  ThemePreference,
  Toast,
  ToastInput,
  ToastTone,
  ModalRequest,
} from './ui';

// ── three (Phase 5) ────────────────────────────────────────────────────────
export {
  useThreeStore,
  useSunStore,
  appliedFacadeOf,
  selectVisibleStoreyId,
  selectAppliedFacade,
  selectEngineStatus,
  selectLastRebuild,
} from './three';
export type {
  ThreeState,
  SunState,
  AppliedFacade,
  EngineStatusWord,
  RebuildTelemetry,
} from './three';
