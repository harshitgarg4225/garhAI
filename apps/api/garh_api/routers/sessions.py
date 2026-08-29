"""Account security: signed-in devices (F-3) and two-factor authentication (F-4).

Both surfaces hang off ``/auth`` because both are things a person does to *their own
sign-in*, and because the refresh cookie is path-scoped to ``{prefix}/auth`` — a device
list that cannot see the cookie cannot tell you which row is the browser you are
reading it in.

============================================  ======  =====================================
route                                          auth    purpose
============================================  ======  =====================================
``GET    /auth/sessions``                     bearer  every device signed in as you
``DELETE /auth/sessions/{family_id}``         bearer  sign one of them out
``POST   /auth/sessions/revoke-others``       bearer  sign out everywhere but here
``GET    /auth/2fa``                          bearer  is a second factor on?
``POST   /auth/2fa/enrol``                    bearer  mint a secret to scan
``POST   /auth/2fa/activate``                 bearer  prove it, get recovery codes
``POST   /auth/2fa/recovery-codes``           bearer  replace the recovery set
``POST   /auth/2fa/disable``                  bearer  turn it off (TOTP *or* recovery)
``POST   /auth/2fa/verify``                   none    finish a sign-in that needs a code
============================================  ======  =====================================

**F-3 is a surface, not a store.** Refresh-token families and the logout-all generation
already existed in :class:`garh_api.auth.SessionStore`; nothing exposed them, so a user
whose token had been stolen had exactly one lever — sign out of everything — and no way
to see that there was a second device at all. A "family" is one sign-in: it survives
every silent 15-minute rotation, so it is the honest unit of "a device".

**Tenancy.** Every key in the session store is hash-tagged by *user* id
(``garh:auth:fam:{u:<uid>}:<family>``), so one firm asking to revoke another firm's
family looks up a key that does not exist and gets a 404 — the same answer as a family
id that was never issued. That is asserted directly in ``tests/test_sessions.py`` and
through the sweep in ``tests/test_cross_tenant.py``.

**F-4 exists because of a live weakness.** This instance runs ``APP_ENV=dev`` with no
``SMTP_HOST``, so ``POST /auth/otp`` returns the sign-in code in its own response body
(:func:`garh_api.auth.dev_echo_otp_enabled`). Anyone who knows an address on the
instance can sign in as that person, and no code here can change that without mail
credentials. What a second factor changes is that knowing the code stops being enough:
:meth:`garh_api.auth.AuthService.verify_otp` returns a 403 ``two_factor_required``
carrying a five-minute challenge, and only ``POST /auth/2fa/verify`` with a live TOTP
or recovery code turns that into a session.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request, Response, status
from pydantic import Field, StringConstraints

from garh_api.auth import LiveSession
from garh_api.deps import Auth, DbSession, Sessions, Tenant, rate_limit_verify_ip
from garh_api.errors import (
    PROBLEM_RESPONSES,
    AuthenticationError,
    InvalidRequestError,
)
from garh_api.repositories.audit_log import ACTION_AUTH_LOGOUT, AuditLogRepository
from garh_api.repositories.two_factor import TwoFactorRepository
from garh_api.repositories.users import UserRepository
from garh_api.routers.auth import _session_body
from garh_api.schemas.auth import AuthModel, SessionResponse
from garh_api.security import (
    REFRESH_COOKIE_NAME,
    TOKEN_TYPE_REFRESH,
    decode_token,
    set_refresh_cookie,
)
from garh_api.tenancy import EntityNotFoundError, TenantCtx
from garh_api.twofactor import (
    ACTION_TWO_FACTOR_DISABLED,
    ACTION_TWO_FACTOR_ENABLED,
    ACTION_TWO_FACTOR_RECOVERY_REGENERATED,
    TOTP_DIGITS,
    TOTP_STEP_SECONDS,
    TwoFactorService,
    status_payload,
)

router = APIRouter(prefix="/auth", tags=["auth"], responses=PROBLEM_RESPONSES)


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------
# Defined here rather than in ``schemas/`` (which ``routers/health.py`` also does):
# they are consumed by exactly this module, and the shared conventions — camelCase
# aliases, ``extra="forbid"``, ``strict=True`` — come from ``AuthModel`` so nothing
# about the wire contract is re-decided.

#: A refresh family id: 32 hex characters from :func:`~garh_api.security.new_token_family`.
#: Constrained at the path so a probe cannot use this route to inject Redis key syntax
#: — the store builds keys by interpolation, and ``{u:<uid>}`` hash tags are literal.
FamilyId = Annotated[str, Path(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]

#: Wide on purpose. A TOTP code is 6 digits and a recovery code is 16 base32
#: characters in 4 dashed groups; a tighter pattern would 422 one of them, and a 422
#: that depends on *which kind* of secret you sent is an oracle. Everything is
#: normalised and compared in :mod:`garh_api.twofactor`.
SecondFactorCode = Annotated[str, StringConstraints(min_length=6, max_length=40)]


class SessionSummary(AuthModel):
    """One signed-in device."""

    id: str = Field(description="Refresh-family id — pass it to DELETE to sign this one out.")
    current: bool = Field(description="True for the device reading this response.")
    started_at: int = Field(description="Epoch seconds when this sign-in happened.")
    last_used_at: int = Field(description="Epoch seconds of the last token rotation.")
    ip: str | None = Field(default=None, description="Address the sign-in came from.")
    user_agent: str | None = None
    device: str = Field(description="Human-readable guess at the browser and platform.")


class SessionListResponse(AuthModel):
    items: list[SessionSummary]
    count: int


class SessionsRevokedResponse(AuthModel):
    sessions_ended: int = Field(description="How many devices were signed out.")


class TwoFactorStatusResponse(AuthModel):
    enabled: bool
    pending: bool = Field(description="A secret was minted but never proved with a code.")
    confirmed_at: str | None = None
    recovery_codes_remaining: int


class TwoFactorEnrolResponse(AuthModel):
    """Shown once. Nothing re-reads the secret out of the database for display."""

    secret: str = Field(description="base32 secret, for typing in by hand.")
    otpauth_uri: str = Field(description="Render as a QR code for the authenticator app.")
    digits: int
    period_seconds: int


class SecondFactorRequest(AuthModel):
    code: SecondFactorCode = Field(
        description="A 6-digit code from your authenticator app, or a recovery code."
    )


class RecoveryCodesResponse(AuthModel):
    """The only response that ever carries recovery codes in the clear."""

    recovery_codes: list[str]
    enabled: bool = True


class TwoFactorVerifyRequest(AuthModel):
    challenge: str = Field(
        min_length=16,
        max_length=4096,
        description="The ``challenge`` from the 403 ``two_factor_required`` body.",
    )
    code: SecondFactorCode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

#: Browser/platform tokens, longest first so "Edg" is not read as "Chrome" (Edge's UA
#: contains both) and "Chrome" is not read as "Safari" (Chrome's contains that too).
_BROWSERS: tuple[tuple[str, str], ...] = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("Firefox/", "Firefox"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)
_PLATFORMS: tuple[tuple[str, str], ...] = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Mac OS X", "macOS"),
    ("Windows", "Windows"),
    ("Linux", "Linux"),
)


def describe_user_agent(user_agent: str | None) -> str:
    """ "Chrome on Windows" from a UA string, or an honest "Unknown device".

    A guess, and labelled as one in the UI copy. The value is recognition — a user
    scanning this list is looking for the row that is *not* theirs — so a wrong-but-
    plausible label would be worse than none, which is why an unrecognised agent says
    so instead of being bucketed into the nearest match.
    """
    if not user_agent:
        return "Unknown device"
    browser = next((label for token, label in _BROWSERS if token in user_agent), None)
    platform = next((label for token, label in _PLATFORMS if token in user_agent), None)
    if browser and platform:
        return "%s on %s" % (browser, platform)
    return browser or platform or "Unknown device"


def current_family(request: Request) -> str | None:
    """The refresh family of the browser making this call, if it sent its cookie.

    ``verify_expiry=False``: an access token can be live while the refresh cookie is
    past ``exp`` (they have different lifetimes), and in that window the caller is
    still, factually, *this* device. Marking their own row "not current" would invite
    them to revoke the session they are sitting in.

    Never an error. Absent, malformed and expired-beyond-parsing all mean "we cannot
    tell", and the list is still correct — it just has no ``current`` flag on it.
    """
    raw = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw:
        return None
    try:
        claims = decode_token(raw, expected_type=TOKEN_TYPE_REFRESH, verify_expiry=False)
    except Exception:
        return None
    return claims.family


def _summary(live: LiveSession, *, current: str | None) -> SessionSummary:
    return SessionSummary(
        id=live.family,
        current=current is not None and live.family == current,
        started_at=live.started_at,
        last_used_at=live.last_used_at,
        ip=live.ip or None,
        user_agent=live.user_agent or None,
        device=describe_user_agent(live.user_agent),
    )


def _two_factor(session: Any, ctx: TenantCtx) -> TwoFactorService:
    return TwoFactorService(TwoFactorRepository(session, ctx))


def _require_user(ctx: TenantCtx) -> uuid.UUID:
    if ctx.user_id is None:  # pragma: no cover - require_tenant guarantees a user
        raise AuthenticationError()
    return ctx.user_id


# ---------------------------------------------------------------------------
# F-3: signed-in devices
# ---------------------------------------------------------------------------


@router.get(
    "/sessions",
    response_model=SessionListResponse,
    summary="Every device signed in as you",
)
async def list_sessions(request: Request, ctx: Tenant, sessions: Sessions) -> SessionListResponse:
    """The refresh families still alive for this user, most recently used first.

    Reads Redis and fails **closed** (503) rather than returning an empty list: the
    person most likely to load this screen is someone checking whether they have been
    compromised, and "no other devices" is the worst possible wrong answer for them.
    """
    user_id = _require_user(ctx)
    current = current_family(request)
    live = await sessions.list_families(user_id)
    items = [_summary(entry, current=current) for entry in live]
    return SessionListResponse(items=items, count=len(items))


@router.delete(
    "/sessions/{family_id}",
    response_model=SessionsRevokedResponse,
    summary="Sign one device out",
)
async def revoke_session(
    family_id: FamilyId,
    ctx: Tenant,
    sessions: Sessions,
    session: DbSession,
) -> SessionsRevokedResponse:
    """Revoke one refresh family. 404 if it is not a live family **of yours**.

    Family records are keyed by user id, so another firm's family — or another
    colleague's — resolves to a key that does not exist and is answered exactly like an
    id that was never issued (§13: a cross-tenant read is indistinguishable from a
    missing one).

    The device keeps its access token until it expires, at most 15 minutes: killing it
    sooner needs the generation counter, which is user-wide and would sign out every
    device including this one. ``POST /auth/logout-all`` is that lever, deliberately.
    """
    user_id = _require_user(ctx)
    revoked = await sessions.revoke_family(
        user_id=user_id, family=family_id, reason="session_revoked"
    )
    if not revoked:
        raise EntityNotFoundError("session", family_id)
    await AuditLogRepository(session, ctx).record(
        ACTION_AUTH_LOGOUT,
        entity="user",
        entity_id=user_id,
        meta={"family": family_id, "via": "session_manager"},
    )
    return SessionsRevokedResponse(sessions_ended=1)


@router.post(
    "/sessions/revoke-others",
    response_model=SessionsRevokedResponse,
    summary="Sign out everywhere except this device",
)
async def revoke_other_sessions(
    request: Request, ctx: Tenant, sessions: Sessions, session: DbSession
) -> SessionsRevokedResponse:
    """Revoke every family but the one this browser is holding.

    Requires the refresh cookie, and says so rather than guessing: without it we
    cannot tell which family to keep, and the two available guesses are "revoke
    nothing" (useless) and "revoke everything" (signs the user out of the tab they
    just clicked in). ``POST /auth/logout-all`` already does the second thing on
    purpose, so this one refuses instead.
    """
    user_id = _require_user(ctx)
    current = current_family(request)
    if current is None:
        raise InvalidRequestError(
            "We couldn't tell which device you're on, so we didn't sign any out.",
            action="Sign out of everything instead, then sign back in here.",
        )

    live = await sessions.list_families(user_id)
    revoked = 0
    for entry in live:
        if entry.family == current:
            continue
        if await sessions.revoke_family(
            user_id=user_id, family=entry.family, reason="revoke_others"
        ):
            revoked += 1

    await AuditLogRepository(session, ctx).record(
        ACTION_AUTH_LOGOUT,
        entity="user",
        entity_id=user_id,
        meta={"scope": "other_sessions", "sessionsEnded": revoked, "keptFamily": current},
    )
    return SessionsRevokedResponse(sessions_ended=revoked)


# ---------------------------------------------------------------------------
# F-4: two-factor authentication
# ---------------------------------------------------------------------------


@router.get("/2fa", response_model=TwoFactorStatusResponse, summary="Two-factor status")
async def two_factor_status(ctx: Tenant, session: DbSession) -> TwoFactorStatusResponse:
    """Whether a second factor is on, and how many recovery codes are left.

    ``recoveryCodesRemaining`` is the number that matters operationally: a user down
    to their last code is one lost phone away from a support ticket.
    """
    user_id = _require_user(ctx)
    payload = status_payload(await _two_factor(session, ctx).status(user_id))
    return TwoFactorStatusResponse.model_validate(payload)


@router.post(
    "/2fa/enrol",
    response_model=TwoFactorEnrolResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start two-factor enrolment",
)
async def enrol_two_factor(ctx: Tenant, session: DbSession) -> TwoFactorEnrolResponse:
    """Mint a secret and stage it unconfirmed.

    Nothing is enforced yet — the account is still single-factor until
    ``POST /auth/2fa/activate`` proves the user's app actually holds the secret.
    Enrolling before proving is what stops a mis-scanned QR code from locking someone
    out of their own firm.

    Calling this twice replaces the *unconfirmed* secret (the "the QR code wouldn't
    scan, give me another" path) and is refused once a factor is live.
    """
    user_id = _require_user(ctx)
    user = await UserRepository(session, ctx).require(user_id)
    started = await _two_factor(session, ctx).begin_enrolment(user_id, account=user.email)
    return TwoFactorEnrolResponse(
        secret=started.secret,
        otpauth_uri=started.otpauth_uri,
        digits=TOTP_DIGITS,
        period_seconds=TOTP_STEP_SECONDS,
    )


@router.post(
    "/2fa/activate",
    response_model=RecoveryCodesResponse,
    summary="Confirm enrolment and collect recovery codes",
)
async def activate_two_factor(
    payload: SecondFactorRequest, ctx: Tenant, session: DbSession
) -> RecoveryCodesResponse:
    """Prove the staged secret, turn the factor on, and return the recovery codes.

    **This is the only response that will ever contain them.** They are stored as
    ``sha256`` digests; there is no route, no admin screen and no support tool that
    can read them back. The client must show them and tell the user to keep them.
    """
    user_id = _require_user(ctx)
    codes = await _two_factor(session, ctx).activate(user_id, payload.code)
    await AuditLogRepository(session, ctx).record(
        ACTION_TWO_FACTOR_ENABLED,
        entity="user",
        entity_id=user_id,
        meta={"codesIssued": len(codes)},
    )
    return RecoveryCodesResponse(recovery_codes=codes)


@router.post(
    "/2fa/recovery-codes",
    response_model=RecoveryCodesResponse,
    summary="Replace your recovery codes",
)
async def regenerate_recovery_codes(
    payload: SecondFactorRequest, ctx: Tenant, session: DbSession
) -> RecoveryCodesResponse:
    """Issue a fresh set and invalidate the old one. Requires a live proof."""
    user_id = _require_user(ctx)
    codes = await _two_factor(session, ctx).regenerate_recovery_codes(user_id, payload.code)
    await AuditLogRepository(session, ctx).record(
        ACTION_TWO_FACTOR_RECOVERY_REGENERATED,
        entity="user",
        entity_id=user_id,
        meta={"codesIssued": len(codes)},
    )
    return RecoveryCodesResponse(recovery_codes=codes)


@router.post(
    "/2fa/disable",
    response_model=TwoFactorStatusResponse,
    summary="Turn two-factor off",
)
async def disable_two_factor(
    payload: SecondFactorRequest, ctx: Tenant, session: DbSession
) -> TwoFactorStatusResponse:
    """Remove the second factor. A **recovery code is accepted here**, deliberately.

    The user who needs this most is the one whose phone is gone: they can still sign
    in with a recovery code, and if only a TOTP code could turn the factor off they
    would be permanently stuck behind a credential they can never produce again.
    """
    user_id = _require_user(ctx)
    service = _two_factor(session, ctx)
    await service.disable(user_id, payload.code)
    await AuditLogRepository(session, ctx).record(
        ACTION_TWO_FACTOR_DISABLED,
        entity="user",
        entity_id=user_id,
        meta={},
    )
    return TwoFactorStatusResponse.model_validate(status_payload(await service.status(user_id)))


@router.post(
    "/2fa/verify",
    response_model=SessionResponse,
    dependencies=[Depends(rate_limit_verify_ip)],
    summary="Finish a sign-in that needs a second factor",
)
async def verify_second_factor(
    payload: TwoFactorVerifyRequest,
    response: Response,
    auth: Auth,
    session: DbSession,
) -> SessionResponse:
    """Challenge + live code → the same session ``POST /auth/verify`` would have issued.

    Unauthenticated by necessity — the caller has no access token yet; the challenge is
    the credential, and it is worthless on its own. Two limits guard it: this route's
    per-IP budget, and the per-user attempt budget inside
    :func:`garh_api.twofactor.two_factor_attempt_rule`, which is **fail-closed** so a
    Redis outage cannot turn six digits into an unthrottled guessing game.
    """
    issued = await auth.complete_two_factor(payload.challenge, payload.code)
    set_refresh_cookie(response, issued.refresh_token, settings=auth.settings)
    return await _session_body(session, issued)


__all__ = ["current_family", "describe_user_agent", "router"]
