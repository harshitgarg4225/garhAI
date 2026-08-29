"""The structural grid (D-7, working drawing W-02).

A submission set that shows no structure reads as architecture-only, and a
contractor pricing it has nothing to price the frame from. This sheet gives the
grid a column layout implies: lettered lines, numbered lines, and every column drawn
at the size it actually is.

Two things here are quietly load-bearing and both have negative controls:

* **Clustering.** A column nudged half a brick during layout is still meant to be on
  its grid. With no tolerance every such column gets a line of its own, and a grid
  with eleven lines through six columns is a grid nobody can build from.
* **The lettering convention.** Vertical lines are lettered left to right and
  horizontals numbered bottom to top. Swap them and every "column at B/3" on a
  structural drawing points somewhere else — while the sheet still looks perfect.
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
from garh_model.fold import replay  # noqa: E402
from garh_model.ops import op  # noqa: E402
from garh_model.testing import fixed_id, make_two_room_plan_with_openings  # noqa: E402

from services.drawings.render.primitives import Polyline, Text  # noqa: E402
from services.drawings.render.reference_sheets import (  # noqa: E402
    GRID_TOLERANCE_MM,
    _grid_positions,
    structural_grid_primitives,
    structural_grid_sheet,
)
from services.drawings.sheets import TitleBlock  # noqa: E402

#: The nudge a column picks up when it is set out half a brick off its line. Inside
#: GRID_TOLERANCE_MM on purpose — that is the case the tolerance exists for.
NUDGE_MM = 40


def _framed(nudge: int = NUDGE_MM):
    """A 3x2 framed building with one column set out ``nudge`` off its grid line."""
    doc = make_two_room_plan_with_openings()
    storey_id = doc.house.storeys[0].id
    ops, n = [], 0
    for x in (0, 3000, 6000):
        for y in (0, 4000):
            n += 1
            ops.append(
                op(
                    "column.set",
                    action="add",
                    id=fixed_id("column", "C%03d" % n),
                    storeyId=storey_id,
                    pt={"x": x + (nudge if n == 3 else 0), "y": y},
                    sizeMm={"xMm": 230, "yMm": 230},
                )
            )
    return replay(ops, doc), storey_id


# ===========================================================================
# Clustering columns into grid lines
# ===========================================================================
def test_a_column_nudged_within_tolerance_stays_on_its_grid_line() -> None:
    doc, storey_id = _framed()
    raw = sorted({c.pt.x for c in doc.house.columns})
    assert len(raw) == 4, "the fixture must actually contain a nudged column"

    lines = _grid_positions([c.pt.x for c in doc.house.columns])
    assert len(lines) == 3, "three grid lines, not four: %r" % (lines,)


def test_negative_control_without_tolerance_the_nudge_becomes_its_own_grid_line() -> None:
    """Prove the test above discriminates rather than passing on any input."""
    doc, _ = _framed()
    lines = _grid_positions([c.pt.x for c in doc.house.columns], tolerance=0)
    assert len(lines) == 4, "with zero tolerance the nudged column must split its line"


def test_a_column_beyond_tolerance_gets_its_own_line() -> None:
    """The tolerance must not swallow a column that is genuinely somewhere else."""
    doc, _ = _framed(nudge=GRID_TOLERANCE_MM * 4)
    assert len(_grid_positions([c.pt.x for c in doc.house.columns])) == 4


def test_the_grid_line_is_the_mean_of_its_cluster_not_the_first_column() -> None:
    """Otherwise the grid inherits the setting-out error of whichever column was
    drawn first, and the engineer then dimensions everything from it."""
    assert _grid_positions([3000, 3040]) == [3020]
    assert _grid_positions([3040, 3000]) == [3020], "order must not change the answer"


# ===========================================================================
# The drawing convention
# ===========================================================================
def test_vertical_lines_are_lettered_left_to_right_and_horizontals_numbered_up() -> None:
    """The convention, asserted by reading the bubbles off the drawing.

    Get it backwards and every structural reference on the sheet points elsewhere,
    while the sheet itself still looks entirely correct.
    """
    doc, storey_id = _framed()
    primitives, _schedule = structural_grid_primitives(doc.house, storey_id)
    bubbles = [p for p in primitives if isinstance(p, Text)]

    letters = sorted(
        ((p.at[0], p.text) for p in bubbles if p.text.isalpha()), key=lambda item: item[0]
    )
    assert [text for _x, text in letters] == ["A", "B", "C"], "letters run left to right"

    numbers = sorted(
        ((p.at[1], p.text) for p in bubbles if p.text.isdigit()), key=lambda item: item[0]
    )
    assert [text for _y, text in numbers] == ["1", "2"], "numbers run bottom to top"


def test_the_schedule_reference_names_the_grid_the_column_sits_on() -> None:
    doc, storey_id = _framed()
    _primitives, schedule = structural_grid_primitives(doc.house, storey_id)
    assert sorted(row["ref"] for row in schedule) == ["A/1", "A/2", "B/1", "B/2", "C/1", "C/2"]
    # ...including the nudged one, which belongs to B and not to a fourth line.
    assert sum(1 for row in schedule if row["ref"].startswith("B")) == 2


def test_columns_are_drawn_at_their_real_size() -> None:
    """A structural plan that shows columns as dots hides the number being checked."""
    doc, storey_id = _framed()
    primitives, _schedule = structural_grid_primitives(doc.house, storey_id)
    boxes = [p for p in primitives if isinstance(p, Polyline) and p.element_id is not None]
    assert len(boxes) == len(doc.house.columns)
    for box in boxes:
        xs = [v[0] for v in box.vertices]
        ys = [v[1] for v in box.vertices]
        assert max(xs) - min(xs) == 230, "a 230 column must be drawn 230 wide"
        assert max(ys) - min(ys) == 230


# ===========================================================================
# Refusals
# ===========================================================================
def test_a_load_bearing_house_gets_no_structural_grid_and_says_why() -> None:
    """The common case, not an edge case.

    Most Indian residential work is load-bearing masonry. A "structural grid" for a
    house with no frame implies an engineer was involved when none was.
    """
    doc = make_two_room_plan_with_openings()
    with pytest.raises(ValueError, match="no columns"):
        structural_grid_sheet(doc, doc.house.storeys[0].id, number="W-02", title_block=TitleBlock())


def test_an_unknown_storey_refuses_by_name() -> None:
    doc, _ = _framed()
    with pytest.raises(ValueError, match="no storey"):
        structural_grid_sheet(doc, "storey-nope", number="W-02", title_block=TitleBlock())


def test_the_sheet_reports_itself_honestly() -> None:
    doc, storey_id = _framed()
    drawing = structural_grid_sheet(
        doc, storey_id, number="W-02", title_block=TitleBlock(firm_name="Studio")
    )
    drawing.sheet.validate()
    assert drawing.sheet.kind == "structural-grid"
    assert drawing.meta["kind"] == "structural-grid"
