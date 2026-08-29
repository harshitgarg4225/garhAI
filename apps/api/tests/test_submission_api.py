"""Per-authority submission templates over HTTP (D-4).

A rule pack answers *is this design legal*. A submission template answers *is this SET
submittable* — and it is the second one that sends an architect back across the counter.
A fully compliant design still gets returned when the khata number is missing from the
title block, and no compliance engine can see that.

Three things here would each look fine in a green suite and be wrong in production, so
each is asserted directly:

* the readiness endpoint measuring the template against itself rather than against the
  sheets that exist (a check that cannot fail);
* Bengaluru silently getting one authority when it has two, so half the city works to
  the wrong checklist;
* statutory identifiers stored on the project but never reaching the sheet payload,
  which prints a set with the boxes blank while the API reports it ready.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# ``services/`` lives at the repo root — on PYTHONPATH in CI and in the API image
# (its Dockerfile COPYs services/ and sets PYTHONPATH=/app), but not when pytest is
# started from apps/api. Same bootstrap as test_copilot.py.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------
async def test_the_templates_are_listed_with_their_review_status(
    client: Any, api: str, firm_a: Any
) -> None:
    """Nothing may present one of these as settled fact.

    Not one template has been checked against a published municipal checklist. The
    status rides on every row so a screen cannot render the name without the caveat.
    """
    response = await client.get("%s/submission-templates" % api, headers=firm_a.headers)
    assert response.status_code == 200, response.text
    templates = response.json()["templates"]
    assert {t["authority"] for t in templates} == {"bbmp", "bda", "ncr", "ghmc"}
    for template in templates:
        assert template["confidence"] == "seed", template["authority"]
        assert template["review"] == "unreviewed", template["authority"]
        assert template["verify"], template["authority"]
        assert template["citation"], template["authority"]


async def test_bengaluru_offers_two_authorities(client: Any, api: str, firm_a: Any) -> None:
    """The case a "one template per city" design gets silently wrong.

    BBMP and BDA sanction different plots under the same ``blr`` rule pack. Returning
    one would hand half of Bengaluru a checklist for the wrong desk.
    """
    response = await client.get(
        "%s/submission-templates?cityPack=blr" % api, headers=firm_a.headers
    )
    assert response.status_code == 200, response.text
    assert {t["authority"] for t in response.json()["templates"]} == {"bbmp", "bda"}


async def test_filtering_by_a_pack_with_one_authority_returns_one(
    client: Any, api: str, firm_a: Any
) -> None:
    """Negative control for the test above: two is a fact about Bengaluru, not the shape
    of every answer."""
    response = await client.get(
        "%s/submission-templates?cityPack=hyd" % api, headers=firm_a.headers
    )
    assert [t["authority"] for t in response.json()["templates"]] == ["ghmc"]


# ---------------------------------------------------------------------------
# Setting it on a project
# ---------------------------------------------------------------------------
async def _project(client: Any, api: str, firm_a: Any, city_pack: str = "blr") -> str:
    created = await client.post(
        "%s/projects" % api,
        json={"name": "Sharma Residence", "units": "ft-in", "cityPack": city_pack},
        headers=firm_a.headers,
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def test_a_project_starts_with_no_authority_and_offers_its_citys_options(
    client: Any, api: str, firm_a: Any
) -> None:
    project_id = await _project(client, api, firm_a)
    response = await client.get(
        "%s/projects/%s/submission" % (api, project_id), headers=firm_a.headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["authority"] is None
    assert {t["authority"] for t in body["available"]} == {"bbmp", "bda"}


async def test_setting_the_authority_and_its_identifiers_round_trips(
    client: Any, api: str, firm_a: Any
) -> None:
    project_id = await _project(client, api, firm_a)
    saved = await client.put(
        "%s/projects/%s/submission" % (api, project_id),
        json={"authority": "bbmp", "fields": {"khataNumber": "A-1234/56", "wardNumber": "150"}},
        headers=firm_a.headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["authority"] == "bbmp"
    assert saved.json()["fields"]["khataNumber"] == "A-1234/56"

    again = await client.get(
        "%s/projects/%s/submission" % (api, project_id), headers=firm_a.headers
    )
    assert again.json()["fields"] == {"khataNumber": "A-1234/56", "wardNumber": "150"}


async def test_switching_authority_does_not_carry_the_old_identifiers_across(
    client: Any, api: str, firm_a: Any
) -> None:
    """A BBMP khata number must not end up on a set going to BDA."""
    project_id = await _project(client, api, firm_a)
    await client.put(
        "%s/projects/%s/submission" % (api, project_id),
        json={"authority": "bbmp", "fields": {"khataNumber": "A-1234/56"}},
        headers=firm_a.headers,
    )
    switched = await client.put(
        "%s/projects/%s/submission" % (api, project_id),
        json={"authority": "bda", "fields": {"sitalNumber": "44"}},
        headers=firm_a.headers,
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["fields"] == {"sitalNumber": "44"}


async def test_an_authority_this_build_does_not_ship_is_refused_with_the_list(
    client: Any, api: str, firm_a: Any
) -> None:
    """Storing it would print no statutory boxes and report no shortfalls — a set that
    looks finished and is not."""
    project_id = await _project(client, api, firm_a)
    response = await client.put(
        "%s/projects/%s/submission" % (api, project_id),
        json={"authority": "mcgm", "fields": {}},
        headers=firm_a.headers,
    )
    assert response.status_code == 422, response.text
    assert "bbmp" in response.text


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------
async def _readiness(client: Any, api: str, firm_a: Any, project_id: str, **params: str) -> Any:
    query = "".join("&%s=%s" % item for item in params.items())
    response = await client.get(
        "%s/projects/%s/sheets/submission-readiness?x=1%s" % (api, project_id, query),
        headers=firm_a.headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_with_no_authority_chosen_it_asks_instead_of_guessing(
    client: Any, api: str, firm_a: Any
) -> None:
    """Guessing is the failure mode. Picking the first of Bengaluru's two would be a
    silent wrong answer that looks like a right one."""
    project_id = await _project(client, api, firm_a)
    body = await _readiness(client, api, firm_a, project_id)
    assert body["authority"] is None
    assert sorted(body["chooseFrom"]) == ["bbmp", "bda"]
    assert body["ready"] is False


async def test_a_project_with_no_sheets_is_not_ready_and_says_what_is_missing(
    client: Any, api: str, firm_a: Any
) -> None:
    """The check reads the SET, not the template. A template checked against itself is
    a test that cannot fail."""
    project_id = await _project(client, api, firm_a)
    body = await _readiness(client, api, firm_a, project_id, authority="bbmp")
    assert body["ready"] is False
    assert body["satisfied"] == 0
    kinds = {s["what"] for s in body["shortfalls"]}
    assert {"site", "floor", "elevation", "section", "area-statement"} <= kinds
    assert {"khataNumber", "wardNumber"} <= kinds


async def test_filling_the_identifiers_clears_exactly_those_shortfalls(
    client: Any, api: str, firm_a: Any
) -> None:
    """Negative control on the test above: the count must move, and move by the right
    amount, or the check is reporting a constant."""
    project_id = await _project(client, api, firm_a)
    before = await _readiness(client, api, firm_a, project_id, authority="bbmp")
    await client.put(
        "%s/projects/%s/submission" % (api, project_id),
        json={
            "authority": "bbmp",
            "fields": {
                "khataNumber": "A-1234/56",
                "wardNumber": "150",
                "architectRegistrationNo": "CA/2011/51234",
                "ownerName": "R Rao",
            },
        },
        headers=firm_a.headers,
    )
    after = await _readiness(client, api, firm_a, project_id)
    assert after["authority"] == "bbmp", "the stored authority is used when none is asked for"
    assert after["satisfied"] == before["satisfied"] + 4
    assert not [s for s in after["shortfalls"] if s["kind"] == "field"]
    # ...and the sheets are still missing, because filling a form does not draw anything.
    assert after["ready"] is False
    assert {s["kind"] for s in after["shortfalls"]} == {"sheet"}


async def test_readiness_never_reports_a_tick_without_its_caveat(
    client: Any, api: str, firm_a: Any
) -> None:
    project_id = await _project(client, api, firm_a)
    body = await _readiness(client, api, firm_a, project_id, authority="bbmp")
    assert body["confidence"] == "seed"
    assert body["review"] == "unreviewed"
    assert body["verify"]


async def test_an_unknown_authority_in_the_query_is_a_404_not_a_green_tick(
    client: Any, api: str, firm_a: Any
) -> None:
    project_id = await _project(client, api, firm_a)
    response = await client.get(
        "%s/projects/%s/sheets/submission-readiness?authority=mcgm" % (api, project_id),
        headers=firm_a.headers,
    )
    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# The identifiers must reach the sheet job
# ---------------------------------------------------------------------------
async def test_the_statutory_values_reach_the_sheet_payload(
    session: Any, firm_a: Any, project_a: Any
) -> None:
    """Stored on the project and never sent is the whole feature failing silently.

    ``build_sheets_job`` is where the payload is assembled; if the authority and its
    values do not land in it, the worker prints a set with every statutory box blank
    while the readiness endpoint goes on reporting the project ready.
    """
    from garh_api.repositories import ProjectRepository

    await ProjectRepository(session, firm_a.ctx()).set_submission(
        project_a.id, {"authority": "bbmp", "fields": {"khataNumber": "A-1234/56"}}
    )
    project = await ProjectRepository(session, firm_a.ctx()).require(project_a.id)
    assert project.submission is not None, "the domain object must carry it"

    from garh_api.routers.sheets import _submission_fields

    assert _submission_fields(project) == {"khataNumber": "A-1234/56"}


async def test_negative_control_a_project_with_no_authority_sends_no_statutory_values(
    session: Any, firm_a: Any, project_a: Any
) -> None:
    """Prove the test above discriminates rather than passing on any project."""
    from garh_api.repositories import ProjectRepository
    from garh_api.routers.sheets import _submission_fields

    project = await ProjectRepository(session, firm_a.ctx()).require(project_a.id)
    assert _submission_fields(project) == {}
