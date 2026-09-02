"""The ``started`` event can arrive before the row it describes is committed.

The worker takes the job off Redis the moment the api enqueues it — inside the api's
still-open transaction — so the consumer's first apply of ``started`` can find no job
row (``job_events.apply_failed … was not found`` on every live solve). The row exists
a few hundred milliseconds later. This pins that the consumer waits for it, bounded.
"""

from __future__ import annotations

from typing import Any

import pytest
from garh_api import queue
from garh_api.routers.jobs import apply_lifecycle_record_with_retry
from garh_api.tenancy import EntityNotFoundError


def _record() -> queue.LifecycleRecord:
    return queue.LifecycleRecord(
        entry_id="0-1",
        job_id="00000000-0000-0000-0000-000000000001",
        kind="solver.generate",
        firm_id="00000000-0000-0000-0000-0000000000aa",
        project_id=None,
        design_version_id=None,
        type="started",
        attempt=1,
        event=queue.ProgressEvent(job_id="00000000-0000-0000-0000-000000000001", type="started"),
    )


class _RowArrivesLater:
    """Raises not-found ``misses`` times, then applies."""

    def __init__(self, misses: int) -> None:
        self.misses = misses
        self.calls = 0

    async def __call__(self, session: Any, record: queue.LifecycleRecord) -> bool:
        self.calls += 1
        if self.calls <= self.misses:
            raise EntityNotFoundError("solver_job", record.job_id)
        return True


@pytest.mark.integration
async def test_the_consumer_waits_for_the_row_to_be_committed() -> None:
    apply = _RowArrivesLater(misses=2)
    await apply_lifecycle_record_with_retry(_record(), attempts=4, delay_seconds=0.0, apply=apply)
    assert apply.calls == 3


@pytest.mark.integration
async def test_the_wait_is_bounded() -> None:
    """A row that never appears is still an error — the entry must stay pending, not spin."""
    apply = _RowArrivesLater(misses=99)
    with pytest.raises(EntityNotFoundError):
        await apply_lifecycle_record_with_retry(
            _record(), attempts=3, delay_seconds=0.0, apply=apply
        )
    assert apply.calls == 3


@pytest.mark.integration
async def test_other_errors_are_not_retried() -> None:
    """Only the not-yet-committed case is transient; a real fault must surface at once."""

    async def broken(session: Any, record: queue.LifecycleRecord) -> bool:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await apply_lifecycle_record_with_retry(
            _record(), attempts=5, delay_seconds=0.0, apply=broken
        )
