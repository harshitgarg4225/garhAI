from __future__ import annotations

"""Vastu: zone checks, the two modes, and the 0-100 score computed by hand.

Playbook §6: "zones = 3x3 grid oriented to true north ... Score = weighted rule
satisfaction, 0-100, per-rule breakdown for the compass-wheel UI", advisory
scoring vs strict constraints.

The score is the part most easily fudged, so every arithmetic assertion here is
worked out from ``vastu.json``'s own weights in the test body. The pack ships
nine rules whose weights sum to 100:

======================  ======  ===========
rule                    weight  group
======================  ======  ===========
entrance.edge               18  entry
kitchen.zone                14  fire
master.zone                 12  sleep
toilet.never_ne             12  water
pooja.zone                  10  sacred
toilet.zone                 10  water
stair.zone                  10  circulation
brahmasthan.open             8  sacred
water_tank.zone              6  water
======================  ======  ===========

Three properties carry the design and each has its own test: ``off`` drops the
pack entirely (no grey wall of rows), ``advisory`` clamps every severity to
``warn`` while still scoring, and a rule with no matching element leaves **both**
sums — a house with no pooja room is neither credited nor penalised.
"""

from fractions import Fraction
from typing import Any, Dict, List, Optional

import pytest

from garh_rules import evaluate, load_pack_set
from garh_rules.ratio import round_half_up
from garh_rules.results import FAIL, NOT_APPLICABLE, PASS, WARN
from garh_rules.scoring import clamp_severity

from .conftest import RULEPACK_DIR, make_context, make_room

# A 9 x 9 m plot with north up: cell splits land on 3000 / 6000, so a room's zone
# is obvious from its coordinates and a reader can check the test by eye.
SQUARE_9 = [[0, 0], [9000, 0], [9000, 9000], [0, 9000]]

ZONE_CENTRES: Dict[str, Any] = {
    "SW": (1500, 1500),
    "S": (4500, 1500),
    "SE": (7500, 1500),
    "W": (1500, 4500),
    "C": (4500, 4500),
    "E": (7500, 4500),
    "NW": (1500, 7500),
    "N": (4500, 7500),
    "NE": (7500, 7500),
}


def room_in(zone: str, room_id: str, room_type: str, size: int = 1000) -> Dict[str, Any]:
    """A small square room centred on ``zone``'s cell centre."""
    cx, cy = ZONE_CENTRES[zone]
    return make_room(
        room_id,
        room_type,
        x=cx - size // 2,
        y=cy - size // 2,
        width=size,
        depth=size,
        ventilation_mm2=size * size,
    )


def vastu_context(
    *,
    mode: str = "advisory",
    rooms: Optional[List[Dict[str, Any]]] = None,
    stairs: Optional[List[Dict[str, Any]]] = None,
    openings: Optional[List[Dict[str, Any]]] = None,
    services: Optional[List[Dict[str, Any]]] = None,
    north_deg: int = 0,
) -> Any:
    return make_context(
        packs=("vastu",),
        vastu_mode=mode,
        boundary=SQUARE_9,
        north_deg=north_deg,
        rooms=rooms if rooms is not None else [],
        stairs=stairs if stairs is not None else [],
        openings=openings if openings is not None else [],
        service_elements=services if services is not None else [],
    )


def entrance(zone_bearing: int, opening_id: str = "d_main") -> Dict[str, Any]:
    return {
        "id": opening_id,
        "storeyId": "storey_g",
        "kind": "door",
        "role": "main-entrance",
        "widthMm": 1000,
        "heightMm": 2100,
        "outwardNormalDeg": zone_bearing,
    }


def stair_in(zone: str, stair_id: str = "stair_main") -> Dict[str, Any]:
    cx, cy = ZONE_CENTRES[zone]
    return {
        "id": stair_id,
        "storeyId": "storey_g",
        "kind": "dogleg",
        "riserMm": 165,
        "treadMm": 280,
        "widthMm": 1050,
        "headroomMm": 2200,
        "centroidMm": [cx, cy],
    }


def row(context: Any, rule_id: str) -> Any:
    report = evaluate(context, root=RULEPACK_DIR)
    result = report.rule(rule_id)
    assert result is not None, rule_id
    return result


# ---------------------------------------------------------------------------
# zone_check semantics
# ---------------------------------------------------------------------------


class TestZoneCheck:
    def test_an_allowed_zone_passes_with_full_satisfaction(self) -> None:
        result = row(vastu_context(rooms=[room_in("NE", "pooja_1", "pooja")]), "vastu.pooja.zone")
        assert result.status == PASS
        assert result.actual == ["NE"]
        assert result.satisfaction == Fraction(1)
        assert result.limit == {"allow": ["NE"], "fallback": {"allow": ["N", "E"], "scoreRatio": {"num": 1, "den": 2}}}

    def test_a_fallback_zone_warns_at_half_score(self) -> None:
        """"Acceptable but not ideal" is a warning even on a fail-severity rule."""
        result = row(vastu_context(rooms=[room_in("N", "pooja_1", "pooja")]), "vastu.pooja.zone")
        assert result.status == WARN
        assert result.satisfaction == Fraction(1, 2)

    def test_a_wrong_zone_is_a_violation_with_zero_satisfaction(self) -> None:
        result = row(vastu_context(rooms=[room_in("SW", "pooja_1", "pooja")]), "vastu.pooja.zone")
        assert result.status == WARN  # advisory ceiling; the rule declares warn anyway
        assert result.satisfaction == Fraction(0)
        assert result.elements == ("pooja_1",)

    def test_deny_beats_allow_and_is_the_hard_rule(self) -> None:
        context = vastu_context(rooms=[room_in("NE", "bath_1", "bath")])
        never_ne = row(context, "vastu.toilet.never_ne")
        assert never_ne.status == WARN  # advisory clamps the declared `fail`
        assert never_ne.declared_severity == FAIL
        assert never_ne.hard is True
        assert never_ne.satisfaction == Fraction(0)
        assert never_ne.actual == ["NE"]

    def test_a_deny_only_rule_passes_anything_outside_the_forbidden_set(self) -> None:
        result = row(
            vastu_context(rooms=[room_in("W", "bath_1", "bath")]), "vastu.toilet.never_ne"
        )
        assert result.status == PASS
        assert result.satisfaction == Fraction(1)

    def test_several_targets_average_their_satisfaction(self) -> None:
        """Two toilets, one ideal and one wrong: the rule contributes 1/2, and both the
        row and the chip name the offender."""
        context = vastu_context(
            rooms=[room_in("W", "bath_1", "bath"), room_in("SE", "wc_1", "wc")]
        )
        result = row(context, "vastu.toilet.zone")
        assert result.satisfaction == Fraction(1, 2)
        assert result.elements == ("wc_1",)
        assert sorted(result.actual) == ["SE", "W"]
        assert len(result.instances) == 2

    def test_the_union_actual_shows_every_zone_not_just_the_governing_one(self) -> None:
        context = vastu_context(
            rooms=[room_in("NE", "bath_1", "bath"), room_in("N", "wc_1", "wc")]
        )
        assert row(context, "vastu.toilet.never_ne").actual == ["N", "NE"]

    def test_facing_mode_reads_the_openings_outward_normal(self) -> None:
        assert row(vastu_context(openings=[entrance(0)]), "vastu.entrance.edge").status == PASS
        assert row(vastu_context(openings=[entrance(45)]), "vastu.entrance.edge").status == PASS
        south = row(vastu_context(openings=[entrance(180)]), "vastu.entrance.edge")
        assert south.status == WARN and south.actual == ["S"]

    def test_facing_mode_follows_true_north(self) -> None:
        """The same door on a plot rotated 180 degrees faces the other way."""
        assert row(
            vastu_context(openings=[entrance(180)], north_deg=180), "vastu.entrance.edge"
        ).status == PASS

    def test_a_missing_outward_normal_raises_rather_than_passing(self) -> None:
        from garh_rules.errors import ContextError

        opening = entrance(0)
        del opening["outwardNormalDeg"]
        with pytest.raises(ContextError) as excinfo:
            evaluate(vastu_context(openings=[opening]), root=RULEPACK_DIR)
        assert "outwardNormalDeg" in str(excinfo.value)

    def test_no_target_is_not_applicable_and_leaves_the_score_alone(self) -> None:
        result = row(vastu_context(rooms=[]), "vastu.pooja.zone")
        assert result.status == NOT_APPLICABLE
        assert result.not_applicable_reason == "no-instances"
        assert result.satisfaction is None

    def test_stair_targets_need_no_room_type(self) -> None:
        assert row(vastu_context(stairs=[stair_in("SW")]), "vastu.stair.zone").status == PASS
        assert row(vastu_context(stairs=[stair_in("NE")]), "vastu.stair.zone").status == WARN

    def test_service_targets_are_selected_by_kind(self) -> None:
        cx, cy = ZONE_CENTRES["NE"]
        context = vastu_context(services=[{"id": "svc_1", "kind": "oht", "centroidMm": [cx, cy]}])
        assert row(context, "vastu.water_tank.zone").status == PASS


# ---------------------------------------------------------------------------
# brahmasthan_open
# ---------------------------------------------------------------------------


class TestBrahmasthanOpen:
    def _context(self, *rooms: Dict[str, Any]) -> Any:
        return vastu_context(rooms=list(rooms))

    def test_an_empty_centre_passes_at_zero_coverage(self) -> None:
        result = row(self._context(room_in("SW", "bed_1", "bedroom")), "vastu.brahmasthan.open")
        assert result.status == PASS
        assert result.actual == 0
        assert result.limit == 5000  # floor(10000 * 1/2)

    def test_an_open_room_type_over_the_centre_does_not_enclose_it(self) -> None:
        """A courtyard or a living room across the centre is what the rule wants."""
        courtyard = make_room("court_1", "courtyard", x=3000, y=3000, width=3000, depth=3000)
        assert row(self._context(courtyard), "vastu.brahmasthan.open").actual == 0

    def test_exactly_half_the_centre_cell_still_passes(self) -> None:
        # centre cell is 3000 x 3000 at (3000,3000); a 3000 x 1500 bedroom covers half
        bedroom = make_room("bed_1", "bedroom", x=3000, y=3000, width=3000, depth=1500)
        result = row(self._context(bedroom), "vastu.brahmasthan.open")
        assert result.actual == 5000
        assert result.status == PASS

    def test_one_millimetre_more_than_half_fails_and_names_the_room(self) -> None:
        bedroom = make_room("bed_1", "bedroom", x=3000, y=3000, width=3000, depth=1501)
        result = row(self._context(bedroom), "vastu.brahmasthan.open")
        assert result.actual == 5003  # floor(10000 * 4503000 / 9000000)
        assert result.status == WARN
        assert result.elements == ("bed_1",)

    def test_the_worst_room_governs_but_every_offender_is_named(self) -> None:
        # ground-floor bedroom covers 6/9 of the cell, the one above it 4.8/9 — both
        # are over the half-cell limit, and the worse one sets `actual`.
        ground = make_room("bed_1", "bedroom", x=3000, y=3000, width=3000, depth=2000)
        upper = make_room(
            "bed_2", "bedroom", x=3000, y=4000, width=3000, depth=1600, storey_id="storey_1"
        )
        result = row(self._context(ground, upper), "vastu.brahmasthan.open")
        assert result.actual == 6666  # floor(10000 * 6/9)
        assert set(result.elements) == {"bed_1", "bed_2"}

    def test_an_upper_storey_room_encloses_the_centre_too(self) -> None:
        upper = make_room(
            "bed_1", "bedroom", x=3000, y=3000, width=3000, depth=3000, storey_id="storey_1"
        )
        assert row(self._context(upper), "vastu.brahmasthan.open").status == WARN


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


class TestModes:
    def _ne_toilet(self, mode: str) -> Any:
        return vastu_context(mode=mode, rooms=[room_in("NE", "bath_1", "bath")])

    def test_off_drops_the_pack_entirely(self) -> None:
        """Not nine grey ``not_applicable`` rows — the pack "is not loaded at all"."""
        report = evaluate(self._ne_toilet("off"), root=RULEPACK_DIR)
        assert report.results == ()
        assert report.scores == ()
        assert report.score is None

    def test_advisory_clamps_every_severity_to_warn_but_still_scores(self) -> None:
        report = evaluate(self._ne_toilet("advisory"), root=RULEPACK_DIR)
        assert {r.severity for r in report.results} == {"warn"}
        assert any(r.declared_severity == FAIL for r in report.results)
        assert report.is_presentable()  # advisory Vastu never blocks the solver
        assert report.score is not None and report.score.score is not None

    def test_strict_lets_a_fail_through_and_enforces(self) -> None:
        report = evaluate(self._ne_toilet("strict"), root=RULEPACK_DIR)
        never_ne = report.rule("vastu.toilet.never_ne")
        assert never_ne is not None and never_ne.status == FAIL
        assert not report.is_presentable()
        assert report.score is not None and report.score.enforce is True
        assert report.score.hard_violations() == ("vastu.toilet.never_ne",)

    def test_clamp_severity_is_monotone(self) -> None:
        assert clamp_severity("fail", "warn") == "warn"
        assert clamp_severity("warn", "fail") == "warn"  # never promoted
        assert clamp_severity("fail", None) == "fail"


# ---------------------------------------------------------------------------
# The 0-100 score
# ---------------------------------------------------------------------------


class TestScore:
    def test_a_fully_compliant_house_scores_100(self) -> None:
        cx, cy = ZONE_CENTRES["NE"]
        context = vastu_context(
            rooms=[
                room_in("NE", "pooja_1", "pooja"),
                room_in("SE", "kitchen_1", "kitchen"),
                room_in("SW", "master_1", "master_bedroom"),
                room_in("W", "bath_1", "bath"),
            ],
            stairs=[stair_in("S")],
            openings=[entrance(0)],
            services=[{"id": "svc_1", "kind": "oht", "centroidMm": [cx, cy]}],
        )
        report = evaluate(context, root=RULEPACK_DIR)
        assert report.score is not None
        assert report.score.score == 100
        assert report.score.applicable_weight == 100  # all nine rules applied
        assert report.score.total_weight == 100

    def test_only_applicable_rules_are_in_either_sum(self) -> None:
        """One perfectly placed pooja room and nothing else: 10 of 100 weight applies
        (plus brahmasthan, which is always project-scope), and the score is 100 —
        not 10 — because the rules that did not apply are simply absent."""
        report = evaluate(
            vastu_context(rooms=[room_in("NE", "pooja_1", "pooja")]), root=RULEPACK_DIR
        )
        assert report.score is not None
        assert report.score.applicable_weight == 18  # pooja 10 + brahmasthan 8
        assert report.score.score == 100
        assert {r.rule_id for r in report.score.rules} == {
            "vastu.pooja.zone",
            "vastu.brahmasthan.open",
        }

    def test_the_score_is_the_weighted_mean_rounded_half_up_once(self) -> None:
        """pooja in a fallback zone (10 x 1/2) + brahmasthan clear (8 x 1):
        100 * 13/18 = 72.22 -> 72."""
        report = evaluate(
            vastu_context(rooms=[room_in("N", "pooja_1", "pooja")]), root=RULEPACK_DIR
        )
        assert report.score is not None
        expected = round_half_up(Fraction(100) * (Fraction(10, 2) + 8) / 18)
        assert expected == 72
        assert report.score.score == expected

    def test_group_weights_are_derived_from_their_members(self) -> None:
        context = vastu_context(
            rooms=[room_in("NE", "pooja_1", "pooja"), room_in("NE", "bath_1", "bath")],
        )
        report = evaluate(context, root=RULEPACK_DIR)
        assert report.score is not None
        groups = {g.id: g for g in report.score.groups}
        # sacred = pooja (10) + brahmasthan (8); water = toilet.zone (10) + never_ne (12)
        assert groups["sacred"].weight == 18
        assert groups["water"].weight == 22
        assert groups["sacred"].score == 100
        assert groups["water"].score == 0
        # a group whose rules all dropped out reads 0 weight, not a fake score
        assert groups["entry"].weight == 0 and groups["entry"].score == 0

    def test_every_rule_reports_its_own_contribution_for_the_compass_wheel(self) -> None:
        report = evaluate(
            vastu_context(rooms=[room_in("N", "pooja_1", "pooja")]), root=RULEPACK_DIR
        )
        assert report.score is not None
        pooja = next(r for r in report.score.rules if r.rule_id == "vastu.pooja.zone")
        assert pooja.weight == 10
        assert pooja.satisfaction == Fraction(1, 2)
        assert pooja.to_json()["percent"] == 50
        assert pooja.group == "sacred"

    def test_no_applicable_rule_means_no_score_not_a_zero(self) -> None:
        """Zero would read as "terrible"; ``None`` reads as "not assessed"."""
        pack_set = load_pack_set(["vastu"], root=RULEPACK_DIR)
        vastu = pack_set.packs["vastu"]
        from garh_rules.scoring import build_score

        score = build_score(vastu, "advisory", [])
        assert score.score is None
        assert score.applicable_weight == 0
        assert score.total_weight == 100

    def test_the_pack_weights_sum_to_the_scale_maximum(self) -> None:
        pack_set = load_pack_set(["vastu"], root=RULEPACK_DIR)
        weights = [rule.weight or 0 for rule in pack_set.rules]
        assert sum(weights) == 100
        assert len(weights) == 9

    def test_score_survives_the_json_boundary(self) -> None:
        import json

        report = evaluate(
            vastu_context(rooms=[room_in("N", "pooja_1", "pooja")]), root=RULEPACK_DIR
        )
        encoded = json.loads(json.dumps(report.to_json()))
        assert encoded["vastuScore"] == 72
        assert encoded["scores"][0]["rules"][0]["satisfaction"] == {"num": 1, "den": 2}
