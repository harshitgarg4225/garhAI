"""structlog JSON logging for worker processes (playbook §18).

Deliberately independent of ``garh_api.logging``: a worker is a separate process with
its own ``service`` name, and the worker image must stay importable without the API
package. The two share the rule that matters — :data:`FORBIDDEN_LOG_KEYS` is the same
set, kept in sync by ``services/common/tests/test_logging.py``.

Boot::

    configure_worker_logging(settings)
    log = get_logger("solver")

Per job, the runtime binds context once and every line inside that job carries it::

    bind_job_context(job_id=..., firm_id=..., project_id=..., attempt=1)
    ...
    clear_job_context()

PII rule (§13): bind ids, never an email, a phone number, or free brief text. The
scrubber is a backstop for mistakes, not a licence to log secrets and rely on it.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from services.common.config import WorkerSettings, get_worker_settings

#: Keys that must never appear in a log event. Mirrors ``garh_api.logging``.
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
        # Worker-specific: presigned URLs carry a signature in the query string and
        # are, for their lifetime, bearer credentials for an object.
        "presigned_url",
        "put_url",
        "get_url",
        "prompt",
    }
)

_REDACTED = "***"
_configured = False


def scrub_pii(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Replace forbidden keys with ``***`` (§13 secrets hygiene)."""
    for key in list(event_dict.keys()):
        if key.lower() in FORBIDDEN_LOG_KEYS:
            event_dict[key] = _REDACTED
    return event_dict


def _static_fields(settings: WorkerSettings) -> Any:
    service = "garh-worker-%s" % settings.worker_name
    env = settings.env

    def add_static(
        _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        event_dict.setdefault("service", service)
        event_dict.setdefault("worker", settings.worker_name)
        event_dict.setdefault("env", env)
        return event_dict

    return add_static


def configure_worker_logging(
    settings: WorkerSettings | None = None, *, force: bool = False
) -> None:
    """Install structlog and route stdlib logging through it. Idempotent."""
    global _configured
    if _configured and not force:
        return
    cfg = settings or get_worker_settings()

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        # NOT structlog.stdlib.add_logger_name: that processor reads
        # ``logger.name``, which only exists on stdlib loggers — with the
        # PrintLoggerFactory below it raises AttributeError on the first
        # ``log.info`` and takes the whole worker down at boot. The logger
        # name is bound in :func:`get_logger` instead.
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        scrub_pii,
        _static_fields(cfg),
        structlog.processors.format_exc_info,
    ]
    renderer: Any = (
        structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
        if cfg.log_format == "console"
        else structlog.processors.JSONRenderer(sort_keys=True)
    )

    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(cfg.log_level)
            if isinstance(logging.getLevelName(cfg.log_level), int)
            else logging.INFO
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Third-party libraries (redis, httpx, ezdxf) log through stdlib; without this
    # bridge half a worker's output would be unstructured text in a JSON stream.
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=renderer,
            foreign_pre_chain=processors[:-1],
        )
    )
    root.addHandler(handler)
    root.setLevel(cfg.log_level)
    # httpx logs every request line at INFO, including the URL — which for us means
    # presigned URLs in plaintext. Quieten it; we log our own request events.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str | None = None) -> Any:
    """A bound logger. Safe to call before :func:`configure_worker_logging`.

    The name is BOUND (``logger=<name>``) rather than derived by
    ``add_logger_name`` — see the processor-chain note in
    :func:`configure_worker_logging`.
    """
    return structlog.get_logger().bind(logger=name) if name else structlog.get_logger()


def bind_job_context(
    *,
    job_id: str,
    kind: str | None = None,
    firm_id: str | None = None,
    project_id: str | None = None,
    attempt: int | None = None,
    request_id: str | None = None,
    **extra: Any,
) -> None:
    """Bind job-scoped fields to every subsequent log line in this task."""
    bindings: dict[str, Any] = {"job_id": job_id}
    if kind is not None:
        bindings["job_kind"] = kind
    if firm_id is not None:
        bindings["firm_id"] = firm_id
    if project_id is not None:
        bindings["project_id"] = project_id
    if attempt is not None:
        bindings["attempt"] = attempt
    if request_id is not None:
        bindings["request_id"] = request_id
    bindings.update(extra)
    structlog.contextvars.bind_contextvars(**bindings)


def clear_job_context() -> None:
    """Drop job-scoped bindings. Always call in a ``finally``."""
    structlog.contextvars.clear_contextvars()


__all__ = [
    "FORBIDDEN_LOG_KEYS",
    "bind_job_context",
    "clear_job_context",
    "configure_worker_logging",
    "get_logger",
    "scrub_pii",
]
