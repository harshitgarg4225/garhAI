/**
 * Collision feedback — what turns the preview amber, and why.
 *
 * ## It never blocks
 *
 * Golden rule 5 says compliance informs and never blocks, and §15 says the same
 * about every advisory surface. Furniture is the easiest place in the app to
 * get this wrong, because "the sofa overlaps the wall" feels like an error the
 * app should refuse. It is not. Architects overlap things on purpose: a built-in
 * wardrobe IS inside the wall zone; a bay window seat IS outside the room
 * polygon; a car in a stilt parking IS half under the slab above. Every
 * function here returns advisories. `commit()` in `placement.ts` reads none of
 * them.
 *
 * ## Cost
 *
 * The obstacle set is built ONCE per (document, storey) change and cached by
 * the placement context, not rebuilt per pointer move. A move then costs a
 * bounds test per obstacle (an integer compare ×4) plus an exact SAT only for
 * the handful that survive — microseconds for the ~120 obstacles a G+2 demo
 * storey has, which is what keeps the §14 16 ms frame budget intact while the
 * preview follows the cursor.
 */

import type { RoomType } from '@garh/model';

import {
  bounds2x,
  boundsOverlap,
  clearanceQuad2x,
  footprintQuad2x,
  quadInsideRoom,
  quadsOverlap,
  roomAtPt,
  toObstacle,
  wallQuad2x,
  type WallLike,
} from './geometry';
import type {
  CatalogueItem,
  Obstacle,
  PlacedFurniture,
  PlacementIssue,
  Pose,
  RoomLike,
} from './types';

/**
 * Everything the preview needs in order to answer "is this a good spot?".
 * Immutable — a new one is built when the document, storey or snap changes.
 */
export interface PlacementContext {
  readonly storeyId: string | null;
  /** 115 mm module by default; 25 mm on the fine grid; 0 when snap is off. */
  readonly snapStepMm: number;
  readonly obstacles: readonly Obstacle[];
  readonly rooms: readonly RoomLike[];
}

export const EMPTY_CONTEXT: PlacementContext = {
  storeyId: null,
  snapStepMm: 0,
  obstacles: [],
  rooms: [],
};

export interface BuildContextInput {
  readonly storeyId: string | null;
  readonly snapStepMm: number;
  readonly walls: readonly WallLike[];
  readonly furniture: readonly PlacedFurniture[];
  readonly rooms: readonly RoomLike[];
  /** The instance being dragged — it must not collide with itself. */
  readonly excludeFurnitureId?: string | null | undefined;
}

/**
 * Pre-transform the storey's walls and furniture into doubled-mm quads.
 *
 * Furniture whose catalogue entry is missing contributes no obstacle: we do not
 * know how big it is, and inventing a size to collide against would produce a
 * warning nobody can act on.
 */
export function buildPlacementContext(input: BuildContextInput): PlacementContext {
  const obstacles: Obstacle[] = [];

  for (const wall of input.walls) {
    if (wall.storeyId !== input.storeyId) continue;
    obstacles.push(toObstacle(wall.id, 'wall', 'a wall', wallQuad2x(wall)));
  }

  for (const placed of input.furniture) {
    if (placed.storeyId !== input.storeyId) continue;
    if (placed.id === input.excludeFurnitureId) continue;
    if (placed.item === null) continue;
    obstacles.push(
      toObstacle(placed.id, 'furniture', placed.item.name, footprintQuad2x(placed.item, placed.pose)),
    );
  }

  return {
    storeyId: input.storeyId,
    snapStepMm: input.snapStepMm,
    obstacles,
    rooms: [...input.rooms],
  };
}

// ---------------------------------------------------------------------------
// Evaluation
// ---------------------------------------------------------------------------

/** Human wording for a room, falling back to its type label. */
function roomLabel(room: RoomLike): string {
  return room.name.trim() === '' ? room.type.replace(/_/g, ' ') : room.name;
}

/**
 * Advisories for one pose. Ordered most-actionable first, deduped by target so
 * a sofa straddling four wall segments says "overlaps a wall" once.
 *
 * Severity, and the reasoning behind each:
 *
 *  - `overlaps-furniture` **warn** — two solid objects in one place is almost
 *    always a slip.
 *  - `overlaps-wall` **warn** — same, with the honest caveat in the fix hint
 *    that built-ins are supposed to.
 *  - `clearance-blocked` **info** — the access strip is a comfort standard, not
 *    a code minimum, and a bedside table inside a bed's walk-past strip is
 *    normal. Saying "warn" here would cry wolf.
 *  - `outside-room` **info** — placing on a terrace, a porch or a stilt is
 *    ordinary work.
 *  - `unexpected-room` **info** — the catalogue's `roomTypes` is a suggestion
 *    list for the browser filter, not a rule. A washing machine in a kitchen
 *    instead of a utility is a preference, not a defect.
 */
export function evaluatePlacement(
  item: CatalogueItem,
  pose: Pose,
  ctx: PlacementContext,
): PlacementIssue[] {
  const issues: PlacementIssue[] = [];

  const foot = footprintQuad2x(item, pose);
  const footBounds = bounds2x(foot);
  const clear = clearanceQuad2x(item, pose);
  const clearBounds = clear === null ? null : bounds2x(clear);

  const hitFurniture: Obstacle[] = [];
  const hitWalls: Obstacle[] = [];
  const blockingClearance: Obstacle[] = [];

  for (const obstacle of ctx.obstacles) {
    const nearFoot = boundsOverlap(footBounds, obstacle.bounds);
    const nearClear = clearBounds !== null && boundsOverlap(clearBounds, obstacle.bounds);
    if (!nearFoot && !nearClear) continue;

    if (nearFoot && quadsOverlap(foot, obstacle.quad)) {
      if (obstacle.kind === 'furniture') hitFurniture.push(obstacle);
      else hitWalls.push(obstacle);
      continue;
    }
    if (nearClear && clear !== null && quadsOverlap(clear, obstacle.quad)) {
      blockingClearance.push(obstacle);
    }
  }

  if (hitFurniture.length > 0) {
    const names = uniqueNames(hitFurniture);
    issues.push({
      code: 'overlaps-furniture',
      severity: 'warn',
      message:
        names.length === 1
          ? `Overlaps ${firstName(names)}.`
          : `Overlaps ${names.length} other items (${names.slice(0, 2).join(', ')}…).`,
      basis: 'Footprints from the furniture catalogue, in millimetres.',
      fixHint: 'Move it, or rotate with R. You can drop it here anyway and sort it out later.',
      targetIds: hitFurniture.map((o) => o.id),
    });
  }

  if (hitWalls.length > 0) {
    issues.push({
      code: 'overlaps-wall',
      severity: 'warn',
      message: hitWalls.length === 1 ? 'Overlaps a wall.' : `Overlaps ${hitWalls.length} walls.`,
      basis: 'Wall centreline rectangles at their built thickness.',
      fixHint: 'Nudge it clear — or leave it, if this is a built-in that sits in the wall.',
      targetIds: hitWalls.map((o) => o.id),
    });
  }

  if (blockingClearance.length > 0 && item.clearanceMm > 0) {
    const names = uniqueNames(blockingClearance);
    issues.push({
      code: 'clearance-blocked',
      severity: 'info',
      message: `Its ${item.clearanceMm} mm access strip is blocked by ${firstName(names)}${
        names.length > 1 ? ` and ${names.length - 1} more` : ''
      }.`,
      basis: item.clearanceAssumed
        ? `Assumed ${item.clearanceMm} mm access — the catalogue did not send one for this item.`
        : `Catalogue clearance: ${item.clearanceMm} mm of access space in front.`,
      fixHint: 'Turn it to face open floor, or accept the tighter access.',
      targetIds: blockingClearance.map((o) => o.id),
    });
  }

  const room = roomAtPt(ctx.rooms, pose.pt);
  if (ctx.rooms.length > 0) {
    if (room === null) {
      issues.push({
        code: 'outside-room',
        severity: 'info',
        message: 'Not inside any detected room.',
        basis: 'Rooms come from the plan’s wall subdivision.',
        fixHint: 'Fine for a terrace, porch or stilt — otherwise check the walls close.',
        targetIds: [],
      });
    } else {
      if (!quadInsideRoom(foot, room)) {
        issues.push({
          code: 'outside-room',
          severity: 'info',
          message: `Sticks out of ${roomLabel(room)}.`,
          basis: 'Rooms come from the plan’s wall subdivision.',
          fixHint: 'Nudge it in, or leave it if it is meant to run through the opening.',
          targetIds: [room.id],
        });
      }
      if (item.roomTypes.length > 0 && !item.roomTypes.includes(room.type)) {
        issues.push({
          code: 'unexpected-room',
          severity: 'info',
          message: `${item.name} is not a usual fit for ${roomLabel(room)}.`,
          basis: 'The catalogue lists the rooms this item normally goes in.',
          fixHint: 'Only a suggestion — place it if that is the design.',
          targetIds: [room.id],
        });
      }
    }
  }

  return issues;
}

/** First name, or a neutral stand-in — the sentence must never read "undefined". */
function firstName(names: readonly string[]): string {
  return names[0] ?? 'something else';
}

function uniqueNames(obstacles: readonly Obstacle[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const o of obstacles) {
    if (seen.has(o.label)) continue;
    seen.add(o.label);
    out.push(o.label);
  }
  return out;
}

/** The worst severity present — what the preview colour keys off. */
export function issueTone(issues: readonly PlacementIssue[]): 'ok' | 'info' | 'warn' {
  let tone: 'ok' | 'info' | 'warn' = 'ok';
  for (const issue of issues) {
    if (issue.severity === 'warn') return 'warn';
    tone = 'info';
  }
  return tone;
}

/**
 * The room type a browser filter should default to for a given point — used
 * when someone arms the tool with a room selected.
 */
export function roomTypeAt(ctx: PlacementContext, pose: Pose): RoomType | null {
  const room = roomAtPt(ctx.rooms, pose.pt);
  return room === null ? null : room.type;
}

// ---------------------------------------------------------------------------
// The compliance-strip view of the same data
// ---------------------------------------------------------------------------

/**
 * A furniture advisory shaped for the bottom compliance strip.
 *
 * Two honest differences from a real compliance chip, both deliberate:
 *
 *  - Status is never `fail`. Nothing here is a code violation; the worst case
 *    is a furnishing conflict an architect may want on purpose.
 *  - There is no `cite`. These come from catalogue clearances, not from NBC or
 *    a city bye-law, and inventing a clause number for a drawing that goes to a
 *    municipal office would be worse than showing none at all. `basis` says
 *    where the number really came from; show it where a cite would go.
 */
export interface FurnitureAdvisoryChip {
  readonly id: string;
  readonly status: 'warn' | 'not_applicable';
  readonly message: string;
  readonly basis: string;
  readonly fixHint: string;
  readonly targetIds: readonly string[];
}

/**
 * Issues → chips. Pure and cheap.
 *
 * The ≤500 ms debounce (§14) belongs to the strip that renders these, not here:
 * hiding the timer inside this function would put the budget somewhere the
 * surface responsible for it cannot see.
 */
export function furnitureAdvisoryChips(
  issues: readonly PlacementIssue[],
): FurnitureAdvisoryChip[] {
  return issues.map((issue, index) => ({
    id: `furniture-${issue.code}-${index}`,
    status: issue.severity === 'warn' ? 'warn' : 'not_applicable',
    message: issue.message,
    basis: issue.basis,
    fixHint: issue.fixHint,
    targetIds: issue.targetIds,
  }));
}
