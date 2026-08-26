"""default_stage_set() end to end — the REAL stage bodies, no fakes.

test_pipeline.py proves the driver with pure-Python fakes; this file is the other
half: the Phase-3 adapters in ``services.solver.stages`` running the actual
CP-SAT topology, the actual §5.3 refinement, the actual §5.4 rules pass and the
actual ops emission, on a small fixture program. Requires ``ortools`` — skipped
where it is absent, exactly like the CI marker scheme expects.

Two kinds of assertion, per the repo rule that a gate must be able to go red:

* the happy path: ≥1 option, non-empty ops, every op type in the model's §4
  taxonomy, and the ops FOLD through the real ``garh_model.fold`` — checked here
  independently rather than trusting ``house_to_ops``'s own internal proof;
* negative tests: ``placements_to_ops`` without a stage-B model refuses;
  ``evaluate_compliance`` on a plan with no walls produces real ``fail`` rows
  (a §5.4 pass that cannot fail would wave everything through §5.6 — bug
  pattern 3 in CLAUDE.md).
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

pytest.importorskip("ortools")

from services.solver import critic
from services.solver.handler import _parse_params
from services.solver.pipeline import PRODUCTION_PROFILE, SolveContext, run_solver
from services.solver.stage_a import min_frontage_cells, snap_loss_table
from services.solver.stages import placements_to_ops

#: 15×12m plot, 3m front / 1.5m other setbacks → a 12000×7500 envelope. Small
#: enough to solve in seconds, large enough that NBC clear minima genuinely fit.
PAYLOAD: dict[str, Any] = {
    "optionCount": 1,
    "seed": 7,
    "plot": {
        "polygon": [[0, 0], [15_000, 0], [15_000, 12_000], [0, 12_000]],
        "edges": [
            {"index": 0, "role": "front", "setbackMm": 3_000, "roadWidthMm": 9_000},
            {"index": 1, "role": "side", "setbackMm": 1_500},
            {"index": 2, "role": "rear", "setbackMm": 1_500},
            {"index": 3, "role": "side", "setbackMm": 1_500},
        ],
        "northDeg": 0,
    },
    "profile": {
        "cityPack": "blr",
        "coveragePercent": 70,
        "farX100": 225,
        "maxHeightMm": 15_000,
        "maxFloors": 4,
    },
    "brief": {
        "storeys": 1,
        "vastuMode": "off",
        # Explicit, generous targets — what a real brief carries. At the bare
        # NBC minima the §5.6 furniture gate becomes a wall-clock coin flip
        # (rooms one snap away from too-tight); that is a tuning surface, not
        # what this canary is for.
        "rooms": [
            {"type": "living", "minAreaMm2": 11_000_000, "targetAreaMm2": 14_000_000},
            {"type": "bedroom", "minAreaMm2": 11_000_000, "targetAreaMm2": 13_000_000},
            {"type": "kitchen", "minAreaMm2": 6_000_000, "targetAreaMm2": 7_500_000},
            {"type": "bath_wc", "minAreaMm2": 3_200_000, "targetAreaMm2": 3_600_000},
        ],
        "data": {"carParking": 1},
    },
}


@pytest.fixture(scope="module")
def solve_result() -> Any:
    """One real solve, shared by the assertions below. ~15-30s wall clock."""
    params = _parse_params(PAYLOAD, kind="solver.generate")

    async def progress(stage: str, message: str, **data: Any) -> None:
        return None

    context = SolveContext(
        params=params,
        progress=progress,
        check_cancelled=lambda: None,
        # Small budget, few anchors: this is a wiring canary, not a benchmark.
        profile=replace(PRODUCTION_PROFILE, time_budget_seconds=10, num_search_workers=8),
        max_stair_candidates=2,
    )
    return asyncio.run(run_solver(context))


def test_pipeline_produces_at_least_one_presentable_option(solve_result: Any) -> None:
    assert solve_result.considered >= 1, "no candidate even reached the critic"
    assert len(solve_result.options) >= 1, (
        "the real pipeline produced zero §5.6-presentable options on a plot "
        "that comfortably fits the brief — a stage regressed"
    )


def test_option_ops_are_model_ops_and_fold(solve_result: Any) -> None:
    from garh_model.fold import fold
    from garh_model.model import DEFAULTS, ProjectDoc, SCHEMA_VERSION
    from garh_model.ops import OP_TYPES

    from services.solver.repair import wrap_project_doc
    from services.solver.stage_b import _empty_house_json

    params = _parse_params(PAYLOAD, kind="solver.generate")
    option = solve_result.options[0]
    assert option.ops, "a presentable option must carry the §4 ops that build it"
    for op in option.ops:
        assert op["type"] in OP_TYPES, "unknown op type %r" % op["type"]
    types = [op["type"] for op in option.ops]
    assert "storey.add" in types
    assert types.count("wall.add") >= 4, "a plan without walls is not a plan"
    assert "room.assign" in types, "rooms must come back typed, not 'unassigned'"

    # Independent fold proof — deliberately NOT via house_to_ops' own path.
    doc = ProjectDoc.from_json(
        wrap_project_doc(
            _empty_house_json(SCHEMA_VERSION, DEFAULTS, params.profile.city_pack), params
        )
    )
    for op in option.ops:
        doc = fold(doc, dict(op), compute_inverse=False).model
    assert len(doc.house.storeys) >= 1
    assert len(doc.house.rooms) >= 4
    assert all(room.type != "unassigned" or room.area_mm2 < 1_000_000 for room in doc.house.rooms), (
        "every substantial detected room should have received room.assign"
    )


def test_option_compliance_rows_are_real_engine_rows(solve_result: Any) -> None:
    option = solve_result.options[0]
    assert option.compliance, "the critic's rules results must ride on the option"
    statuses = {str(row.get("status")) for row in option.compliance}
    # Bug pattern 2 (CLAUDE.md): 83 rules quietly inert. A healthy report on a
    # solved plan has applicable rows, not a sea of not_applicable.
    assert statuses & {"pass", "warn", "fail"}, "every rule reported not_applicable"
    assert not any(
        str(row.get("status")) == "fail" for row in option.compliance
    ), "a §5.6-presented option may not carry a hard failure"
    rule_ids = {str(row.get("ruleId")) for row in option.compliance}
    assert any(rule_id.startswith("nbc.") for rule_id in rule_ids)


def test_placements_to_ops_refuses_to_run_without_the_stage_b_model() -> None:
    params = _parse_params(PAYLOAD, kind="solver.generate")
    with pytest.raises(ValueError):
        placements_to_ops((), params, model=None)


def test_evaluate_compliance_can_go_red() -> None:
    """A plan with storeys but no walls must FAIL setbacks, not pass vacuously."""
    from garh_model.model import DEFAULTS, SCHEMA_VERSION

    from services.solver.stage_b import _empty_house_json

    params = _parse_params(PAYLOAD, kind="solver.generate")
    house = _empty_house_json(SCHEMA_VERSION, DEFAULTS, params.profile.city_pack)
    house["storeys"] = [
        {
            "id": "storey_test0000000000000000",
            "name": "Ground Floor",
            "level": {
                "fflMm": DEFAULTS.plinth_mm,
                "slabThicknessMm": DEFAULTS.slab_thickness_mm,
                "sillDefaultMm": None,
                "lintelDefaultMm": None,
            },
            "heightMm": 3_000,
        }
    ]
    rows = critic.evaluate_compliance(house, params)
    assert rows, "the engine returned no rows at all"
    assert any(str(row.get("status")) == "fail" for row in rows), (
        "a wall-less plan passed the hard-rule pass — the §5.4 gate cannot go red"
    )


def test_frontage_arithmetic_is_snap_proof() -> None:
    """The §5.2 serving-span cells must survive the §5.3 115mm snap."""
    # 3 coarse cells (900mm) can shrink to 805mm — under every legal door.
    assert snap_loss_table(3)[3] == 95
    # A 750mm bath door needs 980mm; 4 cells (worst 1150mm) is the honest floor.
    assert min_frontage_cells(750 + 230) == 4
    # A 900mm door needs 1130mm; still within 4 cells' worst case.
    assert min_frontage_cells(900 + 230) == 4
    # And the naive answer really is wrong: 3 cells cannot carry any of them.
    assert min_frontage_cells(806) == 4
