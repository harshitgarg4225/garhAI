/**
 * selectTool.ts — V. Select, marquee, drag, and delete.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE STATE MACHINE
 * ────────────────────────────────────────────────────────────────────────────
 *
 *   idle ──down on an element──▶ (armed) ──move past 4 px──▶ drawing ──up──▶ commit
 *     │                             └──up without moving──▶ selection change
 *     ├──down on nothing──▶ (armed) ──move──▶ marquee ──up──▶ selection change
 *     └──Delete──▶ commit (delete ops)
 *
 * Esc cancels a drag and puts everything back — nothing was dispatched, so
 * "putting it back" is just dropping the preview. Enter commits the drag from
 * the keyboard, and typing a number while dragging sets the exact distance.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE SHARED COMMIT PATH
 * ────────────────────────────────────────────────────────────────────────────
 * Dragging a wall and typing into its dimension label are the same edit, so
 * they must be the same code. Both go through `editOps`:
 *
 *   drag a wall / group      → `translateWallsOps`
 *   drag one wall endpoint   → `wallMoveOp`
 *   type into a dimension    → `setWallLengthOps`   ← the overlays agent's door
 *   slide an opening         → `openingMoveOp` + `clampOpeningOffset`
 *
 * The overlay owns the dimension UI; it does NOT own a second way of writing
 * the op. `setWallLengthOps` is exported from `editOps` (and re-exported from
 * this feature's `index.ts`) precisely so there is one.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * PICKING
 * ────────────────────────────────────────────────────────────────────────────
 * The raycast pick (`event.hit()`, the ONE hit-testing system shared with the
 * Phase 5 3D view) is authoritative when it names something. A geometric
 * fallback handles the two cases it cannot: a spec with no renderer, and an
 * element whose mesh has not mounted yet on a freshly-loaded storey. The
 * fallback never contradicts the pick — it only runs when the pick is empty.
 */

import {
  distMm,
  findWall,
  idType,
  pointInPolygon,
  ptRound,
  type Op,
  type Pt,
  type Wall,
} from '@garh/model';

import { formatLength } from '../../../lib/units';
import { snapPtRelativeMm } from '../core/coords';
import { DRAG_THRESHOLD_PX, HINTS } from './constants';
import { BaseTool, type PreviewParts } from './baseTool';
import {
  clampOpeningOffset,
  deleteLabel,
  deleteOps,
  furnitureTransformOp,
  openingMoveOp,
  previewWall,
  translateWallsOps,
  validateCommit,
  wallMoveOp,
} from './editOps';
import type { NumericField } from './numericEntry';
import { projectOnSegment, resolveSnap, snapToleranceMm, toSnapView, type SnapCandidate } from './snapping';
import {
  TOOL_RESPONSE_NONE,
  handled,
  type MarqueeRectMm,
  type PreviewWall,
  type Readout,
  type SelectionIntent,
  type ToolBlock,
  type ToolCommit,
  type ToolContext,
  type ToolId,
  type ToolKeyInput,
  type ToolPointerInput,
  type ToolResponse,
} from './types';

const FIELDS: readonly NumericField[] = [
  { id: 'distance', label: 'Move by', unit: 'mm' },
];

type Drag =
  | { readonly kind: 'none' }
  | { readonly kind: 'armed'; readonly startMm: Pt; readonly target: string | null }
  | { readonly kind: 'marquee'; readonly startMm: Pt; readonly currentMm: Pt }
  | {
      readonly kind: 'move';
      readonly startMm: Pt;
      readonly deltaMm: Pt;
      readonly wallIds: readonly string[];
      readonly furnitureIds: readonly string[];
    }
  | {
      readonly kind: 'opening';
      readonly openingId: string;
      readonly wallId: string;
      readonly offsetMm: number;
    }
  | {
      readonly kind: 'endpoint';
      readonly wallId: string;
      readonly end: 'a' | 'b';
      readonly pointMm: Pt;
    };

export class SelectTool extends BaseTool {
  readonly id: ToolId = 'select';

  private drag: Drag = { kind: 'none' };

  private snap: SnapCandidate | null = null;

  private blocked: ToolBlock | null = null;

  constructor() {
    super(FIELDS);
  }

  // ── pointer ──────────────────────────────────────────────────────────────

  override onPointerDown(ctx: ToolContext, event: ToolPointerInput): ToolResponse {
    if (event.button !== 0) return TOOL_RESPONSE_NONE;
    const point = event.rawPointMm ?? event.pointMm;
    if (point === null) return TOOL_RESPONSE_NONE;

    this.blocked = null;

    // A wall endpoint handle wins over the wall itself: dragging the end of a
    // selected wall is a different edit from dragging the wall.
    const handle = this.endpointHandle(ctx, point);
    if (handle !== null) {
      this.drag = { kind: 'endpoint', wallId: handle.wall.id, end: handle.end, pointMm: point };
      this.phaseState = 'drawing';
      this.touch();
      return handled();
    }

    const target = this.pick(ctx, point, event);
    this.drag = { kind: 'armed', startMm: point, target };

    // Pressing on something that is not selected selects it, so the drag that
    // may follow moves what is under the cursor and not what was left over
    // from a previous selection.
    if (target !== null && !event.shiftKey && !ctx.selectedIds.includes(target)) {
      return handled({ selection: { mode: 'replace', ids: [target] } });
    }
    return handled();
  }

  override onPointerMove(ctx: ToolContext, event: ToolPointerInput): ToolResponse {
    const point = event.rawPointMm ?? event.pointMm;
    if (point === null) return TOOL_RESPONSE_NONE;

    const drag = this.drag;
    if (drag.kind === 'armed') {
      const thresholdMm = Math.max(1, Math.round(DRAG_THRESHOLD_PX * ctx.mmPerPx));
      if (distMm(drag.startMm, point) < thresholdMm) return TOOL_RESPONSE_NONE;
      this.drag = this.beginDrag(ctx, drag, point);
      this.phaseState = 'drawing';
      this.touch();
      return handled();
    }

    if (drag.kind === 'none') return TOOL_RESPONSE_NONE;

    this.updateDrag(ctx, point);
    this.touch();
    return handled();
  }

  override onPointerUp(ctx: ToolContext, event: ToolPointerInput): ToolResponse {
    const point = event.rawPointMm ?? event.pointMm;
    const drag = this.drag;

    if (drag.kind === 'armed') {
      // A press that never moved: a click.
      this.drag = { kind: 'none' };
      this.phaseState = 'idle';
      this.touch();
      return handled({ selection: this.clickSelection(drag.target, event.shiftKey) });
    }

    if (drag.kind === 'marquee') {
      const rect: MarqueeRectMm = {
        ax: drag.startMm.x,
        ay: drag.startMm.y,
        bx: (point ?? drag.currentMm).x,
        by: (point ?? drag.currentMm).y,
      };
      const ids = this.idsInMarquee(ctx, rect);
      this.cancel();
      return handled({
        selection: { mode: event.shiftKey ? 'add' : 'replace', ids },
      });
    }

    if (drag.kind === 'none') return TOOL_RESPONSE_NONE;

    const commit = this.commit(ctx);
    if (commit === null) return handled();
    this.afterCommit(ctx);
    return handled({ commit });
  }

  // ── keys ─────────────────────────────────────────────────────────────────

  protected override onToolKey(ctx: ToolContext, event: ToolKeyInput): ToolResponse {
    if (event.key !== 'Delete' && event.key !== 'Backspace') return TOOL_RESPONSE_NONE;
    if (ctx.selectedIds.length === 0) return TOOL_RESPONSE_NONE;

    const ops = deleteOps(ctx.doc, ctx.selectedIds);
    if (ops.length === 0) return TOOL_RESPONSE_NONE;

    const block = validateCommit(ctx.doc, ops);
    if (block !== null) {
      this.blocked = block;
      this.touch();
      return handled();
    }
    return handled({
      commit: { ops, label: deleteLabel(ops), selectIds: [] },
      selection: { mode: 'clear', ids: [] },
    });
  }

  override wantsKey(event: ToolKeyInput): boolean {
    // Delete has to reach the tool while idle — that is when a selection is
    // usually deleted — and Backspace must never navigate the browser back.
    if (event.key === 'Delete' || event.key === 'Backspace') return true;
    return super.wantsKey(event);
  }

  protected override onEntryChanged(ctx: ToolContext): void {
    const typed = this.typed('distance');
    const drag = this.drag;
    if (typed === null) {
      this.touch();
      return;
    }
    if (drag.kind === 'move') {
      // Keep the direction the pointer chose; take the distance from the
      // keyboard. Integer mm in, integer mm out.
      const length = Math.hypot(drag.deltaMm.x, drag.deltaMm.y);
      if (length > 0) {
        // `ptRound`, not `Math.round`: this delta becomes a `wall.move`
        // payload, and `Math.round` is half-UP, so dragging 2.5 mm west and
        // 2.5 mm east would not be mirror images of each other. See the
        // rounding rule in `core/coords.ts`.
        this.drag = {
          ...drag,
          deltaMm: ptRound((drag.deltaMm.x / length) * typed, (drag.deltaMm.y / length) * typed),
        };
      }
    } else if (drag.kind === 'opening') {
      const wall = findWall(ctx.doc.house, drag.wallId);
      const opening = ctx.doc.house.openings.find((o) => o.id === drag.openingId);
      if (wall !== undefined && opening !== undefined) {
        const clamped = clampOpeningOffset(typed, distMm(wall.a, wall.b), opening.widthMm);
        if (clamped !== null) this.drag = { ...drag, offsetMm: clamped };
      }
    }
    this.touch();
  }

  // ── commit ───────────────────────────────────────────────────────────────

  commit(ctx: ToolContext): ToolCommit | null {
    const drag = this.drag;
    let ops: Op[] = [];
    let label = 'Moved';

    if (drag.kind === 'move') {
      if (drag.deltaMm.x === 0 && drag.deltaMm.y === 0) return null;
      ops = [
        ...translateWallsOps(ctx.doc, drag.wallIds, drag.deltaMm),
        ...drag.furnitureIds.flatMap((id) => {
          const item = ctx.doc.house.furniture.find((f) => f.id === id);
          if (item === undefined) return [];
          return [
            furnitureTransformOp(id, {
              pt: { x: item.pt.x + drag.deltaMm.x, y: item.pt.y + drag.deltaMm.y },
            }),
          ];
        }),
      ];
      label = ops.length === 1 ? 'Wall moved' : `${String(ops.length)} things moved`;
      if (drag.wallIds.length === 0 && drag.furnitureIds.length > 0) label = 'Furniture moved';
    } else if (drag.kind === 'opening') {
      const opening = ctx.doc.house.openings.find((o) => o.id === drag.openingId);
      if (opening === undefined) return null;
      if (opening.offsetMm === drag.offsetMm) return null;
      ops = [openingMoveOp(drag.openingId, drag.offsetMm)];
      label = 'Opening moved';
    } else if (drag.kind === 'endpoint') {
      const wall = findWall(ctx.doc.house, drag.wallId);
      if (wall === undefined) return null;
      const a = drag.end === 'a' ? drag.pointMm : wall.a;
      const b = drag.end === 'b' ? drag.pointMm : wall.b;
      if (a.x === wall.a.x && a.y === wall.a.y && b.x === wall.b.x && b.y === wall.b.y) return null;
      ops = [wallMoveOp(drag.wallId, a, b)];
      label = 'Wall end moved';
    } else {
      return null;
    }

    if (ops.length === 0) return null;

    const block = validateCommit(ctx.doc, ops);
    if (block !== null) {
      this.blocked = block;
      this.touch();
      return null;
    }
    return { ops, label };
  }

  protected reset(): void {
    this.drag = { kind: 'none' };
    this.snap = null;
    this.blocked = null;
  }

  // ── preview ──────────────────────────────────────────────────────────────

  protected buildPreview(ctx: ToolContext): PreviewParts {
    const drag = this.drag;
    const readouts: Readout[] = [];

    if (drag.kind === 'marquee') {
      return {
        shape: {
          kind: 'marquee',
          rect: {
            ax: drag.startMm.x,
            ay: drag.startMm.y,
            bx: drag.currentMm.x,
            by: drag.currentMm.y,
          },
        },
        readouts,
        blocked: this.blocked,
        cursorMm: drag.currentMm,
        hint: HINTS.selectIdle,
      };
    }

    if (drag.kind === 'move') {
      const ghosts: PreviewWall[] = [];
      for (const id of drag.wallIds) {
        const wall = findWall(ctx.doc.house, id);
        if (wall === undefined) continue;
        ghosts.push(
          previewWall(
            { x: wall.a.x + drag.deltaMm.x, y: wall.a.y + drag.deltaMm.y },
            { x: wall.b.x + drag.deltaMm.x, y: wall.b.y + drag.deltaMm.y },
            wall.thicknessMm,
            wall.kind,
          ),
        );
      }
      const distance = Math.round(Math.hypot(drag.deltaMm.x, drag.deltaMm.y));
      readouts.push({
        id: 'distance',
        label: 'Moved',
        value: formatLength(distance, ctx.unitsDisplay),
        emphasis: true,
      });
      readouts.push({
        id: 'delta',
        label: 'Δx, Δy',
        value: `${String(drag.deltaMm.x)} , ${String(drag.deltaMm.y)} mm`,
      });
      return {
        shape: {
          kind: 'transform',
          targetIds: [...drag.wallIds, ...drag.furnitureIds],
          ghosts,
          deltaMm: drag.deltaMm,
        },
        snap: toSnapView(this.snap),
        readouts,
        blocked: this.blocked,
        cursorMm: null,
        hint: HINTS.selectDragging,
      };
    }

    if (drag.kind === 'endpoint') {
      const wall = findWall(ctx.doc.house, drag.wallId);
      const ghosts: PreviewWall[] = [];
      if (wall !== undefined) {
        const a = drag.end === 'a' ? drag.pointMm : wall.a;
        const b = drag.end === 'b' ? drag.pointMm : wall.b;
        const ghost = previewWall(a, b, wall.thicknessMm, wall.kind);
        ghosts.push(ghost);
        readouts.push({
          id: 'length',
          label: 'Length',
          value: formatLength(ghost.lengthMm, ctx.unitsDisplay),
          emphasis: true,
        });
        readouts.push({ id: 'angle', label: 'Angle', value: `${String(ghost.angleDeg)}°` });
      }
      return {
        shape: { kind: 'transform', targetIds: [drag.wallId], ghosts, deltaMm: { x: 0, y: 0 } },
        snap: toSnapView(this.snap),
        readouts,
        blocked: this.blocked,
        cursorMm: drag.pointMm,
        hint: HINTS.selectDragging,
      };
    }

    if (drag.kind === 'opening') {
      const opening = ctx.doc.house.openings.find((o) => o.id === drag.openingId);
      readouts.push({
        id: 'offset',
        label: 'From wall start',
        value: formatLength(drag.offsetMm, ctx.unitsDisplay),
        emphasis: true,
      });
      if (opening !== undefined) {
        readouts.push({
          id: 'width',
          label: 'Width',
          value: formatLength(opening.widthMm, ctx.unitsDisplay),
        });
      }
      return {
        shape: { kind: 'transform', targetIds: [drag.openingId], ghosts: [], deltaMm: { x: 0, y: 0 } },
        readouts,
        blocked: this.blocked,
        cursorMm: null,
        hint: HINTS.selectDragging,
      };
    }

    return {
      shape: { kind: 'none' },
      readouts,
      blocked: this.blocked,
      cursorMm: null,
      hint: HINTS.selectIdle,
    };
  }

  // ── internals ────────────────────────────────────────────────────────────

  /** Turn an armed press into the drag it actually is. */
  private beginDrag(ctx: ToolContext, armed: Extract<Drag, { kind: 'armed' }>, point: Pt): Drag {
    const target = armed.target;
    if (target === null) {
      return { kind: 'marquee', startMm: armed.startMm, currentMm: point };
    }

    const ids = ctx.selectedIds.includes(target) ? ctx.selectedIds : [target];

    if (idType(target) === 'opening' && ids.length === 1) {
      const opening = ctx.doc.house.openings.find((o) => o.id === target);
      if (opening !== undefined) {
        return {
          kind: 'opening',
          openingId: opening.id,
          wallId: opening.wallId,
          offsetMm: opening.offsetMm,
        };
      }
    }

    const wallIds = ids.filter((id) => idType(id) === 'wall');
    const furnitureIds = ids.filter((id) => idType(id) === 'furniture');
    if (wallIds.length === 0 && furnitureIds.length === 0) {
      return { kind: 'marquee', startMm: armed.startMm, currentMm: point };
    }
    return {
      kind: 'move',
      startMm: armed.startMm,
      deltaMm: { x: 0, y: 0 },
      wallIds,
      furnitureIds,
    };
  }

  /** Advance the live drag to a new pointer position. */
  private updateDrag(ctx: ToolContext, point: Pt): void {
    const drag = this.drag;

    if (drag.kind === 'marquee') {
      this.drag = { ...drag, currentMm: point };
      return;
    }

    if (drag.kind === 'move') {
      const typed = this.typed('distance');
      // Snap the DELTA, not the pointer: a group that started off-grid keeps
      // its internal geometry and moves by whole modules.
      const snapped = snapPtRelativeMm(point, drag.startMm, ctx.snapModuleMm);
      let deltaMm: Pt = { x: snapped.x - drag.startMm.x, y: snapped.y - drag.startMm.y };
      if (typed !== null) {
        const length = Math.hypot(deltaMm.x, deltaMm.y);
        if (length > 0) {
          // Half away from zero — this delta reaches an op payload.
          deltaMm = ptRound((deltaMm.x / length) * typed, (deltaMm.y / length) * typed);
        }
      }
      this.drag = { ...drag, deltaMm };
      return;
    }

    if (drag.kind === 'opening') {
      const wall = findWall(ctx.doc.house, drag.wallId);
      const opening = ctx.doc.house.openings.find((o) => o.id === drag.openingId);
      if (wall === undefined || opening === undefined) return;
      const projection = projectOnSegment(point, wall.a, wall.b);
      const clamped = clampOpeningOffset(
        this.typed('distance') ?? projection.alongMm,
        distMm(wall.a, wall.b),
        opening.widthMm,
      );
      if (clamped === null) return;
      this.drag = { ...drag, offsetMm: clamped };
      return;
    }

    if (drag.kind === 'endpoint') {
      const resolution = resolveSnap(ctx, point, {
        excludeIds: new Set([drag.wallId]),
      });
      this.snap = resolution.candidate;
      this.drag = { ...drag, pointMm: resolution.pointMm };
    }
  }

  /** What a plain click does to the selection. */
  private clickSelection(target: string | null, shiftKey: boolean): SelectionIntent {
    if (target === null) return { mode: shiftKey ? 'add' : 'clear', ids: [] };
    return { mode: shiftKey ? 'toggle' : 'replace', ids: [target] };
  }

  /**
   * Element under the pointer. The raycast pick decides when it can; the
   * geometric fallback covers a pick that returned nothing (see the header).
   */
  private pick(ctx: ToolContext, point: Pt, event: ToolPointerInput): string | null {
    const hit = event.hit();
    if (hit.kind !== 'empty' && hit.id !== null) return hit.id;

    const storeyId = ctx.storeyId;
    if (storeyId === null) return null;
    const tolerance = snapToleranceMm(ctx.mmPerPx);

    // Openings first: an opening always sits on a wall, and the §12 pick
    // priority says the smaller, more deliberate element wins.
    let best: { id: string; distance: number } | null = null;
    for (const opening of ctx.doc.house.openings) {
      const wall = findWall(ctx.doc.house, opening.wallId);
      if (wall === undefined || wall.storeyId !== storeyId) continue;
      const projection = projectOnSegment(point, wall.a, wall.b);
      if (projection.distanceMm > Math.max(tolerance, wall.thicknessMm)) continue;
      if (Math.abs(projection.alongMm - opening.offsetMm) > opening.widthMm / 2) continue;
      if (best === null || projection.distanceMm < best.distance) {
        best = { id: opening.id, distance: projection.distanceMm };
      }
    }
    if (best !== null) return best.id;

    for (const wall of ctx.doc.house.walls) {
      if (wall.storeyId !== storeyId) continue;
      const projection = projectOnSegment(point, wall.a, wall.b);
      const reach = Math.max(tolerance, Math.ceil(wall.thicknessMm / 2));
      if (projection.distanceMm > reach) continue;
      if (best === null || projection.distanceMm < best.distance) {
        best = { id: wall.id, distance: projection.distanceMm };
      }
    }
    return best?.id ?? null;
  }

  /** A selected wall's endpoint under the pointer, if any. */
  private endpointHandle(
    ctx: ToolContext,
    point: Pt,
  ): { wall: Wall; end: 'a' | 'b' } | null {
    const tolerance = snapToleranceMm(ctx.mmPerPx);
    for (const id of ctx.selectedIds) {
      if (idType(id) !== 'wall') continue;
      const wall = findWall(ctx.doc.house, id);
      if (wall === undefined) continue;
      if (distMm(point, wall.a) <= tolerance) return { wall, end: 'a' };
      if (distMm(point, wall.b) <= tolerance) return { wall, end: 'b' };
    }
    return null;
  }

  /**
   * Everything fully inside the marquee. "Fully" on purpose: a partial-crossing
   * selection would pick up half the plan when you rubber-band across it, and
   * the CAD convention that crossing selects is a right-to-left gesture the
   * MVP does not have.
   */
  private idsInMarquee(ctx: ToolContext, rect: MarqueeRectMm): string[] {
    const storeyId = ctx.storeyId;
    if (storeyId === null) return [];
    const minX = Math.min(rect.ax, rect.bx);
    const maxX = Math.max(rect.ax, rect.bx);
    const minY = Math.min(rect.ay, rect.by);
    const maxY = Math.max(rect.ay, rect.by);
    const inside = (p: Pt): boolean => p.x >= minX && p.x <= maxX && p.y >= minY && p.y <= maxY;

    const ids: string[] = [];
    for (const wall of ctx.doc.house.walls) {
      if (wall.storeyId !== storeyId) continue;
      if (inside(wall.a) && inside(wall.b)) ids.push(wall.id);
    }
    for (const item of ctx.doc.house.furniture) {
      if (item.storeyId !== storeyId) continue;
      if (inside(item.pt)) ids.push(item.id);
    }
    for (const stair of ctx.doc.house.stairs) {
      if (stair.storeyId !== storeyId) continue;
      if (inside(stair.origin)) ids.push(stair.id);
    }
    for (const balcony of ctx.doc.house.balconies) {
      if (balcony.storeyId !== storeyId) continue;
      const allIn = balcony.polygon.every((p) => inside(p));
      if (allIn && balcony.polygon.length > 0) ids.push(balcony.id);
    }
    return ids;
  }
}

/**
 * Is `point` inside `polygon`? Re-exported thinly so the overlay can ask the
 * same question the marquee does without importing the model core directly.
 */
export function pointInsidePolygon(point: Pt, polygon: readonly Pt[]): boolean {
  return polygon.length >= 3 && pointInPolygon(point, polygon) !== 'outside';
}
