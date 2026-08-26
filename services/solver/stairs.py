"""§5.2 "stairs first" — 3–6 stair anchor candidates + NBC-sized dogleg wells.

**ortools-free.** The stair is the single most consequential decision in a plan
(§5.2 solves once per candidate and keeps the best), so candidates are enumerated
here as pure geometry that a bare interpreter can prove:

* **near the entry edge** — the plot edge with the widest road (tie → the edge whose
  role is ``front``, tie → lowest index) names an entry side; candidates on that side
  rank first;
* **envelope-edge-adjacent** — every candidate well sits flush against an envelope
  boundary edge, long side parallel to it (a free-floating stair would orphan its
  structure and eat daylight frontage);
* **repeatable across floors** — the well is one fixed rectangle used verbatim on
  every storey (stage A pins it, §5.2 multi-floor fixes it before solving uppers);
* **dogleg default sized from the NBC pack** — riser ≤ ``nbc.stair.riser.max``,
  tread ≥ ``nbc.stair.tread.min``, flight width ≥ ``nbc.stair.width.min`` — READ
  FROM THE PACK, never hard-coded — against the storey height (3000mm, the
  ``garh_model`` storey default, unless the caller says otherwise).

The returned :class:`~services.solver.types.StairAnchor` is deliberately the
pipeline's own slim type (checkpoints serialise it field-for-field);
:func:`well_rect_for` re-derives the exact well rectangle from an anchor
deterministically, so stage A and a resumed job always agree on the geometry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from services.solver.geometry import bbox, point_in_polygon, zone_for_point
from services.solver.program import DEFAULT_STOREY_HEIGHT_MM, load_vastu_zone_rules
from services.solver.types import (
    COARSE_MODULE_MM,
    BuildableEnvelope,
    SolveParams,
    StairAnchor,
)

#: §5.2 says "3–6 stair anchor candidates".
MIN_CANDIDATES = 3
MAX_CANDIDATES = 6

#: Outward side of an axis-aligned envelope edge, from its direction on a CCW ring:
#: interior is to the LEFT of a directed edge, so outward is its right normal.
_OUTWARD_BY_DIRECTION: Dict[Tuple[int, int], str] = {
    (1, 0): "S",  # heading east → outward -Y
    (-1, 0): "N",
    (0, 1): "E",  # heading north → outward +X
    (0, -1): "W",
}


class StairError(ValueError):
    """A storey height no legal stair can serve. Typed, actionable."""

    def __init__(self, code: str, message: str, *, detail: Optional[str] = None) -> None:
        super().__init__("%s — %s" % (code, message))
        self.code = code
        self.message = message
        self.detail = detail


# ---------------------------------------------------------------------------
# NBC stair limits — from the pack, cached
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NbcStairLimits:
    """Riser/tread/width/headroom bounds out of ``nbc-core``. Values live in the pack."""

    riser_max_mm: int
    tread_min_mm: int
    width_min_mm: int
    headroom_min_mm: int


_LIMITS_CACHE: Dict[str, NbcStairLimits] = {}


def load_stair_limits(root: Optional[str] = None) -> NbcStairLimits:
    import os

    key = os.path.abspath(root) if root else "<default>"
    cached = _LIMITS_CACHE.get(key)
    if cached is not None:
        return cached
    # Same import-path convention as services/solver/program.py.
    from services.solver.program import _ensure_apps_api_on_path

    _ensure_apps_api_on_path()
    from garh_rules.packs import load_pack_set

    packs = load_pack_set(("nbc-core",), root=root)
    limits = NbcStairLimits(
        riser_max_mm=packs.require_rule("nbc.stair.riser.max").check.int_param("valueMm"),
        tread_min_mm=packs.require_rule("nbc.stair.tread.min").check.int_param("valueMm"),
        width_min_mm=packs.require_rule("nbc.stair.width.min").check.int_param("valueMm"),
        headroom_min_mm=packs.require_rule("nbc.stair.headroom.min").check.int_param("valueMm"),
    )
    _LIMITS_CACHE[key] = limits
    return limits


# ---------------------------------------------------------------------------
# dogleg sizing — exact integers, conservative rounding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DoglegStair:
    """A default two-flight (dogleg) stair, §5.2's stair shape for Indian homes.

    ``well_width_mm`` runs across the two flights (2 × flight width, they run side
    by side); ``well_depth_mm`` runs along a flight: the longer flight's going plus
    a landing one flight-width deep. Arrival clearance at the foot belongs to the
    circulation room in front of the well, not to the well itself.
    """

    floor_to_floor_mm: int
    riser_mm: int
    riser_count: int
    tread_mm: int
    flight_width_mm: int
    well_width_mm: int
    well_depth_mm: int

    def well_cells(self, module_mm: int = COARSE_MODULE_MM) -> Tuple[int, int]:
        """(width, depth) snapped UP to whole grid cells — a well may not shrink."""
        return (_ceil_div(self.well_width_mm, module_mm), _ceil_div(self.well_depth_mm, module_mm))


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def size_dogleg(
    floor_to_floor_mm: int = DEFAULT_STOREY_HEIGHT_MM,
    limits: Optional[NbcStairLimits] = None,
    *,
    flight_width_mm: Optional[int] = None,
    root: Optional[str] = None,
) -> DoglegStair:
    """Size the default dogleg from the NBC bounds and the storey height.

    All integer arithmetic, rounded AGAINST the design: riser count rounds up (so no
    riser exceeds the pack maximum), the going uses the *longer* flight, and the well
    only ever snaps upward. ``ceil(h / ceil(h / riser_max)) <= riser_max`` always
    holds, so the sized riser is legal by construction.
    """
    if limits is None:
        limits = load_stair_limits(root)
    if floor_to_floor_mm <= 0:
        raise StairError(
            "BAD_HEIGHT", "Storey height must be positive.", detail=str(floor_to_floor_mm)
        )
    riser_count = _ceil_div(floor_to_floor_mm, limits.riser_max_mm)
    if riser_count < 2:
        riser_count = 2  # a one-riser "stair" is a step; dogleg needs two flights
    riser_mm = _ceil_div(floor_to_floor_mm, riser_count)
    flight_up = _ceil_div(riser_count, 2)  # the longer flight
    treads_longer_flight = flight_up - 1
    width = max(limits.width_min_mm, flight_width_mm or 0)
    going_mm = treads_longer_flight * limits.tread_min_mm
    return DoglegStair(
        floor_to_floor_mm=floor_to_floor_mm,
        riser_mm=riser_mm,
        riser_count=riser_count,
        tread_mm=limits.tread_min_mm,
        flight_width_mm=width,
        well_width_mm=2 * width,
        well_depth_mm=going_mm + width,  # going + half-landing (one flight width deep)
    )


def snap_up_mm(value: int, module_mm: int = COARSE_MODULE_MM) -> int:
    """Smallest multiple of the module ≥ value. Never shrinks a code minimum."""
    return _ceil_div(value, module_mm) * module_mm


# ---------------------------------------------------------------------------
# entry edge / side
# ---------------------------------------------------------------------------


def entry_edge_index(params: SolveParams) -> int:
    """Which plot edge the entrance faces: widest road, tie → ``front``, tie → lowest."""
    best_index = 0
    best_rank: Optional[Tuple[int, int, int]] = None
    for edge in params.edges:
        rank = (edge.road_width_mm, 1 if edge.role == "front" else 0, -edge.index)
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best_index = edge.index
    return best_index


def entry_side(params: SolveParams) -> str:
    """Plot-local compass side ('N' = +Y) of the entry edge's outward normal.

    For a non-axis-aligned entry edge, falls back to whichever bbox side its
    midpoint is nearest — the plot may be a parallelogram even though the MVP
    *envelope* is rectilinear.
    """
    index = entry_edge_index(params)
    ring = params.plot_polygon
    count = len(ring)
    a = ring[index % count]
    b = ring[(index + 1) % count]
    direction = (_sign(b[0] - a[0]), _sign(b[1] - a[1]))
    side = _OUTWARD_BY_DIRECTION.get(direction)
    if side is not None:
        return side
    min_x, min_y, max_x, max_y = bbox(ring)
    mid = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
    distances = {
        "S": mid[1] - min_y,
        "N": max_y - mid[1],
        "W": mid[0] - min_x,
        "E": max_x - mid[0],
    }
    return min(sorted(distances), key=lambda s: distances[s])


def _sign(value: int) -> int:
    return 0 if value == 0 else (1 if value > 0 else -1)


def edge_outward_side(polygon: Sequence[Tuple[int, int]], edge_index: int) -> Optional[str]:
    """Outward plot-local side of an axis-aligned CCW polygon edge; ``None`` if slanted.

    Public because stage A uses it to know which footprint side a stair anchor pins
    (§5.2 multi-floor: the stair's flush side fixes an external wall line).
    """
    count = len(polygon)
    a = polygon[edge_index % count]
    b = polygon[(edge_index + 1) % count]
    return _OUTWARD_BY_DIRECTION.get((_sign(b[0] - a[0]), _sign(b[1] - a[1])))


# ---------------------------------------------------------------------------
# candidate enumeration
# ---------------------------------------------------------------------------


def _envelope_edges(polygon: Sequence[Tuple[int, int]]) -> List[Tuple[int, Tuple[int, int], Tuple[int, int], str]]:
    """(index, a, b, outward side) for every axis-aligned envelope edge."""
    out: List[Tuple[int, Tuple[int, int], Tuple[int, int], str]] = []
    count = len(polygon)
    for index in range(count):
        a = polygon[index]
        b = polygon[(index + 1) % count]
        direction = (_sign(b[0] - a[0]), _sign(b[1] - a[1]))
        side = _OUTWARD_BY_DIRECTION.get(direction)
        if side is not None:
            out.append((index, a, b, side))
    return out


def _well_dims_for_side(side: str, long_mm: int, short_mm: int) -> Tuple[int, int]:
    """(dx, dy) with the SHORT dimension parallel to the hugged edge.

    Perpendicular, not parallel, after first execution: the axis NORMAL to the
    hugged edge carries an external wall (230mm inset) while the along-edge axis
    is usually internal (57/58), so putting the tight 1800mm well width on the
    internal axis is what lets the stair room's CLEAR polygon still hold the
    dogleg without growing a cell. It also steals less boundary frontage from
    daylit rooms — the flight runs into the plan, not along the facade.
    """
    if side in ("N", "S"):
        return (short_mm, long_mm)
    return (long_mm, short_mm)


def well_rect_for(
    anchor: StairAnchor,
    envelope: BuildableEnvelope,
    *,
    stair: DoglegStair,
    module_mm: int = COARSE_MODULE_MM,
) -> Tuple[int, int, int, int]:
    """Re-derive the exact well rectangle from an anchor. Pure and deterministic —
    a resumed job and a fresh solve compute the identical box.

    ``anchor.edge_index`` is the ENVELOPE edge the flight runs along (the
    :class:`StairAnchor` field's documented meaning); its outward side fixes the
    orientation, ``anchor.origin`` is the box's SW corner.
    """
    ring = envelope.polygon
    count = len(ring)
    a = ring[anchor.edge_index % count]
    b = ring[(anchor.edge_index + 1) % count]
    side = _OUTWARD_BY_DIRECTION.get((_sign(b[0] - a[0]), _sign(b[1] - a[1])))
    if side is None:
        raise StairError(
            "BAD_ANCHOR",
            "Stair anchor %s names a non-axis-aligned envelope edge." % anchor.id,
            detail="edge %d of %r" % (anchor.edge_index, ring[:6]),
        )
    long_mm = snap_up_mm(stair.well_depth_mm, module_mm)
    short_mm = snap_up_mm(stair.well_width_mm, module_mm)
    dx, dy = _well_dims_for_side(side, long_mm, short_mm)
    x1, y1 = anchor.origin
    return (x1, y1, x1 + dx, y1 + dy)


def _box_inside(polygon: Sequence[Tuple[int, int]], rect: Tuple[int, int, int, int]) -> bool:
    """Corner + edge-midpoint + centre containment — sound for rectilinear envelopes
    whose features are at least one 300mm module wide (the grid guarantees that)."""
    x1, y1, x2, y2 = rect
    mx, my = (x1 + x2) // 2, (y1 + y2) // 2
    probes = (
        (x1, y1), (x2, y1), (x2, y2), (x1, y2),
        (mx, y1), (mx, y2), (x1, my), (x2, my),
        (mx, my),
    )
    return all(point_in_polygon(p, polygon) for p in probes)


def _positions_on_edge(
    a: Tuple[int, int],
    b: Tuple[int, int],
    side: str,
    dims: Tuple[int, int],
    origin: Tuple[int, int],
    module_mm: int,
) -> List[Tuple[str, Tuple[int, int]]]:
    """Flush SW-corner origins along one envelope edge: both ends and the middle,
    snapped to the module grid anchored at the envelope bbox minimum."""
    dx, dy = dims
    ox, oy = origin
    lo_x, hi_x = min(a[0], b[0]), max(a[0], b[0])
    lo_y, hi_y = min(a[1], b[1]), max(a[1], b[1])

    def snap_down(value: int, anchor_mm: int) -> int:
        return anchor_mm + ((value - anchor_mm) // module_mm) * module_mm

    def snap_to(value: int, anchor_mm: int) -> int:
        return anchor_mm + _ceil_div(value - anchor_mm, module_mm) * module_mm

    out: List[Tuple[str, Tuple[int, int]]] = []
    if side in ("S", "N"):
        y = lo_y if side == "S" else hi_y - dy
        starts = (
            ("start", snap_to(lo_x, ox)),
            ("mid", snap_down((lo_x + hi_x - dx) // 2, ox)),
            ("end", snap_down(hi_x - dx, ox)),
        )
        for label, x in starts:
            out.append((label, (x, snap_down(y, oy) if side == "N" else snap_to(y, oy))))
    else:
        x = lo_x if side == "W" else hi_x - dx
        starts = (
            ("start", snap_to(lo_y, oy)),
            ("mid", snap_down((lo_y + hi_y - dy) // 2, oy)),
            ("end", snap_down(hi_y - dy, oy)),
        )
        for label, y in starts:
            out.append((label, (snap_down(x, ox) if side == "E" else snap_to(x, ox), y)))
    return out


def _stair_allowed_zones(params: SolveParams, root: Optional[str]) -> Tuple[str, ...]:
    """The Vastu pack's allowed stair zones (advisory prior — never a hard filter)."""
    if params.vastu_mode == "off":
        return ()
    for rule in load_vastu_zone_rules(root):
        if "staircase" in rule.room_types and rule.allow:
            return rule.allow
    return ()


def enumerate_stair_candidates(
    envelope: BuildableEnvelope,
    params: SolveParams,
    *,
    limit: int = MAX_CANDIDATES,
    limits: Optional[NbcStairLimits] = None,
    floor_to_floor_mm: int = DEFAULT_STOREY_HEIGHT_MM,
    module_mm: int = COARSE_MODULE_MM,
    root: Optional[str] = None,
) -> Tuple[StairAnchor, ...]:
    """§5.2 stair candidates, best-prior first, at most ``limit`` (≤6), ≥3 whenever
    the envelope physically admits three.

    Priors (ints; higher = try first): entry-side positions rank above the far side,
    corners above midpoints (a corner well steals no daylight frontage from two
    sides), and a well whose centre sits in the Vastu pack's allowed stair zones
    gets a bonus when Vastu is on — a *prior*, never a filter: strict-mode zone
    enforcement is stage A's job, on the room the anchor pins.
    """
    stair = size_dogleg(floor_to_floor_mm, limits, root=root)
    long_mm = snap_up_mm(stair.well_depth_mm, module_mm)
    short_mm = snap_up_mm(stair.well_width_mm, module_mm)
    ring = envelope.polygon
    env_bbox = bbox(ring)
    origin = (env_bbox[0], env_bbox[1])
    plot_bbox = bbox(params.plot_polygon)
    entry = entry_side(params)
    allowed_zones = _stair_allowed_zones(params, root)

    seen: Dict[Tuple[int, int], bool] = {}
    scored: List[Tuple[int, str, Tuple[int, int], int]] = []
    for edge_index, a, b, side in _envelope_edges(ring):
        dims = _well_dims_for_side(side, long_mm, short_mm)
        for label, position in _positions_on_edge(a, b, side, dims, origin, module_mm):
            rect = (position[0], position[1], position[0] + dims[0], position[1] + dims[1])
            if not _box_inside(ring, rect):
                continue
            if position in seen:
                continue
            seen[position] = True
            prior = 40
            if side == entry:
                prior += 40
            elif side in _ADJACENT_SIDES.get(entry, ()):
                prior += 15
            if label != "mid":
                prior += 10
            if allowed_zones:
                centre = ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
                if zone_for_point(centre, plot_bbox, params.north_deg) in allowed_zones:
                    prior += 20
            scored.append((prior, "st-%s%d-%s" % (side.lower(), edge_index, label), position, edge_index))

    scored.sort(key=lambda item: (-item[0], item[2][0], item[2][1], item[1]))
    top = scored[: max(1, min(limit, MAX_CANDIDATES))]
    return tuple(
        StairAnchor(
            id=identifier,
            origin=position,
            width_mm=short_mm,
            edge_index=edge_index,
            prior=prior,
        )
        for prior, identifier, position, edge_index in top
    )


_ADJACENT_SIDES: Dict[str, Tuple[str, str]] = {
    "N": ("E", "W"),
    "S": ("E", "W"),
    "E": ("N", "S"),
    "W": ("N", "S"),
}


__all__ = [
    "MAX_CANDIDATES",
    "MIN_CANDIDATES",
    "DoglegStair",
    "NbcStairLimits",
    "StairError",
    "edge_outward_side",
    "entry_edge_index",
    "entry_side",
    "enumerate_stair_candidates",
    "load_stair_limits",
    "size_dogleg",
    "snap_up_mm",
    "well_rect_for",
]
