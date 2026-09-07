"""A drawing set is of the design on the screen.

Sheets and exports are pinned to a design version so an area statement can be
reproduced later. The pin used to be "the latest version, else 409 save one first" —
and the product has no save-version button. The first browser UAT started a project
from a ready-made plan, pressed "Generate the set" and read "There's no saved version
of this design to draw yet." A project with an old checkpoint had it worse: the set
was silently drawn at that checkpoint, however far the design had moved since.

Now the route mints a checkpoint at the head when the head has none, reuses the
version that already IS the head, and refuses only a project with nothing to draw.
"""

from __future__ import annotations

from typing import Any

import pytest
from garh_api.repositories import DesignVersionRepository, OpRepository
from garh_api.repositories.ops import NewOp

from tests import factories
from tests.helpers import main_branch, problem

pytestmark = pytest.mark.integration


async def _latest(session: Any, actor: Any, project_id: Any) -> Any:
    return await DesignVersionRepository(session, actor.ctx()).latest(
        project_id, main_branch(project_id)
    )


async def _version_count(session: Any, actor: Any, project_id: Any) -> int:
    page = await DesignVersionRepository(session, actor.ctx()).list_timeline(project_id)
    return len(page.items)


async def _head(session: Any, actor: Any, project_id: Any) -> tuple[int, int | None]:
    repo = OpRepository(session, actor.ctx())
    branch = main_branch(project_id)
    return await repo.head_idx(project_id, branch), await repo.head_seq(project_id, branch)


async def _generate(client: Any, api: str, actor: Any, project_id: Any) -> Any:
    return await client.post(
        "%s/projects/%s/sheets/generate" % (api, project_id),
        json={"sheetSize": "A2"},
        headers=actor.headers,
    )


async def test_a_project_with_no_plan_cannot_be_drawn_and_says_what_to_do(
    client: Any, api: str, session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """No ops, no version: the one honest refusal left, and it must not mint anything."""
    response = await _generate(client, api, firm_a, project_a.id)
    assert response.status_code == 409, response.text
    body = problem(response)
    assert body["code"] == "no_design_version"
    assert "nothing to draw" in body["message"].lower()
    assert "generate" in body["action"].lower()
    assert "save a version" not in body["action"].lower(), "there is no such button"
    assert await _latest(session, firm_a, project_a.id) is None


async def test_a_drawn_project_with_no_saved_version_gets_one_minted_at_its_head(
    client: Any, api: str, session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """The ready-made-plan case: sixty-odd ops on the branch, never a version row."""
    await factories.seed_plot_and_brief(session, firm_a, project_a.id)
    assert await _latest(session, firm_a, project_a.id) is None

    response = await _generate(client, api, firm_a, project_a.id)
    assert response.status_code == 202, response.text

    version = await _latest(session, firm_a, project_a.id)
    assert version is not None, "the set was queued against no version"
    head_idx, head_seq = await _head(session, firm_a, project_a.id)
    assert version.kind == "auto"
    assert version.op_seq_end == head_seq
    assert version.snapshot is not None and version.snapshot["atIdx"] == head_idx
    assert version.snapshot["doc"]["house"]["storeys"], "the snapshot is the folded design"
    assert response.json()["designVersionId"] == str(version.id)


async def test_the_set_is_of_the_head_not_of_a_stale_checkpoint(
    client: Any, api: str, session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """Edit after a version: the next set gets a NEW version at the new head.

    The old behaviour — reuse the latest version whatever the head — drew the design
    as it was hundreds of ops ago and called it current. The negative control at the
    end: with no edit in between, no new version is minted.
    """
    await factories.seed_plot_and_brief(session, firm_a, project_a.id)
    first = await _generate(client, api, firm_a, project_a.id)
    assert first.status_code == 202, first.text
    first_version = await _latest(session, firm_a, project_a.id)
    assert first_version is not None

    head_idx, _ = await _head(session, firm_a, project_a.id)
    await factories.append_ops(
        session,
        firm_a,
        project_a.id,
        [NewOp(type="plot.set_north", payload={"deg": 90})],
        base_idx=head_idx,
    )

    second = await _generate(client, api, firm_a, project_a.id)
    assert second.status_code == 202, second.text
    second_version = await _latest(session, firm_a, project_a.id)
    assert second_version is not None
    assert second_version.id != first_version.id, "drawn at the stale checkpoint"
    _, head_seq = await _head(session, firm_a, project_a.id)
    assert second_version.op_seq_end == head_seq
    assert second.json()["designVersionId"] == str(second_version.id)

    # Negative control: nothing changed, so nothing new is minted.
    before = await _version_count(session, firm_a, project_a.id)
    third = await _generate(client, api, firm_a, project_a.id)
    assert third.status_code == 202, third.text
    assert third.json()["designVersionId"] == str(second_version.id)
    assert await _version_count(session, firm_a, project_a.id) == before


async def test_an_export_pins_the_same_way(
    client: Any, api: str, session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """DXF export of a project that was only ever edited, never versioned."""
    await factories.seed_plot_and_brief(session, firm_a, project_a.id)
    response = await client.post(
        "%s/projects/%s/export" % (api, project_a.id),
        json={"kind": "dxf"},
        headers=firm_a.headers,
    )
    assert response.status_code == 202, response.text
    version = await _latest(session, firm_a, project_a.id)
    assert version is not None
    assert response.json()["designVersionId"] == str(version.id)
