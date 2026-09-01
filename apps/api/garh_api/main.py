"""The FastAPI application (playbook §11, §13, §18).

``garh_api.main:app`` is what uvicorn serves and what ``docker-compose.yml`` health-checks
(scaffold contract note 4). Everything the process needs to be safe and observable is
wired here, once:

* **structlog** with a request id bound to every line, so a support ticket quoting
  ``requestId`` finds every log the request produced (§18);
* **problem+json for every error**, including the ones nobody anticipated — a raw
  traceback must never reach a browser tab (golden rule 9, §13);
* **security headers** on every response: HSTS in production, and a CSP with no
  ``unsafe-inline`` anywhere (§13 "CSP (no inline scripts)");
* **a CORS allowlist**, never ``*`` — credentials cross this boundary;
* **feature flags read at boot** (§18) into the process-wide registry;
* **the job-event consumer**, which is the only path from "a worker finished" to "the
  job row says so" (see ``routers/jobs.py``).

No I/O at import. The module builds an app object and reads configuration; every
connection — database, Redis — is opened in the lifespan hook, so ``import
garh_api.main`` is safe in a test, in Alembic, or in a shell with nothing running.
Configuration is the one deliberate exception: ``get_settings()`` raises at import in
any non-dev environment missing a secret, which is exactly when that should fail.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders

from garh_api import MODEL_SCHEMA_VERSION, __version__, db
from garh_api.auth import set_otp_mailer
from garh_api.config import Settings, get_settings
from garh_api.db import dispose_async_engine, session_scope
from garh_api.errors import (
    CODE_PAYLOAD_TOO_LARGE,
    PROBLEM_CONTENT_TYPE,
    install_error_handlers,
    problem_body,
)
from garh_api.logging import (
    bind_request_context,
    clear_request_context,
    configure_logging,
    get_logger,
)
from garh_api.mailer import build_mailer
from garh_api.observability import init_sentry
from garh_api.queue import QueueUnavailableError, close_redis
from garh_api.repositories import FLAG_REGISTRY, FlagRepository
from garh_api.routers import api_router
from garh_api.routers import auth as auth_router
from garh_api.routers import health as health_router
from garh_api.schemas import MetaOut

_log = get_logger(__name__)

#: Header carrying the correlation id, inbound and outbound.
REQUEST_ID_HEADER = "x-request-id"

#: Two years, the minimum for HSTS preload eligibility (§13 "HTTPS only, HSTS").
HSTS_MAX_AGE_SECONDS = 63_072_000

#: §13: "CSP (no inline scripts)". No ``unsafe-inline`` and no ``unsafe-eval`` anywhere.
#:
#: This policy is for **API responses**, which are JSON and problem+json. It is
#: deliberately close to a deny-all: nothing served from this origin should ever load a
#: script, a stylesheet or an image. The web app ships its own (looser, but still
#: inline-free) policy from its own server — a single CSP cannot serve both, and a
#: policy loose enough for the SPA would be pointless here.
API_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "sandbox"
)

#: Route suffixes allowed to exceed ``settings.max_request_body_bytes``.
#:
#: Three entries: Phase 2's DXF upload (``POST /projects/{id}/import/dxf``), the
#: underlay image upload (``POST /projects/{id}/underlay/image``) and the
#: inspiration-board upload (``POST /projects/{id}/references``). Listing a
#: path here is only half the job — the route must enforce its own byte cap, because
#: this middleware stops caring about a path once it is listed. All three routers hold
#: up their half with the same ``_read_body_capped`` streaming guard: DXF against
#: ``settings.max_dxf_upload_bytes``, underlay and board against
#: ``settings.max_image_upload_bytes``, each answering 413 as problem+json (§13).
#:
#: Matching is by suffix and ignores the method, so ``GET .../references`` is exempted
#: too. Harmless — a GET carries no body — but it is why the POST's own cap is the
#: real guard and not a belt-and-braces nicety.
LARGE_BODY_PATH_SUFFIXES: tuple[str, ...] = (
    "/import/dxf",
    "/underlay/image",
    "/references",
)

_SECURITY_HEADERS: dict[str, str] = {
    "content-security-policy": API_CONTENT_SECURITY_POLICY,
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-site",
    # The API needs none of these, and saying so is free.
    "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=()",
}

OPENAPI_DESCRIPTION = """
Garh AI — India-first, AI-native house design for architects.

**Conventions**

* Every request and response body is camelCase; every length is an **integer
  millimetre**. A float length is rejected at the boundary, because dimension chains and
  compliance arithmetic depend on exact integers.
* Errors are `application/problem+json`: `{code, message, action, requestId}`. `action`
  is always present — it is the next step the UI offers as a button.
* Lists are cursor-paginated: `{items, nextCursor, hasMore}`. Cursors are opaque.
* Mutations that start work accept an `Idempotency-Key` header; replaying a key returns
  the first response instead of starting a second job.
* Every design change is an **op** appended through `POST /projects/{id}/ops`. A stale
  `baseIdx` gets `409` with `headIdx`, and the client rebases. There is no other way to
  change a design — not the plot form, not the copilot, not the solver.
* Downloads are short-lived signed URLs (≤10 minutes), never direct object paths.
"""

OPENAPI_TAGS: list[dict[str, Any]] = [
    {"name": "health", "description": "Liveness and readiness probes."},
    {"name": "auth", "description": "Email OTP sign-in, JWT access and refresh tokens."},
    {"name": "projects", "description": "Projects, plots, briefs, versions, compliance."},
    {"name": "ops", "description": "The op sequencer: append, sync, and folded model state."},
    {"name": "jobs", "description": "Solver, render, sheet and export jobs, with SSE streams."},
    {
        "name": "copilot",
        "description": (
            "Natural language in, a previewable op diff out (§10). These routes NEVER write "
            "ops: `POST /projects/{id}/copilot` returns typed ops that already passed the "
            "op-catalog schema, a dry-run fold of the real model core, and a no-new-hard-"
            "failure rules diff. Applying is the client's separate act — the same "
            "`POST /projects/{id}/ops` a hand edit takes, with the proposal's `groupId` so "
            "the whole diff is one undo step. `POST /projects/{id}/copilot/decision` records "
            "what the human chose, and writes nothing but a log line."
        ),
    },
    {
        "name": "renders",
        "description": (
            "The §9 render surface beyond a single job: version-pinned history with "
            "re-signed image links, presigned capture-upload slots, the 8-shot client pack "
            "as one job group, and its zip archive. Renders are pinned to a design version; "
            "a model edit marks them stale rather than deleting them."
        ),
    },
    {"name": "share", "description": "Scoped share links and their comments."},
    {
        "name": "share-viewer",
        "description": "The anonymous read-only client surface. No write dependencies.",
    },
    {"name": "catalog", "description": "Rule packs, furniture, materials, facade kits."},
    {"name": "meta", "description": "What the web app needs before it renders anything."},
]


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


def _install_otp_mailer(settings: Settings) -> None:
    """Wire the SMTP mailer (when configured) into the auth layer's delivery seam.

    ``build_mailer`` returns ``None`` unless both ``SMTP_HOST`` and ``SMTP_FROM``
    are set, and installing ``None`` *clears* the hook — so a process that builds
    more than one app (tests do) cannot inherit a stale mailer from a previous
    configuration. Opens no connection: the mailer dials the relay per send.
    """
    mailer = build_mailer(settings)
    set_otp_mailer(mailer)
    if mailer is not None:
        # Host and sender domain only — never the credentials, never an address.
        _log.info(
            "auth.mailer_installed",
            transport=mailer.transport,
            smtp_host=mailer.host,
            from_domain=mailer.from_domain,
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Boot and shutdown. Every external connection is opened and closed here.

    Flag loading and the job-event consumer are both **non-fatal**: an API that refuses
    to start because Redis is slow is worse than one that starts with default flags and
    logs loudly. Config, by contrast, is fatal — ``get_settings()`` raises at import in
    any non-dev environment that is missing a secret, and that is the right time to fail.
    """
    settings = get_settings()
    configure_logging(settings)
    # Error tracking, OFF unless SENTRY_DSN is set — the zero-keys default stack
    # ships zero third-party telemetry (locked decision). Lazy inside: the SDK is
    # only imported when a DSN exists, so this line costs one string check.
    init_sentry(settings)
    _log.info(
        "api.starting",
        version=__version__,
        env=settings.env,
        provider_llm=settings.provider_llm,
        provider_render=settings.provider_render,
        model_schema_version=MODEL_SCHEMA_VERSION,
    )

    # Real OTP mail, when SMTP_* is configured; otherwise this clears the hook and
    # dev keeps its echo (garh_api.auth._deliver_code owns that ordering).
    _install_otp_mailer(settings)

    # §18: "Feature flags table read at boot".
    try:
        async with session_scope(settings) as session:
            flags = await FLAG_REGISTRY.refresh(FlagRepository(session))
        app.state.flags = flags
    except Exception as exc:
        app.state.flags = FLAG_REGISTRY.snapshot()
        _log.error(
            "flags.load_failed",
            error="%s: %s" % (type(exc).__name__, exc),
            fallback="compiled-in defaults (all off)",
        )

    # The worker → job-row bridge. Started last so a failure here cannot stop the API
    # serving reads.
    try:
        from garh_api.routers.jobs import start_job_event_consumer

        start_job_event_consumer(app)
    except Exception as exc:
        _log.error("job_events.start_failed", error="%s: %s" % (type(exc).__name__, exc))

    try:
        yield
    finally:
        _log.info("api.stopping")
        try:
            from garh_api.routers.jobs import stop_job_event_consumer

            await stop_job_event_consumer(app)
        except Exception as exc:
            _log.warning("job_events.stop_failed", error="%s: %s" % (type(exc).__name__, exc))
        await close_redis()
        await dispose_async_engine()
        _log.info("api.stopped")


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


#: Paths whose successful responses are not access-logged. Probes fire every few
#: seconds; logging them buries everything else.
_QUIET_PATHS = frozenset({"/healthz", "/readyz"})


class RequestContextMiddleware:
    """Bind a request id to the log context and echo it on the response.

    Pure ASGI, **not** ``BaseHTTPMiddleware``. That base class consumes the response and
    re-emits it through a task group, which buffers streaming bodies and delays
    ``http.disconnect`` propagation. This API's SSE endpoints are the product's honest
    progress feed (§15); a middleware that holds their chunks would turn a live stream
    into one burst at the end. Wrapping ``send`` costs a few lines and streams untouched.

    The ``finally`` matters too: contextvars live on the task, and tasks are reused
    across requests. Without the clear, one request's firm id leaks into the next
    request's log lines — a debugging trap and a privacy one.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        headers = Headers(scope=scope)
        path = scope.get("path", "")
        request_id = bind_request_context(
            request_id=headers.get(REQUEST_ID_HEADER),
            method=scope.get("method"),
            path=path,
        )
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start":
                duration_ms = int((time.perf_counter() - started) * 1000)
                out = MutableHeaders(scope=message)
                out[REQUEST_ID_HEADER] = request_id
                out["server-timing"] = "app;dur=%d" % duration_ms
                if path not in _QUIET_PATHS:
                    _log.info(
                        "http.request",
                        status=message["status"],
                        duration_ms=duration_ms,
                    )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # The exception handlers own the response body; this records the failure's
            # timing and re-raises so they can.
            _log.error(
                "http.request_failed",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            raise
        finally:
            clear_request_context()


class SecurityHeadersMiddleware:
    """§13 web hardening, on every response including errors and streams."""

    def __init__(self, app: Any, *, production: bool) -> None:
        self.app = app
        self.production = production

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start":
                out = MutableHeaders(scope=message)
                for header, value in _SECURITY_HEADERS.items():
                    if header not in out:
                        out[header] = value
                if self.production and "strict-transport-security" not in out:
                    # Production only: sending HSTS from a dev server pins `localhost`
                    # to HTTPS in the developer's browser, which is genuinely annoying
                    # to undo and teaches nobody anything.
                    out["strict-transport-security"] = (
                        "max-age=%d; includeSubDomains" % HSTS_MAX_AGE_SECONDS
                    )
            await send(message)

        await self.app(scope, receive, send_wrapper)


class CommitBeforeResponseMiddleware:
    """Make the request's DB work durable BEFORE the first response byte leaves.

    FastAPI ≥0.106 runs yield-dependency teardown AFTER the response is sent, so
    the commit in ``db.session_scope`` happens when the client may already be
    acting on the response — a 201 for a created project followed by a 404
    reading it one round-trip later. Every environment before CI run 13's e2e
    smoke was too slow (or too in-process: the test client awaits the full app
    cycle) to lose that race; the compose stack on a runner lost it reliably.

    ``db.get_db_session`` registers each request session on the ASGI scope; this
    middleware intercepts ``http.response.start`` and commits them first. The
    teardown commit remains as a no-op fallback, and the rollback-on-exception
    path is unchanged — an exception unwinds through the dependency before any
    response starts, so the sessions this hook sees on an error response are
    already closed and are skipped.

    Pure ASGI like its neighbours (``BaseHTTPMiddleware`` would buffer SSE). A
    commit failure here converts the response into a 500 problem — the client
    must not receive a success status for work that did not land.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        failed = False

        async def commit_then_send(message: Any) -> None:
            nonlocal failed
            if message["type"] == "http.response.start":
                for session in scope.get(db.SCOPE_SESSIONS_KEY, ()):
                    if not session.is_active or not session.in_transaction():
                        continue
                    try:
                        await session.commit()
                    except Exception as exc:
                        failed = True
                        _log.error(
                            "request.commit_before_response_failed",
                            path=scope.get("path", ""),
                            error="%s: %s" % (type(exc).__name__, exc),
                        )
                        with contextlib.suppress(Exception):
                            await session.rollback()
                if failed:
                    body = json.dumps(
                        problem_body(
                            "internal",
                            "Saving your change failed at the last moment.",
                            "Nothing was stored. Retry the request.",
                        )
                    ).encode("utf-8")
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 500,
                            "headers": [
                                (b"content-type", PROBLEM_CONTENT_TYPE.encode("latin-1")),
                                (b"content-length", str(len(body)).encode("latin-1")),
                            ],
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
                    return
            if failed:
                # The app's original response messages are superseded by the 500.
                return
            await send(message)

        await self.app(scope, receive, commit_then_send)


class BodySizeLimitMiddleware:
    """Refuse a request body larger than ``max_request_body_bytes`` (§13).

    Two checks, because either alone is bypassable:

    * ``Content-Length``, when present, is rejected **before** a single byte is read.
      That is the cheap path and the one a well-behaved client hits.
    * the streamed byte count, because ``Transfer-Encoding: chunked`` carries no
      length and a caller can simply omit the header. Without this half, the limit
      is advisory.

    Why this is not left to uvicorn or the proxy: uvicorn has no body-size limit at
    all, and "there will be an nginx in front" is an assumption, not a control — the
    API is reachable directly in every dev and CI topology this repo ships. Starlette
    buffers the whole body before Pydantic sees it, so an unauthenticated POST of an
    arbitrarily large body is memory pressure that no validator can prevent.

    Pure ASGI for the same reason as the other two middlewares: ``BaseHTTPMiddleware``
    would buffer the SSE streams.

    The 413 body is problem+json, hand-built rather than raised: an exception here
    would escape the app's handlers (this middleware sits outside them).
    """

    def __init__(self, app: Any, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    def _too_large_response(self) -> tuple[bytes, dict[str, str]]:
        # problem_body stamps requestId from the log context itself, which
        # RequestContextMiddleware has already bound by the time we run.
        body = problem_body(
            CODE_PAYLOAD_TOO_LARGE,
            "That request body is too large (limit %d bytes)." % self.max_bytes,
            "Split the change into smaller requests. If this was a file, use the "
            "upload endpoint for that format instead of posting it inline.",
        )
        payload = json.dumps(body).encode("utf-8")
        return payload, {
            "content-type": PROBLEM_CONTENT_TYPE,
            "content-length": str(len(payload)),
            # Nothing useful can follow an aborted body read on this connection.
            "connection": "close",
        }

    async def _reject(self, scope: Any, send: Any) -> None:
        payload, headers = self._too_large_response()
        _log.warning(
            "request.body_too_large",
            path=scope.get("path", ""),
            method=scope.get("method", ""),
            limit_bytes=self.max_bytes,
        )
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
            }
        )
        await send({"type": "http.response.body", "body": payload, "more_body": False})

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "") or ""
        if any(path.endswith(suffix) for suffix in LARGE_BODY_PATH_SUFFIXES):
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    await self._reject(scope, send)
                    return
            except ValueError:
                # A malformed Content-Length is not ours to interpret; the counting
                # receive below still bounds the damage.
                pass

        read = 0
        rejected = False

        async def counting_receive() -> Any:
            nonlocal read, rejected
            message = await receive()
            if message.get("type") == "http.request":
                read += len(message.get("body", b"") or b"")
                if read > self.max_bytes:
                    rejected = True
                    # Hand the app an empty final chunk. It will fail to parse the
                    # truncated body, but we have already sent the 413 — see below.
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Any) -> None:
            if rejected:
                return
            await send(message)

        await self.app(scope, counting_receive, guarded_send)
        if rejected:
            await self._reject(scope, send)


def _install_cors(app: FastAPI, settings: Settings) -> None:
    """Explicit allowlist. Never ``*`` — this API sees bearer tokens and refresh cookies.

    ``expose_headers`` is not decoration: the browser cannot read ``X-Request-Id`` or the
    rate-limit headers from a cross-origin response unless they are listed, and the
    client needs the first for support and the second to back off politely.
    """
    origins = [o for o in settings.cors_allow_origins if o]
    if not origins:
        _log.warning("cors.no_origins_configured", hint="set CORS_ALLOW_ORIGINS")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "authorization",
            "content-type",
            "idempotency-key",
            "last-event-id",
            REQUEST_ID_HEADER,
        ],
        expose_headers=[
            REQUEST_ID_HEADER,
            "retry-after",
            "server-timing",
            "x-ratelimit-limit",
            "x-ratelimit-remaining",
            "x-ratelimit-window",
        ],
        max_age=600,
    )


def _install_queue_error_handler(app: FastAPI) -> None:
    """Render a queue outage as problem+json rather than as a 500.

    ``QueueUnavailableError`` lives in ``garh_api.queue``, which deliberately imports no
    HTTP layer so the workers' own tooling can use it. That means it cannot subclass
    ``errors.ApiError``, so it needs its own handler — but the body it produces is
    identical in shape to every other error.
    """

    async def handle_queue_unavailable(request: Request, exc: Exception) -> Response:
        _log.error("queue.unavailable", http_path=request.url.path, message=str(exc))
        body = problem_body(
            QueueUnavailableError.code,
            str(exc) or "The job queue is unreachable.",
            QueueUnavailableError.action,
        )
        return JSONResponse(
            status_code=QueueUnavailableError.http_status,
            content=body,
            media_type=PROBLEM_CONTENT_TYPE,
            headers={"Retry-After": "5"},
        )

    app.add_exception_handler(QueueUnavailableError, handle_queue_unavailable)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. One call per process; tests may call it repeatedly."""
    cfg = settings or get_settings()

    app = FastAPI(
        title="Garh AI API",
        version=__version__,
        description=OPENAPI_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
        # Interactive docs in dev only. In production they are surface area that
        # documents the attack for you.
        docs_url="/docs" if not cfg.is_production else None,
        redoc_url="/redoc" if not cfg.is_production else None,
        openapi_url="/openapi.json" if not cfg.is_production else None,
        swagger_ui_parameters={"persistAuthorization": True},
    )

    # Middleware runs bottom-up in Starlette: the last one added is the outermost. CORS
    # must be outermost so a preflight is answered before anything else runs, and the
    # request-id binding must wrap the security headers so a rejected request still logs
    # with its id.
    #
    # BodySizeLimitMiddleware is added FIRST so it ends up innermost-but-one: it must
    # run after RequestContextMiddleware (its 413 needs the bound request id) and
    # after CORS (a rejected body still needs Access-Control-Allow-Origin, or the
    # browser reports an opaque network error instead of the 413).
    # Outside the routers, inside everything else: the sessions it commits are
    # created by route dependencies, and its 500-on-commit-failure body needs the
    # bound request id.
    app.add_middleware(CommitBeforeResponseMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=cfg.max_request_body_bytes)
    app.add_middleware(SecurityHeadersMiddleware, production=cfg.is_production)
    app.add_middleware(RequestContextMiddleware)
    _install_cors(app, cfg)

    install_error_handlers(app)
    _install_queue_error_handler(app)

    # Probes at the root: compose, CI and any orchestrator look for /healthz, not
    # /api/v1/healthz (routers/health.py says so in its own docstring).
    app.include_router(health_router.router)

    app.include_router(auth_router.router, prefix=cfg.api_prefix)
    app.include_router(api_router(), prefix=cfg.api_prefix)

    # Phase 2: DXF boundary import. Mounted here rather than inside api_router()
    # so the routers package needs no edit; same prefix, same error contract.
    # Its upload route is the one entry in LARGE_BODY_PATH_SUFFIXES above, and it
    # enforces settings.max_dxf_upload_bytes itself (§13 upload rules).
    from garh_api.routers import imports as imports_router

    app.include_router(imports_router.router, prefix=cfg.api_prefix)

    # Phase 8: the sheet surface beyond the job endpoints — the firm's title-block
    # template, one sheet's SVG for the zoomable viewer, the set summary (chain count
    # and the §7 sum invariant), and the D13 review tray. Mounted here for the same
    # reason as the imports router: same prefix, same error contract, no edit to
    # routers/__init__.py.
    #
    # Route-order note: this router's `/projects/{id}/sheets/review-tray` and
    # `/projects/{id}/sheets/summary` must be registered BEFORE jobs.py's
    # `/projects/{id}/sheets/{sheet_id}.{fmt}`, or Starlette would try to parse
    # "review-tray" as a sheet UUID. `api_router()` above already registered jobs.py,
    # so the literal paths would lose — except that they carry no `.fmt` suffix and
    # cannot match that route's pattern. `/sheets/{sheet_id}/content` is likewise
    # unambiguous. Adding a new literal segment under /sheets means re-checking this.
    from garh_api.routers import sheets as sheets_router

    app.include_router(sheets_router.router, prefix=cfg.api_prefix)

    # The tracing underlay (plan-image trace-over). Mounted here for the same
    # reason as the imports router: same prefix, same error contract, no edit to
    # routers/__init__.py. Its upload route is the second entry in
    # LARGE_BODY_PATH_SUFFIXES above and enforces settings.max_image_upload_bytes
    # itself. No path overlap: /projects/{id}/underlay[/image] is its own segment.
    from garh_api.routers import underlay as underlay_router

    app.include_router(underlay_router.router, prefix=cfg.api_prefix)

    # The per-project inspiration board (§11). Same prefix, same error contract,
    # same mounting reason as the underlay above.
    #
    # Route-order note: it owns /projects/{id}/references and two children,
    # /references/review (a literal) and /references/{reference_id} (a UUID
    # parameter). FastAPI matches in registration order and `review` is declared
    # AFTER the parameterised routes -- which is safe only because the
    # parameterised ones are PATCH and DELETE while `review` is GET, so no GET
    # route can shadow it. Adding a `GET /references/{reference_id}` means moving
    # `review` above it, or `review` starts 404ing on a bad UUID parse.
    from garh_api.routers import references as references_router

    app.include_router(references_router.router, prefix=cfg.api_prefix)

    # Account security (F-3 signed-in devices, F-4 two-factor) and governance
    # (F-5 the readable audit trail, F-6 DPDP export/erasure). Mounted here for the
    # same reason as the imports router: same prefix, same error contract, no edit to
    # routers/__init__.py.
    #
    # Route-order note: `sessions_router` shares the `/auth` prefix with
    # `auth_router` above, which is registered first. No path collides — that router
    # owns /auth/{signup,otp,verify,refresh,logout,logout-all,me} and this one owns
    # /auth/{sessions,2fa}/** — and neither has a path parameter in the first segment
    # after /auth, so nothing can shadow anything.
    from garh_api.routers import privacy as privacy_router
    from garh_api.routers import sessions as sessions_router

    app.include_router(sessions_router.router, prefix=cfg.api_prefix)
    app.include_router(privacy_router.audit_router, prefix=cfg.api_prefix)
    app.include_router(privacy_router.router, prefix=cfg.api_prefix)

    _install_meta_route(app, cfg)

    _log.info(
        "api.routes_registered",
        count=len(app.routes),
        prefix=cfg.api_prefix,
        docs_enabled=not cfg.is_production,
    )
    return app


def _install_meta_route(app: FastAPI, settings: Settings) -> None:
    """``GET /api/v1/meta`` — the one call the web app makes before rendering.

    Everything here is either public configuration or a limit the client must respect;
    no secret, no tenant data. It is what lets the UI show "renders: mock" honestly
    instead of pretending every deployment has a GPU.
    """

    @app.get(
        settings.api_prefix + "/meta",
        response_model=MetaOut,
        tags=["meta"],
        summary="Server capabilities, flags and limits",
    )
    async def meta() -> MetaOut:
        from garh_api.routers.ops import model_engine_available

        return MetaOut(
            service=settings.app_name,
            version=__version__,
            env=settings.env,
            api_prefix=settings.api_prefix,
            model_schema_version=MODEL_SCHEMA_VERSION,
            flags=FLAG_REGISTRY.snapshot(),
            providers={
                "llm": settings.provider_llm,
                "render": settings.provider_render,
                "billing": settings.provider_billing,
                # Honest, and load-bearing: without the model core the server cannot
                # validate an op, and the editor should say so rather than let a user
                # draw for ten minutes into a 503.
                "modelEngine": "ready" if model_engine_available() else "unavailable",
            },
            limits={
                "maxOpsPerAppend": settings.max_ops_per_append,
                "opSnapshotInterval": settings.op_snapshot_interval,
                "opsPerSecond": settings.rate_limit_ops_per_second,
                "solverJobsPerHour": settings.rate_limit_solver_jobs_per_hour,
                "renderConcurrency": settings.render_concurrency_per_firm,
                "maxDxfUploadBytes": settings.max_dxf_upload_bytes,
                "maxImageUploadBytes": settings.max_image_upload_bytes,
                "signedUrlTtlSeconds": settings.s3_signed_url_ttl_seconds,
            },
            server_time=datetime.now(UTC),
        )


#: The ASGI application. ``uvicorn garh_api.main:app``.
app = create_app()


__all__ = [
    "API_CONTENT_SECURITY_POLICY",
    "HSTS_MAX_AGE_SECONDS",
    "REQUEST_ID_HEADER",
    "app",
    "create_app",
    "lifespan",
]
