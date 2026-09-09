"""Plot-edge roles come from geometry, and the engine and the editor agree on them.

``fixtures/model/edge-roles.json`` is hand-written from the definition (front = widest
road; rear = faces away from the front within 45°; the rest side-a / side-b by which
half of the plot they sit in). ``_edge_roles`` here and ``edgeRoles`` in
``apps/web/src/features/plot/geometry.ts`` both assert every case, so the compliance
tab's "rear setback" row and the editor's "Rear" chip can never name different edges.

The negative control is the rule this replaced: ``rear = front + n/2`` by index parity,
which on the 6-edge L-plot called a vertical notch edge "rear". The fixture records
what parity would have said so a regression to it is caught, not just a typo.

File-only: no datastore, always runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from garh_api.compliance import _edge_roles, build_evaluation_context

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "model" / "edge-roles.json"


def _cases() -> list[dict[str, Any]]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return list(data["cases"])


def _widths(roads: list[dict[str, Any]]) -> dict[int, int | None]:
    return {int(r["edgeIndex"]): r["widthMm"] for r in roads}


@pytest.mark.parametrize("case", _cases(), ids=lambda c: str(c["name"]))
def test_edge_roles_match_the_shared_fixture(case: dict[str, Any]) -> None:
    boundary = [(int(x), int(y)) for x, y in case["boundary"]]
    assert _edge_roles(boundary, _widths(case["roads"])) == case["roles"], case["name"]


def test_parity_rule_would_have_disagreed_on_the_l_plot() -> None:
    """Negative control: the old index-parity assignment differs on the L-plot, so the
    fixture is capable of failing — it is not simply re-describing whatever the code does."""
    case = next(c for c in _cases() if "parityRoles" in c)
    assert case["parityRoles"] != case["roles"]
    boundary = [(int(x), int(y)) for x, y in case["boundary"]]
    assert _edge_roles(boundary, _widths(case["roads"])) != case["parityRoles"]


def test_every_role_is_in_the_engine_vocabulary() -> None:
    allowed = {"front", "rear", "side-a", "side-b", "other"}
    for case in _cases():
        assert set(case["roles"]) <= allowed, case["name"]


def test_context_projection_uses_geometric_roles() -> None:
    """The projection the rules engine consumes carries the same roles — an L-plot's
    two back faces both read ``rear`` in ``plot.edges``."""
    case = next(c for c in _cases() if c["name"].startswith("L-plot"))
    document = {
        "plot": {
            "boundary": [{"x": x, "y": y} for x, y in case["boundary"]],
            "roads": [dict(r, name=None) for r in case["roads"]],
            "northDeg": 0,
            "regProfile": {"cityPack": "blr", "overrides": {}},
        },
        "brief": {"data": {}},
        "house": {"storeys": [], "walls": [], "openings": [], "rooms": [], "slabs": []},
    }
    context = build_evaluation_context(document, packs=["nbc-core", "blr"])
    roles = [edge["role"] for edge in context["plot"]["edges"]]
    assert roles == case["roles"]
    assert roles.count("rear") == 2
    # Frontage is still read off the road edge, never a second front-facing face.
    assert context["plot"]["frontageMm"] == 9000
