"""Cryptographic primitives for authentication (playbook §13).

This module knows about keys, tokens and cookies. It knows nothing about the database,
Redis, or FastAPI routing — so it is trivially unit-testable and there is exactly one
place to audit when the token format changes.

**Token design.**

===============  ==========  =================================================
claim            tokens      meaning
===============  ==========  =================================================
``iss``          both        ``JWT_ISSUER`` (default ``garh-ai``)
``aud``          both        ``garh-api`` (access) / ``garh-api/refresh``
``sub``          both        user id (uuid string)
``fid``          both        firm id — lets ``deps`` build a ``TenantCtx`` with
                             zero database round trips on the hot path
``role``         both        ``admin`` | ``member`` (mirrors ``users.role``)
``gen``          both        token generation; ``logout-all`` bumps the user's
                             counter and every older token stops verifying
``typ``          both        ``access`` | ``refresh`` — belt to the audience brace
``jti``          both        unique id; the refresh store keys on it
``fam``          refresh     rotation family; reuse detection kills the family
``fst``          refresh     family start (epoch seconds) — caps how long a
                             rotating session can live in total
``iat``/``nbf``  both        issued-at / not-before
===============  ==========  =================================================

A third, short-lived type exists: :data:`TOKEN_TYPE_TWO_FACTOR`, minted by
:func:`create_two_factor_challenge`. It says "this person proved the first factor" and
nothing else — it is not a credential, cannot be sent as a bearer token (different
audience, so PyJWT rejects it before our code runs) and is worthless without a live
TOTP or recovery code. See :mod:`garh_api.twofactor`.

Deliberately **not** in the token: email, name, phone. Tokens end up in logs, proxies
and browser storage; §13 keeps PII out of them. ``GET /auth/me`` returns the profile.

Access and refresh tokens carry different audiences, so PyJWT rejects a refresh token
presented as a bearer credential before any of our own code runs.

**Keys.** RS256 only. Verification uses the public key, so a future read-only service
can verify without ever holding the signing key. In ``dev`` with no configured pair we
mint an ephemeral in-process keypair and log a warning (restarting invalidates every
token — run ``make dev-keys`` for a stable pair). Non-dev boots cannot reach that path:
``garh_api.config`` refuses to start without the keys.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any, Final

import jwt

from garh_api.config import ConfigError, Settings, get_settings
from garh_api.errors import (
    RefreshTokenMissingError,
    TokenExpiredError,
    TokenInvalidError,
)
from garh_api.logging import get_logger

_log = get_logger(__name__)

#: The only algorithm we accept. Pinned here as well as in config so a mistyped
#: ``JWT_ALGORITHM=HS256`` (which would let the *public* key sign tokens) cannot boot.
JWT_ALGORITHM: Final = "RS256"

TOKEN_TYPE_ACCESS: Final = "access"
TOKEN_TYPE_REFRESH: Final = "refresh"
#: "First factor passed, second one outstanding" — see :mod:`garh_api.twofactor`.
TOKEN_TYPE_TWO_FACTOR: Final = "2fa"

AUDIENCE_ACCESS: Final = "garh-api"
AUDIENCE_REFRESH: Final = "garh-api/refresh"
AUDIENCE_TWO_FACTOR: Final = "garh-api/2fa"

#: Audience per token type. A dict rather than a conditional because there are now
#: three types: the old ``refresh if x else access`` would have silently handed the
#: 2FA challenge the *access* audience, making it usable as a bearer token — which is
#: precisely the thing separate audiences exist to prevent.
_AUDIENCE_BY_TYPE: Final[dict[str, str]] = {
    TOKEN_TYPE_ACCESS: AUDIENCE_ACCESS,
    TOKEN_TYPE_REFRESH: AUDIENCE_REFRESH,
    TOKEN_TYPE_TWO_FACTOR: AUDIENCE_TWO_FACTOR,
}

#: Tolerated clock drift between the API and whatever signed/issued a token.
CLOCK_SKEW_LEEWAY_SECONDS: Final = 10

#: Name of the refresh cookie. Prefixed ``__Host-``? No: ``__Host-`` forbids ``Path``,
#: and we deliberately scope the cookie to the auth path so it is not attached to every
#: API call. Path scoping wins — it shrinks the blast radius of an XSS-adjacent leak
#: more than the prefix would.
REFRESH_COOKIE_NAME: Final = "garh_refresh"

#: ``Bearer`` scheme name used in the ``Authorization`` header.
BEARER_SCHEME: Final = "bearer"


# ---------------------------------------------------------------------------
# Key material
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JwtKeys:
    """The RS256 pair in PEM form, plus where it came from (for the boot log)."""

    private_pem: str
    public_pem: str
    source: str  # "config" | "ephemeral-dev"

    @property
    def is_ephemeral(self) -> bool:
        return self.source == "ephemeral-dev"


_keys: JwtKeys | None = None


def _mint_dev_keypair() -> JwtKeys:
    """Generate a throwaway RSA-2048 pair. Dev only — see the module docstring."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return JwtKeys(private_pem=private_pem, public_pem=public_pem, source="ephemeral-dev")


def get_jwt_keys(settings: Settings | None = None) -> JwtKeys:
    """Process-wide key material. Created on first use, then cached."""
    global _keys
    if _keys is not None:
        return _keys
    cfg = settings or get_settings()

    if cfg.jwt_algorithm != JWT_ALGORITHM:
        raise ConfigError(
            f"JWT_ALGORITHM must be {JWT_ALGORITHM} — got {cfg.jwt_algorithm!r}. "
            "Symmetric algorithms would let anyone holding the public key mint tokens."
        )

    if cfg.jwt_keys_configured:
        _keys = JwtKeys(
            private_pem=cfg.jwt_private_key,
            public_pem=cfg.jwt_public_key,
            source="config",
        )
        return _keys

    if not cfg.is_dev and not cfg.is_test:
        # Unreachable in practice: Settings._fail_fast_on_missing_secrets already
        # refuses to construct. Kept so a future config change cannot silently make
        # production run on an ephemeral key.
        raise ConfigError(
            f"No JWT keypair configured and ENV={cfg.env}. Set JWT_PRIVATE_KEY and "
            "JWT_PUBLIC_KEY (single-line PEM with \\n escapes is fine)."
        )

    _keys = _mint_dev_keypair()
    _log.warning(
        "auth.ephemeral_jwt_keypair",
        reason="JWT_PRIVATE_KEY / JWT_PUBLIC_KEY are empty",
        consequence="every token is invalidated when this process restarts",
        action="run `make dev-keys` to write a stable pair into .env",
    )
    return _keys


def reset_jwt_keys() -> None:
    """Test helper: drop the cached pair so a new config takes effect."""
    global _keys
    _keys = None


# ---------------------------------------------------------------------------
# Small primitives
# ---------------------------------------------------------------------------


def constant_time_compare(left: str, right: str) -> bool:
    """Timing-safe string comparison (OTP codes, token hashes, share tokens)."""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def hash_secret(value: str) -> str:
    """``sha256`` hex. The only form of a bearer secret we ever persist or log."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_opaque_token(nbytes: int = 32) -> str:
    """URL-safe random token. 32 bytes = 256 bits (§13 share links)."""
    if nbytes < 16:
        raise ValueError("An opaque token needs at least 16 bytes of entropy.")
    return secrets.token_urlsafe(nbytes)


def new_token_id() -> str:
    """``jti`` — random, not derived from anything, so it leaks nothing."""
    return uuid.uuid4().hex


def pseudonymise(value: str) -> str:
    """Stable non-reversible key for an email/IP used as a rate-limit bucket.

    Rate-limit keys live in Redis, which is not a PII store. Hashing keeps the buckets
    stable without writing addresses into it.
    """
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:32]


def email_domain(email: str) -> str:
    """Log-safe fragment of an address (§13: bind the domain, never the address)."""
    _, _, domain = email.partition("@")
    return domain.lower() or "unknown"


# ---------------------------------------------------------------------------
# Token claims
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenClaims:
    """A verified token, unpacked. Constructed only by :func:`decode_token`."""

    token_type: str
    user_id: uuid.UUID
    firm_id: uuid.UUID
    role: str
    generation: int
    token_id: str
    issued_at: int
    expires_at: int
    family: str | None = None
    family_started_at: int | None = None
    raw: dict[str, Any] | None = None

    @property
    def is_refresh(self) -> bool:
        return self.token_type == TOKEN_TYPE_REFRESH

    def log_fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "user_id": str(self.user_id),
            "firm_id": str(self.firm_id),
            "role": self.role,
            "token_type": self.token_type,
            "jti": self.token_id,
        }
        if self.family:
            fields["token_family"] = self.family
        return fields


def _now() -> int:
    return int(time.time())


def _encode(
    *,
    settings: Settings,
    token_type: str,
    audience: str,
    user_id: uuid.UUID,
    firm_id: uuid.UUID,
    role: str,
    generation: int,
    token_id: str,
    ttl_seconds: int,
    extra: dict[str, Any] | None = None,
) -> tuple[str, int]:
    keys = get_jwt_keys(settings)
    issued = _now()
    expires = issued + int(ttl_seconds)
    payload: dict[str, Any] = {
        "iss": settings.jwt_issuer,
        "aud": audience,
        "sub": str(user_id),
        "fid": str(firm_id),
        "role": role,
        "gen": int(generation),
        "typ": token_type,
        "jti": token_id,
        "iat": issued,
        "nbf": issued,
        "exp": expires,
    }
    if extra:
        payload.update(extra)
    token = jwt.encode(payload, keys.private_pem, algorithm=JWT_ALGORITHM)
    # PyJWT ≥2 returns str; the isinstance guard keeps the signature honest if that
    # ever changes back.
    return (token if isinstance(token, str) else token.decode("ascii")), expires


def create_access_token(
    *,
    user_id: uuid.UUID,
    firm_id: uuid.UUID,
    role: str,
    generation: int = 0,
    token_id: str | None = None,
    settings: Settings | None = None,
) -> tuple[str, int]:
    """Mint a 15-minute access token. Returns ``(token, expires_at_epoch)``."""
    cfg = settings or get_settings()
    return _encode(
        settings=cfg,
        token_type=TOKEN_TYPE_ACCESS,
        audience=AUDIENCE_ACCESS,
        user_id=user_id,
        firm_id=firm_id,
        role=role,
        generation=generation,
        token_id=token_id or new_token_id(),
        ttl_seconds=cfg.access_token_ttl_seconds,
    )


def create_refresh_token(
    *,
    user_id: uuid.UUID,
    firm_id: uuid.UUID,
    role: str,
    family: str,
    token_id: str,
    generation: int = 0,
    family_started_at: int | None = None,
    settings: Settings | None = None,
) -> tuple[str, int]:
    """Mint a refresh token inside a rotation family.

    ``family_started_at`` caps the *total* life of a rotating session: without it,
    rotation every 14 days would keep a stolen session alive forever. The expiry is
    clamped to ``family_started_at + REFRESH_TOKEN_TTL_SECONDS``.
    """
    cfg = settings or get_settings()
    started = family_started_at if family_started_at is not None else _now()
    hard_deadline = started + cfg.refresh_token_ttl_seconds
    ttl = min(cfg.refresh_token_ttl_seconds, hard_deadline - _now())
    if ttl <= 0:
        raise TokenExpiredError("This session has reached its maximum length.")
    return _encode(
        settings=cfg,
        token_type=TOKEN_TYPE_REFRESH,
        audience=AUDIENCE_REFRESH,
        user_id=user_id,
        firm_id=firm_id,
        role=role,
        generation=generation,
        token_id=token_id,
        ttl_seconds=ttl,
        extra={"fam": family, "fst": started},
    )


def create_two_factor_challenge(
    *,
    user_id: uuid.UUID,
    firm_id: uuid.UUID,
    role: str,
    ttl_seconds: int,
    token_id: str | None = None,
    settings: Settings | None = None,
) -> tuple[str, int]:
    """Mint the short-lived "first factor passed" ticket. Returns ``(token, exp)``.

    It carries no ``gen`` semantics of its own (``gen=0``) because it grants nothing:
    the only route that accepts it exchanges it for a real session *after* a second
    factor verifies, and that route mints the session from scratch. Its audience is
    :data:`AUDIENCE_TWO_FACTOR`, so presenting it as ``Authorization: Bearer`` fails
    inside PyJWT before any of our code sees it.
    """
    cfg = settings or get_settings()
    if ttl_seconds < 1:
        raise ValueError("a two-factor challenge needs a positive TTL")
    return _encode(
        settings=cfg,
        token_type=TOKEN_TYPE_TWO_FACTOR,
        audience=AUDIENCE_TWO_FACTOR,
        user_id=user_id,
        firm_id=firm_id,
        role=role,
        generation=0,
        token_id=token_id or new_token_id(),
        ttl_seconds=ttl_seconds,
    )


def new_token_family() -> str:
    """Identifier for one login session's chain of rotated refresh tokens."""
    return uuid.uuid4().hex


def decode_token(
    token: str,
    *,
    expected_type: str,
    settings: Settings | None = None,
    verify_expiry: bool = True,
) -> TokenClaims:
    """Verify a token and unpack it, or raise.

    ``verify_expiry=False`` exists for exactly one caller: logout, which should end a
    session even when the presented refresh token has already expired. Everything else
    must leave it alone.

    Raises :class:`~garh_api.errors.TokenExpiredError` (client should refresh) or
    :class:`~garh_api.errors.TokenInvalidError` (client should sign in). The distinction
    is the whole reason the web client can refresh silently.
    """
    cfg = settings or get_settings()
    keys = get_jwt_keys(cfg)
    audience = _AUDIENCE_BY_TYPE.get(expected_type, AUDIENCE_ACCESS)
    if not token or not token.strip():
        raise TokenInvalidError()

    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            keys.public_pem,
            algorithms=[JWT_ALGORITHM],
            audience=audience,
            issuer=cfg.jwt_issuer,
            leeway=CLOCK_SKEW_LEEWAY_SECONDS,
            options={
                "require": ["exp", "iat", "nbf", "sub", "aud", "iss", "jti"],
                "verify_exp": verify_expiry,
                "verify_aud": True,
                "verify_iss": True,
                "verify_signature": True,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except jwt.InvalidTokenError as exc:
        # Covers bad signature, wrong audience, wrong issuer, malformed, nbf in future.
        _log.info("auth.token_rejected", reason=type(exc).__name__)
        raise TokenInvalidError() from exc

    if payload.get("typ") != expected_type:
        _log.info(
            "auth.token_rejected",
            reason="wrong_type",
            expected=expected_type,
            got=str(payload.get("typ")),
        )
        raise TokenInvalidError()

    try:
        user_id = uuid.UUID(str(payload["sub"]))
        firm_id = uuid.UUID(str(payload["fid"]))
    except (KeyError, ValueError, AttributeError) as exc:
        raise TokenInvalidError() from exc

    role = str(payload.get("role") or "member")
    family = payload.get("fam")
    family_started = payload.get("fst")

    if expected_type == TOKEN_TYPE_REFRESH and not family:
        raise TokenInvalidError()

    return TokenClaims(
        token_type=expected_type,
        user_id=user_id,
        firm_id=firm_id,
        role=role,
        generation=int(payload.get("gen") or 0),
        token_id=str(payload["jti"]),
        issued_at=int(payload.get("iat") or 0),
        expires_at=int(payload.get("exp") or 0),
        family=str(family) if family else None,
        family_started_at=int(family_started) if family_started is not None else None,
        raw=payload,
    )


def parse_bearer_header(value: str | None) -> str:
    """Extract the credential from ``Authorization: Bearer <token>``.

    Case-insensitive on the scheme (RFC 7235), strict on the shape.
    """
    if not value:
        raise TokenInvalidError("No credentials were sent.")
    scheme, _, credential = value.partition(" ")
    if scheme.strip().lower() != BEARER_SCHEME or not credential.strip():
        raise TokenInvalidError("That credential isn't a bearer token.")
    return credential.strip()


# ---------------------------------------------------------------------------
# Refresh cookie
# ---------------------------------------------------------------------------


def refresh_cookie_path(settings: Settings | None = None) -> str:
    """Cookie ``Path``: the auth routes only.

    The refresh token is useless to every other endpoint, so it should not be attached
    to every request — that keeps it out of logs, out of proxies, and out of the reach
    of a CSRF against any non-auth POST.
    """
    cfg = settings or get_settings()
    prefix = cfg.api_prefix.rstrip("/")
    return f"{prefix}/auth" if prefix else "/auth"


def refresh_cookie_secure(settings: Settings | None = None) -> bool:
    """``Secure`` everywhere except local dev over plain http.

    Chrome and Firefox treat ``http://localhost`` as a secure context and will store a
    ``Secure`` cookie set there, but Safari historically would not — so dev turns it off
    to keep the stack usable in every browser. Staging and prod are HTTPS-only (§13).
    """
    cfg = settings or get_settings()
    return not cfg.is_dev


def set_refresh_cookie(response: Any, token: str, *, settings: Settings | None = None) -> None:
    """Attach the rotating refresh cookie: HttpOnly, SameSite=Lax, Secure, path-scoped.

    ``SameSite=Lax`` is §13's requirement and doubles as the CSRF control for
    ``POST /auth/refresh``: Lax withholds the cookie from cross-site POSTs, so a
    third-party page cannot silently mint an access token for a logged-in visitor.
    """
    cfg = settings or get_settings()
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        max_age=cfg.refresh_token_ttl_seconds,
        path=refresh_cookie_path(cfg),
        httponly=True,
        secure=refresh_cookie_secure(cfg),
        samesite="lax",
    )


def clear_refresh_cookie(response: Any, *, settings: Settings | None = None) -> None:
    """Expire the cookie. Attributes must match ``set_refresh_cookie`` or browsers
    keep the original."""
    cfg = settings or get_settings()
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=refresh_cookie_path(cfg),
        httponly=True,
        secure=refresh_cookie_secure(cfg),
        samesite="lax",
    )


def read_refresh_cookie(request: Any) -> str:
    """Pull the refresh token out of the request, or raise a 401 with a clear action."""
    token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not token:
        raise RefreshTokenMissingError()
    return str(token)


__all__ = [
    "AUDIENCE_ACCESS",
    "AUDIENCE_REFRESH",
    "AUDIENCE_TWO_FACTOR",
    "BEARER_SCHEME",
    "CLOCK_SKEW_LEEWAY_SECONDS",
    "JWT_ALGORITHM",
    "REFRESH_COOKIE_NAME",
    "TOKEN_TYPE_ACCESS",
    "TOKEN_TYPE_REFRESH",
    "TOKEN_TYPE_TWO_FACTOR",
    "JwtKeys",
    "TokenClaims",
    "clear_refresh_cookie",
    "constant_time_compare",
    "create_access_token",
    "create_refresh_token",
    "create_two_factor_challenge",
    "decode_token",
    "email_domain",
    "generate_opaque_token",
    "get_jwt_keys",
    "hash_secret",
    "new_token_family",
    "new_token_id",
    "parse_bearer_header",
    "pseudonymise",
    "read_refresh_cookie",
    "refresh_cookie_path",
    "refresh_cookie_secure",
    "reset_jwt_keys",
    "set_refresh_cookie",
]
