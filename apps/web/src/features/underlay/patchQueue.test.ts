/**
 * The debounced patch queue.
 *
 * A debounce is the definition of code that looks obviously correct and drops
 * the last update, so the two failure shapes are pinned explicitly: a timer
 * firing after an explicit `flush` already emptied the queue (a duplicate
 * request, which for a PATCH means an older value overwriting a newer one), and
 * a push made while a flush is in progress being folded into the batch that has
 * already left (a lost update).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createPatchQueue, PATCH_DEBOUNCE_MS } from './patchQueue';

interface Patch {
  opacity?: number;
  visible?: boolean;
  mmPerPx?: number;
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('createPatchQueue', () => {
  it('sends nothing until the window elapses', () => {
    const sent: Partial<Patch>[] = [];
    const queue = createPatchQueue<Patch>((p) => sent.push(p));

    queue.push({ opacity: 0.5 });
    vi.advanceTimersByTime(PATCH_DEBOUNCE_MS - 1);
    expect(sent).toEqual([]);

    vi.advanceTimersByTime(1);
    expect(sent).toEqual([{ opacity: 0.5 }]);
  });

  it('collapses a slider drag into ONE request carrying the final value', () => {
    const sent: Partial<Patch>[] = [];
    const queue = createPatchQueue<Patch>((p) => sent.push(p));

    // The shape of an actual drag: a burst of samples inside one window.
    for (const opacity of [0.1, 0.2, 0.35, 0.4, 0.45]) {
      queue.push({ opacity });
      vi.advanceTimersByTime(16);
    }
    vi.advanceTimersByTime(PATCH_DEBOUNCE_MS);

    expect(sent).toEqual([{ opacity: 0.45 }]);
  });

  it('merges by field — last write wins per field, others survive', () => {
    const sent: Partial<Patch>[] = [];
    const queue = createPatchQueue<Patch>((p) => sent.push(p));

    queue.push({ opacity: 0.2 });
    queue.push({ visible: false });
    queue.push({ opacity: 0.9 });
    vi.advanceTimersByTime(PATCH_DEBOUNCE_MS);

    expect(sent).toEqual([{ opacity: 0.9, visible: false }]);
  });

  it('starts a fresh window after a send', () => {
    const sent: Partial<Patch>[] = [];
    const queue = createPatchQueue<Patch>((p) => sent.push(p));

    queue.push({ opacity: 0.2 });
    vi.advanceTimersByTime(PATCH_DEBOUNCE_MS);
    queue.push({ opacity: 0.7 });
    vi.advanceTimersByTime(PATCH_DEBOUNCE_MS);

    // Two requests, and the second carries ONLY what changed after the first.
    expect(sent).toEqual([{ opacity: 0.2 }, { opacity: 0.7 }]);
  });

  it('flush sends now, and the disarmed timer does not send again', () => {
    const sent: Partial<Patch>[] = [];
    const queue = createPatchQueue<Patch>((p) => sent.push(p));

    queue.push({ mmPerPx: 3.81 });
    queue.flush();
    expect(sent).toEqual([{ mmPerPx: 3.81 }]);

    // The bug this pins: a timer left armed fires into an empty queue, or
    // worse, re-sends the batch that already went.
    vi.advanceTimersByTime(PATCH_DEBOUNCE_MS * 4);
    expect(sent).toEqual([{ mmPerPx: 3.81 }]);
  });

  it('flush with nothing pending is a no-op, not an empty PATCH', () => {
    const sent: Partial<Patch>[] = [];
    const queue = createPatchQueue<Patch>((p) => sent.push(p));

    queue.flush();
    queue.flush();
    expect(sent).toEqual([]);
  });

  it('a push made from inside the send starts a NEW batch, not the one leaving', () => {
    const sent: Partial<Patch>[] = [];
    // eslint-disable-next-line prefer-const -- referenced by the callback above its own initialiser
    let queue = createPatchQueue<Patch>((p) => {
      sent.push(p);
      if (sent.length === 1) queue.push({ visible: true });
    });

    queue.push({ opacity: 0.3 });
    vi.advanceTimersByTime(PATCH_DEBOUNCE_MS);
    expect(sent).toEqual([{ opacity: 0.3 }]);

    vi.advanceTimersByTime(PATCH_DEBOUNCE_MS);
    expect(sent).toEqual([{ opacity: 0.3 }, { visible: true }]);
  });

  it('cancel drops the pending batch — the row it belonged to is gone', () => {
    const sent: Partial<Patch>[] = [];
    const queue = createPatchQueue<Patch>((p) => sent.push(p));

    queue.push({ opacity: 0.4 });
    expect(queue.pending()).toEqual({ opacity: 0.4 });

    queue.cancel();
    expect(queue.pending()).toBeNull();
    vi.advanceTimersByTime(PATCH_DEBOUNCE_MS * 4);
    expect(sent).toEqual([]);
  });

  it('honours an explicit delay', () => {
    const sent: Partial<Patch>[] = [];
    const queue = createPatchQueue<Patch>((p) => sent.push(p), { delayMs: 50 });

    queue.push({ opacity: 1 });
    vi.advanceTimersByTime(49);
    expect(sent).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(sent).toEqual([{ opacity: 1 }]);
  });
});
