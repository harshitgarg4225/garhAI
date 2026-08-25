"""The shared worker runtime: reserve → run → report → ack (playbook §18).

All three workers are this class plus a handler. What it guarantees:

* **Graceful shutdown.** SIGTERM/SIGINT stop new reservations immediately; in-flight
  jobs get ``WORKER_SHUTDOWN_GRACE_SECONDS`` to finish, and anything still running is
  cancelled and *released back to the queue without burning an attempt*.
* **Visibility timeout.** Each running job holds a lease, renewed on a heartbeat. A
  worker that is SIGKILLed loses its lease and the job is redelivered (at-least-once).
* **Retry with exponential backoff, then dead-letter.** ``5s, 10s, 20s…`` capped, and
  the user is told (§15/golden rule 9: "the UI shows job state honestly").
* **Resumable jobs.** The checkpoint survives a retry, so attempt 2 continues rather
  than restarts.
* **Real progress.** The handler gets a :class:`~services.common.progress.ProgressReporter`
  and nothing else can emit progress — no fake bars can exist by construction (§15).
* **Cancellation.** The API sets a Redis flag; the supervisor notices within a
  heartbeat and cancels the running task.
* **Observability.** structlog JSON with job context bound, plus ``/healthz`` and
  ``/metrics`` including the §18 queue-depth gauge.

CPU-bound handlers (CP-SAT, Pillow compositing, diffusers) must not block the event
loop — they run their heavy section inside :func:`asyncio.to_thread`, which also keeps
cancellation responsive for everything around it.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol

from services.common.blobs import BlobClient
from services.common.checkpoint import JobCheckpoint
from services.common.config import WorkerSettings, get_worker_settings
from services.common.envelope import JOB_KINDS_BY_WORKER, JobEnvelope
from services.common.errors import (
    InvalidJobError,
    JobCancelledError,
    JobTimeoutError,
    PermanentError,
    is_retryable,
    user_facing,
)
from services.common.health import HealthServer, HealthStatus
from services.common.jobstore import JobResult, JobStatusSink, NullJobStatusSink
from services.common.logging import (
    bind_job_context,
    clear_job_context,
    configure_worker_logging,
    get_logger,
)
from services.common.metrics import WorkerMetrics
from services.common.progress import ProgressReporter
from services.common.queue import RedisJobQueue, RedisLike, Reservation, connect

log = get_logger("runtime")


@dataclass
class JobContext:
    """Everything a handler is allowed to touch.

    Note what is absent: no database session, no S3 client, no settings mutation, no
    way to enqueue other jobs. A handler is a pure-ish function from envelope + assets
    to :class:`JobResult`, with progress as its only side channel.
    """

    envelope: JobEnvelope
    settings: WorkerSettings
    progress: ProgressReporter
    checkpoint: JobCheckpoint
    blobs: BlobClient
    #: Set when the API requested cancellation; also polled by the supervisor.
    cancel_event: asyncio.Event

    @property
    def job_id(self) -> str:
        return self.envelope.job_id

    @property
    def payload(self) -> dict[str, Any]:
        return self.envelope.payload

    def raise_if_cancelled(self) -> None:
        """Call between stages of a long job. Cheap, local, no Redis round-trip."""
        if self.cancel_event.is_set():
            raise JobCancelledError()


class JobHandler(Protocol):
    """What a worker package must provide."""

    #: Job kinds this handler accepts (must be a subset of its queue's kinds).
    kinds: tuple[str, ...]
    #: Wall-clock budget, or ``None`` for no limit. §14 budgets live here.
    timeout_seconds: int | None

    def timeout_for(self, ctx: JobContext) -> int | None:
        """Per-job wall-clock budget, read by the runner BEFORE the handler starts."""
        ...

    async def handle(self, ctx: JobContext) -> JobResult: ...


class BaseJobHandler(ABC):
    """Convenience base implementing the boring half of :class:`JobHandler`."""

    kinds: tuple[str, ...] = ()
    timeout_seconds: int | None = None

    def timeout_for(self, ctx: JobContext) -> int | None:
        """The budget for THIS job. The runner reads it before the handler coroutine
        first runs, so a handler that needs a per-kind or per-settings budget must
        override this — assigning ``self.timeout_seconds`` inside :meth:`handle` is
        too late (the runner has already captured the value) and races under
        concurrency because the handler instance is shared across jobs."""
        return self.timeout_seconds

    @abstractmethod
    async def handle(self, ctx: JobContext) -> JobResult:
        """Do the work. Raise a :class:`~services.common.errors.WorkerError` to fail."""


class Worker:
    """A queue consumer. One per process; ``python -m services.<name>.worker``."""

    def __init__(
        self,
        *,
        name: str,
        handler: JobHandler,
        settings: WorkerSettings | None = None,
        redis: RedisLike | None = None,
        status_sink: JobStatusSink | None = None,
    ) -> None:
        self.name = name
        self.handler = handler
        self.settings = settings or get_worker_settings()
        self.queue_name = self.settings.resolve_queue(name)
        self._redis = redis
        self._owns_redis = redis is None
        self.status_sink: JobStatusSink = status_sink or NullJobStatusSink()

        self.metrics = WorkerMetrics(worker=name, queue=self.queue_name)
        self.metrics.concurrency = self.settings.worker_concurrency
        self.blobs = BlobClient(
            timeout_seconds=self.settings.blob_http_timeout_seconds,
            max_bytes=self.settings.blob_max_bytes,
        )

        self._queue: RedisJobQueue | None = None
        self._stopping = asyncio.Event()
        self._in_flight: set[asyncio.Task[None]] = set()
        self._slots: asyncio.Semaphore | None = None
        self._reservations: dict[asyncio.Task[None], Reservation] = {}
        self._health: HealthServer | None = None
        self._redis_ok = False

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def run(self) -> int:
        """Consume until stopped. Returns a process exit code."""
        configure_worker_logging(self.settings)
        self._validate_handler()

        redis = self._redis or connect(self.settings.redis_url)
        self._redis = redis
        queue = RedisJobQueue(
            redis,
            self.queue_name,
            visibility_timeout_seconds=self.settings.queue_visibility_timeout_seconds,
            dead_letter_maxlen=self.settings.queue_dead_letter_maxlen,
        )
        self._queue = queue
        self._slots = asyncio.Semaphore(self.settings.worker_concurrency)

        self._install_signal_handlers()
        self._health = HealthServer(
            host=self.settings.worker_health_host,
            port=self.settings.worker_health_port,
            metrics=self.metrics,
            probe=self._probe,
        )
        await self._health.start()

        log.info(
            "worker.started",
            queue=self.queue_name,
            concurrency=self.settings.worker_concurrency,
            kinds=list(self.handler.kinds),
            provider_llm=self.settings.provider_llm,
            provider_render=self.settings.provider_render,
        )

        sweeper = asyncio.create_task(self._sweep_loop(queue), name="sweep")
        exit_code = 0
        try:
            await self._consume(queue)
        except Exception as exc:  # noqa: BLE001 - last line of defence; must log, not vanish
            log.error("worker.crashed", error=str(exc), exc_info=True)
            exit_code = 1
        finally:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
            await self._drain(queue)
            if self._health is not None:
                await self._health.stop()
            await self.blobs.aclose()
            if self._owns_redis and self._redis is not None:
                with contextlib.suppress(Exception):
                    await self._redis.aclose()
            log.info(
                "worker.stopped",
                queue=self.queue_name,
                **{
                    key: value
                    for key, value in self.metrics.snapshot().items()
                    if key.startswith("jobs")
                },
            )
        return exit_code

    def stop(self, reason: str = "signal") -> None:
        """Ask the worker to finish up. Idempotent, safe from a signal handler."""
        if not self._stopping.is_set():
            log.info("worker.stopping", reason=reason)
            self._stopping.set()

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------
    async def _consume(self, queue: RedisJobQueue) -> None:
        assert self._slots is not None
        while not self._stopping.is_set():
            await self._slots.acquire()
            if self._stopping.is_set():
                self._slots.release()
                break
            try:
                reservation = await queue.reserve(
                    timeout_seconds=self.settings.queue_reserve_timeout_seconds
                )
            except Exception as exc:  # noqa: BLE001 - Redis blip: back off, stay alive
                self._slots.release()
                self._redis_ok = False
                log.error("worker.reserve_failed", error=str(exc))
                await self._sleep_or_stop(2.0)
                continue
            self._redis_ok = True
            if reservation is None:
                self._slots.release()
                continue

            task = asyncio.create_task(
                self._run_job(queue, reservation), name="job:%s" % reservation.envelope.job_id
            )
            self._in_flight.add(task)
            self._reservations[task] = reservation
            task.add_done_callback(self._job_finished)

    def _job_finished(self, task: asyncio.Task[None]) -> None:
        self._in_flight.discard(task)
        self._reservations.pop(task, None)
        if self._slots is not None:
            self._slots.release()

    async def _sleep_or_stop(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)

    # ------------------------------------------------------------------
    # one job
    # ------------------------------------------------------------------
    async def _run_job(self, queue: RedisJobQueue, reservation: Reservation) -> None:
        envelope = reservation.envelope
        started_ms = int(time.monotonic() * 1000)
        bind_job_context(**envelope.log_fields())
        self.metrics.jobs_received += 1
        self.metrics.in_flight = len(self._in_flight)

        assert self._redis is not None
        progress = ProgressReporter(
            self._redis,
            envelope,
            log_maxlen=self.settings.progress_log_maxlen,
            ttl_seconds=self.settings.progress_ttl_seconds,
            events_stream=self.settings.job_events_stream,
            events_maxlen=self.settings.job_events_maxlen,
        )
        checkpoint = JobCheckpoint(
            self._redis, envelope.job_id, ttl_seconds=self.settings.job_checkpoint_ttl_seconds
        )
        cancel_event = asyncio.Event()
        ctx = JobContext(
            envelope=envelope,
            settings=self.settings,
            progress=progress,
            checkpoint=checkpoint,
            blobs=self.blobs,
            cancel_event=cancel_event,
        )

        supervisor: asyncio.Task[None] | None = None
        try:
            self._reject_foreign_kind(envelope)

            if await queue.is_cancelled(envelope.job_id):
                await self._finish_cancelled(queue, reservation, progress, checkpoint)
                return
            if envelope.expired():
                raise PermanentError(
                    "This job waited too long to start, so we stopped it.",
                    action="Start it again.",
                    code="job_expired",
                )

            await progress.started()
            await self._sink(self.status_sink.on_started(envelope))

            # Resolve the budget BEFORE the handler coroutine exists: a handler
            # mutating self.timeout_seconds inside handle() can never affect the
            # job it is currently running (and would race across concurrent jobs).
            timeout = self.handler.timeout_for(ctx)
            handler_task = asyncio.create_task(
                self.handler.handle(ctx), name="handler:%s" % envelope.job_id
            )
            supervisor = asyncio.create_task(
                self._supervise(queue, reservation, handler_task, cancel_event),
                name="supervise:%s" % envelope.job_id,
            )
            try:
                result = (
                    await asyncio.wait_for(handler_task, timeout=timeout)
                    if timeout
                    else await handler_task
                )
            except TimeoutError as exc:
                handler_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await handler_task
                raise JobTimeoutError(
                    "This job took longer than expected and was stopped.",
                    action="Try again — if it keeps happening, simplify the request.",
                    detail="handler exceeded %ss" % timeout,
                ) from exc
            except asyncio.CancelledError as exc:
                if cancel_event.is_set():
                    raise JobCancelledError() from exc
                raise

            await progress.succeeded(message=result.message, **result.to_event_data())
            await queue.ack(reservation)
            await checkpoint.clear()
            await queue.clear_cancel(envelope.job_id)
            await self._sink(self.status_sink.on_succeeded(envelope, result))
            self.metrics.jobs_succeeded += 1
            log.info("job.succeeded", duration_ms=int(time.monotonic() * 1000) - started_ms)

        except JobCancelledError:
            await self._finish_cancelled(queue, reservation, progress, checkpoint)
        except asyncio.CancelledError:
            # Shutdown cancelled us: hand the job straight back, no attempt burned.
            await queue.release(reservation)
            self.metrics.jobs_released += 1
            log.info("job.released", reason="worker shutting down")
            raise
        except Exception as exc:  # noqa: BLE001 - classified below, never swallowed
            try:
                await self._handle_failure(queue, reservation, progress, checkpoint, exc)
            except Exception as failure_exc:  # noqa: BLE001 - the failure path broke
                # The failure path failing is how a job gets stuck "running"
                # forever with a healthy-looking worker (progress.failed's kwarg
                # collision did exactly that on the solver's first real job). An
                # imperfectly-reported failure beats an unreported one: log both
                # errors, best-effort a minimal failed event, and ALWAYS take the
                # job off the queue.
                log.error(
                    "job.failure_handling_crashed",
                    original=repr(exc),
                    failure_error=repr(failure_exc),
                    exc_info=True,
                )
                with contextlib.suppress(Exception):
                    await progress.failed(
                        {
                            "code": "worker_error",
                            "message": "This job failed, and reporting the details also failed.",
                            "action": "Try again — and check the worker logs.",
                        }
                    )
                with contextlib.suppress(Exception):
                    await queue.ack(reservation)
                self.metrics.jobs_failed += 1
        finally:
            if supervisor is not None:
                supervisor.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await supervisor
            self.metrics.record_duration(int(time.monotonic() * 1000) - started_ms)
            self.metrics.in_flight = max(0, len(self._in_flight) - 1)
            clear_job_context()

    async def _handle_failure(
        self,
        queue: RedisJobQueue,
        reservation: Reservation,
        progress: ProgressReporter,
        checkpoint: JobCheckpoint,
        exc: BaseException,
    ) -> None:
        envelope = reservation.envelope
        problem = user_facing(exc)
        detail = getattr(exc, "detail", None) or repr(exc)

        if isinstance(exc, InvalidJobError):
            self.metrics.jobs_invalid += 1

        if is_retryable(exc) and not envelope.is_last_attempt:
            delay = self.settings.retry_delay_seconds(envelope.attempt)
            await progress.retrying(
                attempt=envelope.attempt + 1,
                delay_seconds=delay,
                reason=str(problem.get("message", "")),
            )
            await queue.retry(reservation, delay_seconds=delay)
            # Checkpoint is deliberately KEPT — that is what makes the retry resumable.
            self.metrics.jobs_retried += 1
            log.warning(
                "job.retrying",
                code=problem.get("code"),
                delay_seconds=delay,
                next_attempt=envelope.attempt + 1,
                detail=detail,
            )
            return

        if is_retryable(exc):
            await progress.dead_lettered(problem)
            await queue.dead_letter(reservation, reason=str(detail)[:1000])
            self.metrics.jobs_dead_lettered += 1
            log.error("job.dead_lettered", code=problem.get("code"), detail=detail)
        else:
            await progress.failed(problem)
            await queue.ack(reservation)
            self.metrics.jobs_failed += 1
            log.error("job.failed", code=problem.get("code"), detail=detail)

        await checkpoint.clear()
        await queue.clear_cancel(envelope.job_id)
        await self._sink(self.status_sink.on_failed(envelope, problem))

    async def _finish_cancelled(
        self,
        queue: RedisJobQueue,
        reservation: Reservation,
        progress: ProgressReporter,
        checkpoint: JobCheckpoint,
    ) -> None:
        await progress.cancelled()
        await queue.ack(reservation)
        await checkpoint.clear()
        await queue.clear_cancel(reservation.envelope.job_id)
        await self._sink(self.status_sink.on_cancelled(reservation.envelope))
        self.metrics.jobs_cancelled += 1
        log.info("job.cancelled")

    async def _supervise(
        self,
        queue: RedisJobQueue,
        reservation: Reservation,
        handler_task: asyncio.Task[JobResult],
        cancel_event: asyncio.Event,
    ) -> None:
        """Renew the lease and watch for cancellation while the handler runs."""
        interval = max(1, self.settings.worker_heartbeat_interval_seconds)
        while not handler_task.done():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(handler_task), timeout=interval)
            if handler_task.done():
                return
            try:
                await queue.heartbeat(reservation)
            except Exception as exc:  # noqa: BLE001 - a missed beat is not fatal yet
                log.warning("job.heartbeat_failed", error=str(exc))
            try:
                if await queue.is_cancelled(reservation.envelope.job_id):
                    cancel_event.set()
                    handler_task.cancel()
                    return
            except Exception as exc:  # noqa: BLE001
                log.warning("job.cancel_poll_failed", error=str(exc))

    async def _sink(self, coro: Any) -> None:
        """Await a status-sink call; a sink failure never changes a job's outcome."""
        try:
            await coro
        except Exception as exc:  # noqa: BLE001
            log.warning("worker.status_sink_failed", error=str(exc))

    # ------------------------------------------------------------------
    # background loops
    # ------------------------------------------------------------------
    async def _sweep_loop(self, queue: RedisJobQueue) -> None:
        """Promote due retries, reap dead leases, refresh the depth gauge."""
        while True:
            try:
                await queue.sweep()
                depth = await queue.depth()
                self.metrics.queue_depth_pending = depth.pending
                self.metrics.queue_depth_delayed = depth.delayed
                self.metrics.queue_depth_processing = depth.processing
                self.metrics.queue_depth_dead = depth.dead
                self._redis_ok = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._redis_ok = False
                log.warning("worker.sweep_failed", error=str(exc))
            await asyncio.sleep(self.settings.queue_sweep_interval_seconds)

    async def _probe(self) -> HealthStatus:
        """``/healthz``: healthy when Redis answers. Honest, not decorative."""
        if self._redis is None:
            return HealthStatus(healthy=False, reason="redis client not initialised")
        try:
            await asyncio.wait_for(self._redis.ping(), timeout=3.0)
        except Exception as exc:  # noqa: BLE001
            self._redis_ok = False
            return HealthStatus(healthy=False, reason="redis unreachable: %s" % exc)
        self._redis_ok = True
        return HealthStatus(
            healthy=not self._stopping.is_set(),
            reason="ok" if not self._stopping.is_set() else "draining",
        )

    # ------------------------------------------------------------------
    # shutdown
    # ------------------------------------------------------------------
    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.stop, sig.name)
            except (NotImplementedError, RuntimeError, ValueError):
                # Windows / non-main thread: the caller can still call stop().
                log.debug("worker.signal_handler_unavailable", signal=sig.name)

    async def _drain(self, queue: RedisJobQueue) -> None:
        """Let in-flight jobs finish, then release whatever is left."""
        if not self._in_flight:
            return
        grace = self.settings.worker_shutdown_grace_seconds
        log.info("worker.draining", in_flight=len(self._in_flight), grace_seconds=grace)
        pending = list(self._in_flight)
        done, still_running = await asyncio.wait(pending, timeout=float(grace))
        for task in done:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await task
        if not still_running:
            log.info("worker.drained")
            return

        log.warning("worker.drain_timeout", stuck=len(still_running))
        for task in still_running:
            task.cancel()
        await asyncio.gather(*still_running, return_exceptions=True)
        # Cancellation may have raced the release inside _run_job; releasing an
        # already-acked reservation is a no-op, so this is safe belt-and-braces.
        for task in still_running:
            reservation = self._reservations.get(task)
            if reservation is not None:
                with contextlib.suppress(Exception):
                    await queue.release(reservation)

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------
    def _validate_handler(self) -> None:
        expected = JOB_KINDS_BY_WORKER.get(self.name)
        if expected is None:
            raise ValueError(
                "Unknown worker name %r. Expected one of %s."
                % (self.name, ", ".join(sorted(JOB_KINDS_BY_WORKER)))
            )
        if not self.handler.kinds:
            raise ValueError("Handler for worker %r declares no job kinds." % self.name)
        stray = [kind for kind in self.handler.kinds if kind not in expected]
        if stray:
            raise ValueError(
                "Handler for worker %r claims kinds it cannot receive: %s. Its queue "
                "carries: %s." % (self.name, ", ".join(stray), ", ".join(expected))
            )

    def _reject_foreign_kind(self, envelope: JobEnvelope) -> None:
        if envelope.kind not in self.handler.kinds:
            raise InvalidJobError(
                "This job was sent to the wrong place.",
                action="Start it again from the app.",
                detail="worker %r handles %s, got %r"
                % (self.name, ", ".join(self.handler.kinds), envelope.kind),
            )


def run_worker(*, name: str, handler: JobHandler) -> int:
    """Blocking entrypoint used by every ``services/<name>/worker.py``."""
    worker = Worker(name=name, handler=handler)
    try:
        return asyncio.run(worker.run())
    except KeyboardInterrupt:
        return 0


__all__ = [
    "BaseJobHandler",
    "JobContext",
    "JobHandler",
    "JobResult",
    "Worker",
    "run_worker",
]
