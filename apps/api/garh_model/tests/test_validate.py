"""Rejection cases — the codes are an API (the copilot self-corrects from them).

Every test here asserts a CODE, not a message: the message is copy and may be
rewritten, the code may not (``packages/model/schema/validation-issue.schema.json``
and the copilot fixtures key off it).
"""

from __future__ import annotations

from typing import Any

import pytest

from garh_model.fold import apply_group, fold, try_fold
from garh_model.model import empty_project_doc
from garh_model.ops import op
from garh_model.testing import (
    FIXTURE_IDS,
    fixed_id,
    make_two_room_plan,
    make_two_room_plan_with_openings,
)
from garh_model.validate import (
    MODEL_INVARIANT_CODES,
    VALIDATION_CODES,
    OpRejectedError,
    ValidationIssue,
    is_acceptable,
    issues_by_code,
    render_issues_for_llm,
    validate_model,
    validate_op_shape,
)

GF = FIXTURE_IDS["groundStorey"]
WALL_SOUTH = FIXTURE_IDS["wallSouth"]
WALL_SPINE = FIXTURE_IDS["wallSpine"]


def codes(issues: list[ValidationIssue]) -> list[str]:
    return [i.code for i in issues]


def reject_code(doc: Any, candidate: Any) -> list[str]:
    """Fold and return the rejection codes (asserting it WAS rejected)."""
    outcome = try_fold(doc, candidate)
    assert outcome.ok is False, "expected the op to be rejected"
    return [i.code for i in outcome.issues]


# ---------------------------------------------------------------------------
# Envelope / shape
# ---------------------------------------------------------------------------


def test_every_emitted_code_is_declared() -> None:
    assert len(set(VALIDATION_CODES)) == len(VALIDATION_CODES)
    for code in MODEL_INVARIANT_CODES:
        assert code in VALIDATION_CODES


@pytest.mark.parametrize("candidate", [None, 42, "wall.add", [], ({},)])
def test_non_object_is_not_an_op(candidate: Any) -> None:
    assert codes(validate_op_shape(candidate)) == ["OP_PAYLOAD_NOT_OBJECT"]


def test_unknown_type() -> None:
    issues = validate_op_shape({"type": "wall.explode", "payload": {}})
    assert codes(issues) == ["OP_UNKNOWN_TYPE"]
    assert issues[0].field == "type"


def test_missing_payload_object() -> None:
    assert codes(validate_op_shape({"type": "wall.delete"})) == ["OP_PAYLOAD_NOT_OBJECT"]
    assert codes(validate_op_shape({"type": "wall.delete", "payload": []})) == [
        "OP_PAYLOAD_NOT_OBJECT"
    ]


def test_missing_required_field() -> None:
    issues = validate_op_shape(op("wall.delete"))
    assert codes(issues) == ["OP_FIELD_MISSING"]
    assert issues[0].field == "payload.wallId"


def test_float_lengths_are_refused() -> None:
    """Geometry is integer millimetres — a float never reaches the document."""
    issues = validate_op_shape(
        op(
            "wall.add",
            id=fixed_id("wall", "W1"),
            storeyId=GF,
            a={"x": 0, "y": 0},
            b={"x": 4000, "y": 0},
            thicknessMm=229.5,
            kind="external",
        )
    )
    assert "OP_FIELD_NOT_INT_MM" in codes(issues)


def test_free_form_json_must_be_integer_only() -> None:
    issues = validate_op_shape(op("brief.update", patch={"budget": {"totalInr": 8_500_000.5}}))
    assert codes(issues) == ["OP_FIELD_NOT_INT"]
    assert issues[0].field == "payload.patch.budget.totalInr"


def test_bad_enum_and_bad_id() -> None:
    issues = validate_op_shape(
        op(
            "wall.add",
            id="not-an-id",
            storeyId=GF,
            a={"x": 0, "y": 0},
            b={"x": 4000, "y": 0},
            thicknessMm=230,
            kind="curtain",
        )
    )
    assert "OP_FIELD_BAD_ID" in codes(issues)
    assert "OP_FIELD_BAD_ENUM" in codes(issues)


def test_zero_length_wall_is_caught_before_the_document() -> None:
    issues = validate_op_shape(
        op(
            "wall.add",
            id=fixed_id("wall", "W1"),
            storeyId=GF,
            a={"x": 1000, "y": 1000},
            b={"x": 1000, "y": 1000},
            thicknessMm=230,
            kind="internal",
        )
    )
    assert "WALL_ZERO_LENGTH" in codes(issues)


def test_polygon_must_be_a_closed_simple_ring() -> None:
    bowtie = [
        {"x": 0, "y": 0},
        {"x": 1000, "y": 1000},
        {"x": 1000, "y": 0},
        {"x": 0, "y": 1000},
    ]
    issues = validate_op_shape(op("plot.set_boundary", polygon=bowtie))
    assert codes(issues) == ["OP_FIELD_BAD_POLYGON"]


def test_empty_polygon_is_the_legal_clear_form() -> None:
    """It has to be: it is the inverse of the first plot.set_boundary."""
    assert validate_op_shape(op("plot.set_boundary", polygon=[])) == []


def test_opening_resize_needs_at_least_one_dimension() -> None:
    issues = validate_op_shape(op("opening.resize", openingId=FIXTURE_IDS["doorMain"]))
    assert codes(issues) == ["OP_FIELD_MISSING"]


def test_levels_set_needs_at_least_one_field() -> None:
    assert codes(validate_op_shape(op("levels.set"))) == ["OP_FIELD_MISSING"]


def test_solver_expansion_is_validated_and_the_path_is_reported() -> None:
    issues = validate_op_shape(
        op(
            "solver.apply_option",
            solverJobId="job_x",
            optionIndex=0,
            ops=[{"type": "wall.delete", "payload": {}}],
        )
    )
    assert codes(issues) == ["OP_FIELD_MISSING"]
    assert issues[0].field == "payload.ops[0].payload.wallId"


# ---------------------------------------------------------------------------
# Document preconditions
# ---------------------------------------------------------------------------


def test_unknown_references() -> None:
    doc = make_two_room_plan()
    assert reject_code(doc, op("wall.delete", wallId=fixed_id("wall", "ZZ"))) == ["WALL_UNKNOWN"]
    assert reject_code(doc, op("stair.delete", stairId=fixed_id("stair", "ZZ"))) == [
        "STAIR_UNKNOWN"
    ]
    assert reject_code(doc, op("room.assign", roomId=fixed_id("room", "ZZ"), type="kitchen")) == [
        "ROOM_UNKNOWN"
    ]
    assert reject_code(
        doc,
        op(
            "wall.add",
            id=fixed_id("wall", "N1"),
            storeyId=fixed_id("storey", "ZZ"),
            a={"x": 0, "y": 8000},
            b={"x": 1000, "y": 8000},
            thicknessMm=115,
            kind="internal",
        ),
    ) == ["STOREY_UNKNOWN"]


def test_duplicate_id_is_rejected() -> None:
    doc = make_two_room_plan()
    assert reject_code(
        doc,
        op(
            "wall.add",
            id=WALL_SPINE,
            storeyId=GF,
            a={"x": 1500, "y": 0},
            b={"x": 1500, "y": 4000},
            thicknessMm=115,
            kind="internal",
        ),
    ) == ["OP_ID_ALREADY_EXISTS"]


def test_overlapping_wall_is_rejected() -> None:
    doc = make_two_room_plan()
    assert reject_code(
        doc,
        op(
            "wall.add",
            id=fixed_id("wall", "N2"),
            storeyId=GF,
            a={"x": 1000, "y": 0},
            b={"x": 5000, "y": 0},  # lies on top of the south wall
            thicknessMm=115,
            kind="internal",
        ),
    ) == ["WALL_DUPLICATE"]


def test_storey_index_out_of_range() -> None:
    doc = make_two_room_plan()
    assert reject_code(doc, op("storey.remove", index=4)) == ["STOREY_INDEX_OUT_OF_RANGE"]
    assert reject_code(
        doc, op("storey.add", id=FIXTURE_IDS["firstStorey"], index=5, heightMm=3000)
    ) == ["STOREY_INDEX_OUT_OF_RANGE"]


def test_road_needs_a_boundary_edge() -> None:
    doc = empty_project_doc()
    assert reject_code(doc, op("plot.set_road", edgeIndex=0, widthMm=9000)) == [
        "PLOT_BOUNDARY_NOT_CLOSED"
    ]
    doc = make_two_room_plan()
    assert reject_code(doc, op("plot.set_road", edgeIndex=9, widthMm=9000)) == ["PLOT_EDGE_UNKNOWN"]


def test_opening_wider_than_its_wall_is_rejected() -> None:
    """The playbook's own example of a clean rejection (Phase 1 DoD)."""
    doc = make_two_room_plan()
    issues = reject_code(
        doc,
        op(
            "opening.add",
            id=fixed_id("opening", "X1"),
            wallId=WALL_SPINE,  # 4000mm long: usable = 4000 - 230 = 3770
            kind="window",
            widthMm=3900,
            heightMm=1200,
            sillMm=900,
            offsetMm=2000,
            swing="in-left",
        ),
    )
    assert issues == ["OPENING_OUT_OF_WALL"]


def test_opening_too_close_to_the_wall_end_is_rejected() -> None:
    doc = make_two_room_plan()
    issues = reject_code(
        doc,
        op(
            "opening.add",
            id=fixed_id("opening", "X2"),
            wallId=WALL_SPINE,
            kind="door",
            widthMm=900,
            heightMm=2100,
            sillMm=0,
            offsetMm=200,  # needs >= 115 + 450 = 565
            swing="in-left",
        ),
    )
    assert issues == ["OPENING_OUT_OF_WALL"]


def test_opening_taller_than_the_storey_is_rejected() -> None:
    doc = make_two_room_plan()
    issues = reject_code(
        doc,
        op(
            "opening.add",
            id=fixed_id("opening", "X3"),
            wallId=WALL_SOUTH,
            kind="window",
            widthMm=1200,
            heightMm=2400,
            sillMm=900,  # 3300 > 3000mm storey
            offsetMm=3000,
            swing="in-left",
        ),
    )
    assert issues == ["OPENING_EXCEEDS_STOREY_HEIGHT"]


def test_moving_a_wall_that_would_orphan_an_opening_is_rejected() -> None:
    doc = make_two_room_plan_with_openings()
    issues = reject_code(
        doc,
        op("wall.move", wallId=WALL_SOUTH, a={"x": 0, "y": 0}, b={"x": 1200, "y": 0}),
    )
    assert issues == ["OPENING_OUT_OF_WALL"]


def test_stair_rise_must_match_the_storey_height() -> None:
    doc = make_two_room_plan()
    issues = reject_code(
        doc,
        op(
            "stair.add",
            id=FIXTURE_IDS["stair"],
            storeyId=GF,
            kind="straight",
            origin={"x": 1000, "y": 1000},
            direction="N",
            riserMm=150,
            treadMm=275,
            widthMm=1000,
            risersCount=18,  # 2700mm against a 3000mm storey
        ),
    )
    assert issues == ["STAIR_RISE_MISMATCH"]


def test_stair_rise_within_tolerance_is_accepted() -> None:
    doc = make_two_room_plan()
    outcome = try_fold(
        doc,
        op(
            "stair.add",
            id=FIXTURE_IDS["stair"],
            storeyId=GF,
            kind="straight",
            origin={"x": 1000, "y": 1000},
            direction="N",
            riserMm=167,
            treadMm=275,
            widthMm=1000,
            risersCount=18,  # 3006mm, within +/-10mm of 3000
        ),
    )
    assert outcome.ok is True


def test_wall_split_out_of_range() -> None:
    doc = make_two_room_plan()
    assert reject_code(
        doc,
        op("wall.split", wallId=WALL_SPINE, atMm=9000, newWallId=fixed_id("wall", "N3")),
    ) == ["WALL_SPLIT_OUT_OF_RANGE"]


def test_ffl_array_length_must_match_the_storeys() -> None:
    doc = make_two_room_plan()
    assert reject_code(doc, op("levels.set", fflPerStoreyMm=[600, 3600])) == ["LEVELS_INVALID"]


def test_rejected_op_leaves_the_document_untouched() -> None:
    doc = make_two_room_plan()
    before = doc
    with pytest.raises(OpRejectedError) as excinfo:
        fold(doc, op("wall.delete", wallId=fixed_id("wall", "ZZ")))
    assert doc is before
    assert excinfo.value.issues[0].code == "WALL_UNKNOWN"
    problem = excinfo.value.as_problem()
    assert problem["code"] == "OP_REJECTED"
    assert problem["issues"][0]["code"] == "WALL_UNKNOWN"


def test_group_is_atomic() -> None:
    """If any op in a group is rejected, nothing is applied."""
    doc = make_two_room_plan()
    with pytest.raises(OpRejectedError):
        apply_group(
            doc,
            [
                op("plot.set_north", deg=42),
                op("wall.delete", wallId=fixed_id("wall", "ZZ")),
            ],
            fixed_id("group", "G1"),
        )
    assert doc.plot.north_deg == 0, "the first op must not have survived"


# ---------------------------------------------------------------------------
# Whole-document invariants and reporting helpers
# ---------------------------------------------------------------------------


def test_a_folded_document_satisfies_the_invariants() -> None:
    doc = make_two_room_plan_with_openings()
    assert validate_model(doc, include_warnings=False) == []
    assert is_acceptable(validate_model(doc))


def test_validate_model_flags_a_hand_corrupted_document() -> None:
    from dataclasses import replace

    doc = make_two_room_plan()
    broken_wall = replace(doc.house.walls[0], thickness_mm=5000)
    house = replace(doc.house, walls=(broken_wall,) + doc.house.walls[1:])
    broken = replace(doc, house=house)
    assert "WALL_THICKNESS_INVALID" in codes(validate_model(broken))


def test_stale_room_area_is_a_warning_not_an_error() -> None:
    from dataclasses import replace

    doc = make_two_room_plan()
    stale = replace(doc.house.rooms[0], area_mm2=1)
    house = replace(doc.house, rooms=(stale,) + doc.house.rooms[1:])
    broken = replace(doc, house=house)
    issues = validate_model(broken)
    assert "ROOM_NOT_CLOSED" in codes(issues)
    assert codes(validate_model(broken, include_warnings=False)) == []


def test_issue_helpers_for_the_copilot_loop() -> None:
    doc = make_two_room_plan()
    outcome = try_fold(doc, op("wall.delete", wallId=fixed_id("wall", "ZZ")))
    assert outcome.ok is False
    grouped = issues_by_code(outcome.issues)
    assert "WALL_UNKNOWN" in grouped
    rendered = render_issues_for_llm(outcome.issues)
    assert rendered.startswith("WALL_UNKNOWN")
    assert "FIX:" in rendered
    assert "\n" not in rendered.rstrip("\n") or len(outcome.issues) > 1


def test_issue_serialisation_matches_the_schema_shape() -> None:
    doc = make_two_room_plan()
    outcome = try_fold(
        doc,
        op(
            "opening.add",
            id=fixed_id("opening", "X4"),
            wallId=WALL_SPINE,
            kind="window",
            widthMm=3900,
            heightMm=1200,
            sillMm=900,
            offsetMm=2000,
            swing="in-left",
        ),
    )
    body = outcome.issues[0].to_json()
    assert set(body) >= {"code", "message", "severity", "elementIds"}
    assert body["severity"] == "error"
    assert body["field"] == "payload.widthMm"
    assert body["limit"] == 3770
