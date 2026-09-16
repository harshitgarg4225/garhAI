/**
 * columnTool.ts — C. Place a coordination column by hand.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE STATE MACHINE
 * ────────────────────────────────────────────────────────────────────────────
 *
 *   idle ──move──▶ preview ──click / Enter──▶ commit(column.set add) ──▶ preview
 *                    │  ▲
 *                    │  └── X turns it · type a width or a depth
 *                    └── Esc
 *
 * The tool stays armed after a placement: a column grid is placed a dozen at a
 * time, and walking back to the rail between each is the kind of friction §15
 * budgets against. The centre snaps like every other point (wall ends and
 * midpoints beat the grid), because a column sits at a wall junction far more
 * often than at an arbitrary module.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHAT A COLUMN IS, HONESTLY
 * ────────────────────────────────────────────────────────────────────────────
 * `column.set` (op 24) is a coordination column: a footprint the structural
 * engineer will size, drawn so the plan and the 3D view show where the frame
 * is. It changes no area and no rule; the solver places them too, and this
 * tool is how an architect adds, moves and re-sizes what the solver placed.
 */

import type { Op, Pt, SizeMm } from '@garh/model';

import { formatLength } from '../../../lib/units';
import { HINTS } from './constants';
import { BaseTool, type PreviewParts } from './baseTool';
import { columnAddOp, validateCommit } from './editOps';
import type { NumericField } from './numericEntry';
import { resolveSnap, toSnapView, type SnapCandidate } from './snapping';
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

const FIELDS: readonly NumericField[] = [
  { id: 'width', label: 'Width', unit: 'mm', minMm: 100, maxMm: 2000 },
  { id: 'depth', label: 'Depth', unit: 'mm', minMm: 100, maxMm: 2000 },
];

export class ColumnTool extends BaseTool {
  readonly id: ToolId = 'column';

  private centre: Pt | null = null;

  private snap: SnapCandidate | null = null;

  private blocked: ToolBlock | null = null;

  constructor() {
    super(FIELDS);
  }

  // ── pointer ──────────────────────────────────────────────────────────────

  override onPointerMove(ctx: ToolContext, event: ToolPointerInput): ToolResponse {
    const raw = event.rawPointMm ?? event.pointMm;
    if (raw === null || ctx.storeyId === null) return TOOL_RESPONSE_NONE;
    const resolution = resolveSnap(ctx, raw, {});
    this.snap = resolution.candidate;
    if (
      this.centre !== null &&
      this.centre.x === resolution.pointMm.x &&
      this.centre.y === resolution.pointMm.y
    ) {
      return TOOL_RESPONSE_NONE;
    }
    this.centre = resolution.pointMm;
    this.phaseState = 'preview';
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

  /** X turns the column: width and depth swap, as they do for furniture. */
  protected override onToolKey(ctx: ToolContext, event: ToolKeyInput): ToolResponse {
    if (event.key.toLowerCase() !== 'x' || event.ctrlKey || event.metaKey) {
      return TOOL_RESPONSE_NONE;
    }
    const size = this.sizeMm(ctx);
    this.touch();
    return handled({ settingsPatch: { columnSizeMm: { xMm: size.yMm, yMm: size.xMm } } });
  }

  override wantsKey(event: ToolKeyInput): boolean {
    if (event.key.toLowerCase() === 'x' && !event.ctrlKey && !event.metaKey) return true;
    return super.wantsKey(event);
  }

  // ── commit ───────────────────────────────────────────────────────────────

  commit(ctx: ToolContext): ToolCommit | null {
    const storeyId = ctx.storeyId;
    const centre = this.centre;
    if (storeyId === null || centre === null) return null;

    const sizeMm = this.sizeMm(ctx);
    const id = ctx.newId('column');
    const op: Op = columnAddOp({ id, storeyId, pt: centre, sizeMm });

    const block = validateCommit(ctx.doc, [op]);
    if (block !== null) {
      this.blocked = block;
      this.touch();
      return null;
    }
    this.blocked = null;

    // A typed size becomes the default for the next column, like a typed
    // stair width does — the grid is usually one size.
    if (
      sizeMm.xMm !== ctx.settings.columnSizeMm.xMm ||
      sizeMm.yMm !== ctx.settings.columnSizeMm.yMm
    ) {
      this.pendingSettings = { columnSizeMm: sizeMm };
    }

    return { ops: [op], label: 'Column placed', selectIds: [id] };
  }

  /** Stay armed after a placement, keeping the pointer's position. */
  protected override afterCommit(_ctx: ToolContext): void {
    this.consumeEntry();
    this.blocked = null;
    this.touch();
  }

  protected reset(): void {
    this.centre = null;
    this.snap = null;
    this.blocked = null;
  }

  // ── preview ──────────────────────────────────────────────────────────────

  protected buildPreview(ctx: ToolContext): PreviewParts {
    const centre = this.centre;
    if (centre === null || ctx.storeyId === null) {
      return {
        shape: { kind: 'none' },
        snap: toSnapView(this.snap),
        readouts: [],
        blocked: this.blocked,
        cursorMm: centre,
        hint: ctx.storeyId === null ? HINTS.noStorey : HINTS.columnIdle,
      };
    }
    const sizeMm = this.sizeMm(ctx);
    const readouts: Readout[] = [
      {
        id: 'size',
        label: 'Size',
        value: `${formatLength(sizeMm.xMm, ctx.unitsDisplay)} × ${formatLength(
          sizeMm.yMm,
          ctx.unitsDisplay,
        )}`,
        emphasis: true,
      },
      { id: 'at', label: 'Centre', value: `${String(centre.x)} , ${String(centre.y)} mm` },
    ];
    return {
      shape: { kind: 'column', centreMm: centre, sizeMm },
      snap: toSnapView(this.snap),
      readouts,
      blocked: this.blocked,
      cursorMm: centre,
      hint: HINTS.columnIdle,
    };
  }

  // ── internals ────────────────────────────────────────────────────────────

  private sizeMm(ctx: ToolContext): SizeMm {
    return {
      xMm: this.typed('width') ?? ctx.settings.columnSizeMm.xMm,
      yMm: this.typed('depth') ?? ctx.settings.columnSizeMm.yMm,
    };
  }
}
