/**
 * Spec for M — the one tool that emits nothing.
 *
 * The assertion that matters most here is negative: `commit()` returns null in
 * every state, so measuring never appends an op, never creates an undo entry,
 * and never appears in the version timeline. A measurement showing up in a
 * project's history would be noise in the one place it has to stay readable.
 *
 * Everything else about the tool is ordinary: it snaps like a wall (because
 * "how far is this wall from that one" is only useful measured wall-to-wall),
 * and it chains.
 */

import { describe, expect, it } from 'vitest';

import { HINTS } from './constants';
import { MeasureTool } from './measureTool';
import { FIXTURE_IDS, key, makeCtx, ptr, readout, typeText } from './toolTestKit';

describe('it never changes the document', () => {
  it('has nothing to commit while idle', () => {
    expect(new MeasureTool().commit(makeCtx())).toBeNull();
  });

  it('has nothing to commit after one point', () => {
    const ctx = makeCtx();
    const tool = new MeasureTool();
    tool.onPointerDown(ctx, ptr(1150, 1150));
    expect(tool.commit(ctx)).toBeNull();
  });

  it('has nothing to commit after a full chain', () => {
    const ctx = makeCtx();
    const tool = new MeasureTool();
    tool.onPointerDown(ctx, ptr(1150, 1150));
    tool.onPointerDown(ctx, ptr(4600, 1150));
    tool.onPointerDown(ctx, ptr(4600, 3450));
    expect(tool.commit(ctx)).toBeNull();
  });

  it('returns no commit from any pointer or key verb', () => {
    const ctx = makeCtx();
    const tool = new MeasureTool();
    const responses = [
      tool.onPointerDown(ctx, ptr(1150, 1150)),
      tool.onPointerMove(ctx, ptr(4600, 1150)),
      tool.onPointerDown(ctx, ptr(4600, 1150)),
      tool.onPointerUp(ctx, ptr(4600, 1150)),
      tool.onKey(ctx, key('Enter')),
      tool.onKey(ctx, key('Escape')),
    ];
    for (const response of responses) expect(response.commit ?? null).toBeNull();
  });
});

describe('phases', () => {
  it('starts idle', () => {
    const tool = new MeasureTool();
    expect(tool.phase).toBe('idle');
    expect(tool.preview(makeCtx()).hint).toBe(HINTS.measureIdle);
  });

  it('a click starts a measurement', () => {
    const ctx = makeCtx();
    const tool = new MeasureTool();
    tool.onPointerDown(ctx, ptr(1150, 1150));
    expect(tool.phase).toBe('drawing');
    expect(tool.preview(ctx).hint).toBe(HINTS.measureDrawing);
  });

  it('Enter ends the measurement and clears it', () => {
    const ctx = makeCtx();
    const tool = new MeasureTool();
    tool.onPointerDown(ctx, ptr(1150, 1150));
    tool.onPointerDown(ctx, ptr(4600, 1150));

    const response = tool.onKey(ctx, key('Enter'));
    expect(response.handled).toBe(true);
    expect(tool.phase).toBe('idle');
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'measure' && shape.points).toHaveLength(0);
  });

  it('Esc ends it too', () => {
    const ctx = makeCtx();
    const tool = new MeasureTool();
    tool.onPointerDown(ctx, ptr(1150, 1150));
    expect(tool.onKey(ctx, key('Escape')).handled).toBe(true);
    expect(tool.phase).toBe('idle');
  });

  it('works without a storey — measuring reads nothing it could break', () => {
    const ctx = makeCtx({ storeyId: null });
    const tool = new MeasureTool();
    expect(tool.onPointerDown(ctx, ptr(1150, 1150)).handled).toBe(true);
    expect(tool.phase).toBe('drawing');
  });
});

describe('chaining', () => {
  /** Two clicked legs and a live third: the state an architect is actually in. */
  function chained() {
    const ctx = makeCtx();
    const tool = new MeasureTool();
    tool.onPointerDown(ctx, ptr(1150, 1150));
    tool.onPointerDown(ctx, ptr(4600, 1150));
    tool.onPointerMove(ctx, ptr(4600, 3450));
    return { tool, ctx };
  }

  it('accumulates legs and totals them, rubber band included', () => {
    const { tool, ctx } = chained();
    const shape = tool.preview(ctx).shape;
    expect(shape.kind).toBe('measure');
    if (shape.kind !== 'measure') return;
    expect(shape.points).toEqual([
      { x: 1150, y: 1150 },
      { x: 4600, y: 1150 },
    ]);
    expect(shape.rubber).toEqual({ x: 4600, y: 3450 });
    expect(shape.segmentsMm).toEqual([3450, 2300]);
    expect(shape.totalMm).toBe(5750);
  });

  it('shows the leg, its angle, the deltas and the running total', () => {
    const { tool, ctx } = chained();
    const preview = tool.preview(ctx);
    expect(readout(preview, 'leg')).toContain('2,300 mm');
    expect(readout(preview, 'angle')).toBe('90°');
    expect(readout(preview, 'dxdy')).toBe('0 , 2,300 mm');
    expect(readout(preview, 'total')).toContain('5,750 mm');
  });

  it('Backspace drops the last leg', () => {
    const ctx = makeCtx();
    const tool = new MeasureTool();
    tool.onPointerDown(ctx, ptr(1150, 1150));
    tool.onPointerDown(ctx, ptr(4600, 1150));
    tool.onKey(ctx, key('Backspace'));
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'measure' && shape.points).toHaveLength(1);
    expect(tool.phase).toBe('drawing');
  });
});

describe('snapping', () => {
  it('snaps to a wall endpoint, so the number means what it says', () => {
    const ctx = makeCtx();
    const tool = new MeasureTool();
    tool.onPointerDown(ctx, ptr(6, 6));
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'measure' && shape.points[0]).toEqual({ x: 0, y: 0 });
    expect(tool.preview(ctx).snap?.refId).toBeTruthy();
  });

  it('measures a diagonal freely — ortho is opt-in here, not the default', () => {
    const ctx = makeCtx();
    const tool = new MeasureTool();
    tool.onPointerDown(ctx, ptr(1150, 1150));
    tool.onPointerMove(ctx, ptr(3450, 2300));
    expect(tool.preview(ctx).cursorMm).toEqual({ x: 3450, y: 2300 });
  });

  it('Shift turns the ortho constraint on', () => {
    const ctx = makeCtx();
    const tool = new MeasureTool();
    tool.onPointerDown(ctx, ptr(1150, 1150));
    tool.onPointerMove(ctx, ptr(3450, 2300, { shiftKey: true }));
    expect(tool.preview(ctx).cursorMm).toEqual({ x: 3450, y: 1150 });
  });

  it('measures the fixture wall corner to corner exactly, not approximately', () => {
    const ctx = makeCtx();
    const tool = new MeasureTool();
    tool.onPointerDown(ctx, ptr(2, 2)); // near wallSouth.a
    tool.onPointerMove(ctx, ptr(5998, 2)); // near wallSouth.b
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'measure' && shape.segmentsMm).toEqual([6000]);
    expect(ctx.doc.house.walls.find((w) => w.id === FIXTURE_IDS.wallSouth)?.b).toEqual({
      x: 6000,
      y: 0,
    });
  });
});

describe('numeric entry overrides the mouse (§12)', () => {
  it('a typed leg length sets the point exactly', () => {
    const ctx = makeCtx();
    const tool = new MeasureTool();
    tool.onPointerDown(ctx, ptr(1150, 1150));
    tool.onPointerMove(ctx, ptr(4600, 1150));
    typeText(tool, ctx, '1234');
    expect(tool.preview(ctx).cursorMm).toEqual({ x: 2384, y: 1150 });
  });

  it('claims digits only while measuring', () => {
    const tool = new MeasureTool();
    expect(tool.wantsKey(key('1'))).toBe(false);
    const ctx = makeCtx();
    tool.onPointerDown(ctx, ptr(1150, 1150));
    expect(tool.wantsKey(key('1'))).toBe(true);
  });
});
