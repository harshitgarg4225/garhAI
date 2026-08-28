/**
 * Spec for the measure state machine.
 *
 * THE ASSERTION THIS FILE EXISTS FOR is the first one: a measurement point is
 * resolved by `canvas/tools/snapping.resolveSnap` — the drawing tools' own
 * snapper — and therefore lands EXACTLY on the wall the wall tool would have
 * landed on. A measure tool that snapped to the grid while the wall tool
 * snapped to endpoints would report a number the building does not have, and it
 * would be wrong by a plausible amount rather than an obvious one.
 *
 * Everything else here is the escape ladder and the refusals: Esc discards,
 * Backspace steps back, a degenerate region is refused with a sentence instead
 * of being committed as 0 m².
 *
 * The world is `toolTestKit`'s two-room plan — the same fixture every tool spec
 * uses — so a coordinate here means what it means in `wallTool.test.ts`:
 *
 *   (0,4000) +-----------+-----------+ (6000,4000)
 *            |           |           |
 *      (0,0) +-----------+-----------+ (6000,0)
 *                    x = 3000
 */

import { describe, expect, it } from 'vitest';

import { FIXTURE_IDS, key, makeCtx, ptr } from '../canvas/tools/toolTestKit';
import { resolveSnap, snapToleranceMm } from '../canvas/tools/snapping';
import { measureBlockReason, MeasureSession } from './session';
import { ringAreaMm2, totalLengthMm } from './geometry';
import { MEASURE_ID_PREFIX, type Measurement } from './types';

/** A session with deterministic ids and a frozen clock. */
function makeSession(kind: 'distance' | 'angle' | 'area' = 'distance'): MeasureSession {
  let n = 0;
  return new MeasureSession({
    kind,
    newId: () => {
      n += 1;
      return `${MEASURE_ID_PREFIX}T${String(n)}`;
    },
    now: () => 1_700_000_000_000,
  });
}

describe('snapping is the drawing tools’ snapping', () => {
  it('lands a click near a wall corner EXACTLY on the corner', () => {
    const ctx = makeCtx();
    const session = makeSession();
    // 8.9 mm from the south-east corner: inside the 12 px × 1 mm/px tolerance.
    session.pointerDown(ctx, ptr(6008, 4));
    const draft = session.draft();
    expect(draft?.points).toEqual([{ x: 6000, y: 0 }]);
  });

  it('agrees with `resolveSnap` on a case where the ranking decides', () => {
    // Near the MIDDLE of the east wall: a midpoint candidate (rank 80) beats
    // the wall-line candidate (60) that is nearer. Nothing about that answer is
    // guessable from the coordinates, which is why it is the case worth pinning
    // — a private copy of the snapper would not reproduce it.
    const ctx = makeCtx();
    const raw = { x: 6005, y: 2004 };
    const expected = resolveSnap(ctx, raw, { anchor: null, ortho: false });
    expect(expected.candidate?.kind).toBe('midpoint');

    const session = makeSession();
    session.pointerDown(ctx, ptr(raw.x, raw.y));
    expect(session.draft()?.points).toEqual([expected.pointMm]);
    expect(session.snapView()?.kind).toBe('midpoint');
  });

  it('falls back to the grid module away from any geometry (the control)', () => {
    // If this ever equals the raw pointer, the session has stopped snapping at
    // all and the test above would still pass by luck of being near a wall.
    const ctx = makeCtx();
    const session = makeSession();
    session.pointerDown(ctx, ptr(5000, 5000));
    expect(session.draft()?.points).toEqual([{ x: 4945, y: 4945 }]); // 43 × 115 mm
    expect(session.snapView()).toBeNull();
  });

  it('constrains to the anchor’s axis while Shift is held, and not otherwise', () => {
    const ctx = makeCtx();
    const ortho = makeSession();
    ortho.pointerDown(ctx, ptr(0, 0));
    ortho.pointerDown(ctx, ptr(5000, 300, { shiftKey: true }));
    expect(ortho.draft()?.points[1]).toEqual({ x: 4945, y: 0 });

    const free = makeSession();
    free.pointerDown(ctx, ptr(0, 0));
    free.pointerDown(ctx, ptr(5000, 300));
    // Ortho is opt-IN when measuring: a diagonal is a perfectly good thing to
    // measure, so without Shift the y coordinate survives.
    expect(free.draft()?.points[1]).toEqual({ x: 4945, y: 345 });
  });

  it('uses the same tolerance for closing a ring as for snapping to it', () => {
    const ctx = makeCtx({ mmPerPx: 4 });
    expect(snapToleranceMm(ctx.mmPerPx)).toBe(48);
    const session = makeSession('area');
    session.pointerDown(ctx, ptr(0, 0));
    session.pointerDown(ctx, ptr(6000, 0));
    session.pointerDown(ctx, ptr(6000, 4000));
    // 40 mm from the first corner — outside the 12 mm tolerance of a 1:1 zoom,
    // inside the 48 mm of this one. Zoomed out, the click that closes has to be
    // as forgiving as the click that started.
    const response = session.pointerDown(ctx, ptr(40, 0));
    expect(response.committed?.points).toHaveLength(3);
  });
});

describe('distance — two points and chains', () => {
  it('measures a 3-4-5 triangle’s hypotenuse as 5000 mm', () => {
    const ctx = makeCtx();
    const session = makeSession();
    session.pointerDown(ctx, ptr(0, 0));
    session.pointerDown(ctx, ptr(3000, 4000));
    const response = session.key(ctx, key('Enter'));
    const m = response.committed;
    expect(m).not.toBeNull();
    expect(m?.points).toEqual([
      { x: 0, y: 0 },
      { x: 3000, y: 4000 },
    ]);
    expect(totalLengthMm(m?.points ?? [])).toBe(5000);
  });

  it('chains, and totals the legs', () => {
    const ctx = makeCtx();
    const session = makeSession();
    session.pointerDown(ctx, ptr(0, 0));
    session.pointerDown(ctx, ptr(3000, 4000));
    session.pointerDown(ctx, ptr(6000, 0));
    const readouts = session.readouts(ctx);
    expect(readouts.find((r) => r.id === 'length')?.label).toBe('Total (2 legs)');
    const m = session.key(ctx, key('Enter')).committed;
    expect(m?.points).toHaveLength(3);
    expect(totalLengthMm(m?.points ?? [])).toBe(10_000);
  });

  it('finishes on a double-click without appending the second press', () => {
    // A double-click is two pointerdowns at the same pixel. Appending the second
    // would leave a zero-length leg in the committed chain.
    const ctx = makeCtx();
    const session = makeSession();
    session.pointerDown(ctx, ptr(0, 0));
    session.pointerDown(ctx, ptr(3000, 4000));
    session.pointerDown(ctx, ptr(3000, 4000));
    const m = session.doubleClick(ctx).committed;
    expect(m?.points).toHaveLength(2);
  });

  it('carries the storey and a prefixed id', () => {
    const ctx = makeCtx();
    const session = makeSession();
    session.pointerDown(ctx, ptr(0, 0));
    session.pointerDown(ctx, ptr(3000, 0));
    const m = session.key(ctx, key('Enter')).committed as Measurement;
    expect(m.storeyId).toBe(FIXTURE_IDS.groundStorey);
    expect(m.id.startsWith(MEASURE_ID_PREFIX)).toBe(true);
    expect(m.kind).toBe('distance');
    expect(m.createdAt).toBe(1_700_000_000_000);
  });

  it('reports the rubber band before any commit', () => {
    const ctx = makeCtx();
    const session = makeSession();
    session.pointerDown(ctx, ptr(0, 0));
    session.pointerMove(ctx, ptr(3000, 4000));
    // The live number IS the number that will persist — one function, one path.
    expect(session.readouts(ctx).find((r) => r.id === 'length')?.value).toContain('5,000 mm');
  });
});

describe('angle — three points, committed on the third', () => {
  it('reads the middle click as the corner', () => {
    const ctx = makeCtx();
    const session = makeSession('angle');
    session.pointerDown(ctx, ptr(0, 4000)); // first arm
    session.pointerDown(ctx, ptr(0, 0)); // THE CORNER
    const response = session.pointerDown(ctx, ptr(6000, 0)); // second arm
    const m = response.committed;
    expect(m?.kind).toBe('angle');
    expect(m?.points).toEqual([
      { x: 0, y: 4000 },
      { x: 0, y: 0 },
      { x: 6000, y: 0 },
    ]);
    expect(session.draft()).toBeNull(); // committed, so the draft is gone
  });

  it('does not wait for an Enter it could only interpret one way', () => {
    const ctx = makeCtx();
    const session = makeSession('angle');
    session.pointerDown(ctx, ptr(0, 4000));
    session.pointerDown(ctx, ptr(0, 0));
    expect(session.pointerDown(ctx, ptr(6000, 0)).committed).not.toBeNull();
  });
});

describe('area — a closed region', () => {
  const ctx = makeCtx();

  it('closes on a click at the first corner and stores an OPEN ring', () => {
    const session = makeSession('area');
    session.pointerDown(ctx, ptr(0, 0));
    session.pointerDown(ctx, ptr(6000, 0));
    session.pointerDown(ctx, ptr(6000, 4000));
    session.pointerDown(ctx, ptr(0, 4000));
    const m = session.pointerDown(ctx, ptr(4, 4)).committed;
    // Four corners, NOT five: the closing edge is implied, and a duplicated
    // first vertex would add a zero-length edge to the perimeter.
    expect(m?.points).toHaveLength(4);
    expect(ringAreaMm2(m?.points ?? [])).toBe(24_000_000); // 6 m × 4 m
  });

  it('closes on Enter as well', () => {
    const session = makeSession('area');
    session.pointerDown(ctx, ptr(0, 0));
    session.pointerDown(ctx, ptr(6000, 0));
    session.pointerDown(ctx, ptr(6000, 4000));
    const m = session.key(ctx, key('Enter')).committed;
    expect(ringAreaMm2(m?.points ?? [])).toBe(12_000_000);
  });

  it('flags the closing click before it happens, for the cursor to show', () => {
    const session = makeSession('area');
    session.pointerDown(ctx, ptr(0, 0));
    session.pointerDown(ctx, ptr(6000, 0));
    session.pointerDown(ctx, ptr(6000, 4000));
    session.pointerMove(ctx, ptr(3000, 3000));
    expect(session.draft()?.willClose).toBe(false);
    session.pointerMove(ctx, ptr(6, 6));
    expect(session.draft()?.willClose).toBe(true);
  });
});

describe('the escape ladder', () => {
  const ctx = makeCtx();

  it('Esc discards without committing', () => {
    const session = makeSession();
    session.pointerDown(ctx, ptr(0, 0));
    session.pointerDown(ctx, ptr(3000, 0));
    const response = session.key(ctx, key('Escape'));
    expect(response.handled).toBe(true);
    expect(response.committed).toBeNull();
    expect(session.draft()).toBeNull();
  });

  it('Backspace steps back one point', () => {
    const session = makeSession();
    session.pointerDown(ctx, ptr(0, 0));
    session.pointerDown(ctx, ptr(3000, 0));
    session.pointerDown(ctx, ptr(6000, 0));
    session.key(ctx, key('Backspace'));
    expect(session.draft()?.points).toHaveLength(2);
  });

  it('lets an unhandled key through — an idle Esc still belongs to the page', () => {
    const session = makeSession();
    expect(session.key(ctx, key('Escape')).handled).toBe(false);
    expect(session.key(ctx, key('Enter')).handled).toBe(false);
    expect(session.key(ctx, key('a')).handled).toBe(false);
  });

  it('changing what is measured discards a half-finished draft', () => {
    const session = makeSession();
    session.pointerDown(ctx, ptr(0, 0));
    session.pointerDown(ctx, ptr(3000, 0));
    session.setKind('area');
    // Two points meant as a distance are not two corners of a region.
    expect(session.draft()).toBeNull();
    expect(session.kind).toBe('area');
  });

  it('ignores a non-primary button', () => {
    const session = makeSession();
    expect(session.pointerDown(ctx, ptr(0, 0, { button: 2 })).handled).toBe(false);
    expect(session.draft()).toBeNull();
  });
});

describe('refusals — a wrong number is worse than no number', () => {
  const ctx = makeCtx();

  it('will not commit a one-point distance', () => {
    const session = makeSession();
    session.pointerDown(ctx, ptr(0, 0));
    const response = session.key(ctx, key('Enter'));
    expect(response.committed).toBeNull();
    expect(response.blocked).toBe('A distance needs two points.');
  });

  it('will not commit a straight-line “region” as 0 m²', () => {
    const session = makeSession('area');
    // Three collinear points: all snap to y = 4945 on the 115 mm module.
    session.pointerDown(ctx, ptr(5000, 5000));
    session.pointerDown(ctx, ptr(6000, 5000));
    session.pointerDown(ctx, ptr(7000, 5000));
    const response = session.key(ctx, key('Enter'));
    expect(response.committed).toBeNull();
    expect(response.blocked).toBe('Those corners are in a straight line — no area.');
  });

  it('states the same reasons the panel’s Finish button reads', () => {
    expect(measureBlockReason('distance', [{ x: 0, y: 0 }])).toContain('two points');
    expect(
      measureBlockReason('distance', [
        { x: 0, y: 0 },
        { x: 0, y: 0 },
      ]),
    ).toContain('same point');
    expect(measureBlockReason('angle', [{ x: 0, y: 0 }])).toContain('three points');
    expect(
      measureBlockReason('area', [
        { x: 0, y: 0 },
        { x: 1000, y: 0 },
      ]),
    ).toContain('three corners');
    expect(
      measureBlockReason('distance', [
        { x: 0, y: 0 },
        { x: 3000, y: 4000 },
      ]),
    ).toBeNull();
  });
});
