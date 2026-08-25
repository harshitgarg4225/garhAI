"""Reliable Redis job queue — the transport half of the worker runtime (§18).

QUEUE CONTRACT (the API's enqueue helper must match this exactly). For a queue base
name ``Q`` (``garh:queue:solver`` | ``garh:queue:render`` | ``garh:queue:drawings``):

===========================  ======  =====================================================
key                          type    meaning
===========================  ======  =====================================================
``Q``                        list    pending envelopes. Producers ``LPUSH``; consumers pop
                                     from the right, so the list is FIFO.
``Q:processing``             list    envelopes currently leased by a worker.
``Q:inflight``               hash    ``jobId`` → the exact envelope string in ``Q:processing``
                                     (the reaper needs the byte-identical value to ``LREM``).
``Q:leases``                 zset    ``jobId`` → lease deadline (ms). Expired ⇒ re-queued.
``Q:delayed``                zset    envelope → earliest delivery time (ms). Retry backoff.
``Q:dead``                   list    dead-lettered envelopes + failure reason, capped.
``garh:job:{jobId}:cancel``  string  set by the API to cancel; workers poll between stages.
===========================  ======  =====================================================

**To enqueue, the API only has to do one thing**::

    await redis.lpush("garh:queue:solver", envelope.encode())

Everything else — leases, retries, dead-lettering — is this module's job. Enqueueing
through :meth:`RedisJobQueue.enqueue` additionally honours ``notBeforeMs``.

Delivery semantics are **at-least-once**: a worker that dies mid-job has its lease
expire and the job is redelivered. Handlers must therefore be idempotent, which is
what ``services/common/checkpoint.py`` exists to make cheap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from services.common.envelope import JobEnvelope, now_ms
from services.common.errors import InvalidJobError
from services.common.logging import get_logger

log = get_logger("queue")

#: Cancellation flag key. The API sets it; :meth:`RedisJobQueue.is_cancelled` reads it.
CANCEL_KEY_TEMPLATE = "garh:job:%s:cancel"
#: How long a cancellation flag lives (a cancelled job may not be running yet).
CANCEL_TTL_SECONDS = 86_400


@runtime_checkable
class RedisLike(Protocol):
    """The exact Redis surface this package uses.

    Narrow on purpose: it documents the command set an operator must allow, and it
    lets ``services/common/testing.py`` provide an in-memory double so queue and
    runtime tests need no server. ``redis.asyncio.Redis`` satisfies it structurally;
    :func:`connect` casts because redis-py's own annotations are looser than this.
    """

    async def ping(self) -> Any: ...
    async def lpush(self, name: str, *values: str) -> Any: ...
    async def rpush(self, name: str, *values: str) -> Any: ...
    async def llen(self, name: str) -> Any: ...
    async def lrem(self, name: str, count: int, value: str) -> Any: ...
    async def ltrim(self, name: str, start: int, end: int) -> Any: ...
    async def lrange(self, name: str, start: int, end: int) -> Any: ...
    async def blmove(
        self, first_list: str, second_list: str, timeout: float, src: str, dest: str
    ) -> Any: ...
    async def hset(
        self,
        name: str,
        key: str | None = None,
        value: str | None = None,
        mapping: dict[str, str] | None = None,
    ) -> Any: ...
    async def hget(self, name: str, key: str) -> Any: ...
    async def hdel(self, name: str, *keys: str) -> Any: ...
    async def hlen(self, name: str) -> Any: ...
    async def zadd(self, name: str, mapping: dict[str, float]) -> Any: ...
    async def zrem(self, name: str, *values: str) -> Any: ...
    async def zcard(self, name: str) -> Any: ...
    async def zrangebyscore(
        self, name: str, min: float, max: float, start: int | None = None, num: int | None = None
    ) -> Any: ...
    async def set(self, name: str, value: str, ex: int | None = None, nx: bool = False) -> Any: ...
    async def get(self, name: str) -> Any: ...
    async def delete(self, *names: str) -> Any: ...
    async def exists(self, *names: str) -> Any: ...
    async def expire(self, name: str, time: int) -> Any: ...
    async def incr(self, name: str) -> Any: ...
    async def publish(self, channel: str, message: str) -> Any: ...
    async def xadd(
        self, name: str, fields: dict[str, str], maxlen: int | None = None, approximate: bool = True
    ) -> Any: ...
    async def aclose(self) -> None: ...


def connect(redis_url: str) -> RedisLike:
    """Open an async Redis client typed as :class:`RedisLike`.

    ``decode_responses=True`` so everything in this package is ``str``, never a mix of
    ``str`` and ``bytes`` — that mix is how "why is my key b'garh:...'" bugs happen.
    """
    from typing import cast

    from redis.asyncio import Redis  # local import: keeps import-time cost off tests

    client = Redis.from_url(
        redis_url,
        decode_responses=True,
        health_check_interval=30,
        socket_keepalive=True,
    )
    return cast(RedisLike, client)


@dataclass(frozen=True)
class QueueKeys:
    """Every Redis key derived from one queue name."""

    pending: str
    processing: str
    inflight: str
    leases: str
    delayed: str
    dead: str

    @classmethod
    def for_queue(cls, queue: str) -> QueueKeys:
        base = queue.strip()
        if not base:
            raise ValueError("queue name must not be empty")
        return cls(
            pending=base,
            processing="%s:processing" % base,
            inflight="%s:inflight" % base,
            leases="%s:leases" % base,
            delayed="%s:delayed" % base,
            dead="%s:dead" % base,
        )


@dataclass(frozen=True)
class Reservation:
    """A leased job: the envelope plus the exact string needed to release it."""

    envelope: JobEnvelope
    raw: str


@dataclass(frozen=True)
class QueueDepth:
    """§18's queue-depth metric, broken out so the UI can show queue position."""

    pending: int
    delayed: int
    processing: int
    dead: int

    @property
    def waiting(self) -> int:
        """What a user is actually queued behind."""
        return self.pending + self.delayed


class RedisJobQueue:
    """At-least-once queue with visibility timeouts, backoff and dead-lettering."""

    def __init__(
        self,
        redis: RedisLike,
        queue: str,
        *,
        visibility_timeout_seconds: int = 900,
        dead_letter_maxlen: int = 1_000,
    ) -> None:
        self.redis = redis
        self.queue = queue
        self.keys = QueueKeys.for_queue(queue)
        self.visibility_timeout_seconds = visibility_timeout_seconds
        self.dead_letter_maxlen = dead_letter_maxlen

    # ------------------------------------------------------------------
    # producing
    # ------------------------------------------------------------------
    async def enqueue(self, envelope: JobEnvelope) -> None:
        """Publish a job. Honours ``notBeforeMs`` by routing through the delayed set."""
        stamped = envelope if envelope.queue else _with_queue(envelope, self.queue)
        raw = stamped.encode()
        if stamped.not_before_ms > now_ms():
            await self.redis.zadd(self.keys.delayed, {raw: float(stamped.not_before_ms)})
        else:
            await self.redis.lpush(self.keys.pending, raw)

    # ------------------------------------------------------------------
    # consuming
    # ------------------------------------------------------------------
    async def reserve(self, *, timeout_seconds: int) -> Reservation | None:
        """Block for up to ``timeout_seconds`` for a job, and lease it.

        Returns ``None`` on timeout — the caller's cue to check for shutdown. An
        unparseable envelope is dead-lettered immediately rather than poisoning the
        queue forever.
        """
        raw = await self.redis.blmove(
            self.keys.pending,
            self.keys.processing,
            float(timeout_seconds),
            "RIGHT",
            "LEFT",
        )
        if raw is None:
            return None
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

        try:
            envelope = JobEnvelope.decode(text)
        except InvalidJobError as exc:
            await self.redis.lrem(self.keys.processing, 1, text)
            await self._push_dead(text, reason=exc.detail or exc.message, job_id=None)
            log.error("queue.poison_message", queue=self.queue, reason=exc.detail or exc.message)
            return None

        await self.redis.hset(self.keys.inflight, envelope.job_id, text)
        await self._touch_lease(envelope.job_id)
        return Reservation(envelope=envelope, raw=text)

    async def heartbeat(self, reservation: Reservation) -> None:
        """Extend the lease of a job that is still running."""
        await self._touch_lease(reservation.envelope.job_id)

    async def ack(self, reservation: Reservation) -> None:
        """Job finished (succeeded, cancelled, or permanently failed). Drop the lease."""
        await self._release_lease(reservation.envelope.job_id, reservation.raw)

    async def retry(self, reservation: Reservation, *, delay_seconds: int) -> JobEnvelope:
        """Schedule the next attempt after ``delay_seconds``. Returns the new envelope."""
        await self._release_lease(reservation.envelope.job_id, reservation.raw)
        nxt = reservation.envelope.next_attempt(
            not_before_ms=now_ms() + max(0, delay_seconds) * 1000
        )
        await self.redis.zadd(self.keys.delayed, {nxt.encode(): float(nxt.not_before_ms)})
        return nxt

    async def dead_letter(self, reservation: Reservation, *, reason: str) -> None:
        """Give up on a job, keeping a bounded record of why (golden rule 9)."""
        await self._release_lease(reservation.envelope.job_id, reservation.raw)
        await self._push_dead(
            reservation.raw, reason=reason, job_id=reservation.envelope.job_id
        )

    async def release(self, reservation: Reservation) -> None:
        """Hand a job straight back — used on graceful shutdown, no attempt burned.

        ``RPUSH`` puts it at the head of the FIFO so another worker picks it up next,
        instead of behind everything enqueued while we were draining.
        """
        await self._release_lease(reservation.envelope.job_id, reservation.raw)
        await self.redis.rpush(self.keys.pending, reservation.raw)

    # ------------------------------------------------------------------
    # maintenance sweep (run periodically by exactly one loop per worker)
    # ------------------------------------------------------------------
    async def promote_delayed(self, *, limit: int = 100) -> int:
        """Move due retries back to the pending list. Returns how many moved.

        ``ZREM`` returning 1 is the claim: with several workers sweeping, exactly one
        of them owns each member, so no Lua script is needed.
        """
        due = await self.redis.zrangebyscore(
            self.keys.delayed, 0, float(now_ms()), start=0, num=limit
        )
        moved = 0
        for member in _as_str_list(due):
            claimed = int(await self.redis.zrem(self.keys.delayed, member) or 0)
            if claimed == 1:
                await self.redis.lpush(self.keys.pending, member)
                moved += 1
        if moved:
            log.info("queue.promoted_delayed", queue=self.queue, count=moved)
        return moved

    async def reap_expired_leases(self, *, limit: int = 100) -> int:
        """Recover jobs whose worker died. Returns how many were recovered.

        A recovered job burns an attempt: a job that reliably kills its worker (OOM on
        a pathological plot, say) must dead-letter rather than loop forever.
        """
        expired = await self.redis.zrangebyscore(
            self.keys.leases, 0, float(now_ms()), start=0, num=limit
        )
        recovered = 0
        for job_id in _as_str_list(expired):
            claimed = int(await self.redis.zrem(self.keys.leases, job_id) or 0)
            if claimed != 1:
                continue
            raw = await self.redis.hget(self.keys.inflight, job_id)
            await self.redis.hdel(self.keys.inflight, job_id)
            if raw is None:
                continue
            text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            await self.redis.lrem(self.keys.processing, 1, text)
            try:
                envelope = JobEnvelope.decode(text)
            except InvalidJobError:
                await self._push_dead(text, reason="unparseable on lease expiry", job_id=job_id)
                continue
            if envelope.is_last_attempt:
                await self._push_dead(
                    text, reason="lease expired on the final attempt", job_id=job_id
                )
                log.warning("queue.lease_expired_dead", queue=self.queue, job_id=job_id)
            else:
                nxt = envelope.next_attempt(not_before_ms=now_ms())
                await self.redis.lpush(self.keys.pending, nxt.encode())
                log.warning(
                    "queue.lease_expired_requeued",
                    queue=self.queue,
                    job_id=job_id,
                    attempt=nxt.attempt,
                )
            recovered += 1
        return recovered

    async def sweep(self, *, limit: int = 100) -> tuple[int, int]:
        """One maintenance pass: ``(promoted, recovered)``."""
        return (
            await self.promote_delayed(limit=limit),
            await self.reap_expired_leases(limit=limit),
        )

    # ------------------------------------------------------------------
    # introspection
    # ------------------------------------------------------------------
    async def depth(self) -> QueueDepth:
        """§18's queue-depth metric."""
        return QueueDepth(
            pending=int(await self.redis.llen(self.keys.pending) or 0),
            delayed=int(await self.redis.zcard(self.keys.delayed) or 0),
            processing=int(await self.redis.llen(self.keys.processing) or 0),
            dead=int(await self.redis.llen(self.keys.dead) or 0),
        )

    async def dead_letters(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Most recent dead letters, newest first. Operator tooling."""
        rows = await self.redis.lrange(self.keys.dead, 0, max(0, limit - 1))
        out: list[dict[str, Any]] = []
        for row in _as_str_list(rows):
            try:
                parsed = json.loads(row)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                out.append(parsed)
        return out

    # ------------------------------------------------------------------
    # cancellation (§15: jobs are cancellable and the UI says so honestly)
    # ------------------------------------------------------------------
    async def request_cancel(self, job_id: str) -> None:
        await self.redis.set(
            CANCEL_KEY_TEMPLATE % job_id, "1", ex=CANCEL_TTL_SECONDS
        )

    async def is_cancelled(self, job_id: str) -> bool:
        return bool(int(await self.redis.exists(CANCEL_KEY_TEMPLATE % job_id) or 0))

    async def clear_cancel(self, job_id: str) -> None:
        await self.redis.delete(CANCEL_KEY_TEMPLATE % job_id)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    async def _touch_lease(self, job_id: str) -> None:
        deadline = now_ms() + self.visibility_timeout_seconds * 1000
        await self.redis.zadd(self.keys.leases, {job_id: float(deadline)})

    async def _release_lease(self, job_id: str, raw: str) -> None:
        await self.redis.lrem(self.keys.processing, 1, raw)
        await self.redis.hdel(self.keys.inflight, job_id)
        await self.redis.zrem(self.keys.leases, job_id)

    async def _push_dead(self, raw: str, *, reason: str, job_id: str | None) -> None:
        record = json.dumps(
            {
                "deadAtMs": now_ms(),
                "queue": self.queue,
                "jobId": job_id,
                "reason": reason[:1000],
                "envelope": raw[:20_000],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        await self.redis.lpush(self.keys.dead, record)
        await self.redis.ltrim(self.keys.dead, 0, self.dead_letter_maxlen - 1)


def _with_queue(envelope: JobEnvelope, queue: str) -> JobEnvelope:
    data = envelope.to_json()
    data["queue"] = queue
    return JobEnvelope.from_json(data)


def _as_str_list(value: Any) -> list[str]:
    if not value:
        return []
    out: list[str] = []
    for item in value:
        out.append(item.decode("utf-8") if isinstance(item, bytes) else str(item))
    return out


__all__ = [
    "CANCEL_KEY_TEMPLATE",
    "QueueDepth",
    "QueueKeys",
    "RedisJobQueue",
    "RedisLike",
    "Reservation",
    "connect",
]
