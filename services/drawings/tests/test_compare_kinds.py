"""Two vocabularies for one diff, and why they must not become one (C-8).

``diff_models`` answers two different questions for two different readers:

* **A revision cloud** on a municipal sheet asks "what must a reviewer look at again".
  Furniture is not on a submission drawing at all, so a moved sofa raising a cloud on a
  sanction sheet is noise on a legal document.
* **A version compare** asks "what is different between these two options". An architect
  choosing between A and B absolutely cares that the furniture layout changed, and a
  compare that answered "no change" for two visibly different plans would be worse than
  no compare at all.

The failure mode this file exists to prevent is the two sets quietly becoming one — in
either direction. Widen the default and every sheet grows clouds around sofas; narrow the
compare and it lies by omission.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for _path in (_ROOT, os.path.join(_ROOT, "apps", "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from garh_model.fold import replay  # noqa: E402
from garh_model.ops import op  # noqa: E402
from garh_model.testing import fixed_id, make_two_room_plan_with_openings  # noqa: E402

from services.drawings.revisions import (  # noqa: E402
    COMPARE_KINDS,
    COMPARED_KINDS,
    EXCLUDED_KINDS,
    diff_models,
)


def _with_sofa(nudge: int = 0):
    """The two-room plan plus one furniture item, optionally moved."""
    doc = make_two_room_plan_with_openings()
    storey_id = doc.house.storeys[0].id
    return replay(
        [
            op(
                "furniture.set",
                action="place",
                id=fixed_id("furniture", "sofa"),
                storeyId=storey_id,
                catalogId="sofa-3seat",
                pt={"x": 1500 + nudge, "y": 1500},
                rotationDeg=0,
            )
        ],
        doc,
    )


def test_the_two_vocabularies_differ_by_exactly_furniture() -> None:
    assert set(COMPARE_KINDS) - set(COMPARED_KINDS) == {"furniture"}
    assert set(COMPARED_KINDS) - set(COMPARE_KINDS) == set()


def test_slabs_stay_out_of_both_and_the_reason_is_written_down() -> None:
    """An exclusion nobody can see is indistinguishable from a bug."""
    assert "slab" not in COMPARE_KINDS
    assert "slab" not in COMPARED_KINDS
    assert EXCLUDED_KINDS["slab"]


def test_a_moved_sofa_raises_NO_cloud_on_a_submission_sheet() -> None:
    """The default is the revision-cloud set, and it must stay that way.

    A cloud around a sofa on a sanction drawing sends a municipal reviewer looking for a
    change that is not on the drawing they are holding.
    """
    before = _with_sofa()
    after = _with_sofa(nudge=900)
    diff = diff_models(before, after)
    assert not diff.elements
    assert not diff.unplaced
    assert not diff, "the default diff must read as empty for a furniture-only change"


def test_but_a_version_compare_DOES_see_it() -> None:
    """The other half. Without this the compare lies by omission about two plans that
    are visibly different on screen."""
    before = _with_sofa()
    after = _with_sofa(nudge=900)
    diff = diff_models(before, after, kinds=COMPARE_KINDS)
    assert diff.counts().get("modified") == 1
    assert [kind for _id, kind, _change in diff.unplaced] == ["furniture"]


def test_furniture_is_reported_without_a_box_rather_than_at_an_invented_size() -> None:
    """A furniture instance carries a point and a rotation; its footprint is in the
    catalogue. Drawing a nominal square would put a shape on the overlay that is not the
    shape of the thing, so it is carried as unplaced — counted, never dropped."""
    before = _with_sofa()
    after = _with_sofa(nudge=900)
    diff = diff_models(before, after, kinds=COMPARE_KINDS)
    assert not diff.elements, "no box was invented"
    assert diff.unplaced, "and it was not silently discarded either"
    assert "not locatable" in diff.summary()


def test_a_wall_change_is_seen_by_both_vocabularies() -> None:
    """Negative control for the pair above: the difference is furniture, not that one of
    the two diffs sees nothing at all."""
    before = make_two_room_plan_with_openings()
    storey_id = before.house.storeys[0].id
    wall = next(w for w in before.house.walls if w.storey_id == storey_id)
    after = replay(
        [
            op(
                "wall.move",
                wallId=wall.id,
                a={"x": wall.a.x, "y": wall.a.y - 250},
                b={"x": wall.b.x, "y": wall.b.y - 250},
            )
        ],
        before,
    )
    assert diff_models(before, after).elements
    assert diff_models(before, after, kinds=COMPARE_KINDS).elements
