"""A room you cannot walk into must fail on the COMPLIANCE TAB, not only in the solver.

``garh_model.circulation`` has walked the door graph since CLAUDE.md bug 7 — the
library plan whose front door opened into a dead-end vestibule and whose kitchen was
entered through the bath, over a report that read 0 fail because no loaded rule looks
at doors. The gate was wired into the solver's own gates and stopped there. So the
fix covered exactly one producer of plans: a plan the architect DREW, imported from
DXF, or edited after generating could still hide an unreachable room, and the tab
they trust would still say nothing.

This file walks the other path. Ops are folded for real (``garh_model.replay``),
projected by the real ``build_evaluation_context``, and evaluated by the real engine
through ``evaluate_document`` — no hand-written context, so a projection that forgot
to carry ``access`` fails here rather than passing on a fixture that spells it out.

Three things have to be true, and each has a test that can go red on its own:

1. a walled-off room FAILS ``nbc.circulation.room.reachable``;
2. adding the one missing door turns the same plan green — the positive control,
   without which a rule hard-coded to fail would pass test 1;
3. a document whose door connectivity was never derived is ``not_applicable``, with
   a reason, and NOT a pass. That is the whole reason ``RoomSummary.access`` is
   nullable and the packs gate on ``roomAccessKnown``.
"""

from __future__ import annotations

from typing import Any

import pytest
from garh_api.compliance import build_evaluation_context, evaluate_document
from garh_model import replay
from garh_model.geometry import Seg, segment_length_mm
from garh_model.model import HouseModel
from garh_model.ops import Op
from garh_model.testing import DEMO_PLOT_POLYGON, opening_ops, two_room_plan_ops

REACHABLE_RULE = "nbc.circulation.room.reachable"
BATH_RULE = "nbc.circulation.room.not_through_bath"
PACKS = ("nbc-core", "blr")


def _plot_ops() -> list[Op]:
    """The plot the rest of the API tests use: a 30 x 40 on a 9 m road, BBMP pack."""
    return [
        Op(
            type="plot.set_boundary",
            payload={"polygon": list(DEMO_PLOT_POLYGON), "source": "manual"},
        ),
        Op(type="plot.set_road", payload={"edgeIndex": 0, "widthMm": 9000, "name": "9m Road"}),
        Op(type="plot.set_reg_profile", payload={"cityPack": "blr", "overrides": {}}),
    ]


def _internal_door(house: HouseModel, storey_id: str) -> Op:
    """A 900 door in the middle of the storey's first internal wall."""
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


def _document(ops: list[Op]) -> dict[str, Any]:
    return replay([*_plot_ops(), *ops]).to_json()


def _row(document: dict[str, Any], rule_id: str) -> dict[str, Any]:
    # `evaluate_document` reads the pack set off the document's own reg profile,
    # which is what the route does — so this exercises the production path rather
    # than a pack list the test chose.
    report, _versions = evaluate_document(document)
    row = next((r for r in report["results"] if r["ruleId"] == rule_id), None)
    assert row is not None, "%s did not appear in the report at all — is it loaded?" % rule_id
    return row


@pytest.fixture()
def walled_off() -> list[Op]:
    """Two rooms, a front door into one of them, and no door in the wall between."""
    return [*two_room_plan_ops(), *opening_ops()]


def test_a_walled_off_room_fails_on_the_compliance_tab(walled_off: list[Op]) -> None:
    document = _document(walled_off)
    row = _row(document, REACHABLE_RULE)

    assert row["status"] == "fail", (
        "a room with no door into it passed the compliance tab. This is CLAUDE.md "
        "bug 7 again, for every plan that did not come out of the solver."
    )
    assert row["actual"] == "unreachable"
    assert row["elements"], "the row must name the room an architect has to go and fix"
    # The message is what the architect reads on the chip; it has to say what is wrong.
    assert "reach" in row["message"].lower()


def test_the_one_missing_door_turns_it_green(walled_off: list[Op]) -> None:
    """POSITIVE CONTROL. Without this, a rule hard-coded to fail passes the test above."""
    house = replay([*_plot_ops(), *walled_off]).house
    storey = house.storeys[0].id
    fixed = _document([*walled_off, _internal_door(house, storey)])

    row = _row(fixed, REACHABLE_RULE)
    assert row["status"] == "pass", row
    assert row["actual"] == "reachable"


def test_a_document_with_no_doors_derived_is_not_applicable_not_a_pass() -> None:
    """THE NULL CASE, which is where a reachability check would normally go wrong.

    The engine does no pathfinding: it reads the verdict the model layer hands it. So
    a context whose rooms carry no ``access`` must report "not checked", with a reason
    naming the gate — never "pass". This builds that context the only honest way, by
    taking the real projection and removing the field, which is exactly the shape a
    caller written before the field existed produces.
    """
    document = _document([*two_room_plan_ops(), *opening_ops()])
    context = build_evaluation_context(document, packs=PACKS)
    assert [r.get("access") for r in context["model"]["rooms"]] != [None, None], (
        "the projection is not carrying room access at all, so the case below proves "
        "nothing about the gate"
    )
    for room in context["model"]["rooms"]:
        room.pop("access", None)

    from garh_rules import evaluate

    report = evaluate(context, packs=list(PACKS)).to_json()
    row = next(r for r in report["results"] if r["ruleId"] == REACHABLE_RULE)
    assert row["status"] == "not_applicable", (
        "a model that says nothing about doors was read as a pass — the reachability "
        "rule is no longer gated on roomAccessKnown"
    )
    assert row.get("notApplicableField") == "roomAccessKnown"


def test_a_room_reached_only_through_a_bath_is_warned_about_but_still_reachable() -> None:
    """The second rule, and the reason there are two.

    "You cannot get in" and "you can only get in through the bathroom" are different
    verdicts an architect acts on differently, so they are different rows: the first
    fails, the second warns, and a plan with an en-suite-only bedroom must not be
    reported as unreachable.
    """
    document = _document([*two_room_plan_ops(), *opening_ops()])
    context = build_evaluation_context(document, packs=PACKS)
    rooms = context["model"]["rooms"]
    assert rooms, "fixture"
    # Say it explicitly rather than building a bath-locked plan: the WALK is proven in
    # garh_model/tests/test_circulation.py; what is unproven here is that the two rules
    # read the same label differently, which is a property of the packs.
    for room in rooms:
        room["access"] = "only-via-bath"
        room["type"] = "bedroom"

    from garh_rules import evaluate

    report = evaluate(context, packs=list(PACKS)).to_json()
    rows = {r["ruleId"]: r for r in report["results"]}
    assert rows[REACHABLE_RULE]["status"] == "pass", "only-via-bath is reachable"
    assert rows[BATH_RULE]["status"] == "warn", rows[BATH_RULE]
