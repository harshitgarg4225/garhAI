/**
 * actions.ts — running a geometric constraint from the canvas (C-3).
 *
 * The maths is in `@garh/model`'s `solveConstraint`, deliberately: it is pure, it is
 * the same code the Python twin could run, and keeping it out of the UI means a
 * constraint cannot behave one way in the app and another in a replay.
 *
 * What lives here is everything that is *about the app*: reading the selection in the
 * order the architect clicked, dispatching through the model store so the change is
 * undoable and rules-checked like any other edit, and — the part that matters most —
 * saying out loud what happened.
 *
 * ## Three things this reports that a quieter implementation would swallow
 *
 * 1. **A refusal.** "Nothing happened" after clicking Parallel is indistinguishable
 *    from a broken button. Every refusal from the solver is a sentence, and it is shown.
 * 2. **A rejection by the fold.** `solveConstraint` is geometry, not validation: it can
 *    legitimately propose sliding a wall onto another one, and the model refuses that.
 *    The dispatch path already surfaces rejections, and this must not swallow them.
 * 3. **Rounding drift.** Integer millimetres cannot represent an arbitrary rotation, so
 *    a rotated wall's length can change by about a millimetre. A drafting aid that
 *    silently changes a wall's length silently changes an area statement, so when it
 *    happens the toast says so.
 */

import { constraintLabel, solveConstraint, type ConstraintKind } from '@garh/model';

import { useModelStore } from '../../stores/model';
import { useSelectionStore } from '../../stores/selection';
import { useUiStore } from '../../stores/ui';

/** Selected wall ids, in click order — the order IS the anchor rule. */
export function selectedWallIds(): string[] {
  const { ids, kinds } = useSelectionStore.getState();
  return ids.filter((id) => kinds[id] === 'wall');
}

/** How many walls a kind needs before its button means anything. */
export function requiredWalls(kind: ConstraintKind): number {
  return kind === 'horizontal' || kind === 'vertical' ? 1 : 2;
}

export function canRunConstraint(kind: ConstraintKind): boolean {
  return selectedWallIds().length >= requiredWalls(kind);
}

/**
 * Solve and apply one constraint against the current selection.
 *
 * Returns whether anything was applied, so a caller (and a test) can tell "did it" from
 * "explained why not" without reading the toast queue.
 */
export function runConstraint(kind: ConstraintKind): boolean {
  const doc = useModelStore.getState().doc;
  const toast = useUiStore.getState().pushToast;

  const wallIds = selectedWallIds();
  if (wallIds.length === 0) {
    toast({
      tone: 'info',
      title: 'Select a wall first',
      description: 'Constraints work on walls. Shift-click to add a second one.',
      dedupeKey: 'constraint-empty',
    });
    return false;
  }

  const result = solveConstraint(doc.house, {
    kind,
    // The ids are branded in the model and plain strings in the selection store. The
    // solver looks every one of them up and refuses what it cannot find, which is a
    // stronger check than the cast this replaces.
    wallIds: wallIds as never,
  });

  if (result.ops.length === 0) {
    toast({
      tone: 'info',
      title: constraintLabel(kind).replace(/^Made |^Lengths |^Straightened /, 'Could not: '),
      description: result.reason,
      dedupeKey: `constraint-${kind}`,
    });
    return false;
  }

  const applied = useModelStore.getState().dispatch([...result.ops], {
    label: constraintLabel(kind),
    source: 'manual',
  });
  if (!applied.ok) return false; // The store already toasted the rejection with its reason.

  // Half a millimetre is not worth a sentence; a whole one on a dimensioned wall is.
  if (result.lengthDriftMm >= 1) {
    toast({
      tone: 'warning',
      title: 'Wall length changed by rounding',
      description: `Every coordinate is a whole millimetre, so turning the wall moved its length by up to ${result.lengthDriftMm} mm. Check any dimension that ran to it.`,
      dedupeKey: 'constraint-drift',
    });
  }
  return true;
}
