/**
 * transform.ts — copy / paste / array / mirror, expressed as PLANS OVER OPS
 * THAT ALREADY EXIST.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * NO NEW OP TYPES. THAT IS THE WHOLE POINT.
 * ════════════════════════════════════════════════════════════════════════════
 * `ops.ts` freezes the taxonomy at 32, and the state hash of a folded document
 * must come out byte-identical here and in `apps/api/garh_model`. Every op type
 * added is a new fold branch that has to be written twice and can diverge once.
 * So a paste is not `selection.paste`; it is a group of `wall.add`,
 * `opening.add`, `stair.add`, `column.set`, `furniture.set`, `balcony.set` and
 * `room.assign` — nine op types, all already in §4, all already folded and
 * golden-tested on both sides. A mirror IN PLACE is the same list again: the
 * walls are deleted and re-added at their reflected coordinates, keeping their
 * original ids, because the fold has no `wall.move` and no `opening.flip` to
 * reach for. (An earlier draft of this comment claimed it used exactly those two
 * ops. It never did — neither is emitted by either twin, and `wall.move` is the
 * op §850 below explains a mirror cannot use.)
 *
 * What IS new, and is therefore what the cross-language fixture pins, is this
 * module: two planners that must emit the SAME op list, key for key, for the
 * same document and the same request. `fixtures/model/golden-transforms.json`
 * is that contract; `transform.test.ts` and
 * `apps/api/garh_model/tests/test_transform.py` both assert every row of it.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ONE GESTURE, ONE UNDO
 * ════════════════════════════════════════════════════════════════════════════
 * The planner returns `{ ops, groupId }` and the caller dispatches
 * `applyGroup(doc, plan.ops, plan.groupId)`. A paste of twelve elements is one
 * `UndoEntry`, because `applyGroup` builds the inverse as the reversed
 * concatenation of the per-op inverses (`fold.ts`). The ops come back UNSTAMPED
 * — `applyGroup` stamps `groupId` on each — so the op list a test compares is
 * the same list the fixture stores.
 *
 * Order inside the group is a contract, for the same reason it is in
 * `copyStorey.ts`: walls first (an opening needs a host that exists), then
 * openings, stairs, columns, furniture, balconies, and room metadata last. The
 * reversed inverse then deletes leaves before walls, and undo cannot fail.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * IDS ARE DERIVED FROM THE GROUP ID — a deliberate deviation, stated plainly
 * ════════════════════════════════════════════════════════════════════════════
 * `ids.ts` reserves `derivedId` for elements the MODEL derives (rooms, slabs)
 * and points human-created elements at `newId`. A pasted wall is human-created,
 * so `newId` would be the letter of that rule — and it would make this module
 * untestable across the two languages, because a random ULID cannot be compared
 * with anything.
 *
 * So the new ids are `derivedIdUnique(type, "<groupId>|<type>|<sourceId>#<n>")`
 * against the document's existing ids. The GROUP ID is the randomness: the UI
 * mints it per gesture with `newId('group')`, so the pasted ids are as unique
 * as a ULID, while the PLAN is a pure function of (document, request) and can be
 * pinned in a fixture that both languages read. Two useful consequences fall
 * out: re-planning a refused paste with the same group id is idempotent, and a
 * plan can be diffed in a bug report.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE GEOMETRY, HONESTLY
 * ════════════════════════════════════════════════════════════════════════════
 * Every transform here is a {@link PlaneMap}: `x' = sx·x + tx`, `y' = sy·y + ty`
 * with `sx, sy ∈ {+1, -1}`. That covers translation (paste, array) and mirroring
 * about an axis-aligned line, which is the whole of what §7's orthogonal walls
 * and stairs can mean. It is closed under composition, it is an isometry, and on
 * integer input it produces integer output with no rounding at all — points
 * still go through `ptRound` (the sanctioned float→mm door, half away from zero)
 * so that the discipline holds if the map ever grows a rotation.
 *
 * Consequences that are NOT obvious and are each tested:
 *
 *  • A DOOR CHANGES HAND. `swing` encodes two independent facts: LEFT/RIGHT is
 *    the hinge END along the host wall's a→b parameter, IN/OUT is which side of
 *    that a→b line the leaf sweeps into. A reflection is an isometry, so the
 *    hinge stays at the same end (LEFT/RIGHT does not move); it is orientation
 *    REVERSING, so the side flips (IN/OUT does). `in-left` mirrors to
 *    `out-left`. Physically the leaf still sweeps into the same room, and the
 *    door is now the opposite hand — which is what a mirrored plan means.
 *    This only works because the mirrored wall keeps a↦M(a), b↦M(b) rather than
 *    being re-normalised left-to-right: that is what preserves `offsetMm`.
 *
 *  • NOTHING IS EVER REFLECTED THAT WOULD READ BACKWARDS. Furniture carries a
 *    `rotationDeg`, not a transform, so a mirrored item gets the ROTATION whose
 *    axis matches the reflected one; the catalogue mesh is never handed a
 *    negative scale. Sheet annotations — the only text the document owns — are
 *    not duplicated at all (see WHAT IS NOT COPIED). So no mirrored lettering
 *    can be produced by this module, by construction rather than by promise.
 *
 *  • A BALCONY RING IS RE-WOUND. A reflection reverses polygon orientation, so
 *    the mapped vertex list is reversed to bring the ring back to the winding it
 *    had. A 180° rotation (`sx = sy = -1`) preserves orientation and is left
 *    alone — one predicate, {@link isReflection}, drives the swing flip and the
 *    re-winding both.
 *
 *  • A STAIR IS REBUILT FROM ITS OWN FOOTPRINT. `Stair.origin` is a corner
 *    picked relative to the direction of travel, so mirroring it needs the
 *    footprint extent — and `stairFootprintPolygon` in `fold.ts` already owns
 *    that arithmetic. This module maps that polygon and reads the correct corner
 *    back off it rather than re-deriving flight and landing extents, so there is
 *    one source of truth for how big a stair is.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT IS NOT COPIED, STATED PLAINLY
 * ════════════════════════════════════════════════════════════════════════════
 * Facade components, material assignments and sheet annotations are not
 * duplicated, for the reason `copyStorey.ts` gives: they are building- or
 * sheet-scoped sub-models that merely reference a storey or an element, and
 * silently doubling them would put a second assignment on a surface with no way
 * for the architect to see it happened. Rooms and slabs are DERIVED, so they are
 * never copied either — the walls are copied and the detector rebuilds them; a
 * room's name, type, tags, lock and solver target are carried across separately,
 * against rooms PROVEN to exist after a trial fold.
 */

import { stairFootprintPolygon, tryFold } from './fold';
import type { ProjectDoc } from './model';
import { bbox } from './geometry';
import type { Bbox, Polygon, Pt } from './geometry';
import { derivedIdUnique, idType } from './ids';
import type { Id, StoreyId, WallId } from './ids';
import type {
  Balcony,
  Column,
  Direction4,
  FurnitureInstance,
  HouseModel,
  Opening,
  OpeningSwing,
  Room,
  Stair,
  Wall,
} from './model';
import type { Op } from './ops';
import { roundHalfAwayFromZero } from './units';
import type { ValidationIssue } from './validate';

// ---------------------------------------------------------------------------
// The plane map
// ---------------------------------------------------------------------------

/**
 * `x' = sx·x + tx`, `y' = sy·y + ty` with `sx, sy ∈ {+1, -1}`.
 *
 * Translations, axis-aligned reflections and their compositions — nothing else.
 * A rotation by anything but a multiple of 90° would take an orthogonal wall off
 * the grid and would need real rounding; §7 does not have such walls, so this
 * module does not pretend to transform them.
 */
export interface PlaneMap {
  readonly sx: 1 | -1;
  readonly sy: 1 | -1;
  readonly tx: number;
  readonly ty: number;
}

export const IDENTITY_MAP: PlaneMap = { sx: 1, sy: 1, tx: 0, ty: 0 };

/** Translate by `(dxMm, dyMm)`. */
export function translationMap(dxMm: number, dyMm: number): PlaneMap {
  return { sx: 1, sy: 1, tx: dxMm, ty: dyMm };
}

/** The axis a mirror reflects across. `vertical` is the line `x = at`. */
export type MirrorAxis = 'vertical' | 'horizontal';

/**
 * Reflect across `x = at` (vertical) or `y = at` (horizontal).
 *
 * `twiceAtMm`, not `atMm`: the reflection is `x' = 2·at − x`, and the axis the
 * UI most often wants is the SELECTION'S OWN CENTRE, which lands on a half
 * millimetre whenever the selection's extent is odd. Carrying `2·at` as the
 * integer keeps the map exact and keeps the reflection an exact involution —
 * mirroring twice returns the original coordinates, with no drift to accumulate.
 */
export function reflectionMap(axis: MirrorAxis, twiceAtMm: number): PlaneMap {
  return axis === 'vertical'
    ? { sx: -1, sy: 1, tx: twiceAtMm, ty: 0 }
    : { sx: 1, sy: -1, tx: 0, ty: twiceAtMm };
}

/** True when the map reverses orientation — exactly when a door changes hand. */
export function isReflection(m: PlaneMap): boolean {
  return m.sx * m.sy < 0;
}

/** True when the map moves nothing. */
export function isIdentityMap(m: PlaneMap): boolean {
  return m.sx === 1 && m.sy === 1 && m.tx === 0 && m.ty === 0;
}

/**
 * Map a point. The arithmetic is exact on integer input; `ptRound`'s rounding
 * rule (half away from zero) is applied anyway so that this is the only place a
 * future non-exact map would need to change.
 */
export function mapPt(m: PlaneMap, p: Pt): Pt {
  return {
    x: roundHalfAwayFromZero(m.sx * p.x + m.tx),
    y: roundHalfAwayFromZero(m.sy * p.y + m.ty),
  };
}

/**
 * Map a ring, restoring its winding when the map reverses orientation.
 *
 * Reversing rather than `ensureCcw`-ing is deliberate: it is the exact inverse
 * of what the reflection did, so a CCW ring stays CCW and a (nonconforming) CW
 * ring stays CW instead of being silently normalised behind the caller's back.
 */
export function mapPolygon(m: PlaneMap, poly: Polygon): Pt[] {
  const mapped = poly.map((p) => mapPt(m, p));
  return isReflection(m) ? mapped.reverse() : mapped;
}

/** Unit vector of a direction of travel. */
const DIRECTION_VECTORS: Readonly<Record<Direction4, Pt>> = {
  N: { x: 0, y: 1 },
  E: { x: 1, y: 0 },
  S: { x: 0, y: -1 },
  W: { x: -1, y: 0 },
};

function directionFromVector(x: number, y: number): Direction4 {
  if (x === 0 && y === 1) return 'N';
  if (x === 1 && y === 0) return 'E';
  if (x === 0 && y === -1) return 'S';
  if (x === -1 && y === 0) return 'W';
  // Unreachable for a PlaneMap (sx, sy are ±1 and the four inputs are axial), so
  // this cannot be a silent `return 'W'`: an unreachable branch that quietly
  // picks a direction is how a stair ends up facing the wrong way with every
  // line still reading correctly. Throwing keeps the Python mirror's KeyError
  // and this branch on the same behaviour.
  throw new RangeError(`Not an axial direction vector: (${String(x)}, ${String(y)})`);
}

/** Map a direction of travel. `sx`/`sy` are ±1, so the image is still axial. */
export function mapDirection(m: PlaneMap, d: Direction4): Direction4 {
  const v = DIRECTION_VECTORS[d];
  return directionFromVector(m.sx * v.x, m.sy * v.y);
}

/**
 * A door's `swing` under the map.
 *
 * See the module docstring: LEFT/RIGHT is the hinge end along the wall's a→b
 * parameter and survives any isometry that maps a↦M(a), b↦M(b); IN/OUT is which
 * side of that line the leaf sweeps into and flips under a reflection. A
 * translation or a 180° rotation changes neither.
 */
export function mapSwing(m: PlaneMap, swing: OpeningSwing): OpeningSwing {
  if (!isReflection(m)) return swing;
  const flipped = SWING_UNDER_REFLECTION[swing];
  if (flipped === undefined) {
    throw new RangeError(`Not an opening swing: ${JSON.stringify(swing)}`);
  }
  return flipped;
}

/**
 * IN/OUT flipped, LEFT/RIGHT held. Typed on `string` rather than on
 * `OpeningSwing` deliberately: a mapped type over the literal union would let
 * `noUncheckedIndexedAccess` believe the lookup always succeeds, and a document
 * carrying an out-of-enum swing would then get `undefined` back and mirror into
 * a door with no hand at all. The Python mirror raises on the same input.
 */
const SWING_UNDER_REFLECTION: Readonly<Record<string, OpeningSwing>> = {
  'in-left': 'out-left',
  'in-right': 'out-right',
  'out-left': 'in-left',
  'out-right': 'in-right',
};

/**
 * A furniture instance's `rotationDeg` under the map.
 *
 * The catalogue mesh is placed by a ROTATION, never by a transform, so a
 * mirrored item cannot be handed a negative scale (which is how a label or a
 * fabric print ends up back to front). What it gets instead is the rotation
 * whose forward axis matches the reflected forward axis. For an item with a
 * symmetric footprint that is exactly right; for an asymmetric one the position
 * and facing are right and the chirality is not reproduced, which is the honest
 * limit of a catalogue-instance model and is why this is documented rather than
 * hidden.
 *
 * Integer degrees in, integer degrees out — no trigonometry, so the Python
 * mirror cannot drift by a floating-point ulp.
 */
export function mapRotationDeg(m: PlaneMap, deg: number): number {
  // No `?? [1, 0]`: an unknown map falling back to the identity would leave every
  // mirrored sofa facing the way it already faced, silently, on a page where
  // every other element had moved.
  const entry = ROTATION_UNDER_MAP[`${String(m.sx)},${String(m.sy)}`];
  if (entry === undefined) {
    throw new RangeError(`Not a plane map: sx=${String(m.sx)}, sy=${String(m.sy)}`);
  }
  const [sign, offset] = entry;
  return (((sign * deg + offset) % 360) + 360) % 360;
}

/**
 * `sx,sy` → `[sign, offset]` for `deg' = sign·deg + offset (mod 360)`.
 *
 * Derived from where the map sends the facing vector `(cos d, sin d)`: the
 * identity keeps it, a horizontal mirror negates y (`−d`), a vertical mirror
 * negates x (`180 − d`), and both together is a 180° rotation.
 */
const ROTATION_UNDER_MAP: Readonly<Record<string, readonly [number, number]>> = {
  '1,1': [1, 0],
  '1,-1': [-1, 0],
  '-1,1': [-1, 180],
  '-1,-1': [1, 180],
};

/**
 * The corner of an axis-aligned footprint rectangle that a stair of this
 * `direction` calls its `origin`.
 *
 * `stairFootprintPolygon` builds the rectangle as
 * `origin → origin + right·width → … → origin + forward·depth`, where `right`
 * is `forward` turned 90° clockwise. Reading the corner back off the rectangle
 * (rather than re-deriving flight and landing extents here) keeps ONE source of
 * truth for how large a stair is.
 */
function stairOriginCorner(b: Bbox, direction: Direction4): Pt {
  switch (direction) {
    case 'N':
      return { x: b.minX, y: b.minY };
    case 'E':
      return { x: b.minX, y: b.maxY };
    case 'S':
      return { x: b.maxX, y: b.maxY };
    case 'W':
      return { x: b.maxX, y: b.minY };
  }
}

/** `{ origin, direction }` of a stair after the map. */
export function mapStairPlacement(
  m: PlaneMap,
  stair: Stair,
): { readonly origin: Pt; readonly direction: Direction4 } {
  const direction = mapDirection(m, stair.direction);
  const footprint = stairFootprintPolygon(stair).map((p) => mapPt(m, p));
  return { origin: stairOriginCorner(bbox(footprint), direction), direction };
}

// ---------------------------------------------------------------------------
// Selection
// ---------------------------------------------------------------------------

/** Element families this module can transform. */
export interface SelectionCounts {
  readonly walls: number;
  readonly openings: number;
  readonly stairs: number;
  readonly columns: number;
  readonly furniture: number;
  readonly balconies: number;
}

export const EMPTY_SELECTION_COUNTS: SelectionCounts = {
  walls: 0,
  openings: 0,
  stairs: 0,
  columns: 0,
  furniture: 0,
  balconies: 0,
};

export function totalSelected(counts: SelectionCounts): number {
  return (
    counts.walls +
    counts.openings +
    counts.stairs +
    counts.columns +
    counts.furniture +
    counts.balconies
  );
}

/** The resolved selection: real elements, all on one storey. */
interface ResolvedSelection {
  readonly storeyId: StoreyId;
  readonly walls: readonly Wall[];
  /** Hosted on a selected wall — explicitly selected or carried with the wall. */
  readonly openings: readonly Opening[];
  readonly stairs: readonly Stair[];
  readonly columns: readonly Column[];
  readonly furniture: readonly FurnitureInstance[];
  readonly balconies: readonly Balcony[];
  /** Room / slab ids that were in the selection and skipped (they are derived). */
  readonly derivedSkipped: number;
}

function selectionCounts(sel: ResolvedSelection): SelectionCounts {
  return {
    walls: sel.walls.length,
    openings: sel.openings.length,
    stairs: sel.stairs.length,
    columns: sel.columns.length,
    furniture: sel.furniture.length,
    balconies: sel.balconies.length,
  };
}

/** Every point the selection occupies — the extent a "mirror in place" uses. */
function selectionPoints(sel: ResolvedSelection): Pt[] {
  const pts: Pt[] = [];
  for (const w of sel.walls) pts.push(w.a, w.b);
  for (const s of sel.stairs) for (const p of stairFootprintPolygon(s)) pts.push(p);
  for (const c of sel.columns) pts.push(c.pt);
  for (const f of sel.furniture) pts.push(f.pt);
  for (const b of sel.balconies) for (const p of b.polygon) pts.push(p);
  return pts;
}

// ---------------------------------------------------------------------------
// Requests, plans and refusals
// ---------------------------------------------------------------------------

export type TransformKind = 'paste' | 'array' | 'mirror';

/** Fields every request carries. */
export interface TransformRequestBase {
  /** Element ids to transform. Room and slab ids are skipped (they are derived). */
  readonly elementIds: readonly string[];
  /**
   * The undo group id, and the seed the new element ids are derived from. Mint
   * one per gesture with `newId('group')`.
   */
  readonly groupId: Id<'group'>;
}

/** Paste: one copy, translated by `deltaMm`, optionally onto another storey. */
export interface PasteRequest extends TransformRequestBase {
  readonly deltaMm: Pt;
  /** Storey to paste onto. Absent or null = the storey the selection is on. */
  readonly targetStoreyId?: StoreyId | null;
}

/**
 * Array: a `countX` × `countY` grid of copies, the original at (0, 0).
 *
 * A linear array is a rectangular one with the other count set to 1. Spacings
 * are integer millimetres and may be negative (array to the west / south).
 */
export interface ArrayRequest extends TransformRequestBase {
  readonly countX: number;
  readonly countY: number;
  readonly spacingXMm: number;
  readonly spacingYMm: number;
}

/** Mirror across an axis-aligned line, as a copy or in place. */
export interface MirrorRequest extends TransformRequestBase {
  readonly axis: MirrorAxis;
  /**
   * Where the line sits, in mm. Absent or null puts it through the centre of
   * the selection's own extent — the CAD default, and exact even when that
   * centre falls on a half millimetre (see {@link reflectionMap}).
   */
  readonly atMm?: number | null;
  /** Keep the originals and add a mirrored copy. Default true. */
  readonly keepOriginal?: boolean;
  /** Only meaningful with `keepOriginal`; absent or null = the source storey. */
  readonly targetStoreyId?: StoreyId | null;
}

/** Total instances an array may produce, original included. */
export const MAX_ARRAY_INSTANCES = 400;

/**
 * Total ELEMENTS an array may emit — copies times the size of the selection.
 *
 * The instance cap alone bounds the wrong thing. `buildPlan` folds every emitted
 * op serially on a fork, and each `wall.add` re-runs room detection over a house
 * that is growing as it goes, so the cost is superlinear in the number of
 * ELEMENTS and barely sees the instance count. Measured on the four-wall demo
 * plan: 32 ops 0.14 s, 96 ops 1.48 s, 192 ops 8.41 s, 396 ops 59.6 s. A 20x20
 * array of a four-wall selection is comfortably INSIDE the instance cap and is
 * about 1,600 folds — a frozen tab, refused by nothing.
 *
 * 120 holds the worst case near two seconds while still allowing the arrays
 * people actually draw: a single column 10x10, a parking bay repeated down a
 * row, a four-wall module arrayed 5x6.
 */
export const MAX_ARRAY_ELEMENTS = 120;

export interface TransformPlan {
  /** Dispatch as ONE group: `applyGroup(doc, plan.ops, plan.groupId)`. */
  readonly ops: readonly Op[];
  readonly groupId: Id<'group'>;
  readonly kind: TransformKind;
  readonly sourceStoreyId: StoreyId;
  readonly targetStoreyId: StoreyId;
  /** Copies produced. 0 for a mirror in place, which moves the originals. */
  readonly instances: number;
  readonly selected: SelectionCounts;
  /** Elements created. All zero for a mirror in place. */
  readonly created: SelectionCounts;
  /** Room and slab ids in the selection that were skipped. */
  readonly derivedSkipped: number;
  /** Rooms whose name / type / lock / target travelled with the geometry. */
  readonly roomsCarried: number;
  /** Undo-toast copy: "Pasted 4 walls and 2 openings". */
  readonly label: string;
}

export type TransformRefusalReason =
  | 'empty-selection'
  | 'unknown-element'
  | 'unsupported-element'
  | 'mixed-storeys'
  | 'opening-without-wall'
  | 'unknown-storey'
  | 'count-out-of-range'
  | 'zero-offset'
  | 'rejected';

export interface TransformRefusal {
  readonly reason: TransformRefusalReason;
  readonly message: string;
  /** The fold's own issues when `reason` is 'rejected'; empty otherwise. */
  readonly issues: readonly ValidationIssue[];
}

export type TransformPlanResult =
  | { readonly ok: true; readonly plan: TransformPlan }
  | { readonly ok: false; readonly refusal: TransformRefusal };

function refuse(
  reason: TransformRefusalReason,
  message: string,
  issues: readonly ValidationIssue[] = [],
): TransformPlanResult {
  return { ok: false, refusal: { reason, message, issues } };
}

// ---------------------------------------------------------------------------
// Selection resolution
// ---------------------------------------------------------------------------

/** Families that carry geometry this module knows how to map. */
const TRANSFORMABLE_TYPES: ReadonlySet<string> = new Set([
  'wall',
  'opening',
  'stair',
  'column',
  'furniture',
  'balcony',
]);

/** Families the model derives — present in a rubber-band selection, not copyable. */
const DERIVED_TYPES: ReadonlySet<string> = new Set(['room', 'slab']);

type ResolveResult =
  | { readonly ok: true; readonly selection: ResolvedSelection }
  | { readonly ok: false; readonly refusal: TransformRefusal };

/**
 * Turn a list of ids into real elements on ONE storey.
 *
 * Single-storey is a hard requirement, not a convenience: a transform has one
 * target storey, and quietly flattening a two-storey selection onto it would
 * duplicate the upper floor's walls into the lower one — geometry that folds
 * cleanly and is wrong. So it refuses.
 */
function resolveSelection(house: HouseModel, elementIds: readonly string[]): ResolveResult {
  const wanted = new Set(elementIds);
  if (wanted.size === 0) {
    return {
      ok: false,
      refusal: { reason: 'empty-selection', message: 'Nothing is selected.', issues: [] },
    };
  }

  let derivedSkipped = 0;
  for (const id of wanted) {
    const type = idType(id);
    if (type !== null && DERIVED_TYPES.has(type)) {
      derivedSkipped += 1;
      continue;
    }
    if (type === null || !TRANSFORMABLE_TYPES.has(type)) {
      return {
        ok: false,
        refusal: {
          reason: 'unsupported-element',
          message:
            'Only walls, openings, stairs, columns, furniture and balconies can be copied or mirrored.',
          issues: [],
        },
      };
    }
  }

  const walls = house.walls.filter((w) => wanted.has(w.id));
  const stairs = house.stairs.filter((s) => wanted.has(s.id));
  const columns = house.columns.filter((c) => wanted.has(c.id));
  const furniture = house.furniture.filter((f) => wanted.has(f.id));
  const balconies = house.balconies.filter((b) => wanted.has(b.id));

  // An opening travels with its host wall. One selected explicitly whose wall is
  // NOT selected has nowhere to land — the copy would need a host wall that does
  // not exist — so say so rather than dropping it silently.
  const wallIds = new Set<string>(walls.map((w) => w.id));
  const openings = house.openings.filter((o) => wallIds.has(o.wallId));
  const orphan = house.openings.find((o) => wanted.has(o.id) && !wallIds.has(o.wallId));
  if (orphan !== undefined) {
    return {
      ok: false,
      refusal: {
        reason: 'opening-without-wall',
        message:
          'Select the wall too — a door or window can only be copied with the wall it sits in.',
        issues: [],
      },
    };
  }

  const found = walls.length + stairs.length + columns.length + furniture.length + balconies.length;
  const explicitOpenings = house.openings.filter((o) => wanted.has(o.id)).length;
  if (found + explicitOpenings + derivedSkipped < wanted.size) {
    return {
      ok: false,
      refusal: {
        reason: 'unknown-element',
        message: 'Part of that selection is no longer in this design.',
        issues: [],
      },
    };
  }
  if (found === 0) {
    return {
      ok: false,
      refusal: {
        reason: 'empty-selection',
        message:
          derivedSkipped > 0
            ? 'Rooms are derived from the walls around them — select those walls instead.'
            : 'Nothing is selected.',
        issues: [],
      },
    };
  }

  const storeyIds = new Set<string>();
  for (const w of walls) storeyIds.add(w.storeyId);
  for (const s of stairs) storeyIds.add(s.storeyId);
  for (const c of columns) storeyIds.add(c.storeyId);
  for (const f of furniture) storeyIds.add(f.storeyId);
  for (const b of balconies) storeyIds.add(b.storeyId);
  if (storeyIds.size > 1) {
    return {
      ok: false,
      refusal: {
        reason: 'mixed-storeys',
        message: 'That selection spans more than one storey — copy one storey at a time.',
        issues: [],
      },
    };
  }
  // `size` is exactly 1: `found > 0` above guarantees at least one contributor.
  const storeyId = Array.from(storeyIds)[0] as StoreyId;

  return {
    ok: true,
    selection: { storeyId, walls, openings, stairs, columns, furniture, balconies, derivedSkipped },
  };
}

// ---------------------------------------------------------------------------
// Id minting
// ---------------------------------------------------------------------------

/**
 * Deterministic ids for the copies, unique against the document.
 *
 * The `taken` set starts as every id the document already uses — derived rooms
 * and slabs included, because `derivedIdUnique`'s escape hatch must see them to
 * be an escape hatch — and grows as ids are minted, so two copies in one plan
 * can never collide either.
 */
class IdMint {
  private readonly taken: Set<string>;

  constructor(
    house: HouseModel,
    private readonly groupId: string,
  ) {
    this.taken = new Set<string>();
    for (const s of house.storeys) this.taken.add(s.id);
    for (const w of house.walls) this.taken.add(w.id);
    for (const o of house.openings) this.taken.add(o.id);
    for (const r of house.rooms) this.taken.add(r.id);
    for (const s of house.stairs) this.taken.add(s.id);
    for (const s of house.slabs) this.taken.add(s.id);
    for (const c of house.columns) this.taken.add(c.id);
    for (const f of house.furniture) this.taken.add(f.id);
    for (const b of house.balconies) this.taken.add(b.id);
    for (const c of house.facade.components) this.taken.add(c.id);
    for (const m of house.materials) this.taken.add(m.id);
  }

  mint<T extends 'wall' | 'opening' | 'stair' | 'column' | 'furniture' | 'balcony'>(
    type: T,
    sourceId: string,
    instance: number,
  ): Id<T> {
    const id = derivedIdUnique(
      type,
      `${this.groupId}|${type}|${sourceId}#${String(instance)}`,
      this.taken,
    );
    this.taken.add(id);
    return id;
  }
}

// ---------------------------------------------------------------------------
// Op builders
// ---------------------------------------------------------------------------

/**
 * The add-ops for one copy of the selection under `m`, in the group's order.
 *
 * Walls first so the openings below have a host; then the leaf families. The
 * reversed inverse therefore deletes leaves first and walls last, which is what
 * makes one undo of a paste safe.
 */
function duplicateOps(
  sel: ResolvedSelection,
  m: PlaneMap,
  targetStoreyId: StoreyId,
  mint: IdMint,
  instance: number,
): Op[] {
  const ops: Op[] = [];

  const wallIdMap = new Map<string, WallId>();
  for (const wall of sel.walls) {
    const id = mint.mint('wall', wall.id, instance);
    wallIdMap.set(wall.id, id);
    ops.push({
      type: 'wall.add',
      payload: {
        id,
        storeyId: targetStoreyId,
        // a↦M(a), b↦M(b) — NOT re-normalised. The a→b direction is what
        // `offsetMm` and the swing's hinge end are measured against.
        a: mapPt(m, wall.a),
        b: mapPt(m, wall.b),
        thicknessMm: wall.thicknessMm,
        kind: wall.kind,
        loadBearing: wall.loadBearing,
      },
    });
  }

  for (const opening of sel.openings) {
    const host = wallIdMap.get(opening.wallId);
    if (host === undefined) continue;
    ops.push({
      type: 'opening.add',
      payload: {
        id: mint.mint('opening', opening.id, instance),
        wallId: host,
        kind: opening.kind,
        widthMm: opening.widthMm,
        heightMm: opening.heightMm,
        sillMm: opening.sillMm,
        // An isometry preserves distance along the wall, so the offset is
        // unchanged; only the hand of the swing moves.
        offsetMm: opening.offsetMm,
        swing: mapSwing(m, opening.swing),
        tag: opening.tag,
      },
    });
  }

  for (const stair of sel.stairs) {
    const placement = mapStairPlacement(m, stair);
    ops.push({
      type: 'stair.add',
      payload: {
        id: mint.mint('stair', stair.id, instance),
        storeyId: targetStoreyId,
        kind: stair.kind,
        origin: placement.origin,
        direction: placement.direction,
        riserMm: stair.riserMm,
        treadMm: stair.treadMm,
        widthMm: stair.widthMm,
        risersCount: stair.risersCount,
        landing: stair.landing,
      },
    });
  }

  for (const column of sel.columns) {
    ops.push({
      type: 'column.set',
      payload: {
        action: 'add',
        id: mint.mint('column', column.id, instance),
        storeyId: targetStoreyId,
        pt: mapPt(m, column.pt),
        // Axis-aligned map, axis-aligned box: the footprint keeps its size.
        sizeMm: column.sizeMm,
      },
    });
  }

  for (const item of sel.furniture) {
    ops.push({
      type: 'furniture.set',
      payload: {
        action: 'place',
        id: mint.mint('furniture', item.id, instance),
        storeyId: targetStoreyId,
        catalogId: item.catalogId,
        pt: mapPt(m, item.pt),
        rotationDeg: mapRotationDeg(m, item.rotationDeg),
      },
    });
  }

  for (const balcony of sel.balconies) {
    ops.push({
      type: 'balcony.set',
      payload: {
        action: 'add',
        id: mint.mint('balcony', balcony.id, instance),
        storeyId: targetStoreyId,
        polygon: mapPolygon(m, balcony.polygon),
        railingKind: balcony.railingKind,
        railingHeightMm: balcony.railingHeightMm,
        projectionMm: balcony.projectionMm,
        slabThicknessMm: balcony.slabThicknessMm,
      },
    });
  }

  return ops;
}

/**
 * Move the ORIGINALS under `m` — a mirror with "keep original" turned off.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY THE WALLS ARE DELETED AND RE-ADDED RATHER THAN MOVED
 * ════════════════════════════════════════════════════════════════════════════
 * `wall.move` is the obvious op and it DEADLOCKS on any closed loop of walls.
 * Mirror a 6000×4000 room about the horizontal line through its own centre: the
 * south wall's destination is exactly where the north wall still stands, and the
 * north wall's is exactly where the south wall still stands. `WALL_DUPLICATE`
 * fires whichever one is moved first, and no ordering escapes it — the two
 * positions are a 2-cycle. Every rectangular plan in the product has that shape,
 * so a mirror built on `wall.move` would refuse the commonest case it exists for.
 *
 * Deleting all the selected walls before adding any of them breaks the cycle,
 * and re-adding them WITH THEIR ORIGINAL IDS keeps element identity intact — a
 * flipped plan is the same walls, so annotations anchored to a wall id, and the
 * user's own selection, survive. The ids are free by then: `wall.delete` has
 * already removed them. `wall.delete` also takes the hosted openings with it, so
 * they are re-added (same ids, hand flipped) rather than flipped in place.
 *
 * The leaf families have no duplicate rule and therefore no cycle, so they are
 * genuinely moved: `stair.edit`, and the `move` / `transform` / `edit` actions.
 *
 * TWO THINGS ARE LOST, AND THE FOLD LOSES BOTH. Named here rather than
 * discovered later:
 *
 *  1. A facade component anchored to a mirrored wall is dropped by
 *     `wall.delete`'s own cascade. Facade geometry is regenerated from the kit
 *     (§8) and is isolated from anything that affects areas, so this cannot move
 *     a compliance number.
 *  2. UNDOING this group restores every wall, opening and room POLYGON exactly,
 *     but a room whose id was itself inherited from an earlier merge can come
 *     back under a different derived id, and therefore blank. This is not a
 *     property of this module: `wall.delete` × n followed by `wall.add` × n at
 *     IDENTICAL coordinates has it too, in both languages, and `copyStorey.ts`
 *     has carried it since it was written. Room ids are history (`rooms.ts`
 *     preserves them by max-Jaccard match), the taxonomy has no op that sets a
 *     room id, and `withRoomMetadataRestore` deliberately keys on id rather than
 *     polygon so it can never mis-attach a name. The forward gesture is exact;
 *     it is only the undo of a whole-plan flip that can drop one room name.
 */
function moveInPlaceOps(sel: ResolvedSelection, m: PlaneMap): Op[] {
  const ops: Op[] = [];

  // Phase 1: every selected wall goes, so no destination is occupied.
  for (const wall of sel.walls) {
    ops.push({ type: 'wall.delete', payload: { wallId: wall.id } });
  }

  // Phase 2: the same walls come back, same ids, at the mirrored coordinates.
  for (const wall of sel.walls) {
    ops.push({
      type: 'wall.add',
      payload: {
        id: wall.id,
        storeyId: wall.storeyId,
        a: mapPt(m, wall.a),
        b: mapPt(m, wall.b),
        thicknessMm: wall.thicknessMm,
        kind: wall.kind,
        loadBearing: wall.loadBearing,
      },
    });
  }

  // Phase 3: the openings the delete cascade took, re-hosted with the new hand.
  for (const opening of sel.openings) {
    ops.push({
      type: 'opening.add',
      payload: {
        id: opening.id,
        wallId: opening.wallId,
        kind: opening.kind,
        widthMm: opening.widthMm,
        heightMm: opening.heightMm,
        sillMm: opening.sillMm,
        offsetMm: opening.offsetMm,
        swing: mapSwing(m, opening.swing),
        tag: opening.tag,
      },
    });
  }

  for (const stair of sel.stairs) {
    const placement = mapStairPlacement(m, stair);
    ops.push({
      type: 'stair.edit',
      payload: {
        stairId: stair.id,
        patch: { origin: placement.origin, direction: placement.direction },
      },
    });
  }

  for (const column of sel.columns) {
    ops.push({
      type: 'column.set',
      payload: { action: 'move', id: column.id, pt: mapPt(m, column.pt) },
    });
  }

  for (const item of sel.furniture) {
    ops.push({
      type: 'furniture.set',
      payload: {
        action: 'transform',
        id: item.id,
        pt: mapPt(m, item.pt),
        rotationDeg: mapRotationDeg(m, item.rotationDeg),
      },
    });
  }

  for (const balcony of sel.balconies) {
    ops.push({
      type: 'balcony.set',
      payload: { action: 'edit', id: balcony.id, polygon: mapPolygon(m, balcony.polygon) },
    });
  }

  return ops;
}

// ---------------------------------------------------------------------------
// Room metadata
// ---------------------------------------------------------------------------

/**
 * A room polygon as an order-independent signature.
 *
 * The map is an isometry, so a room's clear polygon maps vertex for vertex —
 * but nothing promises the detector starts the image ring at the image of the
 * source's first vertex, so the vertices are sorted before joining. Coordinates
 * are absolute integer mm, so two different rooms can never collide here.
 */
function roomSignature(polygon: Polygon): string {
  return polygon
    .map((p) => `${String(p.x)},${String(p.y)}`)
    .sort()
    .join(' ');
}

function roomHasMetadata(room: Room): boolean {
  return room.type !== 'unassigned' || room.name !== '' || room.tags.length > 0 || room.locked;
}

function roomHasTarget(room: Room): boolean {
  return room.targetAreaMm2 !== null || room.mustFace !== null;
}

/**
 * Carry room names, types, locks and solver targets onto the rooms the
 * transform produced.
 *
 * `after` is the document as it will be once the geometry ops land, so every id
 * referenced here is PROVEN to exist and these ops cannot fail.
 *
 * Only rooms that come out of the fold BLANK are considered. That is the filter
 * that works for both shapes of transform: a copy must not rename a room that
 * was already there and already named, and a mirror in place — which deletes and
 * re-adds its walls, so its rooms are re-derived from scratch and come back
 * unassigned even when the id is unchanged — must be allowed to put the names
 * back. Keying on "is this id new?" would have been right for the copy and
 * silently wrong for the mirror.
 */
/**
 * Exported for its own test, and for no other caller.
 *
 * The blank-room guard inside cannot be reached through the public API — a paste
 * whose copy lands on an existing room needs walls at the same coordinates, and
 * the fold rejects those as WALL_DUPLICATE first — so the guard is defensive and
 * has to be tested at the level where it CAN fail. The Python twin's test imports
 * `_room_metadata_ops` for exactly the same reason; keeping both twins testable
 * the same way is worth one exported internal.
 */
export function roomMetadataOps(
  before: HouseModel,
  after: HouseModel,
  sourceStoreyId: string,
  targetStoreyId: string,
  maps: readonly PlaneMap[],
): { ops: Op[]; carried: number } {
  const bySignature = new Map<string, Room[]>();
  for (const room of before.rooms) {
    if (room.storeyId !== sourceStoreyId) continue;
    if (!roomHasMetadata(room) && !roomHasTarget(room)) continue;
    for (const m of maps) {
      const key = roomSignature(room.polygon.map((p) => mapPt(m, p)));
      const bucket = bySignature.get(key);
      if (bucket === undefined) bySignature.set(key, [room]);
      else bucket.push(room);
    }
  }
  if (bySignature.size === 0) return { ops: [], carried: 0 };

  const ops: Op[] = [];
  let carried = 0;
  for (const room of after.rooms) {
    if (room.storeyId !== targetStoreyId) continue;
    if (roomHasMetadata(room) || roomHasTarget(room)) continue;
    // `shift`, not `get`: two rooms cannot share a signature, but consuming the
    // match keeps this honest if the detector ever merges two into one.
    const source = bySignature.get(roomSignature(room.polygon))?.shift();
    if (source === undefined) continue;
    carried += 1;
    if (roomHasMetadata(source)) {
      ops.push({
        type: 'room.assign',
        payload: {
          roomId: room.id,
          type: source.type,
          name: source.name,
          tags: source.tags,
          locked: source.locked,
        },
      });
    }
    if (roomHasTarget(source)) {
      ops.push({
        type: 'room.set_target',
        payload: {
          roomId: room.id,
          targetAreaMm2: source.targetAreaMm2,
          mustFace: source.mustFace,
        },
      });
    }
  }
  return { ops, carried };
}

// ---------------------------------------------------------------------------
// The planner core
// ---------------------------------------------------------------------------

function foldAll(
  doc: ProjectDoc,
  ops: readonly Op[],
): { ok: true; doc: ProjectDoc } | { ok: false; issues: readonly ValidationIssue[] } {
  let current = doc;
  for (const op of ops) {
    const outcome = tryFold(current, op, { computeInverse: false });
    if (!outcome.ok) return { ok: false, issues: outcome.issues };
    current = outcome.model;
  }
  return { ok: true, doc: current };
}

function issuesToMessage(issues: readonly ValidationIssue[]): string {
  const first = issues[0];
  if (first === undefined) return 'That transform is not valid here.';
  const more = issues.length > 1 ? ` (+${String(issues.length - 1)} more)` : '';
  return first.fix === undefined
    ? `${first.message}${more}`
    : `${first.message} ${first.fix}${more}`;
}

/** "4 walls, 2 openings and 1 stair" — only the families that are present. */
export function describeSelection(counts: SelectionCounts): string {
  const parts: string[] = [];
  const add = (n: number, one: string, many: string): void => {
    if (n > 0) parts.push(`${String(n)} ${n === 1 ? one : many}`);
  };
  add(counts.walls, 'wall', 'walls');
  add(counts.openings, 'opening', 'openings');
  add(counts.stairs, 'stair', 'stairs');
  add(counts.columns, 'column', 'columns');
  add(counts.furniture, 'furniture item', 'furniture items');
  add(counts.balconies, 'balcony', 'balconies');
  if (parts.length === 0) return 'nothing';
  if (parts.length === 1) return parts[0] as string;
  return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1] as string}`;
}

function scaleCounts(counts: SelectionCounts, factor: number): SelectionCounts {
  return {
    walls: counts.walls * factor,
    openings: counts.openings * factor,
    stairs: counts.stairs * factor,
    columns: counts.columns * factor,
    furniture: counts.furniture * factor,
    balconies: counts.balconies * factor,
  };
}

interface PlanCore {
  readonly kind: TransformKind;
  readonly selection: ResolvedSelection;
  readonly targetStoreyId: StoreyId;
  /** One map per copy. Empty when the plan moves the originals instead. */
  readonly maps: readonly PlaneMap[];
  /** Move the originals rather than adding copies. */
  readonly inPlace: PlaneMap | null;
  readonly label: string;
}

/**
 * Build, verify and package a plan.
 *
 * The geometry ops are folded on a FORK before the plan is returned, so a plan
 * that comes back `ok` is one the real dispatch will accept — a confirm dialog
 * must never promise a paste that then fails. The fork is also what tells us
 * which rooms actually appeared, which is the only honest way to carry room
 * names across (room ids are derived from the polygon, so they cannot be
 * predicted).
 */
function buildPlan(doc: ProjectDoc, groupId: Id<'group'>, core: PlanCore): TransformPlanResult {
  const mint = new IdMint(doc.house, groupId);
  const geometry: Op[] =
    core.inPlace !== null
      ? moveInPlaceOps(core.selection, core.inPlace)
      : core.maps.flatMap((m, i) =>
          duplicateOps(core.selection, m, core.targetStoreyId, mint, i + 1),
        );

  const folded = foldAll(doc, geometry);
  if (!folded.ok) {
    return refuse('rejected', issuesToMessage(folded.issues), folded.issues);
  }

  const maps = core.inPlace !== null ? [core.inPlace] : core.maps;
  const rooms = roomMetadataOps(
    doc.house,
    folded.doc.house,
    core.selection.storeyId,
    core.targetStoreyId,
    maps,
  );
  // Room ops reference ids proven to exist in `folded.doc`, but fold them too:
  // an op list that has never been folded end to end is exactly the kind of
  // "verified" that turns out not to be.
  const roomsFolded = foldAll(folded.doc, rooms.ops);
  if (!roomsFolded.ok) {
    return refuse('rejected', issuesToMessage(roomsFolded.issues), roomsFolded.issues);
  }

  const selected = selectionCounts(core.selection);
  const instances = core.inPlace !== null ? 0 : core.maps.length;
  return {
    ok: true,
    plan: {
      ops: [...geometry, ...rooms.ops],
      groupId,
      kind: core.kind,
      sourceStoreyId: core.selection.storeyId,
      targetStoreyId: core.targetStoreyId,
      instances,
      selected,
      created: scaleCounts(selected, instances),
      derivedSkipped: core.selection.derivedSkipped,
      roomsCarried: rooms.carried,
      label: core.label,
    },
  };
}

/** Resolve the storey a transform lands on. */
function resolveTarget(
  house: HouseModel,
  sourceStoreyId: StoreyId,
  requested: StoreyId | null | undefined,
): { ok: true; storeyId: StoreyId } | { ok: false; refusal: TransformRefusal } {
  if (requested === undefined || requested === null) return { ok: true, storeyId: sourceStoreyId };
  const found = house.storeys.some((s) => s.id === requested);
  if (!found) {
    return {
      ok: false,
      refusal: {
        reason: 'unknown-storey',
        message: 'That storey is no longer part of this design.',
        issues: [],
      },
    };
  }
  return { ok: true, storeyId: requested };
}

// ---------------------------------------------------------------------------
// paste
// ---------------------------------------------------------------------------

/**
 * Plan a paste: one translated copy of the selection.
 *
 * Pure — no store, no dispatch, no mutation of `doc`.
 */
export function planPaste(doc: ProjectDoc, req: PasteRequest): TransformPlanResult {
  const resolved = resolveSelection(doc.house, req.elementIds);
  if (!resolved.ok) return { ok: false, refusal: resolved.refusal };
  const sel = resolved.selection;

  const target = resolveTarget(doc.house, sel.storeyId, req.targetStoreyId);
  if (!target.ok) return { ok: false, refusal: target.refusal };

  const m = translationMap(req.deltaMm.x, req.deltaMm.y);
  // A zero-delta paste onto the same storey stacks every copy exactly on its
  // original. The fold catches that for walls (WALL_DUPLICATE) but NOT for
  // columns, furniture or balconies — nothing forbids two columns at one point —
  // so without this guard a "paste in place" would silently double the schedule
  // and the structural count. Guard it here, once, for every family.
  if (isIdentityMap(m) && target.storeyId === sel.storeyId) {
    return refuse(
      'zero-offset',
      'Pasting in place would stack the copy exactly on the original — move it, or paste onto another storey.',
    );
  }

  return buildPlan(doc, req.groupId, {
    kind: 'paste',
    selection: sel,
    targetStoreyId: target.storeyId,
    maps: [m],
    inPlace: null,
    label: `Pasted ${describeSelection(selectionCounts(sel))}`,
  });
}

// ---------------------------------------------------------------------------
// array
// ---------------------------------------------------------------------------

/**
 * Plan a rectangular (or linear) array.
 *
 * `countX` / `countY` INCLUDE the original, which stays put; the plan creates
 * `countX × countY − 1` copies. Instances are emitted row-major — y outer, x
 * inner — so the op order is a property of the request, not of a hash map's
 * iteration order.
 */
export function planArray(doc: ProjectDoc, req: ArrayRequest): TransformPlanResult {
  const resolved = resolveSelection(doc.house, req.elementIds);
  if (!resolved.ok) return { ok: false, refusal: resolved.refusal };
  const sel = resolved.selection;

  if (
    !Number.isSafeInteger(req.countX) ||
    !Number.isSafeInteger(req.countY) ||
    req.countX < 1 ||
    req.countY < 1 ||
    req.countX * req.countY > MAX_ARRAY_INSTANCES
  ) {
    return refuse(
      'count-out-of-range',
      `An array needs at least 1 in each direction and at most ${String(MAX_ARRAY_INSTANCES)} in total.`,
    );
  }
  if (req.countX * req.countY === 1) {
    return refuse('count-out-of-range', 'An array of one is the original — raise a count above 1.');
  }

  // The cap that actually bounds the work. See MAX_ARRAY_ELEMENTS: the instance
  // count says almost nothing about how long this will take, because what costs is
  // the number of elements folded, and one instance of a four-wall module is four
  // of them.
  const emitted = (req.countX * req.countY - 1) * totalSelected(selectionCounts(sel));
  if (emitted > MAX_ARRAY_ELEMENTS) {
    return refuse(
      'count-out-of-range',
      `That array would add ${String(emitted)} elements and at most ${String(
        MAX_ARRAY_ELEMENTS,
      )} are allowed — array a smaller selection, or fewer copies of this one.`,
    );
  }
  // Same reasoning as the zero-offset guard in `planPaste`, and it bites harder
  // here: a 12-count array with zero spacing puts twelve columns on one point.
  if ((req.countX > 1 && req.spacingXMm === 0) || (req.countY > 1 && req.spacingYMm === 0)) {
    return refuse(
      'zero-offset',
      'Give the array a spacing — at zero every copy lands on top of the original.',
    );
  }

  const maps: PlaneMap[] = [];
  for (let j = 0; j < req.countY; j++) {
    for (let i = 0; i < req.countX; i++) {
      if (i === 0 && j === 0) continue;
      maps.push(translationMap(i * req.spacingXMm, j * req.spacingYMm));
    }
  }

  return buildPlan(doc, req.groupId, {
    kind: 'array',
    selection: sel,
    targetStoreyId: sel.storeyId,
    maps,
    inPlace: null,
    label: `Arrayed ${describeSelection(selectionCounts(sel))} ${String(req.countX)}×${String(req.countY)}`,
  });
}

// ---------------------------------------------------------------------------
// mirror
// ---------------------------------------------------------------------------

/**
 * Plan a mirror across an axis-aligned line.
 *
 * With `keepOriginal` (the default) the originals stay and a mirrored copy is
 * added; without it the originals MOVE — the "flip the plan" gesture, which on
 * an Indian job is usually a Vastu-driven decision rather than a drafting one,
 * and which must not leave a second copy behind.
 */
export function planMirror(doc: ProjectDoc, req: MirrorRequest): TransformPlanResult {
  const resolved = resolveSelection(doc.house, req.elementIds);
  if (!resolved.ok) return { ok: false, refusal: resolved.refusal };
  const sel = resolved.selection;

  const keepOriginal = req.keepOriginal ?? true;
  const target = resolveTarget(
    doc.house,
    sel.storeyId,
    keepOriginal ? req.targetStoreyId : undefined,
  );
  if (!target.ok) return { ok: false, refusal: target.refusal };

  // `2·at` throughout, so the selection-centre default is exact even when the
  // extent is odd and the centre falls on a half millimetre.
  let twiceAt: number;
  if (req.atMm === undefined || req.atMm === null) {
    const pts = selectionPoints(sel);
    if (pts.length === 0) {
      // Unreachable through `resolveSelection` (it refuses an empty selection),
      // but an explicit refusal beats an exception if a family is ever added to
      // the selection without being added to `selectionPoints`.
      return refuse('empty-selection', 'Nothing is selected.');
    }
    const extent = bbox(pts);
    twiceAt = req.axis === 'vertical' ? extent.minX + extent.maxX : extent.minY + extent.maxY;
  } else {
    twiceAt = 2 * req.atMm;
  }

  const m = reflectionMap(req.axis, twiceAt);

  // A mirror that maps the selection onto ITSELF stacks the copy exactly on the
  // original — the same defect `planPaste` guards, arriving by a different route.
  // `isIdentityMap` cannot see it: a reflection is never the identity, yet a
  // selection symmetric about the axis is carried onto its own point set.
  //
  // This is not an exotic case, it is the DEFAULT one. With `atMm` absent the
  // axis is put through the selection's own centre, so any symmetric selection —
  // the usual two columns, a wall pair, a mirrored bathroom block — reflects onto
  // itself. The fold rejects a duplicate wall (WALL_DUPLICATE) but nothing forbids
  // two columns at one point, so without this the structural count and the
  // schedule silently double.
  //
  // Compared as a multiset over the whole selection, deliberately. A selection
  // that is only PARTLY symmetric (one column on the axis, one off it) is allowed
  // through and its on-axis member does stack — refusing the entire mirror because
  // one element sits on the axis would be the worse failure, and the architect can
  // see that one.
  if (keepOriginal && target.storeyId === sel.storeyId) {
    const points = selectionPoints(sel);
    if (points.length > 0) {
      const key = (q: Pt): string => `${q.x},${q.y}`;
      const here = points.map(key).sort();
      const there = points.map((q) => key(mapPt(m, q))).sort();
      if (here.every((value, index) => value === there[index])) {
        return refuse(
          'zero-offset',
          'This selection is symmetric about that axis, so the mirrored copy would land ' +
            'exactly on the original — move the axis, or mirror onto another storey.',
        );
      }
    }
  }

  const axisLabel = req.axis === 'vertical' ? 'vertically' : 'horizontally';

  if (!keepOriginal) {
    return buildPlan(doc, req.groupId, {
      kind: 'mirror',
      selection: sel,
      targetStoreyId: sel.storeyId,
      maps: [],
      inPlace: m,
      label: `Mirrored ${describeSelection(selectionCounts(sel))} ${axisLabel}`,
    });
  }

  return buildPlan(doc, req.groupId, {
    kind: 'mirror',
    selection: sel,
    targetStoreyId: target.storeyId,
    maps: [m],
    inPlace: null,
    label: `Mirrored ${describeSelection(selectionCounts(sel))} ${axisLabel}`,
  });
}
