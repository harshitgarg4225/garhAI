"""Scopes: what the engine iterates to produce one evaluation of a rule.

``x-garh-check-meta.scopes`` in the pack schema fixes this table, and it is the
part of the contract most easily got wrong, so it lives in one place:

======================  ====================================================
``project``             once; only project-level ``when`` fields are bound
``edge``                once per plot edge named by ``check.edge``; binds ``edgeRoadWidthMm``
``storey``              once per storey; binds ``storeyIndex``
``room``                once per room; binds ``roomType``, ``roomIsHabitable``, ``roomIsInternal``, ``storeyIndex``
``opening``             once per opening; binds ``openingKind``, ``openingRole``, ``storeyIndex``
``stair``               once per stair; binds ``storeyIndex``
``projection``          once per *matching* projection; binds ``storeyIndex``
``zone``                once per target the check itself selects
======================  ====================================================

Two design points that matter downstream:

* **Filtering happens here, not in the check.** ``projection_max`` names an
  element and optionally ``intoSetbackOnly``; a non-matching projection must not
  become a passing instance, because a passing instance can win the
  governing-instance selection and put the wrong number in the report.
* **No instances is not a pass.** An empty scope yields ``not_applicable`` with
  the reason ``no-instances`` — a house with no pooja room is not credited with
  having placed it well, and a plot with no ``front`` edge is not credited with a
  front setback.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from .context import EvaluationContext, PlotEdge, RoomSummary
from .errors import ContextError, EvaluationError
from .formatting import (
    PROJECT_LABEL,
    edge_label,
    opening_label,
    projection_label,
    room_label,
    service_label,
    stair_label,
    storey_label,
)
from .packs import Check, Vocabulary
from .zones import ZoneGrid, zone_grid_for

__all__ = [
    "Instance",
    "Outcome",
    "CheckEnv",
    "EDGE_SELECTORS",
    "edge_element_id",
    "edges_covered",
    "instances_for",
]

#: ``edgeSelector`` -> the edge roles it covers. ``all`` is handled separately
#: because it means "every edge in the plot", including ``other``.
EDGE_SELECTORS: Mapping[str, tuple[str, ...]] = {
    "front": ("front",),
    "rear": ("rear",),
    "side-a": ("side-a",),
    "side-b": ("side-b",),
    "sides": ("side-a", "side-b"),
}


def edges_covered(selector: str) -> tuple[str, ...]:
    """Roles a selector names. ``()`` means "every edge" (the ``all`` selector)."""
    if selector == "all":
        return ()
    covered = EDGE_SELECTORS.get(selector)
    if covered is None:
        raise EvaluationError("unknown edge selector %r" % selector)
    return covered


def edge_element_id(edge: PlotEdge, edges: Sequence[PlotEdge]) -> str:
    """The chip id for one plot edge: ``plot.edge.<role>``.

    The vertex index is appended when the plot has two edges in the same role
    (possible for ``other``), so two offenders never collapse into one chip. Counted
    over **every** edge rather than the ones a selector happened to pick, so an edge
    keeps the same id whichever rule names it — and :mod:`garh_rules.areas` calls this
    same function, so a setback row in the area statement and its compliance chip can
    never disagree about which edge they mean.
    """
    if sum(1 for other in edges if other.role == edge.role) > 1:
        return "plot.edge.%s.%d" % (edge.role, edge.index)
    return "plot.edge.%s" % edge.role


@dataclass(frozen=True)
class Instance:
    """One thing to measure, with the ``when`` fields its scope binds."""

    kind: str
    element_id: str | None
    label: str
    fields: Mapping[str, Any] = field(default_factory=dict)
    payload: Any = None

    def require(self, expected: str) -> Any:
        if self.kind != expected:
            raise EvaluationError("check expected a %s instance, got %s" % (expected, self.kind))
        return self.payload


@dataclass
class CheckEnv:
    """Per-run scratch space: the context, the merged vocabulary, cached lookups.

    Built once per :func:`garh_rules.engine.evaluate` call. The zone grid is lazy
    because most runs have no Vastu pack loaded and the rotation is the only
    trigonometry in the engine.
    """

    context: EvaluationContext
    vocabulary: Vocabulary
    _grid: ZoneGrid | None = None
    _rooms: dict[str, RoomSummary] = field(default_factory=dict)
    _storey_index: dict[str, int] = field(default_factory=dict)
    _instances: dict[Any, tuple[Instance, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._rooms = {room.id: room for room in self.context.model.rooms}
        self._storey_index = {s.id: s.index for s in self.context.model.storeys}

    @property
    def grid(self) -> ZoneGrid:
        if self._grid is None:
            self._grid = zone_grid_for(self.context.plot)
        return self._grid

    def room(self, room_id: str) -> RoomSummary | None:
        return self._rooms.get(room_id)

    def storey_index(self, storey_id: str, owner: str) -> int:
        index = self._storey_index.get(storey_id)
        if index is None:
            raise ContextError(
                "%s sits on storey %r, which is not listed in model.storeys — the engine cannot "
                "bind storeyIndex for it" % (owner, storey_id),
                field=owner,
            )
        return index

    def is_habitable(self, room: RoomSummary) -> bool:
        """Straight from the pack vocabulary. "Is a study habitable" is a bye-law
        question, so the answer lives in the pack, never in this code."""
        return room.type in self.vocabulary.habitable_room_types


# ---------------------------------------------------------------------------
# Per-scope iteration
# ---------------------------------------------------------------------------


def _project_instances() -> tuple[Instance, ...]:
    return (Instance(kind="project", element_id=None, label=PROJECT_LABEL),)


def _edge_instances(check: Check, env: CheckEnv) -> tuple[Instance, ...]:
    selector = check.str_param("edge")
    roles = edges_covered(selector)
    all_edges = env.context.plot.edges
    out: list[Instance] = []
    for edge in all_edges:
        if roles and edge.role not in roles:
            continue
        out.append(
            Instance(
                kind="edge",
                element_id=edge_element_id(edge, all_edges),
                label=edge_label(edge),
                fields={"edgeRoadWidthMm": edge.road_width_mm},
                payload=edge,
            )
        )
    return tuple(out)


def _storey_instances(env: CheckEnv) -> tuple[Instance, ...]:
    out: list[Instance] = []
    for storey in env.context.model.storeys:
        out.append(
            Instance(
                kind="storey",
                element_id=storey.id,
                label=storey_label(storey),
                fields={"storeyIndex": storey.index},
                payload=storey,
            )
        )
    return tuple(out)


def _room_instances(env: CheckEnv) -> tuple[Instance, ...]:
    out: list[Instance] = []
    for room in env.context.model.rooms:
        out.append(
            Instance(
                kind="room",
                element_id=room.id,
                label=room_label(room),
                fields={
                    "storeyIndex": env.storey_index(room.storey_id, "room %s" % room.id),
                    "roomType": room.type,
                    "roomIsHabitable": env.is_habitable(room),
                    "roomIsInternal": room.is_internal,
                },
                payload=room,
            )
        )
    return tuple(out)


def _opening_instances(env: CheckEnv) -> tuple[Instance, ...]:
    out: list[Instance] = []
    for opening in env.context.model.openings:
        out.append(
            Instance(
                kind="opening",
                element_id=opening.id,
                label=opening_label(opening, env.context),
                fields={
                    "storeyIndex": env.storey_index(opening.storey_id, "opening %s" % opening.id),
                    "openingKind": opening.kind,
                    "openingRole": opening.role,
                },
                payload=opening,
            )
        )
    return tuple(out)


def _stair_instances(env: CheckEnv) -> tuple[Instance, ...]:
    out: list[Instance] = []
    for stair in env.context.model.stairs:
        out.append(
            Instance(
                kind="stair",
                element_id=stair.id,
                label=stair_label(stair),
                fields={"storeyIndex": env.storey_index(stair.storey_id, "stair %s" % stair.id)},
                payload=stair,
            )
        )
    return tuple(out)


def _projection_instances(check: Check, env: CheckEnv) -> tuple[Instance, ...]:
    element = check.str_param("element")
    into_setback_only = check.bool_param("intoSetbackOnly", False)
    out: list[Instance] = []
    for projection in env.context.model.projections:
        if projection.element != element:
            continue
        if into_setback_only and not projection.into_setback:
            continue
        out.append(
            Instance(
                kind="projection",
                element_id=projection.id,
                label=projection_label(projection),
                fields={
                    "storeyIndex": env.storey_index(
                        projection.storey_id, "projection %s" % projection.id
                    )
                },
                payload=projection,
            )
        )
    return tuple(out)


def _zone_instances(check: Check, env: CheckEnv) -> tuple[Instance, ...]:
    """The targets a ``zone_check`` selects — rooms, openings, services or stairs."""
    target = check.mapping_param("target")
    kind = str(target.get("kind"))
    model = env.context.model
    out: list[Instance] = []

    if kind == "room":
        wanted = frozenset(str(t) for t in (target.get("roomTypes") or ()))
        storey_index = target.get("storeyIndex")
        for room in model.rooms:
            if room.type not in wanted:
                continue
            if (
                storey_index is not None
                and env.storey_index(room.storey_id, "room %s" % room.id) != storey_index
            ):
                continue
            out.append(
                Instance(
                    kind="room",
                    element_id=room.id,
                    label=room_label(room),
                    fields={},
                    payload=room,
                )
            )
    elif kind == "opening":
        roles = frozenset(str(r) for r in (target.get("roles") or ()))
        for opening in model.openings:
            if opening.role not in roles:
                continue
            out.append(
                Instance(
                    kind="opening",
                    element_id=opening.id,
                    label=opening_label(opening, env.context),
                    fields={},
                    payload=opening,
                )
            )
    elif kind == "service":
        kinds = frozenset(str(k) for k in (target.get("serviceKinds") or ()))
        for service in model.service_elements:
            if service.kind not in kinds:
                continue
            out.append(
                Instance(
                    kind="service",
                    element_id=service.id,
                    label=service_label(service),
                    fields={},
                    payload=service,
                )
            )
    elif kind == "stair":
        for stair in model.stairs:
            out.append(
                Instance(
                    kind="stair",
                    element_id=stair.id,
                    label=stair_label(stair),
                    fields={},
                    payload=stair,
                )
            )
    else:  # pragma: no cover - schema-constrained
        raise EvaluationError("unknown zone target kind %r" % kind)
    return tuple(out)


def _cache_key(check: Check, scope: str) -> Any:
    """What makes two rules' instance lists identical.

    Most scopes depend only on the model, so 60-odd room rules share one list.
    ``edge`` varies by selector, ``projection`` by the element it filters on, and
    ``zone`` by its whole target — memoising these is what keeps a 118-rule run
    from rebuilding the same twelve room instances 60 times inside the 100 ms
    budget (§14).
    """
    if scope == "edge":
        return ("edge", check.str_param("edge"), check.str_param("measure", "to-building-line"))
    if scope == "projection":
        return (
            "projection",
            check.str_param("element"),
            check.bool_param("intoSetbackOnly", False),
        )
    if scope == "zone":
        target = check.mapping_param("target")
        return (
            "zone",
            str(target.get("kind")),
            tuple(sorted(str(v) for v in (target.get("roomTypes") or ()))),
            tuple(sorted(str(v) for v in (target.get("roles") or ()))),
            tuple(sorted(str(v) for v in (target.get("serviceKinds") or ()))),
            target.get("storeyIndex"),
            check.str_param("mode"),
        )
    return (scope,)


def instances_for(check: Check, scope: str, env: CheckEnv) -> tuple[Instance, ...]:
    """Every instance of ``scope`` this check applies to, in model order. Memoised."""
    key = _cache_key(check, scope)
    cached = env._instances.get(key)
    if cached is not None:
        return cached
    if scope == "project":
        built = _project_instances()
    elif scope == "edge":
        built = _edge_instances(check, env)
    elif scope == "storey":
        built = _storey_instances(env)
    elif scope == "room":
        built = _room_instances(env)
    elif scope == "opening":
        built = _opening_instances(env)
    elif scope == "stair":
        built = _stair_instances(env)
    elif scope == "projection":
        built = _projection_instances(check, env)
    elif scope == "zone":
        built = _zone_instances(check, env)
    else:
        raise EvaluationError("unknown scope %r" % scope)
    env._instances[key] = built
    return built


# ---------------------------------------------------------------------------
# What a check function returns
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """One instance measured. The check's whole output, and nothing else.

    ``order_key`` is the slack: ``actual - limit`` for a minimum, ``limit - actual``
    for a maximum, ``satisfaction - 1`` for a categorical check. Zero means "exactly
    on the limit" (which passes), negative means violated and *how badly*. It is
    what picks the governing instance when a rule matches several rooms: worst
    status first, then tightest margin, then element id. Selecting on slack rather
    than on raw value is what makes ``ventilation_ratio_min`` behave — its limit
    differs per room, so the raw numbers are not comparable but the margins are.

    ``degraded`` marks a violation that landed in a scored ``fallback``: reported at
    ``warn`` however severe the rule is, because a fallback is explicitly
    "acceptable but not ideal", not a breach.
    """

    satisfied: bool
    actual: Any
    limit: Any
    order_key: Fraction
    satisfaction: Fraction = Fraction(1)
    degraded: bool = False
    note: str | None = None
    #: Overrides the instance's own element id in ``elements[]`` — used by
    #: ``brahmasthan_open``, a project-scope check that names the offending rooms.
    elements: tuple[str, ...] | None = None

    @classmethod
    def at_least(cls, actual: int, limit: int, **kwargs: Any) -> Outcome:
        """A minimum: satisfied when ``actual >= limit``."""
        satisfied = actual >= limit
        return cls(
            satisfied=satisfied,
            actual=actual,
            limit=limit,
            order_key=Fraction(actual - limit),
            satisfaction=Fraction(1) if satisfied else Fraction(0),
            **kwargs,
        )

    @classmethod
    def at_most(cls, actual: int, limit: int, **kwargs: Any) -> Outcome:
        """A maximum: satisfied when ``actual <= limit``."""
        satisfied = actual <= limit
        return cls(
            satisfied=satisfied,
            actual=actual,
            limit=limit,
            order_key=Fraction(limit - actual),
            satisfaction=Fraction(1) if satisfied else Fraction(0),
            **kwargs,
        )
