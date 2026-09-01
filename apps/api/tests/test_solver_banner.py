"""A solve that produces nothing must say why (§15 tone, and plain honesty).

This is the failure this repository keeps producing in new shapes: a green signal that
carries no information. The solve reported ``succeeded``, ``progress: 100``, zero
options and NO text — a blank screen after a two-minute wait, on a product whose first
interaction is "Generate".

The reason existed the whole time. ``services/solver/pipeline.py`` builds it
(``shortfall_banner``: "the ground floor is 8 m² short" rather than "0 options") and
the worker returns it as ``JobResult.message``; the API's lifecycle consumer read
``options`` out of the terminal event and dropped everything else.

Measured against a live stack before the fix, a 2BHK G+1 on a 30×40 ft plot returned
exactly this and nothing more::

    status : succeeded
    options: 0

and after it::

    banner : The rooms fit this floor by area (20.5 m² needed, 108.0 m² available),
             but no arrangement satisfied every constraint at once. ...
"""

from __future__ import annotations

import pytest

from tests import factories


def _lifecycle(job, firm_id, *, options, message):
    from garh_api import queue

    return queue.LifecycleRecord(
        entry_id="0-1",
        job_id=str(job.id),
        kind=queue.JOB_SOLVER_GENERATE,
        firm_id=str(firm_id),
        project_id=str(job.project_id),
        design_version_id=None,
        type="succeeded",
        attempt=1,
        event=queue.ProgressEvent(
            job_id=str(job.id),
            type="succeeded",
            seq=1,
            percent=100,
            message=message,
            data={"options": options},
        ),
    )


@pytest.mark.integration
async def test_a_solve_with_no_options_still_tells_the_architect_why(
    client, api, session, clean_redis, firm_a, project_a
) -> None:
    from garh_api.routers.jobs import apply_lifecycle_record

    job = await factories.create_solver_job(session, firm_a, project_a.id)
    await session.commit()

    reason = (
        "The rooms fit this floor by area (20.5 m² needed, 108.0 m² available), but no "
        "arrangement satisfied every constraint at once."
    )
    assert await apply_lifecycle_record(
        session, _lifecycle(job, firm_a.firm_id, options=[], message=reason)
    )
    await session.commit()

    fetched = await client.get("%s/solver-jobs/%s" % (api, job.id), headers=firm_a.headers)
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["status"] == "succeeded"
    assert body["options"] in ([], None)
    # The whole point: something to read, and something to act on.
    assert body["banner"] == reason
    # ...and NOT reported as a failure. A succeeded row carrying `error` is how a
    # normal outcome gets a red banner.
    assert body["error"] is None


@pytest.mark.integration
async def test_the_banner_is_carried_when_options_DO_come_back_too(
    client, api, session, clean_redis, firm_a, project_a
) -> None:
    """NEGATIVE CONTROL for the case above.

    Without it, a consumer that only set the banner on the empty path would pass —
    and "we found 2 of the 3 you asked for, here is why" would silently vanish, which
    is the §5.6 honest-banner case the Options panel was built around.
    """
    from garh_api.routers.jobs import apply_lifecycle_record

    job = await factories.create_solver_job(session, firm_a, project_a.id)
    await session.commit()

    reason = "We found 2 options; the third could not clear the daylight rule."
    assert await apply_lifecycle_record(
        session,
        _lifecycle(
            job,
            firm_a.firm_id,
            options=[{"id": "a", "rank": 1}, {"id": "b", "rank": 2}],
            message=reason,
        ),
    )
    await session.commit()

    body = (await client.get("%s/solver-jobs/%s" % (api, job.id), headers=firm_a.headers)).json()
    assert len(body["options"]) == 2
    assert body["banner"] == reason
