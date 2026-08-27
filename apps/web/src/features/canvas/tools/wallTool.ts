/**
 * wallTool.ts — W. Ortho-constrained wall chains on the 115 mm module.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE STATE MACHINE
 * ────────────────────────────────────────────────────────────────────────────
 *
 *   idle ──click──▶ drawing ──click──▶ drawing (chain grows)
 *     ▲                │  │                 │
 *     │                │  └──type + Enter───┘   (numeric entry places a segment)
 *     └────Esc─────────┘
 *                      └──Enter / double-click──▶ commit(one group) ──▶ idle
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHY THE CHAIN IS NOT DISPATCHED SEGMENT BY SEGMENT
 * ────────────────────────────────────────────────────────────────────────────
 * §12 asks for the whole chain to be ONE undo group, and the model store has no
 * "extend the previous group" API — nor should it: a group is the atomic unit
 * the server sequences and the inverse is computed against. So the chain lives
 * in the tool, renders as a preview, and becomes N × `wall.add` in a single
 * dispatch when the architect ends it. One undo puts the room back the way it
 * was, which is what "undo" means to the person drawing.
 *
 * The cost of that choice is that Esc discards the whole chain, so Backspace
 * removes the last segment (the CAD idiom) and is documented in the hint copy.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHAT IS CHECKED WHERE (and why not everywhere)
 * ────────────────────────────────────────────────────────────────────────────
 * The rubber-band segment is checked CHEAPLY on every frame: a collinear
 * overlap against this storey's walls, which is integer arithmetic over ~120
 * segments. It is NOT dry-run through `fold`, because folding rebuilds the
 * storey's rooms by planar subdivision (budgeted at 50 ms in §14) and doing
 * that per pointer move would blow the 16 ms frame budget by itself.
 *
 * The commit IS dry-run through the real `applyGroup` (`editOps.validateCommit`),
 * so the group that reaches the store is one `fold` has already accepted, and a
 * refusal quotes the model core's own sentence.
 */

import { distMm, segmentsOverlapCollinear, type Op, type Pt } from '@garh/model';

import { formatLength } from '../../../lib/units';
// Imported from the module, not the `../core` barrel: the barrel pulls in
// `<CanvasRoot>` and therefore React and react-three-fiber, and a tool must be
// constructible in a spec with neither.
import { pointAtLengthMm } from '../core/coords';
import { HINTS, MIN_WALL_LENGTH_MM } from './constants';
import { BaseTool, type PreviewParts } from './baseTool';
import { previewWall, validateCommit, wallAddOp } from './editOps';
import type { NumericField } from './numericEntry';
import { resolveSnap, toSnapView, type SnapCandidate } from './snapping';
import {
  TOOL_RESPONSE_NONE,
  handled,
  type PreviewWall,
  type Readout,
  type ToolBlock,
  type ToolChip,
  type ToolCommit,
  type ToolContext,
  type ToolId,
  type ToolKeyInput,
  type ToolPointerInput,
  type ToolResponse,
} from './types';

/** `length` is the only number the wall tool takes from the keyboard. */
const FIELDS: readonly NumericField[] = [{ id: 'length', label: 'Length', unit: 'mm', minMm: 1 }];

export class WallTool extends BaseTool {
  readonly id: ToolId = 'wall';

  /** Clicked chain points, oldest first. `chain[0]` is where drawing started. */
  private chain: Pt[] = [];

  /** The rubber-band end, snapped. Null before the first move of a segment. */
  private cursor: Pt | null = null;

  private snap: SnapCandidate | null = null;

  /** Live Shift state — Shift INVERTS the ortho setting while held. */
  private shiftHeld = false;

  private blocked: ToolBlock | null = null;

  constructor() {
    super(FIELDS);
  }

  // ── pointer ──────────────────────────────────────────────────────────────

  override onPointerDown(ctx: ToolContext, event: ToolPointerInput): ToolResponse {
    if (event.button !== 0) return TOOL_RESPONSE_NONE;
    if (ctx.storeyId === null) return TOOL_RESPONSE_NONE;

    const point = this.resolve(ctx, event);
    if (point === null) return TOOL_RESPONSE_NONE;

    if (this.phaseState === 'idle') {
      this.chain = [point];
      this.cursor = point;
      this.phaseState = 'drawing';
      this.blocked = null;
      this.touch();
      return handled();
    }

    return this.placePoint(ctx, point);
  }

  override onPointerMove(ctx: ToolContext, event: ToolPointerInput): ToolResponse {
    this.shiftHeld = event.shiftKey;
    const point = this.resolve(ctx, event);
    if (point === null) return TOOL_RESPONSE_NONE;
    if (this.cursor !== null && this.cursor.x === point.x && this.cursor.y === point.y) {
      return TOOL_RESPONSE_NONE;
    }
    this.cursor = point;
    this.touch();
    return { handled: this.phaseState !== 'idle', redraw: true };
  }

  // ── keys ─────────────────────────────────────────────────────────────────

  /**
   * Enter with a typed length places that segment and keeps drawing — the
   * AutoCAD `LINE` idiom. Enter on an empty buffer ends the chain, which is the
   * default path in {@link BaseTool}.
   */
  protected override onEnterKey(ctx: ToolContext): ToolResponse | null {
    if (!this.entryIsActive()) return null;
    if (this.phaseState !== 'drawing') return null;
    const point = this.cursor;
    if (point === null) return null;
    this.consumeEntry();
    return this.placePoint(ctx, point);
  }

  protected override onToolKey(_ctx: ToolContext, event: ToolKeyInput): ToolResponse {
    if (event.key === 'Backspace' && this.phaseState === 'drawing') {
      if (this.chain.length <= 1) {
        this.cancel();
        return handled();
      }
      this.chain = this.chain.slice(0, -1);
      this.blocked = null;
      this.touch();
      return handled();
    }
    return TOOL_RESPONSE_NONE;
  }

  /**
   * A typed length re-derives the rubber end from the direction the mouse
   * already chose. `pointAtLengthMm` rounds half away from zero, so the result
   * is integer mm and identical for a wall drawn east or west.
   */
  protected override onEntryChanged(_ctx: ToolContext): void {
    const anchor = this.anchor();
    const typed = this.typed('length');
    if (anchor !== null && typed !== null && this.cursor !== null) {
      const direction = this.cursor;
      if (direction.x !== anchor.x || direction.y !== anchor.y) {
        this.cursor = pointAtLengthMm(anchor, direction, typed);
      }
    }
    this.touch();
  }

  // ── commit ───────────────────────────────────────────────────────────────

  commit(ctx: ToolContext): ToolCommit | null {
    const storeyId = ctx.storeyId;
    if (storeyId === null) return null;
    if (this.chain.length < 2) return null;

    const ops: Op[] = [];
    const ids: string[] = [];
    for (let i = 0; i < this.chain.length - 1; i++) {
      const a = this.chain[i];
      const b = this.chain[i + 1];
      if (a === undefined || b === undefined) continue;
      // Slipped clicks produce sub-module segments; drop them rather than send
      // them to be rejected as WALL_ZERO_LENGTH.
      if (distMm(a, b) < MIN_WALL_LENGTH_MM) continue;
      const id = ctx.newId('wall');
      ids.push(id);
      ops.push(
        wallAddOp({
          id,
          storeyId,
          a,
          b,
          thicknessMm: ctx.settings.wallThicknessMm,
          kind: ctx.settings.wallKind,
          loadBearing: ctx.settings.wallLoadBearing,
        }),
      );
    }
    if (ops.length === 0) return null;

    const block = validateCommit(ctx.doc, ops);
    if (block !== null) {
      // Keep the chain on screen with the reason attached: throwing the work
      // away because the last segment overlapped something is not a fix.
      this.blocked = block;
      this.touch();
      return null;
    }

    this.blocked = null;
    return {
      ops,
      label: ops.length === 1 ? 'Wall drawn' : `${String(ops.length)} walls drawn`,
      selectIds: ids,
    };
  }

  protected reset(): void {
    this.chain = [];
    this.cursor = null;
    this.snap = null;
    this.blocked = null;
  }

  // ── preview ──────────────────────────────────────────────────────────────

  protected buildPreview(ctx: ToolContext): PreviewParts {
    const segments: PreviewWall[] = [];
    for (let i = 0; i < this.chain.length - 1; i++) {
      const a = this.chain[i];
      const b = this.chain[i + 1];
      if (a === undefined || b === undefined) continue;
      segments.push(previewWall(a, b, ctx.settings.wallThicknessMm, ctx.settings.wallKind));
    }

    const anchor = this.anchor();
    const rubber =
      anchor !== null && this.cursor !== null && distMm(anchor, this.cursor) > 0
        ? previewWall(anchor, this.cursor, ctx.settings.wallThicknessMm, ctx.settings.wallKind)
        : null;

    const readouts: Readout[] = [];
    const chips: ToolChip[] = [];

    if (rubber !== null) {
      readouts.push({
        id: 'length',
        label: 'Length',
        value: formatLength(rubber.lengthMm, ctx.unitsDisplay),
        emphasis: true,
      });
      readouts.push({ id: 'angle', label: 'Angle', value: `${String(rubber.angleDeg)}°` });
      if (this.overlapsExistingWall(ctx, rubber.a, rubber.b)) {
        chips.push({
          id: 'wall-duplicate',
          severity: 'warning',
          text: 'There is already a wall along that line.',
          cite: null,
          fix: 'Move this one off the existing wall, or delete the other first.',
        });
      }
    }

    if (segments.length > 0) {
      const total = segments.reduce((sum, s) => sum + s.lengthMm, 0);
      readouts.push({
        id: 'run',
        label: `Run (${String(segments.length)})`,
        value: formatLength(total, ctx.unitsDisplay),
      });
    }

    readouts.push({
      id: 'thickness',
      label: 'Thickness',
      value: `${String(ctx.settings.wallThicknessMm)} mm`,
    });

    const hint =
      ctx.storeyId === null
        ? HINTS.noStorey
        : this.phaseState === 'idle'
          ? HINTS.wallIdle
          : HINTS.wallDrawing;

    return {
      shape: { kind: 'wall-chain', segments, rubber },
      snap: toSnapView(this.snap),
      readouts,
      chips,
      blocked: this.blocked,
      cursorMm: this.cursor,
      hint,
    };
  }

  // ── internals ────────────────────────────────────────────────────────────

  /** The end of the chain a new segment grows from. */
  private anchor(): Pt | null {
    return this.chain[this.chain.length - 1] ?? null;
  }

  /**
   * Pointer → the point to use, with object snap, ortho, and the typed length
   * applied in that order. Returns null when the ray missed the plane (3D).
   */
  private resolve(ctx: ToolContext, event: ToolPointerInput): Pt | null {
    const raw = event.rawPointMm ?? event.pointMm;
    if (raw === null) return null;
    const anchor = this.anchor();
    const ortho = this.shiftHeld ? !ctx.settings.ortho : ctx.settings.ortho;

    const resolution = resolveSnap(ctx, raw, { anchor, ortho });
    this.snap = resolution.candidate;

    const typed = this.typed('length');
    if (anchor !== null && typed !== null) {
      const direction = resolution.pointMm;
      if (direction.x === anchor.x && direction.y === anchor.y) return resolution.pointMm;
      return pointAtLengthMm(anchor, direction, typed);
    }
    return resolution.pointMm;
  }

  /** Add a point to the chain, ignoring a repeat of the current anchor. */
  private placePoint(ctx: ToolContext, point: Pt): ToolResponse {
    const anchor = this.anchor();
    if (anchor !== null && distMm(anchor, point) < MIN_WALL_LENGTH_MM) {
      // A double-click lands here as a second click in the same spot: treat it
      // as "finish", which is what the gesture means everywhere else.
      const commit = this.commit(ctx);
      if (commit === null) return handled();
      this.afterCommit(ctx);
      return handled({ commit });
    }
    this.chain = [...this.chain, point];
    this.cursor = point;
    this.phaseState = 'drawing';
    this.blocked = null;
    this.consumeEntry();
    this.touch();
    return handled();
  }

  /**
   * Cheap version of the `WALL_DUPLICATE` invariant: does this segment lie
   * along an existing wall on the same storey? Integer-exact, no allocation,
   * O(walls) — see the header for why the real validator is not used here.
   */
  private overlapsExistingWall(ctx: ToolContext, a: Pt, b: Pt): boolean {
    const storeyId = ctx.storeyId;
    if (storeyId === null) return false;
    for (const wall of ctx.doc.house.walls) {
      if (wall.storeyId !== storeyId) continue;
      if (segmentsOverlapCollinear({ a: wall.a, b: wall.b }, { a, b })) return true;
    }
    return false;
  }
}
