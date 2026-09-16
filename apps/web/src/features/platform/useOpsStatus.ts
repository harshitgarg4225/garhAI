/**
 * useOpsStatus — `GET /admin/ops`, re-read on an interval while the page is open.
 *
 * The interval is the point: an operator leaves this tab open during a deploy and
 * watches the queue drain and the new replicas' heartbeats appear. A page that
 * needed a manual refresh would be read once and trusted for an hour.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { api, type ApiClient, type PlatformStatus } from '../../lib/api';
import { AppError } from '../../lib/errors';

export const OPS_REFRESH_MS = 30_000;

export interface OpsStatusState {
  readonly status: PlatformStatus | null;
  readonly loading: boolean;
  readonly error: AppError | null;
  readonly refresh: () => void;
  /** When the document on screen was fetched, for the "as of" line. */
  readonly fetchedAt: Date | null;
}

export function useOpsStatus(
  client: ApiClient = api,
  refreshMs: number = OPS_REFRESH_MS,
): OpsStatusState {
  const [status, setStatus] = useState<PlatformStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<AppError | null>(null);
  const [fetchedAt, setFetchedAt] = useState<Date | null>(null);
  const generation = useRef(0);

  const load = useCallback(async () => {
    const gen = (generation.current += 1);
    setLoading(true);
    try {
      const next = await client.admin.ops.get();
      if (gen !== generation.current) return;
      setStatus(next);
      setFetchedAt(new Date());
      setError(null);
    } catch (err) {
      const problem = AppError.from(err);
      if (problem.isAborted) return;
      if (gen === generation.current) setError(problem);
    } finally {
      if (gen === generation.current) setLoading(false);
    }
  }, [client]);

  useEffect(() => {
    void load();
    if (refreshMs <= 0) return undefined;
    const timer = setInterval(() => void load(), refreshMs);
    return () => clearInterval(timer);
  }, [load, refreshMs]);

  return { status, loading, error, refresh: () => void load(), fetchedAt };
}
