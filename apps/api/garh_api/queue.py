"""The Redis contract shared by the API and the three workers (playbook §9, §11, §18).

This module is the **API side** of a cross-process contract whose worker side is
``services/common/{envelope,queue,progress}.py``. Those files say, in their own words,
"THIS FILE IS A CROSS-PROCESS CONTRACT. The API enqueues exactly this JSON; workers
refuse anything else." Everything here is therefore a faithful mirror of them — same
key names, same field names, same JSON encoding — and the docstrings below name the
worker-side symbol each part corresponds to so drift is visible in review.

Nothing here imports FastAPI, SQLAlchemy or a repository, so a script or a test can use
it without dragging the HTTP layer in.

Queue topology
--------------

Three durable work queues, named by ``Settings`` (``QUEUE_SOLVER`` / ``QUEUE_RENDER`` /
``QUEUE_DRAWINGS``), defaulting to::

    garh:queue:solver      services/solver     — CP-SAT layout jobs (§5)
    garh:queue:render      services/render     — provider renders (§9)
    garh:queue:drawings    services/drawings   — sheet sets and exports (§7)

For a queue base name ``Q`` (mirrors ``services.common.queue.QueueKeys``):

===============  ======  ==========================================================
key              type    meaning
===============  ======  ==========================================================
``Q``            list    pending envelopes. Producers ``LPUSH``; the worker pops
                         from the right, so the list is FIFO.
``Q:processing`` list    envelopes currently leased by a worker.
``Q:inflight``   hash    ``jobId`` → the exact envelope string in ``Q:processing``.
``Q:leases``     zset    ``jobId`` → lease deadline (ms). Expired ⇒ redelivered.
``Q:delayed``    zset    envelope → earliest delivery time (ms). Retry backoff.
``Q:dead``       list    dead-lettered envelopes, capped.
===============  ======  ==========================================================

**The API only ever touches ``Q`` and ``Q:delayed``.** Leases, retries and
dead-lettering belong to the worker runtime; an API that reached into them would be a
second scheduler, and two schedulers means two truths about what is running.

Progress
--------

Mirrors ``services.common.progress``::

    channel   garh:progress:<jobId>          # pub/sub, live listeners
    backlog   garh:progress:<jobId>:log      # capped list, TTL, late joiners
    sequence  garh:progress:<jobId>:seq      # INCR — the SSE `id:` field

Note the key shape: the job id alone, **not** ``<kind>:<jobId>``. Job ids are UUIDs, so
they are already globally unique, and the SSE endpoint knows a job's kind from its
database row rather than from a key it parsed.

Publishing goes to both the channel and the backlog, always. Pure pub/sub loses
everything that happened before the browser opened the SSE stream — exactly the window
in which "Placing staircase…" happens. The backlog makes a reconnect with
``Last-Event-ID`` lossless up to :data:`PROGRESS_BACKLOG_MAX` events.

Durable lifecycle stream
------------------------

``garh:events:jobs`` is a Redis Stream carrying every lifecycle transition
(``started``, ``succeeded``, ``failed``, ``cancelled``, ``dead_lettered``). Pub/sub is
fire-and-forget, so a job that finishes while the API is restarting would otherwise
lose its result and sit in ``running`` forever. The API consumes this stream with a
consumer group (:data:`JOB_EVENTS_GROUP`) and writes ``solver_jobs`` / ``render_jobs``
rows from it — see ``garh_api.routers.jobs.consume_job_events``. Workers hold no
database connection at all (``services/common/jobstore.py`` explains why), so this
stream is the *only* path from "the work finished" to "the row says so".

Cancellation
------------

``garh:job:<jobId>:cancel`` is a short-lived key. The API sets it; workers check it
between stages and stop cooperatively. Cancellation is advisory by design: a worker
mid-CP-SAT-solve finishes its slice rather than leaving half-written state.

Export jobs
-----------

DECISION (needs a ``DECISIONS.md`` row): playbook §2 has no ``export_jobs`` table, but
§11 has ``POST /projects/:id/export → job → signed download URL``. Rather than add a
table outside the schema owner's remit, an export job lives in Redis at
``garh:export:<firmId>:<jobId>`` with a 24h TTL, and its durable facts land in
``credit_events`` (kind ``export``) and ``audit_log`` (``export.created``). An export is
an ephemeral producer of a download URL; nothing in the product reads export history
later. If that changes, promote it to a table.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Optional

from garh_api.config import Settings, get_settings
from garh_api.logging import get_logger

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Versions — bump in lockstep with services/common; workers assert on these.
# ---------------------------------------------------------------------------

#: Mirrors ``services.common.envelope.ENVELOPE_SCHEMA_VERSION``. A worker seeing a
#: higher number fails the job loudly rather than guessing at the fields.
ENVELOPE_SCHEMA_VERSION = 1

#: Mirrors the ``schemaVersion`` that ``services.common.progress.ProgressEvent`` emits.
PROGRESS_EVENT_VERSION = 1


def now_ms() -> int:
    """Wall-clock milliseconds — the one place time enters the queue layer.

    Mirrors ``services.common.envelope.now_ms``. Integer ms everywhere: no float ever
    crosses this boundary, exactly as no float ever crosses the geometry boundary.
    """
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Job kinds (``services.common.envelope.JOB_KINDS_BY_WORKER``)
# ---------------------------------------------------------------------------

#: One worker process / queue per key.
WORKER_SOLVER = "solver"
WORKER_RENDER = "render"
WORKER_DRAWINGS = "drawings"

WORKERS: tuple[str, ...] = (WORKER_SOLVER, WORKER_RENDER, WORKER_DRAWINGS)

#: Job kinds — the envelope's ``kind`` field. The worker dispatches on this.
#: **Byte-identical to ``services.common.envelope.JOB_KINDS_BY_WORKER``.**
JOB_KINDS_BY_WORKER: dict[str, tuple[str, ...]] = {
    WORKER_SOLVER: ("solver.generate", "solver.resolve"),
    WORKER_RENDER: ("render.image",),
    WORKER_DRAWINGS: ("drawings.generate_sheets", "drawings.export", "drawings.import_dxf"),
}

JOB_SOLVER_GENERATE = "solver.generate"
JOB_SOLVER_RESOLVE = "solver.resolve"
JOB_RENDER_IMAGE = "render.image"
JOB_DRAWINGS_GENERATE_SHEETS = "drawings.generate_sheets"
JOB_DRAWINGS_EXPORT = "drawings.export"
JOB_DRAWINGS_IMPORT_DXF = "drawings.import_dxf"

#: Flat tuple of all known kinds.
JOB_KINDS: tuple[str, ...] = tuple(
    kind for kinds in JOB_KINDS_BY_WORKER.values() for kind in kinds
)

#: Export kinds accepted by ``POST /projects/:id/export`` (§11).
EXPORT_KINDS: tuple[str, ...] = ("pdf-set", "dxf", "gltf", "png-pack")

#: Job lifecycle as persisted, identical to ``models.JOB_STATUSES`` (the DB CHECK is
#: the authority). Worker *events* use a richer vocabulary — see :data:`EVENT_TYPES`
#: and :func:`status_for_event` for the mapping.
JOB_STATUSES: tuple[str, ...] = ("queued", "running", "succeeded", "failed", "cancelled")
JOB_TERMINAL_STATUSES: tuple[str, ...] = ("succeeded", "failed", "cancelled")

#: Worker event vocabulary (``services.common.progress.EventType``).
EVENT_TYPES: tuple[str, ...] = (
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
)

#: After one of these, no further event for that job is valid.
TERMINAL_EVENT_TYPES: frozenset[str] = frozenset(
    {"succeeded", "failed", "cancelled", "dead_lettered"}
)

#: Event type → persisted job status.
#:
#: ``dead_lettered`` maps to ``failed``: from the architect's point of view a job that
#: exhausted its retries has failed, and inventing a sixth status the DB CHECK would
#: reject helps nobody. ``retrying`` maps to ``running`` because the job genuinely is
#: still in flight — reporting it as failed and then un-failing it is the kind of
#: dishonest state golden rule 9 exists to prevent.
_EVENT_STATUS: dict[str, str] = {
    "queued": "queued",
    "started": "running",
    "stage": "running",
    "progress": "running",
    "artifact": "running",
    "warning": "running",
    "retrying": "running",
    "succeeded": "succeeded",
    "failed": "failed",
    "dead_lettered": "failed",
    "cancelled": "cancelled",
}


def status_for_event(event_type: str) -> str:
    """Persisted job status implied by a worker event type."""
    return _EVENT_STATUS.get(event_type, "running")


#: Default retry ceiling. Mirrors ``services.common.envelope.JobEnvelope.max_attempts``;
#: the worker runtime owns the backoff schedule.
DEFAULT_MAX_ATTEMPTS = 4

#: How many progress events are retained per job for late SSE joiners. Mirrors
#: ``ProgressReporter(log_maxlen=...)``, which defaults to 200.
PROGRESS_BACKLOG_MAX = 200

#: How long a finished job's progress backlog stays readable (seconds).
PROGRESS_BACKLOG_TTL_SECONDS = 3600

#: How long a cancellation request stays live for a worker to notice (seconds).
#: Mirrors ``services.common.queue.CANCEL_TTL_SECONDS``.
CANCEL_TTL_SECONDS = 86_400

#: How long an export job's Redis record survives (seconds).
EXPORT_JOB_TTL_SECONDS = 24 * 3600

#: How long an ``Idempotency-Key`` result is replayable (seconds, §11).
IDEMPOTENCY_TTL_SECONDS = 24 * 3600

#: Durable lifecycle stream and the API's consumer group on it.
JOB_EVENTS_STREAM = "garh:events:jobs"
JOB_EVENTS_GROUP = "garh-api"
JOB_EVENTS_MAXLEN = 10_000


# ---------------------------------------------------------------------------
# Key names — every Redis key the API touches is built here
# ---------------------------------------------------------------------------


def worker_for_kind(kind: str) -> str:
    """Which worker owns a job kind (``solver`` / ``render`` / ``drawings``)."""
    for worker, kinds in JOB_KINDS_BY_WORKER.items():
        if kind in kinds:
            return worker
    raise ValueError(
        "Unknown job kind %r; expected one of %s." % (kind, ", ".join(JOB_KINDS))
    )


def queue_name(worker: str, settings: Optional[Settings] = None) -> str:
    """The work-queue list key for a worker."""
    cfg = settings or get_settings()
    if worker == WORKER_SOLVER:
        return cfg.queue_solver
    if worker == WORKER_RENDER:
        return cfg.queue_render
    if worker == WORKER_DRAWINGS:
        return cfg.queue_drawings
    raise ValueError("Unknown worker %r; expected one of %s." % (worker, ", ".join(WORKERS)))


def queue_for_kind(kind: str, settings: Optional[Settings] = None) -> str:
    return queue_name(worker_for_kind(kind), settings)


def delayed_queue(worker: str, settings: Optional[Settings] = None) -> str:
    return "%s:delayed" % queue_name(worker, settings)


def processing_queue(worker: str, settings: Optional[Settings] = None) -> str:
    return "%s:processing" % queue_name(worker, settings)


def dead_letter_queue(worker: str, settings: Optional[Settings] = None) -> str:
    return "%s:dead" % queue_name(worker, settings)


def progress_channel(job_id: Any) -> str:
    return "garh:progress:%s" % job_id


def progress_log_key(job_id: Any) -> str:
    return "garh:progress:%s:log" % job_id


def progress_seq_key(job_id: Any) -> str:
    return "garh:progress:%s:seq" % job_id


def cancel_key(job_id: Any) -> str:
    return "garh:job:%s:cancel" % job_id


def export_job_key(firm_id: Any, job_id: Any) -> str:
    return "garh:export:%s:%s" % (firm_id, job_id)


def idempotency_key(firm_id: Any, scope: str, key: str) -> str:
    """``Idempotency-Key`` storage slot. Scoped per firm so keys cannot collide across
    tenants, and per route ``scope`` so the same key on two endpoints is two records."""
    return "garh:idem:%s:%s:%s" % (firm_id, scope, key)


# ---------------------------------------------------------------------------
# Blob references (``services.common.envelope.BlobRef``)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlobRef:
    """A pointer to one binary input or output of a job.

    Exactly one access path is used at a time: ``get_url``/``put_url`` (presigned HTTPS
    — the worker image holds no S3 credentials, §13), ``inline_base64`` (small fixtures
    only), or ``path`` (developer scripts and golden runs).
    """

    get_url: Optional[str] = None
    put_url: Optional[str] = None
    path: Optional[str] = None
    inline_base64: Optional[str] = None
    key: Optional[str] = None
    content_type: Optional[str] = None
    sha256: Optional[str] = None
    size_bytes: Optional[int] = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for wire, value in (
            ("getUrl", self.get_url),
            ("putUrl", self.put_url),
            ("path", self.path),
            ("inlineBase64", self.inline_base64),
            ("key", self.key),
            ("contentType", self.content_type),
            ("sha256", self.sha256),
            ("sizeBytes", self.size_bytes),
        ):
            if value is not None:
                out[wire] = value
        return out

    def redacted(self) -> dict[str, Any]:
        """Log-safe form: a presigned URL is a bearer credential, so show its shape."""
        out: dict[str, Any] = {}
        if self.key:
            out["key"] = self.key
        if self.content_type:
            out["contentType"] = self.content_type
        if self.size_bytes is not None:
            out["sizeBytes"] = self.size_bytes
        out["access"] = (
            "presigned"
            if (self.get_url or self.put_url)
            else "inline"
            if self.inline_base64 is not None
            else "path"
            if self.path
            else "none"
        )
        return out


# ---------------------------------------------------------------------------
# The job envelope (``services.common.envelope.JobEnvelope``)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobEnvelope:
    """One unit of work on a queue. Serialised as canonical JSON, one list entry each.

    Field notes that matter:

    * ``firm_id`` is how a worker attributes its output; the worker never opens a
      database session, so this is provenance carried in the message rather than a
      tenant context it could widen.
    * ``job_id`` is the primary key of the ``solver_jobs`` / ``render_jobs`` row, or the
      Redis export-job id. It is also the progress channel suffix.
    * ``payload`` is job-specific and carries **integer millimetres only**. It never
      carries the folded model — the worker reads that through a presigned asset or
      re-derives it — so the envelope stays small and re-enqueueable.
    * ``request_id`` propagates the API's request id into worker logs (§18).
    """

    job_id: str
    kind: str
    firm_id: str
    queue: str = ""
    project_id: Optional[str] = None
    design_version_id: Optional[str] = None
    actor_user_id: Optional[str] = None
    request_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    attempt: int = 1
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    enqueued_at_ms: int = field(default_factory=now_ms)
    #: Delay-until, honoured by the retry scheduler. 0 = ready immediately.
    not_before_ms: int = 0
    #: Hard wall-clock deadline; past it the runtime fails the job rather than starting
    #: work nobody is waiting for.
    deadline_ms: Optional[int] = None
    payload: dict[str, Any] = field(default_factory=dict)
    payload_ref: Optional[BlobRef] = None
    assets: dict[str, BlobRef] = field(default_factory=dict)
    outputs: dict[str, BlobRef] = field(default_factory=dict)
    schema_version: int = ENVELOPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.kind not in JOB_KINDS:
            raise ValueError(
                "kind must be one of %s, got %r." % (", ".join(JOB_KINDS), self.kind)
            )
        if not self.firm_id:
            raise ValueError("A job envelope must carry firmId — it is the provenance.")
        if not self.job_id:
            raise ValueError("A job envelope must carry jobId.")
        if not self.queue:
            # `queue` is part of the wire shape; fill it from the kind rather than
            # shipping an empty string the worker would have to interpret.
            object.__setattr__(self, "queue", queue_for_kind(self.kind))

    @property
    def worker(self) -> str:
        return worker_for_kind(self.kind)

    def to_json(self) -> dict[str, Any]:
        """camelCase on the wire — the same convention as the HTTP API and the model."""
        out: dict[str, Any] = {
            "schemaVersion": self.schema_version,
            "jobId": self.job_id,
            "kind": self.kind,
            "queue": self.queue,
            "firmId": self.firm_id,
            "attempt": self.attempt,
            "maxAttempts": self.max_attempts,
            "enqueuedAtMs": self.enqueued_at_ms,
            "notBeforeMs": self.not_before_ms,
            "payload": self.payload,
        }
        for wire, value in (
            ("projectId", self.project_id),
            ("designVersionId", self.design_version_id),
            ("actorUserId", self.actor_user_id),
            ("requestId", self.request_id),
            ("idempotencyKey", self.idempotency_key),
            ("deadlineMs", self.deadline_ms),
        ):
            if value is not None:
                out[wire] = value
        if self.payload_ref is not None:
            out["payloadRef"] = self.payload_ref.to_json()
        if self.assets:
            out["assets"] = {name: ref.to_json() for name, ref in self.assets.items()}
        if self.outputs:
            out["outputs"] = {name: ref.to_json() for name, ref in self.outputs.items()}
        return out

    def encode(self) -> str:
        """Canonical JSON for the wire: sorted keys, no whitespace.

        Byte-for-byte identical to ``services.common.envelope.JobEnvelope.encode`` —
        the worker's lease bookkeeping ``LREM``s by exact string value, so encoding
        differences are not cosmetic.
        """
        return json.dumps(
            self.to_json(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )


def new_job_id() -> str:
    """Id for a job that has no database row (exports)."""
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Progress events (``services.common.progress.ProgressEvent``)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProgressEvent:
    """One honest progress fact from a worker (§15 "never a fake bar").

    The API does not synthesise these. If a worker sends nothing, the SSE stream shows
    the database row's state and then silence — which is the truth.

    ``percent`` is deliberately optional: a worker that does not know omits it, and the
    UI renders an indeterminate state. A made-up number is exactly what §15 forbids.
    """

    job_id: str
    type: str
    seq: int = 0
    ts_ms: int = field(default_factory=now_ms)
    stage: Optional[str] = None
    message: Optional[str] = None
    percent: Optional[int] = None
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.type in TERMINAL_EVENT_TYPES

    @property
    def status(self) -> str:
        """The persisted ``JOB_STATUSES`` value this event implies."""
        return status_for_event(self.type)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schemaVersion": PROGRESS_EVENT_VERSION,
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
    def decode(cls, raw: Any) -> ProgressEvent:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        data = json.loads(text)
        percent = data.get("percent")
        payload = data.get("data")
        return cls(
            job_id=str(data["jobId"]),
            type=str(data["type"]),
            seq=int(data.get("seq") or 0),
            ts_ms=int(data.get("tsMs") or 0),
            stage=_opt_str(data.get("stage")),
            message=_opt_str(data.get("message")),
            percent=int(percent) if isinstance(percent, int) else None,
            data=payload if isinstance(payload, dict) else {},
        )

    def sse_event_name(self) -> str:
        """SSE ``event:`` name.

        Terminal events get their own names so a client can close the stream without
        parsing the body; everything else is ``progress``.
        """
        if self.type == "succeeded":
            return "done"
        if self.type in ("failed", "cancelled", "dead_lettered"):
            return "error"
        return "progress"


def _opt_str(value: Any) -> Optional[str]:
    return None if value is None else str(value)


# ---------------------------------------------------------------------------
# Redis client
# ---------------------------------------------------------------------------

_redis_client: Any = None


def get_redis(settings: Optional[Settings] = None) -> Any:
    """Process-wide async Redis client (``redis.asyncio``), created on first use.

    ``decode_responses=True``: every value we store is UTF-8 JSON, and decoding at the
    boundary keeps the rest of the module free of ``bytes``/``str`` branching.
    """
    global _redis_client
    if _redis_client is None:
        from redis.asyncio import Redis

        cfg = settings or get_settings()
        _redis_client = Redis.from_url(
            cfg.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_keepalive=True,
            health_check_interval=30,
        )
    return _redis_client


async def close_redis() -> None:
    """Close the pool (FastAPI lifespan shutdown)."""
    global _redis_client
    client = _redis_client
    _redis_client = None
    if client is not None:
        await client.aclose()


async def ping() -> bool:
    """Liveness probe. Never raises."""
    try:
        return bool(await get_redis().ping())
    except Exception:  # noqa: BLE001 - health probes must not raise
        return False


class QueueUnavailableError(RuntimeError):
    """Redis is unreachable, so a job cannot be queued.

    Carries the ``http_status``/``code``/``action`` trio so ``main.py`` can render it as
    problem+json without this module importing the HTTP layer. Never let a job row claim
    ``queued`` when nothing will pick it up (golden rule 9) — the caller rolls back.
    """

    http_status = 503
    code = "queue_unavailable"
    action = "The job queue is down. Try again in a moment."

    def as_problem(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "action": self.action}


# ---------------------------------------------------------------------------
# Producer API (used by the routers)
# ---------------------------------------------------------------------------


async def enqueue(envelope: JobEnvelope, *, settings: Optional[Settings] = None) -> int:
    """Push a job onto its queue and publish the initial ``queued`` progress event.

    Returns the queue depth after the push (the UI shows "3rd in queue" from it).
    Raises :class:`QueueUnavailableError` if Redis refuses — the caller must then roll
    the job row back rather than leave a ghost.
    """
    client = get_redis(settings)
    key = queue_name(envelope.worker, settings)
    raw = envelope.encode()
    try:
        if envelope.not_before_ms > now_ms():
            await client.zadd(
                delayed_queue(envelope.worker, settings), {raw: float(envelope.not_before_ms)}
            )
            depth = int(await client.llen(key))
        else:
            depth = int(await client.lpush(key, raw))
    except Exception as exc:  # noqa: BLE001 - normalise every redis failure
        _log.error(
            "queue.enqueue_failed",
            queue=key,
            job_id=envelope.job_id,
            job_kind=envelope.kind,
            error="%s: %s" % (type(exc).__name__, exc),
        )
        raise QueueUnavailableError(
            "Could not reach the job queue to start this work."
        ) from exc

    _log.info(
        "queue.enqueued",
        queue=key,
        job_id=envelope.job_id,
        job_kind=envelope.kind,
        attempt=envelope.attempt,
        depth=depth,
    )
    await publish_progress(
        ProgressEvent(
            job_id=envelope.job_id,
            type="queued",
            percent=0,
            stage="queued",
            message="Waiting for a free worker.",
            data={"queueDepth": depth, "kind": envelope.kind},
        ),
        settings=settings,
    )
    return depth


async def next_progress_seq(job_id: Any, *, settings: Optional[Settings] = None) -> int:
    """Allocate the next per-job event sequence number."""
    client = get_redis(settings)
    key = progress_seq_key(job_id)
    seq = int(await client.incr(key))
    await client.expire(key, PROGRESS_BACKLOG_TTL_SECONDS)
    return seq


async def publish_progress(
    event: ProgressEvent, *, settings: Optional[Settings] = None
) -> ProgressEvent:
    """Publish an event to live listeners *and* append it to the replay backlog.

    Allocates ``seq`` when the caller left it at 0. Returns the event actually published.

    Failures are logged and swallowed: telemetry must never break the job it describes.
    The database row remains authoritative and the SSE endpoint re-reads it on connect,
    so a dropped event costs smoothness, not correctness.

    Only the API's own ``queued`` event goes through here — every other event on a job's
    channel is written by ``services.common.progress.ProgressReporter``.
    """
    try:
        client = get_redis(settings)
        stamped = event
        if event.seq <= 0:
            seq = await next_progress_seq(event.job_id, settings=settings)
            stamped = ProgressEvent(
                job_id=event.job_id,
                type=event.type,
                seq=seq,
                ts_ms=event.ts_ms,
                stage=event.stage,
                message=event.message,
                percent=event.percent,
                data=event.data,
            )
        raw = stamped.encode()
        log_key = progress_log_key(stamped.job_id)
        pipe = client.pipeline(transaction=False)
        pipe.rpush(log_key, raw)
        pipe.ltrim(log_key, -PROGRESS_BACKLOG_MAX, -1)
        pipe.expire(log_key, PROGRESS_BACKLOG_TTL_SECONDS)
        pipe.publish(progress_channel(stamped.job_id), raw)
        await pipe.execute()
        return stamped
    except Exception as exc:  # noqa: BLE001 - progress must never break a job
        _log.warning(
            "queue.progress_publish_failed",
            job_id=event.job_id,
            error="%s: %s" % (type(exc).__name__, exc),
        )
        return event


async def read_progress_backlog(
    job_id: Any, *, after_seq: int = 0, settings: Optional[Settings] = None
) -> list[ProgressEvent]:
    """Replay stored events with ``seq > after_seq`` (SSE ``Last-Event-ID`` resume)."""
    try:
        client = get_redis(settings)
        raw_items = await client.lrange(progress_log_key(job_id), 0, -1)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "queue.progress_backlog_failed",
            job_id=str(job_id),
            error="%s: %s" % (type(exc).__name__, exc),
        )
        return []
    events: list[ProgressEvent] = []
    for raw in raw_items:
        try:
            parsed = ProgressEvent.decode(raw)
        except (ValueError, KeyError, TypeError):
            continue
        if parsed.seq > after_seq:
            events.append(parsed)
    events.sort(key=lambda e: e.seq)
    return events


async def progress_stream(
    job_id: Any,
    *,
    after_seq: int = 0,
    settings: Optional[Settings] = None,
    poll_timeout: float = 1.0,
) -> AsyncIterator[ProgressEvent]:
    """Yield backlog events then live events, in ``seq`` order, without duplicates.

    Subscribe-then-backfill, not the other way round: anything published between a
    backlog read and a later subscription would vanish. Duplicates from the deliberate
    overlap are filtered by ``seq``.

    The iterator ends after the first terminal event. Callers must also honour client
    disconnects — see the SSE handlers in ``routers/jobs.py``.
    """
    client = get_redis(settings)
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    seen_max = after_seq
    try:
        await pubsub.subscribe(progress_channel(job_id))

        for event in await read_progress_backlog(
            job_id, after_seq=after_seq, settings=settings
        ):
            if event.seq > seen_max:
                seen_max = event.seq
            yield event
            if event.terminal:
                return

        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=poll_timeout
            )
            if message is None:
                continue
            raw = message.get("data")
            if not raw:
                continue
            try:
                event = ProgressEvent.decode(raw)
            except (ValueError, KeyError, TypeError):
                continue
            if event.seq and event.seq <= seen_max:
                continue
            seen_max = max(seen_max, event.seq)
            yield event
            if event.terminal:
                return
    finally:
        try:
            await pubsub.aclose()
        except Exception:  # noqa: BLE001 - teardown must not mask the real error
            pass


async def queue_depth(worker: str, *, settings: Optional[Settings] = None) -> int:
    """Pending items on a queue — the §18 queue-depth metric and the UI's "position".

    Counts the delayed set too: a job waiting out a retry backoff is genuinely still
    queued, and reporting 0 while three jobs are pending would be a lie the UI repeats.
    Returns ``-1`` when Redis cannot be reached, which the caller renders as "unknown"
    rather than as "empty".
    """
    try:
        client = get_redis(settings)
        pending = int(await client.llen(queue_name(worker, settings)))
        delayed = int(await client.zcard(delayed_queue(worker, settings)) or 0)
        return pending + delayed
    except Exception:  # noqa: BLE001
        return -1


async def queue_depths(*, settings: Optional[Settings] = None) -> dict[str, int]:
    return {worker: await queue_depth(worker, settings=settings) for worker in WORKERS}


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


async def request_cancel(job_id: Any, *, settings: Optional[Settings] = None) -> None:
    """Ask a worker to stop. Advisory: the worker stops at its next checkpoint."""
    try:
        await get_redis(settings).set(cancel_key(job_id), "1", ex=CANCEL_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "queue.cancel_request_failed",
            job_id=str(job_id),
            error="%s: %s" % (type(exc).__name__, exc),
        )


async def clear_cancel(job_id: Any, *, settings: Optional[Settings] = None) -> None:
    try:
        await get_redis(settings).delete(cancel_key(job_id))
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Durable lifecycle stream (the API's side of services/common/jobstore.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LifecycleRecord:
    """One entry read from :data:`JOB_EVENTS_STREAM`.

    ``entry_id`` must be ``XACK``-ed only after the database transaction that applied
    this record has committed — at-least-once delivery is only safe if the acknowledge
    follows the write.
    """

    entry_id: str
    job_id: str
    kind: str
    firm_id: str
    project_id: Optional[str]
    design_version_id: Optional[str]
    type: str
    attempt: int
    event: ProgressEvent

    @property
    def worker(self) -> str:
        return worker_for_kind(self.kind)

    @property
    def status(self) -> str:
        return status_for_event(self.type)


async def ensure_job_events_group(*, settings: Optional[Settings] = None) -> bool:
    """Create the API's consumer group on the lifecycle stream. Idempotent.

    ``mkstream=True`` so the group exists before any worker has ever run; ``id="0"`` so
    a freshly created group replays whatever is already in the stream rather than only
    seeing events published after boot.
    """
    client = get_redis(settings)
    try:
        await client.xgroup_create(
            JOB_EVENTS_STREAM, JOB_EVENTS_GROUP, id="0", mkstream=True
        )
        _log.info("queue.job_events_group_created", stream=JOB_EVENTS_STREAM)
        return True
    except Exception as exc:  # noqa: BLE001 - BUSYGROUP is the normal case
        if "BUSYGROUP" in str(exc):
            return True
        _log.warning(
            "queue.job_events_group_failed",
            stream=JOB_EVENTS_STREAM,
            error="%s: %s" % (type(exc).__name__, exc),
        )
        return False


async def read_job_events(
    consumer: str,
    *,
    count: int = 50,
    block_ms: int = 5000,
    settings: Optional[Settings] = None,
) -> list[LifecycleRecord]:
    """Block for up to ``block_ms`` waiting for new lifecycle records.

    Returns ``[]`` on timeout so the consumer loop can check for shutdown. Malformed
    entries are logged and acknowledged rather than poisoning the group forever — a
    record we cannot parse will not parse on the next attempt either.
    """
    client = get_redis(settings)
    try:
        response = await client.xreadgroup(
            JOB_EVENTS_GROUP,
            consumer,
            {JOB_EVENTS_STREAM: ">"},
            count=count,
            block=block_ms,
        )
    except Exception as exc:  # noqa: BLE001
        if "NOGROUP" in str(exc):
            await ensure_job_events_group(settings=settings)
            return []
        raise

    records: list[LifecycleRecord] = []
    unparseable: list[str] = []
    for _stream, entries in response or []:
        for entry_id, fields in entries:
            try:
                records.append(_parse_lifecycle(str(entry_id), fields))
            except (ValueError, KeyError, TypeError) as exc:
                unparseable.append(str(entry_id))
                _log.error(
                    "queue.lifecycle_unparseable",
                    entry_id=str(entry_id),
                    error="%s: %s" % (type(exc).__name__, exc),
                )
    if unparseable:
        await ack_job_events(unparseable, settings=settings)
    return records


def _parse_lifecycle(entry_id: str, fields: dict[str, Any]) -> LifecycleRecord:
    event = ProgressEvent.decode(fields["event"])
    return LifecycleRecord(
        entry_id=entry_id,
        job_id=str(fields.get("jobId") or event.job_id),
        kind=str(fields["kind"]),
        firm_id=str(fields["firmId"]),
        project_id=str(fields.get("projectId") or "") or None,
        design_version_id=str(fields.get("designVersionId") or "") or None,
        type=str(fields.get("type") or event.type),
        attempt=int(fields.get("attempt") or 1),
        event=event,
    )


async def ack_job_events(
    entry_ids: list[str], *, settings: Optional[Settings] = None
) -> int:
    """``XACK`` — call only after the applying transaction has committed."""
    if not entry_ids:
        return 0
    try:
        return int(
            await get_redis(settings).xack(JOB_EVENTS_STREAM, JOB_EVENTS_GROUP, *entry_ids)
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "queue.lifecycle_ack_failed",
            count=len(entry_ids),
            error="%s: %s" % (type(exc).__name__, exc),
        )
        return 0


# ---------------------------------------------------------------------------
# Export jobs (Redis-backed — see the module docstring for why)
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ExportJob:
    """An export job's whole state. Mirrors the DB job shape so the API response for
    ``GET /export-jobs/:id`` looks like every other job to the client."""

    id: str
    firm_id: str
    project_id: str
    kind: str
    status: str
    progress: int = 0
    design_version_id: Optional[str] = None
    download_url: Optional[str] = None
    error: Optional[str] = None
    params: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "firmId": self.firm_id,
            "projectId": self.project_id,
            "kind": self.kind,
            "status": self.status,
            "progress": self.progress,
            "designVersionId": self.design_version_id,
            "downloadUrl": self.download_url,
            "error": self.error,
            "params": self.params,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExportJob:
        return cls(
            id=str(data["id"]),
            firm_id=str(data["firmId"]),
            project_id=str(data["projectId"]),
            kind=str(data["kind"]),
            status=str(data["status"]),
            progress=int(data.get("progress") or 0),
            design_version_id=_opt_str(data.get("designVersionId")),
            download_url=_opt_str(data.get("downloadUrl")),
            error=_opt_str(data.get("error")),
            params=dict(data.get("params") or {}),
            created_at=str(data.get("createdAt") or _utc_now_iso()),
            updated_at=str(data.get("updatedAt") or _utc_now_iso()),
        )

    def evolve(self, **changes: Any) -> ExportJob:
        current = {
            "id": self.id,
            "firm_id": self.firm_id,
            "project_id": self.project_id,
            "kind": self.kind,
            "status": self.status,
            "progress": self.progress,
            "design_version_id": self.design_version_id,
            "download_url": self.download_url,
            "error": self.error,
            "params": self.params,
            "created_at": self.created_at,
            "updated_at": _utc_now_iso(),
        }
        current.update(changes)
        return ExportJob(**current)  # type: ignore[arg-type]


async def put_export_job(job: ExportJob, *, settings: Optional[Settings] = None) -> ExportJob:
    """Write/overwrite an export job record (24h TTL)."""
    client = get_redis(settings)
    payload = json.dumps(job.to_dict(), separators=(",", ":"), ensure_ascii=False)
    try:
        await client.set(
            export_job_key(job.firm_id, job.id), payload, ex=EXPORT_JOB_TTL_SECONDS
        )
    except Exception as exc:  # noqa: BLE001
        raise QueueUnavailableError("Could not record the export job.") from exc
    return job


async def get_export_job(
    firm_id: Any, job_id: Any, *, settings: Optional[Settings] = None
) -> Optional[ExportJob]:
    """Read an export job **for one firm only** — the key itself is the tenancy scope."""
    try:
        raw = await get_redis(settings).get(export_job_key(firm_id, job_id))
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        return ExportJob.from_dict(json.loads(raw))
    except (ValueError, KeyError, TypeError):
        return None


__all__ = [
    "CANCEL_TTL_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "ENVELOPE_SCHEMA_VERSION",
    "EVENT_TYPES",
    "EXPORT_JOB_TTL_SECONDS",
    "EXPORT_KINDS",
    "IDEMPOTENCY_TTL_SECONDS",
    "JOB_DRAWINGS_EXPORT",
    "JOB_DRAWINGS_GENERATE_SHEETS",
    "JOB_DRAWINGS_IMPORT_DXF",
    "JOB_EVENTS_GROUP",
    "JOB_EVENTS_MAXLEN",
    "JOB_EVENTS_STREAM",
    "JOB_KINDS",
    "JOB_KINDS_BY_WORKER",
    "JOB_RENDER_IMAGE",
    "JOB_SOLVER_GENERATE",
    "JOB_SOLVER_RESOLVE",
    "JOB_STATUSES",
    "JOB_TERMINAL_STATUSES",
    "PROGRESS_BACKLOG_MAX",
    "PROGRESS_BACKLOG_TTL_SECONDS",
    "PROGRESS_EVENT_VERSION",
    "TERMINAL_EVENT_TYPES",
    "WORKERS",
    "WORKER_DRAWINGS",
    "WORKER_RENDER",
    "WORKER_SOLVER",
    "BlobRef",
    "ExportJob",
    "JobEnvelope",
    "LifecycleRecord",
    "ProgressEvent",
    "QueueUnavailableError",
    "ack_job_events",
    "cancel_key",
    "clear_cancel",
    "close_redis",
    "dead_letter_queue",
    "delayed_queue",
    "enqueue",
    "ensure_job_events_group",
    "export_job_key",
    "get_export_job",
    "get_redis",
    "idempotency_key",
    "new_job_id",
    "next_progress_seq",
    "now_ms",
    "ping",
    "processing_queue",
    "progress_channel",
    "progress_log_key",
    "progress_seq_key",
    "progress_stream",
    "publish_progress",
    "put_export_job",
    "queue_depth",
    "queue_depths",
    "queue_for_kind",
    "queue_name",
    "read_job_events",
    "read_progress_backlog",
    "request_cancel",
    "status_for_event",
    "worker_for_kind",
]
