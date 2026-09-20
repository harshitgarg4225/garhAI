"""Car spaces measured off the model — the geometry behind ``parking_min``.

Until 2026-09 every city pack's parking rule passed on a number the architect typed
into the brief (``carParking``) while its message said "{actual} car space(s) are
shown". Nothing was shown. This module is what makes the message true: a car space
is a ``parking-bay`` furniture instance (``fixtures/catalog/furniture.json``, a
2 500 × 5 000 mm rectangle — the size every seeded pack's ``spaceSizeMm`` names)
placed on the ground storey, and the rules engine counts only the bays that are

* **inside the plot** — all four corners on or within the boundary;
* **clear of the house** — a bay may overlap a ``garage``/``stilt``/``porch`` room
  (covered parking) but not a bedroom;
* **not stacked on another bay** — two rectangles on the same ground are one space;
* **reachable from a road** — the straight run from the bay to a road edge crosses
  no room of the house. The run is the axis-aligned box between the bay and the
  edge, so a bay in the front yard is reachable and a bay in the back yard behind
  the building is not, whatever the brief says.

All of it is exact integer arithmetic on axis-aligned rectangles and rectilinear
room polygons; nothing here is approximate, because the count goes into a
compliance number (``garh_model.geometry.polygon_intersection_area_mm2`` is
explicitly not for that). Plot edges that are not axis-aligned cannot host a
reach test and a bay that only reaches such an edge is reported unreachable — the
honest answer for a skewed frontage until the site plan can draw one.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "PARKING_BAY_CATALOG_ID",
    "GARAGE_ROOM_TYPES",
    "bay_footprint_mm",
    "catalog_bay_size_mm",
    "catalog_furniture_items",
    "measure_parking_spaces",
]

#: The catalogue id a car space is drawn as. The solver places it, the site plan
#: draws a car in it, and the rules engine counts it.
PARKING_BAY_CATALOG_ID = "parking-bay"

#: Rooms a bay may sit inside: covered parking is still parking.
GARAGE_ROOM_TYPES = frozenset({"garage", "stilt", "porch", "parking"})

Point = tuple[int, int]
Rect = tuple[int, int, int, int]  # x1, y1, x2, y2 — half-open, integer mm


def bay_footprint_mm(catalog: Sequence[Mapping[str, Any]]) -> tuple[int, int] | None:
    """``(widthMm, depthMm)`` of the bay entry, or ``None`` when the catalogue lacks it."""
    for item in catalog:
        if str(item.get("id")) == PARKING_BAY_CATALOG_ID:
            try:
                return (int(item["widthMm"]), int(item["depthMm"]))
            except (KeyError, TypeError, ValueError):
                return None
    return None


#: Where the served catalogue lives, in the router's own precedence order. Kept
#: here rather than imported from ``garh_api.routers.catalog`` for one hard
#: reason: ``make bare`` runs the rule fixtures on an interpreter with no FastAPI,
#: and importing the router to read a JSON file made the dependency-free gate fail
#: (caught by ``make bare``, which is exactly what it is for). The NUMBERS still
#: come from one file; only the path logic is duplicated, and
#: ``test_parking_measured`` asserts the two agree.
def catalog_furniture_items() -> tuple[Mapping[str, Any], ...]:
    """The served furniture catalogue as plain dicts. Pure stdlib, never raises.

    Delegates to :mod:`garh_api.catalog_data`, which is the module the catalogue
    route answers from, so the bay the drawings service draws, the bay the rule
    measures and the bay the canvas shows are one entry read one way — including
    the built-in table when no files are on disk, which a second reader of its own
    would miss and report as "no bay in the catalogue".
    """
    from garh_api.catalog_data import load_catalog

    _source, items = load_catalog("furniture")
    return tuple(item for item in items if isinstance(item, Mapping))


def catalog_bay_size_mm() -> tuple[int, int] | None:
    """``(widthMm, depthMm)`` of the catalogue's parking bay, read from disk."""
    return bay_footprint_mm(catalog_furniture_items())


# ---------------------------------------------------------------------------
# exact rectangle / polygon predicates
# ---------------------------------------------------------------------------


def _round_half_away(value: float) -> int:
    return int(math.floor(abs(value) + 0.5)) * (1 if value >= 0 else -1)


def _rect_of(centre: Point, width_mm: int, depth_mm: int, rotation_deg: int) -> Rect:
    """The bay's axis-aligned footprint. Quarter turns swap the sides exactly; any
    other angle takes the rotated rectangle's bounding box (rounded half-away)."""
    cx, cy = centre
    turn = rotation_deg % 360
    if turn % 180 == 0:
        w, d = width_mm, depth_mm
    elif turn % 90 == 0:
        w, d = depth_mm, width_mm
    else:
        rad = math.radians(turn)
        c, s = abs(math.cos(rad)), abs(math.sin(rad))
        w = _round_half_away(width_mm * c + depth_mm * s)
        d = _round_half_away(width_mm * s + depth_mm * c)
    x1 = cx - w // 2
    y1 = cy - d // 2
    return (x1, y1, x1 + w, y1 + d)


def _rects_overlap(a: Rect, b: Rect) -> bool:
    """Strict interior overlap — sharing an edge is not overlapping."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _cross(o: Point, a: Point, b: Point) -> int:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _on_segment(a: Point, p: Point, b: Point) -> bool:
    return (
        _cross(a, p, b) == 0
        and min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
        and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])
    )


def _point_in_or_on(p: Point, ring: Sequence[Point]) -> bool:
    """Ray-cast point-in-polygon, inclusive of the boundary. Exact integers."""
    count = len(ring)
    inside = False
    for i in range(count):
        a = ring[i]
        b = ring[(i + 1) % count]
        if _on_segment(a, p, b):
            return True
        if (a[1] > p[1]) != (b[1] > p[1]):
            # x of the edge at height p.y, compared without division:
            # x_edge = a.x + (p.y - a.y) * (b.x - a.x) / (b.y - a.y)
            num = (p[1] - a[1]) * (b[0] - a[0])
            den = b[1] - a[1]
            # p.x < x_edge  <=>  (p.x - a.x) * den < num   (sign of den matters)
            lhs = (p[0] - a[0]) * den
            if (lhs < num) if den > 0 else (lhs > num):
                inside = not inside
    return inside


def _point_strictly_inside(p: Point, ring: Sequence[Point]) -> bool:
    count = len(ring)
    for i in range(count):
        if _on_segment(ring[i], p, ring[(i + 1) % count]):
            return False
    return _point_in_or_on(p, ring)


def _segments_cross(a1: Point, a2: Point, b1: Point, b2: Point) -> bool:
    """Proper crossing (interiors intersect); touching at an endpoint does not count."""
    d1 = _cross(b1, b2, a1)
    d2 = _cross(b1, b2, a2)
    d3 = _cross(a1, a2, b1)
    d4 = _cross(a1, a2, b2)
    return (
        ((d1 > 0) != (d2 > 0))
        and ((d3 > 0) != (d4 > 0))
        and d1 != 0
        and d2 != 0
        and d3 != 0
        and d4 != 0
    )


def _rect_corners(rect: Rect) -> tuple[Point, Point, Point, Point]:
    x1, y1, x2, y2 = rect
    return ((x1, y1), (x2, y1), (x2, y2), (x1, y2))


def _rect_overlaps_ring(rect: Rect, ring: Sequence[Point]) -> bool:
    """Does the rectangle's interior meet the polygon's interior? Exact.

    True when a polygon vertex lies strictly inside the rectangle, a rectangle
    corner lies strictly inside the polygon, or an edge of one properly crosses an
    edge of the other. Touching along an edge is not overlap — a bay parked hard
    against the front wall is clear of the house.
    """
    if len(ring) < 3 or rect[0] >= rect[2] or rect[1] >= rect[3]:
        return False
    x1, y1, x2, y2 = rect
    for p in ring:
        if x1 < p[0] < x2 and y1 < p[1] < y2:
            return True
    corners = _rect_corners(rect)
    for c in corners:
        if _point_strictly_inside(c, ring):
            return True
    count = len(ring)
    for i in range(count):
        a, b = ring[i], ring[(i + 1) % count]
        for j in range(4):
            if _segments_cross(a, b, corners[j], corners[(j + 1) % 4]):
                return True
    # A rectangle centred in a polygon with no crossings and no interior vertices
    # is either fully inside (caught by the corner test) or fully outside.
    return False


def _rect_inside_ring(rect: Rect, ring: Sequence[Point]) -> bool:
    if not all(_point_in_or_on(c, ring) for c in _rect_corners(rect)):
        return False
    # Corners inside a concave plot could still leave an edge outside; one edge
    # crossing anywhere means the rectangle is not contained.
    corners = _rect_corners(rect)
    count = len(ring)
    for i in range(count):
        a, b = ring[i], ring[(i + 1) % count]
        for j in range(4):
            if _segments_cross(a, b, corners[j], corners[(j + 1) % 4]):
                return False
    return True


def _approach_box(rect: Rect, edge_a: Point, edge_b: Point) -> Rect | None:
    """The straight run from the bay to an axis-aligned plot edge, as a box.

    Horizontal edge (``y`` constant): the box spans the bay's x-range from the bay's
    near side to the edge line. Vertical edge likewise in x. ``None`` when the edge
    is slanted, when the bay does not lie within the edge's extent, or when the bay
    already straddles the edge line (it is then outside the plot anyway).
    """
    x1, y1, x2, y2 = rect
    if edge_a[1] == edge_b[1]:
        line = edge_a[1]
        lo, hi = sorted((edge_a[0], edge_b[0]))
        if x1 < lo or x2 > hi:
            return None
        if line <= y1:
            return (x1, line, x2, y1)
        if line >= y2:
            return (x1, y2, x2, line)
        return None
    if edge_a[0] == edge_b[0]:
        line = edge_a[0]
        lo, hi = sorted((edge_a[1], edge_b[1]))
        if y1 < lo or y2 > hi:
            return None
        if line <= x1:
            return (line, y1, x1, y2)
        if line >= x2:
            return (x2, y1, line, y2)
        return None
    return None


# ---------------------------------------------------------------------------
# the measurement
# ---------------------------------------------------------------------------


def measure_parking_spaces(
    *,
    furniture: Sequence[Mapping[str, Any]],
    boundary: Sequence[Point],
    road_edge_indexes: Sequence[int],
    ground_storey_id: str | None,
    ground_rooms: Sequence[tuple[str, Sequence[Point]]],
    bay_size_mm: tuple[int, int],
) -> list[dict[str, Any]]:
    """Every ``parking-bay`` in the model as a ``model.parkingSpaces`` row.

    ``ground_rooms`` are ``(roomType, polygon)`` pairs on the ground storey. The
    returned rows are in furniture order; ``reachable`` is False for a bay that is
    off the ground storey, outside the plot, inside a room that is not a garage,
    stacked on an earlier bay, or cut off from every road edge by the house.
    """
    ring = [(int(p[0]), int(p[1])) for p in boundary]
    house_rings = [
        (str(kind), [(int(p[0]), int(p[1])) for p in poly]) for kind, poly in ground_rooms
    ]
    blocking = [poly for kind, poly in house_rings if kind not in GARAGE_ROOM_TYPES]
    road_edges: list[tuple[Point, Point]] = []
    for index in road_edge_indexes:
        if 0 <= index < len(ring) and len(ring) >= 3:
            road_edges.append((ring[index], ring[(index + 1) % len(ring)]))

    rows: list[dict[str, Any]] = []
    accepted: list[Rect] = []
    for item in furniture:
        if str(item.get("catalogId")) != PARKING_BAY_CATALOG_ID:
            continue
        pt = item.get("pt") or {}
        try:
            centre = (int(pt["x"]), int(pt["y"]))
        except (KeyError, TypeError, ValueError):
            continue
        rect = _rect_of(centre, bay_size_mm[0], bay_size_mm[1], int(item.get("rotationDeg") or 0))
        width = rect[2] - rect[0]
        length = rect[3] - rect[1]
        on_ground = ground_storey_id is not None and str(item.get("storeyId")) == ground_storey_id
        in_plot = len(ring) >= 3 and _rect_inside_ring(rect, ring)
        clear_of_house = not any(_rect_overlaps_ring(rect, poly) for poly in blocking)
        stacked = any(_rects_overlap(rect, other) for other in accepted)
        reachable = False
        if on_ground and in_plot and clear_of_house and not stacked:
            for edge_a, edge_b in road_edges:
                box = _approach_box(rect, edge_a, edge_b)
                if box is None:
                    continue
                if box[0] >= box[2] or box[1] >= box[3]:
                    reachable = True  # the bay sits on the road edge itself
                    break
                if not any(_rect_overlaps_ring(box, poly) for poly in blocking):
                    reachable = True
                    break
        if reachable:
            accepted.append(rect)
        rows.append(
            {
                "id": str(item.get("id")),
                "storeyId": str(item.get("storeyId")),
                "polygonMm": [list(c) for c in _rect_corners(rect)],
                "widthMm": width,
                "lengthMm": length,
                "reachable": reachable,
            }
        )
    return rows
