"""G-5 — the fee and area estimate (``GET /projects/:id/estimate``).

An architect quotes before they design. This endpoint is the only place in the product
that answers "what can I build here, what will it cost, what do I charge" from the plot,
the brief and the loaded packs, and it is the only place that returns money.

Three things this file has to prove, and they map onto three of the four failure classes
in ``CLAUDE.md``:

1. **It is an estimator, not a constant.** A 30×40 ft plot and a 40×60 ft plot must
   produce different envelopes, different ceilings and different rupees, and a brief for
   G+1 must cost less than the same plot at G+3. Anything that returns one number for
   every input is a placeholder wearing an endpoint's clothes.
2. **The compliance numbers have ONE source.** ``CLAUDE.md``: "Two sources of truth for
   FAR is a liability bug in a product selling citable compliance." So the FAR, coverage,
   height and setback numbers are asserted equal to what the rules engine itself
   produced — first against ``evaluate_document`` in-process, then across two HTTP
   endpoints, comparing the estimate against the raw rule results ``GET /compliance``
   serves. Recompute a ratio inside the estimator and both go red.
3. **The binding constraint really binds.** Bug pattern 1 is a cap whose denominator
   makes it unreachable. The estimator has four such caps — coverage vs envelope on the
   ground floor, FAR vs stacked envelope on the total — and this file drives real plots
   through the real ``blr`` pack until each of the four has been observed to fire.

Most of the file needs no datastore: ``build_estimate`` is a pure function of a folded
document, so the plots are folded in-process. The ``integration`` marks are on the HTTP
tests only.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from fractions import Fraction
from typing import Any

import pytest
from garh_api.compliance import evaluate_document
from garh_api.estimator import (
    CONSTRUCTION_RATES,
    ESTIMATE_CONFIDENCE,
    FEE_SCALES,
    MM2_PER_SQFT,
    build_estimate,
    construction_cost_inr,
    fee_inr,
    round_half_away,
)
from garh_model import apply_group, empty_project_doc, to_jsonable

from tests import factories
from tests.helpers import problem

#: 30 × 40 ft and 40 × 60 ft in integer mm — the two plot sizes an Indian residential
#: architect quotes on most often, and the pair the task names.
PLOT_30X40 = (9144, 12192)
PLOT_40X60 = (12192, 18288)


def _rect(width_mm: int, depth_mm: int) -> list[dict[str, int]]:
    """CCW rectangle from the plot's SW corner. Edge 0 is the road edge (south)."""
    return [
        {"x": 0, "y": 0},
        {"x": width_mm, "y": 0},
        {"x": width_mm, "y": depth_mm},
        {"x": 0, "y": depth_mm},
    ]


def _op_log(
    size: tuple[int, int], *, floors_above_ground: int = 1, road_mm: int = 9000
) -> list[dict[str, Any]]:
    """The demo project's own op log with the plot, road and storey count varied.

    Built from ``seed.demo.demo_op_log`` rather than hand-typed so these documents
    cannot drift into a shape the product could not produce — and so the brief carries
    the real room program, which is what makes the estimate a *brief* estimate.
    The modelled storeys are dropped: this endpoint's whole point is answering before
    anything is designed, so the brief must be what drives the storey count.
    """
    from garh_api.seed.demo import demo_op_log, load_demo_brief

    ops = [op for op in demo_op_log(load_demo_brief()) if op["type"] != "storey.add"]
    for op in ops:
        if op["type"] == "plot.set_boundary":
            op["payload"] = {**op["payload"], "polygon": _rect(*size)}
        elif op["type"] == "plot.set_road":
            op["payload"] = {**op["payload"], "widthMm": road_mm}
        elif op["type"] == "brief.update":
            patch = {**op["payload"]["patch"], "floorsAboveGround": floors_above_ground}
            op["payload"] = {**op["payload"], "patch": patch}
    return ops


def _document(
    size: tuple[int, int], *, floors_above_ground: int = 1, road_mm: int = 9000
) -> dict[str, Any]:
    """Fold an op log into a ProjectDoc, in-process. No database, no HTTP."""
    ops = _op_log(size, floors_above_ground=floors_above_ground, road_mm=road_mm)
    return to_jsonable(apply_group(empty_project_doc(), ops, "estimate-fixture").model)


def _estimate(size: tuple[int, int], **kwargs: Any) -> dict[str, Any]:
    return build_estimate(_document(size, **kwargs), city_pack="blr").to_json()


def _standard(estimate: Mapping[str, Any]) -> Mapping[str, Any]:
    return next(band for band in estimate["costs"] if band["tier"] == "standard")


# ---------------------------------------------------------------------------
# It is an estimator
# ---------------------------------------------------------------------------


def test_a_bigger_plot_gives_a_bigger_estimate_everywhere() -> None:
    """The negative control the task names: one number for two plots is not an estimator.

    Every derived quantity is compared, not just the headline: a stub that varied only
    the plot area while holding the envelope, the ceiling and the money constant would
    pass a laxer version of this test.
    """
    small = _estimate(PLOT_30X40)
    large = _estimate(PLOT_40X60)

    assert small["plot"]["areaMm2"] < large["plot"]["areaMm2"]
    assert small["envelope"]["areaMm2"] < large["envelope"]["areaMm2"]
    assert small["buildable"]["maxGroundFloorAreaMm2"] < large["buildable"]["maxGroundFloorAreaMm2"]
    assert small["buildable"]["maxBuiltUpAreaMm2"] < large["buildable"]["maxBuiltUpAreaMm2"]
    assert small["basis"]["areaMm2"] < large["basis"]["areaMm2"]

    for small_band, large_band in zip(small["costs"], large["costs"], strict=True):
        assert small_band["tier"] == large_band["tier"]
        assert small_band["lowInr"] < large_band["lowInr"], small_band["tier"]
        assert small_band["highInr"] < large_band["highInr"], small_band["tier"]
        for small_fee, large_fee in zip(small_band["fees"], large_band["fees"], strict=True):
            assert small_fee["scope"] == large_fee["scope"]
            assert small_fee["lowInr"] < large_fee["lowInr"], small_fee["scope"]
            assert small_fee["highInr"] < large_fee["highInr"], small_fee["scope"]


def test_the_brief_moves_the_price_not_just_the_plot() -> None:
    """Same plot, more floors, more money — the estimate is of the *briefed* house."""
    one = _estimate(PLOT_30X40, floors_above_ground=1)
    two = _estimate(PLOT_30X40, floors_above_ground=2)

    assert one["basis"]["storeys"] == 2, "G+1 is two storeys"
    assert two["basis"]["storeys"] == 3
    assert one["basis"]["source"] == "brief"
    assert two["basis"]["areaMm2"] > one["basis"]["areaMm2"]
    assert _standard(two)["lowInr"] > _standard(one)["lowInr"]

    # The plot did NOT change, so nothing regulatory may move with the brief.
    assert one["plot"] == two["plot"]
    assert one["envelope"]["areaMm2"] == two["envelope"]["areaMm2"]
    assert one["limits"] == two["limits"]


def test_a_brief_beyond_the_bye_law_is_priced_at_what_is_permitted() -> None:
    """G+8 on a Bengaluru residential plot is not a quote, it is a warning."""
    estimate = _estimate(PLOT_30X40, floors_above_ground=8)
    permitted = estimate["limits"]["maxStoreys"]

    assert permitted == 4, "the seeded blr pack allows 4 storeys off a 9 m road"
    assert estimate["basis"]["storeys"] == permitted
    assert any("permit" in warning for warning in estimate["warnings"]), estimate["warnings"]
    # And it must not price the nine storeys the brief asked for.
    assert estimate["basis"]["areaMm2"] <= (estimate["buildable"]["maxBuiltUpAreaMm2"] or 0)


def test_two_caps_at_once_produce_two_distinct_warnings() -> None:
    """40×60 ft, G+8 briefed: the storey cap bites AND then FAR bites on top of it.

    Both sentences must reach the architect, and neither twice — ``warnings`` is a list
    the UI renders verbatim, and a running note re-appended to it reads as a bug.
    """
    estimate = _estimate(PLOT_40X60, floors_above_ground=8)

    assert estimate["basis"]["storeys"] == 4
    assert estimate["basis"]["areaMm2"] == estimate["limits"]["farAllowedMm2"]
    assert len(estimate["warnings"]) == len(set(estimate["warnings"])), estimate["warnings"]
    assert sum("permit" in w for w in estimate["warnings"]) == 1, estimate["warnings"]
    assert sum("FAR allowance caps" in w for w in estimate["warnings"]) == 1, estimate["warnings"]
    # The note is the full story; the warnings are its sentences.
    for warning in estimate["warnings"]:
        assert warning in estimate["basis"]["note"]


# ---------------------------------------------------------------------------
# One source for the compliance numbers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [PLOT_30X40, PLOT_40X60], ids=["30x40", "40x60"])
def test_every_regulatory_number_is_the_rules_engines_own(size: tuple[int, int]) -> None:
    """Byte-for-byte equality with the area statement — no second FAR, ever.

    This is the test that goes red the moment someone "optimises" the estimator by
    computing ``plotArea × 2.25`` locally. It is the whole reason the module calls
    ``evaluate_document`` instead.
    """
    document = _document(size)
    report, _packs = evaluate_document(document, city_pack="blr")
    areas = report["areas"]
    estimate = build_estimate(document, city_pack="blr").to_json()

    assert estimate["plot"]["areaMm2"] == areas["plotAreaMm2"]
    assert estimate["limits"]["farAllowedMm2"] == areas["farAllowedMm2"]
    assert estimate["limits"]["coverageAllowedMm2"] == areas["coverageAllowedMm2"]
    assert estimate["limits"]["maxStoreys"] == areas["floorsAllowed"]
    assert estimate["limits"]["maxHeightMm"] == areas["heightAllowedMm"]

    engine_setbacks = {row["edgeIndex"]: row for row in areas["setbacks"]}
    assert len(estimate["envelope"]["setbacks"]) == len(engine_setbacks)
    for row in estimate["envelope"]["setbacks"]:
        engine = engine_setbacks[row["edgeIndex"]]
        assert row["role"] == engine["role"]
        assert row["requiredMm"] == (engine["requiredMm"] or 0)
        assert row["regulated"] is (engine["requiredMm"] is not None)
        assert row["ruleIds"] == engine["ruleIds"], "the citation must survive to the quote"


def test_the_allowances_follow_the_road_width_band() -> None:
    """Same plot, three road widths, three different bye-law answers.

    Equality against ``evaluate_document`` proves the estimator did not *drift* from the
    engine, but it cannot by itself catch a recomputation that happens to agree today —
    a hardcoded "FAR is 2.25 in Bengaluru" would pass it. This does catch that: in the
    seeded blr pack FAR, height and storeys are all banded on the abutting road width,
    so a value that does not move with the road was never read from the packs at all.
    """
    narrow = _estimate(PLOT_30X40, road_mm=6000)  # < 9 m band
    medium = _estimate(PLOT_30X40, road_mm=9000)  # 9–18 m band
    wide = _estimate(PLOT_30X40, road_mm=18000)  # ≥ 18 m band

    plot_area = narrow["plot"]["areaMm2"]
    assert medium["plot"]["areaMm2"] == wide["plot"]["areaMm2"] == plot_area

    far = [e["limits"]["farAllowedMm2"] for e in (narrow, medium, wide)]
    assert far == sorted(far) and len(set(far)) == 3, far
    # The pack's own seeded ratios: 1.50 / 2.25 / 3.00, floored against the plot area.
    assert far == [plot_area * 150 // 100, plot_area * 225 // 100, plot_area * 300 // 100]

    assert [e["limits"]["maxHeightMm"] for e in (narrow, medium, wide)] == [11500, 15000, 18000]
    assert [e["limits"]["maxStoreys"] for e in (narrow, medium, wide)] == [3, 4, 5]

    # And the front setback, which is banded on road width too, reaches the envelope.
    fronts = [
        next(row["requiredMm"] for row in e["envelope"]["setbacks"] if row["role"] == "front")
        for e in (narrow, medium, wide)
    ]
    assert fronts == [1500, 3000, 4500]
    assert (
        narrow["envelope"]["areaMm2"] > wide["envelope"]["areaMm2"]
    ), "a deeper front setback must shrink the buildable envelope"


def test_the_allowances_are_cited_by_rule_id() -> None:
    """A number an architect can be asked to defend has to name the rule it came from."""
    estimate = _estimate(PLOT_30X40)
    rule_ids = estimate["limits"]["ruleIds"]
    assert rule_ids["far"], "no rule cited for the FAR allowance"
    assert rule_ids["coverage"]
    assert all(rid.startswith("blr.") or rid.startswith("nbc") for rid in rule_ids["far"])
    assert estimate["packVersions"], "the estimate must record which pack versions it used"


# ---------------------------------------------------------------------------
# The buildable envelope, and the caps that bind it
# ---------------------------------------------------------------------------


def test_the_envelope_is_the_boundary_offset_by_the_required_setbacks() -> None:
    """Exact geometry, not "roughly".

    The seeded blr pack on a 30×40 ft plot off a 9 m road requires 3.0 m at the front
    and 1.0 m on the other three edges, so the envelope is 7144 × 8192 mm. That is the
    same 58.5 m² the demo seed's own feasibility note quotes, which is the cross-check
    that this is the envelope the solver packs rooms into and not a second opinion.
    """
    estimate = _estimate(PLOT_30X40)
    required = {row["role"]: row["requiredMm"] for row in estimate["envelope"]["setbacks"]}
    assert required == {"front": 3000, "side-a": 1000, "rear": 1000, "side-b": 1000}

    assert estimate["envelope"]["polygonMm"] == [
        {"x": 1000, "y": 3000},
        {"x": 8144, "y": 3000},
        {"x": 8144, "y": 11192},
        {"x": 1000, "y": 11192},
    ]
    assert estimate["envelope"]["areaMm2"] == 7144 * 8192 == 58_523_648
    assert estimate["envelope"]["note"] is None


def test_setbacks_that_consume_the_plot_yield_no_envelope() -> None:
    """A 3 m × 3 m plot with a 3 m front setback cannot be built on. Say so."""
    estimate = _estimate((3000, 3000))

    assert estimate["envelope"]["polygonMm"] is None, "never an empty polygon — null"
    assert estimate["envelope"]["areaMm2"] == 0
    assert "no buildable area" in (estimate["envelope"]["note"] or "")
    assert estimate["buildable"]["maxGroundFloorAreaMm2"] == 0
    for band in estimate["costs"]:
        assert band["lowInr"] == 0 and band["highInr"] == 0
        assert all(fee["lowInr"] == 0 and fee["highInr"] == 0 for fee in band["fees"])


def test_both_ground_floor_caps_actually_bind_on_real_plots() -> None:
    """Bug pattern 1: a cap that can never be reached is not a cap.

    Neither branch is asserted from a hand-built fixture — both are driven by real plot
    sizes through the real pack, so if the coverage table or the setback table changes
    such that one branch becomes unreachable, this test says so.
    """
    envelope_bound = _estimate(PLOT_30X40)
    assert envelope_bound["buildable"]["groundFloorBinding"] == "envelope"
    assert (
        envelope_bound["buildable"]["maxGroundFloorAreaMm2"]
        == envelope_bound["envelope"]["areaMm2"]
    )
    assert (
        envelope_bound["buildable"]["maxGroundFloorAreaMm2"]
        < envelope_bound["limits"]["coverageAllowedMm2"]
    )

    # 30 m × 30 m = 900 m²: >500 m² band, so 55% coverage against a 598 m² envelope.
    coverage_bound = _estimate((30_000, 30_000))
    assert coverage_bound["buildable"]["groundFloorBinding"] == "coverage"
    assert (
        coverage_bound["buildable"]["maxGroundFloorAreaMm2"]
        == coverage_bound["limits"]["coverageAllowedMm2"]
    )
    assert (
        coverage_bound["buildable"]["maxGroundFloorAreaMm2"] < coverage_bound["envelope"]["areaMm2"]
    )


def test_both_built_up_caps_actually_bind_on_real_plots() -> None:
    """The other half of bug pattern 1: FAR and the stacked envelope must each win once."""
    envelope_bound = _estimate(PLOT_30X40)
    assert envelope_bound["buildable"]["builtUpBinding"] == "envelope"
    assert (
        envelope_bound["buildable"]["maxBuiltUpAreaMm2"]
        == envelope_bound["buildable"]["maxGroundFloorAreaMm2"]
        * envelope_bound["limits"]["maxStoreys"]
    )
    assert (
        envelope_bound["buildable"]["maxBuiltUpAreaMm2"] < envelope_bound["limits"]["farAllowedMm2"]
    )

    far_bound = _estimate(PLOT_40X60)
    assert far_bound["buildable"]["builtUpBinding"] == "far"
    assert far_bound["buildable"]["maxBuiltUpAreaMm2"] == far_bound["limits"]["farAllowedMm2"]
    assert (
        far_bound["buildable"]["maxBuiltUpAreaMm2"]
        < far_bound["buildable"]["maxGroundFloorAreaMm2"] * far_bound["limits"]["maxStoreys"]
    )


# ---------------------------------------------------------------------------
# The money
# ---------------------------------------------------------------------------


def test_every_money_number_is_whole_rupees() -> None:
    """The catalogue convention. A float rupee is a rupee that eventually prints wrong."""
    estimate = _estimate(PLOT_40X60)
    for band in estimate["costs"]:
        for value in (band["lowInr"], band["highInr"]):
            assert isinstance(value, int) and not isinstance(value, bool), band["tier"]
        assert band["lowInr"] < band["highInr"]
        for fee in band["fees"]:
            for value in (fee["lowInr"], fee["highInr"], fee["lowPercentX100"]):
                assert isinstance(value, int) and not isinstance(value, bool), fee["scope"]
            assert fee["lowInr"] < fee["highInr"]
            assert (
                fee["highInr"] < band["lowInr"]
            ), "a professional fee above the whole construction cost is a decimal bug"


def test_fees_are_exactly_their_percentage_of_the_construction_cost() -> None:
    """Recomputed here in exact rationals, so a drifted rounding rule shows up."""
    estimate = _estimate(PLOT_40X60)
    for band in estimate["costs"]:
        for fee in band["fees"]:
            assert fee["lowInr"] == round_half_away(
                Fraction(band["lowInr"] * fee["lowPercentX100"], 10_000)
            )
            assert fee["highInr"] == round_half_away(
                Fraction(band["highInr"] * fee["highPercentX100"], 10_000)
            )


def test_rupee_arithmetic_is_exact_and_rounds_half_away_from_zero() -> None:
    """The repo's rounding contract, on money.

    100 sqft is exactly 9,290,304 mm², so this multiplication has no remainder at all and
    any float in the chain would show. The half cases pin the direction: ``round()``
    would give 0 and 2 here (banker's), and this codebase forbids that.
    """
    hundred_sqft_mm2 = 9_290_304
    assert Fraction(hundred_sqft_mm2) / MM2_PER_SQFT == 100
    assert construction_cost_inr(hundred_sqft_mm2, 2000) == 200_000
    assert fee_inr(200_000, 500) == 10_000

    assert round_half_away(Fraction(1, 2)) == 1
    assert round_half_away(Fraction(5, 2)) == 3
    assert round_half_away(Fraction(-1, 2)) == -1
    assert construction_cost_inr(0, 2000) == 0
    assert fee_inr(0, 500) == 0


def test_every_authored_number_is_marked_seed_and_disclaimed() -> None:
    """The rule packs' honesty convention, applied to numbers we made up ourselves.

    A rupee figure with no confidence marker is a rupee figure someone will paste into a
    contract.
    """
    estimate = _estimate(PLOT_30X40)
    assert estimate["confidence"] == ESTIMATE_CONFIDENCE == "seed"
    assert "Indicative only" in estimate["disclaimer"]

    assert len(estimate["costs"]) == len(CONSTRUCTION_RATES)
    for band in estimate["costs"]:
        assert band["confidence"] == "seed", band["tier"]
        assert band["ratePerSqftLowInr"] < band["ratePerSqftHighInr"]
        assert band["description"]
        assert len(band["fees"]) == len(FEE_SCALES)
        for fee in band["fees"]:
            assert fee["confidence"] == "seed", fee["scope"]
            assert fee["basis"], "a fee band with no stated basis cannot be defended"

    # The one row anchored on a real published scale must say so by name.
    comprehensive = next(
        fee for fee in estimate["costs"][0]["fees"] if fee["scope"] == "comprehensive"
    )
    assert "Council of Architecture" in comprehensive["basis"]


def test_the_tables_themselves_are_ordered_and_sane() -> None:
    """Guards the tables at import time rather than through one sampled estimate."""
    tiers = [rate.low_inr_per_sqft for rate in CONSTRUCTION_RATES]
    assert tiers == sorted(tiers), "finish tiers must be listed cheapest first"
    scopes = [scale.low_percent_x100 for scale in FEE_SCALES]
    assert scopes == sorted(scopes), "fee scopes must be listed narrowest first"
    assert {scale.scope for scale in FEE_SCALES} == {"concept", "submission", "comprehensive"}


# ---------------------------------------------------------------------------
# Through HTTP
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_the_route_serves_the_whole_estimate(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    """Every field a client reads must survive the response model.

    ``ResponseModel`` ignores extras, so a key renamed on one side of the schema mirror
    would vanish silently instead of erroring — this is the assertion that catches it.
    """
    await factories.seed_plot_and_brief(session, firm_a, project_a.id)

    response = await client.get(
        "%s/projects/%s/estimate" % (api, project_a.id), headers=firm_a.headers
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["projectId"] == str(project_a.id)
    assert body["plot"]["areaMm2"] == 9144 * 12192
    assert body["plot"]["areaSqftX100"] > 0
    assert body["envelope"]["areaMm2"] == 7144 * 8192
    assert len(body["envelope"]["polygonMm"]) == 4
    assert body["envelope"]["setbacks"][0]["requiredMm"] == 3000
    assert body["envelope"]["setbacks"][0]["ruleIds"]
    assert body["limits"]["farAllowedMm2"] > 0
    assert body["limits"]["maxStoreys"] == 4
    assert body["buildable"]["groundFloorBinding"] == "envelope"
    assert body["buildable"]["builtUpBinding"] == "envelope"
    assert body["basis"]["storeys"] == 2 and body["basis"]["source"] == "model"
    assert body["basis"]["note"]
    assert body["confidence"] == "seed"
    assert body["disclaimer"]
    assert body["packVersions"]

    standard = _standard(body)
    assert standard["lowInr"] > 0
    assert {fee["scope"] for fee in standard["fees"]} == {
        "concept",
        "submission",
        "comprehensive",
    }
    assert all(fee["lowInr"] > 0 for fee in standard["fees"])


@pytest.mark.integration
async def test_the_estimate_and_the_compliance_tab_quote_the_same_far(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    """One source, asserted across two endpoints and against the raw rule results.

    ``GET /compliance`` serves the engine's per-rule verdicts; the estimate serves the
    allowance. They must be the same number, and the estimate's cited rule ids must be
    the rules that produced it. If the estimator ever grows its own FAR arithmetic this
    is the test that notices.
    """
    await factories.seed_plot_and_brief(session, firm_a, project_a.id)

    compliance = await client.get(
        "%s/projects/%s/compliance" % (api, project_a.id), headers=firm_a.headers
    )
    assert compliance.status_code == 200, compliance.text
    results = compliance.json()["results"]
    far_results = [
        row
        for row in results
        if row.get("checkType") == "far_max" and row.get("status") != "not_applicable"
    ]
    assert far_results, "the blr pack must apply a FAR rule to this plot"

    estimate = await client.get(
        "%s/projects/%s/estimate" % (api, project_a.id), headers=firm_a.headers
    )
    assert estimate.status_code == 200, estimate.text
    body = estimate.json()

    assert body["limits"]["farAllowedMm2"] == min(row["limit"] for row in far_results), (
        "the estimate's FAR allowance differs from the compliance tab's — two sources "
        "of truth for FAR is the liability bug CLAUDE.md names"
    )
    assert set(body["limits"]["ruleIds"]["far"]) == {row["ruleId"] for row in far_results}


@pytest.mark.integration
async def test_estimating_before_the_plot_is_drawn_says_what_to_do(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    """The common case — asked on day one — must name the missing input, not return zero."""
    response = await client.get(
        "%s/projects/%s/estimate" % (api, project_a.id), headers=firm_a.headers
    )
    assert response.status_code == 409, response.text
    body = problem(response)
    assert body["code"] == "no_plot_boundary", body
    assert "Plot tab" in body["action"], body


@pytest.mark.integration
async def test_an_unknown_project_is_404_not_a_zero_estimate(
    client: Any, api: str, firm_a: Any
) -> None:
    response = await client.get(
        "%s/projects/%s/estimate" % (api, uuid.uuid4()), headers=firm_a.headers
    )
    assert response.status_code == 404, response.text
    assert problem(response)["code"] == "not_found"
