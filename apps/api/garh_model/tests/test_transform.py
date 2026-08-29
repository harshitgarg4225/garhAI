"""copy / paste / array / mirror — the Python half of the planner contract.

The heart of this file is :func:`test_golden_transforms_match_the_cross_language_fixture`
and its neighbours: every row of ``fixtures/model/golden-transforms.json`` is
asserted here AND in ``packages/model/src/transform.test.ts``. These transforms
add no op type, so a divergence between the two planners would never be caught by
a fold — it would surface as the browser and the server disagreeing about a
document the architect is still editing.

Everything above the corpus is the unit-level reasoning the corpus depends on:
the plane map's algebra, the door-hand rule, the stair's origin corner, and the
guards. Each guard is written so that deleting the thing it guards turns this
file red.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from garh_model.fold import (
    UndoEntry,
    UndoStack,
    apply_group,
    doc_hash,
    replay,
    state_hash,
)
from garh_model.geometry import Pt
from garh_model.model import HouseModel, ProjectDoc, Room, empty_project_doc
from garh_model.ops import Op, op
from garh_model.testing import (
    FIXTURE_IDS,
    fixed_id,
    make_two_room_plan,
    make_two_room_plan_with_openings,
    two_room_plan_ops,
)
from garh_model.transform import (
    IDENTITY_MAP,
    MAX_ARRAY_INSTANCES,
    ArrayRequest,
    MirrorRequest,
    PasteRequest,
    SelectionCounts,
    TransformPlan,
    TransformPlanResult,
    _room_metadata_ops,
    describe_selection,
    is_reflection,
    map_direction,
    map_polygon,
    map_pt,
    map_rotation_deg,
    map_stair_placement,
    map_swing,
    plan_array,
    plan_mirror,
    plan_paste,
    reflection_map,
    translation_map,
)

GROUP = fixed_id("group", "GTEST")
GF = FIXTURE_IDS["groundStorey"]
FF = FIXTURE_IDS["firstStorey"]
WALLS = [
    FIXTURE_IDS["wallSouth"],
    FIXTURE_IDS["wallEast"],
    FIXTURE_IDS["wallNorth"],
    FIXTURE_IDS["wallWest"],
    FIXTURE_IDS["wallSpine"],
]


def expect_plan(result: TransformPlanResult) -> TransformPlan:
    assert result.ok, f"expected a plan, got refusal {result.refusal}"
    assert result.plan is not None
    return result.plan


def refusal_reason(result: TransformPlanResult) -> str:
    if result.ok:
        return "ok"
    assert result.refusal is not None
    return result.refusal.reason


def geometry_signature(doc: ProjectDoc) -> str:
    """Everything a transform must put back, EXCEPT derived room identity.

    A mirror in place has to delete and re-add its walls (see the module
    docstring of :mod:`garh_model.transform`), and the fold cannot carry a
    derived room's ID through that — ``wall.delete`` x n followed by ``wall.add``
    x n at IDENTICAL coordinates loses it too, in both languages, with no
    transform involved at all. Room ids are history and no op sets one. So the
    round-trip assertion is written against the geometry, which IS fully
    restorable and IS this module's job.
    """
    h = doc.house
    return json.dumps(
        {
            "walls": [
                [
                    w.id,
                    w.storey_id,
                    [w.a.x, w.a.y],
                    [w.b.x, w.b.y],
                    w.thickness_mm,
                    w.kind,
                    w.load_bearing,
                ]
                for w in h.walls
            ],
            "openings": [
                [o.id, o.wall_id, o.kind, o.width_mm, o.height_mm, o.sill_mm, o.offset_mm, o.swing]
                for o in h.openings
            ],
            "stairs": [
                [s.id, s.storey_id, s.kind, [s.origin.x, s.origin.y], s.direction, s.risers_count]
                for s in h.stairs
            ],
            "columns": [
                [c.id, c.storey_id, [c.pt.x, c.pt.y], [c.size_mm.x_mm, c.size_mm.y_mm]]
                for c in h.columns
            ],
            "furniture": [
                [f.id, f.storey_id, f.catalog_id, [f.pt.x, f.pt.y], f.rotation_deg]
                for f in h.furniture
            ],
            "balconies": [
                [b.id, b.storey_id, [[p.x, p.y] for p in b.polygon]] for b in h.balconies
            ],
            "roomPolygons": [
                [r.storey_id, [[p.x, p.y] for p in r.polygon], r.area_mm2] for r in h.rooms
            ],
        },
        sort_keys=True,
    )


# ---------------------------------------------------------------------------
# The plane map
# ---------------------------------------------------------------------------


def test_translation_is_exact() -> None:
    assert map_pt(translation_map(1500, -250), Pt(100, 100)) == Pt(1600, -150)


def test_vertical_reflection_is_an_exact_involution() -> None:
    # ``twice_at`` is 2*9000; the axis is x = 9000.
    m = reflection_map("vertical", 18_000)
    p = Pt(2345, 678)
    assert map_pt(m, p) == Pt(15_655, 678)
    assert map_pt(m, map_pt(m, p)) == p


def test_half_millimetre_axis_does_not_drift() -> None:
    # The selection-centre default: an extent of 0..4001 has its centre at
    # 2000.5, which is why the map carries 2*at as an integer. Rounding the axis
    # instead would move every mirrored point by half a millimetre and would not
    # be an involution.
    m = reflection_map("vertical", 4001)
    assert map_pt(m, Pt(0, 0)) == Pt(4001, 0)
    assert map_pt(m, Pt(4001, 0)) == Pt(0, 0)
    assert map_pt(m, map_pt(m, Pt(1234, 0))) == Pt(1234, 0)


def test_orientation_reversal_is_recognised() -> None:
    assert not is_reflection(IDENTITY_MAP)
    assert not is_reflection(translation_map(10, 20))
    assert is_reflection(reflection_map("vertical", 0))
    assert is_reflection(reflection_map("horizontal", 0))
    # Two reflections compose to a 180-degree rotation, which preserves orientation.
    from garh_model.transform import PlaneMap

    assert not is_reflection(PlaneMap(sx=-1, sy=-1, tx=0, ty=0))


def test_ring_is_rewound_under_a_reflection_only() -> None:
    ccw = [Pt(0, 0), Pt(100, 0), Pt(100, 100), Pt(0, 100)]
    assert map_polygon(translation_map(10, 10), ccw) == [
        Pt(10, 10),
        Pt(110, 10),
        Pt(110, 110),
        Pt(10, 110),
    ]
    assert map_polygon(reflection_map("vertical", 0), ccw) == [
        Pt(0, 100),
        Pt(-100, 100),
        Pt(-100, 0),
        Pt(0, 0),
    ]


def test_directions_of_travel_map() -> None:
    v = reflection_map("vertical", 0)
    h = reflection_map("horizontal", 0)
    assert [map_direction(v, d) for d in ("N", "E", "S", "W")] == ["N", "W", "S", "E"]
    assert [map_direction(h, d) for d in ("N", "E", "S", "W")] == ["S", "E", "N", "W"]


# ---------------------------------------------------------------------------
# The door hand — the geometry claim most likely to be quietly wrong
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("axis", ["vertical", "horizontal"])
def test_mirror_flips_in_out_and_keeps_left_right(axis: str) -> None:
    # LEFT/RIGHT is the hinge END along the wall's a->b parameter; a reflection
    # that maps a -> M(a), b -> M(b) preserves it. IN/OUT is which side of that
    # line the leaf sweeps into, and a reflection reverses exactly that.
    m = reflection_map(axis, 0)
    assert map_swing(m, "in-left") == "out-left"
    assert map_swing(m, "in-right") == "out-right"
    assert map_swing(m, "out-left") == "in-left"
    assert map_swing(m, "out-right") == "in-right"


def test_translation_leaves_the_hand_alone() -> None:
    assert map_swing(translation_map(9000, 0), "in-left") == "in-left"
    assert map_swing(IDENTITY_MAP, "out-right") == "out-right"


def test_refuses_a_swing_that_is_not_in_the_enum() -> None:
    # Bug class 2 in CLAUDE.md: a value outside the enum going quietly inert. A
    # bare dict subscript would raise KeyError here while the TypeScript mirror
    # handed back `undefined` — a door with no hand. Both now fail, loudly and
    # the same way.
    with pytest.raises(ValueError, match="Not an opening swing"):
        map_swing(reflection_map("vertical", 0), "sliding")
    # ...but only when the map actually reflects: a translation is a pass-through
    # by definition and must not start validating its input.
    assert map_swing(translation_map(1, 0), "sliding") == "sliding"


def test_refuses_a_map_it_does_not_know_rather_than_silently_not_rotating() -> None:
    # No fallback to the identity: an unknown map must not leave every mirrored
    # item facing the way it already faced while everything else moves.
    from garh_model.transform import PlaneMap

    with pytest.raises(ValueError, match="Not a plane map"):
        map_rotation_deg(PlaneMap(sx=2, sy=1, tx=0, ty=0), 30)


def test_swing_mapping_is_an_involution() -> None:
    m = reflection_map("vertical", 0)
    for swing in ("in-left", "in-right", "out-left", "out-right"):
        assert map_swing(m, map_swing(m, swing)) == swing


def test_mirroring_a_real_door_keeps_its_offset_and_changes_its_hand() -> None:
    doc = make_two_room_plan_with_openings()
    plan = expect_plan(
        plan_mirror(
            doc,
            MirrorRequest(element_ids=WALLS, group_id=GROUP, axis="vertical", at_mm=9000),
        )
    )
    openings = [o for o in plan.ops if o.type == "opening.add"]
    assert len(openings) == 2
    for one in openings:
        assert str(one.get("swing")).startswith("out-")
    door = next(o for o in openings if o.get("kind") == "door")
    # The offset is a distance along the wall; a reflection is an isometry, so it
    # cannot change. If the wall were re-normalised left-to-right instead of
    # keeping a -> M(a), this would be ``length - offset`` and the door would move.
    assert door.get("offsetMm") == 1500
    assert door.get("swing") == "out-left"


# ---------------------------------------------------------------------------
# Furniture rotation: a rotation, never a reflection
# ---------------------------------------------------------------------------


def test_furniture_rotation_reflects_the_facing_axis() -> None:
    v = reflection_map("vertical", 0)
    h = reflection_map("horizontal", 0)
    assert map_rotation_deg(v, 0) == 180
    assert map_rotation_deg(v, 30) == 150
    assert map_rotation_deg(v, 90) == 90
    assert map_rotation_deg(h, 0) == 0
    assert map_rotation_deg(h, 30) == 330
    assert map_rotation_deg(h, 90) == 270


def test_furniture_rotation_stays_in_range_and_round_trips() -> None:
    v = reflection_map("vertical", 0)
    h = reflection_map("horizontal", 0)
    for deg in range(360):
        for m in (v, h, translation_map(1, 1)):
            out = map_rotation_deg(m, deg)
            assert isinstance(out, int)
            assert 0 <= out < 360
        assert map_rotation_deg(v, map_rotation_deg(v, deg)) == deg
        assert map_rotation_deg(h, map_rotation_deg(h, deg)) == deg


# ---------------------------------------------------------------------------
# Stair origin: a direction-dependent corner, not a mappable point
# ---------------------------------------------------------------------------


def _stair() -> Any:
    from garh_model.model import Stair, StairLanding

    return Stair(
        id=FIXTURE_IDS["stair"],
        storey_id=GF,
        kind="dogleg",
        origin=Pt(1000, 500),
        direction="N",
        riser_mm=167,
        tread_mm=275,
        width_mm=1000,
        risers_count=18,
        landing=StairLanding(width_mm=2100, depth_mm=1000),
    )


def test_stair_placement_is_the_identity_under_the_identity_map() -> None:
    # The round trip is the real assertion: it proves the corner rule agrees with
    # ``stair_footprint_polygon``, which is where the extents actually live.
    assert map_stair_placement(IDENTITY_MAP, _stair()) == (Pt(1000, 500), "N")


def test_stair_keeps_travel_direction_under_a_mirror_in_the_same_axis() -> None:
    # Footprint is x 1000..3100, y 500..3700. Mirrored about x = 12000 the x
    # range becomes 20900..23000, and N travel is unchanged, so the origin corner
    # (min_x, min_y) is (20900, 500).
    assert map_stair_placement(reflection_map("vertical", 24_000), _stair()) == (
        Pt(20_900, 500),
        "N",
    )


def test_stair_reverses_travel_direction_under_a_mirror_across_it() -> None:
    # Mirrored about y = 0: y range becomes -3700..-500, travel becomes S, and an
    # S stair's origin corner is (max_x, max_y) = (3100, -500).
    assert map_stair_placement(reflection_map("horizontal", 0), _stair()) == (
        Pt(3100, -500),
        "S",
    )


def test_stair_placement_round_trips_through_two_mirrors() -> None:
    from dataclasses import replace as dc_replace

    m = reflection_map("vertical", 24_000)
    stair = _stair()
    origin, direction = map_stair_placement(m, stair)
    once = dc_replace(stair, origin=origin, direction=direction)
    assert map_stair_placement(m, once) == (stair.origin, stair.direction)


# ---------------------------------------------------------------------------
# The guards
# ---------------------------------------------------------------------------


def test_refuses_a_selection_that_spans_two_storeys() -> None:
    # A transform has ONE target storey. Flattening a G+1 selection onto it folds
    # cleanly and is wrong, which is exactly the shape of bug this repo keeps
    # shipping — so it is a refusal, not a best effort.
    doc = replay(
        [
            *two_room_plan_ops(),
            op("storey.add", id=FF, index=1, name="First Floor", heightMm=3000),
            op(
                "wall.add",
                id=fixed_id("wall", "FFS"),
                storeyId=FF,
                a={"x": 0, "y": 0},
                b={"x": 6000, "y": 0},
                thicknessMm=230,
                kind="external",
            ),
        ],
        empty_project_doc("ft-in"),
    )
    result = plan_paste(
        doc,
        PasteRequest(
            element_ids=[FIXTURE_IDS["wallSouth"], fixed_id("wall", "FFS")],
            group_id=GROUP,
            delta_mm=Pt(0, 8000),
        ),
    )
    assert refusal_reason(result) == "mixed-storeys"


def test_refuses_a_zero_delta_paste_onto_the_same_storey_where_the_fold_would_not() -> None:
    # This is the guard's whole justification. Walls are protected by
    # WALL_DUPLICATE, but columns are not: prove the fold accepts a second column
    # on the same point, so the reader can see the planner is the only thing
    # standing between the user and a doubled structural count.
    doc = replay(
        [
            *two_room_plan_ops(),
            op(
                "column.set",
                action="add",
                id=FIXTURE_IDS["column"],
                storeyId=GF,
                pt={"x": 3000, "y": 2000},
            ),
        ],
        empty_project_doc("ft-in"),
    )
    stacked = replay(
        [
            op(
                "column.set",
                action="add",
                id=fixed_id("column", "C2"),
                storeyId=GF,
                pt={"x": 3000, "y": 2000},
            )
        ],
        doc,
    )
    assert len(stacked.house.columns) == 2

    result = plan_paste(
        doc,
        PasteRequest(element_ids=[FIXTURE_IDS["column"]], group_id=GROUP, delta_mm=Pt(0, 0)),
    )
    assert refusal_reason(result) == "zero-offset"


def test_allows_a_zero_delta_paste_onto_a_different_storey() -> None:
    doc = replay(
        [
            *two_room_plan_ops(),
            op("storey.add", id=FF, index=1, name="First Floor", heightMm=3000),
        ],
        empty_project_doc("ft-in"),
    )
    plan = expect_plan(
        plan_paste(
            doc,
            PasteRequest(element_ids=WALLS, group_id=GROUP, delta_mm=Pt(0, 0), target_storey_id=FF),
        )
    )
    assert plan.created.walls == 5
    assert plan.target_storey_id == FF


def test_refuses_an_array_with_no_spacing_in_a_direction_it_repeats() -> None:
    doc = replay(
        [
            *two_room_plan_ops(),
            op(
                "column.set",
                action="add",
                id=FIXTURE_IDS["column"],
                storeyId=GF,
                pt={"x": 3000, "y": 2000},
            ),
        ],
        empty_project_doc("ft-in"),
    )
    assert (
        refusal_reason(
            plan_array(
                doc,
                ArrayRequest(
                    element_ids=[FIXTURE_IDS["column"]],
                    group_id=GROUP,
                    count_x=3,
                    count_y=2,
                    spacing_x_mm=0,
                    spacing_y_mm=1500,
                ),
            )
        )
        == "zero-offset"
    )
    # ...but a zero spacing in a direction with count 1 is meaningless, not wrong.
    assert (
        expect_plan(
            plan_array(
                doc,
                ArrayRequest(
                    element_ids=[FIXTURE_IDS["column"]],
                    group_id=GROUP,
                    count_x=3,
                    count_y=1,
                    spacing_x_mm=2000,
                    spacing_y_mm=0,
                ),
            )
        ).instances
        == 2
    )


@pytest.mark.parametrize(
    ("count_x", "count_y"),
    [(0, 3), (1, 1), (MAX_ARRAY_INSTANCES, 2)],
)
def test_refuses_counts_outside_the_range(count_x: int, count_y: int) -> None:
    doc = make_two_room_plan()
    result = plan_array(
        doc,
        ArrayRequest(
            element_ids=[FIXTURE_IDS["wallSpine"]],
            group_id=GROUP,
            count_x=count_x,
            count_y=count_y,
            spacing_x_mm=1000,
            spacing_y_mm=1000,
        ),
    )
    assert refusal_reason(result) == "count-out-of-range"


def test_refuses_an_opening_whose_host_wall_is_not_selected() -> None:
    doc = make_two_room_plan_with_openings()
    assert (
        refusal_reason(
            plan_paste(
                doc,
                PasteRequest(
                    element_ids=[FIXTURE_IDS["doorMain"]], group_id=GROUP, delta_mm=Pt(0, 8000)
                ),
            )
        )
        == "opening-without-wall"
    )


def test_carries_an_unselected_opening_with_its_selected_wall() -> None:
    doc = make_two_room_plan_with_openings()
    plan = expect_plan(
        plan_paste(
            doc,
            PasteRequest(
                element_ids=[FIXTURE_IDS["wallSouth"]], group_id=GROUP, delta_mm=Pt(0, 8000)
            ),
        )
    )
    assert plan.selected.openings == 1
    assert len([o for o in plan.ops if o.type == "opening.add"]) == 1


def test_refuses_an_id_that_is_no_longer_in_the_document() -> None:
    assert (
        refusal_reason(
            plan_paste(
                make_two_room_plan(),
                PasteRequest(
                    element_ids=[fixed_id("wall", "GHOST")], group_id=GROUP, delta_mm=Pt(1000, 0)
                ),
            )
        )
        == "unknown-element"
    )


def test_refuses_a_family_it_cannot_honestly_duplicate() -> None:
    assert (
        refusal_reason(
            plan_paste(
                make_two_room_plan(),
                PasteRequest(
                    element_ids=[FIXTURE_IDS["annotation"]], group_id=GROUP, delta_mm=Pt(1000, 0)
                ),
            )
        )
        == "unsupported-element"
    )


def test_skips_derived_rooms_in_a_mixed_selection_but_refuses_a_rooms_only_one() -> None:
    doc = make_two_room_plan()
    room_id = doc.house.rooms[0].id
    plan = expect_plan(
        plan_paste(
            doc,
            PasteRequest(
                element_ids=[FIXTURE_IDS["wallSpine"], room_id],
                group_id=GROUP,
                delta_mm=Pt(0, 9000),
            ),
        )
    )
    assert plan.derived_skipped == 1
    assert plan.selected.walls == 1
    assert (
        refusal_reason(
            plan_paste(
                doc,
                PasteRequest(element_ids=[room_id], group_id=GROUP, delta_mm=Pt(0, 9000)),
            )
        )
        == "empty-selection"
    )


def test_refuses_when_the_fold_refuses_and_says_why() -> None:
    # A copy landing exactly on an existing wall is WALL_DUPLICATE. The planner
    # does not re-implement that rule; it folds on a fork and reports what the
    # fold said, so there is one source of truth for what a legal wall is.
    result = plan_paste(
        make_two_room_plan(),
        PasteRequest(element_ids=[FIXTURE_IDS["wallSouth"]], group_id=GROUP, delta_mm=Pt(0, 4000)),
    )
    assert refusal_reason(result) == "rejected"
    assert result.refusal is not None
    assert "WALL_DUPLICATE" in [i.code for i in result.refusal.issues]


def test_refuses_an_unknown_target_storey() -> None:
    assert (
        refusal_reason(
            plan_paste(
                make_two_room_plan(),
                PasteRequest(
                    element_ids=[FIXTURE_IDS["wallSpine"]],
                    group_id=GROUP,
                    delta_mm=Pt(0, 0),
                    target_storey_id=fixed_id("storey", "NOPE"),
                ),
            )
        )
        == "unknown-storey"
    )


# ---------------------------------------------------------------------------
# One gesture, one undo
# ---------------------------------------------------------------------------


def _paste_plan(doc: ProjectDoc) -> TransformPlanResult:
    return plan_paste(doc, PasteRequest(element_ids=WALLS, group_id=GROUP, delta_mm=Pt(0, 9000)))


def _array_plan(doc: ProjectDoc) -> TransformPlanResult:
    return plan_array(
        doc,
        ArrayRequest(
            element_ids=[FIXTURE_IDS["wallSpine"]],
            group_id=GROUP,
            count_x=4,
            count_y=1,
            spacing_x_mm=-4000,
            spacing_y_mm=0,
        ),
    )


def _mirror_copy_plan(doc: ProjectDoc) -> TransformPlanResult:
    return plan_mirror(
        doc, MirrorRequest(element_ids=WALLS, group_id=GROUP, axis="vertical", at_mm=9000)
    )


def _mirror_in_place_plan(doc: ProjectDoc) -> TransformPlanResult:
    return plan_mirror(
        doc,
        MirrorRequest(element_ids=WALLS, group_id=GROUP, axis="horizontal", keep_original=False),
    )


@pytest.mark.parametrize(
    ("name", "make_plan", "exact_hash"),
    [
        ("paste", _paste_plan, True),
        ("array", _array_plan, True),
        ("mirror copy", _mirror_copy_plan, True),
        # Additive transforms restore the hash exactly. A mirror in place has to
        # delete and re-add its walls, and the fold cannot carry a derived room's
        # ID through that — see :func:`geometry_signature` for why that is a
        # property of the fold and not of this module.
        ("mirror in place", _mirror_in_place_plan, False),
    ],
)
def test_a_transform_is_a_single_undoable_group(
    name: str, make_plan: Any, exact_hash: bool
) -> None:
    doc = make_two_room_plan_with_openings()
    before_hash = state_hash(doc)
    before_geometry = geometry_signature(doc)
    plan = expect_plan(make_plan(doc))
    assert len(plan.ops) > 1

    group = apply_group(doc, plan.ops, plan.group_id)
    assert state_hash(group.model) != before_hash
    # Every op in the group carries the SAME group_id — that is what makes the
    # whole paste one row in the undo stack rather than twelve.
    for one in group.ops:
        assert one.group_id == plan.group_id

    stack = UndoStack()
    stack.push(
        UndoEntry(group_id=plan.group_id, ops=tuple(group.ops), inverse=tuple(group.inverse))
    )
    undone = stack.undo(group.model)
    assert undone is not None
    # ONE undo, not one per op.
    assert stack.undo_depth == 0
    assert geometry_signature(undone[0]) == before_geometry
    if exact_hash:
        assert state_hash(undone[0]) == before_hash
    redone = stack.redo(undone[0])
    assert redone is not None
    assert geometry_signature(redone[0]) == geometry_signature(group.model)


# ---------------------------------------------------------------------------
# Behaviour that only shows up once the ops are folded
# ---------------------------------------------------------------------------


def test_mirroring_in_place_cannot_use_wall_move_and_does_not() -> None:
    # South and north swap positions under a mirror about the plan's own
    # horizontal centre, so ``wall.move`` on either one first trips
    # WALL_DUPLICATE. The plan therefore deletes every selected wall before
    # re-adding any of them, at the ORIGINAL ids.
    doc = make_two_room_plan_with_openings()
    plan = expect_plan(_mirror_in_place_plan(doc))
    types = [o.type for o in plan.ops]
    assert types.count("wall.delete") == 5
    assert types.count("wall.add") == 5
    assert "wall.move" not in types
    # The last delete comes before the first add: that is what breaks the cycle.
    assert len(types) - 1 - types[::-1].index("wall.delete") < types.index("wall.add")

    after = apply_group(doc, plan.ops, plan.group_id).model
    ids = {w.id for w in after.house.walls}
    for wall_id in WALLS:
        assert wall_id in ids
    assert len(after.house.walls) == 5
    assert len(after.house.openings) == 2
    # Mirroring about the plan's own centre is an involution on the geometry, so
    # doing it twice restores the document exactly.
    twice = apply_group(after, expect_plan(_mirror_in_place_plan(after)).ops, GROUP).model
    assert state_hash(twice) == state_hash(doc)


def test_wall_move_really_would_deadlock() -> None:
    # The negative control for the claim above: prove the obvious implementation
    # is rejected, so the delete/re-add is visibly load-bearing rather than a
    # stylistic choice.
    from garh_model.fold import try_fold

    doc = make_two_room_plan()
    south = next(w for w in doc.house.walls if w.id == FIXTURE_IDS["wallSouth"])
    outcome = try_fold(
        doc,
        op(
            "wall.move",
            wallId=south.id,
            a={"x": south.a.x, "y": 4000},
            b={"x": south.b.x, "y": 4000},
        ),
    )
    assert not outcome.ok
    assert "WALL_DUPLICATE" in [i.code for i in outcome.issues]


def test_room_names_travel_onto_the_copies() -> None:
    doc = make_two_room_plan()
    rooms = doc.house.rooms
    assert len(rooms) == 2
    named = replay(
        [
            op("room.assign", roomId=rooms[0].id, type="living"),
            op("room.assign", roomId=rooms[1].id, type="kitchen"),
        ],
        doc,
    )
    plan = expect_plan(_mirror_copy_plan(named))
    assert plan.rooms_carried == 2
    after = apply_group(named, plan.ops, plan.group_id).model
    assert len(after.house.rooms) == 4
    assert sorted(r.type for r in after.house.rooms) == ["kitchen", "kitchen", "living", "living"]


def test_a_paste_leaves_the_target_storeys_named_rooms_alone() -> None:
    # The carry-over only touches rooms that come out of the fold BLANK. Give the
    # target storey named rooms first and prove the paste leaves them alone.
    doc = make_two_room_plan()
    named = replay(
        [
            op("room.assign", roomId=doc.house.rooms[0].id, type="living", name="Living"),
            op("room.assign", roomId=doc.house.rooms[1].id, type="kitchen", name="Kitchen"),
        ],
        doc,
    )
    # EXACT names, not "non-empty" and not a count of rooms called Living. Both of
    # those assertions stayed TRUE with the blank-room guard deleted — the
    # carry-over overwrote a named room with a different name and every assertion
    # here still passed. A test that cannot fail is worse than no test, and this
    # one could not.
    original = {room.id: room.name for room in named.house.rooms}
    plan = expect_plan(_paste_plan(named))
    after = apply_group(named, plan.ops, plan.group_id).model

    for room_id, name in original.items():
        room = next(r for r in after.house.rooms if r.id == room_id)
        assert room.name == name, "paste renamed %s from %r to %r" % (room_id, name, room.name)

    # ...and the carry-over still did its job on the rooms that WERE blank.
    assert len([r for r in after.house.rooms if r.name == "Living"]) == 2


def test_a_plan_is_idempotent_for_a_group_id_and_differs_for_another() -> None:
    doc = make_two_room_plan()
    req = PasteRequest(element_ids=[FIXTURE_IDS["wallSpine"]], group_id=GROUP, delta_mm=Pt(0, 9000))
    first = expect_plan(plan_paste(doc, req))
    second = expect_plan(plan_paste(doc, req))
    assert [o.to_json() for o in second.ops] == [o.to_json() for o in first.ops]
    from dataclasses import replace as dc_replace

    other = expect_plan(plan_paste(doc, dc_replace(req, group_id=fixed_id("group", "OTHER"))))
    assert [o.to_json() for o in other.ops] != [o.to_json() for o in first.ops]


def test_minted_ids_do_not_collide_with_the_document() -> None:
    doc = make_two_room_plan_with_openings()
    plan = expect_plan(_array_plan(doc))
    minted = [str(o.get("id")) for o in plan.ops if o.type == "wall.add"]
    assert len(set(minted)) == len(minted)
    existing = {w.id for w in doc.house.walls}
    assert not (set(minted) & existing)


def test_describe_selection_reads_like_the_confirm_copy() -> None:
    assert describe_selection(SelectionCounts()) == "nothing"
    assert describe_selection(SelectionCounts(walls=1)) == "1 wall"
    assert (
        describe_selection(SelectionCounts(walls=5, openings=2, stairs=1))
        == "5 walls, 2 openings and 1 stair"
    )


# ---------------------------------------------------------------------------
# THE cross-language check
# ---------------------------------------------------------------------------
#
# fixtures/model/golden-transforms.json is generated by
# fixtures/model/_tools/generate_golden_transforms.py from THIS planner.
# packages/model/src/transform.test.ts asserts the same rows against the
# TypeScript one. A failure here means the two planners disagree about what a
# paste IS — and because these transforms add no op type, neither fold would
# reject the difference. Do not paste the new value; find out which side moved.
# ---------------------------------------------------------------------------


def _run_golden(doc: ProjectDoc, request: dict[str, Any]) -> TransformPlanResult:
    kind = request["kind"]
    if kind == "paste":
        return plan_paste(
            doc,
            PasteRequest(
                element_ids=list(request["elementIds"]),
                group_id=request["groupId"],
                delta_mm=Pt(x=request["deltaMm"]["x"], y=request["deltaMm"]["y"]),
                target_storey_id=request.get("targetStoreyId"),
            ),
        )
    if kind == "array":
        return plan_array(
            doc,
            ArrayRequest(
                element_ids=list(request["elementIds"]),
                group_id=request["groupId"],
                count_x=request["countX"],
                count_y=request["countY"],
                spacing_x_mm=request["spacingXMm"],
                spacing_y_mm=request["spacingYMm"],
            ),
        )
    return plan_mirror(
        doc,
        MirrorRequest(
            element_ids=list(request["elementIds"]),
            group_id=request["groupId"],
            axis=request["axis"],
            at_mm=request.get("atMm"),
            keep_original=request.get("keepOriginal", True),
            target_storey_id=request.get("targetStoreyId"),
        ),
    )


def _counts_json(counts: SelectionCounts) -> dict[str, int]:
    return {
        "walls": counts.walls,
        "openings": counts.openings,
        "stairs": counts.stairs,
        "columns": counts.columns,
        "furniture": counts.furniture,
        "balconies": counts.balconies,
    }


def test_golden_transforms_file_carries_both_halves_of_the_contract(
    golden_transforms: list[dict[str, Any]],
) -> None:
    assert len(golden_transforms) >= 16
    # A corpus of nothing but successes would quietly stop testing the guards.
    assert len([c for c in golden_transforms if "expectedRefusal" in c]) >= 8
    assert len([c for c in golden_transforms if "expectedOps" in c]) >= 7


def test_golden_transforms_match_the_cross_language_fixture(
    golden_transforms: list[dict[str, Any]],
) -> None:
    for case in golden_transforms:
        doc = replay(
            [Op.from_json(o) for o in case["baseOps"]],
            empty_project_doc(case.get("unitsDisplay", "ft-in")),
        )
        result = _run_golden(doc, case["request"])

        if "expectedRefusal" in case:
            assert not result.ok, case["name"]
            assert result.refusal is not None
            assert result.refusal.reason == case["expectedRefusal"]["reason"], case["name"]
            assert result.refusal.message == case["expectedRefusal"]["message"], case["name"]
            continue

        assert result.ok, (case["name"], result.refusal)
        assert result.plan is not None
        plan = result.plan

        assert [o.to_json() for o in plan.ops] == case["expectedOps"], case["name"]

        expected = case["expectedPlan"]
        assert plan.kind == expected["kind"], case["name"]
        assert plan.source_storey_id == expected["sourceStoreyId"], case["name"]
        assert plan.target_storey_id == expected["targetStoreyId"], case["name"]
        assert plan.instances == expected["instances"], case["name"]
        assert _counts_json(plan.selected) == expected["selected"], case["name"]
        assert _counts_json(plan.created) == expected["created"], case["name"]
        assert plan.derived_skipped == expected["derivedSkipped"], case["name"]
        assert plan.rooms_carried == expected["roomsCarried"], case["name"]
        assert plan.label == expected["label"], case["name"]

        after = apply_group(doc, plan.ops, plan.group_id).model
        assert state_hash(after) == case["expectedStateHash"], case["name"]
        assert doc_hash(after) == case["expectedStateHash"], case["name"]


def test_every_golden_hash_is_64_lowercase_hex(
    golden_transforms: list[dict[str, Any]],
) -> None:
    import re

    for case in golden_transforms:
        if "expectedStateHash" not in case:
            continue
        assert re.fullmatch(r"[0-9a-f]{64}", case["expectedStateHash"]), case["name"]


def test_every_golden_transform_undoes_back_to_its_starting_geometry(
    golden_transforms: list[dict[str, Any]],
) -> None:
    for case in golden_transforms:
        if "expectedOps" not in case:
            continue
        doc = replay(
            [Op.from_json(o) for o in case["baseOps"]],
            empty_project_doc(case.get("unitsDisplay", "ft-in")),
        )
        before_hash = state_hash(doc)
        before_geometry = geometry_signature(doc)
        result = _run_golden(doc, case["request"])
        assert result.ok and result.plan is not None, case["name"]
        group = apply_group(doc, result.plan.ops, result.plan.group_id)
        stack = UndoStack()
        stack.push(
            UndoEntry(
                group_id=result.plan.group_id,
                ops=tuple(group.ops),
                inverse=tuple(group.inverse),
            )
        )
        undone = stack.undo(group.model)
        assert undone is not None, case["name"]
        assert geometry_signature(undone[0]) == before_geometry, case["name"]
        # Additive plans restore the hash exactly; the in-place mirror hits the
        # fold's pre-existing derived-room-id gap (see :func:`geometry_signature`).
        if result.plan.instances > 0:
            assert state_hash(undone[0]) == before_hash, case["name"]


def test_the_corpus_rests_on_the_door_it_says_it_rests_on(
    golden_transforms: list[dict[str, Any]],
) -> None:
    # Guards the corpus against a silent change of the shared fixture: if the
    # demo door stops being ``in-left`` at 1500, the mirror rows stop proving
    # anything about the hand and would still be green.
    row = next(c for c in golden_transforms if c["name"] == "mirror-copy-vertical-with-doors")
    door = next(
        o for o in row["baseOps"] if o["type"] == "opening.add" and o["payload"]["kind"] == "door"
    )
    assert door["payload"]["swing"] == "in-left"
    assert door["payload"]["offsetMm"] == 1500
    mirrored = next(
        o
        for o in row["expectedOps"]
        if o["type"] == "opening.add" and o["payload"]["kind"] == "door"
    )
    assert mirrored["payload"]["swing"] == "out-left"
    assert mirrored["payload"]["offsetMm"] == 1500


# ===========================================================================
# Mirroring a symmetric selection onto itself
# ===========================================================================
def test_mirroring_a_symmetric_selection_about_its_own_centre_is_refused() -> None:
    """The default axis runs through the selection's centre, which is the trap.

    A reflection is never the identity map, so ``is_identity_map`` cannot see this
    — but a selection symmetric about the axis is carried onto its own point set,
    and with ``keep_original`` the copy lands exactly on the original. The fold
    rejects a duplicate wall, and nothing at all forbids two columns at one point,
    so before this guard the structural count and the schedule silently doubled.
    """
    doc = make_two_room_plan_with_openings()
    ids = [w.id for w in doc.house.walls]

    for axis in ("vertical", "horizontal"):
        result = plan_mirror(doc, MirrorRequest(element_ids=ids, axis=axis, group_id="g"))
        assert not result.ok, "%s mirror about the selection centre must refuse" % axis
        assert result.refusal is not None
        assert result.refusal.reason == "zero-offset"


def test_an_off_centre_mirror_of_the_same_selection_is_still_allowed() -> None:
    """The negative control for the guard above: it must refuse the stacking case
    and ONLY the stacking case. A guard that refused every mirror would pass the
    test above while making the feature useless."""
    doc = make_two_room_plan_with_openings()
    ids = [w.id for w in doc.house.walls]

    result = plan_mirror(
        doc, MirrorRequest(element_ids=ids, axis="vertical", at_mm=99_000, group_id="g")
    )
    assert result.ok, result.refusal
    assert result.plan is not None
    assert result.plan.ops, "an off-centre mirror must still produce ops"


def test_mirroring_in_place_is_unaffected_by_the_stacking_guard() -> None:
    """`keep_original=False` MOVES the originals — there is no copy to stack, so a
    symmetric selection must still flip. Guarding it would break the Vastu 'flip
    the plan' gesture the docstring calls out."""
    doc = make_two_room_plan_with_openings()
    ids = [w.id for w in doc.house.walls]

    result = plan_mirror(
        doc,
        MirrorRequest(element_ids=ids, axis="vertical", keep_original=False, group_id="g"),
    )
    assert result.ok, result.refusal


def test_the_carry_over_refuses_to_overwrite_a_room_that_already_has_a_name() -> None:
    """The blank-room guard in ``_room_metadata_ops``, tested where it can fail.

    The guard was previously "covered" by a paste test that stayed GREEN with the
    guard deleted, which is bug class 3 — and no amount of strengthening that test
    fixes it, because the collision the guard exists for cannot be built through
    the public API: a paste whose copy lands on an existing room would need walls
    at the same coordinates, and the fold rejects those as WALL_DUPLICATE first.

    So the guard is DEFENSIVE, and it is tested at the level where it is reachable
    — the function's own contract. Hand it an ``after`` in which the target storey
    already holds a named room whose signature matches a mapped source room, and
    it must emit no ``room.assign`` for that room. Delete the guard and this goes
    red, which is the whole point.
    """
    ground, first = fixed_id("storey", "GF2"), fixed_id("storey", "FF2")
    square = (Pt(0, 0), Pt(4000, 0), Pt(4000, 4000), Pt(0, 4000))

    def room(room_id: str, storey: str, name: str, room_type: str) -> Room:
        return Room(
            id=room_id,
            storey_id=storey,
            type=room_type,
            name=name,
            polygon=square,
            area_mm2=4000 * 4000,
            tags=(),
            locked=False,
            target_area_mm2=None,
            must_face=None,
        )

    source = room(fixed_id("room", "SRC2"), ground, "Living", "living")
    # Same polygon on the target storey, and ALREADY NAMED by the architect.
    target = room(fixed_id("room", "TGT2"), first, "Pooja", "pooja")

    before = HouseModel.from_json({**make_two_room_plan().house.to_json(), "rooms": []})
    before = replace(before, rooms=(source,))
    after = replace(before, rooms=(source, target))

    ops, carried = _room_metadata_ops(before, after, ground, first, [IDENTITY_MAP])
    assert carried == 0, "a named room must not be counted as carried"
    assert ops == [], "the carry-over must not rename %r" % target.name


def test_the_array_cap_bounds_the_WORK_not_the_instance_count() -> None:
    """MAX_ARRAY_INSTANCES alone let a frozen tab straight through.

    ``_build_plan`` folds every emitted op serially on a fork and each
    ``wall.add`` re-runs room detection over a growing house, so the cost tracks
    ELEMENTS, not instances. A 20x20 array of a four-wall selection is 1,600 folds
    and was comfortably inside the 400-instance cap — measured at 59.6 s for 396
    ops on this very fixture.
    """
    doc = make_two_room_plan_with_openings()
    ids = [w.id for w in doc.house.walls]

    def array(count: int) -> TransformPlanResult:
        return plan_array(
            doc,
            ArrayRequest(
                element_ids=ids,
                group_id=GROUP,
                count_x=count,
                count_y=count,
                spacing_x_mm=12_000,
                spacing_y_mm=12_000,
            ),
        )

    assert array(3).ok, "a 3x3 of this selection is 56 elements and must be allowed"

    big = array(20)
    assert not big.ok, "a 20x20 is ~2,800 elements and must be refused"
    assert big.refusal is not None
    assert big.refusal.reason == "count-out-of-range"
    assert "elements" in big.refusal.message, "the refusal must name what it bounded"
    # And the point of the whole fix: the instance cap alone would NOT have caught
    # it. 20*20 = 400 is exactly at MAX_ARRAY_INSTANCES, not over it.
    assert MAX_ARRAY_INSTANCES >= 20 * 20
