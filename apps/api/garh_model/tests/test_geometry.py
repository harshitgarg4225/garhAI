"""Integer-mm geometry properties.

Everything here is EXACT: areas are integer mm^2 computed from integer cross
products, orientation is a sign test, and ``canonical_ring`` is a total order —
no epsilon anywhere. The property tests exist to keep it that way.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from garh_model.geometry import (
    Pt,
    Seg,
    bbox,
    canonical_ring,
    collinear_overlap,
    dedupe_collinear,
    ensure_ccw,
    jaccard,
    point_along_seg,
    point_in_polygon,
    polygon_area_mm2,
    polygon_centroid,
    polygon_contains,
    polygon_intersection_area_mm2,
    polygon_is_closed_ring,
    polygon_key,
    polygon_orientation,
    polygon_union_area_mm2,
    pt_eq,
    rect_polygon,
    reverse_polygon,
    segment_intersection,
    segment_length_mm,
)

COORD = st.integers(min_value=-50_000, max_value=50_000)
SIZE = st.integers(min_value=1, max_value=20_000)


def rect(x: int, y: int, w: int, h: int) -> list[Pt]:
    return rect_polygon(x, y, x + w, y + h)


def test_rect_polygon_is_ccw_and_exact() -> None:
    poly = rect_polygon(0, 0, 6000, 4000)
    assert polygon_orientation(poly) == "ccw"
    assert polygon_area_mm2(poly) == 24_000_000
    assert polygon_is_closed_ring(poly)
    assert len(poly) == 4, "no repeated closing vertex"


@given(x=COORD, y=COORD, w=SIZE, h=SIZE)
@settings(max_examples=100, deadline=None)
def test_rectangle_area_is_exactly_w_times_h(x: int, y: int, w: int, h: int) -> None:
    poly = rect(x, y, w, h)
    assert polygon_area_mm2(poly) == w * h
    assert isinstance(polygon_area_mm2(poly), int)


@given(x=COORD, y=COORD, w=SIZE, h=SIZE)
@settings(max_examples=100, deadline=None)
def test_reversing_flips_orientation_but_not_area(x: int, y: int, w: int, h: int) -> None:
    poly = rect(x, y, w, h)
    reversed_poly = reverse_polygon(poly)
    assert polygon_orientation(reversed_poly) == "cw"
    assert polygon_area_mm2(reversed_poly) == polygon_area_mm2(poly)
    assert polygon_orientation(ensure_ccw(reversed_poly)) == "ccw"


@given(x=COORD, y=COORD, w=SIZE, h=SIZE, rotate=st.integers(min_value=0, max_value=3))
@settings(max_examples=100, deadline=None)
def test_canonical_ring_is_rotation_and_winding_invariant(
    x: int, y: int, w: int, h: int, rotate: int
) -> None:
    """Two descriptions of the same rectangle must produce the same key.

    This is what makes derived room ids stable: the id is sha256 of this key.
    """
    poly = rect(x, y, w, h)
    rotated = poly[rotate:] + poly[:rotate]
    assert polygon_key(canonical_ring(rotated)) == polygon_key(canonical_ring(poly))
    assert polygon_key(canonical_ring(reverse_polygon(rotated))) == polygon_key(
        canonical_ring(poly)
    )


def test_canonical_ring_drops_collinear_vertices() -> None:
    with_midpoints = [Pt(0, 0), Pt(3000, 0), Pt(6000, 0), Pt(6000, 4000), Pt(0, 4000)]
    assert len(dedupe_collinear(with_midpoints)) == 4
    assert polygon_key(canonical_ring(with_midpoints)) == polygon_key(
        canonical_ring(rect_polygon(0, 0, 6000, 4000))
    )


@given(x=COORD, y=COORD, w=SIZE, h=SIZE)
@settings(max_examples=100, deadline=None)
def test_bbox_of_a_rectangle(x: int, y: int, w: int, h: int) -> None:
    b = bbox(rect(x, y, w, h))
    assert (b.min_x, b.min_y, b.max_x, b.max_y) == (x, y, x + w, y + h)


def test_point_in_polygon_classifies_boundary_separately() -> None:
    poly = rect_polygon(0, 0, 1000, 1000)
    assert point_in_polygon(Pt(500, 500), poly) == "inside"
    assert point_in_polygon(Pt(0, 500), poly) == "boundary"
    assert point_in_polygon(Pt(1000, 1000), poly) == "boundary"
    assert point_in_polygon(Pt(1001, 500), poly) == "outside"
    assert polygon_contains(poly, Pt(500, 500))


def test_segment_length_is_rounded_to_whole_mm() -> None:
    assert segment_length_mm(Seg(Pt(0, 0), Pt(3000, 0))) == 3000
    assert segment_length_mm(Seg(Pt(0, 0), Pt(3, 4))) == 5
    # 3000 x 4000 -> exactly 5000
    assert segment_length_mm(Seg(Pt(0, 0), Pt(3000, 4000))) == 5000


def test_point_along_seg_is_exact_on_axis_aligned_walls() -> None:
    assert pt_eq(point_along_seg(Seg(Pt(0, 0), Pt(6000, 0)), 2000), Pt(2000, 0))
    assert pt_eq(point_along_seg(Seg(Pt(0, 0), Pt(0, 4000)), 1500), Pt(0, 1500))


def test_segment_intersection_classification() -> None:
    cross = segment_intersection(Seg(Pt(0, 0), Pt(1000, 0)), Seg(Pt(500, -500), Pt(500, 500)))
    assert cross.kind == "point"
    assert cross.point == Pt(500, 0)
    assert cross.exact

    tee = segment_intersection(Seg(Pt(0, 0), Pt(1000, 0)), Seg(Pt(500, 0), Pt(500, 500)))
    assert tee.kind == "point"
    assert tee.point == Pt(500, 0)

    apart = segment_intersection(Seg(Pt(0, 0), Pt(100, 0)), Seg(Pt(0, 50), Pt(100, 50)))
    assert apart.kind == "none"

    overlapping = segment_intersection(Seg(Pt(0, 0), Pt(1000, 0)), Seg(Pt(400, 0), Pt(1400, 0)))
    assert overlapping.kind == "collinear"
    assert overlapping.overlap is not None


def test_collinear_overlap_is_exact() -> None:
    ov = collinear_overlap(Seg(Pt(0, 0), Pt(1000, 0)), Seg(Pt(400, 0), Pt(1400, 0)))
    assert ov is not None
    assert {(ov.a.x, ov.a.y), (ov.b.x, ov.b.y)} == {(400, 0), (1000, 0)}
    assert collinear_overlap(Seg(Pt(0, 0), Pt(1000, 0)), Seg(Pt(2000, 0), Pt(3000, 0))) is None


def test_collinear_overlap_projects_and_does_not_test_collinearity() -> None:
    """Documented contract: it PROJECTS onto the dominant axis.

    Two parallel-but-offset segments therefore return an overlap, and the caller
    (``validate._overlaps_wall``) is the one that confirms collinearity with an
    exact cross product. Both language mirrors rely on this split, so the
    behaviour is pinned here rather than left to be rediscovered.
    """
    ov = collinear_overlap(Seg(Pt(0, 0), Pt(1000, 0)), Seg(Pt(0, 5), Pt(1000, 5)))
    assert ov is not None, "projection-based by design — see the caller's cross-product check"


@given(
    x=st.integers(min_value=-10_000, max_value=10_000),
    y=st.integers(min_value=-10_000, max_value=10_000),
    w=st.integers(min_value=100, max_value=10_000),
    h=st.integers(min_value=100, max_value=10_000),
    dx=st.integers(min_value=-5_000, max_value=5_000),
    dy=st.integers(min_value=-5_000, max_value=5_000),
)
@settings(max_examples=100, deadline=None)
def test_intersection_and_union_of_two_rectangles(
    x: int, y: int, w: int, h: int, dx: int, dy: int
) -> None:
    a = rect(x, y, w, h)
    b = rect(x + dx, y + dy, w, h)
    overlap_w = max(0, w - abs(dx))
    overlap_h = max(0, h - abs(dy))
    expected_inter = overlap_w * overlap_h
    inter = polygon_intersection_area_mm2(a, b)
    assert inter == expected_inter
    union = polygon_union_area_mm2(a, b)
    assert union == 2 * w * h - expected_inter
    if union > 0:
        assert jaccard(a, b) == pytest.approx(expected_inter / union, rel=1e-9)


@given(x=COORD, y=COORD, w=SIZE, h=SIZE)
@settings(max_examples=50, deadline=None)
def test_jaccard_of_identical_polygons_is_one(x: int, y: int, w: int, h: int) -> None:
    poly = rect(x, y, w, h)
    assert jaccard(poly, poly) == pytest.approx(1.0, rel=1e-12)


def test_centroid_of_a_rectangle() -> None:
    assert polygon_centroid(rect_polygon(0, 0, 6000, 4000)) == Pt(3000, 2000)


def test_degenerate_rings_are_not_closed_rings() -> None:
    assert not polygon_is_closed_ring([Pt(0, 0), Pt(1000, 0)])
    assert not polygon_is_closed_ring([Pt(0, 0), Pt(1000, 0), Pt(2000, 0)])  # zero area
    assert not polygon_is_closed_ring(
        [Pt(0, 0), Pt(1000, 1000), Pt(1000, 0), Pt(0, 1000)]  # bow tie
    )
