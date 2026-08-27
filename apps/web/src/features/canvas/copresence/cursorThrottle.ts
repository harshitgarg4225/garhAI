/**
 * cursorThrottle.ts — the rate limiter in front of `POST /collab/cursor`.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHY A THROTTLE AND NOT A DEBOUNCE
 * ────────────────────────────────────────────────────────────────────────────
 * A debounce sends nothing while you are moving and one frame when you stop —
 * which is the exact opposite of what a live cursor is for. A throttle sends a
 * steady ~10Hz while you move, and the TRAILING edge is what guarantees the
 * final resting position also gets published: without it, the last few hundred
 * milliseconds of a movement are dropped and everyone else sees your pointer
 * frozen slightly short of where you actually parked it, next to the wall you
 * are talking about rather than on it.
 *
 * So this is leading + trailing, with the interval as the floor between sends:
 *
 *     move ×40 over 400ms at 100ms   →   t=0 (leading), 100, 200, 300, 400
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHY IT IS A FACTORY WITH AN INJECTED CLOCK
 * ────────────────────────────────────────────────────────────────────────────
 * The failure this file guards against is not "too slow" — it is "silently
 * ungated". A throttle whose window arithmetic is wrong still *works*: cursors
 * still move, and the only symptom is 500 requests a second per user, which
 * nobody notices in a two-person demo and which falls over in a six-person
 * session. That is the same shape as every bug in CLAUDE.md's list: a gate that
 * appears to fire and does not. `now` and `schedule` are parameters so a test
 * can assert the exact send count against a fake clock, and so that assertion
 * can be made to fail by breaking the gate.
 *
 * Nothing here knows what it is sending. It carries an opaque `T`, which is why
 * it can be tested with numbers.
 */

/** A pending-send timer handle. `setTimeout`'s return type differs per host. */
export type ThrottleTimer = ReturnType<typeof setTimeout>;

export interface TrailingThrottleOptions<T> {
  /** Minimum milliseconds between two sends. ~100 for a 10Hz cursor. */
  readonly intervalMs: number;
  /** Where a value actually goes. Must not throw; it is called from a timer. */
  readonly send: (value: T) => void;
  /** Clock. Injected so a test owns time; defaults to the wall clock. */
  readonly now?: () => number;
  /** Timer factory, injected for the same reason. */
  readonly schedule?: (fn: () => void, ms: number) => ThrottleTimer;
  readonly unschedule?: (timer: ThrottleTimer) => void;
}

export interface TrailingThrottle<T> {
  /**
   * Offer a value. Sends it immediately if the interval has elapsed, otherwise
   * remembers it as the trailing value — replacing any earlier one, because a
   * stale pointer position has no value at all once a newer one exists.
   */
  readonly push: (value: T) => void;
  /**
   * Forget the pending trailing value and cancel its timer.
   *
   * Called on pointer-leave, on tab-hide and on unmount. This is the half that
   * keeps the "skip when the pointer leaves the canvas" rule honest: without
   * it, moving the mouse off the canvas would still publish one last frame up
   * to `intervalMs` later, placing a colleague's cursor at the edge of your
   * drawing right after you stopped pointing at anything.
   */
  readonly cancel: () => void;
  /** True while a trailing send is armed. Test and diagnostic surface. */
  readonly pending: () => boolean;
}

export function createTrailingThrottle<T>(
  options: TrailingThrottleOptions<T>,
): TrailingThrottle<T> {
  const now = options.now ?? Date.now;
  const schedule = options.schedule ?? ((fn, ms) => setTimeout(fn, ms));
  const unschedule = options.unschedule ?? ((timer) => clearTimeout(timer));
  const intervalMs = Math.max(0, options.intervalMs);

  /**
   * When the last send happened. `-Infinity` rather than 0 so the very first
   * push is always a leading-edge send regardless of what the clock reads —
   * with a fake timer starting at 0 and a real one at 1.7e12, `0` would give
   * two different first-push behaviours and only one of them would be tested.
   */
  let lastSentAt = Number.NEGATIVE_INFINITY;
  let timer: ThrottleTimer | null = null;
  let trailing: { value: T } | null = null;

  const fire = (value: T): void => {
    lastSentAt = now();
    options.send(value);
  };

  const flushTrailing = (): void => {
    timer = null;
    const held = trailing;
    trailing = null;
    if (held !== null) fire(held.value);
  };

  return {
    push: (value) => {
      const elapsed = now() - lastSentAt;
      if (elapsed >= intervalMs && timer === null) {
        fire(value);
        return;
      }
      // Inside the window: hold the newest value and make sure exactly one
      // timer is armed for the remainder. Re-arming per move would push the
      // send further away on every mouse event — a debounce by accident, and
      // the classic way a throttle turns into "nothing is ever sent while you
      // are moving".
      trailing = { value };
      if (timer === null) {
        timer = schedule(flushTrailing, Math.max(0, intervalMs - elapsed));
      }
    },
    cancel: () => {
      if (timer !== null) {
        unschedule(timer);
        timer = null;
      }
      trailing = null;
    },
    pending: () => trailing !== null,
  };
}
