/**
 * removeStorey.ts — what removing a storey means, before anything is dispatched.
 *
 * `storey.remove` (op 7) takes an INDEX and cascades to everything on that
 * storey — walls, openings, rooms, stairs, columns, furniture, balconies — and
 * the fold's inverse puts every one of them back, so it is one op and one undo.
 * What this module adds is the part a panel needs before it asks: the index
 * resolved from an id against the live document, the count of what will go,
 * and which storey the architect should be looking at afterwards.
 *
 * The plan is pure and is computed live by the confirm dialog, so what the
 * dialog promises ("First Floor and its 12 walls, 8 openings…") is what the op
 * does — the same rule `copyStorey.ts` keeps for its own dialog.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE ONE REFUSAL
 * ────────────────────────────────────────────────────────────────────────────
 * A storey that does not exist. That is all. Removing the LAST storey is
 * allowed: an empty design is a valid document (it is what a new project is),
 * and refusing would leave a stray storey nobody can get rid of. The dialog
 * says plainly that the design will be empty.
 */

import type { Op, ProjectDoc } from '@garh/model';

import { isStoreyEmpty, storeyContentCounts, type StoreyContentCounts } from './copyStorey';

export interface StoreyRemovePlan {
  readonly storeyId: string;
  readonly storeyName: string;
  /** The index `storey.remove` takes, resolved against the live document. */
  readonly index: number;
  /** What the cascade deletes. */
  readonly removed: StoreyContentCounts;
  /** True when nothing but the storey itself goes. */
  readonly empty: boolean;
  /**
   * The storey to make active afterwards: the one below, else the one above,
   * else null when the design is left with no storeys.
   */
  readonly nextActiveStoreyId: string | null;
  /** True when this is the last storey in the design. */
  readonly last: boolean;
  readonly op: Op;
  /** Undo-toast copy (§15): "Ground Floor removed". */
  readonly label: string;
}

export interface StoreyRemoveRefusal {
  readonly reason: 'unknown-storey' | 'rejected';
  readonly message: string;
}

export type StoreyRemovePlanResult =
  | { readonly ok: true; readonly plan: StoreyRemovePlan }
  | { readonly ok: false; readonly refusal: StoreyRemoveRefusal };

export function planStoreyRemove(doc: ProjectDoc, storeyId: string): StoreyRemovePlanResult {
  const storeys = doc.house.storeys;
  const index = storeys.findIndex((s) => s.id === storeyId);
  const storey = storeys[index];
  if (index < 0 || storey === undefined) {
    return {
      ok: false,
      refusal: {
        reason: 'unknown-storey',
        message: 'That storey is no longer part of this design.',
      },
    };
  }

  const removed = storeyContentCounts(doc.house, storeyId);
  const below = storeys[index - 1];
  const above = storeys[index + 1];
  const nextActiveStoreyId = below?.id ?? above?.id ?? null;

  return {
    ok: true,
    plan: {
      storeyId,
      storeyName: storey.name,
      index,
      removed,
      empty: isStoreyEmpty(removed),
      nextActiveStoreyId,
      last: storeys.length === 1,
      op: { type: 'storey.remove', payload: { index } },
      label: `${storey.name} removed`,
    },
  };
}
