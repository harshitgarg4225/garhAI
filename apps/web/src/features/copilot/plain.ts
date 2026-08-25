/**
 * plain.ts — pure mapping from typed ops to what a person reads.
 *
 * Three jobs, no React, no stores (so all of it is vitest-provable):
 *
 *  1. `toDiffOps` — wire ops + the route's `plainLanguage[]` → the shared
 *     DiffPreview's `DiffOpVM[]`. The server's sentence wins when it exists
 *     (it knows room names); `describeOp` is the honest local fallback.
 *  2. `describeOp` — a §15-tone sentence for any §4 op. No jargon, no ids:
 *     "Widen a door to 900 mm", never "opening.resize {openingId…}".
 *  3. `clarificationChips` — quick replies mined from an either/or question.
 *
 * elementIds are extracted here too, because the diff preview highlights the
 * touched elements and the after-canvas needs to know what changed. The rule
 * is mechanical — `payload.id` plus every `*Id` string field — so a new op
 * type gets sensible highlighting with zero code.
 */

import { formatLength, roomDisplayName, ROOM_TYPE_LABELS } from '@garh/model';
import type { ProjectDoc } from '@garh/model';

import type { DiffOpKind, DiffOpVM } from '../../components/types';
import type { CopilotWireOp } from './types';

// ---------------------------------------------------------------------------
// Kind — drives the icon and tint of each row
// ---------------------------------------------------------------------------

/** Verb → DiffPreview row kind. Unknown verbs read as a generic edit. */
export function opKind(opType: string, payload?: Readonly<Record<string, unknown>>): DiffOpKind {
  const verb = opType.slice(opType.indexOf('.') + 1);
  // Composite ops (`column.set`, `furniture.set`, …) carry the real verb in
  // the payload's `action` field.
  const action = typeof payload?.['action'] === 'string' ? (payload['action'] as string) : null;
  const effective = verb === 'set' && action !== null ? action : verb;

  if (effective === 'add' || effective === 'place' || effective === 'split') return 'add';
  if (effective === 'move' || effective === 'transform' || effective === 'flip') return 'move';
  if (effective === 'resize' || effective === 'set_height' || effective === 'set_thickness') {
    return 'resize';
  }
  if (effective === 'delete' || effective === 'remove') return 'remove';
  if (effective === 'assign' || effective === 'apply_kit' || effective === 'apply_option') {
    return 'assign';
  }
  return 'edit';
}

// ---------------------------------------------------------------------------
// Element ids — what the row highlights on the canvas
// ---------------------------------------------------------------------------

/** `payload.id` + every string field ending in `Id`, except grouping meta. */
export function opElementIds(op: CopilotWireOp): string[] {
  const ids: string[] = [];
  for (const [key, value] of Object.entries(op.payload)) {
    if (typeof value !== 'string' || value === '') continue;
    if (key === 'id' || (key.endsWith('Id') && key !== 'clientOpId' && key !== 'groupId')) {
      if (!ids.includes(value)) ids.push(value);
    }
  }
  return ids;
}

// ---------------------------------------------------------------------------
// Fallback sentences
// ---------------------------------------------------------------------------

const NOUN: Readonly<Record<string, string>> = {
  wall: 'wall',
  opening: 'opening',
  room: 'room',
  storey: 'floor',
  stair: 'staircase',
  balcony: 'balcony',
  column: 'column',
  furniture: 'furniture',
  plot: 'plot',
  brief: 'brief',
  facade: 'facade',
  material: 'finish',
  levels: 'levels',
  annotation: 'note',
  solver: 'layout',
};

function mm(value: unknown, doc: ProjectDoc | null): string | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  // "mm in, pretty out" (golden rule 6): format per the project's own units
  // when we have the document; raw integer mm — always exact — when we don't.
  const display = doc?.house.meta.unitsDisplay;
  return display === undefined ? `${value} mm` : formatLength(value, display);
}

function str(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null;
}

/** "door" / "window" / "vent" for an opening payload, looked up when moving. */
function openingNoun(op: CopilotWireOp, doc: ProjectDoc | null): string {
  const kind = str(op.payload['kind']);
  if (kind !== null) return kind;
  const id = str(op.payload['openingId']);
  if (doc !== null && id !== null) {
    const found = doc.house.openings.find((o) => o.id === id);
    if (found !== undefined) return found.kind;
  }
  return 'opening';
}

function roomLabel(op: CopilotWireOp, doc: ProjectDoc | null): string {
  const id = str(op.payload['roomId']);
  if (doc !== null && id !== null) {
    const room = doc.house.rooms.find((r) => r.id === id);
    if (room !== undefined) return roomDisplayName(room);
  }
  return 'a room';
}

/**
 * A readable sentence for one op, without the server's help.
 *
 * Not exhaustive per payload field — the aim is "an architect skims the list
 * and knows what will happen", not a round-trippable serialisation. The
 * server's `plainLanguage` line, when present, replaces this entirely.
 */
export function describeOp(op: CopilotWireOp, doc: ProjectDoc | null = null): string {
  const p = op.payload;
  switch (op.type) {
    case 'wall.add': {
      const t = mm(p['thicknessMm'], doc);
      return t === null ? 'Add a wall' : `Add a ${t} thick wall`;
    }
    case 'wall.move':
      return 'Move a wall';
    case 'wall.split':
      return 'Split a wall in two';
    case 'wall.delete':
      return 'Remove a wall';
    case 'wall.set_thickness': {
      const t = mm(p['thicknessMm'], doc);
      return t === null ? 'Change a wall thickness' : `Make a wall ${t} thick`;
    }
    case 'opening.add': {
      const w = mm(p['widthMm'], doc);
      const noun = openingNoun(op, doc);
      return w === null ? `Add a ${noun}` : `Add a ${w} wide ${noun}`;
    }
    case 'opening.move':
      return `Move a ${openingNoun(op, doc)} along its wall`;
    case 'opening.resize': {
      const w = mm(p['widthMm'], doc);
      const noun = openingNoun(op, doc);
      return w === null ? `Resize a ${noun}` : `Make a ${noun} ${w} wide`;
    }
    case 'opening.flip':
      return `Flip which way a ${openingNoun(op, doc)} opens`;
    case 'opening.delete':
      return `Remove a ${openingNoun(op, doc)}`;
    case 'room.assign': {
      const type = str(p['type']);
      const label =
        type !== null && type in ROOM_TYPE_LABELS
          ? ROOM_TYPE_LABELS[type as keyof typeof ROOM_TYPE_LABELS]
          : (str(p['name']) ?? 'a new use');
      return `Make ${roomLabel(op, doc)} a ${label.toLowerCase()}`;
    }
    case 'room.set_target':
      return `Set a size target for ${roomLabel(op, doc)}`;
    case 'storey.add': {
      const name = str(p['name']);
      return name === null ? 'Add a floor' : `Add ${name}`;
    }
    case 'storey.remove':
      return 'Remove a floor';
    case 'storey.set_height': {
      const h = mm(p['heightMm'], doc);
      return h === null ? 'Change a floor height' : `Make a floor ${h} tall`;
    }
    case 'brief.update':
      return str(p['vastuMode']) !== null
        ? `Set Vastu guidance to ${str(p['vastuMode'])}`
        : 'Update the brief';
    case 'stair.add':
      return 'Add a staircase';
    case 'stair.edit':
      return 'Adjust the staircase';
    case 'stair.delete':
      return 'Remove the staircase';
    case 'facade.apply_kit':
      return 'Apply a facade style';
    case 'facade.edit_component':
      return 'Adjust a facade element';
    case 'material.assign':
      return 'Change a finish';
    case 'levels.set':
      return 'Adjust plinth and parapet levels';
    default: {
      // `column.set` / `furniture.set` / `balcony.set` / `annotation.set` and
      // anything the taxonomy grows later.
      const family = op.type.slice(0, op.type.indexOf('.'));
      const noun = NOUN[family] ?? 'the design';
      const kind = opKind(op.type, op.payload);
      const verb =
        kind === 'add'
          ? 'Add'
          : kind === 'move'
            ? 'Move'
            : kind === 'remove'
              ? 'Remove'
              : kind === 'resize'
                ? 'Resize'
                : 'Update';
      return kind === 'add' ? `${verb} ${indefinite(noun)}` : `${verb} the ${noun}`;
    }
  }
}

function indefinite(noun: string): string {
  return /^[aeiou]/.test(noun) ? `an ${noun}` : `a ${noun}`;
}

// ---------------------------------------------------------------------------
// The DiffPreview rows
// ---------------------------------------------------------------------------

/**
 * Wire ops + server sentences → DiffPreview rows, index-aligned.
 *
 * `doc` (the CURRENT document, pre-fold) improves the fallback text — it can
 * name the room a `room.assign` touches. Pass `null` and everything still
 * works, just more generically.
 */
export function toDiffOps(
  ops: readonly CopilotWireOp[],
  plainLanguage: readonly string[],
  doc: ProjectDoc | null = null,
): DiffOpVM[] {
  return ops.map((op, index) => {
    const server = plainLanguage[index];
    return {
      id: `op-${index}`,
      opType: op.type,
      kind: opKind(op.type, op.payload),
      text: server !== undefined && server !== '' ? server : describeOp(op, doc),
      elementIds: opElementIds(op),
    };
  });
}

// ---------------------------------------------------------------------------
// Quick-reply chips for a clarification
// ---------------------------------------------------------------------------

/** Words that end an option phrase when scanning backwards from "or". */
const CHIP_MAX_WORDS = 4;
const CHIP_MAX = 4;

/**
 * Mine "A or B" alternatives out of a clarifying question.
 *
 * Best-effort by design: chips are an accelerator, not the only path — the
 * question is always shown and the input always works. Returns `[]` rather
 * than guessing when the question has no readable alternatives.
 *
 *   "…take the space from the passage or the adjoining room?"
 *      → ["the passage", "the adjoining room"]
 */
export function clarificationChips(question: string): string[] {
  const chips: string[] = [];

  // Work clause by clause so "Which bedroom…, and should I take…?" only
  // yields options from the clause that actually contains an "or".
  for (const clause of question.split(/[,;?]/)) {
    if (!/\bor\b/i.test(clause)) continue;

    const parts = clause.split(/\bor\b/i);
    if (parts.length < 2) continue;

    // Left side of the FIRST "or": take the trailing noun phrase.
    const left = lastPhrase(parts[0] ?? '');
    if (left !== null) pushChip(chips, left);

    // Every right side is already a phrase ("the adjoining room").
    for (const part of parts.slice(1)) {
      const right = firstPhrase(part);
      if (right !== null) pushChip(chips, right);
    }
  }

  return chips.slice(0, CHIP_MAX);
}

function pushChip(chips: string[], chip: string): void {
  const clean = chip.trim();
  if (clean === '' || chips.includes(clean)) return;
  chips.push(clean);
}

/** Trailing ≤N-word phrase, cut at "the/a/an" when one is in range. */
function lastPhrase(text: string): string | null {
  const words = text.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return null;
  const tail = words.slice(-CHIP_MAX_WORDS);
  const article = tail.findIndex((w) => /^(the|a|an)$/i.test(w));
  const phrase = (article >= 0 ? tail.slice(article) : tail.slice(-2)).join(' ');
  return phrase.length > 1 ? phrase : null;
}

/** Leading ≤N-word phrase of the text after an "or". */
function firstPhrase(text: string): string | null {
  const words = text.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return null;
  const phrase = words.slice(0, CHIP_MAX_WORDS).join(' ').replace(/[.!?]+$/, '');
  return phrase.length > 1 ? phrase : null;
}
