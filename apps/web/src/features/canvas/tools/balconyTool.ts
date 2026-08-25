/**
 * balconyTool.ts — B. Polygon balconies, with the projection-versus-setback
 * answer on screen while you draw it.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE STATE MACHINE
 * ────────────────────────────────────────────────────────────────────────────
 *
 *   idle ──click──▶ drawing ──click──▶ drawing ──┬─ click the first vertex ─┐
 *     ▲                 │                        ├─ Enter ──────────────────┤
 *     └──── Esc ────────┘                        └──────────────────────────┴─▶ commit
 *
 * Backspace drops the last vertex. Ortho is on by default (Shift inverts it),
 * because a balcony that is 3 mm out of square is a balcony whose dimension
 * string reads 2,397 on the submission drawing.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * PROJECTION, AND WHY IT IS MEASURED FROM THE SLAB
 * ────────────────────────────────────────────────────────────────────────────
 * `balcony.set` carries a `projectionMm` — how far the balcony reaches beyond
 * the building line — because that is the number the city's projection rule
 * checks (`blr.projection.balcony.front` and its siblings). The building line
 * here is the storey's DERIVED slab outline, which `fold` computes from the
 * walls by planar subdivision. Measuring from the slab rather than from a
 * bounding box means an L-shaped house gets an L-shaped building line, and the
 * number the chip shows is the number the rules engine will see.
 *
 * The setback envelope is NOT recomputed here. It arrives on the context from
 * the page that already resolved the city pack for the plot editor; a second
 * implementation of the setback table inside a drawing tool is exactly how two
 * parts of an app end up disagreeing about whether a plan is legal.
 *
 * COMPLIANCE NEVER BLOCKS (golden rule 5). Every finding in this tool is a
 * chip with a citation and a fix hint. The only thing that blocks a commit is
 * a ring the model core itself would refuse.
 */

import {
  dedupeCollinear,
  distMm,
  ensureCcw,
  pointInPolygon,
  polygonEdges,
  polygonIsClosedRing,
  type Polygon,
  type Pt,
} from '@garh/model';

import { formatArea, formatLength } from '../../../lib/units';
import { HINTS, MIN_BALCONY_VERTICES, RING_CLOSE_TOLERANCE_MM } from './constants';
import { BaseTool, type PreviewParts } from './baseTool';
import { balconyAddOp, ringAreaMm2, validateCommit } from './editOps';
import { pointAtLengthMm } from '../core/coords';
import type { NumericField } from './numericEntry';
import { projectOnSegment, resolveSnap, toSnapView, type SnapCandidate } from './snapping';
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
  { id: 'length', label: 'Edge length', unit: 'mm', minMm: 1 },
];

export class BalconyTool extends BaseTool {
  readonly id: ToolId = 'balcony';

  private points: Pt[] = [];

  private cursor: Pt | null = null;

  private snap: SnapCandidate | null = null;

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

    const first = this.points[0];
    if (
      first !== undefined &&
      this.points.length >= MIN_BALCONY_VERTICES &&
      distMm(first, point) <= RING_CLOSE_TOLERANCE_MM
    ) {
      const commit = this.commit(ctx);
      if (commit === null) return handled();
      this.afterCommit(ctx);
      return handled({ commit });
    }

    this.points = [...this.points, point];
    this.cursor = point;
    this.phaseState = 'drawing';
    this.blocked = null;
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

  // ── keys ─────────────────────────────────────────────────────────────────

  protected override onEnterKey(_ctx: ToolContext): ToolResponse | null {
    // A typed edge length places the vertex and keeps going, exactly as the
    // wall tool does — one idiom for both polygon-ish tools.
    if (!this.entryIsActive()) return null;
    if (this.phaseState !== 'drawing' || this.cursor === null) return null;
    const point = this.cursor;
    this.consumeEntry();
    this.points = [...this.points, point];
    this.touch();
    return handled();
  }

  protected override onToolKey(_ctx: ToolContext, event: ToolKeyInput): ToolResponse {
    if (event.key === 'Backspace' && this.phaseState === 'drawing') {
      if (this.points.length <= 1) {
        this.cancel();
        return handled();
      }
      this.points = this.points.slice(0, -1);
      this.blocked = null;
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

  // ── commit ───────────────────────────────────────────────────────────────

  commit(ctx: ToolContext): ToolCommit | null {
    const storeyId = ctx.storeyId;
    if (storeyId === null) return null;
    const ring = this.ring();
    if (ring === null) {
      if (this.points.length >= 1) {
        this.blocked = {
          message: 'A balcony needs at least three corners that do not cross.',
          fix: 'Add another corner, or Backspace to undo the last one.',
          issues: [],
        };
        this.touch();
      }
      return null;
    }

    const id = ctx.newId('balcony');
    const op = balconyAddOp({
      id,
      storeyId,
      polygon: ring,
      railingKind: ctx.settings.railingKind,
      railingHeightMm: ctx.settings.railingHeightMm,
      projectionMm: this.projectionMm(ctx, ring),
      slabThicknessMm: ctx.settings.balconySlabThicknessMm,
    });

    const block = validateCommit(ctx.doc, [op]);
    if (block !== null) {
      this.blocked = block;
      this.touch();
      return null;
    }

    return { ops: [op], label: 'Balcony added', selectIds: [id] };
  }

  protected reset(): void {
    this.points = [];
    this.cursor = null;
    this.snap = null;
    this.blocked = null;
  }

  // ── preview ──────────────────────────────────────────────────────────────

  protected buildPreview(ctx: ToolContext): PreviewParts {
    const readouts: Readout[] = [];
    const chips: ToolChip[] = [];
    const anchor = this.points[this.points.length - 1];

    if (anchor !== undefined && this.cursor !== null) {
      const edge = distMm(anchor, this.cursor);
      if (edge > 0) {
        readouts.push({
          id: 'edge',
          label: 'Edge',
          value: formatLength(edge, ctx.unitsDisplay),
          emphasis: true,
        });
      }
    }

    const provisional = this.provisionalRing();
    if (provisional !== null) {
      readouts.push({
        id: 'area',
        label: 'Area',
        value: formatArea(ringAreaMm2(provisional), ctx.unitsDisplay),
      });
      const projection = this.projectionMm(ctx, provisional);
      if (projection > 0) {
        readouts.push({
          id: 'projection',
          label: 'Projection',
          value: formatLength(projection, ctx.unitsDisplay),
        });
      }
      chips.push(...this.projectionChips(ctx, provisional, projection));
    }

    readouts.push({
      id: 'railing',
      label: 'Railing',
      value: `${ctx.settings.railingKind} · ${String(ctx.settings.railingHeightMm)} mm`,
    });

    return {
      shape: {
        kind: 'polygon',
        points: this.points,
        closed: false,
        rubber: this.phaseState === 'drawing' ? this.cursor : null,
      },
      snap: toSnapView(this.snap),
      readouts,
      chips,
      blocked: this.blocked,
      cursorMm: this.cursor,
      hint:
        ctx.storeyId === null
          ? HINTS.noStorey
          : this.phaseState === 'idle'
            ? HINTS.balconyIdle
            : HINTS.balconyDrawing,
    };
  }

  // ── internals ────────────────────────────────────────────────────────────

  private resolve(ctx: ToolContext, event: ToolPointerInput): Pt | null {
    const raw = event.rawPointMm ?? event.pointMm;
    if (raw === null) return null;
    const anchor = this.points[this.points.length - 1] ?? null;
    const ortho = this.shiftHeld ? !ctx.settings.ortho : ctx.settings.ortho;
    const resolution = resolveSnap(ctx, raw, { anchor, ortho });
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

  /** The committed ring: CCW, collinear points removed, simple. Null if invalid. */
  private ring(): Polygon | null {
    if (this.points.length < MIN_BALCONY_VERTICES) return null;
    const cleaned = dedupeCollinear(this.points);
    if (cleaned.length < MIN_BALCONY_VERTICES) return null;
    const ccw = ensureCcw(cleaned);
    return polygonIsClosedRing(ccw) ? ccw : null;
  }

  /** The ring including the rubber point — what the area readout describes. */
  private provisionalRing(): Polygon | null {
    const points =
      this.cursor !== null && this.phaseState === 'drawing'
        ? [...this.points, this.cursor]
        : this.points;
    if (points.length < MIN_BALCONY_VERTICES) return null;
    const cleaned = dedupeCollinear(points);
    if (cleaned.length < MIN_BALCONY_VERTICES) return null;
    const ccw = ensureCcw(cleaned);
    return polygonIsClosedRing(ccw) ? ccw : null;
  }

  /**
   * How far the ring reaches beyond the building line, in mm.
   *
   * The building line is this storey's derived floor slab outline (the one
   * `fold` built from the walls). With no slab yet — a balcony drawn before any
   * wall — the projection is 0, which is honest: there is nothing to project
   * from.
   */
  private projectionMm(ctx: ToolContext, ring: Polygon): number {
    const storeyId = ctx.storeyId;
    if (storeyId === null) return 0;
    const slab = ctx.doc.house.slabs.find(
      (s) => s.storeyId === storeyId && s.kind === 'floor',
    );
    if (slab === undefined || slab.polygon.length < 3) return 0;
    return maxDistanceOutside(ring, slab.polygon);
  }

  /** The non-blocking projection / setback chips (§15 severity + cite + fix). */
  private projectionChips(
    ctx: ToolContext,
    ring: Polygon,
    projectionMm: number,
  ): ToolChip[] {
    const out: ToolChip[] = [];
    const setback = ctx.setback;
    if (setback === null) return out;

    if (setback.maxProjectionMm !== null && projectionMm > setback.maxProjectionMm) {
      out.push({
        id: 'projection.balcony.max',
        severity: 'error',
        text: `This balcony projects ${String(projectionMm)} mm — the most allowed is ${String(setback.maxProjectionMm)} mm.`,
        cite: setback.cite,
        fix: `Pull it back to ${String(setback.maxProjectionMm)} mm from the building line.`,
      });
    }

    if (setback.envelope !== null && setback.envelope.length >= 3) {
      const outside = maxDistanceOutside(ring, setback.envelope);
      if (outside > 0) {
        out.push({
          id: 'setback.balcony',
          severity: 'error',
          text: `This balcony crosses the setback line by ${String(outside)} mm.`,
          cite: setback.cite,
          fix: 'Move it inside the buildable envelope, or reduce its depth.',
        });
      }
    }
    return out;
  }
}

/**
 * Largest distance by which any vertex of `ring` falls outside `boundary`.
 *
 * Vertex-based rather than edge-based: for the rectilinear balconies the MVP
 * draws, the deepest excursion is always at a corner, and a full polygon
 * difference would be a lot of machinery for a number that is displayed to the
 * nearest millimetre. Returns 0 when the ring is entirely inside or on the
 * boundary.
 */
export function maxDistanceOutside(ring: Polygon, boundary: Polygon): number {
  let worst = 0;
  for (const vertex of ring) {
    if (pointInPolygon(vertex, boundary) !== 'outside') continue;
    let nearest = Number.POSITIVE_INFINITY;
    for (const edge of polygonEdges(boundary)) {
      const projection = projectOnSegment(vertex, edge.a, edge.b);
      if (projection.distanceMm < nearest) nearest = projection.distanceMm;
    }
    if (Number.isFinite(nearest) && nearest > worst) worst = nearest;
  }
  return worst;
}
