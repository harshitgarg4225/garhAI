/**
 * Comment anchors → pins on the plan.
 *
 * `Comment.anchor` is unvalidated JSONB on both sides — deliberately, because
 * three surfaces write into it — so every test here is about what happens when
 * it is NOT the shape this file wants. The rule under test is the SSE parser's
 * rule applied to storage: an anchor we cannot fully understand yields no pin,
 * and the comment still belongs in the thread.
 *
 * The numbering tests matter more than they look. Pin numbers appear in comment
 * bodies ("see pin 3"), so they must not depend on which storey is being viewed,
 * whether resolved pins are shown, or what order the caller's array happens to
 * be in.
 */

import { describe, expect, it } from 'vitest';

import type { Comment } from '../../lib/schemas';
import { numberPlanPins, pinExcerpt, planAnchorPayload, planPins, readPlanAnchor } from './anchor';

function comment(over: Partial<Comment> & { id: string }): Comment {
  return {
    projectId: 'p1',
    body: 'Widen this door.',
    authorName: 'Asha',
    anchor: {},
    resolved: false,
    fromShareLink: false,
    createdAt: '2026-08-20T10:00:00Z',
    ...over,
  };
}

const GROUND = 'storey_ground';
const FIRST = 'storey_first';

describe('readPlanAnchor', () => {
  it('reads a plan anchor, storey id and all', () => {
    expect(readPlanAnchor({ kind: 'plan', target: GROUND, x: 1200, y: -450 })).toEqual({
      kind: 'plan',
      storeyId: GROUND,
      x: 1200,
      y: -450,
    });
  });

  it('rejects the other anchor kinds — this file draws plans only', () => {
    expect(readPlanAnchor({ kind: 'sheet', target: 'sheet_1', x: 10, y: 10 })).toBeNull();
    expect(readPlanAnchor({ kind: 'render', target: 'r1', x: 10, y: 10 })).toBeNull();
    // An unanchored comment: the plain composer, and everything written before
    // pins existed.
    expect(readPlanAnchor({})).toBeNull();
  });

  it('rejects an anchor with no usable position', () => {
    expect(readPlanAnchor({ kind: 'plan', target: GROUND })).toBeNull();
    expect(readPlanAnchor({ kind: 'plan', x: '1200', y: 0 })).toBeNull();
    expect(readPlanAnchor({ kind: 'plan', x: null, y: 0 })).toBeNull();
    expect(readPlanAnchor({ kind: 'plan', x: Number.NaN, y: 0 })).toBeNull();
    expect(readPlanAnchor({ kind: 'plan', x: Number.POSITIVE_INFINITY, y: 0 })).toBeNull();
  });

  it('treats a missing or blank target as "every storey"', () => {
    // Fails safe: a pin nobody can see is a comment quietly lost.
    expect(readPlanAnchor({ kind: 'plan', x: 1, y: 2 })?.storeyId).toBeNull();
    expect(readPlanAnchor({ kind: 'plan', target: '   ', x: 1, y: 2 })?.storeyId).toBeNull();
  });

  it('rounds to whole millimetres, half away from zero', () => {
    // The model's rule, not `Math.round` — which is half-UP and would send
    // −0.5 and +0.5 in the same direction.
    expect(readPlanAnchor({ kind: 'plan', x: 10.5, y: -10.5 })).toMatchObject({ x: 11, y: -11 });
    expect(readPlanAnchor({ kind: 'plan', x: 10.4, y: -10.4 })).toMatchObject({ x: 10, y: -10 });
  });

  it('round-trips through planAnchorPayload', () => {
    // These two are each other's inverse. Without this assertion nothing stops
    // a later edit writing `storeyId` on one side and reading `target` on the
    // other — which typechecks perfectly and draws no pins at all.
    const payload = planAnchorPayload({ x: 3400, y: 900 }, FIRST);
    expect(readPlanAnchor(payload)).toEqual({ kind: 'plan', storeyId: FIRST, x: 3400, y: 900 });

    const loose = planAnchorPayload({ x: 0, y: 0 }, null);
    expect(readPlanAnchor(loose)?.storeyId).toBeNull();
  });
});

describe('numberPlanPins', () => {
  it('numbers chronologically, whatever order the list arrives in', () => {
    // `useComments` holds the thread NEWEST first for display. Pin numbers must
    // not inherit that.
    const newest = comment({
      id: 'c3',
      createdAt: '2026-08-20T12:00:00Z',
      anchor: planAnchorPayload({ x: 3, y: 3 }, GROUND),
    });
    const middle = comment({
      id: 'c2',
      createdAt: '2026-08-20T11:00:00Z',
      anchor: planAnchorPayload({ x: 2, y: 2 }, GROUND),
    });
    const oldest = comment({
      id: 'c1',
      createdAt: '2026-08-20T10:00:00Z',
      anchor: planAnchorPayload({ x: 1, y: 1 }, GROUND),
    });

    const pins = numberPlanPins([newest, middle, oldest]);
    expect(pins.map((p) => [p.comment.id, p.number])).toEqual([
      ['c1', 1],
      ['c2', 2],
      ['c3', 3],
    ]);
  });

  it('breaks a same-millisecond tie on id, so numbering is total', () => {
    const a = comment({ id: 'aaa', anchor: planAnchorPayload({ x: 1, y: 1 }, GROUND) });
    const b = comment({ id: 'bbb', anchor: planAnchorPayload({ x: 2, y: 2 }, GROUND) });
    expect(numberPlanPins([b, a]).map((p) => p.comment.id)).toEqual(['aaa', 'bbb']);
  });

  it('skips comments that are not plan-anchored', () => {
    const plain = comment({ id: 'plain' });
    const sheet = comment({ id: 'sheet', anchor: { kind: 'sheet', target: 's1', x: 0, y: 0 } });
    const pinned = comment({ id: 'pinned', anchor: planAnchorPayload({ x: 1, y: 1 }, GROUND) });

    expect(numberPlanPins([plain, sheet, pinned]).map((p) => p.comment.id)).toEqual(['pinned']);
  });
});

describe('planPins — filtering', () => {
  const groundPin = comment({
    id: 'g',
    createdAt: '2026-08-20T10:00:00Z',
    anchor: planAnchorPayload({ x: 1, y: 1 }, GROUND),
  });
  const firstPin = comment({
    id: 'f',
    createdAt: '2026-08-20T11:00:00Z',
    anchor: planAnchorPayload({ x: 2, y: 2 }, FIRST),
  });
  const loosePin = comment({
    id: 'l',
    createdAt: '2026-08-20T12:00:00Z',
    anchor: planAnchorPayload({ x: 3, y: 3 }, null),
  });
  const resolvedPin = comment({
    id: 'r',
    createdAt: '2026-08-20T13:00:00Z',
    resolved: true,
    anchor: planAnchorPayload({ x: 4, y: 4 }, GROUND),
  });
  const all = [resolvedPin, loosePin, firstPin, groundPin];

  it('shows only this storey, plus the storey-less ones', () => {
    const pins = planPins(all, { storeyId: FIRST, includeResolved: false });
    expect(pins.map((p) => p.comment.id)).toEqual(['f', 'l']);
  });

  it('hides resolved pins unless asked', () => {
    expect(
      planPins(all, { storeyId: GROUND, includeResolved: false }).map((p) => p.comment.id),
    ).toEqual(['g', 'l']);
    expect(
      planPins(all, { storeyId: GROUND, includeResolved: true }).map((p) => p.comment.id),
    ).toEqual(['g', 'l', 'r']);
  });

  it('keeps numbers stable across every filter', () => {
    // THE assertion of this file. Pin 4 is pin 4 on the ground floor, on the
    // first floor, and with resolved pins hidden or shown. Numbering the
    // filtered subset instead would renumber every pin on a storey switch and
    // make "see pin 3" in a comment body meaningless.
    const numberOf = (id: string, storeyId: string | null, includeResolved: boolean): number =>
      planPins(all, { storeyId, includeResolved }).find((p) => p.comment.id === id)?.number ?? -1;

    expect(numberOf('l', null, true)).toBe(3);
    expect(numberOf('l', GROUND, false)).toBe(3);
    expect(numberOf('l', FIRST, true)).toBe(3);
    expect(numberOf('r', GROUND, true)).toBe(4);
  });

  it('a null storey shows every pin', () => {
    expect(planPins(all, { storeyId: null, includeResolved: true })).toHaveLength(4);
  });
});

describe('pinExcerpt', () => {
  it('takes the first line only', () => {
    expect(pinExcerpt('Widen this door.\nAlso check the sill.')).toBe('Widen this door.');
  });

  it('caps a long line with an ellipsis', () => {
    const long = 'x'.repeat(200);
    const excerpt = pinExcerpt(long, 20);
    expect(excerpt).toHaveLength(20);
    expect(excerpt.endsWith('…')).toBe(true);
  });

  it('survives an empty body', () => {
    expect(pinExcerpt('')).toBe('');
    expect(pinExcerpt('\n\n')).toBe('');
  });
});
