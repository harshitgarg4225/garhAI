/**
 * useAuditTrail — `GET /audit`, cursor-paginated, with the filter vocabulary.
 *
 * Admin-only on the server; a member's 403 is kept as the error rather than
 * flattened into an empty list, because "you cannot see this" and "nothing
 * happened" must never look the same on a security screen.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { api, type ApiClient, type AuditEntry } from '../../lib/api';
import { AppError } from '../../lib/errors';

export interface AuditFilters {
  readonly action?: string | undefined;
  readonly since?: string | undefined;
}

export interface AuditTrailState {
  readonly entries: AuditEntry[];
  readonly actions: string[];
  readonly loading: boolean;
  readonly loadingMore: boolean;
  readonly error: AppError | null;
  readonly hasMore: boolean;
  readonly loadMore: () => void;
  readonly refresh: () => void;
}

export const AUDIT_PAGE_SIZE = 50;

export function useAuditTrail(
  filters: AuditFilters,
  client: ApiClient = api,
  enabled = true,
): AuditTrailState {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [actions, setActions] = useState<string[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(enabled);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<AppError | null>(null);
  const generation = useRef(0);

  const { action, since } = filters;

  const load = useCallback(
    async (nextCursor: string | null) => {
      const gen = nextCursor === null ? (generation.current += 1) : generation.current;
      if (nextCursor === null) setLoading(true);
      else setLoadingMore(true);
      try {
        const page = await client.audit.list({
          limit: AUDIT_PAGE_SIZE,
          cursor: nextCursor,
          ...(action === undefined || action === '' ? {} : { action }),
          ...(since === undefined || since === '' ? {} : { since }),
        });
        if (gen !== generation.current) return;
        setEntries((current) => (nextCursor === null ? page.items : [...current, ...page.items]));
        setCursor(page.nextCursor);
        setHasMore(page.nextCursor !== null);
        setError(null);
      } catch (err) {
        const problem = AppError.from(err);
        if (problem.isAborted) return;
        if (gen === generation.current) setError(problem);
      } finally {
        if (gen === generation.current) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [client, action, since],
  );

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    void load(null);
  }, [load, enabled]);

  // The vocabulary is read once: it is a constant of the deployment, and a
  // failure here must not stop the trail rendering (the filter just goes empty).
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    client.audit.actions().then(
      (list) => {
        if (!cancelled) setActions(list);
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [client, enabled]);

  return {
    entries,
    actions,
    loading,
    loadingMore,
    error,
    hasMore,
    loadMore: () => {
      if (cursor !== null && !loadingMore) void load(cursor);
    },
    refresh: () => void load(null),
  };
}
