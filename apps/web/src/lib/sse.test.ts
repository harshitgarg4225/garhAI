/**
 * The SSE frame parser. It is pure, and it is the part of the progress stream
 * that a chunked network will exercise hardest: a frame split across two TCP
 * reads must not be dropped, and a keep-alive comment must not be read as an
 * event (§15 — the progress bar tells the truth or says nothing).
 */

import { describe, expect, it } from 'vitest';

import { parseSseBuffer } from './sse';

describe('parseSseBuffer', () => {
  it('reads a complete frame and keeps the partial one back', () => {
    const { frames, rest } = parseSseBuffer(
      'event: progress\ndata: {"seq":1}\n\nevent: progress\ndata: {"seq":2',
    );
    expect(frames).toEqual([{ event: 'progress', data: '{"seq":1}', id: null }]);
    expect(rest).toBe('event: progress\ndata: {"seq":2');
  });

  it('accepts CRLF and lone-CR separators', () => {
    const crlf = parseSseBuffer('event: done\r\ndata: {"ok":true}\r\n\r\n');
    expect(crlf.frames).toHaveLength(1);
    expect(crlf.frames[0]?.event).toBe('done');
    expect(crlf.rest).toBe('');

    const cr = parseSseBuffer('data: {"ok":true}\r\r');
    expect(cr.frames).toHaveLength(1);
  });

  it('ignores comments and keep-alives without emitting a frame', () => {
    const { frames, rest } = parseSseBuffer(': keep-alive\n\n');
    expect(frames).toEqual([]);
    expect(rest).toBe('');
  });

  it('joins multi-line data and strips exactly one leading space', () => {
    const { frames } = parseSseBuffer('data: line one\ndata:line two\n\n');
    expect(frames[0]?.data).toBe('line one\nline two');
  });

  it('defaults the event name to "message" and carries the id', () => {
    const { frames } = parseSseBuffer('id: 42\ndata: {"seq":42}\n\n');
    expect(frames[0]).toEqual({ event: 'message', data: '{"seq":42}', id: '42' });
  });

  it('reassembles a frame delivered in three chunks', () => {
    const chunks = ['event: progr', 'ess\ndata: {"seq":', '7}\n\n'];
    let buffer = '';
    const seen: string[] = [];
    for (const chunk of chunks) {
      buffer += chunk;
      const { frames, rest } = parseSseBuffer(buffer);
      buffer = rest;
      for (const frame of frames) seen.push(frame.data);
    }
    expect(seen).toEqual(['{"seq":7}']);
    expect(buffer).toBe('');
  });
});

// ---------------------------------------------------------------------------
// The progress-event wire contract, pinned with REAL captured frames
// ---------------------------------------------------------------------------

import { progressEventFromState, progressEventSchema } from './schemas';

describe('progressEventSchema (worker wire shape)', () => {
  // Captured verbatim from a live `GET /solver-jobs/:id/events` stream. The
  // worker speaks `type`/`percent`/`tsMs`; the first schema expected the job
  // row's `status`/`progress`/`at` and its catch/default dressed EVERY event
  // as a non-terminal "running" — the theater sat on "Waiting in the queue…"
  // while the worker logged job.succeeded. These frames make that impossible
  // to regress silently.
  const QUEUED_FRAME = {
    data: { kind: 'solver.generate', queueDepth: 1 },
    jobId: 'b3f5cdf6-bbde-4b2f-81f2-be532e2b5ac5',
    message: 'Waiting for a free worker.',
    schemaVersion: 1,
    seq: 1,
    tsMs: 1787734385000,
    type: 'progress',
  };
  const SUCCEEDED_FRAME = {
    data: { options: [] },
    jobId: 'b3f5cdf6-bbde-4b2f-81f2-be532e2b5ac5',
    message: 'Generated 3 plan options.',
    percent: 100,
    schemaVersion: 1,
    seq: 15,
    tsMs: 1787734415000,
    type: 'succeeded',
  };

  it('maps worker event types through the server type→status table', () => {
    const queued = progressEventSchema.parse(QUEUED_FRAME);
    expect(queued.status).toBe('running');
    expect(queued.terminal).toBe(false);
    expect(queued.message).toBe('Waiting for a free worker.');
    expect(queued.jobKind).toBe('solver.generate');

    const done = progressEventSchema.parse(SUCCEEDED_FRAME);
    expect(done.status).toBe('succeeded');
    expect(done.terminal).toBe(true);
    expect(done.progress).toBe(100);
    expect(done.seq).toBe(15);
  });

  it('treats dead_lettered as a terminal failure, like the server does', () => {
    const dead = progressEventSchema.parse({ ...SUCCEEDED_FRAME, type: 'dead_lettered' });
    expect(dead.status).toBe('failed');
    expect(dead.terminal).toBe(true);
  });

  it('refuses a frame with no type — the old row-shaped assumption', () => {
    // The pre-fix schema would have "parsed" this as running/non-terminal.
    const wrong = progressEventSchema.safeParse({
      jobId: 'x',
      status: 'succeeded',
      progress: 100,
      terminal: true,
      at: '2026-08-26T00:00:00Z',
      jobKind: 'solver',
    });
    expect(wrong.success).toBe(false);
  });
});

describe('progressEventFromState (the opening `state` frame)', () => {
  it('turns a finished job row into a terminal event', () => {
    const event = progressEventFromState({
      id: 'b3f5cdf6-bbde-4b2f-81f2-be532e2b5ac5',
      kind: 'solver',
      status: 'succeeded',
      progress: 100,
      updatedAt: '2026-08-26T08:53:10Z',
    });
    expect(event).not.toBeNull();
    expect(event?.terminal).toBe(true);
    expect(event?.status).toBe('succeeded');
  });

  it('keeps a running row non-terminal and carries the error on a failed one', () => {
    const running = progressEventFromState({ id: 'j', status: 'running', progress: 10 });
    expect(running?.terminal).toBe(false);
    const failed = progressEventFromState({ id: 'j', status: 'failed', error: 'boom' });
    expect(failed?.terminal).toBe(true);
    expect(failed?.message).toBe('boom');
  });
});
