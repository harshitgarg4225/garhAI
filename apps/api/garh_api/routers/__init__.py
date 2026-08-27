"""Shared plumbing for every router: dependencies, idempotency, limits, signed URLs.

This module holds the things more than one router needs and nothing else. It imports
**downward only** (repositories, tenancy, config, queue) and never imports a sibling
router at module scope — :func:`api_router` imports them lazily inside the function, so
a router may import this package without a cycle.

Auth boundary
-------------

Authentication (``/auth/otp``, ``/auth/verify``, JWT minting) is owned by
:mod:`garh_api.auth`, and :mod:`garh_api.deps` is the single place a request becomes a
:class:`~garh_api.tenancy.TenantCtx`. This package only *re-exports* those dependencies
under the names the routers use, so a router has one import for its plumbing.

Re-exporting rather than re-deriving is deliberate. An earlier revision resolved the
auth dependency dynamically (``getattr(module, "require_tenant")``) and called it as
``dep(request)``. That is broken by construction: ``deps.require_tenant`` takes an
injected ``claims`` parameter, so calling it by hand raises ``TypeError`` instead of
authenticating, and FastAPI never sees the nested dependency. Tenant resolution has
exactly one implementation, in ``deps.py``, and this module points at it.

Error boundary
--------------

:class:`ApiError` here **is** :class:`garh_api.errors.ApiError`, subclassed only to add
the ``status=``/``code=`` keyword form. That matters: ``install_error_handlers``
dispatches on ``garh_api.errors.ApiError``, so an independent base class would fall
through to the catch-all and turn every deliberate 404 into a 500.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from garh_api import queue
from garh_api.config import Settings, get_settings
from garh_api.deps import (
    AdminTenant,
    DbSession,
    OptionalTenant,
    ShareProjectId,
    ShareViewer,
    Tenant,
    WriterTenant,
    require_admin,
    require_section,
    require_share_viewer,
    require_tenant,
    require_writer,
)
from garh_api.errors import ApiError as _ProblemError
from garh_api.errors import AuthenticationError, ServiceUnavailableError
from garh_api.errors import RateLimitedError as _RateLimitedError
from garh_api.logging import get_logger
from garh_api.repositories import (
    DesignVersionRepository,
    OpRepository,
    Project,
    ProjectRepository,
    TenantCtx,
)
from garh_api.tenancy import (
    MAX_PAGE_SIZE,
    EntityNotFoundError,
    PermissionDeniedError,
)

_log = get_logger(__name__)

#: Aliases the routers in this package use. ``deps.py`` owns the implementations.
SessionDep = DbSession
TenantDep = Tenant
AdminDep = AdminTenant
WriterDep = WriterTenant
OptionalTenantDep = OptionalTenant
ShareViewerDep = ShareViewer
ShareProjectIdDep = ShareProjectId


# ---------------------------------------------------------------------------
# Errors raised by routers (main.py renders them as problem+json)
# ---------------------------------------------------------------------------


class ApiError(_ProblemError):
    """A deliberate, user-facing failure raised from a router (golden rule 9).

    This is :class:`garh_api.errors.ApiError` with one ergonomic addition: the status
    and code may be passed per-instance (``raise ApiError("…", status=404,
    code="not_found")``) instead of requiring a subclass for every one-off failure.
    Because it *is* that class, ``install_error_handlers`` renders it as problem+json.

    ``extra`` is exposed as a live mutable mapping — the op sequencer annotates an
    in-flight rejection with ``opIndex``/``headIdx`` as it unwinds.
    """

    http_status = 400
    code = "bad_request"
    default_message = "We couldn't process that request."
    action = "Check the request and try again."

    def __init__(
        self,
        message: str | None = None,
        *,
        status: int | None = None,
        code: str | None = None,
        action: str | None = None,
        extra: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message, action=action, extra=extra, headers=headers)
        if status is not None:
            self.http_status = status  # type: ignore[misc] - instance shadows the class
        if code is not None:
            self.code = code  # type: ignore[misc]

    @property
    def extra(self) -> dict[str, Any]:
        """The problem body's additional fields. Mutable on purpose (see the docstring)."""
        return self._extra


class UnauthenticatedError(AuthenticationError):
    """Re-exported so routers have one import for their error types."""


class RateLimitedError(_RateLimitedError):
    """§11 rate limits. Always carries ``Retry-After`` so a client can back off."""


# ---------------------------------------------------------------------------
# Tenant resolution
# ---------------------------------------------------------------------------

# ``require_tenant`` / ``require_admin`` / ``require_writer`` / ``require_section`` /
# ``require_share_viewer`` are imported from :mod:`garh_api.deps` at the top of this
# module and re-exported below. There is no second implementation here — see the
# "Auth boundary" note in the module docstring for why the previous dynamic lookup was
# not merely redundant but wrong.

#: Kept as an alias so existing call sites read naturally; ``deps.require_admin`` is the
#: implementation.
require_admin_tenant = require_admin


# ---------------------------------------------------------------------------
# Pagination (§11 cursor pagination)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PageParams:
    limit: int
    cursor: str | None


def page_params(
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, max_length=512),
) -> PageParams:
    return PageParams(limit=limit, cursor=cursor)


PageDep = Annotated[PageParams, Depends(page_params)]


# ---------------------------------------------------------------------------
# Project helpers shared by several routers
# ---------------------------------------------------------------------------

#: Namespace for deriving a project's default op branch.
#:
#: DECISION: playbook §2 gives ``ops`` and ``design_versions`` a ``version_branch`` but
#: gives ``projects`` no "current branch" column. Rather than change a schema owned
#: elsewhere, the active branch is derived: it is the branch of the newest
#: ``design_versions`` row, and for a project that has none it is
#: ``uuid5(MAIN_BRANCH_NAMESPACE, project_id)`` — stable, collision-free, and
#: reproducible from the project id alone. Restoring a version creates a version row on
#: a fresh branch, which is exactly what makes that branch active.
MAIN_BRANCH_NAMESPACE = uuid.UUID("6f1f1f7e-2b4a-5c8d-9e3f-0a1b2c3d4e5f")


def main_branch_id(project_id: uuid.UUID) -> uuid.UUID:
    """The deterministic first branch of a project."""
    return uuid.uuid5(MAIN_BRANCH_NAMESPACE, str(project_id))


async def active_branch(session: AsyncSession, ctx: TenantCtx, project_id: uuid.UUID) -> uuid.UUID:
    """Which branch writes and reads go to right now. See :data:`MAIN_BRANCH_NAMESPACE`."""
    latest = await DesignVersionRepository(session, ctx).latest(project_id)
    if latest is not None:
        return latest.version_branch
    return main_branch_id(project_id)


async def require_project(session: AsyncSession, ctx: TenantCtx, project_id: uuid.UUID) -> Project:
    """Load a project or 404. A project from another firm is indistinguishable from a
    missing one — that is the cross-tenant guarantee, not an accident (§13)."""
    return await ProjectRepository(session, ctx).require(project_id)


async def branch_head(
    session: AsyncSession, ctx: TenantCtx, project_id: uuid.UUID, branch: uuid.UUID
) -> int:
    return await OpRepository(session, ctx).head_idx(project_id, branch)


# ---------------------------------------------------------------------------
# Rate limiting (§11: 60 ops/s, 10 solver jobs/hr on the free tier)
# ---------------------------------------------------------------------------


async def enforce_rate_limit(
    bucket: str,
    subject: Any,
    *,
    limit: int,
    window_seconds: int,
    what: str,
) -> None:
    """Charge one slot against a limit, raising 429 when it is spent.

    A thin delegate to :func:`garh_api.ratelimit.enforce_rate_limit` — the sliding-window
    implementation, its fail-open/fail-closed policy and its ``Retry-After`` accounting
    all live there. This wrapper exists so an imperative call site inside a handler reads
    the same as the declarative ``Depends(rate_limit_ops)`` form, and so there is exactly
    one limiter to audit against the §13 checklist rather than two that drift.
    """
    from garh_api.ratelimit import RateLimitRule
    from garh_api.ratelimit import enforce_rate_limit as _enforce

    rule = RateLimitRule(
        name="%s.per_firm" % bucket,
        limit=limit,
        window_seconds=window_seconds,
        scope="firm",
        message="%s is limited to %d per %s on your plan."
        % (what, limit, _humanise(window_seconds)),
    )
    await _enforce(rule, "firm:%s" % subject)


def _humanise(seconds: int) -> str:
    if seconds >= 3600:
        return "hour" if seconds == 3600 else "%d hours" % (seconds // 3600)
    if seconds >= 60:
        return "minute" if seconds == 60 else "%d minutes" % (seconds // 60)
    return "second" if seconds == 1 else "%d seconds" % seconds


# ---------------------------------------------------------------------------
# Idempotency-Key (§11)
# ---------------------------------------------------------------------------

IdempotencyKeyDep = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        max_length=128,
        description="Replay-safe key. The same key returns the first response instead "
        "of starting a second job.",
    ),
]


class IdempotencyConflictError(ApiError):
    http_status = 409
    code = "idempotency_in_progress"
    action = "The first request with this key is still running. Poll it instead."


@dataclass
class IdempotencyGuard:
    """Claim-then-store around a side-effecting handler.

    ``begin()`` returns the stored response for a replayed key, or ``None`` after
    claiming the key for this request. ``store()`` records the result. An in-flight
    claim that is replayed gets a 409 rather than a duplicate job: two solver runs
    because a proxy retried is a real cost, not a cosmetic one.

    A key is only *ever* honoured within one firm (the Redis key is firm-scoped), so a
    guessed key from another tenant reveals nothing.
    """

    scope: str
    key: str | None
    firm_id: Any

    @property
    def active(self) -> bool:
        return bool(self.key)

    def _redis_key(self) -> str:
        return queue.idempotency_key(self.firm_id, self.scope, str(self.key))

    async def begin(self) -> dict[str, Any] | None:
        if not self.active:
            return None
        try:
            client = queue.get_redis()
            claimed = await client.set(
                self._redis_key(),
                json.dumps({"state": "in_progress"}),
                nx=True,
                ex=queue.IDEMPOTENCY_TTL_SECONDS,
            )
            if claimed:
                return None
            raw = await client.get(self._redis_key())
        except Exception:
            return None
        if not raw:
            return None
        try:
            record = json.loads(raw)
        except ValueError:
            return None
        if record.get("state") == "in_progress":
            raise IdempotencyConflictError(
                "A request with this Idempotency-Key is already running."
            )
        response = record.get("response")
        return response if isinstance(response, dict) else None

    async def store(self, response: dict[str, Any]) -> None:
        if not self.active:
            return
        try:
            await queue.get_redis().set(
                self._redis_key(),
                json.dumps({"state": "done", "response": response}, default=str),
                ex=queue.IDEMPOTENCY_TTL_SECONDS,
            )
        except Exception:
            _log.warning("idempotency.store_failed", scope=self.scope)

    async def release(self) -> None:
        """Drop the claim so a failed request can be retried with the same key."""
        if not self.active:
            return
        with contextlib.suppress(Exception):
            await queue.get_redis().delete(self._redis_key())


# ---------------------------------------------------------------------------
# Short-lived signed download URLs (§11, §13 "signed URLs ≤10min")
# ---------------------------------------------------------------------------

_DOWNLOAD_SIGNING_SALT = b"garh:download:v1"
_process_fallback_secret: bytes | None = None


def _download_secret(settings: Settings) -> bytes:
    """Key for download-URL HMACs.

    Derived from the JWT private key so every API replica agrees without a second
    secret to manage. If no key is configured (dev only), a per-process random secret is
    used — links then break across a restart or a second replica, which is annoying in
    dev and impossible in prod, where config.py refuses to boot without the key.
    """
    global _process_fallback_secret
    if settings.jwt_private_key:
        return hashlib.sha256(_DOWNLOAD_SIGNING_SALT + settings.jwt_private_key.encode()).digest()
    if _process_fallback_secret is None:
        _process_fallback_secret = secrets.token_bytes(32)
        _log.warning(
            "downloads.ephemeral_signing_key",
            reason="no JWT_PRIVATE_KEY configured; signed download links will not "
            "survive a restart or work across replicas",
        )
    return _process_fallback_secret


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64u_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign_download_token(
    payload: dict[str, Any], *, ttl_seconds: int | None = None
) -> tuple[str, datetime]:
    """Mint an opaque, expiring, tamper-evident download token.

    The payload must identify the artifact **and** its firm; redemption re-reads the row
    through a firm-scoped repository, so a forged firm id would have to forge the HMAC
    too. Returns ``(token, expires_at)``.
    """
    settings = get_settings()
    ttl = min(int(ttl_seconds or settings.s3_signed_url_ttl_seconds), 600)
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl)
    body = dict(payload)
    body["exp"] = int(expires_at.timestamp())
    encoded = _b64u(json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(
        _download_secret(settings), encoded.encode("ascii"), hashlib.sha256
    ).digest()
    return "%s.%s" % (encoded, _b64u(signature)), expires_at


def verify_download_token(token: str) -> dict[str, Any]:
    """Validate a download token. Raises :class:`ApiError` (404/410) when it fails.

    404 for a bad signature (never confirm that a well-formed-but-unsigned token names
    a real artifact) and 410 for an honestly expired one, so the UI can offer "get a
    fresh link" instead of "not found".
    """
    settings = get_settings()
    encoded, _, signature = token.partition(".")
    if not encoded or not signature:
        raise ApiError(
            "That download link is not valid.",
            status=404,
            code="not_found",
            action="Open the project and download again.",
        )
    expected = hmac.new(
        _download_secret(settings), encoded.encode("ascii"), hashlib.sha256
    ).digest()
    try:
        provided = _b64u_decode(signature)
    except (ValueError, TypeError) as exc:
        raise ApiError(
            "That download link is not valid.",
            status=404,
            code="not_found",
            action="Open the project and download again.",
        ) from exc
    if not hmac.compare_digest(expected, provided):
        raise ApiError(
            "That download link is not valid.",
            status=404,
            code="not_found",
            action="Open the project and download again.",
        )
    try:
        payload = json.loads(_b64u_decode(encoded).decode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise ApiError(
            "That download link is not valid.",
            status=404,
            code="not_found",
            action="Open the project and download again.",
        ) from exc
    if int(payload.get("exp") or 0) < int(time.time()):
        raise ApiError(
            "That download link has expired.",
            status=410,
            code="download_expired",
            action="Open the project and download again.",
        )
    return payload


def build_download_url(request: Request, token: str) -> str:
    """Absolute URL for a signed download token."""
    settings = get_settings()
    return str(request.base_url).rstrip("/") + "%s/downloads/%s" % (settings.api_prefix, token)


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def client_ip(request: Request) -> str:
    """Best-effort client IP for per-IP limits. Trusts ``X-Forwarded-For``'s first hop,
    which is correct behind our own proxy and harmless in dev."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"


def repo_root() -> str:
    """Filesystem root that holds ``rulepacks/`` and ``fixtures/``.

    Honours ``GARH_ROOT`` and falls back to walking up from this file, which covers both
    the container layout (``/app``) and a bare checkout.
    """
    override = os.environ.get("GARH_ROOT")
    if override:
        return override
    here = os.path.abspath(os.path.dirname(__file__))
    # garh_api/routers -> garh_api -> apps/api -> apps -> <repo root>
    candidate = os.path.abspath(os.path.join(here, "..", "..", "..", ".."))
    return candidate


def ensure_scope(ctx: TenantCtx, section: str) -> None:
    """Share-link section gate that reads naturally at the call site."""
    ctx.require_scope(section)


def not_found(entity: str, entity_id: Any) -> EntityNotFoundError:
    return EntityNotFoundError(entity, entity_id)


def forbidden(message: str) -> PermissionDeniedError:
    return PermissionDeniedError(message)


# ---------------------------------------------------------------------------
# Router assembly
# ---------------------------------------------------------------------------


def api_router() -> APIRouter:
    """The ``/api/v1`` router tree.

    Submodules are imported here, not at module scope: they import *this* module for
    their dependencies, and a top-level import would be a cycle.
    """
    from garh_api.routers import catalog, copilot, jobs, ops, projects, renders, share

    router = APIRouter()
    router.include_router(projects.router)
    router.include_router(ops.router)
    #: Phase 6: propose-only — apply goes back through ops.router (§13 containment).
    router.include_router(copilot.router)
    router.include_router(jobs.router)
    #: Phase 7 render-specific routes (history links, client pack). Distinct paths —
    #: no overlap with jobs.router's /renders POST/GET.
    router.include_router(renders.router)
    router.include_router(catalog.router)
    router.include_router(share.router)
    #: The public viewer surface — separate router, read-only, no write deps (§13).
    router.include_router(share.public_router)
    return router


__all__ = [
    "AdminDep",
    "ApiError",
    "IdempotencyConflictError",
    "IdempotencyGuard",
    "IdempotencyKeyDep",
    "MAIN_BRANCH_NAMESPACE",
    "OptionalTenantDep",
    "PageDep",
    "PageParams",
    "RateLimitedError",
    "ServiceUnavailableError",
    "SessionDep",
    "ShareProjectIdDep",
    "ShareViewerDep",
    "TenantDep",
    "UnauthenticatedError",
    "WriterDep",
    "active_branch",
    "api_router",
    "branch_head",
    "build_download_url",
    "client_ip",
    "enforce_rate_limit",
    "ensure_scope",
    "forbidden",
    "main_branch_id",
    "not_found",
    "page_params",
    "repo_root",
    "require_admin",
    "require_admin_tenant",
    "require_project",
    "require_section",
    "require_share_viewer",
    "require_tenant",
    "require_writer",
    "sign_download_token",
    "verify_download_token",
]
