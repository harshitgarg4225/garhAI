"""Rule overrides, the frozen summary, and "a stale report is never served as current".

Golden rule 5: architects can override anything; overrides are logged. The engine has
supported ``{ruleId: {reason}}`` acknowledgements since Phase 2 and nothing wrote one.
These tests drive the two routes that now do, through HTTP, against the real op log,
the real engine and the real audit table, and pin the three facts the Compliance tab
relies on:

* the row stays in the report, marked ``overridden`` with the reason — never removed;
* who/when are the server's, and the §13 audit row is written for both directions;
* ``GET /compliance`` without ``?version`` answers for the WORKING STATE: a frozen
  report is served only while nothing has been appended since it was frozen.

Every positive assertion has its negative control in the same file.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from garh_api.repositories import AuditLogRepository
from garh_api.repositories.domain import NewOp

from tests import factories

pytestmark = pytest.mark.integration


async def _report(client: Any, api: str, actor: Any, project_id: uuid.UUID) -> dict[str, Any]:
    response = await client.get(
        "%s/projects/%s/compliance" % (api, project_id), headers=actor.headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["evaluated"] is True, body.get("reason")
    return body


def _row(report: dict[str, Any], rule_id: str) -> dict[str, Any]:
    rows = [r for r in report["results"] if r["ruleId"] == rule_id]
    assert len(rows) == 1, "expected exactly one row for %s, got %d" % (rule_id, len(rows))
    return rows[0]


def _a_rule_to_override(report: dict[str, Any]) -> str:
    """A failing rule if the demo design has one, else any rule that applied."""
    for status in ("fail", "warn", "pass"):
        for row in report["results"]:
            if row["status"] == status:
                return str(row["ruleId"])
    raise AssertionError("the demo design evaluated no applicable rule at all")


async def _audit_actions(session: Any, actor: Any, project_id: uuid.UUID) -> list[dict[str, Any]]:
    entries = await AuditLogRepository(session, actor.ctx()).list_for_entity("rule", project_id)
    return [{"action": e.action, "meta": dict(e.meta or {})} for e in entries]


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------


async def test_an_override_marks_the_row_with_the_reason_and_never_removes_it(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    await factories.seed_plot_and_brief(session, firm_a, project_a.id)
    before = await _report(client, api, firm_a, project_a.id)
    rule_id = _a_rule_to_override(before)
    # Negative control. The wire row omits `overridden` unless it is true
    # (RuleResult.to_json), which is itself what the tab relies on.
    assert _row(before, rule_id).get("overridden") is not True

    response = await client.post(
        "%s/projects/%s/compliance/overrides" % (api, project_a.id),
        json={"ruleId": rule_id, "reason": "  Client-signed   deviation, letter attached.  "},
        headers=firm_a.headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["ruleId"] == rule_id
    # Whitespace is normalised; the words are the architect's.
    assert body["reason"] == "Client-signed deviation, letter attached."
    assert body["byUserId"] == str(firm_a.user_id), "who is the server's, not the client's"
    assert body["byName"] == "Asha Rao"
    assert body["at"].endswith("Z")
    assert isinstance(body["headIdx"], int)

    after = await _report(client, api, firm_a, project_a.id)
    row = _row(after, rule_id)  # still there — an override never deletes a row
    assert row["status"] == _row(before, rule_id)["status"], "the real status is unchanged"
    assert row["overridden"] is True
    assert row["overrideReason"] == "Client-signed deviation, letter attached."
    assert after["areas"] is not None
    assert rule_id in after["areas"]["overriddenRuleIds"], "the annexure names it"
    assert after["counts"].get("overridden", 0) >= 1

    actions = await _audit_actions(session, firm_a, project_a.id)
    assert any(
        a["action"] == "compliance.overridden" and a["meta"].get("ruleId") == rule_id
        for a in actions
    ), actions


async def test_revoking_clears_the_mark_and_is_audited_separately(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    await factories.seed_plot_and_brief(session, firm_a, project_a.id)
    rule_id = _a_rule_to_override(await _report(client, api, firm_a, project_a.id))
    created = await client.post(
        "%s/projects/%s/compliance/overrides" % (api, project_a.id),
        json={"ruleId": rule_id, "reason": "Temporary, pending the structural note."},
        headers=firm_a.headers,
    )
    assert created.status_code == 201, created.text

    revoked = await client.delete(
        "%s/projects/%s/compliance/overrides/%s" % (api, project_a.id, rule_id),
        headers=firm_a.headers,
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json() == {
        "ruleId": rule_id,
        "revoked": True,
        "headIdx": revoked.json()["headIdx"],
    }
    assert revoked.json()["headIdx"] == created.json()["headIdx"] + 1

    report = await _report(client, api, firm_a, project_a.id)
    row = _row(report, rule_id)
    assert row.get("overridden") is not True
    assert row.get("overrideReason") in (None, "")
    assert rule_id not in report["areas"]["overriddenRuleIds"]

    actions = [a["action"] for a in await _audit_actions(session, firm_a, project_a.id)]
    assert "compliance.overridden" in actions
    assert "compliance.override_revoked" in actions

    # Revoking twice is a 404, not a silent success.
    again = await client.delete(
        "%s/projects/%s/compliance/overrides/%s" % (api, project_a.id, rule_id),
        headers=firm_a.headers,
    )
    assert again.status_code == 404, again.text
    assert again.json()["code"] == "override_not_found"


async def test_an_override_is_refused_for_a_rule_no_pack_carries_or_without_a_reason(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    await factories.seed_plot_and_brief(session, firm_a, project_a.id)
    url = "%s/projects/%s/compliance/overrides" % (api, project_a.id)

    unknown = await client.post(
        url, json={"ruleId": "blr.setback.front.99m", "reason": "typo"}, headers=firm_a.headers
    )
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["code"] == "unknown_rule"

    reserved = await client.post(
        url, json={"ruleId": "values", "reason": "not a rule"}, headers=firm_a.headers
    )
    assert reserved.status_code == 422, reserved.text

    blank = await client.post(
        url, json={"ruleId": "nbc.room.habitable.area.min", "reason": "   "}, headers=firm_a.headers
    )
    assert blank.status_code == 422, blank.text

    too_short = await client.post(
        url, json={"ruleId": "nbc.room.habitable.area.min", "reason": "ok"}, headers=firm_a.headers
    )
    assert too_short.status_code == 422, too_short.text

    # Nothing above touched the profile: the report has no override on anything.
    report = await _report(client, api, firm_a, project_a.id)
    assert not any(r.get("overridden") for r in report["results"])
    assert await _audit_actions(session, firm_a, project_a.id) == []


# ---------------------------------------------------------------------------
# The frozen summary, and never serving a stale frozen report as current
# ---------------------------------------------------------------------------


async def test_a_live_run_carries_the_area_statement_scores_warnings_and_pack_review(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    await factories.seed_plot_and_brief(session, firm_a, project_a.id)
    report = await _report(client, api, firm_a, project_a.id)
    assert report["live"] is True
    areas = report["areas"]
    assert areas is not None and areas["plotAreaMm2"] > 0
    assert areas["rows"], "the printable area statement rows travel with the report"
    assert any(row["key"] == "plot_area" for row in areas["rows"])
    assert report["disclaimers"], "every pack's disclaimer is stated"
    assert "nbc-core" in report["packReview"]
    assert report["packReview"]["nbc-core"]["status"] == "unreviewed"
    assert isinstance(report["warnings"], list)
    assert report["notes"], "the projection's approximations are stated"


async def test_a_frozen_report_is_served_only_while_it_matches_the_head(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    await factories.seed_plot_and_brief(session, firm_a, project_a.id)

    frozen = await client.post(
        "%s/projects/%s/versions" % (api, project_a.id),
        json={"name": "Submission draft"},
        headers=firm_a.headers,
    )
    assert frozen.status_code == 201, frozen.text
    version = frozen.json()
    assert version["opSeqEnd"] is not None

    # Nothing appended since the freeze: the frozen report IS the working state.
    current = await _report(client, api, firm_a, project_a.id)
    assert current["live"] is False
    assert current["reportId"] is not None
    assert current["designVersionId"] == version["id"]
    # …and it carries the statement it was frozen with, not a re-evaluation.
    assert current["areas"] is not None and current["areas"]["rows"]
    assert current["packReview"]["nbc-core"]["status"] == "unreviewed"
    assert current["disclaimers"]

    # One edit later the frozen report describes an older state. It must not be
    # served as the answer for the working state — this is the bug the fix is for.
    head_idx = await _head_idx(client, api, firm_a, project_a.id)
    await factories.append_ops(
        session,
        firm_a,
        project_a.id,
        [NewOp(type="brief.update", payload={"patch": {"budgetInr": 7_500_000}})],
        base_idx=head_idx,
    )
    after_edit = await _report(client, api, firm_a, project_a.id)
    assert after_edit["live"] is True, "a frozen report behind the head was served as current"
    assert after_edit["reportId"] is None

    # Asking for the version explicitly still returns its frozen report.
    by_version = await client.get(
        "%s/projects/%s/compliance?version=%s" % (api, project_a.id, version["id"]),
        headers=firm_a.headers,
    )
    assert by_version.status_code == 200, by_version.text
    assert by_version.json()["live"] is False
    assert by_version.json()["reportId"] == current["reportId"]


async def _head_idx(client: Any, api: str, actor: Any, project_id: uuid.UUID) -> int:
    response = await client.get("%s/projects/%s/model" % (api, project_id), headers=actor.headers)
    assert response.status_code == 200, response.text
    return int(response.json()["headIdx"])
