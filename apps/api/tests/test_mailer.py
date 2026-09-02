"""The SMTP mailer and its wiring into OTP delivery — no datastore needed.

What is under test, and against what:

* :mod:`garh_api.mailer` against a **fake** ``smtplib.SMTP`` that records the
  conversation and sends nothing — so the assertions cover the exact message and
  the exact protocol steps (STARTTLS, AUTH) without a socket;
* the delivery policy in ``garh_api.auth._deliver_code``: a configured mailer wins
  over the dev echo, an SMTP failure becomes the existing "undeliverable" 503
  (:class:`~garh_api.errors.ServiceUnavailableError` — an ``ApiError``, which
  ``install_error_handlers`` renders as problem+json, never a raw traceback), and
  no mailer + no echo raises loudly and names the ``SMTP_*`` variables to set;
* the enablement gate: ``build_mailer`` returns ``None`` unless both ``SMTP_HOST``
  and ``SMTP_FROM`` are set, and ``main._install_otp_mailer`` installs/clears the
  auth hook accordingly.

Per this repo's rule about gates that cannot go red, the fake is negative-tested:
one test breaks it deliberately (wrong code, STARTTLS off) and asserts the checks
the positive tests rely on would indeed fail.

Settings are built the ``test_config_env`` way — clear every variable a case
reads, set exactly the ones it is about, ``_env_file=None`` — so an inherited
``SMTP_HOST`` or the conftest's ``DEV_ECHO_OTP=1`` cannot make a case pass for
the wrong reason.
"""

from __future__ import annotations

import json as _json
import smtplib
import ssl
from collections.abc import Iterator
from email.message import EmailMessage
from typing import Any

import httpx
import pytest
from garh_api import auth as auth_module
from garh_api.auth import set_otp_mailer
from garh_api.config import Settings
from garh_api.errors import ServiceUnavailableError
from garh_api.mailer import (
    BREVO_SEND_URL,
    OTP_SUBJECT,
    BrevoHttpMailer,
    SmtpMailer,
    build_mailer,
    build_otp_message,
)

#: A code that appears nowhere else, so a containment assertion cannot pass by luck.
CODE = "482913"

#: Every variable these cases read. Cleared before each settings build.
_ENV_KEYS: tuple[str, ...] = (
    "BREVO_API_KEY",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "SMTP_STARTTLS",
    "DEV_ECHO_OTP",
)


def _settings(monkeypatch: Any, **env: str) -> Settings:
    """A dev ``Settings`` built from exactly ``env`` (mail-wise) and nothing else."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("ENV", "dev")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def _mailer_under_test(**overrides: Any) -> SmtpMailer:
    kwargs: dict[str, Any] = {
        "host": "smtp.relay.test",
        "port": 2525,
        "user": "",
        "password": "",
        "from_addr": "no-reply@garh.test",
        "starttls": True,
    }
    kwargs.update(overrides)
    return SmtpMailer(**kwargs)


class _Recorder:
    """Everything the fake transport saw during one test."""

    def __init__(self) -> None:
        self.connections: list[Any] = []
        #: Set by a test to make ``send_message`` raise instead of recording.
        self.fail_with: Exception | None = None


@pytest.fixture
def smtp(monkeypatch: Any) -> _Recorder:
    """Replace ``smtplib.SMTP`` with a recording fake. Yields the recorder."""
    recorder = _Recorder()

    class FakeSmtp:
        """Stands in for ``smtplib.SMTP``: records the conversation, sends nothing."""

        def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
            self.host = host
            self.port = port
            self.timeout = timeout
            self.starttls_called = False
            self.starttls_context: ssl.SSLContext | None = None
            self.login_args: tuple[str, str] | None = None
            self.sent: list[EmailMessage] = []
            self.closed = False
            recorder.connections.append(self)

        def __enter__(self) -> FakeSmtp:
            return self

        def __exit__(self, *exc_info: Any) -> None:
            self.closed = True

        def starttls(self, *, context: ssl.SSLContext | None = None) -> None:
            self.starttls_called = True
            self.starttls_context = context

        def login(self, user: str, password: str) -> None:
            self.login_args = (user, password)

        def send_message(self, message: EmailMessage) -> None:
            if recorder.fail_with is not None:
                raise recorder.fail_with
            self.sent.append(message)

    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    return recorder


@pytest.fixture(autouse=True)
def _clean_mailer_hook() -> Iterator[None]:
    """The hook is module-global state; never let one test's mailer leak into the next."""
    set_otp_mailer(None)
    yield
    set_otp_mailer(None)


# ---------------------------------------------------------------------------
# The message
# ---------------------------------------------------------------------------


class TestOtpMessage:
    def test_contains_code_ttl_and_ignore_line(self) -> None:
        message = build_otp_message(
            to_email="asha@studio.test",
            from_addr="no-reply@garh.test",
            code=CODE,
            ttl_seconds=600,
        )
        body = message.get_content()
        assert CODE in body
        assert "10 minutes" in body, "TTL must be stated in minutes"
        assert "Didn't request this code? Ignore this email" in body
        assert message["Subject"] == OTP_SUBJECT
        assert message["From"] == "no-reply@garh.test"
        assert message["To"] == "asha@studio.test"
        assert message.get_content_type() == "text/plain", "OTP mail is plain text only"

    def test_sub_minute_ttl_still_reads_as_one_minute(self) -> None:
        """A 45s TTL must not render as "0 minutes" — that reads as already expired."""
        message = build_otp_message(
            to_email="asha@studio.test",
            from_addr="no-reply@garh.test",
            code=CODE,
            ttl_seconds=45,
        )
        assert "1 minute" in message.get_content()
        assert "0 minute" not in message.get_content()


# ---------------------------------------------------------------------------
# The transport, against the fake
# ---------------------------------------------------------------------------


class TestSmtpTransport:
    async def test_starttls_yes_auth_no_by_default(self, smtp: _Recorder) -> None:
        await _mailer_under_test()("asha@studio.test", CODE, 600)

        (conn,) = smtp.connections
        assert (conn.host, conn.port) == ("smtp.relay.test", 2525)
        assert conn.starttls_called is True, "STARTTLS is the default posture"
        assert conn.login_args is None, "no SMTP_USER, so AUTH must not be attempted"
        (sent,) = conn.sent
        assert CODE in sent.get_content()
        assert sent["To"] == "asha@studio.test"
        assert conn.closed is True, "one connection per message, closed after"

    async def test_authenticates_only_when_user_configured(self, smtp: _Recorder) -> None:
        await _mailer_under_test(user="mailer@garh.test", password="s3cret")(
            "asha@studio.test", CODE, 600
        )

        (conn,) = smtp.connections
        assert conn.login_args == ("mailer@garh.test", "s3cret")

    async def test_smtp_failure_propagates(self, smtp: _Recorder) -> None:
        smtp.fail_with = smtplib.SMTPException("relay said no")

        with pytest.raises(smtplib.SMTPException):
            await _mailer_under_test()("asha@studio.test", CODE, 600)

        (conn,) = smtp.connections
        assert conn.sent == [], "a failed send must not be recorded as sent"

    def test_from_domain_is_log_safe_even_with_a_display_name(self) -> None:
        mailer = _mailer_under_test(from_addr="Garh AI <no-reply@garh.ai>")
        assert mailer.from_domain == "garh.ai"

    async def test_the_fake_can_fail_these_assertions(self, smtp: _Recorder) -> None:
        """NEGATIVE-TEST the gate: break things deliberately, watch the checks go red.

        Per CLAUDE.md, a green check that cannot go red is worse than no check: this
        proves the containment and STARTTLS assertions above are falsifiable, not
        structurally true of the fake.
        """
        # A mailer with STARTTLS off and a different code...
        await _mailer_under_test(starttls=False)("asha@studio.test", "111111", 600)

        (conn,) = smtp.connections
        # ...fails the STARTTLS assertion:
        assert conn.starttls_called is False
        # ...and fails the code-containment assertion:
        (sent,) = conn.sent
        assert CODE not in sent.get_content()


# ---------------------------------------------------------------------------
# Delivery policy (_deliver_code): mailer > echo > loud failure
# ---------------------------------------------------------------------------


class TestDeliveryPolicy:
    async def test_installed_mailer_wins_over_the_dev_echo(
        self, smtp: _Recorder, monkeypatch: Any
    ) -> None:
        settings = _settings(monkeypatch)  # dev, echo available — the mailer must still win
        set_otp_mailer(_mailer_under_test())

        channel = await auth_module._deliver_code("asha@studio.test", CODE, 600, settings=settings)

        assert channel == "email"
        (conn,) = smtp.connections
        assert CODE in conn.sent[0].get_content()

    async def test_dev_echo_used_when_no_mailer_installed(self, monkeypatch: Any) -> None:
        settings = _settings(monkeypatch)

        channel = await auth_module._deliver_code("asha@studio.test", CODE, 600, settings=settings)

        assert channel == "dev-echo"

    async def test_smtp_failure_becomes_the_undeliverable_503(
        self, smtp: _Recorder, monkeypatch: Any
    ) -> None:
        """The route path: an SMTP outage is a 503 problem+json, never a raw 500.

        ``ServiceUnavailableError`` is an ``ApiError``; ``install_error_handlers``
        (wired in ``create_app``) renders every ``ApiError`` as problem+json with its
        ``http_status`` and headers — so asserting the exception type and its shape
        here is asserting the response contract.
        """
        settings = _settings(monkeypatch)
        smtp.fail_with = smtplib.SMTPException("relay said no")
        set_otp_mailer(_mailer_under_test())

        with pytest.raises(ServiceUnavailableError) as excinfo:
            await auth_module._deliver_code("asha@studio.test", CODE, 600, settings=settings)

        err = excinfo.value
        assert err.http_status == 503
        assert isinstance(err.__cause__, smtplib.SMTPException), "cause kept for the log"
        assert err.headers.get("Retry-After"), "a 503 without Retry-After is rude"
        problem = err.as_problem()
        assert problem["dependency"] == "email"
        assert CODE not in problem["message"], "the code must never ride an error body"

    async def test_undeliverable_error_names_the_smtp_env_vars(self, monkeypatch: Any) -> None:
        """No mailer + echo disabled = the prod misconfiguration, reached via the
        ``DEV_ECHO_OTP=0`` kill switch. The message must hand the operator the fix."""
        settings = _settings(monkeypatch, DEV_ECHO_OTP="0")

        with pytest.raises(ServiceUnavailableError) as excinfo:
            await auth_module._deliver_code("asha@studio.test", CODE, 600, settings=settings)

        message = str(excinfo.value)
        assert "SMTP_HOST" in message
        assert "SMTP_FROM" in message


# ---------------------------------------------------------------------------
# Enablement + startup wiring
# ---------------------------------------------------------------------------


class TestEnablement:
    def test_build_mailer_requires_host_and_from(self, monkeypatch: Any) -> None:
        assert build_mailer(_settings(monkeypatch)) is None
        assert build_mailer(_settings(monkeypatch, SMTP_HOST="smtp.relay.test")) is None
        assert build_mailer(_settings(monkeypatch, SMTP_FROM="no-reply@garh.test")) is None

        mailer = build_mailer(
            _settings(monkeypatch, SMTP_HOST="smtp.relay.test", SMTP_FROM="no-reply@garh.test")
        )
        assert isinstance(mailer, SmtpMailer)
        assert mailer.port == 587, "587 (submission) is the default"
        assert mailer.starttls is True, "STARTTLS is the default"

    def test_install_helper_wires_and_clears_the_hook(self, monkeypatch: Any) -> None:
        """``main._install_otp_mailer`` is the exact function the lifespan calls."""
        from garh_api.main import _install_otp_mailer

        _install_otp_mailer(
            _settings(monkeypatch, SMTP_HOST="smtp.relay.test", SMTP_FROM="no-reply@garh.test")
        )
        assert isinstance(auth_module._mailer, SmtpMailer)

        # Unconfigured settings must CLEAR the hook, not leave a stale mailer behind.
        _install_otp_mailer(_settings(monkeypatch))
        assert auth_module._mailer is None

    def test_smtp_password_never_appears_in_the_boot_dump(self, monkeypatch: Any) -> None:
        settings = _settings(
            monkeypatch,
            SMTP_HOST="smtp.relay.test",
            SMTP_FROM="no-reply@garh.test",
            SMTP_PASSWORD="hunter2-not-for-logs",
        )
        dump = settings.redacted()
        assert dump["smtp_password"] == "***"
        assert "hunter2-not-for-logs" not in repr(dump)


# ---------------------------------------------------------------------------
# The Brevo HTTPS mailer — the transport Railway Hobby can actually use
# ---------------------------------------------------------------------------
#
# Railway disables outbound SMTP below the Pro plan on every port. The first live
# sign-up on the deployed stack timed out on smtp-relay.brevo.com:587 after exactly
# the SMTP mailer's 15 s, and Brevo never saw it. These cases pin the HTTPS path the
# same way the Stability provider is pinned: a STRICT httpx.MockTransport that
# rejects anything but the exact request we mean to send.


def _brevo_transport(recorded: list[httpx.Request], *, status: int = 201, body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        if str(request.url) != BREVO_SEND_URL:
            return httpx.Response(404, json={"code": "wrong_endpoint", "message": str(request.url)})
        if request.headers.get("api-key") != "xkeysib-test":
            return httpx.Response(401, json={"code": "unauthorized"})
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return httpx.Response(415, json={"code": "not_json"})
        return httpx.Response(status, json=body if body is not None else {"messageId": "<m@brevo>"})

    return httpx.MockTransport(handler)


async def test_brevo_sends_the_same_message_over_https() -> None:
    """URL, auth header, and the exact fields Brevo's v3 endpoint reads."""
    recorded: list[httpx.Request] = []
    mailer = BrevoHttpMailer(
        api_key="xkeysib-test",
        from_addr="Garh AI <help@example.test>",
        transport_override=_brevo_transport(recorded),
    )
    await mailer("arch@studio.test", "123456", 600)

    assert len(recorded) == 1
    sent = recorded[0]
    assert sent.method == "POST"
    assert str(sent.url) == BREVO_SEND_URL
    assert sent.headers["api-key"] == "xkeysib-test"
    payload = _json.loads(sent.content)
    assert payload["sender"] == {"email": "help@example.test", "name": "Garh AI"}
    assert payload["to"] == [{"email": "arch@studio.test"}]
    assert payload["subject"] == OTP_SUBJECT
    # Same body as the SMTP path: the code and the "didn't request this" line.
    assert "123456" in payload["textContent"]
    assert "Didn't request this code" in payload["textContent"]
    # §13: the key travels in a header only — never in the URL or the body.
    assert "xkeysib" not in str(sent.url) and b"xkeysib" not in sent.content


async def test_brevo_rejection_raises_so_auth_can_503() -> None:
    """A non-2xx must raise: `_deliver_code` turns any exception into the honest
    "couldn't send" 503. Swallowing it would be a code the architect never gets and
    a screen that says "Check your email"."""
    recorded: list[httpx.Request] = []
    mailer = BrevoHttpMailer(
        api_key="xkeysib-test",
        from_addr="help@example.test",
        transport_override=_brevo_transport(
            recorded,
            status=400,
            body={"code": "invalid_parameter", "message": "sender not verified"},
        ),
    )
    with pytest.raises(RuntimeError, match="invalid_parameter"):
        await mailer("arch@studio.test", "123456", 600)


async def test_brevo_wrong_key_is_a_failure_not_a_send() -> None:
    """NEGATIVE CONTROL for the strict transport: the wrong key must be refused by
    the double, or the success test above proves nothing about authentication."""
    recorded: list[httpx.Request] = []
    mailer = BrevoHttpMailer(
        api_key="xkeysib-WRONG",
        from_addr="help@example.test",
        transport_override=_brevo_transport(recorded),
    )
    with pytest.raises(RuntimeError, match="401"):
        await mailer("arch@studio.test", "123456", 600)


def test_build_mailer_prefers_brevo_when_both_are_configured(monkeypatch: Any) -> None:
    """On a platform where SMTP is blocked, an operator who set BOTH must get the
    one that works. Precedence is the whole point of the HTTP mailer existing."""
    settings = _settings(
        monkeypatch,
        SMTP_HOST="smtp-relay.brevo.com",
        SMTP_FROM="help@example.test",
        BREVO_API_KEY="xkeysib-test",
    )
    mailer = build_mailer(settings)
    assert isinstance(mailer, BrevoHttpMailer)
    assert mailer.transport == "brevo-http"
    assert mailer.from_domain == "example.test"


def test_build_mailer_falls_back_to_smtp_without_a_brevo_key(monkeypatch: Any) -> None:
    """NEGATIVE CONTROL for precedence: without the key it must NOT pick HTTP."""
    settings = _settings(
        monkeypatch, SMTP_HOST="smtp-relay.brevo.com", SMTP_FROM="help@example.test"
    )
    mailer = build_mailer(settings)
    assert isinstance(mailer, SmtpMailer)
    assert mailer.transport == "smtp"


def test_brevo_key_alone_is_not_enough_it_needs_a_sender(monkeypatch: Any) -> None:
    """A key with no SMTP_FROM has nothing to put in `sender` — mail stays off,
    loudly, rather than sending from an empty address."""
    settings = _settings(monkeypatch, BREVO_API_KEY="xkeysib-test")
    assert settings.brevo_configured is False
    assert build_mailer(settings) is None


async def test_starttls_verifies_the_relay_certificate(smtp: _Recorder) -> None:
    """The upgrade must carry a verifying context.

    ``smtplib.SMTP.starttls()`` with no context uses an UNVERIFIED one (CERT_NONE, no
    hostname check), so a MITM at SMTP_HOST would receive the relay login and every
    sign-in code. Pinned here because nothing else in the suite can see the context.
    """
    await _mailer_under_test()("someone@studio.test", CODE, 600)
    (conn,) = smtp.connections
    ctx = conn.starttls_context
    assert ctx is not None, "starttls() was called without an SSL context"
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_a_whitespace_sender_is_not_a_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    """``SMTP_FROM=' '`` must leave mail OFF, not install a mailer with a blank From.

    pydantic-settings does not strip values (verified: the field holds ``'   '``), and
    a blank sender fails at the relay (SMTP) or at Brevo (empty ``sender.email``) — a
    503 on every sign-in that looks exactly like the transport being down.
    """
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-test")
    monkeypatch.setenv("SMTP_FROM", "   ")
    settings = Settings(_env_file=None)
    assert settings.smtp_from == "   ", "the premise: pydantic-settings keeps the whitespace"
    assert settings.smtp_configured is False
    assert settings.brevo_configured is False
    assert build_mailer(settings) is None
