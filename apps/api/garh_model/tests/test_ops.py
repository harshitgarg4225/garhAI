"""The op taxonomy: exactly 32 ops, each catalogued, each example valid.

``OP_CATALOG`` generates the copilot system prompt (section 10), so gaps here
become gaps in what the copilot can do — the coverage assertions are the point.
"""

from __future__ import annotations

import json

import pytest

from garh_model.ops import (
    ANNOTATION_ACTIONS,
    BALCONY_ACTIONS,
    COLUMN_ACTIONS,
    FURNITURE_ACTIONS,
    OP_CATALOG,
    OP_TYPES,
    Op,
    copilot_op_specs,
    get_op_spec,
    is_op,
    is_op_type,
    op,
    render_op_catalog_for_prompt,
)
from garh_model.testing import fixed_id
from garh_model.validate import validate_op_shape

#: Playbook section 4, verbatim and in order. Hard-coded on purpose: this list is
#: the specification, OP_CATALOG is the implementation.
EXPECTED_OP_TYPES = [
    "plot.set_boundary",
    "plot.set_north",
    "plot.set_road",
    "plot.set_reg_profile",
    "brief.update",
    "storey.add",
    "storey.remove",
    "storey.set_height",
    "wall.add",
    "wall.move",
    "wall.split",
    "wall.delete",
    "wall.set_thickness",
    "opening.add",
    "opening.move",
    "opening.resize",
    "opening.flip",
    "opening.delete",
    "room.assign",
    "room.set_target",
    "stair.add",
    "stair.edit",
    "stair.delete",
    "column.set",
    "furniture.set",
    "balcony.set",
    "facade.apply_kit",
    "facade.edit_component",
    "material.assign",
    "levels.set",
    "solver.apply_option",
    "annotation.set",
]


def test_there_are_exactly_32_ops_in_playbook_order() -> None:
    assert list(OP_TYPES) == EXPECTED_OP_TYPES
    assert len(OP_CATALOG) == 32
    assert [spec.number for spec in OP_CATALOG] == list(range(1, 33))


def test_every_op_type_appears_exactly_once() -> None:
    assert len(set(OP_TYPES)) == len(OP_TYPES)


def test_catalogue_lookup() -> None:
    for op_type in OP_TYPES:
        spec = get_op_spec(op_type)
        assert spec is not None and spec.type == op_type
        assert is_op_type(op_type)
    assert get_op_spec("wall.explode") is None
    assert not is_op_type("wall.explode")
    assert not is_op_type(None)


def test_specs_are_self_consistent() -> None:
    for spec in OP_CATALOG:
        assert spec.title and spec.summary
        assert spec.example.type == spec.type
        names = [f.name for f in spec.payload]
        assert len(names) == len(set(names)), f"{spec.type} has duplicate field names"
        for f in spec.payload:
            assert f.description, f"{spec.type}.{f.name} has no description"
            if f.type == "enum":
                assert f.enum_values, f"{spec.type}.{f.name} is an enum with no values"
            if f.type == "id":
                assert f.id_type, f"{spec.type}.{f.name} is an id with no namespace"
        if spec.actions is not None:
            assert "action" in names


def test_combined_ops_declare_their_actions() -> None:
    assert get_op_spec("column.set").actions == COLUMN_ACTIONS  # type: ignore[union-attr]
    assert get_op_spec("furniture.set").actions == FURNITURE_ACTIONS  # type: ignore[union-attr]
    assert get_op_spec("balcony.set").actions == BALCONY_ACTIONS  # type: ignore[union-attr]
    assert get_op_spec("annotation.set").actions == ANNOTATION_ACTIONS  # type: ignore[union-attr]


@pytest.mark.parametrize("spec", list(OP_CATALOG), ids=[s.type for s in OP_CATALOG])
def test_every_catalogue_example_passes_shape_validation(spec: object) -> None:
    issues = validate_op_shape(spec.example)  # type: ignore[attr-defined]
    assert issues == [], [i.code for i in issues]


def test_examples_survive_a_json_round_trip() -> None:
    """The catalogue examples are what fixtures and docs paste — they must be wire-clean."""
    for spec in OP_CATALOG:
        wire = json.loads(json.dumps(spec.example.to_json()))
        parsed = Op.from_json(wire)
        assert parsed.type == spec.type
        assert validate_op_shape(parsed) == []


def test_copilot_filter_excludes_plot_and_solver_ops() -> None:
    copilot_types = {s.type for s in copilot_op_specs()}
    assert "solver.apply_option" not in copilot_types
    assert "plot.set_boundary" not in copilot_types
    assert "plot.set_reg_profile" not in copilot_types
    assert "annotation.set" not in copilot_types
    assert "wall.add" in copilot_types
    # 32 ops minus the four the copilot may not emit
    assert len(copilot_types) == 28


def test_prompt_rendering_is_generated_from_the_catalogue() -> None:
    prompt = render_op_catalog_for_prompt()
    assert "# Op catalogue" in prompt
    assert "INTEGER MILLIMETRES" in prompt
    assert "## wall.add — Add wall" in prompt
    assert "solver.apply_option" not in prompt  # copilot-only by default
    full = render_op_catalog_for_prompt(copilot_only=False)
    assert "solver.apply_option" in full
    for spec in OP_CATALOG:
        assert spec.type in full


def test_is_op_guard() -> None:
    assert is_op(op("plot.set_north", deg=0))
    assert is_op({"type": "plot.set_north", "payload": {"deg": 0}})
    assert not is_op({"type": "plot.set_north"})
    assert not is_op({"type": "nope", "payload": {}})
    assert not is_op({"type": "plot.set_north", "payload": []})
    assert not is_op(None)
    assert not is_op("wall.add")


def test_op_envelope_helpers() -> None:
    base = op("wall.delete", wallId=fixed_id("wall", "W1"))
    assert base.group_id is None
    stamped = base.with_group(fixed_id("group", "G1"))
    assert stamped.group_id == fixed_id("group", "G1")
    assert base.group_id is None, "with_group must not mutate"
    assert stamped.to_json()["groupId"] == fixed_id("group", "G1")
    assert "groupId" not in base.to_json()


def test_absent_versus_null_payload_keys() -> None:
    """The convention the whole engine rests on (see ops.py's docstring)."""
    absent = op("room.set_target", roomId=fixed_id("room", "R1"))
    explicit = op("room.set_target", roomId=fixed_id("room", "R1"), targetAreaMm2=None)
    assert not absent.has("targetAreaMm2")
    assert explicit.has("targetAreaMm2")
    assert explicit.get("targetAreaMm2") is None
    assert absent.get("targetAreaMm2", "fallback") == "fallback"
    assert explicit.get("targetAreaMm2", "fallback") is None
