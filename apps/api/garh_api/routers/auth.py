"""Auth routes (playbook §11: ``POST /auth/otp``, ``POST /auth/verify``).

Seven endpoints, and only two of them are in §11's one-line sketch — the other five are
what those two imply once you write down the session lifecycle:

============================  ====  =========================================
route                         auth  purpose
============================  ====  =========================================
``POST /auth/signup``         no    create a firm + first admin, then send a code
``POST /auth/otp``            no    send a sign-in code
``POST /auth/verify``         no    code → access token + refresh cookie
``POST /auth/refresh``        cookie  rotate the session (silent, every ~15 min)
``POST /auth/logout``         cookie  end this session
``POST /auth/logout-all``     bearer  end every session (§13 "logout-all")
``GET  /auth/me``             bearer  who am I
============================  ====  =========================================

The handlers are deliberately thin: every decision — rate limits, enumeration
resistance, rotation, reuse detection, audit — lives in
:class:`~garh_api.auth.AuthService`, which is testable without a web server. What lives
*here* is the HTTP shape: status codes, the refresh cookie, and making sure a rejected
refresh also clears that cookie.

**The refresh token is never in a response body.** It travels only in the ``HttpOnly``,
``SameSite=Lax``, ``Secure``, path-scoped cookie (§13). ``SameSite=Lax`` doubles as the
CSRF control for ``POST /auth/refresh``: browsers withhold Lax cookies from cross-site
POSTs, so another origin cannot silently mint an access token for a signed-in visitor.
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import APIRouter, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from garh_api.auth import IssuedSession
from garh_api.config import Settings
from garh_api.deps import Auth, DbSession, Tenant
from garh_api.errors import PROBLEM_RESPONSES, AuthenticationError
from garh_api.logging import current_request_id
from garh_api.repositories.firms import FirmRepository
from garh_api.repositories.users import UserRepository
from garh_api.schemas.auth import (
    FirmSummary,
    LogoutResponse,
    MeResponse,
    OtpIssuedResponse,
    OtpRequest,
    SessionResponse,
    SignupRequest,
    UserProfile,
    VerifyRequest,
)
from garh_api.security import (
    REFRESH_COOKIE_NAME,
    clear_refresh_cookie,
    read_refresh_cookie,
    set_refresh_cookie,
)
from garh_api.tenancy import TenantCtx

router = APIRouter(prefix="/auth", tags=["auth"], responses=PROBLEM_RESPONSES)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _expired_cookie_header(settings: Settings) -> Mapping[str, str]:
    """The ``Set-Cookie`` line that deletes the refresh cookie.

    Rendered by asking :func:`~garh_api.security.clear_refresh_cookie` to write onto a
    throwaway response, so the ``Path``/``Secure``/``SameSite`` attributes are byte-for-
    byte the ones used when the cookie was set. Browsers ignore a deletion whose
    attributes do not match, which is a genuinely easy way to leave a dead token in
    place forever.
    """
    probe = Response()
    clear_refresh_cookie(probe, settings=settings)
    value = probe.headers.get("set-cookie")
    return {"set-cookie": value} if value else {}


async def _session_body(
    session: AsyncSession,
    issued: IssuedSession,
) -> SessionResponse:
    """Render an :class:`~garh_api.auth.IssuedSession` as the wire response.

    Loads the ``users`` row rather than reusing the pre-auth principal so ``coaNumber``
    is populated and sign-in returns the same user shape as ``GET /auth/me``.
    """
    principal = issued.principal
    ctx = TenantCtx(
        firm_id=principal.firm_id,
        user_id=principal.user_id,
        role=principal.role,
        request_id=current_request_id(),
    )
    user = await UserRepository(session, ctx).require(principal.user_id)
    return SessionResponse(
        access_token=issued.access_token,
        token_type="Bearer",
        expires_in=issued.expires_in_seconds,
        expires_at=issued.access_expires_at,
        user=UserProfile.from_user(user),
        firm=FirmSummary.from_principal(principal),
    )


# ---------------------------------------------------------------------------
# Sign up / sign in
# ---------------------------------------------------------------------------


@router.post(
    "/signup",
    response_model=OtpIssuedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a firm and send its first admin a sign-in code",
)
async def signup(payload: SignupRequest, auth: Auth) -> OtpIssuedResponse:
    """Create the tenant, then fall straight into the sign-in flow.

    No tokens come back: the new admin still has to prove they own the address. This is
    the one auth route that admits an email is already taken (409
    ``email_already_registered``) — a signup form that silently does nothing strands the
    user, and the route is per-IP rate limited. Sign-in stays non-enumerable.
    """
    result = await auth.signup(
        firm_name=payload.firm_name,
        name=payload.name,
        email=payload.email,
        coa_number=payload.coa_number,
    )
    return OtpIssuedResponse(
        expires_in_seconds=result.expires_in_seconds,
        resend_after_seconds=result.resend_after_seconds,
        dev_code=result.dev_code,
    )


@router.post(
    "/otp",
    response_model=OtpIssuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email a sign-in code",
)
async def request_otp(payload: OtpRequest, auth: Auth) -> OtpIssuedResponse:
    """Send a 6-digit code, valid 10 minutes, 5 attempts (§13).

    202, not 200: we have accepted the request; the email is still in flight. The body
    is identical for a known and an unknown address — see
    :meth:`~garh_api.auth.AuthService.request_otp` for why, and for the three rate
    limits this consumes.
    """
    result = await auth.request_otp(payload.email)
    return OtpIssuedResponse(
        expires_in_seconds=result.expires_in_seconds,
        resend_after_seconds=result.resend_after_seconds,
        dev_code=result.dev_code,
    )


@router.post(
    "/verify",
    response_model=SessionResponse,
    summary="Exchange a code for a session",
)
async def verify_otp(
    payload: VerifyRequest,
    response: Response,
    auth: Auth,
    session: DbSession,
) -> SessionResponse:
    """Correct code → 15-minute access token in the body, refresh token in the cookie.

    Wrong, expired, exhausted or never-issued all return the same 400 ``otp_invalid``.
    """
    issued = await auth.verify_otp(payload.email, payload.code)
    set_refresh_cookie(response, issued.refresh_token, settings=auth.settings)
    return await _session_body(session, issued)


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


@router.post(
    "/refresh",
    response_model=SessionResponse,
    summary="Rotate the session",
)
async def refresh_session(
    request: Request,
    response: Response,
    auth: Auth,
    session: DbSession,
) -> SessionResponse:
    """Spend the refresh cookie and issue its successor (§13 refresh rotation).

    The old token is dead the moment this succeeds. Presenting it again is treated as
    theft and kills the whole family — see
    :class:`~garh_api.errors.RefreshTokenReuseError`.

    Every *authentication* failure also clears the cookie. Without that the browser
    would keep replaying a token we have already declared dead, turning one bad refresh
    into an infinite 401 loop the user can only escape by clearing site data.

    Only :class:`~garh_api.errors.AuthenticationError` is caught, deliberately. A 503
    from a Redis blip must leave the cookie alone — that token is still perfectly good,
    and signing everybody out because a cache hiccuped would be a self-inflicted
    outage.
    """
    settings = auth.settings
    try:
        raw = read_refresh_cookie(request)
        issued = await auth.refresh(raw)
    except AuthenticationError as exc:
        exc.with_headers(_expired_cookie_header(settings))
        raise
    set_refresh_cookie(response, issued.refresh_token, settings=settings)
    return await _session_body(session, issued)


@router.post(
    "/logout",
    response_model=LogoutResponse,
    summary="End this session",
)
async def logout(request: Request, response: Response, auth: Auth) -> LogoutResponse:
    """Revoke this refresh family and clear the cookie. Always 200.

    Signing out is not something a user can fail at: a missing, expired or malformed
    cookie still clears the browser state and still returns success.
    """
    raw = request.cookies.get(REFRESH_COOKIE_NAME)
    ended = await auth.logout(raw)
    clear_refresh_cookie(response, settings=auth.settings)
    return LogoutResponse(signed_out=True, sessions_ended=1 if ended else 0)


@router.post(
    "/logout-all",
    response_model=LogoutResponse,
    summary="End every session on every device",
)
async def logout_all(response: Response, auth: Auth, ctx: Tenant) -> LogoutResponse:
    """Bump the user's token generation, invalidating every access and refresh token.

    Requires a *live* access token: this is a destructive action and should be an
    intentional one, not something a stale tab can trigger.
    """
    ended = await auth.logout_all(ctx)
    clear_refresh_cookie(response, settings=auth.settings)
    return LogoutResponse(signed_out=True, sessions_ended=ended)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@router.get(
    "/me",
    response_model=MeResponse,
    summary="The signed-in user and their firm",
)
async def me(session: DbSession, ctx: Tenant) -> MeResponse:
    """Profile for the app shell. Read from the database, not from the token.

    The token carries only ids and a role (§13 keeps PII out of JWTs), and reading the
    row means a renamed firm or an edited profile shows up on the next page load rather
    than after the next sign-in.
    """
    if ctx.user_id is None:  # pragma: no cover - require_tenant guarantees a user
        raise AuthenticationError()
    user = await UserRepository(session, ctx).require(ctx.user_id)
    firm = await FirmRepository(session, ctx).get_current()
    return MeResponse(
        user=UserProfile.from_user(user),
        firm=FirmSummary(id=firm.id, name=firm.name),
    )


__all__ = ["router"]
