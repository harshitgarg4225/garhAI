"""Comparing two versions of a design (C-8).

The op log has always supported branching and nothing surfaced it. An architect holding
two options wants one answer — *what is actually different* — in elements rather than in
ops: "this wall moved, that room grew, FAR went from 1.42 to 1.58", not a list of 87 log
entries.

Two claims in the response are easy to get subtly wrong and would be believed anyway:

  * **"No change"** must mean "no change in the things I compared", and the response has
    to say which those are. Otherwise a screen renders "identical" over two plans that
    differ in something the diff never looked at.
  * **A change that cannot be drawn** — a moved furniture item, whose footprint lives in
    the catalogue rather than the model — must be counted and returned, not dropped. A
    diff that quietly discards a change is the gate that never fires.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# ``services/`` lives at the repo root — on PYTHONPATH in CI and in the API image, but
# not when pytest is started from apps/api. Same bootstrap as test_copilot.py.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.helpers import op_payload  # noqa: E402

pytestmark = pytest.mark.integration


def _body(ops: list[dict[str, Any]], base_idx: int, **extra: Any) -> dict[str, Any]:
    return {"ops": ops, "baseIdx": base_idx, "source": "manual", **extra}


async def _append(client: Any, api: str, project_id: Any, headers: Any, **kwargs: Any) -> Any:
    return await client.post(
        "%s/projects/%s/ops" % (api, project_id), json=_body(**kwargs), headers=headers
    )


async def _save(client: Any, api: str, project_id: Any, headers: Any, name: str) -> str:
    """Save a named version and hand back its id."""
    saved = await client.post(
        "%s/projects/%s/versions" % (api, project_id),
        json={"name": name},
        headers=headers,
    )
    assert saved.status_code in (200, 201), saved.text
    return str(saved.json()["id"])


async def _compare(client: Any, api: str, project_id: Any, headers: Any, a: str, b: str) -> Any:
    response = await client.get(
        "%s/projects/%s/versions/compare?a=%s&b=%s" % (api, project_id, a, b),
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _two_versions(client: Any, api: str, firm_a: Any, project_a: Any) -> tuple[str, str]:
    """One version, one plot rotation, a second version."""
    await _append(
        client,
        api,
        project_a.id,
        firm_a.headers,
        ops=[op_payload("plot.set_north", deg=0)],
        base_idx=-1,
    )
    first = await _save(client, api, project_a.id, firm_a.headers, "Option A")
    await _append(
        client,
        api,
        project_a.id,
        firm_a.headers,
        ops=[op_payload("plot.set_north", deg=90)],
        base_idx=0,
    )
    second = await _save(client, api, project_a.id, firm_a.headers, "Option B")
    return first, second


async def test_two_versions_can_be_compared_and_name_themselves(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    first, second = await _two_versions(client, api, firm_a, project_a)
    body = await _compare(client, api, project_a.id, firm_a.headers, first, second)
    assert body["a"]["id"] == first
    assert body["b"]["id"] == second
    assert body["a"]["name"] == "Option A"
    assert body["b"]["name"] == "Option B"


async def test_it_says_what_it_compared_and_what_it_did_not(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """The claim that makes "no change" safe to render.

    Without this a screen shows "identical" for two plans that differ in something the
    diff never looked at — and an architect believes it.
    """
    first, second = await _two_versions(client, api, firm_a, project_a)
    body = await _compare(client, api, project_a.id, firm_a.headers, first, second)

    assert "wall" in body["comparedKinds"]
    assert "furniture" in body["comparedKinds"], "a version compare must see furniture"
    assert "slab" not in body["comparedKinds"]
    # ...and the exclusion carries its reason, so the UI can say WHY rather than just
    # listing a word.
    assert body["excludedKinds"]["slab"]


async def test_rotating_the_plot_is_not_a_geometric_change_to_the_house(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """North is a plot property. Nothing in the house moved, and the compare says so
    rather than inventing a difference to look busy."""
    first, second = await _two_versions(client, api, firm_a, project_a)
    body = await _compare(client, api, project_a.id, firm_a.headers, first, second)
    assert body["changes"] == []
    assert body["summary"] == "no geometric change"


async def test_comparing_a_version_with_itself_reports_nothing(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """The identity case. A compare that showed differences here would be measuring its
    own reconstruction rather than the design."""
    first, _second = await _two_versions(client, api, firm_a, project_a)
    body = await _compare(client, api, project_a.id, firm_a.headers, first, first)
    assert body["counts"] == {}
    assert body["changes"] == []
    assert body["unplaced"] == []


async def test_a_version_from_another_project_is_a_404(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """A version id is not a capability. Belonging to the caller's firm is not enough —
    it must belong to THIS project, or one project's compare reads another's design."""
    first, second = await _two_versions(client, api, firm_a, project_a)
    other = await client.post(
        "%s/projects" % api, json={"name": "Different project"}, headers=firm_a.headers
    )
    assert other.status_code == 201, other.text
    response = await client.get(
        "%s/projects/%s/versions/compare?a=%s&b=%s" % (api, other.json()["id"], first, second),
        headers=firm_a.headers,
    )
    assert response.status_code == 404, response.text


async def test_a_version_that_does_not_exist_is_a_404(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    first, _second = await _two_versions(client, api, firm_a, project_a)
    response = await client.get(
        "%s/projects/%s/versions/compare?a=%s&b=%s"
        % (api, project_a.id, first, "00000000-0000-4000-8000-000000000000"),
        headers=firm_a.headers,
    )
    assert response.status_code == 404, response.text


async def test_both_version_ids_are_required(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """Defaulting the missing side to "latest" would be a compare whose meaning changes
    every time someone else edits the project."""
    first, _second = await _two_versions(client, api, firm_a, project_a)
    response = await client.get(
        "%s/projects/%s/versions/compare?a=%s" % (api, project_a.id, first),
        headers=firm_a.headers,
    )
    assert response.status_code == 422, response.text
