"""geometry.py — 2D primitives in INTEGER MILLIMETRES.

Mirror of ``packages/model/src/geometry.ts``.

EXACTNESS CONTRACT (read before using any of this):

**EXACT** (integer arithmetic only, no rounding anywhere):
    :func:`polygon_doubled_area_mm2`, :func:`polygon_area_mm2` for even doubled
    areas, :func:`orientation<polygon_orientation>`, :func:`cross`, :func:`dot`,
    :func:`point_on_segment`, :func:`point_in_polygon`, :func:`bbox`,
    :func:`polygon_is_simple`, :func:`segments_overlap_collinear`,
    :func:`union_axis_aligned_rects`, :func:`dedupe_collinear`,
    :func:`polygons_congruent`.

**ROUNDED** (rational result rounded to whole mm with round-half-away-from-zero;
deterministic and identical in TypeScript because the arithmetic is +,-,*,/ on
IEEE-754 doubles only — no transcendental functions):
    :func:`segment_intersection` (the intersection point), :func:`offset_polygon`,
    :func:`polygon_centroid`, :func:`segment_length_mm`,
    :func:`polygon_perimeter_mm`.

**APPROXIMATE** (double-precision area; used only for ranking/matching, never for
stored geometry or compliance numbers):
    :func:`polygon_intersection_area_mm2`, :func:`polygon_union_area_mm2`,
    :func:`jaccard`.

Angles are never used for ordering: :func:`compare_angle_around` is an exact
integer comparator, so half-edge traversal (:mod:`garh_model.rooms`) has no
floating-point input.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .units import round_half_away_from_zero

__all__ = [
    "Pt",
    "Seg",
    "Polygon",
    "Bbox",
    "Orientation",
    "SegIntersection",
    "Triangle",
    "pt",
    "pt_round",
    "pt_eq",
    "pt_add",
    "pt_sub",
    "pt_key",
    "pt_from_key",
    "compare_pt",
    "cross",
    "dot",
    "dist_sq_mm2",
    "dist_mm",
    "segment_length_mm",
    "segment_length_sq_mm2",
    "is_degenerate_seg",
    "point_along_seg",
    "seg_normal_offset",
    "bbox",
    "bbox_intersects",
    "bbox_contains_pt",
    "bbox_area_mm2",
    "bbox_width_mm",
    "bbox_height_mm",
    "bbox_inflate",
    "polygon_doubled_area_mm2",
    "polygon_signed_area_mm2",
    "polygon_area_mm2",
    "polygon_orientation",
    "reverse_polygon",
    "ensure_ccw",
    "polygon_centroid",
    "polygon_perimeter_mm",
    "polygon_edges",
    "point_on_segment",
    "point_in_polygon",
    "polygon_contains",
    "polygon_is_simple",
    "polygon_is_closed_ring",
    "dedupe_collinear",
    "remove_spurs",
    "polygons_congruent",
    "canonical_ring",
    "polygon_key",
    "segment_intersection",
    "collinear_overlap",
    "segments_properly_cross",
    "segments_identical",
    "segments_overlap_collinear",
    "compare_angle_around",
    "offset_polygon",
    "offset_polygon_uniform",
    "rect_polygon",
    "bbox_polygon",
    "union_axis_aligned_rects",
    "union_axis_aligned_rects_has_holes",
    "triangulate",
    "point_in_triangle",
    "polygon_intersection_area_mm2",
    "polygon_union_area_mm2",
    "jaccard",
    "containment_ratio",
]


@dataclass(frozen=True)
class Pt:
    """A point in plot-local mm. Origin = plot SW corner, +X east, +Y north."""

    x: int
    y: int


@dataclass(frozen=True)
class Seg:
    """A directed segment."""

    a: Pt
    b: Pt


#: A simple polygon: implicitly closed, NO repeated last vertex, at least 3
#: vertices. Stored counter-clockwise by convention (see :func:`ensure_ccw`).
Polygon = Sequence[Pt]


@dataclass(frozen=True)
class Bbox:
    """Axis-aligned bounding box in mm."""

    min_x: int
    min_y: int
    max_x: int
    max_y: int


#: ``'ccw' | 'cw' | 'degenerate'``
Orientation = str

#: ``'inside' | 'outside' | 'boundary'``
PointInPolygon = str

Triangle = tuple[Pt, Pt, Pt]


def _is_safe_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def pt(x: int, y: int) -> Pt:
    """Construct a point, asserting integer mm."""
    if not _is_safe_int(x) or not _is_safe_int(y):
        raise ValueError(f"Pt must be integer mm, got ({x!r}, {y!r})")
    return Pt(x, y)


def pt_round(x: float, y: float) -> Pt:
    """Round a float pair into an integer Pt (the ONLY sanctioned float->Pt door)."""
    return Pt(round_half_away_from_zero(x), round_half_away_from_zero(y))


def pt_eq(a: Pt, b: Pt) -> bool:
    return a.x == b.x and a.y == b.y


def pt_add(a: Pt, b: Pt) -> Pt:
    return Pt(a.x + b.x, a.y + b.y)


def pt_sub(a: Pt, b: Pt) -> Pt:
    return Pt(a.x - b.x, a.y - b.y)


def pt_key(p: Pt) -> str:
    """Stable string key for maps/sets — also the atom of :func:`polygon_key`."""
    return f"{p.x},{p.y}"


def pt_from_key(key: str) -> Pt:
    """Inverse of :func:`pt_key`."""
    i = key.index(",")
    return Pt(int(key[:i]), int(key[i + 1 :]))


def compare_pt(a: Pt, b: Pt) -> int:
    """Lexicographic (x, then y) comparison — the canonical vertex order."""
    if a.x != b.x:
        return -1 if a.x < b.x else 1
    if a.y != b.y:
        return -1 if a.y < b.y else 1
    return 0


def cross(a: Pt, b: Pt, c: Pt) -> int:
    """EXACT cross product (b-a) x (c-a). Sign gives turn direction."""
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def dot(a: Pt, b: Pt, c: Pt) -> int:
    """EXACT dot product (b-a) . (c-a)."""
    return (b.x - a.x) * (c.x - a.x) + (b.y - a.y) * (c.y - a.y)


def dist_sq_mm2(a: Pt, b: Pt) -> int:
    """EXACT squared distance in mm^2."""
    dx = b.x - a.x
    dy = b.y - a.y
    return dx * dx + dy * dy


def dist_mm(a: Pt, b: Pt) -> int:
    """ROUNDED distance in mm (exact for axis-aligned pairs)."""
    dx = b.x - a.x
    dy = b.y - a.y
    if dx == 0:
        return abs(dy)
    if dy == 0:
        return abs(dx)
    return round_half_away_from_zero(math.sqrt(dx * dx + dy * dy))


def segment_length_mm(s: Seg) -> int:
    """ROUNDED segment length in mm."""
    return dist_mm(s.a, s.b)


def segment_length_sq_mm2(s: Seg) -> int:
    """EXACT squared segment length."""
    return dist_sq_mm2(s.a, s.b)


def is_degenerate_seg(s: Seg) -> bool:
    """True when the segment has zero length (the ``WALL_ZERO_LENGTH`` invariant)."""
    return pt_eq(s.a, s.b)


def point_along_seg(s: Seg, along_mm: int) -> Pt:
    """Point at ``along_mm`` from ``s.a`` towards ``s.b``. ROUNDED.

    Exact for axis-aligned segments, which is every wall in the MVP.
    """
    length = segment_length_mm(s)
    if length == 0:
        return Pt(s.a.x, s.a.y)
    t = along_mm / length
    return pt_round(s.a.x + (s.b.x - s.a.x) * t, s.a.y + (s.b.y - s.a.y) * t)


def seg_normal_offset(s: Seg, length_mm: int) -> Pt:
    """Unit-ish normal (left side of a->b) scaled to ``length_mm``. ROUNDED."""
    dx = s.b.x - s.a.x
    dy = s.b.y - s.a.y
    length = math.sqrt(dx * dx + dy * dy)
    if length == 0:
        return Pt(0, 0)
    return pt_round((-dy / length) * length_mm, (dx / length) * length_mm)


# ---------------------------------------------------------------------------
# Bounding boxes
# ---------------------------------------------------------------------------


def bbox(points: Sequence[Pt]) -> Bbox:
    if len(points) == 0:
        raise ValueError("bbox of empty point list")
    min_x = points[0].x
    min_y = points[0].y
    max_x = min_x
    max_y = min_y
    for p in points[1:]:
        if p.x < min_x:
            min_x = p.x
        if p.y < min_y:
            min_y = p.y
        if p.x > max_x:
            max_x = p.x
        if p.y > max_y:
            max_y = p.y
    return Bbox(min_x, min_y, max_x, max_y)


def bbox_intersects(a: Bbox, b: Bbox) -> bool:
    return a.min_x <= b.max_x and b.min_x <= a.max_x and a.min_y <= b.max_y and b.min_y <= a.max_y


def bbox_contains_pt(b: Bbox, p: Pt) -> bool:
    return b.min_x <= p.x <= b.max_x and b.min_y <= p.y <= b.max_y


def bbox_area_mm2(b: Bbox) -> int:
    """EXACT bbox area in mm^2."""
    return (b.max_x - b.min_x) * (b.max_y - b.min_y)


def bbox_width_mm(b: Bbox) -> int:
    return b.max_x - b.min_x


def bbox_height_mm(b: Bbox) -> int:
    return b.max_y - b.min_y


def bbox_inflate(b: Bbox, mm: int) -> Bbox:
    """Grow a bbox by ``mm`` on every side."""
    return Bbox(b.min_x - mm, b.min_y - mm, b.max_x + mm, b.max_y + mm)


# ---------------------------------------------------------------------------
# Polygon basics
# ---------------------------------------------------------------------------


def polygon_doubled_area_mm2(poly: Polygon) -> int:
    """EXACT signed doubled area (shoelace sum). Positive = CCW.

    Doubled so the result stays an exact integer. Coordinates are <= ~1e6 mm
    (1 km), so each term is <= 1e12 and a 100-vertex polygon sums to <= 1e14 —
    comfortably inside ``Number.MAX_SAFE_INTEGER`` on the TypeScript side.
    """
    n = len(poly)
    if n < 3:
        return 0
    total = 0
    for i in range(n):
        p = poly[i]
        q = poly[(i + 1) % n]
        total += p.x * q.y - q.x * p.y
    return total


def polygon_signed_area_mm2(poly: Polygon) -> int:
    """EXACT signed area in mm^2 when the doubled area is even; else ROUNDED."""
    doubled = polygon_doubled_area_mm2(poly)
    if doubled % 2 == 0:
        return doubled // 2
    return round_half_away_from_zero(doubled / 2)


def polygon_area_mm2(poly: Polygon) -> int:
    """Absolute area in mm^2 (see :func:`polygon_signed_area_mm2` for exactness)."""
    return abs(polygon_signed_area_mm2(poly))


def polygon_orientation(poly: Polygon) -> Orientation:
    doubled = polygon_doubled_area_mm2(poly)
    if doubled == 0:
        return "degenerate"
    return "ccw" if doubled > 0 else "cw"


def reverse_polygon(poly: Polygon) -> list[Pt]:
    return list(reversed(list(poly)))


def ensure_ccw(poly: Polygon) -> list[Pt]:
    """Return the polygon CCW-oriented (a copy either way)."""
    return reverse_polygon(poly) if polygon_orientation(poly) == "cw" else list(poly)


def polygon_centroid(poly: Polygon) -> Pt:
    """ROUNDED centroid of the polygon AREA (not of its vertices)."""
    n = len(poly)
    if n == 0:
        raise ValueError("centroid of empty polygon")
    if n == 1:
        return Pt(poly[0].x, poly[0].y)
    doubled = polygon_doubled_area_mm2(poly)
    if doubled == 0:
        # degenerate (collinear) — fall back to the vertex mean
        sx = sum(p.x for p in poly)
        sy = sum(p.y for p in poly)
        return pt_round(sx / n, sy / n)
    cx = 0
    cy = 0
    for i in range(n):
        p = poly[i]
        q = poly[(i + 1) % n]
        f = p.x * q.y - q.x * p.y
        cx += (p.x + q.x) * f
        cy += (p.y + q.y) * f
    return pt_round(cx / (3 * doubled), cy / (3 * doubled))


def polygon_perimeter_mm(poly: Polygon) -> int:
    """ROUNDED perimeter in mm (exact for rectilinear polygons)."""
    n = len(poly)
    total = 0
    for i in range(n):
        total += dist_mm(poly[i], poly[(i + 1) % n])
    return total


def polygon_edges(poly: Polygon) -> list[Seg]:
    """Edges of a polygon as segments, in vertex order."""
    n = len(poly)
    return [Seg(poly[i], poly[(i + 1) % n]) for i in range(n)]


def point_on_segment(p: Pt, s: Seg) -> bool:
    """EXACT: does ``p`` lie on segment ``s`` (endpoints included)?"""
    if cross(s.a, s.b, p) != 0:
        return False
    return min(s.a.x, s.b.x) <= p.x <= max(s.a.x, s.b.x) and min(s.a.y, s.b.y) <= p.y <= max(
        s.a.y, s.b.y
    )


def point_in_polygon(p: Pt, poly: Polygon) -> PointInPolygon:
    """EXACT point-in-polygon (crossing number with integer predicates).

    Boundary points are reported as ``'boundary'``, never guessed.
    """
    n = len(poly)
    if n < 3:
        return "outside"
    inside = False
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        if point_on_segment(p, Seg(a, b)):
            return "boundary"
        # upward/downward crossing of the horizontal ray y = p.y going +X
        crosses = (b.y > p.y) if a.y <= p.y else (b.y <= p.y)
        if crosses:
            side = cross(a, b, p)
            # `side > 0` means p is left of a->b
            if (side > 0) if b.y > a.y else (side < 0):
                inside = not inside
    return "inside" if inside else "outside"


def polygon_contains(poly: Polygon, p: Pt) -> bool:
    """Convenience: inside OR on the boundary."""
    return point_in_polygon(p, poly) != "outside"


def polygon_is_simple(poly: Polygon) -> bool:
    """EXACT: does the polygon avoid self-intersection and repeated vertices?"""
    n = len(poly)
    if n < 3:
        return False
    seen: set[str] = set()
    for p in poly:
        k = pt_key(p)
        if k in seen:
            return False
        seen.add(k)
    edges = polygon_edges(poly)
    for i in range(n):
        for j in range(i + 1, n):
            adjacent = j == i + 1 or (i == 0 and j == n - 1)
            r = segment_intersection(edges[i], edges[j])
            if adjacent:
                # Adjacent edges legitimately touch at their shared vertex, and
                # three collinear vertices (a redundant point on a straight run)
                # are legal. Only a real overlap of non-zero length is a
                # self-intersection.
                if r.kind == "collinear":
                    overlap = r.overlap
                    assert overlap is not None
                    if not pt_eq(overlap.a, overlap.b):
                        return False
            elif r.kind != "none":
                return False
    return True


def polygon_is_closed_ring(poly: Polygon) -> bool:
    """A closed room/plot polygon per the fold invariant "rooms closed".

    At least 3 vertices, non-zero area, no self-intersections, no duplicates.
    """
    return len(poly) >= 3 and polygon_doubled_area_mm2(poly) != 0 and polygon_is_simple(poly)


def dedupe_collinear(poly: Polygon) -> list[Pt]:
    """Remove duplicate consecutive vertices and exactly-collinear vertices."""
    pts: list[Pt] = []
    for p in poly:
        if len(pts) == 0 or not pt_eq(pts[-1], p):
            pts.append(p)
    while len(pts) > 1 and pt_eq(pts[0], pts[-1]):
        pts.pop()
    if len(pts) < 3:
        return pts
    out: list[Pt] = []
    n = len(pts)
    for i in range(n):
        prev = pts[(i - 1 + n) % n]
        cur = pts[i]
        nxt = pts[(i + 1) % n]
        if cross(prev, cur, nxt) != 0:
            out.append(cur)
    # fully collinear input collapses to nothing — return the deduped points
    return out if len(out) >= 3 else pts


def remove_spurs(ring: Polygon) -> list[Pt]:
    """Remove "spurs" — vertex triples (v, w, v) produced when a planar face walk
    runs out and back along a dangling wall. Repeats until stable."""
    pts = list(ring)
    changed = True
    while changed and len(pts) >= 3:
        changed = False
        for i in range(len(pts)):
            prev = pts[(i - 1 + len(pts)) % len(pts)]
            nxt = pts[(i + 1) % len(pts)]
            if pt_eq(prev, nxt):
                drop_a = i
                drop_b = (i + 1) % len(pts)
                pts = [p for idx, p in enumerate(pts) if idx not in (drop_a, drop_b)]
                changed = True
                break
    return pts


def polygons_congruent(a: Polygon, b: Polygon) -> bool:
    """Rotation/reflection-insensitive equality of two rings."""
    if len(a) != len(b):
        return False
    n = len(a)
    if n == 0:
        return True
    for direction in (1, -1):
        for off in range(n):
            ok = True
            for i in range(n):
                j = (off + i) % n if direction == 1 else ((off - i) % n + n) % n
                if not pt_eq(a[i], b[j]):
                    ok = False
                    break
            if ok:
                return True
    return False


def canonical_ring(poly: Polygon) -> list[Pt]:
    """CANONICAL FORM of a ring, and part of the state-hash contract.

    Collinear vertices removed, counter-clockwise, rotated to start at the
    lexicographically smallest vertex. Two rings describing the same area always
    come out identical, so a room polygon does not change (and the hash does not
    move) just because the face walk started somewhere else.
    """
    ccw = ensure_ccw(dedupe_collinear(poly))
    if len(ccw) == 0:
        return []
    best = 0
    for i in range(1, len(ccw)):
        if compare_pt(ccw[i], ccw[best]) < 0:
            best = i
    return [ccw[(best + i) % len(ccw)] for i in range(len(ccw))]


def polygon_key(poly: Polygon) -> str:
    """Canonical, hash-stable key for a polygon (see :func:`canonical_ring`)."""
    return " ".join(pt_key(p) for p in canonical_ring(poly))


# ---------------------------------------------------------------------------
# Segment intersection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SegIntersection:
    """Result of :func:`segment_intersection`.

    ``kind`` is ``'none'``, ``'point'`` or ``'collinear'``; the TypeScript mirror
    uses a discriminated union, which Python spells as a tagged record.
    """

    kind: str
    point: Pt | None = None
    #: False when the crossing point had to be rounded to whole mm.
    exact: bool = False
    on_endpoint: bool = False
    #: The shared sub-segment (possibly a single point) when ``kind`` is collinear.
    overlap: Seg | None = None


_NO_INTERSECTION = SegIntersection(kind="none")


def segment_intersection(s1: Seg, s2: Seg) -> SegIntersection:
    """Segment/segment intersection.

    Classification is EXACT (integer predicates); the crossing point is ROUNDED
    to whole mm when it is not integral, and ``exact=False`` says so.
    """
    p1, p2 = s1.a, s1.b
    p3, p4 = s2.a, s2.b
    d1 = cross(p3, p4, p1)
    d2 = cross(p3, p4, p2)
    d3 = cross(p1, p2, p3)
    d4 = cross(p1, p2, p4)

    if d1 == 0 and d2 == 0:
        # collinear (or one/both degenerate)
        overlap = collinear_overlap(s1, s2)
        return (
            SegIntersection(kind="collinear", overlap=overlap)
            if overlap is not None
            else _NO_INTERSECTION
        )

    straddle1 = (d1 > 0 > d2) or (d1 < 0 < d2)
    straddle2 = (d3 > 0 > d4) or (d3 < 0 < d4)

    if straddle1 and straddle2:
        # ------------------------------------------------------------------
        # CROSS-LANGUAGE DIVERGENCE — DELIBERATE, AND THE TS SIDE IS WRONG.
        #
        # Writing r = p2-p1 and s = p4-p3, the crossing point on segment 1 is
        # p1 + t*r with
        #     t = ((p3-p1) x s) / (r x s)
        # The denominator `den` below is exactly `r x s`. The numerator
        # `(p3-p1) x s` is exactly `d1 = cross(p3, p4, p1)` — already computed
        # above for the straddle test.
        #
        # `packages/model/src/geometry.ts` (in the `straddle1 && straddle2`
        # branch) writes `const t = d3 / den;`. `d3 = cross(p1, p2, p3)` is
        # `r x (p3-p1)` — a different quantity entirely. Verified over 928
        # randomly generated properly-crossing integer segment pairs against an
        # exact `Fraction` reference: `d1/den` is right 928/928, `d3/den` is
        # right 0/928. Example: (0,0)-(100,0) crossed with (30,-10)-(30,10)
        # returns (-50,0) instead of (30,0).
        #
        # Why the bug is NOT mirrored here: rooms.py feeds this point straight
        # into `point_on_segment(r.point, seg)` when deciding where to split
        # walls for the planar graph. A wrong point fails that test, so a
        # crossing wall pair is never split, the half-edge graph is wrong, and
        # the rooms either side of a `+` junction are not detected at all.
        # Mirroring that would bake a geometry bug into the state hash.
        # T-junctions (an endpoint lying on another wall) take the
        # `d1 == 0 and point_on_segment` branch below and are unaffected — which
        # is why the existing two-room fixtures pass on both sides today.
        #
        # ACTION FOR THE TS OWNER: in `segmentIntersection`, change
        #     const t = d3 / den;
        # to
        #     const t = d1 / den;
        # (one character). Until that lands the two implementations disagree for
        # properly crossing segments only; no shipped golden state fixture
        # exercises that case — see `knownDivergences` in
        # fixtures/model/golden-states.json.
        # ------------------------------------------------------------------
        den = (p2.x - p1.x) * (p4.y - p3.y) - (p2.y - p1.y) * (p4.x - p3.x)
        t = d1 / den
        x_num = p1.x + t * (p2.x - p1.x)
        y_num = p1.y + t * (p2.y - p1.y)
        point = pt_round(x_num, y_num)
        exact = float(x_num).is_integer() and float(y_num).is_integer()
        return SegIntersection(kind="point", point=point, exact=exact, on_endpoint=False)

    # touching: an endpoint of one lies on the other
    if d1 == 0 and point_on_segment(p1, s2):
        return SegIntersection(kind="point", point=p1, exact=True, on_endpoint=True)
    if d2 == 0 and point_on_segment(p2, s2):
        return SegIntersection(kind="point", point=p2, exact=True, on_endpoint=True)
    if d3 == 0 and point_on_segment(p3, s1):
        return SegIntersection(kind="point", point=p3, exact=True, on_endpoint=True)
    if d4 == 0 and point_on_segment(p4, s1):
        return SegIntersection(kind="point", point=p4, exact=True, on_endpoint=True)
    return _NO_INTERSECTION


def collinear_overlap(s1: Seg, s2: Seg) -> Seg | None:
    """EXACT shared sub-segment of two collinear segments, or ``None``."""
    dx = s1.b.x - s1.a.x
    dy = s1.b.y - s1.a.y
    if dx == 0 and dy == 0:
        return Seg(s1.a, s1.a) if point_on_segment(s1.a, s2) else None
    # project all four points onto the dominant axis of s1
    use_x = abs(dx) >= abs(dy)

    def key(p: Pt) -> int:
        return p.x if use_x else p.y

    lo1 = min(key(s1.a), key(s1.b))
    hi1 = max(key(s1.a), key(s1.b))
    lo2 = min(key(s2.a), key(s2.b))
    hi2 = max(key(s2.a), key(s2.b))
    lo = max(lo1, lo2)
    hi = min(hi1, hi2)
    if lo > hi:
        return None

    def at(v: int) -> Pt:
        if use_x:
            t = 0.0 if dx == 0 else (v - s1.a.x) / dx
        else:
            t = 0.0 if dy == 0 else (v - s1.a.y) / dy
        return pt_round(s1.a.x + t * dx, s1.a.y + t * dy)

    return Seg(at(lo), at(hi))


def segments_properly_cross(s1: Seg, s2: Seg) -> bool:
    """True when the two segments cross at a single interior point."""
    r = segment_intersection(s1, s2)
    return r.kind == "point" and not r.on_endpoint


def segments_identical(s1: Seg, s2: Seg) -> bool:
    """True when two segments describe the same line segment (either direction)."""
    return (pt_eq(s1.a, s2.a) and pt_eq(s1.b, s2.b)) or (pt_eq(s1.a, s2.b) and pt_eq(s1.b, s2.a))


def segments_overlap_collinear(s1: Seg, s2: Seg) -> bool:
    """True when two segments overlap along a non-zero length.

    The ``WALL_DUPLICATE`` / "no two walls exactly overlapping" invariant uses
    this.
    """
    r = segment_intersection(s1, s2)
    if r.kind != "collinear":
        return False
    overlap = r.overlap
    return overlap is not None and not pt_eq(overlap.a, overlap.b)


def _half_plane(dx: int, dy: int) -> int:
    """0 for angles in [0, 180), 1 for [180, 360)."""
    if dy > 0:
        return 0
    if dy < 0:
        return 1
    return 0 if dx >= 0 else 1


def compare_angle_around(origin: Pt, a: Pt, b: Pt) -> int:
    """EXACT angular comparator for the half-edge graph.

    Orders directions ``a - origin`` and ``b - origin`` counter-clockwise
    starting at +X, using only integer arithmetic (half-plane, then cross
    product). No ``atan2``, so no floating-point ordering instability between
    TypeScript and Python.
    """
    qa = _half_plane(a.x - origin.x, a.y - origin.y)
    qb = _half_plane(b.x - origin.x, b.y - origin.y)
    if qa != qb:
        return -1 if qa < qb else 1
    c = (a.x - origin.x) * (b.y - origin.y) - (a.y - origin.y) * (b.x - origin.x)
    if c > 0:
        return -1  # a is clockwise of b => smaller angle
    if c < 0:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Offsetting (setback envelopes, wall-thickness insets)
# ---------------------------------------------------------------------------


def _reverse_edge_distances(distances_mm: Sequence[float]) -> list[float]:
    """Reversing a ring reverses its edge order: edge ``i`` of the reversed ring
    is edge ``(n-2-i) mod n`` of the original."""
    n = len(distances_mm)
    return [distances_mm[((n - 2 - i) % n + n) % n] for i in range(n)]


def offset_polygon(poly: Polygon, distances_mm: Sequence[float]) -> list[Pt] | None:
    """Offset every edge of a CCW polygon inward by its own distance.

    ``distances_mm[i]`` applies to edge i (``poly[i] -> poly[i+1]``). Positive =
    inward for a CCW polygon (the left normal points into the polygon), negative
    = outward. This is exactly what setback envelopes need: front 1500, sides
    900, rear 1200, per edge.

    ROUNDED vertices. Returns ``None`` when the offset collapses the polygon (all
    setbacks larger than the plot, or an edge pair becomes parallel-degenerate) —
    callers must treat ``None`` as "no buildable envelope", never as an empty
    polygon.

    LIMITATION (documented, MVP-acceptable): this is a naive line-offset that does
    not resolve self-intersections created by deep offsets of reflex corners. It
    is exact for convex and for rectilinear rect/L/T shapes, which are the only
    plot shapes the MVP accepts. The result is checked with
    :func:`polygon_is_simple` and rejected (``None``) when it self-intersects.
    """
    n = len(poly)
    if n < 3:
        return None
    if len(distances_mm) != n:
        raise ValueError(
            f"offset_polygon: {len(distances_mm)} distances for {n} edges — "
            "distances_mm[i] must correspond to edge poly[i]->poly[i+1]"
        )
    for i in range(n):
        if pt_eq(poly[i], poly[(i + 1) % n]):
            return None  # zero-length edge

    # Work CCW (inward = left normal). Reversing the ring reverses the edge order
    # too: edge i of the reversed ring is edge (n-1-i) of the original.
    is_ccw = polygon_orientation(poly) == "ccw"
    source = list(poly) if is_ccw else reverse_polygon(poly)
    dists = list(distances_mm) if is_ccw else _reverse_edge_distances(distances_mm)

    lines: list[tuple[float, float, float, float]] = []
    for i in range(n):
        a = source[i]
        b = source[(i + 1) % n]
        dx = b.x - a.x
        dy = b.y - a.y
        length = math.sqrt(dx * dx + dy * dy)
        if length == 0:
            return None
        # inward normal for CCW = (-dy, dx)/len
        d = dists[i]
        nx = (-dy / length) * d
        ny = (dx / length) * d
        lines.append((a.x + nx, a.y + ny, b.x + nx, b.y + ny))

    out: list[Pt] = []
    for i in range(n):
        l1 = lines[(i - 1 + n) % n]
        l2 = lines[i]
        d1x = l1[2] - l1[0]
        d1y = l1[3] - l1[1]
        d2x = l2[2] - l2[0]
        d2y = l2[3] - l2[1]
        den = d1x * d2y - d1y * d2x
        if den == 0:
            return None  # consecutive edges parallel after dedupe => degenerate
        t = ((l2[0] - l1[0]) * d2y - (l2[1] - l1[1]) * d2x) / den
        out.append(pt_round(l1[0] + d1x * t, l1[1] + d1y * t))

    cleaned = dedupe_collinear(out)
    if len(cleaned) < 3:
        return None
    if polygon_orientation(cleaned) != "ccw":
        return None  # flipped inside-out
    if not polygon_is_simple(cleaned):
        return None
    return cleaned if is_ccw else reverse_polygon(cleaned)


def offset_polygon_uniform(poly: Polygon, distance_mm: float) -> list[Pt] | None:
    """Uniform inward offset — the common case (wall half-thickness inset)."""
    return offset_polygon(poly, [distance_mm] * len(poly))


# ---------------------------------------------------------------------------
# Rect / L / T helpers and union
# ---------------------------------------------------------------------------


def rect_polygon(min_x: int, min_y: int, max_x: int, max_y: int) -> list[Pt]:
    """CCW rectangle polygon from an inclusive bbox."""
    return [pt(min_x, min_y), pt(max_x, min_y), pt(max_x, max_y), pt(min_x, max_y)]


def bbox_polygon(b: Bbox) -> list[Pt]:
    """CCW rectangle polygon from a bbox."""
    return rect_polygon(b.min_x, b.min_y, b.max_x, b.max_y)


@dataclass
class _CoverageGrid:
    xs: list[int]
    ys: list[int]
    #: ``covered[ix][iy]`` for cell xs[ix]..xs[ix+1] x ys[iy]..ys[iy+1]
    covered: list[list[bool]]


def _build_coverage_grid(rects: Sequence[Bbox]) -> _CoverageGrid | None:
    valid = [r for r in rects if r.max_x > r.min_x and r.max_y > r.min_y]
    if len(valid) == 0:
        return None
    xs_set: set[int] = set()
    ys_set: set[int] = set()
    for r in valid:
        xs_set.add(r.min_x)
        xs_set.add(r.max_x)
        ys_set.add(r.min_y)
        ys_set.add(r.max_y)
    xs = sorted(xs_set)
    ys = sorted(ys_set)
    covered: list[list[bool]] = []
    for ix in range(len(xs) - 1):
        col = [False] * max(0, len(ys) - 1)
        for iy in range(len(ys) - 1):
            cx = xs[ix]
            cy = ys[iy]
            for r in valid:
                if (
                    r.min_x <= cx
                    and xs[ix + 1] <= r.max_x
                    and r.min_y <= cy
                    and ys[iy + 1] <= r.max_y
                ):
                    col[iy] = True
                    break
        covered.append(col)
    return _CoverageGrid(xs=xs, ys=ys, covered=covered)


def _trace_coverage_rings(grid: _CoverageGrid) -> tuple[list[list[Pt]], int]:
    xs, ys, covered = grid.xs, grid.ys, grid.covered
    nx = len(xs) - 1
    ny = len(ys) - 1

    def is_covered(ix: int, iy: int) -> bool:
        return 0 <= ix < nx and 0 <= iy < ny and covered[ix][iy]

    # Collect boundary edges as directed segments so the covered region is on the
    # LEFT of each edge (=> CCW outer rings, CW hole rings). A plain dict keeps
    # insertion order, matching the JS Map the TypeScript mirror walks.
    edges: dict[str, list[Pt]] = {}

    def push_edge(a: Pt, b: Pt) -> None:
        edges.setdefault(pt_key(a), []).append(b)

    for ix in range(nx):
        for iy in range(ny):
            if not is_covered(ix, iy):
                continue
            x0, x1 = xs[ix], xs[ix + 1]
            y0, y1 = ys[iy], ys[iy + 1]
            if not is_covered(ix, iy - 1):
                push_edge(pt(x0, y0), pt(x1, y0))  # bottom, ->
            if not is_covered(ix + 1, iy):
                push_edge(pt(x1, y0), pt(x1, y1))  # right, ^
            if not is_covered(ix, iy + 1):
                push_edge(pt(x1, y1), pt(x0, y1))  # top, <-
            if not is_covered(ix - 1, iy):
                push_edge(pt(x0, y1), pt(x0, y0))  # left, v

    rings: list[list[Pt]] = []
    holes = 0
    while len(edges) > 0:
        first_key = next(iter(edges))
        current = pt_from_key(first_key)
        ring: list[Pt] = [current]
        while True:
            lst = edges.get(pt_key(current))
            if lst is None or len(lst) == 0:
                break
            nxt = lst.pop(0)
            if len(lst) == 0:
                edges.pop(pt_key(current), None)
            if pt_eq(nxt, ring[0]):
                current = nxt
                break
            ring.append(nxt)
            current = nxt
        cleaned = dedupe_collinear(ring)
        if len(cleaned) >= 3:
            if polygon_orientation(cleaned) == "ccw":
                rings.append(cleaned)
            else:
                holes += 1
    rings.sort(key=polygon_area_mm2, reverse=True)
    return rings, holes


def union_axis_aligned_rects(rects: Sequence[Bbox]) -> list[list[Pt]]:
    """EXACT union of axis-aligned rectangles, returned as CCW outer rings.

    Build the coordinate grid from all distinct x/y values, mark cells covered by
    any rect, then trace the boundary of the covered region. Entirely integer, no
    tolerance anywhere. This is how rect/L/T plot shapes and per-storey slab
    outlines get built ("L/T = union of <=3 rects").

    Holes are NOT returned (a doughnut union yields only its outer ring) — the MVP
    never produces one; :func:`union_axis_aligned_rects_has_holes` reports the
    case so callers can fail loudly instead of silently filling a courtyard.
    """
    grid = _build_coverage_grid(rects)
    if grid is None:
        return []
    return _trace_coverage_rings(grid)[0]


def union_axis_aligned_rects_has_holes(rects: Sequence[Bbox]) -> bool:
    """True when the union of ``rects`` encloses an uncovered hole."""
    grid = _build_coverage_grid(rects)
    if grid is None:
        return False
    return _trace_coverage_rings(grid)[1] > 0


# ---------------------------------------------------------------------------
# Triangulation, intersection area, Jaccard
# ---------------------------------------------------------------------------


def point_in_triangle(p: Pt, a: Pt, b: Pt, c: Pt) -> bool:
    """EXACT: is ``p`` inside (or on) triangle abc?"""
    d1 = cross(a, b, p)
    d2 = cross(b, c, p)
    d3 = cross(c, a, p)
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)


def triangulate(poly: Polygon) -> list[Triangle]:
    """Ear-clipping triangulation of a simple polygon.

    Deterministic (always clips the first valid ear in index order). Returns an
    empty list for degenerate input.
    """
    ring = ensure_ccw(dedupe_collinear(poly))
    n = len(ring)
    if n < 3:
        return []
    if n == 3:
        return [(ring[0], ring[1], ring[2])]
    idx = list(range(n))
    out: list[Triangle] = []
    guard = 0
    while len(idx) > 3 and guard < n * n + 16:
        guard += 1
        clipped = False
        for i in range(len(idx)):
            i_prev = idx[(i - 1 + len(idx)) % len(idx)]
            i_cur = idx[i]
            i_next = idx[(i + 1) % len(idx)]
            a = ring[i_prev]
            b = ring[i_cur]
            c = ring[i_next]
            if cross(a, b, c) <= 0:
                continue  # reflex or collinear for a CCW ring
            contains = False
            for j in idx:
                if j in (i_prev, i_cur, i_next):
                    continue
                if point_in_triangle(ring[j], a, b, c):
                    contains = True
                    break
            if contains:
                continue
            out.append((a, b, c))
            del idx[i]
            clipped = True
            break
        if not clipped:
            break  # non-simple input; bail with what we have
    if len(idx) == 3:
        out.append((ring[idx[0]], ring[idx[1]], ring[idx[2]]))
    return out


def _clip_half_plane(
    poly: Sequence[tuple[float, float]], a: Pt, b: Pt
) -> list[tuple[float, float]]:
    """Sutherland-Hodgman: clip a convex polygon by the half-plane left of a->b."""

    def side(p: tuple[float, float]) -> float:
        return (b.x - a.x) * (p[1] - a.y) - (b.y - a.y) * (p[0] - a.x)

    out: list[tuple[float, float]] = []
    n = len(poly)
    for i in range(n):
        cur = poly[i]
        nxt = poly[(i + 1) % n]
        sc = side(cur)
        sn = side(nxt)
        if sc >= 0:
            out.append(cur)
        if (sc > 0 > sn) or (sc < 0 < sn):
            t = sc / (sc - sn)
            out.append((cur[0] + (nxt[0] - cur[0]) * t, cur[1] + (nxt[1] - cur[1]) * t))
    return out


def _shoelace_abs(poly: Sequence[tuple[float, float]]) -> float:
    n = len(poly)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        p = poly[i]
        q = poly[(i + 1) % n]
        total += p[0] * q[1] - q[0] * p[1]
    return abs(total) / 2


def polygon_intersection_area_mm2(a: Polygon, b: Polygon) -> int:
    """APPROXIMATE intersection area of two simple polygons, in mm^2.

    Triangulate both, clip every A-triangle against every B-triangle's three
    half-planes, sum the clipped areas. Arithmetic is +,-,*,/ on doubles only, so
    the result is bit-identical in the TypeScript mirror, but it is NOT exact
    integer mm^2 — do not store it, do not put it in a compliance number. Its only
    job is ranking room matches.
    """
    if len(a) < 3 or len(b) < 3:
        return 0
    if not bbox_intersects(bbox(a), bbox(b)):
        return 0
    ta = triangulate(a)
    tb = triangulate(b)
    total = 0.0
    for t1 in ta:
        base: list[tuple[float, float]] = [
            (float(t1[0].x), float(t1[0].y)),
            (float(t1[1].x), float(t1[1].y)),
            (float(t1[2].x), float(t1[2].y)),
        ]
        for t2 in tb:
            # ensure t2 is CCW so "left of each edge" is its interior
            ccw = t2 if cross(t2[0], t2[1], t2[2]) > 0 else (t2[2], t2[1], t2[0])
            poly = base
            poly = _clip_half_plane(poly, ccw[0], ccw[1])
            if len(poly) == 0:
                continue
            poly = _clip_half_plane(poly, ccw[1], ccw[2])
            if len(poly) == 0:
                continue
            poly = _clip_half_plane(poly, ccw[2], ccw[0])
            if len(poly) == 0:
                continue
            total += _shoelace_abs(poly)
    return round_half_away_from_zero(total)


def polygon_union_area_mm2(a: Polygon, b: Polygon) -> int:
    """APPROXIMATE union area = |A| + |B| - |A and B|."""
    return polygon_area_mm2(a) + polygon_area_mm2(b) - polygon_intersection_area_mm2(a, b)


def jaccard(a: Polygon, b: Polygon) -> float:
    """Jaccard overlap ``|A and B| / |A or B|`` in [0, 1].

    LOAD-BEARING: this is how room ids survive edits ("match new faces to existing
    rooms by max-overlap"). Returns 0 when either polygon is degenerate.
    """
    inter = polygon_intersection_area_mm2(a, b)
    if inter <= 0:
        return 0.0
    union = polygon_area_mm2(a) + polygon_area_mm2(b) - inter
    if union <= 0:
        return 0.0
    return inter / union


def containment_ratio(a: Polygon, b: Polygon) -> float:
    """Ratio of A covered by B — useful when A shrank a lot but is "the same room"."""
    area_a = polygon_area_mm2(a)
    if area_a <= 0:
        return 0.0
    return polygon_intersection_area_mm2(a, b) / area_a
