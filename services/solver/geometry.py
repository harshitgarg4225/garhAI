"""Integer-millimetre polygon geometry for the solver.

SKILL.md, non-negotiable: "Geometry lives in integer millimeters… Never store floats
for lengths (floating-point drift breaks dimension chains and compliance math)."

So the rules in this module are:

* every coordinate in and out is ``int`` mm; areas are ``int`` mm²;
* floats appear only *inside* a function, where a square root or a line intersection
  genuinely needs them, and the result is rounded back with :func:`round_half_away`
  before it leaves;
* :func:`round_half_away` matches the model core's rounding contract exactly —
  ``x >= 0 ? floor(x+0.5) : -floor(-x+0.5)``. Not banker's rounding, not Python's
  ``round()``. The TypeScript ``units.ts`` and this file must agree or a plan solved in
  Python will not fold in the browser;
* orientation, area and containment tests use **exact integer arithmetic**, so they
  are decisions, not estimates.

Convention: polygons are counter-clockwise (CCW), first vertex not repeated at the
end, in plot-local coordinates (origin at the plot's SW corner, +X east, +Y north).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

#: A point in integer millimetres.
Pt = tuple[int, int]
Polygon = tuple[Pt, ...]


def round_half_away(value: float) -> int:
    """Round half away from zero — the model core's contract, mirrored."""
    if value >= 0:
        return int(math.floor(value + 0.5))
    return -int(math.floor(-value + 0.5))


def as_polygon(points: Sequence[Sequence[int]]) -> Polygon:
    """Coerce input to a tuple of integer pairs, rejecting floats loudly."""
    out: list[Pt] = []
    for index, point in enumerate(points):
        if len(point) != 2:
            raise ValueError("vertex %d must be [x, y], got %r" % (index, point))
        x, y = point[0], point[1]
        if (
            isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, int)
            or not isinstance(y, int)
        ):
            raise ValueError(
                "vertex %d is not integer millimetres: %r. Geometry is int mm everywhere "
                "(SKILL.md locked decision)." % (index, point)
            )
        out.append((x, y))
    return tuple(out)


def signed_area2(polygon: Polygon) -> int:
    """Twice the signed area, exactly. Positive ⇒ CCW."""
    total = 0
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        total += x1 * y2 - x2 * y1
    return total


def area_mm2(polygon: Polygon) -> int:
    """Absolute area in mm², rounded half away from zero.

    ``signed_area2`` is exact, so the only rounding is the final halving — and it is
    exact whenever the doubled area is even, which it is for every axis-aligned plot.
    """
    doubled = abs(signed_area2(polygon))
    return (doubled + 1) // 2


def is_ccw(polygon: Polygon) -> bool:
    return signed_area2(polygon) > 0


def ensure_ccw(polygon: Polygon) -> Polygon:
    """Canonical winding. Everything downstream assumes interior-on-the-left."""
    return polygon if is_ccw(polygon) else tuple(reversed(polygon))


def bbox(polygon: Polygon) -> tuple[int, int, int, int]:
    """``(min_x, min_y, max_x, max_y)``."""
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    return (min(xs), min(ys), max(xs), max(ys))


def centroid(polygon: Polygon) -> Pt:
    """Area centroid, rounded to integer mm. Falls back to the vertex mean if degenerate."""
    doubled = signed_area2(polygon)
    if doubled == 0:
        count = max(1, len(polygon))
        return (
            round_half_away(sum(point[0] for point in polygon) / count),
            round_half_away(sum(point[1] for point in polygon) / count),
        )
    cx = 0
    cy = 0
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return (round_half_away(cx / (3.0 * doubled)), round_half_away(cy / (3.0 * doubled)))


def perimeter_mm(polygon: Polygon) -> int:
    """Perimeter, rounded to mm. Used for the compactness penalty (§5.2 objective)."""
    total = 0.0
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        total += math.hypot(x2 - x1, y2 - y1)
    return round_half_away(total)


def edge_length_mm(a: Pt, b: Pt) -> int:
    return round_half_away(math.hypot(b[0] - a[0], b[1] - a[1]))


def dedupe_collinear(polygon: Polygon) -> Polygon:
    """Drop repeated vertices and vertices lying exactly on their neighbours' line.

    Exact integer cross-product test — a vertex is only removed when it is *provably*
    collinear, never when it is 1 mm off.
    """
    points: list[Pt] = []
    for point in polygon:
        if not points or points[-1] != point:
            points.append(point)
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    if len(points) < 3:
        return tuple(points)

    changed = True
    while changed and len(points) >= 3:
        changed = False
        index = 0
        while index < len(points) and len(points) >= 3:
            prev_point = points[index - 1]
            current = points[index]
            next_point = points[(index + 1) % len(points)]
            if _cross(prev_point, current, next_point) == 0:
                del points[index]
                changed = True
            else:
                index += 1
    return tuple(points)


def is_simple(polygon: Polygon) -> bool:
    """True when no two non-adjacent edges cross. O(n²) — plots have <20 vertices."""
    count = len(polygon)
    if count < 3:
        return False
    for i in range(count):
        a1 = polygon[i]
        a2 = polygon[(i + 1) % count]
        for j in range(i + 1, count):
            if j == i or (j + 1) % count == i or (i + 1) % count == j:
                continue
            b1 = polygon[j]
            b2 = polygon[(j + 1) % count]
            if _segments_intersect(a1, a2, b1, b2):
                return False
    return True


def point_in_polygon(point: Pt, polygon: Polygon) -> bool:
    """Inclusive point-in-polygon (a point on the boundary counts as inside)."""
    count = len(polygon)
    if count < 3:
        return False
    x, y = point
    inside = False
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if _on_segment((x1, y1), point, (x2, y2)):
            return True
        if (y1 > y) != (y2 > y):
            # Exact: compare cross products instead of computing an x intercept.
            cross = _cross((x1, y1), (x2, y2), point)
            if (cross > 0) == (y2 > y1):
                inside = not inside
    return inside


def polygon_contains_polygon(outer: Polygon, inner: Polygon) -> bool:
    """Every vertex of ``inner`` lies inside ``outer`` and no edges cross.

    Enough for the solver's use (envelope ⊆ plot) because both are simple polygons and
    the inner one is produced by inward offsetting.
    """
    if not all(point_in_polygon(point, outer) for point in inner):
        return False
    outer_count = len(outer)
    inner_count = len(inner)
    for i in range(inner_count):
        a1 = inner[i]
        a2 = inner[(i + 1) % inner_count]
        for j in range(outer_count):
            b1 = outer[j]
            b2 = outer[(j + 1) % outer_count]
            if _segments_properly_intersect(a1, a2, b1, b2):
                return False
    return True


# ---------------------------------------------------------------------------
# 3×3 plot zones — Vastu (§6) and the solver's diversity signature (§5.5)
# ---------------------------------------------------------------------------
#: Row-major from the NORTH-west, i.e. index 0 is NW as a person reads a site plan.
ZONE_NAMES: tuple[str, ...] = ("NW", "N", "NE", "W", "C", "E", "SW", "S", "SE")


def zone_for_point(point: Pt, plot_bbox: tuple[int, int, int, int], north_deg: int = 0) -> str:
    """Which of the 9 zones a point falls in, oriented to TRUE north.

    ``north_deg`` rotates true north clockwise from +Y, matching ``plot.set_north``.
    The point is rotated by ``-north_deg`` about the bbox centre before classification,
    so the zone grid is aligned with true north rather than with the drawing.

    Deliberately coarse (a 3×3 grid): this feeds Vastu scoring and diversity
    signatures, both of which are about *which third*, not about millimetres.
    """
    min_x, min_y, max_x, max_y = plot_bbox
    width = max(1, max_x - min_x)
    height = max(1, max_y - min_y)
    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0

    dx = point[0] - cx
    dy = point[1] - cy
    if north_deg % 360 != 0:
        angle = math.radians(north_deg % 360)
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        # Rotate by -north_deg (clockwise north → screen up).
        dx, dy = dx * cos_a - dy * sin_a, dx * sin_a + dy * cos_a

    col = _third((dx + width / 2.0) / width)
    row = _third((dy + height / 2.0) / height)
    # row 0 is the SOUTH third in +Y-north coordinates; flip so index 0 is north.
    return ZONE_NAMES[(2 - row) * 3 + col]


def _third(fraction: float) -> int:
    if fraction < 1.0 / 3.0:
        return 0
    if fraction < 2.0 / 3.0:
        return 1
    return 2


# ---------------------------------------------------------------------------
# exact predicates
# ---------------------------------------------------------------------------
def _cross(origin: Pt, a: Pt, b: Pt) -> int:
    """Cross product of (a-origin) × (b-origin). Sign = turn direction."""
    return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])


def _on_segment(a: Pt, point: Pt, b: Pt) -> bool:
    if _cross(a, b, point) != 0:
        return False
    return min(a[0], b[0]) <= point[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= point[1] <= max(
        a[1], b[1]
    )


def _segments_properly_intersect(a1: Pt, a2: Pt, b1: Pt, b2: Pt) -> bool:
    """Crossing at an interior point of both segments (touching does not count).

    A PROPER crossing needs all four orientation determinants strictly non-zero.
    A zero means an endpoint lies exactly on the other segment's line — the segments
    touch, or are collinear — which this predicate promises not to count.

    Testing only the sign comparisons is the whole bug this guard now carries:
    ``(d1 > 0) != (d2 > 0)`` reads a zero as "not positive", i.e. lumps it in with
    negative, so an endpoint sitting ON the other line counted as a crossing whenever
    the far endpoint was on the positive side.

    It shipped, and it took a plot with a ZERO SETBACK to expose it. Offset the
    envelope with one setback of 0 and its corner lands exactly on the plot boundary;
    ``polygon_contains_polygon`` then reported the envelope as escaping its own plot,
    and ``derive_envelope`` refused the plot with "We couldn't work out the buildable
    area for this plot shape" — while its own detail line said zero vertices were
    outside, because none were. Zero side setbacks are ordinary on small Indian plots,
    so this failed generation outright for a whole class of real sites.
    """
    d1 = _cross(b1, b2, a1)
    d2 = _cross(b1, b2, a2)
    d3 = _cross(a1, a2, b1)
    d4 = _cross(a1, a2, b2)
    if d1 == 0 or d2 == 0 or d3 == 0 or d4 == 0:
        return False
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _segments_intersect(a1: Pt, a2: Pt, b1: Pt, b2: Pt) -> bool:
    """Any intersection, including touching and collinear overlap."""
    if _segments_properly_intersect(a1, a2, b1, b2):
        return True
    return (
        _on_segment(b1, a1, b2)
        or _on_segment(b1, a2, b2)
        or _on_segment(a1, b1, a2)
        or _on_segment(a1, b2, a2)
    )


__all__ = [
    "ZONE_NAMES",
    "Polygon",
    "Pt",
    "area_mm2",
    "as_polygon",
    "bbox",
    "centroid",
    "dedupe_collinear",
    "edge_length_mm",
    "ensure_ccw",
    "is_ccw",
    "is_simple",
    "perimeter_mm",
    "point_in_polygon",
    "polygon_contains_polygon",
    "round_half_away",
    "signed_area2",
    "zone_for_point",
]
