/**
 * useDimensionEditing — the click-to-edit state machine.
 *
 * §12 requires every canvas interaction to be a state machine with the same
 * three exits. This one is:
 *
 *     idle ──pick a dimension──▶ editing ──Enter──▶ commit(ops) ──▶ idle
 *                                   │
 *                                   └──Esc / click away──▶ idle   (nothing dispatched)
 *
 * It is a hook rather than a component so the Plan page owns the wiring: the
 * page already receives `CanvasPointerEvent`s from `useCanvasControls` (the ONE
 * pointer path — see `core/pickRegistry.ts`), and this hook turns a pick into
 * an open editor and an editor into an op group.
 *
 * THE OP PATH, ONCE: `applyDimensionEdit` builds the ops, the model store
 * dispatches them as ONE group, and the store is the only writer. Nothing here
 * touches document state (golden rule 1). A rejection comes back as issues and
 * becomes a toast with a next action (golden rule 9), never a thrown error and
 * never a silently ignored keystroke.
 */

import { useCallback, useRef, useState } from 'react';

import type { HouseModel } from '@garh/model';

import { useModelStore } from '../../../../stores/model';
import { useUiStore } from '../../../../stores/ui';
import { applyDimensionEdit } from './edit';
import type { DimensionHandle, DimensionHandleIndex } from './DimensionLayer';

export interface DimensionEditSession {
  readonly handle: DimensionHandle;
  /** Canvas-relative pixels where the click landed — where the field opens. */
  readonly atPx: { readonly x: number; readonly y: number };
  readonly valueMm: number;
}

export interface DimensionEditing {
  readonly session: DimensionEditSession | null;
  /** Call from the page's `onClick` when `hit.kind === 'dimension'`. */
  readonly open: (
    index: DimensionHandleIndex,
    pickId: string,
    atPx: { x: number; y: number },
  ) => boolean;
  readonly commit: (house: HouseModel, valueMm: number) => void;
  readonly cancel: () => void;
  /** The last rejection, shown under the field. Cleared on the next edit. */
  readonly error: string | null;
}

export function useDimensionEditing(): DimensionEditing {
  const [session, setSession] = useState<DimensionEditSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Read imperatively rather than subscribed: nothing about this hook should
  // re-render when an unrelated op lands.
  const dispatchRef = useRef(useModelStore.getState().dispatch);
  dispatchRef.current = useModelStore.getState().dispatch;

  const open = useCallback(
    (index: DimensionHandleIndex, pickId: string, atPx: { x: number; y: number }): boolean => {
      const handle = index.lookup(pickId);
      // A pick id from a previous chain set resolves to null. Opening an editor
      // on a stale target would move a wall the user is not looking at, so the
      // click is simply ignored — the next rebuild gives fresh handles.
      if (handle === null) return false;
      setError(null);
      setSession({ handle, atPx, valueMm: handle.segment.valueMm });
      return true;
    },
    [],
  );

  const cancel = useCallback(() => {
    setSession(null);
    setError(null);
  }, []);

  const commit = useCallback(
    (house: HouseModel, valueMm: number) => {
      const current = session;
      if (current === null) return;

      const built = applyDimensionEdit(house, current.handle.target, valueMm);
      if (!built.ok) {
        // Keep the field open with the reason: the value is nearly right and
        // retyping it from scratch is a punishment for a near miss.
        setError(built.reason);
        return;
      }

      const result = dispatchRef.current(built.ops, { label: built.label, source: 'manual' });
      if (!result.ok) {
        const first = result.issues[0];
        setError(first?.message ?? 'That change is not valid here.');
        return;
      }

      // §15: everything undoable, visibly. The store records the group; the
      // toast is what tells you it happened and offers the way back.
      useUiStore.getState().pushToast({
        tone: 'info',
        title: `${built.label} — undo?`,
        action: {
          label: 'Undo',
          run: () => {
            useModelStore.getState().undo();
          },
        },
        durationMs: 5_000,
        dedupeKey: 'dimension-edit',
      });

      setSession(null);
      setError(null);
    },
    [session],
  );

  return { session, open, commit, cancel, error };
}
