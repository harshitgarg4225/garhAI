"""Negative tests for the progress reporter's TERMINAL events.

Why these exist: ``failed()`` and ``dead_lettered()`` splatted the problem dict
into ``emit(..., message=..., **problem)`` — and every real problem dict carries
``message`` (``user_facing`` guarantees it), so the splat collided with the
explicit keyword and the FAILURE REPORTER ITSELF RAISED. The job then died with
an unretrieved task exception and sat "running" forever under a healthy-looking
worker. Static review passed this for months; the solver's first real job found
it in seconds. Per the repo rule, the fix ships with the test that fails
against the old code.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from services.common.envelope import JobEnvelope
from services.common.progress import ProgressReporter
from services.common.queue import RedisLike

pytestmark = pytest.mark.asyncio


class _RecordingRedis:
    """The minimal async surface ``ProgressReporter`` touches, recording streams."""

    def __init__(self) -> None:
        self.stream_events: list[dict[str, Any]] = []

    async def incr(self, key: str) -> int:
        return 1

    async def expire(self, key: str, ttl: int) -> None:
        return None

    async def rpush(self, key: str, value: str) -> None:
        return None

    async def ltrim(self, key: str, start: int, stop: int) -> None:
        return None

    async def publish(self, channel: str, value: str) -> None:
        return None

    async def xadd(self, stream: str, fields: dict[str, Any], **kwargs: Any) -> None:
        self.stream_events.append(dict(fields))


def _reporter(redis: _RecordingRedis) -> ProgressReporter:
    envelope = JobEnvelope(job_id="job-1", kind="solver.generate", firm_id="firm-1")
    # The fake records the subset of RedisLike the reporter touches; the cast is
    # the test's statement that this subset is the contract under test.
    return ProgressReporter(cast(RedisLike, redis), envelope)


#: What ``user_facing`` actually produces for every classified error — the
#: ``message`` key is the one that collided with ``emit``'s explicit kwarg.
FULL_PROBLEM = {
    "code": "invalid_job",
    "message": "This solve request is missing some of its details.",
    "action": "Start it again from the app.",
}


async def test_failed_accepts_a_full_problem_dict() -> None:
    redis = _RecordingRedis()
    event = await _reporter(redis).failed(dict(FULL_PROBLEM))

    assert event.type == "failed"
    assert event.message == FULL_PROBLEM["message"]
    # code/action survive as event data; message is not duplicated inside it.
    assert event.data["code"] == "invalid_job"
    assert event.data["action"] == FULL_PROBLEM["action"]
    assert "message" not in event.data


async def test_dead_lettered_accepts_a_full_problem_dict() -> None:
    redis = _RecordingRedis()
    event = await _reporter(redis).dead_lettered(dict(FULL_PROBLEM))

    assert event.type == "dead_lettered"
    assert event.message == FULL_PROBLEM["message"]
    assert event.data["code"] == "invalid_job"


async def test_failed_reaches_the_lifecycle_stream() -> None:
    """The API consumes ``garh:events:jobs`` to flip the job row to failed —
    a failure event that never lands there is a job stuck "running"."""
    redis = _RecordingRedis()
    await _reporter(redis).failed(dict(FULL_PROBLEM))

    assert len(redis.stream_events) == 1
    assert redis.stream_events[0].get("type") == "failed"
