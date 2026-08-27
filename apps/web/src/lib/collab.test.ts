/**
 * The collab frame contract. Two disciplines under test:
 *
 *  1. Frames that match the frozen wire contract parse, field for field.
 *  2. Anything else — malformed JSON, a missing required field, an event name
 *     from a newer server — is DROPPED (null), never thrown. A bad presence
 *     ping must not take down the plan someone is drawing.
 *
 * The last test runs real SSE buffers through `parseSseBuffer` + the frame
 * parser together, keep-alive comment pings included, because that pairing is
 * the actual read loop.
 *
 * The cursor blocks at the bottom add a third discipline the other frames do
 * not need: an OWN-ECHO filter. The server fans every cursor out to every
 * subscriber including its author, so `isOwnCursorEcho` is the only thing
 * between you and a second pointer shadowing your own. It is negative-tested —
 * the inverted predicate is written out and shown to fail the same assertions —
 * because a filter that silently stops filtering looks like a colleague
 * mirroring your mouse, not like a bug.
 */

import { describe, expect, it } from 'vitest';

import { isOwnCursorEcho, parseCollabFrame } from './collab';
import { parseSseBuffer } from './sse';

const OPS_DATA = {
  headIdx: 42,
  versionBranch: '4f6d2f66-9a1c-4c4e-8f8a-1c2d3e4f5a6b',
  actorId: '7b8c9d0e-1f2a-3b4c-5d6e-7f8a9b0c1d2e',
  source: 'solver',
  groupId: 'grp_01J0000000000000000000000',
};

describe('parseCollabFrame', () => {
  it('parses a hello frame with presence', () => {
    const frame = parseCollabFrame(
      'hello',
      JSON.stringify({
        headIdx: 7,
        presence: [
          { userId: 'u1', name: 'Asha Rao' },
          { userId: 'u2', name: 'Vikram' },
        ],
      }),
    );
    expect(frame).toEqual({
      kind: 'hello',
      hello: {
        headIdx: 7,
        presence: [
          { userId: 'u1', name: 'Asha Rao' },
          { userId: 'u2', name: 'Vikram' },
        ],
      },
    });
  });

  it('parses an ops frame, nullable fields included', () => {
    const frame = parseCollabFrame('ops', JSON.stringify(OPS_DATA));
    expect(frame).toEqual({ kind: 'ops', ops: OPS_DATA });

    const anonymous = parseCollabFrame(
      'ops',
      JSON.stringify({ ...OPS_DATA, actorId: null, groupId: null }),
    );
    expect(anonymous?.kind).toBe('ops');
    if (anonymous?.kind === 'ops') {
      expect(anonymous.ops.actorId).toBeNull();
      expect(anonymous.ops.groupId).toBeNull();
    }
  });

  it("catches an unknown source to 'manual' — quiet sync, not a dropped frame", () => {
    const frame = parseCollabFrame('ops', JSON.stringify({ ...OPS_DATA, source: 'automation' }));
    expect(frame?.kind).toBe('ops');
    if (frame?.kind === 'ops') expect(frame.ops.source).toBe('manual');
  });

  it('parses a presence frame', () => {
    const frame = parseCollabFrame(
      'presence',
      JSON.stringify({ users: [{ userId: 'u3', name: '' }] }),
    );
    expect(frame).toEqual({ kind: 'presence', users: [{ userId: 'u3', name: '' }] });
  });

  it('drops malformed JSON without throwing', () => {
    expect(parseCollabFrame('ops', '{not json')).toBeNull();
    expect(parseCollabFrame('hello', '')).toBeNull();
  });

  it('drops a frame missing a required field without throwing', () => {
    // ops without headIdx is a doorbell that cannot say which door: useless.
    const { headIdx: _dropped, ...rest } = OPS_DATA;
    expect(parseCollabFrame('ops', JSON.stringify(rest))).toBeNull();
    expect(parseCollabFrame('hello', JSON.stringify({ presence: [] }))).toBeNull();
    expect(parseCollabFrame('ops', JSON.stringify({ ...OPS_DATA, headIdx: 1.5 }))).toBeNull();
  });

  it("drops an event name this client does not know (the server's future)", () => {
    // `cursor` USED to be listed here; it is a known frame now and has its own
    // describe block below. `message` stands in for whatever the server grows
    // next: unknown means dropped, never thrown.
    expect(parseCollabFrame('message', JSON.stringify(OPS_DATA))).toBeNull();
    expect(parseCollabFrame('', JSON.stringify(OPS_DATA))).toBeNull();
  });

  it('reads a realistic stream chunk: ping, hello, ops', () => {
    const buffer =
      ': keep-alive\n\n' +
      `event: hello\ndata: ${JSON.stringify({ headIdx: 3, presence: [] })}\n\n` +
      `event: ops\nid: 42\ndata: ${JSON.stringify(OPS_DATA)}\n\n`;

    const { frames, rest } = parseSseBuffer(buffer);
    expect(rest).toBe('');

    const parsed = frames.map((f) => parseCollabFrame(f.event, f.data)).filter((f) => f !== null);
    expect(parsed.map((f) => f.kind)).toEqual(['hello', 'ops']);
  });
});

// ---------------------------------------------------------------------------
// Live cursors
// ---------------------------------------------------------------------------

const CURSOR_DATA = {
  userId: '7b8c9d0e-1f2a-3b4c-5d6e-7f8a9b0c1d2e',
  name: 'Asha Rao',
  x: 4200,
  y: -1150,
  storeyIndex: 1,
};

describe('parseCollabFrame — cursor frames', () => {
  it('parses the five contract keys, negative coordinates included', () => {
    const frame = parseCollabFrame('cursor', JSON.stringify(CURSOR_DATA));
    expect(frame).toEqual({ kind: 'cursor', cursor: CURSOR_DATA });
  });

  it('accepts an explicit null storeyIndex — "not storey-bound"', () => {
    const frame = parseCollabFrame('cursor', JSON.stringify({ ...CURSOR_DATA, storeyIndex: null }));
    expect(frame?.kind).toBe('cursor');
    if (frame?.kind === 'cursor') expect(frame.cursor.storeyIndex).toBeNull();
  });

  it('defaults a missing name — a nameless cursor is still a cursor', () => {
    const { name: _dropped, ...rest } = CURSOR_DATA;
    const frame = parseCollabFrame('cursor', JSON.stringify(rest));
    expect(frame?.kind).toBe('cursor');
    if (frame?.kind === 'cursor') expect(frame.cursor.name).toBe('');
  });

  it('DROPS a frame whose storeyIndex key is absent, rather than defaulting it', () => {
    // The publisher always sends the key. Defaulting an absent one to null
    // would paint a ground-floor pointer onto a first-floor plan — a wrong
    // answer that looks like a working feature. Dropping makes it visible.
    const { storeyIndex: _dropped, ...rest } = CURSOR_DATA;
    expect(parseCollabFrame('cursor', JSON.stringify(rest))).toBeNull();
  });

  it('drops malformed cursor frames without throwing', () => {
    expect(parseCollabFrame('cursor', '{not json')).toBeNull();
    // No identity: unrenderable AND unfilterable.
    expect(parseCollabFrame('cursor', JSON.stringify({ ...CURSOR_DATA, userId: '' }))).toBeNull();
    // No position.
    expect(parseCollabFrame('cursor', JSON.stringify({ x: 1, y: 2 }))).toBeNull();
    // Float millimetres mean the wire contract moved under us.
    expect(parseCollabFrame('cursor', JSON.stringify({ ...CURSOR_DATA, x: 1.5 }))).toBeNull();
    expect(
      parseCollabFrame('cursor', JSON.stringify({ ...CURSOR_DATA, storeyIndex: 'ground' })),
    ).toBeNull();
  });

  it('carries no id: — a cursor must never be replayed as an ops head', () => {
    // Proven against the real read loop: the server emits cursor frames with no
    // `id:` line, and `parseSseBuffer` reports that absence.
    const buffer = `event: cursor\ndata: ${JSON.stringify(CURSOR_DATA)}\n\n`;
    const { frames } = parseSseBuffer(buffer);
    expect(frames).toHaveLength(1);
    expect(frames[0]?.id).toBeNull();
  });
});

describe('isOwnCursorEcho — the own-echo gate', () => {
  const mine = { ...CURSOR_DATA, userId: 'me' };
  const theirs = { ...CURSOR_DATA, userId: 'them' };

  it('drops my own cursor and keeps everyone else’s', () => {
    expect(isOwnCursorEcho(mine, 'me')).toBe(true);
    expect(isOwnCursorEcho(theirs, 'me')).toBe(false);
  });

  it('drops nothing when the signed-in identity is not known yet', () => {
    // Better a cursor we cannot attribute than a cursor discarded.
    expect(isOwnCursorEcho(mine, null)).toBe(false);
    expect(isOwnCursorEcho(mine, undefined)).toBe(false);
  });

  /**
   * NEGATIVE TEST (CLAUDE.md: "a green check that cannot go red is worse than
   * no check"). The server fans every cursor out to everybody, author included,
   * so this predicate is the ONLY thing standing between you and a second
   * cursor shadowing your own by one round trip. Here it is deliberately
   * inverted, to prove the assertions above would actually catch that.
   */
  it('an inverted own-echo filter fails these same assertions', () => {
    const inverted = (frame: { userId: string }, selfUserId: string | null): boolean =>
      !(selfUserId !== null && frame.userId === selfUserId);

    expect(inverted(mine, 'me')).not.toBe(isOwnCursorEcho(mine, 'me'));
    expect(inverted(theirs, 'me')).not.toBe(isOwnCursorEcho(theirs, 'me'));
    // And the failure is exactly the visible bug: my own cursor kept…
    expect(inverted(mine, 'me')).toBe(false);
    // …while my colleague's is thrown away.
    expect(inverted(theirs, 'me')).toBe(true);
  });
});

describe('the cursor dispatch decision, as the read loop makes it', () => {
  /**
   * `subscribeProjectCollab` parses each frame and then, for a cursor, asks
   * `isOwnCursorEcho` before calling `onCursor`. The transport around that is
   * `fetch` + a stream, which this suite deliberately does not mock (the same
   * choice `sse.test.ts` makes). What IS worth pinning is the composition —
   * parse, then filter — because a wiring that parsed correctly and forgot to
   * filter would look identical in the parser tests above.
   */
  const deliver = (event: string, data: string, selfUserId: string | null): unknown => {
    const parsed = parseCollabFrame(event, data);
    if (parsed === null) return null;
    if (parsed.kind !== 'cursor') return parsed;
    return isOwnCursorEcho(parsed.cursor, selfUserId) ? null : parsed;
  };

  it('delivers a colleague and swallows my own echo', () => {
    const theirs = JSON.stringify({ ...CURSOR_DATA, userId: 'them' });
    const mine = JSON.stringify({ ...CURSOR_DATA, userId: 'me' });

    expect(deliver('cursor', theirs, 'me')).not.toBeNull();
    expect(deliver('cursor', mine, 'me')).toBeNull();
  });

  it('reads a realistic stream chunk: ping, cursor, cursor echo, ops', () => {
    const buffer =
      ': keep-alive\n\n' +
      `event: cursor\ndata: ${JSON.stringify({ ...CURSOR_DATA, userId: 'them' })}\n\n` +
      `event: cursor\ndata: ${JSON.stringify({ ...CURSOR_DATA, userId: 'me' })}\n\n` +
      `event: ops\nid: 42\ndata: ${JSON.stringify(OPS_DATA)}\n\n`;

    const { frames, rest } = parseSseBuffer(buffer);
    expect(rest).toBe('');

    const delivered = frames
      .map((f) => deliver(f.event, f.data, 'me'))
      .filter((f): f is { kind: string } => f !== null);
    // The echo is gone; the ops doorbell still rings.
    expect(delivered.map((f) => f.kind)).toEqual(['cursor', 'ops']);
  });
});
