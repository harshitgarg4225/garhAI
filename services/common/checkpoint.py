"""Resumable jobs (golden rule 9: "jobs are resumable").

Delivery is at-least-once, so every handler must tolerate being started twice. A
checkpoint turns that obligation into an advantage: work already finished on attempt 1
is reused on attempt 2 instead of being redone.

Shape: one Redis key per job holding a small JSON object of *handler-defined* stage
results. The runtime never interprets it — only the handler knows whether "stage A
finished with these 4 candidates" is still valid.

Rules a handler must follow:

* Checkpoint **facts**, not progress. "envelope derived, area 63_400_000 mm²" is a
  fact; "42% done" is not.
* Checkpoints are **content-addressed by input**: store ``inputsHash`` and refuse to
  resume when it differs, or a retry after an edit resumes into a stale plan.
* Keep them **small** (a few KB). Large intermediates belong in object storage with
  the checkpoint holding the key.

Usage::

    ck = JobCheckpoint(redis, envelope.job_id, ttl_seconds=86_400)
    state = await ck.load(inputs_hash=hash_of(params))
    if "envelope" not in state:
        state["envelope"] = derive_envelope(...)
        await ck.save(state, inputs_hash=hash_of(params))
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from services.common.logging import get_logger
from services.common.queue import RedisLike

log = get_logger("checkpoint")

CHECKPOINT_KEY_TEMPLATE = "garh:job:%s:checkpoint"
#: A checkpoint bigger than this is a design mistake; refuse rather than bloat Redis.
MAX_CHECKPOINT_BYTES = 256 * 1024


def inputs_hash(payload: Any) -> str:
    """Stable hash of a job's inputs.

    Canonical JSON with sorted keys, matching the model core's
    ``garh-canonical-json/v1`` separators so the two never disagree about what "the
    same inputs" means.
    """
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class JobCheckpoint:
    """Read/write the resumable state of one job."""

    def __init__(self, redis: RedisLike, job_id: str, *, ttl_seconds: int = 86_400) -> None:
        self.redis = redis
        self.job_id = job_id
        self.ttl_seconds = ttl_seconds
        self.key = CHECKPOINT_KEY_TEMPLATE % job_id

    async def load(self, *, inputs_hash: str | None = None) -> dict[str, Any]:
        """Previously saved state, or ``{}``.

        Returns ``{}`` when ``inputs_hash`` differs from the stored one — a resumed
        job must never mix results computed from different inputs.
        """
        try:
            raw = await self.redis.get(self.key)
        except Exception as exc:
            log.warning("checkpoint.load_failed", job_id=self.job_id, error=str(exc))
            return {}
        if raw is None:
            return {}
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            log.warning("checkpoint.corrupt", job_id=self.job_id)
            return {}
        if not isinstance(parsed, dict):
            return {}
        stored_hash = parsed.get("inputsHash")
        if inputs_hash is not None and stored_hash != inputs_hash:
            log.info("checkpoint.stale_inputs", job_id=self.job_id)
            return {}
        state = parsed.get("state")
        if not isinstance(state, dict):
            return {}
        log.info("checkpoint.resumed", job_id=self.job_id, stages=sorted(state))
        return dict(state)

    async def save(self, state: dict[str, Any], *, inputs_hash: str | None = None) -> None:
        """Persist state. Oversized checkpoints are dropped with a loud warning."""
        payload = json.dumps(
            {"inputsHash": inputs_hash, "state": state},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        if len(payload.encode("utf-8")) > MAX_CHECKPOINT_BYTES:
            log.error(
                "checkpoint.too_large",
                job_id=self.job_id,
                bytes=len(payload),
                limit=MAX_CHECKPOINT_BYTES,
                hint="store the intermediate in object storage and checkpoint its key",
            )
            return
        try:
            await self.redis.set(self.key, payload, ex=self.ttl_seconds)
        except Exception as exc:
            log.warning("checkpoint.save_failed", job_id=self.job_id, error=str(exc))

    async def clear(self) -> None:
        """Drop the checkpoint. Called once a job reaches a terminal state."""
        try:
            await self.redis.delete(self.key)
        except Exception as exc:
            log.warning("checkpoint.clear_failed", job_id=self.job_id, error=str(exc))


class NullCheckpoint(JobCheckpoint):
    """In-memory checkpoint for tests and for handlers invoked directly."""

    def __init__(self, job_id: str = "test-job") -> None:
        self.job_id = job_id
        self.ttl_seconds = 0
        self.key = CHECKPOINT_KEY_TEMPLATE % job_id
        self._state: dict[str, Any] = {}
        self._hash: str | None = None

    async def load(self, *, inputs_hash: str | None = None) -> dict[str, Any]:
        if inputs_hash is not None and self._hash is not None and inputs_hash != self._hash:
            return {}
        return dict(self._state)

    async def save(self, state: dict[str, Any], *, inputs_hash: str | None = None) -> None:
        self._state = dict(state)
        self._hash = inputs_hash

    async def clear(self) -> None:
        self._state = {}
        self._hash = None


__all__ = [
    "CHECKPOINT_KEY_TEMPLATE",
    "MAX_CHECKPOINT_BYTES",
    "JobCheckpoint",
    "NullCheckpoint",
    "inputs_hash",
]
