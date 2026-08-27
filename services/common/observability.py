"""Error tracking for worker processes — env-gated by ``SENTRY_DSN``, OFF by default.

Mirrors ``garh_api.observability`` the same way ``services/common/logging.py``
mirrors ``garh_api.logging``: deliberately independent, because a worker image
must stay importable without the API package, but sharing the rules that matter —
the DSN gate, ``send_default_pii=False``, and a ``before_send`` scrubber that
runs ``extra`` through the same :func:`~services.common.logging.scrub_pii` as
every log line.

The locked decision applies here word for word: the whole product runs with zero
API keys and zero third-party telemetry. The SDK import lives *inside*
:func:`init_sentry`, so the mock-default boot never imports ``sentry_sdk`` and a
missing SDK degrades to a warning, never a crash.

What a worker sends when the DSN is set: exceptions, tagged ``service`` /
``worker`` so the three worker fleets separate in one Sentry project. Event
messages should carry error types and codes — room and storey names are
user-authored content and must never ride along (§13). Breadcrumbs stay at the
SDK default; the scrubber is the backstop.

Boot (owned by ``runtime.Worker.run``)::

    init_sentry(settings)          # no-op unless SENTRY_DSN is set

Reporting from a path that must not raise (the failure-path guard in
``runtime._run_job``)::

    capture_exception(exc)         # silent no-op unless init_sentry succeeded
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # sentry_sdk is an optional runtime dependency; types only here
    from sentry_sdk.types import Event

from services.common.config import WorkerSettings, get_worker_settings
from services.common.logging import get_logger, scrub_pii

log = get_logger("observability")

#: The ``sentry_sdk`` module, kept ONLY after a successful :func:`init_sentry`.
#: This is what lets :func:`capture_exception` be a true no-op — no import
#: attempt, no attribute probing — on the zero-DSN default path.
_sdk: Any = None


def _release() -> str | None:
    """Deploy identity: ``APP_VERSION`` beats ``GIT_SHA``; ``None`` = SDK default.

    Deploy metadata rather than configuration, so it is read straight from the
    environment — same rule as the API side.
    """
    return os.environ.get("APP_VERSION") or os.environ.get("GIT_SHA") or None


def scrub_sentry_event(event: Event, _hint: Any = None) -> Event:
    """``before_send``: strip PII from an outbound event (§13).

    Workers serve no HTTP requests, but the request block is scrubbed anyway —
    a guard that only works when nobody misuses the SDK is not a guard. Mutates
    and returns ``event`` (the SDK contract); never returns ``None``, so every
    error stays visible, just stripped.
    """
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None)
        request.pop("cookies", None)
        request.pop("env", None)  # REMOTE_ADDR — an IP is PII
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                key: value for key, value in headers.items() if key.lower() == "x-request-id"
            }
    user = event.get("user")
    if isinstance(user, dict):
        event["user"] = {"id": user["id"]} if "id" in user else {}
    # Same scrubber as every worker log line (presigned URLs, prompts, keys) —
    # the two channels must agree about what a secret is.
    extra = event.get("extra")
    if isinstance(extra, dict):
        scrub_pii(None, "", extra)
    return event


def init_sentry(settings: WorkerSettings | None = None) -> bool:
    """Wire Sentry if ``SENTRY_DSN`` is set. Returns True when initialised.

    The lazy import is load-bearing: the zero-DSN path never imports the SDK
    (``services/common/tests/test_observability.py`` negative-tests exactly
    that), so the default stack cannot be broken by a missing or broken SDK.
    """
    global _sdk
    cfg = settings or get_worker_settings()
    if not cfg.sentry_dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        log.warning(
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
    # Same identity the structlog `service` field carries, so a Sentry issue and
    # a log line for the same crash name the process identically.
    sentry_sdk.set_tag("service", "garh-worker-%s" % cfg.worker_name)
    sentry_sdk.set_tag("worker", cfg.worker_name)
    _sdk = sentry_sdk
    log.info(
        "sentry.initialised",
        environment=cfg.env,
        worker=cfg.worker_name,
        traces_sample_rate=cfg.sentry_traces_sample_rate,
        release=_release() or "(sdk default)",
    )
    return True


def capture_exception(exc: BaseException) -> None:
    """Report ``exc`` if Sentry is initialised; a silent no-op otherwise.

    Exists for paths that are *already* a failure handler — the runtime's
    "failure path crashed" guard calls this, and a reporter that can itself
    raise inside that guard would recreate the exact bug class it exists to
    surface. Never raises.
    """
    if _sdk is None:
        return
    with contextlib.suppress(Exception):
        _sdk.capture_exception(exc)


__all__ = ["capture_exception", "init_sentry", "scrub_sentry_event"]
