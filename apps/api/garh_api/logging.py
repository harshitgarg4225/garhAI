"""structlog JSON logging, request-id binding, Sentry-compatible error hook (§18).

Boot sequence (owned by ``main.py``)::

    from garh_api.config import get_settings
    from garh_api.logging import configure_logging, init_error_reporting

    settings = get_settings()
    configure_logging(settings)
    init_error_reporting(settings)

Per request (middleware)::

    request_id = bind_request_context(
        request_id=request.headers.get("x-request-id"),
        method=request.method,
        path=request.url.path,
    )
    try:
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
    finally:
        clear_request_context()

Everything logged inside that request — including SQLAlchemy and uvicorn records
routed through the stdlib bridge — carries ``request_id`` automatically, because the
binding lives in a :mod:`contextvars` context that survives ``await``.

PII rule (§13): never bind an email address, a phone number, or free brief text.
Bind ids and, when a domain is genuinely useful, the email *domain* only.
"""

from __future__ import annotations

import logging
import logging.config
import sys
import uuid
from collections.abc import Callable, MutableMapping
from typing import Any

import structlog

from garh_api.config import Settings, get_settings

#: Keys that must never appear in a log event. ``scrub_pii`` drops them.
FORBIDDEN_LOG_KEYS = frozenset(
    {
        "password",
        "otp",
        "otp_code",
        "code",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "api_key",
        "anthropic_api_key",
        "jwt_private_key",
        "secret",
        "s3_secret_access_key",
        "razorpay_key_secret",
        "email",
        "phone",
        "brief_text",
    }
)

_REDACTED = "***"

#: Registered error sink. Sentry installs itself here; tests can install a fake.
_error_hook: Callable[[BaseException, MutableMapping[str, Any]], None] | None = None
_configured = False


# ---------------------------------------------------------------------------
# processors
# ---------------------------------------------------------------------------


def scrub_pii(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Drop secrets/PII that leaked into a log call (§13 secrets hygiene)."""
    for key in list(event_dict.keys()):
        if key.lower() in FORBIDDEN_LOG_KEYS:
            event_dict[key] = _REDACTED
    return event_dict


def error_hook_processor(
    _logger: Any, method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Forward error/critical events carrying an exception to the error sink.

    Sentry-compatible: the sink signature is ``(exc, context)``, which is exactly
    what ``sentry_sdk.capture_exception(exc, scope=...)`` wants. Failures inside the
    sink are swallowed — telemetry must never break the request it describes.
    """
    if _error_hook is None or method not in ("error", "critical", "exception"):
        return event_dict
    exc_info = event_dict.get("exc_info")
    exc: BaseException | None = None
    if isinstance(exc_info, BaseException):
        exc = exc_info
    elif exc_info:
        current = sys.exc_info()[1]
        if isinstance(current, BaseException):
            exc = current
    if exc is None:
        return event_dict
    try:
        _error_hook(exc, dict(event_dict))
    except Exception:  # noqa: BLE001 - never let telemetry raise
        pass
    return event_dict


def _shared_processors(settings: Settings) -> list[Any]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        scrub_pii,
        error_hook_processor,
        _static_fields(settings),
    ]


def _static_fields(settings: Settings) -> Any:
    service = settings.app_name
    env = settings.env

    def add_static(
        _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        event_dict.setdefault("service", service)
        event_dict.setdefault("env", env)
        return event_dict

    return add_static


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


def configure_logging(settings: Settings | None = None, *, force: bool = False) -> None:
    """Install structlog + the stdlib bridge. Idempotent unless ``force=True``.

    The stdlib bridge matters: uvicorn, SQLAlchemy and any third-party library log
    through :mod:`logging`, and without it half the output would be unstructured
    text in an otherwise-JSON stream.
    """
    global _configured
    if _configured and not force:
        return
    cfg = settings or get_settings()
    shared = _shared_processors(cfg)

    renderer: Any
    if cfg.log_format == "console":
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    else:
        renderer = structlog.processors.JSONRenderer(sort_keys=True)

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(cfg.log_level)

    # Let these propagate to our single root handler instead of double-printing.
    for name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "sqlalchemy.engine",
        "alembic",
        "httpx",
    ):
        child = logging.getLogger(name)
        child.handlers = []
        child.propagate = True

    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if cfg.sql_echo else logging.WARNING
    )
    _configured = True


def get_logger(name: str | None = None) -> Any:
    """A bound logger. Call after :func:`configure_logging` (safe either way)."""
    return structlog.get_logger(name) if name else structlog.get_logger()


# ---------------------------------------------------------------------------
# request context
# ---------------------------------------------------------------------------


def new_request_id() -> str:
    return uuid.uuid4().hex


def bind_request_context(
    *,
    request_id: str | None = None,
    firm_id: Any = None,
    user_id: Any = None,
    role: str | None = None,
    method: str | None = None,
    path: str | None = None,
    **extra: Any,
) -> str:
    """Bind request-scoped fields to every subsequent log line. Returns the id used.

    An inbound ``x-request-id`` is honoured but length-capped — it ends up in logs,
    so it is untrusted input.
    """
    rid = (request_id or "").strip()[:64] or new_request_id()
    bindings: dict[str, Any] = {"request_id": rid}
    if firm_id is not None:
        bindings["firm_id"] = str(firm_id)
    if user_id is not None:
        bindings["user_id"] = str(user_id)
    if role:
        bindings["role"] = role
    if method:
        bindings["http_method"] = method
    if path:
        bindings["http_path"] = path
    bindings.update(extra)
    structlog.contextvars.bind_contextvars(**bindings)
    return rid


def bind_tenant_context(firm_id: Any, user_id: Any = None, role: str | None = None) -> None:
    """Add tenant identity once auth has resolved it (mid-request)."""
    bindings: dict[str, Any] = {"firm_id": str(firm_id)}
    if user_id is not None:
        bindings["user_id"] = str(user_id)
    if role:
        bindings["role"] = role
    structlog.contextvars.bind_contextvars(**bindings)


def current_request_id() -> str | None:
    ctx = structlog.contextvars.get_contextvars()
    value = ctx.get("request_id")
    return str(value) if value else None


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()


# ---------------------------------------------------------------------------
# error reporting
# ---------------------------------------------------------------------------


def set_error_hook(hook: Callable[[BaseException, MutableMapping[str, Any]], None] | None) -> None:
    """Install (or remove) the error sink used by :func:`error_hook_processor`."""
    global _error_hook
    _error_hook = hook


def init_error_reporting(settings: Settings | None = None) -> bool:
    """Wire Sentry if ``SENTRY_DSN`` is set and ``sentry_sdk`` is installed.

    ``sentry_sdk`` is intentionally **not** a pinned dependency: the platform must
    run with zero third-party telemetry. This is the seam, not the commitment.
    Returns True when a sink was installed.
    """
    cfg = settings or get_settings()
    if not cfg.sentry_enabled:
        return False
    try:
        import sentry_sdk  # type: ignore[import-not-found]
    except ImportError:
        get_logger(__name__).warning(
            "sentry.unavailable",
            reason="SENTRY_DSN is set but sentry_sdk is not installed",
            action="pip install sentry-sdk, or unset SENTRY_DSN",
        )
        return False

    sentry_sdk.init(
        dsn=cfg.sentry_dsn,
        environment=cfg.env,
        release=cfg.app_name,
        traces_sample_rate=cfg.sentry_traces_sample_rate,
        send_default_pii=False,
    )

    def _hook(exc: BaseException, context: MutableMapping[str, Any]) -> None:
        with sentry_sdk.new_scope() as scope:
            for key in ("request_id", "firm_id", "user_id", "http_path", "http_method"):
                if key in context:
                    scope.set_tag(key, context[key])
            scope.set_context("log_event", dict(context))
            sentry_sdk.capture_exception(exc)

    set_error_hook(_hook)
    return True


def report_exception(exc: BaseException, **context: Any) -> None:
    """Report an exception directly (for paths that swallow it deliberately)."""
    logger = get_logger(__name__)
    logger.error("unhandled_exception", exc_info=exc, **context)


__all__ = [
    "FORBIDDEN_LOG_KEYS",
    "bind_request_context",
    "bind_tenant_context",
    "clear_request_context",
    "configure_logging",
    "current_request_id",
    "error_hook_processor",
    "get_logger",
    "init_error_reporting",
    "new_request_id",
    "report_exception",
    "scrub_pii",
    "set_error_hook",
]
