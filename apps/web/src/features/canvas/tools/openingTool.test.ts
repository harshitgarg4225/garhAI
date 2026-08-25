/**
 * Spec for D and N — doors, windows and ventilators.
 *
 * The behaviour this file is really about is the one the brief calls out:
 * **refuse to place where `validate` would reject, and say why inline rather
 * than letting the server bounce it.** That splits into two cases, and both are
 * asserted here against the REAL validator:
 *
 *   - a position that can be fixed by moving is CLAMPED (the 115 mm end margin
 *     made visible, not a workaround for it);
 *   - a placement with no legal position at all BLOCKS, quoting the model
 *     core's own sentence so the inline copy and a 422 would read identically.
 */

import { describe, expect, it } from 'vitest';

import { fixedId } from '@garh/model';

import { HINTS } from './constants';
import { OpeningTool } from './openingTool';
import {
  addWall,
  FIXTURE_IDS,
  hitOn,
  key,
  makeCtx,
  makeTwoRoomPlan,
  nthId,
  opOfType,
  ptr,
  readout,
  typeText,
  type CtxOverrides,
} from './toolTestKit';

/** wallSouth runs (0,0) → (6000,0) and is 230 mm thick. */
const WALL = FIXTURE_IDS.wallSouth;

function doorAt(x: number, y: number, overrides: CtxOverrides = {}) {
  const ctx = makeCtx(overrides);
  const tool = new OpeningTool('door');
  tool.onPointerMove(ctx, ptr(x, y));
  return { tool, ctx };
}

describe('phases', () => {
  it('starts idle with nothing hosted', () => {
    const tool = new OpeningTool('door');
    const ctx = makeCtx();
    expect(tool.phase).toBe('idle');
    expect(tool.preview(ctx).hint).toBe(HINTS.openingIdle);
    expect(tool.commit(ctx)).toBeNull();
  });

  it('hovering a wall moves it to preview and shows the placement', () => {
    const { tool, ctx } = doorAt(1500, 0);
    expect(tool.phase).toBe('preview');
    const shape = tool.preview(ctx).shape;
    expect(shape.kind).toBe('opening');
    if (shape.kind !== 'opening') return;
    expect(shape.wallId).toBe(WALL);
    expect(shape.openingKind).toBe('door');
    expect(shape.offsetMm).toBe(1495);
    expect(shape.centreMm).toEqual({ x: 1495, y: 0 });
    expect(shape.axis).toEqual([
      { x: 1045, y: 0 },
      { x: 1945, y: 0 },
    ]);
    expect(tool.preview(ctx).hint).toBe(HINTS.openingPreview);
  });

  it('leaving the wall drops back to idle', () => {
    const { tool, ctx } = doorAt(1500, 0);
    tool.onPointerMove(ctx, ptr(1500, 2000));
    expect(tool.phase).toBe('idle');
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'opening' && shape.wallId).toBeNull();
  });

  it('prefers the wall the raycast named, when there is one', () => {
    const ctx = makeCtx();
    const tool = new OpeningTool('door');
    tool.onPointerMove(ctx, ptr(1500, 0, { hit: hitOn('wall', WALL, FIXTURE_IDS.groundStorey) }));
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'opening' && shape.wallId).toBe(WALL);
  });

  it('Esc from preview clears the placement', () => {
    const { tool, ctx } = doorAt(1500, 0);
    expect(tool.onKey(ctx, key('Escape')).handled).toBe(true);
    expect(tool.phase).toBe('idle');
  });

  it('stays armed after placing — doors come in pairs', () => {
    const ctx = makeCtx();
    const tool = new OpeningTool('door');
    tool.onPointerDown(ctx, ptr(1500, 0));
    expect(tool.phase).toBe('idle');
    tool.onPointerMove(ctx, ptr(4000, 0));
    expect(tool.phase).toBe('preview');
  });
});

describe('the op it emits', () => {
  it('is a taxonomy-shaped opening.add with the parametric size from the inspector', () => {
    const ctx = makeCtx();
    const tool = new OpeningTool('door');
    const response = tool.onPointerDown(ctx, ptr(1500, 0));

    const commit = response.commit;
    expect(commit?.label).toBe('Door added');
    const op = opOfType(commit?.ops[0], 'opening.add');
    expect(op.payload).toEqual({
      id: nthId('opening', 1),
      wallId: WALL,
      kind: 'door',
      widthMm: 900,
      heightMm: 2100,
      sillMm: 0,
      offsetMm: 1495,
      swing: 'in-left',
    });
    expect(commit?.selectIds).toEqual([nthId('opening', 1)]);
  });

  it('places a window with its own sill', () => {
    const ctx = makeCtx();
    const tool = new OpeningTool('window');
    const op = opOfType(tool.onPointerDown(ctx, ptr(1500, 0)).commit?.ops[0], 'opening.add');
    expect(op.payload.kind).toBe('window');
    expect(op.payload.widthMm).toBe(1200);
    expect(op.payload.heightMm).toBe(1200);
    expect(op.payload.sillMm).toBe(900);
  });

  it('places a ventilator when the N tool is switched to one', () => {
    const ctx = makeCtx({ settings: { windowVariant: 'ventilator' } });
    const tool = new OpeningTool('window');
    const response = tool.onPointerDown(ctx, ptr(1500, 0));
    expect(response.commit?.label).toBe('Ventilator added');
    const op = opOfType(response.commit?.ops[0], 'opening.add');
    expect(op.payload.kind).toBe('ventilator');
    expect(op.payload.widthMm).toBe(600);
    expect(op.payload.sillMm).toBe(1800);
  });

  it('carries the swing the options bar chose', () => {
    const ctx = makeCtx({ settings: { swing: 'out-right' } });
    const tool = new OpeningTool('door');
    const op = opOfType(tool.onPointerDown(ctx, ptr(1500, 0)).commit?.ops[0], 'opening.add');
    expect(op.payload.swing).toBe('out-right');
  });
});

describe('the 115 mm end margin, made visible', () => {
  it('clamps a placement near the wall end to the last legal position', () => {
    const { tool, ctx } = doorAt(100, 0);
    const shape = tool.preview(ctx).shape;
    // 115 + floor(900/2) = 565 — the model invariant, not a tool preference.
    expect(shape.kind === 'opening' && shape.offsetMm).toBe(565);
  });

  it('says it clamped, with a citation and a fix hint', () => {
    const { tool, ctx } = doorAt(100, 0);
    const chip = tool.preview(ctx).chips.find((c) => c.id === 'opening-clamped');
    expect(chip?.severity).toBe('info');
    expect(chip?.cite).toBeTruthy();
    expect(chip?.fix).toBeTruthy();
  });

  it('is silent when nothing needed clamping', () => {
    const { tool, ctx } = doorAt(3000, 0);
    expect(tool.preview(ctx).chips.map((c) => c.id)).not.toContain('opening-clamped');
  });

  it('clamps at the far end too', () => {
    const { tool, ctx } = doorAt(5990, 0);
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'opening' && shape.offsetMm).toBe(5435);
  });

  it('the clamped offset is one fold accepts, not merely one the tool likes', () => {
    const ctx = makeCtx();
    const tool = new OpeningTool('door');
    const response = tool.onPointerDown(ctx, ptr(100, 0));
    // A commit came back at all: `commit()` dry-runs through the real
    // `applyGroup`, so a rejected payload would have returned null.
    expect(response.commit).toBeTruthy();
    expect(opOfType(response.commit?.ops[0], 'opening.add').payload.offsetMm).toBe(565);
  });
});

describe('refusing, inline, in the model core’s own words', () => {
  /** A 1000 mm wall — too short for a 900 mm door plus its two end margins. */
  const shortWallId = fixedId('wall', 'SHORT');
  const shortWallDoc = addWall(
    makeTwoRoomPlan(),
    shortWallId,
    { x: 0, y: 6000 },
    { x: 1000, y: 6000 },
  );

  it('blocks when no legal position exists, quoting the validator', () => {
    const { tool, ctx } = doorAt(500, 6000, { doc: shortWallDoc });
    const blocked = tool.preview(ctx).blocked;
    expect(blocked).not.toBeNull();
    expect(blocked?.issues[0]?.code).toBe('OPENING_OUT_OF_WALL');
    // 1000 − 2×115 = 770 of usable wall. The number is the validator's.
    expect(blocked?.message).toContain('770mm');
    expect(blocked?.message).toContain('900mm');
    expect(blocked?.fix).toBeTruthy();
    expect(tool.commit(ctx)).toBeNull();
  });

  it('does not dispatch anything when it is blocked', () => {
    const ctx = makeCtx({ doc: shortWallDoc });
    const tool = new OpeningTool('door');
    const response = tool.onPointerDown(ctx, ptr(500, 6000));
    expect(response.commit ?? null).toBeNull();
  });

  it('blocks an opening taller than its storey, and explains that instead', () => {
    const { tool, ctx } = doorAt(1500, 0, {
      settings: { door: { widthMm: 900, heightMm: 3100, sillMm: 0 } },
    });
    const blocked = tool.preview(ctx).blocked;
    expect(blocked?.issues[0]?.code).toBe('OPENING_EXCEEDS_STOREY_HEIGHT');
    expect(blocked?.message).toContain('3000mm storey');
    expect(tool.commit(ctx)).toBeNull();
  });

  it('recovers as soon as the pointer moves somewhere legal', () => {
    const ctx = makeCtx({ doc: shortWallDoc });
    const tool = new OpeningTool('door');
    tool.onPointerMove(ctx, ptr(500, 6000));
    expect(tool.preview(ctx).blocked).not.toBeNull();
    tool.onPointerMove(ctx, ptr(1500, 0));
    expect(tool.preview(ctx).blocked).toBeNull();
  });
});

describe('numeric entry overrides the mouse (§12)', () => {
  it('a typed offset replaces the hovered one exactly', () => {
    const { tool, ctx } = doorAt(1500, 0);
    typeText(tool, ctx, '2000');
    expect(tool.preview(ctx).entry?.label).toBe('From wall start');

    const op = opOfType(tool.onKey(ctx, key('Enter')).commit?.ops[0], 'opening.add');
    expect(op.payload.offsetMm).toBe(2000);
  });

  it('a typed offset outside the legal window is still clamped, never rejected', () => {
    const { tool, ctx } = doorAt(1500, 0);
    typeText(tool, ctx, '10');
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'opening' && shape.offsetMm).toBe(565);
  });

  it('Tab reaches the width field, and a typed width becomes the next default', () => {
    const { tool, ctx } = doorAt(1500, 0);
    tool.onKey(ctx, key('Tab'));
    expect(tool.preview(ctx).entry).toBeNull(); // Tab cleared the buffer
    typeText(tool, ctx, '1200');

    const response = tool.onKey(ctx, key('Enter'));
    const op = opOfType(response.commit?.ops[0], 'opening.add');
    expect(op.payload.widthMm).toBe(1200);
    // §15: placing six 1200 mm windows should mean typing 1200 once.
    expect(response.settingsPatch).toEqual({ door: { widthMm: 1200, heightMm: 2100, sillMm: 0 } });
  });

  it('leaves the default alone when the typed width matched it', () => {
    const { tool, ctx } = doorAt(1500, 0);
    tool.onKey(ctx, key('Tab'));
    typeText(tool, ctx, '900');
    expect(tool.onKey(ctx, key('Enter')).settingsPatch).toBeUndefined();
  });
});

describe('the X key', () => {
  it('cycles the swing, and is claimed even before a wall is hovered', () => {
    const ctx = makeCtx();
    const tool = new OpeningTool('door');
    expect(tool.wantsKey(key('x'))).toBe(true);
    expect(tool.onKey(ctx, key('x')).settingsPatch).toEqual({ swing: 'in-right' });
  });

  it('⇧X switches the N tool between a window and a ventilator', () => {
    const ctx = makeCtx();
    const tool = new OpeningTool('window');
    expect(tool.onKey(ctx, key('x', { shiftKey: true })).settingsPatch).toEqual({
      windowVariant: 'ventilator',
    });
  });

  it('⇧X means nothing to the door tool', () => {
    const ctx = makeCtx();
    const tool = new OpeningTool('door');
    expect(tool.onKey(ctx, key('x', { shiftKey: true })).handled).toBe(false);
  });
});

describe('readouts', () => {
  it('shows the offset, the width and how much wall is left', () => {
    const { tool, ctx } = doorAt(1500, 0);
    const preview = tool.preview(ctx);
    expect(readout(preview, 'offset')).not.toBeNull();
    expect(readout(preview, 'width')).not.toBeNull();
    expect(readout(preview, 'remaining')).not.toBeNull();
    // A door has no sill worth reading out; a window does.
    expect(readout(preview, 'sill')).toBeNull();
  });

  it('adds the sill for a window', () => {
    const ctx = makeCtx();
    const tool = new OpeningTool('window');
    tool.onPointerMove(ctx, ptr(1500, 0));
    expect(readout(tool.preview(ctx), 'sill')).not.toBeNull();
  });
});
