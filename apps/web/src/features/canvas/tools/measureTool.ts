/**
 * measureTool.ts — M. Two-point and chained measurement.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE ONE TOOL THAT EMITS NOTHING
 * ────────────────────────────────────────────────────────────────────────────
 * `commit()` returns null, always. Measuring is not an edit: it changes no
 * geometry, appends no op, and must not create an undo entry — a measurement
 * that showed up in the version timeline would be noise in the one place the
 * project's history has to stay readable.
 *
 * That is also why it is worth having as a real tool rather than a hover
 * readout: an architect checking a corridor measures four times before touching
 * anything, and each of those four is free.
 *
 *   idle ──click──▶ drawing ──click──▶ drawing (chain) ──Enter/Esc──▶ idle
 *
 * Every leg is snapped exactly like a wall (endpoints, midpoints, plot edges),
 * because "how far is this wall from that one" is only useful if it starts and
 * ends on the walls rather than near them. Ortho is OFF by default here: a
 * diagonal is a perfectly good thing to measure, and Shift turns the constraint
 * on when you want it.
 *
 * The readout is in the project's display units (`formatLength`), with the
 * millimetres alongside, because the drawing set dimensions in mm and the
 * conversation happens in feet.
 */

import { distMm, type Pt } from '@garh/model';

import { formatIndianNumber, formatLength } from '../../../lib/units';
import { HINTS } from './constants';
import { BaseTool, type PreviewParts } from './baseTool';
import { angleDeg } from './editOps';
import { pointAtLengthMm } from '../core/coords';
import type { NumericField } from './numericEntry';
import { resolveSnap, toSnapView, type SnapCandidate } from './snapping';
import {
  TOOL_RESPONSE_NONE,
  handled,
  type Readout,
  type ToolCommit,
  type ToolContext,
  type ToolId,
  type ToolKeyInput,
  type ToolPointerInput,
  type ToolResponse,
} from './types';

const FIELDS: readonly NumericField[] = [
  { id: 'length', label: 'Leg length', unit: 'mm', minMm: 1 },
];

export class MeasureTool extends BaseTool {
  readonly id: ToolId = 'measure';

  private points: Pt[] = [];

  private cursor: Pt | null = null;

  private snap: SnapCandidate | null = null;

  private shiftHeld = false;

  constructor() {
    super(FIELDS);
  }

  // ── pointer ──────────────────────────────────────────────────────────────

  override onPointerDown(ctx: ToolContext, event: ToolPointerInput): ToolResponse {
    if (event.button !== 0) return TOOL_RESPONSE_NONE;
    const point = this.resolve(ctx, event);
    if (point === null) return TOOL_RESPONSE_NONE;
    this.points = [...this.points, point];
    this.cursor = point;
    this.phaseState = 'drawing';
    this.consumeEntry();
    this.touch();
    return handled();
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

  protected override onToolKey(_ctx: ToolContext, event: ToolKeyInput): ToolResponse {
    if (event.key === 'Backspace' && this.phaseState === 'drawing') {
      if (this.points.length <= 1) {
        this.cancel();
        return handled();
      }
      this.points = this.points.slice(0, -1);
      this.touch();
      return handled();
    }
    return TOOL_RESPONSE_NONE;
  }

  protected override onEntryChanged(_ctx: ToolContext): void {
    const anchor = this.points[this.points.length - 1];
    const typed = this.typed('length');
    if (anchor !== undefined && typed !== null && this.cursor !== null) {
      if (this.cursor.x !== anchor.x || this.cursor.y !== anchor.y) {
        this.cursor = pointAtLengthMm(anchor, this.cursor, typed);
      }
    }
    this.touch();
  }

  /**
   * Enter ends the measurement. It cannot "commit" — there is nothing to
   * commit — so it resets, and returning `handled` stops the keystroke
   * bubbling to the canvas's Enter binding.
   */
  protected override onEnterKey(_ctx: ToolContext): ToolResponse | null {
    if (this.phaseState === 'idle') return null;
    this.cancel();
    return handled();
  }

  // ── commit: never ────────────────────────────────────────────────────────

  commit(_ctx: ToolContext): ToolCommit | null {
    return null;
  }

  protected reset(): void {
    this.points = [];
    this.cursor = null;
    this.snap = null;
  }

  // ── preview ──────────────────────────────────────────────────────────────

  protected buildPreview(ctx: ToolContext): PreviewParts {
    const chain = this.chainPoints();
    const segments: number[] = [];
    for (let i = 0; i < chain.length - 1; i++) {
      const a = chain[i];
      const b = chain[i + 1];
      if (a === undefined || b === undefined) continue;
      segments.push(distMm(a, b));
    }
    const total = segments.reduce((sum, mm) => sum + mm, 0);

    const readouts: Readout[] = [];
    const last = segments[segments.length - 1];
    if (last !== undefined) {
      readouts.push({
        id: 'leg',
        label: 'Length',
        value: `${formatLength(last, ctx.unitsDisplay)} · ${formatIndianNumber(last)} mm`,
        emphasis: true,
      });
      const a = chain[chain.length - 2];
      const b = chain[chain.length - 1];
      if (a !== undefined && b !== undefined) {
        readouts.push({ id: 'angle', label: 'Angle', value: `${String(angleDeg(a, b))}°` });
        readouts.push({
          id: 'dxdy',
          label: 'Δx, Δy',
          value: `${formatIndianNumber(b.x - a.x)} , ${formatIndianNumber(b.y - a.y)} mm`,
        });
      }
    }
    if (segments.length > 1) {
      readouts.push({
        id: 'total',
        label: `Total (${String(segments.length)} legs)`,
        value: `${formatLength(total, ctx.unitsDisplay)} · ${formatIndianNumber(total)} mm`,
      });
    }

    return {
      shape: {
        kind: 'measure',
        points: this.points,
        rubber: this.phaseState === 'drawing' ? this.cursor : null,
        segmentsMm: segments,
        totalMm: total,
      },
      snap: toSnapView(this.snap),
      readouts,
      cursorMm: this.cursor,
      hint: this.phaseState === 'idle' ? HINTS.measureIdle : HINTS.measureDrawing,
    };
  }

  // ── internals ────────────────────────────────────────────────────────────

  private chainPoints(): Pt[] {
    if (this.phaseState === 'drawing' && this.cursor !== null) {
      return [...this.points, this.cursor];
    }
    return this.points;
  }

  private resolve(ctx: ToolContext, event: ToolPointerInput): Pt | null {
    const raw = event.rawPointMm ?? event.pointMm;
    if (raw === null) return null;
    const anchor = this.points[this.points.length - 1] ?? null;
    // Ortho is opt-IN here (Shift), the inverse of the drawing tools: measuring
    // a diagonal is normal, drawing a diagonal wall is not.
    const resolution = resolveSnap(ctx, raw, { anchor, ortho: this.shiftHeld });
    this.snap = resolution.candidate;

    const typed = this.typed('length');
    if (anchor !== null && typed !== null) {
      const direction = resolution.pointMm;
      if (direction.x !== anchor.x || direction.y !== anchor.y) {
        return pointAtLengthMm(anchor, direction, typed);
      }
    }
    return resolution.pointMm;
  }
}
