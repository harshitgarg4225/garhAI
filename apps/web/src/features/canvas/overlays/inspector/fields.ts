/**
 * fields.ts — the current selection becomes editable fields and the ops that
 * commit them. PURE: no React, no store, no three.
 *
 * §12: "right inspector (selection properties, all editable, mm/ft-in aware
 * inputs)". The panel is a thin renderer over this; every judgement about what
 * is editable, what a multi-select shares, and which op an edit becomes is
 * made here, where it can be tested without a DOM.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHAT IS DELIBERATELY READ-ONLY, AND WHY
 * ────────────────────────────────────────────────────────────────────────────
 * A field is editable only when an op in the §4 taxonomy can express the edit.
 * The taxonomy is frozen at M1 and the inspector does not get to grow it, so:
 *
 *   · **wall kind** (external / internal / parapet) — no op. `wall.add` sets it
 *     and nothing changes it. Shown, not editable.
 *   · **wall loadBearing** — same. It is a coordination hint carried by
 *     `wall.add`.
 *   · **opening kind and tag** — `opening.*` covers position, size, sill and
 *     swing. The tag is assigned by the schedule generator (§7).
 *   · **room area** — DERIVED from the walls by planar subdivision. What is
 *     editable is the room's TARGET area (op 20), which is a different number
 *     and is labelled as one.
 *
 * Rendering those as disabled inputs rather than hiding them is the honest
 * choice: "you cannot change this here" is information, and a property that
 * silently vanishes reads as a missing feature.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * MULTI-SELECT
 * ────────────────────────────────────────────────────────────────────────────
 * Fields common to every selected element of the same type are shown once. A
 * field whose value differs carries `mixed: true` and renders empty with a
 * "Mixed" placeholder; committing it writes the same value to all of them, as
 * ONE op group — so five walls thickened together are one undo.
 */

import {
  DIRECTIONS_8,
  OPENING_SWINGS,
  ROOM_TYPES,
  ROOM_TYPE_LABELS,
  STAIR_KINDS,
  DIRECTIONS_4,
  RAILING_KINDS,
  idType,
  ptRound,
  type Direction4,
  type Direction8,
  type ElementType,
  type HouseModel,
  type Op,
  type OpeningSwing,
  type RailingKind,
  type RoomType,
  type StairKind,
  type UnitsDisplay,
} from '@garh/model';

import { formatIndianNumber, formatLength } from '../../../../lib/units';
import { roomAreaText } from '../format';

// ---------------------------------------------------------------------------
// Field model
// ---------------------------------------------------------------------------

export type InspectorFieldKind =
  /** Integer mm. Rendered with `LengthInput` — parses 12'6", 3.8m, 3800. */
  | 'length'
  /** Integer mm². Rendered with an area input. */
  | 'area'
  /** A plain integer count (risers, degrees). */
  | 'count'
  | 'text'
  | 'enum'
  | 'toggle'
  /** Displayed, never editable. Carries a reason. */
  | 'readonly';

export interface EnumOption {
  readonly value: string;
  readonly label: string;
}

export interface InspectorField {
  readonly key: string;
  readonly label: string;
  readonly kind: InspectorFieldKind;
  /** mm for `length`, mm² for `area`, number for `count`, else string/boolean. */
  readonly value: number | string | boolean | null;
  /** Preformatted for `readonly` rows and for the hint line. */
  readonly displayText: string;
  /** True when the selection disagrees about this value. */
  readonly mixed: boolean;
  readonly editable: boolean;
  /** Why it is not editable — shown as the hint on a disabled row. */
  readonly reason?: string | undefined;
  readonly hint?: string | undefined;
  readonly minMm?: number | undefined;
  readonly maxMm?: number | undefined;
  readonly options?: readonly EnumOption[] | undefined;
  /**
   * The ops that commit a new value, across the whole selection. Empty when the
   * value is unchanged, which the panel treats as "nothing to do" rather than
   * dispatching a no-op group.
   */
  readonly build: (next: number | string | boolean) => Op[];
  /** Undo-toast copy (§15). Sentence case, no trailing period. */
  readonly undoLabel: string;
}

export interface InspectorAction {
  readonly key: string;
  readonly label: string;
  readonly tone: 'default' | 'danger';
  readonly ops: readonly Op[];
  readonly undoLabel: string;
}

export interface InspectorSelection {
  /** The single element type selected, `'mixed'`, or `'none'`. */
  readonly kind: ElementType | 'mixed' | 'none';
  readonly count: number;
  /** "Wall", "3 walls", "Master Bedroom", "Nothing selected". */
  readonly title: string;
  readonly subtitle: string | null;
  readonly fields: readonly InspectorField[];
  readonly actions: readonly InspectorAction[];
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function noBuild(): Op[] {
  return [];
}

/** Every selected value of one property, reduced to "shared" or "mixed". */
function shared<T>(values: readonly T[]): { value: T | null; mixed: boolean } {
  const first = values[0];
  if (first === undefined) return { value: null, mixed: false };
  for (const v of values) {
    if (v !== first) return { value: first, mixed: true };
  }
  return { value: first, mixed: false };
}

function readonlyField(
  key: string,
  label: string,
  displayText: string,
  reason: string,
): InspectorField {
  return {
    key,
    label,
    kind: 'readonly',
    value: displayText,
    displayText,
    mixed: false,
    editable: false,
    reason,
    build: noBuild,
    undoLabel: '',
  };
}

function enumOptions(values: readonly string[], labels?: Readonly<Record<string, string>>): EnumOption[] {
  return values.map((value) => ({ value, label: labels?.[value] ?? value }));
}

/** Title case for a small enum value: `in-left` → `In-left`. */
function humanEnum(value: string): string {
  const head = value.charAt(0);
  return head === '' ? value : head.toUpperCase() + value.slice(1);
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export interface InspectorOptions {
  readonly display: UnitsDisplay;
}

/**
 * Build the inspector for the current selection.
 *
 * Ids are resolved against the document on every call — the selection store
 * holds ids precisely so it survives the document being replaced wholesale on
 * each op, and resolving late is what makes the panel show the value that is
 * true right now rather than the one that was true when it was clicked.
 */
export function inspectorSelection(
  house: HouseModel,
  ids: readonly string[],
  options: InspectorOptions,
): InspectorSelection {
  if (ids.length === 0) {
    return { kind: 'none', count: 0, title: 'Nothing selected', subtitle: null, fields: [], actions: [] };
  }

  const types = new Set(ids.map((id) => idType(id)).filter((t): t is ElementType => t !== null));
  if (types.size !== 1) {
    return {
      kind: 'mixed',
      count: ids.length,
      title: `${String(ids.length)} items selected`,
      subtitle: 'Select one kind of thing to edit its properties.',
      fields: [],
      actions: [],
    };
  }

  const type = Array.from(types)[0];
  if (type === undefined) {
    return { kind: 'none', count: 0, title: 'Nothing selected', subtitle: null, fields: [], actions: [] };
  }

  switch (type) {
    case 'wall':
      return wallInspector(house, ids, options);
    case 'opening':
      return openingInspector(house, ids, options);
    case 'room':
      return roomInspector(house, ids, options);
    case 'stair':
      return stairInspector(house, ids);
    case 'furniture':
      return furnitureInspector(house, ids);
    case 'balcony':
      return balconyInspector(house, ids);
    case 'storey':
      return storeyInspector(house, ids);
    default:
      return {
        kind: type,
        count: ids.length,
        title: `${String(ids.length)} ${type}${ids.length === 1 ? '' : 's'}`,
        subtitle: 'This kind of element has no editable properties yet.',
        fields: [],
        actions: [],
      };
  }
}

// ---------------------------------------------------------------------------
// Wall
// ---------------------------------------------------------------------------

/** Common Indian masonry thicknesses. The list a thickness picker offers. */
export const WALL_THICKNESSES_MM: readonly number[] = [115, 150, 200, 230, 300];

function wallInspector(
  house: HouseModel,
  ids: readonly string[],
  options: InspectorOptions,
): InspectorSelection {
  const walls = ids
    .map((id) => house.walls.find((w) => w.id === id))
    .filter((w): w is NonNullable<typeof w> => w !== undefined);
  if (walls.length === 0) return emptySelection('wall');

  const lengths = walls.map((w) => Math.round(Math.hypot(w.b.x - w.a.x, w.b.y - w.a.y)));
  const lengthShared = shared(lengths);
  const thickness = shared(walls.map((w) => w.thicknessMm));
  const kind = shared(walls.map((w) => w.kind));
  const loadBearing = shared(walls.map((w) => w.loadBearing));

  const fields: InspectorField[] = [
    {
      key: 'length',
      label: 'Length',
      kind: 'length',
      value: lengthShared.value,
      displayText: lengthShared.mixed ? 'Mixed' : formatLen(lengthShared.value, options.display),
      mixed: lengthShared.mixed,
      // Only a single wall may be re-lengthened: applying one length to five
      // walls of different orientations moves five `b` endpoints in five
      // directions, which is a bulk edit nobody asked for.
      editable: walls.length === 1,
      reason: walls.length === 1 ? undefined : 'Select one wall to change its length.',
      hint: 'Moves the far end. The near end stays where it is.',
      minMm: 1,
      build: (next) => {
        const wall = walls[0];
        if (wall === undefined || typeof next !== 'number' || next <= 0) return [];
        const dx = wall.b.x - wall.a.x;
        const dy = wall.b.y - wall.a.y;
        const len = Math.hypot(dx, dy);
        if (len === 0) return [];
        // `ptRound` (half away from zero), not `Math.round` (half up): this
        // point is a `wall.move` payload, and a wall drawn westwards must land
        // exactly where the same wall drawn eastwards does. `core/coords.ts`
        // states the rule; `pointAtLengthMm` is the same maths for tools.
        const b = ptRound(wall.a.x + (dx / len) * next, wall.a.y + (dy / len) * next);
        if (b.x === wall.b.x && b.y === wall.b.y) return [];
        return [{ type: 'wall.move', payload: { wallId: wall.id, a: wall.a, b } }];
      },
      undoLabel: 'Wall resized',
    },
    {
      key: 'thickness',
      label: 'Thickness',
      kind: 'length',
      value: thickness.value,
      displayText: thickness.mixed ? 'Mixed' : formatLen(thickness.value, options.display),
      mixed: thickness.mixed,
      editable: true,
      hint: '115 half-brick · 230 full brick. Room areas re-measure.',
      minMm: 50,
      maxMm: 1000,
      build: (next) => {
        if (typeof next !== 'number' || next <= 0) return [];
        return walls
          .filter((w) => w.thicknessMm !== next)
          .map((w) => ({
            type: 'wall.set_thickness' as const,
            payload: { wallId: w.id, thicknessMm: next },
          }));
      },
      undoLabel: walls.length === 1 ? 'Wall thickness changed' : 'Wall thicknesses changed',
    },
    readonlyField(
      'kind',
      'Kind',
      kind.mixed ? 'Mixed' : humanEnum(String(kind.value ?? '')),
      'Set when the wall is drawn. Delete and redraw to change it.',
    ),
    readonlyField(
      'loadBearing',
      'Load bearing',
      loadBearing.mixed ? 'Mixed' : loadBearing.value === true ? 'Yes' : 'No',
      'A structural coordination note, carried from when the wall was drawn.',
    ),
  ];

  const actions: InspectorAction[] = [
    {
      key: 'delete',
      label: walls.length === 1 ? 'Delete wall' : `Delete ${String(walls.length)} walls`,
      tone: 'danger',
      ops: walls.map((w) => ({ type: 'wall.delete' as const, payload: { wallId: w.id } })),
      undoLabel: walls.length === 1 ? 'Wall deleted' : 'Walls deleted',
    },
  ];

  return {
    kind: 'wall',
    count: walls.length,
    title: walls.length === 1 ? 'Wall' : `${String(walls.length)} walls`,
    subtitle:
      walls.length === 1 && !lengthShared.mixed
        ? formatLen(lengthShared.value, options.display)
        : null,
    fields,
    actions,
  };
}

// ---------------------------------------------------------------------------
// Opening
// ---------------------------------------------------------------------------

function openingInspector(
  house: HouseModel,
  ids: readonly string[],
  options: InspectorOptions,
): InspectorSelection {
  const openings = ids
    .map((id) => house.openings.find((o) => o.id === id))
    .filter((o): o is NonNullable<typeof o> => o !== undefined);
  if (openings.length === 0) return emptySelection('opening');

  const width = shared(openings.map((o) => o.widthMm));
  const height = shared(openings.map((o) => o.heightMm));
  const sill = shared(openings.map((o) => o.sillMm));
  const offset = shared(openings.map((o) => o.offsetMm));
  const swing = shared(openings.map((o) => o.swing));
  const kind = shared(openings.map((o) => o.kind));
  const tag = shared(openings.map((o) => o.tag ?? '—'));

  /**
   * `opening.resize` takes width, height and sill as independent optionals, so
   * each field builds a payload carrying only its own key. Written out rather
   * than computed (`{ [field]: next }`) because a computed key widens the
   * payload to `Record<string, number>` and loses the very type checking that
   * stops a sill being written into a width.
   */
  const resize =
    (field: 'widthMm' | 'heightMm' | 'sillMm') =>
    (next: number | string | boolean): Op[] => {
      if (typeof next !== 'number' || next < 0) return [];
      return openings
        .filter((o) => o[field] !== next)
        .map((o): Op => {
          if (field === 'widthMm') {
            return { type: 'opening.resize', payload: { openingId: o.id, widthMm: next } };
          }
          if (field === 'heightMm') {
            return { type: 'opening.resize', payload: { openingId: o.id, heightMm: next } };
          }
          return { type: 'opening.resize', payload: { openingId: o.id, sillMm: next } };
        });
    };

  const fields: InspectorField[] = [
    {
      key: 'width',
      label: 'Width',
      kind: 'length',
      value: width.value,
      displayText: width.mixed ? 'Mixed' : formatLen(width.value, options.display),
      mixed: width.mixed,
      editable: true,
      // NBC door minimums, seeded in `nbc-core`: main 900, internal 800, bath
      // 750. The inspector does not enforce them — the rules engine reports
      // them as chips (golden rule 5) — so the bound here is only physical.
      minMm: 100,
      hint: 'Grows about the centre. NBC: main door 900, internal 800, bath 750.',
      build: resize('widthMm'),
      undoLabel: 'Opening resized',
    },
    {
      key: 'height',
      label: 'Height',
      kind: 'length',
      value: height.value,
      displayText: height.mixed ? 'Mixed' : formatLen(height.value, options.display),
      mixed: height.mixed,
      editable: true,
      minMm: 100,
      build: resize('heightMm'),
      undoLabel: 'Opening resized',
    },
    {
      key: 'sill',
      label: 'Sill height',
      kind: 'length',
      value: sill.value,
      displayText: sill.mixed ? 'Mixed' : formatLen(sill.value, options.display),
      mixed: sill.mixed,
      editable: true,
      minMm: 0,
      hint: 'Above this floor. Doors are 0; windows default to 900.',
      build: resize('sillMm'),
      undoLabel: 'Sill changed',
    },
    {
      key: 'offset',
      label: 'Position along wall',
      kind: 'length',
      value: offset.value,
      displayText: offset.mixed ? 'Mixed' : formatLen(offset.value, options.display),
      mixed: offset.mixed,
      editable: true,
      minMm: 0,
      hint: 'To the centre of the opening, from the wall start.',
      build: (next) => {
        if (typeof next !== 'number' || next < 0) return [];
        return openings
          .filter((o) => o.offsetMm !== next)
          .map((o) => ({
            type: 'opening.move' as const,
            payload: { openingId: o.id, offsetMm: next },
          }));
      },
      undoLabel: 'Opening moved',
    },
    {
      key: 'swing',
      label: 'Swing',
      kind: 'enum',
      value: swing.mixed ? null : (swing.value ?? null),
      displayText: swing.mixed ? 'Mixed' : humanEnum(String(swing.value ?? '')),
      mixed: swing.mixed,
      editable: true,
      options: enumOptions(OPENING_SWINGS, {
        'in-left': 'In, left hand',
        'in-right': 'In, right hand',
        'out-left': 'Out, left hand',
        'out-right': 'Out, right hand',
      }),
      build: (next) => {
        if (typeof next !== 'string') return [];
        const value = next as OpeningSwing;
        return openings
          .filter((o) => o.swing !== value)
          .map((o) => ({ type: 'opening.flip' as const, payload: { openingId: o.id, swing: value } }));
      },
      undoLabel: 'Swing flipped',
    },
    readonlyField(
      'kind',
      'Type',
      kind.mixed ? 'Mixed' : humanEnum(String(kind.value ?? '')),
      'Set when the opening is placed. Delete and re-place to change it.',
    ),
    readonlyField(
      'tag',
      'Schedule tag',
      tag.mixed ? 'Mixed' : String(tag.value ?? '—'),
      'Assigned by the door and window schedule when the sheets are generated.',
    ),
  ];

  return {
    kind: 'opening',
    count: openings.length,
    title: openings.length === 1 ? humanEnum(String(kind.value ?? 'Opening')) : `${String(openings.length)} openings`,
    subtitle:
      openings.length === 1 && !width.mixed && !height.mixed
        ? `${formatLen(width.value, options.display)} × ${formatLen(height.value, options.display)}`
        : null,
    fields,
    actions: [
      {
        key: 'delete',
        label: openings.length === 1 ? 'Delete opening' : `Delete ${String(openings.length)} openings`,
        tone: 'danger',
        ops: openings.map((o) => ({ type: 'opening.delete' as const, payload: { openingId: o.id } })),
        undoLabel: 'Opening deleted',
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Room
// ---------------------------------------------------------------------------

function roomInspector(
  house: HouseModel,
  ids: readonly string[],
  options: InspectorOptions,
): InspectorSelection {
  const rooms = ids
    .map((id) => house.rooms.find((r) => r.id === id))
    .filter((r): r is NonNullable<typeof r> => r !== undefined);
  if (rooms.length === 0) return emptySelection('room');

  const type = shared(rooms.map((r) => r.type));
  const name = shared(rooms.map((r) => r.name));
  const area = shared(rooms.map((r) => r.areaMm2));
  const target = shared(rooms.map((r) => r.targetAreaMm2));
  const face = shared(rooms.map((r) => r.mustFace));
  const locked = shared(rooms.map((r) => r.locked));

  /** `room.assign` replaces type, name, tags and lock together. */
  const assign = (patch: { type?: RoomType; name?: string; locked?: boolean }) => (): Op[] =>
    rooms.map((r) => ({
      type: 'room.assign' as const,
      payload: {
        roomId: r.id,
        type: patch.type ?? r.type,
        name: patch.name ?? r.name,
        tags: r.tags,
        locked: patch.locked ?? r.locked,
      },
    }));

  const fields: InspectorField[] = [
    {
      key: 'name',
      label: 'Name',
      kind: 'text',
      value: name.mixed ? null : (name.value ?? ''),
      displayText: name.mixed ? 'Mixed' : (name.value === '' ? '—' : String(name.value)),
      mixed: name.mixed,
      editable: true,
      hint: 'Leave it empty to use the room type as the label.',
      build: (next) => (typeof next === 'string' ? assign({ name: next })() : []),
      undoLabel: 'Room renamed',
    },
    {
      key: 'type',
      label: 'Type',
      kind: 'enum',
      value: type.mixed ? null : (type.value ?? null),
      displayText: type.mixed ? 'Mixed' : ROOM_TYPE_LABELS[type.value ?? 'unassigned'],
      mixed: type.mixed,
      editable: true,
      options: enumOptions(ROOM_TYPES, ROOM_TYPE_LABELS),
      hint: 'Drives the NBC minimums, the furniture set and the Vastu zone.',
      build: (next) => (typeof next === 'string' ? assign({ type: next as RoomType })() : []),
      undoLabel: 'Room type changed',
    },
    readonlyField(
      'area',
      'Clear area',
      area.mixed ? 'Mixed' : roomAreaText(area.value ?? 0, options.display),
      'Measured from the walls around it. Move a wall to change it.',
    ),
    {
      key: 'targetArea',
      label: 'Target area',
      kind: 'area',
      value: target.mixed ? null : target.value,
      displayText: target.mixed
        ? 'Mixed'
        : target.value === null
          ? 'Not set'
          : roomAreaText(target.value, options.display),
      mixed: target.mixed,
      editable: true,
      hint: 'What you want this room to be. The solver aims for it; it does not move walls now.',
      build: (next) => {
        if (typeof next !== 'number' || next <= 0) return [];
        return rooms
          .filter((r) => r.targetAreaMm2 !== next)
          .map((r) => ({
            type: 'room.set_target' as const,
            payload: { roomId: r.id, targetAreaMm2: next },
          }));
      },
      undoLabel: 'Target area set',
    },
    {
      key: 'mustFace',
      label: 'Must face',
      kind: 'enum',
      value: face.mixed ? null : (face.value ?? ''),
      displayText: face.mixed ? 'Mixed' : (face.value ?? 'Any'),
      mixed: face.mixed,
      editable: true,
      options: [{ value: '', label: 'Any' }, ...enumOptions(DIRECTIONS_8)],
      hint: 'A Vastu or brief requirement the solver has to honour.',
      build: (next) => {
        if (typeof next !== 'string') return [];
        const value = next === '' ? null : (next as Direction8);
        return rooms
          .filter((r) => r.mustFace !== value)
          .map((r) => ({ type: 'room.set_target' as const, payload: { roomId: r.id, mustFace: value } }));
      },
      undoLabel: 'Facing set',
    },
    {
      key: 'locked',
      label: 'Lock against re-solve',
      kind: 'toggle',
      value: locked.mixed ? null : (locked.value ?? false),
      displayText: locked.mixed ? 'Mixed' : locked.value === true ? 'Locked' : 'Unlocked',
      mixed: locked.mixed,
      editable: true,
      hint: 'A locked room comes back untouched when you regenerate the plan.',
      build: (next) => (typeof next === 'boolean' ? assign({ locked: next })() : []),
      undoLabel: 'Room lock changed',
    },
  ];

  return {
    kind: 'room',
    count: rooms.length,
    title:
      rooms.length === 1
        ? (name.value !== '' && name.value !== null
            ? String(name.value)
            : ROOM_TYPE_LABELS[type.value ?? 'unassigned'])
        : `${String(rooms.length)} rooms`,
    subtitle: area.mixed ? null : roomAreaText(area.value ?? 0, options.display),
    fields,
    // Rooms are derived; there is no `room.delete` and there should not be.
    // You delete a room by deleting a wall.
    actions: [],
  };
}

// ---------------------------------------------------------------------------
// Stair
// ---------------------------------------------------------------------------

function stairInspector(house: HouseModel, ids: readonly string[]): InspectorSelection {
  const stairs = ids
    .map((id) => house.stairs.find((s) => s.id === id))
    .filter((s): s is NonNullable<typeof s> => s !== undefined);
  if (stairs.length === 0) return emptySelection('stair');

  /**
   * Presentation-only overrides for a stair field.
   *
   * Deliberately NOT `Partial<InspectorField>`: spreading that over a complete
   * field widens every property to include `undefined` (the whole point of
   * `exactOptionalPropertyTypes`), and it would also let a caller silently
   * replace `build`, which is where the op lives.
   */
  interface StairFieldExtras {
    hint?: string | undefined;
    minMm?: number | undefined;
    maxMm?: number | undefined;
    options?: readonly EnumOption[] | undefined;
  }

  const patchField = (
    key: string,
    label: string,
    kind: InspectorFieldKind,
    values: readonly (number | string)[],
    apply: (next: number | string) => Record<string, unknown>,
    extras: StairFieldExtras = {},
  ): InspectorField => {
    const s = shared(values);
    return {
      key,
      label,
      kind,
      value: s.mixed ? null : (s.value ?? null),
      displayText: s.mixed ? 'Mixed' : String(s.value ?? ''),
      mixed: s.mixed,
      editable: true,
      build: (next) => {
        if (typeof next === 'boolean') return [];
        return stairs.map((stair) => ({
          type: 'stair.edit' as const,
          payload: { stairId: stair.id, patch: apply(next) },
        }));
      },
      undoLabel: 'Stair edited',
      ...extras,
    };
  };

  const fields: InspectorField[] = [
    patchField('riser', 'Riser', 'length', stairs.map((s) => s.riserMm), (n) => ({ riserMm: n }), {
      hint: 'NBC allows 190 mm at most.',
      minMm: 100,
      maxMm: 250,
    }),
    patchField('tread', 'Tread', 'length', stairs.map((s) => s.treadMm), (n) => ({ treadMm: n }), {
      hint: 'NBC needs at least 250 mm.',
      minMm: 150,
      maxMm: 450,
    }),
    patchField('width', 'Clear width', 'length', stairs.map((s) => s.widthMm), (n) => ({ widthMm: n }), {
      hint: 'NBC needs at least 900 mm for a dwelling.',
      minMm: 600,
    }),
    patchField(
      'risers',
      'Number of risers',
      'count',
      stairs.map((s) => s.risersCount),
      (n) => ({ risersCount: n }),
      { hint: 'Risers × riser height has to reach the storey height, ±10 mm.' },
    ),
    patchField(
      'kind',
      'Configuration',
      'enum',
      stairs.map((s) => s.kind),
      (n) => ({ kind: n as StairKind }),
      { options: enumOptions(STAIR_KINDS, { straight: 'Straight', dogleg: 'Dog-leg', L: 'L-shaped', U: 'U-shaped' }) },
    ),
    patchField(
      'direction',
      'Going up towards',
      'enum',
      stairs.map((s) => s.direction),
      (n) => ({ direction: n as Direction4 }),
      { options: enumOptions(DIRECTIONS_4, { N: 'North', E: 'East', S: 'South', W: 'West' }) },
    ),
  ];

  return {
    kind: 'stair',
    count: stairs.length,
    title: stairs.length === 1 ? 'Staircase' : `${String(stairs.length)} staircases`,
    subtitle: null,
    fields,
    actions: [
      {
        key: 'delete',
        label: stairs.length === 1 ? 'Delete staircase' : `Delete ${String(stairs.length)} staircases`,
        tone: 'danger',
        ops: stairs.map((s) => ({ type: 'stair.delete' as const, payload: { stairId: s.id } })),
        undoLabel: 'Staircase deleted',
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Furniture
// ---------------------------------------------------------------------------

function furnitureInspector(house: HouseModel, ids: readonly string[]): InspectorSelection {
  const items = ids
    .map((id) => house.furniture.find((f) => f.id === id))
    .filter((f): f is NonNullable<typeof f> => f !== undefined);
  if (items.length === 0) return emptySelection('furniture');

  const rotation = shared(items.map((f) => f.rotationDeg));
  const catalogId = shared(items.map((f) => f.catalogId));

  const fields: InspectorField[] = [
    {
      key: 'rotation',
      label: 'Rotation',
      kind: 'count',
      value: rotation.mixed ? null : (rotation.value ?? 0),
      displayText: rotation.mixed ? 'Mixed' : `${String(rotation.value ?? 0)}°`,
      mixed: rotation.mixed,
      editable: true,
      hint: 'Whole degrees, anticlockwise.',
      build: (next) => {
        if (typeof next !== 'number') return [];
        const deg = ((Math.round(next) % 360) + 360) % 360;
        return items
          .filter((f) => f.rotationDeg !== deg)
          .map((f) => ({
            type: 'furniture.set' as const,
            payload: { action: 'transform' as const, id: f.id, rotationDeg: deg },
          }));
      },
      undoLabel: 'Furniture rotated',
    },
    readonlyField(
      'catalogId',
      'Catalogue item',
      catalogId.mixed ? 'Mixed' : String(catalogId.value ?? ''),
      'Swap it by deleting this one and placing another.',
    ),
  ];

  return {
    kind: 'furniture',
    count: items.length,
    title: items.length === 1 ? 'Furniture' : `${String(items.length)} items`,
    subtitle: catalogId.mixed ? null : String(catalogId.value ?? ''),
    fields,
    actions: [
      {
        key: 'delete',
        label: items.length === 1 ? 'Delete item' : `Delete ${String(items.length)} items`,
        tone: 'danger',
        ops: items.map((f) => ({
          type: 'furniture.set' as const,
          payload: { action: 'delete' as const, id: f.id },
        })),
        undoLabel: 'Furniture deleted',
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Balcony
// ---------------------------------------------------------------------------

function balconyInspector(house: HouseModel, ids: readonly string[]): InspectorSelection {
  const balconies = ids
    .map((id) => house.balconies.find((b) => b.id === id))
    .filter((b): b is NonNullable<typeof b> => b !== undefined);
  if (balconies.length === 0) return emptySelection('balcony');

  const railingKind = shared(balconies.map((b) => b.railingKind));
  const railingHeight = shared(balconies.map((b) => b.railingHeightMm));
  const projection = shared(balconies.map((b) => b.projectionMm));

  const edit = (patch: Record<string, unknown>): Op[] =>
    balconies.map((b) => ({
      type: 'balcony.set' as const,
      payload: { action: 'edit' as const, id: b.id, ...patch },
    }));

  const fields: InspectorField[] = [
    {
      key: 'railingHeight',
      label: 'Railing height',
      kind: 'length',
      value: railingHeight.mixed ? null : (railingHeight.value ?? 0),
      displayText: railingHeight.mixed ? 'Mixed' : `${String(railingHeight.value ?? 0)} mm`,
      mixed: railingHeight.mixed,
      editable: true,
      minMm: 0,
      hint: 'City packs commonly require 1000 mm or more.',
      build: (next) => (typeof next === 'number' ? edit({ railingHeightMm: next }) : []),
      undoLabel: 'Railing changed',
    },
    {
      key: 'projection',
      label: 'Projection',
      kind: 'length',
      value: projection.mixed ? null : (projection.value ?? 0),
      displayText: projection.mixed ? 'Mixed' : `${String(projection.value ?? 0)} mm`,
      mixed: projection.mixed,
      editable: true,
      minMm: 0,
      hint: 'Beyond the building line. Checked against the projection rule.',
      build: (next) => (typeof next === 'number' ? edit({ projectionMm: next }) : []),
      undoLabel: 'Projection changed',
    },
    {
      key: 'railingKind',
      label: 'Railing type',
      kind: 'enum',
      value: railingKind.mixed ? null : (railingKind.value ?? null),
      displayText: railingKind.mixed ? 'Mixed' : humanEnum(String(railingKind.value ?? '')),
      mixed: railingKind.mixed,
      editable: true,
      options: enumOptions(RAILING_KINDS, {
        ms: 'MS railing',
        glass: 'Glass',
        masonry: 'Masonry',
        ms_glass: 'MS + glass',
        none: 'None',
      }),
      build: (next) => (typeof next === 'string' ? edit({ railingKind: next as RailingKind }) : []),
      undoLabel: 'Railing changed',
    },
  ];

  return {
    kind: 'balcony',
    count: balconies.length,
    title: balconies.length === 1 ? 'Balcony' : `${String(balconies.length)} balconies`,
    subtitle: null,
    fields,
    actions: [
      {
        key: 'delete',
        label: 'Delete balcony',
        tone: 'danger',
        ops: balconies.map((b) => ({
          type: 'balcony.set' as const,
          payload: { action: 'delete' as const, id: b.id },
        })),
        undoLabel: 'Balcony deleted',
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Storey
// ---------------------------------------------------------------------------

function storeyInspector(house: HouseModel, ids: readonly string[]): InspectorSelection {
  const storeys = ids
    .map((id) => house.storeys.find((s) => s.id === id))
    .filter((s): s is NonNullable<typeof s> => s !== undefined);
  if (storeys.length === 0) return emptySelection('storey');

  const height = shared(storeys.map((s) => s.heightMm));

  return {
    kind: 'storey',
    count: storeys.length,
    title: storeys.length === 1 ? (storeys[0]?.name ?? 'Storey') : `${String(storeys.length)} storeys`,
    subtitle: null,
    fields: [
      {
        key: 'height',
        label: 'Floor to floor',
        kind: 'length',
        value: height.mixed ? null : (height.value ?? 0),
        displayText: height.mixed ? 'Mixed' : `${String(height.value ?? 0)} mm`,
        mixed: height.mixed,
        editable: true,
        minMm: 2000,
        maxMm: 6000,
        hint: 'NBC needs 2.75 m clear in a habitable room; 3.0 m floor-to-floor is typical.',
        build: (next) => {
          if (typeof next !== 'number') return [];
          return storeys
            .filter((s) => s.heightMm !== next)
            .map((s) => ({
              type: 'storey.set_height' as const,
              payload: { storeyId: s.id, heightMm: next },
            }));
        },
        undoLabel: 'Storey height changed',
      },
    ],
    actions: [],
  };
}

// ---------------------------------------------------------------------------
// Shared bits
// ---------------------------------------------------------------------------

/**
 * The selection resolved to nothing — every id was pruned between the click and
 * this render. Says so rather than rendering an empty panel that looks broken.
 */
function emptySelection(kind: ElementType): InspectorSelection {
  return {
    kind,
    count: 0,
    title: 'Selection gone',
    subtitle: 'What was selected is no longer in the design.',
    fields: [],
    actions: [],
  };
}

/**
 * A length as the inspector shows it: the project's units first, millimetres in
 * brackets. Both, always — ft-in is what an Indian architect quotes to a client
 * (§15) and mm is what the drawing and the op carry (§7), and a panel that
 * showed only one of them would make the other a guess.
 */
function formatLen(mm: number | null, display: UnitsDisplay): string {
  if (mm === null) return '—';
  return `${formatLength(mm, display)} (${formatIndianNumber(mm)} mm)`;
}
