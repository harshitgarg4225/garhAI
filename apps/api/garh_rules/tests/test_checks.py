from __future__ import annotations

"""One test class per check type, exercised at the boundary and one unit either side.

The fixture corpus already proves the packs' own rules behave; this module proves
the *functions* behave, including the cases no seed pack reaches:
``setback_min.measure='to-projection'``, ``far_max.premium``, ``parking_min`` on a
built-up-area basis, ``floors_max.counts``, multi-instance collapse, and the
governing-instance choice when several rooms fail at once.

Every assertion is on the boundary: exactly at the limit passes, one unit past it
fails. That is where a rounding mistake lives.
"""

from typing import Any, Dict, List

import pytest

from garh_rules import evaluate
from garh_rules.checks import result_unit_of, run_check, scope_of
from garh_rules.packs import Check, PackSet
from garh_rules.results import FAIL, NOT_APPLICABLE, PASS
from garh_rules.scope import CheckEnv, instances_for

from .conftest import RULEPACK_DIR, make_context, make_room


def run_one(check_json: Dict[str, Any], context: Any, pack_set: PackSet) -> List[Any]:
    """Evaluate a hand-written check against every instance of its scope."""
    check = Check.from_json(check_json)
    env = CheckEnv(context=context, vocabulary=pack_set.vocabulary)
    return [
        (instance, run_check(check, instance, env))
        for instance in instances_for(check, scope_of(check), env)
    ]


# ---------------------------------------------------------------------------
# setback_min
# ---------------------------------------------------------------------------


class TestSetbackMin:
    def test_exactly_at_the_limit_passes(self, nbc: PackSet) -> None:
        context = make_context(
            edges=[
                {"index": 0, "role": "front", "roadWidthMm": 9000, "setbackProvidedMm": 3000},
                {"index": 1, "role": "side-a", "roadWidthMm": None, "setbackProvidedMm": 1200},
                {"index": 2, "role": "rear", "roadWidthMm": None, "setbackProvidedMm": 1500},
                {"index": 3, "role": "side-b", "roadWidthMm": None, "setbackProvidedMm": 1200},
            ]
        )
        (_, outcome), = run_one(
            {"type": "setback_min", "edge": "front", "valueMm": 3000}, context, nbc
        )
        assert outcome.satisfied
        assert outcome.actual == 3000
        assert outcome.order_key == 0

    def test_one_millimetre_short_fails(self, nbc: PackSet) -> None:
        context = make_context(
            edges=[
                {"index": 0, "role": "front", "roadWidthMm": 9000, "setbackProvidedMm": 2999},
                {"index": 1, "role": "side-a", "roadWidthMm": None, "setbackProvidedMm": 1200},
                {"index": 2, "role": "rear", "roadWidthMm": None, "setbackProvidedMm": 1500},
                {"index": 3, "role": "side-b", "roadWidthMm": None, "setbackProvidedMm": 1200},
            ]
        )
        (_, outcome), = run_one(
            {"type": "setback_min", "edge": "front", "valueMm": 3000}, context, nbc
        )
        assert not outcome.satisfied
        assert outcome.order_key == -1

    def test_sides_selector_covers_both_side_edges(self, nbc: PackSet) -> None:
        context = make_context()
        results = run_one({"type": "setback_min", "edge": "sides", "valueMm": 1500}, context, nbc)
        assert [i.element_id for i, _ in results] == ["plot.edge.side-a", "plot.edge.side-b"]

    def test_all_selector_covers_every_edge(self, nbc: PackSet) -> None:
        context = make_context()
        results = run_one({"type": "setback_min", "edge": "all", "valueMm": 1000}, context, nbc)
        assert len(results) == 4

    def test_missing_edge_role_yields_no_instances(self, nbc: PackSet) -> None:
        """A plot with no ``front`` edge is not credited with a front setback."""
        context = make_context(
            edges=[
                {"index": 0, "role": "other", "roadWidthMm": None, "setbackProvidedMm": 0},
                {"index": 1, "role": "side-a", "roadWidthMm": None, "setbackProvidedMm": 1200},
                {"index": 2, "role": "rear", "roadWidthMm": None, "setbackProvidedMm": 1500},
            ]
        )
        assert run_one({"type": "setback_min", "edge": "front", "valueMm": 3000}, context, nbc) == []

    def test_to_projection_measures_past_the_deepest_projection(self, nbc: PackSet) -> None:
        """Not covered by any seed pack, so it is covered here."""
        context = make_context(
            projections=[
                {
                    "id": "proj_balcony",
                    "storeyId": "storey_1",
                    "element": "balcony",
                    "edgeRole": "front",
                    "projectionMm": 1000,
                    "intoSetback": True,
                },
                {
                    "id": "proj_chajja",
                    "storeyId": "storey_1",
                    "element": "chajja",
                    "edgeRole": "front",
                    "projectionMm": 600,
                    "intoSetback": True,
                },
            ]
        )
        check = {
            "type": "setback_min",
            "edge": "front",
            "valueMm": 2000,
            "measure": "to-projection",
        }
        (_, outcome), = run_one(check, context, nbc)
        assert outcome.actual == 3000 - 1000  # the deepest projection, not their sum
        assert outcome.satisfied
        assert outcome.note is not None

    def test_duplicate_edge_roles_get_distinct_element_ids(self, nbc: PackSet) -> None:
        context = make_context(
            edges=[
                {"index": 0, "role": "other", "roadWidthMm": None, "setbackProvidedMm": 900},
                {"index": 1, "role": "other", "roadWidthMm": None, "setbackProvidedMm": 800},
                {"index": 2, "role": "front", "roadWidthMm": 9000, "setbackProvidedMm": 3000},
            ]
        )
        results = run_one({"type": "setback_min", "edge": "all", "valueMm": 1000}, context, nbc)
        ids = [i.element_id for i, _ in results]
        assert len(ids) == len(set(ids)), ids


# ---------------------------------------------------------------------------
# Project-scope ratios and caps
# ---------------------------------------------------------------------------


class TestFarMax:
    def test_limit_floors_the_product(self, nbc: PackSet) -> None:
        # 1.75 x 1_000_001 = 1_750_001.75 -> floor 1_750_001
        context = make_context(area_mm2=1_000_001, model={"farCountableAreaMm2": 1_750_001})
        (_, outcome), = run_one(
            {"type": "far_max", "ratio": {"num": 175, "den": 100}}, context, nbc
        )
        assert outcome.limit == 1_750_001
        assert outcome.satisfied

    def test_one_square_millimetre_over_fails(self, nbc: PackSet) -> None:
        context = make_context(area_mm2=1_000_001, model={"farCountableAreaMm2": 1_750_002})
        (_, outcome), = run_one(
            {"type": "far_max", "ratio": {"num": 175, "den": 100}}, context, nbc
        )
        assert not outcome.satisfied

    def test_premium_is_a_note_not_a_larger_limit(self, nbc: PackSet) -> None:
        """Buying premium FAR is the architect's call; the engine must not spend it."""
        context = make_context(area_mm2=100_000_000, model={"farCountableAreaMm2": 200_000_001})
        check = {
            "type": "far_max",
            "ratio": {"num": 2, "den": 1},
            "premium": {"ratio": {"num": 3, "den": 1}, "note": "Available on a 24 m road."},
        }
        (_, outcome), = run_one(check, context, nbc)
        assert outcome.limit == 200_000_000
        assert not outcome.satisfied
        assert outcome.note is not None and "Premium" in outcome.note


class TestCoverageMax:
    def test_boundary(self, nbc: PackSet) -> None:
        context = make_context(area_mm2=300_000_000, model={"footprintAreaMm2": 180_000_000})
        (_, outcome), = run_one(
            {"type": "coverage_max", "ratio": {"num": 60, "den": 100}}, context, nbc
        )
        assert outcome.limit == 180_000_000
        assert outcome.satisfied


class TestHeightMax:
    def test_excluded_components_are_subtracted(self, nbc: PackSet) -> None:
        context = make_context(
            model={
                "buildingHeightMm": 15_000,
                "heightComponentsMm": {"parapet": 1000, "mumty": 2400, "oht": 1200},
            }
        )
        # 15 m to the top of the OHT, less mumty (2400) and OHT (1200) = 11.4 m counted,
        # which is exactly the cap — so it passes only because the exclusions applied.
        check = {"type": "height_max", "valueMm": 11_400, "excludes": ["mumty", "oht"]}
        (_, outcome), = run_one(check, context, nbc)
        assert outcome.actual == 15_000 - 3600
        assert outcome.satisfied
        # ... and without them the same building is over.
        (_, unexcluded), = run_one({"type": "height_max", "valueMm": 11_400}, context, nbc)
        assert unexcluded.actual == 15_000
        assert not unexcluded.satisfied

    def test_a_component_the_building_lacks_subtracts_nothing(self, nbc: PackSet) -> None:
        context = make_context(model={"buildingHeightMm": 12_000, "heightComponentsMm": {}})
        check = {"type": "height_max", "valueMm": 11_999, "excludes": ["mumty", "oht", "parapet"]}
        (_, outcome), = run_one(check, context, nbc)
        assert outcome.actual == 12_000
        assert not outcome.satisfied


class TestFloorsMax:
    def test_plain_storey_count(self, nbc: PackSet) -> None:
        context = make_context(model={"storeyCount": 3})
        (_, outcome), = run_one({"type": "floors_max", "value": 3}, context, nbc)
        assert outcome.satisfied and outcome.actual == 3

    def test_stilt_is_free_unless_counted(self, nbc: PackSet) -> None:
        context = make_context(model={"storeyCount": 3, "hasStilt": True})
        (_, free), = run_one({"type": "floors_max", "value": 3, "counts": []}, context, nbc)
        assert free.satisfied
        (_, counted), = run_one(
            {"type": "floors_max", "value": 3, "counts": ["stilt"]}, context, nbc
        )
        assert not counted.satisfied and counted.actual == 4

    def test_basement_counts_independently(self, nbc: PackSet) -> None:
        context = make_context(model={"storeyCount": 2, "hasStilt": True, "hasBasement": True})
        (_, outcome), = run_one(
            {"type": "floors_max", "value": 3, "counts": ["stilt", "basement"]}, context, nbc
        )
        assert outcome.actual == 4


class TestParkingMin:
    def test_per_dwelling_with_a_floor(self, nbc: PackSet) -> None:
        context = make_context(
            profile={"dwellingUnits": 1, "parkingSpacesProvided": 1},
        )
        check = {
            "type": "parking_min",
            "basis": "dwelling",
            "rate": {"num": 1, "den": 2},
            "minSpaces": 1,
        }
        (_, outcome), = run_one(check, context, nbc)
        assert outcome.limit == 1  # ceil(0.5) = 1, and minSpaces agrees
        assert outcome.satisfied

    def test_built_up_area_basis_ceils(self, nbc: PackSet) -> None:
        """2 ECS per 100 m2 on 250 m2 of built-up = 5 exactly; one mm2 more needs 6."""
        rate = {"num": 2, "den": 100_000_000}
        context = make_context(
            model={"builtUpAreaMm2": 250_000_000}, profile={"parkingSpacesProvided": 5}
        )
        (_, exact), = run_one(
            {"type": "parking_min", "basis": "built-up-area", "rate": rate}, context, nbc
        )
        assert exact.limit == 5 and exact.satisfied
        context = make_context(
            model={"builtUpAreaMm2": 250_000_001}, profile={"parkingSpacesProvided": 5}
        )
        (_, over), = run_one(
            {"type": "parking_min", "basis": "built-up-area", "rate": rate}, context, nbc
        )
        assert over.limit == 6 and not over.satisfied


# ---------------------------------------------------------------------------
# Room scope
# ---------------------------------------------------------------------------


class TestRoomChecks:
    def test_area_boundary(self, nbc: PackSet) -> None:
        context = make_context(
            rooms=[make_room("r1", "bedroom", width=2500, depth=3800)]  # 9_500_000
        )
        (_, outcome), = run_one({"type": "room_area_min", "valueMm2": 9_500_000}, context, nbc)
        assert outcome.satisfied and outcome.actual == 9_500_000

    def test_area_one_square_millimetre_short(self, nbc: PackSet) -> None:
        context = make_context(rooms=[make_room("r1", "bedroom", width=2500, depth=3800)])
        (_, outcome), = run_one({"type": "room_area_min", "valueMm2": 9_500_001}, context, nbc)
        assert not outcome.satisfied

    def test_least_width_is_the_shorter_bbox_side(self, nbc: PackSet) -> None:
        context = make_context(rooms=[make_room("r1", "bedroom", width=2400, depth=9000)])
        (_, outcome), = run_one({"type": "room_width_min", "valueMm": 2400}, context, nbc)
        assert outcome.actual == 2400 and outcome.satisfied

    def test_ceiling_height_is_per_room_not_per_storey(self, nbc: PackSet) -> None:
        context = make_context(
            rooms=[
                make_room("r1", "bedroom", ceiling_mm=2900),
                make_room("r2", "bath", x=4000, ceiling_mm=2199),
            ]
        )
        outcomes = run_one({"type": "ceiling_height_min", "valueMm": 2200}, context, nbc)
        by_id = {i.element_id: o for i, o in outcomes}
        assert by_id["r1"].satisfied
        assert not by_id["r2"].satisfied

    def test_ventilation_takes_the_larger_of_ratio_and_floor(self, nbc: PackSet) -> None:
        # 1.8 m2 bath: one tenth is 180 000 mm2, but the absolute floor is 300 000
        context = make_context(
            rooms=[make_room("b1", "bath", width=1200, depth=1500, ventilation_mm2=300_000)]
        )
        check = {
            "type": "ventilation_ratio_min",
            "ratio": {"num": 1, "den": 10},
            "minAreaMm2": 300_000,
        }
        (_, outcome), = run_one(check, context, nbc)
        assert outcome.limit == 300_000 and outcome.satisfied

    def test_ventilation_ratio_ceils(self, nbc: PackSet) -> None:
        # area 9_500_001 -> one tenth is 950_000.1 -> ceil 950_001
        context = make_context(
            rooms=[
                make_room("r1", "bedroom", width=3, depth=3_166_667, ventilation_mm2=950_000)
            ]
        )
        (_, outcome), = run_one(
            {"type": "ventilation_ratio_min", "ratio": {"num": 1, "den": 10}}, context, nbc
        )
        assert outcome.limit == 950_001
        assert not outcome.satisfied

    def test_ratio_only_check_has_no_absolute_floor(self, nbc: PackSet) -> None:
        context = make_context(
            rooms=[make_room("r1", "bedroom", width=1000, depth=1000, ventilation_mm2=100_000)]
        )
        (_, outcome), = run_one(
            {"type": "ventilation_ratio_min", "ratio": {"num": 1, "den": 10}}, context, nbc
        )
        assert outcome.limit == 100_000 and outcome.satisfied


# ---------------------------------------------------------------------------
# Stair / opening / projection scope
# ---------------------------------------------------------------------------


STAIR = {
    "id": "stair_main",
    "storeyId": "storey_g",
    "kind": "dogleg",
    "riserMm": 165,
    "treadMm": 280,
    "widthMm": 1050,
    "headroomMm": 2200,
    "risersCount": 18,
    "centroidMm": [2000, 2000],
}


class TestStairChecks:
    @pytest.mark.parametrize(
        "check_type,field,limit,pass_value,fail_value",
        [
            ("stair_riser_max", "riserMm", 190, 190, 191),
            ("stair_tread_min", "treadMm", 250, 250, 249),
            ("stair_width_min", "widthMm", 900, 900, 899),
            ("headroom_min", "headroomMm", 2100, 2100, 2099),
        ],
    )
    def test_boundaries(
        self,
        nbc: PackSet,
        check_type: str,
        field: str,
        limit: int,
        pass_value: int,
        fail_value: int,
    ) -> None:
        for value, expected in ((pass_value, True), (fail_value, False)):
            stair = dict(STAIR)
            stair[field] = value
            context = make_context(stairs=[stair])
            (_, outcome), = run_one({"type": check_type, "valueMm": limit}, context, nbc)
            assert outcome.satisfied is expected, "%s=%d" % (field, value)
            assert outcome.actual == value


class TestOpeningWidthMin:
    def test_boundary(self, nbc: PackSet) -> None:
        opening = {
            "id": "d1",
            "storeyId": "storey_g",
            "kind": "door",
            "role": "main-entrance",
            "widthMm": 900,
            "heightMm": 2100,
            "outwardNormalDeg": 180,
        }
        context = make_context(openings=[opening])
        (_, outcome), = run_one({"type": "opening_width_min", "valueMm": 900}, context, nbc)
        assert outcome.satisfied
        opening = dict(opening, widthMm=899)
        context = make_context(openings=[opening])
        (_, outcome), = run_one({"type": "opening_width_min", "valueMm": 900}, context, nbc)
        assert not outcome.satisfied


class TestProjectionMax:
    def _projection(self, **kwargs: Any) -> Dict[str, Any]:
        base = {
            "id": "p1",
            "storeyId": "storey_1",
            "element": "balcony",
            "edgeRole": "front",
            "projectionMm": 1000,
            "intoSetback": True,
        }
        base.update(kwargs)
        return base

    def test_only_the_named_element_is_measured(self, nbc: PackSet) -> None:
        context = make_context(
            projections=[
                self._projection(id="p_balcony", element="balcony", projectionMm=1001),
                self._projection(id="p_chajja", element="chajja", projectionMm=5000),
            ]
        )
        results = run_one(
            {"type": "projection_max", "element": "balcony", "valueMm": 1000}, context, nbc
        )
        assert [i.element_id for i, _ in results] == ["p_balcony"]
        assert not results[0][1].satisfied

    def test_into_setback_only_skips_projections_outside_a_setback(self, nbc: PackSet) -> None:
        context = make_context(
            projections=[self._projection(projectionMm=5000, intoSetback=False)]
        )
        check = {
            "type": "projection_max",
            "element": "balcony",
            "valueMm": 1000,
            "intoSetbackOnly": True,
        }
        assert run_one(check, context, nbc) == []
        # ... and measures it when it does encroach
        context = make_context(projections=[self._projection(projectionMm=5000, intoSetback=True)])
        (_, outcome), = run_one(check, context, nbc)
        assert not outcome.satisfied


# ---------------------------------------------------------------------------
# Collapse behaviour — the part that is easy to get wrong
# ---------------------------------------------------------------------------


class TestInstanceCollapse:
    def _habitable_context(self, *areas: int) -> Any:
        rooms = []
        x = 0
        for index, area in enumerate(areas):
            width = 2500
            depth = area // width
            rooms.append(
                make_room(
                    "room_%d" % index,
                    "bedroom",
                    x=x,
                    width=width,
                    depth=depth,
                    ventilation_mm2=area,  # generous, so only the area rule can fire
                )
            )
            x += width + 200
        return make_context(packs=("nbc-core",), rooms=rooms)

    def test_worst_status_wins_and_every_offender_is_listed(self) -> None:
        context = self._habitable_context(9_500_000, 9_000_000, 8_000_000)
        report = evaluate(context, root=RULEPACK_DIR)
        row = report.rule("nbc.room.habitable.area.min")
        assert row is not None
        assert row.status == FAIL
        assert set(row.elements) == {"room_1", "room_2"}

    def test_the_governing_instance_is_the_worst_violation(self) -> None:
        context = self._habitable_context(9_500_000, 9_000_000, 8_000_000)
        row = evaluate(context, root=RULEPACK_DIR).rule("nbc.room.habitable.area.min")
        assert row is not None
        assert row.actual == 8_000_000  # the worst, not the first

    def test_on_a_clean_run_the_tightest_margin_governs(self) -> None:
        context = self._habitable_context(20_000_000, 9_500_000, 12_000_000)
        row = evaluate(context, root=RULEPACK_DIR).rule("nbc.room.habitable.area.min")
        assert row is not None
        assert row.status == PASS
        assert row.actual == 9_500_000
        assert row.elements == ()

    def test_slack_not_raw_value_orders_a_per_room_limit(self) -> None:
        """``ventilation_ratio_min``'s limit differs per room, so raw values are not
        comparable. The big room with the small *margin* must govern."""
        rooms = [
            # 10 m2 room needs 1 000 000; has 1 000 001 -> margin 1
            make_room("small", "bedroom", width=2500, depth=4000, ventilation_mm2=1_000_001),
            # 40 m2 room needs 4 000 000; has 5 000 000 -> margin 1 000 000
            make_room("big", "bedroom", x=4000, width=5000, depth=8000, ventilation_mm2=5_000_000),
        ]
        row = evaluate(make_context(rooms=rooms), root=RULEPACK_DIR).rule(
            "nbc.ventilation.habitable.min"
        )
        assert row is not None
        assert row.status == PASS
        assert row.actual == 1_000_001 and row.limit == 1_000_000

    def test_no_instances_is_not_applicable_not_pass(self) -> None:
        row = evaluate(make_context(rooms=[]), root=RULEPACK_DIR).rule(
            "nbc.room.habitable.area.min"
        )
        assert row is not None
        assert row.status == NOT_APPLICABLE
        assert row.not_applicable_reason == "no-instances"

    def test_when_gate_records_the_field_that_excluded_the_rule(self) -> None:
        """A ``not_applicable`` row has to be explainable, or nobody trusts the panel."""
        context = make_context(
            packs=("nbc-core", "blr"),
            profile={"cityPack": "blr"},
            edges=[
                {"index": 0, "role": "front", "roadWidthMm": None, "setbackProvidedMm": 3000},
                {"index": 1, "role": "side-a", "roadWidthMm": None, "setbackProvidedMm": 1500},
                {"index": 2, "role": "rear", "roadWidthMm": None, "setbackProvidedMm": 2000},
                {"index": 3, "role": "side-b", "roadWidthMm": None, "setbackProvidedMm": 1500},
            ],
        )
        row = evaluate(context, root=RULEPACK_DIR).rule("blr.far.road.9-18m")
        assert row is not None
        assert row.status == NOT_APPLICABLE
        assert row.not_applicable_reason == "when"
        assert row.not_applicable_field == "roadWidthMm"

    def test_instances_carry_a_sentence_each(self) -> None:
        context = self._habitable_context(8_000_000, 9_000_000)
        row = evaluate(context, root=RULEPACK_DIR).rule("nbc.room.habitable.area.min")
        assert row is not None
        assert len(row.instances) == 2
        assert all(instance.message for instance in row.instances)
        assert "8.00 m2" in row.instances[0].message


# ---------------------------------------------------------------------------
# Result units and scopes are declared for every type
# ---------------------------------------------------------------------------


def test_every_check_type_declares_a_scope_and_a_unit() -> None:
    from garh_rules import CHECK_TYPES

    for check_type in sorted(CHECK_TYPES):
        if check_type == "custom":
            check = Check.from_json(
                {
                    "type": "custom",
                    "fn": "rwh_required",
                    "scope": "project",
                    "args": {"flag": "rwhDeclared"},
                }
            )
        elif check_type == "zone_check":
            check = Check.from_json(
                {
                    "type": "zone_check",
                    "mode": "zone",
                    "target": {"kind": "stair"},
                    "allow": ["S"],
                }
            )
        else:
            check = Check.from_json({"type": check_type})
        assert scope_of(check)
        assert result_unit_of(check)
