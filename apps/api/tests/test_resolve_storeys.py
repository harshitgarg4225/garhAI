"""How many storeys the solver is told to plan for.

The brief states this two ways. ``floorsAboveGround`` is G+n — the seeded demo writes
``1`` for a G+1 — and ``storeys`` is the total, which is what the brief parser emits for
the same house. Only the first was ever read.

The consequence was not a smaller house: it was NO house. A brief a user typed resolved
to one storey, so the whole 3BHK programme was piled onto the ground floor, Stage A
found it about 7 m² short of the buildable area after BBMP setbacks, and the run
returned zero options with the job still reporting "succeeded". The seeded demo escaped
it by writing the other spelling — which is why the demo was the only project in the
product that generated anything.

Fourth instance of the same class in this area: a field written under one name and read
under another, failing silently. See docs/first-run-verification.md.
"""

from __future__ import annotations

import pytest
from garh_api.solver_enqueue import MAX_SOLVER_STOREYS, _resolve_storeys


def resolve(brief=None, document=None, requested=None):
    return _resolve_storeys(requested, document or {}, brief or {})


def test_the_briefs_own_storeys_field_is_read() -> None:
    """The defect, as an assertion. This returned 1."""
    assert resolve({"storeys": 2}) == 2


def test_floors_above_ground_still_means_G_plus_n() -> None:
    """The demo's spelling must keep working — this is an addition, not a swap."""
    assert resolve({"floorsAboveGround": 1}) == 2
    assert resolve({"floorsAboveGround": 0}) == 1


def test_the_two_spellings_are_not_treated_as_interchangeable() -> None:
    """G+1 is two storeys. Reading `floorsAboveGround: 1` as one storey would build
    half the house; reading `storeys: 2` as three would break the height limit."""
    assert resolve({"floorsAboveGround": 1}) == resolve({"storeys": 2}) == 2


def test_floors_above_ground_wins_when_a_brief_carries_both() -> None:
    """Existing data keeps its existing meaning. A brief with both is the demo's shape
    plus a parser field, and the demo's answer must not change under it."""
    assert resolve({"floorsAboveGround": 1, "storeys": 5}) == 2


def test_an_explicit_request_beats_the_brief() -> None:
    assert resolve({"storeys": 2}, requested=3) == 3


def test_a_modelled_house_beats_the_brief() -> None:
    """Once storeys exist in the model, they are the truth — the brief is the wish."""
    document = {"house": {"storeys": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}}
    assert resolve({"storeys": 2}, document=document) == 3


def test_a_brief_that_says_nothing_is_one_storey() -> None:
    """Negative control: the fallback must stay, and must stay 1."""
    assert resolve({}) == 1


@pytest.mark.parametrize("value", [None, True, False, 0, -1, "2", 2.0])
def test_junk_never_becomes_a_storey_count(value) -> None:
    """`True` is the one that matters: `isinstance(True, int)` is True in Python, so a
    brief with `storeys: true` would otherwise plan a one-storey house from a boolean."""
    assert resolve({"storeys": value}) == 1


def test_the_cap_holds_for_both_spellings() -> None:
    assert resolve({"storeys": 99}) == MAX_SOLVER_STOREYS
    assert resolve({"floorsAboveGround": 99}) == MAX_SOLVER_STOREYS
