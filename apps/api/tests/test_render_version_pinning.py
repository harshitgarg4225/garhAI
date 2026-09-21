"""A render is pinned to a version the project may never have cut for itself.

WHY THIS FILE EXISTS, when ``test_render_jobs.py`` already covers renders end to
end. Every test in that file opens with ``factories.create_version(...)`` — it hands
the route the very thing the route's job is to find. So the route's other branch, the
one taken by every real project, had never been executed by anything.

That branch was wrong. It resolved the pin with a look-only helper that answers
``None`` when a project has ops but no checkpoint, and ``None`` is exactly what the
render worker refuses::

    render jobs must carry designVersionId (§9: results pinned to it)

The product has no "save a version" button, and a project started from a ready-made
plan — the front door of the New-project dialog — appends its ops without ever cutting
one. So for the architect most likely to press Render first, the API answered 202, the
UI said "Render started", a credit was spent, and the job died in a worker process no
part of the web app was watching. Timed in a browser: nothing ever arrived.

The three cases here are the three states a project can be in, and the middle one is
the one that was broken:

1. a project with a version → pinned to it (and NOT to a freshly minted one);
2. a project with ops and no version → a version is minted, and the envelope carries
   it, because that is what the worker reads;
3. a project with nothing at all → 409 before a row is written, a credit charged or an
   envelope queued. That last one is the negative control: a fix that minted a version
   unconditionally would turn an empty project's render into a job about an empty
   house, which is worse than a refusal.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from garh_api.repositories import (  # noqa: E402
    DesignVersionRepository,
    RenderJobRepository,
)

from tests import factories  # noqa: E402
from tests.helpers import op_payload, problem  # noqa: E402

pytestmark = pytest.mark.integration

TINY_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4//8/AwAI/AL+p5qgoAAAAABJRU5ErkJggg=="


def _render_body() -> dict[str, Any]:
    return {
        "mode": "explore",
        "preset": "exterior-street-day",
        "seed": 7,
        "width": 512,
        "height": 512,
        "view": {"preset": "exterior-street-day", "fovDeg": 45},
        "inputs": {"viewportPng": TINY_PNG_B64},
    }


async def _draw_something(client: Any, api: str, project_id: Any, headers: Any) -> None:
    """Append one op, the way the app does. No version is cut — nothing cuts one."""
    response = await client.post(
        "%s/projects/%s/ops" % (api, project_id),
        json={"ops": [op_payload("plot.set_north", deg=30)], "baseIdx": -1, "source": "manual"},
        headers=headers,
    )
    assert response.status_code == 200, response.text


async def test_a_project_with_ops_and_no_version_still_renders(
    client: Any, api: str, session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """THE BUG. Ops, no checkpoint — a ready-made plan, a DXF import, a hand-drawn plan."""
    await _draw_something(client, api, project_a.id, firm_a.headers)
    await session.rollback()  # the HTTP call committed in its own session
    assert (
        await DesignVersionRepository(session, firm_a.ctx()).latest(project_a.id)
    ) is None, "the fixture is not in the state this test is about"

    response = await client.post(
        "%s/projects/%s/renders" % (api, project_a.id),
        json=_render_body(),
        headers=firm_a.headers,
    )
    assert response.status_code == 202, response.text
    pinned = response.json()["designVersionId"]
    assert pinned, (
        "the render was accepted with no design version. The worker refuses exactly "
        "this job, so the architect gets a success toast and no image, ever."
    )

    # The response body is a serialisation; the ROW is what the queue envelope and
    # the history pane are both built from, so that is what gets asserted.
    #
    # Not the Redis envelope, deliberately: any render worker pointed at this Redis
    # pops it within milliseconds, and a developer with the dev stack up would see
    # this test fail for a reason that has nothing to do with what it is about. The
    # envelope's designVersionId is covered in test_render_jobs.py, where a version
    # is supplied and no worker is running.
    await session.rollback()
    row = await RenderJobRepository(session, firm_a.ctx()).require(uuid.UUID(response.json()["id"]))
    assert row.design_version_id is not None and str(row.design_version_id) == pinned, (
        "the job row the worker's envelope is built from carries no version: %s"
        % (row.design_version_id,)
    )

    # ...and the version is real, not an id invented to satisfy the field.
    await session.rollback()
    minted = await DesignVersionRepository(session, firm_a.ctx()).require(uuid.UUID(pinned))
    assert minted.project_id == project_a.id


async def test_an_existing_version_is_reused_not_re_minted(
    client: Any, api: str, session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """CONTROL ONE. Minting on every render would orphan history and break §9's stale
    flag: two renders of an untouched design must be renders of the SAME version."""
    version = await factories.create_version(session, firm_a, project_a.id)

    first = await client.post(
        "%s/projects/%s/renders" % (api, project_a.id),
        json=_render_body(),
        headers=firm_a.headers,
    )
    assert first.status_code == 202, first.text
    assert first.json()["designVersionId"] == str(version.id)

    second = await client.post(
        "%s/projects/%s/renders" % (api, project_a.id),
        json=_render_body(),
        headers=firm_a.headers,
    )
    assert second.status_code == 202, second.text
    assert second.json()["designVersionId"] == str(version.id), (
        "a second render of an unchanged design was pinned to a different version — "
        "the history pane would show two renders of 'different' designs"
    )


async def test_NEGATIVE_CONTROL_an_empty_project_is_refused_before_it_costs_anything(
    client: Any, api: str, session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """CONTROL TWO. No ops, no version: there is no building to photograph.

    This is what stops the fix from becoming "always mint": a checkpoint of an empty
    document would send the worker off to render nothing and charge for the privilege.
    A 409 is the honest answer, and it must arrive before the row, the credit and the
    envelope.
    """
    response = await client.post(
        "%s/projects/%s/renders" % (api, project_a.id),
        json=_render_body(),
        headers=firm_a.headers,
    )
    assert response.status_code == 409, response.text
    detail = problem(response)
    assert detail["code"] == "no_design_version"
    # The old copy said "Save the design first" — advice for a button this product
    # does not have, and will not have.
    assert "save" not in detail["action"].lower(), detail["action"]

    assert clean_redis.lrange("garh:queue:render", 0, -1) == [], "a doomed job was queued"

    # Billing is asserted through the route the billing page reads, so this cannot
    # pass against a stale session while the firm is in fact being charged.
    usage = await client.get("%s/billing/usage" % api, headers=firm_a.headers)
    assert usage.status_code == 200, usage.text
    rendered = [line for line in usage.json()["lines"] if line["kind"] == "render"]
    assert rendered and rendered[0]["used"] == 0, "a refused render was billed: %s" % (rendered,)
