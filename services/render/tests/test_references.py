"""The inspiration board: which pictures inform a render, and what to ask first.

An architect collects references — a kitchen the client loves, a facade from a
magazine, a hotel bathroom. Before this the product took one picture's FILENAME and
never read it. There was nothing to say which part of the house a picture was for, what
to take from it, or what to leave.

The load-bearing behaviour is not the prompt text — it is the questions. A render is a
thing a client is shown; "which kitchen did you mean" has to be settled before it is
made, not in the meeting. So the tests that matter are the ones proving a question is
raised, and equally the ones proving a question is NOT raised when there is nothing to
ask, because a product that questions everything gets its questions dismissed.
"""

from __future__ import annotations

import pytest

from services.render.references import (
    INTENTS,
    SCOPES,
    Reference,
    applicable_references,
    find_conflicts,
    reference_prompt,
)
from services.render.types import PRESETS


def preset(preset_id: str):
    return PRESETS[preset_id]


EXTERIOR = preset("exterior-street-day")
INTERIOR = next(p for p in PRESETS.values() if p.scene == "interior")


def ref(rid: str, scope: str, **kw) -> Reference:
    return Reference(id=rid, label=kw.pop("label", rid), scope=scope, **kw)  # type: ignore[arg-type]


# ===========================================================================
# Which references a view can use
# ===========================================================================
def test_a_kitchen_picture_does_not_inform_a_street_elevation() -> None:
    """The whole reason `where` is asked for. Without it, a kitchen reference bleeds
    into an exterior prompt and the render comes back with a kitchen worktop rendered
    onto the porch."""
    kitchen = ref("k", "kitchen", why="the cabinet material")
    assert applicable_references([kitchen], EXTERIOR) == ()
    assert applicable_references([kitchen], INTERIOR) == (kitchen,)


def test_a_facade_picture_does_not_inform_an_interior() -> None:
    facade = ref("f", "facade", why="the chajja depth")
    assert applicable_references([facade], INTERIOR) == ()
    assert applicable_references([facade], EXTERIOR) == (facade,)


def test_whole_house_and_material_speak_to_both() -> None:
    """A material is a material wherever it is seen, and "the whole house feels like
    this" is the most common thing a client actually says."""
    for scope in ("whole-house", "material"):
        item = ref("x", scope, why="warm grey stone")
        assert applicable_references([item], EXTERIOR) == (item,)
        assert applicable_references([item], INTERIOR) == (item,)


def test_an_unannotated_reference_informs_nothing() -> None:
    """An upload with no instruction cannot steer anything — and it is raised as a
    question rather than dropped, see below."""
    assert applicable_references([ref("u", "kitchen")], INTERIOR) == ()


def test_the_architects_order_is_preserved() -> None:
    """The only ranking this module has any business applying. Inventing one would
    silently prefer a picture the architect did not prefer."""
    a, b = ref("a", "material", why="one"), ref("b", "material", why="two")
    assert applicable_references([b, a], EXTERIOR) == (b, a)


# ===========================================================================
# The questions — the point of the feature
# ===========================================================================
def test_two_match_references_on_one_scope_is_a_question() -> None:
    """ "Which kitchen did you mean" — the case the user described. Only one can be
    followed closely."""
    conflicts = find_conflicts(
        [
            ref(
                "a",
                "kitchen",
                label="Client's Pinterest kitchen",
                why="walnut cabinets",
                intent="match",
            ),
            ref("b", "kitchen", label="Hotel kitchen", why="white marble island", intent="match"),
        ],
        INTERIOR,
    )
    competing = [c for c in conflicts if c.kind == "competing"]
    assert len(competing) == 1
    assert set(competing[0].reference_ids) == {"a", "b"}
    assert "Client's Pinterest kitchen" in competing[0].question
    assert "Hotel kitchen" in competing[0].question


def test_two_GUIDE_references_on_one_scope_is_not_a_question() -> None:
    """Negative control, and the one that keeps the questions worth reading. Two
    pictures that both inform the feel of a kitchen is normal practice — asking about
    it would teach the architect to dismiss the prompt."""
    conflicts = find_conflicts(
        [
            ref("a", "kitchen", why="walnut cabinets", intent="guide"),
            ref("b", "kitchen", why="a marble island", intent="guide"),
        ],
        INTERIOR,
    )
    assert [c for c in conflicts if c.kind == "competing"] == []


def test_match_references_on_DIFFERENT_scopes_do_not_compete() -> None:
    conflicts = find_conflicts(
        [
            ref("a", "kitchen", why="walnut", intent="match"),
            ref("b", "bathroom", why="terrazzo", intent="match"),
        ],
        INTERIOR,
    )
    assert [c for c in conflicts if c.kind == "competing"] == []


def test_a_reference_that_cannot_apply_is_raised_not_dropped() -> None:
    """The architect chose that picture. Silently ignoring it is how a person concludes
    the board does nothing."""
    conflicts = find_conflicts([ref("b", "bathroom", label="Hotel bath", why="terrazzo")], EXTERIOR)
    out = [c for c in conflicts if c.kind == "out-of-view"]
    assert len(out) == 1
    assert "Hotel bath" in out[0].question
    assert out[0].default, "a question with no stated default is one people dismiss"


def test_an_unannotated_upload_is_asked_about() -> None:
    conflicts = find_conflicts([ref("u", "kitchen", label="IMG_2831")], INTERIOR)
    unusable = [c for c in conflicts if c.kind == "unusable"]
    assert len(unusable) == 1
    assert "IMG_2831" in unusable[0].question


def test_a_reference_with_only_an_IGNORE_is_usable() -> None:
    """ "Not like this" is a complete instruction, and it is the one clients give most
    often. Treating it as unannotated would throw away the most useful thing on the
    board."""
    conflicts = find_conflicts([ref("n", "kitchen", ignore="the glossy finish")], INTERIOR)
    assert [c for c in conflicts if c.kind == "unusable"] == []


def test_a_well_formed_board_raises_nothing() -> None:
    """The negative control for the whole feature. If this ever starts asking, the
    questions have stopped meaning anything."""
    assert (
        find_conflicts(
            [
                ref("b", "facade", why="deep chajjas", intent="match"),
                ref("c", "material", why="warm grey stone", intent="guide"),
            ],
            EXTERIOR,
        )
        == ()
    )


# ===========================================================================
# What reaches the prompt
# ===========================================================================
def test_why_becomes_something_to_draw_and_ignore_something_not_to() -> None:
    positive, negative = reference_prompt(
        [ref("a", "material", why="warm grey stone", ignore="mirror polish")], EXTERIOR
    )
    assert "warm grey stone" in positive
    assert "mirror polish" in negative


def test_an_avoid_reference_only_reaches_the_negative_side() -> None:
    """ "Avoid" is not a weaker "guide" — it is the opposite. Putting its `why` in the
    positive prompt would render the exact thing the client rejected."""
    positive, negative = reference_prompt(
        [ref("a", "facade", why="blue glass curtain wall", intent="avoid")], EXTERIOR
    )
    assert "blue glass" not in positive
    assert "blue glass" in negative


def test_match_and_guide_read_differently_in_the_prompt() -> None:
    match_positive, _ = reference_prompt(
        [ref("a", "facade", why="deep chajjas", intent="match")], EXTERIOR
    )
    guide_positive, _ = reference_prompt(
        [ref("a", "facade", why="deep chajjas", intent="guide")], EXTERIOR
    )
    assert match_positive != guide_positive
    assert "closely" in match_positive


def test_an_empty_board_contributes_nothing() -> None:
    """The invariant that keeps every existing render reproducible: a project with no
    references must produce exactly the prompt it produced before this feature."""
    assert reference_prompt([], EXTERIOR) == ("", "")


def test_out_of_view_references_never_reach_the_prompt() -> None:
    positive, negative = reference_prompt(
        [ref("k", "kitchen", why="walnut cabinets", ignore="gloss")], EXTERIOR
    )
    assert positive == "" and negative == ""


# ===========================================================================
# The vocabularies
# ===========================================================================
@pytest.mark.parametrize("scope", SCOPES)
def test_every_scope_resolves_for_both_scenes(scope: str) -> None:
    """A scope that applied to neither scene would be a choice in the UI that silently
    disables the picture."""
    item = ref("x", scope, why="something")
    assert applicable_references([item], EXTERIOR) or applicable_references([item], INTERIOR)


@pytest.mark.parametrize("intent", INTENTS)
def test_every_intent_produces_prompt_text(intent: str) -> None:
    positive, negative = reference_prompt(
        [ref("x", "material", why="warm stone", intent=intent)], EXTERIOR
    )
    assert positive or negative, "intent %r contributes nothing" % intent
