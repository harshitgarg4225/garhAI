"""Reachability over doors — the gate the first library plan proved was missing."""

from __future__ import annotations

from garh_model import replay
from garh_model.circulation import (
    OUTSIDE,
    door_edges,
    reachability_problems,
    storey_reachability,
)
from garh_model.model import HouseModel
from garh_model.ops import Op
from garh_model.testing import opening_ops, two_room_plan_ops


def _house(drop_types: frozenset[str] = frozenset()) -> HouseModel:
    ops = [op for op in [*two_room_plan_ops(), *opening_ops()] if op.type not in drop_types]
    return replay(ops).house


def _internal_door(house: HouseModel, storey_id: str) -> Op:
    """A 900 door in the middle of the storey's first internal wall."""
    from garh_model.geometry import Seg, segment_length_mm

    wall = next(w for w in house.walls if w.storey_id == storey_id and w.kind != "external")
    return Op(
        type="opening.add",
        payload={
            "id": "opening_01J00000000000000000000TST",
            "wallId": wall.id,
            "kind": "door",
            "widthMm": 900,
            "heightMm": 2100,
            "sillMm": 0,
            "offsetMm": segment_length_mm(Seg(wall.a, wall.b)) // 2,
            "swing": "in-left",
        },
    )


def test_a_room_behind_a_solid_wall_is_unreachable_until_it_gets_a_door() -> None:
    """The fixture has a front door into room A and no door to room B.

    Room B is exactly the library defect in miniature: a wall between two rooms and no
    opening in it. Adding one door on that wall makes the storey whole.
    """
    house = _house()
    storey = house.storeys[0].id
    before = storey_reachability(house, storey)
    assert before.root == OUTSIDE
    assert len(before.reachable) == 1, door_edges(house, storey)
    assert len(before.unreachable) == 1
    assert reachability_problems(house), "one room is walled off and nothing said so"

    ops = [*two_room_plan_ops(), *opening_ops(), _internal_door(house, storey)]
    after = storey_reachability(replay(ops).house, storey)
    assert after.unreachable == []
    assert len(after.reachable) == 2
    assert reachability_problems(replay(ops).house) == []


def test_without_doors_every_room_is_unreachable() -> None:
    """NEGATIVE CONTROL: strip the openings and the gate must go red."""
    house = _house(drop_types=frozenset({"opening.add"}))
    storey = house.storeys[0].id
    res = storey_reachability(house, storey)
    assert res.unreachable, "no doors, yet everything counts as reachable"
    assert set(res.unreachable) == {r.id for r in house.rooms if r.storey_id == storey}
    assert reachability_problems(house)


def test_windows_are_not_passable() -> None:
    house = _house()
    storey = house.storeys[0].id
    kinds = {
        e.opening_id: next(o.kind for o in house.openings if o.id == e.opening_id)
        for e in door_edges(house, storey)
    }
    assert all(k in ("door", "opening", "arch", "archway", "sliding") for k in kinds.values())
    assert any(o.kind == "window" for o in house.openings), "the fixture should carry a window"
