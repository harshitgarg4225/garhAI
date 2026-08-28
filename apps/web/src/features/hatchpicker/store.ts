/**
 * store.ts — the architect's hatch overrides.
 *
 * Zustand and a module store, for the reason `features/layers/store.ts` gives:
 * two consumers that cannot see each other — the picker in the DOM overlay and
 * anything reading the resolved hatch inside the react-three-fiber root, which
 * React context does not cross.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * DELIBERATELY NOT PERSISTED, AND THAT IS THE INTERESTING PART
 * ════════════════════════════════════════════════════════════════════════════
 * The layer panel writes to `localStorage` because which layers you have
 * hidden is a property of YOUR SESSION: it changes no geometry, reaches no op,
 * and two architects on one project rightly want different answers.
 *
 * A hatch override is the opposite. "This wall is drawn as stone" is a
 * property of the DRAWING — it prints on a submission set, every collaborator
 * must see the same thing, and it belongs in the op log beside the material
 * assignment that it overrides. Writing it to this browser's `localStorage`
 * would manufacture exactly the second, private source of truth this repo
 * keeps paying for: the sheet would say one thing on the architect's machine
 * and another on their colleague's, with no op to explain the difference.
 *
 * So overrides live in memory until `hatch.assign` lands in the model core
 * (see the handoff in `index.ts`). The cost is honest and small — a refresh
 * forgets a hand-picked pattern — and the alternative is a lie that survives
 * refreshes.
 *
 * `bindProject` exists for the same reason: an override set on one project
 * must not follow the user into the next one.
 */

import type { SurfaceGroupRef } from '@garh/model';
import { create } from 'zustand';

import type { HatchPatternKey } from './patterns';
import { hatchTargetKey, type HatchOverrides } from './resolve';

export interface HatchOverrideState {
  /** The project these overrides belong to, or null before a project is open. */
  projectId: string | null;
  /** `hatchTargetKey(target) -> {target, pattern}`. Replaced, never mutated. */
  overrides: HatchOverrides;

  /** Adopt a project. Switching projects clears what the last one chose. */
  bindProject: (projectId: string) => void;
  /** Pick a pattern for a surface target. */
  setOverride: (target: SurfaceGroupRef, pattern: HatchPatternKey) => void;
  /** Drop the override, so the material's implication applies again. */
  clearOverride: (target: SurfaceGroupRef) => void;
  /** Back to "every surface follows its material". */
  clearAllOverrides: () => void;
}

const NONE: HatchOverrides = new Map();

export const useHatchOverrideStore = create<HatchOverrideState>()((set, get) => ({
  projectId: null,
  overrides: NONE,

  bindProject: (projectId) => {
    if (get().projectId === projectId) return;
    set({ projectId, overrides: NONE });
  },

  setOverride: (target, pattern) => {
    const key = hatchTargetKey(target);
    const current = get().overrides;
    // A no-op set would still publish a new Map and re-render every consumer.
    if (current.get(key)?.pattern === pattern) return;
    const next = new Map(current);
    next.set(key, { target, pattern });
    set({ overrides: next });
  },

  clearOverride: (target) => {
    const key = hatchTargetKey(target);
    const current = get().overrides;
    if (!current.has(key)) return;
    const next = new Map(current);
    next.delete(key);
    set({ overrides: next });
  },

  clearAllOverrides: () => {
    if (get().overrides.size === 0) return;
    set({ overrides: NONE });
  },
}));

/** The overrides map, for components that only read. */
export function useHatchOverrides(): HatchOverrides {
  return useHatchOverrideStore((state) => state.overrides);
}
