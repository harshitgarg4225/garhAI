"""OTP expiry and the five-attempt lockout (playbook §13: "10min expiry, 5 attempts").

These two controls are the entire brute-force story for a six-digit code. Six digits is a
million possibilities, which sounds fine until you notice that an unbounded guesser gets
ten minutes of unlimited tries — so the cap is not a nicety, it is the control.

Both are asserted through HTTP, and both are asserted to fail **identically** to a wrong
code: expired, exhausted, never-issued and simply-wrong all return the same
``400 otp_invalid``. Telling them apart would leak whether an address has a live challenge
and how many guesses remain.

The expiry test moves the row's ``expires_at`` into the past rather than sleeping for ten
minutes. That is a direct table write, which the suite is allowed to do (``tests/`` is in
the Makefile's ``TENANCY_LAYER`` allowlist) and which is the honest way to simulate the
passage of time: the control being tested *is* the stored timestamp.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from garh_api import models
from garh_api.config import Settings
from sqlalchemy import select, update

from tests.helpers import problem

pytestmark = pytest.mark.integration

#: A code that is well-formed but wrong. Six digits, so it passes schema validation and
#: reaches the comparison — a malformed code would be rejected earlier and prove nothing.
WRONG_CODE = "000000"
OTHER_WRONG_CODE = "111111"


async def _issue(client: Any, api: str, email: str) -> str:
    response = await client.post("%s/auth/otp" % api, json={"email": email})
    assert response.status_code == 202, response.text
    code = response.json().get("devCode")
    assert isinstance(code, str), response.json()
    return code


def _wrong(code: str) -> str:
    return OTHER_WRONG_CODE if code == WRONG_CODE else WRONG_CODE


async def _verify(client: Any, api: str, email: str, code: str) -> Any:
    return await client.post("%s/auth/verify" % api, json={"email": email, "code": code})


# ---------------------------------------------------------------------------
# (c) Expiry
# ---------------------------------------------------------------------------


async def test_expired_code_is_rejected_even_though_it_is_correct(
    client: Any, api: str, session: Any, firm_a: Any, settings: Settings
) -> None:
    """The right code, ten minutes and one second late, is no longer the right code."""
    code = await _issue(client, api, firm_a.email)

    await session.execute(
        update(models.OtpCode)
        .where(models.OtpCode.email == firm_a.email)
        .where(models.OtpCode.consumed_at.is_(None))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()

    response = await _verify(client, api, firm_a.email, code)
    assert response.status_code == 400, response.text
    assert problem(response)["code"] == "otp_invalid"

    # Expiry consumes the challenge, so the code cannot come back to life if the clock
    # (or a replayed request) says otherwise.
    retry = await _verify(client, api, firm_a.email, code)
    assert retry.status_code == 400, retry.text


async def test_issued_code_expires_ten_minutes_out(
    client: Any, api: str, session: Any, firm_a: Any, settings: Settings
) -> None:
    """The stored expiry is the §13 window, not a longer one someone widened by hand."""
    before = datetime.now(UTC)
    await _issue(client, api, firm_a.email)

    row = (
        (await session.execute(select(models.OtpCode).where(models.OtpCode.email == firm_a.email)))
        .scalars()
        .one()
    )
    ttl = row.expires_at - before
    assert (
        timedelta(seconds=settings.otp_ttl_seconds - 5)
        <= ttl
        <= timedelta(seconds=settings.otp_ttl_seconds + 5)
    ), ttl
    assert settings.otp_ttl_seconds == 600

    # And only a hash is stored (§13: never the code itself).
    assert row.code_hash and row.code_hash != row.email
    assert len(row.code_hash) >= 32


# ---------------------------------------------------------------------------
# (c) The five-attempt lockout
# ---------------------------------------------------------------------------


async def test_five_wrong_attempts_burn_the_challenge(
    client: Any, api: str, session: Any, firm_a: Any, settings: Settings
) -> None:
    """After ``otp_max_attempts`` misses the code is dead — even the correct one.

    This is the test the auth agent's bug report was about: ``session_scope`` rolls the
    request transaction back on any exception, so the ``attempts`` increment lives on a
    path that ends in ``raise`` and was being discarded. Without the explicit commit in
    ``AuthService._persist_failure_record`` the sixth attempt below **succeeds**, and the
    cap is decoration.
    """
    assert settings.otp_max_attempts == 5
    code = await _issue(client, api, firm_a.email)
    wrong = _wrong(code)

    for attempt in range(settings.otp_max_attempts):
        response = await _verify(client, api, firm_a.email, wrong)
        assert response.status_code == 400, "attempt %d: %s" % (attempt + 1, response.text)
        assert problem(response)["code"] == "otp_invalid"

    exhausted = await _verify(client, api, firm_a.email, code)
    assert exhausted.status_code == 400, (
        "the correct code still worked after %d wrong attempts — the five-attempt cap is "
        "not being persisted (see AuthService._persist_failure_record)" % settings.otp_max_attempts
    )
    assert problem(exhausted)["code"] == "otp_invalid"


async def test_attempt_counter_is_persisted_per_attempt(
    client: Any, api: str, session: Any, firm_a: Any
) -> None:
    """The counter must be in the row after each failure, not only after the last one.

    Asserted directly, because "the sixth attempt failed" could also be explained by the
    challenge having been consumed for some other reason. This pins the mechanism.
    """
    code = await _issue(client, api, firm_a.email)
    wrong = _wrong(code)

    for expected in (1, 2, 3):
        await _verify(client, api, firm_a.email, wrong)
        session.expire_all()
        row = (
            (
                await session.execute(
                    select(models.OtpCode).where(models.OtpCode.email == firm_a.email)
                )
            )
            .scalars()
            .one()
        )
        assert row.attempts == expected, (
            "after %d wrong attempt(s) the row says %d — the increment is being rolled "
            "back with the 401" % (expected, row.attempts)
        )
        assert row.consumed_at is None


async def test_reaching_the_cap_consumes_the_challenge_row(
    client: Any, api: str, session: Any, firm_a: Any, settings: Settings
) -> None:
    """The fifth miss marks the row consumed, so it cannot be attempted a sixth time."""
    code = await _issue(client, api, firm_a.email)
    wrong = _wrong(code)
    for _ in range(settings.otp_max_attempts):
        await _verify(client, api, firm_a.email, wrong)

    session.expire_all()
    row = (
        (await session.execute(select(models.OtpCode).where(models.OtpCode.email == firm_a.email)))
        .scalars()
        .one()
    )
    assert row.attempts == settings.otp_max_attempts
    assert row.consumed_at is not None


async def test_a_correct_code_is_single_use(client: Any, api: str, firm_a: Any) -> None:
    """Replaying a used code must not produce a second session."""
    code = await _issue(client, api, firm_a.email)

    first = await _verify(client, api, firm_a.email, code)
    assert first.status_code == 200, first.text

    second = await _verify(client, api, firm_a.email, code)
    assert second.status_code == 400, second.text
    assert problem(second)["code"] == "otp_invalid"


async def test_a_new_code_invalidates_the_previous_one(
    client: Any, api: str, session: Any, firm_a: Any
) -> None:
    """Resend must narrow the guessing window, not widen it to two live codes.

    The 60-second resend cooldown blocks a second ``POST /auth/otp``, so the second code
    is issued through the repository — the layer the cooldown protects, and the layer
    whose ``invalidate_previous`` behaviour is under test.
    """
    from garh_api.repositories import OtpCodeRepository

    first_code = await _issue(client, api, firm_a.email)

    await OtpCodeRepository(session).issue(firm_a.email, "654321")
    await session.commit()

    stale = await _verify(client, api, firm_a.email, first_code)
    assert stale.status_code == 400, stale.text

    fresh = await _verify(client, api, firm_a.email, "654321")
    assert fresh.status_code == 200, fresh.text


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef", "12 34 56"])
async def test_malformed_codes_are_rejected_at_the_boundary(
    client: Any, api: str, firm_a: Any, code: str
) -> None:
    """Pydantic strict validation, not the comparison, handles these (§13 input)."""
    await _issue(client, api, firm_a.email)
    response = await client.post("%s/auth/verify" % api, json={"email": firm_a.email, "code": code})
    assert response.status_code in (400, 422), response.text
    body = problem(response)
    assert body["code"] in ("validation_failed", "otp_invalid", "invalid_request"), body
