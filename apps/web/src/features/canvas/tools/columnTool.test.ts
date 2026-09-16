/**
 * Spec for the column tool — the machine, the op it emits, and that the op
 * folds into a column the plan can click.
 */

import { describe, expect, it } from 'vitest';

import { DEFAULTS, applyGroup, validateOpAgainstDoc } from '@garh/model';

import { HINTS } from './constants';
import { ColumnTool } from './columnTool';
import { FIXTURE_IDS, key, makeCtx, nthId, opOfType, ptr, readout, typeText } from './toolTestKit';

const GROUND = FIXTURE_IDS.groundStorey;

describe('placing', () => {
  it('starts idle and commits nothing', () => {
    const tool = new ColumnTool();
    const ctx = makeCtx();
    expect(tool.phase).toBe('idle');
    expect(tool.commit(ctx)).toBeNull();
    expect(tool.preview(ctx).hint).toBe(HINTS.columnIdle);
  });

  it('previews the footprint at the snapped pointer, sized from the settings', () => {
    const tool = new ColumnTool();
    const ctx = makeCtx();
    tool.onPointerMove(ctx, ptr(1150, 1150));
    expect(tool.phase).toBe('preview');
    const preview = tool.preview(ctx);
    expect(preview.shape).toEqual({
      kind: 'column',
      centreMm: { x: 1150, y: 1150 },
      sizeMm: DEFAULTS.columnSizeMm,
    });
    // The fixture displays ft-in, so the readout is the formatted pair.
    expect(readout(preview, 'size')).toBe(`0'-9" × 0'-9"`);
  });

  it('snaps the centre to a wall end — a column sits on the junction', () => {
    const tool = new ColumnTool();
    const ctx = makeCtx();
    tool.onPointerMove(ctx, ptr(3006, 4004));
    const preview = tool.preview(ctx);
    expect(preview.snap?.kind).toBe('endpoint');
    expect(preview.shape.kind === 'column' && preview.shape.centreMm).toEqual({ x: 3000, y: 4000 });
  });

  it('commits a column.set add the fold accepts, and stays armed', () => {
    const tool = new ColumnTool();
    const ctx = makeCtx({ settings: { columnSizeMm: { xMm: 230, yMm: 450 } } });
    tool.onPointerMove(ctx, ptr(1150, 1150));
    const response = tool.onPointerDown(ctx, ptr(1150, 1150));
    const commit = response.commit;
    expect(commit).toBeTruthy();
    if (commit == null) return;
    const op = opOfType(commit.ops[0], 'column.set');
    expect(op.payload).toEqual({
      action: 'add',
      id: nthId('column', 1),
      storeyId: GROUND,
      pt: { x: 1150, y: 1150 },
      sizeMm: { xMm: 230, yMm: 450 },
    });
    expect(commit.label).toBe('Column placed');
    expect(commit.selectIds).toEqual([nthId('column', 1)]);
    expect(validateOpAgainstDoc(ctx.doc, op)).toEqual([]);
    const after = applyGroup(ctx.doc, commit.ops).model;
    expect(after.house.columns.map((c) => c.pt)).toContainEqual({ x: 1150, y: 1150 });
    // Still in preview: the next click places the next column.
    expect(tool.phase).toBe('preview');
  });

  it('places nothing with no storey to draw on', () => {
    const tool = new ColumnTool();
    const ctx = makeCtx({ storeyId: null });
    tool.onPointerMove(ctx, ptr(1150, 1150));
    expect(tool.onPointerDown(ctx, ptr(1150, 1150)).commit ?? null).toBeNull();
    expect(tool.preview(ctx).hint).toBe(HINTS.noStorey);
  });
});

describe('sizing', () => {
  it('X turns the column by swapping width and depth through the settings', () => {
    const tool = new ColumnTool();
    const ctx = makeCtx({ settings: { columnSizeMm: { xMm: 230, yMm: 450 } } });
    tool.onPointerMove(ctx, ptr(1150, 1150));
    expect(tool.wantsKey(key('x'))).toBe(true);
    const response = tool.onKey(ctx, key('x'));
    expect(response.settingsPatch).toEqual({ columnSizeMm: { xMm: 450, yMm: 230 } });
  });

  it('a typed width overrides the settings and becomes the new default on commit', () => {
    const tool = new ColumnTool();
    const ctx = makeCtx();
    tool.onPointerMove(ctx, ptr(1150, 1150));
    for (const r of typeText(tool, ctx, '300')) expect(r.handled).toBe(true);
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'column' && shape.sizeMm).toEqual({ xMm: 300, yMm: 230 });

    const response = tool.onKey(ctx, key('Enter'));
    const op = opOfType(response.commit?.ops[0], 'column.set');
    expect(op.payload.sizeMm).toEqual({ xMm: 300, yMm: 230 });
    expect(response.settingsPatch).toEqual({ columnSizeMm: { xMm: 300, yMm: 230 } });
  });

  it('Tab moves the typed value to the depth field', () => {
    const tool = new ColumnTool();
    const ctx = makeCtx();
    tool.onPointerMove(ctx, ptr(1150, 1150));
    tool.onKey(ctx, key('Tab'));
    typeText(tool, ctx, '450');
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'column' && shape.sizeMm).toEqual({ xMm: 230, yMm: 450 });
  });

  it('Esc cancels the preview, emitting nothing', () => {
    const tool = new ColumnTool();
    const ctx = makeCtx();
    tool.onPointerMove(ctx, ptr(1150, 1150));
    const response = tool.onKey(ctx, key('Escape'));
    expect(response.handled).toBe(true);
    expect(response.commit ?? null).toBeNull();
    expect(tool.phase).toBe('idle');
  });
});
