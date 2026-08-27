/**
 * patchQueue.ts — one trailing-debounced, field-merged PATCH.
 *
 * The problem it solves is narrow and real: the opacity slider fires an event
 * per pointer move, and an underlay PATCH is a database write plus a fresh
 * presigned URL. Dragging the slider once must be ONE request, and it must be
 * the request that carries where the thumb ended up — not where it passed
 * through 40 ms before the finger lifted.
 *
 * So: trailing edge only (never leading — a leading send would post the FIRST
 * sample of a drag, which is the least interesting one), and merged by field so
 * that "opacity, then visible, then opacity again" inside the window becomes a
 * single body with the last value of each.
 *
 * Extracted from the store and unit-tested because a debounce is exactly the
 * kind of code that looks obviously right and drops the last update: the two
 * bugs worth guarding are (a) the timer firing after the queue was emptied by
 * an explicit `flush`, sending twice; and (b) a push arriving during the flush
 * itself being folded into the batch that just left. Both are covered in
 * `patchQueue.test.ts`.
 */

/** ~400 ms: past the gap between two pointer samples, under human "did it save?". */
export const PATCH_DEBOUNCE_MS = 400;

export interface PatchQueue<P extends object> {
  /** Merge a partial update in and (re)arm the trailing timer. */
  push: (patch: Partial<P>) => void;
  /** Send whatever is pending right now. No-op when nothing is pending. */
  flush: () => void;
  /** Drop whatever is pending. Used when the thing being patched is deleted. */
  cancel: () => void;
  /** What would go out on the next flush, or `null`. Read-only view. */
  pending: () => Partial<P> | null;
}

export interface PatchQueueOptions {
  delayMs?: number | undefined;
}

/**
 * Build a queue over `send`.
 *
 * `send` is fire-and-forget by contract — the queue does not know or care
 * whether the request succeeds, because retrying a superseded slider position
 * would be worse than dropping it. The caller owns error reporting.
 */
export function createPatchQueue<P extends object>(
  send: (merged: Partial<P>) => void,
  options: PatchQueueOptions = {},
): PatchQueue<P> {
  const delayMs = options.delayMs ?? PATCH_DEBOUNCE_MS;

  let queued: Partial<P> | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const disarm = (): void => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  const flush = (): void => {
    disarm();
    const batch = queued;
    // Cleared BEFORE `send`, so a push made from inside `send` (or from a
    // synchronous listener it triggers) starts a new batch rather than being
    // swallowed by the one already on its way.
    queued = null;
    if (batch !== null) send(batch);
  };

  return {
    push: (patch) => {
      queued = queued === null ? { ...patch } : { ...queued, ...patch };
      disarm();
      timer = setTimeout(flush, delayMs);
    },
    flush,
    cancel: () => {
      disarm();
      queued = null;
    },
    pending: () => queued,
  };
}
