"""§7 step 1 — collect wall axes per storey and cluster them by orientation.

    1. Collect wall axes per storey; cluster by orientation (H/V; MVP is
       orthogonal-only).

Three things happen here, and all three are decisions worth stating out loud.

**Non-orthogonal walls are skipped, never approximated.** A wall that is neither
horizontal nor vertical gets recorded in :attr:`StoreyPlan.skipped_walls` with a reason
and takes no part in any chain. The alternative — projecting a diagonal onto the nearest
axis — produces a dimension that is *wrong by design*, and a wrong dimension on a
municipal drawing is worse than a missing one: the contractor cannot tell it is missing.
The sheet renderer surfaces the skip list as a sheet note; the architect dimensions
those walls by hand. (Non-ortho support is v1.1, together with the solver's non-rect
envelopes.)

**Faces come from ``thickness_mm // 2``, the same rule the model uses.** ``garh_model``'s
room detection insets each face by ``half_edges[i].thickness_mm // 2`` (see
``garh_model.rooms.planar_faces``), so a 115mm partition draws as 114mm of clear space
between two rooms. This module deliberately reproduces that floor division rather than
rounding "correctly": a chain that disagrees with the lines drawn on the same sheet is a
defect, and matching the geometry is the only way to keep both honest. It is also why
**breakpoints, not lengths, are the primitive** everywhere downstream (see
:mod:`services.drawings.autodim.outer`) — differences of integers always sum exactly.

**The building line per side is resolved by occlusion, not by "the outermost wall".**
An L-shaped footprint (MVP envelopes are rect / L / T) has *two* south-facing wall runs
at different y. Sorting the external walls of one orientation outward-in and subtracting
each covered interval yields exactly the runs a person standing on that side can see —
so the openings in the recessed leg still land on that side's chain instead of vanishing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

HORIZONTAL = "horizontal"
VERTICAL = "vertical"

#: Sides in a fixed order, so two runs emit chains in the same sequence. South first
#: because that is where the entrance (and the main door chain) usually is in a plan
#: drawn north-up.
SIDE_SOUTH = "S"
SIDE_EAST = "E"
SIDE_NORTH = "N"
SIDE_WEST = "W"
SIDES: Tuple[str, ...] = (SIDE_SOUTH, SIDE_EAST, SIDE_NORTH, SIDE_WEST)

#: Which orientation of wall forms each side, and whether that side is the low or the
#: high extreme of the perpendicular axis.
_SIDE_GEOMETRY: Mapping[str, Tuple[str, int]] = {
    SIDE_SOUTH: (HORIZONTAL, -1),
    SIDE_NORTH: (HORIZONTAL, +1),
    SIDE_WEST: (VERTICAL, -1),
    SIDE_EAST: (VERTICAL, +1),
}

#: Wall kinds that form the building envelope. ``parapet`` is included because on a
#: terrace storey the parapet *is* the outline the reviewer measures.
ENVELOPE_WALL_KINDS: Tuple[str, ...] = ("external", "parapet")

SKIP_NON_ORTHOGONAL = "non-orthogonal"
SKIP_DEGENERATE = "degenerate"
SKIP_TOO_THIN = "below-min-thickness"


# ---------------------------------------------------------------------------
# Input adaptation: dataclass model OR wire JSON, one code path after this
# ---------------------------------------------------------------------------
def _field(source: Any, *names: str) -> Any:
    """Read a field from either a dataclass-ish object or a mapping.

    The engine is called from two places with two shapes: the worker holds a folded
    ``garh_model.HouseModel`` (snake_case attributes), while a golden fixture or an API
    payload is wire JSON (camelCase keys). Normalising here — once, at the boundary —
    keeps every function below single-shaped, which is what makes them worth unit
    testing.
    """
    if isinstance(source, Mapping):
        for name in names:
            if name in source:
                return source[name]
        return None
    for name in names:
        if hasattr(source, name):
            return getattr(source, name)
    return None


def _point(raw: Any) -> Tuple[int, int]:
    if raw is None:
        raise ValueError("wall endpoint is missing")
    if isinstance(raw, Mapping):
        return (int(raw["x"]), int(raw["y"]))
    if isinstance(raw, (list, tuple)):
        return (int(raw[0]), int(raw[1]))
    return (int(getattr(raw, "x")), int(getattr(raw, "y")))


def _house_of(model: Any) -> Any:
    """Accept a ``ProjectDoc``, a ``HouseModel``, or the wire JSON of either."""
    house = _field(model, "house")
    if house is not None and _field(house, "walls") is not None:
        return house
    return model


# ---------------------------------------------------------------------------
# Normalised, orthogonal-only geometry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WallAxis:
    """One orthogonal wall, reduced to an axis coordinate and a span.

    ``axis_mm`` is the constant coordinate of the centreline (y for a horizontal wall,
    x for a vertical one); ``lo_mm``/``hi_mm`` bound it along the other axis, always
    sorted so ``lo_mm < hi_mm`` regardless of which way the wall was drawn.
    """

    id: str
    storey_id: str
    orientation: str
    axis_mm: int
    lo_mm: int
    hi_mm: int
    thickness_mm: int
    kind: str
    #: The original, un-sorted endpoints — an opening's ``offset_mm`` is measured from
    #: ``a`` along the wall, so the drawing direction has to survive normalisation.
    a: Tuple[int, int]
    b: Tuple[int, int]

    @property
    def half_mm(self) -> int:
        """Half thickness, floored — ``garh_model.rooms``' inset rule, exactly."""
        return self.thickness_mm // 2

    @property
    def face_lo_mm(self) -> int:
        """The face on the low side of the axis."""
        return self.axis_mm - self.half_mm

    @property
    def face_hi_mm(self) -> int:
        return self.axis_mm + self.half_mm

    @property
    def length_mm(self) -> int:
        return self.hi_mm - self.lo_mm

    @property
    def is_envelope(self) -> bool:
        return self.kind in ENVELOPE_WALL_KINDS

    def contains_along(self, value_mm: int, *, slack_mm: int = 0) -> bool:
        return self.lo_mm - slack_mm <= value_mm <= self.hi_mm + slack_mm


@dataclass(frozen=True)
class OpeningRef:
    """An opening reduced to a position on the plan.

    ``centre_mm`` is the absolute coordinate of the opening centre *along the host
    wall's axis of travel*, which is exactly what §7 step 2 level 3 dimensions to.
    """

    id: str
    wall_id: str
    kind: str
    width_mm: int
    height_mm: int
    sill_mm: int
    tag: Optional[str]
    orientation: str
    #: Perpendicular coordinate: the host wall's centreline axis.
    axis_mm: int
    centre_mm: int

    @property
    def jamb_lo_mm(self) -> int:
        """Low-side jamb.

        ``centre - width//2``, with the high jamb at ``lo + width`` so the *printed*
        opening width is exact. For an odd width that shifts the drawn centre half a
        millimetre low — 5 microns on a 1:100 print — which is the right trade against
        printing "899" for a 900mm door.
        """
        return self.centre_mm - self.width_mm // 2

    @property
    def jamb_hi_mm(self) -> int:
        return self.jamb_lo_mm + self.width_mm


@dataclass(frozen=True)
class RoomRef:
    """A room reduced to its clear bounding box.

    MVP rooms are rectangles (the solver's stage B refines to axis-aligned rects), so
    the bbox *is* the room. A non-rectangular room still gets a bbox-based chain, which
    is why :attr:`is_rectangular` is recorded: the sheet note tells the architect which
    inner dims to check by hand.
    """

    id: str
    storey_id: str
    type: str
    name: str
    min_x_mm: int
    min_y_mm: int
    max_x_mm: int
    max_y_mm: int
    area_mm2: int
    vertex_count: int

    @property
    def width_mm(self) -> int:
        return self.max_x_mm - self.min_x_mm

    @property
    def depth_mm(self) -> int:
        return self.max_y_mm - self.min_y_mm

    @property
    def is_rectangular(self) -> bool:
        return self.vertex_count == 4

    @property
    def centre(self) -> Tuple[int, int]:
        return (
            (self.min_x_mm + self.max_x_mm) // 2,
            (self.min_y_mm + self.max_y_mm) // 2,
        )


@dataclass(frozen=True)
class SkippedWall:
    """A wall the engine refused to dimension, and why. Surfaced as a sheet note."""

    id: str
    reason: str
    detail: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {"wallId": self.id, "reason": self.reason, "detail": self.detail}


@dataclass(frozen=True)
class Extents:
    """The building's outer-face bounding box, in model mm.

    This is the "building line" §7's offsets are measured from, and the source of the
    level 1 overall dimension. Faces, not centrelines: F7-A says "dims to unfinished
    faces", and a reviewer scaling the sheet measures the outside of the masonry.
    """

    min_x_mm: int
    min_y_mm: int
    max_x_mm: int
    max_y_mm: int

    @property
    def width_mm(self) -> int:
        return self.max_x_mm - self.min_x_mm

    @property
    def depth_mm(self) -> int:
        return self.max_y_mm - self.min_y_mm

    def span_for(self, side: str) -> Tuple[int, int]:
        """The measuring interval for a side: x-range for S/N, y-range for W/E."""
        orientation, _ = _SIDE_GEOMETRY[side]
        if orientation == HORIZONTAL:
            return (self.min_x_mm, self.max_x_mm)
        return (self.min_y_mm, self.max_y_mm)

    def building_line_for(self, side: str) -> int:
        """The coordinate the side's chains are offset from."""
        if side == SIDE_SOUTH:
            return self.min_y_mm
        if side == SIDE_NORTH:
            return self.max_y_mm
        if side == SIDE_WEST:
            return self.min_x_mm
        return self.max_x_mm

    def to_json(self) -> Dict[str, int]:
        return {
            "minXMm": self.min_x_mm,
            "minYMm": self.min_y_mm,
            "maxXMm": self.max_x_mm,
            "maxYMm": self.max_y_mm,
        }


@dataclass(frozen=True)
class FacadeRun:
    """A stretch of one side that is actually visible from outside on that side."""

    side: str
    axis_mm: int
    lo_mm: int
    hi_mm: int
    wall_ids: Tuple[str, ...]


@dataclass(frozen=True)
class StoreyPlan:
    """Everything the chain builders need about one storey. Pure data, no model refs."""

    storey_id: str
    walls: Tuple[WallAxis, ...]
    openings: Tuple[OpeningRef, ...]
    rooms: Tuple[RoomRef, ...]
    extents: Optional[Extents]
    runs: Mapping[str, Tuple[FacadeRun, ...]]
    skipped_walls: Tuple[SkippedWall, ...]

    def walls_of(self, orientation: str) -> Tuple[WallAxis, ...]:
        """§7 step 1's clustering: walls grouped by orientation, id-ordered."""
        return tuple(w for w in self.walls if w.orientation == orientation)

    def wall_by_id(self, wall_id: str) -> Optional[WallAxis]:
        for wall in self.walls:
            if wall.id == wall_id:
                return wall
        return None

    def openings_on(self, wall_ids: Sequence[str]) -> Tuple[OpeningRef, ...]:
        wanted = set(wall_ids)
        return tuple(o for o in self.openings if o.wall_id in wanted)


# ---------------------------------------------------------------------------
# Step 1
# ---------------------------------------------------------------------------
def collect_wall_axes(
    model: Any,
    storey_id: str,
    *,
    min_thickness_mm: int = 50,
) -> Tuple[Tuple[WallAxis, ...], Tuple[SkippedWall, ...]]:
    """§7 step 1. Returns ``(orthogonal walls, skipped walls)``, both id-sorted.

    Pure: the same model and storey always produce the same tuples in the same order,
    which is the precondition for everything downstream being deterministic.
    """
    house = _house_of(model)
    raw_walls = _field(house, "walls") or ()
    kept: List[WallAxis] = []
    skipped: List[SkippedWall] = []

    for raw in raw_walls:
        wall_storey = _field(raw, "storey_id", "storeyId")
        if str(wall_storey) != storey_id:
            continue
        wall_id = str(_field(raw, "id"))
        ax, ay = _point(_field(raw, "a"))
        bx, by = _point(_field(raw, "b"))
        thickness = int(_field(raw, "thickness_mm", "thicknessMm") or 0)
        kind = str(_field(raw, "kind") or "internal")

        if ax == bx and ay == by:
            skipped.append(SkippedWall(wall_id, SKIP_DEGENERATE, "zero-length wall"))
            continue
        if thickness < min_thickness_mm:
            skipped.append(
                SkippedWall(wall_id, SKIP_TOO_THIN, "thickness %dmm" % thickness)
            )
            continue

        if ay == by:
            orientation, axis, lo, hi = HORIZONTAL, ay, min(ax, bx), max(ax, bx)
        elif ax == bx:
            orientation, axis, lo, hi = VERTICAL, ax, min(ay, by), max(ay, by)
        else:
            skipped.append(
                SkippedWall(
                    wall_id,
                    SKIP_NON_ORTHOGONAL,
                    "(%d,%d)->(%d,%d): dimension this wall by hand" % (ax, ay, bx, by),
                )
            )
            continue

        kept.append(
            WallAxis(
                id=wall_id,
                storey_id=storey_id,
                orientation=orientation,
                axis_mm=axis,
                lo_mm=lo,
                hi_mm=hi,
                thickness_mm=thickness,
                kind=kind,
                a=(ax, ay),
                b=(bx, by),
            )
        )

    kept.sort(key=lambda w: (w.orientation, w.axis_mm, w.lo_mm, w.id))
    skipped.sort(key=lambda s: (s.reason, s.id))
    return tuple(kept), tuple(skipped)


def collect_openings(model: Any, walls: Sequence[WallAxis]) -> Tuple[OpeningRef, ...]:
    """Openings hosted by the given walls, positioned in plan coordinates.

    ``Opening.offset_mm`` is "distance along the host wall from ``wall.a`` to the
    opening CENTRE" (``garh_model.model.Opening``), so the direction the wall was drawn
    in decides the sign. Getting this backwards silently mirrors every opening on the
    facade, which is why it is computed once, here, from ``wall.a``.
    """
    house = _house_of(model)
    by_id = {wall.id: wall for wall in walls}
    out: List[OpeningRef] = []

    for raw in _field(house, "openings") or ():
        wall_id = str(_field(raw, "wall_id", "wallId"))
        wall = by_id.get(wall_id)
        if wall is None:
            continue  # another storey, or a wall the engine skipped
        offset = int(_field(raw, "offset_mm", "offsetMm") or 0)
        if wall.orientation == HORIZONTAL:
            direction = 1 if wall.b[0] >= wall.a[0] else -1
            centre = wall.a[0] + direction * offset
        else:
            direction = 1 if wall.b[1] >= wall.a[1] else -1
            centre = wall.a[1] + direction * offset
        out.append(
            OpeningRef(
                id=str(_field(raw, "id")),
                wall_id=wall_id,
                kind=str(_field(raw, "kind") or "window"),
                width_mm=int(_field(raw, "width_mm", "widthMm") or 0),
                height_mm=int(_field(raw, "height_mm", "heightMm") or 0),
                sill_mm=int(_field(raw, "sill_mm", "sillMm") or 0),
                tag=(lambda t: None if t is None else str(t))(_field(raw, "tag")),
                orientation=wall.orientation,
                axis_mm=wall.axis_mm,
                centre_mm=centre,
            )
        )

    out.sort(key=lambda o: (o.orientation, o.axis_mm, o.centre_mm, o.id))
    return tuple(out)


def collect_rooms(model: Any, storey_id: str) -> Tuple[RoomRef, ...]:
    """Rooms of one storey as clear bounding boxes, ordered south-west to north-east.

    The ordering is the reading order of a plan and it is what makes inner-chain
    de-duplication deterministic: when two rooms across a shared wall produce the same
    chain, the *first* one keeps it (see :mod:`services.drawings.autodim.inner`).
    """
    house = _house_of(model)
    out: List[RoomRef] = []
    for raw in _field(house, "rooms") or ():
        if str(_field(raw, "storey_id", "storeyId")) != storey_id:
            continue
        polygon = [_point(p) for p in (_field(raw, "polygon") or ())]
        if len(polygon) < 3:
            continue
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        out.append(
            RoomRef(
                id=str(_field(raw, "id")),
                storey_id=storey_id,
                type=str(_field(raw, "type") or "unassigned"),
                name=str(_field(raw, "name") or ""),
                min_x_mm=min(xs),
                min_y_mm=min(ys),
                max_x_mm=max(xs),
                max_y_mm=max(ys),
                area_mm2=int(_field(raw, "area_mm2", "areaMm2") or 0),
                vertex_count=len(polygon),
            )
        )
    out.sort(key=lambda r: (r.min_y_mm, r.min_x_mm, r.id))
    return tuple(out)


# ---------------------------------------------------------------------------
# Extents and per-side facade runs
# ---------------------------------------------------------------------------
def _envelope_walls(walls: Sequence[WallAxis]) -> Tuple[WallAxis, ...]:
    """External/parapet walls, or every wall when a fixture marks none as external.

    A partial plan (one room drawn in the editor, all walls "internal") still has to
    dimension, so the fallback is deliberate rather than an error.
    """
    envelope = tuple(w for w in walls if w.is_envelope)
    return envelope or tuple(walls)


def compute_extents(walls: Sequence[WallAxis]) -> Optional[Extents]:
    """Outer-face bounding box of the envelope. ``None`` for an empty storey.

    Each extreme is pushed out by the half-thickness of the walls that actually sit on
    it, so a 230mm external wall on a centreline at x=1200 gives an outer face at 1085.
    """
    envelope = _envelope_walls(walls)
    if not envelope:
        return None

    horizontals = [w for w in envelope if w.orientation == HORIZONTAL]
    verticals = [w for w in envelope if w.orientation == VERTICAL]

    def extreme(group: Sequence[WallAxis], *, lowest: bool) -> Optional[int]:
        if not group:
            return None
        axis = min(w.axis_mm for w in group) if lowest else max(w.axis_mm for w in group)
        half = max(w.half_mm for w in group if w.axis_mm == axis)
        return axis - half if lowest else axis + half

    # Cross-axis walls also bound the building: a plan of two parallel walls has no
    # perpendicular envelope, so fall back to the centreline span of everything.
    min_y = extreme(horizontals, lowest=True)
    max_y = extreme(horizontals, lowest=False)
    min_x = extreme(verticals, lowest=True)
    max_x = extreme(verticals, lowest=False)

    if min_x is None or max_x is None:
        spans = [w.lo_mm for w in envelope] + [w.hi_mm for w in envelope]
        min_x, max_x = min(spans), max(spans)
    if min_y is None or max_y is None:
        spans = [w.lo_mm for w in envelope] + [w.hi_mm for w in envelope]
        min_y, max_y = min(spans), max(spans)

    if min_x >= max_x or min_y >= max_y:
        return None
    return Extents(min_x_mm=min_x, min_y_mm=min_y, max_x_mm=max_x, max_y_mm=max_y)


def _subtract_covered(
    span: Tuple[int, int], covered: Sequence[Tuple[int, int]]
) -> List[Tuple[int, int]]:
    """``span`` minus every interval in ``covered``. Integer 1D interval arithmetic."""
    pieces = [span]
    for lo, hi in covered:
        nxt: List[Tuple[int, int]] = []
        for piece_lo, piece_hi in pieces:
            if hi <= piece_lo or lo >= piece_hi:
                nxt.append((piece_lo, piece_hi))
                continue
            if lo > piece_lo:
                nxt.append((piece_lo, min(lo, piece_hi)))
            if hi < piece_hi:
                nxt.append((max(hi, piece_lo), piece_hi))
        pieces = nxt
        if not pieces:
            break
    return [(lo, hi) for lo, hi in pieces if hi > lo]


def facade_runs(walls: Sequence[WallAxis], side: str) -> Tuple[FacadeRun, ...]:
    """The visible wall runs on one side, outermost first.

    Sweeps the envelope walls of the matching orientation from the outside in, keeping
    only the part of each wall that nothing already covers. On a rectangle this returns
    a single run; on an L it returns the front leg *and* the recessed leg, which is what
    puts the recessed leg's windows on the right chain.
    """
    orientation, direction = _SIDE_GEOMETRY[side]
    candidates = [
        w for w in _envelope_walls(walls) if w.orientation == orientation
    ]
    if not candidates:
        return ()

    # Outward-in: for a low side (S/W) the outermost wall has the *smallest* axis, so
    # sweep ascending; for a high side (N/E), descending. Ties broken by span then id so
    # the sweep is deterministic.
    outward = -1 if direction < 0 else 1  # multiplier that makes "outermost" smallest
    candidates.sort(key=lambda w: (outward * -w.axis_mm, w.lo_mm, w.hi_mm, w.id))

    covered: List[Tuple[int, int]] = []
    runs: List[FacadeRun] = []
    for wall in candidates:
        visible = _subtract_covered((wall.lo_mm, wall.hi_mm), covered)
        covered.append((wall.lo_mm, wall.hi_mm))
        for lo, hi in visible:
            runs.append(
                FacadeRun(side=side, axis_mm=wall.axis_mm, lo_mm=lo, hi_mm=hi,
                          wall_ids=(wall.id,))
            )

    # Merge collinear touching runs so a facade built from three collinear walls reads
    # as one run (and therefore one set of L2 breakpoints).
    runs.sort(key=lambda r: (r.axis_mm, r.lo_mm, r.hi_mm))
    merged: List[FacadeRun] = []
    for run in runs:
        if merged:
            last = merged[-1]
            if last.axis_mm == run.axis_mm and run.lo_mm <= last.hi_mm:
                merged[-1] = FacadeRun(
                    side=side,
                    axis_mm=last.axis_mm,
                    lo_mm=last.lo_mm,
                    hi_mm=max(last.hi_mm, run.hi_mm),
                    wall_ids=tuple(sorted(set(last.wall_ids + run.wall_ids))),
                )
                continue
        merged.append(run)

    outermost_first = direction < 0
    merged.sort(key=lambda r: (r.axis_mm if outermost_first else -r.axis_mm, r.lo_mm))
    return tuple(merged)


def build_storey_plan(
    model: Any,
    storey_id: str,
    *,
    min_thickness_mm: int = 50,
) -> StoreyPlan:
    """§7 step 1, end to end: the input every chain builder shares."""
    walls, skipped = collect_wall_axes(
        model, storey_id, min_thickness_mm=min_thickness_mm
    )
    return StoreyPlan(
        storey_id=storey_id,
        walls=walls,
        openings=collect_openings(model, walls),
        rooms=collect_rooms(model, storey_id),
        extents=compute_extents(walls),
        runs={side: facade_runs(walls, side) for side in SIDES},
        skipped_walls=skipped,
    )


def storey_ids(model: Any) -> Tuple[str, ...]:
    """Every storey id in the model, in model order (ground first)."""
    house = _house_of(model)
    return tuple(str(_field(s, "id")) for s in _field(house, "storeys") or ())


__all__ = [
    "ENVELOPE_WALL_KINDS",
    "HORIZONTAL",
    "SIDES",
    "SIDE_EAST",
    "SIDE_NORTH",
    "SIDE_SOUTH",
    "SIDE_WEST",
    "SKIP_DEGENERATE",
    "SKIP_NON_ORTHOGONAL",
    "SKIP_TOO_THIN",
    "VERTICAL",
    "Extents",
    "FacadeRun",
    "OpeningRef",
    "RoomRef",
    "SkippedWall",
    "StoreyPlan",
    "WallAxis",
    "build_storey_plan",
    "collect_openings",
    "collect_rooms",
    "collect_wall_axes",
    "compute_extents",
    "facade_runs",
    "storey_ids",
]
