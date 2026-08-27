"""The evaluator. Pure, deterministic, integer-only, under 100 ms for a house.

    results = evaluate(context).results

§6: "engine takes (model, plot, profile, packs) -> results[] {ruleId, status,
actual, limit, cite, fixHint, elements[], confidence}. Pure, deterministic,
<100ms for a house — safe to run debounced on every edit and inside the solver
critic." Everything about this module follows from that sentence:

* **Pure.** No I/O in the hot path. Packs are loaded once and memoised
  (:func:`garh_rules.packs.load_pack_set`); nothing here opens a file, touches a
  clock, or reads an environment variable.
* **Deterministic.** Results come back in resolved pack order — root pack first,
  then each child in authoring order — which is also the order a compliance
  annexure reads in. No dict iteration, no set ordering, no sort on a float.
* **One row per rule.** Several instances of a scope collapse: worst status wins,
  ``elements[]`` lists every offender, and ``actual``/``limit`` come from the
  *governing* instance — the worst violation, or on a clean run the tightest
  margin. The per-element detail rides along in ``instances[]`` so a chip can still
  say "Bedroom 2 is 8.9 m2".
* **Never a silent pass.** ``not_applicable`` is a distinct status with a reason.
  An unloadable pack raises. A missing centroid on a Vastu target raises. A rule
  whose scope produced nothing is reported as unevaluated, not as satisfied.

The collapse rule in one line: sort matched instances by
``(satisfied, slack, elementId)`` and take the first. Sorting on *slack* rather
than on the raw measurement is what makes ``ventilation_ratio_min`` work — its
limit differs per room, so raw values are not comparable across rooms but margins
are.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .areas import AreaStatement, build_area_statement
from .checks import (
    UNION_ACTUAL_CHECKS,
    AppliedValueOverride,
    result_unit_of,
    run_check,
    scope_of,
    substitute_value_override,
    union_actual,
)
from .context import EvaluationContext
from .formatting import render_message
from .packs import PackSet, Rule, load_pack_set
from .predicates import SCOPE_WHEN_FIELDS, bind_project_fields, when_matches
from .results import (
    FAIL,
    NOT_APPLICABLE,
    PASS,
    WARN,
    ResultInstance,
    RuleResult,
    worst_status,
)
from .scope import CheckEnv, Instance, Outcome, instances_for
from .scoring import RuleScore, VastuScore, build_score, clamp_severity, mode_for

__all__ = ["EvaluationReport", "evaluate", "evaluate_parts", "PERFORMANCE_BUDGET_MS"]

#: §14's budget for a full run on a house. Asserted by ``tests/test_performance.py``.
PERFORMANCE_BUDGET_MS = 100

#: Every ``when`` field that only exists inside a scope. A rule whose gate uses none
#: of these can be decided once instead of once per instance — which is most city
#: rules, and most of the speed.
_SCOPE_ONLY_FIELDS = frozenset(name for names in SCOPE_WHEN_FIELDS.values() for name in names)


@dataclass(frozen=True)
class EvaluationReport:
    """One compliance run: rows, counts, the Vastu score, and the area statement."""

    results: tuple[RuleResult, ...]
    packs: tuple[str, ...]
    pack_versions: Mapping[str, str]
    counts: Mapping[str, int]
    areas: AreaStatement
    scores: tuple[VastuScore, ...] = ()
    warnings: tuple[str, ...] = ()
    disclaimers: tuple[tuple[str, str], ...] = ()

    # -- views -------------------------------------------------------------
    @property
    def score(self) -> VastuScore | None:
        """The Vastu score, when a scoring pack was loaded and the mode is not ``off``."""
        return self.scores[0] if self.scores else None

    @property
    def applicable(self) -> tuple[RuleResult, ...]:
        return tuple(r for r in self.results if r.applicable)

    def by_status(self, status: str) -> tuple[RuleResult, ...]:
        return tuple(r for r in self.results if r.status == status)

    def worst_status(self) -> str:
        return worst_status([r.status for r in self.results])

    def failures(self) -> tuple[RuleResult, ...]:
        """Every ``fail`` row, overrides included — the honest list."""
        return tuple(r for r in self.results if r.status == FAIL)

    def blocking_failures(self) -> tuple[RuleResult, ...]:
        """``fail`` rows the architect has **not** knowingly accepted.

        This is the §5.6 solver gate ("all hard rules pass") and the export
        warning. Overridden rows are excluded here and only here: an override is a
        logged human decision on this project, and re-blocking on it would make the
        override button a lie. They stay in :meth:`failures` and in the report.

        Value-overridden rows (``value_overridden``) are NOT excluded: a value
        override moves the limit to the architect's number, and a design that fails
        against the architect's own number is still a blocking failure.
        """
        return tuple(r for r in self.results if r.status == FAIL and not r.overridden)

    def is_presentable(self) -> bool:
        """§5.6's hard-rule gate: no un-overridden ``fail`` row."""
        return not self.blocking_failures()

    def rule(self, rule_id: str) -> RuleResult | None:
        for result in self.results:
            if result.rule_id == rule_id:
                return result
        return None

    def to_json(self, *, include_not_applicable: bool = True, full: bool = False) -> dict[str, Any]:
        """The shape ``compliance_reports.results`` / ``GET /compliance`` carry.

        ``not_applicable`` rows are included by default: "12 of 118 rules applied to
        this plot" is information an architect wants, and dropping them makes a
        report look thinner than the run actually was. The API may omit them
        (``x-garh-check-meta.statuses`` allows it) for the live editor's chip strip.
        """
        rows = [
            r.to_json(full=full)
            for r in self.results
            if include_not_applicable or r.status != NOT_APPLICABLE
        ]
        return {
            "packs": list(self.packs),
            "packVersions": dict(self.pack_versions),
            "counts": dict(self.counts),
            "worstStatus": self.worst_status(),
            "presentable": self.is_presentable(),
            "results": rows,
            "areas": self.areas.to_json(),
            "scores": [s.to_json() for s in self.scores],
            "vastuScore": self.score.score if self.score is not None else None,
            "warnings": list(self.warnings),
            "disclaimers": [
                {"packId": pack_id, "text": text} for pack_id, text in self.disclaimers
            ],
        }


# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------


def _not_applicable(
    rule: Rule,
    unit: str,
    reason: str,
    failing_field: str | None,
    overridden: bool,
    override_reason: str | None,
    effective_severity: str,
) -> RuleResult:
    """A row for a rule that did not apply.

    ``severity`` is the *effective* one — clamped by the scoring mode's ceiling and
    by any ``relax-to-warn`` override — exactly as on an evaluated row. A client
    that colours rows by ``severity`` must not see "fail" on an advisory-mode Vastu
    rule just because the rule happened not to apply; ``declared_severity`` is
    where the pack's own value lives.
    """
    return RuleResult(
        rule_id=rule.id,
        pack_id=rule.pack_id,
        status=NOT_APPLICABLE,
        actual=None,
        limit=None,
        unit=unit,
        title=rule.title,
        message=_NOT_APPLICABLE_MESSAGES.get(reason, "This rule does not apply to this design."),
        cite=rule.cite_full,
        cite_short=rule.cite,
        fix_hint=rule.fix,
        confidence=rule.confidence,
        severity=effective_severity,
        declared_severity=rule.severity,
        check_type=rule.check.type,
        cite_url=rule.cite_url,
        autofix=rule.autofix,
        hard=rule.hard,
        weight=rule.weight,
        group=rule.group,
        tags=rule.tags,
        overridden=overridden,
        override_reason=override_reason,
        not_applicable_reason=reason,
        not_applicable_field=failing_field,
        relaxed_to_warn=rule.relaxed_to_warn,
    )


_NOT_APPLICABLE_MESSAGES: Mapping[str, str] = {
    "when": "This rule does not apply to this plot or building type.",
    "no-instances": "There is nothing in the design for this rule to measure yet.",
}


def _instance_status(outcome: Outcome, effective_severity: str) -> str:
    if outcome.satisfied:
        return PASS
    if outcome.degraded:
        return WARN
    return effective_severity


def _evaluate_rule(
    rule: Rule,
    env: CheckEnv,
    project_fields: Mapping[str, Any],
    ceilings: Mapping[str, str | None],
) -> RuleResult:
    context = env.context
    unit = result_unit_of(rule.check)
    scope = scope_of(rule.check)
    override = context.profile.overrides.get(rule.id)
    overridden = override is not None
    override_reason = override.reason if override is not None else None
    effective_severity = clamp_severity(rule.severity, ceilings.get(rule.pack_id))

    # A gate that names no scope-bound field is decided once for the whole rule.
    gate_is_project_only = not (frozenset(rule.when) & _SCOPE_ONLY_FIELDS)
    if gate_is_project_only and rule.when:
        matched_gate, failing = when_matches(rule.when, project_fields)
        if not matched_gate:
            return _not_applicable(
                rule, unit, "when", failing, overridden, override_reason, effective_severity
            )

    instances = instances_for(rule.check, scope, env)
    if not instances:
        return _not_applicable(
            rule, unit, "no-instances", None, overridden, override_reason, effective_severity
        )

    # Phase-3 wiring of the Phase-2 deferral (DECISIONS.md, 2026-08-05): the
    # architect's value overrides substitute into the check limit per instance —
    # per instance because a `setback_min` rule on the `all` selector meets edges
    # whose roles want different override keys. The un-substituted check is kept
    # for the governing row's `original_limit`, and a fail against the overridden
    # limit still fails: overrides move the line, they never silence the check.
    value_overrides = context.profile.value_overrides
    matched: list[tuple[Instance, Outcome, AppliedValueOverride | None]] = []
    gate_failure: str | None = None
    for instance in instances:
        if not gate_is_project_only and rule.when:
            fields: dict[str, Any] = dict(project_fields)
            fields.update(instance.fields)
            ok, failing = when_matches(rule.when, fields)
            if not ok:
                gate_failure = gate_failure or failing
                continue
        effective_check, applied = substitute_value_override(rule.check, instance, value_overrides)
        matched.append((instance, run_check(effective_check, instance, env), applied))

    if not matched:
        return _not_applicable(
            rule, unit, "when", gate_failure, overridden, override_reason, effective_severity
        )

    # Worst status first, then tightest margin, then element id — a total order, so
    # two runs on the same model always quote the same room.
    governing_instance, governing, governing_applied = min(
        matched,
        key=lambda entry: (
            0 if not entry[1].satisfied else 1,
            entry[1].order_key,
            entry[0].element_id or "",
        ),
    )

    severe = any(not outcome.satisfied and not outcome.degraded for _, outcome, _a in matched)
    violated = any(not outcome.satisfied for _, outcome, _a in matched)
    status = PASS if not violated else (effective_severity if severe else WARN)

    if rule.check.type in UNION_ACTUAL_CHECKS:
        actual: Any = union_actual([outcome for _, outcome, _a in matched])
    else:
        actual = governing.actual
    limit = governing.limit

    # The audit trail for the substitution: every key that reached this rule, and
    # the governing instance's un-overridden limit for display (golden rule 4 — an
    # architect's value must not look like it came from the bye-law).
    override_value_keys = tuple(
        sorted({applied.key for _i, _o, applied in matched if applied is not None})
    )
    original_limit: Any = None
    if governing_applied is not None:
        original_limit = run_check(rule.check, governing_instance, env).limit

    offenders: list[str] = []
    for instance, outcome, _applied in matched:
        if outcome.satisfied:
            continue
        ids = (
            outcome.elements
            if outcome.elements is not None
            else ((instance.element_id,) if instance.element_id else ())
        )
        for element_id in ids:
            if element_id and element_id not in offenders:
                offenders.append(element_id)

    satisfaction: Fraction | None = None
    if rule.scoring:
        # Arithmetic mean over matched targets, exact. Several toilets share one
        # rule and one score contribution.
        satisfaction = sum((o.satisfaction for _, o, _a in matched), Fraction(0)) / len(matched)

    result_instances = tuple(
        ResultInstance(
            element_id=instance.element_id,
            label=instance.label,
            status=_instance_status(outcome, effective_severity),
            actual=outcome.actual,
            limit=outcome.limit,
            message=render_message(
                rule.message,
                element=instance.label,
                actual=outcome.actual,
                limit=outcome.limit,
                unit=unit,
                cite=rule.cite_full,
            ),
            satisfaction=outcome.satisfaction if rule.scoring else None,
            note=outcome.note,
        )
        for instance, outcome, _applied in matched
    )

    return RuleResult(
        rule_id=rule.id,
        pack_id=rule.pack_id,
        status=status,
        actual=actual,
        limit=limit,
        unit=unit,
        title=rule.title,
        message=render_message(
            rule.message,
            element=governing_instance.label,
            actual=actual,
            limit=limit,
            unit=unit,
            cite=rule.cite_full,
        ),
        cite=rule.cite_full,
        cite_short=rule.cite,
        fix_hint=rule.fix,
        confidence=rule.confidence,
        severity=effective_severity,
        declared_severity=rule.severity,
        check_type=rule.check.type,
        elements=tuple(offenders),
        cite_url=rule.cite_url,
        autofix=rule.autofix,
        hard=rule.hard,
        weight=rule.weight,
        group=rule.group,
        satisfaction=satisfaction,
        tags=rule.tags,
        note=governing.note,
        overridden=overridden,
        override_reason=override_reason,
        relaxed_to_warn=rule.relaxed_to_warn,
        value_overridden=bool(override_value_keys),
        override_value_keys=override_value_keys,
        original_limit=original_limit,
        instances=result_instances,
    )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _active(
    pack_set: PackSet, vastu_mode: str
) -> tuple[tuple[Rule, ...], dict[str, str | None], list[str]]:
    """Drop scoring packs the brief turned off; collect each pack's severity ceiling.

    ``vastuMode: off`` means the Vastu pack "is not loaded at all" — so its rules
    produce no rows, not nine ``not_applicable`` ones. A wall of grey Vastu rows on
    a project that opted out is noise, and the pack's own README says it plainly.
    """
    ceilings: dict[str, str | None] = {}
    dropped: list[str] = []
    warnings: list[str] = []
    for pack_id in pack_set.load_order:
        pack = pack_set.packs[pack_id]
        if pack.scoring is None:
            ceilings[pack_id] = None
            continue
        mode = mode_for(pack, vastu_mode)
        if mode is None:
            dropped.append(pack_id)
            if vastu_mode != "off":
                warnings.append(
                    "Pack %s declares no %r mode, so it was not evaluated." % (pack_id, vastu_mode)
                )
            continue
        ceilings[pack_id] = mode.severity_ceiling
    rules = tuple(rule for rule in pack_set.rules if rule.pack_id not in dropped)
    return rules, ceilings, warnings


def evaluate(
    context: Any,
    *,
    packs: Any = None,
    root: str | None = None,
) -> EvaluationReport:
    """Run every applicable rule against one design.

    ``context`` is an :class:`~garh_rules.context.EvaluationContext` or its JSON form
    (``rulepacks/schema/fixture.schema.json`` -> ``$defs.evaluationContext``).

    ``packs`` may be a pre-resolved :class:`~garh_rules.packs.PackSet` (what the
    solver critic passes, so 5 000 candidate scorings share one load), a list of
    pack ids, or ``None`` to use ``context.packs``.
    """
    ctx = EvaluationContext.coerce(context)
    if isinstance(packs, PackSet):
        pack_set = packs
    else:
        pack_set = load_pack_set(tuple(packs) if packs else ctx.packs, root=root)

    rules, ceilings, warnings = _active(pack_set, ctx.vastu_mode)
    env = CheckEnv(context=ctx, vocabulary=pack_set.vocabulary)
    project_fields = bind_project_fields(ctx)

    results = tuple(_evaluate_rule(rule, env, project_fields, ceilings) for rule in rules)

    counts: dict[str, int] = {PASS: 0, WARN: 0, FAIL: 0, NOT_APPLICABLE: 0, "overridden": 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
        if result.overridden:
            counts["overridden"] += 1

    scores: list[VastuScore] = []
    for pack in pack_set.scoring_packs():
        if pack.id not in ceilings:
            continue  # dropped: the brief's vastuMode has no matching scoring mode
        contributions = [
            RuleScore(
                rule_id=result.rule_id,
                title=result.title,
                group=result.group or "",
                weight=result.weight or 0,
                satisfaction=result.satisfaction
                if result.satisfaction is not None
                else Fraction(0),
                status=result.status,
                hard=result.hard,
            )
            for result in results
            if result.pack_id == pack.id and result.applicable
        ]
        scores.append(build_score(pack, ctx.vastu_mode, contributions))

    # Honesty about the value-override map: a key nothing consumed is either off
    # the engine's substitution vocabulary or banded onto a rule this plot never
    # triggered — both are facts the architect's panel should be able to state.
    declared_value_keys = frozenset(ctx.profile.value_overrides)
    if declared_value_keys:
        consumed_value_keys = frozenset(
            key for result in results for key in result.override_value_keys
        )
        unused_value_keys = sorted(declared_value_keys - consumed_value_keys)
        if unused_value_keys:
            warnings.append(
                "Value override(s) %s did not reach any evaluated rule in this run — either "
                "no loaded rule of that kind applied to this plot, or the key is not one the "
                "engine substitutes (see garh_rules.checks.VALUE_OVERRIDE_KEYS)."
                % ", ".join(unused_value_keys)
            )

    unclassified = ctx.unclassified_room_types(sorted(pack_set.room_types))
    if unclassified:
        warnings.append(
            "Room type(s) %s are outside the packs' vocabulary, so no room rule selected them and "
            "they were not checked. Map them in garh_rules.context.ROOM_TYPE_ALIASES if they are a "
            "spelling drift rather than genuinely unclassified space." % ", ".join(unclassified)
        )

    areas = build_area_statement(ctx, pack_set, results)

    return EvaluationReport(
        results=results,
        packs=pack_set.load_order,
        pack_versions=pack_set.pack_versions,
        counts=counts,
        areas=areas,
        scores=tuple(scores),
        warnings=tuple(warnings) + pack_set.notes + areas.warnings,
        disclaimers=pack_set.disclaimers(),
    )


def evaluate_parts(
    model: Any,
    plot: Any,
    profile: Any,
    packs: Sequence[str],
    *,
    vastu_mode: str = "off",
    root: str | None = None,
    pack_set: PackSet | None = None,
) -> EvaluationReport:
    """§6's literal signature: ``(model, plot, profile, packs) -> results``.

    A thin adapter over :func:`evaluate`; the four parts are assembled into the one
    context shape that the fixtures also use, so there is a single input contract.
    """
    from .context import context_from_parts

    ctx = context_from_parts(
        packs=packs, plot=plot, profile=profile, model=model, vastu_mode=vastu_mode
    )
    return evaluate(ctx, packs=pack_set if pack_set is not None else list(packs), root=root)
