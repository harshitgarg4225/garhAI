/**
 * `three` — the 3D view's chrome state (Phase 5).
 *
 * The task sheet calls this "camera mode, sun datetime, storey visibility,
 * facade kit + seed". Two of those four already have exactly one home, and
 * this slice deliberately does NOT duplicate them:
 *
 *   · **Camera mode** is `ui.viewMode` — it existed before Phase 5, the Tab
 *     binding (`view.toggle`) writes it, and the camera rig reads it. A second
 *     copy here would be two sources of truth for one keystroke.
 *   · **Sun datetime** is `features/canvas/sun/sunStore.ts` — deliberately a
 *     store of its own, outside the ProjectDoc, so that scrubbing the sun can
 *     never dirty the model (`scrubInvariance.test.ts` pins the document hash
 *     across a full-day scrub). It is re-exported below so app code has one
 *     import path for "3D view state".
 *
 * What this slice OWNS:
 *
 *   · **Storey visibility** — which storey the 3D view shows (`null` = the
 *     whole building). View state, not design state: it folds from no op and
 *     must never touch the document.
 *   · **Applied facade kit + seed** — a read-only MIRROR of
 *     `doc.house.facade`, synced by a model-store subscription below, so
 *     chrome (top-bar chips, the e2e probe) can subscribe to "which kit is on"
 *     without re-folding the doc. The model store remains the ONLY writer of
 *     the facade itself; the panel dispatches ops 27/28 and this mirror
 *     follows the fold.
 *   · **3D honesty telemetry** — the boolean-engine state and the last §14
 *     rebuild stats, written by the 3D page's `onEngineStatus` /
 *     `onRebuildStats` hooks and read by the status chip. Keeping them in a
 *     store rather than page state means the chip, the shell and the Playwright
 *     spec all read the same numbers.
 *
 * Nothing in this file writes model state. Golden rule 1 stands: the op is the
 * atom, and this store holds no ops.
 */

import { create } from 'zustand';

import { useModelStore } from './model';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Mirror of `doc.house.facade`'s identity fields. */
export interface AppliedFacade {
  readonly kitId: string | null;
  readonly seed: number;
  readonly colorwayId: string | null;
  /** Component count — the cheapest "did op 28 change anything" probe. */
  readonly componentCount: number;
}

/**
 * The boolean engine's state, as words the UI can show (§15: the chip is an
 * honest control, not decoration). Mirrors
 * `features/canvas/three/booleans.ts`'s `BooleanEngineStatus.state`.
 */
export type EngineStatusWord = 'idle' | 'loading' | 'ready' | 'unavailable';

/** The last incremental rebuild, as reported by `ThreeDScene.onRebuildStats`. */
export interface RebuildTelemetry {
  /** Wall-clock ms of the rebuild — the §14 <100 ms evidence. */
  readonly ms: number;
  /** Group keys that actually re-meshed. */
  readonly rebuiltGroups: readonly string[];
  readonly totalGroups: number;
  /** False while any wall renders without its opening holes (no WASM yet). */
  readonly holesApplied: boolean;
  /**
   * Monotonic counter of rebuilds that re-meshed at least one group. The sun
   * scrub spec asserts this does NOT move while the time slider does.
   */
  readonly rebuildCount: number;
}

export interface ThreeState {
  /** Storey shown in 3D. `null` = all storeys (the whole building). */
  visibleStoreyId: string | null;

  /** Mirror of the applied facade (see the header). Never write it directly. */
  appliedFacade: AppliedFacade;

  engineStatus: EngineStatusWord;
  /** One-line detail for `unavailable` ("WASM failed to load: …"). */
  engineDetail: string | null;
  lastRebuild: RebuildTelemetry | null;

  // ── actions ────────────────────────────────────────────────────────────
  /** Show one storey, or `null` for all. Unknown ids are accepted and pruned
   *  by the model subscription the moment the storey stops existing. */
  setVisibleStorey: (storeyId: string | null) => void;
  /** Toggle: showing `id` already → back to all storeys. */
  toggleVisibleStorey: (storeyId: string) => void;

  noteEngineStatus: (status: EngineStatusWord, detail?: string | null) => void;
  noteRebuild: (stats: {
    ms: number;
    rebuiltGroups: readonly string[];
    totalGroups: number;
    holesApplied: boolean;
  }) => void;

  /** Internal: model-subscription sync. Exported for the store's own tests. */
  _syncFromModel: (facade: AppliedFacade, storeyIds: readonly string[]) => void;
}

const EMPTY_FACADE: AppliedFacade = {
  kitId: null,
  seed: 0,
  colorwayId: null,
  componentCount: 0,
};

export const useThreeStore = create<ThreeState>()((set, get) => ({
  visibleStoreyId: null,
  appliedFacade: EMPTY_FACADE,
  engineStatus: 'idle',
  engineDetail: null,
  lastRebuild: null,

  setVisibleStorey: (storeyId) => {
    if (get().visibleStoreyId === storeyId) return;
    set({ visibleStoreyId: storeyId });
  },

  toggleVisibleStorey: (storeyId) =>
    set((s) => ({ visibleStoreyId: s.visibleStoreyId === storeyId ? null : storeyId })),

  noteEngineStatus: (status, detail = null) => {
    const s = get();
    if (s.engineStatus === status && s.engineDetail === detail) return;
    set({ engineStatus: status, engineDetail: detail });
  },

  noteRebuild: (stats) =>
    set((s) => {
      const moved = stats.rebuiltGroups.length > 0;
      return {
        lastRebuild: {
          ms: stats.ms,
          rebuiltGroups: [...stats.rebuiltGroups],
          totalGroups: stats.totalGroups,
          holesApplied: stats.holesApplied,
          rebuildCount: (s.lastRebuild?.rebuildCount ?? 0) + (moved ? 1 : 0),
        },
      };
    }),

  _syncFromModel: (facade, storeyIds) => {
    const s = get();
    const patch: Partial<
      Pick<ThreeState, 'appliedFacade' | 'visibleStoreyId'>
    > = {};
    const f = s.appliedFacade;
    if (
      f.kitId !== facade.kitId ||
      f.seed !== facade.seed ||
      f.colorwayId !== facade.colorwayId ||
      f.componentCount !== facade.componentCount
    ) {
      patch.appliedFacade = facade;
    }
    // A storey deleted (or undone away) while filtered-to must not leave the
    // 3D view pinned to nothing — that reads as "the building vanished".
    if (s.visibleStoreyId !== null && !storeyIds.includes(s.visibleStoreyId)) {
      patch.visibleStoreyId = null;
    }
    if (Object.keys(patch).length > 0) set(patch);
  },
}));

// ---------------------------------------------------------------------------
// The model → mirror subscription
// ---------------------------------------------------------------------------

/**
 * Pure extraction, exported for tests. Reads only fields the fold guarantees.
 */
export function appliedFacadeOf(house: {
  readonly facade: {
    readonly kitId: string | null;
    readonly seed: number;
    readonly colorwayId: string | null;
    readonly components: readonly unknown[];
  };
}): AppliedFacade {
  return {
    kitId: house.facade.kitId,
    seed: house.facade.seed,
    colorwayId: house.facade.colorwayId,
    componentCount: house.facade.components.length,
  };
}

/**
 * Module-scope subscription, not a hook: the mirror must stay true even when
 * no 3D component is mounted (a copilot op on the Plan tab can apply a kit in
 * Phase 6). Guarded by document identity so an unrelated store write (a toast,
 * a pending-queue tick) costs one `===`.
 */
let lastSeenHouse: unknown = null;
useModelStore.subscribe((s) => {
  const house = s.doc.house;
  if (house === lastSeenHouse) return;
  lastSeenHouse = house;
  useThreeStore.getState()._syncFromModel(
    appliedFacadeOf(house),
    house.storeys.map((storey) => storey.id),
  );
});

// ---------------------------------------------------------------------------
// Selectors
// ---------------------------------------------------------------------------

export const selectVisibleStoreyId = (s: ThreeState): string | null => s.visibleStoreyId;
export const selectAppliedFacade = (s: ThreeState): AppliedFacade => s.appliedFacade;
export const selectEngineStatus = (s: ThreeState): EngineStatusWord => s.engineStatus;
export const selectLastRebuild = (s: ThreeState): RebuildTelemetry | null => s.lastRebuild;

// One import path for "3D view state": the sun scrubber's store lives with the
// sun feature (see the header for why), re-exported here for app code.
export { useSunStore } from '../features/canvas/sun/sunStore';
export type { SunState } from '../features/canvas/sun/sunStore';
