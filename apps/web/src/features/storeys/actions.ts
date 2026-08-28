/**
 * actions.ts — where a planned storey copy becomes ONE undoable edit.
 *
 * `copyStorey.ts` builds the op list and touches nothing. This file is the one
 * place that dispatches it, and the whole file exists to make one property
 * unmissable and testable:
 *
 *   **ONE GESTURE IS ONE UNDO.**
 *
 * A storey copy on a real G+1 is fifty-odd ops — clear the target, add the
 * walls, re-host the openings, carry the room names. Dispatching them one at a
 * time would fold to the same document and hash the same, and would be a bug
 * anyway: the architect made one decision, so ⌘Z must take back one decision,
 * not peel a floor off a wall at a time. `dispatch(ops)` applies the whole list
 * under a single `groupId` and pushes a single history entry — see
 * `stores/model.ts`. There is exactly one `dispatch` call per action below, and
 * `copyStorey.test.ts` asserts the undo stack grows by exactly one and that a
 * single `undo()` restores the state hash. Break the grouping and that test
 * goes red; that is the point of it.
 *
 * The active storey follows the copy. "Make the first floor the same as the
 * ground floor, then change three things" means the next three things happen
 * upstairs, so the switch is part of the gesture. It is view state, not model
 * state, which is why it is set here and not in an op — and why undo does not
 * take you back down: nothing about which floor you are looking at belongs in
 * the op log.
 */

import { useModelStore } from '../../stores/model';
import { useUiStore } from '../../stores/ui';
import {
  addStoreyOp,
  isStoreyEmpty,
  planStoreyCopy,
  type StoreyCopyInput,
  type StoreyCopyPlan,
  type StoreyCopyRefusal,
} from './copyStorey';

export type StoreyCopyOutcome =
  | { readonly ok: true; readonly plan: StoreyCopyPlan }
  | { readonly ok: false; readonly refusal: StoreyCopyRefusal };

/** Toast copy for a group the user can take back with one press. */
function undoToast(title: string, description: string | null): void {
  useUiStore.getState().pushToast({
    tone: 'success',
    title,
    description,
    action: { label: 'Undo', run: () => void useModelStore.getState().undo() },
  });
}

/**
 * Plan a copy against the live document and apply it as one group.
 *
 * Returns the refusal rather than throwing or toasting it: the panel that asked
 * for the copy is the right place to show why it cannot happen, next to the
 * controls that would change the answer.
 */
export function runStoreyCopy(input: StoreyCopyInput): StoreyCopyOutcome {
  const model = useModelStore.getState();
  const planned = planStoreyCopy(model.doc, input);
  if (!planned.ok) return planned;
  const plan = planned.plan;

  // ONE dispatch. See the header — this line is the undo contract.
  const result = model.dispatch(plan.ops, { label: plan.label, source: 'manual' });

  if (!result.ok) {
    // The plan folded clean a moment ago, so this is a document that moved
    // under us (a collaborator's op, a rebase). Say so honestly instead of
    // pretending the copy happened.
    return {
      ok: false,
      refusal: {
        reason: 'rejected',
        message:
          'The design changed while that copy was being prepared. Try it again from the panel.',
        issues: result.issues,
      },
    };
  }

  useUiStore.getState().setActiveStorey(plan.targetStoreyId);

  const replacedNote = isStoreyEmpty(plan.replaced)
    ? `${String(plan.ops.length)} changes, one undo step.`
    : `${plan.targetName}'s previous contents were replaced. One undo puts them back.`;
  undoToast(plan.label, replacedNote);

  return { ok: true, plan };
}

export type AddStoreyOutcome =
  | { readonly ok: true; readonly storeyId: string; readonly name: string }
  | { readonly ok: false; readonly message: string };

/** Add an empty storey on top and make it active. One op, one undo. */
export function runAddStorey(): AddStoreyOutcome {
  const model = useModelStore.getState();
  const { op, storeyId } = addStoreyOp(model.doc);
  const result = model.dispatch([op], { label: 'Storey added', source: 'manual' });
  if (!result.ok) {
    const first = result.issues[0];
    return { ok: false, message: first?.message ?? 'That storey could not be added.' };
  }

  // The fold names the storey (`defaultStoreyName`), so the name is read back
  // from the document rather than guessed here — one source of truth for
  // "First Floor", shared with the Python twin.
  const name = result.doc.house.storeys.find((s) => s.id === storeyId)?.name ?? 'New storey';
  useUiStore.getState().setActiveStorey(storeyId);
  undoToast(`${name} added`, null);
  return { ok: true, storeyId, name };
}
