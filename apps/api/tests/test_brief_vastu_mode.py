"""Saving a brief with a Vastu mode must actually turn the Vastu pack on.

Found by the plan-library verification (2026-09-03): ``PUT /brief`` wrote
``vastuMode`` into the data patch, the fold reads it from the op's own field, so the
folded brief stayed ``off`` and ``packs_for`` never loaded ``vastu`` — while the
solver, reading the stray key in ``data``, optimised for advisory. Two readers, two
answers. This pins the one write path.
"""

from __future__ import annotations

from typing import Any

import pytest
from garh_api.compliance import packs_for
from garh_model import ProjectDoc, replay

from tests.factories import create_project


async def _folded(client: Any, api: str, actor: Any, project_id: str) -> dict[str, Any]:
    state = await client.get("%s/projects/%s/model" % (api, project_id), headers=actor.headers)
    assert state.status_code == 200, state.text
    body = state.json()
    initial = ProjectDoc.from_json(body["snapshot"]) if body.get("snapshot") is not None else None
    tail = [{"type": op["type"], "payload": op["payload"]} for op in body.get("ops") or []]
    return replay(tail, initial=initial).to_json()


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["advisory", "strict"])
async def test_the_saved_vastu_mode_reaches_the_fold_and_the_pack_set(
    client: Any, api: str, session: Any, firm_a: Any, mode: str
) -> None:
    project = await create_project(session, firm_a)
    response = await client.put(
        "%s/projects/%s/brief" % (api, project.id),
        json={"data": {"rooms": [{"type": "kitchen", "count": 1}]}, "vastuMode": mode},
        headers=firm_a.headers,
    )
    assert response.status_code in (200, 201), response.text
    assert response.json()["vastuMode"] == mode, "the projection row"

    document = await _folded(client, api, firm_a, str(project.id))
    assert document["brief"]["vastuMode"] == mode, "the folded document"
    assert "vastuMode" not in (document["brief"].get("data") or {}), "no stray key in data"
    assert "vastu" in packs_for(document), "the pack the compliance report loads"


@pytest.mark.integration
async def test_off_is_the_honest_default_when_nothing_is_said(
    client: Any, api: str, session: Any, firm_a: Any
) -> None:
    project = await create_project(session, firm_a)
    response = await client.put(
        "%s/projects/%s/brief" % (api, project.id),
        json={"data": {"rooms": [{"type": "kitchen", "count": 1}]}},
        headers=firm_a.headers,
    )
    assert response.status_code in (200, 201), response.text
    document = await _folded(client, api, firm_a, str(project.id))
    assert document["brief"]["vastuMode"] == "off"
    assert "vastu" not in packs_for(document)
