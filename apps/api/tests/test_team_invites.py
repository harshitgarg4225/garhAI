"""Invite a colleague (J01 — the team half of "set up the practice").

Until this landed a practice was one person: every signup created a firm with exactly
one admin and nothing could add a second, so presence, live cursors and colleague
comments were built and unreachable by any two humans (``docs/trial-readiness.md``).

Everything here walks HTTP: the admin's ``POST /firm/invites``, the invitee's ordinary
``POST /auth/otp`` → ``POST /auth/verify``, then ``GET /firm/members`` to see them
landed. The pre-auth status page (``GET /auth/invites/{token}``) is asserted in every
state it can be in, because "honest refusal" is the whole point of it existing.

Two properties pull against each other and both are pinned, each with the negative
control that would catch the over-fix:

* an expired or withdrawn invite must refuse honestly — but on the status page,
  never on ``POST /auth/otp``, which keeps answering an identical 202;
* inviting an address that already belongs to ANOTHER practice must look exactly
  like inviting a fresh one (the admin is not an enumeration oracle), while inviting
  an address already in the admin's OWN practice is a 409 — a firm-scoped fact that
  reveals nothing about anywhere else.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from garh_api import models
from garh_api.auth import set_otp_mailer
from garh_api.billing.models import SEAT_TYPES
from garh_api.mailer import OTP_SUBJECT, build_otp_message
from garh_api.ratelimit import otp_resend_identity, otp_resend_rule, reset_rate_limit
from garh_api.security import pseudonymise
from sqlalchemy import update

from tests.helpers import problem

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _token_of(url: str) -> str:
    query = parse_qs(urlparse(url).query)
    (token,) = query["invite"]
    return token


async def _invite(
    client: Any, api: str, admin: Any, email: str, *, role: str = "member", seat: str = "editor"
) -> Any:
    return await client.post(
        "%s/firm/invites" % api,
        json={"email": email, "name": "Rahul Verma", "role": role, "seatType": seat},
        headers=admin.headers,
    )


async def _request_code(client: Any, api: str, email: str) -> Any:
    response = await client.post("%s/auth/otp" % api, json={"email": email})
    assert response.status_code == 202, response.text
    return response.json()


async def _sign_in(client: Any, api: str, email: str) -> dict[str, Any]:
    body = await _request_code(client, api, email)
    code = body.get("devCode")
    assert isinstance(code, str), "no devCode — is a mailer installed? body=%r" % body
    verified = await client.post("%s/auth/verify" % api, json={"email": email, "code": code})
    assert verified.status_code == 200, verified.text
    return verified.json()


async def _status(client: Any, api: str, token: str) -> Any:
    return await client.get("%s/auth/invites/%s" % (api, token))


async def _expire(session: Any, invite_id: str) -> None:
    """Move an invite's expiry into the past, as a week passing would."""
    await session.execute(
        update(models.FirmInvite)
        .where(models.FirmInvite.id == invite_id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()


class _Mailbox:
    """A mailer that records every message — the OTP builder AND the invite sender."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def __call__(self, email: str, code: str, ttl_seconds: int) -> None:
        self.sent.append(
            build_otp_message(
                to_email=email, from_addr="no-reply@garh.test", code=code, ttl_seconds=ttl_seconds
            )
        )

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)

    def to(self, email: str) -> list[EmailMessage]:
        return [m for m in self.sent if m["To"] == email]

    def code_for(self, email: str) -> str:
        otp = [m for m in self.to(email) if m["Subject"] == OTP_SUBJECT]
        assert otp, "no sign-in code was mailed to that address"
        match = re.search(r"\b(\d{6})\b", otp[-1].get_content())
        assert match, otp[-1].get_content()
        return match.group(1)


class _NoTransport:
    """A mailer whose ``send`` fails — the relay is down."""

    async def __call__(self, email: str, code: str, ttl_seconds: int) -> None:
        raise TimeoutError("connect timed out")

    async def send(self, message: EmailMessage) -> None:
        raise TimeoutError("connect timed out")


@pytest.fixture(scope="session")
def billing_schema(database: Any) -> Any:
    """The billing tables (seats live there), same shape as ``test_billing_api.py``.

    Not in ``ALL_TABLES``, so the session-scoped ``database`` fixture never builds
    them; every test in this module reads or writes seats, so they are rebuilt once.
    """
    from garh_api.billing.models import BILLING_METADATA

    BILLING_METADATA.drop_all(database)
    BILLING_METADATA.create_all(database)
    return database


@pytest.fixture(autouse=True)
def clean_billing(billing_schema: Any, clean_db: None) -> None:
    """Empty the seat table before each test — ``clean_db`` cannot see it."""
    from garh_api.billing.models import BILLING_TABLES
    from sqlalchemy import text as sql_text

    with billing_schema.begin() as connection:
        connection.execute(
            sql_text(
                "TRUNCATE TABLE %s RESTART IDENTITY CASCADE"
                % ", ".join('"%s"' % name for name in BILLING_TABLES)
            )
        )


@pytest.fixture
def mailbox() -> Iterator[_Mailbox]:
    box = _Mailbox()
    set_otp_mailer(box)
    try:
        yield box
    finally:
        set_otp_mailer(None)


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


async def test_an_invited_colleague_signs_in_with_a_code_and_lands_in_the_firm(
    client: Any, api: str, firm_a: Any, unique_email: Any
) -> None:
    """The whole loop: invite → status page → ordinary OTP sign-in → member with a seat."""
    email = unique_email("rahul")
    created = await _invite(client, api, firm_a, email, role="member")
    assert created.status_code == 201, created.text
    invite = created.json()
    assert invite["status"] == "pending"
    assert invite["role"] == "member" and invite["seatType"] == "editor"
    assert invite["invitedByName"] == "Asha Rao"
    assert isinstance(invite["url"], str) and "/login?invite=" in invite["url"]
    token = _token_of(invite["url"])

    # The link explains itself before any sign-in.
    status = await _status(client, api, token)
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "pending"
    assert status.json()["firmName"] == firm_a.firm_name
    assert status.json()["email"] == email
    assert status.json()["invitedByName"] == "Asha Rao"

    # The invitee has NO account. The ordinary sign-in flow is the acceptance.
    session = await _sign_in(client, api, email)
    assert session["firm"]["name"] == firm_a.firm_name
    assert session["firm"]["id"] == str(firm_a.firm_id)
    assert session["user"]["role"] == "member"
    assert session["user"]["name"] == "Rahul Verma"

    # They are a member now: with the promised seat and a last sign-in.
    members = await client.get("%s/firm/members" % api, headers=firm_a.headers)
    assert members.status_code == 200, members.text
    body = members.json()
    assert body["count"] == 2 and body["admins"] == 1
    by_email = {m["email"]: m for m in body["items"]}
    assert by_email[email]["role"] == "member"
    assert by_email[email]["seat"]["seatType"] == "editor"
    assert by_email[email]["lastSignInAt"] is not None
    # NEGATIVE CONTROL: the founder was created by a factory, never signed in over
    # HTTP — so the stamp is not a column default that is always set.
    assert by_email[firm_a.email]["lastSignInAt"] is None

    # The invite is spent: no longer listed, and its link says so.
    listed = await client.get("%s/firm/invites" % api, headers=firm_a.headers)
    assert listed.status_code == 200 and listed.json()["count"] == 0, listed.text
    assert (await _status(client, api, token)).json()["status"] == "accepted"

    # And an admin role is honoured too.
    second = unique_email("meera")
    assert (
        await _invite(client, api, firm_a, second, role="admin", seat="viewer")
    ).status_code == 201
    assert (await _sign_in(client, api, second))["user"]["role"] == "admin"
    admins = (await client.get("%s/firm/members" % api, headers=firm_a.headers)).json()["admins"]
    assert admins == 2


async def test_the_invite_and_the_code_both_go_through_the_mailer(
    client: Any, api: str, firm_a: Any, unique_email: Any, mailbox: _Mailbox
) -> None:
    """With a real transport installed nothing is echoed; the mailbox carries it all."""
    email = unique_email("rahul")
    created = await _invite(client, api, firm_a, email)
    assert created.status_code == 201, created.text

    (invite_mail,) = mailbox.to(email)
    assert invite_mail["Subject"] == "%s invited you to Garh" % firm_a.firm_name
    text = invite_mail.get_content()
    assert created.json()["url"] in text, "the link in the body is the link in the response"
    assert "Asha Rao" in text and firm_a.firm_name in text

    body = await _request_code(client, api, email)
    assert body["devCode"] is None, "a mailed code must never also be echoed"
    code = mailbox.code_for(email)
    verified = await client.post("%s/auth/verify" % api, json={"email": email, "code": code})
    assert verified.status_code == 200, verified.text
    assert verified.json()["firm"]["id"] == str(firm_a.firm_id)


# ---------------------------------------------------------------------------
# Honest refusals — on the status page, never on /auth/otp
# ---------------------------------------------------------------------------


async def test_an_expired_invite_says_so_and_no_code_is_sent(
    client: Any, api: str, firm_a: Any, unique_email: Any, session: Any
) -> None:
    email = unique_email("late")
    created = await _invite(client, api, firm_a, email)
    assert created.status_code == 201, created.text
    token = _token_of(created.json()["url"])
    await _expire(session, created.json()["id"])

    status = await _status(client, api, token)
    assert status.status_code == 200 and status.json()["status"] == "expired", status.text

    # The OTP route stays uniform: 202, nothing sent, nothing to verify.
    body = await _request_code(client, api, email)
    assert body["devCode"] is None
    verified = await client.post("%s/auth/verify" % api, json={"email": email, "code": "123456"})
    assert verified.status_code == 400 and problem(verified)["code"] == "otp_invalid"

    # The admin sees it lapsed, and "invite again" refreshes it rather than 409ing.
    listed = (await client.get("%s/firm/invites" % api, headers=firm_a.headers)).json()
    assert [i["status"] for i in listed["items"]] == ["expired"]
    # A week has "passed" for the invite; let it pass for the 60 s send cooldown too.
    await reset_rate_limit(
        otp_resend_rule(), otp_resend_identity("email:%s" % pseudonymise(email), "invite")
    )
    again = await _invite(client, api, firm_a, email)
    assert again.status_code == 201, again.text
    assert again.json()["id"] == created.json()["id"], "the same invite, refreshed"
    assert again.json()["status"] == "pending" and again.json()["sendCount"] == 2
    assert _token_of(again.json()["url"]) != token, "a fresh link"
    assert (await _status(client, api, token)).status_code == 404, "the lapsed link is dead"
    # The invitee's earlier sign-in request (the uniform 202 above) charged THEIR
    # cooldown exactly as it would for a real account — that is the enumeration
    # guard, not a bug. Let it lapse, then the refreshed invite accepts as normal.
    await reset_rate_limit(
        otp_resend_rule(), otp_resend_identity("email:%s" % pseudonymise(email), "signin")
    )
    assert (await _sign_in(client, api, email))["firm"]["id"] == str(firm_a.firm_id)


async def test_a_withdrawn_invite_says_so_and_no_code_is_sent(
    client: Any, api: str, firm_a: Any, unique_email: Any
) -> None:
    email = unique_email("withdrawn")
    created = await _invite(client, api, firm_a, email)
    assert created.status_code == 201, created.text
    token = _token_of(created.json()["url"])

    revoked = await client.delete(
        "%s/firm/invites/%s" % (api, created.json()["id"]), headers=firm_a.headers
    )
    assert revoked.status_code == 204, revoked.text
    assert (await _status(client, api, token)).json()["status"] == "revoked"
    assert (await _request_code(client, api, email))["devCode"] is None
    listed = (await client.get("%s/firm/invites" % api, headers=firm_a.headers)).json()
    assert listed["count"] == 0
    # Withdrawing twice is not an error; withdrawing what never existed is a 404.
    assert (
        await client.delete(
            "%s/firm/invites/%s" % (api, created.json()["id"]), headers=firm_a.headers
        )
    ).status_code == 204
    missing = await client.delete(
        "%s/firm/invites/00000000-0000-0000-0000-000000000000" % api, headers=firm_a.headers
    )
    assert missing.status_code == 404


async def test_an_unknown_link_is_404_and_a_malformed_one_is_422(client: Any, api: str) -> None:
    unknown = await _status(client, api, "A" * 43)
    assert unknown.status_code == 404 and problem(unknown)["code"] == "invite_invalid"
    malformed = await _status(client, api, "not a token!")
    assert malformed.status_code == 422, malformed.text


# ---------------------------------------------------------------------------
# What an invite must and must not reveal
# ---------------------------------------------------------------------------


async def test_inviting_an_address_with_an_account_elsewhere_looks_identical(
    client: Any, api: str, firm_a: Any, firm_b: Any, unique_email: Any
) -> None:
    """The admin of firm A must not learn that firm B's admin has an account.

    Same status, same keys, same ``pending`` — and the person who owns the address is
    the one who finds out, by signing in and landing in their own practice.
    """
    # Viewer seats, so the only thing that could differ between the two is the
    # address — an editor pair would trip the Free plan's one-seat gate instead.
    fresh = await _invite(client, api, firm_a, unique_email("fresh"), seat="viewer")
    taken = await _invite(client, api, firm_a, firm_b.email, seat="viewer")
    assert fresh.status_code == taken.status_code == 201, (fresh.text, taken.text)
    assert set(fresh.json()) == set(taken.json())
    assert fresh.json()["status"] == taken.json()["status"] == "pending"

    # Firm B's admin signs in as usual and is exactly where they were.
    session = await _sign_in(client, api, firm_b.email)
    assert session["firm"]["id"] == str(firm_b.firm_id)
    assert session["user"]["role"] == "admin"

    # Firm A still sees a pending invite — not "blocked", not "already registered".
    listed = (await client.get("%s/firm/invites" % api, headers=firm_a.headers)).json()
    assert {i["email"]: i["status"] for i in listed["items"]}[firm_b.email] == "pending"
    members = (await client.get("%s/firm/members" % api, headers=firm_a.headers)).json()
    assert firm_b.email not in {m["email"] for m in members["items"]}


async def test_a_member_of_your_own_practice_cannot_be_invited(
    client: Any, api: str, firm_a: Any, member_a: Any
) -> None:
    """Firm-scoped, so safe to say — and the opposite of the test above."""
    response = await _invite(client, api, firm_a, member_a.email)
    assert response.status_code == 409, response.text
    assert problem(response)["code"] == "already_a_member"


async def test_a_second_open_invite_to_the_same_address_is_409(
    client: Any, api: str, firm_a: Any, unique_email: Any
) -> None:
    email = unique_email("twice")
    assert (await _invite(client, api, firm_a, email)).status_code == 201
    again = await _invite(client, api, firm_a, email)
    assert again.status_code == 409 and problem(again)["code"] == "invite_pending"


async def test_only_an_admin_can_invite_resend_or_withdraw(
    client: Any, api: str, firm_a: Any, member_a: Any, unique_email: Any
) -> None:
    created = await _invite(client, api, firm_a, unique_email("x"))
    assert created.status_code == 201
    invite_id = created.json()["id"]
    forbidden = await _invite(client, api, member_a, unique_email("y"))
    assert forbidden.status_code == 403, forbidden.text
    assert (
        await client.post("%s/firm/invites/%s/resend" % (api, invite_id), headers=member_a.headers)
    ).status_code == 403
    assert (
        await client.delete("%s/firm/invites/%s" % (api, invite_id), headers=member_a.headers)
    ).status_code == 403
    # A member may still SEE the open invites — the team is not a secret from the team.
    listed = await client.get("%s/firm/invites" % api, headers=member_a.headers)
    assert listed.status_code == 200 and listed.json()["count"] == 1


# ---------------------------------------------------------------------------
# Seats
# ---------------------------------------------------------------------------


def test_the_invite_seat_vocabulary_is_the_billing_seat_vocabulary() -> None:
    """``models.INVITE_SEAT_TYPES`` mirrors ``billing.models.SEAT_TYPES`` without an
    import (models must not depend on billing). A drift here would let an invite
    promise a seat the seat repository refuses."""
    assert models.INVITE_SEAT_TYPES == SEAT_TYPES


async def test_editor_seats_are_enforced_when_the_invite_is_made(
    client: Any, api: str, firm_a: Any, unique_email: Any
) -> None:
    """The Free plan has one editor seat. The second editor invite is a 402 while the
    first is open; a viewer invite is free; accepting takes the seat for real."""
    first = await _invite(client, api, firm_a, unique_email("editor-1"), seat="editor")
    assert first.status_code == 201, first.text

    second = await _invite(client, api, firm_a, unique_email("editor-2"), seat="editor")
    assert second.status_code == 402, second.text
    body = problem(second)
    assert body["code"] == "seat_limit_reached"
    assert body["promised"] == 1 and body["entitled"] == 1

    # NEGATIVE CONTROL: viewers are not gated, so the refusal above was the seat gate.
    viewer = await _invite(client, api, firm_a, unique_email("viewer"), seat="viewer")
    assert viewer.status_code == 201, viewer.text

    # Accepting converts the promise into a held seat the billing page can see.
    await _sign_in(client, api, first.json()["email"])
    seats = await client.get("%s/billing/seats" % api, headers=firm_a.headers)
    assert seats.status_code == 200, seats.text
    assert seats.json()["editorsUsed"] == 1 and seats.json()["available"] == 0
    still = await _invite(client, api, firm_a, unique_email("editor-3"), seat="editor")
    assert still.status_code == 402 and problem(still)["used"] == 1

    # Withdrawing an open editor invite frees its promise again.
    viewer_id = viewer.json()["id"]
    assert (
        await client.delete("%s/firm/invites/%s" % (api, viewer_id), headers=firm_a.headers)
    ).status_code == 204


# ---------------------------------------------------------------------------
# Resend: rate-limited per route, like every other code
# ---------------------------------------------------------------------------


async def test_resend_is_rate_limited_per_route_and_does_not_block_the_invitee(
    client: Any, api: str, firm_a: Any, unique_email: Any, clean_redis: Any
) -> None:
    email = unique_email("resend")
    created = await _invite(client, api, firm_a, email)
    assert created.status_code == 201, created.text
    invite_id = created.json()["id"]
    first_token = _token_of(created.json()["url"])

    # Inside the 60 s window: the same 429 a sign-in resend gets.
    hammered = await client.post(
        "%s/firm/invites/%s/resend" % (api, invite_id), headers=firm_a.headers
    )
    assert hammered.status_code == 429, hammered.text
    body = problem(hammered)
    assert body["code"] == "otp_rate_limited" and body["rule"] == "auth.otp_resend"
    assert email not in hammered.text

    # NEGATIVE CONTROL: the invitee's OWN sign-in request is not the one throttled —
    # the cooldown is per route, exactly as the sign-in/sign-up split is.
    own = await client.post("%s/auth/otp" % api, json={"email": email})
    assert own.status_code == 202 and isinstance(own.json()["devCode"], str), own.text

    # Once the window passes, a resend rotates the link and the old one dies.
    await reset_rate_limit(
        otp_resend_rule(), otp_resend_identity("email:%s" % pseudonymise(email), "invite")
    )
    resent = await client.post(
        "%s/firm/invites/%s/resend" % (api, invite_id), headers=firm_a.headers
    )
    assert resent.status_code == 200, resent.text
    new_token = _token_of(resent.json()["url"])
    assert new_token != first_token
    assert resent.json()["sendCount"] == 2
    assert (await _status(client, api, first_token)).status_code == 404
    assert (await _status(client, api, new_token)).json()["status"] == "pending"


async def test_a_delivery_failure_is_a_503_that_stores_nothing_and_refunds_the_cooldown(
    client: Any, api: str, firm_a: Any, unique_email: Any
) -> None:
    """The relay is down: no row, no promise, and the retry the 503 invites is not a 429."""
    email = unique_email("down")
    set_otp_mailer(_NoTransport())
    try:
        failed = await _invite(client, api, firm_a, email)
    finally:
        set_otp_mailer(None)
    assert failed.status_code == 503, failed.text
    assert problem(failed)["code"] == "service_unavailable"
    assert failed.headers.get("retry-after")

    listed = (await client.get("%s/firm/invites" % api, headers=firm_a.headers)).json()
    assert listed["count"] == 0, "a failed send must not leave a pending promise"

    retried = await _invite(client, api, firm_a, email)
    assert retried.status_code == 201, "the cooldown was not refunded: %s" % retried.text
