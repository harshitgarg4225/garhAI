#!/usr/bin/env python3
"""Run the copilot eval corpus through the REAL pipeline, on a bare python3.

For every row of ``commands.json``: build the named model state, hand the command to
:func:`garh_api.copilot_loop.run_copilot_command` with the mock provider, and assert

* the outcome class matches ``expected.outcome``;
* in-scope commands are ``applicable`` with exactly ``expected.opTypes``, their ops
  apply cleanly through ``garh_model.fold.apply_group`` (the diff really is
  applicable, not just plausible), and the dry-run fold stayed inside the §14 10ms
  budget;
* refusal outcomes carry ZERO ops.

Exit code 0 = the Phase-6 DoD numbers hold. This is the zero-dependency local twin
of ``apps/api/tests/test_copilot.py`` — same corpus, same pipeline, no pytest, no
database. ``services/dev_stubs.py`` supplies structlog/pydantic stand-ins when the
real packages are absent.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS_DIR = HERE.parent
REPO_ROOT = CORPUS_DIR.parents[2]

for path in (str(REPO_ROOT), str(REPO_ROOT / "apps" / "api")):
    if path not in sys.path:
        sys.path.insert(0, path)

from services.dev_stubs import install_worker_dep_stubs  # noqa: E402

_STUBBED = install_worker_dep_stubs()

from garh_model.fold import apply_group, try_fold  # noqa: E402
from garh_model.testing import make_empty_doc, make_two_room_plan_with_openings  # noqa: E402

from services.llm.mock import MockLlmProvider  # noqa: E402

from garh_api.copilot_loop import (  # noqa: E402
    DRY_RUN_BUDGET_MS,
    build_copilot_service,
)

#: In-scope commands that must yield applicable diffs, per the Phase-6 DoD.
REQUIRED_SUCCESS_RATE = 0.9


def _load(name: str) -> dict:
    with (CORPUS_DIR / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def build_state(states_doc: dict, name: str):
    spec = states_doc["states"][name]
    doc = make_empty_doc() if spec["base"] == "empty" else make_two_room_plan_with_openings()
    for op in spec["ops"]:
        outcome = try_fold(doc, dict(op))
        if not outcome.ok:
            raise SystemExit(
                "model state %r does not fold: %s" % (name, [i.message for i in outcome.issues])
            )
        doc = outcome.model
    return doc


async def run() -> int:
    commands = _load("commands.json")["commands"]
    states_doc = _load("model-states.json")
    states = {name: build_state(states_doc, name) for name in states_doc["states"]}
    provider = MockLlmProvider()

    failures: list = []
    in_scope = 0
    in_scope_ok = 0
    worst_dry_run_ms = 0.0

    for entry in commands:
        command = entry["command"]
        expected = entry["expected"]
        document = states[entry["modelState"]].to_json()
        service, folder, _gate = build_copilot_service(provider, document=document)
        proposal = await service.propose(command, model=document)

        got = (
            "ops"
            if proposal.applicable
            else "cannotDo"
            if proposal.cannot_do
            else "needsClarification"
            if proposal.needs_clarification
            else "invalid"
        )
        if got != expected["outcome"]:
            failures.append(
                "%s: expected %s, got %s (%s)"
                % (
                    entry["id"],
                    expected["outcome"],
                    got,
                    "; ".join(i.message for i in proposal.issues)[:160],
                )
            )
            if expected["outcome"] == "ops":
                in_scope += 1
            continue

        if expected["outcome"] != "ops":
            if proposal.ops:
                failures.append("%s: refusal outcome carried %d ops" % (entry["id"], len(proposal.ops)))
            continue

        in_scope += 1
        op_types = [str(op.get("type")) for op in proposal.ops]
        if op_types != expected.get("opTypes"):
            failures.append(
                "%s: op types %s != expected %s" % (entry["id"], op_types, expected.get("opTypes"))
            )
            continue
        if len(proposal.plain_language) != len(proposal.ops):
            failures.append("%s: %d ops but %d plain-language lines" % (entry["id"], len(proposal.ops), len(proposal.plain_language)))
            continue
        worst_dry_run_ms = max(worst_dry_run_ms, folder.last_duration_ms)
        # The diff must actually apply — fold it for real, as one group.
        try:
            apply_group(states[entry["modelState"]], [dict(op) for op in proposal.ops])
        except Exception as exc:  # noqa: BLE001 - report, don't trace
            failures.append("%s: proposal does not apply: %s" % (entry["id"], exc))
            continue
        in_scope_ok += 1

    rate = (in_scope_ok / in_scope) if in_scope else 0.0
    print(
        "copilot eval: %d commands | in-scope %d/%d applicable (%.0f%%) | worst dry-run %.2fms (budget %dms)%s"
        % (
            len(commands),
            in_scope_ok,
            in_scope,
            rate * 100,
            worst_dry_run_ms,
            DRY_RUN_BUDGET_MS,
            " | stubbed: %s" % ", ".join(_STUBBED) if _STUBBED else "",
        )
    )
    if worst_dry_run_ms >= DRY_RUN_BUDGET_MS:
        failures.append(
            "dry-run fold %.2fms breaches the §14 %dms budget" % (worst_dry_run_ms, DRY_RUN_BUDGET_MS)
        )
    if rate < REQUIRED_SUCCESS_RATE:
        failures.append(
            "in-scope success %.0f%% is below the DoD's %.0f%%"
            % (rate * 100, REQUIRED_SUCCESS_RATE * 100)
        )
    for line in failures:
        print("FAIL " + line, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
