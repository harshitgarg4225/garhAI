"""The Team page's other half (J01): roles, removal, and your own profile.

The load-bearing test is :func:`test_removing_a_member_ends_their_live_sessions`. A
removed colleague holding a 15-minute bearer must be out NOW, not at expiry — so the
member signs in through the real flow (a token minted by a factory has no family to
revoke and would prove nothing), is shown to be in, is removed, and is shown to be
out on both the access token and the refresh cookie.

Every "never" in the route table carries a negative control here: never the last
admin (demote and remove), never yourself, never by a member.
"""

from __future__ import annotations

from typing import Any

import pytest
from garh_api.ratelimit import otp_resend_identity, otp_resend_rule, reset_rate_limit
from garh_api.security import REFRESH_COOKIE_NAME, pseudonymise

from tests.helpers import problem

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def billing_schema(database: Any) -> Any:
    """Seats live in the billing tables; see ``test_team_invites.py``."""
    from garh_api.billing.models import BILLING_METADATA

    BILLING_METADATA.drop_all(database)
    BILLING_METADATA.create_all(database)
    return database


@pytest.fixture(autouse=True)
def clean_billing(billing_schema: Any, clean_db: None) -> None:
    from garh_api.billing.models import BILLING_TABLES
    from sqlalchemy import text as sql_text

    with billing_schema.begin() as connection:
        connection.execute(
            sql_text(
                "TRUNCATE TABLE %s RESTART IDENTITY CASCADE"
                % ", ".join('"%s"' % name for name in BILLING_TABLES)
            )
        )


async def _sign_in(client: Any, api: str, email: str) -> dict[str, Any]:
    issued = await client.post("%s/auth/otp" % api, json={"email": email})
    assert issued.status_code == 202, issued.text
    code = issued.json().get("devCode")
    assert isinstance(code, str), issued.text
    verified = await client.post("%s/auth/verify" % api, json={"email": email, "code": code})
    assert verified.status_code == 200, verified.text
    return verified.json()


async def _members(client: Any, api: str, actor: Any) -> dict[str, Any]:
    response = await client.get("%s/firm/members" % api, headers=actor.headers)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


async def test_members_are_listed_with_role_seat_and_last_sign_in(
    client: Any, api: str, firm_a: Any, member_a: Any, session: Any
) -> None:
    from garh_api.billing.repositories import SeatRepository

    await SeatRepository(session, firm_a.ctx()).assign(
        user_id=member_a.user_id, seat_type="viewer", assigned_by=firm_a.user_id
    )
    await session.commit()
    await _sign_in(client, api, member_a.email)

    body = await _members(client, api, member_a)  # a member can see the team
    assert body["count"] == 2 and body["admins"] == 1
    by_email = {m["email"]: m for m in body["items"]}
    assert by_email[firm_a.email]["role"] == "admin"
    assert by_email[firm_a.email]["seat"] is None
    assert by_email[firm_a.email]["lastSignInAt"] is None, "never signed in over HTTP"
    assert by_email[member_a.email]["role"] == "member"
    assert by_email[member_a.email]["seat"]["seatType"] == "viewer"
    assert by_email[member_a.email]["lastSignInAt"] is not None


async def test_another_firm_sees_none_of_it(
    client: Any, api: str, firm_a: Any, member_a: Any, firm_b: Any
) -> None:
    body = await _members(client, api, firm_b)
    assert body["count"] == 1
    assert {m["email"] for m in body["items"]} == {firm_b.email}


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


async def test_an_admin_can_promote_and_demote_but_never_the_last_admin(
    client: Any, api: str, firm_a: Any, member_a: Any
) -> None:
    # The only admin cannot demote themselves.
    alone = await client.patch(
        "%s/firm/members/%s" % (api, firm_a.user_id),
        json={"role": "member"},
        headers=firm_a.headers,
    )
    assert alone.status_code == 409 and problem(alone)["code"] == "last_admin", alone.text

    promoted = await client.patch(
        "%s/firm/members/%s" % (api, member_a.user_id),
        json={"role": "admin"},
        headers=firm_a.headers,
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["role"] == "admin"
    assert (await _members(client, api, firm_a))["admins"] == 2

    # Now the founder may step down…
    stepped = await client.patch(
        "%s/firm/members/%s" % (api, firm_a.user_id),
        json={"role": "member"},
        headers=firm_a.headers,
    )
    assert stepped.status_code == 200 and stepped.json()["role"] == "member", stepped.text

    # …and the new sole admin is protected in turn (asked for by the new admin).
    last = await client.patch(
        "%s/firm/members/%s" % (api, member_a.user_id),
        json={"role": "member"},
        headers=member_a.headers,
    )
    # member_a's TOKEN still says "member" — the promotion reaches the bearer on the
    # next refresh — so this is the 403 a member gets, which is also correct.
    assert last.status_code == 403, last.text


async def test_a_member_cannot_change_roles_or_remove_anyone(
    client: Any, api: str, firm_a: Any, member_a: Any
) -> None:
    patched = await client.patch(
        "%s/firm/members/%s" % (api, firm_a.user_id),
        json={"role": "member"},
        headers=member_a.headers,
    )
    assert patched.status_code == 403, patched.text
    removed = await client.delete(
        "%s/firm/members/%s" % (api, firm_a.user_id), headers=member_a.headers
    )
    assert removed.status_code == 403, removed.text


async def test_an_invalid_role_is_a_422(client: Any, api: str, firm_a: Any, member_a: Any) -> None:
    response = await client.patch(
        "%s/firm/members/%s" % (api, member_a.user_id),
        json={"role": "owner"},
        headers=firm_a.headers,
    )
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------


async def test_removing_a_member_ends_their_live_sessions(
    client: Any, api: str, firm_a: Any, member_a: Any, session: Any
) -> None:
    from garh_api.billing.repositories import SeatRepository

    await SeatRepository(session, firm_a.ctx()).assign(
        user_id=member_a.user_id, seat_type="editor", assigned_by=firm_a.user_id
    )
    await session.commit()

    # A REAL sign-in: a family in Redis and a bearer good for fifteen minutes.
    live = await _sign_in(client, api, member_a.email)
    bearer = {"Authorization": "Bearer %s" % live["accessToken"]}
    refresh_cookie = client.cookies.get(REFRESH_COOKIE_NAME)
    assert (await client.get("%s/auth/me" % api, headers=bearer)).status_code == 200

    removed = await client.delete(
        "%s/firm/members/%s" % (api, member_a.user_id), headers=firm_a.headers
    )
    assert removed.status_code == 204, removed.text

    # Out now: the bearer is revoked, and so is the refresh family.
    me = await client.get("%s/auth/me" % api, headers=bearer)
    assert me.status_code == 401 and problem(me)["code"] == "token_revoked", me.text
    client.cookies.clear()
    refreshed = await client.post(
        "%s/auth/refresh" % api, headers={"Cookie": "%s=%s" % (REFRESH_COOKIE_NAME, refresh_cookie)}
    )
    assert refreshed.status_code == 401, refreshed.text

    # Gone from the team, and their seat is free again.
    body = await _members(client, api, firm_a)
    assert {m["email"] for m in body["items"]} == {firm_a.email}
    seats = await client.get("%s/billing/seats" % api, headers=firm_a.headers)
    assert seats.json()["editorsUsed"] == 0, seats.text

    # And a sign-in for the removed address is the uniform "nothing sent". (Their
    # sign-in a moment ago charged the 60 s cooldown, as it should; let it lapse.)
    await reset_rate_limit(
        otp_resend_rule(),
        otp_resend_identity("email:%s" % pseudonymise(member_a.email), "signin"),
    )
    issued = await client.post("%s/auth/otp" % api, json={"email": member_a.email})
    assert issued.status_code == 202 and issued.json()["devCode"] is None

    # Removing again is a 404, like any id the firm does not own.
    again = await client.delete(
        "%s/firm/members/%s" % (api, member_a.user_id), headers=firm_a.headers
    )
    assert again.status_code == 404


async def test_nobody_removes_themselves_or_the_last_admin(
    client: Any, api: str, firm_a: Any, member_a: Any
) -> None:
    yourself = await client.delete(
        "%s/firm/members/%s" % (api, firm_a.user_id), headers=firm_a.headers
    )
    assert yourself.status_code == 403, yourself.text

    # Promote the member, demote the founder, then try to remove the only admin.
    assert (
        await client.patch(
            "%s/firm/members/%s" % (api, member_a.user_id),
            json={"role": "admin"},
            headers=firm_a.headers,
        )
    ).status_code == 200
    assert (
        await client.patch(
            "%s/firm/members/%s" % (api, firm_a.user_id),
            json={"role": "member"},
            headers=firm_a.headers,
        )
    ).status_code == 200
    # firm_a's factory token still carries role=admin (tokens are not re-read until
    # refresh), so it can still call the admin route — and the guard is on the ROW.
    last = await client.delete(
        "%s/firm/members/%s" % (api, member_a.user_id), headers=firm_a.headers
    )
    assert last.status_code == 409 and problem(last)["code"] == "last_admin", last.text
    assert (await _members(client, api, firm_a))["count"] == 2


# ---------------------------------------------------------------------------
# Your own profile
# ---------------------------------------------------------------------------


async def test_you_can_edit_your_own_name_and_coa_number(
    client: Any, api: str, member_a: Any
) -> None:
    edited = await client.patch(
        "%s/auth/me" % api,
        json={"name": "Rahul V. Verma", "coaNumber": "CA/2019/12345"},
        headers=member_a.headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["user"]["name"] == "Rahul V. Verma"
    assert edited.json()["user"]["coaNumber"] == "CA/2019/12345"

    # An empty CoA clears it; an omitted field is left alone.
    cleared = await client.patch(
        "%s/auth/me" % api, json={"coaNumber": ""}, headers=member_a.headers
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["user"]["coaNumber"] is None
    assert cleared.json()["user"]["name"] == "Rahul V. Verma"

    # A blank name is refused, not stored.
    blank = await client.patch("%s/auth/me" % api, json={"name": "   "}, headers=member_a.headers)
    assert blank.status_code in (400, 422), blank.text
    me = await client.get("%s/auth/me" % api, headers=member_a.headers)
    assert me.json()["user"]["name"] == "Rahul V. Verma"
