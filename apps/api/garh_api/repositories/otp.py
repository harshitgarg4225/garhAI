"""Email-OTP repository (§13: 10 minute expiry, 5 attempts).

Non-tenant by necessity: an OTP is issued and verified *before* the firm is known.

Storage discipline — the plaintext code never touches the database or a log:

* the caller generates the code, hashes it with :func:`hash_otp_code`, and stores the
  hash;
* verification hashes the attempt and compares with
  :func:`hmac.compare_digest` (constant time), so timing cannot leak the code;
* attempts are counted in the row and capped at 5, and expiry is a stored timestamp
  rather than something inferred from ``created_at`` at read time.

This layer owns the counting and expiry rules. Emailing, throttling per IP, and JWT
minting belong to the auth layer above it.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from garh_api import models
from garh_api.config import get_settings
from garh_api.logging import get_logger
from garh_api.repositories.domain import OtpChallenge
from garh_api.repositories.users import normalise_email
from garh_api.tenancy import RepositoryUsageError

_log = get_logger(__name__)

#: Outcomes of :meth:`OtpCodeRepository.verify`. The auth layer maps every failure to
#: the *same* user-facing message ("That code didn't work — request a new one") so an
#: attacker learns nothing about which condition tripped.
VERIFY_OK = "ok"
VERIFY_NO_CHALLENGE = "no_challenge"
VERIFY_EXPIRED = "expired"
VERIFY_CONSUMED = "consumed"
VERIFY_TOO_MANY_ATTEMPTS = "too_many_attempts"
VERIFY_MISMATCH = "mismatch"


def hash_otp_code(email: str, code: str) -> str:
    """``sha256(email || ":" || code)``.

    Salting with the email means one stolen hash cannot be replayed against a
    different account, and identical codes issued to two users hash differently.
    """
    material = "%s:%s" % (normalise_email(email), code)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def generate_otp_code(length: int | None = None) -> str:
    """Cryptographically random numeric code (default 6 digits)."""
    size = length if length is not None else get_settings().otp_code_length
    if size < 4:
        raise RepositoryUsageError("An OTP needs at least 4 digits.")
    return "".join(str(secrets.randbelow(10)) for _ in range(size))


@dataclass(frozen=True)
class OtpVerification:
    """Result of a verification attempt."""

    outcome: str
    challenge: OtpChallenge | None = None
    attempts_remaining: int = 0

    @property
    def ok(self) -> bool:
        return self.outcome == VERIFY_OK


class OtpCodeRepository:
    """Non-tenant repository for ``otp_codes``.

    Constructor::

        OtpCodeRepository(session: AsyncSession)
    """

    entity_name = "otp_code"

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- writes --------------------------------------------------------
    async def issue(
        self,
        email: str,
        code: str,
        *,
        ttl_seconds: int | None = None,
        meta: dict[str, Any] | None = None,
        invalidate_previous: bool = True,
    ) -> OtpChallenge:
        """Store a new challenge. Pass the plaintext ``code``; only its hash is kept.

        ``invalidate_previous`` consumes any outstanding challenge for the address so
        only the newest code works — otherwise "resend" would widen the guessing
        window instead of narrowing it.
        """
        clean_email = normalise_email(email)
        if "@" not in clean_email:
            raise RepositoryUsageError("That doesn't look like an email address.")
        settings = get_settings()
        ttl = ttl_seconds if ttl_seconds is not None else settings.otp_ttl_seconds
        if ttl <= 0:
            raise RepositoryUsageError("OTP ttl must be positive.")

        if invalidate_previous:
            await self._session.execute(
                update(models.OtpCode)
                .where(models.OtpCode.email == clean_email)
                .where(models.OtpCode.consumed_at.is_(None))
                .values(consumed_at=datetime.now(UTC))
            )

        row = models.OtpCode(
            email=clean_email,
            code_hash=hash_otp_code(clean_email, code),
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
            attempts=0,
            meta=meta or {},
        )
        self._session.add(row)
        await self._session.flush()
        _log.info(
            "otp.issued",
            otp_id=str(row.id),
            email_domain=clean_email.partition("@")[2],
            ttl_seconds=ttl,
        )
        return OtpChallenge.from_row(row)

    async def verify(self, email: str, code: str) -> OtpVerification:
        """Check a submitted code against the newest live challenge.

        Consumes the challenge on success. On mismatch, increments ``attempts`` and
        consumes the challenge once the cap is reached, so five wrong guesses end the
        challenge rather than allowing unlimited retries against one code.
        """
        clean_email = normalise_email(email)
        settings = get_settings()
        now = datetime.now(UTC)

        stmt = (
            select(models.OtpCode)
            .where(models.OtpCode.email == clean_email)
            .where(models.OtpCode.consumed_at.is_(None))
            .order_by(models.OtpCode.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        result = await self._session.execute(stmt)
        row = result.scalars().first()
        if row is None:
            return OtpVerification(outcome=VERIFY_NO_CHALLENGE)
        if row.expires_at <= now:
            row.consumed_at = now
            await self._session.flush()
            return OtpVerification(outcome=VERIFY_EXPIRED, challenge=OtpChallenge.from_row(row))
        if row.attempts >= settings.otp_max_attempts:
            row.consumed_at = now
            await self._session.flush()
            return OtpVerification(
                outcome=VERIFY_TOO_MANY_ATTEMPTS, challenge=OtpChallenge.from_row(row)
            )

        expected = row.code_hash
        supplied = hash_otp_code(clean_email, code or "")
        if not hmac.compare_digest(expected, supplied):
            row.attempts = row.attempts + 1
            remaining = max(0, settings.otp_max_attempts - row.attempts)
            if remaining == 0:
                row.consumed_at = now
            await self._session.flush()
            _log.info(
                "otp.mismatch",
                otp_id=str(row.id),
                attempts=row.attempts,
                attempts_remaining=remaining,
            )
            return OtpVerification(
                outcome=VERIFY_MISMATCH,
                challenge=OtpChallenge.from_row(row),
                attempts_remaining=remaining,
            )

        row.consumed_at = now
        await self._session.flush()
        _log.info("otp.verified", otp_id=str(row.id))
        return OtpVerification(outcome=VERIFY_OK, challenge=OtpChallenge.from_row(row))

    async def consume(self, otp_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            update(models.OtpCode)
            .where(models.OtpCode.id == otp_id)
            .where(models.OtpCode.consumed_at.is_(None))
            .values(consumed_at=datetime.now(UTC))
        )
        return bool(result.rowcount)

    async def purge_expired(self, *, before: datetime | None = None) -> int:
        """Delete spent/expired challenges. Housekeeping for a worker."""
        cutoff = before or datetime.now(UTC)
        result = await self._session.execute(
            delete(models.OtpCode).where(models.OtpCode.expires_at <= cutoff)
        )
        count = int(result.rowcount or 0)
        if count:
            _log.info("otp.purged", count=count)
        return count

    # -- reads ---------------------------------------------------------
    async def latest_active(self, email: str) -> OtpChallenge | None:
        stmt = (
            select(models.OtpCode)
            .where(models.OtpCode.email == normalise_email(email))
            .where(models.OtpCode.consumed_at.is_(None))
            .where(models.OtpCode.expires_at > datetime.now(UTC))
            .order_by(models.OtpCode.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalars().first()
        return None if row is None else OtpChallenge.from_row(row)

    async def count_issued_since(self, email: str, since: datetime) -> int:
        """Challenges issued to an address since a timestamp — per-address throttling."""
        stmt = (
            select(models.OtpCode.id)
            .where(models.OtpCode.email == normalise_email(email))
            .where(models.OtpCode.created_at >= since)
        )
        result = await self._session.execute(stmt)
        return len(result.all())


__all__ = [
    "VERIFY_CONSUMED",
    "VERIFY_EXPIRED",
    "VERIFY_MISMATCH",
    "VERIFY_NO_CHALLENGE",
    "VERIFY_OK",
    "VERIFY_TOO_MANY_ATTEMPTS",
    "OtpCodeRepository",
    "OtpVerification",
    "generate_otp_code",
    "hash_otp_code",
]
