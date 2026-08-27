"""Error tracking (§18 "Sentry-compatible error hook") — OFF by default.

The locked decision (SKILL.md, CLAUDE.md): the whole product runs and is
e2e-testable with zero API keys and zero third-party telemetry. So everything in
this module is gated on ``SENTRY_DSN`` being non-empty, and the SDK import lives
*inside* :func:`init_sentry` — the default zero-keys boot never pays for it, never
imports it, and cannot be broken by it.

What turning it on gives you, and what it deliberately withholds:

* ``sentry_sdk.init`` with the FastAPI/Starlette integrations auto-enabled (the
  SDK detects them; no explicit integration list to drift), tracing at a small
  ``SENTRY_TRACES_SAMPLE_RATE``, and a release stamped from ``APP_VERSION`` or
  ``GIT_SHA`` when the deploy pipeline provides one.
* The structlog bridge: :func:`garh_api.logging.error_hook_processor` forwards
  error/critical events carrying an exception to the hook installed here, with the
  already-scrubbed log context attached as tags + a ``log_event`` context.
* **PII discipline (§13).** ``send_default_pii=False``, and
  :func:`scrub_sentry_event` runs as ``before_send`` on every event: request
  bodies, cookies and headers are dropped (only ``x-request-id`` survives, so a
  Sentry issue can still be joined against the JSON logs), the server ``env``
  block (client IP) is dropped, ``user`` is reduced to its id, and ``extra`` is
  passed through the same :func:`~garh_api.logging.scrub_pii` the log pipeline
  uses. Room and storey names are user-authored content: an event should carry
  error types and codes, never user strings — the scrubber is the backstop for
  mistakes, not a licence to attach model state.

Boot (owned by ``main.py``'s lifespan)::

    init_sentry(settings)   # no-op unless SENTRY_DSN is set
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from typing import Any

from garh_api.config import Settings, get_settings
from garh_api.logging import get_logger, scrub_pii, set_error_hook

#: The one request header allowed through to an event: it joins a Sentry issue to
#: the JSON log lines for the same request, and identifies nobody.
_ALLOWED_REQUEST_HEADER = "x-request-id"


def _release() -> str | None:
    """Deploy identity for Sentry's release tracking.

    ``APP_VERSION`` (a human-chosen tag) beats ``GIT_SHA`` (what CI/CD stamps);
    with neither set, ``None`` lets the SDK apply its own detection. Read from the
    process environment rather than ``Settings`` because it is deploy metadata,
    not configuration — no default belongs in ``.env.example``'s working values.
    """
    return os.environ.get("APP_VERSION") or os.environ.get("GIT_SHA") or None


def scrub_sentry_event(
    event: MutableMapping[str, Any], _hint: Any = None
) -> MutableMapping[str, Any]:
    """``before_send``: strip request/user PII from an outbound event (§13).

    Sentry must not become a side channel around the log scrubber. Mutates and
    returns ``event`` (the SDK contract); returning it — never ``None`` — keeps
    every error visible, just stripped.
    """
    request = event.get("request")
    if isinstance(request, dict):
        # Bodies and cookies are the §13 request-PII carriers; drop, don't mask —
        # a masked body still leaks its keys and its shape.
        request.pop("data", None)
        request.pop("cookies", None)
        # `env` is where the SDK puts REMOTE_ADDR — an IP is PII.
        request.pop("env", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                key: value
                for key, value in headers.items()
                if key.lower() == _ALLOWED_REQUEST_HEADER
            }
    # send_default_pii=False should keep `user` empty; belt-and-braces, keep the
    # id (ids are the §13-sanctioned identity in telemetry) and nothing else.
    user = event.get("user")
    if isinstance(user, dict):
        event["user"] = {"id": user["id"]} if "id" in user else {}
    # `extra` goes through the SAME scrubber as every log line, so the two
    # channels cannot disagree about what a secret is.
    extra = event.get("extra")
    if isinstance(extra, dict):
        scrub_pii(None, "", extra)
    return event


def init_sentry(settings: Settings | None = None) -> bool:
    """Wire Sentry if ``SENTRY_DSN`` is set. Returns True when a sink was installed.

    The lazy import is load-bearing, not style: the zero-DSN path must never
    import ``sentry_sdk``, so a broken/absent SDK cannot take down the mock
    default stack, and ``tests/test_observability.py`` negative-tests exactly
    that. DSN set but SDK missing degrades to a loud warning, never a crash —
    telemetry must not break the process it describes.
    """
    cfg = settings or get_settings()
    if not cfg.sentry_enabled:
        return False
    try:
        import sentry_sdk
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
        release=_release(),
        traces_sample_rate=cfg.sentry_traces_sample_rate,
        send_default_pii=False,
        before_send=scrub_sentry_event,
    )

    def _hook(exc: BaseException, context: MutableMapping[str, Any]) -> None:
        # `context` is the structlog event_dict AFTER scrub_pii ran in the
        # processor chain, so tags and the log_event context are already clean.
        with sentry_sdk.new_scope() as scope:
            for key in ("request_id", "firm_id", "user_id", "http_path", "http_method"):
                if key in context:
                    scope.set_tag(key, context[key])
            scope.set_context("log_event", dict(context))
            sentry_sdk.capture_exception(exc)

    set_error_hook(_hook)
    get_logger(__name__).info(
        "sentry.initialised",
        environment=cfg.env,
        traces_sample_rate=cfg.sentry_traces_sample_rate,
        release=_release() or "(sdk default)",
    )
    return True


__all__ = ["init_sentry", "scrub_sentry_event"]
