"""The result row — the engine's whole output surface.

Playbook §6 fixes the shape: ``{ruleId, status, actual, limit, cite, fixHint,
elements[], confidence}``. Everything beyond that is additive and exists for a
named consumer:

* ``instances[]`` — the per-element breakdown. The contract row is **one per
  rule** (``x-garh-check-meta``: several instances of a scope collapse, worst
  status wins, ``elements[]`` lists every offender), and the fixtures assert
  exactly that row. But a chip has to say "Bedroom 2 is 8.9 m2", not "one of your
  rooms is too small", so each matched element also carries its own status,
  numbers and sentence. The UI renders ``instances``; the compliance annexure and
  the fixtures read the collapsed row.
* ``unit`` — the ``resultUnit`` from the check-semantics table, so a client can
  format ``actual``/``limit`` without a lookup table of its own.
* ``severity`` vs ``declaredSeverity`` — what the row reports after a mode ceiling
  or a ``relax-to-warn`` override, and what the pack declared. Both, because
  "why is this a warning?" is a question an architect will ask.
* ``overridden`` / ``overrideReason`` — §13. An overridden rule still evaluates
  and still reports its real status; suppressing it would hide it from the
  drawing set's compliance annexure.
* ``notApplicableReason`` / ``notApplicableField`` — the difference between "this
  rule does not apply to your plot" and "we could not tell", which is the
  difference between a panel that gets trusted and one that gets ignored.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from .packs import AutoFix

__all__ = [
    "STATUSES",
    "PASS",
    "WARN",
    "FAIL",
    "NOT_APPLICABLE",
    "ResultInstance",
    "RuleResult",
    "worst_status",
]

PASS = "pass"
WARN = "warn"
FAIL = "fail"
NOT_APPLICABLE = "not_applicable"

#: Ordered worst-first. Used for the "worst status wins" collapse and for sorting
#: a chip strip so the red ones are on the left.
STATUSES: tuple[str, ...] = (FAIL, WARN, PASS, NOT_APPLICABLE)

_SEVERITY_OF_STATUS: Mapping[str, int] = {FAIL: 3, WARN: 2, PASS: 1, NOT_APPLICABLE: 0}


def worst_status(statuses: Sequence[str]) -> str:
    """The worst of several statuses. ``not_applicable`` only wins when it is alone."""
    if not statuses:
        return NOT_APPLICABLE
    return max(statuses, key=lambda s: _SEVERITY_OF_STATUS.get(s, 0))


def _ratio_json(value: Fraction | None) -> dict[str, int] | None:
    if value is None:
        return None
    return {"num": value.numerator, "den": value.denominator}


@dataclass(frozen=True)
class ResultInstance:
    """One element measured against one rule."""

    element_id: str | None
    label: str
    status: str
    actual: Any
    limit: Any
    message: str
    satisfaction: Fraction | None = None
    note: str | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "elementId": self.element_id,
            "label": self.label,
            "status": self.status,
            "actual": self.actual,
            "limit": self.limit,
            "message": self.message,
        }
        if self.satisfaction is not None:
            out["satisfaction"] = _ratio_json(self.satisfaction)
        if self.note:
            out["note"] = self.note
        return out


@dataclass(frozen=True)
class RuleResult:
    """One rule's verdict on one design."""

    rule_id: str
    pack_id: str
    status: str
    actual: Any
    limit: Any
    unit: str
    title: str
    message: str
    cite: str  # full citation: citations_base + clause
    cite_short: str  # the clause alone, as written in the pack
    fix_hint: str
    confidence: str
    severity: str
    declared_severity: str
    check_type: str
    elements: tuple[str, ...] = ()
    cite_url: str | None = None
    autofix: AutoFix | None = None
    hard: bool = False
    weight: int | None = None
    group: str | None = None
    satisfaction: Fraction | None = None
    tags: tuple[str, ...] = ()
    note: str | None = None
    overridden: bool = False
    override_reason: str | None = None
    not_applicable_reason: str | None = None
    not_applicable_field: str | None = None
    relaxed_to_warn: bool = False
    #: The architect's value override(s) (``profile.overrides.values``) substituted
    #: into this rule's limit at evaluation time (Phase 3 wiring of the Phase-2
    #: deferral). Distinct from ``overridden``: a value override moves the limit and
    #: the rule still blocks on failure; a rule acknowledgement leaves the limit and
    #: un-blocks a logged failure. ``original_limit`` keeps the pack's own number for
    #: display (golden rule 4 — the replaced value must stay visible).
    value_overridden: bool = False
    override_value_keys: tuple[str, ...] = ()
    original_limit: Any = None
    instances: tuple[ResultInstance, ...] = field(default_factory=tuple)

    # -- predicates used by the solver gates and the UI ---------------------
    @property
    def applicable(self) -> bool:
        return self.status != NOT_APPLICABLE

    @property
    def violated(self) -> bool:
        return self.status in (FAIL, WARN)

    @property
    def is_hard_failure(self) -> bool:
        """A ``fail`` row. §5.6: the solver discards an option that has one."""
        return self.status == FAIL

    @property
    def fix_available(self) -> bool:
        """True when the pack supplies a *computable* auto-fix — the "Fix it" button
        only appears when pressing it can actually do something (§15)."""
        return self.autofix is not None and self.autofix.computable

    def to_json(self, *, full: bool = False) -> dict[str, Any]:
        """The wire/persistence form.

        ``instances`` is emitted for **violated** rules only (or everything with
        ``full=True``). A passing rule's twelve green per-room rows carry nothing a
        client acts on, and ``compliance_reports.results`` is a jsonb column that a
        118-rule pack set would otherwise fill with them. The per-edge setback detail
        the area statement needs survives regardless — it is serialised in
        ``areas.setbacks``.
        """
        out: dict[str, Any] = {
            "ruleId": self.rule_id,
            "packId": self.pack_id,
            "status": self.status,
            "actual": self.actual,
            "limit": self.limit,
            "unit": self.unit,
            "title": self.title,
            "message": self.message,
            "cite": self.cite,
            "citeShort": self.cite_short,
            "fixHint": self.fix_hint,
            "elements": list(self.elements),
            "confidence": self.confidence,
            "severity": self.severity,
            "declaredSeverity": self.declared_severity,
            "checkType": self.check_type,
            "hard": self.hard,
            "fixAvailable": self.fix_available,
        }
        if self.cite_url:
            out["citeUrl"] = self.cite_url
        if self.autofix is not None:
            out["autofix"] = self.autofix.to_json()
        if self.weight is not None:
            out["weight"] = self.weight
        if self.group is not None:
            out["group"] = self.group
        if self.satisfaction is not None:
            out["satisfaction"] = _ratio_json(self.satisfaction)
        if self.tags:
            out["tags"] = list(self.tags)
        if self.note:
            out["note"] = self.note
        if self.overridden or self.value_overridden:
            # One display flag for "an architect touched this rule" (chip styling);
            # the two kinds keep their own fields below because their semantics
            # differ (see the dataclass field comment).
            out["overridden"] = True
        if self.overridden:
            out["overrideReason"] = self.override_reason
        if self.value_overridden:
            out["valueOverridden"] = True
            out["overrideValueKeys"] = list(self.override_value_keys)
            out["originalLimit"] = self.original_limit
        if self.relaxed_to_warn:
            out["relaxedToWarn"] = True
        if self.not_applicable_reason:
            out["notApplicableReason"] = self.not_applicable_reason
            if self.not_applicable_field:
                out["notApplicableField"] = self.not_applicable_field
        if self.instances and (full or self.violated):
            out["instances"] = [i.to_json() for i in self.instances]
        return out
