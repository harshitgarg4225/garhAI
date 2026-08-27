"""``init_sentry``'s gates and the PII scrubber — file-only, no datastore, no network.

Three claims worth proving, each negative-tested per the CLAUDE.md rule (a green
check that cannot go red is worse than no check):

1. **The zero-DSN path never imports the SDK.** The locked decision — the whole
   product runs with zero keys and zero third-party telemetry — is only true if
   the default boot cannot even be *broken* by ``sentry_sdk``. Asserted by
   evicting the module from ``sys.modules`` and proving it stays evicted.
2. **A configured init is PII-safe by construction**: ``send_default_pii=False``
   and the ``before_send`` scrubber are actually passed to ``sentry_sdk.init``,
   not merely intended. Asserted against a fake module so no event ever leaves
   the process.
3. **The scrubber scrubs.** An event carrying the exact §13 carriers — request
   body, cookies, headers, client IP, secret-bearing ``extra`` keys — comes out
   without them, and the one allowed header (``x-request-id``) survives so a
   Sentry issue still joins against the JSON logs.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from typing import Any

import pytest
from garh_api import logging as garh_logging
from garh_api import observability
from garh_api.config import Settings

#: Syntactically valid, semantically nothing: o0/0 is no real org or project, and
#: the fake module below means no transport is ever constructed anyway.
FAKE_DSN = "https://x@o0.ingest.sentry.io/0"


class _FakeSentry(types.ModuleType):
    """Just enough surface for ``init_sentry``: records what ``init`` received."""

    def __init__(self) -> None:
        super().__init__("sentry_sdk")
        self.init_kwargs: dict[str, Any] | None = None

    def init(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs


def _settings(**overrides: Any) -> Settings:
    """A dev Settings that cannot be contaminated by the developer's .env."""
    return Settings(_env_file=None, **overrides)


@pytest.fixture(autouse=True)
def _reset_error_hook() -> Iterator[None]:
    """``init_sentry`` installs a process-global hook; never leak it across tests."""
    yield
    garh_logging.set_error_hook(None)


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------


def test_empty_dsn_is_a_noop_that_never_imports_the_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delitem(sys.modules, "sentry_sdk", raising=False)

    assert observability.init_sentry(_settings(sentry_dsn="")) is False

    # The load-bearing assertion: a lazy import that ran would have put the
    # module back into sys.modules. Its absence is proof of the no-op.
    assert "sentry_sdk" not in sys.modules
    assert garh_logging._error_hook is None


def test_dsn_set_but_sdk_missing_degrades_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SENTRY_DSN set on a box without the SDK must warn, not crash the boot."""
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)  # import → ImportError
    assert observability.init_sentry(_settings(sentry_dsn=FAKE_DSN)) is False


# ---------------------------------------------------------------------------
# a configured init
# ---------------------------------------------------------------------------


def test_dsn_set_configures_the_sdk_with_pii_off(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeSentry()
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)
    settings = _settings(sentry_dsn=FAKE_DSN)

    assert observability.init_sentry(settings) is True

    kwargs = fake.init_kwargs
    assert kwargs is not None
    assert kwargs["dsn"] == FAKE_DSN
    assert kwargs["send_default_pii"] is False
    assert kwargs["environment"] == settings.env
    assert kwargs["before_send"] is observability.scrub_sentry_event
    assert kwargs["traces_sample_rate"] == pytest.approx(settings.sentry_traces_sample_rate)
    assert kwargs["release"] is None  # neither APP_VERSION nor GIT_SHA set
    # The structlog error/critical → Sentry bridge is armed.
    assert garh_logging._error_hook is not None


def test_release_prefers_app_version_then_git_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_SHA", "abc1234")
    monkeypatch.setenv("APP_VERSION", "1.2.3")
    assert observability._release() == "1.2.3"
    monkeypatch.delenv("APP_VERSION")
    assert observability._release() == "abc1234"


def test_traces_sample_rate_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.5")
    assert _settings().sentry_traces_sample_rate == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# the scrubber, negative-tested with the payloads it must drop
# ---------------------------------------------------------------------------


def test_scrubber_drops_body_cookies_headers_and_ip_but_keeps_request_id() -> None:
    event: dict[str, Any] = {
        "request": {
            "url": "http://localhost:8000/api/v1/projects",
            "method": "POST",
            "data": {"name": "Sharma residence", "phone": "+91 98765 43210"},
            "cookies": {"garh_refresh": "definitely-a-credential"},
            "env": {"REMOTE_ADDR": "203.0.113.7"},
            "headers": {
                "X-Request-Id": "req-abc123",
                "Authorization": "Bearer eyJhbGciOi...",
                "Cookie": "garh_refresh=definitely-a-credential",
                "User-Agent": "Mozilla/5.0",
            },
        },
        "user": {"id": "user_01J", "ip_address": "203.0.113.7", "email": "a@b.c"},
        "extra": {"token": "secret-token", "email": "a@b.c", "op_count": 3},
    }

    out = observability.scrub_sentry_event(event, None)

    request = out["request"]
    assert "data" not in request
    assert "cookies" not in request
    assert "env" not in request
    # ONLY the correlation header survives — assert the whole dict, not membership,
    # so a second header slipping through fails the test.
    assert request["headers"] == {"X-Request-Id": "req-abc123"}
    assert out["user"] == {"id": "user_01J"}
    # `extra` goes through the log scrubber: secrets masked, real telemetry kept.
    assert out["extra"]["token"] == "***"
    assert out["extra"]["email"] == "***"
    assert out["extra"]["op_count"] == 3


def test_scrubber_returns_the_event_untouched_shape_for_minimal_events() -> None:
    """No request, no extra — the scrubber must pass it through, never drop it."""
    event: dict[str, Any] = {"message": "boom", "level": "error"}
    assert observability.scrub_sentry_event(event, None) is event


def test_init_error_reporting_still_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """The historical seam in garh_api.logging keeps working via delegation."""
    monkeypatch.delitem(sys.modules, "sentry_sdk", raising=False)
    assert garh_logging.init_error_reporting(_settings(sentry_dsn="")) is False
    assert "sentry_sdk" not in sys.modules
