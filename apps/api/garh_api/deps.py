"""FastAPI dependencies — **the only place an HTTP request becomes a TenantCtx**.

Everything downstream (routers, services, repositories) receives an already-resolved
:class:`~garh_api.tenancy.TenantCtx` and cannot widen it. That is the whole point: if
tenant resolution happened in three places, a security review would have to check three
places, and one of them would eventually be wrong.

Import surface a router needs::

    from garh_api.deps import DbSession, Tenant, AdminTenant, Origin, rate_limit_ops

    @router.post("/projects/{project_id}/ops", dependencies=[Depends(rate_limit_ops)])
    async def append_ops(project_id: UUID, session: DbSession, ctx: Tenant) -> ...:
        return await OpRepository(session, ctx).append(...)

The ``Annotated`` aliases at the bottom are the intended style — they keep handler
signatures readable and mean a change of dependency wiring happens here, once.

**Client IP.** ``request.client.host`` is the peer address. Behind a load balancer that
is the balancer, so every caller would share one rate-limit bucket. Two supported
fixes, in order of preference:

1. run uvicorn with ``--proxy-headers --forwarded-allow-ips=<lb-ip>`` — uvicorn then
   rewrites ``request.client`` itself, from a *trusted* peer only, and this module needs
   no configuration;
2. set ``TRUSTED_PROXY_HOPS=<n>`` to make this module read the n-th-from-the-right
   ``X-Forwarded-For`` entry.

The default is 0: ``X-Forwarded-For`` is **ignored** unless you opt in. A spoofable
client IP means spoofable per-IP auth limits, so the unsafe direction is not the
default — even though it means a misconfigured deployment throttles rather than
over-admits.
"""

# NO `from __future__ import annotations` in this module, deliberately.
# FirmRateLimit / IpRateLimit are callable-CLASS dependencies, and FastAPI
# resolves a dependency's annotations through `call.__globals__` — which an
# instance does not have. With postponed evaluation their `response: Response`
# stays an unresolvable string, FastAPI falls back to treating it as a query
# parameter, and every router that mounts them dies at import time with
# `PydanticUndefinedAnnotation: name 'Response' is not defined`. Eager
# annotations resolve at class-definition time, where Response is in scope.

import os
import uuid
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from garh_api.auth import (
    AuthService,
    RequestOrigin,
    SessionStore,
    authenticate_access_token,
    tenant_ctx_from_claims,
)
from garh_api.config import Settings, get_settings
from garh_api.db import get_db_session
from garh_api.errors import AuthenticationError, ShareLinkInvalidError
from garh_api.logging import bind_tenant_context, current_request_id, get_logger
from garh_api.ratelimit import (
    RateLimitDecision,
    RateLimitRule,
    auth_ip_rule,
    enforce_rate_limit,
    export_jobs_per_firm_rule,
    ops_per_firm_rule,
    render_jobs_per_firm_rule,
    sheet_jobs_per_firm_rule,
    solver_jobs_per_firm_rule,
    verify_ip_rule,
)
from garh_api.repositories.share_links import ShareTokenResolver
from garh_api.security import TokenClaims, parse_bearer_header
from garh_api.tenancy import PermissionDeniedError, TenantCtx

_log = get_logger(__name__)

#: How many reverse proxies sit in front of the API. See the module docstring.
TRUSTED_PROXY_HOPS: int = max(0, int(os.environ.get("TRUSTED_PROXY_HOPS", "0") or 0))

#: Used when the peer address is genuinely unknown (ASGI test transports, unix
#: sockets). All such callers share one bucket, which is the safe direction.
UNKNOWN_CLIENT_IP = "unknown"


# ---------------------------------------------------------------------------
# Request-scoped primitives
# ---------------------------------------------------------------------------


def get_app_settings() -> Settings:
    """Cached settings, as a dependency (so tests can override it)."""
    return get_settings()


def client_ip(request: Request) -> str:
    """The caller's address, as trustworthily as the deployment allows."""
    if TRUSTED_PROXY_HOPS > 0:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            hops = [part.strip() for part in forwarded.split(",") if part.strip()]
            if hops:
                index = max(0, len(hops) - TRUSTED_PROXY_HOPS)
                return hops[index][:64]
    if request.client is not None and request.client.host:
        return str(request.client.host)[:64]
    return UNKNOWN_CLIENT_IP


def request_origin(request: Request) -> RequestOrigin:
    """IP + user agent, for rate limiting and the audit trail."""
    return RequestOrigin(ip=client_ip(request), user_agent=request.headers.get("user-agent"))


#: One session and one transaction per request; commits when the handler returns.
#:
#: A direct alias of :func:`garh_api.db.get_db_session`, not a wrapper — wrapping an
#: async-generator dependency in another async generator changes when the inner
#: ``finally`` runs on the error path, which is how request transactions get silently
#: rolled back instead of committed. Routers get one import for every dependency;
#: the semantics stay exactly those of :func:`garh_api.db.session_scope`.
db_session = get_db_session


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


async def access_token_claims(
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> TokenClaims:
    """Verify the bearer token: signature, claims, and logout-all generation.

    Missing credentials are ``unauthenticated``; present-but-bad ones are
    ``token_invalid``/``token_expired``/``token_revoked``. The web client relies on
    that distinction to refresh silently instead of bouncing the user to sign-in.
    """
    header = request.headers.get("authorization")
    if not header:
        raise AuthenticationError()
    raw = parse_bearer_header(header)
    return await authenticate_access_token(raw, settings=settings)


async def require_tenant(
    request: Request,
    claims: Annotated[TokenClaims, Depends(access_token_claims)],
) -> TenantCtx:
    """Resolve the request to a tenant context. Every authenticated route uses this.

    Also binds ``firm_id``/``user_id``/``role`` to the log context, so every line
    emitted for the rest of the request carries them (§18 observability) and
    :func:`garh_api.tenancy.system_unscoped_session` stands out by *not* having them.
    """
    ctx = tenant_ctx_from_claims(claims, request_id=current_request_id())
    bind_tenant_context(ctx.firm_id, ctx.user_id, ctx.role)
    request.state.tenant = ctx
    return ctx


async def optional_tenant(
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> TenantCtx | None:
    """Tenant context if the caller sent credentials, ``None`` if they sent none.

    A *bad* credential is still an error — "optional" means optional to supply, not
    optional to be valid. Used by endpoints that behave differently when signed in
    (the demo project, share pages opened by their owner).
    """
    if not request.headers.get("authorization"):
        return None
    claims = await access_token_claims(request, settings)
    ctx = tenant_ctx_from_claims(claims, request_id=current_request_id())
    bind_tenant_context(ctx.firm_id, ctx.user_id, ctx.role)
    request.state.tenant = ctx
    return ctx


async def require_admin(ctx: Annotated[TenantCtx, Depends(require_tenant)]) -> TenantCtx:
    """Firm-admin routes: seat management, billing, firm settings."""
    ctx.require_admin("this action")
    return ctx


async def require_writer(ctx: Annotated[TenantCtx, Depends(require_tenant)]) -> TenantCtx:
    """Any mutating route. Rejects a read-only share viewer with 403, not 404 —
    the viewer already knows the resource exists, so there is nothing to conceal."""
    ctx.require_write("this change")
    return ctx


def require_section(section: str) -> Callable[..., Any]:
    """Dependency factory gating a share-link section (``plan``, ``renders``, …).

    Firm users always pass; a share viewer passes only if their scope lists it::

        @router.get("/sheets", dependencies=[Depends(require_section("sheets"))])
    """

    async def dependency(ctx: Annotated[TenantCtx, Depends(require_tenant)]) -> TenantCtx:
        ctx.require_scope(section)
        return ctx

    return dependency


async def get_auth_service(
    session: Annotated[AsyncSession, Depends(db_session)],
    origin: Annotated[RequestOrigin, Depends(request_origin)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> AuthService:
    """The auth service, wired to this request's session and origin."""
    return AuthService(session, origin, settings=settings)


def get_session_store(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> SessionStore:
    """Refresh-family / generation store. Rarely needed directly outside auth."""
    return SessionStore(settings=settings)


# ---------------------------------------------------------------------------
# Share links (the other, read-only way to get a TenantCtx)
# ---------------------------------------------------------------------------


async def resolve_share_context(
    token: str,
    session: AsyncSession,
    *,
    request_id: str | None = None,
) -> tuple[TenantCtx, uuid.UUID]:
    """Turn a share token into a ``share_viewer`` context plus its project id.

    Unknown, revoked and expired tokens are one indistinguishable
    :class:`~garh_api.errors.ShareLinkInvalidError`. The returned context has
    ``can_write == False``, so the read-only surface is enforced by the same repository
    machinery as everything else rather than by the viewer router remembering to.
    """
    resolved = await ShareTokenResolver(session).resolve(token)
    if resolved is None:
        raise ShareLinkInvalidError()
    ctx = TenantCtx.for_share_viewer(
        firm_id=resolved.firm_id,
        share_link_id=resolved.share_link_id,
        scope=resolved.scope,
        request_id=request_id,
    )
    bind_tenant_context(ctx.firm_id, None, ctx.role)
    return ctx, resolved.project_id


async def require_share_viewer(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
) -> TenantCtx:
    """Share-viewer context from a ``{token}`` path parameter.

    ::

        @share_router.get("/share/{token}/plan")
        async def plan(ctx: ShareViewer, ...): ...
    """
    token = str(request.path_params.get("token") or "")
    if not token:
        raise ShareLinkInvalidError()
    ctx, project_id = await resolve_share_context(token, session, request_id=current_request_id())
    request.state.tenant = ctx
    request.state.share_project_id = project_id
    return ctx


def share_project_id(request: Request) -> uuid.UUID:
    """The project a share token points at. Requires :func:`require_share_viewer`."""
    project_id = getattr(request.state, "share_project_id", None)
    if project_id is None:  # pragma: no cover - programmer error, not user input
        raise PermissionDeniedError("This link doesn't include that.")
    return project_id


# ---------------------------------------------------------------------------
# Rate limiting (route layer)
# ---------------------------------------------------------------------------
#
# Why classes and not the ``@rate_limited`` decorator from garh_api.ratelimit: a
# decorator hides the handler's real signature from FastAPI, which silently breaks
# dependency injection and the OpenAPI schema. A callable dependency keeps both.


class FirmRateLimit:
    """Per-firm limit as a route dependency (§11: 60 ops/s, 10 solver jobs/hr).

    ::

        @router.post("/solve", dependencies=[Depends(rate_limit_solver_jobs)])

    Sets ``X-RateLimit-Limit`` / ``-Remaining`` / ``-Window`` on the response, and
    ``Retry-After`` comes with the 429 that :class:`RateLimitedError` raises.
    """

    def __init__(self, rule_factory: Callable[..., RateLimitRule], *, cost: int = 1) -> None:
        self._rule_factory = rule_factory
        self._cost = cost

    async def __call__(
        self,
        response: Response,
        ctx: Annotated[TenantCtx, Depends(require_tenant)],
        settings: Annotated[Settings, Depends(get_app_settings)],
    ) -> RateLimitDecision:
        decision = await enforce_rate_limit(
            self._rule_factory(settings), f"firm:{ctx.firm_id}", cost=self._cost
        )
        response.headers.update(decision.headers())
        return decision


class IpRateLimit:
    """Per-IP limit as a route dependency (§13: "rate limits per firm + per IP on auth").

    **Not used on ``/auth/otp`` and ``/auth/verify``.** :class:`~garh_api.auth.AuthService`
    applies the IP limits itself so it can interleave them with the per-address limits
    in a deliberate order; attaching this as well would charge the bucket twice. Use it
    for other unauthenticated surfaces (share-link viewing, share comments).
    """

    def __init__(self, rule_factory: Callable[..., RateLimitRule], *, cost: int = 1) -> None:
        self._rule_factory = rule_factory
        self._cost = cost

    async def __call__(
        self,
        request: Request,
        response: Response,
        settings: Annotated[Settings, Depends(get_app_settings)],
    ) -> RateLimitDecision:
        decision = await enforce_rate_limit(
            self._rule_factory(settings), f"ip:{client_ip(request)}", cost=self._cost
        )
        response.headers.update(decision.headers())
        return decision


#: Ready-made instances. Routers import these rather than building their own, so the
#: security checklist has one list to audit.
rate_limit_ops = FirmRateLimit(ops_per_firm_rule)
rate_limit_solver_jobs = FirmRateLimit(solver_jobs_per_firm_rule)
#: F-7. The render enqueue charges one slot; the client-pack route charges one PER SHOT
#: and therefore calls ``enforce_rate_limit`` itself (a dependency cannot see the body).
rate_limit_render_jobs = FirmRateLimit(render_jobs_per_firm_rule)
#: Two mounts, two sentences, ONE bucket — see ``export_jobs_per_firm_rule``.
rate_limit_export_jobs = FirmRateLimit(export_jobs_per_firm_rule)
rate_limit_sheet_jobs = FirmRateLimit(sheet_jobs_per_firm_rule)
rate_limit_auth_ip = IpRateLimit(auth_ip_rule)
rate_limit_verify_ip = IpRateLimit(verify_ip_rule)


# ---------------------------------------------------------------------------
# Annotated aliases — the intended handler style
# ---------------------------------------------------------------------------

AppSettings = Annotated[Settings, Depends(get_app_settings)]
DbSession = Annotated[AsyncSession, Depends(db_session)]
Origin = Annotated[RequestOrigin, Depends(request_origin)]
ClientIp = Annotated[str, Depends(client_ip)]
Claims = Annotated[TokenClaims, Depends(access_token_claims)]
Tenant = Annotated[TenantCtx, Depends(require_tenant)]
OptionalTenant = Annotated[TenantCtx | None, Depends(optional_tenant)]
AdminTenant = Annotated[TenantCtx, Depends(require_admin)]
WriterTenant = Annotated[TenantCtx, Depends(require_writer)]
ShareViewer = Annotated[TenantCtx, Depends(require_share_viewer)]
ShareProjectId = Annotated[uuid.UUID, Depends(share_project_id)]
Auth = Annotated[AuthService, Depends(get_auth_service)]
Sessions = Annotated[SessionStore, Depends(get_session_store)]


__all__ = [
    "TRUSTED_PROXY_HOPS",
    "UNKNOWN_CLIENT_IP",
    "AdminTenant",
    "AppSettings",
    "Auth",
    "Claims",
    "ClientIp",
    "DbSession",
    "FirmRateLimit",
    "IpRateLimit",
    "OptionalTenant",
    "Origin",
    "Sessions",
    "ShareProjectId",
    "ShareViewer",
    "Tenant",
    "WriterTenant",
    "access_token_claims",
    "client_ip",
    "db_session",
    "get_app_settings",
    "get_auth_service",
    "get_session_store",
    "optional_tenant",
    "rate_limit_auth_ip",
    "rate_limit_export_jobs",
    "rate_limit_ops",
    "rate_limit_render_jobs",
    "rate_limit_sheet_jobs",
    "rate_limit_solver_jobs",
    "rate_limit_verify_ip",
    "request_origin",
    "require_admin",
    "require_section",
    "require_share_viewer",
    "require_tenant",
    "require_writer",
    "resolve_share_context",
    "share_project_id",
]
