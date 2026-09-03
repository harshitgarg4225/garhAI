"""The ready-made plan library: every recipe is real, flat, foldable, compliant, drawn.

``fixtures/plans/<id>.json`` is the whole op log of a project after the Options screen
applied the solver's best option, flattened so a new project can replay it without the
original solver job (``scripts/seed_plan_library.py``, ``scripts/flatten_plan_recipes.py``).
``<id>.svg`` beside it is the picker thumbnail, drawn through the sheet renderer's own
primitives. This file pins that every one of them:

* carries no ``solver.apply_option`` wrapper — the loader refuses one at import, and
  ``dispatch_ops`` (the template/form append path) refuses one at create time, so a
  wrapper can neither reach the registry nor be folded from client-supplied ops
  (both negative controls below);
* folds through the real model core to the counts recorded at capture;
* creates a project whose live rules report has no hard failure (it is the solver's
  own gated output, so a failure here means the packs or the fold drifted);
* renders to exactly the stored thumbnail (so the picture cannot drift from the ops).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from garh_api.templates import PLAN_KIND, PLAN_TEMPLATES, _read_plan, plans_dir
from garh_model import replay
from garh_model.circulation import reachability_problems

pytestmark = pytest.mark.skipif(not PLAN_TEMPLATES, reason="no recipes in fixtures/plans")


def _record(template_id: str) -> dict[str, Any]:
    return json.loads(Path(plans_dir(), "%s.json" % template_id).read_text(encoding="utf-8"))


def _fold(ops: list[dict[str, Any]]) -> dict[str, Any]:
    return replay([{"type": op["type"], "payload": op["payload"]} for op in ops]).to_json()


@pytest.mark.parametrize("template", PLAN_TEMPLATES, ids=lambda t: t.id)
def test_the_recipe_is_flat_and_came_from_a_real_solver_run(template: Any) -> None:
    record = _record(template.id)
    types = [op["type"] for op in record["ops"]]
    assert "solver.apply_option" not in types
    assert record["solver"]["jobId"] and record["solver"]["optionsOffered"] >= 1
    assert types.count("wall.add") == record["model"]["walls"]
    assert types.count("opening.add") == record["model"]["openings"]
    assert types.count("stair.add") == record["model"]["stairs"]
    assert types.count("storey.add") == record["storeys"]


@pytest.mark.parametrize("template", PLAN_TEMPLATES, ids=lambda t: t.id)
def test_the_recipe_folds_to_what_was_captured(template: Any) -> None:
    record = _record(template.id)
    house = _fold(template.build()).get("house") or {}
    assert len(house.get("walls") or []) == record["model"]["walls"]
    assert (
        len(house.get("rooms") or []) == record["model"]["rooms"]
    ), "room detection drifted: the room.assign ops would now name the wrong rooms"
    assert len(house.get("stairs") or []) == record["model"]["stairs"]
    assert len(house.get("storeys") or []) == record["storeys"]
    named = [r for r in house.get("rooms") or [] if r.get("type")]
    assert (
        len(named) >= record["brief"]["beds"] + 2
    ), "bedrooms, living and kitchen must carry names"


@pytest.mark.parametrize("template", PLAN_TEMPLATES, ids=lambda t: t.id)
def test_every_room_can_be_walked_to(template: Any) -> None:
    """The first library plan had a front door into a dead-end vestibule and a kitchen
    entered through the bath; no rule caught it, so this gate exists (garh_model.circulation)."""
    house = replay(
        [{"type": op["type"], "payload": op["payload"]} for op in template.build()]
    ).house
    assert reachability_problems(house) == []


@pytest.mark.parametrize("template", PLAN_TEMPLATES, ids=lambda t: t.id)
def test_the_thumbnail_is_the_sheet_renderer_drawing_this_recipe(template: Any) -> None:
    pytest.importorskip("services.drawings.render.svg")
    from garh_api.template_preview import preview_svg

    stored = template.preview()
    assert stored, "%s has no .svg beside its recipe" % template.id
    assert preview_svg(_record(template.id)["ops"]) == stored, (
        "%s.svg is stale — run scripts/render_plan_previews.py" % template.id
    )
    assert template.kind == PLAN_KIND


@pytest.mark.integration
@pytest.mark.parametrize("template", PLAN_TEMPLATES, ids=lambda t: t.id)
async def test_a_project_from_the_plan_has_no_hard_rule_failure(
    template: Any, client: Any, api: str, firm_a: Any
) -> None:
    created = await client.post(
        "%s/projects" % api,
        json={"name": "From %s" % template.id, "templateId": template.id},
        headers=firm_a.headers,
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    report = await client.get(
        "%s/projects/%s/compliance" % (api, project_id), headers=firm_a.headers
    )
    assert report.status_code == 200, report.text
    body = report.json()
    failed = [r for r in body.get("results") or [] if str(r.get("status")) == "fail"]
    assert body.get("results"), "the rules did not run at all"
    assert not failed, "%s breaks: %s" % (
        template.id,
        sorted({str(r.get("ruleId") or r.get("rule_id")) for r in failed}),
    )


def test_a_wrapped_recipe_is_refused_at_load_time(tmp_path: Path) -> None:
    """NEGATIVE CONTROL: the loader must reject what the sequencer stores un-flattened."""
    wrapped = {
        "id": "x",
        "ops": [{"type": "solver.apply_option", "payload": {"solverJobId": "0", "ops": []}}],
    }
    path = tmp_path / "x.json"
    path.write_text(json.dumps(wrapped))
    with pytest.raises(ValueError, match="flatten_plan_recipes"):
        _read_plan(str(path))


def test_every_recipe_on_disk_is_registered() -> None:
    on_disk = sorted(p[: -len(".json")] for p in os.listdir(plans_dir()) if p.endswith(".json"))
    assert sorted(t.id for t in PLAN_TEMPLATES) == on_disk


@pytest.mark.integration
async def test_the_form_append_path_refuses_a_wrapper_too(session: Any, firm_a: Any) -> None:
    """NEGATIVE CONTROL at create time: no side door for op 31 through dispatch_ops."""
    from garh_api.errors import ApiError
    from garh_api.routers.ops import dispatch_ops
    from garh_api.schemas.ops import OpIn

    from tests.factories import create_project

    project = await create_project(session, firm_a)
    wrapper = OpIn(
        type="solver.apply_option",
        payload={"solverJobId": str(project.id), "optionIndex": 0, "ops": []},
    )
    with pytest.raises(ApiError) as excinfo:
        await dispatch_ops(session, firm_a.ctx(), project.id, [wrapper], source="system")
    assert excinfo.value.http_status == 422
    assert excinfo.value.code == "solver_apply_not_here"
