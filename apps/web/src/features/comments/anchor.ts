/**
 * anchor.ts — reading and writing a comment's `anchor`, and turning a thread
 * into pins on the plan.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE SHAPE, AND WHY IT IS PARSED DEFENSIVELY
 * ────────────────────────────────────────────────────────────────────────────
 * `Comment.anchor` is JSONB on the server (`models.py`) typed as
 * `dict[str, Any]` and defaulting to `{}` (`CommentIn`). Nothing validates its
 * contents on either side — deliberately, because three surfaces write into it
 * with three different meanings:
 *
 *     {kind: 'plan',   target: <storeyId>, x: <mm>, y: <mm>}   ← this file
 *     {kind: 'sheet',  target: <sheetId>,  x: <mm>, y: <mm>}
 *     {kind: 'render', target: <renderId>, x: <mm>, y: <mm>}
 *
 * An unanchored comment (the plain composer, and every comment written before
 * pins existed) is `{}`. So EVERY read here has to survive: an empty object, a
 * `kind` this build has never heard of, a `x` that arrived as a string from a
 * client we do not control, and a `null` where a number was expected. It does
 * that by returning `null` for anything it cannot fully understand, which is
 * the same drop-don't-throw discipline the SSE frame parser uses — a comment
 * with a malformed anchor still belongs in the thread; it just gets no pin.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHY `target` HOLDS THE STOREY ID
 * ────────────────────────────────────────────────────────────────────────────
 * A plan pin has to know which storey it is on, or a comment about the
 * first-floor bathroom appears on top of the ground-floor kitchen. `target` is
 * the anchor's existing "which drawing surface" slot, so a plan anchor puts the
 * storey id there rather than inventing a parallel key — and an id rather than
 * an index because indices shift the moment anyone inserts a storey, which
 * would silently relocate every pin in the project.
 *
 * A plan anchor with NO target is treated as "every storey". That is the
 * honest reading of an anchor written by a surface that did not know about
 * storeys, and it fails safe: the pin is visible and can be read, rather than
 * invisible everywhere and quietly lost.
 */

import type { Pt } from '@garh/model';

import type { Comment } from '../../lib/schemas';

/** The anchor kinds the product writes. `plan` is the only one this file draws. */
export const ANCHOR_KINDS = ['plan', 'sheet', 'render'] as const;
export type AnchorKind = (typeof ANCHOR_KINDS)[number];

/** A comment anchored to a point on the plan. */
export interface PlanAnchor {
  readonly kind: 'plan';
  /** Storey id, or `null` for "not storey-bound" (shows on every storey). */
  readonly storeyId: string | null;
  /** Plot-local integer millimetres — the same space walls live in. */
  readonly x: number;
  readonly y: number;
}

/** A finite number, or null. Rejects `NaN`, `Infinity`, strings and `null`. */
function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** ISO timestamp → epoch ms; an unparseable one sorts as 0 (see the caller). */
function timeOf(iso: string): number {
  const parsed = Date.parse(iso);
  return Number.isNaN(parsed) ? 0 : parsed;
}

/** A non-empty string, or null. */
function nonEmptyString(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
}

/**
 * Read a comment's anchor as a plan anchor, or `null` if it is not one.
 *
 * Coordinates are rounded to whole millimetres on the way in. The model's
 * integer-mm contract applies to anything that describes a position in the
 * drawing, and the server stores whatever JSON number it is handed — so this is
 * the boundary where a float from an older client or a hand-written anchor
 * becomes a millimetre.
 */
export function readPlanAnchor(anchor: Readonly<Record<string, unknown>>): PlanAnchor | null {
  if (anchor.kind !== 'plan') return null;
  const x = finiteNumber(anchor.x);
  const y = finiteNumber(anchor.y);
  if (x === null || y === null) return null;
  return {
    kind: 'plan',
    storeyId: nonEmptyString(anchor.target),
    // Half-away-from-zero is the model's rule, but these values never become an
    // op payload — they are read-only marks on someone else's geometry — so
    // plain truncation-free rounding through `Math.round` would be a different
    // rule for no reason. Use the same asymmetry-free form `roundMm` uses.
    x: x >= 0 ? Math.floor(x + 0.5) : -Math.floor(-x + 0.5),
    y: y >= 0 ? Math.floor(y + 0.5) : -Math.floor(-y + 0.5),
  };
}

/**
 * Build the `anchor` body for a new plan-pinned comment.
 *
 * Returned as a plain `Record<string, unknown>` because that is what
 * `api.comments.create` takes and what the server stores; there is no schema to
 * satisfy, only a shape to keep consistent with {@link readPlanAnchor}. The two
 * are each other's inverse and the unit tests assert the round trip — which is
 * the only thing stopping a future edit from writing `storeyId` here and
 * reading `target` there.
 */
export function planAnchorPayload(ptMm: Pt, storeyId: string | null): Record<string, unknown> {
  return {
    kind: 'plan',
    target: storeyId ?? '',
    x: ptMm.x,
    y: ptMm.y,
  };
}

// ---------------------------------------------------------------------------
// Comments → pins
// ---------------------------------------------------------------------------

/** One numbered pin on the plan. */
export interface CommentPin {
  readonly comment: Comment;
  readonly anchor: PlanAnchor;
  /**
   * The number drawn in the pin, 1-based.
   *
   * Assigned over EVERY plan-anchored comment in the project in chronological
   * order — before any storey or resolved filtering — so that "pin 3" means the
   * same thread whichever storey you are looking at and whether or not the
   * resolved ones are hidden. Numbering the visible subset instead would
   * renumber every pin the moment you switched floors, and a reference like
   * "see pin 3" in a comment body would stop meaning anything.
   */
  readonly number: number;
}

export interface PinFilter {
  /** Storey being drawn. `null` shows every pin (no storey context). */
  readonly storeyId: string | null;
  /** Include pins whose comment is resolved. */
  readonly includeResolved: boolean;
}

/**
 * Every plan-anchored comment, numbered chronologically. Order is oldest-first
 * regardless of what order the caller's list is in — `useComments` holds the
 * thread newest-first for display, and pin numbers must not depend on that.
 */
export function numberPlanPins(comments: readonly Comment[]): CommentPin[] {
  const anchored: { comment: Comment; anchor: PlanAnchor }[] = [];
  for (const comment of comments) {
    const anchor = readPlanAnchor(comment.anchor);
    if (anchor !== null) anchored.push({ comment, anchor });
  }
  anchored.sort((a, b) => {
    // `createdAt` is the raw ISO STRING off the wire (`isoDateTime` in
    // schemas.ts is a string, not a coerced Date). Parse rather than compare
    // lexically: the server's offset format is consistent today, but a string
    // comparison that silently stops being chronological the day someone emits
    // a `Z` next to a `+05:30` is exactly the kind of quiet wrongness that
    // renumbers a project's pins with no error anywhere.
    const byTime = timeOf(a.comment.createdAt) - timeOf(b.comment.createdAt);
    // Ties are real: two comments posted in the same millisecond during a seed
    // or an import, and an unparseable date makes every comparison a tie. Fall
    // back to the id so the numbering is total and stable rather than dependent
    // on the input array's order.
    if (byTime !== 0) return byTime;
    return a.comment.id < b.comment.id ? -1 : a.comment.id > b.comment.id ? 1 : 0;
  });
  return anchored.map((entry, index) => ({ ...entry, number: index + 1 }));
}

/**
 * The pins to draw on one storey. Numbering is inherited from
 * {@link numberPlanPins}, never recomputed on the filtered subset.
 */
export function planPins(comments: readonly Comment[], filter: PinFilter): CommentPin[] {
  return numberPlanPins(comments).filter((pin) => {
    if (!filter.includeResolved && pin.comment.resolved) return false;
    // A pin with no storey belongs to all of them; a storey-less view shows all.
    if (pin.anchor.storeyId === null || filter.storeyId === null) return true;
    return pin.anchor.storeyId === filter.storeyId;
  });
}

/** The tooltip line for a pin: the first line of the body, trimmed and capped. */
export function pinExcerpt(body: string, maxChars = 80): string {
  const firstLine = body.split('\n', 1)[0]?.trim() ?? '';
  if (firstLine.length <= maxChars) return firstLine;
  return `${firstLine.slice(0, maxChars - 1).trimEnd()}…`;
}
