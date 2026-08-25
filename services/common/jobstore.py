"""Who writes ``solver_jobs.status`` — and why it is not the worker.

The obvious design is for a worker to open a database session and update its own job
row. This package deliberately does not, for three reasons:

1. **Statelessness (§18).** "Architecture keeps workers stateless for later k8s move."
   A worker that owns a Postgres pool owns migrations, tenancy context, and the API's
   dependency set. The render worker would then run third-party model code in the same
   process as a credentialled database connection — a bad neighbourhood (§13).
2. **One writer per row.** The API already owns ``solver_jobs`` / ``render_jobs``
   through the tenancy repository layer. Two writers means two truths.
3. **Durability is already solved.** Every lifecycle transition is appended to the
   ``garh:events:jobs`` Redis Stream by :class:`~services.common.progress.ProgressReporter`
   (see its module docstring for the exact fields). A stream is replayable and has
   consumer groups, so the API can persist transitions with at-least-once delivery
   even across an API restart — which pub/sub alone cannot promise.

**The API side of the contract** (owned by the api-routes agent):

* consume ``garh:events:jobs`` with a consumer group, e.g. ``XREADGROUP GROUP garh-api``;
* map ``type`` → repository call:
  ``started`` → ``set_progress(0)``, ``succeeded`` → ``succeed(...)``,
  ``failed``/``dead_lettered`` → ``fail(problem.message)``, ``cancelled`` → ``cancel()``;
* the payload of ``succeeded`` carries the handler's result — ``options`` for solver
  jobs, ``outputUrl`` for renders — under ``event.data``;
* ``XACK`` only after the transaction commits.

If that consumer is not wanted, :class:`JobStatusSink` is the alternative seam: the
API can implement it against its own repositories and pass an instance to
:class:`~services.common.runtime.Worker`, keeping the ``garh_api`` import on the API
side of the boundary where it belongs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from services.common.envelope import BlobRef, JobEnvelope
from services.common.logging import get_logger

log = get_logger("jobstore")


@dataclass
class JobResult:
    """What a handler returns on success.

    ``data`` is published verbatim in the ``succeeded`` event and is what the API
    persists — ``{"options": [...]}`` for the solver, ``{"outputUrl": "..."}`` for a
    render, ``{"sheets": [...]}`` for drawings.
    """

    data: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, BlobRef] = field(default_factory=dict)
    message: str | None = None

    def to_event_data(self) -> dict[str, Any]:
        payload = dict(self.data)
        if self.outputs:
            payload["outputs"] = {
                name: ref.redacted() for name, ref in sorted(self.outputs.items())
            }
        return payload


class JobStatusSink(Protocol):
    """Optional hook for persisting job lifecycle transitions.

    Implementations must be **non-fatal**: a sink error is logged and the job's own
    outcome stands. The queue, not the sink, is the source of truth for retries.
    """

    async def on_started(self, envelope: JobEnvelope) -> None: ...

    async def on_progress(self, envelope: JobEnvelope, percent: int) -> None: ...

    async def on_succeeded(self, envelope: JobEnvelope, result: JobResult) -> None: ...

    async def on_failed(self, envelope: JobEnvelope, problem: dict[str, Any]) -> None: ...

    async def on_cancelled(self, envelope: JobEnvelope) -> None: ...


class NullJobStatusSink:
    """Default sink: does nothing, because the event stream already did the work."""

    async def on_started(self, envelope: JobEnvelope) -> None:
        return None

    async def on_progress(self, envelope: JobEnvelope, percent: int) -> None:
        return None

    async def on_succeeded(self, envelope: JobEnvelope, result: JobResult) -> None:
        return None

    async def on_failed(self, envelope: JobEnvelope, problem: dict[str, Any]) -> None:
        return None

    async def on_cancelled(self, envelope: JobEnvelope) -> None:
        return None


class RecordingJobStatusSink:
    """Test double that remembers every call, in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    async def on_started(self, envelope: JobEnvelope) -> None:
        self.calls.append(("started", envelope.job_id, None))

    async def on_progress(self, envelope: JobEnvelope, percent: int) -> None:
        self.calls.append(("progress", envelope.job_id, percent))

    async def on_succeeded(self, envelope: JobEnvelope, result: JobResult) -> None:
        self.calls.append(("succeeded", envelope.job_id, result))

    async def on_failed(self, envelope: JobEnvelope, problem: dict[str, Any]) -> None:
        self.calls.append(("failed", envelope.job_id, problem))

    async def on_cancelled(self, envelope: JobEnvelope) -> None:
        self.calls.append(("cancelled", envelope.job_id, None))


__all__ = ["JobResult", "JobStatusSink", "NullJobStatusSink", "RecordingJobStatusSink"]
