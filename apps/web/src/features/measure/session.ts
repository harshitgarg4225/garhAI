/**
 * session.ts — the measure state machine.
 *
 * Shaped exactly like the tools in `features/canvas/tools`: a plain class with
 * its own mutable state, fed values (a `ToolContext`, a `ToolPointerInput`) and
 * returning values. No React, no store, no three.js — which is why the whole
 * thing is testable with three function calls and no renderer, and why
 * `session.test.ts` can assert the snap result directly.
 *
 * It is NOT a `Tool`, and that is deliberate. A `Tool` communicates by
 * returning `Op`s for the model store to apply; a measurement is not an op and
 * must never become one (see `types.ts`). So the session returns the committed
 * {@link Measurement} as a value and lets its controller decide where to put
 * it, rather than borrowing a channel that means "change the building".
 *
 * ────────────────────────────────────────────────────────────────────────────
 * SNAPPING IS THE DRAWING TOOLS' SNAPPING. NOT A COPY OF IT.
 * ────────────────────────────────────────────────────────────────────────────
 * Every point resolves through `resolveSnap` from `canvas/tools/snapping.ts` —
 * the same function, with the same candidate ranking, that the wall tool uses.
 * This is the whole credibility of the feature: "how far is that wall from this
 * one" is only an answer if the measurement starts ON the wall rather than near
 * it. A measure tool that snapped to the grid while the wall tool snapped to
 * endpoints would report a number the building does not have, and it would be
 * off by a plausible-looking amount rather than an obvious one.
 *
 * Ortho is opt-IN (Shift), the inverse of the drawing tools, for the reason
 * `measureTool.ts` gives: measuring a diagonal is normal, drawing a diagonal
 * wall is not.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE THREE MACHINES
 * ────────────────────────────────────────────────────────────────────────────
 *   distance  click ▸ click ▸ … ▸ Enter/double-click    (chain: legs + total)
 *   angle     click ▸ CLICK(vertex) ▸ click             (commits on the third)
 *   area      click ▸ click ▸ click ▸ … ▸ close/Enter   (m² and ft²)
 *
 * Esc always discards the draft without committing, and Backspace steps back
 * one point — the same escape ladder every tool in this product offers, because
 * a measure tool that trapped you would be abandoned within a day.
 */

import { distMm, ptEq, type Pt } from '@garh/model';

import {
  resolveSnap,
  snapToleranceMm,
  toSnapView,
  type SnapCandidate,
} from '../canvas/tools/snapping';
import type {
  Readout,
  SnapView,
  ToolContext,
  ToolKeyInput,
  ToolPointerInput,
} from '../canvas/tools/types';
import { measureReadouts } from './format';
import { closesRing, ringAreaMm2 } from './geometry';
import { MEASURE_ID_PREFIX, type MeasureDraft, type MeasureKind, type Measurement } from './types';

// ---------------------------------------------------------------------------
// Ids
// ---------------------------------------------------------------------------

/**
 * Module-scoped so two sessions (a remount, a second canvas in a future
 * split view) cannot mint the same id. Specs inject their own factory rather
 * than resetting this, which is what keeps their expectations byte-stable.
 */
let idCounter = 0;

export function createMeasureIdFactory(): () => string {
  return () => {
    idCounter += 1;
    return `${MEASURE_ID_PREFIX}${String(idCounter)}`;
  };
}

// ---------------------------------------------------------------------------
// Response
// ---------------------------------------------------------------------------

export interface MeasureResponse {
  /** True when the session consumed the event; the caller must not fall through. */
  readonly handled: boolean;
  /** The screen changed — ask the canvas for a frame. */
  readonly redraw: boolean;
  /** A finished measurement for the caller to persist. */
  readonly committed: Measurement | null;
  /**
   * Why a commit that was asked for did not happen — a degenerate ring, too few
   * points. Shown inline; never thrown, never silently swallowed.
   */
  readonly blocked: string | null;
}

const NONE: MeasureResponse = { handled: false, redraw: false, committed: null, blocked: null };

function ok(extra: Partial<Omit<MeasureResponse, 'handled'>> = {}): MeasureResponse {
  return { handled: true, redraw: true, committed: null, blocked: null, ...extra };
}

// ---------------------------------------------------------------------------
// Hints (§15: the HUD always says what the next click does)
// ---------------------------------------------------------------------------

export const MEASURE_HINTS: Readonly<Record<MeasureKind, readonly [string, string]>> = {
  distance: [
    'Click the first point',
    'Click to add a leg · Enter or double-click to finish · Backspace undoes a point',
  ],
  angle: ['Click the first arm', 'Click the CORNER, then the second arm'],
  area: [
    'Click the first corner of the region',
    'Click each corner · click the first again or press Enter to close',
  ],
};

export interface MeasureSessionOptions {
  readonly kind?: MeasureKind | undefined;
  readonly newId?: (() => string) | undefined;
  readonly now?: (() => number) | undefined;
}

// ---------------------------------------------------------------------------
// The session
// ---------------------------------------------------------------------------

export class MeasureSession {
  private kindState: MeasureKind;

  private points: Pt[] = [];

  private cursor: Pt | null = null;

  private snap: SnapCandidate | null = null;

  private shiftHeld = false;

  private willCloseState = false;

  private readonly mintId: () => string;

  private readonly now: () => number;

  constructor(options: MeasureSessionOptions = {}) {
    this.kindState = options.kind ?? 'distance';
    this.mintId = options.newId ?? createMeasureIdFactory();
    this.now = options.now ?? (() => Date.now());
  }

  get kind(): MeasureKind {
    return this.kindState;
  }

  /** True while points have been clicked but nothing has been committed. */
  get active(): boolean {
    return this.points.length > 0;
  }

  /**
   * Change what is being measured. Any half-finished draft is DISCARDED: three
   * points meant as a chain are not the same three points meant as an angle,
   * and carrying them across would silently reinterpret the architect's clicks.
   */
  setKind(kind: MeasureKind): MeasureResponse {
    if (kind === this.kindState) return NONE;
    this.kindState = kind;
    this.reset();
    return ok();
  }

  // ── pointer ──────────────────────────────────────────────────────────────

  pointerMove(ctx: ToolContext, event: ToolPointerInput): MeasureResponse {
    this.shiftHeld = event.shiftKey;
    const point = this.resolve(ctx, event);
    if (point === null) return NONE;
    this.willCloseState =
      this.kindState === 'area' && closesRing(this.points, point, snapToleranceMm(ctx.mmPerPx));
    if (this.cursor !== null && ptEq(this.cursor, point)) return NONE;
    this.cursor = point;
    // A move with nothing started is still worth a redraw — the snap marker
    // follows the pointer before the first click, which is how an architect
    // knows the tool will land on the wall end and not beside it.
    return { handled: this.active, redraw: true, committed: null, blocked: null };
  }

  pointerDown(ctx: ToolContext, event: ToolPointerInput): MeasureResponse {
    if (event.button !== 0) return NONE;
    this.shiftHeld = event.shiftKey;
    const point = this.resolve(ctx, event);
    if (point === null) return NONE;
    this.cursor = point;

    // Closing click on an area ring: the first vertex is NOT stored twice — the
    // closing edge is implied (see `Measurement.points`), and a duplicated
    // vertex would add a zero-length edge to the perimeter.
    if (this.kindState === 'area' && closesRing(this.points, point, snapToleranceMm(ctx.mmPerPx))) {
      return this.commit(ctx);
    }

    const last = this.points[this.points.length - 1];
    if (last !== undefined && ptEq(last, point)) {
      // A second press at the same place — the first half of a double-click, or
      // an impatient re-click. Consumed so it cannot fall through to the canvas,
      // but never appended: a zero-length leg would sit in the readouts and drag
      // the total nowhere.
      return ok();
    }

    this.points.push(point);
    this.willCloseState = false;

    // The angle machine is the one that knows when it is done: three points is
    // an angle, and asking for an Enter after the third click would be asking
    // for a keystroke that can only mean one thing.
    if (this.kindState === 'angle' && this.points.length === 3) return this.commit(ctx);

    return ok();
  }

  /** Double-click finishes a chain — the CAD idiom for "that was the last point". */
  doubleClick(ctx: ToolContext): MeasureResponse {
    if (!this.active) return NONE;
    return this.commit(ctx);
  }

  // ── keys ─────────────────────────────────────────────────────────────────

  key(ctx: ToolContext, event: ToolKeyInput): MeasureResponse {
    if (event.key === 'Escape') {
      if (!this.active && this.cursor === null) return NONE;
      this.reset();
      return ok();
    }
    if (event.key === 'Enter') {
      if (!this.active) return NONE;
      return this.commit(ctx);
    }
    if (event.key === 'Backspace') {
      if (!this.active) return NONE;
      this.points.pop();
      return ok();
    }
    return NONE;
  }

  // ── commit ───────────────────────────────────────────────────────────────

  /**
   * Turn the draft into a measurement, or say why not.
   *
   * The refusals are real refusals, not defensive noise: two coincident points
   * are a mis-click, and a three-point ring whose vertices are collinear has no
   * area. Committing either would put a confident wrong number on the drawing,
   * which is the failure mode this codebase keeps paying for.
   */
  commit(ctx: ToolContext): MeasureResponse {
    const points = this.points;
    const blocked = measureBlockReason(this.kindState, points);
    if (blocked !== null) return { handled: true, redraw: false, committed: null, blocked };

    const measurement: Measurement = {
      id: this.mintId(),
      kind: this.kindState,
      points: points.slice(),
      storeyId: ctx.storeyId,
      createdAt: this.now(),
    };
    this.reset();
    return { handled: true, redraw: true, committed: measurement, blocked: null };
  }

  cancel(): void {
    this.reset();
  }

  // ── what the screen needs ────────────────────────────────────────────────

  /** `null` when nothing is being drawn — the layer skips the whole pass. */
  draft(): MeasureDraft | null {
    if (!this.active) return null;
    return {
      kind: this.kindState,
      points: this.points.slice(),
      cursor: this.cursor,
      willClose: this.willCloseState,
    };
  }

  /** The snap marker, or null when the grid (or nothing) decided the point. */
  snapView(): SnapView | null {
    return toSnapView(this.snap);
  }

  /** Live numbers for the HUD — the same function the committed list uses. */
  readouts(ctx: ToolContext): Readout[] {
    return measureReadouts(this.kindState, this.previewPoints(), ctx.unitsDisplay);
  }

  hint(): string {
    const pair = MEASURE_HINTS[this.kindState];
    return this.active ? pair[1] : pair[0];
  }

  // ── internals ────────────────────────────────────────────────────────────

  /**
   * The points the readouts describe: what has been clicked, plus the pointer.
   * A rubber-band leg is a leg — the number has to move with the mouse or the
   * tool is a two-step form rather than a measurement.
   */
  private previewPoints(): Pt[] {
    if (this.cursor === null) return this.points.slice();
    const last = this.points[this.points.length - 1];
    if (last !== undefined && ptEq(last, this.cursor)) return this.points.slice();
    return [...this.points, this.cursor];
  }

  private resolve(ctx: ToolContext, event: ToolPointerInput): Pt | null {
    const raw = event.rawPointMm ?? event.pointMm;
    if (raw === null) return null;
    const anchor = this.points[this.points.length - 1] ?? null;
    const resolution = resolveSnap(ctx, raw, { anchor, ortho: this.shiftHeld });
    this.snap = resolution.candidate;
    return resolution.pointMm;
  }

  private reset(): void {
    this.points = [];
    this.cursor = null;
    this.snap = null;
    this.willCloseState = false;
  }
}

// ---------------------------------------------------------------------------
// Validity
// ---------------------------------------------------------------------------

/**
 * Why `points` cannot be committed as `kind`, or null when they can.
 *
 * Exported because it is the contract, not an implementation detail: the HUD
 * disables its Finish button on the same answer the session refuses on, so the
 * two can never disagree about whether a measurement is finishable.
 */
export function measureBlockReason(kind: MeasureKind, points: readonly Pt[]): string | null {
  const first = points[0];
  switch (kind) {
    case 'distance': {
      if (points.length < 2) return 'A distance needs two points.';
      const second = points[1];
      if (points.length === 2 && first !== undefined && second !== undefined) {
        if (distMm(first, second) === 0) return 'Those two points are the same point.';
      }
      return null;
    }
    case 'angle':
      return points.length === 3 ? null : 'An angle needs three points: arm, corner, arm.';
    case 'area': {
      if (points.length < 3) return 'An area needs at least three corners.';
      if (ringAreaMm2(points) === 0) return 'Those corners are in a straight line — no area.';
      return null;
    }
  }
}
