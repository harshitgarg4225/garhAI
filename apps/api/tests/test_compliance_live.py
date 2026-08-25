"""Regression: the live (unpersisted) compliance projection must be constructible.

``ComplianceOut`` carries a FIELD named ``live`` (wire contract: "this run was not
persisted"). A classmethod constructor with the same name was silently stripped by
pydantic's model construction — every annotated name is claimed as a field and the
same-named attribute removed from the class namespace — so ``ComplianceOut.live(...)``
raised ``AttributeError`` at request time and ``GET /projects/{id}/compliance``
returned 500 on ANY project whose rules actually evaluated (i.e. any project with a
plot boundary). Nothing covered the evaluated path: the only test touching the route
was a cross-tenant 404 probe, so the defect shipped green.

The constructor is now ``live_run``. This test pins both halves: the constructor is
reachable, and the ``live`` field it sets still serialises on the wire.
"""

from __future__ import annotations

import uuid

from garh_api.schemas.project import ComplianceOut


def test_live_run_constructor_exists_and_marks_the_run_unpersisted() -> None:
    project_id = uuid.uuid4()
    payload = {
        "results": [{"ruleId": "nbc-core/far", "status": "pass"}],
        "counts": {"pass": 1, "warn": 0, "fail": 0, "not_applicable": 0},
        "worstStatus": "pass",
        "notes": ["projection approximated the mezzanine as a storey"],
    }

    out = ComplianceOut.live_run(project_id, payload, {"nbc-core": "0.1.0"})

    assert out.evaluated is True
    assert out.live is True
    assert out.report_id is None and out.created_at is None
    assert out.design_version_id is None
    assert out.counts["pass"] == 1
    assert out.worst_status == "pass"

    wire = out.model_dump(by_alias=True)
    assert wire["live"] is True, "the wire field the UI keys on must survive"
