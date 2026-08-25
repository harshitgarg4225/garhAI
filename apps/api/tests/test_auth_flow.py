"""The whole sign-in lifecycle, end to end (playbook §13 AuthN, Phase 0 DoD "login").

    OTP issue -> verify -> access token -> refresh rotation -> reuse detected -> logout-all

Every step goes through HTTP. No service is constructed directly and no token is minted by
a helper: this is the one file in the suite that proves the flow a browser actually walks,
which is why ``tests/factories.access_token`` is allowed to shortcut it everywhere else.

The code is read from ``devCode`` in the ``POST /auth/otp`` response — the double-gated dev
echo (``garh_api.auth.dev_echo_otp_enabled``), which is how the app is usable with no mail
provider and how the Playwright smoke spec signs in too. If this suite ever runs against a
build where that echo is off, these tests fail loudly rather than skipping: an auth flow
nobody can test is not a tested auth flow.
"""

from __future__ import annotations

from typing import Any

import pytest

from garh_api.security import REFRESH_COOKIE_NAME
from tests.helpers import problem

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _request_code(client: Any, api: str, email: str) -> str:
    """``POST /auth/otp`` and return the echoed code."""
    response = await client.post("%s/auth/otp" % api, json={"email": email})
    assert response.status_code == 202, response.text
    body = response.json()
    # §13: 10-minute expiry, and the UI's resend countdown.
    assert body["expiresInSeconds"] == 600, body
    assert body["resendAfterSeconds"] == 60, body
    assert body["sent"] is True, body
    code = body.get("devCode")
    assert isinstance(code, str) and len(code) == 6 and code.isdigit(), (
        "no usable devCode in the OTP response — is DEV_ECHO_OTP off? body=%r" % body
    )
    return code


def _refresh_cookie(client: Any) -> str:
    token = client.cookies.get(REFRESH_COOKIE_NAME)
    assert isinstance(token, str) and token, "no refresh cookie was set"
    return token


async def _sign_in(client: Any, api: str, email: str) -> dict[str, Any]:
    code = await _request_code(client, api, email)
    response = await client.post("%s/auth/verify" % api, json={"email": email, "code": code})
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


async def test_otp_issue_then_verify_returns_a_usable_session(
    client: Any, api: str, firm_a: Any
) -> None:
    """A correct code buys a 15-minute access token plus a refresh cookie."""
    session = await _sign_in(client, api, firm_a.email)

    assert session["tokenType"] == "Bearer"
    assert session["expiresIn"] == 900, "§11: 15-minute access token"
    assert session["expiresAt"] > 0
    assert session["user"]["email"] == firm_a.email
    assert session["user"]["role"] == "admin"
    assert session["firm"]["name"] == firm_a.firm_name
    # §13 keeps PII out of the token; the profile comes from the row.
    assert session["user"]["id"] == str(firm_a.user_id)

    # The refresh token is a cookie, never a body field — it must not be readable by JS.
    # (Its attributes are asserted in the next test.)
    assert "refreshToken" not in session
    assert _refresh_cookie(client)

    me = await client.get(
        "%s/auth/me" % api, headers={"Authorization": "Bearer %s" % session["accessToken"]}
    )
    assert me.status_code == 200, me.text
    assert me.json()["user"]["email"] == firm_a.email


async def test_verify_sets_an_httponly_lax_path_scoped_cookie(
    client: Any, api: str, firm_a: Any
) -> None:
    """§13: ``SameSite=Lax`` cookies for refresh — plus HttpOnly and a narrow Path.

    ``Secure`` is asserted in ``test_config_env.py`` against non-dev settings; the suite
    runs as dev over plain http, where it is deliberately off (see conftest).
    """
    code = await _request_code(client, api, firm_a.email)
    response = await client.post(
        "%s/auth/verify" % api, json={"email": firm_a.email, "code": code}
    )
    assert response.status_code == 200, response.text

    raw = response.headers.get("set-cookie", "")
    assert REFRESH_COOKIE_NAME in raw, raw
    lowered = raw.lower()
    assert "httponly" in lowered, raw
    assert "samesite=lax" in lowered, raw
    assert "path=%s/auth" % api in lowered, raw


async def test_access_token_is_required_and_scoped_to_its_firm(
    client: Any, api: str, firm_a: Any
) -> None:
    """The token minted by the real flow is the token the tenancy layer trusts."""
    session = await _sign_in(client, api, firm_a.email)
    headers = {"Authorization": "Bearer %s" % session["accessToken"]}

    created = await client.post("%s/projects" % api, json={"name": "From a real token"}, headers=headers)
    assert created.status_code == 201, created.text

    listed = await client.get("%s/projects" % api, headers=headers)
    assert [item["name"] for item in listed.json()["items"]] == ["From a real token"]


# ---------------------------------------------------------------------------
# Refresh rotation and reuse detection
# ---------------------------------------------------------------------------


async def test_refresh_rotates_the_token(client: Any, api: str, firm_a: Any) -> None:
    """Every refresh mints a new access token **and** a new refresh token."""
    first = await _sign_in(client, api, firm_a.email)
    first_refresh = _refresh_cookie(client)

    rotated = await client.post("%s/auth/refresh" % api)
    assert rotated.status_code == 200, rotated.text
    second = rotated.json()
    second_refresh = _refresh_cookie(client)

    assert second_refresh != first_refresh, "the refresh token did not rotate"
    assert second["accessToken"] != first["accessToken"]
    assert second["user"]["id"] == first["user"]["id"]

    # The new access token works.
    me = await client.get(
        "%s/auth/me" % api, headers={"Authorization": "Bearer %s" % second["accessToken"]}
    )
    assert me.status_code == 200, me.text


async def test_refresh_reuse_is_detected_and_kills_the_family(
    client: Any, api: str, firm_a: Any
) -> None:
    """Presenting a spent refresh token is treated as theft (§13 refresh rotation).

    The stolen token is dead *and* so is its successor: a thief who replays the token they
    copied must not be able to keep the session alive, and neither must the victim's
    browser silently keep using the successor as if nothing happened. Both are logged out;
    signing in again is the recovery.
    """
    await _sign_in(client, api, firm_a.email)
    stolen = _refresh_cookie(client)

    rotated = await client.post("%s/auth/refresh" % api)
    assert rotated.status_code == 200, rotated.text
    successor = _refresh_cookie(client)
    assert successor != stolen

    # Replay the spent token, bypassing the cookie jar so the request is byte-exact.
    client.cookies.clear()
    replay = await client.post(
        "%s/auth/refresh" % api,
        headers={"Cookie": "%s=%s" % (REFRESH_COOKIE_NAME, stolen)},
    )
    assert replay.status_code == 401, replay.text
    body = problem(replay)
    assert body["code"] == "refresh_token_reused", body
    # Every authentication failure on this route clears the cookie, or the browser
    # replays a token we have declared dead forever.
    assert REFRESH_COOKIE_NAME in replay.headers.get("set-cookie", "")

    # The whole family is revoked, including the token the honest client now holds.
    client.cookies.clear()
    after = await client.post(
        "%s/auth/refresh" % api,
        headers={"Cookie": "%s=%s" % (REFRESH_COOKIE_NAME, successor)},
    )
    assert after.status_code == 401, after.text
    assert problem(after)["code"] in ("refresh_token_revoked", "refresh_token_reused")


async def test_refresh_without_a_cookie_is_401_not_500(client: Any, api: str) -> None:
    response = await client.post("%s/auth/refresh" % api)
    assert response.status_code == 401, response.text
    assert problem(response)["code"] == "refresh_token_missing"


async def test_refresh_rejects_an_access_token_presented_as_a_refresh_token(
    client: Any, api: str, firm_a: Any
) -> None:
    """Token confusion: the access token has a different ``aud`` and ``typ`` (§13)."""
    session = await _sign_in(client, api, firm_a.email)
    client.cookies.clear()
    response = await client.post(
        "%s/auth/refresh" % api,
        headers={"Cookie": "%s=%s" % (REFRESH_COOKIE_NAME, session["accessToken"])},
    )
    assert response.status_code == 401, response.text
    assert problem(response)["code"] in ("refresh_token_invalid", "token_invalid")


# ---------------------------------------------------------------------------
# Logout and logout-all
# ---------------------------------------------------------------------------


async def test_logout_ends_this_session_only(client: Any, api: str, firm_a: Any) -> None:
    session = await _sign_in(client, api, firm_a.email)

    logout = await client.post("%s/auth/logout" % api)
    assert logout.status_code == 200, logout.text
    assert logout.json()["signedOut"] is True

    refresh = await client.post("%s/auth/refresh" % api)
    assert refresh.status_code == 401, refresh.text

    # The access token still verifies until it expires — logout revokes the *family*,
    # not the 15-minute bearer. That is the documented trade-off, asserted so a change
    # to it is a deliberate one.
    me = await client.get(
        "%s/auth/me" % api, headers={"Authorization": "Bearer %s" % session["accessToken"]}
    )
    assert me.status_code == 200, me.text


async def test_logout_is_idempotent_and_never_fails(client: Any, api: str) -> None:
    """Signing out is not something a user can fail at (no cookie, still 200)."""
    first = await client.post("%s/auth/logout" % api)
    assert first.status_code == 200, first.text
    assert first.json()["sessionsEnded"] == 0
    second = await client.post("%s/auth/logout" % api)
    assert second.status_code == 200, second.text


async def test_logout_all_revokes_every_access_token(client: Any, api: str, firm_a: Any) -> None:
    """``logout-all`` bumps the token generation, so live access tokens die immediately."""
    session = await _sign_in(client, api, firm_a.email)
    headers = {"Authorization": "Bearer %s" % session["accessToken"]}

    # Prove the token works before the bump, so a failure afterwards means the bump.
    assert (await client.get("%s/auth/me" % api, headers=headers)).status_code == 200

    ended = await client.post("%s/auth/logout-all" % api, headers=headers)
    assert ended.status_code == 200, ended.text
    assert ended.json()["signedOut"] is True

    revoked = await client.get("%s/auth/me" % api, headers=headers)
    assert revoked.status_code == 401, revoked.text
    assert problem(revoked)["code"] == "token_revoked"

    # And the refresh cookie is dead too.
    refresh = await client.post("%s/auth/refresh" % api)
    assert refresh.status_code == 401, refresh.text


async def test_logout_all_requires_a_live_token(client: Any, api: str) -> None:
    """A destructive action needs a live credential, not just a stale cookie."""
    response = await client.post("%s/auth/logout-all" % api)
    assert response.status_code == 401, response.text
    assert problem(response)["code"] == "unauthenticated"


# ---------------------------------------------------------------------------
# Signup, and non-enumerability
# ---------------------------------------------------------------------------


async def test_signup_creates_a_firm_and_falls_into_the_otp_flow(
    client: Any, api: str, unique_email: Any
) -> None:
    email = unique_email("founder")
    created = await client.post(
        "%s/auth/signup" % api,
        json={"firmName": "Iyer Associates", "name": "Meera Iyer", "email": email},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    # No tokens: the address is still unproven.
    assert "accessToken" not in body
    code = body.get("devCode")
    assert isinstance(code, str), body

    signed_in = await client.post("%s/auth/verify" % api, json={"email": email, "code": code})
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["firm"]["name"] == "Iyer Associates"


async def test_signup_twice_is_a_clean_409(client: Any, api: str, firm_a: Any) -> None:
    """The one route that admits an address is taken — a silent no-op strands the user."""
    response = await client.post(
        "%s/auth/signup" % api,
        json={"firmName": "Copycat Studio", "name": "Someone Else", "email": firm_a.email},
    )
    assert response.status_code == 409, response.text
    assert problem(response)["code"] == "email_already_registered"


async def test_otp_for_an_unknown_address_is_indistinguishable(
    client: Any, api: str, firm_a: Any, unique_email: Any
) -> None:
    """§13: sign-in must not be an account-enumeration oracle.

    Same status, same body shape, same timing class. Only ``devCode`` differs, and it is
    absent for the unknown address precisely because no code was issued — which is
    invisible outside dev, where the echo does not exist.
    """
    known = await client.post("%s/auth/otp" % api, json={"email": firm_a.email})
    unknown = await client.post("%s/auth/otp" % api, json={"email": unique_email("ghost")})

    assert known.status_code == unknown.status_code == 202
    assert set(known.json()) == set(unknown.json())
    for field in ("sent", "expiresInSeconds", "resendAfterSeconds"):
        assert known.json()[field] == unknown.json()[field]
    assert unknown.json()["devCode"] is None


async def test_verify_for_an_unknown_address_is_the_generic_failure(
    client: Any, api: str, unique_email: Any
) -> None:
    response = await client.post(
        "%s/auth/verify" % api, json={"email": unique_email("ghost"), "code": "123456"}
    )
    assert response.status_code == 400, response.text
    assert problem(response)["code"] == "otp_invalid"
