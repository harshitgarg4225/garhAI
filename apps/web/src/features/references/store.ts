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

/*
 * IN-FLIGHT ANSWERS, AND WHY THE REVIEW HAS TO WAIT FOR THEM.
 *
 * Every answer on a card commits on BLUR. Clicking "Check before rendering"
 * blurs the box the architect was typing in, so the PATCH and the review POST
 * leave the browser in that order — and then race on the server. Lose the race
 * and the review reads the row as it was a moment ago and asks "What should
 * <picture> contribute? If you do nothing: it is skipped", about the sentence
 * the architect just finished writing.
 *
 * That is the worst kind of wrong answer this feature can give: it is about
 * whether the product heard you, it appears exactly when you are being careful,
 * and pressing the button again makes it go away — which teaches people the
 * check is noise.
 *
 * Module scope rather than store state on purpose: a pending write is not
 * something any component renders, and putting it in the store would publish a
 * re-render on every keystroke-commit for no one's benefit.
 */
const inFlightWrites = new Set<Promise<void>>();

/** Resolve once every answer written so far has landed (or failed). */
async function settleWrites(): Promise<void> {
  // Snapshot: a write that starts AFTER the review was asked for is not one the
  // architect was waiting on, and awaiting the live set could never terminate.
  await Promise.allSettled([...inFlightWrites]);
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
    const write = (async () => {
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
    })();
    inFlightWrites.add(write);
    try {
      await write;
    } finally {
      inFlightWrites.delete(write);
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
      await settleWrites();
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
