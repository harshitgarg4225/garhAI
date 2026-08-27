/**
 * store.ts — the underlay's state, shared across the `<Canvas>` boundary.
 *
 * WHY A STORE AND NOT PROPS. The underlay has two consumers that cannot see
 * each other: `UnderlayLayer` lives inside react-three-fiber's separate React
 * root, and `UnderlayPanel` lives in the DOM overlay outside it. React context
 * does not cross that boundary (`CanvasRoot` says so in its own comments), so
 * the choice is threading every value through `PlanPage` twice, or one module
 * store both sides subscribe to. Zustand, same as `useToolSettings` and
 * `useSunStore` — and for the same reason those exist: this is view state, it
 * appears in no op, it changes no document, and no undo entry may mention it.
 *
 * WHAT IS AND IS NOT PERSISTED. The record (`mmPerPx`, origin, opacity, locked,
 * visible) lives on the server and every change is PATCHed. The interaction
 * state — which mode is armed, where the calibration marks are, whether the
 * texture failed — is local and dies with the tab, which is right: an
 * abandoned half-finished calibration must not greet the next person who
 * opens the project.
 *
 * OPTIMISM, AND THE ONE PLACE IT IS GIVEN UP. Every patch updates the local
 * record immediately and goes out on the debounced queue, so the slider tracks
 * the finger. When a response comes back we adopt the server's record ONLY if
 * nothing is queued behind it — otherwise the answer to request N would stomp
 * the local value the user has already dragged past, and the slider would jump
 * backwards under their hand.
 */

import { create } from 'zustand';

import { api, type Underlay, type UnderlayPatch } from '../../lib/api';
import { AppError } from '../../lib/errors';
import { createPatchQueue, type PatchQueue } from './patchQueue';
import type { MarkMm, UnderlayCalibration } from './calibration';

/**
 * What the pointer is doing.
 *
 * `off` — the canvas belongs to the tools, as normal.
 * `calibrate` — the next two clicks are the scale marks.
 * `move` — a drag repositions the underlay.
 *
 * The two armed modes are MODAL on purpose. The underlay is deliberately not
 * registered with `PickRegistry` (a tracing aid that steals a click from the
 * wall being drawn over it is worse than useless), so there is no way to
 * "grab" it by hitting it — which means the only honest way to drag it is to
 * say so first and take the pointer explicitly.
 */
export type UnderlayMode = 'off' | 'calibrate' | 'move';

export interface UnderlayState {
  /** The project the record belongs to; guards responses from a stale tab. */
  projectId: string | null;
  record: Underlay | null;
  loading: boolean;
  /** Upload or delete in flight — the panel disables its buttons on it. */
  busy: boolean;
  /** Quiet inline copy for a failed load/patch/upload. Never a thrown error. */
  error: string | null;
  /** Set when the texture could not be fetched even after a URL refresh. */
  imageError: string | null;
  /**
   * Bumped whenever a fresh presigned URL is fetched. The layer keys its
   * texture effect on it so a re-signed URL that happens to be byte-identical
   * still triggers a reload.
   */
  imageNonce: number;
  mode: UnderlayMode;
  /** Calibration marks, in float model mm. Two of them completes the gesture. */
  marks: readonly MarkMm[];

  load: (projectId: string) => Promise<void>;
  upload: (file: Blob) => Promise<void>;
  /** Local-first, network-debounced. Positions must already be integer mm. */
  patch: (patch: UnderlayPatch) => void;
  /** Send anything queued now (before a mode change or on unmount). */
  flush: () => void;
  remove: () => Promise<void>;
  /** Re-GET the record for a fresh presigned URL. Answers false if it failed. */
  refreshImageUrl: () => Promise<boolean>;
  setImageError: (message: string | null) => void;
  setMode: (mode: UnderlayMode) => void;
  addMark: (mark: MarkMm) => void;
  clearMarks: () => void;
  /** Apply a finished calibration: scale plus the origin that keeps mark A put. */
  applyCalibration: (next: UnderlayCalibration) => void;
  dismissError: () => void;
  /** Forget everything (project closed, or the plan tab unmounted). */
  reset: () => void;
}

/** Human copy for any AppError, in the "what happened + what next" shape. */
function errorText(err: unknown): string {
  const error = AppError.from(err);
  return error.action === null ? error.message : `${error.message} ${error.action}`;
}

const INITIAL = {
  projectId: null,
  record: null,
  loading: false,
  busy: false,
  error: null,
  imageError: null,
  imageNonce: 0,
  mode: 'off',
  marks: [],
} satisfies Partial<UnderlayState>;

export const useUnderlayStore = create<UnderlayState>()((set, get) => {
  /**
   * The one debounced writer. Built here rather than per-component so a panel
   * that unmounts mid-drag (storey switch, tab change) still has its last
   * value flushed by whoever calls `flush`.
   */
  const queue: PatchQueue<UnderlayPatch> = createPatchQueue<UnderlayPatch>((merged) => {
    const { projectId } = get();
    if (projectId === null) return;
    void (async () => {
      try {
        const fresh = await api.underlay.patch(projectId, merged);
        // Only adopt the server's copy when nothing is queued behind this
        // response — see the header note on giving up optimism.
        const state = get();
        if (state.projectId !== projectId) return;
        if (queue.pending() !== null) return;
        set({ record: fresh, imageNonce: state.imageNonce + 1, error: null });
      } catch (err) {
        if (AppError.from(err).isAborted) return;
        set({ error: errorText(err) });
      }
    })();
  });

  return {
    ...INITIAL,

    load: async (projectId) => {
      set({ projectId, loading: true, error: null });
      try {
        const record = await api.underlay.get(projectId);
        // A tab that switched project while the GET was in flight must not
        // adopt the previous project's underlay.
        if (get().projectId !== projectId) return;
        set((s) => ({
          record,
          loading: false,
          imageError: null,
          imageNonce: s.imageNonce + 1,
        }));
      } catch (err) {
        if (get().projectId !== projectId) return;
        if (AppError.from(err).isAborted) return;
        set({ loading: false, error: errorText(err) });
      }
    },

    upload: async (file) => {
      const projectId = get().projectId;
      if (projectId === null) return;
      // Let a pending opacity/visibility change land first: the upload keeps
      // the row (and its calibration), so the two are not in conflict.
      queue.flush();
      set({ busy: true, error: null, imageError: null });
      try {
        const record = await api.underlay.upload({ projectId, file });
        if (get().projectId !== projectId) return;
        set((s) => ({ record, busy: false, imageNonce: s.imageNonce + 1, mode: 'off', marks: [] }));
      } catch (err) {
        if (get().projectId !== projectId) return;
        set({ busy: false, error: errorText(err) });
      }
    },

    patch: (patch) => {
      const record = get().record;
      if (record === null) return;
      set({ record: { ...record, ...patch }, error: null });
      queue.push(patch);
    },

    flush: () => {
      queue.flush();
    },

    remove: async () => {
      const projectId = get().projectId;
      if (projectId === null) return;
      // Not `flush`: the row is going away, and a PATCH racing a DELETE would
      // come back 404 `no_underlay` and surface as an error for something the
      // user asked for and got.
      queue.cancel();
      set({ busy: true, error: null });
      try {
        await api.underlay.remove(projectId);
        if (get().projectId !== projectId) return;
        set({ record: null, busy: false, mode: 'off', marks: [], imageError: null });
      } catch (err) {
        if (get().projectId !== projectId) return;
        set({ busy: false, error: errorText(err) });
      }
    },

    refreshImageUrl: async () => {
      const projectId = get().projectId;
      if (projectId === null) return false;
      try {
        const record = await api.underlay.get(projectId);
        if (get().projectId !== projectId) return false;
        set((s) => ({ record, imageNonce: s.imageNonce + 1 }));
        return record !== null;
      } catch {
        return false;
      }
    },

    setImageError: (message) => set({ imageError: message }),

    setMode: (mode) => {
      // Arming a mode flushes first so the scale the gesture is about to read
      // is the scale the server has.
      queue.flush();
      set({ mode, marks: [] });
    },

    addMark: (mark) => set((s) => ({ marks: [...s.marks, mark] })),

    clearMarks: () => set({ marks: [] }),

    applyCalibration: (next) => {
      const record = get().record;
      if (record === null) return;
      set({
        record: { ...record, ...next },
        mode: 'off',
        marks: [],
        error: null,
      });
      queue.push({
        mmPerPx: next.mmPerPx,
        originXMm: next.originXMm,
        originYMm: next.originYMm,
      });
      queue.flush();
    },

    dismissError: () => set({ error: null }),

    reset: () => {
      queue.cancel();
      set({ ...INITIAL });
    },
  };
});
