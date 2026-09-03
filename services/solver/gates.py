"""§5.6 — presentability gates. **Fully implemented.**

    An option is presentable iff: all hard rules pass, furniture-fit passes for all
    habitable rooms, circulation <=18%, composite >=55. If <3 options clear gates,
    relax soft weights once and re-run; if still <3, return what passed with an honest
    banner ("2 strong options found for this plot").

This is golden rule 2 made executable — *feasible is not plausible; never show a
hard-fail plan*. It is a pure predicate over a scored option, so it is real today, and
it is deliberately the last word: nothing downstream may re-admit a rejected option.

Every rejection carries a reason. They surface in the job log and in the "why only two
options?" explanation, so an architect is never left guessing why a plan they can
almost picture did not appear.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from services.solver.types import PlanOption

#: §5.6 thresholds. Changing one changes what users are shown — treat as product config.
MAX_CIRCULATION_PERCENT = 18
MIN_COMPOSITE_SCORE = 55
MIN_FURNITURE_FIT = 100  # every habitable room must take its standard furniture set
#: §5.5 keeps 3-5; below the minimum the caller relaxes soft weights and re-runs once.
TARGET_OPTION_COUNT = 3


@dataclass(frozen=True)
class GateResult:
    """Whether one option may be shown, and why not when it may not."""

    passed: bool
    reasons: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {"passed": self.passed, "reasons": list(self.reasons)}


def circulation_problems(ops: Sequence[Mapping[str, Any]]) -> list[str]:
    """Fold an option's ops with the real model core and walk its doors.

    Empty when every room on every storey can be reached from the entrance (ground)
    or the stair (above) through openings, and no habitable room is reached only
    through a bath. The rules engine has no rule for this, so a plan with a walled-off
    kitchen was gate-clean — this is the gate. Import is lazy: the worker image carries
    ``apps/api`` on its path; a unit test without it gets an honest ImportError.
    """
    from garh_model import replay
    from garh_model.circulation import reachability_problems
    from garh_model.validate import OpRejectedError

    # Only the fabric matters here: storeys, walls, openings, stairs, and the room
    # assignments that name types (a bath is a destination, not a corridor). Anything
    # else an option carries is irrelevant to whether a person can walk through it,
    # and a room op with no room id (test stubs emit those) is skipped, not fatal.
    fabric = []
    for op in ops:
        kind = str(op["type"])
        payload = dict(op["payload"])
        if (
            kind in ("storey.add", "wall.add", "opening.add", "stair.add")
            or kind == "room.assign"
            and payload.get("roomId")
        ):
            fabric.append({"type": kind, "payload": payload})
    try:
        house = replay(fabric).house
    except OpRejectedError as exc:
        # An option whose geometry the model core refuses could never be applied
        # either; naming that here is more honest than a silent green.
        return ["the option's geometry could not be folded: %s" % exc]
    return reachability_problems(house)


def check_option(
    option: PlanOption,
    *,
    compliance: Sequence[Mapping[str, object]] | None = None,
    max_circulation_percent: int = MAX_CIRCULATION_PERCENT,
    min_composite: int = MIN_COMPOSITE_SCORE,
) -> GateResult:
    """Apply all four §5.6 gates to one scored option."""
    reasons: list[str] = []
    findings = compliance if compliance is not None else option.compliance

    hard_failures = [
        str(finding.get("ruleId") or "unknown rule")
        for finding in findings
        if str(finding.get("status")) == "fail"
    ]
    if hard_failures:
        reasons.append(
            "breaks %d hard rule(s): %s"
            % (len(hard_failures), ", ".join(sorted(set(hard_failures))[:5]))
        )

    if option.scores.furniture_fit < MIN_FURNITURE_FIT:
        reasons.append(
            "a habitable room can't take its standard furniture (fit %d/100)"
            % option.scores.furniture_fit
        )

    if option.scores.circulation_percent > max_circulation_percent:
        reasons.append(
            "circulation is %d%% of the floor area, over the %d%% limit"
            % (option.scores.circulation_percent, max_circulation_percent)
        )

    if option.scores.composite < min_composite:
        reasons.append(
            "overall score %d is below the %d needed to be worth showing"
            % (option.scores.composite, min_composite)
        )

    return GateResult(passed=not reasons, reasons=tuple(reasons))


def filter_presentable(
    options: Sequence[PlanOption],
    *,
    max_circulation_percent: int = MAX_CIRCULATION_PERCENT,
    min_composite: int = MIN_COMPOSITE_SCORE,
) -> tuple[tuple[PlanOption, ...], dict[str, GateResult]]:
    """Split options into presentable ones and a per-option rejection record."""
    kept: list[PlanOption] = []
    results: dict[str, GateResult] = {}
    for option in options:
        result = check_option(
            option,
            max_circulation_percent=max_circulation_percent,
            min_composite=min_composite,
        )
        results[option.id] = result
        if result.passed:
            kept.append(option)
    return tuple(kept), results


def banner_for(count: int, *, target: int = TARGET_OPTION_COUNT) -> str | None:
    """§5.6's honest banner. ``None`` when there is nothing to explain.

    Deliberately never apologetic and never padded — a plot that only supports two good
    plans is a fact about the plot, and saying so is more useful than a third option
    the architect would discard.
    """
    if count >= target:
        return None
    if count == 0:
        return (
            "We couldn't find a plan that meets every rule for this plot and brief. "
            "Try relaxing a room size, adding a floor, or checking the setbacks."
        )
    if count == 1:
        return "1 strong option found for this plot."
    return "%d strong options found for this plot." % count


def should_relax_and_retry(
    presentable_count: int, *, already_relaxed: bool, target: int = TARGET_OPTION_COUNT
) -> bool:
    """§5.6: relax soft weights **once** when too few options clear the gates.

    Once, not until-it-works: the hard rules are never relaxed, so a second relaxation
    would only trade away plan quality for a number on a screen.
    """
    return presentable_count < target and not already_relaxed


__all__ = [
    "MAX_CIRCULATION_PERCENT",
    "MIN_COMPOSITE_SCORE",
    "MIN_FURNITURE_FIT",
    "TARGET_OPTION_COUNT",
    "GateResult",
    "banner_for",
    "check_option",
    "filter_presentable",
    "should_relax_and_retry",
]
