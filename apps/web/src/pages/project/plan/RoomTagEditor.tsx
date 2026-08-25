/**
 * RoomTagEditor — click a room's name or its area and type a new one.
 *
 * §15: *"Numbers editable everywhere: any dimension/area label on canvas is
 * click-to-edit (dispatches op). No dead text."* The overlays layer already
 * gives dimensions that behaviour through `DimensionEditor`; the room tag is
 * the other label on the plan, and this is its equivalent.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHICH HALF WAS CLICKED
 * ════════════════════════════════════════════════════════════════════════════
 * `RoomTagLayer` registers both lines of a tag as `kind: 'room'`; the area line
 * carries `AREA_HANDLE_SUFFIX` on its pick id so the two are distinguishable
 * without inventing a synthetic `PickKind`. `parseRoomTagHandle` splits it, and
 * that decides which op this field builds:
 *
 *   name → `room.assign`     (op 19, carries the CURRENT type so a rename is a
 *                             rename and not a silent reclassification)
 *   area → `room.set_target` (op 20 — the BRIEF target, not the measured area)
 *
 * That second point is the one worth being loud about, and the field says it
 * on screen: a room's real area is a consequence of where its walls are, and
 * typing a number cannot move four walls. What you are setting is the target
 * the solver and the brief work to. Pretending otherwise would be a lie that
 * shows up as "I typed 12 m² and nothing moved".
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE CONVERSION BOUNDARY
 * ════════════════════════════════════════════════════════════════════════════
 * `parseAreaInput` returns integer mm², `roomTargetAreaOp` takes it unchanged.
 * The field never computes a coordinate.
 */

import { useEffect, useRef, useState } from 'react';

import { cn } from '@garh/ui';

import type { Op, Room, UnitsDisplay } from '@garh/model';

import {
  areaEditSeed,
  areaHint,
  parseAreaInput,
  roomNameOp,
  roomTargetAreaOp,
  roomTypeLabel,
} from '../../../features/canvas/overlays';
import { useModelStore } from '../../../stores/model';
import { useUiStore } from '../../../stores/ui';

/** Which half of the tag is open. */
export type RoomTagPart = 'name' | 'area';

export interface RoomTagEditSession {
  readonly roomId: string;
  readonly part: RoomTagPart;
  readonly atPx: { readonly x: number; readonly y: number };
}

export interface RoomTagEditorProps {
  readonly session: RoomTagEditSession;
  readonly room: Room;
  readonly display: UnitsDisplay;
  readonly onClose: () => void;
}

const FIELD_WIDTH_PX = 168;

export function RoomTagEditor({
  session,
  room,
  display,
  onClose,
}: RoomTagEditorProps): JSX.Element {
  const isArea = session.part === 'area';

  const [text, setText] = useState(() =>
    isArea
      ? room.targetAreaMm2 === null
        ? areaEditSeed(room.areaMm2, display)
        : areaEditSeed(room.targetAreaMm2, display)
      : room.name,
  );
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Open with the value selected: the first keystroke replaces it, which is
  // what makes this feel like editing a label rather than filling a form.
  useEffect(() => {
    inputRef.current?.select();
  }, [session.roomId, session.part]);

  const commit = (): void => {
    const trimmed = text.trim();

    if (isArea) {
      // An empty field clears the target rather than failing — "I no longer
      // want to constrain this room" needs to be expressible.
      if (trimmed === '') {
        dispatch([roomTargetAreaOp(room.id, null)], 'Room target cleared');
        onClose();
        return;
      }
      const parsed = parseAreaInput(trimmed, display);
      if (!parsed.ok) {
        setError(parsed.error);
        return;
      }
      dispatch([roomTargetAreaOp(room.id, parsed.mm2)], 'Room target set');
      onClose();
      return;
    }

    if (trimmed === room.name) {
      onClose();
      return;
    }
    dispatch([roomNameOp(room.id, room.type, trimmed)], 'Room renamed');
    onClose();
  };

  return (
    <div
      className="pointer-events-auto absolute z-10"
      style={{
        left: `${session.atPx.x}px`,
        top: `${session.atPx.y}px`,
        width: `${FIELD_WIDTH_PX}px`,
        transform: 'translate(-50%, -140%)',
      }}
      // The global keyboard map must stay quiet while this is focused, or `w`
      // in "Powder Room" arms the wall tool. `isTypingTarget` reads this.
      data-garh-keys="off"
    >
      <label className="block rounded-md border border-line-strong bg-surface p-1.5 shadow-lg">
        <span className="mb-1 block text-2xs font-medium text-ink-subtle">
          {isArea ? `Target area · ${roomTypeLabel(room.type)}` : 'Room name'}
        </span>
        <input
          ref={inputRef}
          value={text}
          aria-label={isArea ? 'Target area' : 'Room name'}
          onChange={(event) => {
            setText(event.target.value);
            setError(null);
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              commit();
            } else if (event.key === 'Escape') {
              event.preventDefault();
              onClose();
            }
          }}
          onBlur={onClose}
          className={cn(
            'garh-focus-ring h-7 w-full rounded border bg-surface px-1.5 text-xs text-ink garh-nums',
            error === null ? 'border-line' : 'border-fail',
          )}
        />
      </label>
      <p
        className={cn(
          'mt-1 rounded bg-surface/95 px-1.5 py-1 text-2xs leading-4 backdrop-blur',
          error === null ? 'text-ink-subtle' : 'text-fail',
        )}
        role={error === null ? undefined : 'alert'}
      >
        {error ??
          (isArea
            ? `${areaHint(display)} This sets the target, not the walls.`
            : 'Enter to save, Esc to leave it.')}
      </p>
    </div>
  );
}

/** One dispatch path, with the §15 undo toast, for both halves of the tag. */
function dispatch(ops: readonly Op[], label: string): void {
  const result = useModelStore.getState().dispatch(ops, { label, source: 'manual' });
  if (result.ok) {
    useUiStore.getState().pushToast({
      tone: 'info',
      title: label,
      action: {
        label: 'Undo',
        run: () => {
          useModelStore.getState().undo();
        },
      },
    });
    return;
  }
  useUiStore.getState().pushToast({
    tone: 'warning',
    title: result.issues[0]?.message ?? 'That change is not valid here.',
    // `?? null`: ToastInput.description is `string | null` and does not admit
    // an explicit undefined under exactOptionalPropertyTypes (TS2375).
    description: result.issues[0]?.fix ?? null,
    dedupeKey: 'room-tag-rejected',
  });
}
