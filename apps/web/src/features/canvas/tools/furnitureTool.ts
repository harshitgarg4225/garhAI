/**
 * furnitureTool.ts — F. Furniture placement mode, from the catalogue.
 *
 * NOTE (provenance): this file was reconstructed from its spec
 * (`furnitureTool.test.ts`) and the sibling placement tools after the original
 * blob was lost from the repository archive. The spec pins every behaviour
 * below; if an implementation choice here looks odd, the test is the intent.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE STATE MACHINE
 * ────────────────────────────────────────────────────────────────────────────
 *
 *   idle ──move (item chosen)──▶ preview ──click / Enter──▶ commit ──▶ preview
 *                                  │  ▲
 *                                  │  └── X turns it · type an angle
 *                                  └── Esc
 *
 * Committing does NOT disarm the tool: a bedroom needs a bed, two side tables
 * and a wardrobe, and re-picking the item between each would turn one task
 * into four. `afterCommit` therefore keeps the phase and the cursor and only
 * clears the typed angle; Esc is how you leave placement mode.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE RULE WORTH DEFENDING
 * ────────────────────────────────────────────────────────────────────────────
 * **The catalogue is data, not a guess.** With no chosen item — or an id the
 * catalogue has not loaded yet — the tool places nothing and says why, rather
 * than inventing a 900 × 600 box. A placeholder footprint would draw a plan
 * claiming a wardrobe fits where it does not, and the furniture-fit score
 * would then disagree with the drawing.
 *
 * SNAPPING. Furniture sits on the grid module, full stop. The object snap
 * (wall ends, midpoints, plot corners) is for construction geometry; a bed
 * snapped to a wall endpoint is never what anyone meant, so `resolveSnap` is
 * deliberately not consulted and the preview shows no snap marker.
 */

import type { Pt } from '@garh/model';

import type { FurnitureItem } from '../../../lib/schemas';
import { snapMm } from '../../../lib/units';
import { BaseTool, type PreviewParts } from './baseTool';
import { HINTS, ROTATE_STEP_DEG } from './constants';
import { furnitureFootprintMm, furniturePlaceOp, validateCommit } from './editOps';
import type { NumericField } from './numericEntry';
import {
  TOOL_RESPONSE_NONE,
  handled,
  type Readout,
  type ToolBlock,
  type ToolCommit,
  type ToolContext,
  type ToolId,
  type ToolKeyInput,
  type ToolPointerInput,
  type ToolResponse,
} from './types';

const FIELDS: readonly NumericField[] = [{ id: 'rotation', label: 'Rotation', unit: 'deg' }];

/** Any integer → the canonical 0–359 the op schema wants. */
export function normaliseRotationDeg(deg: number): number {
  return ((Math.round(deg) % 360) + 360) % 360;
}

export class FurnitureTool extends BaseTool {
  readonly id: ToolId = 'furniture';

  /** Where the piece would land — grid-snapped. Null until the first move. */
  private cursor: Pt | null = null;

  private blocked: ToolBlock | null = null;

  constructor() {
    super(FIELDS);
  }

  // ── pointer ──────────────────────────────────────────────────────────────

  override onPointerMove(ctx: ToolContext, event: ToolPointerInput): ToolResponse {
    const raw = event.rawPointMm ?? event.pointMm;
    if (raw === null || ctx.storeyId === null) return TOOL_RESPONSE_NONE;

    // Grid module only — see the snapping note in the header.
    const snapped: Pt = {
      x: snapMm(raw.x, ctx.snapModuleMm),
      y: snapMm(raw.y, ctx.snapModuleMm),
    };
    if (this.cursor !== null && this.cursor.x === snapped.x && this.cursor.y === snapped.y) {
      return TOOL_RESPONSE_NONE;
    }
    this.cursor = snapped;

    // Armed only when the catalogue can actually answer for the item.
    if (this.item(ctx) !== null) this.phaseState = 'preview';
    this.touch();
    return { handled: true, redraw: true };
  }

  override onPointerDown(ctx: ToolContext, event: ToolPointerInput): ToolResponse {
    if (event.button !== 0) return TOOL_RESPONSE_NONE;
    this.onPointerMove(ctx, event);
    const commit = this.commit(ctx);
    if (commit === null) return handled();
    this.afterCommit(ctx);
    return handled({ commit, settingsPatch: this.drainSettings() });
  }

  // ── keys ─────────────────────────────────────────────────────────────────

  protected override onToolKey(ctx: ToolContext, event: ToolKeyInput): ToolResponse {
    if (event.key.toLowerCase() === 'x') {
      const step = event.shiftKey ? -ROTATE_STEP_DEG : ROTATE_STEP_DEG;
      const next = normaliseRotationDeg(ctx.settings.furnitureRotationDeg + step);
      this.touch();
      return handled({ settingsPatch: { furnitureRotationDeg: next } });
    }
    return TOOL_RESPONSE_NONE;
  }

  override wantsKey(event: ToolKeyInput): boolean {
    if (event.key.toLowerCase() === 'x' && !event.ctrlKey && !event.metaKey) return true;
    return super.wantsKey(event);
  }

  // ── commit ───────────────────────────────────────────────────────────────

  commit(ctx: ToolContext): ToolCommit | null {
    const storeyId = ctx.storeyId;
    const item = this.item(ctx);
    const cursor = this.cursor;
    if (storeyId === null || item === null || cursor === null) return null;

    const rotationDeg = this.rotationDeg(ctx);
    const id = ctx.newId('furniture');
    const op = furniturePlaceOp({
      id,
      storeyId,
      catalogId: item.id,
      pt: cursor,
      rotationDeg,
    });

    const block = validateCommit(ctx.doc, [op]);
    if (block !== null) {
      this.blocked = block;
      this.touch();
      return null;
    }
    this.blocked = null;

    // A typed angle becomes the next default, the same way a typed width does
    // on the stair tool — the options bar and the tool must agree afterwards.
    if (rotationDeg !== ctx.settings.furnitureRotationDeg) {
      this.pendingSettings = { furnitureRotationDeg: rotationDeg };
    }

    return {
      ops: [op],
      label: `${item.name} placed`,
      selectIds: [id],
    };
  }

  /** Stay armed — see the header. Only the typed angle is consumed. */
  protected override afterCommit(_ctx: ToolContext): void {
    this.consumeEntry();
    this.touch();
  }

  protected reset(): void {
    this.cursor = null;
    this.blocked = null;
  }

  // ── preview ──────────────────────────────────────────────────────────────

  protected buildPreview(ctx: ToolContext): PreviewParts {
    const item = this.item(ctx);

    if (ctx.storeyId === null) {
      return {
        shape: { kind: 'none' },
        blocked: this.blocked,
        cursorMm: this.cursor,
        hint: HINTS.noStorey,
      };
    }

    if (item === null) {
      return {
        shape: { kind: 'none' },
        blocked: this.blocked,
        cursorMm: this.cursor,
        hint: HINTS.furnitureNoItem,
      };
    }

    const cursor = this.cursor;
    if (cursor === null) {
      return {
        shape: { kind: 'none' },
        blocked: this.blocked,
        cursorMm: null,
        hint: HINTS.furnitureIdle,
      };
    }

    const rotationDeg = this.rotationDeg(ctx);
    const readouts: Readout[] = [
      { id: 'item', label: 'Item', value: item.name, emphasis: true },
      { id: 'size', label: 'Size', value: `${String(item.widthMm)} × ${String(item.depthMm)} mm` },
      { id: 'rotation', label: 'Rotation', value: `${String(rotationDeg)}°` },
    ];

    return {
      shape: {
        kind: 'furniture',
        catalogId: item.id,
        centreMm: cursor,
        rotationDeg,
        sizeMm: furnitureFootprintMm(item.widthMm, item.depthMm, rotationDeg),
      },
      // Always null: furniture ignores the object snap (see the header).
      snap: null,
      readouts,
      blocked: this.blocked,
      cursorMm: cursor,
      hint: HINTS.furnitureIdle,
    };
  }

  // ── internals ────────────────────────────────────────────────────────────

  /** The chosen catalogue item, or null when unchosen or not yet loaded. */
  private item(ctx: ToolContext): FurnitureItem | null {
    const id = ctx.settings.furnitureCatalogId;
    if (id === null) return null;
    return ctx.furnitureCatalog.get(id) ?? null;
  }

  /** Typed angle beats the setting (§12: typing overrides the mouse). */
  private rotationDeg(ctx: ToolContext): number {
    const typed = this.typed('rotation');
    return normaliseRotationDeg(typed ?? ctx.settings.furnitureRotationDeg);
  }
}
