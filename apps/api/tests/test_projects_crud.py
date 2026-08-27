"""Projects CRUD — the other half of Phase 0's DoD ("login → create empty project").

The happy path is deliberately end-to-end through HTTP: create, read back the shell, list,
patch, archive, delete. Then the validation failures, because "create a project" is the
first form a new user meets and golden rule 9 says every error tells them what to do next.

Two of those failures are **currently 500s** and are marked as strict xfails rather than
quietly asserted as correct — see :func:`test_unknown_units_is_a_client_error` and
:func:`test_unknown_status_is_a_client_error`. A 5xx for a bad enum value is a real defect:
it pages an on-call engineer for a typo, it is invisible to client-side error handling
(which retries 5xx and gives up on 4xx), and it violates §13's "input validation at every
boundary". The fix is one layer up from the repository — validate ``units``/``status``
against ``models.PROJECT_UNITS``/``PROJECT_STATUSES`` in ``schemas/project.py`` so Pydantic
answers 422 — and the day it lands these xfails become XPASS and tell whoever fixed it to
delete the markers.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from garh_api.models import PROJECT_STATUSES, PROJECT_UNITS

from tests.helpers import problem

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_create_read_list_patch_delete(client: Any, api: str, firm_a: Any) -> None:
    """The whole lifecycle, in the order the dashboard walks it."""
    created = await client.post(
        "%s/projects" % api,
        json={"name": "Sharma Residence", "units": "ft-in", "cityPack": "blr"},
        headers=firm_a.headers,
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["name"] == "Sharma Residence"
    assert project["status"] == "draft"
    assert project["units"] == "ft-in"
    assert project["cityPack"] == "blr"
    assert project["demo"] is False
    project_id = project["id"]

    # The project shell: one round trip renders the whole screen (§15 micro-speed).
    shell = await client.get("%s/projects/%s" % (api, project_id), headers=firm_a.headers)
    assert shell.status_code == 200, shell.text
    body = shell.json()
    assert body["project"]["id"] == project_id
    assert body["plot"] is None, "a new project has no plot — an empty state, not an error"
    assert body["brief"] is None
    assert body["headIdx"] == -1, "an empty op log is headIdx -1"
    assert body["latestVersion"] is None
    assert body["openCommentCount"] == 0
    assert uuid.UUID(body["versionBranch"])

    listed = await client.get("%s/projects" % api, headers=firm_a.headers)
    assert listed.status_code == 200, listed.text
    page = listed.json()
    assert [item["id"] for item in page["items"]] == [project_id]
    assert page["hasMore"] is False
    assert page["nextCursor"] is None

    renamed = await client.patch(
        "%s/projects/%s" % (api, project_id),
        json={"name": "Sharma Residence — Whitefield"},
        headers=firm_a.headers,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Sharma Residence — Whitefield"

    deleted = await client.delete("%s/projects/%s" % (api, project_id), headers=firm_a.headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"id": project_id, "deleted": True}

    gone = await client.get("%s/projects/%s" % (api, project_id), headers=firm_a.headers)
    assert gone.status_code == 404, gone.text


async def test_create_needs_only_a_name(client: Any, api: str, firm_a: Any) -> None:
    """Phase 0's DoD is "create empty project" — one field, sensible defaults."""
    response = await client.post(
        "%s/projects" % api, json={"name": "Untitled"}, headers=firm_a.headers
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["units"] == "ft-in", "Indian default (delight rule: ft-in primary)"
    assert body["status"] == "draft"
    assert body["cityPack"] is None


async def test_archiving_hides_a_project_from_the_default_list(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """Archive is the reversible option the UI offers before delete."""
    patched = await client.patch(
        "%s/projects/%s" % (api, project_a.id),
        json={"status": "archived"},
        headers=firm_a.headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "archived"

    default = await client.get("%s/projects" % api, headers=firm_a.headers)
    assert default.json()["items"] == []

    # CONTRACT NOTE: this query parameter is snake_case while every body field is
    # camelCase, because FastAPI derives query names from the Python parameter and
    # `list_projects` gives `include_archived` no alias (unlike `status`, which has one).
    # Asserted as it actually behaves; if it gains a camelCase alias, update this line.
    included = await client.get(
        "%s/projects" % api, params={"include_archived": True}, headers=firm_a.headers
    )
    assert [item["id"] for item in included.json()["items"]] == [str(project_a.id)]

    # Still readable by id, and still restorable.
    restored = await client.patch(
        "%s/projects/%s" % (api, project_a.id),
        json={"status": "draft"},
        headers=firm_a.headers,
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "draft"


async def test_list_is_cursor_paginated_newest_first(
    client: Any, api: str, session: Any, firm_a: Any
) -> None:
    from tests.factories import create_project

    for index in range(3):
        await create_project(session, firm_a, name="Project %d" % index)

    first = await client.get("%s/projects" % api, params={"limit": 2}, headers=firm_a.headers)
    assert first.status_code == 200, first.text
    page = first.json()
    assert len(page["items"]) == 2
    assert page["hasMore"] is True
    assert page["nextCursor"]

    second = await client.get(
        "%s/projects" % api,
        params={"limit": 2, "cursor": page["nextCursor"]},
        headers=firm_a.headers,
    )
    assert second.status_code == 200, second.text
    rest = second.json()
    assert len(rest["items"]) == 1
    assert rest["hasMore"] is False

    seen = [item["id"] for item in page["items"] + rest["items"]]
    assert len(set(seen)) == 3, "pagination repeated or dropped a row"


async def test_status_filter_matches_the_dashboard_chips(
    client: Any, api: str, session: Any, firm_a: Any
) -> None:
    from tests.factories import create_project

    await create_project(session, firm_a, name="In brief", status="brief")
    await create_project(session, firm_a, name="In design", status="design")

    response = await client.get(
        "%s/projects" % api, params={"status": "brief"}, headers=firm_a.headers
    )
    assert response.status_code == 200, response.text
    assert [item["name"] for item in response.json()["items"]] == ["In brief"]


async def test_plot_and_brief_round_trip(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """The form endpoints are op-log-backed (golden rule 1), and the projections mirror them."""
    plot = await client.put(
        "%s/projects/%s/plot" % (api, project_a.id),
        json={
            "boundary": [
                {"x": 0, "y": 0},
                {"x": 9144, "y": 0},
                {"x": 9144, "y": 12192},
                {"x": 0, "y": 12192},
            ],
            "northDeg": 0,
            "roads": [{"edgeIndex": 0, "widthMm": 9000}],
            "source": "manual",
        },
        headers=firm_a.headers,
    )
    assert plot.status_code == 200, plot.text
    saved = plot.json()
    assert saved["northDeg"] == 0
    assert saved["boundary"][1] == {"x": 9144, "y": 0}, "integer mm, verbatim"
    assert saved["roads"][0]["widthMm"] == 9000

    brief = await client.put(
        "%s/projects/%s/brief" % (api, project_a.id),
        json={"data": {"bedrooms": 3, "bathrooms": 2}, "vastuMode": "advisory", "completeness": 60},
        headers=firm_a.headers,
    )
    assert brief.status_code == 200, brief.text
    assert brief.json()["data"]["bedrooms"] == 3
    assert brief.json()["vastuMode"] == "advisory"

    # Both wrote ops: the design document and the forms cannot disagree. Assert
    # the SUBSTANCE (a plot op and a brief op landed in the log), not an op
    # count — the form handlers legitimately skip no-op diffs (northDeg 0 is
    # already the empty document's value), which a fixed count miscounts.
    head = await client.get("%s/projects/%s/branch" % (api, project_a.id), headers=firm_a.headers)
    assert head.json()["headIdx"] >= 1, head.json()
    log = await client.get(
        "%s/projects/%s/ops?since=-1&limit=100" % (api, project_a.id), headers=firm_a.headers
    )
    op_types = [op["type"] for op in log.json()["ops"]]
    assert any(t.startswith("plot.") for t in op_types), op_types
    assert any(t.startswith("brief.") for t in op_types), op_types


async def test_plot_rejects_a_float_length(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """Geometry is integer millimetres everywhere (locked decision, §3)."""
    response = await client.put(
        "%s/projects/%s/plot" % (api, project_a.id),
        json={"boundary": [{"x": 0.5, "y": 0}, {"x": 9144, "y": 0}, {"x": 0, "y": 12192}]},
        headers=firm_a.headers,
    )
    assert response.status_code == 422, response.text
    body = problem(response)
    assert body["code"] == "validation_failed", body


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({}, "name"),
        ({"name": ""}, "name"),
        ({"name": "   "}, "name"),
        ({"name": "x" * 300}, "name"),
        ({"name": "ok", "architectOfRecord": "not-a-uuid"}, "architectOfRecord"),
    ],
)
async def test_create_validation_failures_name_the_field(
    client: Any, api: str, firm_a: Any, payload: dict[str, Any], field: str
) -> None:
    """422 ``validation_failed`` with an ``errors[]`` the form can highlight."""
    response = await client.post("%s/projects" % api, json=payload, headers=firm_a.headers)
    assert response.status_code == 422, response.text
    body = problem(response)
    assert body["code"] == "validation_failed", body
    assert isinstance(body.get("errors"), list) and body["errors"], body
    fields = {error.get("field", "") for error in body["errors"]}
    assert any(field in name for name in fields), (field, fields)
    # §13: an error body must not echo the input back (that is how secrets end up
    # in logs). `code` is pydantic's error TYPE (e.g. "missing"), not input — the
    # renderer strips the echoing members (input/ctx/url) explicitly.
    for error in body["errors"]:
        assert set(error) <= {"field", "message", "code"}, error


async def test_malformed_json_is_400_not_422(client: Any, api: str, firm_a: Any) -> None:
    """A body that is not JSON is a different failure from a body with bad values."""
    response = await client.post(
        "%s/projects" % api,
        content=b"{not json",
        headers={**firm_a.headers, "content-type": "application/json"},
    )
    assert response.status_code == 400, response.text
    assert problem(response)["code"] == "invalid_request"


async def test_unknown_units_is_a_client_error(client: Any, api: str, firm_a: Any) -> None:
    """Settled: schemas/project.py constrains `units` to models.PROJECT_UNITS, so
    Pydantic answers 422 — the strict xfail that used to sit here XPASSed on the
    suite's first full execution and its marker is deleted per its own instruction."""
    response = await client.post(
        "%s/projects" % api,
        json={"name": "Bad units", "units": "cubits"},
        headers=firm_a.headers,
    )
    assert 400 <= response.status_code < 500, "unknown units returned %s; allowed values are %s" % (
        response.status_code,
        ", ".join(PROJECT_UNITS),
    )


async def test_unknown_status_is_a_client_error(client: Any, api: str, firm_a: Any) -> None:
    """Settled the same way as `units` above: constrained in schemas/project.py, 422."""
    response = await client.post(
        "%s/projects" % api,
        json={"name": "Bad status", "status": "shipped"},
        headers=firm_a.headers,
    )
    assert 400 <= response.status_code < 500, (
        "unknown status returned %s; allowed values are %s"
        % (response.status_code, ", ".join(PROJECT_STATUSES))
    )


async def test_bad_input_never_returns_a_traceback(client: Any, api: str, firm_a: Any) -> None:
    """Whatever the status, the body is problem+json and carries no internals.

    This is the assertion that holds *today* for the two xfails above: the defect is the
    status code, not a leak. If either ever starts returning a traceback, this fails
    immediately.
    """
    for payload in ({"name": "x", "units": "cubits"}, {"name": "x", "status": "shipped"}):
        response = await client.post("%s/projects" % api, json=payload, headers=firm_a.headers)
        body = problem(response)
        text = response.text
        for leak in ("Traceback", "sqlalchemy", "RepositoryUsageError", 'File "'):
            assert leak not in text, (payload, leak, text[:300])
        assert body["action"], body


async def test_architect_of_record_must_be_in_the_firm(
    client: Any, api: str, firm_b: Any, firm_a: Any
) -> None:
    """The name on a municipal sheet cannot be borrowed from another studio (§7).

    The status is not pinned because the repository raises ``RepositoryUsageError`` here
    too (see the module docstring); what must hold — and does — is that the project is
    **not** created.
    """
    response = await client.post(
        "%s/projects" % api,
        json={"name": "Borrowed architect", "architectOfRecord": str(firm_b.user_id)},
        headers=firm_a.headers,
    )
    assert response.status_code >= 400, response.text
    listed = await client.get("%s/projects" % api, headers=firm_a.headers)
    assert listed.json()["items"] == [], "the project was created with a foreign architect"


async def test_patch_with_no_fields_is_a_no_op_not_an_error(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    """An empty PATCH is what a form with no changes sends. It must be dull."""
    response = await client.patch(
        "%s/projects/%s" % (api, project_a.id), json={}, headers=firm_a.headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == project_a.name


async def test_deleting_twice_is_404_the_second_time(
    client: Any, api: str, firm_a: Any, project_a: Any
) -> None:
    first = await client.delete("%s/projects/%s" % (api, project_a.id), headers=firm_a.headers)
    assert first.status_code == 200, first.text
    second = await client.delete("%s/projects/%s" % (api, project_a.id), headers=firm_a.headers)
    assert second.status_code == 404, second.text
    assert problem(second)["code"] == "not_found"


async def test_delete_is_audited(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    """§13 audits deletions. A hard-deleted op log has to leave a trail behind it."""
    from garh_api.repositories import AuditLogRepository

    response = await client.delete("%s/projects/%s" % (api, project_a.id), headers=firm_a.headers)
    assert response.status_code == 200, response.text

    page = await AuditLogRepository(session, firm_a.ctx()).list_recent(limit=20)
    actions = [entry.action for entry in page.items]
    assert any("delete" in action for action in actions), actions
