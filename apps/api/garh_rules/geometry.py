from __future__ import annotations

"""The only geometry the rules engine is allowed to do.

The engine reads **pre-derived** scalars (``room.areaMm2``, ``leastWidthMm``,
``centroidMm``) from the EvaluationContext. That is deliberate: it is what keeps
a full compliance run inside the 100 ms budget (§14) and what lets the fixture
suite assert that a fixture cannot lie about its own geometry.

So this module holds only:

* the same three derivations, exposed so whoever builds the context (the model
  layer's ``build_context``) and the fixture verifier compute them identically;
* the 3x3 zone grid's bounding box and the rotation that orients it to true
  north (:mod:`garh_rules.zones` uses these);
* exact polygon-vs-rectangle intersection area, needed by ``brahmasthan_open``
  and by nothing else.

Everything is exact. Areas are integers or :class:`fractions.Fraction`; the only
float in the file is the ``sin``/``cos`` of a non-cardinal north bearing, and its
result is rounded half-up to whole millimetres *before* anything is classified,
so the classification itself stays reproducible (``rulepacks/README.md``).
"""

import math
from fractions import Fraction
from typing import List, Sequence, Tuple

__all__ = [
    "Point",
    "Ring",
    "polygon_area_x2",
    "polygon_area_mm2",
    "polygon_centroid_mm",
    "polygon_bbox",
    "polygon_least_width_mm",
    "clip_area_against_rect",
    "rotate_ccw_deg",
    "round_half_up_int",
]

#: An integer-millimetre point. Plot-local: origin at the plot's SW corner,
#: +X east, +Y north *of the plot*, which true north may differ from.
Point = Tuple[int, int]
Ring = Sequence[Point]


def round_half_up_int(value: float) -> int:
    """``floor(value + 0.5)``: 2.5 -> 3, -2.5 -> -2.

    The one rounding rule used for coordinates anywhere in this package, matching
    ``fixtures/rules/_tools/verify_fixtures.py``.
    """
    return math.floor(value + 0.5)


def polygon_area_x2(ring: Ring) -> int:
    """Twice the signed shoelace area — exact, integer, sign carries orientation.

    Doubled so nothing is ever divided: an odd result means the true area ends in
    ``.5 mm2``, which callers handle explicitly instead of silently truncating.
    """
    total = 0
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        total += x0 * y1 - x1 * y0
    return total


def polygon_area_mm2(ring: Ring) -> int:
    """Unsigned area in whole mm2, rounded half-up from the exact half-integer."""
    doubled = abs(polygon_area_x2(ring))
    return doubled // 2 + (doubled % 2)


def polygon_centroid_mm(ring: Ring) -> Point:
    """Area centroid, exact rational, rounded half-up to whole millimetres.

    Half-up (not banker's, not truncation) because the 3x3 zone classification
    keys off this value: two engines must agree on which cell a room sits in, and
    they only can if the rounding rule is stated. Falls back to the vertex mean
    for a degenerate (zero-area) ring rather than dividing by zero.
    """
    doubled = polygon_area_x2(ring)
    if doubled == 0:
        n = max(1, len(ring))
        return (
            round_half_up_int(sum(p[0] for p in ring) / n),
            round_half_up_int(sum(p[1] for p in ring) / n),
        )
    cx = 0
    cy = 0
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    fx = Fraction(cx, 3 * doubled)
    fy = Fraction(cy, 3 * doubled)
    return (math.floor(fx + Fraction(1, 2)), math.floor(fy + Fraction(1, 2)))


def polygon_bbox(ring: Ring) -> Tuple[int, int, int, int]:
    """``(min_x, min_y, max_x, max_y)``."""
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return (min(xs), min(ys), max(xs), max(ys))


def polygon_least_width_mm(ring: Ring) -> int:
    """Least width = the shorter side of the axis-aligned bounding box.

    The DSL says so explicitly (``check_room_width_min``): MVP is orthogonal-only,
    so the bbox is the room. A true minimum-width (rotating-calipers) measure
    would disagree on a diagonal room and must not be substituted quietly — the
    pack values were authored against this definition.
    """
    min_x, min_y, max_x, max_y = polygon_bbox(ring)
    return min(max_x - min_x, max_y - min_y)


def rotate_ccw_deg(point: Point, degrees: int) -> Point:
    """Rotate a plot-local point counter-clockwise by ``degrees``, half-up rounded.

    ``plot.northDeg`` is the plot-local bearing of TRUE north measured clockwise
    from +Y, so a vector along true north is ``(sin N, cos N)``; rotating the
    whole plot CCW by ``N`` maps that to ``(0, 1)`` — true north becomes +Y, which
    is what the 3x3 Vastu grid is defined against.

    Cardinal bearings are handled by exact integer swaps so the overwhelmingly
    common cases (0/90/180/270) never touch a transcendental function.
    """
    d = degrees % 360
    x, y = point
    if d == 0:
        return (x, y)
    if d == 90:
        return (-y, x)
    if d == 180:
        return (-x, -y)
    if d == 270:
        return (y, -x)
    rad = math.radians(d)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    return (
        round_half_up_int(x * cos_a - y * sin_a),
        round_half_up_int(x * sin_a + y * cos_a),
    )


# ---------------------------------------------------------------------------
# Exact polygon ∩ axis-aligned rectangle area
# ---------------------------------------------------------------------------

_FPoint = Tuple[Fraction, Fraction]


def _clip_half_plane(
    ring: Sequence[_FPoint], axis: int, bound: Fraction, keep_greater: bool
) -> List[_FPoint]:
    """Sutherland-Hodgman against one axis-aligned half-plane, exactly.

    Intersection parameters are rationals, so :class:`~fractions.Fraction`
    coordinates keep the clipped ring exact and the resulting area exact. That
    matters: ``brahmasthan_open`` compares ``floor(10000 * overlap / cell)``, and
    a float overlap could move that integer by one at the boundary — which is
    precisely where the fixtures sit.
    """
    if not ring:
        return []

    def inside(p: _FPoint) -> bool:
        return p[axis] >= bound if keep_greater else p[axis] <= bound

    out: List[_FPoint] = []
    n = len(ring)
    for i in range(n):
        cur = ring[i]
        nxt = ring[(i + 1) % n]
        cur_in = inside(cur)
        nxt_in = inside(nxt)
        if cur_in:
            out.append(cur)
        if cur_in != nxt_in:
            span = nxt[axis] - cur[axis]
            if span == 0:  # pragma: no cover - impossible when sides differ
                continue
            t = (bound - cur[axis]) / span
            other = 1 - axis
            crossing: List[Fraction] = [Fraction(0), Fraction(0)]
            crossing[axis] = bound
            crossing[other] = cur[other] + (nxt[other] - cur[other]) * t
            out.append((crossing[0], crossing[1]))
    return out


def clip_area_against_rect(
    ring: Ring, x0: int, y0: int, x1: int, y1: int
) -> Fraction:
    """Exact area of ``ring`` ∩ the axis-aligned rectangle ``[x0,x1] x [y0,y1]``.

    Returns 0 for a degenerate rectangle or an empty intersection. The polygon
    may be given in either winding order; the result is unsigned.
    """
    if x1 <= x0 or y1 <= y0 or len(ring) < 3:
        return Fraction(0)
    poly: List[_FPoint] = [(Fraction(p[0]), Fraction(p[1])) for p in ring]
    poly = _clip_half_plane(poly, 0, Fraction(x0), True)
    poly = _clip_half_plane(poly, 0, Fraction(x1), False)
    poly = _clip_half_plane(poly, 1, Fraction(y0), True)
    poly = _clip_half_plane(poly, 1, Fraction(y1), False)
    if len(poly) < 3:
        return Fraction(0)
    doubled = Fraction(0)
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        doubled += ax * by - bx * ay
    return abs(doubled) / 2
