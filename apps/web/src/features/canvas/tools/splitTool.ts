/**
 * splitTool.ts — K. Cut a wall in two where you click.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE STATE MACHINE
 * ────────────────────────────────────────────────────────────────────────────
 *
 *   idle ──hover a wall──▶ preview ──click / Enter──▶ commit(wall.split) ──▶ idle
 *                            │  ▲
 *                            │  └── type a distance from the wall start
 *                            └── Esc, or the pointer leaves the wall
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHERE THE CUT LANDS
 * ────────────────────────────────────────────────────────────────────────────
 * `wall.split` (op 11) takes a distance along the wall from its `a` end, so the
 * pointer is projected onto the centreline and the cut is the along-wall
 * coordinate. Three things decide it, in this order:
 *
 *   1. a typed distance — the §12 "typing a number overrides the mouse" rule;
 *   2. an object snap ON this wall: the end of another wall that meets it (a
 *      T-junction is the commonest reason to split), or its midpoint;
 *   3. the grid module along the wall, measured from `a`.
 *
 * The result is clamped into `wallSplitWindow` — strictly inside the wall — so
 * the preview can never show a cut the fold would refuse as
 * `WALL_SPLIT_OUT_OF_RANGE`. Which wall is hovered comes from the one picker
 * (`event.hit()`), with the same geometric fallback the select tool uses for a
 * mesh that has not mounted yet.
 */

import { distMm, findWall, pointAlongSeg, type Op, type Pt, type Wall } from '@garh/model';

import { formatLength, snapMm } from '../../../lib/units';
import { HINTS } from './constants';
import { BaseTool, type PreviewParts } from './baseTool';
import { clampSplitAt, splitWallOps, validateCommit, wallSplitWindow } from './editOps';
import type { NumericField } from './numericEntry';
import { collectSnapCandidates, projectOnSegment, snapToleranceMm, toSnapView } from './snapping';
import type { SnapCandidate } from './snapping';
import {
  TOOL_RESPONSE_NONE,
  handled,
  type Readout,
  type ToolBlock,
  type ToolCommit,
  type ToolContext,
  type ToolId,
  type ToolPointerInput,
  type ToolResponse,
} from './types';

const FIELDS: readonly NumericField[] = [
  { id: 'distance', label: 'From wall start', unit: 'mm', minMm: 1 },
];

interface Cut {
  readonly wall: Wall;
  readonly atMm: number;
  readonly lengthMm: number;
}

export class SplitTool extends BaseTool {
  readonly id: ToolId = 'split';

  private cut: Cut | null = null;

  private snap: SnapCandidate | null = null;

  private blocked: ToolBlock | null = null;

  private cursor: Pt | null = null;

  constructor() {
    super(FIELDS);
  }

  // ── pointer ──────────────────────────────────────────────────────────────

  override onPointerMove(ctx: ToolContext, event: ToolPointerInput): ToolResponse {
    const point = event.rawPointMm ?? event.pointMm;
    this.cursor = point;
    const previous = this.cut;
    this.recompute(ctx, point, event);
    const changed = previous?.wall.id !== this.cut?.wall.id || previous?.atMm !== this.cut?.atMm;
    if (!changed) return TOOL_RESPONSE_NONE;
    this.touch();
    return { handled: this.cut !== null, redraw: true };
  }

  override onPointerDown(ctx: ToolContext, event: ToolPointerInput): ToolResponse {
    if (event.button !== 0) return TOOL_RESPONSE_NONE;
    const point = event.rawPointMm ?? event.pointMm;
    this.recompute(ctx, point, event);
    const commit = this.commit(ctx);
    if (commit === null) return handled();
    this.afterCommit(ctx);
    return handled({ commit });
  }

  // ── keys ─────────────────────────────────────────────────────────────────

  protected override onEntryChanged(ctx: ToolContext): void {
    const cut = this.cut;
    const typed = this.typed('distance');
    if (cut !== null && typed !== null) {
      const clamped = clampSplitAt(typed, cut.lengthMm);
      if (clamped !== null) this.cut = { ...cut, atMm: clamped };
    } else if (cut !== null && this.cursor !== null) {
      // Buffer cleared: back to what the pointer says.
      this.recompute(ctx, this.cursor, null);
    }
    this.touch();
  }

  // ── commit ───────────────────────────────────────────────────────────────

  commit(ctx: ToolContext): ToolCommit | null {
    const cut = this.cut;
    if (cut === null) return null;
    const newWallId = ctx.newId('wall');
    const ops: Op[] = splitWallOps(ctx.doc, cut.wall.id, cut.atMm, newWallId);
    if (ops.length === 0) return null;
    const block = validateCommit(ctx.doc, ops);
    if (block !== null) {
      this.blocked = block;
      this.touch();
      return null;
    }
    this.blocked = null;
    return { ops, label: 'Wall split', selectIds: [cut.wall.id, newWallId] };
  }

  protected reset(): void {
    this.cut = null;
    this.snap = null;
    this.blocked = null;
  }

  // ── preview ──────────────────────────────────────────────────────────────

  protected buildPreview(ctx: ToolContext): PreviewParts {
    const cut = this.cut;
    if (cut === null) {
      return {
        shape: { kind: 'none' },
        snap: toSnapView(this.snap),
        readouts: [],
        blocked: this.blocked,
        cursorMm: this.cursor,
        hint: ctx.storeyId === null ? HINTS.noStorey : HINTS.splitIdle,
      };
    }
    const pointMm = pointAlongSeg({ a: cut.wall.a, b: cut.wall.b }, cut.atMm);
    const readouts: Readout[] = [
      {
        id: 'distance',
        label: 'From wall start',
        value: formatLength(cut.atMm, ctx.unitsDisplay),
        emphasis: true,
      },
      {
        id: 'remaining',
        label: 'To wall end',
        value: formatLength(cut.lengthMm - cut.atMm, ctx.unitsDisplay),
      },
    ];
    return {
      shape: {
        kind: 'split',
        wallId: cut.wall.id,
        pointMm,
        cut: cutLine(cut.wall, pointMm),
      },
      snap: toSnapView(this.snap),
      readouts,
      blocked: this.blocked,
      cursorMm: pointMm,
      hint: HINTS.splitPreview,
    };
  }

  // ── internals ────────────────────────────────────────────────────────────

  /**
   * Decide the wall and the cut for a pointer position. `event` may be null
   * when re-deriving after the numeric buffer is cleared.
   */
  private recompute(ctx: ToolContext, point: Pt | null, event: ToolPointerInput | null): void {
    if (point === null || ctx.storeyId === null) {
      this.cut = null;
      this.snap = null;
      this.phaseState = 'idle';
      return;
    }
    const wall = this.hoveredWall(ctx, point, event);
    if (wall === null) {
      this.cut = null;
      this.snap = null;
      this.phaseState = 'idle';
      return;
    }
    const lengthMm = distMm(wall.a, wall.b);
    if (wallSplitWindow(lengthMm) === null) {
      this.cut = null;
      this.snap = null;
      this.phaseState = 'idle';
      return;
    }

    const typed = this.typed('distance');
    let atMm: number;
    this.snap = null;
    if (typed !== null) {
      atMm = typed;
    } else {
      const onWall = this.snapOnWall(ctx, point, wall);
      if (onWall !== null) {
        this.snap = onWall;
        atMm = projectOnSegment(onWall.pointMm, wall.a, wall.b).alongMm;
      } else {
        const projection = projectOnSegment(point, wall.a, wall.b);
        atMm = snapMm(projection.alongMm, ctx.snapModuleMm);
      }
    }
    const clamped = clampSplitAt(atMm, lengthMm);
    if (clamped === null) {
      this.cut = null;
      this.phaseState = 'idle';
      return;
    }
    this.cut = { wall, atMm: clamped, lengthMm };
    this.phaseState = 'preview';
  }

  /** The wall under the pointer: the picker's answer, else the nearest wall. */
  private hoveredWall(ctx: ToolContext, point: Pt, event: ToolPointerInput | null): Wall | null {
    if (event !== null) {
      const hit = event.hit();
      if (hit.kind === 'wall' && hit.id !== null) {
        const picked = findWall(ctx.doc.house, hit.id);
        if (picked !== undefined && picked.storeyId === ctx.storeyId) return picked;
      }
      if (hit.kind !== 'empty') return null;
    }
    const tolerance = snapToleranceMm(ctx.mmPerPx);
    let best: { wall: Wall; distance: number } | null = null;
    for (const wall of ctx.doc.house.walls) {
      if (wall.storeyId !== ctx.storeyId) continue;
      const projection = projectOnSegment(point, wall.a, wall.b);
      const reach = Math.max(tolerance, Math.ceil(wall.thicknessMm / 2));
      if (projection.distanceMm > reach) continue;
      if (best === null || projection.distanceMm < best.distance) {
        best = { wall, distance: projection.distanceMm };
      }
    }
    return best?.wall ?? null;
  }

  /**
   * An object snap that lies ON this wall's centreline: the end of a wall that
   * meets it, or its own midpoint. Snaps to the wall's own ends are excluded —
   * a cut there is no cut.
   */
  private snapOnWall(ctx: ToolContext, point: Pt, wall: Wall): SnapCandidate | null {
    const candidates = collectSnapCandidates(ctx, point, {}).filter((candidate) => {
      if (candidate.kind !== 'endpoint' && candidate.kind !== 'midpoint') return false;
      if (candidate.refId === wall.id && candidate.kind === 'endpoint') return false;
      const projection = projectOnSegment(candidate.pointMm, wall.a, wall.b);
      return projection.inside && projection.distanceMm <= 1;
    });
    let best: SnapCandidate | null = null;
    for (const candidate of candidates) {
      if (best === null || candidate.rank > best.rank || candidate.distanceMm < best.distanceMm) {
        best = candidate;
      }
    }
    return best;
  }
}

/** The cut line across a wall at a point on its centreline, integer mm. */
function cutLine(wall: Wall, pointMm: Pt): readonly [Pt, Pt] {
  const dx = wall.b.x - wall.a.x;
  const dy = wall.b.y - wall.a.y;
  const len = Math.hypot(dx, dy);
  if (len === 0) return [pointMm, pointMm];
  // Drawn a little past the faces so the mark reads as a cut, not a joint.
  const reach = Math.ceil(wall.thicknessMm / 2) + 60;
  const nx = Math.round((-dy / len) * reach);
  const ny = Math.round((dx / len) * reach);
  return [
    { x: pointMm.x + nx, y: pointMm.y + ny },
    { x: pointMm.x - nx, y: pointMm.y - ny },
  ];
}
