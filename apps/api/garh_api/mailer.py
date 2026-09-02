"""Transactional email: the OTP sign-in code, over SMTP (§13 sign-in, §18 config).

One message type lives here — the sign-in code — and one transport: plain SMTP out
of the stdlib. That is a deliberate zero-new-dependency choice, the same reasoning
as the rules engine: every transactional-mail service worth using (SES, Postmark,
Resend, a Gmail relay) speaks SMTP submission on port 587 with STARTTLS, so
``smtplib`` covers all of them without a vendor client library each. ``smtplib``
is blocking; :meth:`SmtpMailer.__call__` runs the whole SMTP conversation on a
worker thread via :func:`asyncio.to_thread`, so the event loop never waits on a
mail server.

Wiring: :func:`build_mailer` reads the ``SMTP_*`` settings and returns ``None``
unless both ``SMTP_HOST`` and ``SMTP_FROM`` are set — mail is **off by default**,
keeping the dev echo (:func:`garh_api.auth.dev_echo_otp_enabled`) the zero-config
dev channel. ``main.py``'s lifespan installs the result through
:func:`garh_api.auth.set_otp_mailer`; nothing else imports this module.

PII rule (§13 secrets hygiene): neither the code nor the recipient address ever
reaches a log line from here — only ``email_domain`` and the relay host, matching
what ``garh_api.auth`` logs. An SMTP exception's *message* can quote the recipient
(``SMTPRecipientsRefused`` does), so callers must log the exception's type, not
its text — see ``garh_api.auth._deliver_code``.
"""

from __future__ import annotations

import asyncio
import contextlib
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any, Final

import httpx

from garh_api.config import Settings
from garh_api.logging import get_logger
from garh_api.security import email_domain

_log = get_logger(__name__)

#: Subject line of every code email. A constant so the e2e mail check and the unit
#: tests assert the same string the user's inbox filters on.
OTP_SUBJECT: Final = "Your Garh sign-in code"

#: Plain text only — an OTP mail has one job, and text/plain survives every client,
#: screen reader and corporate filter. The "didn't request this" line is §13 hygiene:
#: an unsolicited code means someone typed this address, and the right response is
#: nothing.
_OTP_BODY_TEMPLATE: Final = """\
Your Garh sign-in code is:

    %(code)s

It expires in %(ttl_minutes)d minute%(plural)s. Enter it on the sign-in screen to
continue.

Didn't request this code? Ignore this email — nobody can sign in without it.
"""


def build_otp_message(
    *, to_email: str, from_addr: str, code: str, ttl_seconds: int
) -> EmailMessage:
    """The sign-in code email, as a stdlib :class:`~email.message.EmailMessage`.

    Split out of the transport so a test can assert on the exact message without a
    socket, and so the body cannot drift between transports if one is ever added.
    """
    ttl_minutes = max(1, ttl_seconds // 60)
    message = EmailMessage()
    message["From"] = from_addr
    message["To"] = to_email
    message["Subject"] = OTP_SUBJECT
    message.set_content(
        _OTP_BODY_TEMPLATE
        % {
            "code": code,
            "ttl_minutes": ttl_minutes,
            "plural": "" if ttl_minutes == 1 else "s",
        }
    )
    return message


class SmtpMailer:
    """Sends the OTP email over SMTP. Satisfies :data:`garh_api.auth.OtpMailer`.

    One connection per message, opened and closed inside the call: OTP volume is
    rate-limited to single digits per address per hour (``garh_api.ratelimit``), so
    pooling would buy nothing and a held-open connection to someone else's relay is
    a liability. STARTTLS is on by default (port 587 submission); AUTH PLAIN/LOGIN
    happens only when a user is configured, because an IP-allowlisted internal
    relay legitimately needs neither.
    """

    def __init__(
        self,
        *,
        host: str,
        from_addr: str,
        port: int = 587,
        user: str = "",
        password: str = "",
        starttls: bool = True,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.from_addr = from_addr
        self.starttls = starttls
        self.timeout_seconds = timeout_seconds

    #: Named so the boot log can say WHICH path is live. On Railway's Hobby tier
    #: SMTP is disabled platform-wide, and "mailer installed" without the transport
    #: name is how that shipped looking healthy.
    transport: Final = "smtp"

    @property
    def from_domain(self) -> str:
        """Log-safe fragment of the sender (handles ``Name <addr>`` forms too)."""
        return email_domain(parseaddr(self.from_addr)[1])

    async def __call__(self, email: str, code: str, ttl_seconds: int) -> None:
        """Deliver one code. Raises on any SMTP failure — the auth layer decides
        what an undelivered code means (it 503s; see ``_deliver_code``)."""
        message = build_otp_message(
            to_email=email, from_addr=self.from_addr, code=code, ttl_seconds=ttl_seconds
        )
        await asyncio.to_thread(self._send_sync, message)
        _log.info(
            "mailer.otp_sent",
            email_domain=email_domain(email),
            smtp_host=self.host,
        )

    def _send_sync(self, message: EmailMessage) -> None:
        """The blocking SMTP conversation. Runs on a thread, never on the loop."""
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout_seconds) as client:
            if self.starttls:
                # starttls() EHLOs for us if needed, and re-EHLOs after the upgrade.
                # An explicit default context: without one, smtplib upgrades with an
                # UNVERIFIED context (CERT_NONE, no hostname check), so the relay's
                # credentials and the code would go to whoever answers at SMTP_HOST.
                client.starttls(context=ssl.create_default_context())
            if self.user:
                client.login(self.user, self.password)
            client.send_message(message)


#: Brevo's transactional endpoint. v3 is their current API; the key that
#: authenticates it is an ``xkeysib-…`` API key, NOT the ``xsmtpsib-…`` SMTP key —
#: they are different credentials from different tabs of the same console.
BREVO_SEND_URL: Final = "https://api.brevo.com/v3/smtp/email"


class BrevoHttpMailer:
    """Sends the OTP email through Brevo's HTTPS API. Satisfies ``OtpMailer``.

    WHY THIS EXISTS BESIDE THE SMTP MAILER
    --------------------------------------
    Railway disables outbound SMTP on Free, Trial and Hobby plans — every port, not
    just 25 (``docs.railway.com/networking/outbound-networking``: *"SMTP is only
    available on the Pro plan and above"*). The first live sign-up on the deployed
    stack timed out on ``smtp-relay.brevo.com:587`` after exactly the SMTP mailer's
    15-second connect timeout, and Brevo never saw the connection. HTTPS egress is
    open on every plan — this container already reaches Anthropic and Stability
    over it — so the platform's own recommendation is the provider's HTTP API.

    The message body comes from the SAME :func:`build_otp_message` the SMTP path
    uses, so the two transports cannot drift: what an architect reads is identical
    whichever way it travelled.

    One request per message, one client per mailer (``httpx`` pools per client), a
    hard timeout, and no retries here — the auth layer already 503s with a
    retry-after and the rate limiter caps resends. Raises on any non-2xx so
    ``_deliver_code``'s existing blanket ``except`` turns it into the same honest
    "couldn't send" the SMTP path produces.
    """

    transport: Final = "brevo-http"
    #: Reported in the boot log where the SMTP mailer reports its relay host.
    host: Final = "api.brevo.com"

    def __init__(
        self,
        *,
        api_key: str,
        from_addr: str,
        timeout_seconds: float = 15.0,
        transport_override: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.from_addr = from_addr
        self.timeout_seconds = timeout_seconds
        # The injectable transport is for tests only: a strict MockTransport pins the
        # exact wire format without a socket, the same way the Stability provider is
        # tested. Production never passes one.
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            transport=transport_override,
            headers={"accept": "application/json", "content-type": "application/json"},
        )

    @property
    def from_domain(self) -> str:
        return email_domain(parseaddr(self.from_addr)[1])

    def _payload(self, message: EmailMessage) -> dict[str, Any]:
        name, addr = parseaddr(message["From"])
        sender: dict[str, str] = {"email": addr}
        if name:
            sender["name"] = name
        return {
            "sender": sender,
            "to": [{"email": message["To"]}],
            "subject": message["Subject"],
            "textContent": message.get_content(),
        }

    async def __call__(self, email: str, code: str, ttl_seconds: int) -> None:
        message = build_otp_message(
            to_email=email, from_addr=self.from_addr, code=code, ttl_seconds=ttl_seconds
        )
        response = await self._client.post(
            BREVO_SEND_URL,
            json=self._payload(message),
            headers={"api-key": self.api_key},
        )
        if response.status_code >= 300:
            # Status and Brevo's own error code only — the body can echo the
            # recipient address, and this log line must stay PII-free.
            detail = ""
            # A non-JSON error body is still an error; the status alone is enough.
            with contextlib.suppress(Exception):
                detail = str(response.json().get("code", ""))
            _log.error(
                "mailer.otp_http_rejected",
                status=response.status_code,
                brevo_code=detail,
                email_domain=email_domain(email),
            )
            raise RuntimeError(
                "Brevo rejected the send: HTTP %d %s" % (response.status_code, detail)
            )
        _log.info(
            "mailer.otp_sent",
            email_domain=email_domain(email),
            transport=self.transport,
            host=self.host,
        )


def build_mailer(settings: Settings) -> SmtpMailer | BrevoHttpMailer | None:
    """The configured mailer, or ``None`` when mail is off.

    ``None`` is meaningful to the caller: installing it via ``set_otp_mailer``
    *clears* the hook, so a process whose configuration lost its SMTP settings
    cannot keep sending through a stale mailer.
    """
    if settings.brevo_configured:
        # Precedence is deliberate: on a platform where SMTP is blocked, an operator
        # who set BOTH must get the one that works, not the one listed first.
        return BrevoHttpMailer(api_key=settings.brevo_api_key, from_addr=settings.smtp_from)
    if not settings.smtp_configured:
        return None
    return SmtpMailer(
        host=settings.smtp_host,
        port=settings.smtp_port,
        user=settings.smtp_user,
        password=settings.smtp_password,
        from_addr=settings.smtp_from,
        starttls=settings.smtp_starttls,
    )


__all__ = [
    "OTP_SUBJECT",
    "SmtpMailer",
    "build_mailer",
    "build_otp_message",
]
