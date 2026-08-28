/**
 * store.ts — the list of saved views.
 *
 * Zustand, and a module store rather than React context, for the reason
 * `features/underlay/store.ts` and `features/layers/store.ts` both give: this
 * state has consumers on either side of the `<Canvas>` boundary, and context
 * does not cross react-three-fiber's separate React root. The panel lives in
 * the DOM overlay; a future "restore view" keyboard command lives in the page.
 * One store, both sides.
 *
 * WHAT IS AND IS NOT IN HERE. The list, and nothing else. The CAMERA is not:
 * `ViewportController` owns the live camera and this store must never mirror
 * it, because a store that re-renders React on every pan is exactly the §14
 * budget failure the controller exists to avoid. Restoring is therefore a
 * function call against the controller (`restore.ts`), not a state change here.
 *
 * PERSISTENCE. `bind(scope)` is called by the panel once the project and the
 * signed-in user are known; every mutation writes back on that key. Before
 * `bind`, and after `unbind`, the store works normally and simply does not
 * remember — which is also what a browser with storage switched off gets.
 */

import { create } from 'zustand';

import { isStorableCamera } from './camera';
import { cleanViewName, clearViews, MAX_VIEWS, readViews, writeViews } from './persist';
import type { NamedView, SavedCamera, ViewsScope } from './types';

/**
 * Why a save was refused. The panel says this out loud rather than dropping the
 * click — a Save button that sometimes does nothing is unlearnable.
 *
 *   full             the list is at `MAX_VIEWS`
 *   unusable-camera  a camera the controller would not take back unchanged
 *   no-canvas        there is no canvas yet, so there is no camera to capture
 */
export type SaveRefusal = 'full' | 'unusable-camera' | 'no-canvas';

export interface SaveResult {
  readonly view: NamedView | null;
  readonly refused: SaveRefusal | null;
}

export interface ViewsState {
  /** Set once the panel knows the project and the user. Null = do not persist. */
  scope: ViewsScope | null;
  /** Display order IS array order. `createdAt` is shown, never sorted on. */
  views: NamedView[];

  /** Adopt a project+user scope and load its stored list. */
  bind: (scope: ViewsScope) => void;
  /** Forget the scope (project closed). The list stays; writes stop. */
  unbind: () => void;

  /** Append a view. Empty names get a generated one — see {@link nextViewName}. */
  saveView: (name: string, camera: SavedCamera) => SaveResult;
  /** Rename in place. An all-whitespace name is refused, leaving the old one. */
  rename: (id: string, name: string) => void;
  remove: (id: string) => void;
  /** Move a view to `toIndex`, clamped. Out-of-range indices are not an error. */
  move: (id: string, toIndex: number) => void;
  /** Drop every view for the bound scope, and the stored payload with it. */
  clearAll: () => void;
}

/**
 * Local id counter.
 *
 * NOT `newUuid()` from `lib/ids`: that throws on a browser with no Web Crypto,
 * and refusing to let someone name a view because of a missing crypto API would
 * be absurd. A view id is a list key inside one browser's `localStorage` — it
 * needs to be unique in that list, not globally unique, and never leaves the
 * device. The timestamp keeps ids unique across page loads; the counter keeps
 * them unique within one.
 */
let idCounter = 0;

function newViewId(existing: readonly NamedView[]): string {
  const taken = new Set(existing.map((view) => view.id));
  for (;;) {
    idCounter += 1;
    const id = `view_${Date.now().toString(36)}_${idCounter.toString(36)}`;
    if (!taken.has(id)) return id;
  }
}

/**
 * "View 1", "View 2", … skipping numbers already in use.
 *
 * Exported because the panel shows it as the input's placeholder, so what you
 * see before typing is what you get if you do not.
 */
export function nextViewName(existing: readonly NamedView[]): string {
  const taken = new Set(existing.map((view) => view.name));
  for (let n = existing.length + 1; ; n++) {
    const name = `View ${String(n)}`;
    if (!taken.has(name)) return name;
  }
}

/** Persist the list, if there is a scope to persist it under. */
function persist(scope: ViewsScope | null, views: readonly NamedView[]): void {
  if (scope === null) return;
  writeViews(scope, views);
}

export const useViewsStore = create<ViewsState>()((set, get) => ({
  scope: null,
  views: [],

  bind: (scope) => {
    const current = get().scope;
    if (
      current !== null &&
      current.userId === scope.userId &&
      current.projectId === scope.projectId
    ) {
      return;
    }
    set({ scope, views: readViews(scope) ?? [] });
  },

  unbind: () => {
    set({ scope: null });
  },

  saveView: (name, camera) => {
    // A camera the controller would not take back unchanged is a bookmark that
    // lands somewhere else. Refuse it here rather than store a lie.
    if (!isStorableCamera(camera)) return { view: null, refused: 'unusable-camera' };

    const { scope, views } = get();
    if (views.length >= MAX_VIEWS) return { view: null, refused: 'full' };

    const cleaned = cleanViewName(name);
    const view: NamedView = {
      id: newViewId(views),
      name: cleaned === '' ? nextViewName(views) : cleaned,
      camera,
      createdAt: Date.now(),
    };
    const next = [...views, view];
    set({ views: next });
    persist(scope, next);
    return { view, refused: null };
  },

  rename: (id, name) => {
    const cleaned = cleanViewName(name);
    // Refusing an empty rename beats accepting one: a row with no label is
    // unclickable in any meaningful sense, and "cancel" is what Escape is for.
    if (cleaned === '') return;
    const { scope, views } = get();
    let changed = false;
    const next = views.map((view) => {
      if (view.id !== id || view.name === cleaned) return view;
      changed = true;
      return { ...view, name: cleaned };
    });
    if (!changed) return;
    set({ views: next });
    persist(scope, next);
  },

  remove: (id) => {
    const { scope, views } = get();
    const next = views.filter((view) => view.id !== id);
    if (next.length === views.length) return;
    set({ views: next });
    persist(scope, next);
  },

  move: (id, toIndex) => {
    const { scope, views } = get();
    const from = views.findIndex((view) => view.id === id);
    if (from === -1) return;
    const target = Math.min(views.length - 1, Math.max(0, Math.trunc(toIndex)));
    if (target === from) return;
    const next = [...views];
    const [moved] = next.splice(from, 1);
    if (moved === undefined) return;
    next.splice(target, 0, moved);
    set({ views: next });
    persist(scope, next);
  },

  clearAll: () => {
    const { scope } = get();
    set({ views: [] });
    if (scope !== null) clearViews(scope);
  },
}));

// ---------------------------------------------------------------------------
// Selectors
// ---------------------------------------------------------------------------

export const selectViews = (state: ViewsState): NamedView[] => state.views;

export const selectViewCount = (state: ViewsState): number => state.views.length;

/** True when the list is at {@link MAX_VIEWS} and a save would be refused. */
export const selectIsFull = (state: ViewsState): boolean => state.views.length >= MAX_VIEWS;

export function findView(state: ViewsState, id: string): NamedView | null {
  return state.views.find((view) => view.id === id) ?? null;
}
