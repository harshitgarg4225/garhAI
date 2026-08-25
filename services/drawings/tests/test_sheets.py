"""Tests for the §7 sheet model, the paper transform, the frame and the composer.

    python "services/drawings/tests/test_sheets.py"      # no pytest needed
    pytest services/drawings/tests/test_sheets.py         # in CI

See the header of ``test_projection.py`` for why the bootstrap below exists.

THE ONE THING THESE TESTS EXIST FOR
-----------------------------------
§7 says it plainly: *a wrong scale here silently ruins every sheet*. Nothing else in the
drawing engine fails so quietly — the geometry is right, the layers are right, the labels
are right, and the print is unusable. So the transform is pinned from both ends:

* §7's dimension offsets come out as **2400 / 1800 / 1200** at 1:100 and halve at 1:50;
* a 2.5mm letter survives the model→paper round trip as 2.5mm;
* a 6m wall lands in exactly 60mm of paper at 1:100 and 120mm at 1:50;
* the frame sits at the frame margins, in paper millimetres, at every scale;
* nothing rotates or mirrors on the way through.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_APPS_API = os.path.join(_REPO_ROOT, "apps", "api")
for _path in (_REPO_ROOT, _APPS_API):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from services.dev_stubs import install_worker_dep_stubs  # noqa: E402

STUBBED = install_worker_dep_stubs()

from garh_model.testing import FIXTURE_IDS, make_two_room_plan_with_openings  # noqa: E402
from services.drawings.dimensions import (  # noqa: E402
    LEVEL_1_OFFSET_MM,
    LEVEL_2_OFFSET_MM,
    LEVEL_3_OFFSET_MM,
)
from services.drawings.layers import A_TITL, A_WALL  # noqa: E402
from services.drawings.projection import (  # noqa: E402
    Arc,
    Hatch,
    Line,
    Text,
    bbox_of,
    by_kind,
    count_by_layer,
    find_unsafe_text,
    project_plan,
    validate_primitives,
)
from services.drawings.sheets import (  # noqa: E402
    DEFAULT_PAPER,
    DEFAULT_SCALE,
    DEFAULT_SHEET_PLAN,
    PAPER_SIZES,
    PAPER_UM_PER_MM,
    SCALE_1_50,
    SHEET_KINDS,
    ELEVATION_ORDER,
    Frame,
    PaperTransform,
    Scale,
    Sheet,
    TitleBlock,
    Viewport,
    build_sheet_set,
    compose_plan_sheet,
    compose_sheet,
    default_frame,
    dim_chain_offset_model_mm,
    drawable_area_paper_mm,
    fit_to_frame,
    frame_primitives,
    paper_mm_to_um,
    plan_options_for,
    scale_denominator_of,
    section_line_through_stair,
    sheet_title_block,
    title_block_cells,
    transform_primitives,
    with_annotations,
)

STOREY_ID = FIXTURE_IDS["groundStorey"]


def two_room_house():
    return make_two_room_plan_with_openings().house


def demo_sheet(scale: Scale = DEFAULT_SCALE, paper: str = DEFAULT_PAPER) -> Sheet:
    return Sheet(
        id="floor-plan-gf",
        kind="floor-plan",
        number="A-02",
        title="Ground Floor Plan",
        viewport=Viewport(storey_id=STOREY_ID),
        scale=scale,
        frame=default_frame(paper, title_block=TitleBlock(firm_name="Studio Demo")),
    )


# ---------------------------------------------------------------------------
# The model: defaults and validation
# ---------------------------------------------------------------------------
def test_defaults_are_the_section_7_defaults():
    """"scale (1:100 default), frame A2 landscape default"."""
    assert DEFAULT_SCALE.denominator == 100 and DEFAULT_SCALE.label == "1:100"
    frame = default_frame()
    assert frame.paper.name == "A2"
    assert (frame.paper.width_mm, frame.paper.height_mm) == (594, 420), "A2, landscape"
    assert PAPER_SIZES["A2"].width_mm == 594
    assert SHEET_KINDS == tuple(kind for kind, _number, _title in DEFAULT_SHEET_PLAN)
    assert len(SHEET_KINDS) == 6, "the MVP cut line: six sheet kinds"


def test_viewport_requires_exactly_one_selector():
    """A sheet that looks at a storey *and* a direction is a sheet nobody can render."""
    Viewport(storey_id="s1").validate()
    Viewport(elevation_direction="N").validate()
    Viewport(section_line=((0, 0), (1000, 0))).validate()
    for bad in (
        Viewport(),
        Viewport(storey_id="s1", elevation_direction="N"),
        Viewport(storey_id="s1", section_line=((0, 0), (1, 0))),
    ):
        try:
            bad.validate()
        except ValueError:
            continue
        raise AssertionError("accepted an ambiguous viewport: %r" % (bad,))


def test_table_sheets_need_no_viewport():
    """The schedule and the area statement are tables, not projections."""
    for kind in ("door-window-schedule", "area-statement"):
        Sheet(id=kind, kind=kind, number="A-09", title=kind, viewport=Viewport()).validate()
    try:
        Sheet(id="x", kind="floor-plan", number="A-02", title="x", viewport=Viewport()).validate()
    except ValueError:
        return
    raise AssertionError("a floor plan with no viewport should not validate")


def test_scale_rejects_nonsense_and_reads_ints_correctly():
    """``(100).denominator`` is 1 — the trap that silently made every sheet 1:1."""
    assert scale_denominator_of(100) == 100
    assert scale_denominator_of(Scale(50)) == 50
    for bad in (0, -1, True, None, "100"):
        try:
            scale_denominator_of(bad)
        except (TypeError, ValueError):
            continue
        raise AssertionError("accepted scale %r" % (bad,))


# ---------------------------------------------------------------------------
# §7's paper-scaled dimension offsets
# ---------------------------------------------------------------------------
def test_dim_chain_offsets_are_section_7s_numbers_at_1_100():
    """"Offsets: L1 at 2400mm from building line (paper-scaled), L2 1800, L3 1200"."""
    assert (LEVEL_1_OFFSET_MM, LEVEL_2_OFFSET_MM, LEVEL_3_OFFSET_MM) == (2400, 1800, 1200)
    assert [dim_chain_offset_model_mm(level, 100) for level in (1, 2, 3)] == [2400, 1800, 1200]


def test_dim_chain_offsets_are_paper_scaled_not_model_constants():
    """At 1:50 they halve — otherwise the first chain lands 2.4m out on a 1:50 sheet."""
    assert [dim_chain_offset_model_mm(level, 50) for level in (1, 2, 3)] == [1200, 900, 600]
    assert [dim_chain_offset_model_mm(level, 200) for level in (1, 2, 3)] == [4800, 3600, 2400]
    # 24mm of paper at every scale — that is what "paper-scaled" means.
    for denominator in (50, 100, 200):
        assert dim_chain_offset_model_mm(1, denominator) / denominator == 24.0


# ---------------------------------------------------------------------------
# The transform
# ---------------------------------------------------------------------------
def test_paper_micrometres_are_exact_at_standard_scales():
    """1 model mm is 10µm at 1:100, 20 at 1:50, 5 at 1:200 — no rounding anywhere."""
    for denominator, expected in ((50, 20), (100, 10), (200, 5)):
        transform = PaperTransform(scale_denominator=denominator)
        assert transform.length_to_paper_um(1) == expected
        assert transform.length_to_paper_um(6000) == 6000 * expected
    assert paper_mm_to_um(2.5) == 2500
    assert PAPER_UM_PER_MM == 1000


def test_transform_round_trips_a_building_without_drift():
    """A 12m building must come back to the millimetre it started on."""
    transform = PaperTransform(
        scale_denominator=100, model_anchor_mm=(3000, 2000), paper_origin_um=(297000, 210000)
    )
    for point in ((0, 0), (12000, 9000), (-1150, 4325), (3000, 2000)):
        assert transform.point_to_model(transform.point_to_paper(point)) == point


def test_a_six_metre_wall_lands_in_sixty_millimetres_of_paper():
    """The sanity check a drafter would do with a scale rule."""
    for denominator, paper_mm in ((100, 60.0), (50, 120.0), (200, 30.0)):
        transform = PaperTransform(scale_denominator=denominator)
        span = transform.point_to_paper((6000, 0))[0] - transform.point_to_paper((0, 0))[0]
        assert span / PAPER_UM_PER_MM == paper_mm


def test_fit_centres_the_drawing_in_the_drawable_area():
    frame = default_frame()
    extent = (0, 0, 6230, 4230)
    fit = fit_to_frame(extent, frame, DEFAULT_SCALE)
    assert fit.fits
    area_x, area_y, area_w, area_h = drawable_area_paper_mm(frame)
    centre_paper = fit.transform.point_to_paper(((0 + 6230) // 2, (0 + 4230) // 2))
    assert centre_paper == (
        paper_mm_to_um(area_x + area_w / 2),
        paper_mm_to_um(area_y + area_h / 2),
    )
    # The whole drawing sits inside the border.
    for corner in ((0, 0), (6230, 4230)):
        px, py = fit.transform.point_to_paper(corner)
        assert paper_mm_to_um(frame.margin_left_mm) <= px <= paper_mm_to_um(
            frame.paper.width_mm - frame.margin_right_mm
        )
        assert paper_mm_to_um(frame.margin_bottom_mm) <= py <= paper_mm_to_um(
            frame.paper.height_mm - frame.margin_top_mm
        )


def test_title_block_is_reserved_so_the_drawing_does_not_land_on_it():
    frame = default_frame()
    with_block = drawable_area_paper_mm(frame, reserve_title_block=True)
    without = drawable_area_paper_mm(frame, reserve_title_block=False)
    assert with_block[3] == without[3] - frame.title_block_height_mm
    assert with_block[1] == without[1] + frame.title_block_height_mm


def test_fit_reports_an_overrun_and_suggests_a_scale():
    """Print-true means never silently shrinking: report and suggest (golden rule 9)."""
    frame = default_frame()
    huge = (0, 0, 40_000, 30_000)
    fit = fit_to_frame(huge, frame, SCALE_1_50)
    assert not fit.fits
    assert fit.suggested_denominator() > 50
    smaller = fit_to_frame(huge, frame, Scale(fit.suggested_denominator()))
    assert smaller.fits, "the suggestion has to actually fit"


# ---------------------------------------------------------------------------
# The frame and title block
# ---------------------------------------------------------------------------
def test_frame_is_all_a_titl_and_sits_at_the_margins():
    frame = default_frame(title_block=TitleBlock(firm_name="Studio Demo"))
    primitives = frame_primitives(frame)
    validate_primitives(primitives)
    assert set(count_by_layer(primitives)) == {A_TITL}, "the frame is title-block work only"

    border = by_kind(primitives, "sheet-border")[0]
    xs = sorted(point[0] for point in border.points)
    ys = sorted(point[1] for point in border.points)
    assert xs[0] == paper_mm_to_um(frame.margin_left_mm)
    assert xs[-1] == paper_mm_to_um(frame.paper.width_mm - frame.margin_right_mm)
    assert ys[0] == paper_mm_to_um(frame.margin_bottom_mm)
    assert ys[-1] == paper_mm_to_um(frame.paper.height_mm - frame.margin_top_mm)
    assert border.closed


def test_title_block_cells_tile_the_block_exactly():
    """Proportional rows must fill the box — no gap, no overlap, at any block size."""
    for width, height in ((180, 60), (200, 70), (150, 50)):
        frame = Frame(
            paper=PAPER_SIZES["A2"].landscape(),
            title_block_width_mm=width,
            title_block_height_mm=height,
        )
        cells = title_block_cells(frame)
        area = sum(cell.width_mm * cell.height_mm for cell in cells)
        assert abs(area - width * height) < 1e-6, (width, height)
        for cell in cells:
            assert 0 <= cell.x_mm <= width and 0 <= cell.y_mm <= height
            assert cell.x_mm + cell.width_mm <= width + 1e-9
            assert cell.y_mm + cell.height_mm <= height + 1e-9


def test_title_block_prints_its_values_and_skips_the_empty_ones():
    frame = default_frame(
        title_block=TitleBlock(
            firm_name="Studio Demo",
            project_name="Demo Residence",
            sheet_number="A-02",
            scale_label="1:100",
            date="21-08-2026",
        )
    )
    values = {item.text for item in by_kind(frame_primitives(frame), "title-block-value")}
    assert {"Studio Demo", "Demo Residence", "A-02", "1:100", "21-08-2026"} <= values
    labels = {item.text for item in by_kind(frame_primitives(frame), "title-block-label")}
    assert {"PROJECT", "SHEET", "SCALE", "DATE", "REV", "DRAWN", "CHECKED"} <= labels
    assert "" not in values, "an empty field prints its label, not an empty string"


def test_title_block_text_is_sanitised():
    """§13: user-entered firm/project text reaches a sheet, so it goes through the filter."""
    frame = default_frame(
        title_block=TitleBlock(firm_name="<script>alert(1)</script>", notes="line1\nline2")
    )
    primitives = frame_primitives(frame)
    assert find_unsafe_text(primitives) == ()
    texts = " ".join(item.text for item in primitives if isinstance(item, Text))
    assert "<" not in texts and "\n" not in texts


def test_sheet_title_block_copies_rather_than_mutates():
    frame = default_frame(title_block=TitleBlock(firm_name="Studio Demo"))
    updated = sheet_title_block(frame, drawing_title="Section A-A", sheet_number="A-07")
    assert updated.title_block.drawing_title == "Section A-A"
    assert updated.title_block.firm_name == "Studio Demo", "firm fields carried over"
    assert frame.title_block.drawing_title == "", "the shared frame is untouched"


# ---------------------------------------------------------------------------
# Composition — the one place that scales
# ---------------------------------------------------------------------------
def test_compose_scales_lengths_and_leaves_angles_alone():
    transform = PaperTransform(scale_denominator=100)
    line = Line(layer=A_WALL, a=(0, 0), b=(6000, 0), kind="wall-face")
    arc = Arc(layer=A_WALL, centre=(1000, 0), radius_mm=900, start_deg=0, end_deg=90)
    text = Text(layer=A_WALL, position=(0, 0), text="LIVING", height_mm=250, rotation_deg=90)
    hatch = Hatch(layer=A_WALL, boundary=((0, 0), (100, 0), (100, 100)), angle_deg=45, spacing_mm=250)

    scaled_line, scaled_arc, scaled_text, scaled_hatch = transform_primitives(
        (line, arc, text, hatch), transform
    )
    assert scaled_line.b == (60_000, 0), "6m → 60mm of paper, in µm"
    assert scaled_arc.radius_mm == 9_000
    assert (scaled_arc.start_deg, scaled_arc.end_deg) == (0, 90), "angles never scale"
    assert scaled_text.height_mm == 2_500, "250mm of model = 2.5mm of paper"
    assert scaled_text.rotation_deg == 90
    assert scaled_hatch.angle_deg == 45 and scaled_hatch.spacing_mm == 2_500


def test_compose_puts_the_frame_first_and_keeps_everything_on_paper():
    sheet = demo_sheet()
    model = project_plan(two_room_house(), STOREY_ID, sheet.scale)
    composed = compose_sheet(sheet, model)
    validate_primitives(composed.primitives)
    assert composed.warnings == ()

    assert composed.primitives[0].kind == "sheet-border", "frame is drawn first"
    box = bbox_of(composed.primitives)
    assert box is not None
    min_x, min_y, max_x, max_y = box
    assert min_x >= 0 and min_y >= 0
    assert max_x <= paper_mm_to_um(sheet.frame.paper.width_mm)
    assert max_y <= paper_mm_to_um(sheet.frame.paper.height_mm)


def test_composed_text_heights_come_back_out_in_paper_millimetres():
    """The round trip that proves the two halves of the transform are inverses."""
    sheet = demo_sheet()
    composed = compose_plan_sheet(sheet, two_room_house())
    heights = {
        item.height_mm / PAPER_UM_PER_MM for item in composed.primitives if isinstance(item, Text)
    }
    assert 2.5 in heights, "room names: ISO 3098 body text"
    assert 2.0 in heights, "area lines and the level label"
    assert max(heights) <= 6.0, "nothing on a sheet is 6mm tall except the firm name"


def test_the_same_plan_composes_identically_at_two_scales_in_paper_terms():
    """A 1:50 sheet is twice as big on paper; the *symbols* stay the same size."""
    small = compose_plan_sheet(demo_sheet(DEFAULT_SCALE), two_room_house())
    large = compose_plan_sheet(demo_sheet(SCALE_1_50), two_room_house())

    def wall_span(composed):
        walls = [
            item
            for item in composed.primitives
            if isinstance(item, Line) and item.kind == "wall-face"
        ]
        box = bbox_of(walls)
        return box[2] - box[0]

    assert wall_span(large) == 2 * wall_span(small)
    small_names = {item.height_mm for item in small.primitives if isinstance(item, Text)}
    large_names = {item.height_mm for item in large.primitives if isinstance(item, Text)}
    assert small_names == large_names, "text is paper-sized, so it does not change"


def test_compose_warns_when_a_drawing_overruns_its_sheet():
    sheet = demo_sheet(SCALE_1_50, paper="A4")
    composed = compose_sheet(sheet, project_plan(two_room_house(), STOREY_ID, SCALE_1_50))
    assert not composed.fit.fits
    assert composed.warnings and "1:" in composed.warnings[0]
    assert "A-02" in composed.warnings[0], "the warning names the sheet"


def test_compose_passes_paper_space_content_through_untouched():
    """The schedule and area-statement sheets are already paper-space tables."""
    sheet = Sheet(
        id="door-window-schedule",
        kind="door-window-schedule",
        number="A-08",
        title="Door & Window Schedule",
        viewport=Viewport(),
        frame=default_frame(),
    )
    row = Text(layer=A_TITL, position=(30_000, 300_000), text="D1  900 × 2100", height_mm=2_500)
    composed = compose_sheet(sheet, paper_primitives=(row,))
    assert row in composed.primitives


# ---------------------------------------------------------------------------
# The sheet set
# ---------------------------------------------------------------------------
def test_build_sheet_set_covers_the_six_kinds():
    house = two_room_house()
    sheets = build_sheet_set(house, title_block=TitleBlock(firm_name="Studio Demo"))
    kinds = [sheet.kind for sheet in sheets]
    for kind in SHEET_KINDS:
        assert kind in kinds, kind
    assert kinds.count("floor-plan") == len(house.storeys)
    assert kinds.count("elevation") == len(ELEVATION_ORDER) == 4
    assert kinds.count("section") == 1
    numbers = [sheet.number for sheet in sheets]
    assert numbers == sorted(numbers), "numbered in submission order"
    assert len(set(numbers)) == len(numbers), "no duplicate sheet numbers"
    for sheet in sheets:
        sheet.validate()
        assert sheet.frame.title_block.sheet_number == sheet.number
        assert sheet.frame.title_block.scale_label == sheet.scale.label


def test_sheet_ids_are_deterministic_so_annotations_survive_regeneration():
    """§7's regeneration contract: an annotation's ``sheet_id`` must still resolve."""
    house = two_room_house()
    first = build_sheet_set(house)
    second = build_sheet_set(house)
    assert [sheet.id for sheet in first] == [sheet.id for sheet in second]
    # Derived from the kind and the element, with no minted component: a floor plan's id
    # is "floor-plan-<storeyId>", so it survives a regeneration of the same model.
    for sheet in first:
        assert sheet.id.startswith(sheet.kind)
    plan = next(sheet for sheet in first if sheet.kind == "floor-plan")
    assert plan.id == "floor-plan-%s" % STOREY_ID

    note = _Anchored(plan.id)
    assert len(with_annotations(plan, [note]).annotations) == 1
    assert with_annotations(plan, [_Anchored("some-other-sheet")]).annotations == ()


class _Anchored:
    """Stand-in for a ``ProjectDoc.annotations`` entry — all ``with_annotations`` reads.

    The real ``garh_model.model.Annotation`` carries a payload and an anchor as well; the
    join under test is only on ``sheet_id``, and building a full document annotation here
    would test ``fold``, not this.
    """

    def __init__(self, sheet_id: str) -> None:
        self.sheet_id = sheet_id


def test_include_filters_the_set_without_renumbering_it():
    """A job that asks for only the plans must not renumber them."""
    house = two_room_house()
    full = build_sheet_set(house)
    plans_only = build_sheet_set(house, include=("floor-plan",))
    full_plans = [sheet for sheet in full if sheet.kind == "floor-plan"]
    assert [sheet.number for sheet in plans_only] == [sheet.number for sheet in full_plans]
    assert [sheet.id for sheet in plans_only] == [sheet.id for sheet in full_plans]


def test_section_line_runs_along_the_stair_flight():
    """§7: "section line auto-chosen through stair flight" — along it, not across it."""
    from garh_model.fold import apply_group
    from garh_model.model import DEFAULTS
    from garh_model.ops import op
    from garh_model.testing import fixed_id

    doc = apply_group(
        make_two_room_plan_with_openings(),
        [
            op(
                "stair.add",
                id=fixed_id("stair", "ST1"),
                storeyId=STOREY_ID,
                kind="straight",
                origin={"x": 3400, "y": 300},
                direction="N",
                riserMm=167,
                treadMm=200,
                widthMm=DEFAULTS.stair_width_mm,
                risersCount=18,
                landing=None,
            )
        ],
    ).model
    line = section_line_through_stair(doc.house)
    assert line is not None
    (x1, y1), (x2, y2) = line
    assert x1 == x2 == 3400 + DEFAULTS.stair_width_mm // 2, "through the flight's centre"
    assert y1 < 0 and y2 > 4000, "running past the building at both ends"

    # And the plans show a marker on that same line, so the plan and the section agree.
    sheets = build_sheet_set(doc.house)
    options = plan_options_for(sheets, north_deg=doc.plot.north_deg)
    assert options.section_markers and options.section_markers[0].a == line[0]


def test_section_falls_back_to_the_middle_when_there_is_no_stair():
    line = section_line_through_stair(two_room_house())
    assert line is not None
    (x1, _y1), (x2, _y2) = line
    assert x1 == x2 == 3000, "the middle of a 0..6000 building"


def test_a_house_with_no_walls_has_no_section_line():
    from garh_model.testing import make_empty_doc

    assert section_line_through_stair(make_empty_doc().house) is None


TESTS = [value for name, value in sorted(globals().items()) if name.startswith("test_")]


def _main() -> int:
    failures = []
    for test in TESTS:
        try:
            test()
        except Exception as exc:  # noqa: BLE001 - a test runner reports everything
            failures.append((test.__name__, exc))
            print("FAIL %s: %s: %s" % (test.__name__, type(exc).__name__, exc))
        else:
            print("ok   %s" % test.__name__)
    print("")
    print(
        "%d passed, %d failed (stubbed: %s)"
        % (len(TESTS) - len(failures), len(failures), ", ".join(STUBBED) or "nothing")
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
