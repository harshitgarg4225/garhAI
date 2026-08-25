/**
 * Op builders for furniture. **This file is the conversion boundary.**
 *
 * Everything upstream of here may hold a float: a pointer position is a float,
 * a free-rotate bearing is a float, a drag delta is a float. Nothing downstream
 * of here may. Each builder rounds, snaps and asserts before it produces a
 * payload, so a float that escaped the tool state machine fails loudly at the
 * op boundary instead of quietly becoming a 1524.9998 mm bed in a drawing set
 * someone submits to a municipal office.
 *
 * ## The op is `furniture.set`
 *
 * The playbook talks about "furniture.place / transform / delete"; the model
 * core implements all three as ONE op — `furniture.set` with an `action`
 * discriminator (op 25, `packages/model/src/ops.ts`). These builders are named
 * after the actions and emit the real op. The fold's inverse for each action is
 * already written (`packages/model/src/fold.ts`), so undo works with no help
 * from this feature.
 *
 * Golden rule 1: these BUILD ops. The model store dispatches them. Nothing here
 * touches state.
 */

import {
  assertIntMm,
  newId,
  roundHalfAwayFromZero,
  type FurnitureId,
  type Op,
  type Pt,
  type StoreyId,
} from '@garh/model';

import { normaliseRotationDeg } from './geometry';
import type { CatalogueItem, Pose } from './types';

/** A fresh instance id. Kept here so the tool never invents an id format. */
export function newFurnitureId(): FurnitureId {
  return newId('furniture');
}

/**
 * Round a pose to the op contract and prove it.
 *
 * `assertIntMm` throws on a non-integer — which is the point. A silent
 * `Math.round` here would hide the bug that produced the float; a throw during
 * development names the tool that leaked one.
 */
function toPayloadPose(pose: Pose): { pt: Pt; rotationDeg: number } {
  const pt: Pt = {
    x: roundHalfAwayFromZero(pose.pt.x),
    y: roundHalfAwayFromZero(pose.pt.y),
  };
  assertIntMm(pt.x, 'furniture.pt.x');
  assertIntMm(pt.y, 'furniture.pt.y');
  return { pt, rotationDeg: normaliseRotationDeg(pose.rotationDeg) };
}

export interface PlaceFurnitureInput {
  readonly id: FurnitureId;
  readonly storeyId: string;
  readonly catalogId: string;
  readonly pose: Pose;
}

/** `action: 'place'` — a new instance on a storey. */
export function placeFurnitureOp(input: PlaceFurnitureInput): Op {
  const { pt, rotationDeg } = toPayloadPose(input.pose);
  return {
    type: 'furniture.set',
    payload: {
      action: 'place',
      id: input.id,
      storeyId: input.storeyId as StoreyId,
      catalogId: input.catalogId,
      pt,
      rotationDeg,
    },
  };
}

/**
 * `action: 'transform'` — move and/or rotate an existing instance.
 *
 * Both fields are always sent even when only one changed. The fold treats a
 * missing field as "keep the previous value", so sending both makes the op
 * self-describing: replaying a log, or reading a diff in the copilot preview,
 * shows where the item ended up without needing the state before it.
 */
export function transformFurnitureOp(id: string, pose: Pose): Op {
  const { pt, rotationDeg } = toPayloadPose(pose);
  return {
    type: 'furniture.set',
    payload: { action: 'transform', id: id as FurnitureId, pt, rotationDeg },
  };
}

/** `action: 'delete'`. The fold's inverse re-places the item, so undo restores it. */
export function deleteFurnitureOp(id: string): Op {
  return {
    type: 'furniture.set',
    payload: { action: 'delete', id: id as FurnitureId },
  };
}

/** Delete several items as one undo step. */
export function deleteFurnitureOps(ids: readonly string[]): Op[] {
  return ids.map(deleteFurnitureOp);
}

// ---------------------------------------------------------------------------
// Undo-toast labels (§15: "Wall deleted — Undo", sentence case, no full stop)
// ---------------------------------------------------------------------------

export function placeLabel(item: CatalogueItem | null): string {
  return item === null ? 'Furniture placed' : `${item.name} placed`;
}

export function moveLabel(item: CatalogueItem | null): string {
  return item === null ? 'Furniture moved' : `${item.name} moved`;
}

export function deleteLabel(items: readonly (CatalogueItem | null)[]): string {
  const only = items.length === 1 ? items[0] : undefined;
  if (items.length === 1) {
    return only === null || only === undefined ? 'Furniture deleted' : `${only.name} deleted`;
  }
  return `${items.length} items deleted`;
}
