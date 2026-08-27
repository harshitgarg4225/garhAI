/**
 * openingTool.ts — D (door) and N (window / ventilator).
 *
 * One class, parametrised by kind: a door, a window and a ventilator differ
 * only in their default width/height/sill and in whether a swing means
 * anything. Three near-identical tools would be three places to forget the end
 * margin.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE STATE MACHINE
 * ────────────────────────────────────────────────────────────────────────────
 *
 *   idle ──hover a wall──▶ preview ──click / Enter──▶ commit ──▶ idle
 *            ▲                │                                   │
 *            └───leave wall───┘                                   │
 *                   ▲──────────────── Esc ─────────────────────────┘
 *
 * The tool stays armed after placing: doors come in pairs, windows in fours.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * "REFUSE WHERE VALIDATE WOULD REJECT, AND SAY WHY INLINE"
 * ────────────────────────────────────────────────────────────────────────────
 * The §3 invariant is that 115 mm of solid wall must remain at each end of an
 * opening. Two behaviours follow, and both matter:
 *
 *  1. **The offset is CLAMPED into the legal window while you hover.** Sliding
 *     a door towards the corner parks it at the last legal position instead of
 *     refusing to draw. That is not a workaround for the rule; it is the rule,
 *     made visible.
 *  2. **When no legal position exists** — the wall is shorter than the opening
 *     plus its margins, or the opening is taller than the storey — the tool
 *     blocks and shows the reason. The sentence comes from the REAL
 *     `validateOpAgainstDoc` (via `editOps.dryRun`), not from a paraphrase, so
 *     the inline copy and the server's 422 copy are the same string.
 *
 * The hover path uses the cheap mirrored predicate (`openingOffsetWindow`) to
 * decide *whether* it is legal, and only asks the validator for the *sentence*
 * when it is not — because a rejected op is refused before `fold` does any
 * work, while an accepted one would rebuild the storey's rooms, and doing that
 * per pointer move would cost the §14 frame budget several times over.
 *
 * That shortcut is only safe while the mirror and the validator agree, so
 * `editOps.test.ts` (where `openingOffsetWindow` lives) pins the two against
 * each other at the exact boundary offsets — including the odd-width case,
 * where the `floor`/`ceil` asymmetry is the thing that drifts.
 */

import { distMm, findStorey, type OpeningKind, type Op, type Pt, type Wall } from '@garh/model';

import { formatLength, snapMm } from '../../../lib/units';
import { pointAtLengthMm } from '../core/coords';
import { HINTS } from './constants';
import { BaseTool, type PreviewParts } from './baseTool';
import {
  clampOpeningOffset,
  dryRun,
  nextSwing,
  openingAddOp,
  toBlock,
  validateCommit,
} from './editOps';
import type { NumericField } from './numericEntry';
import { projectOnSegment, snapToleranceMm } from './snapping';
import {
  TOOL_RESPONSE_NONE,
  handled,
  type OpeningParams,
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
  { id: 'offset', label: 'From wall start', unit: 'mm', minMm: 0 },
  { id: 'width', label: 'Width', unit: 'mm', minMm: 300, maxMm: 6000 },
];

/** Where the opening would go, once the wall and the offset are known. */
interface Placement {
  readonly wall: Wall;
  readonly offsetMm: number;
  readonly widthMm: number;
  readonly wallLengthMm: number;
}

export class OpeningTool extends BaseTool {
  readonly id: ToolId;

  private placement: Placement | null = null;

  private blocked: ToolBlock | null = null;

  private cursor: Pt | null = null;

  /** True when the end-margin invariant moved the offset for you. */
  private placementWasClamped = false;

  constructor(toolId: 'door' | 'window') {
    super(FIELDS);
    this.id = toolId;
  }

  /** Doors are doors; the N tool places a window or a ventilator (⇧X toggles). */
  private kind(ctx: ToolContext): OpeningKind {
    return this.id === 'door' ? 'door' : ctx.settings.windowVariant;
  }

  private params(ctx: ToolContext): OpeningParams {
    const kind = this.kind(ctx);
    if (kind === 'door') return ctx.settings.door;
    if (kind === 'window') return ctx.settings.window;
    return ctx.settings.ventilator;
  }

  // ── pointer ──────────────────────────────────────────────────────────────

  override onPointerMove(ctx: ToolContext, event: ToolPointerInput): ToolResponse {
    const point = event.rawPointMm ?? event.pointMm;
    this.cursor = point;
    const previous = this.placement;
    this.recompute(ctx, point, event);
    const changed =
      previous?.wall.id !== this.placement?.wall.id ||
      previous?.offsetMm !== this.placement?.offsetMm;
    if (!changed) return TOOL_RESPONSE_NONE;
    this.touch();
    return { handled: true, redraw: true };
  }

  override onPointerDown(ctx: ToolContext, event: ToolPointerInput): ToolResponse {
    if (event.button !== 0) return TOOL_RESPONSE_NONE;
    const point = event.rawPointMm ?? event.pointMm;
    this.recompute(ctx, point, event);
    const commit = this.commit(ctx);
    if (commit === null) return handled();
    this.afterCommit(ctx);
    return handled({ commit, settingsPatch: this.drainSettings() });
  }

  // ── keys ─────────────────────────────────────────────────────────────────

  protected override onToolKey(ctx: ToolContext, event: ToolKeyInput): ToolResponse {
    if (event.key.toLowerCase() !== 'x') return TOOL_RESPONSE_NONE;

    // ⇧X switches the N tool between a window and a ventilator; X alone cycles
    // the swing. Neither is in the global keyboard map, so neither collides.
    if (event.shiftKey) {
      if (this.id !== 'window') return TOOL_RESPONSE_NONE;
      const variant = ctx.settings.windowVariant === 'window' ? 'ventilator' : 'window';
      this.touch();
      return handled({ settingsPatch: { windowVariant: variant } });
    }
    this.touch();
    return handled({ settingsPatch: { swing: nextSwing(ctx.settings.swing) } });
  }

  protected override onEntryChanged(ctx: ToolContext): void {
    this.recompute(ctx, this.cursor, null);
    this.touch();
  }

  override wantsKey(event: ToolKeyInput): boolean {
    // `X` must reach the tool even before a wall is hovered, so the swing can be
    // set up front. It is not a keyboard-map binding, so nothing is stolen.
    if (event.key.toLowerCase() === 'x' && !event.ctrlKey && !event.metaKey) return true;
    return super.wantsKey(event);
  }

  // ── commit ───────────────────────────────────────────────────────────────

  commit(ctx: ToolContext): ToolCommit | null {
    const placement = this.placement;
    if (placement === null) return null;
    if (this.blocked !== null) return null;

    const kind = this.kind(ctx);
    const params = this.params(ctx);
    const id = ctx.newId('opening');
    const op: Op = openingAddOp({
      id,
      wallId: placement.wall.id,
      kind,
      widthMm: placement.widthMm,
      heightMm: params.heightMm,
      sillMm: params.sillMm,
      offsetMm: placement.offsetMm,
      swing: ctx.settings.swing,
    });

    const block = validateCommit(ctx.doc, [op]);
    if (block !== null) {
      this.blocked = block;
      this.touch();
      return null;
    }

    const typedWidth = this.typed('width');
    if (typedWidth !== null && typedWidth !== params.widthMm) {
      // A typed width becomes the default for the next one — placing six
      // 1200 mm windows should mean typing 1200 once.
      const next = { ...params, widthMm: typedWidth };
      this.pendingSettings =
        kind === 'door'
          ? { door: next }
          : kind === 'window'
            ? { window: next }
            : { ventilator: next };
    }

    const label =
      kind === 'door' ? 'Door added' : kind === 'window' ? 'Window added' : 'Ventilator added';
    return { ops: [op], label, selectIds: [id] };
  }

  protected reset(): void {
    this.placement = null;
    this.blocked = null;
  }

  // ── preview ──────────────────────────────────────────────────────────────

  protected buildPreview(ctx: ToolContext): PreviewParts {
    const kind = this.kind(ctx);
    const params = this.params(ctx);
    const placement = this.placement;
    const readouts: Readout[] = [];
    const chips: ToolChip[] = [];

    let axis: readonly [Pt, Pt] | null = null;
    let centre: Pt | null = null;
    if (placement !== null) {
      const half = placement.widthMm / 2;
      const start = pointAtLengthMm(
        placement.wall.a,
        placement.wall.b,
        Math.max(0, placement.offsetMm - Math.floor(half)),
      );
      const end = pointAtLengthMm(
        placement.wall.a,
        placement.wall.b,
        Math.min(placement.wallLengthMm, placement.offsetMm + Math.ceil(half)),
      );
      axis = [start, end];
      centre = pointAtLengthMm(placement.wall.a, placement.wall.b, placement.offsetMm);

      readouts.push({
        id: 'offset',
        label: 'From wall start',
        value: formatLength(placement.offsetMm, ctx.unitsDisplay),
        emphasis: true,
      });
      readouts.push({
        id: 'width',
        label: 'Width',
        value: formatLength(placement.widthMm, ctx.unitsDisplay),
      });
      if (kind !== 'door') {
        readouts.push({
          id: 'sill',
          label: 'Sill',
          value: formatLength(params.sillMm, ctx.unitsDisplay),
        });
      }
      const remaining = placement.wallLengthMm - placement.offsetMm;
      readouts.push({
        id: 'remaining',
        label: 'To wall end',
        value: formatLength(remaining, ctx.unitsDisplay),
      });
    }

    if (this.placementWasClamped) {
      chips.push({
        id: 'opening-clamped',
        severity: 'info',
        text: 'Held 115 mm clear of the wall end.',
        cite: 'Model invariant: openings keep 115 mm of solid wall at each end',
        fix: 'Use a longer wall, or a narrower opening, to move it closer.',
      });
    }

    return {
      shape: {
        kind: 'opening',
        wallId: placement?.wall.id ?? null,
        openingKind: kind,
        centreMm: centre,
        widthMm: placement?.widthMm ?? params.widthMm,
        heightMm: params.heightMm,
        sillMm: params.sillMm,
        swing: ctx.settings.swing,
        offsetMm: placement?.offsetMm ?? 0,
        axis,
      },
      readouts,
      chips,
      blocked: this.blocked,
      cursorMm: centre ?? this.cursor,
      hint:
        ctx.storeyId === null
          ? HINTS.noStorey
          : placement === null
            ? HINTS.openingIdle
            : HINTS.openingPreview,
    };
  }

  // ── internals ────────────────────────────────────────────────────────────

  /**
   * Recompute the placement from a pointer position.
   *
   * The host wall is found GEOMETRICALLY (nearest centreline within tolerance)
   * rather than from the raycast pick, with the pick used only as a preference
   * when it names a wall. Two reasons: the geometric path works with no
   * renderer at all, which is what makes this tool testable; and a wall whose
   * mesh is hidden behind a room fill still hosts a door.
   */
  private recompute(ctx: ToolContext, point: Pt | null, event: ToolPointerInput | null): void {
    this.placement = null;
    this.blocked = null;
    this.placementWasClamped = false;
    if (point === null || ctx.storeyId === null) {
      this.phaseState = 'idle';
      return;
    }

    const wall = this.hostWall(ctx, point, event);
    if (wall === null) {
      this.phaseState = 'idle';
      return;
    }

    const params = this.params(ctx);
    const widthMm = this.typed('width') ?? params.widthMm;
    const wallLengthMm = distMm(wall.a, wall.b);
    const projection = projectOnSegment(point, wall.a, wall.b);
    const desired = this.typed('offset') ?? snapMm(projection.alongMm, ctx.snapModuleMm);

    const offsetMm = clampOpeningOffset(desired, wallLengthMm, widthMm);
    if (offsetMm === null) {
      this.phaseState = 'preview';
      this.blocked = this.authoritativeBlock(ctx, wall, Math.max(0, desired), widthMm);
      this.placement = { wall, offsetMm: Math.max(0, desired), widthMm, wallLengthMm };
      return;
    }

    this.placementWasClamped = offsetMm !== desired;
    this.placement = { wall, offsetMm, widthMm, wallLengthMm };
    this.phaseState = 'preview';

    // Height is the other invariant an opening can break, and unlike the offset
    // there is nothing sensible to clamp it to — the sill and height come from
    // the inspector, so the honest answer is to say so and refuse.
    const storey = findStorey(ctx.doc.house, wall.storeyId);
    if (storey !== undefined && params.sillMm + params.heightMm > storey.heightMm) {
      this.blocked = this.authoritativeBlock(ctx, wall, offsetMm, widthMm);
    }
  }

  /**
   * The model core's own sentence for why this placement is refused.
   *
   * Only called once the cheap predicate has already decided it IS refused, so
   * `fold` rejects at validation and never reaches the expensive room rebuild.
   */
  private authoritativeBlock(
    ctx: ToolContext,
    wall: Wall,
    offsetMm: number,
    widthMm: number,
  ): ToolBlock | null {
    const params = this.params(ctx);
    const op = openingAddOp({
      id: ctx.newId('opening'),
      wallId: wall.id,
      kind: this.kind(ctx),
      widthMm,
      heightMm: params.heightMm,
      sillMm: params.sillMm,
      offsetMm,
      swing: ctx.settings.swing,
    });
    return toBlock(dryRun(ctx.doc, [op]));
  }

  /** Nearest wall centreline on the active storey, within click tolerance. */
  private hostWall(ctx: ToolContext, point: Pt, event: ToolPointerInput | null): Wall | null {
    const storeyId = ctx.storeyId;
    if (storeyId === null) return null;

    if (event !== null) {
      const hit = event.hit();
      if (hit.kind === 'wall' && hit.id !== null) {
        const picked = ctx.doc.house.walls.find((w) => w.id === hit.id);
        if (picked !== undefined && picked.storeyId === storeyId) return picked;
      }
    }

    let best: Wall | null = null;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (const wall of ctx.doc.house.walls) {
      if (wall.storeyId !== storeyId) continue;
      const projection = projectOnSegment(point, wall.a, wall.b);
      // Anywhere within the wall's own thickness counts as "on the wall", plus
      // the usual screen-constant slop so a thin partition is still clickable.
      const tolerance = Math.max(snapToleranceMm(ctx.mmPerPx), Math.ceil(wall.thicknessMm / 2));
      if (projection.distanceMm > tolerance) continue;
      if (projection.distanceMm < bestDistance) {
        bestDistance = projection.distanceMm;
        best = wall;
      }
    }
    return best;
  }
}
