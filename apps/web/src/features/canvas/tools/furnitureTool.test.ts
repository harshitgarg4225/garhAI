/**
 * Spec for F — furniture placement mode.
 *
 * The rule worth defending: **the catalogue is data, not a guess.** With no
 * chosen item — or an id the catalogue has not loaded — the tool places
 * nothing and says why, rather than inventing a 900 × 600 box. A placeholder
 * footprint would draw a plan claiming a wardrobe fits where it does not, and
 * the furniture-fit score would then disagree with the drawing.
 */

import { describe, expect, it } from 'vitest';

import { validateOpAgainstDoc, validateOpShape } from '@garh/model';

import type { FurnitureItem } from '../../../lib/schemas';
import { HINTS, ROTATE_STEP_DEG } from './constants';
import { FurnitureTool, normaliseRotationDeg } from './furnitureTool';
import { FIXTURE_IDS, key, makeCtx, nthId, opOfType, ptr, readout, typeText } from './toolTestKit';

const BED: FurnitureItem = {
  id: 'bed-queen-1900x1525',
  name: 'Queen bed',
  category: 'bedroom',
  widthMm: 1900,
  depthMm: 1525,
  heightMm: 600,
  clearanceMm: 0,
  assetUrl: null,
  roomTypes: ['bedroom_master'],
};

const CATALOG: ReadonlyMap<string, FurnitureItem> = new Map([[BED.id, BED]]);

function armed(rotationDeg = 0) {
  const ctx = makeCtx({
    settings: { furnitureCatalogId: BED.id, furnitureRotationDeg: rotationDeg },
    furnitureCatalog: CATALOG,
  });
  const tool = new FurnitureTool();
  tool.onPointerMove(ctx, ptr(2000, 2000));
  return { tool, ctx };
}

describe('without a chosen item it places nothing', () => {
  it('stays idle and asks for one', () => {
    const ctx = makeCtx({ furnitureCatalog: CATALOG });
    const tool = new FurnitureTool();
    tool.onPointerMove(ctx, ptr(2000, 2000));
    expect(tool.phase).toBe('idle');
    expect(tool.preview(ctx).hint).toBe(HINTS.furnitureNoItem);
    expect(tool.preview(ctx).shape).toEqual({ kind: 'none' });
    expect(tool.commit(ctx)).toBeNull();
  });

  it('refuses an id the catalogue has not loaded, rather than guessing a size', () => {
    const ctx = makeCtx({ settings: { furnitureCatalogId: 'wardrobe-that-is-not-loaded' } });
    const tool = new FurnitureTool();
    tool.onPointerMove(ctx, ptr(2000, 2000));
    expect(tool.phase).toBe('idle');
    expect(tool.commit(ctx)).toBeNull();
  });

  it('places nothing without a storey', () => {
    const ctx = makeCtx({
      storeyId: null,
      settings: { furnitureCatalogId: BED.id },
      furnitureCatalog: CATALOG,
    });
    const tool = new FurnitureTool();
    tool.onPointerMove(ctx, ptr(2000, 2000));
    expect(tool.commit(ctx)).toBeNull();
  });
});

describe('with an item chosen', () => {
  it('previews the footprint at the snapped cursor', () => {
    const { tool, ctx } = armed();
    expect(tool.phase).toBe('preview');
    const shape = tool.preview(ctx).shape;
    expect(shape.kind).toBe('furniture');
    if (shape.kind !== 'furniture') return;
    expect(shape.catalogId).toBe(BED.id);
    // 2000 → 1955 on the 115 mm module.
    expect(shape.centreMm).toEqual({ x: 1955, y: 1955 });
    expect(shape.sizeMm).toEqual({ xMm: 1900, yMm: 1525 });
    expect(shape.rotationDeg).toBe(0);
  });

  it('swaps the footprint on a quarter turn', () => {
    const { tool, ctx } = armed(90);
    const shape = tool.preview(ctx).shape;
    expect(shape.kind === 'furniture' && shape.sizeMm).toEqual({ xMm: 1525, yMm: 1900 });
  });

  it('names the item and its real size in the readouts', () => {
    const { tool, ctx } = armed();
    const preview = tool.preview(ctx);
    expect(readout(preview, 'item')).toBe('Queen bed');
    expect(readout(preview, 'size')).toBe('1900 × 1525 mm');
    expect(readout(preview, 'rotation')).toBe('0°');
  });

  it('emits a furniture.set place the real validator accepts', () => {
    const ctx = makeCtx({
      settings: { furnitureCatalogId: BED.id },
      furnitureCatalog: CATALOG,
    });
    const tool = new FurnitureTool();
    const response = tool.onPointerDown(ctx, ptr(2000, 2000));

    const op = opOfType(response.commit?.ops[0], 'furniture.set');
    expect(op.payload).toEqual({
      action: 'place',
      id: nthId('furniture', 1),
      storeyId: FIXTURE_IDS.groundStorey,
      catalogId: BED.id,
      pt: { x: 1955, y: 1955 },
      rotationDeg: 0,
    });
    expect(validateOpShape(op)).toEqual([]);
    expect(validateOpAgainstDoc(ctx.doc, op)).toEqual([]);
    expect(response.commit?.label).toBe('Queen bed placed');
    expect(response.commit?.selectIds).toEqual([nthId('furniture', 1)]);
  });

  it('stays armed after placing — a bedroom needs four pieces, not four re-arms', () => {
    const ctx = makeCtx({
      settings: { furnitureCatalogId: BED.id },
      furnitureCatalog: CATALOG,
    });
    const tool = new FurnitureTool();
    tool.onPointerDown(ctx, ptr(2000, 2000));
    tool.onPointerMove(ctx, ptr(3500, 2000));
    expect(tool.phase).toBe('preview');
    expect(tool.commit(ctx)).not.toBeNull();
  });

  it('ignores the object snap — furniture sits on the grid, not on wall ends', () => {
    const ctx = makeCtx({
      settings: { furnitureCatalogId: BED.id },
      furnitureCatalog: CATALOG,
    });
    const tool = new FurnitureTool();
    tool.onPointerMove(ctx, ptr(4, 4));
    expect(tool.preview(ctx).cursorMm).toEqual({ x: 0, y: 0 });
    expect(tool.preview(ctx).snap).toBeNull();
  });
});

describe('rotation', () => {
  it('normalises any integer into 0–359', () => {
    expect(normaliseRotationDeg(0)).toBe(0);
    expect(normaliseRotationDeg(90)).toBe(90);
    expect(normaliseRotationDeg(360)).toBe(0);
    expect(normaliseRotationDeg(450)).toBe(90);
    expect(normaliseRotationDeg(-90)).toBe(270);
    expect(normaliseRotationDeg(-450)).toBe(270);
  });

  it('X turns the piece a quarter, ⇧X the other way', () => {
    expect(ROTATE_STEP_DEG).toBe(90);
    const { tool, ctx } = armed();
    expect(tool.wantsKey(key('x'))).toBe(true);
    expect(tool.onKey(ctx, key('x')).settingsPatch).toEqual({ furnitureRotationDeg: 90 });
    expect(tool.onKey(ctx, key('x', { shiftKey: true })).settingsPatch).toEqual({
      furnitureRotationDeg: 270,
    });
  });

  it('a typed angle overrides the mouse and becomes the next default', () => {
    const { tool, ctx } = armed();
    typeText(tool, ctx, '45');
    expect(tool.preview(ctx).entry?.echo).toBe('45°');

    const response = tool.onKey(ctx, key('Enter'));
    const op = opOfType(response.commit?.ops[0], 'furniture.set');
    expect(op.payload.rotationDeg).toBe(45);
    expect(response.settingsPatch).toEqual({ furnitureRotationDeg: 45 });
  });
});

describe('cancel', () => {
  it('Esc leaves placement mode without emitting anything', () => {
    const { tool, ctx } = armed();
    const response = tool.onKey(ctx, key('Escape'));
    expect(response.handled).toBe(true);
    expect(response.commit ?? null).toBeNull();
    expect(tool.phase).toBe('idle');
  });
});
