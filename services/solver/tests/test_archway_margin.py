"""A cased archway keeps the model's minimum at each end; a framed door keeps a pier.

The distinction is load-bearing: stage A sizes a passage 4 coarse cells (1200 mm)
wide and compact plans enter it at its END, so the opening's jambs are the return
walls' faces. With the door pier (230 mm) at both ends nothing legal fits in 1200,
every such candidate died at DOOR_DOES_NOT_FIT, and the 30 x 40 programs went
infeasible once stage A was made to guarantee what stage B demands.
"""

from __future__ import annotations

from garh_model.validate import WALL_END_MARGIN_MM as MODEL_MIN_MARGIN_MM

from services.solver.openings import (
    ARCHWAY_END_MARGIN_MM,
    WALL_END_MARGIN_MM,
    _fit_opening,
    _WallOccupancy,
)
from services.solver.stage_a import min_frontage_cells
from services.solver.walls import WallSpec


def _span_wall(length_mm: int) -> WallSpec:
    return WallSpec(
        axis="h", a=(0, 0), b=(length_mm, 0), thickness_mm=115, kind="internal", line_mm=0
    )


def test_archway_margin_is_the_models_minimum_and_below_the_door_pier() -> None:
    assert (
        ARCHWAY_END_MARGIN_MM == MODEL_MIN_MARGIN_MM
    ), "an archway may not go below what the fold accepts"
    assert WALL_END_MARGIN_MM > ARCHWAY_END_MARGIN_MM


def test_an_800_archway_fits_a_1035_span_where_a_framed_door_cannot() -> None:
    wall = _span_wall(1035)
    # NEGATIVE CONTROL: the door pier at both ends leaves 575 mm — no door.
    assert _fit_opening(wall, _WallOccupancy(), 0, 0, 1035, 800) is None
    centre = _fit_opening(
        wall, _WallOccupancy(), 0, 0, 1035, 800, end_margin_mm=ARCHWAY_END_MARGIN_MM
    )
    assert centre is not None
    start, end = centre - 400, centre + 400
    assert start >= ARCHWAY_END_MARGIN_MM and end <= 1035 - ARCHWAY_END_MARGIN_MM


def test_stage_a_floors_agree_with_stage_b() -> None:
    """A 4-cell passage can be entered at its end by an archway but not by a door."""
    assert min_frontage_cells(800 + 2 * ARCHWAY_END_MARGIN_MM) == 4
    assert min_frontage_cells(800 + 2 * WALL_END_MARGIN_MM) == 5
