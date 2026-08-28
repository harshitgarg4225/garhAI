/**
 * `features/views` — named views and saved cameras.
 *
 * Save where the camera is, name it, and get back to it exactly — in the plan
 * or in the 3D view, both of which are the same camera rig with two
 * projections. Plus three views nobody should have to save (fit all, fit
 * selection, fit storey), computed from the model rather than stored.
 *
 * Read the modules in this order the first time:
 *   types      what a saved view IS, and why the camera is a union
 *   camera     capture / apply / equality — where "exact" is made true
 *   tween      the shape of the flight, and why its endpoint is the target
 *   restore    the flight itself, interruption, and the cross-mode decision
 *   builtins   fit all / selection / storey, over the model
 *   persist    localStorage, per project and per user, every access wrapped
 *   store      the list
 *   useViews   the one seam that knows the app's stores exist
 *   ViewsPanel the DOM overlay panel
 *
 * MOUNTING IT. `<ViewsPanel projectId={project.id} core={core} />` in the plan
 * page's DOM overlay, where `core` is what `CanvasRoot`'s `onCoreReady` handed
 * over. Nothing else is needed: the panel binds its own storage scope and wires
 * its own mode switch through the `ui` store.
 *
 * WHAT THIS FEATURE DOES NOT DO. It adds no canvas layer, registers nothing
 * with `PickRegistry`, and dispatches no op. It reads the viewport controller
 * and writes the viewport controller, and that is the whole of its contact with
 * the scene.
 */

export type {
  BuiltInViewId,
  BuiltInViewSpec,
  NamedView,
  RestoreOutcome,
  Saved2dCamera,
  Saved3dCamera,
  SavedCamera,
  ViewExtent,
  ViewsScope,
} from './types';
export { BUILT_IN_VIEW_IDS } from './types';

export {
  applyCamera,
  cameraForExtent,
  captureCamera,
  describeCamera,
  isStorableCamera,
  normaliseCamera,
  sameCamera,
} from './camera';

export { easeOutCubic, interpolateCamera, shortestAngleDeltaDeg } from './tween';

export { prefersReducedMotion, restoreCamera, RESTORE_DURATION_MS } from './restore';
export type { RestoreOptions, TransitionClock } from './restore';

export {
  builtInExtent,
  builtInViews,
  fitAllExtent,
  fitSelectionExtent,
  fitStoreyExtent,
} from './builtins';
export type { BuiltInInput } from './builtins';

export {
  cleanViewName,
  clearViews,
  MAX_NAME_LENGTH,
  MAX_VIEWS,
  parseCamera,
  readViews,
  storageKey,
  writeViews,
} from './persist';

export {
  findView,
  nextViewName,
  selectIsFull,
  selectViewCount,
  selectViews,
  useViewsStore,
} from './store';
export type { SaveRefusal, SaveResult, ViewsState } from './store';

export { ANONYMOUS_USER_ID, useViews } from './useViews';
export type { UseViewsOptions, ViewsController } from './useViews';

export { ViewsPanel } from './ViewsPanel';
export type { ViewsPanelProps } from './ViewsPanel';
