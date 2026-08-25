"""The op engine: canonical JSON, state hash, determinism, and undo/redo.

The Phase 1 Definition of Done, tested here:

* any generated op sequence folds deterministically and replays to an identical
  state hash;
* undo/redo round-trips a long random history;
* an invalid op is rejected cleanly (that one lives in ``test_validate.py``).

Plus the thing neither language can check alone:
:func:`test_golden_states_match_the_cross_language_fixture` asserts the hashes in
``fixtures/model/golden-states.json``, which ``packages/model`` asserts too.
"""

from __future__ import annotations

import json
import random
from typing import Any, Dict, List

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from garh_model.fold import (
    CANONICAL_JSON_SPEC,
    STATE_HASH_ALGORITHM,
    CanonicalJsonError,
    UndoEntry,
    UndoStack,
    apply_group,
    apply_merge_patch,
    canonical_json,
    doc_hash,
    fold,
    invert_merge_patch,
    locked_room_ids,
    replay,
    stair_footprint_polygon,
    state_hash,
    storey_built_up_area_mm2,
    storey_carpet_area_mm2,
    try_fold,
    wall_length_mm,
)
from garh_model.model import empty_project_doc, to_jsonable
from garh_model.validate import OpRejectedError
from garh_model.ops import Op, op
from garh_model.testing import (
    FIXTURE_IDS,
    fixed_id,
    make_empty_doc,
    make_two_room_plan,
    make_two_room_plan_with_openings,
    opening_ops,
    two_room_plan_ops,
)

GF = FIXTURE_IDS["groundStorey"]
FF = FIXTURE_IDS["firstStorey"]

# ---------------------------------------------------------------------------
# canonical JSON — rule by rule
# ---------------------------------------------------------------------------


def test_spec_tags_are_pinned() -> None:
    assert CANONICAL_JSON_SPEC == "garh-canonical-json/v1"
    assert STATE_HASH_ALGORITHM == "sha256(garh-canonical-json/v1)"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "null"),
        (True, "true"),
        (False, "false"),
        (0, "0"),
        (-0, "0"),
        (-0.0, "0"),
        (42, "42"),
        (-3810, "-3810"),
        (9007199254740991, "9007199254740991"),
        (3.0, "3"),  # JavaScript cannot tell 3.0 from 3; neither may the hash
        ("", '""'),
        ("plain", '"plain"'),
        ([], "[]"),
        ({}, "{}"),
        ([1, 2, 3], "[1,2,3]"),
    ],
)
def test_canonical_scalars(value: Any, expected: str) -> None:
    assert canonical_json(value) == expected


def test_canonical_object_keys_are_sorted_by_code_point() -> None:
    assert canonical_json({"b": 1, "a": 2, "A": 3, "_": 4}) == '{"A":3,"_":4,"a":2,"b":1}'
    # nested, and no whitespace anywhere
    assert canonical_json({"z": {"y": [1, {"x": 0}]}}) == '{"z":{"y":[1,{"x":0}]}}'


def test_canonical_string_escaping() -> None:
    assert canonical_json('a"b') == '"a\\"b"'
    assert canonical_json("a\\b") == '"a\\\\b"'
    assert canonical_json("\b\t\n\f\r") == '"\\b\\t\\n\\f\\r"'
    assert canonical_json("\x00\x07\x1f") == '"\\u0000\\u0007\\u001f"'
    # non-ASCII is emitted literally as UTF-8, never \uXXXX
    assert canonical_json("श्री") == '"श्री"'
    assert canonical_json("₹95") == '"₹95"'
    assert canonical_json("🏠") == '"🏠"'
    # and neither / nor U+2028 is escaped
    assert canonical_json("a/b c") == '"a/b c"'


@pytest.mark.parametrize("value", [1.5, float("nan"), float("inf"), 2**53, -(2**53)])
def test_canonical_json_refuses_unrepresentable_numbers(value: Any) -> None:
    with pytest.raises(CanonicalJsonError):
        canonical_json(value)


def test_canonical_json_refuses_exotic_types() -> None:
    with pytest.raises(CanonicalJsonError):
        canonical_json({1, 2, 3})
    with pytest.raises(CanonicalJsonError):
        canonical_json(object())


def test_state_hash_is_64_lowercase_hex() -> None:
    h = state_hash({"a": 1})
    assert len(h) == 64
    assert h == h.lower()
    int(h, 16)  # parses as hex


def test_state_hash_ignores_key_insertion_order() -> None:
    assert state_hash({"a": 1, "b": 2}) == state_hash({"b": 2, "a": 1})


def test_state_hash_accepts_dataclasses_through_to_jsonable() -> None:
    doc = make_two_room_plan()
    assert doc_hash(doc) == state_hash(to_jsonable(doc))


# ---------------------------------------------------------------------------
# RFC 7386 merge patch
# ---------------------------------------------------------------------------


def test_merge_patch_semantics() -> None:
    target = {"a": 1, "b": {"c": 2, "d": 3}, "e": 4}
    patch = {"b": {"c": 20, "d": None}, "e": None, "f": 5}
    assert apply_merge_patch(target, patch) == {"a": 1, "b": {"c": 20}, "f": 5}


def test_merge_patch_inverse_round_trips() -> None:
    target = {"a": 1, "b": {"c": 2, "d": 3}, "e": 4}
    patch = {"b": {"c": 20, "d": None}, "e": None, "f": 5}
    patched = apply_merge_patch(target, patch)
    undone = apply_merge_patch(patched, invert_merge_patch(target, patch))
    assert undone == target


@given(
    keys=st.lists(st.sampled_from(["a", "b", "c", "d"]), min_size=1, max_size=4, unique=True),
    values=st.lists(st.integers(min_value=-5, max_value=5), min_size=1, max_size=4),
)
@settings(max_examples=60, deadline=None)
def test_merge_patch_inverse_property(keys: List[str], values: List[int]) -> None:
    target: Dict[str, Any] = {"a": 1, "b": {"n": 2}, "c": "keep"}
    patch: Dict[str, Any] = {}
    for i, k in enumerate(keys):
        v = values[i % len(values)]
        patch[k] = None if v == 0 else ({"n": v} if k == "b" else v)
    patched = apply_merge_patch(target, patch)
    assert apply_merge_patch(patched, invert_merge_patch(target, patch)) == target


# ---------------------------------------------------------------------------
# determinism and replay
# ---------------------------------------------------------------------------


def test_fold_does_not_mutate_its_input() -> None:
    doc = make_two_room_plan()
    before = doc_hash(doc)
    fold(doc, op("plot.set_north", deg=90))
    assert doc_hash(doc) == before


def test_replay_is_deterministic() -> None:
    ops = two_room_plan_ops() + opening_ops()
    first = replay(ops, make_empty_doc())
    second = replay(ops, make_empty_doc())
    assert doc_hash(first) == doc_hash(second)


def test_replay_from_wire_json_matches_replay_from_objects() -> None:
    ops = two_room_plan_ops() + opening_ops()
    wire = json.loads(json.dumps([o.to_json() for o in ops]))
    assert doc_hash(replay(wire, make_empty_doc())) == doc_hash(replay(ops, make_empty_doc()))


def test_insertion_order_of_explicit_ids_does_not_change_the_hash() -> None:
    """finalize() sorts every element array by id, so insertion order cannot leak.

    Tested on elements whose ids come from the op (columns, furniture): for
    DERIVED ids see :func:`test_room_ids_depend_on_history_not_only_on_geometry`.
    """
    base = make_two_room_plan()
    placements = [
        op(
            "column.set",
            action="add",
            id=fixed_id("column", f"C{i}"),
            storeyId=GF,
            pt={"x": 1000 * (i + 1), "y": 2000},
        )
        for i in range(3)
    ] + [
        op(
            "furniture.set",
            action="place",
            id=fixed_id("furniture", f"F{i}"),
            storeyId=GF,
            catalogId="chair-dining",
            pt={"x": 1000 * (i + 1), "y": 3000},
        )
        for i in range(3)
    ]
    forward = apply_group(base, placements).model
    backward = apply_group(base, list(reversed(placements))).model
    assert doc_hash(forward) == doc_hash(backward)


def test_room_ids_depend_on_history_not_only_on_geometry() -> None:
    """A KNOWN, DELIBERATE property of id preservation — pinned so it is not a surprise.

    A room keeps its id through edits (max-Jaccard match), which means the id a
    room ends up with depends on the order the walls arrived in: build the outer
    box first and the whole floor becomes one room whose id one half then
    INHERITS when the spine splits it; build the spine first and both halves are
    minted from their own polygons. Same drawing, same areas, different room ids
    — and therefore a different document hash.

    The TypeScript mirror does exactly the same thing (identical algorithm), so
    this is a property of the model, not a divergence. It is why golden states
    pin an op LOG rather than a picture.
    """
    ops = two_room_plan_ops()
    head, walls = ops[:4], list(ops[4:])
    box_first = replay(head + walls, make_empty_doc())
    spine_first = replay(head + [walls[-1]] + walls[:-1], make_empty_doc())

    assert [w.id for w in box_first.house.walls] == [w.id for w in spine_first.house.walls]
    assert sorted(r.area_mm2 for r in box_first.house.rooms) == sorted(
        r.area_mm2 for r in spine_first.house.rooms
    )
    assert {r.id for r in box_first.house.rooms} != {r.id for r in spine_first.house.rooms}
    assert doc_hash(box_first) != doc_hash(spine_first)
    # the same LOG always folds to the same state — that is the guarantee
    assert doc_hash(replay(head + walls, make_empty_doc())) == doc_hash(box_first)


def test_derived_rooms_and_slabs_are_recomputed_not_carried() -> None:
    doc = make_two_room_plan()
    assert len(doc.house.rooms) == 2
    assert len(doc.house.slabs) == 1
    assert doc.house.slabs[0].kind == "floor"
    assert storey_carpet_area_mm2(doc, GF) == sum(r.area_mm2 for r in doc.house.rooms)
    assert storey_built_up_area_mm2(doc, GF) == (6000 + 230) * (4000 + 230)


def test_levels_are_derived_from_plinth_and_storey_heights() -> None:
    doc = apply_group(
        make_two_room_plan(),
        [op("storey.add", id=FF, index=1, name="First Floor", heightMm=3200)],
    ).model
    assert list(doc.house.levels.ffl_per_storey_mm) == [600, 3600]
    assert [s.level.ffl_mm for s in doc.house.storeys] == [600, 3600]
    doc = fold(doc, op("levels.set", plinthMm=750)).model
    assert list(doc.house.levels.ffl_per_storey_mm) == [750, 3750]
    # an EXPLICIT array wins over the derivation
    doc = fold(doc, op("levels.set", fflPerStoreyMm=[0, 3000])).model
    assert list(doc.house.levels.ffl_per_storey_mm) == [0, 3000]


def test_wall_length_and_locked_rooms_helpers() -> None:
    doc = make_two_room_plan()
    south = next(w for w in doc.house.walls if w.id == FIXTURE_IDS["wallSouth"])
    assert wall_length_mm(south) == 6000
    assert locked_room_ids(doc) == []
    room_id = doc.house.rooms[0].id
    doc = fold(doc, op("room.assign", roomId=room_id, type="pooja", locked=True)).model
    assert locked_room_ids(doc) == [room_id]


def test_stair_footprint_is_exact_for_a_straight_flight() -> None:
    doc = fold(
        make_two_room_plan(),
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
            risersCount=18,
        ),
    ).model
    stair = doc.house.stairs[0]
    poly = stair_footprint_polygon(stair)
    xs = sorted({p.x for p in poly})
    ys = sorted({p.y for p in poly})
    assert xs == [1000, 2000]  # width
    assert ys == [1000, 1000 + 17 * 275]  # going = (risers - 1) * tread


def test_a_stair_cuts_the_slab_above_it() -> None:
    ops = (
        two_room_plan_ops()
        + [op("storey.add", id=FF, index=1, name="First Floor", heightMm=3000)]
        + [
            op(
                "wall.add",
                id=fixed_id("wall", f"FF{tag}"),
                storeyId=FF,
                a={"x": ax, "y": ay},
                b={"x": bx, "y": by},
                thicknessMm=230,
                kind="external",
            )
            for tag, ax, ay, bx, by in [
                ("S", 0, 0, 6000, 0),
                ("E", 6000, 0, 6000, 4000),
                ("N", 6000, 4000, 0, 4000),
                ("W", 0, 4000, 0, 0),
            ]
        ]
        + [
            op(
                "stair.add",
                id=FIXTURE_IDS["stair"],
                storeyId=GF,
                kind="straight",
                origin={"x": 1000, "y": 500},
                direction="N",
                riserMm=167,
                treadMm=275,
                widthMm=1000,
                risersCount=18,
            )
        ]
    )
    doc = replay(ops, make_empty_doc())
    upper = [s for s in doc.house.slabs if s.storey_id == FF]
    assert len(upper) == 1
    assert len(upper[0].cutouts) == 1, "the flight below must punch the slab"
    assert storey_built_up_area_mm2(doc, FF) < storey_built_up_area_mm2(doc, GF)


# ---------------------------------------------------------------------------
# inverses
# ---------------------------------------------------------------------------


def _round_trip(doc: Any, candidate: Op) -> None:
    before = doc_hash(doc)
    result = fold(doc, candidate)
    assert doc_hash(result.model) != before, f"{candidate.type} was a no-op"
    undone = result.model
    for inv in result.inverse:
        undone = fold(undone, inv).model
    assert doc_hash(undone) == before, f"{candidate.type} did not round-trip"


def test_inverse_round_trip_for_every_mutating_op() -> None:
    """Every op's inverse restores the exact document (hash equality)."""
    base = make_two_room_plan_with_openings()
    rooms = sorted(base.house.rooms, key=lambda r: r.polygon[0].x)
    west = rooms[0].id

    scenarios: List[Op] = [
        op("plot.set_boundary", polygon=[{"x": 0, "y": 0}, {"x": 9000, "y": 0}, {"x": 9000, "y": 9000}]),
        op("plot.set_north", deg=270),
        op("plot.set_road", edgeIndex=1, widthMm=6000, name="Service Lane"),
        op("plot.set_reg_profile", cityPack="hyd", overrides={"farMax": 200}),
        op("brief.update", patch={"bedrooms": 3}, vastuMode="strict", completeness=55),
        op("storey.add", id=FF, index=1, name="First Floor", heightMm=3000),
        op("storey.set_height", storeyId=GF, heightMm=3300),
        op(
            "wall.add",
            id=fixed_id("wall", "NEW"),
            storeyId=GF,
            a={"x": 0, "y": 2000},
            b={"x": 3000, "y": 2000},
            thicknessMm=115,
            kind="internal",
        ),
        op("wall.move", wallId=FIXTURE_IDS["wallSpine"], a={"x": 3600, "y": 0}, b={"x": 3600, "y": 4000}),
        op("wall.split", wallId=FIXTURE_IDS["wallSpine"], atMm=2000, newWallId=fixed_id("wall", "SP2")),
        op("wall.set_thickness", wallId=FIXTURE_IDS["wallSpine"], thicknessMm=200),
        op("wall.delete", wallId=FIXTURE_IDS["wallSpine"]),
        op(
            "opening.add",
            id=fixed_id("opening", "W9"),
            wallId=FIXTURE_IDS["wallEast"],
            kind="window",
            widthMm=1200,
            heightMm=1200,
            sillMm=900,
            offsetMm=2000,
            swing="in-left",
        ),
        op("opening.move", openingId=FIXTURE_IDS["doorMain"], offsetMm=2500),
        op("opening.resize", openingId=FIXTURE_IDS["windowWest"], widthMm=1500, heightMm=1500),
        op("opening.flip", openingId=FIXTURE_IDS["doorMain"], swing="out-right"),
        op("opening.delete", openingId=FIXTURE_IDS["windowWest"]),
        op("room.assign", roomId=west, type="kitchen", name="Kitchen", tags=["wet"], locked=True),
        op("room.set_target", roomId=west, targetAreaMm2=9_500_000, mustFace="NE"),
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
            risersCount=18,
        ),
        op("column.set", action="add", id=FIXTURE_IDS["column"], storeyId=GF, pt={"x": 3000, "y": 2000}),
        op(
            "furniture.set",
            action="place",
            id=FIXTURE_IDS["sofa"],
            storeyId=GF,
            catalogId="sofa-3seat",
            pt={"x": 1500, "y": 2000},
            rotationDeg=90,
        ),
        op(
            "balcony.set",
            action="add",
            id=FIXTURE_IDS["balcony"],
            storeyId=GF,
            polygon=[
                {"x": 0, "y": 4000},
                {"x": 2400, "y": 4000},
                {"x": 2400, "y": 4900},
                {"x": 0, "y": 4900},
            ],
        ),
        op(
            "facade.apply_kit",
            kitId="contemporary",
            seed=3,
            colorwayId=None,
            components=[
                {
                    "id": fixed_id("facadecomp", "FC1"),
                    "kind": "chajja",
                    "storeyId": GF,
                    "wallId": FIXTURE_IDS["wallSouth"],
                    "openingId": None,
                    "params": {"projectionMm": 600},
                }
            ],
        ),
        op(
            "material.assign",
            id=FIXTURE_IDS["material"],
            target={"group": "external_wall", "storeyId": None, "elementId": None},
            materialId="texture-paint-grey",
        ),
        op("levels.set", plinthMm=900, parapetMm=1200),
        op(
            "annotation.set",
            action="add",
            id=FIXTURE_IDS["annotation"],
            sheetId=FIXTURE_IDS["sheet"],
            anchorElementId=FIXTURE_IDS["wallSouth"],
            anchorKind="wall",
            payload={"text": "beam over"},
        ),
    ]
    for candidate in scenarios:
        _round_trip(base, candidate)


def test_inverse_round_trip_for_the_second_action_of_combined_ops() -> None:
    """add/move/delete and place/transform/delete each need their own inverse."""
    doc = apply_group(
        make_two_room_plan(),
        [
            op(
                "column.set",
                action="add",
                id=FIXTURE_IDS["column"],
                storeyId=GF,
                pt={"x": 3000, "y": 2000},
            ),
            op(
                "furniture.set",
                action="place",
                id=FIXTURE_IDS["sofa"],
                storeyId=GF,
                catalogId="sofa-3seat",
                pt={"x": 1500, "y": 2000},
            ),
            op(
                "balcony.set",
                action="add",
                id=FIXTURE_IDS["balcony"],
                storeyId=GF,
                polygon=[
                    {"x": 0, "y": 4000},
                    {"x": 2400, "y": 4000},
                    {"x": 2400, "y": 4900},
                    {"x": 0, "y": 4900},
                ],
            ),
            op(
                "annotation.set",
                action="add",
                id=FIXTURE_IDS["annotation"],
                sheetId=FIXTURE_IDS["sheet"],
                anchorKind="sheet",
                payload={"text": "note"},
            ),
        ],
    ).model
    for candidate in [
        op("column.set", action="move", id=FIXTURE_IDS["column"], pt={"x": 3500, "y": 2500}),
        op("column.set", action="delete", id=FIXTURE_IDS["column"]),
        op("furniture.set", action="transform", id=FIXTURE_IDS["sofa"], rotationDeg=180),
        op("furniture.set", action="delete", id=FIXTURE_IDS["sofa"]),
        op("balcony.set", action="edit", id=FIXTURE_IDS["balcony"], projectionMm=1200),
        op("balcony.set", action="delete", id=FIXTURE_IDS["balcony"]),
        op("annotation.set", action="edit", id=FIXTURE_IDS["annotation"], orphaned=True),
        op("annotation.set", action="delete", id=FIXTURE_IDS["annotation"]),
    ]:
        _round_trip(doc, candidate)


def test_storey_removal_restores_everything_on_it() -> None:
    doc = apply_group(
        make_two_room_plan_with_openings(),
        [
            op("storey.add", id=FF, index=1, name="First Floor", heightMm=3000),
            op(
                "wall.add",
                id=fixed_id("wall", "FFA"),
                storeyId=FF,
                a={"x": 0, "y": 0},
                b={"x": 6000, "y": 0},
                thicknessMm=230,
                kind="external",
            ),
            op(
                "column.set",
                action="add",
                id=FIXTURE_IDS["column"],
                storeyId=FF,
                pt={"x": 3000, "y": 2000},
            ),
        ],
    ).model
    _round_trip(doc, op("storey.remove", index=1))


def _shape_signature(doc: Any) -> str:
    """Canonical form with room IDS stripped — everything else must round-trip.

    Room ids are history-dependent by design (see
    :func:`test_room_ids_depend_on_history_not_only_on_geometry`), so an undo
    that had to re-derive a dead room's id is still a correct undo of the
    DRAWING. This signature is what "correct undo" means in that case.
    """
    payload = to_jsonable(doc)
    house = payload["house"]
    house["rooms"] = sorted(
        [{k: v for k, v in room.items() if k != "id"} for room in house["rooms"]],
        key=lambda r: json.dumps(r, sort_keys=True),
    )
    return canonical_json(payload)


def _geometry_signature(doc: Any) -> str:
    """Canonical form with room IDENTITY AND METADATA stripped.

    When a room dies, both its id and its type/name/tags/lock die with it — the
    inverse restores walls and openings, and the room comes back as a fresh
    unassigned room. This is the weakest thing an undo must still guarantee: the
    DRAWING is identical.
    """
    payload = to_jsonable(doc)
    house = payload["house"]
    house["rooms"] = sorted(
        [{"polygon": room["polygon"], "areaMm2": room["areaMm2"]} for room in house["rooms"]],
        key=lambda r: json.dumps(r, sort_keys=True),
    )
    return canonical_json(payload)


def test_deleting_an_internal_wall_restores_its_openings_and_room_metadata() -> None:
    """The spine merge: both rooms survive somewhere, so undo is EXACT."""
    doc = make_two_room_plan_with_openings()
    rooms = sorted(doc.house.rooms, key=lambda r: r.polygon[0].x)
    doc = apply_group(
        doc,
        [
            op("room.assign", roomId=rooms[0].id, type="living", name="Living", locked=True),
            op("room.assign", roomId=rooms[1].id, type="bedroom_master", name="Master"),
            op("room.set_target", roomId=rooms[1].id, targetAreaMm2=11_000_000, mustFace="NE"),
        ],
    ).model
    before = doc_hash(doc)
    result = fold(doc, op("wall.delete", wallId=FIXTURE_IDS["wallSpine"]))
    assert len(result.model.house.rooms) == 1, "the two rooms merged"
    undone = result.model
    for inv in result.inverse:
        undone = fold(undone, inv).model
    assert doc_hash(undone) == before
    restored = {r.name for r in undone.house.rooms}
    assert restored == {"Living", "Master"}, "room metadata came back with the geometry"


def test_deleting_an_external_wall_restores_the_drawing_but_may_re_derive_room_ids() -> None:
    """The honest limit: a room that DIES cannot get its old id back.

    Deleting an external wall opens the envelope, so both rooms disappear. The
    inverse restores the wall and its openings exactly, and the rooms come back
    with the same geometry — but a room whose id died is re-minted from its own
    polygon, and ``_with_room_metadata_restore`` deliberately emits nothing for
    an id it cannot prove will reappear. Same behaviour in the TypeScript mirror.
    """
    doc = make_two_room_plan_with_openings()
    before_shape = _shape_signature(doc)
    before_ids = {r.id for r in doc.house.rooms}

    result = fold(doc, op("wall.delete", wallId=FIXTURE_IDS["wallSouth"]))
    assert len(result.model.house.openings) == 1, "the hosted door went with the wall"
    assert result.model.house.rooms == (), "the envelope is open, so there are no rooms"

    undone = result.model
    for inv in result.inverse:
        undone = fold(undone, inv).model

    assert _shape_signature(undone) == before_shape, "the drawing must come back exactly"
    assert {w.id for w in undone.house.walls} == {w.id for w in doc.house.walls}
    assert {o.id for o in undone.house.openings} == {o.id for o in doc.house.openings}
    assert len(undone.house.rooms) == len(doc.house.rooms)
    assert {r.id for r in undone.house.rooms} != before_ids, (
        "documented limitation: ids of rooms that died are re-derived on undo"
    )


def test_known_gap_undo_is_stranded_when_a_named_room_dies() -> None:
    """KNOWN GAP — pinned so it cannot regress silently, and cannot be forgotten.

    Two ops are enough:

    1. ``room.assign`` names a room. Its inverse is ``room.assign`` back — an op
       that names the room BY ID.
    2. ``wall.move`` drags an external wall outward. The envelope opens, the room
       disappears, and (because that room's id was INHERITED from an earlier
       whole-floor room rather than derived from its own polygon) undoing the
       move brings the space back under a freshly derived id.

    Walking the stack back now raises ``ROOM_UNKNOWN`` on step 1's inverse: the
    inverse was computed when the group was applied and it names a room that no
    longer exists. In the editor this is a user naming a room and then dragging a
    wall — undo must not throw.

    The TypeScript mirror computes inverses the same way and rejects
    ``room.assign`` for a missing room the same way, so this is a property of the
    model core, not of this mirror. Fixing it is a shared decision (make
    metadata-only ops best-effort inside an undo group, have the room-killing op
    carry the dead room's id so it can be resurrected, or re-derive the inverse
    at undo time) — see the contract notes.
    """
    doc = make_two_room_plan()
    west = min(doc.house.rooms, key=lambda r: r.polygon[0].x)
    stack = UndoStack()

    history = [
        [op("room.assign", roomId=west.id, type="living", name="Living")],
        [op("wall.move", wallId=FIXTURE_IDS["wallWest"], a={"x": -250, "y": 4000}, b={"x": -250, "y": 0})],
    ]
    for i, ops in enumerate(history):
        gid = fixed_id("group", f"K{i}")
        result = apply_group(doc, ops, gid)
        stack.push(UndoEntry(group_id=gid, ops=result.ops, inverse=result.inverse))
        doc = result.model

    # the wall.move killed the named room
    assert west.id not in {r.id for r in doc.house.rooms}

    out = stack.undo(doc)  # undo the wall.move: geometry comes back...
    assert out is not None
    doc = out[0]
    assert len(doc.house.rooms) == 2
    assert west.id not in {r.id for r in doc.house.rooms}, "...but under a new id"

    with pytest.raises(OpRejectedError) as excinfo:
        stack.undo(doc)  # undo the room.assign: its inverse names a dead room
    assert excinfo.value.issues[0].code == "ROOM_UNKNOWN"


def test_first_plot_boundary_has_an_expressible_inverse() -> None:
    doc = make_empty_doc()
    _round_trip(
        doc,
        op(
            "plot.set_boundary",
            polygon=[
                {"x": 0, "y": 0},
                {"x": 9144, "y": 0},
                {"x": 9144, "y": 12192},
                {"x": 0, "y": 12192},
            ],
            source="manual",
        ),
    )


def test_shrinking_the_boundary_restores_the_roads_it_dropped() -> None:
    doc = apply_group(
        make_empty_doc(),
        [
            op(
                "plot.set_boundary",
                polygon=[
                    {"x": 0, "y": 0},
                    {"x": 9000, "y": 0},
                    {"x": 9000, "y": 12000},
                    {"x": 0, "y": 12000},
                ],
            ),
            op("plot.set_road", edgeIndex=3, widthMm=9000, name="9m Road"),
        ],
    ).model
    _round_trip(
        doc,
        op(
            "plot.set_boundary",
            polygon=[{"x": 0, "y": 0}, {"x": 9000, "y": 0}, {"x": 9000, "y": 12000}],
        ),
    )


# ---------------------------------------------------------------------------
# groups, solver expansion, undo/redo
# ---------------------------------------------------------------------------


def test_group_inverse_is_the_reversed_concatenation() -> None:
    doc = make_two_room_plan()
    result = apply_group(doc, opening_ops(), fixed_id("group", "G1"))
    assert all(o.group_id == fixed_id("group", "G1") for o in result.ops)
    assert all(o.group_id == fixed_id("group", "G1") for o in result.inverse)
    undone = result.model
    for inv in result.inverse:
        undone = fold(undone, inv).model
    assert doc_hash(undone) == doc_hash(doc)


def test_solver_option_equals_applying_its_expansion_directly() -> None:
    inner = [o.to_json() for o in two_room_plan_ops()[4:]]  # the five wall.add ops
    head = two_room_plan_ops()[:4]
    direct = apply_group(make_empty_doc(), head + [Op.from_json(o) for o in inner]).model
    via_solver = apply_group(
        make_empty_doc(),
        head + [op("solver.apply_option", solverJobId="job_x", optionIndex=0, ops=inner)],
    ).model
    assert doc_hash(via_solver) == doc_hash(direct)


def test_solver_option_is_atomic() -> None:
    """A bad op inside the expansion rolls the whole option back."""
    doc = make_two_room_plan()
    before = doc_hash(doc)
    outcome = try_fold(
        doc,
        op(
            "solver.apply_option",
            solverJobId="job_x",
            optionIndex=0,
            ops=[
                op("plot.set_north", deg=45).to_json(),
                op("wall.delete", wallId=fixed_id("wall", "ZZ")).to_json(),
            ],
        ),
    )
    assert outcome.ok is False
    assert doc_hash(doc) == before


def test_undo_stack_walks_the_history_both_ways() -> None:
    doc = make_two_room_plan()
    stack = UndoStack()
    hashes = [doc_hash(doc)]
    # Every group here PRESERVES the room id set, so undo is exact to the hash.
    # Groups that destroy or mint rooms are covered by the two wall.delete tests.
    groups = [
        [op("plot.set_north", deg=90)],
        opening_ops(),
        [op("wall.set_thickness", wallId=FIXTURE_IDS["wallSpine"], thicknessMm=200)],
        [op("levels.set", plinthMm=900, parapetMm=1200)],
        [op("brief.update", patch={"bedrooms": 3}, completeness=40)],
    ]
    for i, ops in enumerate(groups):
        gid = fixed_id("group", f"G{i}")
        result = apply_group(doc, ops, gid)
        stack.push(UndoEntry(group_id=gid, ops=result.ops, inverse=result.inverse, label=f"step {i}"))
        doc = result.model
        hashes.append(doc_hash(doc))

    assert stack.undo_depth == len(groups)
    assert stack.next_undo_label == f"step {len(groups) - 1}"

    while stack.can_undo:
        undone = stack.undo(doc)
        assert undone is not None
        doc = undone[0]
        hashes.pop()
        assert doc_hash(doc) == hashes[-1]

    assert not stack.can_undo
    assert stack.redo_depth == len(groups)

    while stack.can_redo:
        redone = stack.redo(doc)
        assert redone is not None
        doc = redone[0]
        hashes.append(doc_hash(doc))
    assert doc_hash(doc) == hashes[-1]
    assert stack.undo_depth == len(groups)


def test_undo_stack_serialises() -> None:
    doc = make_two_room_plan()
    stack = UndoStack()
    result = apply_group(doc, opening_ops(), fixed_id("group", "G1"))
    stack.push(
        UndoEntry(
            group_id=fixed_id("group", "G1"),
            ops=result.ops,
            inverse=result.inverse,
            label="Openings added",
        )
    )
    restored = UndoStack.from_json(json.loads(json.dumps(stack.to_json())))
    assert restored.undo_depth == 1
    assert restored.next_undo_label == "Openings added"
    out = restored.undo(result.model)
    assert out is not None
    assert doc_hash(out[0]) == doc_hash(doc)


def test_pushing_clears_the_redo_branch() -> None:
    doc = make_two_room_plan()
    stack = UndoStack()
    r1 = apply_group(doc, [op("plot.set_north", deg=45)], fixed_id("group", "G1"))
    stack.push(UndoEntry(group_id=fixed_id("group", "G1"), ops=r1.ops, inverse=r1.inverse))
    doc = r1.model
    out = stack.undo(doc)
    assert out is not None
    doc = out[0]
    assert stack.can_redo
    r2 = apply_group(doc, [op("plot.set_north", deg=180)], fixed_id("group", "G2"))
    stack.push(UndoEntry(group_id=fixed_id("group", "G2"), ops=r2.ops, inverse=r2.inverse))
    assert not stack.can_redo


# ---------------------------------------------------------------------------
# property: long random histories
# ---------------------------------------------------------------------------

_ROTATIONS = [0, 90, 180, 270]


def _candidate_ops(rng: random.Random, doc: Any, step: int) -> List[Op]:
    """A small, plausible edit for the current document (may be inapplicable)."""
    kind = rng.randrange(10)
    walls = [w for w in doc.house.walls if w.storey_id == GF]
    rooms = list(doc.house.rooms)
    openings = list(doc.house.openings)

    if kind == 0:
        x = 500 * rng.randint(2, 10)
        return [
            op(
                "wall.add",
                id=fixed_id("wall", f"R{step}"),
                storeyId=GF,
                a={"x": x, "y": 0},
                b={"x": x, "y": 4000},
                thicknessMm=rng.choice([115, 150, 200]),
                kind="internal",
            )
        ]
    if kind == 1 and walls:
        wall = rng.choice(walls)
        return [op("wall.set_thickness", wallId=wall.id, thicknessMm=rng.choice([115, 200, 230]))]
    if kind == 2 and walls:
        wall = rng.choice([w for w in walls if w.a.x == w.b.x] or walls)
        dx = rng.choice([-500, -250, 250, 500])
        return [
            op(
                "wall.move",
                wallId=wall.id,
                a={"x": wall.a.x + dx, "y": wall.a.y},
                b={"x": wall.b.x + dx, "y": wall.b.y},
            )
        ]
    if kind == 3 and walls:
        wall = rng.choice(walls)
        return [
            op(
                "opening.add",
                id=fixed_id("opening", f"R{step}"),
                wallId=wall.id,
                kind="window",
                widthMm=900,
                heightMm=1200,
                sillMm=900,
                offsetMm=2000,
                swing="in-left",
            )
        ]
    if kind == 4 and openings:
        o = rng.choice(openings)
        return [op("opening.move", openingId=o.id, offsetMm=rng.choice([1500, 2000, 2500]))]
    if kind == 5 and openings:
        o = rng.choice(openings)
        return [op("opening.flip", openingId=o.id, swing=rng.choice(["in-left", "out-right"]))]
    if kind == 6 and rooms:
        r = rng.choice(rooms)
        return [
            op(
                "room.assign",
                roomId=r.id,
                type=rng.choice(["living", "bedroom", "kitchen", "store"]),
                name=f"R{step}",
            )
        ]
    if kind == 7:
        return [
            op(
                "column.set",
                action="add",
                id=fixed_id("column", f"R{step}"),
                storeyId=GF,
                pt={"x": 500 * rng.randint(1, 10), "y": 500 * rng.randint(1, 7)},
            )
        ]
    if kind == 8:
        return [
            op(
                "furniture.set",
                action="place",
                id=fixed_id("furniture", f"R{step}"),
                storeyId=GF,
                catalogId="bed-queen-1900x1525",
                pt={"x": 500 * rng.randint(1, 10), "y": 500 * rng.randint(1, 7)},
                rotationDeg=rng.choice(_ROTATIONS),
            )
        ]
    return [op("levels.set", plinthMm=rng.choice([450, 600, 750, 900]))]


@given(seed=st.integers(min_value=0, max_value=5_000))
@settings(max_examples=20, deadline=None)
def test_random_history_undoes_and_redoes_exactly(seed: int) -> None:
    """Phase 1 DoD: undo/redo round-trips a long random history by state hash.

    Ops that the engine legitimately rejects (a duplicate wall, an opening that
    no longer fits) are skipped — rejection is tested in ``test_validate.py``;
    what is under test here is that everything APPLIED can be walked back and
    forwards without drift.
    """
    rng = random.Random(seed)
    doc = make_two_room_plan()
    stack = UndoStack()
    hashes = [doc_hash(doc)]
    geometries = [_geometry_signature(doc)]
    #: step_is_exact[i] — did group i preserve the SET of room ids?
    step_is_exact: List[bool] = []
    applied = 0

    for step in range(25):
        candidate = _candidate_ops(rng, doc, step)
        outcome = try_fold(doc, candidate[0])
        if not outcome.ok:
            continue
        gid = fixed_id("group", f"S{step}")
        before_rooms = {r.id for r in doc.house.rooms}
        result = apply_group(doc, candidate, gid)
        stack.push(UndoEntry(group_id=gid, ops=result.ops, inverse=result.inverse))
        doc = result.model
        # A group that CHANGES THE SET of room ids (a room died, or a new
        # enclosure was minted) may not reproduce those ids on undo: the id
        # follows the max-Jaccard chain forwards, and the chain is not
        # invertible once a link is gone. See
        # test_deleting_an_external_wall_restores_the_drawing_but_may_re_derive_room_ids.
        step_is_exact.append(before_rooms == {r.id for r in doc.house.rooms})
        hashes.append(doc_hash(doc))
        geometries.append(_geometry_signature(doc))
        applied += 1

    assert applied >= 5, "the generator produced almost nothing applicable"

    def hash_is_reachable(index: int) -> bool:
        """State `index` is reproducible by undo iff every LATER step was exact."""
        return all(step_is_exact[index:])

    # --- walk the whole history back
    for index in range(applied - 1, -1, -1):
        try:
            out = stack.undo(doc)
        except OpRejectedError as e:
            # KNOWN GAP, pinned by
            # test_known_gap_undo_is_stranded_when_a_named_room_dies: an inverse
            # is computed when the group is applied, so it can name a room id
            # that a LATER undo could not bring back. Assert it is exactly that
            # case and stop — the rest of the walk-back is meaningless.
            assert e.issues[0].code == "ROOM_UNKNOWN", e
            assert not all(step_is_exact[index:]), "a room must have died to strand this inverse"
            return
        assert out is not None
        doc = out[0]
        assert _geometry_signature(doc) == geometries[index], "the drawing must come back"
        if hash_is_reachable(index):
            assert doc_hash(doc) == hashes[index]

    assert not stack.can_undo
    assert stack.redo_depth == applied

    # --- and forwards again
    for index in range(1, applied + 1):
        out = stack.redo(doc)
        assert out is not None
        doc = out[0]
        assert _geometry_signature(doc) == geometries[index]
        if all(step_is_exact):
            assert doc_hash(doc) == hashes[index]

    assert stack.undo_depth == applied


@given(seed=st.integers(min_value=0, max_value=5_000))
@settings(max_examples=20, deadline=None)
def test_op_log_replays_to_the_same_hash(seed: int) -> None:
    """Fold a random log, then replay the same log from scratch: identical state."""
    rng = random.Random(seed)
    doc = make_two_room_plan()
    log: List[Op] = list(two_room_plan_ops())

    for step in range(20):
        candidate = _candidate_ops(rng, doc, step)[0]
        outcome = try_fold(doc, candidate)
        if not outcome.ok:
            continue
        assert outcome.model is not None
        doc = outcome.model
        log.append(candidate)

    assert doc_hash(replay(log, make_empty_doc())) == doc_hash(doc)
    # and again, from the wire form
    wire = json.loads(json.dumps([o.to_json() for o in log]))
    assert doc_hash(replay(wire, make_empty_doc())) == doc_hash(doc)


# ---------------------------------------------------------------------------
# THE cross-language check
# ---------------------------------------------------------------------------


def test_golden_states_match_the_cross_language_fixture(golden_states: List[Dict[str, Any]]) -> None:
    """Every case in ``fixtures/model/golden-states.json`` folds to its hash.

    ``packages/model/src/fold.test.ts`` asserts the same rows. A failure here
    means Python and TypeScript disagree about what a design IS — do not paste
    the new hash; find out which side moved.
    """
    assert len(golden_states) >= 10
    for case in golden_states:
        initial = empty_project_doc(case.get("unitsDisplay", "ft-in"))
        doc = apply_group(initial, [Op.from_json(o) for o in case["ops"]]).model
        assert doc_hash(doc) == case["expectedStateHash"], case["name"]


def test_golden_states_are_also_stable_under_replay(golden_states: List[Dict[str, Any]]) -> None:
    for case in golden_states:
        initial = empty_project_doc(case.get("unitsDisplay", "ft-in"))
        assert doc_hash(replay(case["ops"], initial)) == case["expectedStateHash"], case["name"]


def test_two_room_golden_case_has_the_documented_geometry(
    golden_states: List[Dict[str, Any]],
) -> None:
    """Guards the fixture itself: a silently-changed fixture proves nothing."""
    case = next(c for c in golden_states if c["name"] == "two-room-plan")
    doc = apply_group(make_empty_doc(), [Op.from_json(o) for o in case["ops"]]).model
    assert len(doc.house.rooms) == 2
    assert all(r.area_mm2 == 10_661_560 for r in doc.house.rooms)
    assert len(doc.house.slabs) == 1
