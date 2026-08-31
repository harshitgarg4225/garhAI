/**
 * store.ts — the inspiration board's client state.
 *
 * Keyed by project, because the board IS per-project: an architect with two
 * houses open must never see one client's references while annotating the other.
 * That is the whole feature's premise, so it is the store's shape rather than a
 * convention to remember.
 *
 * Annotations are written straight through to the server and the answer replaces
 * the row. No optimistic local edit: the four answers steer a render, and a UI
 * that showed an annotation the server never accepted would send an architect
 * into a client meeting with a render that followed something else.
 */

import { create } from 'zustand';

import {
  api,
  type ProjectReference,
  type ReferencePatch,
  type ReferenceReview,
} from '../../lib/api';

interface BoardState {
  /** projectId → the board, in the architect's own order. */
  readonly byProject: Readonly<Record<string, readonly ProjectReference[]>>;
  readonly loading: boolean;
  readonly error: string | null;
  /** The last review fetched, and the preset it was for. */
  readonly review: ReferenceReview | null;
  readonly reviewing: boolean;

  load: (projectId: string) => Promise<void>;
  add: (projectId: string, file: Blob) => Promise<ProjectReference | null>;
  annotate: (projectId: string, id: string, patch: ReferencePatch) => Promise<void>;
  remove: (projectId: string, id: string) => Promise<void>;
  review_: (projectId: string, preset: string) => Promise<void>;
  clearReview: () => void;
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : 'Something went wrong.';
}

export const useReferenceStore = create<BoardState>((set) => ({
  byProject: {},
  loading: false,
  error: null,
  review: null,
  reviewing: false,

  load: async (projectId) => {
    set({ loading: true, error: null });
    try {
      const references = await api.references.list(projectId);
      set((s) => ({
        byProject: { ...s.byProject, [projectId]: references },
        loading: false,
      }));
    } catch (err) {
      set({ loading: false, error: message(err) });
    }
  },

  add: async (projectId, file) => {
    set({ error: null });
    try {
      const added = await api.references.add({ projectId, file });
      set((s) => ({
        byProject: {
          ...s.byProject,
          [projectId]: [...(s.byProject[projectId] ?? []), added],
        },
        // The board changed, so any review on screen is about a different board.
        review: null,
      }));
      return added;
    } catch (err) {
      set({ error: message(err) });
      return null;
    }
  },

  annotate: async (projectId, id, patch) => {
    set({ error: null });
    try {
      const updated = await api.references.annotate(projectId, id, patch);
      set((s) => ({
        byProject: {
          ...s.byProject,
          [projectId]: (s.byProject[projectId] ?? []).map((r) => (r.id === id ? updated : r)),
        },
        review: null,
      }));
    } catch (err) {
      set({ error: message(err) });
    }
  },

  remove: async (projectId, id) => {
    set({ error: null });
    try {
      await api.references.remove(projectId, id);
      set((s) => ({
        byProject: {
          ...s.byProject,
          [projectId]: (s.byProject[projectId] ?? []).filter((r) => r.id !== id),
        },
        review: null,
      }));
    } catch (err) {
      set({ error: message(err) });
    }
  },

  review_: async (projectId, preset) => {
    set({ reviewing: true, error: null });
    try {
      set({ review: await api.references.review(projectId, preset), reviewing: false });
    } catch (err) {
      set({ reviewing: false, error: message(err) });
    }
  },

  clearReview: () => set({ review: null }),
}));

/** The board for one project, or an empty list. Stable identity per project. */
const EMPTY: readonly ProjectReference[] = [];
export function selectBoard(projectId: string) {
  return (s: BoardState): readonly ProjectReference[] => s.byProject[projectId] ?? EMPTY;
}

/**
 * How many references on this board have no answer yet.
 *
 * Surfaced as a count rather than left to the review call, because it is the
 * number that tells an architect the board is not finished — and it must be
 * visible without picking a preset first.
 */
export function selectUnannotatedCount(projectId: string) {
  return (s: BoardState): number =>
    (s.byProject[projectId] ?? EMPTY).filter((r) => r.why === '' && r.ignore === '').length;
}
