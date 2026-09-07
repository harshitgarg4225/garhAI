"""A terminal SSE frame must not outrun the job row it announces.

The worker publishes its last progress event on pub/sub and appends the lifecycle
record in the same breath; the API's consumer writes the row a few tens of
milliseconds later. A browser that re-reads the row on the terminal frame saw
"running", overwrote the outcome with it, and — the stream having closed — waited
forever. The frames generator now holds the terminal frame until the row agrees.
"""

from __future__ import annotations

from typing import Any

from garh_api import queue
from garh_api.routers.jobs import _row_has_settled, job_event_frames


class _Request:
    async def is_disconnected(self) -> bool:
        return False


class _Initial:
    status = "running"

    def model_dump_json(self, **_: Any) -> str:
        return '{"status":"running"}'


def _events(job_id: str) -> list[queue.ProgressEvent]:
    return [
        queue.ProgressEvent(job_id=job_id, type="progress", seq=1, stage="packing"),
        queue.ProgressEvent(job_id=job_id, type="succeeded", seq=2, data={"options": []}),
    ]


async def _frames(
    monkeypatch: Any, job_id: str, row_status: Any, settle: float
) -> list[dict[str, Any]]:
    async def fake_stream(_job_id: Any, *, after_seq: int = 0) -> Any:
        for event in _events(job_id):
            if event.seq > after_seq:
                yield event

    monkeypatch.setattr(queue, "progress_stream", fake_stream)
    out: list[dict[str, Any]] = []
    async for frame in job_event_frames(
        _Request(), job_id, _Initial(), 0, row_status, settle_timeout=settle
    ):
        out.append(frame)
    return out


async def test_the_terminal_frame_waits_until_the_row_agrees(monkeypatch: Any) -> None:
    reads: list[str] = []

    async def row_status() -> str | None:
        # The consumer lands the row on the third read — the race the UAT hit.
        reads.append("read")
        return "succeeded" if len(reads) >= 3 else "running"

    frames = await _frames(monkeypatch, "job-1", row_status, settle=2.0)
    assert [f["event"] for f in frames] == ["state", "progress", "done"]
    assert len(reads) == 3, "the terminal frame went out before the row was terminal"


async def test_without_a_reader_the_frames_are_unchanged(monkeypatch: Any) -> None:
    """Negative control: the wait is opt-in, so an endpoint that passes no reader
    streams exactly as before."""
    frames = await _frames(monkeypatch, "job-2", None, settle=2.0)
    assert [f["event"] for f in frames] == ["state", "progress", "done"]


async def test_a_row_that_never_settles_does_not_hide_the_workers_last_word(
    monkeypatch: Any,
) -> None:
    async def stuck() -> str | None:
        return "running"

    frames = await _frames(monkeypatch, "job-3", stuck, settle=0.15)
    assert [f["event"] for f in frames][
        -1
    ] == "done", "a down consumer must not swallow the outcome"


async def test_row_has_settled_reports_the_deadline_honestly() -> None:
    calls = 0

    async def flips_late() -> str | None:
        nonlocal calls
        calls += 1
        return "failed" if calls >= 2 else "queued"

    assert await _row_has_settled(flips_late, settle_seconds=1.0, poll=0.01) is True

    async def never() -> str | None:
        return "running"

    assert await _row_has_settled(never, settle_seconds=0.05, poll=0.01) is False

    async def broken() -> str | None:
        raise RuntimeError("db hiccup")

    # A read failure is a wait, not a crash of the stream.
    assert await _row_has_settled(broken, settle_seconds=0.05, poll=0.01) is False
