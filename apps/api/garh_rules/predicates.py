"""The closed ``when`` field set, and the six operators. Nothing else exists.

``when`` is an applicability gate, not a language. Six operators
(``lt lte gt gte eq in``), AND only, no OR, no NOT, no nesting — because a
predicate language with boolean nesting stops being reviewable by an architect,
and reviewability is the entire point of keeping regulations as data.

Two rules carry all the subtlety:

**Nulls are false.** A numeric operator applied to a null field — ``roadWidthMm``
on a plot whose road is not set yet — yields ``False``, so the rule becomes
``not_applicable``. It never silently passes. This is why the *absence* of a road
makes the FAR-by-road-width family drop out instead of picking the most generous
band.

**``plotAreaSqm`` is scaled, not rounded.** Bye-law tables are banded in whole
square metres, so ``{"lte": 360}`` means ``plot.areaMm2 <= 360_000_000`` exactly.
A 360.4 m2 plot correctly falls *outside* the band; nothing is rounded to make it
fit.

:data:`BOUND_WHEN_FIELDS` is cross-checked against the schema at pack load
(:meth:`garh_rules.packs.PackLoader._check_engine_covers_schema`), so a field
added to ``rulepack.schema.json`` without a binding here is a loud load error.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .context import EvaluationContext
from .errors import EvaluationError

__all__ = [
    "OPERATORS",
    "BOUND_WHEN_FIELDS",
    "PROJECT_WHEN_FIELDS",
    "SCOPE_WHEN_FIELDS",
    "FIELD_SCALE",
    "NUMERIC_OPERATORS",
    "bind_project_fields",
    "when_matches",
    "predicate_matches",
]

#: The six, and only the six.
OPERATORS: frozenset[str] = frozenset({"lt", "lte", "gt", "gte", "eq", "in"})

NUMERIC_OPERATORS: frozenset[str] = frozenset({"lt", "lte", "gt", "gte"})

#: Fields bound for every rule, from the plot / profile / model summary.
PROJECT_WHEN_FIELDS: tuple[str, ...] = (
    "cityPack",
    "zoneCategory",
    "buildingUse",
    "plotAreaSqm",
    "plotAreaMm2",
    "plotFrontageMm",
    "plotDepthMm",
    "roadWidthMm",
    "cornerPlot",
    "abuttingRoadCount",
    "storeys",
    "hasStilt",
    "hasBasement",
    "buildingHeightMm",
    "builtUpAreaMm2",
    "farCountableAreaMm2",
    "dwellingUnits",
    "vastuMode",
)

#: Fields bound only by the scope the check declares. Outside that scope they are
#: absent, hence null, hence every predicate on them is false.
SCOPE_WHEN_FIELDS: Mapping[str, tuple[str, ...]] = {
    "edge": ("edgeRoadWidthMm",),
    "storey": ("storeyIndex",),
    "room": ("storeyIndex", "roomType", "roomIsHabitable", "roomIsInternal"),
    "opening": ("storeyIndex", "openingKind", "openingRole"),
    "stair": ("storeyIndex",),
    "projection": ("storeyIndex",),
    "zone": (),
    "project": (),
}

BOUND_WHEN_FIELDS: frozenset[str] = frozenset(PROJECT_WHEN_FIELDS) | frozenset(
    name for names in SCOPE_WHEN_FIELDS.values() for name in names
)

#: Pack-side threshold unit -> context unit. Only one field needs it, and it is the
#: one every bye-law table is written in.
FIELD_SCALE: Mapping[str, int] = {"plotAreaSqm": 1_000_000}


def bind_project_fields(context: EvaluationContext) -> dict[str, Any]:
    """The project-level half of the predicate environment. Built once per run."""
    plot = context.plot
    profile = context.profile
    model = context.model
    return {
        "cityPack": profile.city_pack,
        "zoneCategory": profile.zone_category,
        "buildingUse": profile.building_use,
        # plotAreaSqm shares the mm2 value; FIELD_SCALE lifts the pack's whole-m2
        # threshold instead of dividing the plot, so nothing is ever rounded.
        "plotAreaSqm": plot.area_mm2,
        "plotAreaMm2": plot.area_mm2,
        "plotFrontageMm": plot.frontage_mm,
        "plotDepthMm": plot.depth_mm,
        "roadWidthMm": plot.front_road_width_mm(),
        "cornerPlot": plot.corner_plot,
        "abuttingRoadCount": plot.abutting_road_count(),
        "storeys": model.storey_count,
        "hasStilt": model.has_stilt,
        "hasBasement": model.has_basement,
        "buildingHeightMm": model.building_height_mm,
        "builtUpAreaMm2": model.built_up_area_mm2,
        "farCountableAreaMm2": model.far_countable_area_mm2,
        "dwellingUnits": profile.dwelling_units,
        "vastuMode": context.vastu_mode,
    }


def _scaled(field_name: str, threshold: Any) -> Any:
    scale = FIELD_SCALE.get(field_name)
    if scale is None or isinstance(threshold, bool) or not isinstance(threshold, int):
        return threshold
    return threshold * scale


def _numeric(value: Any, field_name: str, operator: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvaluationError(
            "operator %r cannot apply to the non-numeric field %r (value %r) — the schema's "
            "per-field predicate types should have rejected this at load"
            % (operator, field_name, value)
        )
    return value


def predicate_matches(field_name: str, predicate: Mapping[str, Any], value: Any) -> bool:
    """Evaluate one field's predicate. Several operators on one field are ANDed."""
    if value is None:
        # Null is false for every operator, including `eq`. A rule banded on a
        # value the project has not set yet is not applicable, not satisfied.
        return False
    for operator, raw in predicate.items():
        threshold = _scaled(field_name, raw)
        if operator == "eq":
            if value != threshold:
                return False
        elif operator == "in":
            options = [_scaled(field_name, item) for item in raw]
            if value not in options:
                return False
        elif operator in NUMERIC_OPERATORS:
            left = _numeric(value, field_name, operator)
            right = _numeric(threshold, field_name, operator)
            if operator == "lt" and not left < right:
                return False
            if operator == "lte" and not left <= right:
                return False
            if operator == "gt" and not left > right:
                return False
            if operator == "gte" and not left >= right:
                return False
        else:  # pragma: no cover - rejected at pack load
            raise EvaluationError("unknown operator %r on field %r" % (operator, field_name))
    return True


def when_matches(
    when: Mapping[str, Mapping[str, Any]], fields: Mapping[str, Any]
) -> tuple[bool, str | None]:
    """``(matched, first_failing_field)``.

    The failing field name is not decoration: it is what makes a
    ``not_applicable`` row explainable ("this FAR band did not apply because the
    road width is not set"), which is the difference between a compliance panel a
    architect trusts and one they ignore.
    """
    for field_name, predicate in when.items():
        if not predicate_matches(field_name, predicate, fields.get(field_name)):
            return (False, field_name)
    return (True, None)
