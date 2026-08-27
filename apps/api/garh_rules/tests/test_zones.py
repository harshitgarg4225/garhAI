"""The 3x3 grid, the 8 compass sectors, and the four geometry primitives.

The tiebreaker is ``rulepacks/schema/rulepack.schema.json`` -> ``$defs.zone`` and
``$defs.compass8``, quoted in :mod:`garh_rules.zones`. Two engines (this one and
the fixture generator) must put a room in the same cell, so every rule that could
be read two ways is pinned here:

* thirds are rounded **half-up** to whole millimetres;
* a centroid exactly on a split belongs to the **more-north / more-east** cell;
* the grid is built in the frame where TRUE north is +Y, so ``plot.northDeg``
  rotates the plot **counter-clockwise** before the bounding box is taken;
* facing sectors are 45 degrees wide and centred on the cardinal, half-open —
  22 degrees is N, 23 is NE.

The geometry helpers are tested against exact hand-computed values because the
engine reads pre-derived scalars from the context and the fixture verifier
recomputes them from ``polygonMm``: the two derivations have to be the same one.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from garh_rules.geometry import (
    clip_area_against_rect,
    polygon_area_mm2,
    polygon_area_x2,
    polygon_bbox,
    polygon_centroid_mm,
    polygon_least_width_mm,
    rotate_ccw_deg,
    round_half_up_int,
)
from garh_rules.zones import COMPASS8, ZONES, ZoneGrid, facing_of, format_zone_list, zone_grid_for

from .conftest import make_context, rect

SQUARE_9 = [[0, 0], [9000, 0], [9000, 9000], [0, 9000]]


# ---------------------------------------------------------------------------
# Polygon primitives
# ---------------------------------------------------------------------------


class TestPolygonPrimitives:
    def test_area_is_exact_and_orientation_independent(self) -> None:
        ccw = [(0, 0), (3000, 0), (3000, 4000), (0, 4000)]
        cw = list(reversed(ccw))
        assert polygon_area_x2(ccw) == 2 * 12_000_000
        assert polygon_area_x2(cw) == -2 * 12_000_000
        assert polygon_area_mm2(ccw) == polygon_area_mm2(cw) == 12_000_000

    def test_a_half_millimetre_area_rounds_up_not_down(self) -> None:
        """A triangle with an odd doubled area ends in .5 mm2; truncating would
        under-report a room and could hand it a pass it did not earn."""
        triangle = [(0, 0), (3, 0), (0, 1)]
        assert polygon_area_x2(triangle) == 3
        assert polygon_area_mm2(triangle) == 2

    def test_centroid_of_a_rectangle(self) -> None:
        assert polygon_centroid_mm([(0, 0), (3000, 0), (3000, 4000), (0, 4000)]) == (1500, 2000)

    def test_centroid_rounds_half_up(self) -> None:
        # centre of a 1 x 1 mm square is (0.5, 0.5) -> (1, 1)
        assert polygon_centroid_mm([(0, 0), (1, 0), (1, 1), (0, 1)]) == (1, 1)

    def test_centroid_of_an_l_shape_is_not_its_bbox_centre(self) -> None:
        # Two rectangles: 6000x2000 at the base (centroid 3000,1000, area 12 m2) and
        # 2000x4000 above it (centroid 1000,4000, area 8 m2) -> (2200, 2200).
        shape = [(0, 0), (6000, 0), (6000, 2000), (2000, 2000), (2000, 6000), (0, 6000)]
        assert polygon_centroid_mm(shape) == (2200, 2200)
        assert polygon_bbox(shape) == (0, 0, 6000, 6000)

    def test_degenerate_ring_falls_back_to_the_vertex_mean(self) -> None:
        assert polygon_centroid_mm([(0, 0), (100, 0), (200, 0)]) == (100, 0)

    def test_least_width_is_the_shorter_bbox_side(self) -> None:
        assert polygon_least_width_mm(rect(0, 0, 2400, 9000)) == 2400
        assert polygon_least_width_mm(rect(0, 0, 9000, 2400)) == 2400

    def test_round_half_up_on_negatives_goes_toward_zero(self) -> None:
        assert round_half_up_int(2.5) == 3
        assert round_half_up_int(-2.5) == -2
        assert round_half_up_int(2.4999) == 2


class TestRotation:
    @pytest.mark.parametrize(
        "degrees,expected",
        [(0, (100, 200)), (90, (-200, 100)), (180, (-100, -200)), (270, (200, -100))],
    )
    def test_cardinals_are_exact_integer_swaps(
        self, degrees: int, expected: tuple[int, int]
    ) -> None:
        assert rotate_ccw_deg((100, 200), degrees) == expected

    def test_a_non_cardinal_bearing_rounds_to_whole_millimetres(self) -> None:
        rotated = rotate_ccw_deg((1000, 0), 45)
        assert rotated == (707, 707)  # 1000 / sqrt(2) = 707.1

    def test_rotation_preserves_area(self) -> None:
        ring = [(0, 0), (3000, 0), (3000, 4000), (0, 4000)]
        rotated = [rotate_ccw_deg(p, 90) for p in ring]
        assert polygon_area_mm2(rotated) == polygon_area_mm2(ring)

    def test_full_turn_is_the_identity(self) -> None:
        assert rotate_ccw_deg((123, -456), 360) == (123, -456)


# ---------------------------------------------------------------------------
# The 3x3 grid
# ---------------------------------------------------------------------------


class TestZoneGrid:
    def test_nine_cells_in_the_documented_layout(self) -> None:
        grid = ZoneGrid.from_ring(SQUARE_9, 0)
        # cell centres, bottom row first
        assert grid.zone_of((1500, 1500)) == "SW"
        assert grid.zone_of((4500, 1500)) == "S"
        assert grid.zone_of((7500, 1500)) == "SE"
        assert grid.zone_of((1500, 4500)) == "W"
        assert grid.zone_of((4500, 4500)) == "C"
        assert grid.zone_of((7500, 4500)) == "E"
        assert grid.zone_of((1500, 7500)) == "NW"
        assert grid.zone_of((4500, 7500)) == "N"
        assert grid.zone_of((7500, 7500)) == "NE"
        assert set(ZONES) == {
            grid.zone_of((x, y)) for x in (1500, 4500, 7500) for y in (1500, 4500, 7500)
        }

    def test_a_centroid_on_a_split_belongs_to_the_more_north_east_cell(self) -> None:
        grid = ZoneGrid.from_ring(SQUARE_9, 0)
        assert grid.col_split_1 == 3000 and grid.row_split_1 == 3000
        assert grid.zone_of((2999, 2999)) == "SW"
        assert grid.zone_of((3000, 2999)) == "S"
        assert grid.zone_of((2999, 3000)) == "W"
        assert grid.zone_of((3000, 3000)) == "C"

    def test_thirds_of_an_indivisible_span_round_half_up(self) -> None:
        """A 10 000 mm span: thirds at 3333.3 and 6666.7 -> 3333 and 6667."""
        grid = ZoneGrid.from_ring(rect(0, 0, 10_000, 10_000), 0)
        assert (grid.col_split_1, grid.col_split_2) == (3333, 6667)
        assert (grid.row_split_1, grid.row_split_2) == (3333, 6667)

    def test_north_rotates_the_grid_not_the_labels(self) -> None:
        """A plot whose true north points along +X (northDeg 90): the cell on the
        plot's east side is the one Vastu calls north."""
        grid = ZoneGrid.from_ring(SQUARE_9, 90)
        assert grid.zone_of((7500, 4500)) == "N"
        assert grid.zone_of((1500, 4500)) == "S"
        assert grid.zone_of((4500, 7500)) == "W"
        assert grid.zone_of((4500, 1500)) == "E"
        assert grid.zone_of((4500, 4500)) == "C"

    def test_north_180_mirrors_the_grid(self) -> None:
        grid = ZoneGrid.from_ring(SQUARE_9, 180)
        assert grid.zone_of((1500, 1500)) == "NE"
        assert grid.zone_of((7500, 7500)) == "SW"

    def test_the_centre_cell_is_one_ninth_of_a_square_plot(self) -> None:
        grid = ZoneGrid.from_ring(SQUARE_9, 0)
        x0, y0, x1, y1 = grid.centre_cell_rect()
        assert (x1 - x0) * (y1 - y0) == 3000 * 3000

    def test_cell_rects_tile_the_bounding_box_without_gaps(self) -> None:
        grid = ZoneGrid.from_ring(rect(0, 0, 10_000, 7_000), 0)
        total = 0
        for zone in ZONES:
            x0, y0, x1, y1 = grid.cell_rect(zone)
            total += (x1 - x0) * (y1 - y0)
        assert total == 10_000 * 7_000

    def test_unknown_zone_raises(self) -> None:
        grid = ZoneGrid.from_ring(SQUARE_9, 0)
        with pytest.raises(KeyError):
            grid.cell_rect("NNE")

    def test_grid_comes_from_the_plot_summary(self) -> None:
        context = make_context(boundary=SQUARE_9, north_deg=90)
        grid = zone_grid_for(context.plot)
        assert grid.north_deg == 90
        assert grid.zone_of((7500, 4500)) == "N"


class TestFacing:
    @pytest.mark.parametrize(
        "bearing,expected",
        [
            (0, "N"),
            (22, "N"),
            (23, "NE"),
            (45, "NE"),
            (67, "NE"),
            (68, "E"),
            (90, "E"),
            (135, "SE"),
            (180, "S"),
            (225, "SW"),
            (270, "W"),
            (315, "NW"),
            (337, "NW"),
            (338, "N"),
            (359, "N"),
        ],
    )
    def test_sectors_are_half_open_and_centred_on_the_cardinal(
        self, bearing: int, expected: str
    ) -> None:
        assert facing_of(bearing, 0) == expected

    def test_north_bearing_shifts_every_sector(self) -> None:
        # A door whose outward normal is plot-south (180) on a plot rotated 180 faces N.
        assert facing_of(180, 180) == "N"
        assert facing_of(0, 180) == "S"

    def test_every_sector_is_reachable_and_c_is_not_one(self) -> None:
        produced = {facing_of(bearing, 0) for bearing in range(360)}
        assert produced == set(COMPASS8)
        assert "C" not in produced


# ---------------------------------------------------------------------------
# Exact polygon ∩ rectangle — brahmasthan_open depends on it being exact
# ---------------------------------------------------------------------------


class TestClipAreaAgainstRect:
    def test_fully_inside(self) -> None:
        assert clip_area_against_rect(rect(100, 100, 200, 200), 0, 0, 1000, 1000) == Fraction(
            40_000
        )

    def test_fully_outside(self) -> None:
        assert clip_area_against_rect(rect(5000, 5000, 100, 100), 0, 0, 1000, 1000) == 0

    def test_partial_overlap_is_the_intersection_only(self) -> None:
        # 1000 x 1000 room straddling the cell edge at x = 500
        assert clip_area_against_rect(rect(0, 0, 1000, 1000), 500, 0, 1500, 1000) == Fraction(
            500_000
        )

    def test_touching_edges_have_no_area(self) -> None:
        assert clip_area_against_rect(rect(1000, 0, 500, 500), 0, 0, 1000, 1000) == 0

    def test_result_is_exact_on_a_diagonal(self) -> None:
        """A triangle clipped by a rectangle: the answer is 1/2 mm2 exactly, and a
        float would make ``floor(10000 * overlap / cell)`` ambiguous at the boundary."""
        triangle = [(0, 0), (1, 0), (0, 1)]
        assert clip_area_against_rect(triangle, 0, 0, 10, 10) == Fraction(1, 2)

    def test_winding_order_does_not_matter(self) -> None:
        ring = rect(0, 0, 1000, 1000)
        assert clip_area_against_rect(ring, 0, 0, 500, 500) == clip_area_against_rect(
            list(reversed(ring)), 0, 0, 500, 500
        )

    def test_degenerate_rectangle_is_zero_not_an_error(self) -> None:
        assert clip_area_against_rect(rect(0, 0, 100, 100), 50, 50, 50, 50) == 0


def test_format_zone_list_reads_like_a_sentence() -> None:
    assert format_zone_list(["NE"]) == "NE"
    assert format_zone_list(["N", "NE"]) == "N or NE"
    assert format_zone_list(["N", "NE", "E"]) == "N, NE or E"
    assert format_zone_list([]) == ""
