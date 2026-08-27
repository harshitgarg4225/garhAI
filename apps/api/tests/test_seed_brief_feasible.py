"""The seeded demo brief must actually GENERATE — proven against the real solver.

The demo project is the first thing every visitor sees, and its "Generate plans"
button is the product's first promise. Golden brief 01 (the demo brief's corpus
sibling) is CP-SAT-provably infeasible on the demo plot — §5.2's fixed stair well
counts toward §5.6's hard circulation cap, and its 16-room program leaves no floor
the exact-tiling arithmetic can close — which is why the demo brief is authored in
``garh_api.seed.demo`` instead of read from the corpus. This test is the receipt:

* it folds the seed's own op log (the same ``{type, payload}`` dicts the runner
  dispatches) into a document,
* builds the worker payload with ``garh_api.solver_enqueue``'s own helpers plus the
  same rules-engine evaluation the route uses (``test_solve_enqueue`` proves the
  DB-backed route assembles these identically),
* and runs the REAL CP-SAT pipeline in-process under the production profile,
  asserting at least one §5.6-presentable option comes back.

If a future edit to :func:`garh_api.seed.demo.demo_brief_data` re-breaks first-run
feasibility, this fails with zero options instead of a visitor finding out.

Marked ``solver``: needs ortools and tens of seconds of wall clock, so CI can place
it deliberately (it is not an ``integration`` test — no Postgres, no Redis).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.solver

REPO_ROOT = Path(__file__).resolve().parents[3]

pytest.importorskip("ortools", reason="the CP-SAT proof needs ortools")


def _solver_modules() -> tuple[Any, ...]:
    """Import the worker's parser and pipeline, same path dance as test_solve_enqueue."""
    for root in (str(REPO_ROOT), str(REPO_ROOT / "apps" / "api")):
        if root not in sys.path:
            sys.path.insert(0, root)
    try:
        from services.solver.handler import _parse_params
        from services.solver.pipeline import PRODUCTION_PROFILE, SolveContext, run_solver
    except ImportError as exc:  # pragma: no cover - environment, not product
        pytest.skip(
            "services.solver is not importable from the api tests (%s) — "
            "the seed-brief feasibility proof cannot run." % exc
        )
    return _parse_params, PRODUCTION_PROFILE, SolveContext, run_solver


def _payload_for_seeded_document() -> dict[str, Any]:
    """The ``solver.generate`` payload the API would enqueue for the demo project.

    Mirrors ``garh_api.solver_enqueue.build_solve_inputs`` field for field using its
    own helpers — the only substitution is folding the seed op log directly instead
    of reading it back from Postgres (workers hold no database either way).
    """
    from garh_api.compliance import evaluate_document
    from garh_api.seed import demo as demo_data
    from garh_api.solver_enqueue import (
        UNREGULATED_COVERAGE_PERCENT,
        UNREGULATED_FAR_X100,
        UNREGULATED_FLOORS,
        UNREGULATED_HEIGHT_MM,
        _brief_declarations,
        _limit,
        _plot_payload,
        _ratio_x100,
        _resolve_storeys,
        _room_requests,
    )
    from garh_model.fold import replay

    brief = demo_data.load_demo_brief()
    ops = demo_data.demo_op_log(brief)
    document = replay([{"type": op["type"], "payload": op["payload"]} for op in ops]).to_json()

    plot_doc = dict(document.get("plot") or {})
    boundary = list(plot_doc.get("boundary") or [])
    assert len(boundary) >= 3, "the seed op log did not fold a plot boundary"

    brief_doc = dict(document.get("brief") or {})
    brief_data = dict(brief_doc.get("data") or {})
    rooms = _room_requests(brief_data)
    assert rooms, "the seeded brief folded to zero room requests"

    report, _pack_versions = evaluate_document(document, city_pack=demo_data.DEMO_CITY_PACK)
    areas_raw = report.get("areas")
    areas: dict[str, Any] = areas_raw if isinstance(areas_raw, dict) else {}
    plot_area = areas.get("plotAreaMm2")
    plot_area = plot_area if isinstance(plot_area, int) else 0

    storeys = _resolve_storeys(None, document, brief_data)
    payload_brief: dict[str, Any] = {
        "storeys": storeys,
        "vastuMode": str(brief_doc.get("vastuMode") or "advisory"),
        "rooms": rooms,
    }
    declarations = _brief_declarations(brief_data)
    if declarations:
        payload_brief["data"] = declarations

    return {
        # What POST /solve sends by default: SolveIn's optionCount, no seed override.
        "optionCount": 3,
        "storeys": storeys,
        "plot": _plot_payload(boundary, plot_doc, areas),
        "profile": {
            "cityPack": demo_data.DEMO_CITY_PACK,
            "coveragePercent": _ratio_x100(
                areas.get("coverageAllowedMm2"), plot_area, cap=UNREGULATED_COVERAGE_PERCENT
            ),
            "farX100": _ratio_x100(areas.get("farAllowedMm2"), plot_area, cap=UNREGULATED_FAR_X100),
            "maxHeightMm": _limit(areas.get("heightAllowedMm"), cap=UNREGULATED_HEIGHT_MM),
            "maxFloors": _limit(areas.get("floorsAllowed"), cap=UNREGULATED_FLOORS),
        },
        "brief": payload_brief,
    }


async def test_first_run_generate_produces_an_option() -> None:
    """The §17 first-run promise: the seeded G+1 3BHK solves on its own plot."""
    _parse_params, PRODUCTION_PROFILE, SolveContext, run_solver = _solver_modules()

    payload = _payload_for_seeded_document()
    params = _parse_params(payload, kind="solver.generate")

    # The document really is the §17 demo: G+1 (two modelled storeys) with the
    # car space declared — without it the blr parking gate rejects every candidate,
    # and this test would "pass" only because the gate never armed.
    assert params.storeys == 2, "the demo document must fold to G+1"
    assert params.brief_data.get("carParking", 0) >= 1

    async def progress(stage: str, message: str, **data: Any) -> None:
        return None

    context = SolveContext(
        params=params,
        progress=progress,
        check_cancelled=lambda: None,
        # The stock production profile — the exact race the worker runs. The full
        # anchor set finishes in ~10-20s here; the per-candidate budget bounds the
        # worst case well under this suite's patience.
        profile=PRODUCTION_PROFILE,
    )
    result = await run_solver(context)

    assert len(result.options) >= 1, (
        "the SEEDED demo brief produced zero options — every first-time visitor's "
        "Generate now fails. Banner: %r; considered=%d rejected=%d"
        % (result.banner, result.considered, result.rejected_by_gates)
    )
    for option in result.options:
        option_json = option.to_json()
        assert option_json["ops"], "a presentable option must carry fold-able ops"
        scores = option_json.get("scores") or {}
        assert isinstance(
            scores.get("composite"), int
        ), "options must arrive scored — the gates cannot have run without scores"


def test_the_proof_cannot_pass_vacuously() -> None:
    """Negative control (CLAUDE.md rule: break the thing, watch the gate go red).

    A payload whose brief lost its rooms must REFUSE to parse — so the assertion
    above can never green-light an empty program that "solved" trivially.
    """
    _parse_params, *_ = _solver_modules()
    from services.common.errors import InvalidJobError

    payload = _payload_for_seeded_document()
    payload["brief"]["rooms"] = []
    with pytest.raises(InvalidJobError):
        _parse_params(payload, kind="solver.generate")
