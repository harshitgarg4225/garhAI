"""The area statement (§7) — derived from the rule results, never recomputed.

§7 is explicit about why this lives here and not in the drawings engine: "area
statement per municipal format: plot area, per-storey built-up, total, FAR
achieved vs allowed, coverage achieved vs allowed, setbacks provided vs required
(**from rules results — same numbers, one source**)".

That sentence is a correctness requirement, not a style note. If the sheet's FAR
row were computed from the model and the compliance chip from the packs, they
would eventually disagree by one rounding step — and a submission drawing whose
area statement contradicts its own compliance annexure is a rejected drawing. So:

* every *allowance* (FAR, coverage, height, floors) is read out of the applicable
  rule results, taking the **strictest** when several bands apply;
* every *requirement* (setbacks, parking) takes the **maximum**, because minimums
  stack — city front-setback tables are indexed by plot size *and* road width, and
  the packs encode them as independent families whose maximum is the real
  requirement;
* an allowance with no applicable rule is ``None``, which renders as "not
  regulated by the loaded packs" — never as unlimited, and never as zero.

:func:`build_area_statement` takes the results the engine already produced.
:func:`area_statement` is the one-call form for the drawings engine and the UI, and
runs the same evaluation underneath, so there is exactly one code path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .context import EvaluationContext
from .formatting import format_area_mm2, format_count, format_length_mm, format_ratio, storey_label
from .packs import PackSet
from .results import NOT_APPLICABLE, RuleResult
from .scope import edge_element_id

__all__ = [
    "AreaStatement",
    "StoreyAreaRow",
    "SetbackRow",
    "AreaRow",
    "build_area_statement",
    "area_statement",
]


@dataclass(frozen=True)
class StoreyAreaRow:
    storey_id: str
    index: int
    label: str
    built_up_area_mm2: int | None

    def to_json(self) -> dict[str, Any]:
        return {
            "storeyId": self.storey_id,
            "index": self.index,
            "label": self.label,
            "builtUpAreaMm2": self.built_up_area_mm2,
        }


@dataclass(frozen=True)
class SetbackRow:
    """One edge, provided vs required, with the rules that set the requirement."""

    edge_index: int
    role: str
    element_id: str
    provided_mm: int
    required_mm: int | None
    rule_ids: tuple[str, ...]

    @property
    def status(self) -> str:
        if self.required_mm is None:
            return "not_regulated"
        return "ok" if self.provided_mm >= self.required_mm else "short"

    @property
    def shortfall_mm(self) -> int:
        if self.required_mm is None:
            return 0
        return max(0, self.required_mm - self.provided_mm)

    def to_json(self) -> dict[str, Any]:
        return {
            "edgeIndex": self.edge_index,
            "role": self.role,
            "elementId": self.element_id,
            "providedMm": self.provided_mm,
            "requiredMm": self.required_mm,
            "shortfallMm": self.shortfall_mm,
            "status": self.status,
            "ruleIds": list(self.rule_ids),
        }


@dataclass(frozen=True)
class AreaRow:
    """One printable line. The sheet engine formats; this carries the numbers.

    ``kind`` tells the sheet what to call the second column: a FAR cap is
    *permissible*, a setback is *required*, a plot area is neither. Printing
    "allowed 1.50 m" against a minimum setback is the kind of small wrongness that
    gets a drawing queried at the counter.
    """

    key: str
    label: str
    value: Any
    unit: str
    allowed: Any = None
    kind: str = "informational"  # allowance | requirement | informational
    note: str | None = None
    rule_ids: tuple[str, ...] = ()

    @property
    def limit_label(self) -> str:
        if self.kind == "allowance":
            return "Permissible"
        if self.kind == "requirement":
            return "Required"
        return ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "value": self.value,
            "unit": self.unit,
            "kind": self.kind,
        }
        if self.allowed is not None:
            out["allowed"] = self.allowed
            out["limitLabel"] = self.limit_label
        if self.note:
            out["note"] = self.note
        if self.rule_ids:
            out["ruleIds"] = list(self.rule_ids)
        return out


@dataclass(frozen=True)
class AreaStatement:
    """Every number the municipal area statement needs, in integer mm / mm2."""

    plot_area_mm2: int
    footprint_area_mm2: int
    coverage_allowed_mm2: int | None
    total_built_up_area_mm2: int
    far_countable_area_mm2: int
    far_allowed_mm2: int | None
    per_storey: tuple[StoreyAreaRow, ...]
    setbacks: tuple[SetbackRow, ...]
    storey_count: int
    floors_counted: int | None
    floors_allowed: int | None
    building_height_mm: int
    height_counted_mm: int | None
    height_allowed_mm: int | None
    parking_provided: int
    parking_required: int | None
    rule_ids: Mapping[str, tuple[str, ...]]
    warnings: tuple[str, ...] = ()

    # -- exact achieved ratios --------------------------------------------
    @property
    def far_achieved(self) -> Fraction:
        return Fraction(self.far_countable_area_mm2, max(1, self.plot_area_mm2))

    @property
    def far_allowed(self) -> Fraction | None:
        if self.far_allowed_mm2 is None:
            return None
        return Fraction(self.far_allowed_mm2, max(1, self.plot_area_mm2))

    @property
    def coverage_achieved(self) -> Fraction:
        return Fraction(self.footprint_area_mm2, max(1, self.plot_area_mm2))

    @property
    def coverage_allowed(self) -> Fraction | None:
        if self.coverage_allowed_mm2 is None:
            return None
        return Fraction(self.coverage_allowed_mm2, max(1, self.plot_area_mm2))

    def rows(self) -> tuple[AreaRow, ...]:
        """The statement as printable rows, in municipal reading order."""
        rows: list[AreaRow] = [
            AreaRow("plot_area", "Plot area", self.plot_area_mm2, "mm2"),
            AreaRow(
                "coverage",
                "Ground coverage",
                self.footprint_area_mm2,
                "mm2",
                allowed=self.coverage_allowed_mm2,
                kind="allowance",
                note=self._ratio_note(self.coverage_achieved, self.coverage_allowed),
                rule_ids=self.rule_ids.get("coverage", ()),
            ),
        ]
        for storey in self.per_storey:
            rows.append(
                AreaRow(
                    "built_up.%s" % storey.storey_id,
                    "%s built-up" % storey.label,
                    storey.built_up_area_mm2,
                    "mm2",
                )
            )
        rows.append(
            AreaRow("built_up_total", "Total built-up", self.total_built_up_area_mm2, "mm2")
        )
        rows.append(
            AreaRow(
                "far",
                "FAR-countable area",
                self.far_countable_area_mm2,
                "mm2",
                allowed=self.far_allowed_mm2,
                kind="allowance",
                note=self._ratio_note(self.far_achieved, self.far_allowed),
                rule_ids=self.rule_ids.get("far", ()),
            )
        )
        for setback in self.setbacks:
            rows.append(
                AreaRow(
                    "setback.%s" % setback.role,
                    "%s setback" % setback.role.replace("-", " ").title(),
                    setback.provided_mm,
                    "mm",
                    allowed=setback.required_mm,
                    kind="requirement",
                    rule_ids=setback.rule_ids,
                )
            )
        rows.append(
            AreaRow(
                "floors",
                "Floors above ground",
                self.floors_counted if self.floors_counted is not None else self.storey_count,
                "count",
                allowed=self.floors_allowed,
                kind="allowance",
                rule_ids=self.rule_ids.get("floors", ()),
            )
        )
        rows.append(
            AreaRow(
                "height",
                "Building height",
                self.height_counted_mm
                if self.height_counted_mm is not None
                else self.building_height_mm,
                "mm",
                allowed=self.height_allowed_mm,
                kind="allowance",
                note=(
                    "Height as counted by the governing rule; excluded components are not "
                    "included."
                    if self.height_counted_mm is not None
                    and self.height_counted_mm != self.building_height_mm
                    else None
                ),
                rule_ids=self.rule_ids.get("height", ()),
            )
        )
        rows.append(
            AreaRow(
                "parking",
                "Car parking spaces",
                self.parking_provided,
                "count",
                allowed=self.parking_required,
                kind="requirement",
                rule_ids=self.rule_ids.get("parking", ()),
            )
        )
        return tuple(rows)

    @staticmethod
    def _ratio_note(achieved: Fraction, allowed: Fraction | None) -> str | None:
        if allowed is None:
            return "%s achieved; not regulated by the loaded packs" % format_ratio(achieved)
        return "%s achieved against %s allowed" % (
            format_ratio(achieved),
            format_ratio(allowed),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "plotAreaMm2": self.plot_area_mm2,
            "footprintAreaMm2": self.footprint_area_mm2,
            "coverageAllowedMm2": self.coverage_allowed_mm2,
            "coverageAchieved": format_ratio(self.coverage_achieved),
            "coverageAllowed": (
                format_ratio(self.coverage_allowed) if self.coverage_allowed is not None else None
            ),
            "totalBuiltUpAreaMm2": self.total_built_up_area_mm2,
            "farCountableAreaMm2": self.far_countable_area_mm2,
            "farAllowedMm2": self.far_allowed_mm2,
            "farAchieved": format_ratio(self.far_achieved),
            "farAllowed": format_ratio(self.far_allowed) if self.far_allowed is not None else None,
            "perStorey": [s.to_json() for s in self.per_storey],
            "setbacks": [s.to_json() for s in self.setbacks],
            "storeyCount": self.storey_count,
            "floorsCounted": self.floors_counted,
            "floorsAllowed": self.floors_allowed,
            "buildingHeightMm": self.building_height_mm,
            "heightCountedMm": self.height_counted_mm,
            "heightAllowedMm": self.height_allowed_mm,
            "parkingProvided": self.parking_provided,
            "parkingRequired": self.parking_required,
            "ruleIds": {k: list(v) for k, v in sorted(self.rule_ids.items())},
            "warnings": list(self.warnings),
            "rows": [r.to_json() for r in self.rows()],
        }

    def text_rows(self) -> tuple[tuple[str, str, str, str], ...]:
        """``(label, value, limit, limitLabel)`` already formatted — what the sheet prints."""
        out: list[tuple[str, str, str, str]] = []
        for row in self.rows():
            if row.unit == "mm2":
                value = format_area_mm2(row.value) if row.value is not None else "-"
                allowed = format_area_mm2(row.allowed) if row.allowed is not None else "-"
            elif row.unit == "mm":
                value = format_length_mm(row.value) if row.value is not None else "-"
                allowed = format_length_mm(row.allowed) if row.allowed is not None else "-"
            else:
                value = format_count(row.value) if row.value is not None else "-"
                allowed = format_count(row.allowed) if row.allowed is not None else "-"
            out.append((row.label, value, allowed, row.limit_label))
        return tuple(out)


# ---------------------------------------------------------------------------
# Building it
# ---------------------------------------------------------------------------


def _strictest(values: Sequence[int]) -> int | None:
    """The binding allowance when several bands apply: the smallest."""
    return min(values) if values else None


def _largest(values: Sequence[int]) -> int | None:
    """The binding requirement when several minima stack: the largest."""
    return max(values) if values else None


def build_area_statement(
    context: EvaluationContext, pack_set: PackSet, results: Sequence[RuleResult]
) -> AreaStatement:
    """Assemble the statement from results the engine already produced."""
    model = context.model
    plot = context.plot

    far_limits: list[int] = []
    coverage_limits: list[int] = []
    height_limits: list[int] = []
    height_counted: list[int] = []
    floor_limits: list[int] = []
    floors_counted: list[int] = []
    parking_required: list[int] = []
    rule_ids: dict[str, list[str]] = {
        "far": [],
        "coverage": [],
        "height": [],
        "floors": [],
        "parking": [],
    }
    setback_required: dict[str, int] = {}
    setback_rules: dict[str, list[str]] = {}

    for result in results:
        if result.status == NOT_APPLICABLE:
            continue
        kind = result.check_type
        if kind == "far_max" and isinstance(result.limit, int):
            far_limits.append(result.limit)
            rule_ids["far"].append(result.rule_id)
        elif kind == "coverage_max" and isinstance(result.limit, int):
            coverage_limits.append(result.limit)
            rule_ids["coverage"].append(result.rule_id)
        elif kind == "height_max" and isinstance(result.limit, int):
            height_limits.append(result.limit)
            if isinstance(result.actual, int):
                height_counted.append(result.actual)
            rule_ids["height"].append(result.rule_id)
        elif kind == "floors_max" and isinstance(result.limit, int):
            floor_limits.append(result.limit)
            if isinstance(result.actual, int):
                floors_counted.append(result.actual)
            rule_ids["floors"].append(result.rule_id)
        elif kind == "parking_min" and isinstance(result.limit, int):
            parking_required.append(result.limit)
            rule_ids["parking"].append(result.rule_id)
        elif kind == "setback_min":
            # Per-edge, from the instance rows: one rule can cover two edges
            # ("sides"), and each edge keeps the largest requirement across rules —
            # minimums stack, and the plot-size and road-width families are separate
            # rules whose maximum is the real requirement.
            for instance in result.instances:
                if instance.element_id is None or not isinstance(instance.limit, int):
                    continue
                key = instance.element_id
                previous = setback_required.get(key)
                setback_required[key] = (
                    instance.limit if previous is None else max(previous, instance.limit)
                )
                setback_rules.setdefault(key, []).append(result.rule_id)

    setbacks: list[SetbackRow] = []
    for edge in plot.edges:
        # Same id the compliance chip uses, from the same function — a setback row and
        # its chip must never disagree about which edge they mean.
        element_id = edge_element_id(edge, plot.edges)
        setbacks.append(
            SetbackRow(
                edge_index=edge.index,
                role=edge.role,
                element_id=element_id,
                provided_mm=edge.setback_provided_mm,
                required_mm=setback_required.get(element_id),
                rule_ids=tuple(sorted(set(setback_rules.get(element_id, ())))),
            )
        )

    per_storey = tuple(
        StoreyAreaRow(
            storey_id=storey.id,
            index=storey.index,
            label=storey_label(storey),
            built_up_area_mm2=storey.built_up_area_mm2,
        )
        for storey in sorted(model.storeys, key=lambda s: s.index)
    )

    warnings: list[str] = []
    known = [s.built_up_area_mm2 for s in per_storey if s.built_up_area_mm2 is not None]
    if len(known) == len(per_storey) and per_storey:
        total = sum(known)
        if total != model.built_up_area_mm2:
            # A statement whose rows do not add up is a rejected drawing. Say it here
            # rather than letting the sheet print it.
            warnings.append(
                "Per-storey built-up areas sum to %d mm2 but model.builtUpAreaMm2 is %d mm2 — the "
                "area statement rows will not add up until the model projection agrees with "
                "itself." % (total, model.built_up_area_mm2)
            )
    elif per_storey:
        warnings.append(
            "%d of %d storeys carry no builtUpAreaMm2, so the per-storey breakdown is incomplete."
            % (len(per_storey) - len(known), len(per_storey))
        )
    if not far_limits:
        warnings.append("No FAR rule applied, so no FAR allowance is stated.")
    if not coverage_limits:
        warnings.append("No coverage rule applied, so no coverage allowance is stated.")

    return AreaStatement(
        plot_area_mm2=plot.area_mm2,
        footprint_area_mm2=model.footprint_area_mm2,
        coverage_allowed_mm2=_strictest(coverage_limits),
        total_built_up_area_mm2=model.built_up_area_mm2,
        far_countable_area_mm2=model.far_countable_area_mm2,
        far_allowed_mm2=_strictest(far_limits),
        per_storey=per_storey,
        setbacks=tuple(setbacks),
        storey_count=model.storey_count,
        floors_counted=max(floors_counted) if floors_counted else None,
        floors_allowed=_strictest(floor_limits),
        building_height_mm=model.building_height_mm,
        height_counted_mm=max(height_counted) if height_counted else None,
        height_allowed_mm=_strictest(height_limits),
        parking_provided=context.profile.parking_spaces_provided,
        parking_required=_largest(parking_required),
        rule_ids={k: tuple(sorted(set(v))) for k, v in rule_ids.items() if v},
        warnings=tuple(warnings),
    )


def area_statement(context: Any, *, packs: Any = None, root: str | None = None) -> AreaStatement:
    """One call the drawings engine and the UI both use.

    Runs the rules and returns only the statement — same evaluation, same numbers,
    no second code path. When you already have a report, read ``report.areas``
    instead of calling this again.
    """
    from .engine import evaluate  # local import: engine imports this module

    return evaluate(context, packs=packs, root=root).areas
