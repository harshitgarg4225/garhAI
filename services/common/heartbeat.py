"""Worker heartbeats — the one Redis key that says "this worker process is alive".

Playbook §18 asks for a queue-depth metric and a health probe per worker. Both
exist (``metrics.py``, ``health.py``), and both are answered *by the worker*: an
operator has to reach each container's health port to read them. Nothing in the
product could say "the render worker has not been seen for ten minutes" from the
API side — which is exactly the failure that matters, because a worker that is
simply *absent* answers no probe at all.

So every worker writes one small JSON document to Redis on each sweep::

    garh:worker:<worker>:<instance>   →   {"worker": "render", "sentAt": ..., ...}

with an expiry of a few sweep intervals. A live worker refreshes it; a dead one
lets it lapse, and Redis deletes it. The API's operations endpoint
(``garh_api.platform_status``) SCANs the prefix and reports what it finds — and,
more importantly, which of the three expected workers it does NOT find.

THIS FILE IS A CROSS-PROCESS CONTRACT. ``garh_api.queue.WORKER_HEARTBEAT_PREFIX``
mirrors the prefix and ``garh_api.platform_status`` reads the field names below.
Change either side and the other in the same commit.
"""

from __future__ import annotations

import os
import socket
from datetime import UTC, datetime
from typing import Any

from services.common.metrics import WorkerMetrics

#: Mirrored by ``garh_api.queue.WORKER_HEARTBEAT_PREFIX``.
WORKER_HEARTBEAT_PREFIX = "garh:worker:"

#: Floor on the key's expiry. A heartbeat interval is the sweep interval (10 s by
#: default); the key outlives three of them so one slow sweep does not read as a
#: dead worker, and never expires in under half a minute.
HEARTBEAT_MIN_TTL_SECONDS = 30
HEARTBEAT_TTL_MULTIPLIER = 3

#: The worker names the API expects to see. A deployment without one of these
#: reports it as ``missing`` on the ops page — that is the alarm, not decoration.
EXPECTED_WORKERS: tuple[str, ...] = ("solver", "render", "drawings")


def heartbeat_key(worker: str, instance: str) -> str:
    """``garh:worker:<worker>:<instance>``; the instance id carries no colons."""
    cleaned = instance.replace(":", "-")
    return "%s%s:%s" % (WORKER_HEARTBEAT_PREFIX, worker, cleaned)


def heartbeat_ttl_seconds(sweep_interval_seconds: int) -> int:
    """How long a heartbeat key lives without being refreshed."""
    return max(HEARTBEAT_MIN_TTL_SECONDS, HEARTBEAT_TTL_MULTIPLIER * max(1, sweep_interval_seconds))


def instance_id() -> str:
    """``<hostname>-<pid>``: readable in a list, unique per process on a host."""
    return "%s-%d" % (socket.gethostname().replace(":", "-"), os.getpid())


def heartbeat_payload(
    *,
    metrics: WorkerMetrics,
    instance: str,
    interval_seconds: int,
    env: str,
    provider_llm: str,
    provider_render: str,
    sentry_enabled: bool,
    draining: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The document a worker writes. Plain JSON, sorted by the writer.

    Nothing here is secret or personal: names, counters and timestamps only. The
    provider names and the Sentry flag are here so the ops page can show what each
    *worker* actually booted with, not what the API's own settings say — a render
    worker left on ``mock`` while the API says ``stability`` is a real drift.
    """
    at = now or datetime.now(UTC)
    return {
        "worker": metrics.worker,
        "queue": metrics.queue,
        "instance": instance,
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "env": env,
        "release": os.environ.get("APP_VERSION") or os.environ.get("GIT_SHA") or None,
        "sentAt": at.isoformat(),
        "intervalSeconds": interval_seconds,
        "uptimeSeconds": metrics.uptime_seconds,
        "draining": draining,
        "inFlight": metrics.in_flight,
        "concurrency": metrics.concurrency,
        "jobsReceived": metrics.jobs_received,
        "jobsSucceeded": metrics.jobs_succeeded,
        "jobsFailed": metrics.jobs_failed,
        "jobsRetried": metrics.jobs_retried,
        "jobsDeadLettered": metrics.jobs_dead_lettered,
        "p50DurationMs": metrics.p50_duration_ms,
        "p95DurationMs": metrics.p95_duration_ms,
        "queueDepth": {
            "pending": metrics.queue_depth_pending,
            "delayed": metrics.queue_depth_delayed,
            "processing": metrics.queue_depth_processing,
            "dead": metrics.queue_depth_dead,
        },
        "providerLlm": provider_llm,
        "providerRender": provider_render,
        "sentry": "on" if sentry_enabled else "off",
    }


__all__ = [
    "EXPECTED_WORKERS",
    "HEARTBEAT_MIN_TTL_SECONDS",
    "HEARTBEAT_TTL_MULTIPLIER",
    "WORKER_HEARTBEAT_PREFIX",
    "heartbeat_key",
    "heartbeat_payload",
    "heartbeat_ttl_seconds",
    "instance_id",
]
