"""The evaluator's contract: one row per rule, deterministic, never a silent pass.

§6 fixes the signature and the row shape; §13 adds logged overrides; §15 adds the
chip sentence. This module covers the parts no single check type owns:

* the report surface (``counts``, ``worst_status``, ``blocking_failures``,
  ``to_json``) and its determinism;
* ``not_applicable`` as a first-class status **with a reason** — the difference
  between "does not apply to your plot" and "we could not tell";
* an override that is honoured for the solver gate and still reported honestly;
* the message rendering an architect actually reads;
* both public entry points (``evaluate`` and §6's literal
  ``evaluate_parts(model, plot, profile, packs)``) agreeing;
* the context boundary refusing floats and dangling references, because a
  rounding-tolerant parser here would put a drifting number on a municipal drawing.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from garh_rules import evaluate, evaluate_parts, load_pack_set
from garh_rules.context import (
    MODEL_FIELDS_NOT_IN_MODEL_CORE,
    EvaluationContext,
    normalise_room_type,
)
from garh_rules.errors import ContextError
from garh_rules.results import FAIL, NOT_APPLICABLE, PASS, WARN, worst_status

from .conftest import RULEPACK_DIR, make_context, make_room

SHORT_BEDROOM = {"width": 2500, "depth": 3600}  # 9.0 m2 — 0.5 m2 short of NBC's minimum


def report_for(**kwargs: Any) -> Any:
    return evaluate(make_context(**kwargs), root=RULEPACK_DIR)


# ---------------------------------------------------------------------------
# Shape and determinism
# ---------------------------------------------------------------------------


class TestReportShape:
    def test_one_row_per_loaded_rule_in_pack_order(self) -> None:
        pack_set = load_pack_set(["blr"], root=RULEPACK_DIR)
        report = report_for(packs=("blr",), profile={"cityPack": "blr"})
        assert [r.rule_id for r in report.results] == [r.id for r in pack_set.rules]

    def test_counts_add_up_to_the_row_count(self) -> None:
        report = report_for(packs=("blr",), rooms=[make_room("r1", "bedroom", **SHORT_BEDROOM)])
        statuses = (PASS, WARN, FAIL, NOT_APPLICABLE)
        assert sum(report.counts[s] for s in statuses) == len(report.results)

    def test_worst_status_and_presentability_track_the_failures(self) -> None:
        clean = report_for(packs=("nbc-core",), rooms=[make_room("r1", "bedroom")])
        assert clean.worst_status() in (PASS, WARN)
        assert clean.is_presentable()
        broken = report_for(
            packs=("nbc-core",), rooms=[make_room("r1", "bedroom", **SHORT_BEDROOM)]
        )
        assert broken.worst_status() == FAIL
        assert not broken.is_presentable()
        assert [r.rule_id for r in broken.blocking_failures()] == [
            r.rule_id for r in broken.failures()
        ]

    def test_worst_status_helper_only_returns_not_applicable_when_alone(self) -> None:
        assert worst_status([]) == NOT_APPLICABLE
        assert worst_status([NOT_APPLICABLE, PASS]) == PASS
        assert worst_status([PASS, WARN]) == WARN
        assert worst_status([WARN, FAIL]) == FAIL

    def test_two_runs_produce_byte_identical_json(self) -> None:
        context = make_context(packs=("blr", "vastu"), vastu_mode="advisory")
        first = json.dumps(evaluate(context, root=RULEPACK_DIR).to_json(), sort_keys=True)
        second = json.dumps(evaluate(context, root=RULEPACK_DIR).to_json(), sort_keys=True)
        assert first == second

    def test_json_carries_the_playbook_row_fields(self) -> None:
        report = report_for(
            packs=("nbc-core",), rooms=[make_room("r1", "bedroom", **SHORT_BEDROOM)]
        )
        row = next(
            r for r in report.to_json()["results"] if r["ruleId"] == "nbc.room.habitable.area.min"
        )
        for key in (
            "ruleId",
            "status",
            "actual",
            "limit",
            "cite",
            "fixHint",
            "elements",
            "confidence",
        ):
            assert key in row, key
        assert row["status"] == FAIL
        assert row["elements"] == ["r1"]
        assert row["cite"].startswith("NBC 2016")
        assert row["instances"], "a violated row carries its per-element breakdown"

    def test_a_passing_row_omits_its_instances_unless_asked(self) -> None:
        report = report_for(packs=("nbc-core",), rooms=[make_room("r1", "bedroom")])
        passing = next(
            r for r in report.to_json()["results"] if r["ruleId"] == "nbc.room.habitable.area.min"
        )
        assert "instances" not in passing
        full = next(
            r
            for r in report.to_json(full=True)["results"]
            if r["ruleId"] == "nbc.room.habitable.area.min"
        )
        assert full["instances"]

    def test_not_applicable_rows_can_be_omitted_for_the_chip_strip(self) -> None:
        report = report_for(packs=("blr",))
        with_na = report.to_json()["results"]
        without = report.to_json(include_not_applicable=False)["results"]
        assert len(without) < len(with_na)
        assert all(row["status"] != NOT_APPLICABLE for row in without)

    def test_pack_versions_and_disclaimers_ride_along(self) -> None:
        report = report_for(packs=("blr",))
        assert set(report.pack_versions) == {"nbc-core", "blr"}
        assert len(report.to_json()["disclaimers"]) == 2

    def test_lookup_by_rule_id(self) -> None:
        report = report_for(packs=("nbc-core",))
        assert report.rule("nbc.stair.riser.max") is not None
        assert report.rule("nbc.does.not.exist") is None


# ---------------------------------------------------------------------------
# not_applicable is a status, not an absence
# ---------------------------------------------------------------------------


class TestNotApplicable:
    def test_a_gate_that_excludes_the_project_names_the_field(self) -> None:
        report = report_for(
            packs=("blr",),
            profile={"cityPack": "blr", "zoneCategory": "commercial"},
        )
        row = report.rule("blr.coverage.plot.241-500")
        assert row is not None
        assert row.status == NOT_APPLICABLE
        assert row.not_applicable_field == "zoneCategory"
        assert "does not apply" in row.message

    def test_an_empty_scope_says_there_is_nothing_to_measure(self) -> None:
        row = report_for(packs=("nbc-core",), stairs=[]).rule("nbc.stair.riser.max")
        assert row is not None
        assert row.status == NOT_APPLICABLE
        assert row.not_applicable_reason == "no-instances"

    def test_a_scope_gate_that_matches_nothing_is_when_not_no_instances(self) -> None:
        """There *are* rooms, but none is habitable — the rule was gated out, and the
        reason must say which field did it."""
        row = report_for(
            packs=("nbc-core",), rooms=[make_room("b1", "bath", width=1200, depth=1500)]
        ).rule("nbc.room.habitable.area.min")
        assert row is not None
        assert row.status == NOT_APPLICABLE
        assert row.not_applicable_reason == "when"
        assert row.not_applicable_field == "roomIsHabitable"

    def test_a_null_context_field_never_satisfies_a_predicate(self) -> None:
        """No road set: the FAR band must drop out rather than pick the most generous
        one."""
        report = report_for(
            packs=("blr",),
            profile={"cityPack": "blr"},
            edges=[
                {"index": 0, "role": "front", "roadWidthMm": None, "setbackProvidedMm": 3000},
                {"index": 1, "role": "side-a", "roadWidthMm": None, "setbackProvidedMm": 1500},
                {"index": 2, "role": "rear", "roadWidthMm": None, "setbackProvidedMm": 2000},
                {"index": 3, "role": "side-b", "roadWidthMm": None, "setbackProvidedMm": 1500},
            ],
        )
        for rule_id in ("blr.far.road.lt9m", "blr.far.road.9-18m", "blr.far.road.ge18m"):
            row = report.rule(rule_id)
            assert row is not None and row.status == NOT_APPLICABLE, rule_id

    def test_plot_area_bands_are_scaled_not_rounded(self) -> None:
        """``{lte: 240}`` means 240 000 000 mm2 exactly, so a 240.000001 m2 plot falls
        outside the band."""
        inside = report_for(packs=("blr",), profile={"cityPack": "blr"}, area_mm2=240_000_000).rule(
            "blr.setback.front.plot.121-240"
        )
        outside = report_for(
            packs=("blr",), profile={"cityPack": "blr"}, area_mm2=240_000_001
        ).rule("blr.setback.front.plot.121-240")
        assert inside is not None and inside.status != NOT_APPLICABLE
        assert outside is not None and outside.status == NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Overrides (§13)
# ---------------------------------------------------------------------------


class TestOverrides:
    def _overridden(self) -> Any:
        return report_for(
            packs=("nbc-core",),
            rooms=[make_room("r1", "bedroom", **SHORT_BEDROOM)],
            profile={
                "overrides": {
                    "nbc.room.habitable.area.min": {
                        "reason": "Client accepted a 9.0 m2 study-bedroom; sanction precedent 44/21.",
                        "byUserId": "user_1",
                    }
                }
            },
        )

    def test_an_overridden_rule_still_reports_its_real_status(self) -> None:
        row = self._overridden().rule("nbc.room.habitable.area.min")
        assert row is not None
        assert row.status == FAIL  # suppressing it would hide it from the annexure
        assert row.overridden is True
        assert "precedent" in (row.override_reason or "")

    def test_an_override_clears_the_solver_gate_but_not_the_failure_list(self) -> None:
        report = self._overridden()
        assert [r.rule_id for r in report.failures()] == ["nbc.room.habitable.area.min"]
        assert report.blocking_failures() == ()
        assert report.is_presentable()
        assert report.counts["overridden"] == 1

    def test_the_override_reaches_the_json(self) -> None:
        row = next(
            r
            for r in self._overridden().to_json()["results"]
            if r["ruleId"] == "nbc.room.habitable.area.min"
        )
        assert row["overridden"] is True
        assert row["overrideReason"]

    def test_value_overrides_coexist_with_rule_acknowledgements(self) -> None:
        """The reserved ``values`` key is the plot panel's integer value-override
        map, NOT a rule id. Context building must route it — a profile carrying
        both shapes parses, the rule acknowledgement still works, and the values
        round-trip through ``to_json`` untouched."""
        report = report_for(
            packs=("nbc-core",),
            rooms=[make_room("r1", "bedroom", **SHORT_BEDROOM)],
            profile={
                "overrides": {
                    "values": {"setbackFrontMm": 1200, "farX100": 175},
                    "nbc.room.habitable.area.min": {
                        "reason": "Client accepted a 9.0 m2 study-bedroom.",
                    },
                }
            },
        )
        row = report.rule("nbc.room.habitable.area.min")
        assert row is not None and row.overridden is True

    def test_value_overrides_round_trip_and_reject_non_integers(self) -> None:
        from garh_rules.context import ProfileSummary

        profile = ProfileSummary.from_json(
            {
                "cityPack": "nbc-core",
                "zoneCategory": "residential",
                "buildingUse": "dwelling-single",
                "dwellingUnits": 1,
                "parkingSpacesProvided": 0,
                "rwhDeclared": False,
                "overrides": {
                    "values": {"setbackFrontMm": 1200},
                    "some.rule": {"reason": "logged"},
                },
            }
        )
        assert profile.value_overrides == {"setbackFrontMm": 1200}
        assert list(profile.overrides) == ["some.rule"]
        out = profile.to_json()["overrides"]
        assert out["values"] == {"setbackFrontMm": 1200}
        assert out["some.rule"]["reason"] == "logged"
        # Geometry discipline holds inside overrides too: integers only.
        with pytest.raises(ContextError):
            ProfileSummary.from_json(
                {
                    "cityPack": "nbc-core",
                    "zoneCategory": "residential",
                    "buildingUse": "dwelling-single",
                    "dwellingUnits": 1,
                    "parkingSpacesProvided": 0,
                    "rwhDeclared": False,
                    "overrides": {"values": {"setbackFrontMm": 1200.5}},
                }
            )

    def test_a_value_override_changes_the_limit_the_check_runs_against(self) -> None:
        """The substitution must MOVE the verdict, not just decorate the row.

        The parse/round-trip tests above passed for months while nothing asserted
        that a substituted limit changes a result — the exact "gate that cannot go
        red" shape. The default 30x40 plot provides 3000 mm in front; blr's pack
        value for a plot this size is 1500 mm (pass). Overriding the front setback
        to 3500 mm must flip that row to FAIL, with the row carrying the overridden
        limit AND the pack's original for the citation trail.
        """
        rule_id = "blr.setback.front.plot.le120"

        clean = report_for(packs=("blr",), profile={"cityPack": "blr"})
        row = clean.rule(rule_id)
        assert row is not None and row.status == PASS

        overridden = report_for(
            packs=("blr",),
            profile={"cityPack": "blr", "overrides": {"values": {"setbackFrontMm": 3500}}},
        )
        row = overridden.rule(rule_id)
        assert row is not None and row.status == FAIL
        assert row.limit == 3500
        assert row.original_limit == 1500
        # And an override that RELAXES below what the plot provides passes —
        # both directions, so the assertion cannot be satisfied by a constant.
        relaxed = report_for(
            packs=("blr",),
            profile={"cityPack": "blr", "overrides": {"values": {"setbackFrontMm": 1000}}},
        )
        row = relaxed.rule(rule_id)
        assert row is not None and row.status == PASS
        assert row.limit == 1000


# ---------------------------------------------------------------------------
# Chip text (§15)
# ---------------------------------------------------------------------------


class TestMessages:
    def test_the_chip_reads_like_the_playbook_example(self) -> None:
        """§15: "Bedroom 2 is 8.9m2 — NBC needs 9.5m2" (we print two decimals)."""
        rooms = [make_room("r1", "bedroom", width=2500, depth=3560, name="Bedroom 2")]
        row = report_for(packs=("nbc-core",), rooms=rooms).rule("nbc.room.habitable.area.min")
        assert row is not None
        assert "Bedroom 2" in row.message
        assert "8.90 m2" in row.message
        assert "9.50 m2" in row.message
        # The citation is a separate field (the chip shows it on hover, §15) — this
        # pack's template does not inline it, and the row must carry it regardless.
        assert row.cite.startswith("NBC 2016") and row.cite_short in row.cite

    def test_a_value_that_two_decimals_would_round_away_keeps_its_digits(self) -> None:
        """A 1 mm2 shortfall must not print as "has 9.50 m2, needs 9.50 m2"."""
        rooms = [make_room("r1", "bedroom", width=2500, depth=3799)]  # 9 497 500 mm2
        row = report_for(packs=("nbc-core",), rooms=rooms).rule("nbc.room.habitable.area.min")
        assert row is not None
        assert "9.4975 m2" in row.message

    def test_no_placeholder_survives_rendering(self) -> None:
        report = report_for(
            packs=("blr", "vastu"),
            vastu_mode="advisory",
            rooms=[make_room("r1", "bedroom", **SHORT_BEDROOM)],
            stairs=[
                {
                    "id": "s1",
                    "storeyId": "storey_g",
                    "riserMm": 200,
                    "treadMm": 240,
                    "widthMm": 850,
                    "headroomMm": 2000,
                    "centroidMm": [1000, 1000],
                }
            ],
        )
        for row in report.results:
            for token in ("{element}", "{actual}", "{limit}", "{cite}"):
                assert token not in row.message, row.rule_id
            for instance in row.instances:
                assert "{" not in instance.message, row.rule_id

    def test_small_lengths_stay_in_millimetres(self) -> None:
        """NBC quotes door and riser minima in mm; "0.75 m" reads like a different rule."""
        opening = {
            "id": "d_bath",
            "storeyId": "storey_g",
            "kind": "door",
            "role": "bath",
            "widthMm": 700,
            "heightMm": 2100,
        }
        row = report_for(packs=("nbc-core",), openings=[opening]).rule("nbc.door.bath.width.min")
        assert row is not None
        assert "700 mm" in row.message and "750 mm" in row.message

    def test_labels_name_the_element_the_way_a_drawing_does(self) -> None:
        report = report_for(
            packs=("blr",),
            profile={"cityPack": "blr"},
            edges=[
                {"index": 0, "role": "front", "roadWidthMm": 9000, "setbackProvidedMm": 1000},
                {"index": 1, "role": "side-a", "roadWidthMm": None, "setbackProvidedMm": 1500},
                {"index": 2, "role": "rear", "roadWidthMm": None, "setbackProvidedMm": 2000},
                {"index": 3, "role": "side-b", "roadWidthMm": None, "setbackProvidedMm": 1500},
            ],
        )
        row = report.rule("blr.setback.front.road.9-18m")
        assert row is not None
        assert "front setback" in row.message
        assert row.elements == ("plot.edge.front",)


# ---------------------------------------------------------------------------
# Entry points and the context boundary
# ---------------------------------------------------------------------------


class TestEntryPoints:
    def test_evaluate_parts_matches_evaluate(self) -> None:
        context = make_context(packs=("blr",), rooms=[make_room("r1", "bedroom")])
        direct = evaluate(context, root=RULEPACK_DIR)
        parts = evaluate_parts(
            context.model.to_json(),
            context.plot.to_json(),
            context.profile.to_json(),
            ["blr"],
            root=RULEPACK_DIR,
        )
        assert parts.to_json() == direct.to_json()

    def test_a_pre_resolved_pack_set_is_reused(self) -> None:
        """What the solver critic passes, so thousands of candidate scorings share one
        load and the hot path never touches the filesystem."""
        pack_set = load_pack_set(["blr"], root=RULEPACK_DIR)
        report = evaluate(make_context(packs=("blr",)), packs=pack_set)
        assert report.packs == pack_set.load_order

    def test_packs_argument_overrides_the_contexts_own_list(self) -> None:
        report = evaluate(make_context(packs=("blr",)), packs=["nbc-core"], root=RULEPACK_DIR)
        assert report.packs == ("nbc-core",)

    def test_a_json_context_and_a_dataclass_context_agree(self) -> None:
        context = make_context(packs=("nbc-core",), rooms=[make_room("r1", "bedroom")])
        assert (
            evaluate(context.to_json(), root=RULEPACK_DIR).to_json()
            == evaluate(context, root=RULEPACK_DIR).to_json()
        )


class TestContextBoundary:
    def _json(self, **model_overrides: Any) -> dict[str, Any]:
        context = make_context(packs=("nbc-core",), rooms=[make_room("r1", "bedroom")])
        data = context.to_json()
        data["model"].update(model_overrides)
        return data

    def test_a_float_length_is_refused_not_rounded(self) -> None:
        data = self._json()
        data["model"]["rooms"][0]["areaMm2"] = 9_500_000.5
        with pytest.raises(ContextError) as excinfo:
            EvaluationContext.from_json(data)
        assert "integer millimetres" in str(excinfo.value)

    def test_a_boolean_is_not_an_integer(self) -> None:
        data = self._json(storeyCount=True)
        with pytest.raises(ContextError):
            EvaluationContext.from_json(data)

    def test_a_room_on_an_unknown_storey_raises_rather_than_guessing(self) -> None:
        data = self._json()
        data["model"]["rooms"][0]["storeyId"] = "storey_nowhere"
        context = EvaluationContext.from_json(data)
        with pytest.raises(ContextError) as excinfo:
            evaluate(context, root=RULEPACK_DIR)
        assert "storey_nowhere" in str(excinfo.value)

    def test_an_out_of_range_north_bearing_is_refused(self) -> None:
        data = self._json()
        data["plot"]["northDeg"] = 360
        with pytest.raises(ContextError):
            EvaluationContext.from_json(data)

    def test_an_unknown_vastu_mode_is_refused(self) -> None:
        data = self._json()
        data["vastuMode"] = "strictish"
        with pytest.raises(ContextError):
            EvaluationContext.from_json(data)

    def test_a_context_with_no_packs_is_refused(self) -> None:
        data = self._json()
        data["packs"] = []
        with pytest.raises(ContextError):
            EvaluationContext.from_json(data)

    def test_the_context_round_trips_through_json(self) -> None:
        context = make_context(
            packs=("blr",),
            rooms=[make_room("r1", "bedroom")],
            projections=[
                {
                    "id": "p1",
                    "storeyId": "storey_g",
                    "element": "chajja",
                    "edgeRole": "front",
                    "projectionMm": 600,
                    "intoSetback": True,
                }
            ],
            service_elements=[{"id": "svc", "kind": "oht", "centroidMm": [100, 200]}],
        )
        assert EvaluationContext.from_json(context.to_json()).to_json() == context.to_json()

    def test_coerce_refuses_anything_else(self) -> None:
        with pytest.raises(ContextError):
            EvaluationContext.coerce(42)


class TestRoomTypeDrift:
    def test_a_model_core_spelling_is_aliased_onto_the_pack_vocabulary(self) -> None:
        """Without this, a master bedroom is not habitable as far as the packs are
        concerned and NBC's 9.5 m2 minimum silently never fires on it."""
        assert normalise_room_type("bedroom_master") == "master_bedroom"
        row = report_for(
            packs=("nbc-core",),
            rooms=[make_room("r1", "bedroom_master", **SHORT_BEDROOM)],
        ).rule("nbc.room.habitable.area.min")
        assert row is not None
        assert row.status == FAIL

    def test_an_unmapped_room_type_is_reported_not_assumed_compliant(self) -> None:
        report = report_for(packs=("nbc-core",), rooms=[make_room("r1", "home_theatre")])
        assert any("home_theatre" in warning for warning in report.warnings)

    def test_the_fields_the_model_core_still_owes_are_named(self) -> None:
        assert MODEL_FIELDS_NOT_IN_MODEL_CORE
        assert any("openingRole" in note for note in MODEL_FIELDS_NOT_IN_MODEL_CORE)
