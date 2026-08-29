"""What actually changed between two model states, as geometry. **Pure integers.**

A revision cloud is only worth drawing if it is *derived*. A hand-maintained list of
"things I changed in R2" is wrong within a week — the architect who moved the kitchen wall
at 11pm does not go back and add a cloud — and a wrong cloud is worse than none, because
the reviewer trusts it and stops looking. So this module computes the difference between
two folded model states and hands back, per changed element, the box on the plan that a
cloud has to enclose.

WHAT IS COMPARED, AND WHY THOSE
-------------------------------
Walls, openings, rooms, stairs, columns and balconies: the six element kinds a municipal
*plan* actually draws. Two kinds are deliberately left out and it is worth saying why,
because "the diff missed it" and "the diff excluded it" must not look the same:

* **Slabs** are derived per storey and are the size of the floor. A slab whose polygon
  followed a moved wall would cloud the entire sheet, which tells a reviewer nothing. The
  wall that moved is clouded instead, and it is the wall the reviewer wants to see.
* **Furniture** is not part of a submission drawing at all (§7 draws it on the client set,
  not the municipal one), so a moved sofa must not raise a revision cloud on a sanction
  sheet.

Both exclusions are stated in :data:`COMPARED_KINDS` and asserted by
``test_revisions.py::test_slabs_and_furniture_are_excluded_deliberately`` — an exclusion
nobody can see is indistinguishable from a bug.

MATCHING IS BY ID, AND THAT IS THE RIGHT CALL
---------------------------------------------
Element ids are stable across a fold (``garh_model`` mints them once and the op log keeps
them), so "the same wall, moved" and "a different wall" are distinguishable — which is
exactly what a cloud has to get right. A wall deleted and redrawn in the same place is
genuinely two elements and reads as ``removed`` + ``added``; that is the truth, and it is
what the architect did.

THE BOX
-------
Every changed element reports an axis-aligned bounding box in model millimetres, on the
storey it belongs to. Boxes are conservative — a skewed wall reports the bounding box of
its swept rectangle, not the rectangle — because a cloud that is 50 mm too big is correct
and a cloud that clips the thing it points at is not.

A **removed** element's box comes from the *before* state (it is the only state that has
it); an added or modified element's from the *after*. An opening's box needs its host
wall, so it is taken from the same state the opening was read from — an opening whose wall
went away in the same revision is reported against the wall it had.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "COMPARED_KINDS",
    "COMPARE_KINDS",
    "EXCLUDED_KINDS",
    "Box",
    "ChangedElement",
    "ModelDiff",
    "cluster_boxes",
    "diff_models",
    "merge_boxes",
]

#: ``(min_x, min_y, max_x, max_y)`` in model millimetres.
Box = tuple[int, int, int, int]

#: The element kinds a plan draws and this diff therefore compares, in draw order.
COMPARED_KINDS: tuple[str, ...] = ("wall", "opening", "room", "stair", "column", "balcony")

#: Kinds deliberately not compared, with the reason. See the module docstring.
EXCLUDED_KINDS: Mapping[str, str] = {
    "slab": "derived and floor-sized — clouding it would cloud the whole sheet",
    "furniture": "not drawn on a municipal submission sheet",
}

#: Fields whose change is a *consequence* of another element's change, not an edit in its
#: own right. A room's polygon is derived from the walls by planar subdivision
#: (``garh_model.fold``), so moving one partition rewrites the polygons of the rooms either
#: side of it — and clouding those would put a cloud around the whole floor for a 500 mm
#: wall move, which tells a reviewer nothing.
#:
#: Such a change is **recorded** (``ChangedElement.derived``) and excluded from clouding by
#: default, never dropped: the wall that caused it is clouded, and a caller that wants the
#: full picture passes ``include_derived=True``. Nothing else in this model has derived
#: geometry — slabs, the other derived array, are excluded outright above.
_DERIVED_FIELDS: Mapping[str, frozenset[str]] = {"room": frozenset({"polygon"})}

#: Fields compared per kind. Anything not listed cannot raise a cloud, so the list is the
#: specification: ``Room.locked`` changing is not a drawing change, ``Room.name`` is.
_COMPARED_FIELDS: Mapping[str, tuple[str, ...]] = {
    "wall": ("a", "b", "thickness_mm", "kind", "load_bearing", "storey_id"),
    "opening": (
        "wall_id",
        "kind",
        "width_mm",
        "height_mm",
        "sill_mm",
        "offset_mm",
        "swing",
        "tag",
    ),
    "room": ("type", "name", "polygon", "storey_id"),
    # C-8 only. A furniture instance IS its catalogue id, its point and its rotation —
    # the footprint lives in the catalogue, so those four fields are the whole of what
    # can change about one here.
    "furniture": ("catalog_id", "pt", "rotation_deg", "storey_id"),
    "stair": (
        "kind",
        "origin",
        "direction",
        "riser_mm",
        "tread_mm",
        "width_mm",
        "risers_count",
        "landing",
        "storey_id",
    ),
    "column": ("pt", "size_mm", "storey_id"),
    "balcony": (
        "polygon",
        "railing_kind",
        "railing_height_mm",
        "projection_mm",
        "slab_thickness_mm",
        "storey_id",
    ),
}


@dataclass(frozen=True)
class ChangedElement:
    """One element that differs between two states, and where it sits on the plan."""

    element_id: str
    #: One of :data:`COMPARED_KINDS`.
    kind: str
    #: ``added`` | ``removed`` | ``modified``.
    change: str
    storey_id: str
    box: Box
    #: For ``modified``: the field names that differ, sorted. Empty otherwise.
    fields: tuple[str, ...] = ()
    #: True when every changed field is derived from another element's change — see
    #: :data:`_DERIVED_FIELDS`. Recorded, but not clouded by default.
    derived: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "elementId": self.element_id,
            "kind": self.kind,
            "change": self.change,
            "storeyId": self.storey_id,
            "box": list(self.box),
            "fields": list(self.fields),
            "derived": self.derived,
        }


@dataclass(frozen=True)
class ModelDiff:
    """Every changed element between two states, with the storeys they land on."""

    elements: tuple[ChangedElement, ...]
    #: Storeys present in either state, ground first — the sheet order.
    storey_ids: tuple[str, ...] = ()
    #: ``(element_id, kind, change)`` for elements that changed but could not be placed
    #: on a plan — an opening whose host wall is missing from both states, a room with an
    #: empty polygon. They cannot be clouded, but they are NOT dropped: a diff that
    #: quietly discards a change is the "gate that never fires" defect, so they are
    #: carried here, counted by :meth:`counts` and printed by :meth:`summary`.
    unplaced: tuple[tuple[str, str, str], ...] = ()

    def __bool__(self) -> bool:
        return bool(self.elements)

    def for_storey(
        self, storey_id: str, *, include_derived: bool = True
    ) -> tuple[ChangedElement, ...]:
        return tuple(
            e
            for e in self.elements
            if e.storey_id == storey_id and (include_derived or not e.derived)
        )

    def boxes_for_storey(self, storey_id: str, *, include_derived: bool = False) -> tuple[Box, ...]:
        """Boxes to cloud. Derived changes are out by default — see :data:`_DERIVED_FIELDS`."""
        return tuple(e.box for e in self.for_storey(storey_id, include_derived=include_derived))

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for element in self.elements:
            out[element.change] = out.get(element.change, 0) + 1
        for _id, _kind, change in self.unplaced:
            out[change] = out.get(change, 0) + 1
        return out

    def summary(self) -> str:
        """One line for the register's description field, when the user gave none."""
        if not self.elements and not self.unplaced:
            return "no geometric change"
        counts = self.counts()
        parts = [
            "%d %s" % (counts[change], change)
            for change in ("added", "modified", "removed")
            if counts.get(change)
        ]
        kinds = sorted({e.kind for e in self.elements} | {k for _i, k, _c in self.unplaced})
        summary = "%s (%s)" % (", ".join(parts), ", ".join(kinds))
        if self.unplaced:
            summary += "; %d not locatable on a plan" % len(self.unplaced)
        return summary

    def to_json(self) -> dict[str, Any]:
        return {
            "elements": [e.to_json() for e in self.elements],
            "storeyIds": list(self.storey_ids),
            "counts": self.counts(),
            "unplaced": [{"elementId": i, "kind": k, "change": c} for i, k, c in self.unplaced],
        }


# ---------------------------------------------------------------------------
# Reading a state — model object or JSON, one code path after this point
# ---------------------------------------------------------------------------
def _house_of(state: Any) -> Any:
    """The ``HouseModel`` (or its JSON) inside whatever the caller passed."""
    if isinstance(state, Mapping):
        if "house" in state and isinstance(state["house"], Mapping):
            return state["house"]
        if "model" in state and isinstance(state["model"], Mapping):
            return _house_of(state["model"])
        return state
    house = getattr(state, "house", None)
    return state if house is None else house


_JSON_KEYS: Mapping[str, str] = {
    "wall": "walls",
    "opening": "openings",
    "room": "rooms",
    "stair": "stairs",
    "column": "columns",
    "balcony": "balconies",
    # Read only when a caller asks for COMPARE_KINDS (C-8); the revision-cloud default
    # never reaches for it.
    "furniture": "furniture",
}


class _Element:
    """A kind-tagged view of one element, reading objects and JSON the same way.

    JSON is camelCase and dataclasses are snake_case; the mapping is mechanical
    (``garh_model.snake_to_camel``), so one accessor covers both rather than two parallel
    diff implementations that can disagree about what "changed" means.
    """

    __slots__ = ("_raw", "kind")

    def __init__(self, raw: Any, kind: str) -> None:
        self._raw = raw
        self.kind = kind

    @property
    def id(self) -> str:
        return str(self.get("id"))

    def get(self, field: str) -> Any:
        if isinstance(self._raw, Mapping):
            return _canonical(self._raw.get(_camel(field)))
        return _canonical(getattr(self._raw, field, None))

    def signature(self) -> tuple[tuple[str, Any], ...]:
        return tuple((name, self.get(name)) for name in _COMPARED_FIELDS[self.kind])


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def _canonical(value: Any) -> Any:
    """Normalise a field value so an object and its JSON compare equal.

    Points, sizes and landings are small records on one side and dicts on the other;
    polygons are tuples of one or the other. Everything collapses to nested tuples of
    primitives, which are hashable, ordered and compare by value — so ``a`` moving by
    1 mm is a difference and ``Pt(0,0)`` vs ``{"x":0,"y":0}`` is not.
    """
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return tuple(sorted((str(k), _canonical(v)) for k, v in value.items()))
    if isinstance(value, list | tuple):
        return tuple(_canonical(item) for item in value)
    fields = getattr(value, "__dataclass_fields__", None)
    if fields is not None:
        return tuple((name, _canonical(getattr(value, name))) for name in sorted(fields))
    return value


def _elements(house: Any, kind: str) -> tuple[_Element, ...]:
    key = _JSON_KEYS[kind]
    raw = house.get(key) or () if isinstance(house, Mapping) else getattr(house, key, ()) or ()
    return tuple(_Element(item, kind) for item in raw)


def _storeys(house: Any) -> tuple[str, ...]:
    if isinstance(house, Mapping):
        return tuple(str(s["id"]) for s in (house.get("storeys") or ()))
    return tuple(str(s.id) for s in (getattr(house, "storeys", ()) or ()))


# ---------------------------------------------------------------------------
# Boxes
# ---------------------------------------------------------------------------
def _pt(value: Any) -> tuple[int, int]:
    """A model point from either shape. Integer mm, model-wide — floats are refused."""
    canonical = _canonical(value)
    if isinstance(canonical, tuple) and len(canonical) == 2:
        pairs = dict(canonical) if all(isinstance(i, tuple) for i in canonical) else None
        if pairs is not None and "x" in pairs and "y" in pairs:
            return (_int_mm(pairs["x"]), _int_mm(pairs["y"]))
        return (_int_mm(canonical[0]), _int_mm(canonical[1]))
    raise TypeError("cannot read a model point from %r" % (value,))


def _int_mm(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            "model geometry is integer millimetres; got %r (%s). Parse at the boundary."
            % (value, type(value).__name__)
        )
    return value


def _polygon_box(value: Any) -> Box | None:
    points = [_pt(item) for item in (value or ())]
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _wall_box(wall: _Element) -> Box | None:
    a = _pt(wall.get("a"))
    b = _pt(wall.get("b"))
    half = max(0, _int_mm(wall.get("thickness_mm") or 0) // 2)
    # Inflate the centreline's box by the half-thickness on both axes. For an orthogonal
    # wall that is exactly the wall rectangle; for a skewed one it is a superset, which
    # is the safe direction for something a cloud has to contain.
    return (
        min(a[0], b[0]) - half,
        min(a[1], b[1]) - half,
        max(a[0], b[0]) + half,
        max(a[1], b[1]) + half,
    )


def _opening_box(opening: _Element, walls: Mapping[str, _Element]) -> tuple[Box | None, str]:
    """The opening's footprint on its host wall, and the storey that wall is on."""
    wall = walls.get(str(opening.get("wall_id")))
    if wall is None:
        return (None, "")
    a = _pt(wall.get("a"))
    b = _pt(wall.get("b"))
    length_x = b[0] - a[0]
    length_y = b[1] - a[1]
    span = max(1, math.isqrt(length_x * length_x + length_y * length_y))
    offset = _int_mm(opening.get("offset_mm") or 0)
    half_width = _int_mm(opening.get("width_mm") or 0) // 2
    half_thick = max(0, _int_mm(wall.get("thickness_mm") or 0) // 2)
    # Centre of the opening on the wall centreline, then half its width each way along
    # the wall. Integer arithmetic throughout: the products are exact and the single
    # division is floor, which widens the box by at most a millimetre.
    lo = max(0, offset - half_width)
    hi = min(span, offset + half_width)
    p_lo = (a[0] + length_x * lo // span, a[1] + length_y * lo // span)
    p_hi = (a[0] + length_x * hi // span, a[1] + length_y * hi // span)
    box = (
        min(p_lo[0], p_hi[0]) - half_thick,
        min(p_lo[1], p_hi[1]) - half_thick,
        max(p_lo[0], p_hi[0]) + half_thick,
        max(p_lo[1], p_hi[1]) + half_thick,
    )
    return (box, str(wall.get("storey_id")))


def _stair_box(stair: _Element) -> Box | None:
    """The stair's drawn footprint: flight run x clear width, plus any landing.

    Deliberately the same construction ``reference_sheets._stair_primitives`` draws from
    (``treads = risers_count - 1``, halved when there is a landing) so the cloud contains
    the treads the reviewer can see, not an idealised rectangle. The landing is added at
    the top of the flight, in the direction of travel.
    """
    origin = _pt(stair.get("origin"))
    width = _int_mm(stair.get("width_mm") or 0)
    tread = _int_mm(stair.get("tread_mm") or 0)
    treads = max(1, _int_mm(stair.get("risers_count") or 1) - 1)
    landing = stair.get("landing")
    landing_depth = 0
    if landing:
        # A dogleg draws half its risers as the flight; the landing takes the rest of the
        # run. `landing` arrives canonicalised, so it is a tuple of pairs keyed either
        # camelCase (JSON) or snake_case (dataclass) — both spellings are read here
        # rather than in two diff implementations.
        treads = max(1, treads // 2)
        fields = dict(landing)
        landing_depth = _int_mm(fields.get("depthMm", fields.get("depth_mm", 0)))
    run = treads * tread + landing_depth
    direction = str(stair.get("direction") or "N")
    ox, oy = origin
    if direction in ("N", "S"):
        sign = 1 if direction == "N" else -1
        return (
            min(ox, ox + width),
            min(oy, oy + sign * run),
            max(ox, ox + width),
            max(oy, oy + sign * run),
        )
    sign = 1 if direction == "E" else -1
    return (
        min(ox, ox + sign * run),
        min(oy, oy + width),
        max(ox, ox + sign * run),
        max(oy, oy + width),
    )


def _column_box(column: _Element) -> Box | None:
    centre = _pt(column.get("pt"))
    size = dict(_canonical(column.get("size_mm")) or ())
    half_w = _int_mm(size.get("widthMm", size.get("width_mm", 0))) // 2
    half_d = _int_mm(size.get("depthMm", size.get("depth_mm", 0))) // 2
    return (centre[0] - half_w, centre[1] - half_d, centre[0] + half_w, centre[1] + half_d)


def _box_for(element: _Element, walls: Mapping[str, _Element]) -> tuple[Box | None, str]:
    """``(box, storey_id)`` for one element, or ``(None, "")`` when it cannot be placed."""
    kind = element.kind
    if kind == "wall":
        return (_wall_box(element), str(element.get("storey_id")))
    if kind == "opening":
        return _opening_box(element, walls)
    if kind in ("room", "balcony"):
        return (_polygon_box(element.get("polygon")), str(element.get("storey_id")))
    if kind == "stair":
        return (_stair_box(element), str(element.get("storey_id")))
    if kind == "column":
        return (_column_box(element), str(element.get("storey_id")))
    if kind == "furniture":
        # Deliberately unboxed. A furniture INSTANCE carries a point and a rotation; its
        # footprint lives in the catalogue, and this module has no business reading it.
        # Drawing a nominal square instead would put a shape on a compare overlay that
        # is not the shape of the thing — so a moved sofa is reported and counted as
        # changed-but-unplaced, which is true, rather than clouded at the wrong size.
        return (None, str(element.get("storey_id")))
    raise KeyError("no box rule for element kind %r" % kind)


# ---------------------------------------------------------------------------
# The diff
# ---------------------------------------------------------------------------
#: The wider set a VERSION COMPARE asks for (C-8). Comparing two design options is a
#: different question from clouding a submission sheet: an architect choosing between
#: Option A and Option B cares that the furniture layout differs, and a compare that
#: answered "no change" for two visibly different plans would be worse than none.
#: Slabs stay out for the same reason as ever — derived, and the size of the floor.
COMPARE_KINDS: tuple[str, ...] = (*COMPARED_KINDS, "furniture")


def diff_models(before: Any, after: Any, *, kinds: Sequence[str] = COMPARED_KINDS) -> ModelDiff:
    """Every element that differs between two model states, with its plan box.

    ``before`` and ``after`` are folded ``ProjectDoc``/``HouseModel`` objects or their
    JSON — the two forms are read through one accessor, so a JSON-vs-object comparison
    is meaningful rather than a diff of every field.

    ``kinds`` defaults to :data:`COMPARED_KINDS` — the revision-cloud set — so the sheet
    pipeline's behaviour is unchanged by the existence of the wider :data:`COMPARE_KINDS`.
    Passing a different set is an explicit decision at the call site, never a default.

    The result is ordered: by storey (as the *after* state lists them, ground first), then
    by ``kinds``, then by element id. Determinism matters here for the same
    reason it matters in a golden — a sheet whose clouds move between two runs of the same
    input is a sheet nobody can review.
    """
    before_house = _house_of(before)
    after_house = _house_of(after)
    before_walls = {e.id: e for e in _elements(before_house, "wall")}
    after_walls = {e.id: e for e in _elements(after_house, "wall")}

    changes: list[ChangedElement] = []
    unplaced: list[tuple[str, str, str]] = []
    for kind in kinds:
        old = {e.id: e for e in _elements(before_house, kind)}
        new = {e.id: e for e in _elements(after_house, kind)}
        for element_id in sorted(set(old) | set(new)):
            old_element = old.get(element_id)
            new_element = new.get(element_id)
            if old_element is not None and new_element is not None:
                fields = tuple(
                    name
                    for (name, was), (_, now) in zip(
                        old_element.signature(), new_element.signature(), strict=True
                    )
                    if was != now
                )
                if not fields:
                    continue
                box, storey_id = _box_for(new_element, after_walls)
                change = "modified"
                derived = bool(fields) and set(fields) <= _DERIVED_FIELDS.get(kind, frozenset())
            elif new_element is not None:
                box, storey_id = _box_for(new_element, after_walls)
                fields = ()
                change = "added"
                derived = False
            else:
                assert old_element is not None
                box, storey_id = _box_for(old_element, before_walls)
                fields = ()
                change = "removed"
                derived = False
            if box is None or not storey_id:
                # An element we cannot place cannot be clouded — an opening whose host
                # wall is gone from both states has no position on any plan. Dropping it
                # silently is the "gate that never fires" failure, so it goes on
                # `unplaced`, where the counts and the summary still see it.
                unplaced.append((element_id, kind, change))
                continue
            changes.append(
                ChangedElement(
                    element_id=element_id,
                    kind=kind,
                    change=change,
                    storey_id=storey_id,
                    box=box,
                    fields=fields,
                    derived=derived,
                )
            )

    order = {storey_id: index for index, storey_id in enumerate(_storeys(after_house))}
    for storey_id in _storeys(before_house):
        order.setdefault(storey_id, len(order))
    kind_order = {kind: index for index, kind in enumerate(COMPARED_KINDS)}
    changes.sort(
        key=lambda c: (order.get(c.storey_id, len(order)), kind_order[c.kind], c.element_id)
    )
    storey_ids = tuple(sorted(order, key=lambda s: order[s]))
    return ModelDiff(
        elements=tuple(changes), storey_ids=storey_ids, unplaced=tuple(sorted(unplaced))
    )


# ---------------------------------------------------------------------------
# Clustering — one cloud per area of change, not one per element
# ---------------------------------------------------------------------------
def merge_boxes(first: Box, second: Box) -> Box:
    return (
        min(first[0], second[0]),
        min(first[1], second[1]),
        max(first[2], second[2]),
        max(first[3], second[3]),
    )


def _overlaps(first: Box, second: Box, gap_mm: int) -> bool:
    """Do the boxes touch, once each is grown by ``gap_mm`` on every side?"""
    return not (
        first[2] + gap_mm < second[0] - gap_mm
        or second[2] + gap_mm < first[0] - gap_mm
        or first[3] + gap_mm < second[1] - gap_mm
        or second[3] + gap_mm < first[1] - gap_mm
    )


def cluster_boxes(boxes: Sequence[Box], *, gap_mm: int) -> tuple[Box, ...]:
    """Merge boxes that are within ``gap_mm`` of each other, to fixpoint.

    Moving one wall changes the wall, both rooms either side of it and the openings in
    it: five boxes on top of each other. Five concentric clouds is not a drawing anyone
    can read, so they become one cloud around the area that changed.

    The merge runs to a fixpoint rather than in one pass, because merging A into B can
    bring the result within ``gap_mm`` of C — a single pass would leave two overlapping
    clouds, which is the same unreadable sheet with extra steps.
    """
    if gap_mm < 0:
        raise ValueError("cluster gap must be >= 0, got %d" % gap_mm)
    remaining = sorted(set(boxes))
    merged: list[Box] = []
    while remaining:
        current = remaining.pop(0)
        changed = True
        while changed:
            changed = False
            keep: list[Box] = []
            for box in remaining:
                if _overlaps(current, box, gap_mm):
                    current = merge_boxes(current, box)
                    changed = True
                else:
                    keep.append(box)
            remaining = keep
        merged.append(current)
    return tuple(sorted(merged))
