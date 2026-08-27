/**
 * numericEntry.ts — "typing a number overrides the mouse" (§12), as a pure
 * state machine.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHAT IT IS FOR
 * ────────────────────────────────────────────────────────────────────────────
 * Every tool in this directory has at least one number the pointer is
 * approximating: the wall tool's length, the door tool's offset from the wall
 * start, the furniture tool's rotation. §12 requires that typing that number
 * while drawing replaces the pointer's guess exactly. This module owns the
 * text buffer, the field cycling (Tab), the parse, and the inline echo — so
 * seven tools do not grow seven subtly different versions of it.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE KEYBOARD COLLISION, AND HOW IT IS RESOLVED
 * ────────────────────────────────────────────────────────────────────────────
 * The §12 keyboard map spends `1`, `2`, `3` on storey switching and single
 * letters on tools — and `3.8m` is a length made entirely of those keys. The
 * rule, implemented by {@link wantsKey} and enforced by the capture-phase
 * listener in `useToolController`:
 *
 *   - **Digits and length punctuation** (`0-9 . - / ' "`) go to the tool
 *     whenever it is mid-draw. `3` while drawing a wall starts a length;
 *     `3` while idle is still "go to the second floor".
 *   - **Unit letters** (`m`, `ft`, `in`, `cm`, `yd` and the words they spell)
 *     go to the tool only once a buffer exists. `m` on an empty buffer is
 *     still the measure tool; `m` after `3.8` is metres.
 *   - **Backspace** edits the buffer while one exists, and is a tool verb
 *     (drop the last chain segment / vertex) when it does not.
 *
 * Nothing else is claimed. `Esc`, `Enter` and `Tab` are handled by the tool
 * itself, which asks this module first: Esc with a buffer clears the buffer
 * rather than cancelling the drawing, which is what a CAD user expects and
 * what stops a mistyped number from throwing away a ten-segment chain.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * PARSING
 * ────────────────────────────────────────────────────────────────────────────
 * Length fields parse through `parseLengthMm(buffer, 'mm')` — the golden-tested
 * boundary in `packages/model/src/units.ts`, which agrees pair-for-pair with
 * the Python mirror. A bare number is therefore MILLIMETRES, not feet, even in
 * a ft-in project: `3600` means 3600 mm and `12'` means twelve feet. That is
 * §12's own example ("typing `3600` or `12'`"), and the echo shows both forms
 * so there is never a doubt about which was understood.
 */

import { formatIndianNumber, formatLength, roundMm, tryParseLengthMm } from '../../../lib/units';
import type { NumericEntryView, ToolKeyInput } from './types';
import type { UnitsDisplay } from '@garh/model';

// ---------------------------------------------------------------------------
// Fields
// ---------------------------------------------------------------------------

/** What a numeric field measures. Drives the parser and the echo. */
export type NumericUnit = 'mm' | 'deg' | 'count';

export interface NumericField {
  readonly id: string;
  /** Sentence-case label shown inline: "Length", "From wall start". */
  readonly label: string;
  readonly unit: NumericUnit;
  readonly minMm?: number | undefined;
  readonly maxMm?: number | undefined;
}

/**
 * The buffer. Immutable — every key returns a new state, which is what makes
 * the specs read as a table of transitions rather than a sequence of mutations.
 */
export interface NumericEntryState {
  readonly fields: readonly NumericField[];
  readonly index: number;
  readonly buffer: string;
}

export function createEntry(fields: readonly NumericField[]): NumericEntryState {
  return { fields, index: 0, buffer: '' };
}

/** True when the user has typed something that is waiting to be applied. */
export function isEntryActive(state: NumericEntryState): boolean {
  return state.buffer !== '';
}

/** The field the buffer currently applies to, or null when there are none. */
export function activeField(state: NumericEntryState): NumericField | null {
  return state.fields[state.index] ?? null;
}

// ---------------------------------------------------------------------------
// Key classification
// ---------------------------------------------------------------------------

/** Keys that may START a numeric entry. */
const START_KEYS = /^[0-9.'"/-]$/;

/**
 * Keys that may only CONTINUE one. Every letter that appears in a unit word
 * this parser accepts: mm, cm, m, mt, mtr, metre(s), meter(s), ft, foot,
 * feet, in, inch(es), yd, yard(s).
 */
const CONTINUE_KEYS = /^[mcteforinhsyda ]$/;

/**
 * Does this key belong to the numeric entry rather than to the keyboard map?
 * See the collision rules in the module header.
 */
export function wantsKey(state: NumericEntryState, event: ToolKeyInput): boolean {
  if (event.ctrlKey || event.metaKey || event.altKey) return false;
  if (state.fields.length === 0) return false;
  const key = event.key;
  if (key === 'Backspace') return isEntryActive(state);
  if (key === 'Tab') return state.fields.length > 1;
  if (key.length !== 1) return false;
  if (START_KEYS.test(key)) return true;
  return isEntryActive(state) && CONTINUE_KEYS.test(key.toLowerCase());
}

// ---------------------------------------------------------------------------
// Transitions
// ---------------------------------------------------------------------------

export type EntryAction =
  /** The key was not ours. */
  | 'ignored'
  /** The buffer changed. */
  | 'typed'
  /** The buffer was emptied (Backspace on the last character, or Esc). */
  | 'cleared'
  /** Tab moved to another field. */
  | 'field';

export interface EntryStep {
  readonly state: NumericEntryState;
  readonly action: EntryAction;
}

/** Feed one key. Never throws; an unparseable buffer is a display problem. */
export function feedKey(state: NumericEntryState, event: ToolKeyInput): EntryStep {
  if (!wantsKey(state, event)) return { state, action: 'ignored' };
  const key = event.key;

  if (key === 'Backspace') {
    const buffer = state.buffer.slice(0, -1);
    return { state: { ...state, buffer }, action: buffer === '' ? 'cleared' : 'typed' };
  }

  if (key === 'Tab') {
    // Shift-Tab walks backwards, like every other field cycle in the app.
    const step = event.shiftKey ? -1 : 1;
    const count = state.fields.length;
    const index = (((state.index + step) % count) + count) % count;
    // The buffer belongs to the field it was typed into, so switching clears it.
    return { state: { ...state, index, buffer: '' }, action: 'field' };
  }

  return { state: { ...state, buffer: state.buffer + key }, action: 'typed' };
}

/** Esc while typing: drop the buffer, keep the drawing. Returns null if empty. */
export function clearEntry(state: NumericEntryState): NumericEntryState | null {
  if (!isEntryActive(state)) return null;
  return { ...state, buffer: '' };
}

/** Start over on the same field (after the tool has applied a value). */
export function resetBuffer(state: NumericEntryState): NumericEntryState {
  return state.buffer === '' ? state : { ...state, buffer: '' };
}

// ---------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------

export interface ParsedEntry {
  /** Value in the field's unit — integer mm, integer degrees, or a count. */
  readonly value: number;
  readonly field: NumericField;
}

/**
 * Parse the buffer for the active field. Returns null when it is empty or does
 * not parse yet — "12'" is complete, "12'6" is complete, "1/" is not, and none
 * of those is an error while the user is still typing.
 */
export function parseEntry(state: NumericEntryState): ParsedEntry | null {
  const field = activeField(state);
  if (field === null || state.buffer === '') return null;

  if (field.unit === 'mm') {
    const parsed = tryParseLengthMm(state.buffer, 'mm');
    if (!parsed.ok) return null;
    return { value: parsed.mm, field };
  }

  // Degrees and counts: a plain (possibly signed, possibly decimal) number.
  // `parseLengthMm` would happily read `12'` as feet here, which is nonsense
  // for an angle, so this path is deliberately narrower.
  if (!/^-?\d+(?:\.\d+)?$/.test(state.buffer.trim())) return null;
  return { value: roundMm(Number(state.buffer.trim())), field };
}

/** The parse error to show under the field, or null while it is fine. */
export function entryError(state: NumericEntryState): string | null {
  const field = activeField(state);
  if (field === null || state.buffer === '') return null;

  const parsed = parseEntry(state);
  if (parsed === null) {
    // Trailing separators are mid-typing, not mistakes.
    if (/[.'"/\s-]$/.test(state.buffer)) return null;
    return 'Try 3600, 3.6m or 12\'-6".';
  }
  if (field.minMm !== undefined && parsed.value < field.minMm) {
    return `${field.label} must be at least ${formatIndianNumber(field.minMm)} mm.`;
  }
  if (field.maxMm !== undefined && parsed.value > field.maxMm) {
    return `${field.label} can be at most ${formatIndianNumber(field.maxMm)} mm.`;
  }
  return null;
}

/** True when the parsed value is inside the field's bounds. */
export function isEntryApplicable(state: NumericEntryState): boolean {
  const parsed = parseEntry(state);
  if (parsed === null) return false;
  const { value, field } = parsed;
  if (field.minMm !== undefined && value < field.minMm) return false;
  if (field.maxMm !== undefined && value > field.maxMm) return false;
  return true;
}

/**
 * The applicable value for `fieldId`, or null.
 *
 * Tools call this rather than reading the buffer: "does the user's typed
 * length apply to the segment I am drawing?" is one question with one answer,
 * and it must be `null` (not 0, not NaN) while the answer is no.
 */
export function entryValueFor(state: NumericEntryState, fieldId: string): number | null {
  const field = activeField(state);
  if (field === null || field.id !== fieldId) return null;
  if (!isEntryApplicable(state)) return null;
  return parseEntry(state)?.value ?? null;
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------

/** `3,600 mm · 11'-10"` — both forms, so the parse is never a guess. */
export function formatEcho(value: number, unit: NumericUnit, display: UnitsDisplay): string {
  if (unit === 'deg') return `${String(value)}°`;
  if (unit === 'count') return String(value);
  return `${formatIndianNumber(value)} mm · ${formatLength(value, display)}`;
}

/** The inline entry chip the HUD renders, or null when nothing is being typed. */
export function entryView(
  state: NumericEntryState,
  display: UnitsDisplay,
): NumericEntryView | null {
  const field = activeField(state);
  if (field === null) return null;
  if (state.buffer === '') return null;
  const parsed = parseEntry(state);
  return {
    fieldId: field.id,
    label: field.label,
    buffer: state.buffer,
    value: parsed?.value ?? null,
    echo: parsed === null ? '' : formatEcho(parsed.value, field.unit, display),
    error: entryError(state),
    fields: state.fields.map((f) => ({ id: f.id, label: f.label })),
  };
}
