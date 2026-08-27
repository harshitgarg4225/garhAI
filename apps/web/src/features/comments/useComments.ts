/**
 * useComments — the project comment thread's state, owned by the shell.
 *
 * One instance lives in `ProjectShell` (not in the panel) because two surfaces
 * read the same facts: the top-bar badge needs the unresolved count before the
 * panel has ever opened, and the panel needs the list. Two hook instances
 * would double-fetch and disagree.
 *
 * Server truths this hook is shaped around (routers/share.py):
 *   - `GET /projects/:id/comments` answers only UNRESOLVED comments, oldest
 *     first. The hook reverses to newest-first for display.
 *   - Resolving succeeds server-side and the comment simply stops being
 *     listed. The hook marks the row resolved IN PLACE rather than refetching,
 *     so the person sees "Resolved" where they clicked instead of the row
 *     vanishing under their pointer; the next open refetches and drops it.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { useToast } from '@garh/ui';

import { api } from '../../lib/api';
import { AppError } from '../../lib/errors';
import type { Comment } from '../../lib/schemas';
import { useSessionStore } from '../../stores/session';

export interface CommentsState {
  /** Newest first. Resolved rows linger (marked) until the next refresh. */
  readonly comments: readonly Comment[];
  readonly loading: boolean;
  /** The last list failure; the panel shows it with a retry. */
  readonly error: AppError | null;
  readonly unresolvedCount: number;
  /** Composer submit in flight. */
  readonly busy: boolean;
  /** The comment currently being resolved, or null. */
  readonly resolvingId: string | null;
  readonly refresh: () => Promise<void>;
  /** Resolves true on success so the composer knows to clear. */
  readonly add: (body: string) => Promise<boolean>;
  readonly resolve: (commentId: string) => Promise<boolean>;
}

export function useComments(projectId: string): CommentsState {
  const { toast } = useToast();
  const userName = useSessionStore((s) => s.user?.name ?? '');

  const [comments, setComments] = useState<readonly Comment[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<AppError | null>(null);
  const [busy, setBusy] = useState(false);
  const [resolvingId, setResolvingId] = useState<string | null>(null);

  // Stale-response guard: a slow list for project A must not land on project
  // B's panel. Bumped on every project switch and read after every await.
  const runRef = useRef(0);

  const refresh = useCallback(async (): Promise<void> => {
    if (projectId === '') return;
    const run = runRef.current;
    setLoading(true);
    try {
      const list = await api.comments.list(projectId);
      if (runRef.current !== run) return;
      setError(null);
      // Server order is oldest-first; the panel reads newest-first.
      setComments([...list].reverse());
    } catch (err) {
      const appError = AppError.from(err);
      if (runRef.current !== run || appError.isAborted) return;
      // Keep whatever was on screen — a stale thread beats a vanished one.
      setError(appError);
    } finally {
      if (runRef.current === run) setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    runRef.current += 1;
    setComments([]);
    setError(null);
    setLoading(false);
    setBusy(false);
    setResolvingId(null);
    void refresh();
  }, [projectId, refresh]);

  const add = useCallback(
    async (body: string): Promise<boolean> => {
      const text = body.trim();
      if (text === '' || projectId === '') return false;
      const run = runRef.current;
      setBusy(true);
      try {
        const created = await api.comments.create(projectId, {
          body: text,
          ...(userName === '' ? {} : { authorName: userName }),
        });
        if (runRef.current !== run) return true;
        setComments((current) => [created, ...current]);
        return true;
      } catch (err) {
        const appError = AppError.from(err);
        if (appError.isAborted) return false;
        toast({
          severity: 'fail',
          title: "Couldn't post that comment",
          description: appError.message,
          action: { label: 'Try again', onClick: () => void add(text) },
        });
        return false;
      } finally {
        if (runRef.current === run) setBusy(false);
      }
    },
    [projectId, userName, toast],
  );

  const resolve = useCallback(
    async (commentId: string): Promise<boolean> => {
      if (projectId === '') return false;
      const run = runRef.current;
      setResolvingId(commentId);
      try {
        const updated = await api.comments.setResolved(projectId, commentId, true);
        if (runRef.current !== run) return true;
        setComments((current) =>
          current.map((c) => (c.id === commentId ? { ...c, resolved: updated.resolved } : c)),
        );
        return true;
      } catch (err) {
        const appError = AppError.from(err);
        if (appError.isAborted) return false;
        toast({
          severity: 'fail',
          title: "Couldn't resolve that comment",
          description: appError.message,
          action: { label: 'Try again', onClick: () => void resolve(commentId) },
        });
        return false;
      } finally {
        if (runRef.current === run) setResolvingId(null);
      }
    },
    [projectId, toast],
  );

  return {
    comments,
    loading,
    error,
    unresolvedCount: comments.filter((c) => !c.resolved).length,
    busy,
    resolvingId,
    refresh,
    add,
    resolve,
  };
}

export default useComments;
