"""F-3: "these are your signed-in devices", and "sign that one out".

Every session in this file is created by walking the real sign-in flow over HTTP —
``POST /auth/otp`` for the echoed code, then ``POST /auth/verify`` — because the thing
under test is whether a *refresh family* is visible and revocable, and a family only
exists if a real sign-in made one. Minting a token with ``tests.factories.access_token``
would produce a caller with no family at all, and every assertion here would pass
against a store that never wrote anything.

The load-bearing test is :func:`test_revoking_one_device_kills_only_that_one`: it
proves the *positive* half (the other device's refresh still rotates) in the same
breath as the negative half (the revoked one is 401). A revoke that killed everything
would satisfy "the revoked session is dead" on its own.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from garh_api.auth import SessionStore, family_key, user_families_key
from garh_api.ratelimit import otp_per_email_rule, otp_resend_rule, reset_rate_limit
from garh_api.routers.sessions import describe_user_agent
from garh_api.security import REFRESH_COOKIE_NAME, new_token_family, pseudonymise

from tests.helpers import problem

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0"
SAFARI_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) Version/17.5 Safari/605.1"


async def clear_email_budget(email: str) -> None:
    """Drop the per-address OTP buckets.

    Signing the same person in twice inside 60 seconds is exactly what "I opened the
    app on my phone as well" looks like, and it is what a two-device test needs — but
    it is also what the resend cooldown exists to stop. ``reset_rate_limit`` is the
    supported way to un-stick an address (its own docstring says "for tests and for
    support"), so the test resets the bucket rather than mocking the limiter, and the
    limiter stays real for the tests that are about it.
    """
    identity = "email:%s" % pseudonymise(email)
    await reset_rate_limit(otp_resend_rule(), identity)
    await reset_rate_limit(otp_per_email_rule(), identity)


async def sign_in(client: Any, api: str, email: str, *, user_agent: str, ip: str) -> dict[str, Any]:
    """One full sign-in from one "device". Returns its token and refresh cookie.

    The cookie jar is cleared afterwards and every later call sends an explicit
    ``Cookie`` header: one httpx client is standing in for several browsers, and a
    shared jar would silently make device two present device one's credential.
    """
    await clear_email_budget(email)
    headers = {"user-agent": user_agent, "x-forwarded-for": ip}

    issued = await client.post("%s/auth/otp" % api, json={"email": email}, headers=headers)
    assert issued.status_code == 202, issued.text
    code = issued.json().get("devCode")
    assert isinstance(code, str) and code.isdigit(), (
        "no devCode in the OTP response — is DEV_ECHO_OTP off? body=%r" % issued.json()
    )

    verified = await client.post(
        "%s/auth/verify" % api, json={"email": email, "code": code}, headers=headers
    )
    assert verified.status_code == 200, verified.text
    refresh = verified.cookies.get(REFRESH_COOKIE_NAME)
    assert refresh, "sign-in set no refresh cookie"
    client.cookies.clear()
    return {
        "access": verified.json()["accessToken"],
        "refresh": refresh,
        "auth": {"Authorization": "Bearer %s" % verified.json()["accessToken"]},
        "cookie": {"Cookie": "%s=%s" % (REFRESH_COOKIE_NAME, refresh)},
    }


# ---------------------------------------------------------------------------
# The device list
# ---------------------------------------------------------------------------


async def test_two_sign_ins_show_as_two_devices(client: Any, api: str, firm_a: Any) -> None:
    """Signing in twice produces two rows, each labelled with where it came from."""
    laptop = await sign_in(client, api, firm_a.email, user_agent=CHROME_UA, ip="203.0.113.10")
    phone = await sign_in(client, api, firm_a.email, user_agent=SAFARI_UA, ip="198.51.100.7")

    listing = await client.get("%s/auth/sessions" % api, headers=laptop["auth"])
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["count"] == 2, body
    devices = {item["device"] for item in body["items"]}
    assert devices == {"Chrome on Windows", "Safari on iPhone"}, body
    addresses = {item["ip"] for item in body["items"]}
    assert addresses == {"203.0.113.10", "198.51.100.7"}, body
    for item in body["items"]:
        assert item["startedAt"] > 0 and item["lastUsedAt"] >= item["startedAt"], item

    # Nothing was mixed up: the phone sees the same two rows.
    from_phone = await client.get("%s/auth/sessions" % api, headers=phone["auth"])
    assert from_phone.json()["count"] == 2


async def test_the_device_holding_the_cookie_is_marked_current(
    client: Any, api: str, firm_a: Any
) -> None:
    """Exactly one row is ``current``, and it is the one whose cookie was sent.

    Without this the UI has no safe "sign out my other devices" button — it would be
    guessing which row not to kill.
    """
    laptop = await sign_in(client, api, firm_a.email, user_agent=CHROME_UA, ip="203.0.113.10")
    phone = await sign_in(client, api, firm_a.email, user_agent=SAFARI_UA, ip="198.51.100.7")

    listing = await client.get(
        "%s/auth/sessions" % api, headers={**laptop["auth"], **laptop["cookie"]}
    )
    items = listing.json()["items"]
    current = [item for item in items if item["current"]]
    assert len(current) == 1, items
    assert current[0]["device"] == "Chrome on Windows", current

    # Same token, the *phone's* cookie: the flag follows the cookie, not the bearer.
    swapped = await client.get(
        "%s/auth/sessions" % api, headers={**laptop["auth"], **phone["cookie"]}
    )
    marked = [item for item in swapped.json()["items"] if item["current"]]
    assert len(marked) == 1 and marked[0]["device"] == "Safari on iPhone", swapped.json()


async def test_no_cookie_means_no_row_is_claimed_as_current(
    client: Any, api: str, firm_a: Any
) -> None:
    """A bearer-only caller still gets the list; nothing is falsely marked "this one"."""
    laptop = await sign_in(client, api, firm_a.email, user_agent=CHROME_UA, ip="203.0.113.10")
    listing = await client.get("%s/auth/sessions" % api, headers=laptop["auth"])
    assert listing.status_code == 200
    assert [item["current"] for item in listing.json()["items"]] == [False]


async def test_listing_sessions_needs_a_token(client: Any, api: str) -> None:
    response = await client.get("%s/auth/sessions" % api)
    assert response.status_code == 401, response.text
    assert problem(response)["code"] == "unauthenticated"


# ---------------------------------------------------------------------------
# Revoking one device
# ---------------------------------------------------------------------------


async def test_revoking_one_device_kills_only_that_one(client: Any, api: str, firm_a: Any) -> None:
    """The revoked family cannot refresh; the other one still can.

    Both halves matter. "The revoked session is dead" passes just as well against a
    revoke that killed every session the user had, which would be a far worse bug than
    the one this route exists to fix.
    """
    laptop = await sign_in(client, api, firm_a.email, user_agent=CHROME_UA, ip="203.0.113.10")
    phone = await sign_in(client, api, firm_a.email, user_agent=SAFARI_UA, ip="198.51.100.7")

    listing = await client.get(
        "%s/auth/sessions" % api, headers={**phone["auth"], **phone["cookie"]}
    )
    laptop_row = next(
        item for item in listing.json()["items"] if item["device"] == "Chrome on Windows"
    )
    assert laptop_row["current"] is False

    revoked = await client.delete(
        "%s/auth/sessions/%s" % (api, laptop_row["id"]), headers=phone["auth"]
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["sessionsEnded"] == 1

    dead = await client.post("%s/auth/refresh" % api, headers=laptop["cookie"])
    assert dead.status_code == 401, dead.text
    assert problem(dead)["code"] == "refresh_token_revoked"

    alive = await client.post("%s/auth/refresh" % api, headers=phone["cookie"])
    assert alive.status_code == 200, (
        "revoking one device killed the other — the whole point of this route is that "
        "it does not: %s" % alive.text
    )

    after = await client.get("%s/auth/sessions" % api, headers=phone["auth"])
    assert after.json()["count"] == 1, after.json()


async def test_revoking_a_family_that_was_never_issued_is_404(
    client: Any, api: str, firm_a: Any
) -> None:
    """An invented id is "not found", not "nothing to do"."""
    device = await sign_in(client, api, firm_a.email, user_agent=CHROME_UA, ip="203.0.113.10")
    response = await client.delete(
        "%s/auth/sessions/%s" % (api, new_token_family()), headers=device["auth"]
    )
    assert response.status_code == 404, response.text
    assert problem(response)["code"] == "not_found"


async def test_one_colleague_cannot_revoke_anothers_session(
    client: Any, api: str, firm_a: Any, member_a: Any
) -> None:
    """Same firm, different person: 404, and the session survives.

    Family records are keyed by user id, so this is the same structural answer the
    cross-tenant sweep gets — but inside one firm, where a shared ``firm_id`` would
    have made a firm-scoped check pass.
    """
    victim = await sign_in(client, api, firm_a.email, user_agent=CHROME_UA, ip="203.0.113.10")
    listing = await client.get("%s/auth/sessions" % api, headers=victim["auth"])
    family = listing.json()["items"][0]["id"]

    attempt = await client.delete("%s/auth/sessions/%s" % (api, family), headers=member_a.headers)
    assert attempt.status_code == 404, attempt.text

    still_alive = await client.post("%s/auth/refresh" % api, headers=victim["cookie"])
    assert still_alive.status_code == 200, (
        "a colleague's revoke attempt killed the session anyway: %s" % still_alive.text
    )


async def test_another_firms_session_is_invisible_and_untouchable(
    client: Any, api: str, firm_a: Any, firm_b: Any
) -> None:
    """Firm B sees none of firm A's devices and cannot end one."""
    victim = await sign_in(client, api, firm_a.email, user_agent=CHROME_UA, ip="203.0.113.10")
    family = (await client.get("%s/auth/sessions" % api, headers=victim["auth"])).json()["items"][
        0
    ]["id"]

    listing_b = await client.get("%s/auth/sessions" % api, headers=firm_b.headers)
    assert listing_b.status_code == 200
    assert listing_b.json()["count"] == 0, listing_b.json()

    attempt = await client.delete("%s/auth/sessions/%s" % (api, family), headers=firm_b.headers)
    assert attempt.status_code == 404, attempt.text

    still_alive = await client.post("%s/auth/refresh" % api, headers=victim["cookie"])
    assert still_alive.status_code == 200, still_alive.text


# ---------------------------------------------------------------------------
# Revoke everything else
# ---------------------------------------------------------------------------


async def test_revoke_others_keeps_the_calling_device(client: Any, api: str, firm_a: Any) -> None:
    """Two others die, this one lives — which is the difference from logout-all."""
    first = await sign_in(client, api, firm_a.email, user_agent=CHROME_UA, ip="203.0.113.10")
    second = await sign_in(client, api, firm_a.email, user_agent=SAFARI_UA, ip="198.51.100.7")
    keeper = await sign_in(client, api, firm_a.email, user_agent="curl/8.4.0", ip="192.0.2.44")

    response = await client.post(
        "%s/auth/sessions/revoke-others" % api,
        headers={**keeper["auth"], **keeper["cookie"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["sessionsEnded"] == 2, response.json()

    for dead in (first, second):
        killed = await client.post("%s/auth/refresh" % api, headers=dead["cookie"])
        assert killed.status_code == 401, killed.text

    alive = await client.post("%s/auth/refresh" % api, headers=keeper["cookie"])
    assert alive.status_code == 200, (
        "revoke-others signed out the device that asked for it: %s" % alive.text
    )


async def test_revoke_others_refuses_when_it_cannot_tell_which_device_you_are(
    client: Any, api: str, firm_a: Any
) -> None:
    """No cookie, no idea which family to keep — so it revokes nothing and says so.

    The two available guesses are both wrong: revoking nothing is a silent no-op, and
    revoking everything is ``logout-all`` wearing a different name.
    """
    laptop = await sign_in(client, api, firm_a.email, user_agent=CHROME_UA, ip="203.0.113.10")
    phone = await sign_in(client, api, firm_a.email, user_agent=SAFARI_UA, ip="198.51.100.7")

    response = await client.post("%s/auth/sessions/revoke-others" % api, headers=laptop["auth"])
    assert response.status_code == 400, response.text
    body = problem(response)
    assert body["code"] == "invalid_request"
    assert body["action"], "golden rule 9: say what to do next"

    for survivor in (laptop, phone):
        alive = await client.post("%s/auth/refresh" % api, headers=survivor["cookie"])
        assert alive.status_code == 200, alive.text


# ---------------------------------------------------------------------------
# The store beneath the routes
# ---------------------------------------------------------------------------


async def test_list_families_drops_index_entries_whose_record_has_expired(
    clean_redis: Any, firm_a: Any
) -> None:
    """The user-families set is the one structure with no per-member TTL.

    A family record expires on its own; its name in the index does not. Without the
    tidy-up in ``list_families`` the set grows for the life of the account, and the
    device list gets slower for exactly the person who signs in from a lot of places.
    """
    store = SessionStore()
    live = new_token_family()
    gone = new_token_family()
    now = int(time.time())
    for family in (live, gone):
        await store.start_family(
            user_id=firm_a.user_id, firm_id=firm_a.firm_id, family=family, started_at=now
        )

    # Expire one record the way Redis eventually will, leaving its name in the index.
    # ``clean_redis`` yields the synchronous client, which is what makes this a
    # one-liner from a test that is not itself talking to the async pool.
    clean_redis.delete(family_key(firm_a.user_id, gone))

    families = await store.list_families(firm_a.user_id)
    assert [entry.family for entry in families] == [live]
    assert clean_redis.smembers(user_families_key(firm_a.user_id)) == {live}


async def test_list_families_hides_a_revoked_family(clean_redis: Any, firm_a: Any) -> None:
    """A revoked record survives (for reuse detection) but is not a "device"."""
    store = SessionStore()
    family = new_token_family()
    await store.start_family(
        user_id=firm_a.user_id,
        firm_id=firm_a.firm_id,
        family=family,
        started_at=int(time.time()),
    )
    assert len(await store.list_families(firm_a.user_id)) == 1

    await store.revoke_family(user_id=firm_a.user_id, family=family, reason="logout")
    assert await store.list_families(firm_a.user_id) == []


async def test_start_family_records_where_the_sign_in_came_from(
    clean_redis: Any, firm_a: Any
) -> None:
    """The device label is only as good as what sign-in stamped on the family."""
    store = SessionStore()
    family = new_token_family()
    await store.start_family(
        user_id=firm_a.user_id,
        firm_id=firm_a.firm_id,
        family=family,
        started_at=int(time.time()),
        ip="203.0.113.10",
        user_agent=CHROME_UA,
    )
    entry = (await store.list_families(firm_a.user_id))[0]
    assert entry.ip == "203.0.113.10"
    assert entry.user_agent == CHROME_UA
    assert entry.last_used_at == entry.started_at


async def test_a_users_families_are_invisible_to_another_user(
    clean_redis: Any, firm_a: Any, firm_b: Any
) -> None:
    """Store level, not route level: the keys are hash-tagged by user id."""
    store = SessionStore()
    family = new_token_family()
    await store.start_family(
        user_id=firm_a.user_id,
        firm_id=firm_a.firm_id,
        family=family,
        started_at=int(time.time()),
    )
    assert await store.list_families(firm_b.user_id) == []
    assert await store.revoke_family(user_id=firm_b.user_id, family=family, reason="probe") is False
    assert len(await store.list_families(firm_a.user_id)) == 1


# ---------------------------------------------------------------------------
# The device label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("user_agent", "expected"),
    [
        (CHROME_UA, "Chrome on Windows"),
        (SAFARI_UA, "Safari on iPhone"),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
            "Edge on Windows",
        ),
        (
            "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
            "Firefox on Linux",
        ),
        ("curl/8.4.0", "Unknown device"),
        ("", "Unknown device"),
        (None, "Unknown device"),
    ],
)
def test_device_labels_are_recognisable_or_honest(user_agent: Any, expected: str) -> None:
    """Edge's user agent contains "Chrome" and "Safari"; Chrome's contains "Safari".

    A naive substring check calls all three Safari, which is worse than useless on a
    screen whose whole job is "find the row that isn't you". An agent that matches
    nothing says so rather than being bucketed into the nearest guess.
    """
    assert describe_user_agent(user_agent) == expected
