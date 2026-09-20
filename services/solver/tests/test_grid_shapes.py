"""Which plot shapes the grid can describe, and which the solver can actually plan.

Two different questions, and conflating them is what made the solver look narrower
than it is:

* :func:`buildable_rects_of_mask` is the grid describing ITSELF. It used to refuse
  anything past §5.2's "union of ≤3 rects" with ``UNSUPPORTED_SHAPE`` — so a U, a
  cross, a stepped frontage and every skewed plot were "unsupported".
* Stage A consumes :func:`void_rects_of_mask`, which never had a shape limit. The
  live path has always taken those plots; nothing in the solve ever called the
  function that refused them.

So these tests pin both halves: the decomposition is now general and exact (a
partition — disjoint, and covering exactly the buildable cells), and the PIPELINE
really does plan an L-shaped plot end to end through real CP-SAT.

The remaining limit is skew, and it is a resolution limit rather than a refusal:
an axis-aligned grid staircases a diagonal, always inward (a cell counts only when
all four corners and its centre are inside), so a plan on a trapezoidal plot is
buildable and compliant but leaves a sliver along the diagonal unused. The last
test measures that under-use rather than asserting it away.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from services.solver.grid import (
    MAX_DECOMPOSITION_RECTS,
    CellRect,
    GridError,
    build_grid,
    buildable_rects_of_mask,
    is_rectilinear,
    void_rects_of_mask,
)

FT = 304.8


def _mask(rows: Sequence[str]) -> tuple[tuple[bool, ...], ...]:
    """``"##.."`` → a row of cells; row 0 is the SOUTH edge (cell space's minimum)."""
    return tuple(tuple(ch == "#" for ch in row) for row in rows)


def _cells(rects: Sequence[CellRect]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for rect in rects:
        for row in range(rect.row1, rect.row2):
            for col in range(rect.col1, rect.col2):
                out.append((col, row))
    return out


def _assert_partitions(mask: Sequence[Sequence[bool]], rects: Sequence[CellRect]) -> None:
    """The rects must be disjoint and cover exactly the buildable cells."""
    covered = _cells(rects)
    assert len(covered) == len(set(covered)), "rectangles overlap — not a partition"
    expected = {
        (col, row) for row, cells in enumerate(mask) for col, cell in enumerate(cells) if cell
    }
    assert set(covered) == expected, "the rects and the mask disagree about what is buildable"


# ---------------------------------------------------------------------------
# the shapes §5.2 names — unchanged output
# ---------------------------------------------------------------------------


def test_a_rectangle_is_one_rect() -> None:
    mask = _mask(["####", "####", "####"])
    rects = buildable_rects_of_mask(mask)
    assert len(rects) == 1
    _assert_partitions(mask, rects)


def test_an_l_is_two_and_a_t_is_at_most_three() -> None:
    l_mask = _mask(["####", "####", "##..", "##.."])
    l_rects = buildable_rects_of_mask(l_mask)
    assert len(l_rects) == 2
    _assert_partitions(l_mask, l_rects)

    t_mask = _mask(["######", "######", "..##..", "..##.."])
    t_rects = buildable_rects_of_mask(t_mask)
    assert len(t_rects) <= 3
    _assert_partitions(t_mask, t_rects)


# ---------------------------------------------------------------------------
# the shapes it used to refuse
# ---------------------------------------------------------------------------


def test_a_u_shaped_envelope_is_described_instead_of_refused() -> None:
    """A courtyard open to the north: a row with TWO runs, which bands cannot do."""
    mask = _mask(["######", "######", "##..##", "##..##", "##..##"])
    rects = buildable_rects_of_mask(mask)
    _assert_partitions(mask, rects)
    assert len(rects) >= 3


def test_a_cross_and_a_stepped_frontage_are_described() -> None:
    cross = _mask(["..##..", "..##..", "######", "######", "..##..", "..##.."])
    _assert_partitions(cross, buildable_rects_of_mask(cross))

    steps = _mask(["######", "#####.", "####..", "###...", "##....", "#....."])
    rects = buildable_rects_of_mask(steps)
    _assert_partitions(steps, rects)
    assert len(rects) == 6, "one rect per distinct run, merged downward"


def test_a_staircase_from_a_skewed_boundary_no_longer_raises() -> None:
    """The trapezium case: every row a different run. It used to be UNSUPPORTED_SHAPE."""
    mask = _mask(["#" * (12 - i) + "." * i for i in range(12)])
    rects = buildable_rects_of_mask(mask)
    _assert_partitions(mask, rects)
    assert len(rects) == 12


def test_a_hole_in_the_middle_is_described() -> None:
    """Not a plot shape the product makes, but the partition must still be exact."""
    mask = _mask(["#####", "#...#", "#.#.#", "#...#", "#####"])
    _assert_partitions(mask, buildable_rects_of_mask(mask))


def test_an_absurd_mask_is_refused_with_a_typed_code() -> None:
    """NEGATIVE CONTROL: the bound is real, and it names itself.

    A checkerboard is one rect per cell. Sized just past the limit, the function
    must refuse — a decomposition with no ceiling is how a bad input becomes a
    memory problem instead of an error message.
    """
    # A checkerboard is one rectangle per cell: 40 x 64 gives 1,280 of them, well
    # past the 512 ceiling, and no two adjacent cells can ever merge.
    rows = ["".join("#" if (r + c) % 2 == 0 else "." for c in range(64)) for r in range(40)]
    mask = _mask(rows)
    with pytest.raises(GridError) as excinfo:
        buildable_rects_of_mask(mask)
    assert excinfo.value.code == "TOO_COMPLEX"
    assert str(MAX_DECOMPOSITION_RECTS) in (excinfo.value.detail or "")


def test_a_checkerboard_just_inside_the_limit_is_allowed() -> None:
    """The other side of the same control: under the bound, it describes it."""
    rows = ["".join("#" if (r + c) % 2 == 0 else "." for c in range(12)) for r in range(12)]
    mask = _mask(rows)
    rects = buildable_rects_of_mask(mask)
    assert len(rects) <= MAX_DECOMPOSITION_RECTS
    _assert_partitions(mask, rects)


# ---------------------------------------------------------------------------
# build_grid on real envelope polygons
# ---------------------------------------------------------------------------


def test_build_grid_takes_an_l_shaped_envelope() -> None:
    poly = ((0, 0), (9000, 0), (9000, 6000), (6000, 6000), (6000, 9000), (0, 9000))
    assert is_rectilinear(poly)
    grid = build_grid(poly)
    assert grid.buildable_cell_count() > 0
    _assert_partitions(grid.mask, grid.rects)
    assert grid.voids, "the notch must be a mandatory-void rect for stage A"


def test_build_grid_still_refuses_a_slanted_edge_and_says_which_path_does_not() -> None:
    """``build_grid``'s own contract is rectilinear; the SOLVER's is not.

    The message must not leave a reader thinking the product refuses skewed plots —
    ``stages.grid_envelope`` grids them, staircasing the diagonal.
    """
    with pytest.raises(GridError) as excinfo:
        build_grid(((0, 0), (9000, 0), (7000, 9000), (0, 9000)))
    assert excinfo.value.code == "NOT_RECTILINEAR"


# ---------------------------------------------------------------------------
# what the SOLVER does with these plots — the claim that matters
# ---------------------------------------------------------------------------


def test_the_void_rects_stage_a_consumes_have_never_had_a_shape_limit() -> None:
    """Whatever the mask, stage A gets its mandatory-void rectangles."""
    for rows in (
        ["######", "######", "##..##", "##..##"],  # U
        ["#" * (12 - i) + "." * i for i in range(12)],  # staircase
        ["..##..", "######", "######", "..##.."],  # cross
    ):
        mask = _mask(rows)
        voids = void_rects_of_mask(mask)
        empty = {
            (col, row)
            for row, cells in enumerate(mask)
            for col, cell in enumerate(cells)
            if not cell
        }
        assert set(_cells(voids)) == empty, "the voids and the mask disagree"


def test_the_pipeline_plans_an_l_shaped_plot_end_to_end() -> None:
    """A real CP-SAT solve on an L-plot: the shape an architect actually meets.

    Not a grid unit test — the whole §5 pipeline, because "the grid can describe it"
    and "the product can plan it" are different claims and only the second is worth
    anything to an architect.
    """
    pytest.importorskip("ortools")
    import asyncio
    from typing import Any

    from services.solver.handler import _parse_params
    from services.solver.pipeline import PRODUCTION_PROFILE, SolveContext, run_solver
    from services.solver.tests.test_stages_integration import PAYLOAD

    width, depth = int(40 * FT), int(60 * FT)
    notch = 4_000
    payload: dict[str, Any] = {
        **PAYLOAD,
        "plot": {
            # L-plot: a 4 x 4 m bite out of the rear-east corner.
            "polygon": [
                [0, 0],
                [width, 0],
                [width, depth - notch],
                [width - notch, depth - notch],
                [width - notch, depth],
                [0, depth],
            ],
            "edges": [
                {"index": 0, "role": "front", "setbackMm": 3_000, "roadWidthMm": 9_000},
                {"index": 1, "role": "side", "setbackMm": 1_500},
                {"index": 2, "role": "rear", "setbackMm": 1_500},
                {"index": 3, "role": "side", "setbackMm": 1_500},
                {"index": 4, "role": "rear", "setbackMm": 1_500},
                {"index": 5, "role": "side", "setbackMm": 1_500},
            ],
            "northDeg": 0,
        },
    }
    params = _parse_params(payload, kind="solver.generate")

    async def progress(stage: str, message: str, **data: Any) -> None:
        return None

    result = asyncio.run(
        run_solver(
            SolveContext(
                params=params,
                progress=progress,
                check_cancelled=lambda: None,
                profile=PRODUCTION_PROFILE,
            )
        )
    )
    assert result.options, (
        "the solver produced nothing on an L-shaped plot that comfortably fits the "
        "brief — banner %r, considered %d" % (result.banner, result.considered)
    )
    # Every placed room must sit inside the L, not in the notch it bit out.
    for option in result.options:
        for placement in option.placements:
            in_notch = (
                placement.x_mm + placement.width_mm > width - notch
                and placement.y_mm + placement.depth_mm > depth - notch
            )
            assert not in_notch, "a room was placed in the plot's missing corner"


def test_a_skewed_plot_solves_and_the_staircase_is_conservative() -> None:
    """Skew is a resolution limit, not a refusal — and it always errs inward.

    The grid is axis-aligned, so a diagonal boundary becomes a staircase of 300 mm
    cells. This measures the consequence honestly: the mask's buildable area is
    SMALLER than the envelope's real area (never larger), so the solver under-uses
    a skewed plot rather than placing a room outside it.
    """
    from services.solver.envelope import derive_envelope
    from services.solver.geometry import area_mm2, point_in_polygon
    from services.solver.stages import grid_envelope
    from services.solver.types import COARSE_MODULE_MM, PlotEdge, RegProfile

    width, depth = int(30 * FT), int(40 * FT)
    # A trapezium: the east boundary splays in by 2.5 m over the depth.
    polygon = ((0, 0), (width, 0), (width - 2_500, depth), (0, depth))
    edges = (
        PlotEdge(index=0, role="front", setback_mm=3_000, road_width_mm=9_000),
        PlotEdge(index=1, role="side", setback_mm=1_000),
        PlotEdge(index=2, role="rear", setback_mm=1_500),
        PlotEdge(index=3, role="side", setback_mm=1_000),
    )
    profile = RegProfile(
        city_pack="blr", coverage_percent=60, far_x100=175, max_height_mm=11_000, max_floors=3
    )

    envelope = derive_envelope(polygon, edges, profile, storeys=2)
    assert not is_rectilinear(envelope.polygon), "the skew must survive the offset"

    spec = grid_envelope(envelope)
    assert spec.buildable_cells() > 0, "a skewed plot must still grid"

    gridded = spec.buildable_cells() * COARSE_MODULE_MM * COARSE_MODULE_MM
    real = area_mm2(envelope.polygon)
    assert gridded < real, "the staircase must lose area, not invent it"
    assert gridded > real * 0.8, (
        "the staircase lost more than a fifth of a 30 x 40 trapezium — that is no "
        "longer a resolution artefact, it is a defect (gridded %d of %d mm²)" % (gridded, real)
    )
    # And every buildable cell's centre really is inside the envelope.
    for row in range(spec.rows):
        for col in range(spec.cols):
            if spec.mask[row][col]:
                origin = spec.cell_origin(col, row)
                centre = (
                    origin[0] + COARSE_MODULE_MM // 2,
                    origin[1] + COARSE_MODULE_MM // 2,
                )
                assert point_in_polygon(centre, envelope.polygon)
