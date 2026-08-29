/**
 * api.ts — loading a version compare (C-8).
 *
 * Thin on purpose: the endpoint call and its zod shape live in `lib/api.ts` and
 * `lib/schemas.ts`, the catalogued §11 surface. What is here is the store choreography,
 * so a component never has to remember to clear `error` before setting `loading`.
 */

import { api } from '../../lib/api';
import { AppError } from '../../lib/errors';
import { useCompareStore } from './store';

/**
 * Fetch the compare for the two versions currently chosen.
 *
 * Does nothing when either side is unchosen — the caller does not have to guard, and a
 * request with a missing side would be a 422 the architect never asked for.
 */
export async function loadCompare(projectId: string, signal?: AbortSignal): Promise<void> {
  const { a, b } = useCompareStore.getState();
  if (a === null || b === null) return;

  const store = useCompareStore.getState();
  store.setError(null);
  store.setLoading(true);
  try {
    const result = await api.versions.compare(projectId, a, b, signal ? { signal } : {});
    if (signal?.aborted) return;
    useCompareStore.getState().setResult(result);
  } catch (cause: unknown) {
    if (signal?.aborted) return;
    useCompareStore
      .getState()
      .setError(
        cause instanceof AppError
          ? `${cause.message} ${cause.action}`
          : 'Could not compare those versions.',
      );
  }
}
