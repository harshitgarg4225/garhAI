"""Worker error tracking: the DSN gate, the tags, and ``capture_exception``.

Same negative-testing discipline as the API-side twin
(``apps/api/tests/test_observability.py``): the zero-DSN path is proven to never
import the SDK, a configured init is proven PII-off with the scrubber attached,
and the scrubber is fed the exact payloads it must drop. On top of that, the
worker-only surface — ``capture_exception``, which the runtime's
``job.failure_handling_crashed`` guard calls — is proven to be a silent no-op
when uninitialised and to never raise, because it runs inside a failure handler
whose own crash is the bug class it exists to report.

No Redis, no network: ``sentry_sdk`` is faked in ``sys.modules`` throughout.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:  # sentry_sdk is optional at runtime; its Event type is test-only here
    from sentry_sdk.types import Event

import pytest

from services.common import observability
from services.common.config import WorkerSettings

FAKE_DSN = "https://x@o0.ingest.sentry.io/0"


class _FakeSentry(types.ModuleType):
    """Enough surface for ``init_sentry``: records init kwargs and tags."""

    def __init__(self) -> None:
        super().__init__("sentry_sdk")
        self.init_kwargs: dict[str, Any] | None = None
        self.tags: dict[str, str] = {}
        self.captured: list[BaseException] = []

    def init(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs

    def set_tag(self, key: str, value: str) -> None:
        self.tags[key] = value

    def capture_exception(self, exc: BaseException) -> None:
        self.captured.append(exc)


def _settings(**overrides: Any) -> WorkerSettings:
    # pydantic-settings takes _env_file at runtime; its stubs do not declare it.
    return WorkerSettings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def _uninitialised() -> Iterator[None]:
    """Each test starts and ends with no SDK held — the module is process-global."""
    observability._sdk = None
    yield
    observability._sdk = None


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------


def test_empty_dsn_is_a_noop_that_never_imports_the_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delitem(sys.modules, "sentry_sdk", raising=False)

    assert observability.init_sentry(_settings(sentry_dsn="")) is False

    assert "sentry_sdk" not in sys.modules
    assert observability._sdk is None


def test_dsn_set_but_sdk_missing_degrades_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker with SENTRY_DSN set but no SDK must boot, not crash."""
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)  # import → ImportError
    assert observability.init_sentry(_settings(sentry_dsn=FAKE_DSN)) is False
    assert observability._sdk is None


# ---------------------------------------------------------------------------
# a configured init
# ---------------------------------------------------------------------------


def test_dsn_set_configures_pii_off_and_tags_the_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeSentry()
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)
    settings = _settings(sentry_dsn=FAKE_DSN, worker_name="solver")

    assert observability.init_sentry(settings) is True

    kwargs = fake.init_kwargs
    assert kwargs is not None
    assert kwargs["dsn"] == FAKE_DSN
    assert kwargs["send_default_pii"] is False
    assert kwargs["environment"] == settings.env
    assert kwargs["before_send"] is observability.scrub_sentry_event
    assert kwargs["traces_sample_rate"] == pytest.approx(settings.sentry_traces_sample_rate)
    # The same identity the structlog `service` field carries, so a Sentry issue
    # and a log line for one crash name the process identically.
    assert fake.tags["service"] == "garh-worker-solver"
    assert fake.tags["worker"] == "solver"
    assert observability._sdk is fake


# ---------------------------------------------------------------------------
# capture_exception — called from inside a failure handler, so it must be inert
# ---------------------------------------------------------------------------


def test_capture_exception_is_a_noop_when_uninitialised() -> None:
    observability.capture_exception(RuntimeError("boom"))  # must not raise


def test_capture_exception_reports_when_initialised(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeSentry()
    monkeypatch.setattr(observability, "_sdk", fake)
    error = RuntimeError("boom")

    observability.capture_exception(error)

    assert fake.captured == [error]


def test_capture_exception_swallows_a_broken_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reporter raising inside the failure-path guard would recreate the bug
    class it exists to surface — prove it cannot."""

    class _Broken:
        def capture_exception(self, _exc: BaseException) -> None:
            raise ConnectionError("sentry is down")

    monkeypatch.setattr(observability, "_sdk", _Broken())
    observability.capture_exception(RuntimeError("boom"))  # must not raise


# ---------------------------------------------------------------------------
# the scrubber, negative-tested with the payloads it must drop
# ---------------------------------------------------------------------------


def test_scrubber_drops_pii_carriers_and_masks_extra() -> None:
    event: dict[str, Any] = {
        "request": {
            "url": "http://minio:9000/garh-local/blob",
            "data": b"...",
            "cookies": {"c": "v"},
            "env": {"REMOTE_ADDR": "203.0.113.7"},
            "headers": {"X-Request-Id": "req-1", "Authorization": "AWS4-HMAC..."},
        },
        "user": {"id": "user_01J", "ip_address": "203.0.113.7"},
        "extra": {
            # Worker-specific §13 carriers: a presigned URL is a bearer credential
            # for its lifetime, and a prompt can quote user-authored text.
            "presigned_url": "https://minio/blob?X-Amz-Signature=...",
            "prompt": "add a pooja room next to the kitchen",
            "job_kind": "solve.options",
        },
    }

    out = observability.scrub_sentry_event(cast("Event", event), None)

    request = out["request"]
    assert "data" not in request
    assert "cookies" not in request
    assert "env" not in request
    assert request["headers"] == {"X-Request-Id": "req-1"}
    assert out["user"] == {"id": "user_01J"}
    assert out["extra"]["presigned_url"] == "***"
    assert out["extra"]["prompt"] == "***"
    assert out["extra"]["job_kind"] == "solve.options"


def test_scrubber_passes_minimal_events_through() -> None:
    event: dict[str, Any] = {"message": "boom", "level": "error"}
    assert observability.scrub_sentry_event(cast("Event", event), None) is event
