/**
 * store.ts — which two versions are being compared, and what came back (C-8).
 *
 * Kept out of the model store deliberately: a compare is a *view* of two saved states,
 * not an edit to the live one. Putting it in `model` would give it an undo entry, and
 * "undo the comparison" is not a thing an architect means.
 *
 * `a` and `b` are both explicit and neither defaults to "latest". A compare whose
 * meaning changes when a collaborator saves a version is a compare nobody can cite.
 */

import { create } from 'zustand';

import type { VersionCompareResponse } from '../../lib/schemas';

export interface CompareState {
  /** The version compared FROM. Null until the architect picks one. */
  a: string | null;
  /** The version compared TO. */
  b: string | null;
  result: VersionCompareResponse | null;
  loading: boolean;
  /** Why the last attempt produced nothing. A sentence, never a code. */
  error: string | null;
  /** Whether the change boxes are drawn on the canvas. */
  overlayVisible: boolean;

  setA: (id: string | null) => void;
  setB: (id: string | null) => void;
  setResult: (result: VersionCompareResponse | null) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  toggleOverlay: () => void;
  reset: () => void;
}

const EMPTY = {
  a: null,
  b: null,
  result: null,
  loading: false,
  error: null,
  overlayVisible: true,
} as const;

export const useCompareStore = create<CompareState>((set) => ({
  ...EMPTY,

  // Changing either side drops the previous result rather than leaving it on screen.
  // A change list captioned with the versions it no longer describes is worse than a
  // blank one, and this is the exact shape of stale-view bug that gets believed.
  setA: (a) => set({ a, result: null, error: null }),
  setB: (b) => set({ b, result: null, error: null }),
  setResult: (result) => set({ result, loading: false }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error, loading: false }),
  toggleOverlay: () => set((s) => ({ overlayVisible: !s.overlayVisible })),
  reset: () => set({ ...EMPTY }),
}));

/** Boxes to draw for one storey. Empty when the overlay is off or nothing loaded. */
export function compareBoxesForStorey(
  state: CompareState,
  storeyId: string | null,
): readonly (readonly number[])[] {
  if (!state.overlayVisible || state.result === null || storeyId === null) return [];
  return state.result.changes
    .filter((change) => change.storeyId === storeyId && change.box.length === 4)
    .map((change) => change.box);
}
