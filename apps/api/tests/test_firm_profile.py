"""Practice settings (J01): name, address, GSTIN, registration — and the title block.

The sign-up copy has promised "add your CoA later in firm settings" since the first
trial; there was no firm settings route. ``GET/PATCH /firm`` is that route. The
title-block fields the sheets print are NOT duplicated here: they already live behind
``PUT /firm/drawing-preferences`` (routers/sheets.py), and the one thing this router
shares with them is ``firms.name`` — asserted below, because a rename that did not
reach the drawing set would be the trust bug this surface exists to close.
"""

from __future__ import annotations

from typing import Any

import pytest
from garh_api.billing.gst import gstin_checksum

pytestmark = pytest.mark.integration

#: A GSTIN that passes shape, state code and check digit — computed, not typed.
_GSTIN_14 = "29AABCG1234H1Z"
VALID_GSTIN = _GSTIN_14 + gstin_checksum(_GSTIN_14)


async def _firm(client: Any, api: str, actor: Any) -> dict[str, Any]:
    response = await client.get("%s/firm" % api, headers=actor.headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_the_profile_starts_blank_and_a_patch_merges(
    client: Any, api: str, firm_a: Any
) -> None:
    before = await _firm(client, api, firm_a)
    assert before["name"] == firm_a.firm_name
    assert before["practice"] == {"address": "", "gstin": "", "registrationNumber": "", "phone": ""}

    first = await client.patch(
        "%s/firm" % api,
        json={
            "name": "Studio One LLP",
            "practice": {"address": "12 MG Road\nBengaluru 560001", "gstin": VALID_GSTIN.lower()},
        },
        headers=firm_a.headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["name"] == "Studio One LLP"
    assert first.json()["practice"]["gstin"] == VALID_GSTIN, "normalised to upper case"
    assert first.json()["practice"]["address"] == "12 MG Road\nBengaluru 560001"

    # A second patch naming only the registration leaves the rest alone.
    second = await client.patch(
        "%s/firm" % api,
        json={"practice": {"registrationNumber": "CA/2011/52345", "phone": "+91 98765 43210"}},
        headers=firm_a.headers,
    )
    assert second.status_code == 200, second.text
    practice = second.json()["practice"]
    assert practice["gstin"] == VALID_GSTIN
    assert practice["address"] == "12 MG Road\nBengaluru 560001"
    assert practice["registrationNumber"] == "CA/2011/52345"
    assert practice["phone"] == "+91 98765 43210"
    assert (await _firm(client, api, firm_a))["practice"] == practice

    # An explicit empty string clears a field.
    cleared = await client.patch(
        "%s/firm" % api, json={"practice": {"gstin": ""}}, headers=firm_a.headers
    )
    assert cleared.status_code == 200 and cleared.json()["practice"]["gstin"] == ""


async def test_a_wrong_gstin_is_refused_with_the_reason(client: Any, api: str, firm_a: Any) -> None:
    """The check digit is real: a one-character typo is caught before it reaches an invoice."""
    typo = VALID_GSTIN[:-1] + ("A" if VALID_GSTIN[-1] != "A" else "B")
    response = await client.patch(
        "%s/firm" % api, json={"practice": {"gstin": typo}}, headers=firm_a.headers
    )
    assert response.status_code == 422, response.text
    assert "check digit" in response.text.lower() or "gstin" in response.text.lower()
    # NEGATIVE CONTROL: the valid one goes through on the same code path.
    ok = await client.patch(
        "%s/firm" % api, json={"practice": {"gstin": VALID_GSTIN}}, headers=firm_a.headers
    )
    assert ok.status_code == 200, ok.text


async def test_only_an_admin_edits_the_practice_but_a_member_can_read_it(
    client: Any, api: str, firm_a: Any, member_a: Any
) -> None:
    denied = await client.patch(
        "%s/firm" % api, json={"name": "Hijacked"}, headers=member_a.headers
    )
    assert denied.status_code == 403, denied.text
    assert (await _firm(client, api, member_a))["name"] == firm_a.firm_name
    blank = await client.patch("%s/firm" % api, json={"name": "   "}, headers=firm_a.headers)
    assert blank.status_code in (400, 422), blank.text


async def test_a_rename_reaches_the_sheet_title_block(client: Any, api: str, firm_a: Any) -> None:
    """``load_drawing_preferences`` falls back to ``firms.name`` for ``firmName``."""
    before = await client.get("%s/firm/drawing-preferences" % api, headers=firm_a.headers)
    assert before.status_code == 200, before.text
    assert before.json()["titleBlock"]["firmName"] == firm_a.firm_name

    renamed = await client.patch(
        "%s/firm" % api, json={"name": "Iyer & Rao Architects"}, headers=firm_a.headers
    )
    assert renamed.status_code == 200, renamed.text

    after = await client.get("%s/firm/drawing-preferences" % api, headers=firm_a.headers)
    assert after.json()["titleBlock"]["firmName"] == "Iyer & Rao Architects"


async def test_a_profile_edit_is_audited(client: Any, api: str, firm_a: Any, session: Any) -> None:
    from garh_api.repositories.audit_log import ACTION_FIRM_SETTINGS_CHANGED, AuditLogRepository

    response = await client.patch(
        "%s/firm" % api,
        json={"name": "Audited Studio", "practice": {"phone": "080 2222 3333"}},
        headers=firm_a.headers,
    )
    assert response.status_code == 200, response.text
    page = await AuditLogRepository(session, firm_a.ctx()).list_recent(limit=20)
    rows = [entry for entry in page.items if entry.action == ACTION_FIRM_SETTINGS_CHANGED]
    assert rows, "no firm.settings_changed row was written"
    assert sorted(rows[0].meta["fields"]) == ["name", "phone"]
    assert "080 2222 3333" not in str(rows[0].meta), "the audit row names fields, not values"
    # NEGATIVE CONTROL: a no-op patch writes nothing.
    noop = await client.patch("%s/firm" % api, json={}, headers=firm_a.headers)
    assert noop.status_code == 200
    again = await AuditLogRepository(session, firm_a.ctx()).list_recent(limit=20)
    assert len([e for e in again.items if e.action == ACTION_FIRM_SETTINGS_CHANGED]) == 1


async def test_firm_b_never_sees_firm_a(client: Any, api: str, firm_a: Any, firm_b: Any) -> None:
    await client.patch(
        "%s/firm" % api, json={"practice": {"gstin": VALID_GSTIN}}, headers=firm_a.headers
    )
    other = await _firm(client, api, firm_b)
    assert other["id"] == str(firm_b.firm_id)
    assert other["practice"]["gstin"] == ""
    assert VALID_GSTIN not in str(other)
