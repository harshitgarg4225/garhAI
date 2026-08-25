/**
 * DimensionEditor.tsx — the field that opens when you click a dimension.
 *
 * ONE DOM node, and only while editing. §14's ban on DOM per label is about the
 * hundreds of labels drawn on the plan; there is never more than one edit field
 * open, and a real `<input>` is what gives us IME support, mobile keyboards,
 * text selection, and the accessibility §15 asks for. Rendering a text cursor
 * in WebGL to avoid one input would be the wrong trade in both directions.
 *
 * The three behaviours §12 requires of every tool, here as well:
 *   Esc     cancels — nothing is dispatched, the label goes back to what it was
 *   Enter   commits
 *   typing  overrides — the field opens with its value SELECTED, so the first
 *           keystroke replaces it rather than appending to it
 *
 * The field is positioned where the pointer was, not where the label is. Two
 * reasons: it needs no world→screen projection (so it cannot drift a frame
 * behind the camera), and it appears under the cursor, which is where the eye
 * already is.
 */

import { useEffect, useRef, useState } from 'react';

import { cn } from '@garh/ui';

import type { UnitsDisplay } from '@garh/model';

import { dimensionEditSeed, dimensionHint, dimensionTextMm, parseDimensionInput } from '../format';

export interface DimensionEditorProps {
  /** Canvas-relative CSS pixels — where the click landed. */
  atPx: { readonly x: number; readonly y: number };
  /** Current value, seeded into the field and selected. */
  valueMm: number;
  display: UnitsDisplay;
  /** What is being edited, for the field's label: "Bay width", "Door width". */
  label?: string | undefined;
  /** Called with integer mm. Never called with an unparsed string. */
  onCommit: (mm: number) => void;
  onCancel: () => void;
  /** Rejection from the op layer ("that would push the opening off the wall"). */
  error?: string | null | undefined;
}

/** Field width in pixels. Wide enough for `12'-6 1/2"`, no wider. */
const FIELD_WIDTH_PX = 132;

export function DimensionEditor({
  atPx,
  valueMm,
  display,
  label = 'Dimension',
  onCommit,
  onCancel,
  error,
}: DimensionEditorProps): JSX.Element {
  const [text, setText] = useState(() => dimensionEditSeed(valueMm));
  const [localError, setLocalError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Select on open: typing a number replaces the value, which is the whole
  // interaction. Focusing without selecting makes every edit a select-all first.
  useEffect(() => {
    inputRef.current?.select();
  }, []);

  // A new target while the field is open (click one dimension, then another)
  // re-seeds it rather than leaving the previous value in place.
  useEffect(() => {
    setText(dimensionEditSeed(valueMm));
    setLocalError(null);
  }, [valueMm]);

  const commit = (): void => {
    const parsed = parseDimensionInput(text);
    if (!parsed.ok) {
      setLocalError(parsed.error);
      return;
    }
    setLocalError(null);
    onCommit(parsed.mm);
  };

  const preview = parseDimensionInput(text);
  const shown = localError ?? error ?? null;

  return (
    <div
      className="pointer-events-auto absolute z-20"
      // Offset so the field does not sit under the cursor that opened it.
      style={{ left: atPx.x + 10, top: atPx.y - 14, width: FIELD_WIDTH_PX }}
      // The canvas listens on the container; without this, typing `3600` would
      // also be read as tool shortcuts (3 = storey, 6 = nothing, 0 = nothing).
      onPointerDown={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      <div className="rounded-md border border-line-strong bg-surface p-1.5 shadow-lg">
        <label className="sr-only" htmlFor="garh-dimension-edit">
          {label}
        </label>
        <input
          id="garh-dimension-edit"
          ref={inputRef}
          type="text"
          inputMode="text"
          autoComplete="off"
          spellCheck={false}
          value={text}
          aria-label={label}
          aria-invalid={shown !== null || undefined}
          aria-describedby="garh-dimension-edit-hint"
          className={cn(
            'garh-focus-ring h-7 w-full rounded-sm border bg-surface px-1.5 text-sm font-semibold',
            'text-ink garh-nums',
            shown === null ? 'border-line-strong' : 'border-fail',
          )}
          onChange={(e) => {
            setText(e.target.value);
            if (localError !== null) setLocalError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              commit();
            } else if (e.key === 'Escape') {
              e.preventDefault();
              onCancel();
            }
          }}
          // Clicking away is a cancel, not a commit. A dimension edit moves a
          // wall; doing that because someone clicked elsewhere on the canvas is
          // a change nobody asked for. (Contrast `LengthInput` in a form, where
          // blur-commits is right — the value is not geometry until saved.)
          onBlur={onCancel}
        />
        <p id="garh-dimension-edit-hint" className="mt-1 text-2xs leading-tight text-ink-subtle">
          {shown !== null ? (
            <span className="text-fail-ink">{shown}</span>
          ) : preview.ok ? (
            <span className="garh-nums">{dimensionTextMm(preview.mm)}</span>
          ) : (
            dimensionHint(display)
          )}
        </p>
      </div>
    </div>
  );
}
