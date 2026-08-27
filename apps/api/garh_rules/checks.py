"""The 18 check types. One small pure function each, and nothing else in here.

The tiebreaker for every line of this module is the check-semantics table in
``rulepacks/README.md``. Three artefacts state those semantics — that table, the
fixture generator, and this file — and when two disagree the table wins and one of
the other two has a bug. So each function is written to read like its row:

=========================  ===========  ===========================================
type                       scope        actual vs limit
=========================  ===========  ===========================================
``setback_min``            edge         provided setback >= ``valueMm``
``far_max``                project       ``farCountableAreaMm2`` <= ``floor(ratio * plotArea)``
``coverage_max``           project       ``footprintAreaMm2`` <= ``floor(ratio * plotArea)``
``height_max``             project       ``buildingHeight - excluded`` <= ``valueMm``
``floors_max``             project       ``storeyCount + counted extras`` <= ``value``
``room_area_min``          room          ``areaMm2`` >= ``valueMm2``
``room_width_min``         room          ``leastWidthMm`` >= ``valueMm``
``ceiling_height_min``     room          ``clearCeilingHeightMm`` >= ``valueMm``
``ventilation_ratio_min``  room          openable >= ``max(ceil(ratio * area), minAreaMm2)``
``stair_riser_max``        stair         ``riserMm`` <= ``valueMm``
``stair_tread_min``        stair         ``treadMm`` >= ``valueMm``
``stair_width_min``        stair         ``widthMm`` >= ``valueMm``
``headroom_min``           stair         ``headroomMm`` >= ``valueMm``
``projection_max``         projection    ``projectionMm`` <= ``valueMm``
``parking_min``            project       provided >= ``max(ceil(rate * basis), minSpaces)``
``opening_width_min``      opening       ``widthMm`` >= ``valueMm``
``zone_check``             zone          target's zone/facing in ``allow`` / not in ``deny``
``custom``                 per fn        see :mod:`garh_rules.customfns`
=========================  ===========  ===========================================

``opening_width_min`` is the 18th, an addition to playbook §6's list of 17: §6
seeds door minima (main 900 / internal 800 / bath 750) that none of the 17 could
express, and routing something as ordinary as a door width through ``custom``
would have hidden it from the schema.

Rounding always moves the boundary *against* the design — ``floor`` on an
allowance, ``ceil`` on a requirement — so a value exactly on a limit passes and
nothing slips through on a rounding artefact.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .context import (
    OpeningSummary,
    PlotEdge,
    ProjectionSummary,
    RoomSummary,
    ServiceElementSummary,
    StairSummary,
)
from .customfns import custom_result_unit, run_custom
from .errors import ContextError, EvaluationError
from .packs import DEFAULT_COUNT_KINDS, Check
from .ratio import Ratio
from .scope import CheckEnv, Instance, Outcome
from .zones import facing_of

__all__ = [
    "CHECK_TYPES",
    "CHECK_SCOPES",
    "RESULT_UNITS",
    "UNION_ACTUAL_CHECKS",
    "VALUE_OVERRIDE_KEYS",
    "EDGE_ROLE_TO_OVERRIDE_KEY",
    "AppliedValueOverride",
    "scope_of",
    "result_unit_of",
    "run_check",
    "substitute_value_override",
    "union_actual",
    "zone_limit_of",
]

CheckFn = Callable[[Check, Instance, CheckEnv], Outcome]


# ---------------------------------------------------------------------------
# Plot / project scope
# ---------------------------------------------------------------------------


def check_setback_min(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    """Minimum open space between a plot line and the building line.

    ``measure: to-projection`` measures to the outermost balcony/chajja on that
    edge instead of the wall face, so the clear distance is the provided setback
    less the deepest projection on it. It is not clamped at zero: a projection that
    reaches past the plot line should report a negative clear distance, because
    that is what it is.
    """
    edge: PlotEdge = instance.require("edge")
    limit = check.int_param("valueMm")
    provided = edge.setback_provided_mm
    note: str | None = None
    if check.str_param("measure", "to-building-line") == "to-projection":
        deepest = 0
        for projection in env.context.model.projections:
            if projection.edge_role == edge.role:
                deepest = max(deepest, projection.projection_mm)
        if deepest:
            provided = provided - deepest
            note = (
                "Measured to the outermost projection (%d mm beyond the building line)." % deepest
            )
    return Outcome.at_least(provided, limit, note=note)


def check_far_max(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    """FAR / FSI cap. ``premium`` is surfaced as a note and never added to the limit —
    buying premium FAR is the architect's decision, not the engine's."""
    ratio = check.ratio_param("ratio")
    limit = ratio.floor_of(env.context.plot.area_mm2)
    premium = check.params.get("premium")
    note: str | None = None
    if isinstance(premium, Mapping):
        premium_ratio = Ratio.from_json(premium["ratio"], "check.premium.ratio")
        note = "Premium FAR of %s may be available (%s). Not applied automatically." % (
            premium_ratio,
            premium.get("note", ""),
        )
    return Outcome.at_most(env.context.model.far_countable_area_mm2, limit, note=note)


def check_coverage_max(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    """Maximum ground coverage. ``footprintAreaMm2`` already includes whatever the
    pack's ``vocabulary.coverageInclusions`` names — that addition is the model
    layer's job, because which projections count is a bye-law question."""
    ratio = check.ratio_param("ratio")
    limit = ratio.floor_of(env.context.plot.area_mm2)
    return Outcome.at_most(env.context.model.footprint_area_mm2, limit)


def check_height_max(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    """Height cap, less the components this bye-law excludes.

    A named component the building does not have subtracts nothing — a house with
    no lift machine room does not get a free 2 m.
    """
    limit = check.int_param("valueMm")
    components = env.context.model.height_components_mm
    excluded = sum(components.get(name, 0) for name in check.list_param("excludes"))
    return Outcome.at_most(env.context.model.building_height_mm - excluded, limit)


def check_floors_max(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    """Floor count. ``value: 3`` means G+2.

    ``counts`` names extra levels that count toward the limit; an omitted/empty list
    means stilt and basement are free. The loader rejects entries the context cannot
    supply, so this can never under-count silently.
    """
    limit = check.int_param("value")
    counted = check.list_param("counts")
    model = env.context.model
    extras = 0
    if "stilt" in counted and model.has_stilt:
        extras += 1
    if "basement" in counted and model.has_basement:
        extras += 1
    return Outcome.at_most(model.storey_count + extras, limit)


def check_parking_min(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    """``required = max(ceil(rate * basis), minSpaces)``.

    ``basis: built-up-area`` makes the rate spaces per mm2 — "2 ECS per 100 m2" is
    ``{num: 2, den: 100000000}``. Integer cross-multiplication throughout, so the
    familiar rate never becomes 1.9999 spaces.
    """
    rate = check.ratio_param("rate")
    basis = check.str_param("basis")
    if basis == "dwelling":
        quantity = env.context.profile.dwelling_units
    elif basis == "built-up-area":
        quantity = env.context.model.built_up_area_mm2
    else:  # pragma: no cover - rejected at pack load
        raise EvaluationError("unknown parking basis %r" % basis)
    required = max(rate.ceil_of(quantity), check.opt_int_param("minSpaces", 0))
    return Outcome.at_least(env.context.profile.parking_spaces_provided, required)


# ---------------------------------------------------------------------------
# Room scope
# ---------------------------------------------------------------------------


def check_room_area_min(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    room: RoomSummary = instance.require("room")
    return Outcome.at_least(room.area_mm2, check.int_param("valueMm2"))


def check_room_width_min(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    """Least width = the shorter side of the room's bounding box (schema's definition;
    MVP is orthogonal-only). Pre-derived by the model layer, verified against the
    polygon by the fixture suite."""
    room: RoomSummary = instance.require("room")
    return Outcome.at_least(room.least_width_mm, check.int_param("valueMm"))


def check_ceiling_height_min(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    """Clear floor-to-ceiling height, per room — not per storey. A dropped ceiling
    over one wet area is exactly the case this has to catch."""
    room: RoomSummary = instance.require("room")
    return Outcome.at_least(room.clear_ceiling_height_mm, check.int_param("valueMm"))


def check_ventilation_ratio_min(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    """``required = max(ceil(ratio * roomArea), minAreaMm2)`` — the larger of the two.

    That is how NBC states a bathroom: a fraction of the floor area, floored by an
    absolute minimum. With only one parameter present the other contributes 0.

    ``countKinds`` is an instruction to the *model* layer (which opening kinds it
    summed into ``ventilationOpeningAreaMm2``), not something the engine can
    re-derive — the loader rejects any pack asking for a different set.
    """
    room: RoomSummary = instance.require("room")
    ratio = check.opt_ratio_param("ratio")
    from_ratio = ratio.ceil_of(room.area_mm2) if ratio is not None else 0
    required = max(from_ratio, check.opt_int_param("minAreaMm2", 0))
    kinds = check.list_param("countKinds", DEFAULT_COUNT_KINDS)
    return Outcome.at_least(
        room.ventilation_opening_area_mm2,
        required,
        note="Counting %s." % " + ".join(kinds) if kinds != DEFAULT_COUNT_KINDS else None,
    )


# ---------------------------------------------------------------------------
# Stair scope
# ---------------------------------------------------------------------------


def check_stair_riser_max(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    stair: StairSummary = instance.require("stair")
    return Outcome.at_most(stair.riser_mm, check.int_param("valueMm"))


def check_stair_tread_min(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    stair: StairSummary = instance.require("stair")
    return Outcome.at_least(stair.tread_mm, check.int_param("valueMm"))


def check_stair_width_min(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    stair: StairSummary = instance.require("stair")
    return Outcome.at_least(stair.width_mm, check.int_param("valueMm"))


def check_headroom_min(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    stair: StairSummary = instance.require("stair")
    return Outcome.at_least(stair.headroom_mm, check.int_param("valueMm"))


# ---------------------------------------------------------------------------
# Projection / opening scope
# ---------------------------------------------------------------------------


def check_projection_max(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    """Only matching projections reach here — :func:`garh_rules.scope.instances_for`
    filters on ``element`` and ``intoSetbackOnly`` so a non-matching projection never
    becomes a passing instance."""
    projection: ProjectionSummary = instance.require("projection")
    return Outcome.at_most(projection.projection_mm, check.int_param("valueMm"))


def check_opening_width_min(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    opening: OpeningSummary = instance.require("opening")
    return Outcome.at_least(opening.width_mm, check.int_param("valueMm"))


# ---------------------------------------------------------------------------
# Zone scope (Vastu)
# ---------------------------------------------------------------------------


def zone_limit_of(check: Check) -> dict[str, Any]:
    """The ``limit`` object for a zone rule: only the keys the pack actually set."""
    limit: dict[str, Any] = {}
    for key in ("allow", "deny", "fallback"):
        if check.params.get(key) is not None:
            limit[key] = check.params[key]
    return limit


def _zone_label(check: Check, instance: Instance, env: CheckEnv) -> str:
    mode = check.str_param("mode")
    payload = instance.payload
    if mode == "facing":
        opening: OpeningSummary = instance.require("opening")
        if opening.outward_normal_deg is None:
            raise ContextError(
                "opening %s has no outwardNormalDeg, so its facing cannot be classified. A Vastu "
                "facing rule must not be reported as satisfied on a missing direction."
                % opening.id,
                field="model.openings.outwardNormalDeg",
            )
        return facing_of(opening.outward_normal_deg, env.context.plot.north_deg)

    centroid: tuple[int, int] | None = None
    if isinstance(payload, RoomSummary | StairSummary | OpeningSummary | ServiceElementSummary):
        centroid = payload.centroid_mm
    if centroid is None:
        raise ContextError(
            "%s has no centroidMm, so its 3x3 zone cannot be classified. A Vastu zone rule must "
            "not be reported as satisfied on a missing centroid." % (instance.element_id,),
            field="centroidMm",
        )
    return env.grid.zone_of(centroid)


def check_zone_check(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    """Directional placement, per target.

    Precedence is ``deny`` first, then ``allow``, then ``fallback``:

    * in ``deny`` -> violated, satisfaction 0, whatever ``allow`` says. ``deny`` is
      the absolute form (Vastu's never-NE toilet);
    * in ``allow`` -> pass, satisfaction 1;
    * in ``fallback.allow`` -> ``warn`` with a fractional satisfaction. Reported at
      ``warn`` even for a ``fail``-severity rule: a fallback is "acceptable but not
      ideal", not a breach;
    * a rule with **no** ``allow`` (deny-only) passes anything outside ``deny``;
    * otherwise -> violated, satisfaction 0.
    """
    label = _zone_label(check, instance, env)
    allow = check.list_param("allow")
    deny = check.list_param("deny")
    fallback = check.mapping_param("fallback")
    fallback_allow = tuple(str(z) for z in (fallback.get("allow") or ()))
    limit = zone_limit_of(check)

    if label in deny:
        return Outcome(
            satisfied=False,
            actual=label,
            limit=limit,
            order_key=Fraction(-1),
            satisfaction=Fraction(0),
        )
    if allow and label in allow:
        return Outcome(
            satisfied=True,
            actual=label,
            limit=limit,
            order_key=Fraction(0),
            satisfaction=Fraction(1),
        )
    if fallback_allow and label in fallback_allow:
        ratio = Ratio.from_json(fallback["scoreRatio"], "check.fallback.scoreRatio")
        satisfaction = ratio.as_fraction()
        return Outcome(
            satisfied=False,
            actual=label,
            limit=limit,
            order_key=satisfaction - 1,
            satisfaction=satisfaction,
            degraded=True,
        )
    if not allow:
        # Deny-only rule: anything outside the forbidden set is compliant.
        return Outcome(
            satisfied=True,
            actual=label,
            limit=limit,
            order_key=Fraction(0),
            satisfaction=Fraction(1),
        )
    return Outcome(
        satisfied=False,
        actual=label,
        limit=limit,
        order_key=Fraction(-1),
        satisfaction=Fraction(0),
    )


def check_custom(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    return run_custom(check, instance, env)


# ---------------------------------------------------------------------------
# Value-override substitution (Phase 3 — the deferred half of the Phase-2 fix)
# ---------------------------------------------------------------------------
#
# ``profile.overrides["values"]`` (``garh_rules.context.VALUE_OVERRIDES_KEY``) is the
# plot panel's flat integer map of the architect's value overrides. Phase 2 parsed,
# audited and round-tripped it; DECISIONS.md (2026-08-05) deferred *substituting* the
# values into check limits to Phase 3. This is that substitution.
#
# The key vocabulary mirrors ``REG_VALUE_KEYS`` in
# ``apps/web/src/features/plot/rules.ts`` — the panel is the only writer, so its keys
# are the contract. Ratios are stored ×100 (``farX100: 175`` = FAR 1.75,
# ``coveragePct: 65`` = 65%), which keeps the override map integer-only, matching the
# geometry discipline that ``ProfileSummary.from_json`` already enforces.
#
# Two properties are deliberate and tested:
#
# * **An override changes the limit, never the verdict machinery.** A design that
#   fails against the architect's own value still FAILS, still blocks the §5.6
#   solver gate, and still shows in the report. Value overrides are not rule
#   acknowledgements (those are the ``{ruleId: {reason}}`` siblings) and never
#   silence a check.
# * **The original limit stays on the row** (``RuleResult.original_limit``), so the
#   UI can show "1.2 m (pack value 1.5 m, overridden)" — golden rule 4: a seeded
#   value the architect replaced must not look like it came from the bye-law.

#: Override key -> the check type whose limit it substitutes into. The three setback
#: keys share ``setback_min`` and are selected per edge instance by its role.
VALUE_OVERRIDE_KEYS: Mapping[str, str] = {
    "setbackFrontMm": "setback_min",
    "setbackRearMm": "setback_min",
    "setbackSideMm": "setback_min",
    "farX100": "far_max",
    "coveragePct": "coverage_max",
    "heightMaxMm": "height_max",
    "floorsMax": "floors_max",
}

#: Plot-edge role -> the setback override key that governs it. ``other`` edges have
#: no override key — the panel exposes front/rear/side only.
EDGE_ROLE_TO_OVERRIDE_KEY: Mapping[str, str] = {
    "front": "setbackFrontMm",
    "rear": "setbackRearMm",
    "side-a": "setbackSideMm",
    "side-b": "setbackSideMm",
}


@dataclass(frozen=True)
class AppliedValueOverride:
    """One substitution that actually happened, for the result row's audit trail."""

    key: str
    value: int


def substitute_value_override(
    check: Check, instance: Instance, value_overrides: Mapping[str, int]
) -> tuple[Check, AppliedValueOverride | None]:
    """Return the check with the architect's value substituted, when one applies.

    Pure and total: an empty override map, an unrelated check type, or an edge role
    with no override key all return the check unchanged with ``None``. The caller
    (``engine._evaluate_rule``) runs this per instance because a ``setback_min`` rule
    with the ``all`` selector meets edges whose roles want different keys.
    """
    if not value_overrides:
        return (check, None)

    if check.type == "setback_min" and instance.kind == "edge":
        edge: PlotEdge = instance.payload
        key = EDGE_ROLE_TO_OVERRIDE_KEY.get(edge.role)
        if key is None or key not in value_overrides:
            return (check, None)
        value = value_overrides[key]
        params = dict(check.params)
        params["valueMm"] = value
        return (Check(type=check.type, params=params), AppliedValueOverride(key, value))

    if check.type == "far_max" and "farX100" in value_overrides:
        value = value_overrides["farX100"]
        params = dict(check.params)
        params["ratio"] = {"num": value, "den": 100}
        return (Check(type=check.type, params=params), AppliedValueOverride("farX100", value))

    if check.type == "coverage_max" and "coveragePct" in value_overrides:
        value = value_overrides["coveragePct"]
        params = dict(check.params)
        params["ratio"] = {"num": value, "den": 100}
        return (Check(type=check.type, params=params), AppliedValueOverride("coveragePct", value))

    if check.type == "height_max" and "heightMaxMm" in value_overrides:
        value = value_overrides["heightMaxMm"]
        params = dict(check.params)
        params["valueMm"] = value
        return (Check(type=check.type, params=params), AppliedValueOverride("heightMaxMm", value))

    if check.type == "floors_max" and "floorsMax" in value_overrides:
        value = value_overrides["floorsMax"]
        params = dict(check.params)
        params["value"] = value
        return (Check(type=check.type, params=params), AppliedValueOverride("floorsMax", value))

    return (check, None)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: Mapping[str, CheckFn] = {
    "setback_min": check_setback_min,
    "far_max": check_far_max,
    "coverage_max": check_coverage_max,
    "height_max": check_height_max,
    "floors_max": check_floors_max,
    "room_area_min": check_room_area_min,
    "room_width_min": check_room_width_min,
    "ceiling_height_min": check_ceiling_height_min,
    "ventilation_ratio_min": check_ventilation_ratio_min,
    "stair_riser_max": check_stair_riser_max,
    "stair_tread_min": check_stair_tread_min,
    "stair_width_min": check_stair_width_min,
    "headroom_min": check_headroom_min,
    "projection_max": check_projection_max,
    "parking_min": check_parking_min,
    "opening_width_min": check_opening_width_min,
    "zone_check": check_zone_check,
    "custom": check_custom,
}

#: The complete set the engine implements — cross-checked against the schema's own
#: enum at pack load, so schema and engine cannot drift apart unnoticed.
CHECK_TYPES: frozenset[str] = frozenset(_REGISTRY)

#: check type -> scope. ``custom`` declares its own via ``check.scope``.
CHECK_SCOPES: Mapping[str, str] = {
    "setback_min": "edge",
    "far_max": "project",
    "coverage_max": "project",
    "height_max": "project",
    "floors_max": "project",
    "room_area_min": "room",
    "room_width_min": "room",
    "ceiling_height_min": "room",
    "ventilation_ratio_min": "room",
    "stair_riser_max": "stair",
    "stair_tread_min": "stair",
    "stair_width_min": "stair",
    "headroom_min": "stair",
    "projection_max": "projection",
    "parking_min": "project",
    "opening_width_min": "opening",
    "zone_check": "zone",
}

#: check type -> the unit of ``actual``/``limit`` in the result row.
RESULT_UNITS: Mapping[str, str] = {
    "setback_min": "mm",
    "far_max": "mm2",
    "coverage_max": "mm2",
    "height_max": "mm",
    "floors_max": "count",
    "room_area_min": "mm2",
    "room_width_min": "mm",
    "ceiling_height_min": "mm",
    "ventilation_ratio_min": "mm2",
    "stair_riser_max": "mm",
    "stair_tread_min": "mm",
    "stair_width_min": "mm",
    "headroom_min": "mm",
    "projection_max": "mm",
    "parking_min": "count",
    "opening_width_min": "mm",
    "zone_check": "zone",
}

#: Checks whose collapsed ``actual`` is the **union** over instances rather than the
#: governing instance's value. Only ``zone_check``: its row reports every zone the
#: matched targets occupy ("the toilets sit in NE and W"), because a single zone
#: label would hide the second toilet.
UNION_ACTUAL_CHECKS: frozenset[str] = frozenset({"zone_check"})


def scope_of(check: Check) -> str:
    if check.type == "custom":
        return check.str_param("scope")
    scope = CHECK_SCOPES.get(check.type)
    if scope is None:  # pragma: no cover - rejected at pack load
        raise EvaluationError("no scope registered for check type %r" % check.type)
    return scope


def result_unit_of(check: Check) -> str:
    if check.type == "custom":
        return custom_result_unit(check.str_param("fn"))
    unit = RESULT_UNITS.get(check.type)
    if unit is None:  # pragma: no cover - rejected at pack load
        raise EvaluationError("no result unit registered for check type %r" % check.type)
    return unit


def run_check(check: Check, instance: Instance, env: CheckEnv) -> Outcome:
    """Evaluate one check against one instance. Pure: no I/O, no mutation."""
    fn = _REGISTRY.get(check.type)
    if fn is None:  # pragma: no cover - rejected at pack load
        raise EvaluationError("no implementation for check type %r" % check.type)
    return fn(check, instance, env)


def union_actual(outcomes: Sequence[Outcome]) -> list[str]:
    """Sorted unique labels across instances — the ``zone_check`` ``actual``.

    Plain lexicographic :func:`sorted`, which is what "sorted unique zone labels"
    says and what another implementation would reproduce without consulting a
    compass-order table.
    """
    labels = {str(outcome.actual) for outcome in outcomes}
    return sorted(labels)
