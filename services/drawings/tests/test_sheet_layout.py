"""The sheet layout, and the defect it was built on top of (D-3).

Every practice has its own sheet composition: an A1 plotter and a folding habit, a
220 mm title block because the letterhead carries three lines of statutory
registration, portrait for a deep narrow plot. Until this existed there was one
hard-coded A2 landscape frame with 20/10/10/10 margins.

THE DEFECT IS THE FIRST TEST IN THIS FILE, and it is worth stating plainly because of
its shape. ``sheetSize`` was a field on the API, on the firm's preferences, on the job
payload and on the recorded result — and it reached nothing. Every builder called
``default_frame()`` with no argument, so a set requested on A1 came out A2 AND recorded
itself as A2. Internally consistent, and wrong on paper. Nothing in the system could
notice; only a tape measure on a plot could.

That is why the paper test asserts the frame's own dimensions in millimetres rather than
the recorded label. The label was never the thing that was wrong.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for _path in (_ROOT, os.path.join(_ROOT, "apps", "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pytest  # noqa: E402

from services.dev_stubs import install_worker_dep_stubs  # noqa: E402

install_worker_dep_stubs()

from garh_model.testing import make_two_room_plan_with_openings  # noqa: E402

from services.drawings.pipeline import SheetBundle, build_sheets  # noqa: E402
from services.drawings.sheets import DEFAULT_SHEET_LAYOUT, SheetLayout  # noqa: E402


def _sheet(**payload):
    doc = make_two_room_plan_with_openings()
    bundle = SheetBundle.from_payload({"kinds": ["floor"], **payload}, document=doc.to_json())
    result = build_sheets(bundle)
    assert result.sheets, result.skipped
    return result.sheets[0]


# ===========================================================================
# The defect
# ===========================================================================
@pytest.mark.parametrize(
    ("paper", "width_mm", "height_mm"),
    [("A4", 297, 210), ("A3", 420, 297), ("A2", 594, 420), ("A1", 841, 594)],
)
def test_the_requested_paper_reaches_the_actual_frame(
    paper: str, width_mm: int, height_mm: int
) -> None:
    """Measured in millimetres, not read off the label.

    Before D-3 the label said A1 and the sheet was 594x420. Asserting the label would
    have passed then and would pass now, which makes it not a test.
    """
    frame = _sheet(sheetSize=paper).drawing.sheet.frame
    assert (frame.paper.width_mm, frame.paper.height_mm) == (width_mm, height_mm)


def test_the_recorded_paper_agrees_with_the_frame_that_was_drawn() -> None:
    """The two must not be able to drift again."""
    sheet = _sheet(sheetSize="A1")
    assert sheet.paper == sheet.drawing.sheet.frame.paper.name == "A1"


def test_a_smaller_sheet_chooses_a_smaller_scale() -> None:
    """The consequence that proves the frame is really being used.

    The renderer fits the building to the drawable area. If the paper were still A2
    underneath, the scale would not move — so this catches a layout that is carried but
    ignored, which is exactly what was happening.
    """
    big = _sheet(sheetSize="A1").scale_denominator
    small = _sheet(sheetSize="A4").scale_denominator
    assert small > big, "a smaller sheet must draw the same building smaller"


# ===========================================================================
# The rest of the composition
# ===========================================================================
def test_orientation_turns_the_sheet() -> None:
    frame = _sheet(sheetSize="A3", sheetLayout={"orientation": "portrait"}).drawing.sheet.frame
    assert frame.paper.height_mm > frame.paper.width_mm


def test_margins_and_the_title_block_box_reach_the_frame() -> None:
    frame = _sheet(
        sheetLayout={
            "marginLeftMm": 35,
            "marginRightMm": 15,
            "titleBlockWidthMm": 220,
            "titleBlockHeightMm": 75,
        }
    ).drawing.sheet.frame
    assert frame.margin_left_mm == 35
    assert frame.margin_right_mm == 15
    assert frame.title_block_width_mm == 220
    assert frame.title_block_height_mm == 75


def test_a_wider_title_block_leaves_less_room_to_draw() -> None:
    """Negative control on the test above: the numbers must have consequences, not just
    survive the trip."""
    narrow = _sheet(sheetLayout={"titleBlockWidthMm": 120}).drawing.sheet.frame
    wide = _sheet(sheetLayout={"titleBlockWidthMm": 300}).drawing.sheet.frame
    assert narrow.drawable_width_mm() == wide.drawable_width_mm(), "the drawable area is the same"
    # ...but the title block occupies more of it, so its origin moves left.
    assert wide.title_block_origin_mm()[0] < narrow.title_block_origin_mm()[0]


def test_the_request_wins_over_the_firm_template() -> None:
    """A caller that names A1 gets A1 even when the firm's letterhead says A2 —
    the size is the thing they just chose."""
    frame = _sheet(sheetSize="A1", sheetLayout={"paper": "A4"}).drawing.sheet.frame
    assert frame.paper.name == "A1"


def test_no_layout_at_all_is_the_house_style() -> None:
    """Every existing caller keeps exactly the sheet it had before."""
    frame = _sheet().drawing.sheet.frame
    assert frame.paper.name == "A2"
    assert (frame.margin_left_mm, frame.margin_right_mm) == (20, 10)
    assert (frame.title_block_width_mm, frame.title_block_height_mm) == (180, 60)
    assert DEFAULT_SHEET_LAYOUT.paper == "A2"


# ===========================================================================
# Refusals — a layout with nowhere to draw
# ===========================================================================
def test_a_title_block_wider_than_the_paper_refuses() -> None:
    """Otherwise the renderer scales the building down to fit what is left, and the
    architect finds out on a plot they have already paid for."""
    with pytest.raises(ValueError, match="does not fit"):
        SheetLayout(paper="A4", title_block_width_mm=400).validate()


def test_margins_that_eat_the_sheet_refuse() -> None:
    with pytest.raises(ValueError, match="no drawable area"):
        SheetLayout(paper="A4", margin_left_mm=200, margin_right_mm=200).validate()


def test_a_title_block_taller_than_the_paper_refuses() -> None:
    with pytest.raises(ValueError, match="does not fit"):
        SheetLayout(paper="A4", title_block_height_mm=300).validate()


def test_negative_control_a_tight_but_workable_layout_is_accepted() -> None:
    """Prove the refusals discriminate rather than rejecting anything unusual.

    A4 landscape is 297x210; margins of 10 all round leave 277x190, and a 270x55 title
    block fits inside it. Cramped, legal, and someone's house style.
    """
    layout = SheetLayout(
        paper="A4",
        margin_left_mm=10,
        margin_right_mm=10,
        margin_top_mm=10,
        margin_bottom_mm=10,
        title_block_width_mm=270,
        title_block_height_mm=55,
    )
    layout.validate()
    assert layout.frame().drawable_width_mm() == 277


def test_an_unknown_paper_size_names_the_ones_that_exist() -> None:
    with pytest.raises(ValueError, match="A1"):
        SheetLayout(paper="Letter").validate()


def test_an_unknown_orientation_refuses() -> None:
    with pytest.raises(ValueError, match="landscape or portrait"):
        SheetLayout(orientation="diagonal").validate()


# ===========================================================================
# Round trip
# ===========================================================================
def test_a_layout_survives_json_in_both_directions() -> None:
    layout = SheetLayout(paper="A1", orientation="portrait", margin_left_mm=25)
    assert SheetLayout.from_json(layout.to_json()) == layout


def test_a_partial_payload_falls_back_field_by_field() -> None:
    """An absent field means "the house default", never "invalid" — a firm that saves
    only its paper size must not have its margins zeroed."""
    layout = SheetLayout.from_json({"paper": "A3"})
    assert layout.paper == "A3"
    assert layout.margin_left_mm == DEFAULT_SHEET_LAYOUT.margin_left_mm
    assert layout.title_block_width_mm == DEFAULT_SHEET_LAYOUT.title_block_width_mm
