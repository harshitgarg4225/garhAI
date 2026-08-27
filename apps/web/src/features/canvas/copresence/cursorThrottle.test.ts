/**
 * The cursor throttle. The whole point of testing it is that a broken throttle
 * still LOOKS like a working feature: cursors move, everything renders, and the
 * only symptom is 500 requests a second per user — invisible in a two-person
 * demo, and a stampede in a six-person session.
 *
 * So the assertions are counts against a driven clock, not "it eventually
 * sends". Vitest's fake timers replace `Date.now` as well as `setTimeout`, so
 * the throttle's default clock is already under test control; the tests below
 * lean on that rather than injecting a second one, because the code path with
 * the real defaults is the one that ships.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createTrailingThrottle } from './cursorThrottle';

const INTERVAL = 100;

describe('createTrailingThrottle', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('sends the first value immediately — the leading edge', () => {
    const send = vi.fn();
    const throttle = createTrailingThrottle<number>({ intervalMs: INTERVAL, send });

    throttle.push(1);
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenLastCalledWith(1);
  });

  it('collapses a burst inside one window into a single trailing send', () => {
    const send = vi.fn();
    const throttle = createTrailingThrottle<number>({ intervalMs: INTERVAL, send });

    // 40 moves in 40ms — a high-polling mouse over a fifth of a window.
    for (let i = 0; i < 40; i += 1) {
      throttle.push(i);
      vi.advanceTimersByTime(1);
    }
    // Leading edge only, so far.
    expect(send).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(INTERVAL);
    expect(send).toHaveBeenCalledTimes(2);
    // The trailing send carries the NEWEST value. An older position would put
    // a colleague's pointer behind where the mouse actually is.
    expect(send).toHaveBeenLastCalledWith(39);
  });

  it('holds the rate at ~10/s across a sustained drag (N moves → ≤ expected)', () => {
    const send = vi.fn();
    const throttle = createTrailingThrottle<number>({ intervalMs: INTERVAL, send });

    // One second of a 500Hz mouse: 500 moves, 2ms apart.
    for (let i = 0; i < 500; i += 1) {
      throttle.push(i);
      vi.advanceTimersByTime(2);
    }
    vi.advanceTimersByTime(INTERVAL);

    // 1000ms at a 100ms floor is at most 11 sends (leading + ten windows).
    // The assertion that matters is the CEILING: without the gate this is 500.
    expect(send.mock.calls.length).toBeLessThanOrEqual(11);
    expect(send.mock.calls.length).toBeGreaterThanOrEqual(9);
  });

  it('always publishes the resting position (the trailing edge)', () => {
    const send = vi.fn();
    const throttle = createTrailingThrottle<number>({ intervalMs: INTERVAL, send });

    throttle.push(1); // leading
    vi.advanceTimersByTime(10);
    throttle.push(2);
    vi.advanceTimersByTime(10);
    throttle.push(3); // the pointer stops here
    expect(send).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(INTERVAL);
    // Without a trailing edge, everyone else would see the pointer frozen at
    // `1` — slightly short of the thing you are actually pointing at.
    expect(send).toHaveBeenLastCalledWith(3);
  });

  it('cancel drops the pending trailing send — the pointer-leave path', () => {
    const send = vi.fn();
    const throttle = createTrailingThrottle<number>({ intervalMs: INTERVAL, send });

    throttle.push(1); // leading
    throttle.push(2); // held
    expect(throttle.pending()).toBe(true);

    throttle.cancel();
    expect(throttle.pending()).toBe(false);

    vi.advanceTimersByTime(INTERVAL * 5);
    // Only the leading send ever happened. Without cancel, moving off the
    // canvas would park a colleague's cursor at its edge up to 100ms later.
    expect(send).toHaveBeenCalledTimes(1);
  });

  it('re-arms cleanly after a cancel', () => {
    const send = vi.fn();
    const throttle = createTrailingThrottle<number>({ intervalMs: INTERVAL, send });

    throttle.push(1);
    throttle.push(2);
    throttle.cancel();

    vi.advanceTimersByTime(INTERVAL);
    throttle.push(3);
    // The window has elapsed, so this is a fresh leading edge, not a queued one.
    expect(send).toHaveBeenCalledTimes(2);
    expect(send).toHaveBeenLastCalledWith(3);
  });

  it('does not re-arm the timer per move (a throttle, not an accidental debounce)', () => {
    const send = vi.fn();
    const throttle = createTrailingThrottle<number>({ intervalMs: INTERVAL, send });

    throttle.push(0); // leading at t=0
    // Push every 10ms forever. If each push re-armed the timer for a fresh
    // 100ms, the trailing send would keep being pushed out and nothing would
    // ever arrive while the pointer was moving — the classic silent failure.
    for (let i = 1; i <= 9; i += 1) {
      vi.advanceTimersByTime(10);
      throttle.push(i);
    }
    vi.advanceTimersByTime(10); // t = 100
    expect(send).toHaveBeenCalledTimes(2);
  });

  it('honours an injected clock and scheduler', () => {
    // The injection points exist so a caller with its own frame clock can use
    // them; proving they are wired stops them rotting into decoration.
    let now = 0;
    const scheduled: { fn: () => void; ms: number }[] = [];
    const send = vi.fn();
    const throttle = createTrailingThrottle<string>({
      intervalMs: INTERVAL,
      send,
      now: () => now,
      schedule: (fn, ms) => {
        scheduled.push({ fn, ms });
        return 0 as unknown as ReturnType<typeof setTimeout>;
      },
      unschedule: () => undefined,
    });

    throttle.push('a');
    expect(send).toHaveBeenCalledTimes(1);

    now = 30;
    throttle.push('b');
    expect(scheduled).toHaveLength(1);
    // Scheduled for the REMAINDER of the window, not a fresh full one.
    expect(scheduled[0]?.ms).toBe(70);

    now = 100;
    scheduled[0]?.fn();
    expect(send).toHaveBeenLastCalledWith('b');
  });
});
