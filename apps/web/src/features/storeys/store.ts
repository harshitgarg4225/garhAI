/**
 * store.ts — the storey-below ghost's view state, shared across the `<Canvas>`
 * boundary.
 *
 * WHY A STORE AND NOT PROPS. Exactly the reason `features/underlay/store.ts`
 * gives, and this feature copies its shape deliberately: `StoreyGhostLayer`
 * lives inside react-three-fiber's separate React root, `StoreyPanel` lives in
 * the DOM overlay outside it, and React context does not cross that boundary.
 * One module store both sides subscribe to is the only shared channel.
 *
 * WHAT IS NOT HERE, AND WHY. No storey list, no active storey, no document.
 *   · The storeys ARE the document (`doc.house.storeys`) — mirroring them here
 *     would be a second source of truth for something the fold owns.
 *   · The active storey is `ui.activeStoreyId`, which pre-dates this feature
 *     and is what `PlanPage`, the tools, the picker and the 3D view all read.
 *     A second "current storey" is exactly how a canvas ends up drawing one
 *     floor while the tools edit another.
 *
 * THERE IS NO `reset`, ON PURPOSE. The underlay store has one because it holds
 * a project's record and a half-finished calibration; this holds two display
 * preferences that should survive a storey switch, a tab change and the next
 * project the architect opens in the same session. Resetting them would be a
 * setting that quietly undoes itself.
 *
 * NOTHING HERE IS PERSISTED, and that is right. The image underlay has a server
 * record because a scan is uploaded content; the storey ghost is derived from
 * the document you already have, so whether it is showing is a per-tab display
 * choice, not project state. It appears in no op, changes no document, and no
 * undo entry may mention it.
 */

import { create } from 'zustand';

/** Ghost opacity bounds. Below 5% it is invisible; above 45% it competes with
 *  the storey being drawn, which is the failure mode that makes an underlay
 *  worse than nothing. */
export const MIN_GHOST_OPACITY = 0.05;
export const MAX_GHOST_OPACITY = 0.45;

/**
 * The default. Faint enough to read as "not this floor" at a glance, strong
 * enough to snap a wall to at 1:100.
 */
export const DEFAULT_GHOST_OPACITY = 0.22;

export interface StoreysState {
  /** Draw the storey below the active one as a faded underlay. */
  ghostVisible: boolean;
  /** 0–1. Clamped on write, so no caller can push it out of range. */
  ghostOpacity: number;

  setGhostVisible: (visible: boolean) => void;
  setGhostOpacity: (opacity: number) => void;
}

const INITIAL = {
  ghostVisible: true,
  ghostOpacity: DEFAULT_GHOST_OPACITY,
} satisfies Partial<StoreysState>;

function clampOpacity(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_GHOST_OPACITY;
  return Math.min(MAX_GHOST_OPACITY, Math.max(MIN_GHOST_OPACITY, value));
}

export const useStoreysStore = create<StoreysState>()((set) => ({
  ...INITIAL,

  setGhostVisible: (visible) => set({ ghostVisible: visible }),
  setGhostOpacity: (opacity) => set({ ghostOpacity: clampOpacity(opacity) }),
}));
