/**
 * useUsage — the firm's trial allowance, read from `GET /billing/usage`.
 *
 * One fetch per mount plus a `refresh()` the caller wires to whatever changes the
 * numbers (a job reaching a terminal state). The API aggregates the same
 * `credit_events` rows the quota gate and the spend cap read, so this is the
 * number that will refuse the next Generate — not a decorative estimate.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { api, type Usage } from '../../lib/api';
import { AppError } from '../../lib/errors';

export interface UsageState {
  readonly usage: Usage | null;
  readonly loading: boolean;
  readonly error: string | null;
  readonly refresh: () => void;
}

export function useUsage(refreshKey: unknown = null): UsageState {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);

  const load = useCallback(async () => {
    const gen = (generation.current += 1);
    setLoading(true);
    try {
      const next = await api.billing.usage();
      if (gen !== generation.current) return; // superseded by a newer load
      setUsage(next);
      setError(null);
    } catch (err) {
      const problem = AppError.from(err);
      if (problem.isAborted) return;
      setError(problem.message);
    } finally {
      if (gen === generation.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  return { usage, loading, error, refresh: () => void load() };
}
