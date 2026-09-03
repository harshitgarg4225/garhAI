"""The gate that folds an option and walks its doors — with a negative control."""

from __future__ import annotations

from garh_model.testing import opening_ops, ops_to_json, two_room_plan_ops

from services.solver import gates


def _door_between_rooms(ops: list[dict]) -> dict:
    wall = next(
        op for op in ops if op["type"] == "wall.add" and op["payload"]["kind"] == "internal"
    )
    a, b = wall["payload"]["a"], wall["payload"]["b"]
    length = abs(b["x"] - a["x"]) + abs(b["y"] - a["y"])
    return {
        "type": "opening.add",
        "payload": {
            "id": "opening_01J00000000000000000000GAT",
            "wallId": wall["payload"]["id"],
            "kind": "door",
            "widthMm": 900,
            "heightMm": 2100,
            "sillMm": 0,
            "offsetMm": length // 2,
            "swing": "in-left",
        },
    }


def test_an_option_with_a_walled_off_room_is_named() -> None:
    ops = ops_to_json([*two_room_plan_ops(), *opening_ops()])
    problems = gates.circulation_problems(ops)
    assert problems, "one room has no door and the gate said nothing"
    assert "no door path" in problems[0]


def test_one_door_makes_the_option_walkable() -> None:
    """NEGATIVE CONTROL for the control: the gate must go green when the door exists."""
    ops = ops_to_json([*two_room_plan_ops(), *opening_ops()])
    ops.append(_door_between_rooms(ops))
    assert gates.circulation_problems(ops) == []
