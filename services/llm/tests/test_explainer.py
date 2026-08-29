"""B-9: a failed rule becomes an explanation that invents nothing.

The finding under test is built from the **real** ``rulepacks/blr.json`` entry, not from
invented prose, so the citation, the fix hint and the message template are the strings
an architect would actually see. If the pack's shape changes, these tests notice.

What each gate has to survive:

* a fabricated number → rejected, and the deterministic explanation is shown instead
  (``test_an_invented_number_is_rejected``). This is the one that matters: an architect
  acts on numbers, and the rules engine is the only thing entitled to produce one;
* a foreign rule id in the prose → rejected (``test_a_foreign_rule_id_is_rejected``);
* coordinates → rejected (``test_coordinates_are_rejected``): LLMs never emit geometry;
* a fact that was never supplied → rejected (``test_an_unsupplied_fact_is_rejected``).

And the citation is not gated at all, because it is never model-authored: it is copied
from the row. ``test_the_citation_is_never_model_authored`` proves that by handing the
model a chance to say otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.llm import redaction
from services.llm.explain_mock import synthesize_explanation
from services.llm.explainer import (
    ComplianceExplainer,
    NotExplainable,
    compose_explanation,
    finding_facts,
    verify_explanation,
)
from services.llm.mock import MockLlmProvider
from services.llm.prompts import compliance_explain_user, parse_finding
from services.llm.tests.doubles import RecordingProvider
from services.llm.types import LlmTask, LlmUnavailableError

REPO_ROOT = Path(__file__).resolve().parents[3]
RULE_ID = "blr.setback.front.plot.le120"


def pack_rule() -> dict[str, Any]:
    """The real Bengaluru front-setback rule, straight out of the pack."""
    with (REPO_ROOT / "rulepacks" / "blr.json").open(encoding="utf-8") as handle:
        pack = json.load(handle)
    for rule in pack["rules"]:
        if rule["id"] == RULE_ID:
            return dict(rule)
    raise AssertionError("%s is no longer in rulepacks/blr.json — update this test" % RULE_ID)


def a_finding(**overrides: Any) -> dict[str, Any]:
    """A rules-engine result row, shaped exactly like ``RuleResult.to_json``."""
    rule = pack_rule()
    row: dict[str, Any] = {
        "ruleId": rule["id"],
        "packId": "blr",
        "status": "fail",
        "severity": "fail",
        "checkType": "setback_min",
        "actual": 1_200,
        "limit": 1_500,
        "unit": "mm",
        "title": rule["title"],
        "message": "The front setback is 1.20 m - a plot of this size needs at least 1.50 m.",
        "cite": "BBMP Building Bye-laws 2003 - %s" % rule["cite"],
        "citeShort": rule["cite"],
        "fixHint": rule["fix"],
        "confidence": rule["confidence"],
        "elements": ["plot_edge_front"],
        "hard": True,
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# The deterministic floor
# ---------------------------------------------------------------------------


def test_composed_explanation_cites_the_rule_and_invents_nothing() -> None:
    finding = a_finding()
    explanation = compose_explanation(finding)

    assert explanation.rule_id == RULE_ID
    assert explanation.cite == finding["cite"]
    assert explanation.headline == finding["message"]
    assert explanation.source == "rules"
    assert explanation.confidence == "seed", "the UI must keep showing seed confidence"
    assert finding["fixHint"] in explanation.fixes
    # Every number it prints is one the engine produced.
    assert (
        verify_explanation(
            explanation.body,
            explanation.fixes,
            (),
            explanation.facts,
            rule_id=RULE_ID,
        )
        == ()
    )


def test_a_passing_rule_is_not_explainable() -> None:
    """Explaining a pass would mean writing a reason nobody computed."""
    with pytest.raises(NotExplainable):
        compose_explanation(a_finding(status="pass"))
    with pytest.raises(NotExplainable):
        compose_explanation(a_finding(status="not_applicable"))


def test_a_warning_is_explainable_and_says_it_does_not_block() -> None:
    explanation = compose_explanation(a_finding(status="warn", severity="warn"))
    assert explanation.status == "warn"
    assert "does not block" in explanation.body


# ---------------------------------------------------------------------------
# The allowlist: what the model is allowed to be told
# ---------------------------------------------------------------------------


def test_the_prompt_carries_no_user_authored_text() -> None:
    """A finding row can pick up user text; the projection is what stops it."""
    leaky = a_finding(
        clientName="Ramesh Kumar",
        notes="call 9876543210",
        instances=[{"label": "Ramesh Kumar bedroom", "elementId": "room_1"}],
        elements=["plot_edge_front"],
    )
    prompt = compliance_explain_user(
        redaction.pick(leaky, redaction.EXPLAINER_FINDING_FIELDS), authority="BBMP"
    )
    for needle in ("Ramesh", "9876543210", "Ramesh Kumar bedroom"):
        assert needle not in prompt
    # Non-vacuous: the substance really is there.
    assert RULE_ID in prompt
    assert "1.20 m" in prompt


def test_the_explainer_allowlist_is_covered_by_the_import_time_gate() -> None:
    """§13's gate must actually police the field list B-9 added, not just the old ones."""
    original = redaction._SUMMARY_ALLOWLISTS
    redaction._SUMMARY_ALLOWLISTS = (("EXPLAINER_FINDING_FIELDS", ("ruleId", "clientName")),)
    try:
        with pytest.raises(RuntimeError) as caught:
            redaction.check_allowlists_are_pii_free()
        assert "clientName" in str(caught.value)
    finally:
        redaction._SUMMARY_ALLOWLISTS = original
    # And the real list passes.
    redaction.check_allowlists_are_pii_free()


def test_prompt_and_parser_cannot_drift_apart() -> None:
    """The mock reads the finding back out of the prompt; that round trip is a contract."""
    row = redaction.pick(a_finding(), redaction.EXPLAINER_FINDING_FIELDS)
    assert parse_finding(compliance_explain_user(row)) == row
    assert parse_finding("no finding here") is None


# ---------------------------------------------------------------------------
# The verifier
# ---------------------------------------------------------------------------


def test_an_invented_number_is_caught() -> None:
    facts = finding_facts(a_finding())
    problems = verify_explanation(
        "Your front setback is 1.2 m and the bye-law wants 3 m.", (), (), facts, rule_id=RULE_ID
    )
    assert any("3" in problem and "did not produce" in problem for problem in problems)


def test_a_supplied_number_written_differently_is_accepted() -> None:
    """1.5 and the engine's 1.50 are the same number; rejecting that weakens the gate."""
    facts = finding_facts(a_finding())
    assert (
        verify_explanation(
            "The front setback is 1.2 m where 1.5 m is required.", (), (), facts, rule_id=RULE_ID
        )
        == ()
    )


def test_a_foreign_rule_id_is_caught() -> None:
    facts = finding_facts(a_finding())
    problems = verify_explanation(
        "See also ncr.setback.rear.plot for the rear edge.", (), (), facts, rule_id=RULE_ID
    )
    assert any("named a rule other than" in problem for problem in problems)


def test_coordinates_are_caught() -> None:
    facts = finding_facts(a_finding())
    problems = verify_explanation("Move the wall to (1200, 4500).", (), (), facts, rule_id=RULE_ID)
    assert any("never emits coordinates" in problem for problem in problems)


def test_an_unsupplied_fact_is_caught() -> None:
    facts = finding_facts(a_finding())
    problems = verify_explanation(
        "The setback is short.", (), ("BBMP charges a compounding fee",), facts, rule_id=RULE_ID
    )
    assert any("not supplied" in problem for problem in problems)


def test_an_over_long_explanation_is_caught() -> None:
    facts = finding_facts(a_finding())
    problems = verify_explanation(" ".join(["setback"] * 200), (), (), facts, rule_id=RULE_ID)
    assert any("over the" in problem for problem in problems)


# ---------------------------------------------------------------------------
# End to end on the mock — the zero-key path
# ---------------------------------------------------------------------------


async def test_the_mock_produces_a_verified_explanation() -> None:
    explanation = await ComplianceExplainer(MockLlmProvider()).explain(a_finding())

    assert explanation.source == "model", explanation.problems
    assert explanation.problems == ()
    assert explanation.rule_id == RULE_ID
    assert "1.20 m" in explanation.body and "1.50 m" in explanation.body
    assert explanation.fixes
    assert explanation.cite == a_finding()["cite"]


async def test_the_mock_answers_the_actual_numbers_not_a_canned_pair() -> None:
    """A pinned fixture would be wrong on the next plot. This proves it is not pinned."""
    other = await ComplianceExplainer(MockLlmProvider()).explain(
        a_finding(
            actual=900,
            limit=2_000,
            message="The front setback is 0.90 m - a plot of this size needs at least 2 m.",
        )
    )
    assert "0.90 m" in other.body
    assert "1.20 m" not in other.body
    assert other.source == "model"


async def test_an_invented_number_falls_back_to_the_engines_own_words() -> None:
    """The whole point: a fabricated compliance number never reaches an architect."""
    provider = RecordingProvider(
        [
            {
                "factsUsed": list(finding_facts(a_finding()))[:1],
                "explanation": "Your front setback is 1.2 m and Bengaluru wants 3 m.",
                "fixes": ["Move the wall back by 1.8 m."],
            }
        ]
    )
    explanation = await ComplianceExplainer(provider).explain(a_finding())

    assert explanation.source == "rules"
    assert any("did not produce" in problem for problem in explanation.problems)
    assert "3 m" not in explanation.body
    assert explanation.headline == a_finding()["message"]


async def test_the_citation_is_never_model_authored() -> None:
    provider = RecordingProvider(
        [
            {
                "factsUsed": list(finding_facts(a_finding()))[:1],
                "explanation": "The front setback is short of what the bye-law allows.",
                "fixes": ["Pull the building line back."],
            }
        ]
    )
    explanation = await ComplianceExplainer(provider).explain(a_finding())
    assert explanation.source == "model"
    # The model wrote no citation and could not have; the row supplied it.
    assert explanation.cite == a_finding()["cite"]
    assert explanation.cite_short == a_finding()["citeShort"]
    assert "cite" not in json.dumps(provider.payloads)


async def test_a_provider_outage_still_produces_a_cited_explanation() -> None:
    class DeadProvider:
        name = "dead"
        model = "none"

        async def complete_json(self, task: LlmTask) -> Any:
            raise LlmUnavailableError("down")

        async def aclose(self) -> None:
            return None

    explanation = await ComplianceExplainer(DeadProvider()).explain(a_finding())
    assert explanation.source == "rules"
    assert explanation.cite == a_finding()["cite"]
    assert explanation.body
    assert explanation.problems == ("provider unavailable: llm_unavailable",)


async def test_explaining_a_passing_rule_raises_rather_than_calling_the_model() -> None:
    provider = RecordingProvider([])
    with pytest.raises(NotExplainable):
        await ComplianceExplainer(provider).explain(a_finding(status="pass"))
    assert provider.tasks == []


def test_the_synthesizer_only_uses_supplied_facts() -> None:
    """Checked directly, so a mock that starts inventing fails here and not in a demo."""
    row = redaction.pick(a_finding(), redaction.EXPLAINER_FINDING_FIELDS)
    answer = synthesize_explanation(compliance_explain_user(row))
    problems = verify_explanation(
        answer["explanation"],
        answer["fixes"],
        answer["factsUsed"],
        finding_facts(row),
        rule_id=RULE_ID,
    )
    assert problems == ()


def test_the_synthesizer_degrades_honestly_on_an_unreadable_prompt() -> None:
    answer = synthesize_explanation("nothing that looks like a finding")
    problems = verify_explanation(
        answer["explanation"],
        answer["fixes"],
        answer["factsUsed"],
        finding_facts(a_finding()),
        rule_id=RULE_ID,
    )
    assert problems, "an unreadable prompt must not yield a confident answer"
