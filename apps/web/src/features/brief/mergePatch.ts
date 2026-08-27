/**
 * mergePatch.ts — merge-patch construction for `brief.update` (op 5).
 *
 * `brief.update` is defined (ops.ts) as "an RFC 7386 JSON merge patch on
 * `brief.data`". The APPLY half already lives in the model core —
 * `applyMergePatch` in `@garh/model` is what `fold` runs — so this module
 * deliberately imports it rather than shipping a second implementation that
 * could drift. What is added here is the CONSTRUCTION half the client needs:
 * building patches, predicting the merged result to stamp completeness, and
 * translating the parser's dotted assumption paths into edits.
 *
 * RFC 7386 in three lines (all three are load-bearing for the brief):
 *   - objects merge recursively, key by key;
 *   - `null` DELETES a key (so "clear the budget" is `{budgetInr: null}`);
 *   - anything else — including ARRAYS — replaces wholesale. The rooms list is
 *     an array precisely so an edit replaces it atomically instead of merging
 *     two half-lists into nonsense.
 */

import {
  applyMergePatch,
  type BriefDoc,
  type BriefUpdateOp,
  type JsonObject,
  type JsonValue,
  type VastuMode,
} from '@garh/model';

import { computeCompleteness } from './completeness';
import { normaliseRooms, readBriefData, setRoomCount, type RoomRequest } from './types';

/** Re-exported so feature code and tests use the fold's own implementation. */
export { applyMergePatch };

function isPlainObject(v: JsonValue | undefined): v is JsonObject {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

// ---------------------------------------------------------------------------
// Patch construction
// ---------------------------------------------------------------------------

/**
 * The smallest patch that turns `from` into `to` (both objects). NOTE the
 * deletion semantics: keys of `from` absent from `to` become `null` (RFC 7386
 * deletes). That is right for "make the brief exactly this" flows and WRONG
 * for merging a partial parse result — the free-text screen wants
 * {@link pruneUnchanged} instead.
 */
export function diffMergePatch(from: JsonObject, to: JsonObject): JsonObject {
  const patch: JsonObject = {};
  for (const [key, next] of Object.entries(to)) {
    const prev = from[key];
    if (isPlainObject(prev) && isPlainObject(next)) {
      const inner = diffMergePatch(prev, next);
      if (Object.keys(inner).length > 0) patch[key] = inner;
      continue;
    }
    if (!deepEqual(prev, next)) patch[key] = next;
  }
  for (const key of Object.keys(from)) {
    if (!(key in to)) patch[key] = null; // RFC 7386: null deletes
  }
  return patch;
}

/**
 * Drop top-level keys of `patch` whose value the brief already holds. A parse
 * result is PARTIAL — keys it does not mention must survive — so this never
 * emits deletions; it only keeps the op log free of no-op keys.
 */
export function pruneUnchanged(patch: JsonObject, current: JsonObject): JsonObject {
  const out: JsonObject = {};
  for (const [key, value] of Object.entries(patch)) {
    if (!deepEqual(current[key], value)) out[key] = value;
  }
  return out;
}

function deepEqual(a: JsonValue | undefined, b: JsonValue | undefined): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((v, i) => deepEqual(v, b[i]));
  }
  if (isPlainObject(a) && isPlainObject(b)) {
    const ka = Object.keys(a);
    const kb = Object.keys(b);
    return ka.length === kb.length && ka.every((k) => deepEqual(a[k], b[k]));
  }
  return false;
}

// ---------------------------------------------------------------------------
// brief.update construction
// ---------------------------------------------------------------------------

export interface BriefUpdateOptions {
  /** Set alongside the patch when the Vastu mode itself changes. */
  readonly vastuMode?: VastuMode | undefined;
}

/**
 * Build the `brief.update` op for a patch against the CURRENT brief.
 *
 * The completeness stamped on the payload is computed on the MERGED data —
 * i.e. what the brief will contain after this op folds (predicted with the
 * fold's own `applyMergePatch`) — so the op is self-consistent under replay
 * and the dashboard chip moves with the edit, not one edit behind it.
 */
export function briefUpdateOp(
  brief: BriefDoc,
  patch: JsonObject,
  options: BriefUpdateOptions = {},
): BriefUpdateOp {
  const nextData = applyMergePatch(brief.data, patch);
  const completeness = computeCompleteness(nextData).score;
  return {
    type: 'brief.update',
    payload: {
      patch,
      completeness,
      ...(options.vastuMode === undefined ? {} : { vastuMode: options.vastuMode }),
    },
  };
}

// ---------------------------------------------------------------------------
// Assumption-chip edits — dotted field paths → data edits
// ---------------------------------------------------------------------------

/**
 * Assumption fields arrive as dotted paths (`brief.storeys`,
 * `brief.rooms.bath_wc.count` — see `services/llm/schemas.py`). This applies
 * one such edit to a pending parse-result data object and returns the new
 * object, or `null` when the path is not one we know how to edit (the chip
 * then renders read-only rather than pretending the edit stuck).
 */
export function setBriefField(
  data: JsonObject,
  field: string,
  value: JsonValue,
): JsonObject | null {
  const path = field.startsWith('brief.') ? field.slice('brief.'.length) : field;
  const parts = path.split('.');

  // brief.rooms.<type>.count — the one nested path the parser emits.
  if (parts.length === 3 && parts[0] === 'rooms' && parts[2] === 'count') {
    const type = parts[1];
    if (
      typeof value !== 'number' ||
      !Number.isSafeInteger(value) ||
      value < 0 ||
      type === undefined
    ) {
      return null;
    }
    const rooms = readBriefData(data).rooms ?? [];
    return { ...data, rooms: setRoomCount(rooms, type, value) as unknown as JsonValue };
  }

  // brief.<scalar> — everything else the parser tracks is a top-level key.
  if (parts.length === 1 && parts[0] !== undefined && parts[0] !== '') {
    if (value === null) {
      const next = { ...data };
      delete next[parts[0]];
      return next;
    }
    return { ...data, [parts[0]]: value };
  }

  return null;
}

/**
 * Canonicalise a parse result's data before it becomes a patch: rooms get
 * normalised (per-bedroom entries, grouped others), unknown values pass
 * through untouched. Keeps a parser fixture and a form edit converging on the
 * same stored shape.
 */
export function canonicaliseParsedData(data: JsonObject): JsonObject {
  const rooms = readBriefData(data).rooms;
  if (rooms === undefined) return data;
  return { ...data, rooms: normaliseRooms(rooms) as unknown as JsonValue };
}

export type { RoomRequest };
