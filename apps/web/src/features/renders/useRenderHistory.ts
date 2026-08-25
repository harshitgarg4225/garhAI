/**
 * useRenderHistory.ts — the §9 gallery's data: newest first, pinned to
 * `designVersionId`, `stale` straight from the server (the ops pipeline flips
 * it on every visual edit — this hook never guesses staleness client-side).
 *
 * Refresh triggers: mount, explicit `refresh()`, any tracked render job going
 * terminal (the jobs store already refetches the row; we re-list so the new
 * image appears), and the model's `headIdx` moving (debounced — that is the
 * moment the server marked rows stale, and the banner should follow promptly).
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { AppError } from '../../lib/errors';
import { useJobsStore } from '../../stores/jobs';
import { useModelStore } from '../../stores/model';
import { listRenderHistory, type RenderJob } from './api';

const HEAD_DEBOUNCE_MS = 1_200;
const PAGE_SIZE = 30;

export interface RenderHistory {
  readonly items: readonly RenderJob[];
  readonly loading: boolean;
  readonly error: string | null;
  readonly hasMore: boolean;
  readonly refresh: () => void;
  readonly loadMore: () => void;
}

export function useRenderHistory(projectId: string): RenderHistory {
  const [items, setItems] = useState<readonly RenderJob[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);

  const load = useCallback(
    async (append: boolean, after: string | null) => {
      const gen = (generation.current += append ? 0 : 1);
      setLoading(true);
      try {
        const page = await listRenderHistory(projectId, {
          cursor: after,
          limit: PAGE_SIZE,
        });
        if (!append && gen !== generation.current) return; // superseded
        setItems((prev) => (append ? [...prev, ...page.items] : page.items));
        setCursor(page.nextCursor);
        setHasMore(page.hasMore);
        setError(null);
      } catch (err) {
        const problem = AppError.from(err);
        if (problem.isAborted) return;
        setError(problem.message);
      } finally {
        setLoading(false);
      }
    },
    [projectId],
  );

  const refresh = useCallback(() => {
    void load(false, null);
  }, [load]);

  const loadMore = useCallback(() => {
    if (cursor !== null) void load(true, cursor);
  }, [load, cursor]);

  // Mount + project switch.
  useEffect(() => {
    refresh();
  }, [refresh]);

  // A render finished: the gallery should show it without a manual refresh.
  useEffect(
    () =>
      useJobsStore.subscribe((state, previous) => {
        const now = state.byProject[projectId] ?? [];
        const before = previous.byProject[projectId] ?? [];
        const finished = now.some(
          (job) =>
            job.kind === 'render' &&
            job.status === 'succeeded' &&
            before.find((b) => b.id === job.id)?.status !== 'succeeded',
        );
        if (finished) refresh();
      }),
    [projectId, refresh],
  );

  // The model moved: the server just marked rows stale; show the banner soon.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    let last = useModelStore.getState().headIdx;
    const unsubscribe = useModelStore.subscribe((state) => {
      if (state.headIdx === last) return;
      last = state.headIdx;
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(refresh, HEAD_DEBOUNCE_MS);
    });
    return () => {
      unsubscribe();
      if (timer !== null) clearTimeout(timer);
    };
  }, [refresh]);

  return { items, loading, error, hasMore, refresh, loadMore };
}
