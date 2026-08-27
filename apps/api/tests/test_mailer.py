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

import smtplib
from collections.abc import Iterator
from email.message import EmailMessage
from typing import Any

import pytest
from garh_api import auth as auth_module
from garh_api.auth import set_otp_mailer
from garh_api.config import Settings
from garh_api.errors import ServiceUnavailableError
from garh_api.mailer import OTP_SUBJECT, SmtpMailer, build_mailer, build_otp_message

#: A code that appears nowhere else, so a containment assertion cannot pass by luck.
CODE = "482913"

#: Every variable these cases read. Cleared before each settings build.
_ENV_KEYS: tuple[str, ...] = (
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
            self.login_args: tuple[str, str] | None = None
            self.sent: list[EmailMessage] = []
            self.closed = False
            recorder.connections.append(self)

        def __enter__(self) -> FakeSmtp:
            return self

        def __exit__(self, *exc_info: Any) -> None:
            self.closed = True

        def starttls(self) -> None:
            self.starttls_called = True

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
