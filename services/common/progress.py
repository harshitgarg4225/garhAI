"""Real progress events (playbook §15, §18).

§15 is explicit: the generation theater — "Placing staircase… packing rooms… checking
BBMP setbacks…" — is **driven by real worker events**, never a fake bar. So there is
exactly one way for a worker to say something happened, and it is
:meth:`ProgressReporter.stage` / :meth:`ProgressReporter.progress`. Nothing in this
package interpolates, animates or invents a percentage.

Wire contract (the API's SSE endpoint reads these):

===================================  =======  ==============================================
key                                  type     meaning
===================================  =======  ==============================================
``garh:progress:{jobId}``            pubsub   live events, one JSON object per message
``garh:progress:{jobId}:log``        list     ring buffer of the same events, newest LAST,
                                              so a client that connects late replays them
``garh:progress:{jobId}:seq``        string   monotonic counter → the SSE ``id:`` field,
                                              which makes ``Last-Event-ID`` resume work
``garh:events:jobs``                 stream   lifecycle transitions for durable persistence
===================================  =======  ==============================================

The Redis Stream is the important one for correctness: pub/sub is fire-and-forget, so
a job that succeeds while the API is restarting would otherwise lose its result. The
stream is the durable record the API consumes to write ``solver_jobs`` /
``render_jobs`` rows (see ``services/common/jobstore.py``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from services.common.envelope import JobEnvelope, now_ms
from services.common.logging import get_logger
from services.common.queue import RedisLike

log = get_logger("progress")

PROGRESS_CHANNEL_TEMPLATE = "garh:progress:%s"
PROGRESS_LOG_TEMPLATE = "garh:progress:%s:log"
PROGRESS_SEQ_TEMPLATE = "garh:progress:%s:seq"

EventType = Literal[
    "queued",
    "started",
    "stage",
    "progress",
    "artifact",
    "warning",
    "succeeded",
    "failed",
    "cancelled",
    "retrying",
    "dead_lettered",
]

#: Terminal event types — after one of these, no further event for that job is valid.
TERMINAL_EVENT_TYPES: frozenset[str] = frozenset(
    {"succeeded", "failed", "cancelled", "dead_lettered"}
)


@dataclass(frozen=True)
class ProgressEvent:
    """One honest thing that happened.

    ``percent`` is optional and must be omitted when the worker does not genuinely
    know — a missing percent renders as an indeterminate state, which is truthful;
    a made-up one is the thing §15 forbids.
    """

    job_id: str
    type: EventType
    seq: int = 0
    ts_ms: int = field(default_factory=now_ms)
    stage: str | None = None
    message: str | None = None
    percent: int | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schemaVersion": 1,
            "jobId": self.job_id,
            "type": self.type,
            "seq": self.seq,
            "tsMs": self.ts_ms,
        }
        if self.stage is not None:
            out["stage"] = self.stage
        if self.message is not None:
            out["message"] = self.message
        if self.percent is not None:
            out["percent"] = self.percent
        if self.data:
            out["data"] = self.data
        return out

    def encode(self) -> str:
        return json.dumps(
            self.to_json(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    @classmethod
    def decode(cls, raw: str | bytes) -> ProgressEvent:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        data = json.loads(text)
        percent = data.get("percent")
        payload = data.get("data")
        return cls(
            job_id=str(data["jobId"]),
            type=data["type"],
            seq=int(data.get("seq", 0)),
            ts_ms=int(data.get("tsMs", 0)),
            stage=data.get("stage"),
            message=data.get("message"),
            percent=int(percent) if isinstance(percent, int) else None,
            data=payload if isinstance(payload, dict) else {},
        )


class ProgressReporter:
    """Publishes events for ONE job. Handlers receive one of these, nothing else.

    Every method is fire-and-forget from the handler's point of view: a Redis blip
    must not fail a job that is otherwise fine, so publish errors are logged and
    swallowed. The durable record is the job's terminal event, which the runtime
    publishes on the stream and retries.
    """

    def __init__(
        self,
        redis: RedisLike,
        envelope: JobEnvelope,
        *,
        log_maxlen: int = 200,
        ttl_seconds: int = 3_600,
        events_stream: str = "garh:events:jobs",
        events_maxlen: int = 10_000,
    ) -> None:
        self.redis = redis
        self.envelope = envelope
        self.job_id = envelope.job_id
        self.log_maxlen = log_maxlen
        self.ttl_seconds = ttl_seconds
        self.events_stream = events_stream
        self.events_maxlen = events_maxlen
        self.channel = PROGRESS_CHANNEL_TEMPLATE % self.job_id
        self.log_key = PROGRESS_LOG_TEMPLATE % self.job_id
        self.seq_key = PROGRESS_SEQ_TEMPLATE % self.job_id
        self._last_percent: int | None = None

    # -- the API handlers use -------------------------------------------
    async def started(self, message: str | None = None) -> ProgressEvent:
        return await self.emit("started", message=message, percent=0)

    async def stage(
        self,
        name: str,
        message: str,
        *,
        percent: int | None = None,
        **data: Any,
    ) -> ProgressEvent:
        """A named pipeline stage began. This is what the staged UI copy renders."""
        return await self.emit("stage", stage=name, message=message, percent=percent, **data)

    async def progress(
        self, percent: int, *, message: str | None = None, **data: Any
    ) -> ProgressEvent:
        """Fractional progress WITHIN the current stage. Monotonic, never invented."""
        return await self.emit("progress", message=message, percent=percent, **data)

    async def artifact(self, name: str, **data: Any) -> ProgressEvent:
        """A partial result exists — e.g. a plan silhouette that passed the gates.

        §15: "plan silhouettes appearing as they pass gates" is this event.
        """
        return await self.emit("artifact", message=None, artifactName=name, **data)

    async def warning(self, message: str, **data: Any) -> ProgressEvent:
        """Something is off but the job continues. Shown, not hidden."""
        return await self.emit("warning", message=message, **data)

    async def succeeded(self, *, message: str | None = None, **data: Any) -> ProgressEvent:
        return await self.emit("succeeded", message=message, percent=100, **data)

    async def failed(self, problem: dict[str, Any]) -> ProgressEvent:
        """Terminal failure. ``problem`` is ``{code, message, action}`` (§11)."""
        return await self.emit("failed", message=str(problem.get("message", "")), **problem)

    async def cancelled(self) -> ProgressEvent:
        return await self.emit("cancelled", message="Cancelled.")

    async def retrying(self, *, attempt: int, delay_seconds: int, reason: str) -> ProgressEvent:
        """Golden rule 9: "the UI shows job state honestly" — including retries."""
        return await self.emit(
            "retrying",
            message="Hit a snag — retrying in %ds." % delay_seconds,
            attempt=attempt,
            delaySeconds=delay_seconds,
            reason=reason[:300],
        )

    async def dead_lettered(self, problem: dict[str, Any]) -> ProgressEvent:
        return await self.emit(
            "dead_lettered", message=str(problem.get("message", "")), **problem
        )

    # -- plumbing --------------------------------------------------------
    async def emit(
        self,
        event_type: EventType,
        *,
        stage: str | None = None,
        message: str | None = None,
        percent: int | None = None,
        **data: Any,
    ) -> ProgressEvent:
        clamped = None if percent is None else max(0, min(100, int(percent)))
        if clamped is not None and self._last_percent is not None:
            # Progress that goes backwards reads as a bug to a user watching it.
            clamped = max(clamped, self._last_percent)
        if clamped is not None:
            self._last_percent = clamped

        event = ProgressEvent(
            job_id=self.job_id,
            type=event_type,
            seq=await self._next_seq(),
            stage=stage,
            message=message,
            percent=clamped,
            data={key: value for key, value in data.items() if value is not None},
        )
        await self._publish(event)
        return event

    async def _next_seq(self) -> int:
        try:
            value = await self.redis.incr(self.seq_key)
            await self.redis.expire(self.seq_key, self.ttl_seconds)
            return int(value)
        except Exception as exc:  # noqa: BLE001 - telemetry must never fail a job
            log.warning("progress.seq_failed", job_id=self.job_id, error=str(exc))
            return 0

    async def _publish(self, event: ProgressEvent) -> None:
        raw = event.encode()
        try:
            await self.redis.rpush(self.log_key, raw)
            await self.redis.ltrim(self.log_key, -self.log_maxlen, -1)
            await self.redis.expire(self.log_key, self.ttl_seconds)
            await self.redis.publish(self.channel, raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("progress.publish_failed", job_id=self.job_id, error=str(exc))

        if event.type in TERMINAL_EVENT_TYPES or event.type == "started":
            await self._append_lifecycle(event)

    async def _append_lifecycle(self, event: ProgressEvent) -> None:
        """Durable record of a lifecycle transition (see module docstring)."""
        fields = {
            "jobId": self.job_id,
            "kind": self.envelope.kind,
            "firmId": self.envelope.firm_id,
            "projectId": self.envelope.project_id or "",
            "designVersionId": self.envelope.design_version_id or "",
            "type": event.type,
            "tsMs": str(event.ts_ms),
            "attempt": str(self.envelope.attempt),
            "event": event.encode(),
        }
        try:
            await self.redis.xadd(
                self.events_stream, fields, maxlen=self.events_maxlen, approximate=True
            )
        except Exception as exc:  # noqa: BLE001
            log.error("progress.lifecycle_failed", job_id=self.job_id, error=str(exc))


class NullProgressReporter(ProgressReporter):
    """No-op reporter for unit tests that call a handler directly.

    Records everything it was told so a test can assert the staged messages are real
    and in order — which is exactly the §15 property worth testing.
    """

    def __init__(self, envelope: JobEnvelope) -> None:  # noqa: D107 - see class docstring
        self.envelope = envelope
        self.job_id = envelope.job_id
        self.events: list[ProgressEvent] = []
        self._last_percent = None
        self.channel = PROGRESS_CHANNEL_TEMPLATE % self.job_id
        self.log_key = PROGRESS_LOG_TEMPLATE % self.job_id
        self.seq_key = PROGRESS_SEQ_TEMPLATE % self.job_id
        self.log_maxlen = 200
        self.ttl_seconds = 0
        self.events_stream = ""
        self.events_maxlen = 0

    async def emit(
        self,
        event_type: EventType,
        *,
        stage: str | None = None,
        message: str | None = None,
        percent: int | None = None,
        **data: Any,
    ) -> ProgressEvent:
        clamped = None if percent is None else max(0, min(100, int(percent)))
        if clamped is not None and self._last_percent is not None:
            clamped = max(clamped, self._last_percent)
        if clamped is not None:
            self._last_percent = clamped
        event = ProgressEvent(
            job_id=self.job_id,
            type=event_type,
            seq=len(self.events) + 1,
            stage=stage,
            message=message,
            percent=clamped,
            data={key: value for key, value in data.items() if value is not None},
        )
        self.events.append(event)
        return event


__all__ = [
    "PROGRESS_CHANNEL_TEMPLATE",
    "PROGRESS_LOG_TEMPLATE",
    "PROGRESS_SEQ_TEMPLATE",
    "TERMINAL_EVENT_TYPES",
    "EventType",
    "NullProgressReporter",
    "ProgressEvent",
    "ProgressReporter",
]
