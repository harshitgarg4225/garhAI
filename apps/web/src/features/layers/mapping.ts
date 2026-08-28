/**
 * mapping.ts — which §7 layer each thing on the plan belongs to, and what the
 * canvas draws once some of those layers are switched off.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE RULES ARE PYTHON'S, COPIED WITH THEIR LINE NUMBERS
 * ════════════════════════════════════════════════════════════════════════════
 * The drawings service already decides, per element, which layer it goes on.
 * Inventing a second opinion here would put the panel out of step with the DXF
 * the architect exports, so every rule below is lifted from the projection code
 * rather than reasoned about afresh:
 *
 *   services/drawings/projection/walls.py:351
 *       A_WALL_PART if wall.kind == "parapet" else A_WALL
 *   services/drawings/projection/walls.py:490
 *       A_DOOR if opening.kind == "door" else A_WIND
 *   services/drawings/projection/symbols.py:516,616
 *       columns and balconies (with their railings) → A_WALL_PART
 *   services/drawings/projection/symbols.py:166..220
 *       stair treads, nosing and the up arrow → A_STAIR
 *   services/drawings/projection/symbols.py:374,382
 *       room outline → A_AREA, room name and area text → A_TEXT
 *   services/drawings/autodim/render.py:78..128
 *       dimension chains, witness lines, leaders → A_DIM
 *   services/drawings/render/frame.py:118
 *       sheet frame and title block → A_TITL  (no canvas presence)
 *
 * ════════════════════════════════════════════════════════════════════════════
 * HOW HIDING IS APPLIED — AND WHY IT IS NOT A FORK OF THE RENDER PATH
 * ════════════════════════════════════════════════════════════════════════════
 * `PlanScene` builds one merged mesh per element family from the folded
 * `HouseModel` it is handed, and `PlanPage` passes `visible` to the three
 * overlay layers. There are therefore exactly two seams, and both already
 * exist:
 *
 *   · the MODEL handed to `PlanScene` — filter it and the merged geometry is
 *     built without those elements, by the same builder, in one draw call;
 *   · the `visible` props on room fill, dimensions and room tags.
 *
 * {@link resolvePlanLayerView} produces both. Nothing here builds geometry,
 * duplicates a builder, or knows what a triangle is — which is the whole point:
 * a second geometry path is how SVG, DXF and PDF would start to disagree, and
 * this module is not going to start that inside the editor.
 *
 * MODEL-DRIVEN, NOT DXF-FREEZE. Hiding A-DOOR removes the door from the model
 * the plan is drawn from, so the wall poché closes over the opening the door
 * hosted. That is Revit's behaviour for hiding a hosted category, not
 * AutoCAD's for freezing a layer, and it is the honest consequence of having
 * one wall mesh whose gaps are cut by `house.openings`. The alternative —
 * keeping the gap while dropping the leaf — would need `PlanScene` to read two
 * opening lists, which is a change in `PlanScene`, not here.
 *
 * IDENTITY IS LOAD-BEARING. When nothing is hidden, {@link filterHouseByLayers}
 * returns the SAME object it was given. `PlanScene` memoises its geometry on
 * `house` identity, so a fresh copy per render would rebuild every buffer on
 * every render and blow the §14 frame budget. The default state must cost
 * nothing at all.
 */

import type { HouseModel, Opening, Wall } from '@garh/model';

// Type-only, so this module stays free of three.js exactly as `stores/selection`
// does. `PickKind` and not `string` because a typo'd kind ('dimensions') would
// otherwise sit in the blocked set for ever, matching nothing — this repo has
// already shipped 83 rules that went inert on a value outside its own enum.
import type { PickKind } from '../canvas/core/constants';
import type { DrawingLayerName } from './layerSpecs';

// ---------------------------------------------------------------------------
// Element → layer
// ---------------------------------------------------------------------------

/** walls.py:351 — a parapet is partial-height, everything else is a full wall. */
export function layerOfWall(wall: Wall): 'A-WALL' | 'A-WALL-PART' {
  return wall.kind === 'parapet' ? 'A-WALL-PART' : 'A-WALL';
}

/**
 * walls.py:490 — doors on A-DOOR, everything else glazed on A-WIND.
 *
 * Written as `=== 'door'` rather than a lookup keyed on `OpeningKind` on
 * purpose: `ventilator` is a third member of the enum today and a fourth may
 * arrive, and a table would answer `undefined` for it while this answers
 * A-WIND — which is what the DXF writer does.
 */
export function layerOfOpening(opening: Opening): 'A-DOOR' | 'A-WIND' {
  return opening.kind === 'door' ? 'A-DOOR' : 'A-WIND';
}

// ---------------------------------------------------------------------------
// The visibility state this module consumes
// ---------------------------------------------------------------------------

/** Per-layer booleans. Every one of the nine names is present, always. */
export type LayerFlags = Readonly<Record<DrawingLayerName, boolean>>;

/**
 * What the plan editor should draw, expressed in the props the canvas already
 * takes. Every field here maps to exactly one existing seam — see the header.
 */
export interface PlanLayerView {
  /** The model to hand `<PlanScene house={…}>`. Identical to the input when nothing is hidden. */
  readonly house: HouseModel;
  /** `<PlanScene showRooms={…}>` — the A-AREA room wash and its outline. */
  readonly showRooms: boolean;
  /** `<DimensionLayer visible={…}>` — A-DIM. */
  readonly showDimensions: boolean;
  /** `<RoomTagLayer visible={…}>` — A-TEXT, the room name and area labels. */
  readonly showRoomTags: boolean;
}

// ---------------------------------------------------------------------------
// Filtering the model the plan is drawn from
// ---------------------------------------------------------------------------

/**
 * Drop every element whose layer is switched off.
 *
 * Returns `house` itself — same reference — when the five model-backed layers
 * are all on, so the default state adds no work and no re-memo anywhere
 * downstream.
 *
 * Openings orphaned by a hidden host wall go with it. `openingsOfStorey`
 * already ignores an opening whose wall it cannot find, so leaving them would
 * not draw anything; removing them keeps the filtered model internally
 * consistent for anything else that reads it.
 */
export function filterHouseByLayers(house: HouseModel, visible: LayerFlags): HouseModel {
  const hideWalls = !visible['A-WALL'];
  const hidePart = !visible['A-WALL-PART'];
  const hideDoors = !visible['A-DOOR'];
  const hideWindows = !visible['A-WIND'];
  const hideStairs = !visible['A-STAIR'];

  if (!hideWalls && !hidePart && !hideDoors && !hideWindows && !hideStairs) return house;

  const walls =
    hideWalls || hidePart ? house.walls.filter((w) => visible[layerOfWall(w)]) : house.walls;

  const wallIds = walls === house.walls ? null : new Set(walls.map((w) => w.id));

  const openings = house.openings.filter(
    (o) => visible[layerOfOpening(o)] && (wallIds === null || wallIds.has(o.wallId)),
  );

  return {
    ...house,
    walls,
    openings,
    stairs: hideStairs ? [] : house.stairs,
    // symbols.py puts columns and balconies (and balcony railings) on
    // A-WALL-PART with the parapets, so they leave and return together.
    columns: hidePart ? [] : house.columns,
    balconies: hidePart ? [] : house.balconies,
  };
}

/**
 * The whole answer: what `PlanPage` should hand the canvas for a given layer
 * state. One call, four values, no branching left at the call site.
 */
export function resolvePlanLayerView(house: HouseModel, visible: LayerFlags): PlanLayerView {
  return {
    house: filterHouseByLayers(house, visible),
    showRooms: visible['A-AREA'],
    showDimensions: visible['A-DIM'],
    showRoomTags: visible['A-TEXT'],
  };
}

// ---------------------------------------------------------------------------
// Locking — the ids and kinds the picker must refuse
// ---------------------------------------------------------------------------

/**
 * What {@link installLayerPickGate} refuses.
 *
 * Two channels because the plan has two kinds of pick target. Walls, openings,
 * stairs, columns and balconies pick as themselves and are refused BY ID.
 * Dimension segments and room labels are drawn by overlay layers whose pick
 * ids are synthetic (a dimension segment is not an element of the model at
 * all), so they are refused BY KIND.
 */
export interface LayerPickBlock {
  readonly ids: ReadonlySet<string>;
  readonly kinds: ReadonlySet<PickKind>;
}

export const EMPTY_PICK_BLOCK: LayerPickBlock = { ids: new Set(), kinds: new Set() };

/**
 * Everything the picker must not return, given the current layer state.
 *
 * BOTH locked and hidden layers are listed. Hidden geometry is normally not
 * registered at all — `PlanScene` passes a null resolver with `visible={false}`
 * and the overlay layers hide a whole `<group>`, which `isEffectivelyVisible`
 * already respects — so the hidden half is belt to that braces. It costs one
 * set insertion per element and it means the gate is still correct if only
 * half of this feature is ever wired up. A hidden element that stays clickable
 * is the bug `pickRegistry.ts` names in its own comments: you delete a wall you
 * cannot see.
 *
 * ROOMS ARE ONE PICK TARGET FOR TWO LAYERS. `RoomTagLayer` resolves its labels
 * to `{ kind: 'room', id }` — the same target the A-AREA room wash resolves to
 * — so there is no seam between them at the registry. Locking EITHER A-AREA or
 * A-TEXT therefore refuses room picks. Over-refusing is the safe direction for
 * a lock; under-refusing would be a lock that silently does nothing, which is
 * the failure this feature exists to avoid.
 */
export function blockedPicks(
  house: HouseModel,
  visible: LayerFlags,
  locked: LayerFlags,
): LayerPickBlock {
  const off = (layer: DrawingLayerName): boolean => locked[layer] || !visible[layer];

  const ids = new Set<string>();
  const kinds = new Set<PickKind>();

  const wallOff = off('A-WALL');
  const partOff = off('A-WALL-PART');
  if (wallOff || partOff) {
    for (const wall of house.walls) {
      if (off(layerOfWall(wall))) ids.add(wall.id);
    }
  }
  if (partOff) {
    for (const column of house.columns) ids.add(column.id);
    for (const balcony of house.balconies) ids.add(balcony.id);
  }

  if (off('A-DOOR') || off('A-WIND')) {
    for (const opening of house.openings) {
      if (off(layerOfOpening(opening))) ids.add(opening.id);
    }
  }

  if (off('A-STAIR')) {
    for (const stair of house.stairs) ids.add(stair.id);
  }

  // Kind-level refusals. A dimension segment's id is synthetic, and a room is
  // reachable through two layers (see the note above).
  if (off('A-DIM')) kinds.add('dimension');
  if (locked['A-AREA'] || locked['A-TEXT']) kinds.add('room');

  return { ids, kinds };
}
