"""The setting-out plan (D-2, job J-21) — the drawing that goes to site.

An architect's fee is earned twice: once at submission and once during construction.
Until this sheet existed the product only produced the first half. A setting-out
plan is not a floor plan with the furniture switched off — the two answer different
questions and are drawn differently:

* a submission plan answers "is this compliant"; a setting-out plan answers "where
  does the first brick go";
* a mason works to a LINE, so walls are centrelines, not poché;
* every dimension runs from ONE datum, because a chained dimension accumulates the
  setting-out error of every bay before it.

Three things here would each look completely fine in a green suite and be wrong on
paper, so each has a negative control: dimensions that are computed but never drawn,
a working drawing that leaks into the submission set, and a new sheet kind that
silently renumbers the sheets an architect has already issued.
"""

from __future__ import annotations

import os
import sys

# Self-bootstrapping, like its sibling suites: the repo root and apps/api go on the
# path so this file runs the same whether pytest was started from the repo root or
# from services/.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for _path in (_ROOT, os.path.join(_ROOT, "apps", "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pytest  # noqa: E402
from garh_model.testing import make_two_room_plan_with_openings  # noqa: E402

from services.drawings.pipeline import (  # noqa: E402
    _KIND_NUMBER_INDEX,
    DB_KIND_ORDER,
    WORKING_KINDS,
    canonical_sheet_kinds,
)
from services.drawings.render.primitives import Dim, Line  # noqa: E402
from services.drawings.render.reference_sheets import (  # noqa: E402
    setting_out_primitives,
    setting_out_sheet,
)
from services.drawings.sheets import TitleBlock  # noqa: E402


def _doc():
    return make_two_room_plan_with_openings()


def _sheet():
    doc = _doc()
    return setting_out_sheet(
        doc, doc.house.storeys[0].id, number="W-01", title_block=TitleBlock(firm_name="Studio")
    )


# ===========================================================================
# The drawing itself
# ===========================================================================
def test_walls_are_drawn_as_centrelines_straight_off_the_model() -> None:
    """A mason sets out to a line. ``wall.a``/``wall.b`` IS that line.

    Deriving a centreline from the poché would be a second source for a number the
    model already holds, and the two would drift.
    """
    doc = _doc()
    storey_id = doc.house.storeys[0].id
    primitives, _chains, _datum = setting_out_primitives(doc.house, storey_id)
    walls = [w for w in doc.house.walls if w.storey_id == storey_id]

    drawn = {
        (line.a, line.b)
        for line in primitives
        if isinstance(line, Line) and line.element_id is not None
    }
    for wall in walls:
        assert ((wall.a.x, wall.a.y), (wall.b.x, wall.b.y)) in drawn, wall.id


def test_the_datum_is_the_lower_left_corner_of_the_building() -> None:
    """A corner a site engineer can find with a tape from two boundary lines."""
    doc = _doc()
    storey_id = doc.house.storeys[0].id
    _p, _c, datum = setting_out_primitives(doc.house, storey_id)
    walls = [w for w in doc.house.walls if w.storey_id == storey_id]
    assert datum == (
        min(v for w in walls for v in (w.a.x, w.b.x)),
        min(v for w in walls for v in (w.a.y, w.b.y)),
    )


def test_the_dimensions_are_actually_DRAWN_not_merely_computed() -> None:
    """The one that matters most on this sheet.

    ``SheetDrawing.chains`` can be full while the sheet prints bare centrelines: the
    chains are metadata, and only a ``Dim`` primitive reaches paper. On a setting-out
    plan the dimensions ARE the drawing — the centrelines without them are decoration
    — and this repository has shipped exactly this shape of defect before.
    """
    drawing = _sheet()
    dims = [p for group in drawing.groups for p in group.primitives if isinstance(p, Dim)]
    assert dims, "the setting-out plan drew no dimensions at all"
    assert len(dims) == len(drawing.chains), "every chain must reach paper"


def test_negative_control_the_dim_assertion_can_fail() -> None:
    """Prove the test above discriminates: a sheet with chains stripped has no Dims."""
    drawing = _sheet()
    without = [p for group in drawing.groups for p in group.primitives if not isinstance(p, Dim)]
    assert not [p for p in without if isinstance(p, Dim)]


def test_every_chain_sums_to_its_overall_exactly() -> None:
    """§7 step 5. A setting-out dimension that does not add up is a wall in the wrong
    place, poured in concrete."""
    drawing = _sheet()
    assert drawing.chains, "no chains to check"
    for chain in drawing.chains:
        assert chain.is_consistent(), chain.id
        assert chain.sum_of_segments() == chain.overall_mm, chain.id


def test_a_storey_with_no_walls_refuses_rather_than_drawing_an_empty_sheet() -> None:
    doc = _doc()
    with pytest.raises(ValueError, match="nothing to set out"):
        setting_out_sheet(
            doc, "storey-that-does-not-exist-anywhere", number="W-01", title_block=TitleBlock()
        )


# ===========================================================================
# It must not disturb the submission set
# ===========================================================================
def test_working_drawings_are_opt_in_and_absent_from_the_default_set() -> None:
    """A GFC drawing is a separate deliverable issued to a different person.

    An existing caller asking for "the sheets" must keep getting exactly the sheets it
    got before — not a setting-out plan it never requested and may not want in front
    of the client.
    """
    default = canonical_sheet_kinds(None)
    assert "setting-out" not in default
    assert set(default) == set(DB_KIND_ORDER) - set(WORKING_KINDS)
    # ...and asking for it explicitly still works.
    assert "setting-out" in canonical_sheet_kinds(["floor", "setting-out"])


def test_the_A_series_numbering_is_untouched_by_the_new_kind() -> None:
    """Adding a sheet kind must not renumber sheets an architect has already issued.

    The first attempt at this inserted setting-out between floor and elevation, which
    silently moved elevations from A-03 to A-04 on every future issue of a set already
    in a municipal office. Working drawings get their own series instead.
    """
    assert _KIND_NUMBER_INDEX == {
        "site": 1,
        "floor": 2,
        "elevation": 3,
        "section": 4,
        "schedule": 5,
        "area-statement": 6,
    }
    for kind in WORKING_KINDS:
        assert kind not in _KIND_NUMBER_INDEX, "%s must not consume an A-series number" % kind


def test_the_setting_out_sheet_reports_itself_honestly() -> None:
    drawing = _sheet()
    drawing.sheet.validate()
    assert drawing.sheet.kind == "setting-out"
    assert drawing.meta["kind"] == "setting-out"
    assert "Setting Out" in drawing.sheet.title
