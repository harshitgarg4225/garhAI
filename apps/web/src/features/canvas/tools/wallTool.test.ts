/**
 * Spec for W — the wall tool.
 *
 * The state machine §12 asks for:
 *
 *   idle ──click──▶ drawing ──click──▶ drawing ──Enter──▶ commit(ONE group) ──▶ idle
 *
 * plus the three guarantees: Esc cancels, Enter commits, and typing a number
 * overrides the mouse. The last one is the reason half this file exists — the
 * assertion that matters is that a typed length lands on a coordinate the
 * 115 mm grid could never have produced, which is the only way to prove the
 * keyboard actually beat the snap rather than agreeing with it by luck.
 */

import { describe, expect, it } from 'vitest';

import { distMm } from '@garh/model';

import { HINTS, MIN_WALL_LENGTH_MM } from './constants';
import {
  FIXTURE_IDS,
  key,
  makeCtx,
  nthId,
  opOfType,
  opsOfType,
  ptr,
  ptrOffPlane,
  readout,
  typeText,
} from './toolTestKit';
import { WallTool } from './wallTool';

/** Start a chain at (1150, 1150) — on the module, clear of every fixture wall. */
function started(): { tool: WallTool; ctx: ReturnType<typeof makeCtx> } {
  const ctx = makeCtx();
  const tool = new WallTool();
  tool.onPointerDown(ctx, ptr(1150, 1150));
  return { tool, ctx };
}

describe('phases', () => {
  it('starts idle and draws nothing', () => {
    const tool = new WallTool();
    const ctx = makeCtx();
    expect(tool.phase).toBe('idle');
    const preview = tool.preview(ctx);
    expect(preview.shape).toEqual({ kind: 'wall-chain', segments: [], rubber: null });
    expect(preview.hint).toBe(HINTS.wallIdle);
  });

  it('a click moves it to drawing and anchors the chain', () => {
    const { tool, ctx } = started();
    expect(tool.phase).toBe('drawing');
    expect(tool.preview(ctx).hint).toBe(HINTS.wallDrawing);
    expect(tool.preview(ctx).cursorMm).toEqual({ x: 1150, y: 1150 });
  });

  it('ignores a right-click', () => {
    const tool = new WallTool();
    const ctx = makeCtx();
    expect(tool.onPointerDown(ctx, ptr(1150, 1150, { button: 2 })).handled).toBe(false);
    expect(tool.phase).toBe('idle');
  });

  it('ignores a pointer whose ray never reached the plane', () => {
    const tool = new WallTool();
    const ctx = makeCtx();
    expect(tool.onPointerDown(ctx, ptrOffPlane()).handled).toBe(false);
    expect(tool.phase).toBe('idle');
  });

  it('refuses to start without a storey, and says so', () => {
    const tool = new WallTool();
    const ctx = makeCtx({ storeyId: null });
    expect(tool.onPointerDown(ctx, ptr(1150, 1150)).handled).toBe(false);
    expect(tool.phase).toBe('idle');
    expect(tool.preview(ctx).hint).toBe(HINTS.noStorey);
  });

  it('Esc from idle is declined, so a dialog above can still close', () => {
    const tool = new WallTool();
    expect(tool.onKey(makeCtx(), key('Escape')).handled).toBe(false);
  });
});

describe('ortho and snapping while drawing', () => {
  it('locks the rubber band to the anchor axis', () => {
    const { tool, ctx } = started();
    tool.onPointerMove(ctx, ptr(4600, 1500));
    expect(tool.preview(ctx).cursorMm).toEqual({ x: 4600, y: 1150 });
  });

  it('Shift inverts the ortho setting while it is held', () => {
    const { tool, ctx } = started();
    tool.onPointerMove(ctx, ptr(4600, 1500, { shiftKey: true }));
    // Free angle: both axes land on the 115 mm module instead.
    expect(tool.preview(ctx).cursorMm).toEqual({ x: 4600, y: 1495 });
  });

  it('snaps to an existing wall endpoint in preference to the grid', () => {
    const ctx = makeCtx();
    const tool = new WallTool();
    tool.onPointerDown(ctx, ptr(4, 4));
    // (0,0) is the corner where wallSouth and wallWest meet — and the plot
    // corner. The endpoint wins, and it is exact, not rounded to a module.
    expect(tool.preview(ctx).cursorMm).toEqual({ x: 0, y: 0 });
    expect(tool.preview(ctx).snap?.kind).toBe('endpoint');
  });

  it('reports the live length, angle and thickness', () => {
    const { tool, ctx } = started();
    tool.onPointerMove(ctx, ptr(4600, 1500));
    const preview = tool.preview(ctx);
    expect(readout(preview, 'angle')).toBe('0°');
    expect(readout(preview, 'thickness')).toBe('230 mm');
    expect(readout(preview, 'length')).not.toBeNull();
  });
});

describe('the chain is ONE undo group', () => {
  it('emits nothing on the intermediate clicks', () => {
    const { tool, ctx } = started();
    const second = tool.onPointerDown(ctx, ptr(4600, 1500));
    expect(second.commit ?? null).toBeNull();
    const third = tool.onPointerDown(ctx, ptr(4700, 3450));
    expect(third.commit ?? null).toBeNull();
  });

  it('commits every segment together on Enter, then returns to idle', () => {
    const { tool, ctx } = started();
    tool.onPointerDown(ctx, ptr(4600, 1500));
    tool.onPointerDown(ctx, ptr(4700, 3450));

    const response = tool.onKey(ctx, key('Enter'));
    expect(response.handled).toBe(true);
    const commit = response.commit;
    expect(commit).toBeTruthy();
    if (!commit) return;

    const ops = opsOfType(commit.ops, 'wall.add');
    expect(ops).toHaveLength(2);
    expect(ops[0]?.payload.a).toEqual({ x: 1150, y: 1150 });
    expect(ops[0]?.payload.b).toEqual({ x: 4600, y: 1150 });
    expect(ops[1]?.payload.a).toEqual({ x: 4600, y: 1150 });
    expect(ops[1]?.payload.b).toEqual({ x: 4600, y: 3450 });
    expect(commit.label).toBe('2 walls drawn');
    expect(commit.selectIds).toEqual([nthId('wall', 1), nthId('wall', 2)]);
    expect(tool.phase).toBe('idle');
  });

  it('names a single segment in the singular, for the undo toast', () => {
    const { tool, ctx } = started();
    tool.onPointerDown(ctx, ptr(4600, 1500));
    expect(tool.onKey(ctx, key('Enter')).commit?.label).toBe('Wall drawn');
  });

  it('takes the thickness and kind from the tool settings, never from a guess', () => {
    const ctx = makeCtx({
      settings: { wallThicknessMm: 115, wallKind: 'internal', wallLoadBearing: true },
    });
    const tool = new WallTool();
    tool.onPointerDown(ctx, ptr(1150, 1150));
    tool.onPointerDown(ctx, ptr(4600, 1500));
    const op = opOfType(tool.onKey(ctx, key('Enter')).commit?.ops[0], 'wall.add');
    expect(op.payload.thicknessMm).toBe(115);
    expect(op.payload.kind).toBe('internal');
    expect(op.payload.loadBearing).toBe(true);
    expect(op.payload.storeyId).toBe(FIXTURE_IDS.groundStorey);
  });

  it('has nothing to commit from a single point', () => {
    const { tool, ctx } = started();
    expect(tool.commit(ctx)).toBeNull();
    expect(tool.onKey(ctx, key('Enter')).handled).toBe(false);
  });

  it('treats a second click in the same spot as "finish"', () => {
    const { tool, ctx } = started();
    tool.onPointerDown(ctx, ptr(4600, 1500));
    const response = tool.onPointerDown(ctx, ptr(4600, 1150));
    expect(response.commit?.ops).toHaveLength(1);
    expect(tool.phase).toBe('idle');
  });
});

describe('numeric entry overrides the mouse (§12)', () => {
  it('does not claim digits while idle — 3 is still the second floor', () => {
    const tool = new WallTool();
    expect(tool.wantsKey(key('3'))).toBe(false);
    expect(tool.wantsKey(key('m'))).toBe(false);
  });

  it('claims digits the moment a wall is being drawn', () => {
    const { tool } = started();
    expect(tool.wantsKey(key('3'))).toBe(true);
    expect(tool.wantsKey(key("'"))).toBe(true);
    // …but never a modified one, so ⌘Z stays undo mid-chain.
    expect(tool.wantsKey(key('z', { metaKey: true }))).toBe(false);
  });

  it('a typed 3600 lands the segment exactly 3600 mm away, off the module', () => {
    const { tool, ctx } = started();
    tool.onPointerMove(ctx, ptr(4600, 1500));

    const responses = typeText(tool, ctx, '3600');
    expect(responses.every((r) => r.handled)).toBe(true);
    expect(tool.preview(ctx).entry?.value).toBe(3600);

    // First Enter places the typed segment and keeps drawing (the CAD idiom).
    const placing = tool.onKey(ctx, key('Enter'));
    expect(placing.commit ?? null).toBeNull();
    expect(tool.phase).toBe('drawing');

    const op = opOfType(tool.onKey(ctx, key('Enter')).commit?.ops[0], 'wall.add');
    expect(op.payload.b).toEqual({ x: 4750, y: 1150 });
    expect(distMm(op.payload.a, op.payload.b)).toBe(3600);
    // 4750 is not a multiple of 115: the keyboard beat the grid, which is the
    // whole point of the requirement.
    expect(4750 % 115).not.toBe(0);
  });

  it("a typed 12' is read as feet, not millimetres", () => {
    const { tool, ctx } = started();
    tool.onPointerMove(ctx, ptr(4600, 1500));
    typeText(tool, ctx, "12'");
    tool.onKey(ctx, key('Enter'));
    const op = opOfType(tool.onKey(ctx, key('Enter')).commit?.ops[0], 'wall.add');
    expect(distMm(op.payload.a, op.payload.b)).toBe(3658);
  });

  it('keeps the direction the mouse chose and takes only the distance', () => {
    const { tool, ctx } = started();
    tool.onPointerMove(ctx, ptr(1200, 3450)); // northwards, ortho-locked to X
    typeText(tool, ctx, '2400');
    // Still due north of the anchor, now exactly 2400 mm along it — and 3550 is
    // not on the module, so this is the typed value and not the snap.
    expect(tool.preview(ctx).cursorMm).toEqual({ x: 1150, y: 3550 });
    expect(3550 % 115).not.toBe(0);
  });

  it('clears the buffer between segments, so the next one is free again', () => {
    const { tool, ctx } = started();
    tool.onPointerMove(ctx, ptr(4600, 1500));
    typeText(tool, ctx, '3600');
    tool.onKey(ctx, key('Enter'));
    expect(tool.preview(ctx).entry).toBeNull();
  });
});

describe('the escape ladder', () => {
  it('Esc with a buffer clears the number and keeps the chain', () => {
    const { tool, ctx } = started();
    tool.onPointerDown(ctx, ptr(4600, 1500));
    typeText(tool, ctx, '36');
    expect(tool.preview(ctx).entry?.buffer).toBe('36');

    const response = tool.onKey(ctx, key('Escape'));
    expect(response.handled).toBe(true);
    expect(tool.phase).toBe('drawing');
    expect(tool.preview(ctx).entry).toBeNull();
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'wall-chain' && shape.segments).toHaveLength(1);
  });

  it('Esc with no buffer throws the whole chain away, emitting nothing', () => {
    const { tool, ctx } = started();
    tool.onPointerDown(ctx, ptr(4600, 1500));
    const response = tool.onKey(ctx, key('Escape'));
    expect(response.handled).toBe(true);
    expect(response.commit ?? null).toBeNull();
    expect(tool.phase).toBe('idle');
    expect(tool.preview(ctx).shape).toEqual({ kind: 'wall-chain', segments: [], rubber: null });
  });

  it('Backspace drops the last segment rather than the whole chain', () => {
    const { tool, ctx } = started();
    tool.onPointerDown(ctx, ptr(4600, 1500));
    tool.onPointerDown(ctx, ptr(4700, 3450));

    tool.onKey(ctx, key('Backspace'));
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'wall-chain' && shape.segments).toHaveLength(1);
    expect(tool.phase).toBe('drawing');
  });

  it('Backspace on the first point cancels, because there is nothing left', () => {
    const { tool, ctx } = started();
    tool.onKey(ctx, key('Backspace'));
    expect(tool.phase).toBe('idle');
  });
});

describe('refusing what fold would reject', () => {
  it('warns while the rubber band lies along an existing wall', () => {
    const ctx = makeCtx();
    const tool = new WallTool();
    tool.onPointerDown(ctx, ptr(0, 0));
    tool.onPointerMove(ctx, ptr(3000, 0));
    expect(tool.preview(ctx).chips.map((c) => c.id)).toContain('wall-duplicate');
  });

  it('blocks the commit with the model core’s own sentence, keeping the work', () => {
    const ctx = makeCtx();
    const tool = new WallTool();
    tool.onPointerDown(ctx, ptr(0, 0));
    tool.onPointerDown(ctx, ptr(3000, 0));

    const response = tool.onKey(ctx, key('Enter'));
    expect(response.commit ?? null).toBeNull();
    expect(tool.phase).toBe('drawing'); // the chain is still on screen

    const blocked = tool.preview(ctx).blocked;
    expect(blocked?.message).toBe('There is already a wall along that line.');
    expect(blocked?.issues[0]?.code).toBe('WALL_DUPLICATE');
    expect(blocked?.fix).toBeTruthy();
  });

  it('drops a slipped sub-module click instead of sending a zero-length wall', () => {
    expect(MIN_WALL_LENGTH_MM).toBe(115);
    const { tool, ctx } = started();
    // A 50 mm nudge is never an intention; placePoint reads it as "finish".
    tool.onPointerDown(ctx, ptr(1200, 1150));
    expect(tool.commit(ctx)).toBeNull();
  });
});
