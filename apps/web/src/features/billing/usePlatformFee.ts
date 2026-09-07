/**
 * usePlatformFee — the fee in force, read from `GET /admin/billing/markup`.
 *
 * Read by every signed-in user (the dashboard decides whether to show the owner's
 * entry from `canSet`), written only by an owner. `save` resolves with the server's
 * own view of the new fee, so what the page shows after a change is what the next
 * charge will be debited at — not what was typed.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { api, type ApiClient, type PlatformMarkup } from '../../lib/api';
import { AppError } from '../../lib/errors';

export interface PlatformFeeState {
  readonly markup: PlatformMarkup | null;
  readonly loading: boolean;
  readonly error: AppError | null;
  readonly refresh: () => void;
  /** PUT the new percentage. Throws the `AppError` (403 for a non-owner) unchanged. */
  readonly save: (percent: string) => Promise<PlatformMarkup>;
}

export function usePlatformFee(client: ApiClient = api): PlatformFeeState {
  const [markup, setMarkup] = useState<PlatformMarkup | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<AppError | null>(null);
  const generation = useRef(0);

  const load = useCallback(async () => {
    const gen = (generation.current += 1);
    setLoading(true);
    try {
      const next = await client.admin.markup.get();
      if (gen !== generation.current) return;
      setMarkup(next);
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
  }, [load]);

  const save = useCallback(
    async (percent: string): Promise<PlatformMarkup> => {
      const next = await client.admin.markup.set({ percent });
      generation.current += 1; // a load in flight must not overwrite the fresher answer
      setMarkup(next);
      setError(null);
      setLoading(false);
      return next;
    },
    [client],
  );

  return { markup, loading, error, refresh: () => void load(), save };
}
