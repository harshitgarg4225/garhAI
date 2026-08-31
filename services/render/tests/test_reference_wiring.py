"""The board actually reaches the render, and the render says which ones it used (§11).

``test_references.py`` proves the rules. This proves the WIRING — the join that
CLAUDE.md's fourth bug class is made of: a module that believes it is registered,
documents itself as integrated, and is never called. The furniture layer passed review
that way; every placed item was invisible to clicks.

So each test here starts from a job PAYLOAD, the shape the API actually enqueues, and
ends at something observable: the prompt string a provider receives, or the credit list
a finished render carries. Nothing is asserted about an intermediate object that could
be built correctly and then dropped.
"""

from __future__ import annotations

from services.render.handler import _references_from
from services.render.prompts import build_prompt
from services.render.references import Reference
from services.render.types import PRESETS, RenderRequest

_ELEVATION = "elevation-north-morning"
_INTERIOR = next(key for key, preset in PRESETS.items() if preset.scene == "interior")


def _payload_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "id": "ref-1",
        "label": "Client's verandah photo",
        "scope": "facade",
        "why": "the deep shaded verandah with slender columns",
        "ignore": "",
        "intent": "match",
    }
    entry.update(overrides)
    return entry


def _request(payload_references: list[dict[str, object]], preset: str) -> RenderRequest:
    """A request built the way the worker builds one: references parsed from a payload."""
    return RenderRequest(
        viewport_png=b"\x89PNG\r\n\x1a\n",
        mode="explore",
        preset=preset,
        seed=7,
        size=(1024, 768),
        references=_references_from(payload_references),
    )


# ---------------------------------------------------------------------------
# Payload → prompt
# ---------------------------------------------------------------------------


def test_a_named_reference_reaches_the_prompt_from_a_job_payload() -> None:
    """The architect's own words, carried from the enqueued payload into the prompt."""
    spec = build_prompt(_request([_payload_entry()], _ELEVATION))
    assert "deep shaded verandah with slender columns" in spec.positive
    assert "closely following" in spec.positive, "intent=match must read as follow closely"


def test_an_empty_board_produces_the_prompt_this_product_produced_before() -> None:
    """The one property that keeps every pre-§11 render reproducible.

    NEGATIVE CONTROL for the test above: if the wiring appended anything at all when
    the board is empty, this fails.
    """
    without = build_prompt(_request([], _ELEVATION))
    assert without.references_used == ()
    with_board = build_prompt(_request([_payload_entry()], _ELEVATION))
    assert with_board.positive != without.positive, (
        "if these are equal the board is not reaching the prompt and the test above "
        "is passing on the base template alone"
    )


def test_what_to_leave_out_reaches_the_negative_prompt() -> None:
    """ "Not like this" is what clients say most and no tool records. It must land."""
    spec = build_prompt(_request([_payload_entry(ignore="the glass balustrade")], _ELEVATION))
    assert "the glass balustrade" in spec.negative
    assert "the glass balustrade" not in spec.positive


def test_an_avoid_reference_steers_the_negative_side_only() -> None:
    spec = build_prompt(
        _request([_payload_entry(intent="avoid", why="mirror-glass curtain walling")], _ELEVATION)
    )
    assert "mirror-glass curtain walling" in spec.negative
    assert "mirror-glass curtain walling" not in spec.positive


def test_a_reference_for_another_view_contributes_nothing_here() -> None:
    """A kitchen picture cannot inform a street elevation, and must not try."""
    kitchen = _payload_entry(id="ref-k", scope="kitchen", why="walnut cabinet fronts")
    spec = build_prompt(_request([kitchen], _ELEVATION))
    assert "walnut" not in spec.positive
    assert spec.references_used == ()

    # NEGATIVE CONTROL: the same reference on an interior view MUST be used, or the
    # assertion above would also pass against a board that is wired to nothing.
    inside = build_prompt(_request([kitchen], _INTERIOR))
    assert "walnut cabinet fronts" in inside.positive
    assert [entry["id"] for entry in inside.references_used] == ["ref-k"]


# ---------------------------------------------------------------------------
# Payload → the credit list a finished render carries
# ---------------------------------------------------------------------------


def test_the_render_credits_the_references_it_followed_by_name() -> None:
    """ "Did it use my reference?" must have an answer on the render itself."""
    spec = build_prompt(_request([_payload_entry()], _ELEVATION))
    assert [entry["label"] for entry in spec.references_used] == ["Client's verandah photo"]
    assert spec.references_used[0]["intent"] == "match"
    assert spec.references_used[0]["id"] == "ref-1"


def test_the_credit_list_holds_no_prompt_text() -> None:
    """§13: ids and the architect's own labels travel; their instructions do not."""
    spec = build_prompt(_request([_payload_entry()], _ELEVATION))
    for entry in spec.references_used:
        assert set(entry) == {"id", "label", "intent"}
        assert "verandah" not in entry["intent"]
    assert "positiveChars" in spec.summary()
    assert spec.summary()["referencesUsed"] == 1


def test_the_credit_list_is_exactly_what_the_prompt_consumed() -> None:
    """One source. A credited reference must be one whose words are in the prompt.

    This is the assertion that would have caught a separately-derived credit list —
    the render UI saying "followed Reference A" while the prompt followed B.
    """
    board = [
        _payload_entry(id="a", label="A", why="a deep verandah", scope="facade"),
        _payload_entry(id="b", label="B", why="walnut fronts", scope="kitchen"),
        _payload_entry(id="c", label="C", why="", ignore="", scope="facade"),
    ]
    spec = build_prompt(_request(board, _ELEVATION))
    credited = {entry["id"] for entry in spec.references_used}
    assert credited == {"a"}, "only the applicable, annotated, in-view reference"
    assert "a deep verandah" in spec.positive
    assert "walnut fronts" not in spec.positive
    for entry in spec.references_used:
        source = next(r for r in board if r["id"] == entry["id"])
        assert str(source["why"]) in spec.positive


# ---------------------------------------------------------------------------
# The payload parser — a malformed board costs a reference, never the render
# ---------------------------------------------------------------------------


def test_a_scope_the_render_side_cannot_read_is_dropped_not_defaulted() -> None:
    """``applies_to`` answers True for anything it does not recognise.

    So an unreadable scope that survived parsing would leak into EVERY view. Dropping
    it is the safe answer; defaulting it to whole-house would be the unsafe one.
    """
    assert _references_from([_payload_entry(scope="roof-terrace")]) == ()
    assert _references_from([_payload_entry(intent="obey")]) == ()


def test_a_malformed_board_never_fails_the_render() -> None:
    assert _references_from(None) == ()
    assert _references_from("a string") == ()
    assert _references_from([None, 7, "x"]) == ()
    # The readable entries in a partly-broken list still come through.
    parsed = _references_from([None, _payload_entry(), {"scope": "facade"}])
    assert [r.id for r in parsed] == ["ref-1"]


def test_the_parser_produces_what_the_rules_module_consumes() -> None:
    """A shape mismatch here is the join going silently inert."""
    (parsed,) = _references_from([_payload_entry()])
    assert isinstance(parsed, Reference)
    assert parsed.to_json() == _payload_entry()
