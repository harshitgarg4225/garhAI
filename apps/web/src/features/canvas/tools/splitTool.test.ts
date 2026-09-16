/**
 * Spec for the split tool: where the cut lands, and that it is the fold's cut.
 *
 * The fixture is the two-room plan: the south wall runs (0,0)→(6000,0) at 230
 * thick, and the 115 spine meets it at x = 3000. That junction is the reason
 * an architect splits a wall, so it is the snap the spec leans on.
 */

import { describe, expect, it } from 'vitest';

import { applyGroup, validateOpAgainstDoc } from '@garh/model';

import { HINTS } from './constants';
import { SplitTool } from './splitTool';
import {
  FIXTURE_IDS,
  hitOn,
  key,
  makeCtx,
  nthId,
  opOfType,
  ptr,
  readout,
  typeText,
} from './toolTestKit';

const SOUTH = FIXTURE_IDS.wallSouth;
const GROUND = FIXTURE_IDS.groundStorey;

describe('hovering', () => {
  it('starts idle with the split hint and no shape', () => {
    const tool = new SplitTool();
    const ctx = makeCtx();
    expect(tool.phase).toBe('idle');
    expect(tool.preview(ctx).shape).toEqual({ kind: 'none' });
    expect(tool.preview(ctx).hint).toBe(HINTS.splitIdle);
  });

  it('previews a cut on the wall the picker names, at the grid module along it', () => {
    const tool = new SplitTool();
    const ctx = makeCtx();
    // 1490 along on the 115 module snaps to 1495.
    tool.onPointerMove(ctx, ptr(1490, 40, { hit: hitOn('wall', SOUTH, GROUND) }));
    expect(tool.phase).toBe('preview');
    const preview = tool.preview(ctx);
    expect(preview.shape.kind).toBe('split');
    if (preview.shape.kind !== 'split') return;
    expect(preview.shape.wallId).toBe(SOUTH);
    expect(preview.shape.pointMm).toEqual({ x: 1495, y: 0 });
    // The fixture displays ft-in: 1495 mm ≈ 4'-11", 4505 mm ≈ 14'-9".
    expect(readout(preview, 'distance')).toBe(`4'-11"`);
    expect(readout(preview, 'remaining')).toBe(`14'-9"`);
    expect(preview.hint).toBe(HINTS.splitPreview);
  });

  it('falls back to the nearest wall when the pick is empty (no mesh mounted yet)', () => {
    const tool = new SplitTool();
    const ctx = makeCtx();
    tool.onPointerMove(ctx, ptr(1495, 0));
    expect(tool.phase).toBe('preview');
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'split' && shape.wallId).toBe(SOUTH);
  });

  it('snaps the cut to a wall that meets this one — the T-junction', () => {
    const tool = new SplitTool();
    const ctx = makeCtx();
    // 8 mm off the spine's end at (3000, 0): inside the 12 px snap tolerance.
    tool.onPointerMove(ctx, ptr(3008, 0, { hit: hitOn('wall', SOUTH, GROUND) }));
    const preview = tool.preview(ctx);
    expect(preview.snap?.kind).toBe('endpoint');
    expect(preview.snap?.refId).toBe(FIXTURE_IDS.wallSpine);
    expect(preview.shape.kind === 'split' && preview.shape.pointMm).toEqual({ x: 3000, y: 0 });
  });

  it('never previews a cut at the wall’s own end', () => {
    const tool = new SplitTool();
    const ctx = makeCtx();
    // Right on the south-west corner: the wall's own endpoint snap is refused,
    // and the projection clamps into the legal window.
    tool.onPointerMove(ctx, ptr(0, 0, { hit: hitOn('wall', SOUTH, GROUND) }));
    const shape = tool.preview(ctx).shape;
    expect(shape.kind).toBe('split');
    if (shape.kind !== 'split') return;
    expect(shape.pointMm.x).toBeGreaterThanOrEqual(1);
  });

  it('drops the preview when the pointer leaves every wall', () => {
    const tool = new SplitTool();
    const ctx = makeCtx();
    tool.onPointerMove(ctx, ptr(1495, 0));
    expect(tool.phase).toBe('preview');
    tool.onPointerMove(ctx, ptr(1500, 2000));
    expect(tool.phase).toBe('idle');
    expect(tool.preview(ctx).shape).toEqual({ kind: 'none' });
  });

  it('declines with no storey to draw on', () => {
    const tool = new SplitTool();
    const ctx = makeCtx({ storeyId: null });
    expect(tool.onPointerMove(ctx, ptr(1495, 0)).handled).toBe(false);
    expect(tool.preview(ctx).hint).toBe(HINTS.noStorey);
  });
});

describe('typing a distance overrides the mouse', () => {
  it('cuts exactly where the number says, unsnapped', () => {
    const tool = new SplitTool();
    const ctx = makeCtx();
    tool.onPointerMove(ctx, ptr(1495, 0));
    for (const r of typeText(tool, ctx, '2390')) expect(r.handled).toBe(true);
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'split' && shape.pointMm).toEqual({ x: 2390, y: 0 });
  });

  it('clamps a typed distance past the wall end into the legal window', () => {
    const tool = new SplitTool();
    const ctx = makeCtx();
    tool.onPointerMove(ctx, ptr(1495, 0));
    typeText(tool, ctx, '9000');
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'split' && shape.pointMm).toEqual({ x: 5999, y: 0 });
  });

  it('Esc clears a mistyped number before it cancels the cut', () => {
    const tool = new SplitTool();
    const ctx = makeCtx();
    tool.onPointerMove(ctx, ptr(1495, 0));
    typeText(tool, ctx, '24');
    tool.onKey(ctx, key('Escape'));
    expect(tool.phase).toBe('preview');
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'split' && shape.pointMm).toEqual({ x: 1495, y: 0 });
    tool.onKey(ctx, key('Escape'));
    expect(tool.phase).toBe('idle');
  });
});

describe('committing', () => {
  it('emits one wall.split the fold accepts, and selects both halves', () => {
    const tool = new SplitTool();
    const ctx = makeCtx();
    tool.onPointerMove(ctx, ptr(2300, 0, { hit: hitOn('wall', SOUTH, GROUND) }));
    const response = tool.onPointerDown(ctx, ptr(2300, 0, { hit: hitOn('wall', SOUTH, GROUND) }));
    const commit = response.commit;
    expect(commit).toBeTruthy();
    if (commit == null) return;
    expect(commit.label).toBe('Wall split');
    const op = opOfType(commit.ops[0], 'wall.split');
    expect(op.payload).toEqual({ wallId: SOUTH, atMm: 2300, newWallId: nthId('wall', 1) });
    expect(commit.selectIds).toEqual([SOUTH, nthId('wall', 1)]);
    expect(validateOpAgainstDoc(ctx.doc, op)).toEqual([]);

    const after = applyGroup(ctx.doc, commit.ops).model;
    const near = after.house.walls.find((w) => w.id === SOUTH);
    const far = after.house.walls.find((w) => w.id === nthId('wall', 1));
    expect(near?.b).toEqual({ x: 2300, y: 0 });
    expect(far?.a).toEqual({ x: 2300, y: 0 });
    expect(far?.b).toEqual({ x: 6000, y: 0 });
  });

  it('Enter commits the previewed cut', () => {
    const tool = new SplitTool();
    const ctx = makeCtx();
    tool.onPointerMove(ctx, ptr(1495, 0));
    const response = tool.onKey(ctx, key('Enter'));
    expect(response.commit?.ops).toHaveLength(1);
    expect(tool.phase).toBe('idle');
  });

  it('commits nothing from idle', () => {
    const tool = new SplitTool();
    expect(tool.commit(makeCtx())).toBeNull();
    expect(tool.onKey(makeCtx(), key('Enter')).commit ?? null).toBeNull();
  });

  it('refuses a wall too short to cut, rather than emitting an op fold rejects', () => {
    const tool = new SplitTool();
    const short = applyGroup(makeCtx().doc, [
      {
        type: 'wall.add',
        payload: {
          id: nthId('wall', 9),
          storeyId: GROUND,
          a: { x: 8000, y: 8000 },
          b: { x: 8001, y: 8000 },
          thicknessMm: 115,
          kind: 'internal',
        },
      },
    ]).model;
    const ctx = makeCtx({ doc: short });
    tool.onPointerMove(ctx, ptr(8000, 8000, { hit: hitOn('wall', nthId('wall', 9), GROUND) }));
    expect(tool.phase).toBe('idle');
    expect(tool.commit(ctx)).toBeNull();
  });
});
