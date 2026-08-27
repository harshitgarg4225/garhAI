"""The Vastu 0-100 score, with the per-rule breakdown the compass wheel renders.

The formula is the pack's, not ours (``vastu.json`` -> ``scoring``):

    score = scale.max * SUM(weight_i * satisfaction_i) / SUM(weight_i)

over **applicable rules only**. Three properties are load-bearing:

* A rule with no matching element is ``not_applicable`` and drops out of *both*
  sums. A house with no pooja room is not penalised for placing it badly, and is
  not credited for placing it well either.
* ``satisfaction_i`` is an exact rational in [0, 1]: 1 in ``allow``,
  ``fallback.scoreRatio`` in ``fallback.allow``, 0 otherwise; with several matched
  elements it is their arithmetic mean, still exact. Everything here is
  :class:`~fractions.Fraction`.
* Rounding is half-up and happens **once**, on the final score. Never on an
  intermediate — a score a client sees has to be reproducible from the published
  weights by hand.

One rule set serves both brief modes, so no rule id is ever duplicated:
``advisory`` clamps every severity to ``warn`` and scores; ``strict`` lets
severities through and lets the solver treat ``hard`` rules as constraints;
``off`` means the pack is never loaded at all (:func:`garh_rules.engine.evaluate`
drops it before evaluation, so ``off`` produces no rows rather than a wall of
``not_applicable``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .packs import Pack, ScoringMode
from .ratio import round_half_up

__all__ = [
    "RuleScore",
    "GroupScore",
    "VastuScore",
    "mode_for",
    "clamp_severity",
    "build_score",
]

_SEVERITY_RANK: Mapping[str, int] = {"warn": 0, "fail": 1}


def mode_for(pack: Pack, vastu_mode: str) -> ScoringMode | None:
    """The scoring mode in force, or ``None`` when the pack does not score."""
    if pack.scoring is None:
        return None
    return pack.scoring.modes.get(vastu_mode)


def clamp_severity(severity: str, ceiling: str | None) -> str:
    """Clamp a declared severity to a mode's ceiling. ``warn`` in advisory mode is
    how one rule set serves both modes without duplicating a single rule id."""
    if ceiling is None:
        return severity
    if _SEVERITY_RANK.get(severity, 0) > _SEVERITY_RANK.get(ceiling, 0):
        return ceiling
    return severity


@dataclass(frozen=True)
class RuleScore:
    """One rule's contribution. ``satisfaction`` is exact; ``percent`` is display."""

    rule_id: str
    title: str
    group: str
    weight: int
    satisfaction: Fraction
    status: str
    hard: bool

    @property
    def weighted(self) -> Fraction:
        return self.satisfaction * self.weight

    def to_json(self) -> dict[str, Any]:
        return {
            "ruleId": self.rule_id,
            "title": self.title,
            "group": self.group,
            "weight": self.weight,
            "satisfaction": {
                "num": self.satisfaction.numerator,
                "den": self.satisfaction.denominator,
            },
            "percent": round_half_up(self.satisfaction * 100),
            "status": self.status,
            "hard": self.hard,
        }


@dataclass(frozen=True)
class GroupScore:
    """A compass-wheel bucket. Weight is **derived** — the sum of its members'."""

    id: str
    label: str
    description: str
    weight: int
    score: int
    rule_ids: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "weight": self.weight,
            "score": self.score,
            "ruleIds": list(self.rule_ids),
        }


@dataclass(frozen=True)
class VastuScore:
    pack_id: str
    pack_version: str
    mode: str
    enforce: bool
    severity_ceiling: str
    score: int | None
    scale_min: int
    scale_max: int
    applicable_weight: int
    total_weight: int
    rules: tuple[RuleScore, ...]
    groups: tuple[GroupScore, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "packId": self.pack_id,
            "packVersion": self.pack_version,
            "mode": self.mode,
            "enforce": self.enforce,
            "severityCeiling": self.severity_ceiling,
            "score": self.score,
            "scale": {"min": self.scale_min, "max": self.scale_max},
            "applicableWeight": self.applicable_weight,
            "totalWeight": self.total_weight,
            "rules": [r.to_json() for r in self.rules],
            "groups": [g.to_json() for g in self.groups],
        }

    def hard_violations(self) -> tuple[str, ...]:
        """Rule ids marked ``hard`` that are not satisfied — what §5.2's strict mode
        may encode as CP-SAT constraints."""
        return tuple(r.rule_id for r in self.rules if r.hard and r.satisfaction < 1)


def build_score(
    pack: Pack,
    mode_name: str,
    contributions: Sequence[RuleScore],
) -> VastuScore:
    """Aggregate a scoring pack's applicable rules into one 0-100 score.

    ``contributions`` holds only the **applicable** rules (the caller drops
    ``not_applicable`` rows, which is what keeps them out of both sums). An empty
    list yields ``score = None`` — no applicable Vastu rule means there is nothing
    to score, and 0 would read as "terrible" rather than "not assessed".
    """
    if pack.scoring is None:  # pragma: no cover - caller checks
        raise ValueError("pack %s declares no scoring block" % pack.id)
    scoring = pack.scoring
    mode = scoring.modes.get(mode_name)
    enforce = bool(mode.enforce) if mode else False
    ceiling = mode.severity_ceiling if mode else "warn"
    wants_score = bool(mode.score) if mode else False

    total_weight = sum(r.weight for r in contributions)
    numerator = sum((r.weighted for r in contributions), Fraction(0))
    score: int | None = None
    if wants_score and total_weight > 0:
        score = round_half_up(Fraction(scoring.scale_max) * numerator / total_weight)

    groups: list[GroupScore] = []
    for group_id, label, description in scoring.groups:
        members = [r for r in contributions if r.group == group_id]
        weight = sum(r.weight for r in members)
        group_numerator = sum((r.weighted for r in members), Fraction(0))
        # Group scores are a display breakdown, so each is rounded for its own
        # readout. The overall score is NOT built from them — it is one weighted
        # mean over the rules, rounded once, exactly as the pack states.
        group_score = (
            round_half_up(Fraction(scoring.scale_max) * group_numerator / weight)
            if weight > 0
            else 0
        )
        groups.append(
            GroupScore(
                id=group_id,
                label=label,
                description=description,
                weight=weight,
                score=group_score,
                rule_ids=tuple(r.rule_id for r in members),
            )
        )

    return VastuScore(
        pack_id=pack.id,
        pack_version=pack.version,
        mode=mode_name,
        enforce=enforce,
        severity_ceiling=ceiling,
        score=score,
        scale_min=scoring.scale_min,
        scale_max=scoring.scale_max,
        applicable_weight=total_weight,
        total_weight=sum(int(rule.get("weight") or 0) for rule in pack.raw.get("rules", ())),
        rules=tuple(contributions),
        groups=tuple(groups),
    )
