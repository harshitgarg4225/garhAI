/**
 * useReferenceReview — the board's verdict for one render style.
 *
 * Its own hook rather than the board store's `review`, because the two callers
 * ask at different moments and must not clobber each other: the board's panel
 * answers "check this board", the launcher answers "about this render, now".
 * Sharing one slot would mean opening the launcher wiped the panel an architect
 * was reading.
 *
 * Answers `null` while loading AND on failure. The board is additive — a review
 * that cannot be fetched must never stop a render or show a scary banner over a
 * feature the architect may not even be using.
 */

import { useEffect, useState } from 'react';

import { api, type ReferenceReview } from '../../lib/api';

export function useReferenceReview(
  projectId: string,
  preset: string | null,
): ReferenceReview | null {
  const [review, setReview] = useState<ReferenceReview | null>(null);

  useEffect(() => {
    if (preset === null || preset === '') {
      setReview(null);
      return undefined;
    }
    const controller = new AbortController();
    let live = true;
    void (async () => {
      try {
        const next = await api.references.review(projectId, preset, {
          signal: controller.signal,
        });
        if (live) setReview(next);
      } catch {
        // Including the honest 503 when the render package is not loaded on this
        // server: the launcher simply shows nothing about references.
        if (live) setReview(null);
      }
    })();
    return () => {
      live = false;
      controller.abort();
    };
  }, [projectId, preset]);

  return review;
}
