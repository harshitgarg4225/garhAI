"""model.py — the HouseModel document and the ProjectDoc the op log folds into.

Mirror of ``packages/model/src/model.ts`` (playbook section 3).

TWO DOCUMENTS, ON PURPOSE:

    :class:`HouseModel` is EXACTLY the section-3 shape — storeys, walls, openings,
    rooms, stairs, slabs, columns, furniture, facade, materials, levels,
    balconies, meta. It is what a design version stores
    (``design_versions.snapshot``), what the solver emits, and what the 3D/sheet
    pipelines consume.

    :class:`ProjectDoc` wraps it with the three things ops 1-5 and 32 mutate but
    which the DB keeps in their own tables (``plots``, ``briefs``,
    ``annotations``): ``{schemaVersion, plot, brief, house, annotations}``.
    ``fold()`` operates on ProjectDoc because the op log is per-project and
    contains plot/brief/annotation ops; a server that folded only HouseModel
    would have nowhere to put ``plot.set_boundary``. ``state_hash`` covers the
    whole ProjectDoc.

EVERY LENGTH IS INTEGER MILLIMETRES. Every area is integer mm^2. Angles are
integer degrees. There is no float anywhere in this document — ``canonical_json``
raises if one appears, which is how the hash stays stable across languages.

PYTHON <-> JSON FIELD NAMES
---------------------------
The wire/DB/hashed form uses the TypeScript field names (camelCase); Python
attributes are snake_case. The mapping is purely mechanical
(:func:`snake_to_camel` / :func:`camel_to_snake`) and is applied by ONE generic
converter (:func:`to_jsonable`) rather than by per-class name tables — there is
no place for a hand-written mapping to drift. ``tests/test_model.py`` asserts the
serialised key set of a fully populated document against
``packages/model/schema/*.schema.json``, so a renamed field fails the build
instead of silently changing every stored ``snapshot_hash``.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

try:  # pragma: no cover - typing only
    from typing import Literal
except ImportError:  # pragma: no cover
    from typing_extensions import Literal  # type: ignore[assignment]

from .geometry import Pt, Polygon, polygon_area_mm2
from .units import UnitsDisplay

__all__ = [
    "JsonValue",
    "JsonObject",
    "SCHEMA_VERSION",
    "ROOM_TYPES",
    "RoomType",
    "ROOM_TYPE_LABELS",
    "HABITABLE_ROOM_TYPES",
    "WET_ROOM_TYPES",
    "is_habitable_room_type",
    "is_wet_room_type",
    "WALL_KINDS",
    "WallKind",
    "OPENING_KINDS",
    "OpeningKind",
    "OPENING_SWINGS",
    "OpeningSwing",
    "STAIR_KINDS",
    "StairKind",
    "DIRECTIONS_4",
    "Direction4",
    "DIRECTIONS_8",
    "Direction8",
    "SLAB_KINDS",
    "SlabKind",
    "RAILING_KINDS",
    "RailingKind",
    "FACADE_COMPONENT_KINDS",
    "FacadeComponentKind",
    "SURFACE_GROUPS",
    "SurfaceGroup",
    "VASTU_MODES",
    "VastuMode",
    "OP_SOURCES",
    "OpSource",
    "ANNOTATION_ANCHOR_KINDS",
    "AnnotationAnchorKind",
    "SizeMm",
    "LevelData",
    "Storey",
    "Wall",
    "Opening",
    "Room",
    "StairLanding",
    "Stair",
    "Slab",
    "Column",
    "FurnitureInstance",
    "Balcony",
    "FacadeComponent",
    "FacadeModel",
    "SurfaceGroupRef",
    "MaterialAssignment",
    "Levels",
    "ModelMeta",
    "HouseModel",
    "Road",
    "RegProfile",
    "PlotDoc",
    "BriefDoc",
    "Annotation",
    "ProjectDoc",
    "Model",
    "DEFAULTS",
    "empty_levels",
    "empty_facade",
    "empty_house_model",
    "empty_plot",
    "empty_brief",
    "empty_project_doc",
    "default_level_data",
    "find_storey",
    "storey_index",
    "find_wall",
    "find_opening",
    "find_room",
    "find_stair",
    "walls_of_storey",
    "rooms_of_storey",
    "openings_of_wall",
    "effective_sill_mm",
    "effective_lintel_mm",
    "building_height_mm",
    "built_up_area_mm2",
    "room_display_name",
    "snake_to_camel",
    "camel_to_snake",
    "to_jsonable",
]

# ---------------------------------------------------------------------------
# JSON value types (brief data, reg-profile overrides, annotation payloads)
# ---------------------------------------------------------------------------

#: Free-form JSON. Numbers inside these are INTEGERS ONLY — see the module
#: docstring and ``validate.check_json_integral``.
JsonValue = Any
JsonObject = Dict[str, Any]

#: The document schema version. Bump => write a migration.
SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Enums — every one spelled out, no string widening anywhere
# ---------------------------------------------------------------------------

#: Room programme types. Drives NBC minimums, furniture sets, Vastu zones,
#: schedules and labels — so it is a closed list, not free text.
ROOM_TYPES: Tuple[str, ...] = (
    "unassigned",
    "living",
    "dining",
    "living_dining",
    "kitchen",
    "utility",
    "store",
    "bedroom_master",
    "bedroom",
    "guest_bedroom",
    "servant_room",
    "study",
    "pooja",
    "bath",
    "wc",
    "bath_wc",
    "dress",
    "passage",
    "lobby",
    "foyer",
    "staircase",
    "balcony",
    "terrace",
    "porch",
    "garage",
    "stilt",
    "shaft",
    "duct",
    "void",
)
RoomType = str

#: Human labels for chips, room tags and drawing labels.
ROOM_TYPE_LABELS: Mapping[str, str] = {
    "unassigned": "Room",
    "living": "Living",
    "dining": "Dining",
    "living_dining": "Living / Dining",
    "kitchen": "Kitchen",
    "utility": "Utility",
    "store": "Store",
    "bedroom_master": "Master Bedroom",
    "bedroom": "Bedroom",
    "guest_bedroom": "Guest Bedroom",
    "servant_room": "Servant Room",
    "study": "Study",
    "pooja": "Pooja",
    "bath": "Bath",
    "wc": "W.C.",
    "bath_wc": "Toilet",
    "dress": "Dress",
    "passage": "Passage",
    "lobby": "Lobby",
    "foyer": "Foyer",
    "staircase": "Staircase",
    "balcony": "Balcony",
    "terrace": "Terrace",
    "porch": "Porch",
    "garage": "Garage",
    "stilt": "Stilt",
    "shaft": "Shaft",
    "duct": "Duct",
    "void": "Void",
}

#: NBC "habitable room" set — these carry the 9.5m^2 / 2.4m width / 1:10 light rules.
HABITABLE_ROOM_TYPES: Tuple[str, ...] = (
    "living",
    "dining",
    "living_dining",
    "bedroom_master",
    "bedroom",
    "guest_bedroom",
    "servant_room",
    "study",
)

#: Wet rooms — drive plumbing-stack scoring and shaft adjacency.
WET_ROOM_TYPES: Tuple[str, ...] = ("kitchen", "bath", "wc", "bath_wc", "utility")


def is_habitable_room_type(t: str) -> bool:
    return t in HABITABLE_ROOM_TYPES


def is_wet_room_type(t: str) -> bool:
    return t in WET_ROOM_TYPES


WALL_KINDS: Tuple[str, ...] = ("external", "internal", "parapet")
WallKind = str

OPENING_KINDS: Tuple[str, ...] = ("door", "window", "ventilator")
OpeningKind = str

#: Section 3, verbatim. Sliding/fixed leaves are a v1.1 concern.
OPENING_SWINGS: Tuple[str, ...] = ("in-left", "in-right", "out-left", "out-right")
OpeningSwing = str

STAIR_KINDS: Tuple[str, ...] = ("straight", "dogleg", "L", "U")
StairKind = str

#: Orthogonal travel direction. MVP walls and stairs are orthogonal.
DIRECTIONS_4: Tuple[str, ...] = ("N", "E", "S", "W")
Direction4 = str

#: 8-way compass, used for facing/Vastu zones and elevation naming.
DIRECTIONS_8: Tuple[str, ...] = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
Direction8 = str

SLAB_KINDS: Tuple[str, ...] = ("floor", "terrace", "plinth", "mumty")
SlabKind = str

RAILING_KINDS: Tuple[str, ...] = ("ms", "glass", "masonry", "ms_glass", "none")
RailingKind = str

FACADE_COMPONENT_KINDS: Tuple[str, ...] = (
    "window_trim",
    "chajja",
    "parapet_profile",
    "cladding_zone",
    "porch",
    "railing",
    "band",
    "louver",
    "entry_feature",
)
FacadeComponentKind = str

#: Surface groups a material can be assigned to (op 29).
SURFACE_GROUPS: Tuple[str, ...] = (
    "external_wall",
    "internal_wall",
    "floor",
    "ceiling",
    "roof",
    "parapet",
    "railing",
    "door",
    "window",
    "cladding",
    "plinth",
    "staircase",
)
SurfaceGroup = str

VASTU_MODES: Tuple[str, ...] = ("off", "advisory", "strict")
VastuMode = str

#: Where an op came from — mirrors ``ops.source`` in the DDL.
OP_SOURCES: Tuple[str, ...] = ("manual", "copilot", "solver", "system")
OpSource = str

#: What a sheet annotation is anchored to (section 7 annotation anchoring).
ANNOTATION_ANCHOR_KINDS: Tuple[str, ...] = (
    "wall",
    "opening",
    "room",
    "stair",
    "column",
    "balcony",
    "sheet",
)
AnnotationAnchorKind = str


# ---------------------------------------------------------------------------
# Python <-> JSON field-name bridge
# ---------------------------------------------------------------------------

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def snake_to_camel(name: str) -> str:
    """``ffl_per_storey_mm`` -> ``fflPerStoreyMm``. The ONLY name mapping there is."""
    head, _, rest = name.partition("_")
    if rest == "":
        return head
    return head + "".join(part[:1].upper() + part[1:] for part in rest.split("_"))


def camel_to_snake(name: str) -> str:
    """``fflPerStoreyMm`` -> ``ffl_per_storey_mm``. Exact inverse for our names."""
    return _CAMEL_BOUNDARY.sub("_", name).lower()


def to_jsonable(value: Any) -> Any:
    """Convert dataclasses / tuples into the JSON shape the hash is taken over.

    Dataclass attributes become camelCase keys; sequences become lists; free-form
    ``dict`` payloads (brief data, facade params, annotation payloads) pass
    through untouched because their keys are user/LLM data, not Python
    identifiers.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out: Dict[str, Any] = {}
        for f in dataclasses.fields(value):
            out[snake_to_camel(f.name)] = to_jsonable(getattr(value, f.name))
        return out
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    raise TypeError(f"to_jsonable: cannot convert {type(value).__name__}")


def _pt_from_json(raw: Any) -> Pt:
    return Pt(int(raw["x"]), int(raw["y"]))


def _polygon_from_json(raw: Any) -> Tuple[Pt, ...]:
    return tuple(_pt_from_json(p) for p in (raw or []))


# ---------------------------------------------------------------------------
# Element dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SizeMm:
    """Rectangular size in mm (columns, landings, catalogue footprints)."""

    x_mm: int
    y_mm: int

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "SizeMm":
        return cls(x_mm=int(raw["xMm"]), y_mm=int(raw["yMm"]))


@dataclass(frozen=True)
class LevelData:
    """Per-storey level data.

    First-class because sections and compliance consume it: a section draws FFL,
    sill and lintel lines straight off these numbers.
    """

    #: Finished floor level of this storey, measured from plot datum (0).
    ffl_mm: int
    #: Structural slab thickness under this storey's FFL.
    slab_thickness_mm: int
    #: Storey-level override of ``Levels.sill_default_mm``, or None to inherit.
    sill_default_mm: Optional[int]
    #: Storey-level override of ``Levels.lintel_default_mm``, or None to inherit.
    lintel_default_mm: Optional[int]

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "LevelData":
        return cls(
            ffl_mm=int(raw["fflMm"]),
            slab_thickness_mm=int(raw["slabThicknessMm"]),
            sill_default_mm=_opt_int(raw.get("sillDefaultMm")),
            lintel_default_mm=_opt_int(raw.get("lintelDefaultMm")),
        )


def _opt_int(v: Any) -> Optional[int]:
    return None if v is None else int(v)


def _opt_str(v: Any) -> Optional[str]:
    return None if v is None else str(v)


@dataclass(frozen=True)
class Storey:
    id: str
    #: Display name: "Ground Floor", "First Floor", "Terrace".
    name: str
    level: LevelData
    #: Floor-to-floor height in mm.
    height_mm: int

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "Storey":
        return cls(
            id=str(raw["id"]),
            name=str(raw["name"]),
            level=LevelData.from_json(raw["level"]),
            height_mm=int(raw["heightMm"]),
        )


@dataclass(frozen=True)
class Wall:
    id: str
    storey_id: str
    #: Centreline start.
    a: Pt
    #: Centreline end.
    b: Pt
    #: 115 / 150 / 200 / 230 / custom, always integer mm.
    thickness_mm: int
    kind: WallKind
    #: Coordination hint for the structural note; not used for geometry.
    load_bearing: bool

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "Wall":
        return cls(
            id=str(raw["id"]),
            storey_id=str(raw["storeyId"]),
            a=_pt_from_json(raw["a"]),
            b=_pt_from_json(raw["b"]),
            thickness_mm=int(raw["thicknessMm"]),
            kind=str(raw["kind"]),
            load_bearing=bool(raw["loadBearing"]),
        )


@dataclass(frozen=True)
class Opening:
    id: str
    wall_id: str
    kind: OpeningKind
    width_mm: int
    height_mm: int
    #: Height of the sill above this storey's FFL. Doors are 0.
    sill_mm: int
    #: Distance along the host wall from ``wall.a`` to the opening CENTRE.
    offset_mm: int
    swing: OpeningSwing
    #: Schedule tag: D1, W2, V1... assigned by the schedule generator.
    tag: Optional[str]

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "Opening":
        return cls(
            id=str(raw["id"]),
            wall_id=str(raw["wallId"]),
            kind=str(raw["kind"]),
            width_mm=int(raw["widthMm"]),
            height_mm=int(raw["heightMm"]),
            sill_mm=int(raw["sillMm"]),
            offset_mm=int(raw["offsetMm"]),
            swing=str(raw["swing"]),
            tag=_opt_str(raw.get("tag")),
        )


@dataclass(frozen=True)
class Room:
    id: str
    storey_id: str
    type: RoomType
    #: Empty string until the user or solver names it; UI falls back to the label.
    name: str
    #: Clear (inside-face) polygon, CCW, integer mm.
    polygon: Tuple[Pt, ...]
    #: Clear floor area of ``polygon``, integer mm^2.
    area_mm2: int
    tags: Tuple[str, ...]
    #: True => solver partial re-solve must return this room untouched.
    locked: bool
    #: Brief/solver target area (op 20), or None.
    target_area_mm2: Optional[int]
    #: Required facing (op 20), or None.
    must_face: Optional[Direction8]

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "Room":
        return cls(
            id=str(raw["id"]),
            storey_id=str(raw["storeyId"]),
            type=str(raw["type"]),
            name=str(raw["name"]),
            polygon=_polygon_from_json(raw["polygon"]),
            area_mm2=int(raw["areaMm2"]),
            tags=tuple(str(t) for t in raw.get("tags", [])),
            locked=bool(raw["locked"]),
            target_area_mm2=_opt_int(raw.get("targetAreaMm2")),
            must_face=_opt_str(raw.get("mustFace")),
        )


@dataclass(frozen=True)
class StairLanding:
    """Landing block of a stair, or None for a single straight flight."""

    width_mm: int
    depth_mm: int

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "StairLanding":
        return cls(width_mm=int(raw["widthMm"]), depth_mm=int(raw["depthMm"]))


@dataclass(frozen=True)
class Stair:
    id: str
    storey_id: str
    kind: StairKind
    #: Bottom-left corner of the stair footprint (first riser, going ``direction``).
    origin: Pt
    #: Direction of travel going UP.
    direction: Direction4
    riser_mm: int
    tread_mm: int
    #: Clear flight width.
    width_mm: int
    #: ``risers_count * riser_mm`` must be the storey height within +/-10mm.
    risers_count: int
    landing: Optional[StairLanding]

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "Stair":
        landing = raw.get("landing")
        return cls(
            id=str(raw["id"]),
            storey_id=str(raw["storeyId"]),
            kind=str(raw["kind"]),
            origin=_pt_from_json(raw["origin"]),
            direction=str(raw["direction"]),
            riser_mm=int(raw["riserMm"]),
            tread_mm=int(raw["treadMm"]),
            width_mm=int(raw["widthMm"]),
            risers_count=int(raw["risersCount"]),
            landing=None if landing is None else StairLanding.from_json(landing),
        )


@dataclass(frozen=True)
class Slab:
    id: str
    storey_id: str
    kind: SlabKind
    #: Outer boundary, CCW.
    polygon: Tuple[Pt, ...]
    thickness_mm: int
    #: Stair wells, double-height voids, shafts.
    cutouts: Tuple[Tuple[Pt, ...], ...]

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "Slab":
        return cls(
            id=str(raw["id"]),
            storey_id=str(raw["storeyId"]),
            kind=str(raw["kind"]),
            polygon=_polygon_from_json(raw["polygon"]),
            thickness_mm=int(raw["thicknessMm"]),
            cutouts=tuple(_polygon_from_json(c) for c in raw.get("cutouts", [])),
        )


@dataclass(frozen=True)
class Column:
    """Coordination-only column: never affects rooms or areas."""

    id: str
    storey_id: str
    #: Centre of the column.
    pt: Pt
    size_mm: SizeMm

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "Column":
        return cls(
            id=str(raw["id"]),
            storey_id=str(raw["storeyId"]),
            pt=_pt_from_json(raw["pt"]),
            size_mm=SizeMm.from_json(raw["sizeMm"]),
        )


@dataclass(frozen=True)
class FurnitureInstance:
    id: str
    storey_id: str
    #: Key into the furniture catalogue (``GET /catalog/furniture``).
    catalog_id: str
    #: Centre of the footprint.
    pt: Pt
    #: Integer degrees CCW; 0 = catalogue default orientation.
    rotation_deg: int

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "FurnitureInstance":
        return cls(
            id=str(raw["id"]),
            storey_id=str(raw["storeyId"]),
            catalog_id=str(raw["catalogId"]),
            pt=_pt_from_json(raw["pt"]),
            rotation_deg=int(raw["rotationDeg"]),
        )


@dataclass(frozen=True)
class Balcony:
    id: str
    storey_id: str
    polygon: Tuple[Pt, ...]
    railing_kind: RailingKind
    railing_height_mm: int
    #: Projection beyond the building line — checked against projection rules.
    projection_mm: int
    slab_thickness_mm: int

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "Balcony":
        return cls(
            id=str(raw["id"]),
            storey_id=str(raw["storeyId"]),
            polygon=_polygon_from_json(raw["polygon"]),
            railing_kind=str(raw["railingKind"]),
            railing_height_mm=int(raw["railingHeightMm"]),
            projection_mm=int(raw["projectionMm"]),
            slab_thickness_mm=int(raw["slabThicknessMm"]),
        )


@dataclass(frozen=True)
class FacadeComponent:
    id: str
    kind: FacadeComponentKind
    storey_id: Optional[str]
    wall_id: Optional[str]
    opening_id: Optional[str]
    #: Generator parameters. Integers only for lengths (projection_mm etc.).
    params: JsonObject

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "FacadeComponent":
        return cls(
            id=str(raw["id"]),
            kind=str(raw["kind"]),
            storey_id=_opt_str(raw.get("storeyId")),
            wall_id=_opt_str(raw.get("wallId")),
            opening_id=_opt_str(raw.get("openingId")),
            params=dict(raw.get("params") or {}),
        )


@dataclass(frozen=True)
class FacadeModel:
    """Facade sub-model. ISOLATED BY DESIGN.

    Nothing in here may affect walls, rooms, openings or areas, so facade churn
    can never break the drawing set or a compliance number.
    """

    #: Kit id, or None when no kit has been applied.
    kit_id: Optional[str]
    #: Variation seed for the generator (integer).
    seed: int
    colorway_id: Optional[str]
    components: Tuple[FacadeComponent, ...]

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "FacadeModel":
        return cls(
            kit_id=_opt_str(raw.get("kitId")),
            seed=int(raw["seed"]),
            colorway_id=_opt_str(raw.get("colorwayId")),
            components=tuple(FacadeComponent.from_json(c) for c in raw.get("components", [])),
        )


@dataclass(frozen=True)
class SurfaceGroupRef:
    group: SurfaceGroup
    #: Narrow the assignment to one storey, or None for the whole building.
    storey_id: Optional[str]
    #: Narrow to a single element (wall/opening/facade component), or None.
    element_id: Optional[str]

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "SurfaceGroupRef":
        return cls(
            group=str(raw["group"]),
            storey_id=_opt_str(raw.get("storeyId")),
            element_id=_opt_str(raw.get("elementId")),
        )


@dataclass(frozen=True)
class MaterialAssignment:
    id: str
    target: SurfaceGroupRef
    #: Key into the material catalogue.
    material_id: str

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "MaterialAssignment":
        return cls(
            id=str(raw["id"]),
            target=SurfaceGroupRef.from_json(raw["target"]),
            material_id=str(raw["materialId"]),
        )


@dataclass(frozen=True)
class Levels:
    """Building-wide levels.

    First-class because sections, elevations and the ventilation/height rules all
    read them.
    """

    #: Plinth height above ground level.
    plinth_mm: int
    #: FFL of each storey, index-aligned with ``storeys``.
    ffl_per_storey_mm: Tuple[int, ...]
    #: Default window sill height (NBC/city packs expect 900).
    sill_default_mm: int
    #: Default lintel height above FFL (2100 typical).
    lintel_default_mm: int
    #: Terrace parapet height (1000 typical, city packs may raise it).
    parapet_mm: int

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "Levels":
        return cls(
            plinth_mm=int(raw["plinthMm"]),
            ffl_per_storey_mm=tuple(int(v) for v in raw.get("fflPerStoreyMm", [])),
            sill_default_mm=int(raw["sillDefaultMm"]),
            lintel_default_mm=int(raw["lintelDefaultMm"]),
            parapet_mm=int(raw["parapetMm"]),
        )


@dataclass(frozen=True)
class ModelMeta:
    units_display: UnitsDisplay
    #: Reference to the regulatory profile in use (``plots.reg_profile``), or None.
    reg_profile_ref: Optional[str]
    #: Reference to the brief this model was generated from, or None.
    brief_ref: Optional[str]

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "ModelMeta":
        return cls(
            units_display=str(raw["unitsDisplay"]),  # type: ignore[arg-type]
            reg_profile_ref=_opt_str(raw.get("regProfileRef")),
            brief_ref=_opt_str(raw.get("briefRef")),
        )


@dataclass(frozen=True)
class HouseModel:
    """The section-3 house document.

    Arrays are kept in a canonical order (see ``finalize`` in
    :mod:`garh_model.fold`) so that two folds of the same op log serialise
    identically.
    """

    schema_version: int
    #: Ordered, ground floor = index 0.
    storeys: Tuple[Storey, ...]
    walls: Tuple[Wall, ...]
    openings: Tuple[Opening, ...]
    #: DERIVED from walls by planar subdivision, but persisted with stable ids.
    rooms: Tuple[Room, ...]
    stairs: Tuple[Stair, ...]
    #: DERIVED per storey.
    slabs: Tuple[Slab, ...]
    columns: Tuple[Column, ...]
    furniture: Tuple[FurnitureInstance, ...]
    facade: FacadeModel
    materials: Tuple[MaterialAssignment, ...]
    levels: Levels
    balconies: Tuple[Balcony, ...]
    meta: ModelMeta

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "HouseModel":
        return cls(
            schema_version=int(raw["schemaVersion"]),
            storeys=tuple(Storey.from_json(s) for s in raw.get("storeys", [])),
            walls=tuple(Wall.from_json(w) for w in raw.get("walls", [])),
            openings=tuple(Opening.from_json(o) for o in raw.get("openings", [])),
            rooms=tuple(Room.from_json(r) for r in raw.get("rooms", [])),
            stairs=tuple(Stair.from_json(s) for s in raw.get("stairs", [])),
            slabs=tuple(Slab.from_json(s) for s in raw.get("slabs", [])),
            columns=tuple(Column.from_json(c) for c in raw.get("columns", [])),
            furniture=tuple(FurnitureInstance.from_json(f) for f in raw.get("furniture", [])),
            facade=FacadeModel.from_json(raw["facade"]),
            materials=tuple(MaterialAssignment.from_json(m) for m in raw.get("materials", [])),
            levels=Levels.from_json(raw["levels"]),
            balconies=tuple(Balcony.from_json(b) for b in raw.get("balconies", [])),
            meta=ModelMeta.from_json(raw["meta"]),
        )

    def to_json(self) -> JsonObject:
        result: JsonObject = to_jsonable(self)
        return result


# ---------------------------------------------------------------------------
# Plot / brief / annotations — the rest of the folded project document
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Road:
    """A road on one edge of the plot boundary (drives setback tables)."""

    #: Index of the boundary edge ``boundary[i] -> boundary[i+1]``.
    edge_index: int
    #: Road width in mm, or None for "no road on this edge".
    width_mm: Optional[int]
    name: Optional[str]

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "Road":
        return cls(
            edge_index=int(raw["edgeIndex"]),
            width_mm=_opt_int(raw.get("widthMm")),
            name=_opt_str(raw.get("name")),
        )


@dataclass(frozen=True)
class RegProfile:
    """The regulatory profile: a city pack plus per-project overrides."""

    #: Rule pack id: 'blr' | 'ncr' | 'hyd' | ... (packs live in ``rulepacks/``).
    city_pack: Optional[str]
    #: Per-project overrides (logged in ``audit_log``).
    overrides: JsonObject

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "RegProfile":
        return cls(
            city_pack=_opt_str(raw.get("cityPack")),
            overrides=dict(raw.get("overrides") or {}),
        )


@dataclass(frozen=True)
class PlotDoc:
    #: Plot boundary, CCW, plot-local mm, origin at the SW corner.
    boundary: Tuple[Pt, ...]
    #: Integer degrees: rotation of TRUE north from +Y, measured clockwise.
    north_deg: int
    roads: Tuple[Road, ...]
    reg_profile: RegProfile
    #: How the boundary got here: 'manual' | 'dxf' | 'seed'.
    source: str

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "PlotDoc":
        return cls(
            boundary=_polygon_from_json(raw.get("boundary")),
            north_deg=int(raw["northDeg"]),
            roads=tuple(Road.from_json(r) for r in raw.get("roads", [])),
            reg_profile=RegProfile.from_json(raw["regProfile"]),
            source=str(raw["source"]),
        )


@dataclass(frozen=True)
class BriefDoc:
    #: Free-form brief data; the shape is owned by the brief schema, not geometry.
    data: JsonObject
    vastu_mode: VastuMode
    #: 0-100 completeness meter.
    completeness: int

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "BriefDoc":
        return cls(
            data=dict(raw.get("data") or {}),
            vastu_mode=str(raw["vastuMode"]),
            completeness=int(raw["completeness"]),
        )


@dataclass(frozen=True)
class Annotation:
    """A sheet annotation anchored to a model element."""

    id: str
    sheet_id: str
    anchor_element_id: Optional[str]
    anchor_kind: AnnotationAnchorKind
    payload: JsonObject
    #: True after a solver re-run destroyed the anchor -> Review Tray.
    orphaned: bool

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "Annotation":
        return cls(
            id=str(raw["id"]),
            sheet_id=str(raw["sheetId"]),
            anchor_element_id=_opt_str(raw.get("anchorElementId")),
            anchor_kind=str(raw["anchorKind"]),
            payload=dict(raw.get("payload") or {}),
            orphaned=bool(raw["orphaned"]),
        )


@dataclass(frozen=True)
class ProjectDoc:
    """THE FOLDED DOCUMENT.

    ``fold(model, op)`` takes and returns this; ``state_hash(doc)`` hashes exactly
    this.
    """

    schema_version: int
    plot: PlotDoc
    brief: BriefDoc
    house: HouseModel
    annotations: Tuple[Annotation, ...]

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "ProjectDoc":
        return cls(
            schema_version=int(raw["schemaVersion"]),
            plot=PlotDoc.from_json(raw["plot"]),
            brief=BriefDoc.from_json(raw["brief"]),
            house=HouseModel.from_json(raw["house"]),
            annotations=tuple(Annotation.from_json(a) for a in raw.get("annotations", [])),
        )

    def to_json(self) -> JsonObject:
        """The exact JSON shape ``state_hash`` is taken over."""
        result: JsonObject = to_jsonable(self)
        return result


#: Alias for call sites that read better as "the model".
Model = ProjectDoc


# ---------------------------------------------------------------------------
# Defaults / constructors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Defaults:
    """Indian residential defaults, all integer mm. Cited in assumption chips."""

    storey_height_mm: int = 3000
    slab_thickness_mm: int = 150
    plinth_mm: int = 600
    sill_default_mm: int = 900
    lintel_default_mm: int = 2100
    parapet_mm: int = 1000
    external_wall_thickness_mm: int = 230
    internal_wall_thickness_mm: int = 115
    parapet_thickness_mm: int = 115
    door_width_mm: int = 900
    door_height_mm: int = 2100
    bath_door_width_mm: int = 750
    window_width_mm: int = 1200
    window_height_mm: int = 1200
    ventilator_width_mm: int = 600
    ventilator_height_mm: int = 450
    ventilator_sill_mm: int = 1800
    riser_mm: int = 165
    tread_mm: int = 275
    stair_width_mm: int = 900
    railing_height_mm: int = 1000
    balcony_projection_mm: int = 900
    column_size_mm: SizeMm = field(default_factory=lambda: SizeMm(x_mm=230, y_mm=230))


DEFAULTS = _Defaults()


def empty_levels() -> Levels:
    return Levels(
        plinth_mm=DEFAULTS.plinth_mm,
        ffl_per_storey_mm=(),
        sill_default_mm=DEFAULTS.sill_default_mm,
        lintel_default_mm=DEFAULTS.lintel_default_mm,
        parapet_mm=DEFAULTS.parapet_mm,
    )


def empty_facade() -> FacadeModel:
    return FacadeModel(kit_id=None, seed=0, colorway_id=None, components=())


def empty_house_model(units_display: str = "ft-in") -> HouseModel:
    return HouseModel(
        schema_version=SCHEMA_VERSION,
        storeys=(),
        walls=(),
        openings=(),
        rooms=(),
        stairs=(),
        slabs=(),
        columns=(),
        furniture=(),
        facade=empty_facade(),
        materials=(),
        levels=empty_levels(),
        balconies=(),
        meta=ModelMeta(units_display=units_display, reg_profile_ref=None, brief_ref=None),
    )


def empty_plot() -> PlotDoc:
    return PlotDoc(
        boundary=(),
        north_deg=0,
        roads=(),
        reg_profile=RegProfile(city_pack=None, overrides={}),
        source="manual",
    )


def empty_brief() -> BriefDoc:
    return BriefDoc(data={}, vastu_mode="off", completeness=0)


def empty_project_doc(units_display: str = "ft-in") -> ProjectDoc:
    """The initial state every op log folds from."""
    return ProjectDoc(
        schema_version=SCHEMA_VERSION,
        plot=empty_plot(),
        brief=empty_brief(),
        house=empty_house_model(units_display),
        annotations=(),
    )


def default_level_data(ffl_mm: int) -> LevelData:
    """Default level data for a storey at ``ffl_mm``."""
    return LevelData(
        ffl_mm=ffl_mm,
        slab_thickness_mm=DEFAULTS.slab_thickness_mm,
        sill_default_mm=None,
        lintel_default_mm=None,
    )


# ---------------------------------------------------------------------------
# Lookups & derived reads (pure, no mutation)
# ---------------------------------------------------------------------------


def find_storey(house: HouseModel, storey_id: str) -> Optional[Storey]:
    return next((s for s in house.storeys if s.id == storey_id), None)


def storey_index(house: HouseModel, storey_id: str) -> int:
    for i, s in enumerate(house.storeys):
        if s.id == storey_id:
            return i
    return -1


def find_wall(house: HouseModel, wall_id: str) -> Optional[Wall]:
    return next((w for w in house.walls if w.id == wall_id), None)


def find_opening(house: HouseModel, opening_id: str) -> Optional[Opening]:
    return next((o for o in house.openings if o.id == opening_id), None)


def find_room(house: HouseModel, room_id: str) -> Optional[Room]:
    return next((r for r in house.rooms if r.id == room_id), None)


def find_stair(house: HouseModel, stair_id: str) -> Optional[Stair]:
    return next((s for s in house.stairs if s.id == stair_id), None)


def walls_of_storey(house: HouseModel, storey_id: str) -> List[Wall]:
    return [w for w in house.walls if w.storey_id == storey_id]


def rooms_of_storey(house: HouseModel, storey_id: str) -> List[Room]:
    return [r for r in house.rooms if r.storey_id == storey_id]


def openings_of_wall(house: HouseModel, wall_id: str) -> List[Opening]:
    return [o for o in house.openings if o.wall_id == wall_id]


def effective_sill_mm(house: HouseModel, storey_id: str) -> int:
    """Effective sill default for a storey: storey override, else building default."""
    storey = find_storey(house, storey_id)
    if storey is not None and storey.level.sill_default_mm is not None:
        return storey.level.sill_default_mm
    return house.levels.sill_default_mm


def effective_lintel_mm(house: HouseModel, storey_id: str) -> int:
    """Effective lintel height for a storey."""
    storey = find_storey(house, storey_id)
    if storey is not None and storey.level.lintel_default_mm is not None:
        return storey.level.lintel_default_mm
    return house.levels.lintel_default_mm


def building_height_mm(house: HouseModel) -> int:
    """Sum of storey heights + plinth — the height a ``height_max`` rule checks."""
    total = house.levels.plinth_mm
    for s in house.storeys:
        total += s.height_mm
    return total


def built_up_area_mm2(house: HouseModel) -> int:
    """Total built-up area = sum of floor-slab areas minus cutouts, in mm^2."""
    total = 0
    for slab in house.slabs:
        if slab.kind != "floor":
            continue
        total += polygon_area_mm2(slab.polygon)
        for cut in slab.cutouts:
            total -= polygon_area_mm2(cut)
    return total


def room_display_name(room: Room, ordinal: Optional[int] = None) -> str:
    """Explicit name, else the type label, else "Room N"."""
    if room.name != "":
        return room.name
    label = ROOM_TYPE_LABELS[room.type]
    if room.type == "unassigned" and ordinal is not None:
        return f"{label} {ordinal}"
    return label
