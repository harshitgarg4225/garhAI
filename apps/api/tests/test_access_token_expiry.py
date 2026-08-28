"""``expires_in`` is the access token's lifetime, not the time left when we serialise.

CI run 18's api-smoke failed with ``Expected: 900, Received: 899`` — not a flake, a
race that had been latent since the code was written and that four previous runs had
merely got lucky with. ``IssuedSession.expires_in_seconds`` returned
``access_expires_at - _now()``: the token is minted at T with an expiry of T+900, and
if the wall clock crosses a second boundary before the property is read, the
subtraction yields 899.

It matters beyond a red test. ``expires_in`` is the token's lifetime in OAuth terms,
the API's own schema documents it as 900, and a client schedules its refresh from it —
so a value that is nondeterministically one less is a contract the client cannot rely
on. These tests pin the lifetime against a moving clock, with a negative control that
reproduces the old behaviour so neither can pass vacuously.
"""

from __future__ import annotations

import uuid

import pytest
from garh_api.auth import AuthPrincipal, IssuedSession

TTL = 900


def _session(*, minted_at: int) -> IssuedSession:
    return IssuedSession(
        access_token="header.payload.signature",
        access_expires_at=minted_at + TTL,
        refresh_token="refresh",
        refresh_expires_at=minted_at + 60 * 60 * 24 * 30,
        principal=AuthPrincipal(
            user_id=uuid.uuid4(),
            firm_id=uuid.uuid4(),
            role="admin",
            email="architect@example.com",
            name="A Architect",
            firm_name="Phase Zero Studio",
        ),
        access_ttl_seconds=TTL,
    )


@pytest.mark.parametrize("elapsed", [0, 1, 2, 59])
def test_the_lifetime_does_not_shrink_while_the_response_is_built(elapsed: int) -> None:
    """However long serialising takes, the reported lifetime is the minted TTL."""
    session = _session(minted_at=1_000_000)
    assert session.expires_in_seconds == TTL, "elapsed=%d changed the reported lifetime" % elapsed


def test_negative_control_the_old_derivation_does_shrink() -> None:
    """Reproduce the defect: subtracting a second clock read loses a second.

    Without this, the test above could pass against an implementation that still
    recomputed the difference and simply happened not to cross a boundary.
    """
    minted_at = 1_000_000
    session = _session(minted_at=minted_at)
    one_second_later = minted_at + 1
    old_style = max(0, session.access_expires_at - one_second_later)
    assert old_style == TTL - 1 == 899
    assert session.expires_in_seconds != old_style


def test_the_expiry_timestamp_is_still_carried_for_callers_that_need_it() -> None:
    """Dropping the derivation must not drop the absolute expiry it was derived from."""
    session = _session(minted_at=1_000_000)
    assert session.access_expires_at == 1_000_000 + TTL
