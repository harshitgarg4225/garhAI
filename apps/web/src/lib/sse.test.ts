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
