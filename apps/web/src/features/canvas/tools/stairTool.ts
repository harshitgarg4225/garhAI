/**
 * stairTool.ts — S. Straight, dogleg, L and U flights, dimensioned from the
 * storey height rather than from a guess.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE STATE MACHINE
 * ────────────────────────────────────────────────────────────────────────────
 *
 *   idle ──move──▶ preview ──click / Enter──▶ commit ──▶ idle
 *                    │  ▲
 *                    │  └── X turns it · [ ] change the type · type a width
 *                    └── Esc
 *
 * The tool is in `preview` as soon as the pointer is over the storey: the whole
 * point is that you SEE the flight — its riser count, its going, whether it
 * clears the wall — before you commit to a position.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHERE THE NUMBERS COME FROM
 * ────────────────────────────────────────────────────────────────────────────
 * `stairFlight.solveFlight` searches integer riser counts for one that lands
 * on the floor above within the model's ±10 mm invariant while keeping the
 * riser under the NBC's 190 mm and near the architect's preferred 165 mm. The
 * tread follows the comfort rule unless the inspector pins one. Nothing here
 * invents a dimension: if no flight fits the storey height, the tool says so
 * and refuses, rather than emitting an op the fold will reject.
 *
 * The footprint drawn is `stairFootprintPolygon` from `@garh/model` — the SAME
 * function `fold` uses to cut the stairwell out of the slab above. A preview
 * that drew its own idea of the footprint would be a preview that lies about
 * the hole in the floor.
 */

import {
  DEFAULTS,
  findStorey,
  stairFootprintPolygon,
  storeyIndex,
  type Direction4,
  type Op,
  type Pt,
  type Stair,
  type StairKind,
} from '@garh/model';

import { formatLength } from '../../../lib/units';
import { HINTS, ROTATE_STEP_DEG } from './constants';
import { BaseTool, type PreviewParts } from './baseTool';
import { stairAddOp, validateCommit } from './editOps';
import type { NumericField } from './numericEntry';
import { resolveSnap, toSnapView, type SnapCandidate } from './snapping';
import { flightIssues, solveFlight, type FlightSolution } from './stairFlight';
import {
  TOOL_RESPONSE_NONE,
  handled,
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

const FIELDS: readonly NumericField[] = [
  { id: 'width', label: 'Flight width', unit: 'mm', minMm: 600, maxMm: 3000 },
  { id: 'tread', label: 'Tread', unit: 'mm', minMm: 200, maxMm: 500 },
];

const KIND_CYCLE: readonly StairKind[] = ['straight', 'dogleg', 'L', 'U'];
const DIRECTION_CYCLE: readonly Direction4[] = ['N', 'E', 'S', 'W'];

/** Forward = travel direction; right = 90° clockwise from it. Mirrors fold.ts. */
const VECTORS: Readonly<Record<Direction4, { fx: number; fy: number; rx: number; ry: number }>> = {
  N: { fx: 0, fy: 1, rx: 1, ry: 0 },
  E: { fx: 1, fy: 0, rx: 0, ry: -1 },
  S: { fx: 0, fy: -1, rx: -1, ry: 0 },
  W: { fx: -1, fy: 0, rx: 0, ry: 1 },
};

export class StairTool extends BaseTool {
  readonly id: ToolId = 'stair';

  private origin: Pt | null = null;

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
      this.origin !== null &&
      this.origin.x === resolution.pointMm.x &&
      this.origin.y === resolution.pointMm.y
    ) {
      return TOOL_RESPONSE_NONE;
    }
    this.origin = resolution.pointMm;
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

  protected override onToolKey(ctx: ToolContext, event: ToolKeyInput): ToolResponse {
    const key = event.key.toLowerCase();
    if (key === 'x') {
      const i = DIRECTION_CYCLE.indexOf(ctx.settings.stairDirection);
      const next = DIRECTION_CYCLE[(i + 1) % DIRECTION_CYCLE.length] ?? 'N';
      this.touch();
      return handled({ settingsPatch: { stairDirection: next } });
    }
    if (key === '[' || key === ']') {
      const i = KIND_CYCLE.indexOf(ctx.settings.stairKind);
      const step = key === ']' ? 1 : KIND_CYCLE.length - 1;
      const next = KIND_CYCLE[(i + step) % KIND_CYCLE.length] ?? 'straight';
      this.touch();
      return handled({ settingsPatch: { stairKind: next } });
    }
    return TOOL_RESPONSE_NONE;
  }

  override wantsKey(event: ToolKeyInput): boolean {
    const key = event.key.toLowerCase();
    if ((key === 'x' || key === '[' || key === ']') && !event.ctrlKey && !event.metaKey) {
      return true;
    }
    return super.wantsKey(event);
  }

  // ── commit ───────────────────────────────────────────────────────────────

  commit(ctx: ToolContext): ToolCommit | null {
    const storeyId = ctx.storeyId;
    const origin = this.origin;
    if (storeyId === null || origin === null) return null;

    const solved = this.solve(ctx);
    if (solved === null) return null;

    const id = ctx.newId('stair');
    const op: Op = stairAddOp({
      id,
      storeyId,
      kind: ctx.settings.stairKind,
      origin,
      direction: ctx.settings.stairDirection,
      riserMm: solved.riserMm,
      treadMm: solved.treadMm,
      widthMm: this.widthMm(ctx),
      risersCount: solved.risersCount,
      landing: solved.landing,
    });

    const block = validateCommit(ctx.doc, [op]);
    if (block !== null) {
      this.blocked = block;
      this.touch();
      return null;
    }

    const typedWidth = this.typed('width');
    if (typedWidth !== null && typedWidth !== ctx.settings.stairWidthMm) {
      this.pendingSettings = { stairWidthMm: typedWidth };
    }

    return {
      ops: [op],
      label: 'Stair added',
      selectIds: [id],
    };
  }

  protected reset(): void {
    this.origin = null;
    this.snap = null;
    this.blocked = null;
  }

  // ── preview ──────────────────────────────────────────────────────────────

  protected buildPreview(ctx: ToolContext): PreviewParts {
    const origin = this.origin;
    const solved = this.solve(ctx);
    const readouts: Readout[] = [];
    const chips: ToolChip[] = [];

    if (origin === null || solved === null) {
      return {
        shape: { kind: 'none' },
        snap: toSnapView(this.snap),
        readouts,
        chips,
        blocked: this.blocked,
        cursorMm: origin,
        hint: ctx.storeyId === null ? HINTS.noStorey : HINTS.stairIdle,
      };
    }

    const widthMm = this.widthMm(ctx);
    const stair = this.previewStair(ctx, origin, solved, widthMm);
    const footprint = stairFootprintPolygon(stair);
    const { treads, arrow } = this.flightLines(stair, solved);

    readouts.push({
      id: 'flight',
      label: 'Flight',
      value: `${String(solved.risersCount)}R × ${String(solved.riserMm)} mm`,
      emphasis: true,
    });
    readouts.push({
      id: 'tread',
      label: 'Tread',
      value: formatLength(solved.treadMm, ctx.unitsDisplay),
    });
    readouts.push({
      id: 'going',
      label: 'Going',
      value: formatLength(solved.goingMm, ctx.unitsDisplay),
    });
    readouts.push({
      id: 'width',
      label: 'Width',
      value: formatLength(widthMm, ctx.unitsDisplay),
    });
    readouts.push({
      id: 'kind',
      label: 'Type',
      value: `${ctx.settings.stairKind} · up ${ctx.settings.stairDirection}`,
    });
    if (solved.riseErrorMm !== 0) {
      readouts.push({
        id: 'rise',
        label: 'Rise vs floor',
        value: `${solved.riseErrorMm > 0 ? '+' : ''}${String(solved.riseErrorMm)} mm`,
      });
    }

    for (const issue of flightIssues(solved, widthMm)) {
      chips.push({
        id: issue.id,
        severity: issue.severity,
        text: issue.text,
        cite: issue.cite,
        fix: issue.fix,
      });
    }

    return {
      shape: {
        kind: 'stair',
        footprint,
        treads,
        arrow,
        risersCount: solved.risersCount,
        riserMm: solved.riserMm,
        treadMm: solved.treadMm,
      },
      snap: toSnapView(this.snap),
      readouts,
      chips,
      blocked: this.blocked,
      cursorMm: origin,
      hint: HINTS.stairIdle,
    };
  }

  // ── internals ────────────────────────────────────────────────────────────

  private widthMm(ctx: ToolContext): number {
    return this.typed('width') ?? ctx.settings.stairWidthMm;
  }

  /**
   * Solve the flight for the active storey, recording the failure as an inline
   * block when there isn't one. Cheap enough to call per frame: it is a loop
   * over at most 33 integer candidates.
   */
  private solve(ctx: ToolContext): FlightSolution | null {
    const storeyId = ctx.storeyId;
    if (storeyId === null) return null;
    const storey = findStorey(ctx.doc.house, storeyId);
    if (storey === undefined) return null;

    const typedTread = this.typed('tread');
    const result = solveFlight({
      storeyHeightMm: storey.heightMm,
      kind: ctx.settings.stairKind,
      widthMm: this.widthMm(ctx),
      preferredRiserMm: ctx.settings.stairPreferredRiserMm,
      ...(typedTread === null ? {} : { treadMm: typedTread }),
      slabThicknessMm: this.slabAboveMm(ctx),
    });

    if (!result.ok) {
      this.blocked = { message: result.failure.reason, fix: result.failure.fix, issues: [] };
      return null;
    }
    if (this.blocked !== null && this.blocked.issues.length === 0) this.blocked = null;
    return result.flight;
  }

  /**
   * Thickness of the slab you would hit your head on: the storey ABOVE's, when
   * there is one. Falls back to this storey's own slab, which is the right
   * assumption for the top floor's mumty and is stated rather than hidden.
   */
  private slabAboveMm(ctx: ToolContext): number {
    const storeyId = ctx.storeyId;
    if (storeyId === null) return DEFAULTS.slabThicknessMm;
    const house = ctx.doc.house;
    const index = storeyIndex(house, storeyId);
    const above = index >= 0 ? house.storeys[index + 1] : undefined;
    const self = findStorey(house, storeyId);
    return above?.level.slabThicknessMm ?? self?.level.slabThicknessMm ?? DEFAULTS.slabThicknessMm;
  }

  /** A `Stair` shaped exactly like the one the op will create, for the preview. */
  private previewStair(
    ctx: ToolContext,
    origin: Pt,
    flight: FlightSolution,
    widthMm: number,
  ): Stair {
    return {
      id: 'stair_preview',
      storeyId: ctx.storeyId ?? 'storey_preview',
      kind: ctx.settings.stairKind,
      origin,
      direction: ctx.settings.stairDirection,
      riserMm: flight.riserMm,
      treadMm: flight.treadMm,
      widthMm,
      risersCount: flight.risersCount,
      landing: flight.landing,
    };
  }

  /**
   * Tread lines and the UP arrow for the first flight, in integer mm.
   *
   * The direction vectors are 0/±1, so every product stays an integer and no
   * rounding happens anywhere in here.
   */
  private flightLines(
    stair: Stair,
    flight: FlightSolution,
  ): { treads: (readonly [Pt, Pt])[]; arrow: readonly [Pt, Pt] | null } {
    const v = VECTORS[stair.direction];
    const { x, y } = stair.origin;
    const at = (forwardMm: number, rightMm: number): Pt => ({
      x: x + v.fx * forwardMm + v.rx * rightMm,
      y: y + v.fy * forwardMm + v.ry * rightMm,
    });

    const treads: (readonly [Pt, Pt])[] = [];
    for (let i = 1; i < flight.risersToLanding; i++) {
      const forward = i * stair.treadMm;
      treads.push([at(forward, 0), at(forward, stair.widthMm)]);
    }

    const half = Math.floor(stair.widthMm / 2);
    const arrow: readonly [Pt, Pt] | null =
      flight.goingMm > stair.treadMm
        ? [at(Math.floor(stair.treadMm / 2), half), at(flight.goingMm, half)]
        : null;

    return { treads, arrow };
  }
}

/** Degrees the `X` key turns a stair by. Exported so the options bar agrees. */
export const STAIR_ROTATE_STEP_DEG = ROTATE_STEP_DEG;
