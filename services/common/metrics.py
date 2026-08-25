"""Worker metrics (playbook §18: "worker queue-depth metric").

No Prometheus client dependency: the exposition format is a dozen lines of text and
this keeps the mock-path install light. Everything here is process-local and cheap;
the only value read from Redis is queue depth, refreshed by the runtime's sweep loop.

Exposed at ``GET /metrics`` on the worker's health port (``services/common/health.py``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class WorkerMetrics:
    """Counters and gauges for one worker process."""

    worker: str
    queue: str

    jobs_received: int = 0
    jobs_succeeded: int = 0
    jobs_failed: int = 0
    jobs_retried: int = 0
    jobs_cancelled: int = 0
    jobs_dead_lettered: int = 0
    jobs_released: int = 0
    jobs_invalid: int = 0

    in_flight: int = 0
    concurrency: int = 1

    queue_depth_pending: int = 0
    queue_depth_delayed: int = 0
    queue_depth_processing: int = 0
    queue_depth_dead: int = 0

    #: Duration of the last N completed jobs, in ms. Bounded — this is a metric, not
    #: a history: a 60s solver budget is only meaningful against recent runs anyway.
    recent_durations_ms: list[int] = field(default_factory=list)
    max_recent: int = 50

    started_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    last_job_finished_at_ms: int | None = None

    def record_duration(self, duration_ms: int) -> None:
        self.recent_durations_ms.append(max(0, duration_ms))
        if len(self.recent_durations_ms) > self.max_recent:
            del self.recent_durations_ms[: len(self.recent_durations_ms) - self.max_recent]
        self.last_job_finished_at_ms = int(time.time() * 1000)

    @property
    def uptime_seconds(self) -> int:
        return max(0, (int(time.time() * 1000) - self.started_at_ms) // 1000)

    @property
    def p50_duration_ms(self) -> int:
        return _percentile(self.recent_durations_ms, 50)

    @property
    def p95_duration_ms(self) -> int:
        return _percentile(self.recent_durations_ms, 95)

    def snapshot(self) -> dict[str, int | str | None]:
        """JSON-able view — used by ``/healthz`` and by tests."""
        return {
            "worker": self.worker,
            "queue": self.queue,
            "uptimeSeconds": self.uptime_seconds,
            "inFlight": self.in_flight,
            "concurrency": self.concurrency,
            "jobsReceived": self.jobs_received,
            "jobsSucceeded": self.jobs_succeeded,
            "jobsFailed": self.jobs_failed,
            "jobsRetried": self.jobs_retried,
            "jobsCancelled": self.jobs_cancelled,
            "jobsDeadLettered": self.jobs_dead_lettered,
            "jobsReleased": self.jobs_released,
            "jobsInvalid": self.jobs_invalid,
            "queueDepthPending": self.queue_depth_pending,
            "queueDepthDelayed": self.queue_depth_delayed,
            "queueDepthProcessing": self.queue_depth_processing,
            "queueDepthDead": self.queue_depth_dead,
            "p50DurationMs": self.p50_duration_ms,
            "p95DurationMs": self.p95_duration_ms,
            "lastJobFinishedAtMs": self.last_job_finished_at_ms,
        }

    def render_prometheus(self) -> str:
        """Prometheus text exposition (v0.0.4). Labels: ``worker``, ``queue``."""
        labels = 'worker="%s",queue="%s"' % (_escape(self.worker), _escape(self.queue))
        lines: list[str] = []

        def metric(name: str, kind: str, help_text: str, value: int) -> None:
            lines.append("# HELP %s %s" % (name, help_text))
            lines.append("# TYPE %s %s" % (name, kind))
            lines.append("%s{%s} %d" % (name, labels, value))

        metric(
            "garh_worker_queue_depth",
            "gauge",
            "Jobs waiting on this queue (playbook 18).",
            self.queue_depth_pending + self.queue_depth_delayed,
        )
        metric(
            "garh_worker_queue_pending",
            "gauge",
            "Jobs on the pending list.",
            self.queue_depth_pending,
        )
        metric(
            "garh_worker_queue_delayed",
            "gauge",
            "Jobs waiting out a retry backoff.",
            self.queue_depth_delayed,
        )
        metric(
            "garh_worker_queue_processing",
            "gauge",
            "Jobs leased by some worker right now.",
            self.queue_depth_processing,
        )
        metric(
            "garh_worker_queue_dead",
            "gauge",
            "Dead-lettered jobs retained for inspection.",
            self.queue_depth_dead,
        )
        metric("garh_worker_in_flight", "gauge", "Jobs running in this process.", self.in_flight)
        metric(
            "garh_worker_concurrency", "gauge", "Configured parallel slots.", self.concurrency
        )
        metric("garh_worker_uptime_seconds", "gauge", "Process uptime.", self.uptime_seconds)
        metric(
            "garh_worker_jobs_received_total", "counter", "Jobs reserved.", self.jobs_received
        )
        metric(
            "garh_worker_jobs_succeeded_total",
            "counter",
            "Jobs that completed successfully.",
            self.jobs_succeeded,
        )
        metric(
            "garh_worker_jobs_failed_total",
            "counter",
            "Jobs that failed permanently.",
            self.jobs_failed,
        )
        metric(
            "garh_worker_jobs_retried_total",
            "counter",
            "Jobs rescheduled with backoff.",
            self.jobs_retried,
        )
        metric(
            "garh_worker_jobs_cancelled_total",
            "counter",
            "Jobs cancelled by a user.",
            self.jobs_cancelled,
        )
        metric(
            "garh_worker_jobs_dead_lettered_total",
            "counter",
            "Jobs that exhausted their retries.",
            self.jobs_dead_lettered,
        )
        metric(
            "garh_worker_jobs_invalid_total",
            "counter",
            "Envelopes rejected as malformed.",
            self.jobs_invalid,
        )
        metric(
            "garh_worker_job_duration_p50_ms",
            "gauge",
            "Median duration of recent jobs.",
            self.p50_duration_ms,
        )
        metric(
            "garh_worker_job_duration_p95_ms",
            "gauge",
            "95th percentile duration of recent jobs.",
            self.p95_duration_ms,
        )
        return "\n".join(lines) + "\n"


def _percentile(values: list[int], pct: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    # Nearest-rank: deterministic, no interpolation, no float surprises.
    rank = max(1, (pct * len(ordered) + 99) // 100)
    return ordered[min(rank, len(ordered)) - 1]


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


__all__ = ["WorkerMetrics"]
