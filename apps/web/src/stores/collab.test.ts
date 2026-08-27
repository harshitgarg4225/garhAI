/**
 * The presence reducer and the remote-ops scheduler — the two collab pieces
 * with logic worth breaking on purpose.
 *
 * The scheduler tests are the negative-testing rule applied up front: a frame
 * BEHIND our head must not pull (that is the self-echo path — get it wrong and
 * every local edit triggers a redundant round trip), and a burst must collapse
 * to ONE pull (a solver apply is dozens of groups in quick succession).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createRemoteOpsScheduler, useCollabStore } from './collab';

describe('useCollabStore (presence reducer)', () => {
  beforeEach(() => {
    useCollabStore.getState().reset();
  });

  it('replaces the roster wholesale and dedupes by userId', () => {
    const s = useCollabStore.getState();
    s.setPresence([
      { userId: 'u1', name: 'Asha' },
      { userId: 'u2', name: 'Vikram' },
    ]);
    // A later frame is the whole truth, not a merge — u2 left, u3 arrived,
    // and a duplicated u1 row is a server hiccup to absorb, not render.
    s.setPresence([
      { userId: 'u1', name: 'Asha' },
      { userId: 'u1', name: 'Asha' },
      { userId: 'u3', name: 'Meera' },
    ]);
    expect(useCollabStore.getState().users).toEqual([
      { userId: 'u1', name: 'Asha' },
      { userId: 'u3', name: 'Meera' },
    ]);
  });

  it('tracks connectivity and reset clears both facts', () => {
    const s = useCollabStore.getState();
    s.setConnected(true);
    s.setPresence([{ userId: 'u1', name: 'Asha' }]);
    expect(useCollabStore.getState().connected).toBe(true);

    s.reset();
    expect(useCollabStore.getState().users).toEqual([]);
    expect(useCollabStore.getState().connected).toBe(false);
  });
});

describe('createRemoteOpsScheduler', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  function build(headIdx: number) {
    const pull = vi.fn(() => Promise.resolve());
    const announce = vi.fn();
    const scheduler = createRemoteOpsScheduler({
      getHeadIdx: () => headIdx,
      pull,
      announce,
      debounceMs: 250,
    });
    return { pull, announce, scheduler };
  }

  it('ignores a frame at or behind the current head (our own echo)', async () => {
    const { pull, scheduler } = build(10);
    scheduler.notice(10, 'manual');
    scheduler.notice(3, 'solver');
    await vi.advanceTimersByTimeAsync(1_000);
    expect(pull).not.toHaveBeenCalled();
  });

  it('pulls once, after the trailing debounce, when the frame is ahead', async () => {
    const { pull, scheduler } = build(10);
    scheduler.notice(11, 'manual');
    expect(pull).not.toHaveBeenCalled(); // trailing, not leading
    await vi.advanceTimersByTimeAsync(249);
    expect(pull).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(pull).toHaveBeenCalledTimes(1);
  });

  it('collapses a burst into a single pull', async () => {
    const { pull, scheduler } = build(10);
    scheduler.notice(11, 'manual');
    await vi.advanceTimersByTimeAsync(100);
    scheduler.notice(12, 'manual');
    await vi.advanceTimersByTimeAsync(100);
    scheduler.notice(13, 'manual');
    await vi.advanceTimersByTimeAsync(250);
    expect(pull).toHaveBeenCalledTimes(1);
  });

  it('announces solver/copilot bursts once, after the pull; manual stays silent', async () => {
    const { pull, announce, scheduler } = build(10);
    scheduler.notice(11, 'manual');
    scheduler.notice(12, 'solver');
    scheduler.notice(13, 'manual');
    await vi.advanceTimersByTimeAsync(250);
    expect(pull).toHaveBeenCalledTimes(1);
    expect(announce).toHaveBeenCalledTimes(1);
    expect(announce).toHaveBeenCalledWith('solver');

    // A purely manual burst: quiet sync, no toast.
    scheduler.notice(14, 'manual');
    await vi.advanceTimersByTimeAsync(250);
    expect(pull).toHaveBeenCalledTimes(2);
    expect(announce).toHaveBeenCalledTimes(1);
  });

  it('cancel drops the pending pull and the pending announcement', async () => {
    const { pull, announce, scheduler } = build(10);
    scheduler.notice(11, 'copilot');
    scheduler.cancel();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(pull).not.toHaveBeenCalled();
    expect(announce).not.toHaveBeenCalled();
  });

  it('a rejected pull does not escape the scheduler', async () => {
    const pull = vi.fn(() => Promise.reject(new Error('offline')));
    const announce = vi.fn();
    const scheduler = createRemoteOpsScheduler({
      getHeadIdx: () => 0,
      pull,
      announce,
      debounceMs: 250,
    });
    scheduler.notice(1, 'solver');
    await vi.advanceTimersByTimeAsync(250);
    await Promise.resolve(); // let the rejection settle through the .catch
    expect(pull).toHaveBeenCalledTimes(1);
    // No pull, no announcement — the toast must describe an applied change.
    expect(announce).not.toHaveBeenCalled();
  });
});
