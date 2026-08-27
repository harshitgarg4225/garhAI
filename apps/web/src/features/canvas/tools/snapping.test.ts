/**
 * Spec for where the pointer actually lands.
 *
 * Two properties matter more than any individual case, and both are asserted
 * repeatedly below:
 *
 *  1. **Everything that comes out is integer millimetres.** `resolveSnap` is the
 *     last thing between a float-ish pointer and an op payload; if a float ever
 *     escapes here, `canonicalJson` throws somewhere far away from the cause.
 *  2. **The choice is deterministic.** Two walls sharing an endpoint produce two
 *     identical-distance candidates, and a snap marker that flickers between
 *     them under a still mouse is a bug that takes a day to find.
 */

import { describe, expect, it } from 'vitest';

import { SNAP_TOLERANCE_PX } from './constants';
import {
  collectSnapCandidates,
  compareSnapCandidates,
  projectOnSegment,
  resolveSnap,
  snapToleranceMm,
  snapWalls,
  toSnapView,
  type SnapCandidate,
} from './snapping';
import { FIXTURE_IDS, makeCtx } from './toolTestKit';

describe('projectOnSegment', () => {
  const a = { x: 0, y: 0 };
  const b = { x: 6000, y: 0 };

  it('projects a point onto the segment and reports the distance along it', () => {
    const p = projectOnSegment({ x: 1500, y: 300 }, a, b);
    expect(p.pointMm).toEqual({ x: 1500, y: 0 });
    expect(p.alongMm).toBe(1500);
    expect(p.distanceMm).toBe(300);
    expect(p.inside).toBe(true);
  });

  it('clamps past the ends, and says the projection was outside', () => {
    const before = projectOnSegment({ x: -400, y: 0 }, a, b);
    expect(before.pointMm).toEqual({ x: 0, y: 0 });
    expect(before.alongMm).toBe(0);
    expect(before.inside).toBe(false);

    const after = projectOnSegment({ x: 9000, y: 0 }, a, b);
    expect(after.pointMm).toEqual({ x: 6000, y: 0 });
    expect(after.inside).toBe(false);
  });

  it('survives a degenerate segment instead of dividing by zero', () => {
    const p = projectOnSegment({ x: 300, y: 400 }, a, a);
    expect(p.pointMm).toEqual(a);
    expect(p.distanceMm).toBe(500);
    expect(p.inside).toBe(false);
  });

  it('returns integer millimetres from a diagonal, where the maths is not exact', () => {
    const p = projectOnSegment({ x: 1000, y: 0 }, { x: 0, y: 0 }, { x: 3000, y: 3000 });
    expect(Number.isInteger(p.pointMm.x)).toBe(true);
    expect(Number.isInteger(p.pointMm.y)).toBe(true);
    expect(Number.isInteger(p.alongMm)).toBe(true);
    expect(Number.isInteger(p.distanceMm)).toBe(true);
  });
});

describe('tolerance is constant in screen pixels', () => {
  it('scales with the zoom', () => {
    expect(snapToleranceMm(1)).toBe(SNAP_TOLERANCE_PX);
    expect(snapToleranceMm(10)).toBe(SNAP_TOLERANCE_PX * 10);
  });

  it('never collapses to zero when zoomed all the way in', () => {
    expect(snapToleranceMm(0.001)).toBe(1);
  });
});

describe('candidate collection', () => {
  it('only considers walls on the active storey', () => {
    const ctx = makeCtx();
    expect(snapWalls(ctx)).toHaveLength(5);
    expect(snapWalls(ctx).every((w) => w.storeyId === FIXTURE_IDS.groundStorey)).toBe(true);
  });

  it('honours excludeIds — a wall being dragged does not snap to itself', () => {
    const ctx = makeCtx();
    const kept = snapWalls(ctx, new Set([FIXTURE_IDS.wallSouth]));
    expect(kept.map((w) => w.id)).not.toContain(FIXTURE_IDS.wallSouth);
    expect(kept).toHaveLength(4);
  });

  it('finds nothing when the pointer is out in the open', () => {
    const ctx = makeCtx();
    expect(collectSnapCandidates(ctx, { x: 1000, y: 1000 })).toEqual([]);
  });

  it('finds the plot corner as well as the wall endpoints that share it', () => {
    const ctx = makeCtx();
    const kinds = new Set(collectSnapCandidates(ctx, { x: 0, y: 0 }).map((c) => c.kind));
    expect(kinds.has('endpoint')).toBe(true);
    expect(kinds.has('plot-corner')).toBe(true);
  });
});

describe('candidate ordering', () => {
  function candidate(over: Partial<SnapCandidate>): SnapCandidate {
    return {
      kind: 'endpoint',
      pointMm: { x: 0, y: 0 },
      label: 'Wall end',
      refId: null,
      distanceMm: 0,
      rank: 100,
      ...over,
    };
  }

  it('ranks an endpoint over a midpoint over the wall line', () => {
    const sorted = [
      candidate({ kind: 'wall-line', rank: 60 }),
      candidate({ kind: 'endpoint', rank: 100 }),
      candidate({ kind: 'midpoint', rank: 80 }),
    ].sort(compareSnapCandidates);
    expect(sorted.map((c) => c.kind)).toEqual(['endpoint', 'midpoint', 'wall-line']);
  });

  it('breaks a rank tie by distance, then by position — never arbitrarily', () => {
    const near = candidate({ distanceMm: 2 });
    const far = candidate({ distanceMm: 9 });
    expect([far, near].sort(compareSnapCandidates)[0]).toBe(near);

    const left = candidate({ pointMm: { x: 10, y: 0 } });
    const right = candidate({ pointMm: { x: 40, y: 0 } });
    expect([right, left].sort(compareSnapCandidates)[0]).toBe(left);
  });
});

describe('resolveSnap', () => {
  it('takes the endpoint when the pointer is on one', () => {
    const ctx = makeCtx();
    const r = resolveSnap(ctx, { x: 6000, y: 4004 });
    expect(r.candidate?.kind).toBe('endpoint');
    expect(r.pointMm).toEqual({ x: 6000, y: 4000 });
  });

  it('picks the same candidate every time a still pointer asks', () => {
    // Two walls share (6000, 4000); the total order must not flicker.
    const ctx = makeCtx();
    const first = resolveSnap(ctx, { x: 6000, y: 4000 });
    const second = resolveSnap(ctx, { x: 6000, y: 4000 });
    expect(second.candidate?.refId).toBe(first.candidate?.refId);
    expect([FIXTURE_IDS.wallEast, FIXTURE_IDS.wallNorth]).toContain(first.candidate?.refId);
  });

  it('takes the midpoint of a wall over merely being on its line', () => {
    const ctx = makeCtx();
    const r = resolveSnap(ctx, { x: 6000, y: 2000 });
    expect(r.candidate?.kind).toBe('midpoint');
    expect(r.candidate?.refId).toBe(FIXTURE_IDS.wallEast);
    expect(r.pointMm).toEqual({ x: 6000, y: 2000 });
  });

  it('falls back to the 115 mm module with nothing else in reach', () => {
    const ctx = makeCtx();
    const r = resolveSnap(ctx, { x: 1000, y: 1000 });
    expect(r.candidate).toBeNull();
    expect(r.pointMm).toEqual({ x: 1035, y: 1035 });
  });

  it('uses the grid alone when object snap is switched off', () => {
    const ctx = makeCtx();
    const r = resolveSnap(ctx, { x: 6000, y: 2000 }, { objectSnap: false });
    expect(r.candidate).toBeNull();
    // 6000 and 2000 are not multiples of 115, so the grid genuinely moved them.
    expect(r.pointMm).toEqual({ x: 5980, y: 1955 });
  });

  it('snaps only the free axis under ortho, so a chain does not drift onto the grid', () => {
    const ctx = makeCtx();
    const anchor = { x: 1150, y: 1150 };
    const r = resolveSnap(ctx, { x: 4600, y: 1500 }, { anchor, ortho: true });
    expect(r.pointMm).toEqual({ x: 4600, y: 1150 });
    expect(r.candidate).toBeNull();
  });

  it('locks the other axis when the pointer moved further in Y', () => {
    const ctx = makeCtx();
    const anchor = { x: 1150, y: 1150 };
    const r = resolveSnap(ctx, { x: 1200, y: 3450 }, { anchor, ortho: true });
    expect(r.pointMm).toEqual({ x: 1150, y: 3450 });
  });

  it('keeps the ortho constraint and reports an off-axis snap as an alignment', () => {
    const ctx = makeCtx();
    const anchor = { x: 1150, y: 1150 };
    const r = resolveSnap(ctx, { x: 6000, y: 2000 }, { anchor, ortho: true });
    expect(r.candidate?.kind).toBe('extension');
    expect(r.candidate?.label).toBe('Aligned with wall middle');
    // On the anchor's axis, at the snap's X — aligned with the feature, not on it.
    expect(r.pointMm).toEqual({ x: 6000, y: 1150 });
    expect(r.candidate?.pointMm).toEqual({ x: 6000, y: 1150 });
  });

  it('keeps the snap itself when it already sits on the ortho axis', () => {
    const ctx = makeCtx();
    const r = resolveSnap(ctx, { x: 6000, y: 2000 }, { anchor: { x: 6000, y: 1150 }, ortho: true });
    expect(r.candidate?.kind).toBe('midpoint');
    expect(r.pointMm).toEqual({ x: 6000, y: 2000 });
  });

  it('ignores ortho without an anchor', () => {
    const ctx = makeCtx();
    const r = resolveSnap(ctx, { x: 1000, y: 1000 }, { ortho: true });
    expect(r.pointMm).toEqual({ x: 1035, y: 1035 });
  });

  it('excludes the walls it was told to, and drops to the grid', () => {
    const ctx = makeCtx();
    const r = resolveSnap(
      ctx,
      { x: 6000, y: 4000 },
      {
        excludeIds: new Set([FIXTURE_IDS.wallEast, FIXTURE_IDS.wallNorth]),
      },
    );
    expect(r.candidate).toBeNull();
    expect(r.pointMm).toEqual({ x: 5980, y: 4025 });
  });

  it('always returns integer millimetres, grid on or off', () => {
    const ctx = makeCtx();
    const fine = makeCtx({ snapModuleMm: 25 });
    const off = makeCtx({ snapModuleMm: 0 });
    for (const c of [ctx, fine, off]) {
      for (const raw of [
        { x: 0, y: 0 },
        { x: 1013, y: 2027 },
        { x: -517, y: 8123 },
        { x: 6000, y: 2000 },
      ]) {
        const r = resolveSnap(c, raw);
        expect(Number.isInteger(r.pointMm.x)).toBe(true);
        expect(Number.isInteger(r.pointMm.y)).toBe(true);
      }
    }
  });
});

describe('toSnapView', () => {
  it('passes null through, so the overlay draws no marker', () => {
    expect(toSnapView(null)).toBeNull();
  });

  it('carries the label and the reference the marker needs', () => {
    const ctx = makeCtx();
    const view = toSnapView(resolveSnap(ctx, { x: 6000, y: 2000 }).candidate);
    expect(view).toEqual({
      kind: 'midpoint',
      label: 'Wall middle',
      pointMm: { x: 6000, y: 2000 },
      refId: FIXTURE_IDS.wallEast,
    });
  });
});
