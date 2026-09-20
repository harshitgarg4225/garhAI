"""The worker heartbeat key (``services/common/heartbeat.py``), negative-tested.

What an operator relies on: a live worker refreshes ``garh:worker:<name>:<instance>``
with an expiry, a stopped one deletes it, and the document carries the counters
and provider names the ops page renders. Each claim has the run that would break
it: a payload with no expiry would outlive its process forever (the "seen 3 days
ago" worker that is not there), a retire that did not delete would leave a
deploy's old replica on the page until the TTL lapsed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from services.common.config import WorkerSettings
from services.common.heartbeat import (
    EXPECTED_WORKERS,
    HEARTBEAT_MIN_TTL_SECONDS,
    WORKER_HEARTBEAT_PREFIX,
    heartbeat_key,
    heartbeat_payload,
    heartbeat_ttl_seconds,
    instance_id,
)
from services.common.jobstore import JobResult
from services.common.metrics import WorkerMetrics
from services.common.runtime import BaseJobHandler, JobContext, Worker
from services.common.testing import FakeRedis


class _NoopHandler(BaseJobHandler):
    # Annotated, not inferred: bare ("solve",) is tuple[str], and JobHandler's
    # protocol wants tuple[str, ...] — mypy --strict rejects the narrower one.
    kinds: tuple[str, ...] = ("solve",)

    async def handle(self, ctx: JobContext) -> JobResult:  # pragma: no cover - never run
        raise AssertionError("the heartbeat tests never run a job")


def _settings(**overrides: Any) -> WorkerSettings:
    """A WorkerSettings that cannot be contaminated by the developer's .env.

    ``**overrides: Any`` rather than ``object``: every field has its own literal or
    scalar type, so ``object`` makes each one an arg-type error under --strict.
    """
    return WorkerSettings(_env_file=None, **overrides)  # type: ignore[call-arg]


def _worker(redis: FakeRedis, **overrides: Any) -> Worker:
    return Worker(
        name="solver", handler=_NoopHandler(), settings=_settings(**overrides), redis=redis
    )


# ---------------------------------------------------------------------------
# The key and the payload
# ---------------------------------------------------------------------------


def test_key_is_under_the_shared_prefix_and_never_carries_a_colon_in_the_instance() -> None:
    key = heartbeat_key("render", "host:with:colons-42")
    assert key.startswith(WORKER_HEARTBEAT_PREFIX)
    assert key == "garh:worker:render:host-with-colons-42"
    assert instance_id().count(":") == 0


def test_ttl_is_three_sweeps_with_a_floor() -> None:
    assert heartbeat_ttl_seconds(10) == HEARTBEAT_MIN_TTL_SECONDS
    assert heartbeat_ttl_seconds(20) == 60
    assert heartbeat_ttl_seconds(0) == HEARTBEAT_MIN_TTL_SECONDS


def test_payload_carries_what_the_ops_page_renders_and_nothing_secret() -> None:
    metrics = WorkerMetrics(worker="render", queue="garh:queue:render")
    metrics.jobs_succeeded = 3
    metrics.jobs_failed = 1
    metrics.in_flight = 2
    metrics.concurrency = 4
    metrics.record_duration(400)
    metrics.record_duration(1200)
    at = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    payload = heartbeat_payload(
        metrics=metrics,
        instance="host-7",
        interval_seconds=10,
        env="dev",
        provider_llm="mock",
        provider_render="stability",
        sentry_enabled=False,
        draining=False,
        now=at,
    )
    assert payload["worker"] == "render"
    assert payload["queue"] == "garh:queue:render"
    assert payload["instance"] == "host-7"
    assert payload["sentAt"] == at.isoformat()
    assert payload["intervalSeconds"] == 10
    assert payload["jobsSucceeded"] == 3 and payload["jobsFailed"] == 1
    assert payload["inFlight"] == 2 and payload["concurrency"] == 4
    assert payload["p50DurationMs"] == 400 and payload["p95DurationMs"] == 1200
    assert payload["providerRender"] == "stability" and payload["providerLlm"] == "mock"
    assert payload["sentry"] == "off"
    assert payload["draining"] is False
    # Round-trips as plain JSON — the API reads it with json.loads, nothing else.
    assert json.loads(json.dumps(payload, sort_keys=True)) == payload
    # Names only. A key that looks like a credential is a bug in this function.
    for name in payload:
        assert not any(part in name.lower() for part in ("key", "secret", "token", "dsn")), name


def test_the_expected_worker_names_are_the_three_queues() -> None:
    assert EXPECTED_WORKERS == ("solver", "render", "drawings")


# ---------------------------------------------------------------------------
# The runtime writes it, with an expiry, and retires it on stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_writes_the_key_with_an_expiry() -> None:
    redis = FakeRedis()
    worker = _worker(redis, queue_sweep_interval_seconds=10)

    await worker.publish_heartbeat()

    raw = redis.strings.get(worker.heartbeat_key)
    assert raw is not None, "no heartbeat key written"
    assert worker.heartbeat_key.startswith("garh:worker:solver:")
    body = json.loads(raw)
    assert body["worker"] == "solver" and body["queue"] == worker.queue_name
    assert body["instance"] == worker.instance
    # The negative control that matters: WITHOUT an expiry a crashed worker would
    # sit on the ops page as "alive" forever.
    assert redis.ttls.get(worker.heartbeat_key) == heartbeat_ttl_seconds(10)


@pytest.mark.asyncio
async def test_a_draining_worker_says_so() -> None:
    redis = FakeRedis()
    worker = _worker(redis)
    worker.stop("test")
    await worker.publish_heartbeat()
    assert json.loads(redis.strings[worker.heartbeat_key])["draining"] is True


@pytest.mark.asyncio
async def test_retire_deletes_the_key_so_an_old_replica_vanishes_at_once() -> None:
    redis = FakeRedis()
    worker = _worker(redis)
    await worker.publish_heartbeat()
    assert worker.heartbeat_key in redis.strings

    await worker.retire_heartbeat()
    assert worker.heartbeat_key not in redis.strings


@pytest.mark.asyncio
async def test_publish_is_a_no_op_before_redis_is_attached() -> None:
    worker = Worker(name="solver", handler=_NoopHandler(), settings=_settings(), redis=None)
    # No client yet (run() opens one); must not raise, must not invent a key.
    await worker.publish_heartbeat()
    await worker.retire_heartbeat()


@pytest.mark.asyncio
async def test_the_runtime_source_publishes_from_the_sweep_and_retires_on_shutdown() -> None:
    """A structural pin: the calls must sit where the docstring says they do.

    The sweep loop is the cadence and the shutdown path is the retirement; a refactor
    that moved either call somewhere it never runs would leave every other test here
    green while no worker ever wrote a heartbeat.
    """
    import inspect

    from services.common import runtime

    sweep = inspect.getsource(runtime.Worker._sweep_loop)
    assert "await self.publish_heartbeat()" in sweep
    run = inspect.getsource(runtime.Worker.run)
    assert "await self.retire_heartbeat()" in run
    assert run.index("await self._drain(queue)") < run.index("await self.retire_heartbeat()")
