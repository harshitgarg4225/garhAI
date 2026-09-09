"""An override is carried into the area statement as a NAMED acknowledgement.

The annexure a drawing set carries quotes the area statement. A rule an architect
accepted with a reason must appear there as overridden — not silently green, not
silently missing. ``AreaStatement.overridden_rule_ids`` is that list, built from the
same results the rest of the statement is built from.

The negative control is the same design without the acknowledgement: the list must be
empty, or the field is decoration.
"""

from __future__ import annotations

from garh_rules import evaluate

from .conftest import RULEPACK_DIR, make_context, make_room

RULE = "nbc.room.habitable.area.min"


def _small_bedroom_context(overrides: dict[str, object] | None = None):
    # 2.0 × 3.0 m = 6.0 m², under the 9.5 m² habitable minimum: the rule fails.
    rooms = [make_room("r1", "bedroom", width=2000, depth=3000)]
    profile = {"overrides": overrides} if overrides is not None else None
    return make_context(packs=("nbc-core",), rooms=rooms, profile=profile)


def test_an_acknowledged_failure_is_named_in_the_area_statement() -> None:
    report = evaluate(
        _small_bedroom_context({RULE: {"reason": "Client-signed deviation, attached."}}),
        root=RULEPACK_DIR,
    )
    row = report.rule(RULE)
    assert row is not None and row.status == "fail" and row.overridden
    assert report.areas.overridden_rule_ids == (RULE,)
    assert report.to_json()["areas"]["overriddenRuleIds"] == [RULE]
    # The override un-blocks the gate but never leaves the failure list.
    assert RULE in {r.rule_id for r in report.failures()}
    assert RULE not in {r.rule_id for r in report.blocking_failures()}


def test_negative_control_no_override_means_an_empty_list() -> None:
    report = evaluate(_small_bedroom_context(), root=RULEPACK_DIR)
    row = report.rule(RULE)
    assert row is not None and row.status == "fail" and not row.overridden
    assert report.areas.overridden_rule_ids == ()
    assert report.to_json()["areas"]["overriddenRuleIds"] == []
