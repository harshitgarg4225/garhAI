/**
 * store.ts — where measurements live while the tab is open.
 *
 * WHY A STORE. The two halves of this feature cannot see each other:
 * `MeasureLayer` renders inside react-three-fiber's own React root and
 * `MeasurePanel` renders in the DOM overlay outside it, and context does not
 * cross that boundary (`CanvasRoot` and `CanvasDomOverlay` both say so). Same
 * shape, same reason, as `underlay/store.ts` and `useToolSettings`.
 *
 * WHY NOTHING IS PERSISTED. A measurement is a question the architect asked,
 * not a fact about the building: it appends no op, it is in no version, it is
 * on no sheet, and it dies with the tab. Persisting it would mean deciding
 * whose measurement it is in a shared session and whether it belongs in the
 * submission set — both answers are "no", so the question never arises.
 *
 * The draft is written on every (already rAF-coalesced) pointer move. Only the
 * DOM panel subscribes to it through React; `MeasureLayer` reads the store
 * imperatively and mutates buffers, so a moving pointer reconciles the small
 * readout panel and nothing else — the §14 rule that no canvas geometry
 * re-renders because the mouse moved.
 */

import { create } from 'zustand';

import type { MeasureDraft, MeasureKind, Measurement } from './types';

export interface MeasureState {
  /** What the next measurement will be. Survives a commit — you measure again. */
  kind: MeasureKind;
  /** Committed measurements, oldest first. */
  measurements: readonly Measurement[];
  /** The in-progress measurement, or null. Written per pointer frame. */
  draft: MeasureDraft | null;
  /** Highlighted by a canvas pick or a panel hover. */
  selectedId: string | null;
  /** Master visibility — one switch to get the drawing back. */
  visible: boolean;
  /** The last refusal ("those corners are in a straight line"). Inline, never a throw. */
  notice: string | null;

  setKind: (kind: MeasureKind) => void;
  add: (measurement: Measurement) => void;
  dismiss: (id: string) => void;
  dismissAll: () => void;
  setDraft: (draft: MeasureDraft | null) => void;
  select: (id: string | null) => void;
  setVisible: (visible: boolean) => void;
  setNotice: (notice: string | null) => void;
}

const INITIAL = {
  kind: 'distance' as MeasureKind,
  measurements: [] as readonly Measurement[],
  draft: null as MeasureDraft | null,
  selectedId: null as string | null,
  visible: true,
  notice: null as string | null,
};

export const useMeasureStore = create<MeasureState>()((set) => ({
  ...INITIAL,

  setKind: (kind) => set({ kind, notice: null }),

  add: (measurement) =>
    set((s) => ({
      measurements: [...s.measurements, measurement],
      // A new measurement always becomes the selected one: it is the number the
      // architect just asked for, and the panel scrolls to the selection.
      selectedId: measurement.id,
      notice: null,
      draft: null,
    })),

  dismiss: (id) =>
    set((s) => ({
      measurements: s.measurements.filter((m) => m.id !== id),
      // Clearing the selection with it matters: a selected id that no longer
      // exists would keep the panel highlighting a row that is not there and
      // the layer drawing a highlight for geometry it no longer has.
      selectedId: s.selectedId === id ? null : s.selectedId,
    })),

  dismissAll: () => set({ measurements: [], selectedId: null, notice: null }),

  setDraft: (draft) => set({ draft }),

  select: (id) => set({ selectedId: id }),

  setVisible: (visible) => set({ visible }),

  setNotice: (notice) => set({ notice }),
}));

/** Measurements taken on `storeyId` (plus storey-agnostic ones). */
export function measurementsForStorey(
  measurements: readonly Measurement[],
  storeyId: string | null,
): Measurement[] {
  return measurements.filter((m) => m.storeyId === null || m.storeyId === storeyId);
}

/** Test/teardown helper. Not called by the app — the tab's lifetime is the state's. */
export function resetMeasureStore(): void {
  useMeasureStore.setState({ ...INITIAL });
}
