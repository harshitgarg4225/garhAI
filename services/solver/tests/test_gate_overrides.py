"""An accepted override un-blocks Generate, and a value override does not.

`POST /projects/:id/compliance/overrides` is the architect saying "I know this rule
fails here, and here is why" — with their name and the time in the audit trail. The
route's docstring has always said it "stops blocking the solver gate". It did not:
`check_option` filtered on `status == "fail"` and never read `overridden`, so an
architect could accept a deviation, export the drawing set, and then find Generate
still refusing to offer them anything. Same project, same rule, two answers.

The subtlety this file exists for is in the row JSON, which uses ONE flag for two
different decisions:

* `overridden: true` is set for EITHER kind — it is a display flag meaning "an
  architect touched this rule";
* `valueOverridden: true` marks the second kind, where the architect moved the LIMIT
  to a number of their own.

A design that fails against the architect's own number has not been accepted by
anybody, so only the first kind may un-block. `EvaluationReport.blocking_failures`
already drew that line; this gate now mirrors it, and the second test is what stops
someone "simplifying" the check to `if row.get("overridden")` and quietly turning
every rule into an opt-out.
"""

from __future__ import annotations

from typing import Any

from services.solver import gates
from services.solver.types import PlanOption, ScoreBreakdown

RULE = "blr.setback.front.min"


def _row(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ruleId": RULE,
        "status": "fail",
        "severity": "fail",
        "message": "The front setback is 1 500 mm - it needs at least 3 000 mm.",
    }
    row.update(over)
    return row


def _option(*rows: dict[str, Any]) -> PlanOption:
    """A scored option that clears every gate EXCEPT, possibly, the rule gate.

    The other three thresholds are set comfortably clear on purpose: if this option
    is refused, the only thing that can have refused it is the rule row.
    """
    return PlanOption(
        id="opt_1",
        rank=0,
        scores=ScoreBreakdown(
            composite=90,
            # MIN_FURNITURE_FIT is 100: EVERY habitable room must take its standard
            # furniture set, so 95 is a fail, not a near-miss.
            furniture_fit=100,
            circulation_percent=10,
        ),
        placements=(),
        ops=(),
        signature=(),
        stair_anchor_id="stair_1",
        built_up_mm2=80_000_000,
        footprint_mm2=40_000_000,
        compliance=tuple(rows),
    )


def test_a_plain_failure_still_blocks() -> None:
    """The baseline. Without this, the two tests below prove nothing."""
    result = gates.check_option(_option(_row()))
    assert not result.passed
    assert any(RULE in reason for reason in result.reasons), result.reasons


def test_an_accepted_override_un_blocks_generate() -> None:
    result = gates.check_option(
        _option(_row(overridden=True, overrideReason="Corner plot; BBMP approved the deviation."))
    )
    assert result.passed, (
        "the architect accepted this rule in writing, the export honours it, and "
        "Generate still refuses — the override button is telling them something "
        "that is not true. %s" % (result.reasons,)
    )
    assert not result.reasons


def test_a_VALUE_override_does_not() -> None:
    """THE CONTROL. A value override moves the limit to the architect's own number.

    Failing against your own number is not an accepted deviation, it is a design that
    does not meet the requirement you set. The row still carries `overridden: true`
    for chip styling, so a gate that read only that flag would wave this through —
    and every rule in every pack would become editable into a pass.
    """
    result = gates.check_option(
        _option(
            _row(
                overridden=True,
                valueOverridden=True,
                overrideValueKeys=["setbackFrontMm"],
                originalLimit=3_000,
            )
        )
    )
    assert not result.passed, (
        "a plan that fails against the architect's OWN limit was presented. The gate "
        "is reading `overridden` without checking `valueOverridden`, which makes "
        "every rule opt-out by editing its value."
    )
    assert any(RULE in reason for reason in result.reasons), result.reasons


def test_one_accepted_and_one_not_still_blocks_on_the_one_that_is_not() -> None:
    """An override is per rule. Accepting one does not accept the next."""
    other = "nbc.room.habitable.area.min"
    result = gates.check_option(
        _option(
            _row(overridden=True, overrideReason="approved"),
            _row(ruleId=other),
        )
    )
    assert not result.passed
    joined = " ".join(result.reasons)
    assert other in joined, result.reasons
    assert (
        RULE not in joined
    ), "the accepted rule is still being named as a reason for refusing the plan"
