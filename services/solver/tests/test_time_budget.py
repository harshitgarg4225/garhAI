"""§14's budget and §5.2's determinism, measured on a real CP-SAT solve.

Two claims this repository has made in prose and never executed:

* **"Solver 3 options ≤60s (fixtures); CI uses 2 workers: ≤120s"** (§14's perf
  table). Nothing timed a solve. A regression that doubled the search would have
  been invisible until an architect sat watching a spinner.
* **``DETERMINISTIC_TEST_PROFILE`` makes the output a pure function of its
  inputs** (one worker, a fixed ``random_seed``, solution/conflict limits instead
  of wall-clock time — the pipeline's own docstring, and the premise §16's
  plan-JSON goldens rest on). It has only ever been exercised against pure-Python
  fakes in ``test_pipeline``, where determinism is trivially true; against real
  CP-SAT it was an assumption.

Both run the STOCK profiles on the demo brief — the one payload in this repo with
a proven solve record — so a failure here is a real regression in the search, not
a fixture on the feasibility edge.

Marked ``solver``: these take tens of seconds and need ortools.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import pytest

pytest.importorskip("ortools")

from services.solver.handler import _parse_params
from services.solver.pipeline import (
    DETERMINISTIC_TEST_PROFILE,
    PRODUCTION_PROFILE,
    SolveContext,
    run_solver,
)
from services.solver.tests.test_stages_integration import PAYLOAD

pytestmark = pytest.mark.solver

#: §14: 60 s on a full machine, 120 s where CI gives the search two workers. The
#: production profile races ``num_search_workers=8`` per candidate and solves two
#: candidates at a time, so a box with fewer cores than that is the CI case —
#: measured against the looser figure rather than pretending the machine is bigger.
FULL_MACHINE_CORES = 8
BUDGET_SECONDS = 60
CI_BUDGET_SECONDS = 120


def _budget_seconds() -> int:
    return BUDGET_SECONDS if (os.cpu_count() or 1) >= FULL_MACHINE_CORES else CI_BUDGET_SECONDS


async def _noop(stage: str, message: str, **data: Any) -> None:
    return None


def _solve(profile: Any, *, seed: int | None = None) -> Any:
    payload = dict(PAYLOAD)
    if seed is not None:
        payload["seed"] = seed
    params = _parse_params(payload, kind="solver.generate")
    context = SolveContext(
        params=params,
        progress=_noop,
        check_cancelled=lambda: None,
        profile=profile,
    )
    return asyncio.run(run_solver(context))


def test_three_options_arrive_inside_the_section_14_budget() -> None:
    """The §14 row, executed: 3 options on the demo brief, inside the wall clock."""
    budget = _budget_seconds()
    started = time.monotonic()
    result = _solve(PRODUCTION_PROFILE)
    elapsed = time.monotonic() - started

    assert len(result.options) >= 3, (
        "§14 promises three options for this brief; got %d (banner %r, considered %d, "
        "rejected %d)"
        % (len(result.options), result.banner, result.considered, result.rejected_by_gates)
    )
    assert elapsed <= budget, (
        "a Generate took %.1f s against the §14 budget of %d s on %s cores — the search "
        "regressed, or a stage started doing work it did not do before"
        % (elapsed, budget, os.cpu_count())
    )
    # Not a vacuous pass: the options are real plans, not empty shells.
    for option in result.options:
        assert option.ops, "an option inside the budget must still carry its ops"
        assert option.scores.composite >= 55, "§5.6's floor still applies under the clock"


def test_the_deterministic_profile_pins_the_search() -> None:
    """Same params + DETERMINISTIC_TEST_PROFILE ⇒ the same plans, twice.

    What is pinned is the SEARCH: the option ids (content-addressed over the
    placements, the signature and the seed), the geometry behind them, the scores,
    and how many candidates were considered and gated. Element ids are not pinned
    and are not meant to be — ``stage_b`` mints them through ``garh_model.ids`` so
    production gets globally unique ULIDs; the next test installs the seeded
    factory those ids exist for, and gets byte-equality.

    Run against REAL CP-SAT. ``test_pipeline`` proves the same property with
    pure-Python fakes, where determinism is trivially true.
    """
    first = _solve(DETERMINISTIC_TEST_PROFILE)
    second = _solve(DETERMINISTIC_TEST_PROFILE)

    assert first.options, "a deterministic run that produces nothing proves nothing"
    assert [o.id for o in first.options] == [o.id for o in second.options], (
        "two runs of the deterministic profile found different plans — the profile "
        "does not pin the search"
    )
    assert (first.considered, first.rejected_by_gates) == (
        second.considered,
        second.rejected_by_gates,
    )
    for a, b in zip(first.options, second.options, strict=True):
        assert a.placements == b.placements, "same id, different rooms — the id lies"
        assert a.scores == b.scores
        assert a.signature == b.signature
        assert a.seed == b.seed == DETERMINISTIC_TEST_PROFILE.random_seed


def test_with_the_seeded_id_factory_the_plan_json_is_byte_identical() -> None:
    """§16's goldens compare plan JSON with tolerance 0. This is why they can.

    ``stage_b``'s docstring promises exactly this: "a test that installs
    ``seeded_ulid_factory`` gets byte-identical JSON, and production gets globally
    unique ids from the same code path". Nothing executed it until now.
    """
    from garh_model.ids import seeded_ulid_factory, set_ulid_factory

    try:
        set_ulid_factory(seeded_ulid_factory(1))
        first = _solve(DETERMINISTIC_TEST_PROFILE)
        set_ulid_factory(seeded_ulid_factory(1))
        second = _solve(DETERMINISTIC_TEST_PROFILE)
    finally:
        set_ulid_factory(None)

    assert first.options, "a deterministic run that produces nothing proves nothing"
    assert json.dumps(first.to_json(), sort_keys=True) == json.dumps(
        second.to_json(), sort_keys=True
    ), "the same seeds and the same id factory produced different plan JSON"


def test_the_determinism_check_can_go_red() -> None:
    """NEGATIVE CONTROL: change only the CP-SAT seed and the plans must differ.

    Without this, the tests above would pass just as happily against a solver that
    ignored its inputs entirely and returned one cached answer.
    """
    from dataclasses import replace

    baseline = _solve(DETERMINISTIC_TEST_PROFILE)
    different = _solve(replace(DETERMINISTIC_TEST_PROFILE, random_seed=12345))
    assert baseline.options and different.options
    assert [o.id for o in baseline.options] != [
        o.id for o in different.options
    ], "two different CP-SAT seeds produced the same plans — the seed reaches nothing"
