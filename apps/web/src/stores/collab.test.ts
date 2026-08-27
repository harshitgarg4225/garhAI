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

import {
  CURSOR_TTL_MS,
  createRemoteOpsScheduler,
  pruneCursors,
  upsertCursor,
  useCollabStore,
  visibleCursors,
} from './collab';

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

// ---------------------------------------------------------------------------
// Remote cursors: the expiry rule
// ---------------------------------------------------------------------------
//
// Expiry is the part of live cursors that can be quietly wrong. Presence is
// authoritative — the server replaces the roster wholesale — but nothing stores
// a cursor anywhere, so a user who closes the tab, sleeps the laptop or drops
// the network sends no goodbye. Without a TTL their last position sits on the
// plan for the rest of the session, pointing at a wall nobody is looking at.
//
// Every test below drives the clock explicitly rather than waiting ten seconds.

const FRAME = { userId: 'u1', name: 'Asha', x: 1000, y: 2000, storeyIndex: 0 };

describe('cursor reducers', () => {
  it('upserts by userId — a moving pointer replaces, never accumulates', () => {
    let cursors = upsertCursor(new Map(), FRAME, 1_000);
    cursors = upsertCursor(cursors, { ...FRAME, x: 1500 }, 1_100);
    cursors = upsertCursor(cursors, { ...FRAME, userId: 'u2', name: 'Vikram' }, 1_100);

    expect(cursors.size).toBe(2);
    expect(cursors.get('u1')).toEqual({
      userId: 'u1',
      name: 'Asha',
      x: 1500,
      y: 2000,
      storeyIndex: 0,
      at: 1_100,
    });
  });

  it('expires an entry after the TTL of silence', () => {
    const cursors = upsertCursor(new Map(), FRAME, 1_000);

    // One millisecond short of the deadline: still here.
    expect(pruneCursors(cursors, 1_000 + CURSOR_TTL_MS - 1).size).toBe(1);
    // On the deadline: gone. A closed tab must not leave a ghost.
    expect(pruneCursors(cursors, 1_000 + CURSOR_TTL_MS).size).toBe(0);
  });

  it('expires only the silent ones', () => {
    let cursors = upsertCursor(new Map(), FRAME, 1_000);
    cursors = upsertCursor(cursors, { ...FRAME, userId: 'u2' }, 9_000);

    const pruned = pruneCursors(cursors, 12_000);
    expect([...pruned.keys()]).toEqual(['u2']);
  });

  it('returns the SAME map when nothing expired', () => {
    // Identity, not equality. The sweeper runs every 2s for the whole life of
    // an open project; a fresh map each tick would re-render the cursor layer
    // forever on a project where nobody is moving.
    const cursors = upsertCursor(new Map(), FRAME, 1_000);
    expect(pruneCursors(cursors, 1_500)).toBe(cursors);
  });

  it('drops a cursor on a storey you are not looking at', () => {
    let cursors = upsertCursor(new Map(), { ...FRAME, userId: 'ground', storeyIndex: 0 }, 1_000);
    cursors = upsertCursor(cursors, { ...FRAME, userId: 'first', storeyIndex: 1 }, 1_000);
    cursors = upsertCursor(cursors, { ...FRAME, userId: 'loose', storeyIndex: null }, 1_000);

    // Viewing storey 1: the ground-floor pointer is over geometry you cannot
    // see, so drawing it would be pointing at nothing. The unbound one shows.
    expect(visibleCursors(cursors, 1, 1_000).map((c) => c.userId)).toEqual(['first', 'loose']);
    // No storey context at all: show everybody.
    expect(visibleCursors(cursors, null, 1_000)).toHaveLength(3);
  });

  it('filters expired cursors at READ time as well as on the sweep', () => {
    // Browsers throttle timers in a backgrounded tab, so the sweeper alone
    // would let a ghost survive a tab switch. The TTL must be true at the
    // moment of drawing, not at the moment of the last timer tick.
    const cursors = upsertCursor(new Map(), FRAME, 1_000);
    expect(visibleCursors(cursors, null, 1_000 + CURSOR_TTL_MS)).toHaveLength(0);
  });
});

describe('useCollabStore (cursors)', () => {
  beforeEach(() => {
    useCollabStore.getState().reset();
  });

  it('records a frame and expires it on a sweep', () => {
    useCollabStore.getState().noteCursor(FRAME, 1_000);
    expect(useCollabStore.getState().cursors.size).toBe(1);

    useCollabStore.getState().sweepCursors(1_500);
    expect(useCollabStore.getState().cursors.size).toBe(1);

    useCollabStore.getState().sweepCursors(1_000 + CURSOR_TTL_MS);
    expect(useCollabStore.getState().cursors.size).toBe(0);
  });

  it('prunes on write, so a quiet colleague ages out on the next frame', () => {
    useCollabStore.getState().noteCursor({ ...FRAME, userId: 'quiet' }, 1_000);
    useCollabStore.getState().noteCursor({ ...FRAME, userId: 'busy' }, 1_000 + CURSOR_TTL_MS);
    expect([...useCollabStore.getState().cursors.keys()]).toEqual(['busy']);
  });

  it('reset clears cursors along with presence — project B never shows A’s', () => {
    const s = useCollabStore.getState();
    s.noteCursor(FRAME, 1_000);
    s.setPresence([{ userId: 'u1', name: 'Asha' }]);
    s.setConnected(true);

    s.reset();
    const after = useCollabStore.getState();
    expect(after.cursors.size).toBe(0);
    expect(after.users).toEqual([]);
    expect(after.connected).toBe(false);
  });
});
