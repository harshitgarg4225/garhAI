"""Moving a room downstairs when an upper floor will not tile.

The default programme puts every bedroom-ish room upstairs. On a generous plot that is
right; on a 30 x 40 ft G+1 it puts three bedrooms and two baths on one 7 x 9 m plate
that cannot be tiled — while the ground floor sits half empty — and the run returns
nothing at all. Measured: 0 of 6 stair anchors solved before, 3 of 6 after.

An architect's answer is not to shrink a bedroom. It is to put the guest room
downstairs, which is the ordinary arrangement in an Indian G+1 anyway, and it is exactly
what the seeded demo brief does by hand — the workaround that let the demo be the only
project in this product that ever generated anything.

The two properties that keep this honest, both negative-controlled below: the master
never moves, and the move happens only after a floor has actually failed.
"""

from __future__ import annotations

from services.solver.program import ProgramRoom, RoomProgram, rebalance_off_storey


def room(key: str, room_type: str, storey: int | None, *, packed: bool = True) -> ProgramRoom:
    return ProgramRoom(
        key=key,
        room_type=room_type,
        min_area_mm2=9_500_000,
        target_area_mm2=11_500_000,
        max_area_mm2=0,
        min_width_mm=3_000,
        max_aspect_x100=220,
        storey_index=storey,
        needs_external_wall=True,
        is_wet=False,
        packed=packed,
    )


def program(*rooms: ProgramRoom, storeys: int = 2) -> RoomProgram:
    return RoomProgram(rooms=tuple(rooms), adjacency=(), storeys=storeys, vastu_mode="advisory")


UPSTAIRS = (
    room("bedroom_master", "bedroom_master", 1),
    room("bedroom", "bedroom", 1),
    room("bedroom2", "bedroom", 1),
    room("bath_wc", "bath_wc", 1),
    room("bath_wc2", "bath_wc", 1),
)
DOWNSTAIRS = (
    room("living_dining", "living_dining", 0),
    room("kitchen", "kitchen", 0),
)


def storey_of(result: RoomProgram, key: str) -> int | None:
    return result.by_key(key).storey_index


# ===========================================================================
# Which room moves
# ===========================================================================
def test_a_bedroom_moves_down() -> None:
    result = rebalance_off_storey(program(*UPSTAIRS, *DOWNSTAIRS), 1)
    assert result is not None
    assert storey_of(result, "bedroom2") == 0


def test_the_master_never_moves() -> None:
    """Upstairs and private is the point of a master bedroom. Moving it to rescue a
    tiling failure would trade a plan that does not exist for one nobody wants."""
    result = rebalance_off_storey(program(*UPSTAIRS, *DOWNSTAIRS), 1)
    assert result is not None
    assert storey_of(result, "bedroom_master") == 1


def test_the_master_stays_even_when_it_is_the_only_bedroom_left() -> None:
    """Negative control on the rule above: with nothing else movable the answer is
    None, not "move the master"."""
    only_master = program(room("bedroom_master", "bedroom_master", 1), *DOWNSTAIRS)
    assert rebalance_off_storey(only_master, 1) is None


def test_a_guest_bedroom_goes_before_an_ordinary_one() -> None:
    """The order is the order an architect would pick: the guest room is the one that
    belongs downstairs anyway."""
    rooms = (*UPSTAIRS, room("guest_bedroom", "guest_bedroom", 1), *DOWNSTAIRS)
    result = rebalance_off_storey(program(*rooms), 1)
    assert result is not None
    assert storey_of(result, "guest_bedroom") == 0
    assert storey_of(result, "bedroom2") == 1


def test_the_last_of_a_type_moves_first() -> None:
    """`bedroom2` before `bedroom`, so the room the client named first keeps the
    better position."""
    result = rebalance_off_storey(program(*UPSTAIRS, *DOWNSTAIRS), 1)
    assert result is not None
    assert storey_of(result, "bedroom") == 1
    assert storey_of(result, "bedroom2") == 0


# ===========================================================================
# The bath follows
# ===========================================================================
def test_a_toilet_follows_the_bedroom_down() -> None:
    """A ground-floor guest room whose only toilet is upstairs is a worse plan than the
    one this is rescuing."""
    result = rebalance_off_storey(program(*UPSTAIRS, *DOWNSTAIRS), 1)
    assert result is not None
    assert storey_of(result, "bath_wc2") == 0


def test_the_floor_it_left_keeps_a_toilet() -> None:
    """The other half: a bedroom floor with no bath at all is not an improvement."""
    result = rebalance_off_storey(program(*UPSTAIRS, *DOWNSTAIRS), 1)
    assert result is not None
    assert storey_of(result, "bath_wc") == 1


def test_a_lone_toilet_does_not_follow() -> None:
    """With only one bath upstairs it stays: moving it would strip the floor."""
    rooms = (
        room("bedroom_master", "bedroom_master", 1),
        room("bedroom", "bedroom", 1),
        room("bath_wc", "bath_wc", 1),
        *DOWNSTAIRS,
    )
    result = rebalance_off_storey(program(*rooms), 1)
    assert result is not None
    assert storey_of(result, "bath_wc") == 1


# ===========================================================================
# Refusals and bookkeeping
# ===========================================================================
def test_the_ground_floor_has_nowhere_to_send_a_room() -> None:
    assert rebalance_off_storey(program(*UPSTAIRS, *DOWNSTAIRS), 0) is None


def test_nothing_movable_returns_none_rather_than_a_no_op_program() -> None:
    """A caller retries on a new program. Returning an unchanged one would spend the
    whole budget re-solving the identical problem."""
    stuck = program(room("kitchen", "kitchen", 1), *DOWNSTAIRS)
    assert rebalance_off_storey(stuck, 1) is None


def test_the_move_is_recorded_as_an_assumption_the_architect_can_see() -> None:
    """Golden rule 4. A floor plan that quietly rearranged the client's brief is the
    kind of surprise that costs trust in every later suggestion."""
    result = rebalance_off_storey(program(*UPSTAIRS, *DOWNSTAIRS), 1)
    assert result is not None
    assert len(result.assumptions) == 1
    reason = result.assumptions[0].reason
    assert "floor 1" in reason.lower() or "floor 0" in reason.lower()
    assert "Brief tab" in reason, "it must say how to override the choice"


def test_every_other_room_is_left_exactly_where_it_was() -> None:
    """Negative control on the whole function: a rescue that reshuffled the plan would
    be indistinguishable from a bug."""
    before = program(*UPSTAIRS, *DOWNSTAIRS)
    result = rebalance_off_storey(before, 1)
    assert result is not None
    moved = {"bedroom2", "bath_wc2"}
    for original in before.rooms:
        if original.key in moved:
            continue
        assert result.by_key(original.key).storey_index == original.storey_index
