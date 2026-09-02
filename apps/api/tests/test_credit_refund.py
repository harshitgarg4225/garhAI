"""A job that never delivered gives its credit back — and a delivered one keeps it.

Execution find, first trial architect (2026-09-02): two generates died in a worker
image that could not open its catalogue, and the usage page counted both against the
ten free generations. Credits are charged at ENQUEUE (the quota and the spend cap must
answer then); this file pins that the terminal lifecycle events which mean "nothing
was delivered" — failed, dead_lettered, cancelled — refund that charge through the
same consumer that records the outcome, and that succeeded does not.

Every reader of the ledger is covered: the count quota (usage_by_kind), the money
cap (spent_micros), the billing page (GET /billing/usage).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from garh_api import queue
from garh_api.repositories import CreditEventRepository
from garh_api.routers.jobs import apply_lifecycle_record

from tests.factories import create_project, create_render_job, create_solver_job


def _lifecycle(
    job: Any, firm_id: Any, kind: str, event_type: str, **data: Any
) -> queue.LifecycleRecord:
    """A worker lifecycle record, exactly as ``read_job_events`` would parse one."""
    return queue.LifecycleRecord(
        entry_id="0-1",
        job_id=str(job.id),
        kind=kind,
        firm_id=str(firm_id),
        project_id=str(job.project_id),
        design_version_id=None,
        type=event_type,
        attempt=1,
        event=queue.ProgressEvent(job_id=str(job.id), type=event_type, seq=1, data=data),
    )


FAILED = {"message": "Something went wrong on our side.", "action": "Try again."}


async def _charged_solver_job(session: Any, actor: Any, *, cost_micros: int = 0) -> Any:
    project = await create_project(session, actor)
    job = await create_solver_job(session, actor, project.id)
    await CreditEventRepository(session, actor.ctx()).record(
        kind="solver",
        meta={"jobId": str(job.id), "projectId": str(project.id)},
        cost_micros=cost_micros,
    )
    await session.commit()
    return job


@pytest.mark.integration
async def test_a_failed_generate_gives_the_generation_back(session: Any, firm_a: Any) -> None:
    job = await _charged_solver_job(session, firm_a)
    repo = CreditEventRepository(session, firm_a.ctx())
    assert (await repo.usage_by_kind()).get("solver", 0) == 1

    await apply_lifecycle_record(
        session, _lifecycle(job, firm_a.firm_id, queue.JOB_SOLVER_GENERATE, "failed", **FAILED)
    )
    await session.commit()

    assert (await repo.usage_by_kind()).get("solver", 0) == 0, "the failed generate still counts"
    (event,) = (await repo.list_recent()).items
    assert event.job_id == job.id
    assert event.refunded_at is not None
    assert event.meta.get("refund") == "failed"


@pytest.mark.integration
@pytest.mark.parametrize("outcome", ["dead_lettered", "cancelled"])
async def test_dead_lettered_and_cancelled_refund_too(
    session: Any, firm_a: Any, outcome: str
) -> None:
    job = await _charged_solver_job(session, firm_a)
    await apply_lifecycle_record(
        session, _lifecycle(job, firm_a.firm_id, queue.JOB_SOLVER_GENERATE, outcome, **FAILED)
    )
    await session.commit()
    assert (await CreditEventRepository(session, firm_a.ctx()).usage_by_kind()).get(
        "solver", 0
    ) == 0


@pytest.mark.integration
async def test_a_delivered_generate_keeps_its_credit(session: Any, firm_a: Any) -> None:
    """NEGATIVE CONTROL: a refund that fires on success is a free trial forever."""
    job = await _charged_solver_job(session, firm_a)
    await apply_lifecycle_record(
        session,
        _lifecycle(
            job, firm_a.firm_id, queue.JOB_SOLVER_GENERATE, "succeeded", options=[], message=""
        ),
    )
    await session.commit()
    repo = CreditEventRepository(session, firm_a.ctx())
    assert (await repo.usage_by_kind()).get("solver", 0) == 1
    (event,) = (await repo.list_recent()).items
    assert event.refunded_at is None


@pytest.mark.integration
async def test_a_refund_happens_exactly_once(session: Any, firm_a: Any) -> None:
    """``failed`` then a replayed ``dead_lettered`` must not refund a second row."""
    job = await _charged_solver_job(session, firm_a)
    repo = CreditEventRepository(session, firm_a.ctx())
    assert await repo.refund_for_job(job.id, reason="failed") == 1
    assert await repo.refund_for_job(job.id, reason="dead_lettered") == 0
    await session.commit()
    (event,) = (await repo.list_recent()).items
    assert event.meta.get("refund") == "failed", "the first reason stands"


@pytest.mark.integration
async def test_the_money_cap_reopens_after_a_refund(session: Any, firm_a: Any) -> None:
    """The lifetime spend the $5 cap reads must not include money we never delivered."""
    job = await _charged_solver_job(session, firm_a, cost_micros=5_000_000)
    repo = CreditEventRepository(session, firm_a.ctx())
    assert await repo.spent_micros() == 5_000_000
    await apply_lifecycle_record(
        session, _lifecycle(job, firm_a.firm_id, queue.JOB_SOLVER_GENERATE, "failed", **FAILED)
    )
    await session.commit()
    assert await repo.spent_micros() == 0


@pytest.mark.integration
async def test_a_failed_render_refunds_the_same_way(session: Any, firm_a: Any) -> None:
    project = await create_project(session, firm_a)
    job = await create_render_job(session, firm_a, project.id)
    await CreditEventRepository(session, firm_a.ctx()).record(
        kind="render", meta={"jobId": str(job.id), "projectId": str(project.id)}
    )
    await session.commit()
    await apply_lifecycle_record(
        session, _lifecycle(job, firm_a.firm_id, queue.JOB_RENDER_IMAGE, "failed", **FAILED)
    )
    await session.commit()
    assert (await CreditEventRepository(session, firm_a.ctx()).usage_by_kind()).get(
        "render", 0
    ) == 0


@pytest.mark.integration
async def test_the_usage_page_shows_the_refund(
    client: Any, api: str, session: Any, firm_a: Any
) -> None:
    """What the architect is shown is what the gate enforces — after a refund too."""
    job = await _charged_solver_job(session, firm_a)
    before = await client.get("%s/billing/usage" % api, headers=firm_a.headers)
    assert before.status_code == 200, before.text
    solver_before = next(line for line in before.json()["lines"] if line["kind"] == "solver")
    assert solver_before["used"] == 1

    await apply_lifecycle_record(
        session, _lifecycle(job, firm_a.firm_id, queue.JOB_SOLVER_GENERATE, "failed", **FAILED)
    )
    await session.commit()

    after = await client.get("%s/billing/usage" % api, headers=firm_a.headers)
    solver_after = next(line for line in after.json()["lines"] if line["kind"] == "solver")
    assert solver_after["used"] == 0


@pytest.mark.integration
async def test_a_refund_cannot_reach_another_firms_ledger(
    session: Any, firm_a: Any, firm_b: Any
) -> None:
    """The refund is keyed on (firm, job); a job id alone opens no other tenant's row."""
    job = await _charged_solver_job(session, firm_a)
    assert (
        await CreditEventRepository(session, firm_b.ctx()).refund_for_job(job.id, reason="failed")
        == 0
    )
    assert (await CreditEventRepository(session, firm_a.ctx()).usage_by_kind()).get(
        "solver", 0
    ) == 1


def test_unknown_or_missing_job_ids_do_not_break_recording() -> None:
    from garh_api.repositories.credits import _job_id_from_meta

    assert _job_id_from_meta({}) is None
    assert _job_id_from_meta({"jobId": "not-a-uuid"}) is None
    assert _job_id_from_meta({"jobId": 42}) is None
    known = uuid.uuid4()
    assert _job_id_from_meta({"jobId": str(known)}) == known
