"""Sizing the rooms a client asked for but did not dimension.

Nobody says "the master bedroom is 13.5 m² with a 3.3 m minimum width". Something has
to turn "3BHK with a pooja room" into numbers, because the solver cannot tile a room
that has no size — and before this module nothing did. The parser returned
``{"type": "bedroom", "count": 2}``, the program layer read
``int(raw.get("minAreaMm2") or 0)``, and every room reached Stage A as a zero-area
rectangle. The job then finished "succeeded" with no options, which is the worst
possible way to fail: the architect waits, and is told nothing is wrong.

The load-bearing property is WHERE each number comes from. A minimum is law and comes
from the rule pack — the same file the compliance tab cites — because a product
selling citable compliance must not hold a second opinion about the smallest legal
bedroom. A target is a judgement and lives here, as an editable assumption.
"""

from __future__ import annotations

import json
import os

import pytest

from services.llm.room_defaults import (
    TARGET_RATIOS,
    default_for,
    legal_minimums,
    size_rooms,
)

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))


def _pack_value(rule_id: str) -> int:
    with open(os.path.join(_ROOT, "rulepacks", "nbc-core.json"), encoding="utf-8") as handle:
        pack = json.load(handle)
    rule = next(r for r in pack["rules"] if r["id"] == rule_id)
    check = rule["check"]
    return int(check.get("valueMm2") or check.get("valueMm"))


# ===========================================================================
# The minimum is the rule pack's, not a second opinion
# ===========================================================================
def test_the_habitable_minimum_is_read_from_the_pack_not_restated() -> None:
    """If this module carried its own 9.5 m², a pack edit would leave the brief and the
    compliance tab disagreeing about the same room."""
    assert default_for("bedroom").min_area_mm2 == _pack_value("nbc.room.habitable.area.min")
    assert default_for("kitchen").min_area_mm2 == _pack_value("nbc.room.kitchen.area.min")
    assert default_for("bath_wc").min_area_mm2 == _pack_value("nbc.room.bath_wc.area.min")


def test_every_minimum_carries_the_clause_it_came_from() -> None:
    """An assumption chip that cannot cite itself is a number the architect has to take
    on trust, which is the one thing this product does not ask of them."""
    for room_type in ("bedroom", "kitchen", "bath_wc", "living_dining"):
        assert default_for(room_type).cite, room_type


def test_service_rooms_are_not_held_to_the_habitable_floor() -> None:
    """A pooja room or a utility is not a habitable room.

    Sizing them off the 9.5 m² habitable minimum would make an ordinary Indian house
    illegal on paper — and would push a 3BHK off a 30 x 40 ft plot for no reason.
    """
    habitable = _pack_value("nbc.room.habitable.area.min")
    for room_type in ("pooja", "utility", "store"):
        assert default_for(room_type).min_area_mm2 < habitable, room_type


def test_an_unknown_room_type_is_treated_as_habitable() -> None:
    """The conservative answer. A room this module cannot name might be lived in, and
    under-sizing it is the failure that reaches a municipal counter."""
    assert default_for("snug").min_area_mm2 == _pack_value("nbc.room.habitable.area.min")


# ===========================================================================
# The target is a judgement, and never below the law
# ===========================================================================
def test_no_target_is_ever_below_its_own_legal_minimum() -> None:
    """The invariant that makes the defaults safe to ship: aiming below the code would
    generate a plan that cannot pass the check the same product is about to run."""
    for room_type in TARGET_RATIOS:
        sized = default_for(room_type)
        assert sized.target_area_mm2 >= sized.min_area_mm2, room_type


def test_a_master_bedroom_is_given_more_room_than_a_second_bedroom() -> None:
    """Not arbitrary: the master takes a wardrobe wall and a bed you can walk both
    sides of. If these ever equalise, the programme has stopped meaning anything."""
    assert default_for("bedroom_master").target_area_mm2 > default_for("bedroom").target_area_mm2


def test_the_targets_land_where_an_architect_put_them_by_hand() -> None:
    """Derived, then checked against the seeded demo brief — which a person wrote.

    The demo's numbers were chosen by hand for a plan that provably solves. Deriving
    the same figures from the code minimum is the evidence that the ratios are practice
    and not invention; it is also why this test names them rather than importing them.
    """
    assert default_for("bedroom_master").target_area_mm2 == 13_500_000
    assert default_for("bedroom").target_area_mm2 == 11_500_000
    assert default_for("living_dining").target_area_mm2 == 20_000_000
    assert default_for("kitchen").target_area_mm2 == 8_000_000
    assert default_for("bath_wc").target_area_mm2 == 4_200_000


def test_practice_width_beats_the_legal_width_where_the_code_is_too_generous() -> None:
    """A 2.4 m bedroom is legal and unusable — a bed plus a wardrobe does not fit."""
    assert _pack_value("nbc.room.habitable.width.min") == 2_400
    assert default_for("bedroom").min_width_mm == 3_000


# ===========================================================================
# Filling a brief in
# ===========================================================================
def test_a_room_with_no_sizes_gets_all_three() -> None:
    rooms, _ = size_rooms([{"type": "bedroom", "count": 2}])
    assert rooms[0]["minAreaMm2"] > 0
    assert rooms[0]["targetAreaMm2"] > 0
    assert rooms[0]["minWidthMm"] > 0


def test_a_size_the_client_stated_is_never_overwritten() -> None:
    """The client's own number always wins, even a strange one. Overruling it would
    make the brief lie about what they asked for."""
    rooms, _ = size_rooms([{"type": "kitchen", "count": 1, "minAreaMm2": 7_000_000}])
    assert rooms[0]["minAreaMm2"] == 7_000_000
    assert rooms[0]["targetAreaMm2"] > 0, "the fields it did NOT state are still filled"


def test_every_default_becomes_a_visible_assumption() -> None:
    """Golden rule 4: anything not stated is an assumption, never a silence.

    A default the architect cannot see is a number they will discover on a drawing.
    """
    rooms, assumptions = size_rooms([{"type": "bedroom", "count": 1}])
    assert len(assumptions) == 3
    fields = {item["field"] for item in assumptions}
    assert fields == {
        "brief.rooms.bedroom.minAreaMm2",
        "brief.rooms.bedroom.targetAreaMm2",
        "brief.rooms.bedroom.minWidthMm",
    }
    for item in assumptions:
        assert item["reason"], "an assumption with no reason is a number nobody can argue with"


def test_a_stated_size_produces_no_assumption_for_that_field() -> None:
    """Negative control on the test above: chips must mean "we filled this in". If a
    field the client typed is also chipped, the chips stop carrying information."""
    _rooms, assumptions = size_rooms([{"type": "bedroom", "count": 1, "minAreaMm2": 12_000_000}])
    fields = {item["field"] for item in assumptions}
    assert "brief.rooms.bedroom.minAreaMm2" not in fields
    assert "brief.rooms.bedroom.targetAreaMm2" in fields


def test_a_room_it_cannot_read_is_passed_through_rather_than_dropped() -> None:
    """A brief that loses a room the client asked for is worse than one with a room
    this module could not size."""
    rooms, _ = size_rooms([{"no_type": True}, "nonsense", {"type": "kitchen"}])
    assert len(rooms) == 3


def test_a_non_list_is_not_an_error() -> None:
    assert size_rooms(None) == ([], [])
    assert size_rooms("rooms") == ([], [])


@pytest.mark.parametrize("room_type", sorted(TARGET_RATIOS))
def test_every_type_in_the_ratio_table_can_actually_be_sized(room_type: str) -> None:
    """A ratio for a type the minimum table cannot resolve would be a default that
    never applies — the shape of a rule that goes quietly inert."""
    sized = default_for(room_type)
    assert sized.min_area_mm2 > 0 and sized.min_width_mm > 0 and sized.cite


def test_the_pack_is_read_once() -> None:
    assert legal_minimums() is legal_minimums()
