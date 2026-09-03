"""A Vastu-mode brief with a staircase must evaluate, not crash the rules pass.

The engine refuses to classify a stair's 3x3 zone without a centroid, and the
projection sent it centroidMm: None for every stair. Nobody noticed because the
Vastu mode never reached the fold (PUT /brief wrote it into the data patch). The day
that was fixed, every Generate with a stair died in the solver's rules pass with
ComplianceUnavailable. This pins the projection: a stair row carries the centroid
of its footprint, and a doc with vastuMode on evaluates end to end.
"""

from __future__ import annotations

from garh_api.compliance import evaluate_document, packs_for
from garh_model import replay
from garh_model.ops import Op
from garh_model.testing import opening_ops, two_room_plan_ops

STAIR = Op(
    type="stair.add",
    payload={
        "id": "stair_01J00000000000000000000TST",
        "storeyId": None,  # filled in below from the fixture's first storey
        "kind": "straight",
        "origin": {"x": 1200, "y": 1200},
        "direction": "N",
        "riserMm": 150,
        "treadMm": 250,
        "widthMm": 1000,
        "risersCount": 20,
        "landing": None,
    },
)


def _doc_with_stair(vastu_mode: str):
    base = [*two_room_plan_ops(), *opening_ops()]
    storey_id = replay(base).house.storeys[0].id
    stair = Op(type=STAIR.type, payload={**STAIR.payload, "storeyId": storey_id})
    vastu = Op(
        type="brief.update",
        payload={"patch": {"rooms": [{"type": "kitchen", "count": 1}]}, "vastuMode": vastu_mode},
    )
    return replay([*base, stair, vastu]).to_json()


def test_the_stair_row_carries_its_footprint_centroid() -> None:
    from garh_api.compliance import _stair_centroid_mm

    payload = {**STAIR.payload, "storeyId": "storey_x"}
    centroid = _stair_centroid_mm(payload)
    assert centroid is not None and len(centroid) == 2
    assert all(isinstance(v, int) for v in centroid)
    assert _stair_centroid_mm({"id": "broken"}) is None


def test_vastu_mode_with_a_stair_evaluates_instead_of_crashing() -> None:
    doc = _doc_with_stair("advisory")
    assert "vastu" in packs_for(doc)
    payload, _versions = evaluate_document(doc, city_pack="blr")
    results = payload.get("results") or []
    assert results, "the rules ran"
    stair_rows = [
        r
        for r in results
        if "stair" in str(r.get("ruleId", "")) and "vastu" in str(r.get("packId", ""))
    ]
    assert stair_rows, (
        "the vastu pack looked at the stair: %s" % sorted({r.get("ruleId") for r in results})[:12]
    )
    assert all(r.get("status") in ("pass", "warn", "fail") for r in stair_rows)


def test_vastu_off_still_evaluates_the_same_doc() -> None:
    payload, _versions = evaluate_document(_doc_with_stair("off"), city_pack="blr")
    assert payload.get("results")
