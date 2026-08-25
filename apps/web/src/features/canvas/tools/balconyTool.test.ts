/**
 * Spec for B — balconies, and the projection-versus-setback answer that has to
 * be on screen while you draw one.
 *
 * The rule this file protects is golden rule 5: **compliance never blocks, it
 * informs.** A balcony that crosses the setback line is a chip with a citation
 * and a fix hint — never a refused commit. The only thing that blocks is a ring
 * the model core itself would not accept, and even then the vertices stay on
 * screen so the work is not lost.
 */

import { describe, expect, it } from 'vitest';

import { validateOpAgainstDoc, validateOpShape } from '@garh/model';

import { BalconyTool, maxDistanceOutside } from './balconyTool';
import { HINTS, MIN_BALCONY_VERTICES, RING_CLOSE_TOLERANCE_MM } from './constants';
import {
  chipIds,
  FIXTURE_IDS,
  key,
  makeCtx,
  nthId,
  opOfType,
  ptr,
  readout,
  typeText,
  type CtxOverrides,
} from './toolTestKit';
import type { SetbackContext } from './types';

/** A triangle north of the house, on the 115 mm module, clear of every wall. */
const P1 = { x: 1150, y: 4600 };
const P2 = { x: 3450, y: 4600 };
const P3 = { x: 3450, y: 5750 };

/** The house footprint, used as a deterministic stand-in for the envelope. */
const HOUSE_ENVELOPE = [
  { x: 0, y: 0 },
  { x: 6000, y: 0 },
  { x: 6000, y: 4000 },
  { x: 0, y: 4000 },
];

/** Ortho off, so a clicked corner is exactly where the spec says it is. */
function drawn(overrides: CtxOverrides = {}) {
  const ctx = makeCtx({ ...overrides, settings: { ortho: false, ...overrides.settings } });
  const tool = new BalconyTool();
  for (const p of [P1, P2, P3]) tool.onPointerDown(ctx, ptr(p.x, p.y));
  return { tool, ctx };
}

describe('phases', () => {
  it('starts idle', () => {
    const tool = new BalconyTool();
    const ctx = makeCtx();
    expect(tool.phase).toBe('idle');
    expect(tool.preview(ctx).hint).toBe(HINTS.balconyIdle);
    expect(tool.commit(ctx)).toBeNull();
  });

  it('collects corners as a polygon preview', () => {
    const { tool, ctx } = drawn();
    expect(tool.phase).toBe('drawing');
    const shape = tool.preview(ctx).shape;
    expect(shape.kind).toBe('polygon');
    if (shape.kind !== 'polygon') return;
    expect(shape.points).toEqual([P1, P2, P3]);
    expect(tool.preview(ctx).hint).toBe(HINTS.balconyDrawing);
  });

  it('refuses to start without a storey', () => {
    const tool = new BalconyTool();
    const ctx = makeCtx({ storeyId: null });
    expect(tool.onPointerDown(ctx, ptr(P1.x, P1.y)).handled).toBe(false);
    expect(tool.phase).toBe('idle');
  });

  it('constrains an edge to the ortho axis by default', () => {
    const ctx = makeCtx();
    const tool = new BalconyTool();
    tool.onPointerDown(ctx, ptr(1150, 4600));
    tool.onPointerDown(ctx, ptr(3450, 4700));
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'polygon' && shape.points[1]).toEqual({ x: 3450, y: 4600 });
  });
});

describe('the op it emits', () => {
  it('is a balcony.set add the real validator accepts', () => {
    const { tool, ctx } = drawn();
    const response = tool.onKey(ctx, key('Enter'));

    const op = opOfType(response.commit?.ops[0], 'balcony.set');
    expect(op.payload.action).toBe('add');
    expect(op.payload.id).toBe(nthId('balcony', 1));
    expect(op.payload.storeyId).toBe(FIXTURE_IDS.groundStorey);
    expect(op.payload.polygon).toEqual([P1, P2, P3]);
    expect(op.payload.railingKind).toBe('ms');
    expect(op.payload.railingHeightMm).toBe(1000);
    expect(op.payload.slabThicknessMm).toBe(125);
    expect(Number.isInteger(op.payload.projectionMm)).toBe(true);

    expect(validateOpShape(op)).toEqual([]);
    expect(validateOpAgainstDoc(ctx.doc, op)).toEqual([]);
    expect(response.commit?.label).toBe('Balcony added');
    expect(tool.phase).toBe('idle');
  });

  it('carries the railing the options bar chose', () => {
    const { tool, ctx } = drawn({ settings: { railingKind: 'glass', railingHeightMm: 1100 } });
    const op = opOfType(tool.onKey(ctx, key('Enter')).commit?.ops[0], 'balcony.set');
    expect(op.payload.railingKind).toBe('glass');
    expect(op.payload.railingHeightMm).toBe(1100);
  });

  it('closes when the pointer comes back to the first corner', () => {
    const { tool, ctx } = drawn();
    const response = tool.onPointerDown(ctx, ptr(P1.x, P1.y));
    expect(response.commit?.ops).toHaveLength(1);
    expect(tool.phase).toBe('idle');
  });

  it('treats "near the first corner" as closing it, within one module', () => {
    expect(RING_CLOSE_TOLERANCE_MM).toBe(115);
    const { tool, ctx } = drawn();
    // 1190 snaps back to 1150 on the module, landing on P1 exactly.
    const response = tool.onPointerDown(ctx, ptr(P1.x + 40, P1.y));
    expect(response.commit?.ops).toHaveLength(1);
  });
});

describe('refusing a ring the model core would not accept', () => {
  it('needs three corners, and says so without throwing the work away', () => {
    expect(MIN_BALCONY_VERTICES).toBe(3);
    const ctx = makeCtx({ settings: { ortho: false } });
    const tool = new BalconyTool();
    tool.onPointerDown(ctx, ptr(P1.x, P1.y));
    tool.onPointerDown(ctx, ptr(P2.x, P2.y));

    const response = tool.onKey(ctx, key('Enter'));
    expect(response.commit ?? null).toBeNull();
    expect(tool.preview(ctx).blocked?.message).toContain('three corners');
    expect(tool.preview(ctx).blocked?.fix).toBeTruthy();
    // The two corners are still there to build on.
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'polygon' && shape.points).toHaveLength(2);
  });

  it('Backspace drops the last corner instead of the whole ring', () => {
    const { tool, ctx } = drawn();
    tool.onKey(ctx, key('Backspace'));
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'polygon' && shape.points).toHaveLength(2);
    expect(tool.phase).toBe('drawing');
  });

  it('Esc cancels the whole ring and emits nothing', () => {
    const { tool, ctx } = drawn();
    const response = tool.onKey(ctx, key('Escape'));
    expect(response.commit ?? null).toBeNull();
    expect(tool.phase).toBe('idle');
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'polygon' && shape.points).toHaveLength(0);
  });
});

describe('projection and setback — chips, never blocks', () => {
  const setback: SetbackContext = {
    envelope: HOUSE_ENVELOPE,
    maxProjectionMm: 900,
    cite: 'BBMP 2020, Table 5',
  };

  it('says by how much the balcony crosses the setback line, and how to fix it', () => {
    const { tool, ctx } = drawn({ setback });
    const chip = tool.preview(ctx).chips.find((c) => c.id === 'setback.balcony');
    expect(chip?.severity).toBe('error');
    // The deepest corner, P3, is 1750 mm beyond the envelope's north edge.
    expect(chip?.text).toBe('This balcony crosses the setback line by 1750 mm.');
    expect(chip?.cite).toBe('BBMP 2020, Table 5');
    expect(chip?.fix).toBeTruthy();
  });

  it('still commits — an architect may override anything', () => {
    const { tool, ctx } = drawn({ setback });
    expect(tool.preview(ctx).blocked).toBeNull();
    expect(tool.onKey(ctx, key('Enter')).commit?.ops).toHaveLength(1);
  });

  it('raises nothing at all when the page has no setback context', () => {
    const { tool, ctx } = drawn();
    expect(chipIds(tool.preview(ctx))).toEqual([]);
  });

  it('is quiet when the balcony sits inside the envelope', () => {
    const roomy: SetbackContext = {
      envelope: [
        { x: 0, y: 0 },
        { x: 9000, y: 0 },
        { x: 9000, y: 9000 },
        { x: 0, y: 9000 },
      ],
      maxProjectionMm: null,
      cite: null,
    };
    const { tool, ctx } = drawn({ setback: roomy });
    expect(chipIds(tool.preview(ctx))).not.toContain('setback.balcony');
  });

  it('reports the area while you draw', () => {
    const { tool, ctx } = drawn();
    expect(readout(tool.preview(ctx), 'area')).not.toBeNull();
  });
});

describe('maxDistanceOutside', () => {
  it('is zero for a ring entirely inside the boundary', () => {
    expect(
      maxDistanceOutside(
        [
          { x: 1000, y: 1000 },
          { x: 2000, y: 1000 },
          { x: 2000, y: 2000 },
        ],
        HOUSE_ENVELOPE,
      ),
    ).toBe(0);
  });

  it('is zero for a ring exactly on the boundary', () => {
    expect(maxDistanceOutside(HOUSE_ENVELOPE, HOUSE_ENVELOPE)).toBe(0);
  });

  it('measures the deepest excursion, not the first one', () => {
    expect(maxDistanceOutside([P1, P2, P3], HOUSE_ENVELOPE)).toBe(1750);
  });
});

describe('numeric entry overrides the mouse (§12)', () => {
  it('a typed edge length places the corner exactly', () => {
    const ctx = makeCtx({ settings: { ortho: false } });
    const tool = new BalconyTool();
    tool.onPointerDown(ctx, ptr(1150, 4600));
    tool.onPointerMove(ctx, ptr(3450, 4600));
    typeText(tool, ctx, '2000');

    // Enter with a buffer places the corner and keeps drawing.
    expect(tool.onKey(ctx, key('Enter')).commit ?? null).toBeNull();
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'polygon' && shape.points[1]).toEqual({ x: 3150, y: 4600 });
    expect(tool.phase).toBe('drawing');
  });

  it('claims digits only while a ring is being drawn', () => {
    const tool = new BalconyTool();
    expect(tool.wantsKey(key('2'))).toBe(false);
    const ctx = makeCtx({ settings: { ortho: false } });
    tool.onPointerDown(ctx, ptr(1150, 4600));
    expect(tool.wantsKey(key('2'))).toBe(true);
  });
});
