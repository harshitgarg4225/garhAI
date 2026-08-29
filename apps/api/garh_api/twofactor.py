"""TOTP second factor (F-4): the algorithm, the codes, and the policy around them.

This module is deliberately the *only* place the second-factor rules live, in the same
way :mod:`garh_api.auth` owns sign-in policy. It knows about time, HMAC and Redis
attempt counters; it knows nothing about HTTP. The row lives in
:class:`garh_api.models.UserTwoFactor` and is reached through
:class:`garh_api.repositories.two_factor.TwoFactorRepository`.

**Why this matters here.** The deployed API runs with ``APP_ENV=dev`` and no
``SMTP_HOST``, so :func:`garh_api.auth.dev_echo_otp_enabled` hands the sign-in code back
in the response body: anyone who knows an address on the instance can sign in as that
person. That cannot be fixed without mail credentials. A second factor can be enrolled
*without* mail, and once a user has one, knowing the echoed OTP is no longer enough.

**Algorithm.** RFC 6238 TOTP over RFC 4226 HOTP: HMAC-SHA1, 6 digits, 30-second step,
±1 step of accepted drift. Those are not preferences — they are what Google
Authenticator, Authy, 1Password and iOS Passwords implement, and an authenticator the
user already has is the whole point. SHA-1 here is a MAC keyed with 160 bits of secret,
not a collision-resistance claim; RFC 6238 §1.2 and every shipped authenticator use it.
Implemented on ``hmac``/``hashlib``/``base64``/``secrets`` from the standard library, so
no dependency and nothing to add to DECISIONS.md.

**Three things that are easy to get wrong, and what this module does about each.**

*Replay.* A TOTP code is valid for its whole 30-second step (plus drift), so a code
read over a shoulder, out of a proxy log or off a phishing page can be spent twice.
:func:`verify_totp` therefore takes the highest step already spent and refuses anything
at or below it; :meth:`TwoFactorService.verify_second_factor` writes the new high-water
mark back before the session is issued.

*Brute force.* Six digits is 10^6, which falls in minutes at HTTP speed. Every
verification path — sign-in, disable, code regeneration — is charged against
:func:`two_factor_attempt_rule` (5 attempts per 10 minutes per user, **fail-closed**:
if Redis cannot answer we refuse rather than admit). A verification that *succeeds*
releases the budget again, so the five are five wrong guesses rather than five uses:
signing in on a sixth device inside ten minutes is not an attack.

*Lock-out.* Recovery codes are the only way back in from a lost phone, and this is the
path that is tested hardest (``tests/test_twofactor.py``). They are 80-bit random
strings shown exactly once, stored as ``sha256`` digests, single-use (a spent code is
*removed* from the list, so "how many are left" is ``len()`` and cannot drift), and a
recovery code is accepted anywhere a TOTP code is — including disabling the factor
entirely, which is what a user with a dead phone actually needs.

**What is not here.** The secret is stored in plaintext in ``user_two_factor.secret``.
Column-level encryption needs a symmetric key in ``Settings`` and there is none; the
JWT keypair is *ephemeral in dev*, so deriving one from it would make every enrolment
undecryptable after a restart — a guaranteed lock-out in exchange for defence-in-depth
against an attacker who already has the database. Encryption at rest is the deployment
control today. See the handoff note for the follow-up.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import struct
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from garh_api.config import Settings, get_settings
from garh_api.errors import ApiError
from garh_api.logging import get_logger
from garh_api.ratelimit import (
    TWO_FACTOR_ATTEMPT_WINDOW_SECONDS,
    TWO_FACTOR_MAX_ATTEMPTS,
    enforce_rate_limit,
    reset_rate_limit,
    two_factor_attempt_rule,
)
from garh_api.repositories.two_factor import TwoFactorEnrolment, TwoFactorRepository
from garh_api.security import (
    TOKEN_TYPE_TWO_FACTOR,
    create_two_factor_challenge,
    hash_secret,
)

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Algorithm parameters — every one of these is an interoperability constraint
# ---------------------------------------------------------------------------

#: Digits in a TOTP code. Six is what every authenticator app renders.
TOTP_DIGITS: Final = 6

#: Seconds per step (RFC 6238 default, and the only value apps display a ring for).
TOTP_STEP_SECONDS: Final = 30

#: Steps of clock drift accepted either side of "now". One step = ±30s, which covers a
#: phone whose clock is a little off without widening the guessable window to 3 codes
#: for longer than it has to be.
TOTP_DRIFT_STEPS: Final = 1

#: Bytes of shared secret. 20 bytes = 160 bits = one SHA-1 block, RFC 4226 §4 R6, and
#: exactly 32 base32 characters — so the encoded form never needs ``=`` padding, which
#: several authenticator apps mishandle in an ``otpauth://`` URI.
SECRET_BYTES: Final = 20

#: How many recovery codes an enrolment gets.
RECOVERY_CODE_COUNT: Final = 10

#: Bytes per recovery code. 10 bytes = 80 bits = 16 base32 characters. That is what
#: makes an *unsalted* ``sha256`` safe: there is no dictionary to try, and 2^80 offline
#: guesses is not a threat model anyone reaches.
RECOVERY_CODE_BYTES: Final = 10

#: How long the "you passed the first factor" ticket lives. Long enough to open an
#: authenticator app and read a code, short enough that a leaked challenge is stale.
CHALLENGE_TTL_SECONDS: Final = 300

#: Issuer label shown in the authenticator app's list.
TOTP_ISSUER: Final = "Garh AI"

#: Everything a human might type between the groups of a recovery code, plus
#: whitespace. Spelled with escapes rather than literal dashes — the same reason
#: ``schemas/auth.py`` does: the intent has to survive a copy-paste, and a literal
#: ``-`` next to ``\s`` inside a character class is a *range*, not a dash.
_CODE_NOISE_RE: Final = re.compile(r"[\s\u002d\u2010-\u2015\u2212_.]")

#: Codes this module can emit in problem+json. They are declared here rather than in
#: :mod:`garh_api.errors` because this agent does not own that module; promoting them
#: into ``errors.ERROR_CODES`` is the follow-up (see the handoff note).
CODE_TWO_FACTOR_REQUIRED: Final = "two_factor_required"
CODE_TWO_FACTOR_INVALID: Final = "two_factor_invalid"
CODE_TWO_FACTOR_STATE: Final = "two_factor_state"

TWO_FACTOR_ERROR_CODES: tuple[str, ...] = (
    CODE_TWO_FACTOR_REQUIRED,
    CODE_TWO_FACTOR_INVALID,
    CODE_TWO_FACTOR_STATE,
)

# ---------------------------------------------------------------------------
# Audit actions
# ---------------------------------------------------------------------------
# §13 requires an audit row on auth events, and turning a second factor on or off is
# one. They are declared here rather than in ``repositories.audit_log.AUDIT_ACTIONS``
# for the same ownership reason as the error codes above; the registry is the right
# long-term home and moving them is a one-line change per constant (handoff note).
# ``AuditLogRepository.record`` takes a free string precisely so a new action cannot
# 500 a security write path.

ACTION_TWO_FACTOR_CHALLENGED: Final = "auth.two_factor_challenged"
ACTION_TWO_FACTOR_FAILED: Final = "auth.two_factor_failed"
ACTION_TWO_FACTOR_ENABLED: Final = "auth.two_factor_enabled"
ACTION_TWO_FACTOR_DISABLED: Final = "auth.two_factor_disabled"
ACTION_TWO_FACTOR_RECOVERY_USED: Final = "auth.two_factor_recovery_used"
ACTION_TWO_FACTOR_RECOVERY_REGENERATED: Final = "auth.two_factor_recovery_regenerated"

TWO_FACTOR_AUDIT_ACTIONS: tuple[str, ...] = (
    ACTION_TWO_FACTOR_CHALLENGED,
    ACTION_TWO_FACTOR_FAILED,
    ACTION_TWO_FACTOR_ENABLED,
    ACTION_TWO_FACTOR_DISABLED,
    ACTION_TWO_FACTOR_RECOVERY_USED,
    ACTION_TWO_FACTOR_RECOVERY_REGENERATED,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TwoFactorRequiredError(ApiError):
    """The password-equivalent factor passed; the second one has not been presented.

    403 rather than 401: the caller *is* authenticated as far as the first factor
    goes, and a 401 obliges us to send a ``WWW-Authenticate`` challenge for a scheme
    no client implements. The body carries a short-lived ``challenge`` the client
    posts back to ``/auth/2fa/verify`` together with the code — the same shape Auth0's
    ``mfa_required`` uses, and the reason it is safe to put in an error body is that
    the challenge alone proves nothing: it is worthless without a live TOTP code.
    """

    http_status = 403
    code = CODE_TWO_FACTOR_REQUIRED
    default_message = "Enter the code from your authenticator app to finish signing in."
    action = "Open your authenticator app and enter the 6-digit code."

    def __init__(self, challenge: str, *, expires_in_seconds: int) -> None:
        super().__init__(
            extra={"challenge": challenge, "expiresInSeconds": int(expires_in_seconds)}
        )


class TwoFactorInvalidError(ApiError):
    """One answer for every second-factor failure.

    Wrong code, expired challenge, replayed code and unknown recovery code all render
    identically, for the same reason :class:`~garh_api.errors.OtpVerificationError`
    does: telling them apart says whether a guess was *close*.
    """

    http_status = 400
    code = CODE_TWO_FACTOR_INVALID
    default_message = "That code didn't work."
    action = "Wait for the next code in your authenticator app, or use a recovery code."


class TwoFactorStateError(ApiError):
    """Two-factor is already on when you asked to turn it on, or off when you asked
    to use it. A state error, never a credential error — it leaks nothing a signed-in
    user does not already know about their own account."""

    http_status = 409
    code = CODE_TWO_FACTOR_STATE
    default_message = "Two-factor authentication isn't in the right state for that."
    action = "Reload your security settings and try again."


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
#
# :func:`two_factor_attempt_rule` and its two numbers now live in
# :mod:`garh_api.ratelimit`, beside ``RULE_FACTORIES`` — the registry the security
# checklist audits. They are re-exported here (see ``__all__``) so this module stays
# the one import site for second-factor policy, but the *definition* sits next to the
# tuple it has to be in, because a rule defined elsewhere is a rule somebody has to
# remember to register. This one was not registered for its whole life until F-4's
# review caught it.


# ---------------------------------------------------------------------------
# The algorithm (RFC 4226 / RFC 6238)
# ---------------------------------------------------------------------------


def generate_secret() -> str:
    """A fresh base32 TOTP secret, 160 bits, no padding."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    """base32 → bytes, tolerating lowercase and missing padding.

    Raises ``ValueError`` rather than returning a wrong key: a silently mis-decoded
    secret would make every code fail and look exactly like a broken phone clock.
    """
    cleaned = (secret or "").strip().replace(" ", "").upper()
    if not cleaned:
        raise ValueError("empty TOTP secret")
    padded = cleaned + "=" * (-len(cleaned) % 8)
    try:
        return base64.b32decode(padded, casefold=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("TOTP secret is not valid base32") from exc


def counter_at(moment: float | None = None) -> int:
    """The RFC 6238 time step containing ``moment`` (default: now)."""
    seconds = time.time() if moment is None else float(moment)
    return int(seconds // TOTP_STEP_SECONDS)


def totp_at(secret: str, counter: int, *, digits: int = TOTP_DIGITS) -> str:
    """The HOTP value for one step — RFC 4226 §5.3 dynamic truncation."""
    if counter < 0:
        raise ValueError("TOTP counter cannot be negative")
    digest = hmac.new(_decode_secret(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**digits)).rjust(digits, "0")


def normalise_totp_code(value: str) -> str:
    """What people actually type: ``123 456``, ``123-456``, ``  123456 ``."""
    return _CODE_NOISE_RE.sub("", value or "")


def verify_totp(
    secret: str,
    code: str,
    *,
    last_counter: int = -1,
    moment: float | None = None,
    drift: int = TOTP_DRIFT_STEPS,
) -> int | None:
    """Check one code. Returns the step it matched, or ``None``.

    ``last_counter`` is the replay guard and it is not optional: a code that matches a
    step at or below the highest already spent is rejected even though the arithmetic
    is correct. Without it, one observed code works for its whole 30-second window on
    every device that sees it.
    """
    cleaned = normalise_totp_code(code)
    if len(cleaned) != TOTP_DIGITS or not cleaned.isdigit():
        return None
    now_step = counter_at(moment)
    matched: int | None = None
    for offset in range(-abs(drift), abs(drift) + 1):
        candidate = now_step + offset
        if candidate < 0:
            continue
        # No early break: every attempt walks the same number of steps, so the
        # response time does not say which end of the window matched.
        if hmac.compare_digest(totp_at(secret, candidate), cleaned):
            matched = candidate
    if matched is None or matched <= last_counter:
        return None
    return matched


def otpauth_uri(secret: str, *, account: str, issuer: str = TOTP_ISSUER) -> str:
    """The ``otpauth://`` URI an authenticator app scans as a QR code.

    Both the label and the ``issuer`` parameter carry the issuer, which is what the
    Key Uri Format spec asks for and what makes the entry read "Garh AI (name@studio.in)"
    rather than an anonymous six digits in a list of twenty.
    """
    label = urllib.parse.quote("%s:%s" % (issuer, account), safe="")
    query = urllib.parse.urlencode(
        {
            "secret": secret,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": TOTP_DIGITS,
            "period": TOTP_STEP_SECONDS,
        }
    )
    return "otpauth://totp/%s?%s" % (label, query)


# ---------------------------------------------------------------------------
# Recovery codes
# ---------------------------------------------------------------------------


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """``count`` single-use codes, formatted for a human to write down.

    ``XXXX-XXXX-XXXX-XXXX``: 16 base32 characters, 80 bits, grouped so it can be read
    off paper without losing your place. The groups are cosmetic —
    :func:`normalise_recovery_code` strips them before hashing, so a user who types it
    without dashes still gets in.
    """
    codes: list[str] = []
    for _ in range(max(1, int(count))):
        raw = base64.b32encode(secrets.token_bytes(RECOVERY_CODE_BYTES)).decode("ascii")
        body = raw.rstrip("=")
        codes.append("-".join(body[index : index + 4] for index in range(0, len(body), 4)))
    return codes


def normalise_recovery_code(value: str) -> str:
    """Uppercase, dashes and spaces removed. The form that gets hashed."""
    return _CODE_NOISE_RE.sub("", (value or "").strip()).upper()


def hash_recovery_code(value: str) -> str:
    """``sha256`` of the normalised code — the only form ever stored.

    Unsalted on purpose: see :data:`RECOVERY_CODE_BYTES`. Salting would buy nothing
    against 80 bits of entropy and would cost a second column.
    """
    return hash_secret(normalise_recovery_code(value))


def match_recovery_code(value: str, hashes: list[str]) -> str | None:
    """The stored digest this code matches, or ``None``. Constant time per entry."""
    candidate = hash_recovery_code(value)
    if not normalise_recovery_code(value):
        return None
    found: str | None = None
    for stored in hashes:
        if hmac.compare_digest(str(stored), candidate):
            found = str(stored)
    return found


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnrolmentStart:
    """What ``POST /auth/2fa/enrol`` hands back. The secret is shown once."""

    secret: str
    otpauth_uri: str


@dataclass(frozen=True)
class TwoFactorStatus:
    """What the security screen renders."""

    enabled: bool
    pending: bool
    confirmed_at: datetime | None
    recovery_codes_remaining: int


@dataclass(frozen=True)
class SecondFactorResult:
    """A verified second factor: which kind, and what to persist."""

    used_recovery_code: bool
    recovery_codes_remaining: int


class TwoFactorService:
    """Enrol, verify, disable. One object so the rules cannot be half-applied.

    Constructed with an already-scoped :class:`TwoFactorRepository`, so nothing in
    here can reach another firm's row — the tenancy guarantee is the repository's, not
    this class's, exactly as everywhere else in the API.
    """

    def __init__(
        self,
        repo: TwoFactorRepository,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._repo = repo
        self._settings = settings or get_settings()

    # -- reads ---------------------------------------------------------
    async def status(self, user_id: uuid.UUID) -> TwoFactorStatus:
        record = await self._repo.for_user(user_id)
        if record is None:
            return TwoFactorStatus(
                enabled=False, pending=False, confirmed_at=None, recovery_codes_remaining=0
            )
        return TwoFactorStatus(
            enabled=record.is_confirmed,
            pending=not record.is_confirmed,
            confirmed_at=record.confirmed_at,
            recovery_codes_remaining=len(record.recovery_hashes),
        )

    async def is_enabled(self, user_id: uuid.UUID) -> bool:
        """Is a *confirmed* second factor in force for this user?

        An unconfirmed enrolment deliberately answers ``False``: a user who scanned a
        QR code and closed the tab must still be able to sign in.
        """
        record = await self._repo.for_user(user_id)
        return record is not None and record.is_confirmed

    # -- enrolment -----------------------------------------------------
    async def begin_enrolment(self, user_id: uuid.UUID, *, account: str) -> EnrolmentStart:
        """Mint a secret and stage it unconfirmed. Idempotent-ish: calling twice
        replaces the *unconfirmed* secret, which is what "the QR code didn't scan,
        show me another" needs. Refuses once a factor is live — turning it off is a
        deliberate, code-proving action, not a side effect of pressing "enrol"."""
        existing = await self._repo.for_user(user_id)
        if existing is not None and existing.is_confirmed:
            raise TwoFactorStateError("Two-factor authentication is already on for this account.")
        secret = generate_secret()
        await self._repo.upsert_pending(user_id, secret=secret)
        _log.info("twofactor.enrolment_started", user_id=str(user_id))
        return EnrolmentStart(secret=secret, otpauth_uri=otpauth_uri(secret, account=account))

    async def activate(self, user_id: uuid.UUID, code: str) -> list[str]:
        """Prove the staged secret with a live code, then hand back recovery codes.

        The codes are returned in plaintext exactly here and nowhere else; only their
        digests are stored. Activation is charged against the attempt limit like every
        other verification, because an enrolment endpoint that is not rate limited is
        an oracle for whether a given secret is live.
        """
        record = await self._repo.for_user(user_id)
        if record is None:
            raise TwoFactorStateError("Start by scanning the QR code — there's nothing to confirm.")
        if record.is_confirmed:
            raise TwoFactorStateError("Two-factor authentication is already on for this account.")

        await self._charge_attempt(user_id)
        counter = verify_totp(record.secret, code, last_counter=record.last_counter)
        if counter is None:
            _log.info("twofactor.activation_failed", user_id=str(user_id))
            raise TwoFactorInvalidError()

        await self._clear_attempts(user_id)
        codes = generate_recovery_codes()
        await self._repo.confirm(
            user_id,
            last_counter=counter,
            recovery_hashes=[hash_recovery_code(code_value) for code_value in codes],
            confirmed_at=datetime.now(UTC),
        )
        _log.info("twofactor.enabled", user_id=str(user_id), recovery_codes=len(codes))
        return codes

    async def regenerate_recovery_codes(self, user_id: uuid.UUID, code: str) -> list[str]:
        """Replace the recovery set. Requires a live factor and a fresh proof.

        Proving the factor first is what stops a hijacked *session* from minting itself
        a permanent way back in after the real owner rotates the secret.
        """
        record = await self._require_enabled(user_id)
        await self._verify_against(record, code)
        codes = generate_recovery_codes()
        await self._repo.replace_recovery_hashes(
            user_id, [hash_recovery_code(value) for value in codes]
        )
        _log.info("twofactor.recovery_codes_regenerated", user_id=str(user_id))
        return codes

    async def disable(self, user_id: uuid.UUID, code: str) -> None:
        """Turn the factor off. A recovery code is accepted here on purpose.

        This is the lost-phone path: the user cannot produce a TOTP code by
        definition, so if only TOTP were accepted the recovery codes would let them
        *sign in* and then strand them with a factor they can never satisfy again.
        """
        record = await self._require_enabled(user_id)
        await self._verify_against(record, code)
        await self._repo.remove(user_id)
        _log.info("twofactor.disabled", user_id=str(user_id))

    # -- sign-in -------------------------------------------------------
    def challenge_for(self, *, user_id: uuid.UUID, firm_id: uuid.UUID, role: str) -> str:
        """Mint the "first factor passed" ticket handed back with the 403."""
        token, _expires = create_two_factor_challenge(
            user_id=user_id,
            firm_id=firm_id,
            role=role,
            ttl_seconds=CHALLENGE_TTL_SECONDS,
            settings=self._settings,
        )
        return token

    async def verify_second_factor(self, user_id: uuid.UUID, code: str) -> SecondFactorResult:
        """Spend a TOTP or recovery code. Raises unless it was good.

        Whatever it spends is spent: a TOTP step is recorded as used, a recovery code
        is removed from the list. Both writes happen before the caller issues a
        session, so a crash between here and the token leaves the credential consumed
        rather than replayable.
        """
        record = await self._require_enabled(user_id)
        return await self._verify_against(record, code)

    # -- internals -----------------------------------------------------
    async def _require_enabled(self, user_id: uuid.UUID) -> TwoFactorEnrolment:
        record = await self._repo.for_user(user_id)
        if record is None or not record.is_confirmed:
            raise TwoFactorStateError("Two-factor authentication isn't on for this account.")
        return record

    async def _charge_attempt(self, user_id: uuid.UUID) -> None:
        await enforce_rate_limit(
            two_factor_attempt_rule(self._settings),
            "user:%s" % user_id,
            settings=self._settings,
        )

    async def _clear_attempts(self, user_id: uuid.UUID) -> None:
        """Hand the budget back after a *successful* verification.

        The limit exists to bound guessing, and a proof of possession ends the guess.
        Without this the budget counts successes too, so somebody signing in on a sixth
        device inside ten minutes — or activating, then signing in, then rotating their
        recovery codes — is refused for doing everything right; and a legitimate user
        who mistypes twice has three real attempts left rather than five.

        Best effort on purpose: :func:`~garh_api.ratelimit.reset_rate_limit` swallows a
        Redis failure, and the correct behaviour when the reset cannot be written is to
        leave the slots spent (fail towards the limit) rather than to fail the sign-in
        that has already succeeded.
        """
        await reset_rate_limit(
            two_factor_attempt_rule(self._settings),
            "user:%s" % user_id,
            settings=self._settings,
        )

    async def _verify_against(self, record: TwoFactorEnrolment, code: str) -> SecondFactorResult:
        """TOTP first, recovery code second, one error for both.

        Every attempt is charged before anything is checked, and a *successful* one
        hands the budget straight back — the limit is there to bound guessing, and a
        proof of possession is not a guess.
        """
        await self._charge_attempt(record.user_id)

        counter = verify_totp(record.secret, code, last_counter=record.last_counter)
        if counter is not None:
            await self._repo.record_counter(record.user_id, counter)
            await self._clear_attempts(record.user_id)
            return SecondFactorResult(
                used_recovery_code=False,
                recovery_codes_remaining=len(record.recovery_hashes),
            )

        spent = match_recovery_code(code, record.recovery_hashes)
        if spent is not None:
            remaining = await self._repo.spend_recovery_hash(record.user_id, spent)
            await self._clear_attempts(record.user_id)
            _log.warning(
                "twofactor.recovery_code_used",
                user_id=str(record.user_id),
                remaining=remaining,
            )
            return SecondFactorResult(used_recovery_code=True, recovery_codes_remaining=remaining)

        _log.info("twofactor.verification_failed", user_id=str(record.user_id))
        raise TwoFactorInvalidError()


def status_payload(status: TwoFactorStatus) -> dict[str, Any]:
    """The status dataclass as the wire shape, in one place so the router and the
    DPDP export (F-6) cannot disagree about what "enabled" means."""
    return {
        "enabled": status.enabled,
        "pending": status.pending,
        "confirmedAt": status.confirmed_at.isoformat() if status.confirmed_at else None,
        "recoveryCodesRemaining": status.recovery_codes_remaining,
    }


__all__ = [
    "ACTION_TWO_FACTOR_CHALLENGED",
    "ACTION_TWO_FACTOR_DISABLED",
    "ACTION_TWO_FACTOR_ENABLED",
    "ACTION_TWO_FACTOR_FAILED",
    "ACTION_TWO_FACTOR_RECOVERY_REGENERATED",
    "ACTION_TWO_FACTOR_RECOVERY_USED",
    "CHALLENGE_TTL_SECONDS",
    "CODE_TWO_FACTOR_INVALID",
    "CODE_TWO_FACTOR_REQUIRED",
    "CODE_TWO_FACTOR_STATE",
    "RECOVERY_CODE_COUNT",
    "TOKEN_TYPE_TWO_FACTOR",
    "TOTP_DIGITS",
    "TOTP_DRIFT_STEPS",
    "TOTP_STEP_SECONDS",
    "TWO_FACTOR_ATTEMPT_WINDOW_SECONDS",
    "TWO_FACTOR_AUDIT_ACTIONS",
    "TWO_FACTOR_ERROR_CODES",
    "TWO_FACTOR_MAX_ATTEMPTS",
    "EnrolmentStart",
    "SecondFactorResult",
    "TwoFactorInvalidError",
    "TwoFactorRequiredError",
    "TwoFactorService",
    "TwoFactorStateError",
    "TwoFactorStatus",
    "counter_at",
    "generate_recovery_codes",
    "generate_secret",
    "hash_recovery_code",
    "match_recovery_code",
    "normalise_recovery_code",
    "normalise_totp_code",
    "otpauth_uri",
    "status_payload",
    "totp_at",
    "two_factor_attempt_rule",
    "verify_totp",
]
