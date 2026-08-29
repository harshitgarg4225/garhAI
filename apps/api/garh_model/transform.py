"""transform.py — copy / paste / array / mirror as PLANS OVER OPS THAT EXIST.

Mirror of ``packages/model/src/transform.ts``. Read that module's docstring
first; this one repeats only what a Python reader needs.

NO NEW OP TYPES. THAT IS THE WHOLE POINT
----------------------------------------
``ops.py`` freezes the taxonomy at 32, and the state hash of a folded document
must come out byte-identical here and in ``packages/model``. Every op type added
is a new fold branch that has to be written twice and can diverge once. So a
paste is not ``selection.paste``; it is a group of ``wall.add``, ``opening.add``,
``stair.add``, ``column.set``, ``furniture.set``, ``balcony.set`` and
``room.assign`` — nine op types, all already in section 4, all already folded and
golden-tested on both sides. A mirror IN PLACE is the same list again: the walls
are deleted and re-added at their reflected coordinates, keeping their original
ids, because the fold has no ``wall.move`` and no ``opening.flip`` to reach for.
(An earlier draft of this comment claimed it used exactly those two ops. It never
did — neither is emitted by either twin, and neither exists.)

What IS new — and is therefore what the cross-language fixture pins — is this
module: two planners that must emit the SAME op list, key for key, for the same
document and the same request. ``fixtures/model/golden-transforms.json`` is that
contract; ``garh_model/tests/test_transform.py`` and
``packages/model/src/transform.test.ts`` both assert every row of it.

ONE GESTURE, ONE UNDO
---------------------
The planner returns ``(ops, group_id)`` and the caller dispatches
``apply_group(doc, plan.ops, plan.group_id)``. A paste of twelve elements is one
``UndoEntry``, because ``apply_group`` builds the inverse as the reversed
concatenation of the per-op inverses. The ops come back UNSTAMPED — apply_group
stamps ``group_id`` on each — so the op list a test compares is the op list the
fixture stores.

Order inside the group is a contract: walls first (an opening needs a host that
exists), then openings, stairs, columns, furniture, balconies, and room metadata
last. The reversed inverse then deletes leaves before walls, so undo cannot fail.

IDS ARE DERIVED FROM THE GROUP ID — a deliberate deviation, stated plainly
-------------------------------------------------------------------------
``ids.py`` reserves ``derived_id`` for elements the MODEL derives (rooms, slabs)
and points human-created elements at ``new_id``. A pasted wall is human-created,
so ``new_id`` would be the letter of that rule — and it would make this module
untestable across the two languages, because a random ULID cannot be compared
with anything.

So the new ids are ``derived_id_unique(type, "<group_id>|<type>|<source>#<n>")``
against the document's existing ids. The GROUP ID is the randomness: the UI mints
one per gesture with ``new_id('group')``, so pasted ids are as unique as a ULID,
while the PLAN is a pure function of (document, request) and can be pinned in a
fixture both languages read. Two useful consequences fall out: re-planning a
refused paste with the same group id is idempotent, and a plan can be diffed in
a bug report.

THE GEOMETRY, HONESTLY
----------------------
Every transform here is a :class:`PlaneMap`: ``x' = sx*x + tx``,
``y' = sy*y + ty`` with ``sx, sy`` in ``{+1, -1}``. That covers translation
(paste, array) and mirroring about an axis-aligned line, which is the whole of
what section 7's orthogonal walls and stairs can mean. It is closed under
composition, it is an isometry, and on integer input it produces integer output
with no rounding at all — points still go through ``round_half_away_from_zero``
(the sanctioned float->mm door) so the discipline holds if the map ever grows a
rotation.

Consequences that are NOT obvious, and are each tested:

* A DOOR CHANGES HAND. ``swing`` encodes two independent facts: LEFT/RIGHT is
  the hinge END along the host wall's a->b parameter, IN/OUT is which side of
  that a->b line the leaf sweeps into. A reflection is an isometry, so the hinge
  stays at the same end (LEFT/RIGHT does not move); it is orientation REVERSING,
  so the side flips (IN/OUT does). ``in-left`` mirrors to ``out-left``.
  Physically the leaf still sweeps into the same room, and the door is now the
  opposite hand — which is what a mirrored plan means. This only works because
  the mirrored wall keeps ``a -> M(a)``, ``b -> M(b)`` rather than being
  re-normalised left-to-right: that is what preserves ``offset_mm``.
* NOTHING IS EVER REFLECTED THAT WOULD READ BACKWARDS. Furniture carries a
  ``rotation_deg``, not a transform, so a mirrored item gets the ROTATION whose
  axis matches the reflected one; the catalogue mesh is never handed a negative
  scale. Sheet annotations — the only text the document owns — are not
  duplicated at all. So no mirrored lettering can be produced by this module, by
  construction rather than by promise.
* A BALCONY RING IS RE-WOUND. A reflection reverses polygon orientation, so the
  mapped vertex list is reversed to bring the ring back to the winding it had. A
  180-degree rotation (``sx == sy == -1``) preserves orientation and is left
  alone — one predicate, :func:`is_reflection`, drives the swing flip and the
  re-winding both.
* A STAIR IS REBUILT FROM ITS OWN FOOTPRINT. ``Stair.origin`` is a corner picked
  relative to the direction of travel, so mirroring it needs the footprint
  extent — and ``stair_footprint_polygon`` in :mod:`garh_model.fold` already owns
  that arithmetic. This module maps that polygon and reads the correct corner
  back off it rather than re-deriving flight and landing extents, so there is one
  source of truth for how big a stair is.

WHAT IS NOT COPIED, STATED PLAINLY
----------------------------------
Facade components, material assignments and sheet annotations are not
duplicated: they are building- or sheet-scoped sub-models that merely reference a
storey or an element, and silently doubling them would put a second assignment on
a surface with no way for the architect to see it happened. Rooms and slabs are
DERIVED, so they are never copied either — the walls are copied and the detector
rebuilds them; a room's name, type, tags, lock and solver target are carried
across separately, against rooms PROVEN to exist after a trial fold.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .fold import stair_footprint_polygon, try_fold
from .geometry import Bbox, Polygon, Pt, bbox
from .ids import derived_id_unique, id_type
from .model import (
    Balcony,
    Column,
    Direction4,
    FurnitureInstance,
    HouseModel,
    Opening,
    OpeningSwing,
    ProjectDoc,
    Room,
    Stair,
    Wall,
)
from .ops import Op, op
from .units import round_half_away_from_zero
from .validate import ValidationIssue

__all__ = [
    "PlaneMap",
    "IDENTITY_MAP",
    "MirrorAxis",
    "translation_map",
    "reflection_map",
    "is_reflection",
    "is_identity_map",
    "map_pt",
    "map_polygon",
    "map_direction",
    "map_swing",
    "map_rotation_deg",
    "map_stair_placement",
    "SelectionCounts",
    "EMPTY_SELECTION_COUNTS",
    "total_selected",
    "describe_selection",
    "TransformKind",
    "PasteRequest",
    "ArrayRequest",
    "MirrorRequest",
    "MAX_ARRAY_ELEMENTS",
    "MAX_ARRAY_INSTANCES",
    "TransformPlan",
    "TransformRefusal",
    "TransformPlanResult",
    "plan_paste",
    "plan_array",
    "plan_mirror",
]


# ---------------------------------------------------------------------------
# The plane map
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlaneMap:
    """``x' = sx*x + tx``, ``y' = sy*y + ty`` with ``sx, sy`` in ``{+1, -1}``.

    Translations, axis-aligned reflections and their compositions — nothing
    else. A rotation by anything but a multiple of 90 degrees would take an
    orthogonal wall off the grid and would need real rounding; section 7 does not
    have such walls, so this module does not pretend to transform them.
    """

    sx: int
    sy: int
    tx: int
    ty: int


IDENTITY_MAP = PlaneMap(sx=1, sy=1, tx=0, ty=0)

#: The axis a mirror reflects across. ``vertical`` is the line ``x = at``.
MirrorAxis = Literal["vertical", "horizontal"]


def translation_map(dx_mm: int, dy_mm: int) -> PlaneMap:
    """Translate by ``(dx_mm, dy_mm)``."""
    return PlaneMap(sx=1, sy=1, tx=dx_mm, ty=dy_mm)


def reflection_map(axis: str, twice_at_mm: int) -> PlaneMap:
    """Reflect across ``x = at`` (vertical) or ``y = at`` (horizontal).

    ``twice_at_mm``, not ``at_mm``: the reflection is ``x' = 2*at - x``, and the
    axis the UI most often wants is the SELECTION'S OWN CENTRE, which lands on a
    half millimetre whenever the selection's extent is odd. Carrying ``2*at`` as
    the integer keeps the map exact and keeps the reflection an exact involution
    — mirroring twice returns the original coordinates, with no drift to
    accumulate.
    """
    if axis == "vertical":
        return PlaneMap(sx=-1, sy=1, tx=twice_at_mm, ty=0)
    return PlaneMap(sx=1, sy=-1, tx=0, ty=twice_at_mm)


def is_reflection(m: PlaneMap) -> bool:
    """True when the map reverses orientation — exactly when a door changes hand."""
    return m.sx * m.sy < 0


def is_identity_map(m: PlaneMap) -> bool:
    """True when the map moves nothing."""
    return m.sx == 1 and m.sy == 1 and m.tx == 0 and m.ty == 0


def map_pt(m: PlaneMap, p: Pt) -> Pt:
    """Map a point.

    The arithmetic is exact on integer input; the half-away-from-zero rule is
    applied anyway so that this is the only place a future non-exact map would
    need to change.
    """
    return Pt(
        x=round_half_away_from_zero(m.sx * p.x + m.tx),
        y=round_half_away_from_zero(m.sy * p.y + m.ty),
    )


def map_polygon(m: PlaneMap, poly: Polygon) -> list[Pt]:
    """Map a ring, restoring its winding when the map reverses orientation.

    Reversing rather than ``ensure_ccw``-ing is deliberate: it is the exact
    inverse of what the reflection did, so a CCW ring stays CCW and a
    (nonconforming) CW ring stays CW instead of being silently normalised behind
    the caller's back.
    """
    mapped = [map_pt(m, p) for p in poly]
    if is_reflection(m):
        mapped.reverse()
    return mapped


#: Unit vector of a direction of travel.
_DIRECTION_VECTORS: dict[str, tuple[int, int]] = {
    "N": (0, 1),
    "E": (1, 0),
    "S": (0, -1),
    "W": (-1, 0),
}


def _direction_from_vector(x: int, y: int) -> Direction4:
    if x == 0 and y == 1:
        return "N"
    if x == 1 and y == 0:
        return "E"
    if x == 0 and y == -1:
        return "S"
    if x == -1 and y == 0:
        return "W"
    # Unreachable for a PlaneMap (sx, sy are +/-1 and the four inputs are axial),
    # so this cannot be a silent ``return "W"``: an unreachable branch that
    # quietly picks a direction is how a stair ends up facing the wrong way with
    # every line still reading correctly.
    raise ValueError(f"Not an axial direction vector: ({x}, {y})")


def map_direction(m: PlaneMap, d: str) -> Direction4:
    """Map a direction of travel. ``sx``/``sy`` are +/-1, so the image is axial."""
    vx, vy = _DIRECTION_VECTORS[d]
    return _direction_from_vector(m.sx * vx, m.sy * vy)


def map_swing(m: PlaneMap, swing: str) -> OpeningSwing:
    """A door's ``swing`` under the map.

    See the module docstring: LEFT/RIGHT is the hinge end along the wall's a->b
    parameter and survives any isometry that maps ``a -> M(a)``, ``b -> M(b)``;
    IN/OUT is which side of that line the leaf sweeps into and flips under a
    reflection. A translation or a 180-degree rotation changes neither.
    """
    if not is_reflection(m):
        return swing
    flipped = _SWING_UNDER_REFLECTION.get(swing)
    if flipped is None:
        raise ValueError(f"Not an opening swing: {swing!r}")
    return flipped


#: IN/OUT flipped, LEFT/RIGHT held. ``.get`` plus an explicit raise rather than a
#: bare subscript, so an out-of-enum swing fails the same way it does in the
#: TypeScript mirror instead of surfacing as a KeyError on one side only.
_SWING_UNDER_REFLECTION: dict[str, OpeningSwing] = {
    "in-left": "out-left",
    "in-right": "out-right",
    "out-left": "in-left",
    "out-right": "in-right",
}


def map_rotation_deg(m: PlaneMap, deg: int) -> int:
    """A furniture instance's ``rotation_deg`` under the map.

    The catalogue mesh is placed by a ROTATION, never by a transform, so a
    mirrored item cannot be handed a negative scale (which is how a label or a
    fabric print ends up back to front). What it gets instead is the rotation
    whose forward axis matches the reflected forward axis. For an item with a
    symmetric footprint that is exactly right; for an asymmetric one the position
    and facing are right and the chirality is not reproduced, which is the honest
    limit of a catalogue-instance model and is why this is documented rather than
    hidden.

    Integer degrees in, integer degrees out — no trigonometry, so the TypeScript
    mirror cannot drift by a floating-point ulp.
    """
    # An explicit raise, not a fallback to the identity: an unknown map silently
    # leaving every mirrored sofa facing the way it already faced, on a page
    # where every other element had moved, is precisely a gate that never fires.
    entry = _ROTATION_UNDER_MAP.get((m.sx, m.sy))
    if entry is None:
        raise ValueError(f"Not a plane map: sx={m.sx}, sy={m.sy}")
    sign, offset = entry
    return (sign * deg + offset) % 360


#: ``(sx, sy) -> (sign, offset)`` for ``deg' = sign*deg + offset (mod 360)``.
#: Derived from where the map sends the facing vector ``(cos d, sin d)``:
#: identity keeps it, a horizontal mirror negates y (``-d``), a vertical mirror
#: negates x (``180 - d``), and both together is a 180-degree rotation.
_ROTATION_UNDER_MAP: dict[tuple[int, int], tuple[int, int]] = {
    (1, 1): (1, 0),
    (1, -1): (-1, 0),
    (-1, 1): (-1, 180),
    (-1, -1): (1, 180),
}


def _stair_origin_corner(b: Bbox, direction: str) -> Pt:
    """The corner of a footprint rectangle that a stair of this direction calls origin.

    ``stair_footprint_polygon`` builds the rectangle as
    ``origin -> origin + right*width -> ... -> origin + forward*depth``, where
    ``right`` is ``forward`` turned 90 degrees clockwise. Reading the corner back
    off the rectangle (rather than re-deriving flight and landing extents here)
    keeps ONE source of truth for how large a stair is.
    """
    if direction == "N":
        return Pt(x=b.min_x, y=b.min_y)
    if direction == "E":
        return Pt(x=b.min_x, y=b.max_y)
    if direction == "S":
        return Pt(x=b.max_x, y=b.max_y)
    return Pt(x=b.max_x, y=b.min_y)


def map_stair_placement(m: PlaneMap, stair: Stair) -> tuple[Pt, Direction4]:
    """``(origin, direction)`` of a stair after the map."""
    direction = map_direction(m, stair.direction)
    footprint = [map_pt(m, p) for p in stair_footprint_polygon(stair)]
    return _stair_origin_corner(bbox(footprint), direction), direction


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SelectionCounts:
    """Element families this module can transform."""

    walls: int = 0
    openings: int = 0
    stairs: int = 0
    columns: int = 0
    furniture: int = 0
    balconies: int = 0


EMPTY_SELECTION_COUNTS = SelectionCounts()


def total_selected(counts: SelectionCounts) -> int:
    return (
        counts.walls
        + counts.openings
        + counts.stairs
        + counts.columns
        + counts.furniture
        + counts.balconies
    )


@dataclass(frozen=True)
class _ResolvedSelection:
    """The resolved selection: real elements, all on one storey."""

    storey_id: str
    walls: tuple[Wall, ...]
    #: Hosted on a selected wall — explicitly selected or carried with the wall.
    openings: tuple[Opening, ...]
    stairs: tuple[Stair, ...]
    columns: tuple[Column, ...]
    furniture: tuple[FurnitureInstance, ...]
    balconies: tuple[Balcony, ...]
    #: Room / slab ids that were in the selection and skipped (they are derived).
    derived_skipped: int


def _selection_counts(sel: _ResolvedSelection) -> SelectionCounts:
    return SelectionCounts(
        walls=len(sel.walls),
        openings=len(sel.openings),
        stairs=len(sel.stairs),
        columns=len(sel.columns),
        furniture=len(sel.furniture),
        balconies=len(sel.balconies),
    )


def _selection_points(sel: _ResolvedSelection) -> list[Pt]:
    """Every point the selection occupies — the extent a "mirror in place" uses."""
    pts: list[Pt] = []
    for w in sel.walls:
        pts.append(w.a)
        pts.append(w.b)
    for s in sel.stairs:
        pts.extend(stair_footprint_polygon(s))
    for c in sel.columns:
        pts.append(c.pt)
    for f in sel.furniture:
        pts.append(f.pt)
    for b in sel.balconies:
        pts.extend(b.polygon)
    return pts


# ---------------------------------------------------------------------------
# Requests, plans and refusals
# ---------------------------------------------------------------------------

TransformKind = Literal["paste", "array", "mirror"]

#: Total instances an array may produce, original included.
MAX_ARRAY_INSTANCES = 400

#: Total ELEMENTS an array may emit — copies times the size of the selection.
#:
#: The instance cap alone bounds the wrong thing. ``_build_plan`` folds every
#: emitted op serially on a fork, and each ``wall.add`` re-runs room detection
#: over a house that is growing as it goes, so the cost is superlinear in the
#: number of ELEMENTS and barely sees the instance count. Measured on the
#: four-wall demo plan: 32 ops 0.14 s, 96 ops 1.48 s, 192 ops 8.41 s, 396 ops
#: 59.6 s. A 20x20 array of a four-wall selection is comfortably INSIDE the
#: instance cap and is about 1,600 folds — a frozen tab, refused by nothing.
#:
#: 120 holds the worst case near two seconds while still allowing the arrays
#: people actually draw: a single column 10x10, a parking bay repeated down a
#: row, a four-wall module arrayed 5x6.
MAX_ARRAY_ELEMENTS = 120


@dataclass(frozen=True)
class PasteRequest:
    """One copy, translated by ``delta_mm``, optionally onto another storey."""

    element_ids: Sequence[str]
    #: The undo group id, and the seed the new element ids are derived from.
    group_id: str
    delta_mm: Pt
    #: Storey to paste onto. ``None`` = the storey the selection is on.
    target_storey_id: str | None = None


@dataclass(frozen=True)
class ArrayRequest:
    """A ``count_x`` x ``count_y`` grid of copies, the original at (0, 0).

    A linear array is a rectangular one with the other count set to 1. Spacings
    are integer millimetres and may be negative (array to the west / south).
    """

    element_ids: Sequence[str]
    group_id: str
    count_x: int
    count_y: int
    spacing_x_mm: int
    spacing_y_mm: int


@dataclass(frozen=True)
class MirrorRequest:
    """Mirror across an axis-aligned line, as a copy or in place."""

    element_ids: Sequence[str]
    group_id: str
    axis: str
    #: Where the line sits, in mm. ``None`` puts it through the centre of the
    #: selection's own extent — the CAD default, and exact even when that centre
    #: falls on a half millimetre (see :func:`reflection_map`).
    at_mm: int | None = None
    #: Keep the originals and add a mirrored copy.
    keep_original: bool = True
    #: Only meaningful with ``keep_original``; ``None`` = the source storey.
    target_storey_id: str | None = None


@dataclass(frozen=True)
class TransformPlan:
    #: Dispatch as ONE group: ``apply_group(doc, plan.ops, plan.group_id)``.
    ops: tuple[Op, ...]
    group_id: str
    kind: str
    source_storey_id: str
    target_storey_id: str
    #: Copies produced. 0 for a mirror in place, which moves the originals.
    instances: int
    selected: SelectionCounts
    #: Elements created. All zero for a mirror in place.
    created: SelectionCounts
    #: Room and slab ids in the selection that were skipped.
    derived_skipped: int
    #: Rooms whose name / type / lock / target travelled with the geometry.
    rooms_carried: int
    #: Undo-toast copy: "Pasted 4 walls and 2 openings".
    label: str


@dataclass(frozen=True)
class TransformRefusal:
    reason: str
    message: str
    #: The fold's own issues when ``reason`` is 'rejected'; empty otherwise.
    issues: tuple[ValidationIssue, ...] = ()


@dataclass(frozen=True)
class TransformPlanResult:
    ok: bool
    plan: TransformPlan | None = None
    refusal: TransformRefusal | None = None


def _refuse(
    reason: str, message: str, issues: Sequence[ValidationIssue] = ()
) -> TransformPlanResult:
    return TransformPlanResult(
        ok=False, refusal=TransformRefusal(reason=reason, message=message, issues=tuple(issues))
    )


# ---------------------------------------------------------------------------
# Selection resolution
# ---------------------------------------------------------------------------

#: Families that carry geometry this module knows how to map.
_TRANSFORMABLE_TYPES = frozenset({"wall", "opening", "stair", "column", "furniture", "balcony"})

#: Families the model derives — present in a rubber-band selection, not copyable.
_DERIVED_TYPES = frozenset({"room", "slab"})


@dataclass(frozen=True)
class _ResolveResult:
    ok: bool
    selection: _ResolvedSelection | None = None
    refusal: TransformRefusal | None = None


def _resolve_selection(house: HouseModel, element_ids: Iterable[str]) -> _ResolveResult:
    """Turn a list of ids into real elements on ONE storey.

    Single-storey is a hard requirement, not a convenience: a transform has one
    target storey, and quietly flattening a two-storey selection onto it would
    duplicate the upper floor's walls into the lower one — geometry that folds
    cleanly and is wrong. So it refuses.
    """
    wanted = set(element_ids)
    if not wanted:
        return _ResolveResult(
            ok=False,
            refusal=TransformRefusal(reason="empty-selection", message="Nothing is selected."),
        )

    derived_skipped = 0
    for element_id in wanted:
        kind = id_type(element_id)
        if kind is not None and kind in _DERIVED_TYPES:
            derived_skipped += 1
            continue
        if kind is None or kind not in _TRANSFORMABLE_TYPES:
            return _ResolveResult(
                ok=False,
                refusal=TransformRefusal(
                    reason="unsupported-element",
                    message=(
                        "Only walls, openings, stairs, columns, furniture and balconies can be "
                        "copied or mirrored."
                    ),
                ),
            )

    walls = tuple(w for w in house.walls if w.id in wanted)
    stairs = tuple(s for s in house.stairs if s.id in wanted)
    columns = tuple(c for c in house.columns if c.id in wanted)
    furniture = tuple(f for f in house.furniture if f.id in wanted)
    balconies = tuple(b for b in house.balconies if b.id in wanted)

    # An opening travels with its host wall. One selected explicitly whose wall
    # is NOT selected has nowhere to land — the copy would need a host wall that
    # does not exist — so say so rather than dropping it silently.
    wall_ids = {w.id for w in walls}
    openings = tuple(o for o in house.openings if o.wall_id in wall_ids)
    orphan = next((o for o in house.openings if o.id in wanted and o.wall_id not in wall_ids), None)
    if orphan is not None:
        return _ResolveResult(
            ok=False,
            refusal=TransformRefusal(
                reason="opening-without-wall",
                message=(
                    "Select the wall too — a door or window can only be copied with the wall it "
                    "sits in."
                ),
            ),
        )

    found = len(walls) + len(stairs) + len(columns) + len(furniture) + len(balconies)
    explicit_openings = sum(1 for o in house.openings if o.id in wanted)
    if found + explicit_openings + derived_skipped < len(wanted):
        return _ResolveResult(
            ok=False,
            refusal=TransformRefusal(
                reason="unknown-element",
                message="Part of that selection is no longer in this design.",
            ),
        )
    if found == 0:
        return _ResolveResult(
            ok=False,
            refusal=TransformRefusal(
                reason="empty-selection",
                message=(
                    "Rooms are derived from the walls around them — select those walls instead."
                    if derived_skipped > 0
                    else "Nothing is selected."
                ),
            ),
        )

    storey_ids = set()
    for w in walls:
        storey_ids.add(w.storey_id)
    for s in stairs:
        storey_ids.add(s.storey_id)
    for c in columns:
        storey_ids.add(c.storey_id)
    for f in furniture:
        storey_ids.add(f.storey_id)
    for b in balconies:
        storey_ids.add(b.storey_id)
    if len(storey_ids) > 1:
        return _ResolveResult(
            ok=False,
            refusal=TransformRefusal(
                reason="mixed-storeys",
                message="That selection spans more than one storey — copy one storey at a time.",
            ),
        )
    # Exactly one: ``found > 0`` above guarantees at least one contributor.
    storey_id = next(iter(storey_ids))

    return _ResolveResult(
        ok=True,
        selection=_ResolvedSelection(
            storey_id=storey_id,
            walls=walls,
            openings=openings,
            stairs=stairs,
            columns=columns,
            furniture=furniture,
            balconies=balconies,
            derived_skipped=derived_skipped,
        ),
    )


# ---------------------------------------------------------------------------
# Id minting
# ---------------------------------------------------------------------------


class _IdMint:
    """Deterministic ids for the copies, unique against the document.

    The ``taken`` set starts as every id the document already uses — derived
    rooms and slabs included, because ``derived_id_unique``'s escape hatch must
    see them to be an escape hatch — and grows as ids are minted, so two copies
    in one plan can never collide either.
    """

    def __init__(self, house: HouseModel, group_id: str) -> None:
        self._group_id = group_id
        taken: set[str] = set()
        for s in house.storeys:
            taken.add(s.id)
        for w in house.walls:
            taken.add(w.id)
        for o in house.openings:
            taken.add(o.id)
        for r in house.rooms:
            taken.add(r.id)
        for st in house.stairs:
            taken.add(st.id)
        for sl in house.slabs:
            taken.add(sl.id)
        for c in house.columns:
            taken.add(c.id)
        for f in house.furniture:
            taken.add(f.id)
        for b in house.balconies:
            taken.add(b.id)
        for fc in house.facade.components:
            taken.add(fc.id)
        for ma in house.materials:
            taken.add(ma.id)
        self._taken = taken

    def mint(self, element_type: str, source_id: str, instance: int) -> str:
        new = derived_id_unique(
            element_type, f"{self._group_id}|{element_type}|{source_id}#{instance}", self._taken
        )
        self._taken.add(new)
        return new


# ---------------------------------------------------------------------------
# Op builders
# ---------------------------------------------------------------------------


def _duplicate_ops(
    sel: _ResolvedSelection,
    m: PlaneMap,
    target_storey_id: str,
    mint: _IdMint,
    instance: int,
) -> list[Op]:
    """The add-ops for one copy of the selection under ``m``, in the group's order.

    Walls first so the openings below have a host; then the leaf families. The
    reversed inverse therefore deletes leaves first and walls last, which is what
    makes one undo of a paste safe.
    """
    ops: list[Op] = []

    wall_id_map: dict[str, str] = {}
    for wall in sel.walls:
        new_id = mint.mint("wall", wall.id, instance)
        wall_id_map[wall.id] = new_id
        ops.append(
            op(
                "wall.add",
                id=new_id,
                storeyId=target_storey_id,
                # a -> M(a), b -> M(b) — NOT re-normalised. The a->b direction is
                # what offsetMm and the swing's hinge end are measured against.
                a=_pt_json(map_pt(m, wall.a)),
                b=_pt_json(map_pt(m, wall.b)),
                thicknessMm=wall.thickness_mm,
                kind=wall.kind,
                loadBearing=wall.load_bearing,
            )
        )

    for opening in sel.openings:
        host = wall_id_map.get(opening.wall_id)
        if host is None:
            continue
        ops.append(
            op(
                "opening.add",
                id=mint.mint("opening", opening.id, instance),
                wallId=host,
                kind=opening.kind,
                widthMm=opening.width_mm,
                heightMm=opening.height_mm,
                sillMm=opening.sill_mm,
                # An isometry preserves distance along the wall, so the offset is
                # unchanged; only the hand of the swing moves.
                offsetMm=opening.offset_mm,
                swing=map_swing(m, opening.swing),
                tag=opening.tag,
            )
        )

    for stair in sel.stairs:
        origin, direction = map_stair_placement(m, stair)
        ops.append(
            op(
                "stair.add",
                id=mint.mint("stair", stair.id, instance),
                storeyId=target_storey_id,
                kind=stair.kind,
                origin=_pt_json(origin),
                direction=direction,
                riserMm=stair.riser_mm,
                treadMm=stair.tread_mm,
                widthMm=stair.width_mm,
                risersCount=stair.risers_count,
                landing=(
                    None
                    if stair.landing is None
                    else {"widthMm": stair.landing.width_mm, "depthMm": stair.landing.depth_mm}
                ),
            )
        )

    for column in sel.columns:
        ops.append(
            op(
                "column.set",
                action="add",
                id=mint.mint("column", column.id, instance),
                storeyId=target_storey_id,
                pt=_pt_json(map_pt(m, column.pt)),
                # Axis-aligned map, axis-aligned box: the footprint keeps its size.
                sizeMm={"xMm": column.size_mm.x_mm, "yMm": column.size_mm.y_mm},
            )
        )

    for item in sel.furniture:
        ops.append(
            op(
                "furniture.set",
                action="place",
                id=mint.mint("furniture", item.id, instance),
                storeyId=target_storey_id,
                catalogId=item.catalog_id,
                pt=_pt_json(map_pt(m, item.pt)),
                rotationDeg=map_rotation_deg(m, item.rotation_deg),
            )
        )

    for balcony in sel.balconies:
        ops.append(
            op(
                "balcony.set",
                action="add",
                id=mint.mint("balcony", balcony.id, instance),
                storeyId=target_storey_id,
                polygon=[_pt_json(p) for p in map_polygon(m, balcony.polygon)],
                railingKind=balcony.railing_kind,
                railingHeightMm=balcony.railing_height_mm,
                projectionMm=balcony.projection_mm,
                slabThicknessMm=balcony.slab_thickness_mm,
            )
        )

    return ops


def _move_in_place_ops(sel: _ResolvedSelection, m: PlaneMap) -> list[Op]:
    """Move the ORIGINALS under ``m`` — a mirror with "keep original" turned off.

    WHY THE WALLS ARE DELETED AND RE-ADDED RATHER THAN MOVED
    --------------------------------------------------------
    ``wall.move`` is the obvious op and it DEADLOCKS on any closed loop of walls.
    Mirror a 6000x4000 room about the horizontal line through its own centre: the
    south wall's destination is exactly where the north wall still stands, and
    the north wall's is exactly where the south wall still stands.
    ``WALL_DUPLICATE`` fires whichever one is moved first, and no ordering
    escapes it — the two positions are a 2-cycle. Every rectangular plan in the
    product has that shape, so a mirror built on ``wall.move`` would refuse the
    commonest case it exists for.

    Deleting all the selected walls before adding any of them breaks the cycle,
    and re-adding them WITH THEIR ORIGINAL IDS keeps element identity intact — a
    flipped plan is the same walls, so annotations anchored to a wall id, and the
    user's own selection, survive. The ids are free by then: ``wall.delete`` has
    already removed them. ``wall.delete`` also takes the hosted openings with it,
    so they are re-added (same ids, hand flipped) rather than flipped in place.

    The leaf families have no duplicate rule and therefore no cycle, so they are
    genuinely moved: ``stair.edit``, and the move / transform / edit actions.

    TWO THINGS ARE LOST, AND THE FOLD LOSES BOTH. Named here rather than
    discovered later:

    1. A facade component anchored to a mirrored wall is dropped by
       ``wall.delete``'s own cascade. Facade geometry is regenerated from the kit
       (section 8) and is isolated from anything that affects areas, so this
       cannot move a compliance number.
    2. UNDOING this group restores every wall, opening and room POLYGON exactly,
       but a room whose id was itself inherited from an earlier merge can come
       back under a different derived id, and therefore blank. This is not a
       property of this module: ``wall.delete`` x n followed by ``wall.add`` x n
       at IDENTICAL coordinates has it too, in both languages, and
       ``copyStorey.ts`` has carried it since it was written. Room ids are
       history (``rooms.py`` preserves them by max-Jaccard match), the taxonomy
       has no op that sets a room id, and ``_with_room_metadata_restore``
       deliberately keys on id rather than polygon so it can never mis-attach a
       name. The forward gesture is exact; it is only the undo of a whole-plan
       flip that can drop one room name.
    """
    ops: list[Op] = []

    # Phase 1: every selected wall goes, so no destination is occupied.
    for wall in sel.walls:
        ops.append(op("wall.delete", wallId=wall.id))

    # Phase 2: the same walls come back, same ids, at the mirrored coordinates.
    for wall in sel.walls:
        ops.append(
            op(
                "wall.add",
                id=wall.id,
                storeyId=wall.storey_id,
                a=_pt_json(map_pt(m, wall.a)),
                b=_pt_json(map_pt(m, wall.b)),
                thicknessMm=wall.thickness_mm,
                kind=wall.kind,
                loadBearing=wall.load_bearing,
            )
        )

    # Phase 3: the openings the delete cascade took, re-hosted with the new hand.
    for opening in sel.openings:
        ops.append(
            op(
                "opening.add",
                id=opening.id,
                wallId=opening.wall_id,
                kind=opening.kind,
                widthMm=opening.width_mm,
                heightMm=opening.height_mm,
                sillMm=opening.sill_mm,
                offsetMm=opening.offset_mm,
                swing=map_swing(m, opening.swing),
                tag=opening.tag,
            )
        )

    for stair in sel.stairs:
        origin, direction = map_stair_placement(m, stair)
        ops.append(
            op(
                "stair.edit",
                stairId=stair.id,
                patch={"origin": _pt_json(origin), "direction": direction},
            )
        )

    for column in sel.columns:
        ops.append(op("column.set", action="move", id=column.id, pt=_pt_json(map_pt(m, column.pt))))

    for item in sel.furniture:
        ops.append(
            op(
                "furniture.set",
                action="transform",
                id=item.id,
                pt=_pt_json(map_pt(m, item.pt)),
                rotationDeg=map_rotation_deg(m, item.rotation_deg),
            )
        )

    for balcony in sel.balconies:
        ops.append(
            op(
                "balcony.set",
                action="edit",
                id=balcony.id,
                polygon=[_pt_json(p) for p in map_polygon(m, balcony.polygon)],
            )
        )

    return ops


def _pt_json(p: Pt) -> dict[str, int]:
    """Op payloads are the WIRE form — camelCase JSON, not model dataclasses."""
    return {"x": p.x, "y": p.y}


# ---------------------------------------------------------------------------
# Room metadata
# ---------------------------------------------------------------------------


def _room_signature(polygon: Sequence[Pt]) -> str:
    """A room polygon as an order-independent signature.

    The map is an isometry, so a room's clear polygon maps vertex for vertex —
    but nothing promises the detector starts the image ring at the image of the
    source's first vertex, so the vertices are sorted before joining. Coordinates
    are absolute integer mm, so two different rooms can never collide here.
    """
    return " ".join(sorted(f"{p.x},{p.y}" for p in polygon))


def _room_has_metadata(room: Room) -> bool:
    return room.type != "unassigned" or room.name != "" or len(room.tags) > 0 or room.locked


def _room_has_target(room: Room) -> bool:
    return room.target_area_mm2 is not None or room.must_face is not None


def _room_metadata_ops(
    before: HouseModel,
    after: HouseModel,
    source_storey_id: str,
    target_storey_id: str,
    maps: Sequence[PlaneMap],
) -> tuple[list[Op], int]:
    """Carry room names, types, locks and solver targets onto the new rooms.

    ``after`` is the document as it will be once the geometry ops land, so every
    id referenced here is PROVEN to exist and these ops cannot fail.

    Only rooms that come out of the fold BLANK are considered. That is the filter
    that works for both shapes of transform: a copy must not rename a room that
    was already there and already named, and a mirror in place — which deletes
    and re-adds its walls, so its rooms are re-derived from scratch and come back
    unassigned even when the id is unchanged — must be allowed to put the names
    back. Keying on "is this id new?" would have been right for the copy and
    silently wrong for the mirror.
    """
    by_signature: dict[str, list[Room]] = {}
    for room in before.rooms:
        if room.storey_id != source_storey_id:
            continue
        if not _room_has_metadata(room) and not _room_has_target(room):
            continue
        for m in maps:
            key = _room_signature([map_pt(m, p) for p in room.polygon])
            by_signature.setdefault(key, []).append(room)
    if not by_signature:
        return [], 0

    ops: list[Op] = []
    carried = 0
    for room in after.rooms:
        if room.storey_id != target_storey_id:
            continue
        if _room_has_metadata(room) or _room_has_target(room):
            continue
        # ``pop(0)``, not a plain lookup: two rooms cannot share a signature, but
        # consuming the match keeps this honest if the detector ever merges two
        # into one.
        bucket = by_signature.get(_room_signature(room.polygon))
        if not bucket:
            continue
        source = bucket.pop(0)
        carried += 1
        if _room_has_metadata(source):
            ops.append(
                op(
                    "room.assign",
                    roomId=room.id,
                    type=source.type,
                    name=source.name,
                    tags=list(source.tags),
                    locked=source.locked,
                )
            )
        if _room_has_target(source):
            ops.append(
                op(
                    "room.set_target",
                    roomId=room.id,
                    targetAreaMm2=source.target_area_mm2,
                    mustFace=source.must_face,
                )
            )
    return ops, carried


# ---------------------------------------------------------------------------
# The planner core
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FoldAllResult:
    ok: bool
    doc: ProjectDoc | None = None
    issues: tuple[ValidationIssue, ...] = ()


def _fold_all(doc: ProjectDoc, ops: Sequence[Op]) -> _FoldAllResult:
    current = doc
    for one in ops:
        outcome = try_fold(current, one, compute_inverse=False)
        if not outcome.ok or outcome.model is None:
            return _FoldAllResult(ok=False, issues=tuple(outcome.issues))
        current = outcome.model
    return _FoldAllResult(ok=True, doc=current)


def _issues_to_message(issues: Sequence[ValidationIssue]) -> str:
    if not issues:
        return "That transform is not valid here."
    first = issues[0]
    more = f" (+{len(issues) - 1} more)" if len(issues) > 1 else ""
    if first.fix is None:
        return f"{first.message}{more}"
    return f"{first.message} {first.fix}{more}"


def describe_selection(counts: SelectionCounts) -> str:
    """ "4 walls, 2 openings and 1 stair" — only the families that are present."""
    parts: list[str] = []

    def add(n: int, one: str, many: str) -> None:
        if n > 0:
            parts.append(f"{n} {one if n == 1 else many}")

    add(counts.walls, "wall", "walls")
    add(counts.openings, "opening", "openings")
    add(counts.stairs, "stair", "stairs")
    add(counts.columns, "column", "columns")
    add(counts.furniture, "furniture item", "furniture items")
    add(counts.balconies, "balcony", "balconies")
    if not parts:
        return "nothing"
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _scale_counts(counts: SelectionCounts, factor: int) -> SelectionCounts:
    return SelectionCounts(
        walls=counts.walls * factor,
        openings=counts.openings * factor,
        stairs=counts.stairs * factor,
        columns=counts.columns * factor,
        furniture=counts.furniture * factor,
        balconies=counts.balconies * factor,
    )


def _build_plan(
    doc: ProjectDoc,
    group_id: str,
    *,
    kind: str,
    selection: _ResolvedSelection,
    target_storey_id: str,
    maps: Sequence[PlaneMap],
    in_place: PlaneMap | None,
    label: str,
) -> TransformPlanResult:
    """Build, verify and package a plan.

    The geometry ops are folded on a FORK before the plan is returned, so a plan
    that comes back ``ok`` is one the real dispatch will accept — a confirm
    dialog must never promise a paste that then fails. The fork is also what
    tells us which rooms actually appeared, which is the only honest way to carry
    room names across (room ids are derived from the polygon, so they cannot be
    predicted).
    """
    mint = _IdMint(doc.house, group_id)
    if in_place is not None:
        geometry = _move_in_place_ops(selection, in_place)
    else:
        geometry = []
        for i, m in enumerate(maps):
            geometry.extend(_duplicate_ops(selection, m, target_storey_id, mint, i + 1))

    folded = _fold_all(doc, geometry)
    if not folded.ok or folded.doc is None:
        return _refuse("rejected", _issues_to_message(folded.issues), folded.issues)

    applied_maps = [in_place] if in_place is not None else list(maps)
    room_ops, carried = _room_metadata_ops(
        doc.house,
        folded.doc.house,
        selection.storey_id,
        target_storey_id,
        applied_maps,
    )
    # Room ops reference ids proven to exist in ``folded.doc``, but fold them
    # too: an op list that has never been folded end to end is exactly the kind
    # of "verified" that turns out not to be.
    rooms_folded = _fold_all(folded.doc, room_ops)
    if not rooms_folded.ok:
        return _refuse("rejected", _issues_to_message(rooms_folded.issues), rooms_folded.issues)

    selected = _selection_counts(selection)
    instances = 0 if in_place is not None else len(maps)
    return TransformPlanResult(
        ok=True,
        plan=TransformPlan(
            ops=tuple(geometry) + tuple(room_ops),
            group_id=group_id,
            kind=kind,
            source_storey_id=selection.storey_id,
            target_storey_id=target_storey_id,
            instances=instances,
            selected=selected,
            created=_scale_counts(selected, instances),
            derived_skipped=selection.derived_skipped,
            rooms_carried=carried,
            label=label,
        ),
    )


@dataclass(frozen=True)
class _TargetResult:
    ok: bool
    storey_id: str = ""
    refusal: TransformRefusal | None = None


def _resolve_target(
    house: HouseModel, source_storey_id: str, requested: str | None
) -> _TargetResult:
    """Resolve the storey a transform lands on."""
    if requested is None:
        return _TargetResult(ok=True, storey_id=source_storey_id)
    if not any(s.id == requested for s in house.storeys):
        return _TargetResult(
            ok=False,
            refusal=TransformRefusal(
                reason="unknown-storey",
                message="That storey is no longer part of this design.",
            ),
        )
    return _TargetResult(ok=True, storey_id=requested)


# ---------------------------------------------------------------------------
# paste
# ---------------------------------------------------------------------------


def plan_paste(doc: ProjectDoc, req: PasteRequest) -> TransformPlanResult:
    """Plan a paste: one translated copy of the selection.

    Pure — no store, no dispatch, no mutation of ``doc``.
    """
    resolved = _resolve_selection(doc.house, req.element_ids)
    if not resolved.ok or resolved.selection is None:
        return TransformPlanResult(ok=False, refusal=resolved.refusal)
    sel = resolved.selection

    target = _resolve_target(doc.house, sel.storey_id, req.target_storey_id)
    if not target.ok:
        return TransformPlanResult(ok=False, refusal=target.refusal)

    m = translation_map(req.delta_mm.x, req.delta_mm.y)
    # A zero-delta paste onto the same storey stacks every copy exactly on its
    # original. The fold catches that for walls (WALL_DUPLICATE) but NOT for
    # columns, furniture or balconies — nothing forbids two columns at one point
    # — so without this guard a "paste in place" would silently double the
    # schedule and the structural count. Guard it here, once, for every family.
    if is_identity_map(m) and target.storey_id == sel.storey_id:
        return _refuse(
            "zero-offset",
            "Pasting in place would stack the copy exactly on the original — move it, or paste "
            "onto another storey.",
        )

    return _build_plan(
        doc,
        req.group_id,
        kind="paste",
        selection=sel,
        target_storey_id=target.storey_id,
        maps=[m],
        in_place=None,
        label=f"Pasted {describe_selection(_selection_counts(sel))}",
    )


# ---------------------------------------------------------------------------
# array
# ---------------------------------------------------------------------------


def plan_array(doc: ProjectDoc, req: ArrayRequest) -> TransformPlanResult:
    """Plan a rectangular (or linear) array.

    ``count_x`` / ``count_y`` INCLUDE the original, which stays put; the plan
    creates ``count_x * count_y - 1`` copies. Instances are emitted row-major — y
    outer, x inner — so the op order is a property of the request, not of a hash
    map's iteration order.
    """
    resolved = _resolve_selection(doc.house, req.element_ids)
    if not resolved.ok or resolved.selection is None:
        return TransformPlanResult(ok=False, refusal=resolved.refusal)
    sel = resolved.selection

    if (
        not _is_int(req.count_x)
        or not _is_int(req.count_y)
        or req.count_x < 1
        or req.count_y < 1
        or req.count_x * req.count_y > MAX_ARRAY_INSTANCES
    ):
        return _refuse(
            "count-out-of-range",
            f"An array needs at least 1 in each direction and at most {MAX_ARRAY_INSTANCES} "
            "in total.",
        )
    if req.count_x * req.count_y == 1:
        return _refuse(
            "count-out-of-range", "An array of one is the original — raise a count above 1."
        )

    # The cap that actually bounds the work. See MAX_ARRAY_ELEMENTS: the instance
    # count says almost nothing about how long this will take, because what costs
    # is the number of elements folded, and one instance of a four-wall module is
    # four of them.
    emitted = (req.count_x * req.count_y - 1) * total_selected(_selection_counts(sel))
    if emitted > MAX_ARRAY_ELEMENTS:
        return _refuse(
            "count-out-of-range",
            f"That array would add {emitted} elements and at most {MAX_ARRAY_ELEMENTS} are "
            "allowed — array a smaller selection, or fewer copies of this one.",
        )
    # Same reasoning as the zero-offset guard in :func:`plan_paste`, and it bites
    # harder here: a 12-count array with zero spacing puts twelve columns on one
    # point.
    if (req.count_x > 1 and req.spacing_x_mm == 0) or (req.count_y > 1 and req.spacing_y_mm == 0):
        return _refuse(
            "zero-offset",
            "Give the array a spacing — at zero every copy lands on top of the original.",
        )

    maps: list[PlaneMap] = []
    for j in range(req.count_y):
        for i in range(req.count_x):
            if i == 0 and j == 0:
                continue
            maps.append(translation_map(i * req.spacing_x_mm, j * req.spacing_y_mm))

    return _build_plan(
        doc,
        req.group_id,
        kind="array",
        selection=sel,
        target_storey_id=sel.storey_id,
        maps=maps,
        in_place=None,
        label=(
            f"Arrayed {describe_selection(_selection_counts(sel))} " f"{req.count_x}×{req.count_y}"
        ),
    )


def _is_int(v: Any) -> bool:
    """``bool`` is an ``int`` in Python and must not pass as a count."""
    return isinstance(v, int) and not isinstance(v, bool)


# ---------------------------------------------------------------------------
# mirror
# ---------------------------------------------------------------------------


def plan_mirror(doc: ProjectDoc, req: MirrorRequest) -> TransformPlanResult:
    """Plan a mirror across an axis-aligned line.

    With ``keep_original`` (the default) the originals stay and a mirrored copy
    is added; without it the originals MOVE — the "flip the plan" gesture, which
    on an Indian job is usually a Vastu-driven decision rather than a drafting
    one, and which must not leave a second copy behind.
    """
    resolved = _resolve_selection(doc.house, req.element_ids)
    if not resolved.ok or resolved.selection is None:
        return TransformPlanResult(ok=False, refusal=resolved.refusal)
    sel = resolved.selection

    target = _resolve_target(
        doc.house, sel.storey_id, req.target_storey_id if req.keep_original else None
    )
    if not target.ok:
        return TransformPlanResult(ok=False, refusal=target.refusal)

    # ``2*at`` throughout, so the selection-centre default is exact even when the
    # extent is odd and the centre falls on a half millimetre.
    if req.at_mm is None:
        pts = _selection_points(sel)
        if not pts:
            # Unreachable through _resolve_selection (it refuses an empty
            # selection), but an explicit refusal beats an exception if a family
            # is ever added to the selection without being added to
            # _selection_points.
            return _refuse("empty-selection", "Nothing is selected.")
        extent = bbox(pts)
        twice_at = (
            extent.min_x + extent.max_x if req.axis == "vertical" else (extent.min_y + extent.max_y)
        )
    else:
        twice_at = 2 * req.at_mm

    m = reflection_map(req.axis, twice_at)

    # A mirror that maps the selection onto ITSELF stacks the copy exactly on the
    # original — the same defect `plan_paste` guards, arriving by a different
    # route. `is_identity_map` cannot see it: a reflection is never the identity,
    # yet a selection symmetric about the axis is carried onto its own point set.
    #
    # This is not an exotic case, it is the DEFAULT one. With `at_mm=None` the
    # axis is put through the selection's own centre, so any symmetric selection
    # — the usual two columns, a wall pair, a mirrored bathroom block — reflects
    # onto itself. The fold rejects a duplicate wall (WALL_DUPLICATE) but nothing
    # forbids two columns at one point, so without this the structural count and
    # the schedule silently double.
    #
    # Compared as a multiset over the whole selection, deliberately. A selection
    # that is only PARTLY symmetric (one column on the axis, one off it) is
    # allowed through and its on-axis member does stack — refusing the entire
    # mirror because one element sits on the axis would be the worse failure, and
    # the architect can see that one.
    if req.keep_original and target.storey_id == sel.storey_id:
        points = _selection_points(sel)
        here = sorted((q.x, q.y) for q in points)
        there = sorted((r.x, r.y) for r in (map_pt(m, q) for q in points))
        if points and here == there:
            return _refuse(
                "zero-offset",
                "This selection is symmetric about that axis, so the mirrored copy would land "
                "exactly on the original — move the axis, or mirror onto another storey.",
            )

    axis_label = "vertically" if req.axis == "vertical" else "horizontally"
    label = f"Mirrored {describe_selection(_selection_counts(sel))} {axis_label}"

    if not req.keep_original:
        return _build_plan(
            doc,
            req.group_id,
            kind="mirror",
            selection=sel,
            target_storey_id=sel.storey_id,
            maps=[],
            in_place=m,
            label=label,
        )

    return _build_plan(
        doc,
        req.group_id,
        kind="mirror",
        selection=sel,
        target_storey_id=target.storey_id,
        maps=[m],
        in_place=None,
        label=label,
    )
