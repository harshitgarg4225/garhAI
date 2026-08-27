"""Project templates (Task #29): the registry folds, applies, and stays honest.

Three layers of proof, mirroring how the seed's own content is proven:

1. **Every template's recipe folds** through the real ``garh_model`` fold — the
   same proof pattern as ``test_seed_brief_feasible``'s document build. Plus the
   CLAUDE.md negative control: break a recipe in-test and watch the fold assertion
   actually go red, so a green here can never be vacuous.
2. **The dispatch path is the seed's**: ``POST /projects`` with a ``templateId``
   produces an op log that is all ``source="system"`` with ``tpl-%02d`` client op
   ids, a folded model reflecting the template, and mirrored plot/brief
   projections. ``blank`` is provably identical to no template at all.
3. **Template projects are invisible to the stale-demo detector**: they carry
   ``demo=False``, ``get_demo_project()`` never returns them, and a re-seed leaves
   them byte-for-byte untouched.
"""

from __future__ import annotations

from typing import Any

import pytest
from garh_api.templates import (
    BLANK_TEMPLATE_ID,
    PLOT_20X30_DEPTH_MM,
    PLOT_20X30_WIDTH_MM,
    PLOT_40X60_DEPTH_MM,
    PLOT_40X60_WIDTH_MM,
    TEMPLATES,
    get_template,
    template_ids,
)
from garh_model import ROOM_TYPES, OpRejectedError
from garh_model.fold import replay

from tests.helpers import problem

# ---------------------------------------------------------------------------
# Registry + fold proofs (no datastore — these always run)
# ---------------------------------------------------------------------------

EXPECTED_IDS = ("blank", "blr-30x40-g1-3bhk", "plot-40x60-empty-brief", "plot-20x30-compact")


def _fold(ops: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold wire ops from empty with the real model core; raises on any bad op."""
    return replay([{"type": op["type"], "payload": op["payload"]} for op in ops]).to_json()


def test_registry_lists_four_templates_blank_first() -> None:
    assert template_ids() == EXPECTED_IDS
    assert TEMPLATES[0].id == BLANK_TEMPLATE_ID, "Blank is first and the picker default"
    assert len({t.id for t in TEMPLATES}) == len(TEMPLATES)
    for template in TEMPLATES:
        assert template.name.strip(), template.id
        assert template.description.strip(), template.id
    # The blank template is the current behavior, listed explicitly: zero ops.
    assert TEMPLATES[0].build() == []
    assert get_template("no-such-template") is None


def test_the_demo_template_reuses_the_seeds_own_builders() -> None:
    """The 30×40 template must BE the seed's op log, not a copy that can drift."""
    from garh_api.seed import demo as demo_data

    authored = demo_data.demo_op_log(demo_data.load_demo_brief())
    storey_ids = list(demo_data.demo_storey_ids())
    authored.extend(demo_data.solved_plan_ops(storey_ids=storey_ids))
    authored.extend(demo_data.facade_ops(storey_ids=storey_ids))

    template = get_template("blr-30x40-g1-3bhk")
    assert template is not None
    assert template.build() == authored


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.id)
def test_every_template_folds(template: Any) -> None:
    """Every recipe folds from empty through the real model core, no exceptions."""
    ops = template.build()
    document = _fold(ops)

    if template.id == BLANK_TEMPLATE_ID:
        assert ops == []
        assert (document.get("plot") or {}).get("boundary") in ([], None)
        return

    plot = document.get("plot") or {}
    assert len(plot.get("boundary") or []) >= 3, "%s folded no plot boundary" % template.id
    assert (plot.get("regProfile") or {}).get("cityPack"), template.id
    assert plot.get("roads"), "%s folded no road edge" % template.id

    brief = (document.get("brief") or {}).get("data") or {}
    rooms = brief.get("rooms") or []
    assert rooms, "%s folded an empty room program" % template.id
    for room in rooms:
        assert room["type"] in ROOM_TYPES, (template.id, room["type"])

    storeys = (document.get("house") or {}).get("storeys") or []
    assert len(storeys) == 2, "%s must fold to G+1 (two storeys)" % template.id

    # The starter-plot templates ship NO solved plan — the user Generates.
    walls = (document.get("house") or {}).get("walls") or []
    assert walls == [], "%s must not invent walls (that is the solver's output)" % template.id


def test_starter_plots_fold_the_advertised_geometry_and_programs() -> None:
    doc_40x60 = _fold(get_template("plot-40x60-empty-brief").build())  # type: ignore[union-attr]
    boundary = (doc_40x60.get("plot") or {}).get("boundary") or []
    assert {"x": PLOT_40X60_WIDTH_MM, "y": PLOT_40X60_DEPTH_MM} in boundary
    brief = (doc_40x60.get("brief") or {}).get("data") or {}
    assert brief.get("bedrooms") == 4
    assert sum(r.get("count", 0) for r in brief["rooms"] if "bedroom" in r["type"]) == 4

    doc_20x30 = _fold(get_template("plot-20x30-compact").build())  # type: ignore[union-attr]
    boundary = (doc_20x30.get("plot") or {}).get("boundary") or []
    assert {"x": PLOT_20X30_WIDTH_MM, "y": PLOT_20X30_DEPTH_MM} in boundary
    brief = (doc_20x30.get("brief") or {}).get("data") or {}
    assert brief.get("bedrooms") == 2
    assert sum(r.get("count", 0) for r in brief["rooms"] if "bedroom" in r["type"]) == 2


def test_the_fold_proof_cannot_pass_vacuously() -> None:
    """Negative control (CLAUDE.md: break the thing, watch the gate go red).

    A recipe with a misspelled op type, and one smuggling a float into the brief,
    must both make :func:`_fold` raise — proving the fold assertions above are
    armed, not decorative.
    """
    template = get_template("plot-40x60-empty-brief")
    assert template is not None

    broken_type = template.build()
    broken_type[0]["type"] = "plot.set_boundry"  # sic
    with pytest.raises(OpRejectedError):
        _fold(broken_type)

    broken_float = template.build()
    brief_op = next(op for op in broken_float if op["type"] == "brief.update")
    brief_op["payload"]["patch"]["budgetInr"] = 0.5
    with pytest.raises(OpRejectedError):
        _fold(broken_float)


# ---------------------------------------------------------------------------
# The route: POST /projects?templateId + GET /templates
# ---------------------------------------------------------------------------


async def _create(client: Any, api: str, actor: Any, template_id: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "Templated %s" % (template_id or "none")}
    if template_id is not None:
        body["templateId"] = template_id
    response = await client.post("%s/projects" % api, json=body, headers=actor.headers)
    assert response.status_code == 201, response.text
    project: dict[str, Any] = response.json()
    return project


async def _ops_of(client: Any, api: str, actor: Any, project_id: str) -> list[dict[str, Any]]:
    response = await client.get(
        "%s/projects/%s/ops?since=-1&limit=100" % (api, project_id), headers=actor.headers
    )
    assert response.status_code == 200, response.text
    ops: list[dict[str, Any]] = response.json()["ops"]
    return ops


@pytest.mark.integration
async def test_get_templates_requires_auth(client: Any, api: str) -> None:
    response = await client.get("%s/templates" % api)
    assert response.status_code == 401, response.text
    assert problem(response)["code"] == "unauthenticated"


@pytest.mark.integration
async def test_get_templates_lists_the_registry_in_picker_order(
    client: Any, api: str, firm_a: Any
) -> None:
    response = await client.get("%s/templates" % api, headers=firm_a.headers)
    assert response.status_code == 200, response.text
    cards = response.json()["templates"]
    assert [card["id"] for card in cards] == list(EXPECTED_IDS)
    for card in cards:
        assert set(card) >= {"id", "name", "description", "plotSizeLabel", "tags"}, card
    by_id = {card["id"]: card for card in cards}
    assert by_id["blr-30x40-g1-3bhk"]["plotSizeLabel"] == "30 × 40 ft"
    assert by_id["blank"]["plotSizeLabel"] == ""


@pytest.mark.integration
@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.id)
async def test_create_from_template_reflects_it_in_the_model(
    template: Any, client: Any, api: str, firm_a: Any
) -> None:
    """The created project's op log folds to exactly what the template authored."""
    project = await _create(client, api, firm_a, template.id)
    assert project["demo"] is False, "a template project must NEVER carry the demo flag"

    ops = await _ops_of(client, api, firm_a, project["id"])
    authored = template.build()
    assert [(op["type"], op["payload"]) for op in ops] == [
        (op["type"], op["payload"]) for op in authored
    ]
    for index, op in enumerate(ops):
        assert op["source"] == "system", op
        assert op["clientOpId"] == "tpl-%02d" % index, op

    document = _fold([{"type": op["type"], "payload": op["payload"]} for op in ops])
    shell = await client.get("%s/projects/%s" % (api, project["id"]), headers=firm_a.headers)
    assert shell.status_code == 200, shell.text
    detail = shell.json()

    if template.id == BLANK_TEMPLATE_ID:
        assert ops == []
        assert detail["headIdx"] == -1
        assert detail["plot"] is None and detail["brief"] is None
        return

    # The folded model reflects the template...
    plot_doc = document.get("plot") or {}
    brief_data = (document.get("brief") or {}).get("data") or {}
    assert len(plot_doc.get("boundary") or []) >= 3
    assert brief_data.get("rooms")

    # ...and the projections mirror the folded document, not some third source.
    assert detail["headIdx"] == len(ops) - 1
    assert detail["plot"] is not None, "the plot projection must be mirrored"
    assert detail["plot"]["boundary"] == plot_doc["boundary"]
    assert detail["brief"] is not None, "the brief projection must be mirrored"
    assert detail["brief"]["data"]["bedrooms"] == brief_data["bedrooms"]
    assert len(detail["brief"]["data"]["rooms"]) == len(brief_data["rooms"])


@pytest.mark.integration
async def test_blank_template_is_identical_to_no_template(
    client: Any, api: str, firm_a: Any
) -> None:
    """`templateId: "blank"` ≡ omitting the field: same empty shell either way."""
    with_blank = await _create(client, api, firm_a, BLANK_TEMPLATE_ID)
    without = await _create(client, api, firm_a, None)
    for project in (with_blank, without):
        shell = await client.get("%s/projects/%s" % (api, project["id"]), headers=firm_a.headers)
        detail = shell.json()
        assert detail["headIdx"] == -1
        assert detail["plot"] is None and detail["brief"] is None
        assert detail["project"]["demo"] is False


@pytest.mark.integration
async def test_unknown_template_is_422_naming_the_valid_ids(
    client: Any, api: str, firm_a: Any
) -> None:
    response = await client.post(
        "%s/projects" % api,
        json={"name": "Bad template", "templateId": "penthouse-mars"},
        headers=firm_a.headers,
    )
    assert response.status_code == 422, response.text
    body = problem(response)
    assert body["code"] == "validation_failed", body
    errors = body.get("errors") or []
    template_errors = [e for e in errors if "templateId" in str(e.get("field", ""))]
    assert template_errors, errors
    message = template_errors[0]["message"]
    for template_id in EXPECTED_IDS:
        assert template_id in message, (template_id, message)

    # And nothing was created: the validator runs before the project row exists.
    listed = await client.get("%s/projects" % api, headers=firm_a.headers)
    assert listed.json()["items"] == []


@pytest.mark.integration
async def test_template_projects_are_invisible_to_the_stale_demo_detector(
    client: Any, api: str, session: Any, clean_redis: Any
) -> None:
    """The NOTE in the design, executed.

    The stale-demo auto-migration only ever examines the project
    ``get_demo_project()`` returns (``WHERE demo IS TRUE``). A template project is
    ``demo=False`` — even one built from the demo's own op log, in the demo firm
    itself — so a re-seed must reuse the real demo and leave the template project
    byte-for-byte alone.
    """
    from garh_api import models
    from garh_api.repositories import ProjectRepository
    from garh_api.seed.runner import REUSED, SeedOptions, seed
    from garh_api.tenancy import TenantCtx
    from sqlalchemy import select

    from tests.factories import access_token

    first = await seed(session, SeedOptions())
    await session.commit()

    # The demo firm's admin creates a project from the demo-content template.
    demo_admin = {
        "Authorization": "Bearer %s"
        % access_token(firm_id=first.firm_id, user_id=first.user_id, role="admin")
    }
    created = await client.post(
        "%s/projects" % api,
        json={"name": "From template, demo firm", "templateId": "blr-30x40-g1-3bhk"},
        headers=demo_admin,
    )
    assert created.status_code == 201, created.text
    template_project = created.json()
    assert template_project["demo"] is False

    ops_before = [
        (op.type, op.payload, op.source, op.client_op_id)
        for op in (
            await session.execute(
                select(models.Op)
                .where(models.Op.project_id == template_project["id"])
                .order_by(models.Op.idx)
            )
        ).scalars()
    ]
    assert ops_before, "the template must have written an op log"
    assert all(cid.startswith("tpl-") for (_, _, _, cid) in ops_before)

    second = await seed(session, SeedOptions())
    await session.commit()
    assert second.steps["demoProject"] == REUSED
    assert second.project_id == first.project_id

    ctx = TenantCtx(firm_id=first.firm_id, user_id=first.user_id, role="admin", request_id="test")
    session.expire_all()
    demo_row = await ProjectRepository(session, ctx).get_demo_project()
    assert demo_row is not None and str(demo_row.id) == str(first.project_id)
    assert str(demo_row.id) != template_project["id"]

    ops_after = [
        (op.type, op.payload, op.source, op.client_op_id)
        for op in (
            await session.execute(
                select(models.Op)
                .where(models.Op.project_id == template_project["id"])
                .order_by(models.Op.idx)
            )
        ).scalars()
    ]
    assert ops_after == ops_before, "a re-seed must never touch a template project"
