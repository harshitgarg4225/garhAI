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
 */

import { describe, expect, it } from 'vitest';

import { parseCollabFrame } from './collab';
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
    expect(parseCollabFrame('cursor', JSON.stringify({ x: 1, y: 2 }))).toBeNull();
    expect(parseCollabFrame('message', JSON.stringify(OPS_DATA))).toBeNull();
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
