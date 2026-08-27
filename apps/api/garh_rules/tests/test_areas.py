"""The area statement — §7's "same numbers, one source", tested as such.

§7: "area statement per municipal format: plot area, per-storey built-up, total,
FAR achieved vs allowed, coverage achieved vs allowed, setbacks provided vs
required (**from rules results — same numbers, one source**)".

So the assertions here are mostly about *provenance*: every allowance in the
statement has to be the number a rule result carried, every requirement has to be
the strictest of the rules that applied, and each row has to name the rule ids it
came from so the compliance annexure and the sheet can be reconciled by eye.

The two directions are deliberately opposite and both are pinned: an **allowance**
takes the smallest (the binding cap), a **requirement** takes the largest (minimums
stack — a city front-setback table indexed by plot size *and* road width is two
rule families whose maximum is the real requirement).
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any

from garh_rules import area_statement, evaluate, load_pack_set
from garh_rules.areas import build_area_statement

from .conftest import RULEPACK_DIR, make_context, make_room

# 300 m2 plot (17.32 x 17.32 m), 9 m road: lands in BBMP's 241-500 m2 band, so
# coverage 60%, FAR 2.25, front setback 3 m (both the plot-size and road-width
# families apply), sides 1.5 m, rear 2 m, 2 parking spaces.
PLOT_300 = [[0, 0], [17_320, 0], [17_320, 17_320], [0, 17_320]]
PLOT_300_AREA = 300_000_000


def blr_context(**overrides: Any) -> Any:
    edges: list[dict[str, Any]] = [
        {"index": 0, "role": "front", "roadWidthMm": 9000, "setbackProvidedMm": 3000},
        {"index": 1, "role": "side-a", "roadWidthMm": None, "setbackProvidedMm": 1500},
        {"index": 2, "role": "rear", "roadWidthMm": None, "setbackProvidedMm": 2000},
        {"index": 3, "role": "side-b", "roadWidthMm": None, "setbackProvidedMm": 1500},
    ]
    model: dict[str, Any] = {
        "storeyCount": 2,
        "buildingHeightMm": 7500,
        "heightComponentsMm": {"parapet": 900, "mumty": 2400, "oht": 1200},
        "footprintAreaMm2": 150_000_000,
        "builtUpAreaMm2": 300_000_000,
        "farCountableAreaMm2": 280_000_000,
        "storeys": [
            {"id": "storey_g", "index": 0, "heightMm": 3000, "builtUpAreaMm2": 150_000_000},
            {"id": "storey_1", "index": 1, "heightMm": 3000, "builtUpAreaMm2": 150_000_000},
        ],
    }
    model.update(overrides.pop("model", {}) or {})
    profile: dict[str, Any] = {"cityPack": "blr", "parkingSpacesProvided": 2, "dwellingUnits": 1}
    profile.update(overrides.pop("profile", {}) or {})
    return make_context(
        packs=("blr",),
        boundary=overrides.pop("boundary", PLOT_300),
        area_mm2=overrides.pop("area_mm2", PLOT_300_AREA),
        edges=overrides.pop("edges", edges),
        model=model,
        profile=profile,
        **overrides,
    )


def statement(**overrides: Any) -> Any:
    return evaluate(blr_context(**overrides), root=RULEPACK_DIR).areas


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class TestNumbersComeFromTheRules:
    def test_far_allowance_is_the_rules_limit_not_a_second_calculation(self) -> None:
        report = evaluate(blr_context(), root=RULEPACK_DIR)
        far_rule = report.rule("blr.far.road.9-18m")
        assert far_rule is not None and far_rule.limit == 675_000_000  # 2.25 x 300 m2
        assert report.areas.far_allowed_mm2 == far_rule.limit
        assert report.areas.rule_ids["far"] == ("blr.far.road.9-18m",)

    def test_coverage_allowance_and_achieved_ratio(self) -> None:
        areas = statement()
        assert areas.coverage_allowed_mm2 == 180_000_000  # 60% of 300 m2
        assert areas.coverage_achieved == Fraction(150_000_000, 300_000_000)
        assert areas.coverage_allowed == Fraction(180_000_000, 300_000_000)

    def test_far_achieved_is_an_exact_rational(self) -> None:
        areas = statement()
        assert areas.far_achieved == Fraction(280_000_000, 300_000_000)
        assert areas.to_json()["farAchieved"] == "0.93"
        assert areas.to_json()["farAllowed"] == "2.25"

    def test_the_strictest_allowance_governs_when_two_bands_apply(self) -> None:
        """Both height rules cannot apply at once here, so the test builds the
        situation directly: two applicable caps, the smaller one binds."""
        report = evaluate(blr_context(), root=RULEPACK_DIR)
        pack_set = load_pack_set(["blr"], root=RULEPACK_DIR)
        results = list(report.results)
        far_row = next(r for r in results if r.check_type == "far_max" and r.applicable)
        from dataclasses import replace

        tighter = replace(far_row, rule_id="test.far.tighter", limit=100_000_000)
        rebuilt = build_area_statement(blr_context(), pack_set, [*results, tighter])
        assert rebuilt.far_allowed_mm2 == 100_000_000
        assert rebuilt.rule_ids["far"] == ("blr.far.road.9-18m", "test.far.tighter")

    def test_setback_requirements_stack_to_the_largest(self) -> None:
        """The front edge is covered by the plot-size family (3 m) and the road-width
        family (3 m); a shorter provided setback must be short against the maximum."""
        areas = statement(
            edges=[
                {"index": 0, "role": "front", "roadWidthMm": 9000, "setbackProvidedMm": 2500},
                {"index": 1, "role": "side-a", "roadWidthMm": None, "setbackProvidedMm": 1500},
                {"index": 2, "role": "rear", "roadWidthMm": None, "setbackProvidedMm": 2000},
                {"index": 3, "role": "side-b", "roadWidthMm": None, "setbackProvidedMm": 1500},
            ]
        )
        front = next(row for row in areas.setbacks if row.role == "front")
        assert front.required_mm == 3000
        assert front.provided_mm == 2500
        assert front.status == "short"
        assert front.shortfall_mm == 500
        assert len(front.rule_ids) == 2  # both families are named

    def test_a_sides_rule_fills_both_side_rows(self) -> None:
        areas = statement()
        sides = {row.role: row for row in areas.setbacks if row.role.startswith("side")}
        assert set(sides) == {"side-a", "side-b"}
        for row in sides.values():
            assert row.required_mm == 1500
            assert row.status == "ok"
            assert row.rule_ids == ("blr.setback.side.plot.241-500",)

    def test_a_setback_row_and_its_chip_name_the_same_edge(self) -> None:
        """Two edges in the same role (legal for ``other``) must not collapse into one
        chip, and the statement must use the id the chip uses — one helper, both."""
        from garh_rules.scope import edge_element_id

        context = blr_context(
            edges=[
                {"index": 0, "role": "front", "roadWidthMm": 9000, "setbackProvidedMm": 3000},
                {"index": 1, "role": "other", "roadWidthMm": None, "setbackProvidedMm": 900},
                {"index": 2, "role": "other", "roadWidthMm": None, "setbackProvidedMm": 800},
                {"index": 3, "role": "rear", "roadWidthMm": None, "setbackProvidedMm": 2000},
            ]
        )
        report = evaluate(context, root=RULEPACK_DIR)
        ids = [row.element_id for row in report.areas.setbacks]
        assert ids == [edge_element_id(edge, context.plot.edges) for edge in context.plot.edges]
        assert len(set(ids)) == len(ids)
        assert "plot.edge.other.1" in ids and "plot.edge.other.2" in ids
        front = next(row for row in report.areas.setbacks if row.role == "front")
        assert front.element_id == "plot.edge.front"
        assert front.required_mm == 3000

    def test_parking_requirement_is_the_rules_limit(self) -> None:
        areas = statement()
        assert areas.parking_provided == 2
        assert areas.parking_required == 2
        assert areas.rule_ids["parking"] == ("blr.parking.plot.gt240",)

    def test_height_row_reports_the_counted_height_not_the_raw_one(self) -> None:
        """BBMP excludes parapet, mumty and OHT, so the number on the drawing is the
        counted one — with a note saying so."""
        areas = statement()
        assert areas.building_height_mm == 7500
        assert areas.height_counted_mm == 7500 - (900 + 2400 + 1200)
        assert areas.height_allowed_mm == 15_000
        row = next(r for r in areas.rows() if r.key == "height")
        assert row.value == areas.height_counted_mm
        assert row.note is not None

    def test_floors_row_uses_the_rules_count(self) -> None:
        areas = statement()
        assert areas.floors_counted == 2
        assert areas.floors_allowed == 4  # 9-18 m road


class TestNotRegulated:
    def test_an_unregulated_allowance_is_none_never_zero_and_never_unlimited(self) -> None:
        """nbc-core has no FAR or coverage rule at all: the statement must say "not
        regulated by the loaded packs", not print a 0.00 cap."""
        areas = area_statement(make_context(packs=("nbc-core",)), root=RULEPACK_DIR)
        assert areas.far_allowed_mm2 is None
        assert areas.coverage_allowed_mm2 is None
        assert areas.far_allowed is None
        assert "not regulated" in (next(row for row in areas.rows() if row.key == "far").note or "")
        assert any("No FAR rule applied" in w for w in areas.warnings)

    def test_an_unregulated_setback_row_still_reports_what_was_provided(self) -> None:
        areas = area_statement(make_context(packs=("nbc-core",)), root=RULEPACK_DIR)
        front = next(row for row in areas.setbacks if row.role == "front")
        assert front.provided_mm == 3000
        assert front.required_mm is None
        assert front.status == "not_regulated"
        assert front.shortfall_mm == 0

    def test_not_applicable_rules_contribute_nothing(self) -> None:
        """A plot with no road: every road-banded FAR/height/floor rule drops out, so
        no allowance may be stated from them."""
        areas = statement(
            edges=[
                {"index": 0, "role": "front", "roadWidthMm": None, "setbackProvidedMm": 3000},
                {"index": 1, "role": "side-a", "roadWidthMm": None, "setbackProvidedMm": 1500},
                {"index": 2, "role": "rear", "roadWidthMm": None, "setbackProvidedMm": 2000},
                {"index": 3, "role": "side-b", "roadWidthMm": None, "setbackProvidedMm": 1500},
            ]
        )
        assert areas.far_allowed_mm2 is None
        assert areas.height_allowed_mm is None
        assert areas.floors_allowed is None
        # ... while the plot-size families still apply
        assert areas.coverage_allowed_mm2 == 180_000_000


class TestRowsAndWarnings:
    def test_rows_are_in_municipal_reading_order(self) -> None:
        keys = [row.key for row in statement().rows()]
        assert keys[:2] == ["plot_area", "coverage"]
        assert "built_up_total" in keys
        assert (
            keys.index("far") < keys.index("floors") < keys.index("height") < keys.index("parking")
        )
        assert [k for k in keys if k.startswith("built_up.")] == [
            "built_up.storey_g",
            "built_up.storey_1",
        ]

    def test_limit_labels_distinguish_a_cap_from_a_minimum(self) -> None:
        """Printing "allowed 1.50 m" against a minimum setback is the kind of small
        wrongness that gets a drawing queried at the counter."""
        rows = {row.key: row for row in statement().rows()}
        assert rows["far"].limit_label == "Permissible"
        assert rows["setback.front"].limit_label == "Required"
        assert rows["plot_area"].limit_label == ""

    def test_text_rows_are_formatted_for_the_sheet(self) -> None:
        text = {label: (value, limit) for label, value, limit, _ in statement().text_rows()}
        assert text["Plot area"][0] == "300.00 m2"
        assert text["Ground coverage"] == ("150.00 m2", "180.00 m2")
        assert text["Front setback"] == ("3.00 m", "3.00 m")
        assert text["Car parking spaces"] == ("2", "2")

    def test_a_per_storey_sum_that_disagrees_with_the_total_is_reported(self) -> None:
        """A statement whose rows do not add up is a rejected drawing."""
        areas = statement(model={"builtUpAreaMm2": 299_000_000})
        assert any("will not add up" in w for w in areas.warnings)

    def test_a_missing_per_storey_area_is_reported(self) -> None:
        areas = statement(
            model={
                "storeys": [
                    {"id": "storey_g", "index": 0, "heightMm": 3000},
                    {"id": "storey_1", "index": 1, "heightMm": 3000, "builtUpAreaMm2": 150_000_000},
                ]
            }
        )
        assert any("carry no builtUpAreaMm2" in w for w in areas.warnings)
        assert areas.per_storey[0].built_up_area_mm2 is None

    def test_per_storey_rows_are_ordered_by_index_whatever_the_model_order(self) -> None:
        areas = statement(
            model={
                "storeys": [
                    {"id": "storey_1", "index": 1, "heightMm": 3000, "builtUpAreaMm2": 150_000_000},
                    {"id": "storey_g", "index": 0, "heightMm": 3000, "builtUpAreaMm2": 150_000_000},
                ]
            }
        )
        assert [s.index for s in areas.per_storey] == [0, 1]
        assert [s.label for s in areas.per_storey] == ["Ground floor", "First floor"]

    def test_the_statement_is_json_serialisable(self) -> None:
        import json

        encoded = json.loads(json.dumps(statement().to_json()))
        assert encoded["plotAreaMm2"] == PLOT_300_AREA
        assert encoded["setbacks"][0]["role"] == "front"
        assert encoded["rows"]

    def test_area_statement_helper_matches_the_reports_own(self) -> None:
        """One code path: the drawings engine and the compliance panel cannot drift."""
        context = blr_context()
        report = evaluate(context, root=RULEPACK_DIR)
        assert area_statement(context, root=RULEPACK_DIR).to_json() == report.areas.to_json()


class TestAreaWarningsSurfaceInTheReport:
    def test_report_warnings_include_the_statements(self) -> None:
        report = evaluate(blr_context(model={"builtUpAreaMm2": 299_000_000}), root=RULEPACK_DIR)
        assert any("will not add up" in w for w in report.warnings)

    def test_the_room_area_of_a_habitable_room_does_not_reach_the_statement(self) -> None:
        """The statement carries only the municipal rows; a room minimum is a chip, not
        a line in the area statement."""
        areas = evaluate(
            blr_context(model={"rooms": [make_room("r1", "bedroom")]}), root=RULEPACK_DIR
        ).areas
        assert all(not row.key.startswith("room") for row in areas.rows())
