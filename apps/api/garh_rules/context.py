"""The engine's input: a flattened, pre-derived projection of model + plot + profile.

The shape is not ours to invent — it is
``rulepacks/schema/fixture.schema.json`` -> ``$defs.evaluationContext``, which is
simultaneously the fixture format and this contract. Two consequences worth
stating out loud:

1. **The engine does no geometry.** Room area, least width, centroid, clear
   ceiling height and openable area arrive pre-derived. The fixture verifier
   recomputes all of them from ``polygonMm`` and fails on disagreement, so a
   fixture cannot lie about its own geometry — and the engine does not have to
   spend the 100 ms budget re-deriving what the model layer already knows.
2. **Integers only, everywhere.** :func:`~garh_rules.ratio.require_int` rejects a
   float length at the boundary. A rounding-tolerant parser here would put a
   drifting number into a compliance report and a municipal drawing.

``build_context`` — turning a live ``garh_model.ProjectDoc`` into one of these —
belongs to the model layer, and two of the fields it must supply
(``opening.role`` and ``model.serviceElements``) **do not exist in the model core
yet**; see :data:`MODEL_FIELDS_NOT_IN_MODEL_CORE`. Until they do, callers build a
context from the parts they have (:func:`context_from_parts`), and the engine
reports the affected rules honestly rather than passing them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .errors import ContextError
from .ratio import require_int

__all__ = [
    "EvaluationContext",
    "PlotEdge",
    "PlotSummary",
    "ProfileSummary",
    "RuleOverride",
    "VALUE_OVERRIDES_KEY",
    "StoreySummary",
    "RoomSummary",
    "OpeningSummary",
    "StairSummary",
    "ProjectionSummary",
    "ServiceElementSummary",
    "ModelSummary",
    "ROOM_TYPE_ALIASES",
    "MODEL_FIELDS_NOT_IN_MODEL_CORE",
    "normalise_room_type",
    "context_from_parts",
]


#: Model-core room type -> rule-pack room type.
#:
#: The two vocabularies drifted (``garh_model.ROOM_TYPES`` vs the ``roomType``
#: enum in ``rulepacks/schema/rulepack.schema.json``) and the drift is not
#: cosmetic: without the first row a master bedroom is not habitable as far as
#: the packs are concerned, so NBC's 9.5 m2 minimum silently never fires on it.
#: That is exactly the class of bug this codebase refuses to ship, so the mapping
#: is an explicit, reviewable table rather than a fuzzy match.
#:
#: Every judgement call is annotated. A model type that reaches the engine without
#: an alias and without being in the pack vocabulary is reported as unchecked
#: (see :meth:`EvaluationContext.unclassified_room_types`), never assumed benign.
ROOM_TYPE_ALIASES: Mapping[str, str] = {
    "bedroom_master": "master_bedroom",  # pure spelling drift
    "passage": "corridor",  # both are non-habitable circulation
    "foyer": "lobby",  # entrance hall; non-habitable either way
    "duct": "shaft",  # service void; both sit in farExclusions
    "void": "courtyard",  # a double-height void is what the brahmasthan rule calls "open"
    "dress": "other",  # a dressing room carries no NBC minimum; do not claim habitable
    "unassigned": "other",  # no programme assigned yet -> no type-specific rule applies
}

#: Fields the EvaluationContext requires that ``garh_model``'s document cannot
#: supply today. Named here so nobody papers over them with a guess.
MODEL_FIELDS_NOT_IN_MODEL_CORE: tuple[str, ...] = (
    "opening.role — openingRole (main-entrance|internal|bath|balcony|service|garage) "
    "drives every door-width minimum; the model core has no such field, so inferring it "
    "would mislabel the main door.",
    "model.serviceElements — water_tank / oht / sump centroids, required by "
    "vastu.water_tank.zone.",
    "room.ventilationOpeningAreaMm2 — the openable area serving each room; openings are "
    "hosted on walls, so the model layer must resolve opening -> room itself.",
    "plot.edges[].setbackProvidedMm — the real open distance from each plot line to the "
    "building line.",
)


def _opt_int(value: Any, what: str) -> int | None:
    if value is None:
        return None
    return require_int(value, what)


def _point(value: Any, what: str) -> tuple[int, int]:
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise ContextError("%s must be a [x, y] pair, got %r" % (what, value), field=what)
    return (require_int(value[0], "%s[0]" % what), require_int(value[1], "%s[1]" % what))


def _ring(value: Any, what: str) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, list | tuple) or len(value) < 3:
        raise ContextError("%s must be a ring of >= 3 points" % what, field=what)
    return tuple(_point(p, "%s[%d]" % (what, i)) for i, p in enumerate(value))


def _str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContextError("%s must be a non-empty string, got %r" % (what, value), field=what)
    return value


def _bool(value: Any, what: str) -> bool:
    if not isinstance(value, bool):
        raise ContextError("%s must be a boolean, got %r" % (what, value), field=what)
    return value


def normalise_room_type(room_type: str) -> str:
    """Map a model-core room type onto the rule packs' vocabulary.

    Unknown types pass through unchanged: they then match no ``roomType``
    predicate, which is the correct outcome for a room with no programme — and
    :meth:`EvaluationContext.unclassified_room_types` makes that visible instead
    of letting it look like a clean pass.
    """
    return ROOM_TYPE_ALIASES.get(room_type, room_type)


# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlotEdge:
    """One plot boundary edge. ``index`` points at its start vertex in ``boundaryMm``."""

    index: int
    role: str  # front | rear | side-a | side-b | other
    road_width_mm: int | None
    setback_provided_mm: int

    @classmethod
    def from_json(cls, data: Mapping[str, Any], where: str) -> PlotEdge:
        return cls(
            index=require_int(data.get("index"), "%s.index" % where),
            role=_str(data.get("role"), "%s.role" % where),
            road_width_mm=_opt_int(data.get("roadWidthMm"), "%s.roadWidthMm" % where),
            setback_provided_mm=require_int(
                data.get("setbackProvidedMm"), "%s.setbackProvidedMm" % where
            ),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "role": self.role,
            "roadWidthMm": self.road_width_mm,
            "setbackProvidedMm": self.setback_provided_mm,
        }


@dataclass(frozen=True)
class PlotSummary:
    boundary_mm: tuple[tuple[int, int], ...]
    area_mm2: int
    north_deg: int
    corner_plot: bool
    edges: tuple[PlotEdge, ...]
    frontage_mm: int | None = None
    depth_mm: int | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> PlotSummary:
        edges = data.get("edges")
        if not isinstance(edges, list | tuple) or len(edges) < 3:
            raise ContextError("plot.edges must list at least 3 edges", field="plot.edges")
        north = require_int(data.get("northDeg"), "plot.northDeg")
        if not 0 <= north <= 359:
            raise ContextError(
                "plot.northDeg must be 0..359, got %d" % north, field="plot.northDeg"
            )
        return cls(
            boundary_mm=_ring(data.get("boundaryMm"), "plot.boundaryMm"),
            area_mm2=require_int(data.get("areaMm2"), "plot.areaMm2"),
            north_deg=north,
            corner_plot=_bool(data.get("cornerPlot"), "plot.cornerPlot"),
            edges=tuple(PlotEdge.from_json(e, "plot.edges[%d]" % i) for i, e in enumerate(edges)),
            frontage_mm=_opt_int(data.get("frontageMm"), "plot.frontageMm"),
            depth_mm=_opt_int(data.get("depthMm"), "plot.depthMm"),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "boundaryMm": [list(p) for p in self.boundary_mm],
            "areaMm2": self.area_mm2,
            "northDeg": self.north_deg,
            "frontageMm": self.frontage_mm,
            "depthMm": self.depth_mm,
            "cornerPlot": self.corner_plot,
            "edges": [e.to_json() for e in self.edges],
        }

    def edges_with_role(self, role: str) -> tuple[PlotEdge, ...]:
        return tuple(e for e in self.edges if e.role == role)

    def front_road_width_mm(self) -> int | None:
        """The primary access road: the road on the ``front`` edge, else ``None``.

        ``None`` is load-bearing. Every numeric operator on a null context field
        is false, so a rule banded on road width becomes ``not_applicable`` on a
        plot whose road is not set yet — it never silently passes.
        """
        for edge in self.edges:
            if edge.role == "front":
                return edge.road_width_mm
        return None

    def abutting_road_count(self) -> int:
        return sum(1 for e in self.edges if e.road_width_mm is not None)


# ---------------------------------------------------------------------------
# Regulatory profile
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleOverride:
    """An architect's logged override of one rule (§13 audit trail).

    The rule still evaluates and still reports its real status; the row is simply
    marked accepted-with-override. Suppressing the result would hide it from the
    compliance annexure the drawing set carries.
    """

    reason: str
    by_user_id: str | None = None
    at: str | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any], where: str) -> RuleOverride:
        return cls(
            reason=_str(data.get("reason"), "%s.reason" % where),
            by_user_id=data.get("byUserId"),
            at=data.get("at"),
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"reason": self.reason}
        if self.by_user_id is not None:
            out["byUserId"] = self.by_user_id
        if self.at is not None:
            out["at"] = self.at
        return out


#: Reserved key inside ``profile.overrides``: NOT a rule id. The plot panel stores
#: the architect's value overrides under it as a flat integer map
#: (``{"values": {"setbackFrontMm": 1200, "farX100": 175}}`` — ratios ×100). The two
#: shapes share one object, so the parser must route this key BEFORE iterating
#: rule-id acknowledgements, or every value-overridden project fails context building.
VALUE_OVERRIDES_KEY = "values"


@dataclass(frozen=True)
class ProfileSummary:
    city_pack: str
    zone_category: str
    building_use: str
    dwelling_units: int
    parking_spaces_provided: int
    rwh_declared: bool
    overrides: Mapping[str, RuleOverride] = field(default_factory=dict)
    #: Architect's value overrides (``VALUE_OVERRIDES_KEY``), integer-valued.
    #: Parsed and round-tripped here; SUBSTITUTED into check limits at evaluation
    #: time since Phase 3 (``garh_rules.checks.substitute_value_override``, called
    #: per instance by the engine). The result row keeps the pack's own number in
    #: ``original_limit`` and reports ``valueOverridden`` — an override moves the
    #: limit, it never silences the check.
    value_overrides: Mapping[str, int] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> ProfileSummary:
        raw_overrides = data.get("overrides") or {}
        if not isinstance(raw_overrides, Mapping):
            raise ContextError("profile.overrides must be an object", field="profile.overrides")
        raw_values = raw_overrides.get(VALUE_OVERRIDES_KEY) or {}
        if not isinstance(raw_values, Mapping):
            raise ContextError(
                "profile.overrides.values must be an object of integers",
                field="profile.overrides.values",
            )
        return cls(
            city_pack=_str(data.get("cityPack"), "profile.cityPack"),
            zone_category=_str(data.get("zoneCategory"), "profile.zoneCategory"),
            building_use=_str(data.get("buildingUse"), "profile.buildingUse"),
            dwelling_units=require_int(data.get("dwellingUnits"), "profile.dwellingUnits"),
            parking_spaces_provided=require_int(
                data.get("parkingSpacesProvided"), "profile.parkingSpacesProvided"
            ),
            rwh_declared=_bool(data.get("rwhDeclared"), "profile.rwhDeclared"),
            overrides={
                rule_id: RuleOverride.from_json(value, "profile.overrides.%s" % rule_id)
                for rule_id, value in raw_overrides.items()
                if rule_id != VALUE_OVERRIDES_KEY
            },
            value_overrides={
                _str(key, "profile.overrides.values key"): require_int(
                    value, "profile.overrides.values.%s" % key
                )
                for key, value in raw_values.items()
            },
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "cityPack": self.city_pack,
            "zoneCategory": self.zone_category,
            "buildingUse": self.building_use,
            "dwellingUnits": self.dwelling_units,
            "parkingSpacesProvided": self.parking_spaces_provided,
            "rwhDeclared": self.rwh_declared,
        }
        if self.overrides or self.value_overrides:
            merged: dict[str, Any] = {k: v.to_json() for k, v in sorted(self.overrides.items())}
            if self.value_overrides:
                merged[VALUE_OVERRIDES_KEY] = dict(sorted(self.value_overrides.items()))
            out["overrides"] = merged
        return out

    def flag(self, name: str) -> bool | None:
        """Boolean profile fields addressable by name, for ``custom.rwh_required``."""
        if name == "rwhDeclared":
            return self.rwh_declared
        return None


# ---------------------------------------------------------------------------
# Model elements
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoreySummary:
    id: str
    index: int
    height_mm: int
    clear_height_mm: int | None = None
    built_up_area_mm2: int | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any], where: str) -> StoreySummary:
        return cls(
            id=_str(data.get("id"), "%s.id" % where),
            index=require_int(data.get("index"), "%s.index" % where),
            height_mm=require_int(data.get("heightMm"), "%s.heightMm" % where),
            clear_height_mm=_opt_int(data.get("clearHeightMm"), "%s.clearHeightMm" % where),
            built_up_area_mm2=_opt_int(data.get("builtUpAreaMm2"), "%s.builtUpAreaMm2" % where),
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "index": self.index, "heightMm": self.height_mm}
        if self.clear_height_mm is not None:
            out["clearHeightMm"] = self.clear_height_mm
        if self.built_up_area_mm2 is not None:
            out["builtUpAreaMm2"] = self.built_up_area_mm2
        return out


@dataclass(frozen=True)
class RoomSummary:
    id: str
    storey_id: str
    type: str
    name: str
    polygon_mm: tuple[tuple[int, int], ...]
    area_mm2: int
    least_width_mm: int
    centroid_mm: tuple[int, int]
    clear_ceiling_height_mm: int
    ventilation_opening_area_mm2: int
    is_internal: bool
    has_shaft_access: bool = False
    #: The type as it arrived, before :data:`ROOM_TYPE_ALIASES` was applied.
    raw_type: str = ""

    @classmethod
    def from_json(cls, data: Mapping[str, Any], where: str) -> RoomSummary:
        raw_type = _str(data.get("type"), "%s.type" % where)
        return cls(
            id=_str(data.get("id"), "%s.id" % where),
            storey_id=_str(data.get("storeyId"), "%s.storeyId" % where),
            type=normalise_room_type(raw_type),
            name=_str(data.get("name"), "%s.name" % where) if data.get("name") else raw_type,
            polygon_mm=_ring(data.get("polygonMm"), "%s.polygonMm" % where),
            area_mm2=require_int(data.get("areaMm2"), "%s.areaMm2" % where),
            least_width_mm=require_int(data.get("leastWidthMm"), "%s.leastWidthMm" % where),
            centroid_mm=_point(data.get("centroidMm"), "%s.centroidMm" % where),
            clear_ceiling_height_mm=require_int(
                data.get("clearCeilingHeightMm"), "%s.clearCeilingHeightMm" % where
            ),
            ventilation_opening_area_mm2=require_int(
                data.get("ventilationOpeningAreaMm2"), "%s.ventilationOpeningAreaMm2" % where
            ),
            is_internal=_bool(data.get("isInternal"), "%s.isInternal" % where),
            has_shaft_access=bool(data.get("hasShaftAccess", False)),
            raw_type=raw_type,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "storeyId": self.storey_id,
            "type": self.raw_type or self.type,
            "name": self.name,
            "polygonMm": [list(p) for p in self.polygon_mm],
            "areaMm2": self.area_mm2,
            "leastWidthMm": self.least_width_mm,
            "centroidMm": list(self.centroid_mm),
            "clearCeilingHeightMm": self.clear_ceiling_height_mm,
            "ventilationOpeningAreaMm2": self.ventilation_opening_area_mm2,
            "isInternal": self.is_internal,
            "hasShaftAccess": self.has_shaft_access,
        }


@dataclass(frozen=True)
class OpeningSummary:
    id: str
    storey_id: str
    kind: str
    role: str
    width_mm: int
    height_mm: int
    wall_id: str | None = None
    sill_mm: int | None = None
    room_ids: tuple[str, ...] = ()
    centroid_mm: tuple[int, int] | None = None
    outward_normal_deg: int | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any], where: str) -> OpeningSummary:
        normal = data.get("outwardNormalDeg")
        if normal is not None:
            normal = require_int(normal, "%s.outwardNormalDeg" % where)
            if not 0 <= normal <= 359:
                raise ContextError(
                    "%s.outwardNormalDeg must be 0..359, got %d" % (where, normal), field=where
                )
        raw_rooms = data.get("roomIds") or ()
        centroid = data.get("centroidMm")
        return cls(
            id=_str(data.get("id"), "%s.id" % where),
            storey_id=_str(data.get("storeyId"), "%s.storeyId" % where),
            kind=_str(data.get("kind"), "%s.kind" % where),
            role=_str(data.get("role"), "%s.role" % where),
            width_mm=require_int(data.get("widthMm"), "%s.widthMm" % where),
            height_mm=require_int(data.get("heightMm"), "%s.heightMm" % where),
            wall_id=data.get("wallId"),
            sill_mm=_opt_int(data.get("sillMm"), "%s.sillMm" % where),
            room_ids=tuple(str(r) for r in raw_rooms),
            centroid_mm=_point(centroid, "%s.centroidMm" % where) if centroid else None,
            outward_normal_deg=normal,
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "storeyId": self.storey_id,
            "kind": self.kind,
            "role": self.role,
            "widthMm": self.width_mm,
            "heightMm": self.height_mm,
        }
        if self.wall_id is not None:
            out["wallId"] = self.wall_id
        if self.sill_mm is not None:
            out["sillMm"] = self.sill_mm
        if self.room_ids:
            out["roomIds"] = list(self.room_ids)
        if self.centroid_mm is not None:
            out["centroidMm"] = list(self.centroid_mm)
        out["outwardNormalDeg"] = self.outward_normal_deg
        return out


@dataclass(frozen=True)
class StairSummary:
    id: str
    storey_id: str
    riser_mm: int
    tread_mm: int
    width_mm: int
    headroom_mm: int
    kind: str | None = None
    risers_count: int | None = None
    centroid_mm: tuple[int, int] | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any], where: str) -> StairSummary:
        centroid = data.get("centroidMm")
        return cls(
            id=_str(data.get("id"), "%s.id" % where),
            storey_id=_str(data.get("storeyId"), "%s.storeyId" % where),
            riser_mm=require_int(data.get("riserMm"), "%s.riserMm" % where),
            tread_mm=require_int(data.get("treadMm"), "%s.treadMm" % where),
            width_mm=require_int(data.get("widthMm"), "%s.widthMm" % where),
            headroom_mm=require_int(data.get("headroomMm"), "%s.headroomMm" % where),
            kind=data.get("kind"),
            risers_count=_opt_int(data.get("risersCount"), "%s.risersCount" % where),
            centroid_mm=_point(centroid, "%s.centroidMm" % where) if centroid else None,
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "storeyId": self.storey_id,
            "riserMm": self.riser_mm,
            "treadMm": self.tread_mm,
            "widthMm": self.width_mm,
            "headroomMm": self.headroom_mm,
        }
        if self.kind is not None:
            out["kind"] = self.kind
        if self.risers_count is not None:
            out["risersCount"] = self.risers_count
        if self.centroid_mm is not None:
            out["centroidMm"] = list(self.centroid_mm)
        return out


@dataclass(frozen=True)
class ProjectionSummary:
    id: str
    storey_id: str
    element: str
    edge_role: str
    projection_mm: int
    into_setback: bool = False

    @classmethod
    def from_json(cls, data: Mapping[str, Any], where: str) -> ProjectionSummary:
        return cls(
            id=_str(data.get("id"), "%s.id" % where),
            storey_id=_str(data.get("storeyId"), "%s.storeyId" % where),
            element=_str(data.get("element"), "%s.element" % where),
            edge_role=_str(data.get("edgeRole"), "%s.edgeRole" % where),
            projection_mm=require_int(data.get("projectionMm"), "%s.projectionMm" % where),
            into_setback=bool(data.get("intoSetback", False)),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "storeyId": self.storey_id,
            "element": self.element,
            "edgeRole": self.edge_role,
            "projectionMm": self.projection_mm,
            "intoSetback": self.into_setback,
        }


@dataclass(frozen=True)
class ServiceElementSummary:
    id: str
    kind: str
    centroid_mm: tuple[int, int]
    storey_id: str | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any], where: str) -> ServiceElementSummary:
        return cls(
            id=_str(data.get("id"), "%s.id" % where),
            kind=_str(data.get("kind"), "%s.kind" % where),
            centroid_mm=_point(data.get("centroidMm"), "%s.centroidMm" % where),
            storey_id=data.get("storeyId"),
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "centroidMm": list(self.centroid_mm),
        }
        if self.storey_id is not None:
            out["storeyId"] = self.storey_id
        return out


@dataclass(frozen=True)
class ModelSummary:
    storey_count: int
    has_stilt: bool
    has_basement: bool
    building_height_mm: int
    footprint_area_mm2: int
    built_up_area_mm2: int
    far_countable_area_mm2: int
    storeys: tuple[StoreySummary, ...] = ()
    rooms: tuple[RoomSummary, ...] = ()
    openings: tuple[OpeningSummary, ...] = ()
    stairs: tuple[StairSummary, ...] = ()
    projections: tuple[ProjectionSummary, ...] = ()
    service_elements: tuple[ServiceElementSummary, ...] = ()
    height_components_mm: Mapping[str, int] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> ModelSummary:
        components_raw = data.get("heightComponentsMm") or {}
        if not isinstance(components_raw, Mapping):
            raise ContextError(
                "model.heightComponentsMm must be an object", field="model.heightComponentsMm"
            )
        components = {
            _str(k, "model.heightComponentsMm key"): require_int(
                v, "model.heightComponentsMm.%s" % k
            )
            for k, v in components_raw.items()
        }
        return cls(
            storey_count=require_int(data.get("storeyCount"), "model.storeyCount"),
            has_stilt=_bool(data.get("hasStilt"), "model.hasStilt"),
            has_basement=_bool(data.get("hasBasement"), "model.hasBasement"),
            building_height_mm=require_int(data.get("buildingHeightMm"), "model.buildingHeightMm"),
            footprint_area_mm2=require_int(data.get("footprintAreaMm2"), "model.footprintAreaMm2"),
            built_up_area_mm2=require_int(data.get("builtUpAreaMm2"), "model.builtUpAreaMm2"),
            far_countable_area_mm2=require_int(
                data.get("farCountableAreaMm2"), "model.farCountableAreaMm2"
            ),
            storeys=tuple(
                StoreySummary.from_json(s, "model.storeys[%d]" % i)
                for i, s in enumerate(data.get("storeys") or ())
            ),
            rooms=tuple(
                RoomSummary.from_json(r, "model.rooms[%d]" % i)
                for i, r in enumerate(data.get("rooms") or ())
            ),
            openings=tuple(
                OpeningSummary.from_json(o, "model.openings[%d]" % i)
                for i, o in enumerate(data.get("openings") or ())
            ),
            stairs=tuple(
                StairSummary.from_json(s, "model.stairs[%d]" % i)
                for i, s in enumerate(data.get("stairs") or ())
            ),
            projections=tuple(
                ProjectionSummary.from_json(p, "model.projections[%d]" % i)
                for i, p in enumerate(data.get("projections") or ())
            ),
            service_elements=tuple(
                ServiceElementSummary.from_json(s, "model.serviceElements[%d]" % i)
                for i, s in enumerate(data.get("serviceElements") or ())
            ),
            height_components_mm=components,
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "storeyCount": self.storey_count,
            "hasStilt": self.has_stilt,
            "hasBasement": self.has_basement,
            "buildingHeightMm": self.building_height_mm,
            "footprintAreaMm2": self.footprint_area_mm2,
            "builtUpAreaMm2": self.built_up_area_mm2,
            "farCountableAreaMm2": self.far_countable_area_mm2,
            "storeys": [s.to_json() for s in self.storeys],
            "rooms": [r.to_json() for r in self.rooms],
            "openings": [o.to_json() for o in self.openings],
            "stairs": [s.to_json() for s in self.stairs],
        }
        if self.height_components_mm:
            out["heightComponentsMm"] = dict(sorted(self.height_components_mm.items()))
        if self.projections:
            out["projections"] = [p.to_json() for p in self.projections]
        if self.service_elements:
            out["serviceElements"] = [s.to_json() for s in self.service_elements]
        return out


# ---------------------------------------------------------------------------
# The context itself
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationContext:
    """Everything the engine is allowed to look at. Immutable, integer-only."""

    packs: tuple[str, ...]
    vastu_mode: str
    plot: PlotSummary
    profile: ProfileSummary
    model: ModelSummary

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> EvaluationContext:
        if not isinstance(data, Mapping):
            raise ContextError("EvaluationContext must be an object, got %r" % type(data).__name__)
        packs = data.get("packs")
        if not isinstance(packs, list | tuple) or not packs:
            raise ContextError("context.packs must list at least one pack id", field="packs")
        mode = _str(data.get("vastuMode"), "vastuMode")
        if mode not in ("off", "advisory", "strict"):
            raise ContextError(
                "vastuMode must be off|advisory|strict, got %r" % mode, field="vastuMode"
            )
        for key in ("plot", "profile", "model"):
            if not isinstance(data.get(key), Mapping):
                raise ContextError("context.%s is missing or not an object" % key, field=key)
        return cls(
            packs=tuple(_str(p, "packs[]") for p in packs),
            vastu_mode=mode,
            plot=PlotSummary.from_json(data["plot"]),
            profile=ProfileSummary.from_json(data["profile"]),
            model=ModelSummary.from_json(data["model"]),
        )

    @classmethod
    def coerce(cls, value: Any) -> EvaluationContext:
        """Accept either a context object or its JSON form. One call site, one shape."""
        if isinstance(value, EvaluationContext):
            return value
        if isinstance(value, Mapping):
            return cls.from_json(value)
        raise ContextError(
            "expected an EvaluationContext or its JSON mapping, got %r" % type(value).__name__
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "packs": list(self.packs),
            "vastuMode": self.vastu_mode,
            "plot": self.plot.to_json(),
            "profile": self.profile.to_json(),
            "model": self.model.to_json(),
        }

    # -- lookups -----------------------------------------------------------
    def storey_index(self, storey_id: str) -> int | None:
        for storey in self.model.storeys:
            if storey.id == storey_id:
                return storey.index
        return None

    def storey_index_or_raise(self, storey_id: str, owner: str) -> int:
        index = self.storey_index(storey_id)
        if index is None:
            raise ContextError(
                "%s references storey %r, which is not in model.storeys" % (owner, storey_id),
                field=owner,
            )
        return index

    def unclassified_room_types(self, known_types: Sequence[str]) -> tuple[str, ...]:
        """Room types no rule can select — reported, never assumed compliant.

        A room whose type is outside the packs' vocabulary matches no
        ``when.roomType`` and derives ``roomIsHabitable == False``, so every
        dimensional minimum skips it. That is the right answer for an unassigned
        room and the wrong answer for a drifted spelling, and the caller cannot
        tell the difference unless we say so.
        """
        known = frozenset(known_types)
        return tuple(sorted({r.type for r in self.model.rooms if r.type not in known}))


def context_from_parts(
    *,
    packs: Sequence[str],
    plot: Any,
    profile: Any,
    model: Any,
    vastu_mode: str = "off",
) -> EvaluationContext:
    """Build a context from the four §6 inputs, in either dataclass or JSON form.

    This is the seam the playbook's ``(model, plot, profile, packs)`` signature
    lands on. It does no derivation: whoever calls it has already computed the
    projection (that is the model layer's ``build_context`` job — see
    :data:`MODEL_FIELDS_NOT_IN_MODEL_CORE` for what the model core still owes).
    """
    return EvaluationContext.from_json(
        {
            "packs": list(packs),
            "vastuMode": vastu_mode,
            "plot": plot.to_json() if isinstance(plot, PlotSummary) else plot,
            "profile": profile.to_json() if isinstance(profile, ProfileSummary) else profile,
            "model": model.to_json() if isinstance(model, ModelSummary) else model,
        }
    )
