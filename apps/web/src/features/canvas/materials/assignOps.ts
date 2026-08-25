/**
 * assignOps.ts — the `material.assign` builders (op 29). Pure; pinned by
 * `assignOps.test.ts` against the REAL `applyGroup`.
 *
 * ONE ASSIGNMENT PER TARGET. The fold keys assignments by their id: same id →
 * replace, `materialId: null` → delete. So the builder derives the id
 * DETERMINISTICALLY from the target (`derivedId`, the same 130-bit SHA-256
 * scheme room ids use) and reuses the id of any existing assignment with the
 * same target. Re-assigning "external walls, whole building" therefore
 * replaces one document entry instead of accumulating shadowed rows —
 * without this, every swatch click would grow the document forever.
 */

import { derivedId, type HouseModel, type Id, type Op, type SurfaceGroupRef } from '@garh/model';

/** Canonical key for a target — the derived-id input and the equality rule. */
export function surfaceTargetKey(target: SurfaceGroupRef): string {
  return `material-assign:${target.group}:${target.storeyId ?? '*'}:${target.elementId ?? '*'}`;
}

function sameTarget(a: SurfaceGroupRef, b: SurfaceGroupRef): boolean {
  return surfaceTargetKey(a) === surfaceTargetKey(b);
}

/** The assignment id ops for `target` must carry (existing id, else derived). */
export function assignmentIdFor(house: HouseModel, target: SurfaceGroupRef): Id<'material'> {
  const existing = house.materials.find((m) => sameTarget(m.target, target));
  if (existing !== undefined) return existing.id;
  return derivedId('material', surfaceTargetKey(target));
}

/** `material.assign` — set (or replace) the material a surface group wears. */
export function materialAssignOp(
  house: HouseModel,
  target: SurfaceGroupRef,
  materialId: string,
): Op {
  return {
    type: 'material.assign',
    payload: { id: assignmentIdFor(house, target), target, materialId },
  };
}

/**
 * Clear the assignment on `target`, or null when there is nothing to clear —
 * dispatching a delete for a row that does not exist would put a no-op group
 * on the undo stack.
 */
export function materialClearOp(house: HouseModel, target: SurfaceGroupRef): Op | null {
  const existing = house.materials.find((m) => sameTarget(m.target, target));
  if (existing === undefined) return null;
  return {
    type: 'material.assign',
    payload: { id: existing.id, target: existing.target, materialId: null },
  };
}
