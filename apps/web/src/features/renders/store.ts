/**
 * store.ts — the renders feature's one piece of cross-tab state.
 *
 * A render is CAPTURED on the 3D view (the only place the live scene exists)
 * but often REQUESTED from the Renders tab (history's "Re-render", the pack
 * button on an empty gallery). The bridge between those two places is a
 * pending request: the Renders tab writes it and navigates to `/3d`; the
 * launcher (mounted on the 3D view) sees it, captures, submits, clears it.
 *
 * Deliberately NOT stored: capture bytes. Caching eight multi-megabyte PNG
 * strings to make re-render work from a tab without a scene would trade
 * correctness (a stale capture of an edited model — the §9 lie) for
 * convenience. The scene is one keystroke away; honesty wins.
 */

import { create } from 'zustand';

import type { RenderMode } from './presets';

export type PendingRenderRequest =
  | {
      readonly kind: 'single';
      readonly preset: string;
      readonly mode: RenderMode;
      readonly seed: number;
    }
  | {
      readonly kind: 'pack';
      readonly seed: number;
    };

export interface RendersUiState {
  /** Set by the Renders tab; consumed (once) by the launcher on the 3D view. */
  pending: PendingRenderRequest | null;
  /** Pack ids started this session, newest first — drives the pack queue UI. */
  packIds: readonly string[];

  requestRender: (request: PendingRenderRequest) => void;
  takePending: () => PendingRenderRequest | null;
  notePack: (packId: string) => void;
}

export const useRendersUiStore = create<RendersUiState>()((set, get) => ({
  pending: null,
  packIds: [],

  requestRender: (request) => set({ pending: request }),

  takePending: () => {
    const pending = get().pending;
    if (pending !== null) set({ pending: null });
    return pending;
  },

  notePack: (packId) =>
    set((s) => ({
      packIds: [packId, ...s.packIds.filter((id) => id !== packId)].slice(0, 8),
    })),
}));
