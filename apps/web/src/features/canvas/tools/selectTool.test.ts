/**
 * Spec for V — select, marquee, drag, delete.
 *
 * Two things are being pinned:
 *
 *  1. **The armed → drag threshold.** A press that never moves is a click and
 *     must change the selection; a press that moves is a transform and must
 *     not. Getting that boundary wrong makes every click feel like it nudged
 *     something, which is the classic way a canvas editor loses trust.
 *  2. **The shared commit path.** Dragging a wall and typing into its dimension
 *     label are the same edit, so both go through `editOps`. This file asserts
 *     the drag half; `editOps.test.ts` asserts `setWallLengthOps`, which is the
 *     overlay's half of the same door.
 */

import { describe, expect, it } from 'vitest';

import { fixedId, makeTwoRoomPlanWithOpenings, validateOpAgainstDoc } from '@garh/model';

import { DRAG_THRESHOLD_PX, HINTS } from './constants';
import { pointInsidePolygon, SelectTool } from './selectTool';
import { FIXTURE_IDS, hitOn, key, makeCtx, opOfType, ptr, readout, typeText } from './toolTestKit';

const SPINE = FIXTURE_IDS.wallSpine;
const GROUND = FIXTURE_IDS.groundStorey;

describe('clicking', () => {
  it('starts idle', () => {
    const tool = new SelectTool();
    const ctx = makeCtx();
    expect(tool.phase).toBe('idle');
    expect(tool.preview(ctx).shape).toEqual({ kind: 'none' });
    expect(tool.preview(ctx).hint).toBe(HINTS.selectIdle);
  });

  it('selects the wall under the pointer, and again on release', () => {
    const ctx = makeCtx();
    const tool = new SelectTool();
    const down = tool.onPointerDown(ctx, ptr(3000, 2000));
    expect(down.selection).toEqual({ mode: 'replace', ids: [SPINE] });

    const up = tool.onPointerUp(ctx, ptr(3000, 2000));
    expect(up.selection).toEqual({ mode: 'replace', ids: [SPINE] });
    expect(tool.phase).toBe('idle');
  });

  it('clears the selection when the click lands on nothing', () => {
    const ctx = makeCtx({ selectedIds: [SPINE] });
    const tool = new SelectTool();
    tool.onPointerDown(ctx, ptr(1500, 2000));
    expect(tool.onPointerUp(ctx, ptr(1500, 2000)).selection).toEqual({ mode: 'clear', ids: [] });
  });

  it('Shift-click toggles instead of replacing', () => {
    const ctx = makeCtx();
    const tool = new SelectTool();
    tool.onPointerDown(ctx, ptr(3000, 2000, { shiftKey: true }));
    expect(tool.onPointerUp(ctx, ptr(3000, 2000, { shiftKey: true })).selection).toEqual({
      mode: 'toggle',
      ids: [SPINE],
    });
  });

  it('trusts the raycast when it names something — the one hit-testing system', () => {
    const ctx = makeCtx();
    const tool = new SelectTool();
    const hit = hitOn('wall', FIXTURE_IDS.wallNorth, GROUND);
    // The pointer is nowhere near wallNorth; the pick is authoritative anyway.
    const down = tool.onPointerDown(ctx, ptr(1500, 2000, { hit }));
    expect(down.selection).toEqual({ mode: 'replace', ids: [FIXTURE_IDS.wallNorth] });
  });

  it('picks an opening over the wall that hosts it', () => {
    const ctx = makeCtx({ doc: makeTwoRoomPlanWithOpenings() });
    const tool = new SelectTool();
    // doorMain sits at offset 1500 on wallSouth, 900 wide.
    const down = tool.onPointerDown(ctx, ptr(1500, 0));
    expect(down.selection).toEqual({ mode: 'replace', ids: [FIXTURE_IDS.doorMain] });
  });

  it('does not re-select something already selected', () => {
    const ctx = makeCtx({ selectedIds: [SPINE] });
    const tool = new SelectTool();
    expect(tool.onPointerDown(ctx, ptr(3000, 2000)).selection ?? null).toBeNull();
  });
});

describe('the drag threshold', () => {
  it('a press that barely moves is still a click', () => {
    expect(DRAG_THRESHOLD_PX).toBe(4);
    const ctx = makeCtx({ selectedIds: [SPINE] });
    const tool = new SelectTool();
    tool.onPointerDown(ctx, ptr(3000, 2000));
    // 3 mm at 1 mm/px is under the 4 px threshold.
    expect(tool.onPointerMove(ctx, ptr(3003, 2000)).handled).toBe(false);
    expect(tool.phase).toBe('idle');
  });

  it('a press that moves past it becomes a drag', () => {
    const ctx = makeCtx({ selectedIds: [SPINE] });
    const tool = new SelectTool();
    tool.onPointerDown(ctx, ptr(3000, 2000));
    tool.onPointerMove(ctx, ptr(3115, 2000));
    expect(tool.phase).toBe('drawing');
    expect(tool.preview(ctx).hint).toBe(HINTS.selectDragging);
  });
});

describe('dragging a wall', () => {
  function dragging() {
    const ctx = makeCtx({ selectedIds: [SPINE] });
    const tool = new SelectTool();
    tool.onPointerDown(ctx, ptr(3000, 2000));
    tool.onPointerMove(ctx, ptr(3115, 2000)); // begins the drag
    tool.onPointerMove(ctx, ptr(3115, 2000)); // advances it
    return { tool, ctx };
  }

  it('snaps the delta, not the pointer, so the wall keeps its own geometry', () => {
    const { tool, ctx } = dragging();
    const shape = tool.preview(ctx).shape;
    expect(shape.kind).toBe('transform');
    if (shape.kind !== 'transform') return;
    expect(shape.deltaMm).toEqual({ x: 115, y: 0 });
    expect(shape.targetIds).toEqual([SPINE]);
    expect(shape.ghosts[0]?.a).toEqual({ x: 3115, y: 0 });
  });

  it('commits one wall.move the real validator accepts', () => {
    const { tool, ctx } = dragging();
    const response = tool.onPointerUp(ctx, ptr(3115, 2000));

    const op = opOfType(response.commit?.ops[0], 'wall.move');
    expect(op.payload).toEqual({
      wallId: SPINE,
      a: { x: 3115, y: 0 },
      b: { x: 3115, y: 4000 },
    });
    expect(validateOpAgainstDoc(ctx.doc, op)).toEqual([]);
    expect(response.commit?.label).toBe('Wall moved');
    expect(tool.phase).toBe('idle');
  });

  it('reports the distance and the deltas while dragging', () => {
    const { tool, ctx } = dragging();
    const preview = tool.preview(ctx);
    expect(readout(preview, 'delta')).toBe('115 , 0 mm');
    expect(readout(preview, 'distance')).not.toBeNull();
  });

  it('commits nothing when the drag ended where it started', () => {
    const ctx = makeCtx({ selectedIds: [SPINE] });
    const tool = new SelectTool();
    tool.onPointerDown(ctx, ptr(3000, 2000));
    tool.onPointerMove(ctx, ptr(3010, 2000)); // begins, delta still zero
    expect(tool.commit(ctx)).toBeNull();
  });

  it('Esc puts it back, because nothing was ever dispatched', () => {
    const { tool, ctx } = dragging();
    const response = tool.onKey(ctx, key('Escape'));
    expect(response.handled).toBe(true);
    expect(response.commit ?? null).toBeNull();
    expect(tool.phase).toBe('idle');
    expect(tool.preview(ctx).shape).toEqual({ kind: 'none' });
  });

  it('a typed distance keeps the direction and takes the length (§12)', () => {
    const { tool, ctx } = dragging();
    typeText(tool, ctx, '1000');
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'transform' && shape.deltaMm).toEqual({ x: 1000, y: 0 });

    const op = opOfType(tool.onKey(ctx, key('Enter')).commit?.ops[0], 'wall.move');
    expect(op.payload.a).toEqual({ x: 4000, y: 0 });
    expect(op.payload.b).toEqual({ x: 4000, y: 4000 });
  });
});

describe('dragging a wall endpoint', () => {
  it('is a different edit from dragging the wall, and wins the press', () => {
    const ctx = makeCtx({ selectedIds: [SPINE] });
    const tool = new SelectTool();
    tool.onPointerDown(ctx, ptr(3000, 0)); // wallSpine.a
    expect(tool.phase).toBe('drawing');

    tool.onPointerMove(ctx, ptr(2990, 460));
    const response = tool.onPointerUp(ctx, ptr(2990, 460));
    const op = opOfType(response.commit?.ops[0], 'wall.move');
    expect(op.payload.a).toEqual({ x: 2990, y: 460 });
    expect(op.payload.b).toEqual({ x: 3000, y: 4000 }); // the far end held still
    expect(response.commit?.label).toBe('Wall end moved');
  });

  it('does not snap the dragged wall to itself', () => {
    const ctx = makeCtx({ selectedIds: [SPINE] });
    const tool = new SelectTool();
    tool.onPointerDown(ctx, ptr(3000, 0));
    // (2995, 2000) is within snap range of wallSpine's OWN midpoint. Without the
    // self-exclusion the endpoint would jump onto the wall it belongs to and
    // collapse it; with it, the grid decides.
    tool.onPointerMove(ctx, ptr(2995, 2000));
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'transform' && shape.ghosts[0]?.a).toEqual({ x: 2990, y: 1955 });
    expect(tool.preview(ctx).snap).toBeNull();
  });

  it('shows the resulting length and angle before committing', () => {
    const ctx = makeCtx({ selectedIds: [SPINE] });
    const tool = new SelectTool();
    tool.onPointerDown(ctx, ptr(3000, 0));
    tool.onPointerMove(ctx, ptr(2990, 460));
    expect(readout(tool.preview(ctx), 'length')).not.toBeNull();
    expect(readout(tool.preview(ctx), 'angle')).not.toBeNull();
  });
});

describe('dragging an opening along its wall', () => {
  const doc = makeTwoRoomPlanWithOpenings();

  it('slides it and commits an opening.move', () => {
    const ctx = makeCtx({ doc, selectedIds: [FIXTURE_IDS.doorMain] });
    const tool = new SelectTool();
    tool.onPointerDown(ctx, ptr(1500, 0));
    tool.onPointerMove(ctx, ptr(2000, 0));
    tool.onPointerMove(ctx, ptr(2000, 0));

    const response = tool.onPointerUp(ctx, ptr(2000, 0));
    const op = opOfType(response.commit?.ops[0], 'opening.move');
    expect(op.payload).toEqual({ openingId: FIXTURE_IDS.doorMain, offsetMm: 2000 });
    expect(response.commit?.label).toBe('Opening moved');
    expect(validateOpAgainstDoc(doc, op)).toEqual([]);
  });

  it('clamps at the end margin instead of sliding off the wall', () => {
    const ctx = makeCtx({ doc, selectedIds: [FIXTURE_IDS.doorMain] });
    const tool = new SelectTool();
    tool.onPointerDown(ctx, ptr(1500, 0));
    tool.onPointerMove(ctx, ptr(0, 0));
    tool.onPointerMove(ctx, ptr(0, 0));
    const op = opOfType(tool.onPointerUp(ctx, ptr(0, 0)).commit?.ops[0], 'opening.move');
    // 115 + floor(900/2) = 565, the same invariant the opening tool enforces.
    expect(op.payload.offsetMm).toBe(565);
  });

  it('commits nothing when the opening did not actually move', () => {
    const ctx = makeCtx({ doc, selectedIds: [FIXTURE_IDS.doorMain] });
    const tool = new SelectTool();
    tool.onPointerDown(ctx, ptr(1500, 0));
    tool.onPointerMove(ctx, ptr(1520, 0));
    tool.onPointerMove(ctx, ptr(1500, 0));
    expect(tool.commit(ctx)).toBeNull();
  });
});

describe('marquee', () => {
  it('selects everything fully inside it', () => {
    const ctx = makeCtx();
    const tool = new SelectTool();
    tool.onPointerDown(ctx, ptr(-500, -500));
    tool.onPointerMove(ctx, ptr(6500, 4500));
    tool.onPointerMove(ctx, ptr(6500, 4500));

    const shape = tool.preview(ctx).shape;
    expect(shape.kind).toBe('marquee');

    const response = tool.onPointerUp(ctx, ptr(6500, 4500));
    expect(response.selection).toEqual({
      mode: 'replace',
      ids: [
        FIXTURE_IDS.wallSouth,
        FIXTURE_IDS.wallEast,
        FIXTURE_IDS.wallNorth,
        FIXTURE_IDS.wallWest,
        FIXTURE_IDS.wallSpine,
      ],
    });
    expect(tool.phase).toBe('idle');
  });

  it('takes nothing that only partly overlaps — no accidental crossing select', () => {
    const ctx = makeCtx();
    const tool = new SelectTool();
    tool.onPointerDown(ctx, ptr(-500, -500));
    tool.onPointerMove(ctx, ptr(3500, 2000));
    tool.onPointerMove(ctx, ptr(3500, 2000));
    expect(tool.onPointerUp(ctx, ptr(3500, 2000)).selection).toEqual({ mode: 'replace', ids: [] });
  });

  it('Shift-marquee adds to the selection', () => {
    const ctx = makeCtx();
    const tool = new SelectTool();
    tool.onPointerDown(ctx, ptr(-500, -500));
    tool.onPointerMove(ctx, ptr(6500, 4500));
    expect(tool.onPointerUp(ctx, ptr(6500, 4500, { shiftKey: true })).selection?.mode).toBe('add');
  });
});

describe('delete', () => {
  it('claims Delete even while idle — that is when a selection is deleted', () => {
    const tool = new SelectTool();
    expect(tool.wantsKey(key('Delete'))).toBe(true);
    expect(tool.wantsKey(key('Backspace'))).toBe(true);
  });

  it('deletes the selection as one group', () => {
    const ctx = makeCtx({ selectedIds: [SPINE] });
    const tool = new SelectTool();
    const response = tool.onKey(ctx, key('Delete'));
    const op = opOfType(response.commit?.ops[0], 'wall.delete');
    expect(op.payload.wallId).toBe(SPINE);
    expect(response.commit?.label).toBe('Wall deleted');
    expect(response.selection).toEqual({ mode: 'clear', ids: [] });
  });

  it('does nothing with an empty selection', () => {
    const tool = new SelectTool();
    expect(tool.onKey(makeCtx(), key('Delete')).handled).toBe(false);
  });

  it('does nothing for a room — rooms are derived, not deletable', () => {
    const ctx = makeCtx({ selectedIds: [fixedId('room', 'R1')] });
    const tool = new SelectTool();
    expect(tool.onKey(ctx, key('Delete')).handled).toBe(false);
  });
});

describe('pointInsidePolygon', () => {
  const square = [
    { x: 0, y: 0 },
    { x: 1000, y: 0 },
    { x: 1000, y: 1000 },
    { x: 0, y: 1000 },
  ];

  it('counts the boundary as inside', () => {
    expect(pointInsidePolygon({ x: 500, y: 500 }, square)).toBe(true);
    expect(pointInsidePolygon({ x: 0, y: 500 }, square)).toBe(true);
    expect(pointInsidePolygon({ x: 1500, y: 500 }, square)).toBe(false);
  });

  it('is false for a degenerate ring', () => {
    expect(pointInsidePolygon({ x: 0, y: 0 }, [{ x: 0, y: 0 }])).toBe(false);
  });
});
