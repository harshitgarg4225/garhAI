"""What ``POST /auth/otp`` says versus what it did — with a real (fake) mailer installed.

Two execution finds from the deployed stack (2026-09-01/02), both invisible to every
test that ran before, because the suite has no mailer installed and so only ever
exercised the dev-echo channel:

1. The response echoed the code whenever the dev echo was *enabled*, not whenever it
   was *used*. With a mailer installed on a dev-env deployment the code went by mail
   AND came back in the body — any caller could sign in as any address. Masked only
   while the mailer itself was failing.
2. A delivery 503 says "try again in a few seconds", but the 60-second resend cooldown
   had already been charged, so the retry it invites was a 429 "we just sent a code"
   for a code that was never sent.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from garh_api.auth import set_otp_mailer

from tests.helpers import problem


class _Recorder:
    """A mailer that succeeds and remembers what it was asked to send."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, int]] = []

    async def __call__(self, email: str, code: str, ttl_seconds: int) -> None:
        self.sent.append((email, code, ttl_seconds))


class _Down:
    """A mailer whose transport is unreachable — Railway Hobby's SMTP, exactly."""

    async def __call__(self, email: str, code: str, ttl_seconds: int) -> None:
        raise TimeoutError("connect timed out")


@pytest.fixture
def recorder() -> Iterator[_Recorder]:
    mailer = _Recorder()
    set_otp_mailer(mailer)
    try:
        yield mailer
    finally:
        set_otp_mailer(None)


@pytest.fixture
def mail_is_down() -> Iterator[None]:
    set_otp_mailer(_Down())
    try:
        yield
    finally:
        set_otp_mailer(None)


@pytest.mark.integration
async def test_a_mailed_code_is_not_also_echoed_in_the_response(
    client: Any, api: str, clean_redis: Any, firm_a: Any, recorder: _Recorder
) -> None:
    """Channel decides, not the setting: this suite runs with the dev echo ENABLED."""
    response = await client.post("%s/auth/otp" % api, json={"email": firm_a.email})
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["sent"] is True
    assert body.get("devCode") is None, "the code went by mail and came back in the body too"
    # ...and it really did go by mail — this is not a test of nothing having happened.
    assert len(recorder.sent) == 1
    email, code, _ttl = recorder.sent[0]
    assert email == firm_a.email
    assert code and code not in response.text


@pytest.mark.integration
async def test_signup_does_not_echo_a_mailed_code_either(
    client: Any, api: str, clean_redis: Any, recorder: _Recorder
) -> None:
    address = "new-%s@studio.test" % uuid.uuid4().hex[:8]
    response = await client.post(
        "%s/auth/signup" % api,
        json={"firmName": "Channel Studio", "name": "A Tester", "email": address},
    )
    assert response.status_code in (200, 201, 202), response.text
    assert response.json().get("devCode") is None
    assert [sent[0] for sent in recorder.sent] == [address]


@pytest.mark.integration
async def test_a_delivery_503_refunds_the_resend_cooldown(
    client: Any, api: str, clean_redis: Any, firm_a: Any, mail_is_down: None
) -> None:
    """The 503's own advice — "try again in a few seconds" — must be honoured."""
    first = await client.post("%s/auth/otp" % api, json={"email": firm_a.email})
    assert first.status_code == 503, first.text
    assert problem(first)["code"] == "service_unavailable"

    second = await client.post("%s/auth/otp" % api, json={"email": firm_a.email})
    assert second.status_code != 429, (
        "the retry the 503 invited was refused by the cooldown of a code that was "
        "never sent: %s" % second.text
    )
    assert second.status_code == 503, second.text


@pytest.mark.integration
async def test_a_delivery_503_on_signup_refunds_its_cooldown_too(
    client: Any, api: str, clean_redis: Any, mail_is_down: None
) -> None:
    """Yesterday's live sequence: signup 503 → 'Try again' → 429. Now: 503 → 503."""
    address = "retry-%s@studio.test" % uuid.uuid4().hex[:8]
    payload = {"firmName": "Retry Studio", "name": "A Tester", "email": address}
    first = await client.post("%s/auth/signup" % api, json=payload)
    assert first.status_code == 503, first.text
    second = await client.post("%s/auth/signup" % api, json=payload)
    assert second.status_code == 503, second.text  # the firm was rolled back, so not 409


@pytest.mark.integration
async def test_a_successful_send_keeps_the_cooldown(
    client: Any, api: str, clean_redis: Any, firm_a: Any, recorder: _Recorder
) -> None:
    """NEGATIVE CONTROL for the refund: it must not turn into 'no cooldown at all'."""
    first = await client.post("%s/auth/otp" % api, json={"email": firm_a.email})
    assert first.status_code == 202
    second = await client.post("%s/auth/otp" % api, json={"email": firm_a.email})
    assert second.status_code == 429, second.text
    assert len(recorder.sent) == 1
