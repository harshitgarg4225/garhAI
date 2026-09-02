"""The 60-second resend cooldown is per ROUTE, and the sign-in oracle stays closed.

Execution find, first live trial sign-in (2026-09-02 04:05 UTC on the deployed stack):

    POST /auth/otp     found=false -> otp_requested_unknown -> 202   (nothing sent)
    POST /auth/signup  30 s later  -> 429 auth.otp_resend "We just sent a code"

An architect with no account pressed Sign in, read "Check your email", then pressed
Create an account — and the sign-up that would actually have sent a code was refused,
because the sign-in attempt for a non-existent address had spent the one cooldown
both routes shared.

Two properties are pinned here, and they pull in opposite directions:

1. Sign-in's cooldown must not reach sign-up (the fix).
2. On ``/auth/otp`` the cooldown must stay IDENTICAL for known and unknown addresses.
   "Don't charge unknown addresses" would have fixed (1) and opened an enumeration
   oracle: a second request inside the window would 429 for a real account and 202
   for a fake one. The second test guards against exactly that over-fix, so it must
   stay green under the negative control that turns the first test red.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.helpers import problem


def _fresh_address() -> str:
    return "nobody-%s@studio.test" % uuid.uuid4().hex[:10]


async def _request_code(client: Any, api: str, email: str) -> Any:
    return await client.post("%s/auth/otp" % api, json={"email": email})


async def _sign_up(client: Any, api: str, email: str) -> Any:
    return await client.post(
        "%s/auth/signup" % api,
        json={"firmName": "Resend Scope Studio", "name": "A Tester", "email": email},
    )


@pytest.mark.integration
async def test_sign_in_for_an_unknown_address_does_not_block_sign_up(
    client: Any, api: str, clean_redis: Any
) -> None:
    """The live defect, verbatim: sign in with no account, then create one."""
    email = _fresh_address()

    first = await _request_code(client, api, email)
    assert first.status_code == 202, first.text  # anti-enumeration: looks sent

    # Well inside the 60-second window — the exact sequence the architect performed.
    signup = await _sign_up(client, api, email)
    assert signup.status_code != 429, (
        "sign-up was refused by a cooldown that a sign-in for a NON-EXISTENT account "
        "spent: %s" % signup.text
    )
    assert signup.status_code in (200, 201, 202), signup.text
    assert signup.json().get("sent") is True


@pytest.mark.integration
async def test_the_sign_in_cooldown_is_identical_for_known_and_unknown_addresses(
    client: Any, api: str, clean_redis: Any, firm_a: Any
) -> None:
    """The oracle guard. A fix that stopped charging unknown addresses passes the
    test above and fails this one — which is the point of this one existing.

    Two addresses, one real and one that has never been seen, each asked twice inside
    the window. The SECOND answers must be indistinguishable: same status, same code.
    """
    unknown = _fresh_address()

    known_1 = await _request_code(client, api, firm_a.email)
    unknown_1 = await _request_code(client, api, unknown)
    assert known_1.status_code == unknown_1.status_code == 202

    known_2 = await _request_code(client, api, firm_a.email)
    unknown_2 = await _request_code(client, api, unknown)
    assert known_2.status_code == unknown_2.status_code, (
        "enumeration oracle: known=%d unknown=%d" % (known_2.status_code, unknown_2.status_code)
    )
    assert known_2.status_code == 429
    assert problem(known_2)["code"] == problem(unknown_2)["code"] == "otp_rate_limited"


@pytest.mark.integration
async def test_sign_up_keeps_a_cooldown_of_its_own(client: Any, api: str, clean_redis: Any) -> None:
    """Per-route, not unlimited: two sign-ups inside the window are still throttled.

    NEGATIVE CONTROL for the first test — an implementation that simply removed the
    resend rule from sign-up would pass it and fail here.
    """
    email = _fresh_address()
    first = await _sign_up(client, api, email)
    assert first.status_code in (200, 201, 202), first.text

    second = await _sign_up(client, api, email)
    assert second.status_code == 429, second.text
    body = problem(second)
    assert body["code"] == "otp_rate_limited", body
    assert body["rule"] == "auth.otp_resend", body
    assert int(second.headers["retry-after"]) >= 1
    # A 429 must not leak the address (same guarantee test_rate_limits.py pins).
    assert email not in second.text
