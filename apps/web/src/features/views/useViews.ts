/**
 * useViews — the one place the saved-view list, the camera, and the rest of the
 * app are wired together.
 *
 * Everything below this hook is pure or store-shaped and testable without
 * React: `camera.ts` reads the controller, `builtins.ts` reads the model,
 * `restore.ts` drives the flight, `store.ts` holds the list. This file is the
 * seam, and it is deliberately the only file in the feature that knows the
 * `ui`, `model`, `selection` and `session` stores exist.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY THE MODE SWITCH IS WIRED HERE AND NOT LEFT TO THE CALLER
 * ════════════════════════════════════════════════════════════════════════════
 * Restoring a view saved in the other projection has to change the projection
 * (see the decision note in `restore.ts`), and the only honest way to do that
 * is `ui.setViewMode` — the store `PlanPage` passes to `CanvasRoot`, which
 * `CameraRig` turns into a camera swap. Writing `viewport.setMode` directly
 * would set the controller's idea of the mode while R3F kept rendering through
 * the other camera: a module that believes it is wired up and is not, which is
 * a failure shape this repo has already shipped once.
 *
 * Wiring it inside the hook (rather than exposing a `requestMode` prop for the
 * page to pass) means there is no way to mount this feature half-connected.
 * `useViews.test`-style coverage lives in `ViewsPanel.test.tsx`, which restores
 * a 2D view while the controller is in 3D and asserts the `ui` store actually
 * flipped.
 *
 * PERFORMANCE. The built-in extents walk the model, so they are memoised on the
 * document, the storey and the selection. Nothing here subscribes to the
 * camera: a pan must not re-render this panel, and the controller keeps the
 * camera outside React precisely so it does not have to.
 */

import { useCallback, useEffect, useMemo, useRef } from 'react';

import { useModelStore } from '../../stores/model';
import { useSelectionStore } from '../../stores/selection';
import { useSessionStore } from '../../stores/session';
import { useUiStore } from '../../stores/ui';
import type { CanvasCore } from '../canvas/core/context';
import { cameraForExtent, captureCamera } from './camera';
import { builtInViews } from './builtins';
import { restoreCamera, type RestoreOptions } from './restore';
import { nextViewName, selectIsFull, selectViews, useViewsStore, type SaveResult } from './store';
import type {
  BuiltInViewId,
  BuiltInViewSpec,
  CanvasMode,
  NamedView,
  RestoreOutcome,
} from './types';

/**
 * Scope for a viewer with no account — a share link. Their saved views live in
 * their own browser and are theirs; the alternative is a panel that silently
 * forgets everything, which is worse than a shared bucket on a device that is
 * already theirs alone.
 */
export const ANONYMOUS_USER_ID = 'anon';

export interface UseViewsOptions {
  readonly projectId: string;
  /** The canvas core, once `CanvasRoot` has handed it over. Null until then. */
  readonly core: CanvasCore | null;
  /** Test seam: injected clock / reduced-motion / duration. */
  readonly restoreOptions?: RestoreOptions | undefined;
}

export interface ViewsController {
  readonly views: readonly NamedView[];
  readonly builtIns: readonly BuiltInViewSpec[];
  /** False until the canvas exists; every control is disabled until it does. */
  readonly ready: boolean;
  readonly isFull: boolean;
  /** What an unnamed save would be called. Shown as the input's placeholder. */
  readonly suggestedName: string;
  readonly saveCurrent: (name: string) => SaveResult;
  readonly restore: (id: string) => RestoreOutcome | null;
  readonly restoreBuiltIn: (id: BuiltInViewId) => RestoreOutcome | null;
  readonly rename: (id: string, name: string) => void;
  readonly remove: (id: string) => void;
  readonly move: (id: string, toIndex: number) => void;
  readonly clearAll: () => void;
}

export function useViews({ projectId, core, restoreOptions }: UseViewsOptions): ViewsController {
  const views = useViewsStore(selectViews);
  const isFull = useViewsStore(selectIsFull);
  const userId = useSessionStore((state) => state.user?.id ?? null);

  const doc = useModelStore((state) => state.doc);
  const activeStoreyId = useUiStore((state) => state.activeStoreyId);
  const selectionIds = useSelectionStore((state) => state.ids);

  // Adopt the project+user scope and load whatever was stored for it. Runs
  // again if the user signs in mid-session, which correctly swaps an anonymous
  // list for their own.
  useEffect(() => {
    useViewsStore.getState().bind({ userId: userId ?? ANONYMOUS_USER_ID, projectId });
  }, [userId, projectId]);

  const builtIns = useMemo(
    () =>
      builtInViews({
        house: doc.house,
        plotBoundary: doc.plot.boundary,
        activeStoreyId,
        selectionIds,
      }),
    [doc, activeStoreyId, selectionIds],
  );

  // The flight currently in the air, so a second restore replaces it instead of
  // fighting it, and so unmounting the panel does not leave one running.
  const inFlight = useRef<(() => void) | null>(null);
  useEffect(
    () => () => {
      inFlight.current?.();
      inFlight.current = null;
    },
    [],
  );

  const requestMode = useCallback((mode: CanvasMode) => {
    useUiStore.getState().setViewMode(mode);
  }, []);

  const run = useCallback((outcome: RestoreOutcome): RestoreOutcome => {
    inFlight.current = outcome.cancel;
    return outcome;
  }, []);

  const restore = useCallback(
    (id: string): RestoreOutcome | null => {
      if (core === null) return null;
      const view = useViewsStore.getState().views.find((candidate) => candidate.id === id);
      if (view === undefined) return null;
      inFlight.current?.();
      return run(
        restoreCamera(core.viewport, view.camera, { requestMode, ...(restoreOptions ?? {}) }),
      );
    },
    [core, requestMode, restoreOptions, run],
  );

  const restoreBuiltIn = useCallback(
    (id: BuiltInViewId): RestoreOutcome | null => {
      if (core === null) return null;
      const extent = builtIns.find((candidate) => candidate.id === id)?.extent ?? null;
      if (extent === null) return null;
      inFlight.current?.();
      // Computed against the LIVE projection, so "fit selection" means the same
      // thing in the plan and in the 3D view without a second code path.
      const camera = cameraForExtent(core.viewport, extent);
      return run(restoreCamera(core.viewport, camera, { requestMode, ...(restoreOptions ?? {}) }));
    },
    [builtIns, core, requestMode, restoreOptions, run],
  );

  const saveCurrent = useCallback(
    (name: string): SaveResult => {
      // No canvas yet means no camera to capture — a distinct refusal from a
      // full list, because the panel's answer to it is "wait", not "delete one".
      if (core === null) return { view: null, refused: 'no-canvas' };
      return useViewsStore.getState().saveView(name, captureCamera(core.viewport));
    },
    [core],
  );

  // Actions are read imperatively rather than subscribed to: they never change
  // identity, and the panel already re-renders when the list does.
  const rename = useCallback((id: string, name: string) => {
    useViewsStore.getState().rename(id, name);
  }, []);
  const remove = useCallback((id: string) => {
    useViewsStore.getState().remove(id);
  }, []);
  const move = useCallback((id: string, toIndex: number) => {
    useViewsStore.getState().move(id, toIndex);
  }, []);
  const clearAll = useCallback(() => {
    useViewsStore.getState().clearAll();
  }, []);

  return {
    views,
    builtIns,
    ready: core !== null,
    isFull,
    suggestedName: nextViewName(views),
    saveCurrent,
    restore,
    restoreBuiltIn,
    rename,
    remove,
    move,
    clearAll,
  };
}
