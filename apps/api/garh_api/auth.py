"""The authentication service (playbook §11 auth endpoints, §13 AuthN checklist).

This module owns *policy*. :mod:`garh_api.security` owns the crypto, the repositories
own the rows, and :mod:`garh_api.routers.auth` owns the HTTP shapes — so the rules
below live in exactly one place and can be unit-tested without a web server.

**Sign-in is email + OTP.** ``POST /auth/otp`` issues a 6-digit code (10 minute expiry,
5 attempts, rate-limited per IP *and* per address). ``POST /auth/verify`` exchanges a
correct code for tokens. Only ``sha256(email:code)`` is stored; the comparison is
constant time (both in :class:`~garh_api.repositories.otp.OtpCodeRepository`).

**Enumeration.** ``POST /auth/otp`` returns the identical body for a known and an
unknown address, sends nothing for the unknown one, and still consumes the rate-limit
budget so the endpoint cannot be used as a directory *or* as a mail cannon. Signup is
the one place that admits an address is taken — see
:class:`~garh_api.errors.EmailAlreadyRegisteredError` for why that trade is deliberate.

**Sessions.** A login starts a *family*: one chain of refresh tokens. Every refresh
rotates — the presented token is marked spent and a fresh one is issued into the same
family. Presenting a spent token is either theft or a double-submit; we cannot tell, so
the whole family dies (§13 "refresh rotation"). ``logout-all`` bumps a per-user
generation counter, which invalidates every access *and* refresh token instantly
without keeping an allow-list of live access tokens.

**Where session state lives: Redis, not Postgres.** There is no ``refresh_tokens``
table in playbook §2 and this agent does not own the schema. Redis is a defensible home
regardless — the records are short-lived, write-heavy, and read on the hot path — but
it has one consequence worth stating plainly: **flushing Redis resets every generation
counter to 0, which un-does past ``logout-all`` calls for any access token still inside
its 15-minute life.** Run Redis with AOF/RDB persistence. Moving the counter to a
``users.token_generation`` column removes the caveat entirely and is the recommended
follow-up (see the contract notes).

Keys (all under ``garh:auth``, hash-tagged by user so a future Redis Cluster keeps one
user's records in one slot)::

    garh:auth:gen:{u:<uid>}                counter, no TTL   — logout-all generation
    garh:auth:ufam:{u:<uid>}               set,     TTL      — this user's live families
    garh:auth:fam:{u:<uid>}:<family>       hash,    TTL      — family state
    garh:auth:rt:{u:<uid>}:<jti>           hash,    TTL      — one refresh token's state
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from garh_api.config import Settings, get_settings
from garh_api.errors import (
    AccountUnknownError,
    EmailAlreadyRegisteredError,
    OtpVerificationError,
    RefreshTokenReuseError,
    RefreshTokenRevokedError,
    ServiceUnavailableError,
    TokenExpiredError,
    TokenInvalidError,
    TokenRevokedError,
)
from garh_api.logging import get_logger
from garh_api.ratelimit import (
    OTP_RESEND_COOLDOWN_SECONDS,
    OtpRoute,
    auth_ip_rule,
    enforce_rate_limit,
    get_redis,
    otp_per_email_rule,
    otp_resend_identity,
    otp_resend_rule,
    reset_rate_limit,
    verify_ip_rule,
)
from garh_api.repositories.audit_log import (
    ACTION_AUTH_LOGOUT,
    ACTION_AUTH_LOGOUT_ALL,
    ACTION_AUTH_OTP_FAILED,
    ACTION_AUTH_OTP_REQUESTED,
    ACTION_AUTH_OTP_VERIFIED,
    ACTION_AUTH_REFRESH_REUSE,
    ACTION_AUTH_SIGNUP,
    ACTION_AUTH_TOKEN_REFRESHED,
    AuditLogRepository,
)
from garh_api.repositories.auth_directory import AuthDirectoryRepository
from garh_api.repositories.domain import AuthPrincipal
from garh_api.repositories.otp import (
    OtpCodeRepository,
    generate_otp_code,
)
from garh_api.repositories.two_factor import TwoFactorRepository
from garh_api.repositories.users import normalise_email
from garh_api.security import (
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
    TOKEN_TYPE_TWO_FACTOR,
    TokenClaims,
    create_access_token,
    create_refresh_token,
    decode_token,
    email_domain,
    new_token_family,
    new_token_id,
    pseudonymise,
)
from garh_api.tenancy import TenantCtx
from garh_api.twofactor import (
    ACTION_TWO_FACTOR_CHALLENGED,
    ACTION_TWO_FACTOR_FAILED,
    ACTION_TWO_FACTOR_RECOVERY_USED,
    CHALLENGE_TTL_SECONDS,
    TwoFactorInvalidError,
    TwoFactorRequiredError,
    TwoFactorService,
    TwoFactorStateError,
)

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Audit actions this module writes
# ---------------------------------------------------------------------------
# ``repositories.audit_log`` owns the canonical list. ``auth.signup``,
# ``auth.logout`` and ``auth.refresh_reuse_detected`` used to be declared here
# instead, because that module had no constants for them — they are now in
# ``AUDIT_ACTIONS`` where a security review greps, and imported above. There is one
# definition of each string; do not re-add a local copy.

#: Every action from the ``AUDIT_ACTIONS`` **registry** that the auth layer emits (the
#: security-checklist test asserts §13's "audit_log on auth events" is actually wired,
#: in both directions — an action listed here and missing from the registry fails it).
#:
#: The second factor (F-4) emits six more, declared in
#: :data:`garh_api.twofactor.TWO_FACTOR_AUDIT_ACTIONS` because that agent does not own
#: the registry module. Promoting them into ``AUDIT_ACTIONS`` and appending them here
#: is one line each; until then this tuple is not the whole auth trail and says so.
AUTH_AUDIT_ACTIONS: tuple[str, ...] = (
    ACTION_AUTH_OTP_REQUESTED,
    ACTION_AUTH_OTP_VERIFIED,
    ACTION_AUTH_OTP_FAILED,
    ACTION_AUTH_TOKEN_REFRESHED,
    ACTION_AUTH_LOGOUT,
    ACTION_AUTH_LOGOUT_ALL,
    ACTION_AUTH_SIGNUP,
    ACTION_AUTH_REFRESH_REUSE,
)


# ---------------------------------------------------------------------------
# Dev-only OTP echo
# ---------------------------------------------------------------------------

#: Env var that turns the dev echo off (it is *on* by default in dev so a fresh clone
#: can sign in with no mail provider configured — golden rule 8, "under 10 minutes").
DEV_ECHO_OTP_ENV: Final = "DEV_ECHO_OTP"

#: The only environments in which the echo can ever happen. ``staging``/``prod`` are
#: absent on purpose and the check is on :attr:`Settings.env`, which pydantic has
#: already constrained to the four literals — so setting ``DEV_ECHO_OTP=1`` in
#: production is inert, not dangerous.
_ECHO_ALLOWED_ENVS: Final = ("dev", "test")

_TRUE_VALUES: Final = frozenset({"1", "true", "yes", "on"})


def dev_echo_otp_enabled(settings: Settings | None = None) -> bool:
    """Should ``POST /auth/otp`` hand the code back in its response?

    Two gates, and both must pass: the environment must be ``dev``/``test``, and
    ``DEV_ECHO_OTP`` must not be explicitly disabled. There is no configuration of
    ``staging``/``prod`` that reaches ``True``.
    """
    cfg = settings or get_settings()
    if cfg.env not in _ECHO_ALLOWED_ENVS:
        return False
    raw = os.environ.get(DEV_ECHO_OTP_ENV)
    if raw is None:
        return True
    return raw.strip().lower() in _TRUE_VALUES


#: ``(email, code, ttl_seconds) -> None``. Installed by the platform, if it has mail.
OtpMailer = Callable[[str, str, int], Awaitable[None]]

_mailer: OtpMailer | None = None


def set_otp_mailer(mailer: OtpMailer | None) -> None:
    """Install the transactional-mail sender.

    Nothing in the MVP installs one — there is no mail provider in the playbook's
    dependency set, and dev does not need one (see :func:`dev_echo_otp_enabled`). This
    hook exists so adding SES/Postmark later is a wiring change in ``main.py`` and not
    an edit to the auth policy.
    """
    global _mailer
    _mailer = mailer


async def _deliver_code(email: str, code: str, ttl_seconds: int, *, settings: Settings) -> str:
    """Get the code to the user. Returns the channel used, for the audit row.

    Order: a configured mailer wins; otherwise the dev echo; otherwise this is a
    misconfiguration and we say so loudly rather than pretending a mail was sent.
    """
    if _mailer is not None:
        try:
            await _mailer(email, code, ttl_seconds)
        except Exception as exc:  # blanket on purpose - any transport failure means "not sent"
            # Type name only: an SMTP error's message can quote the recipient address
            # (SMTPRecipientsRefused does), and this line must stay PII-free.
            _log.error(
                "auth.otp_mail_failed",
                email_domain=email_domain(email),
                error=type(exc).__name__,
            )
            raise ServiceUnavailableError(
                "We couldn't send your sign-in code just now.",
                dependency="email",
                retry_after_seconds=30,
            ) from exc
        return "email"

    if dev_echo_otp_enabled(settings):
        # WARNING, not INFO: this line contains a live credential and must be
        # impossible to miss in a log that should never have been captured. The key is
        # `dev_otp_code`, not `code`/`otp_code`, so logging.scrub_pii does not redact
        # the one line whose entire purpose is to show it.
        _log.warning(
            "auth.otp_dev_echo",
            dev_otp_code=code,
            email_domain=email_domain(email),
            ttl_seconds=ttl_seconds,
            note="DEV ONLY — set DEV_ECHO_OTP=0 to disable; impossible outside dev/test",
        )
        return "dev-echo"

    _log.error(
        "auth.otp_undeliverable",
        email_domain=email_domain(email),
        reason=(
            "no mailer installed and the dev echo is unavailable in this environment; "
            "set SMTP_HOST and SMTP_FROM (plus SMTP_USER/SMTP_PASSWORD if the relay "
            "wants auth) to enable real mail"
        ),
    )
    raise ServiceUnavailableError(
        "We couldn't send your sign-in code just now. This server has no email "
        "transport configured — an operator must set SMTP_HOST and SMTP_FROM.",
        dependency="email",
        retry_after_seconds=30,
    )


# ---------------------------------------------------------------------------
# Session store (Redis)
# ---------------------------------------------------------------------------

AUTH_KEY_PREFIX: Final = "garh:auth"

REFRESH_STATE_ACTIVE: Final = "active"
REFRESH_STATE_ROTATED: Final = "rotated"

FAMILY_STATE_ACTIVE: Final = "active"
FAMILY_STATE_REVOKED: Final = "revoked"

#: Rotation outcomes returned by :meth:`SessionStore.rotate`.
ROTATE_OK: Final = "ok"
ROTATE_REUSE: Final = "reuse"
ROTATE_UNKNOWN: Final = "unknown"
ROTATE_FAMILY_REVOKED: Final = "family_revoked"
ROTATE_FAMILY_UNKNOWN: Final = "family_unknown"

#: Extra life given to a refresh record beyond the token's own ``exp``, so a replay
#: arriving a moment after expiry is still recognisable as a replay.
_RECORD_GRACE_SECONDS: Final = 300

#: When Redis cannot answer "has this session been revoked?", do we still honour a
#: cryptographically valid access token?
#:
#: **Yes.** An access token lives 15 minutes and its signature is intact; refusing it
#: would take the whole product down for a Redis blip, which is a far more likely event
#: than a stolen token being used inside the same blip. Every refusal-critical path —
#: rotation, logout, logout-all — fails *closed* instead (they need Redis to be correct
#: at all). The degraded check is logged at WARNING so the gap is visible.
REVOCATION_FAIL_OPEN: Final = True


def _tag(user_id: uuid.UUID | str) -> str:
    """Redis Cluster hash tag: keeps one user's auth keys in one slot."""
    return "{u:%s}" % (user_id,)


def generation_key(user_id: uuid.UUID | str) -> str:
    return f"{AUTH_KEY_PREFIX}:gen:{_tag(user_id)}"


def user_families_key(user_id: uuid.UUID | str) -> str:
    return f"{AUTH_KEY_PREFIX}:ufam:{_tag(user_id)}"


def family_key(user_id: uuid.UUID | str, family: str) -> str:
    return f"{AUTH_KEY_PREFIX}:fam:{_tag(user_id)}:{family}"


def refresh_key(user_id: uuid.UUID | str, token_id: str) -> str:
    return f"{AUTH_KEY_PREFIX}:rt:{_tag(user_id)}:{token_id}"


#: Rotate-or-detect-reuse, atomically. Two API processes racing on the same refresh
#: token cannot both succeed: the first HSET flips the state inside the script.
#:
#: KEYS[1] = refresh record, KEYS[2] = family record
#: ARGV[1] = now (epoch s), ARGV[2] = successor jti, ARGV[3] = record ttl (s)
_ROTATE_LUA: Final = """
local family_state = redis.call('HGET', KEYS[2], 'state')
if not family_state then return {0, 'family_unknown'} end
if family_state ~= 'active' then return {0, 'family_revoked'} end

local state = redis.call('HGET', KEYS[1], 'state')
if not state then return {0, 'unknown'} end

if state == 'active' then
  redis.call('HSET', KEYS[1], 'state', 'rotated', 'rotated_at', ARGV[1], 'successor', ARGV[2])
  redis.call('EXPIRE', KEYS[1], ARGV[3])
  -- "Last used" for the F-3 device list. Written here rather than in `register`
  -- because this branch has already proved the family exists and is active: a bare
  -- HSET on a missing key CREATES it, with no TTL, forever (see _REVOKE_FAMILY_LUA).
  redis.call('HSET', KEYS[2], 'last_used_at', ARGV[1])
  return {1, 'ok'}
end

redis.call('HSET', KEYS[2],
  'state', 'revoked', 'revoked_at', ARGV[1], 'revoked_reason', 'refresh_token_reuse')
return {0, 'reuse'}
"""

#: Revoke one family, if it is still there.
#:
#: The ``EXISTS`` guard is load-bearing: a bare ``HSET`` on a missing key *creates* it,
#: and a key created this way has no TTL. Signing out with a long-expired cookie would
#: then leave an immortal "revoked" record in Redis, once per attempt, forever.
#:
#: KEYS[1] = family record, KEYS[2] = user-families set
#: ARGV[1] = now, ARGV[2] = reason, ARGV[3] = family id, ARGV[4] = ttl (s)
_REVOKE_FAMILY_LUA: Final = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  redis.call('SREM', KEYS[2], ARGV[3])
  return 0
end
redis.call('HSET', KEYS[1], 'state', 'revoked', 'revoked_at', ARGV[1], 'revoked_reason', ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[4])
redis.call('SREM', KEYS[2], ARGV[3])
return 1
"""

#: Revoke every live family for one user (logout-all). Key construction inside the
#: script is safe here because every key shares the user's hash tag.
#:
#: KEYS[1] = user-families set; ARGV[1] = now, ARGV[2] = family key prefix
_REVOKE_ALL_LUA: Final = """
local families = redis.call('SMEMBERS', KEYS[1])
local revoked = 0
for i = 1, #families do
  local key = ARGV[2] .. families[i]
  if redis.call('EXISTS', key) == 1 then
    redis.call('HSET', key,
      'state', 'revoked', 'revoked_at', ARGV[1], 'revoked_reason', 'logout_all')
    revoked = revoked + 1
  end
end
redis.call('DEL', KEYS[1])
return revoked
"""

_rotate_script: Any = None
_revoke_family_script: Any = None
_revoke_all_script: Any = None


def _scripts(client: Redis) -> tuple[Any, Any, Any]:
    """Registered scripts, cached. ``register_script`` does EVALSHA with a NOSCRIPT
    fallback, so a Redis restart reloads them transparently."""
    global _rotate_script, _revoke_family_script, _revoke_all_script
    if _rotate_script is None or _revoke_family_script is None or _revoke_all_script is None:
        _rotate_script = client.register_script(_ROTATE_LUA)
        _revoke_family_script = client.register_script(_REVOKE_FAMILY_LUA)
        _revoke_all_script = client.register_script(_REVOKE_ALL_LUA)
    return _rotate_script, _revoke_family_script, _revoke_all_script


def reset_session_scripts() -> None:
    """Test helper: forget the registered scripts (mirrors ``ratelimit.close_redis``)."""
    global _rotate_script, _revoke_family_script, _revoke_all_script
    _rotate_script = None
    _revoke_family_script = None
    _revoke_all_script = None


def _now() -> int:
    return int(datetime.now(UTC).timestamp())


def _as_int(raw: Any, *, default: int = 0) -> int:
    """Redis hash field → int. A hand-edited or truncated field must not 500."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class LiveSession:
    """One signed-in device, as :meth:`SessionStore.list_families` reports it (F-3).

    This is the refresh *family* — the chain of rotated tokens one sign-in produces —
    which is what "a device" actually means here: one browser profile on one machine
    has exactly one, and it survives every silent 15-minute refresh.
    """

    family: str
    started_at: int
    last_used_at: int
    ip: str = ""
    user_agent: str = ""


class SessionStore:
    """Refresh-token families and logout-all generations, in Redis.

    Every method that a security decision depends on fails **closed**: if Redis is
    unreachable we raise :class:`~garh_api.errors.ServiceUnavailableError` rather than
    issue a session we would be unable to revoke. The single exception is
    :meth:`current_generation` — see :data:`REVOCATION_FAIL_OPEN`.
    """

    def __init__(self, redis: Redis | None = None, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._redis = redis or get_redis(self._settings)

    # -- generation ----------------------------------------------------
    async def current_generation(self, user_id: uuid.UUID) -> int | None:
        """Live generation for a user. ``None`` means "Redis could not answer"."""
        try:
            raw = await self._redis.get(generation_key(user_id))
        except (RedisError, OSError, TimeoutError) as exc:
            _log.warning(
                "auth.generation_unavailable",
                user_id=str(user_id),
                error=type(exc).__name__,
                fail_open=REVOCATION_FAIL_OPEN,
            )
            return None
        return int(raw) if raw is not None else 0

    async def bump_generation(self, user_id: uuid.UUID) -> int:
        """Invalidate every token this user holds. Fails closed."""
        try:
            return int(await self._redis.incr(generation_key(user_id)))
        except (RedisError, OSError, TimeoutError) as exc:
            raise ServiceUnavailableError(
                "We couldn't sign you out everywhere just now.",
                dependency="redis",
            ) from exc

    # -- families & tokens ---------------------------------------------
    async def start_family(
        self,
        *,
        user_id: uuid.UUID,
        firm_id: uuid.UUID,
        family: str,
        started_at: int,
        ip: str = "",
        user_agent: str = "",
    ) -> None:
        """Open a new session. ``ip``/``user_agent`` are what the F-3 device list shows.

        Recorded once, at sign-in, and never rewritten: "signed in from Chrome on
        Windows, from this address" is a property of the *login*, and letting a later
        rotation overwrite it would let a stolen refresh token quietly relabel the
        session it stole as the user's own laptop.
        """
        ttl = self._settings.refresh_token_ttl_seconds + _RECORD_GRACE_SECONDS
        try:
            pipe = self._redis.pipeline(transaction=True)
            pipe.hset(
                family_key(user_id, family),
                mapping={
                    "state": FAMILY_STATE_ACTIVE,
                    "user_id": str(user_id),
                    "firm_id": str(firm_id),
                    "started_at": str(started_at),
                    "last_used_at": str(started_at),
                    "ip": ip or "",
                    "user_agent": user_agent or "",
                },
            )
            pipe.expire(family_key(user_id, family), ttl)
            pipe.sadd(user_families_key(user_id), family)
            pipe.expire(user_families_key(user_id), ttl)
            await pipe.execute()
        except (RedisError, OSError, TimeoutError) as exc:
            raise ServiceUnavailableError(
                "We couldn't start your session just now.", dependency="redis"
            ) from exc

    async def register(
        self,
        *,
        user_id: uuid.UUID,
        family: str,
        token_id: str,
        expires_at: int,
    ) -> None:
        """Record a freshly minted refresh token as the family's live one."""
        ttl = max(1, expires_at - _now() + _RECORD_GRACE_SECONDS)
        try:
            pipe = self._redis.pipeline(transaction=True)
            pipe.hset(
                refresh_key(user_id, token_id),
                mapping={
                    "state": REFRESH_STATE_ACTIVE,
                    "family": family,
                    "issued_at": str(_now()),
                },
            )
            pipe.expire(refresh_key(user_id, token_id), ttl)
            # Sliding the family TTL keeps an actively used session alive up to the
            # hard deadline that `fst` enforces inside the token itself.
            pipe.expire(
                family_key(user_id, family),
                self._settings.refresh_token_ttl_seconds + _RECORD_GRACE_SECONDS,
            )
            await pipe.execute()
        except (RedisError, OSError, TimeoutError) as exc:
            raise ServiceUnavailableError(
                "We couldn't start your session just now.", dependency="redis"
            ) from exc

    async def rotate(
        self,
        *,
        user_id: uuid.UUID,
        family: str,
        token_id: str,
        successor_token_id: str,
    ) -> str:
        """Spend one refresh token. Returns one of the ``ROTATE_*`` outcomes.

        ``ROTATE_REUSE`` means the token had already been spent; the script has
        already revoked the family by the time this returns.
        """
        rotate, _, _ = _scripts(self._redis)
        ttl = self._settings.refresh_token_ttl_seconds + _RECORD_GRACE_SECONDS
        try:
            raw = await rotate(
                keys=[refresh_key(user_id, token_id), family_key(user_id, family)],
                args=[_now(), successor_token_id, ttl],
            )
        except (RedisError, OSError, TimeoutError) as exc:
            # Fail closed: we cannot prove this token has not been used before.
            raise ServiceUnavailableError(
                "We couldn't refresh your session just now.", dependency="redis"
            ) from exc
        outcome = raw[1]
        return outcome.decode("utf-8") if isinstance(outcome, bytes) else str(outcome)

    async def revoke_family(self, *, user_id: uuid.UUID, family: str, reason: str) -> bool:
        """Kill one session. ``True`` when a live family was actually revoked."""
        _, revoke_family, _ = _scripts(self._redis)
        ttl = self._settings.refresh_token_ttl_seconds + _RECORD_GRACE_SECONDS
        try:
            revoked = await revoke_family(
                keys=[family_key(user_id, family), user_families_key(user_id)],
                args=[_now(), reason, family, ttl],
            )
        except (RedisError, OSError, TimeoutError) as exc:
            raise ServiceUnavailableError(
                "We couldn't sign you out just now.", dependency="redis"
            ) from exc
        return bool(int(revoked))

    async def list_families(self, user_id: uuid.UUID) -> list[LiveSession]:
        """Every live session this user has, newest use first (F-3).

        Reads only — but it fails **closed** like every other family operation. A
        device list that silently comes back empty because Redis blinked would tell a
        user "nothing else is signed in", which is exactly the wrong answer to give
        someone checking whether they have been compromised.

        Families whose record has expired are dropped from the index on the way past:
        the set is the only structure with no natural per-member TTL, so without this
        it grows for the life of the user.
        """
        try:
            # ``cast``: redis-py types its async commands ``Awaitable[T] | T`` because
            # the sync and async clients share one signature, and mypy cannot tell
            # which arm this client returns. At runtime it is always the awaitable.
            families = sorted(
                await cast("Awaitable[set[str]]", self._redis.smembers(user_families_key(user_id)))
            )
            if not families:
                return []
            pipe = self._redis.pipeline(transaction=False)
            for family in families:
                pipe.hgetall(family_key(user_id, family))
            records = await pipe.execute()
        except (RedisError, OSError, TimeoutError) as exc:
            raise ServiceUnavailableError(
                "We couldn't list your signed-in devices just now.", dependency="redis"
            ) from exc

        live: list[LiveSession] = []
        stale: list[str] = []
        for family, record in zip(families, records, strict=True):
            if not record:
                stale.append(str(family))
                continue
            if record.get("state") != FAMILY_STATE_ACTIVE:
                continue
            started = _as_int(record.get("started_at"))
            live.append(
                LiveSession(
                    family=str(family),
                    started_at=started,
                    last_used_at=_as_int(record.get("last_used_at"), default=started),
                    ip=str(record.get("ip") or ""),
                    user_agent=str(record.get("user_agent") or ""),
                )
            )

        if stale:
            try:
                await cast("Awaitable[int]", self._redis.srem(user_families_key(user_id), *stale))
            except (RedisError, OSError, TimeoutError):  # pragma: no cover - best effort
                _log.warning("auth.family_index_tidy_failed", user_id=str(user_id))

        live.sort(key=lambda item: (item.last_used_at, item.started_at), reverse=True)
        return live

    async def revoke_all_families(self, user_id: uuid.UUID) -> int:
        _, _, revoke_all = _scripts(self._redis)
        try:
            return int(
                await revoke_all(
                    keys=[user_families_key(user_id)],
                    args=[_now(), f"{AUTH_KEY_PREFIX}:fam:{_tag(user_id)}:"],
                )
            )
        except (RedisError, OSError, TimeoutError) as exc:
            raise ServiceUnavailableError(
                "We couldn't sign you out everywhere just now.", dependency="redis"
            ) from exc


# ---------------------------------------------------------------------------
# Access-token verification (used by garh_api.deps — the one place a request
# becomes a TenantCtx)
# ---------------------------------------------------------------------------


async def authenticate_access_token(
    raw_token: str,
    *,
    settings: Settings | None = None,
    store: SessionStore | None = None,
) -> TokenClaims:
    """Verify signature/claims **and** that the session has not been signed out.

    Raises :class:`~garh_api.errors.TokenExpiredError`,
    :class:`~garh_api.errors.TokenInvalidError` or
    :class:`~garh_api.errors.TokenRevokedError`.
    """
    cfg = settings or get_settings()
    claims = decode_token(raw_token, expected_type=TOKEN_TYPE_ACCESS, settings=cfg)
    sessions = store or SessionStore(settings=cfg)
    generation = await sessions.current_generation(claims.user_id)
    if generation is None:
        # Redis is down. See REVOCATION_FAIL_OPEN for why this is not a 503.
        if not REVOCATION_FAIL_OPEN:  # pragma: no cover - constant, kept honest
            raise ServiceUnavailableError(
                "We can't check your session right now.", dependency="redis"
            )
        return claims
    if claims.generation != generation:
        _log.info(
            "auth.token_revoked",
            user_id=str(claims.user_id),
            token_generation=claims.generation,
            current_generation=generation,
        )
        raise TokenRevokedError()
    return claims


def tenant_ctx_from_claims(claims: TokenClaims, *, request_id: str | None = None) -> TenantCtx:
    """Build the request's tenant context from a verified access token.

    The firm id comes from the signed ``fid`` claim, so the hot path costs no database
    round trip. It is only ever *narrowing*: nothing downstream can widen the scope,
    because every repository filters on ``ctx.firm_id`` unconditionally.
    """
    return TenantCtx(
        firm_id=claims.firm_id,
        user_id=claims.user_id,
        role=claims.role,
        request_id=request_id,
    )


# ---------------------------------------------------------------------------
# Service results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RequestOrigin:
    """Where the call came from. Feeds rate limits and the audit trail."""

    ip: str
    user_agent: str | None = None

    def short_user_agent(self) -> str | None:
        if not self.user_agent:
            return None
        return self.user_agent[:120]


@dataclass(frozen=True)
class OtpIssueResult:
    """Outcome of ``POST /auth/otp``. Identical for known and unknown addresses."""

    expires_in_seconds: int
    resend_after_seconds: int
    #: Populated only when the dev echo was the delivery channel — never when a
    #: mailer is installed, and always ``None`` in prod.
    dev_code: str | None = None


@dataclass(frozen=True)
class IssuedSession:
    """A new access token plus the refresh token that will replace it."""

    access_token: str
    access_expires_at: int
    refresh_token: str
    refresh_expires_at: int
    principal: AuthPrincipal
    #: The TTL the token was actually minted with, carried rather than re-derived.
    access_ttl_seconds: int

    @property
    def expires_in_seconds(self) -> int:
        """The access token's lifetime — the configured TTL, not the time left now.

        This used to return ``access_expires_at - _now()``, which reads the clock a
        second time: the token is minted at T with an expiry of T+900, and if the
        wall clock crosses a second boundary before the response is serialised the
        subtraction yields 899. CI's api-smoke caught exactly that, and it is the
        client's refresh schedule that pays for it — `expires_in` is the token's
        lifetime in OAuth terms, and the API's own schema documents it as 900.
        """
        return self.access_ttl_seconds


# ---------------------------------------------------------------------------
# AuthService
# ---------------------------------------------------------------------------


class AuthService:
    """All authentication policy, in one testable object.

    Constructor::

        AuthService(session: AsyncSession, origin: RequestOrigin, settings=None, store=None)

    The service does not commit on the success path — the request-scoped session from
    :func:`garh_api.db.get_db_session` commits once when the handler returns, so an OTP
    row and its audit entry land together or not at all.

    It **does** commit on three failure paths. See :meth:`_persist_failure_record` for
    why that is not a style violation but a correctness requirement.
    """

    def __init__(
        self,
        session: AsyncSession,
        origin: RequestOrigin,
        *,
        settings: Settings | None = None,
        store: SessionStore | None = None,
    ) -> None:
        self._session = session
        self._origin = origin
        self._settings = settings or get_settings()
        self._store = store or SessionStore(settings=self._settings)
        self._directory = AuthDirectoryRepository(session)
        self._otp = OtpCodeRepository(session)

    # -- helpers -------------------------------------------------------
    @property
    def store(self) -> SessionStore:
        return self._store

    @property
    def settings(self) -> Settings:
        """The settings this service was built with — routers need them for cookies."""
        return self._settings

    @property
    def origin(self) -> RequestOrigin:
        return self._origin

    def _ip_identity(self) -> str:
        return f"ip:{self._origin.ip}"

    @staticmethod
    def _email_identity(email: str) -> str:
        # Hashed: rate-limit buckets live in Redis, which is not a PII store.
        return f"email:{pseudonymise(email)}"

    def _audit(self, principal: AuthPrincipal) -> AuditLogRepository:
        ctx = TenantCtx(
            firm_id=principal.firm_id,
            user_id=principal.user_id,
            role=principal.role,
        )
        return AuditLogRepository(self._session, ctx)

    def _origin_meta(self, **extra: Any) -> dict[str, Any]:
        meta: dict[str, Any] = {"ip": self._origin.ip}
        ua = self._origin.short_user_agent()
        if ua:
            meta["userAgent"] = ua
        meta.update(extra)
        return meta

    async def _persist_failure_record(self, what: str) -> None:
        """Commit security bookkeeping that must outlive the error we are about to raise.

        **This is load-bearing, not defensive tidiness.** ``garh_api.db.session_scope``
        rolls the request transaction back on *any* exception, so anything written on a
        path that ends in ``raise`` is discarded. Three writes in this module are on
        exactly such a path, and all three are security controls:

        * the OTP ``attempts`` increment — without this commit the §13 "5 attempts" cap
          never persists, and one issued code can be guessed an unbounded number of
          times inside its 10-minute life (only the per-IP verify limit, which lives in
          Redis and is therefore unaffected, would still bite);
        * the ``auth.otp_failed`` audit row — §13 requires an audit trail of auth
          events, and a trail that records only *successes* is not one;
        * the ``auth.refresh_reuse_detected`` audit row — the single most
          security-relevant event the auth layer can emit.

        A failed commit must not become the user's problem: the original authentication
        error is the honest answer and is still raised by the caller. We log at ERROR
        so a database that is silently dropping the audit trail is visible.
        """
        try:
            await self._session.commit()
        except SQLAlchemyError as exc:
            # Leave the session clean so the caller's `raise` unwinds through
            # session_scope's rollback without a second failure on top of the first.
            await self._session.rollback()
            _log.error(
                "auth.failure_record_lost",
                what=what,
                error=type(exc).__name__,
                consequence="attempt counter and/or audit row were not persisted",
            )

    # -- OTP issue -----------------------------------------------------
    async def request_otp(self, email: str) -> OtpIssueResult:
        """Issue a sign-in code. Same answer whether or not the address exists.

        Rate limits, in the order they are consumed (all fail closed — an unmetered
        auth endpoint is an open brute-force and mail-bombing target):

        1. per IP, hourly — the cheapest defence, applied before any lookup;
        2. per address, 60s resend cooldown — what the UI counts down against;
        3. per address, hourly — caps how much mail one address can be sent.

        An unknown address burns the same budget as a known one. If it did not, the
        difference in ``429`` behaviour would itself be an enumeration oracle.
        """
        clean = normalise_email(email)
        await enforce_rate_limit(auth_ip_rule(self._settings), self._ip_identity())
        await self._consume_email_budget(clean, route="signin")

        principal = await self._directory.find_principal_by_email(clean)
        if principal is None:
            # No row written, no mail sent, no audit entry (there is no firm to file it
            # under) — but an identical response body and status.
            _log.info("auth.otp_requested_unknown", email_domain=email_domain(clean))
            return self._otp_result()

        return await self._send_code(principal, clean, route="signin")

    async def _consume_email_budget(self, clean_email: str, *, route: OtpRoute) -> None:
        """Per-address limits: the 60s resend cooldown, then the hourly cap.

        The resend cooldown is keyed per ROUTE (``signin`` / ``signup``); the hourly
        cap is shared. Execution find, first live trial sign-in: an architect with no
        account pressed Sign in (202, nothing sent — see ``request_otp``), read "Check
        your email", then thirty seconds later pressed Create an account and was
        refused with "We just sent a code to that address". The sign-in attempt for a
        non-existent address had spent the one cooldown both routes shared, and the
        sign-up that would actually have sent something was the call that paid for it.

        Why per-route rather than "don't charge unknown addresses": on ``/auth/otp``
        the charge MUST be identical for known and unknown addresses, or a second
        request inside the window would 429 for a real account and 202 for a fake
        one — the enumeration oracle the uniform 202 exists to prevent. Keying by
        route keeps that property on sign-in and stops sign-in's cooldown reaching
        sign-up. Sign-up has no enumeration property to protect: it already answers
        409 for an existing address by design.

        The hourly cap stays on the bare address, because it is the spam cap and a
        spammer alternating routes must not get double the allowance.
        """
        identity = self._email_identity(clean_email)
        await enforce_rate_limit(
            otp_resend_rule(self._settings), otp_resend_identity(identity, route)
        )
        await enforce_rate_limit(otp_per_email_rule(self._settings), identity)

    def _otp_result(self, dev_code: str | None = None) -> OtpIssueResult:
        return OtpIssueResult(
            expires_in_seconds=self._settings.otp_ttl_seconds,
            resend_after_seconds=OTP_RESEND_COOLDOWN_SECONDS,
            dev_code=dev_code,
        )

    async def _send_code(
        self, principal: AuthPrincipal, clean_email: str, *, route: OtpRoute
    ) -> OtpIssueResult:
        """Generate, store and deliver a code for a known principal.

        Assumes the caller has already consumed the relevant rate-limit budget, so
        signup does not pay the per-IP toll twice for one round trip.
        """
        ttl = self._settings.otp_ttl_seconds
        code = generate_otp_code(self._settings.otp_code_length)
        challenge = await self._otp.issue(
            clean_email,
            code,
            ttl_seconds=ttl,
            meta=self._origin_meta(),
        )
        try:
            channel = await _deliver_code(clean_email, code, ttl, settings=self._settings)
        except ServiceUnavailableError:
            # The 503 says "try again in a few seconds"; make that true. The resend
            # cooldown was charged before delivery and Redis is outside the DB
            # transaction, so without this the retry it invites is a 429 "we just
            # sent a code" for a code that was never sent (execution find, 2026-09-01
            # 18:35 on the deployed stack). Only the 60-second cooldown is refunded:
            # the hourly and per-IP caps are what bound a mail-bombing loop, and a
            # reset there would wipe legitimate earlier hits, not one slot.
            await reset_rate_limit(
                otp_resend_rule(self._settings),
                otp_resend_identity(self._email_identity(clean_email), route),
                settings=self._settings,
            )
            raise

        await self._audit(principal).record(
            ACTION_AUTH_OTP_REQUESTED,
            entity="user",
            entity_id=principal.user_id,
            meta=self._origin_meta(channel=channel, emailDomain=email_domain(clean_email)),
        )
        _log.info(
            "auth.otp_requested",
            user_id=str(principal.user_id),
            firm_id=str(principal.firm_id),
            otp_id=str(challenge.id),
            channel=channel,
        )
        # The response mirrors the CHANNEL, not the setting. With a mailer installed the
        # code went by mail and must not also come back in the body: on a dev-env
        # deployment that would hand any caller any account's code (execution find,
        # 2026-09-02 audit — masked only while the mailer itself was failing).
        return self._otp_result(code if channel == "dev-echo" else None)

    # -- OTP verify ----------------------------------------------------
    async def verify_otp(self, email: str, code: str) -> IssuedSession:
        """Exchange a correct code for a session.

        Every failure mode — no challenge, expired, wrong code, attempts exhausted —
        raises the same :class:`~garh_api.errors.OtpVerificationError`. Telling them
        apart would reveal whether an address has a live challenge and how many guesses
        are left.
        """
        clean = normalise_email(email)
        await enforce_rate_limit(verify_ip_rule(self._settings), self._ip_identity())

        verification = await self._otp.verify(clean, code)
        principal = await self._directory.find_principal_by_email(clean)

        if not verification.ok:
            if principal is not None:
                await self._audit(principal).record(
                    ACTION_AUTH_OTP_FAILED,
                    entity="user",
                    entity_id=principal.user_id,
                    meta=self._origin_meta(
                        outcome=verification.outcome,
                        attemptsRemaining=verification.attempts_remaining,
                    ),
                )
            _log.info(
                "auth.otp_verify_failed",
                outcome=verification.outcome,
                email_domain=email_domain(clean),
            )
            # Unconditional, even when `principal is None` and no audit row was written:
            # the `attempts` increment inside `OtpCodeRepository.verify` is itself the
            # §13 five-attempt cap, and it is lost without this.
            await self._persist_failure_record("otp_verify_failed")
            raise OtpVerificationError()

        if principal is None:
            # The code was right, so control of the address is proven — the account
            # just vanished between issue and verify. Naming that is safe and useful.
            # Commit first: `verify` marked the challenge consumed, and a rollback would
            # hand that single-use code back for a replay.
            await self._persist_failure_record("otp_verified_account_gone")
            raise AccountUnknownError()

        # F-4. The first factor is proved; if a second one is enrolled, no session is
        # issued here — the caller gets a short-lived challenge and must come back
        # through `complete_two_factor`. This is the control that makes the instance
        # safe while `dev_echo_otp_enabled` is handing sign-in codes back in the
        # response body: knowing the code stops being enough.
        if await self.two_factor(principal).is_enabled(principal.user_id):
            challenge = self.two_factor(principal).challenge_for(
                user_id=principal.user_id,
                firm_id=principal.firm_id,
                role=principal.role,
            )
            await self._audit(principal).record(
                ACTION_TWO_FACTOR_CHALLENGED,
                entity="user",
                entity_id=principal.user_id,
                meta=self._origin_meta(),
            )
            # Same reasoning as the branch above: `self._otp.verify` has already marked
            # this challenge consumed, and unwinding that would hand a single-use code
            # back for a replay against an account we have just told to prove more.
            await self._persist_failure_record("otp_verified_awaiting_second_factor")
            raise TwoFactorRequiredError(challenge, expires_in_seconds=CHALLENGE_TTL_SECONDS)

        session = await self._issue_session(principal)
        await self._audit(principal).record(
            ACTION_AUTH_OTP_VERIFIED,
            entity="user",
            entity_id=principal.user_id,
            meta=self._origin_meta(),
        )
        _log.info(
            "auth.signed_in",
            user_id=str(principal.user_id),
            firm_id=str(principal.firm_id),
            user_role=principal.role,
        )
        return session

    # -- second factor -------------------------------------------------
    def two_factor(self, principal: AuthPrincipal) -> TwoFactorService:
        """The 2FA service, scoped to one principal's firm.

        A method rather than a constructor argument because a single ``AuthService``
        handles pre-auth requests where no principal exists yet, and a repository built
        without a :class:`~garh_api.tenancy.TenantCtx` is the one thing the tenancy
        layer refuses to construct.
        """
        ctx = TenantCtx(
            firm_id=principal.firm_id,
            user_id=principal.user_id,
            role=principal.role,
        )
        return TwoFactorService(TwoFactorRepository(self._session, ctx), settings=self._settings)

    async def complete_two_factor(self, raw_challenge: str, code: str) -> IssuedSession:
        """Exchange a challenge plus a live second factor for a session.

        The challenge is only a receipt for "the first factor passed"; the session is
        minted from scratch here, against a **freshly re-read principal**, so a role
        change or a removed seat between the two round trips takes effect immediately
        rather than being frozen into the challenge.
        """
        try:
            claims = decode_token(
                raw_challenge,
                expected_type=TOKEN_TYPE_TWO_FACTOR,
                settings=self._settings,
            )
        except (TokenExpiredError, TokenInvalidError) as exc:
            # Deliberately distinguishable from a wrong code: the client cannot recover
            # from a stale challenge by trying again, it has to restart sign-in, and
            # telling it so leaks nothing about the account.
            raise TwoFactorInvalidError(
                "That sign-in attempt expired.",
                action="Request a new sign-in code and start again.",
            ) from exc

        principal = await self._directory.get_principal(claims.user_id)
        if principal is None:
            raise AccountUnknownError()

        service = self.two_factor(principal)
        try:
            result = await service.verify_second_factor(principal.user_id, code)
        except (TwoFactorInvalidError, TwoFactorStateError):
            await self._audit(principal).record(
                ACTION_TWO_FACTOR_FAILED,
                entity="user",
                entity_id=principal.user_id,
                meta=self._origin_meta(),
            )
            # The spent TOTP counter and the attempt row are security bookkeeping on a
            # path that ends in `raise`; without this commit they are rolled back and
            # the replay guard never persists.
            await self._persist_failure_record("two_factor_failed")
            raise

        session = await self._issue_session(principal)
        await self._audit(principal).record(
            ACTION_AUTH_OTP_VERIFIED,
            entity="user",
            entity_id=principal.user_id,
            meta=self._origin_meta(
                secondFactor="recovery_code" if result.used_recovery_code else "totp"
            ),
        )
        if result.used_recovery_code:
            await self._audit(principal).record(
                ACTION_TWO_FACTOR_RECOVERY_USED,
                entity="user",
                entity_id=principal.user_id,
                meta=self._origin_meta(remaining=result.recovery_codes_remaining),
            )
        _log.info(
            "auth.signed_in",
            user_id=str(principal.user_id),
            firm_id=str(principal.firm_id),
            user_role=principal.role,
            second_factor="recovery_code" if result.used_recovery_code else "totp",
        )
        return session

    # -- signup --------------------------------------------------------
    async def signup(
        self,
        *,
        firm_name: str,
        name: str,
        email: str,
        coa_number: str | None = None,
    ) -> OtpIssueResult:
        """Create a firm plus its first admin, then send that admin a code.

        Signup does not return tokens: the new user still has to prove they own the
        address. It therefore ends in exactly the same place as sign-in — waiting on
        ``POST /auth/verify``.
        """
        clean = normalise_email(email)
        await enforce_rate_limit(auth_ip_rule(self._settings), self._ip_identity())
        await self._consume_email_budget(clean, route="signup")

        if await self._directory.email_exists(clean):
            raise EmailAlreadyRegisteredError()

        try:
            principal = await self._directory.create_firm_with_owner(
                firm_name=firm_name,
                email=clean,
                name=name,
                coa_number=coa_number,
            )
        except IntegrityError as exc:
            # Two signups for the same address raced; the unique index is the real
            # guard and it just fired. Same answer as the pre-check.
            await self._session.rollback()
            raise EmailAlreadyRegisteredError() from exc

        await self._audit(principal).record(
            ACTION_AUTH_SIGNUP,
            entity="firm",
            entity_id=principal.firm_id,
            meta=self._origin_meta(emailDomain=email_domain(clean)),
        )
        _log.info(
            "auth.signed_up",
            user_id=str(principal.user_id),
            firm_id=str(principal.firm_id),
        )

        # Same code path as sign-in, so there is one OTP policy rather than two — but
        # calling `_send_code` directly rather than `request_otp` avoids charging the
        # per-IP bucket twice for a single round trip.
        return await self._send_code(principal, clean, route="signup")

    # -- refresh -------------------------------------------------------
    async def refresh(self, raw_token: str) -> IssuedSession:
        """Rotate a refresh token. Detects reuse and kills the family when it sees it.

        Sequence, and every step can only make the session *less* powerful:

        1. verify the JWT (signature, ``exp``, audience ``garh-api/refresh``, ``typ``);
        2. check the logout-all generation;
        3. atomically spend the token — a second presentation lands in the ``reuse``
           branch and revokes the family;
        4. re-resolve the principal, so a role change or a removed seat takes effect
           without waiting 15 minutes for the access token to expire;
        5. mint the successor into the same family, preserving ``fst`` so a rotating
           session still dies at the original hard deadline.
        """
        claims = decode_token(raw_token, expected_type=TOKEN_TYPE_REFRESH, settings=self._settings)
        family = claims.family or ""

        generation = await self._store.current_generation(claims.user_id)
        if generation is not None and claims.generation != generation:
            raise RefreshTokenRevokedError()

        successor_id = new_token_id()
        outcome = await self._store.rotate(
            user_id=claims.user_id,
            family=family,
            token_id=claims.token_id,
            successor_token_id=successor_id,
        )

        if outcome == ROTATE_REUSE:
            principal = await self._directory.get_principal(claims.user_id)
            if principal is not None:
                await self._audit(principal).record(
                    ACTION_AUTH_REFRESH_REUSE,
                    entity="user",
                    entity_id=principal.user_id,
                    meta=self._origin_meta(family=family),
                )
            _log.warning(
                "auth.refresh_reuse_detected",
                user_id=str(claims.user_id),
                token_family=family,
                consequence="family revoked",
            )
            # The family is already dead in Redis (the Lua script revoked it), but the
            # audit row lives in Postgres and would be rolled back with the 401.
            await self._persist_failure_record("refresh_reuse_detected")
            raise RefreshTokenReuseError()

        if outcome != ROTATE_OK:
            _log.info(
                "auth.refresh_rejected",
                user_id=str(claims.user_id),
                token_family=family,
                outcome=outcome,
            )
            raise RefreshTokenRevokedError()

        principal = await self._directory.get_principal(claims.user_id)
        if principal is None:
            await self._store.revoke_family(
                user_id=claims.user_id, family=family, reason="principal_gone"
            )
            raise RefreshTokenRevokedError()

        session = await self._issue_session(
            principal,
            family=family,
            family_started_at=claims.family_started_at,
            refresh_token_id=successor_id,
            generation=claims.generation,
        )
        await self._audit(principal).record(
            ACTION_AUTH_TOKEN_REFRESHED,
            entity="user",
            entity_id=principal.user_id,
            meta=self._origin_meta(family=family),
        )
        return session

    # -- logout --------------------------------------------------------
    async def logout(self, raw_token: str | None) -> bool:
        """End this one session. Idempotent, and never an error the user must act on.

        ``verify_expiry=False``: signing out with an expired refresh token should still
        clear the cookie and revoke the family, not fail.
        """
        if not raw_token:
            return False
        try:
            claims = decode_token(
                raw_token,
                expected_type=TOKEN_TYPE_REFRESH,
                settings=self._settings,
                verify_expiry=False,
            )
        except Exception:
            _log.info("auth.logout_unparseable_token")
            return False

        revoked = await self._store.revoke_family(
            user_id=claims.user_id, family=claims.family or "", reason="logout"
        )
        principal = await self._directory.get_principal(claims.user_id)
        if principal is not None:
            await self._audit(principal).record(
                ACTION_AUTH_LOGOUT,
                entity="user",
                entity_id=principal.user_id,
                meta=self._origin_meta(family=claims.family, revoked=revoked),
            )
        _log.info("auth.signed_out", user_id=str(claims.user_id), revoked=revoked)
        return revoked

    async def logout_all(self, ctx: TenantCtx) -> int:
        """Sign out of every device. Bumps the generation *before* revoking families.

        Order matters: the generation bump is the one that invalidates access tokens,
        so it goes first. If the family sweep then fails, the session is already dead
        everywhere — the leftover family records simply expire.
        """
        if ctx.user_id is None:
            raise RefreshTokenRevokedError()
        generation = await self._store.bump_generation(ctx.user_id)
        revoked = await self._store.revoke_all_families(ctx.user_id)
        await AuditLogRepository(self._session, ctx).record(
            ACTION_AUTH_LOGOUT_ALL,
            entity="user",
            entity_id=ctx.user_id,
            meta=self._origin_meta(generation=generation, familiesRevoked=revoked),
        )
        _log.info(
            "auth.signed_out_everywhere",
            user_id=str(ctx.user_id),
            generation=generation,
            families_revoked=revoked,
        )
        return revoked

    # -- session minting -----------------------------------------------
    async def _issue_session(
        self,
        principal: AuthPrincipal,
        *,
        family: str | None = None,
        family_started_at: int | None = None,
        refresh_token_id: str | None = None,
        generation: int | None = None,
    ) -> IssuedSession:
        """Mint an access + refresh pair, registering the refresh token first."""
        if generation is None:
            live = await self._store.current_generation(principal.user_id)
            generation = live if live is not None else 0

        is_new_family = family is None
        token_family = family or new_token_family()
        started = family_started_at if family_started_at is not None else _now()
        token_id = refresh_token_id or new_token_id()

        if is_new_family:
            await self._store.start_family(
                user_id=principal.user_id,
                firm_id=principal.firm_id,
                family=token_family,
                started_at=started,
                # Stamped once, at sign-in — see `start_family` for why a rotation
                # must not be allowed to relabel it.
                ip=self._origin.ip,
                user_agent=self._origin.short_user_agent() or "",
            )

        refresh_token, refresh_expires = create_refresh_token(
            user_id=principal.user_id,
            firm_id=principal.firm_id,
            role=principal.role,
            family=token_family,
            token_id=token_id,
            generation=generation,
            family_started_at=started,
            settings=self._settings,
        )
        # Registered before the tokens leave this process: a refresh token we cannot
        # see in Redis is one we cannot revoke or reuse-detect.
        await self._store.register(
            user_id=principal.user_id,
            family=token_family,
            token_id=token_id,
            expires_at=refresh_expires,
        )

        access_token, access_expires = create_access_token(
            user_id=principal.user_id,
            firm_id=principal.firm_id,
            role=principal.role,
            generation=generation,
            settings=self._settings,
        )
        return IssuedSession(
            access_token=access_token,
            access_expires_at=access_expires,
            refresh_token=refresh_token,
            refresh_expires_at=refresh_expires,
            principal=principal,
            access_ttl_seconds=self._settings.access_token_ttl_seconds,
        )


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------


async def purge_expired_otps(session: AsyncSession, *, older_than_hours: int = 24) -> int:
    """Delete spent/expired OTP challenges. For a scheduled worker, not a request.

    The rows are harmless (only hashes) but unbounded growth on an auth table is its
    own liability, and §13's retention story is easier to explain when it is empty.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=max(0, older_than_hours))
    return await OtpCodeRepository(session).purge_expired(before=cutoff)


__all__ = [
    "ACTION_AUTH_LOGOUT",
    "ACTION_AUTH_REFRESH_REUSE",
    "ACTION_AUTH_SIGNUP",
    "AUTH_AUDIT_ACTIONS",
    "AUTH_KEY_PREFIX",
    "DEV_ECHO_OTP_ENV",
    "REVOCATION_FAIL_OPEN",
    "ROTATE_FAMILY_REVOKED",
    "ROTATE_FAMILY_UNKNOWN",
    "ROTATE_OK",
    "ROTATE_REUSE",
    "ROTATE_UNKNOWN",
    "AuthService",
    "IssuedSession",
    "LiveSession",
    "OtpIssueResult",
    "OtpMailer",
    "RequestOrigin",
    "SessionStore",
    "authenticate_access_token",
    "dev_echo_otp_enabled",
    "family_key",
    "generation_key",
    "purge_expired_otps",
    "refresh_key",
    "reset_session_scripts",
    "set_otp_mailer",
    "tenant_ctx_from_claims",
    "user_families_key",
]
