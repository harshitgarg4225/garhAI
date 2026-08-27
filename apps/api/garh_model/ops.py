"""ops.py — THE OP TAXONOMY (playbook section 4). 32 ops, no more, no less.

Mirror of ``packages/model/src/ops.ts``.

GOLDEN RULE 1: the op is the atom. The UI never mutates state; it dispatches ops.
The solver emits ops. The copilot emits ops. If a feature cannot be expressed
here, the feature gets redesigned — the taxonomy does not grow casually.

TWO RULES THAT MAKE ``fold`` PURE AND ``replay`` DETERMINISTIC:

1. Ops that CREATE an element carry that element's id in the payload. Ids are
   minted by the op *producer* (``new_id('wall')``), never inside fold.
2. Ops carry no timestamps, no user names, no random values.
   ``solver.apply_option`` carries its expanded op list so replaying it does not
   need the original solver job.

PAYLOADS ARE THE WIRE FORM
--------------------------
Unlike the document (:mod:`garh_model.model`, snake_case attributes on frozen
dataclasses), an op payload is kept as a plain ``dict`` with the **camelCase JSON
keys**. That is deliberate: an op arrives as ``ops.payload`` jsonb from Postgres
or as JSON from the client, is validated, folded and stored again without ever
being re-keyed. One shape, no conversion, nothing to drift.

ABSENT vs NULL — read this once
-------------------------------
TypeScript distinguishes ``undefined`` (field not supplied) from ``null``
(explicitly cleared) and several ops rely on it: ``room.set_target`` with
``targetAreaMm2: null`` CLEARS the target, while omitting the key leaves it
alone. JSON has no ``undefined``, so the wire convention — which this mirror
follows exactly — is:

    key absent  == TypeScript ``undefined`` == "leave unchanged"
    key present with value ``None`` == JSON ``null`` == "clear it"

So never write ``payload["mustFace"] = None`` to mean "unset"; omit the key.

``OP_CATALOG`` at the bottom is a MACHINE-READABLE description of every op:
section 10 requires the copilot system prompt to be generated from it, so it must
be data (not doc comments). It is also what ``schema/ops.schema.json`` mirrors.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .model import (
    ANNOTATION_ANCHOR_KINDS,
    DIRECTIONS_4,
    DIRECTIONS_8,
    OPENING_KINDS,
    OPENING_SWINGS,
    RAILING_KINDS,
    ROOM_TYPES,
    STAIR_KINDS,
    SURFACE_GROUPS,
    WALL_KINDS,
)

__all__ = [
    "OpType",
    "OpCategory",
    "OpFieldType",
    "Op",
    "OpGroup",
    "op",
    "COLUMN_ACTIONS",
    "FURNITURE_ACTIONS",
    "BALCONY_ACTIONS",
    "ANNOTATION_ACTIONS",
    "OP_TYPES",
    "OP_CATALOG",
    "OpFieldSpec",
    "OpSpec",
    "EXAMPLE_IDS",
    "example_id",
    "get_op_spec",
    "is_op_type",
    "is_op",
    "copilot_op_specs",
    "render_op_catalog_for_prompt",
    "op_type_of",
    "payload_of",
]

#: An op type tag, e.g. ``'wall.add'``. Closed set — see :data:`OP_TYPES`.
OpType = str

#: Grouping used by the copilot prompt and the UI command palette.
OpCategory = str

#: Field kind in :class:`OpFieldSpec` — drives prompt rendering and validation.
OpFieldType = str


# ---------------------------------------------------------------------------
# Op envelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Op:
    """One mutation: ``{type, payload}`` plus the optional envelope fields.

    ``group_id``   undo/redo operates on GROUPS, not single ops. A copilot edit
                   or ``solver.apply_option`` is one group.
    ``client_op_id`` client-generated idempotency key; the server dedupes on it.
    ``source``     provenance ('manual' | 'copilot' | 'solver' | 'system'). Set
                   by the server; :func:`garh_model.fold.fold` ignores it.
    """

    type: OpType
    #: camelCase JSON keys — see the module docstring on absent vs null.
    payload: Mapping[str, Any]
    group_id: str | None = None
    client_op_id: str | None = None
    source: str | None = None

    # -- payload access helpers (so call sites read like the TypeScript) ----
    def get(self, key: str, default: Any = None) -> Any:
        """``payload[key]`` or ``default`` when the key is ABSENT.

        A key present with the value ``None`` returns ``None``, not ``default``:
        that is JSON ``null`` and it means something.
        """
        return self.payload.get(key, default)

    def has(self, key: str) -> bool:
        """True when the payload carries the key at all (``null`` counts)."""
        return key in self.payload

    def with_group(self, group_id: str | None) -> Op:
        """Copy with ``group_id`` stamped on — mirrors ``{...op, groupId}``."""
        return replace(self, group_id=group_id)

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Op:
        """Parse the wire form. Raises :class:`TypeError` on a non-op shape."""
        if not isinstance(raw, Mapping):
            raise TypeError("An op must be an object { type, payload }.")
        payload = raw.get("payload")
        if not isinstance(payload, Mapping):
            raise TypeError(f"Op {raw.get('type')!r} needs an object payload.")
        return cls(
            type=str(raw["type"]),
            payload=dict(payload),
            group_id=_opt_str(raw.get("groupId")),
            client_op_id=_opt_str(raw.get("clientOpId")),
            source=_opt_str(raw.get("source")),
        )

    def to_json(self) -> dict[str, Any]:
        """The wire form. Envelope fields are omitted when unset."""
        out: dict[str, Any] = {"type": self.type, "payload": dict(self.payload)}
        if self.group_id is not None:
            out["groupId"] = self.group_id
        if self.client_op_id is not None:
            out["clientOpId"] = self.client_op_id
        if self.source is not None:
            out["source"] = self.source
        return out


def _opt_str(v: Any) -> str | None:
    return None if v is None else str(v)


def op(op_type: OpType, **payload: Any) -> Op:
    """Terse constructor: ``op('wall.delete', wallId=w)``.

    Keyword arguments become payload keys VERBATIM, so pass camelCase. Keys are
    only included when supplied — pass ``None`` explicitly for a JSON ``null``.
    """
    return Op(type=op_type, payload=payload)


def op_type_of(value: Any) -> str | None:
    """``type`` of an :class:`Op` or of a raw wire dict, else ``None``."""
    if isinstance(value, Op):
        return value.type
    if isinstance(value, Mapping):
        raw = value.get("type")
        return raw if isinstance(raw, str) else None
    return None


def payload_of(value: Any) -> Mapping[str, Any] | None:
    """``payload`` of an :class:`Op` or of a raw wire dict, else ``None``."""
    if isinstance(value, Op):
        return value.payload
    if isinstance(value, Mapping):
        raw = value.get("payload")
        return raw if isinstance(raw, Mapping) else None
    return None


@dataclass(frozen=True)
class OpGroup:
    """A batch applied atomically under one ``group_id``."""

    group_id: str
    ops: tuple[Op, ...]


# ---------------------------------------------------------------------------
# Combined-op action enums (ops 24 / 25 / 26 / 32)
# ---------------------------------------------------------------------------

COLUMN_ACTIONS: tuple[str, ...] = ("add", "move", "delete")
FURNITURE_ACTIONS: tuple[str, ...] = ("place", "transform", "delete")
BALCONY_ACTIONS: tuple[str, ...] = ("add", "edit", "delete")
ANNOTATION_ACTIONS: tuple[str, ...] = ("add", "edit", "delete")


# ---------------------------------------------------------------------------
# OP_CATALOG — machine-readable op description (section 10 generates the prompt)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpFieldSpec:
    name: str
    type: OpFieldType
    required: bool
    #: Physical unit, so a prompt/validator never has to guess.
    units: str | None
    description: str
    #: For ``type='id'`` — which id namespace the value must belong to.
    id_type: str | None = None
    #: For ``type='enum'`` — the exact allowed values.
    enum_values: tuple[str, ...] | None = None
    #: May the field be explicitly null?
    nullable: bool = False


@dataclass(frozen=True)
class OpSpec:
    #: Row number in playbook section 4 (1-32).
    number: int
    type: OpType
    category: OpCategory
    #: Imperative title: "Add wall".
    title: str
    #: One-line human summary — goes verbatim into the copilot system prompt.
    summary: str
    payload: tuple[OpFieldSpec, ...]
    #: For combined ops (24/25/26/32): the values ``payload.action`` accepts.
    actions: tuple[str, ...] | None
    creates: tuple[str, ...]
    destroys: tuple[str, ...]
    #: May the copilot emit this op? (solver expansion and plot edits may not.)
    copilot: bool
    #: Must always be applied inside a group (atomic).
    atomic: bool
    #: A valid, hand-checked example — the schema tests fold every one of these.
    example: Op


_CROCKFORD_OK = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def example_id(element_type: str, tag: str) -> str:
    """Stable, readable, VALID element id: ``type_01J0000000000000000000TAG``."""
    clean = "".join(ch for ch in tag.upper() if ch in _CROCKFORD_OK)
    body = ("01J" + "0" * 26)[: 26 - len(clean)] + clean
    return f"{element_type}_{body}"


#: Stable example ids so fixtures and docs read the same every time.
EXAMPLE_IDS: Mapping[str, str] = {
    "storey0": example_id("storey", "GF"),
    "storey1": example_id("storey", "FF"),
    "wall1": example_id("wall", "W1"),
    "wall2": example_id("wall", "W2"),
    "opening1": example_id("opening", "D1"),
    "room1": example_id("room", "R1"),
    "stair1": example_id("stair", "S1"),
    "column1": example_id("column", "C1"),
    "furniture1": example_id("furniture", "F1"),
    "balcony1": example_id("balcony", "B1"),
    "facadeComp1": example_id("facadecomp", "FC1"),
    "material1": example_id("material", "M1"),
    "annotation1": example_id("annotation", "A1"),
    "sheet1": example_id("sheet", "SH1"),
}


def _int_mm(name: str, description: str, required: bool = True) -> OpFieldSpec:
    return OpFieldSpec(
        name=name, type="int-mm", required=required, units="mm", description=description
    )


def _int(
    name: str,
    description: str,
    units: str | None = "count",
    required: bool = True,
) -> OpFieldSpec:
    return OpFieldSpec(
        name=name, type="int", required=required, units=units, description=description
    )


def _id(name: str, id_type: str, description: str, required: bool = True) -> OpFieldSpec:
    return OpFieldSpec(
        name=name,
        type="id",
        required=required,
        units=None,
        id_type=id_type,
        description=description,
    )


def _pt(name: str, description: str, required: bool = True) -> OpFieldSpec:
    return OpFieldSpec(name=name, type="pt", required=required, units="mm", description=description)


def _polygon(name: str, description: str, required: bool = True) -> OpFieldSpec:
    return OpFieldSpec(
        name=name, type="polygon", required=required, units="mm", description=description
    )


def _enum(
    name: str,
    enum_values: Sequence[str],
    description: str,
    required: bool = True,
    nullable: bool = False,
) -> OpFieldSpec:
    return OpFieldSpec(
        name=name,
        type="enum",
        required=required,
        units=None,
        enum_values=tuple(enum_values),
        nullable=nullable,
        description=description,
    )


def _string(
    name: str, description: str, required: bool = True, nullable: bool = False
) -> OpFieldSpec:
    return OpFieldSpec(
        name=name,
        type="string",
        required=required,
        units=None,
        nullable=nullable,
        description=description,
    )


def _bool(name: str, description: str, required: bool = False) -> OpFieldSpec:
    return OpFieldSpec(
        name=name, type="bool", required=required, units=None, description=description
    )


def _json(name: str, description: str, required: bool = True) -> OpFieldSpec:
    return OpFieldSpec(
        name=name, type="json", required=required, units=None, description=description
    )


#: THE SINGLE SOURCE OF TRUTH FOR OP COVERAGE.
#:
#: Consumers:
#:  - ``apps/api`` copilot: system prompt from :func:`render_op_catalog_for_prompt`
#:  - ``packages/model/schema/ops.schema.json``: kept in lockstep, asserted by a test
#:  - ``tests/test_ops.py``: every ``OpType`` appears exactly once and every
#:    ``example`` folds cleanly onto a demo document
OP_CATALOG: tuple[OpSpec, ...] = (
    OpSpec(
        number=1,
        type="plot.set_boundary",
        category="plot",
        title="Set plot boundary",
        summary="Replace the plot boundary polygon (CCW, integer mm, closed, area > 0).",
        payload=(
            _polygon(
                "polygon",
                "Plot boundary ring, origin at the plot SW corner. An EMPTY array clears the "
                "boundary (the undo form of this op); anything else must be a closed ring with "
                "area > 0.",
            ),
            _string("source", "How it was captured: 'manual' | 'dxf' | 'seed'.", False),
        ),
        actions=None,
        creates=(),
        destroys=(),
        copilot=False,
        atomic=False,
        example=op(
            "plot.set_boundary",
            polygon=[
                {"x": 0, "y": 0},
                {"x": 9144, "y": 0},
                {"x": 9144, "y": 12192},
                {"x": 0, "y": 12192},
            ],
            source="manual",
        ),
    ),
    OpSpec(
        number=2,
        type="plot.set_north",
        category="plot",
        title="Set north",
        summary="Rotate true north. Integer degrees 0-359, clockwise from +Y.",
        payload=(
            OpFieldSpec(
                name="deg",
                type="int-deg",
                required=True,
                units="deg",
                description="Rotation of true north from +Y, clockwise, 0-359.",
            ),
        ),
        actions=None,
        creates=(),
        destroys=(),
        copilot=True,
        atomic=False,
        example=op("plot.set_north", deg=0),
    ),
    OpSpec(
        number=3,
        type="plot.set_road",
        category="plot",
        title="Set road on plot edge",
        summary="Attach or remove the abutting road width on one plot edge (drives setback tables).",
        payload=(
            _int("edgeIndex", "Boundary edge index (boundary[i] -> boundary[i+1]).", "index"),
            OpFieldSpec(
                name="widthMm",
                type="int-mm",
                required=True,
                units="mm",
                nullable=True,
                description="Road width, or null for no road.",
            ),
            _string("name", "Road name for the site plan.", False, True),
        ),
        actions=None,
        creates=(),
        destroys=(),
        copilot=True,
        atomic=False,
        example=op("plot.set_road", edgeIndex=0, widthMm=9000, name="9m Road"),
    ),
    OpSpec(
        number=4,
        type="plot.set_reg_profile",
        category="plot",
        title="Set regulatory profile",
        summary="Choose the city rule pack and per-project overrides. Overrides are audited.",
        payload=(
            _string("cityPack", "Rule pack id: 'blr' | 'ncr' | 'hyd', or null.", True, True),
            _json("overrides", "Per-project overrides of pack values."),
        ),
        actions=None,
        creates=(),
        destroys=(),
        copilot=False,
        atomic=False,
        example=op("plot.set_reg_profile", cityPack="blr", overrides={}),
    ),
    OpSpec(
        number=5,
        type="brief.update",
        category="brief",
        title="Update brief",
        summary="Apply an RFC 7386 JSON merge patch to the brief data (null deletes a key).",
        payload=(
            _json("patch", "RFC 7386 merge patch applied to brief.data."),
            _enum("vastuMode", ("off", "advisory", "strict"), "Vastu mode.", False),
            _int("completeness", "Brief completeness meter, 0-100.", "count", False),
        ),
        actions=None,
        creates=(),
        destroys=(),
        copilot=True,
        atomic=False,
        example=op("brief.update", patch={"bedrooms": 3}),
    ),
    OpSpec(
        number=6,
        type="storey.add",
        category="storey",
        title="Add storey",
        summary="Insert a storey at `index` (0 = ground). FFLs re-derive from storey heights.",
        payload=(
            _id("id", "storey", "Id for the new storey (minted by the caller)."),
            _int("index", "Insert position; 0 = ground floor.", "index"),
            _string("name", 'Display name, e.g. "First Floor".', False),
            _int_mm("heightMm", "Floor-to-floor height."),
            OpFieldSpec(
                name="level",
                type="level-data",
                required=False,
                units="mm",
                description=(
                    "Level data override { fflMm, slabThicknessMm, sillDefaultMm, lintelDefaultMm }."
                ),
            ),
        ),
        actions=None,
        creates=("storey",),
        destroys=(),
        copilot=True,
        atomic=False,
        example=op(
            "storey.add",
            id=EXAMPLE_IDS["storey1"],
            index=1,
            name="First Floor",
            heightMm=3000,
        ),
    ),
    OpSpec(
        number=7,
        type="storey.remove",
        category="storey",
        title="Remove storey",
        summary="Remove the storey at `index` and everything on it (walls, openings, rooms, stairs).",
        payload=(_int("index", "Storey index to remove.", "index"),),
        actions=None,
        creates=(),
        destroys=(
            "storey",
            "wall",
            "opening",
            "room",
            "stair",
            "slab",
            "column",
            "furniture",
            "balcony",
        ),
        copilot=True,
        atomic=False,
        example=op("storey.remove", index=1),
    ),
    OpSpec(
        number=8,
        type="storey.set_height",
        category="storey",
        title="Set storey height",
        summary="Change one storey floor-to-floor height; FFLs above it shift, stairs re-check.",
        payload=(
            _id("storeyId", "storey", "Storey to change."),
            _int_mm("heightMm", "New floor-to-floor height."),
        ),
        actions=None,
        creates=(),
        destroys=(),
        copilot=True,
        atomic=False,
        example=op("storey.set_height", storeyId=EXAMPLE_IDS["storey0"], heightMm=3200),
    ),
    OpSpec(
        number=9,
        type="wall.add",
        category="wall",
        title="Add wall",
        summary="Add a wall centreline on a storey. Rooms re-detect afterwards.",
        payload=(
            _id("id", "wall", "Id for the new wall."),
            _id("storeyId", "storey", "Host storey."),
            _pt("a", "Centreline start."),
            _pt("b", "Centreline end."),
            _int_mm("thicknessMm", "Wall thickness: 115 / 150 / 200 / 230 typical."),
            _enum("kind", WALL_KINDS, "Wall kind."),
            _bool("loadBearing", "Structural coordination hint."),
        ),
        actions=None,
        creates=("wall",),
        destroys=(),
        copilot=True,
        atomic=False,
        example=op(
            "wall.add",
            id=EXAMPLE_IDS["wall1"],
            storeyId=EXAMPLE_IDS["storey0"],
            a={"x": 0, "y": 0},
            b={"x": 4000, "y": 0},
            thicknessMm=230,
            kind="external",
        ),
    ),
    OpSpec(
        number=10,
        type="wall.move",
        category="wall",
        title="Move wall",
        summary="Set both wall endpoints. Joins re-resolve and rooms re-detect (ids preserved).",
        payload=(
            _id("wallId", "wall", "Wall to move."),
            _pt("a", "New centreline start."),
            _pt("b", "New centreline end."),
        ),
        actions=None,
        creates=(),
        destroys=(),
        copilot=True,
        atomic=False,
        example=op(
            "wall.move",
            wallId=EXAMPLE_IDS["wall1"],
            a={"x": 0, "y": 115},
            b={"x": 4000, "y": 115},
        ),
    ),
    OpSpec(
        number=11,
        type="wall.split",
        category="wall",
        title="Split wall",
        summary="Split a wall at `atMm` from its `a` end into two walls; openings re-host by position.",
        payload=(
            _id("wallId", "wall", "Wall to split."),
            _int_mm("atMm", "Distance from `a` at which to split (0 < atMm < length)."),
            _id("newWallId", "wall", "Id for the second half."),
        ),
        actions=None,
        creates=("wall",),
        destroys=(),
        copilot=True,
        atomic=False,
        example=op(
            "wall.split", wallId=EXAMPLE_IDS["wall1"], atMm=2000, newWallId=EXAMPLE_IDS["wall2"]
        ),
    ),
    OpSpec(
        number=12,
        type="wall.delete",
        category="wall",
        title="Delete wall",
        summary=(
            "Delete a wall and every opening hosted on it. Rooms re-detect (merged rooms lose one id)."
        ),
        payload=(_id("wallId", "wall", "Wall to delete."),),
        actions=None,
        creates=(),
        destroys=("wall", "opening"),
        copilot=True,
        atomic=False,
        example=op("wall.delete", wallId=EXAMPLE_IDS["wall2"]),
    ),
    OpSpec(
        number=13,
        type="wall.set_thickness",
        category="wall",
        title="Set wall thickness",
        summary="Change wall thickness. Room clear areas shrink/grow accordingly.",
        payload=(
            _id("wallId", "wall", "Wall to change."),
            _int_mm("thicknessMm", "New thickness."),
        ),
        actions=None,
        creates=(),
        destroys=(),
        copilot=True,
        atomic=False,
        example=op("wall.set_thickness", wallId=EXAMPLE_IDS["wall1"], thicknessMm=115),
    ),
    OpSpec(
        number=14,
        type="opening.add",
        category="opening",
        title="Add opening",
        summary=(
            "Host a door/window/ventilator on a wall. `offsetMm` is to the opening CENTRE from "
            "wall.a; must keep 115mm end margins."
        ),
        payload=(
            _id("id", "opening", "Id for the new opening."),
            _id("wallId", "wall", "Host wall."),
            _enum("kind", OPENING_KINDS, "Opening kind."),
            _int_mm("widthMm", "Clear width."),
            _int_mm("heightMm", "Clear height."),
            _int_mm("sillMm", "Sill height above FFL (0 for doors)."),
            _int_mm("offsetMm", "Distance along the wall from `a` to the opening centre."),
            _enum("swing", OPENING_SWINGS, "Leaf swing."),
            _string(
                "tag",
                "Schedule tag (D1/W2/V1); usually assigned by the schedule generator.",
                False,
                True,
            ),
        ),
        actions=None,
        creates=("opening",),
        destroys=(),
        copilot=True,
        atomic=False,
        example=op(
            "opening.add",
            id=EXAMPLE_IDS["opening1"],
            wallId=EXAMPLE_IDS["wall1"],
            kind="door",
            widthMm=900,
            heightMm=2100,
            sillMm=0,
            offsetMm=1200,
            swing="in-left",
        ),
    ),
    OpSpec(
        number=15,
        type="opening.move",
        category="opening",
        title="Move opening",
        summary="Slide an opening along its wall, or re-host it onto another wall by passing `wallId`.",
        payload=(
            _id("openingId", "opening", "Opening to move."),
            _int_mm("offsetMm", "New centre offset from the host wall `a`."),
            _id("wallId", "wall", "New host wall (omit to keep the current one).", False),
        ),
        actions=None,
        creates=(),
        destroys=(),
        copilot=True,
        atomic=False,
        example=op("opening.move", openingId=EXAMPLE_IDS["opening1"], offsetMm=1500),
    ),
    OpSpec(
        number=16,
        type="opening.resize",
        category="opening",
        title="Resize opening",
        summary="Change width / height / sill of an opening. Omitted fields stay as they are.",
        payload=(
            _id("openingId", "opening", "Opening to resize."),
            _int_mm("widthMm", "New clear width.", False),
            _int_mm("heightMm", "New clear height.", False),
            _int_mm("sillMm", "New sill height above FFL.", False),
        ),
        actions=None,
        creates=(),
        destroys=(),
        copilot=True,
        atomic=False,
        example=op("opening.resize", openingId=EXAMPLE_IDS["opening1"], widthMm=1200),
    ),
    OpSpec(
        number=17,
        type="opening.flip",
        category="opening",
        title="Flip opening swing",
        summary="Change the door swing / hand.",
        payload=(
            _id("openingId", "opening", "Opening to flip."),
            _enum("swing", OPENING_SWINGS, "New swing."),
        ),
        actions=None,
        creates=(),
        destroys=(),
        copilot=True,
        atomic=False,
        example=op("opening.flip", openingId=EXAMPLE_IDS["opening1"], swing="in-right"),
    ),
    OpSpec(
        number=18,
        type="opening.delete",
        category="opening",
        title="Delete opening",
        summary="Remove an opening from its wall.",
        payload=(_id("openingId", "opening", "Opening to delete."),),
        actions=None,
        creates=(),
        destroys=("opening",),
        copilot=True,
        atomic=False,
        example=op("opening.delete", openingId=EXAMPLE_IDS["opening1"]),
    ),
    OpSpec(
        number=19,
        type="room.assign",
        category="room",
        title="Assign room type",
        summary=(
            "Set a detected room's programme type, name, tags and lock flag. Never changes geometry."
        ),
        payload=(
            _id("roomId", "room", "Room to assign."),
            _enum("type", ROOM_TYPES, "Programme type."),
            _string("name", "Display name; empty string falls back to the type label.", False),
            OpFieldSpec(
                name="tags",
                type="string-array",
                required=False,
                units=None,
                description='Free-form tags (e.g. "attached", "guest").',
            ),
            _bool("locked", "Lock against solver re-solve (section 5.7)."),
        ),
        actions=None,
        creates=(),
        destroys=(),
        copilot=True,
        atomic=False,
        example=op(
            "room.assign",
            roomId=EXAMPLE_IDS["room1"],
            type="bedroom_master",
            name="Master Bedroom",
        ),
    ),
    OpSpec(
        number=20,
        type="room.set_target",
        category="room",
        title="Set room target",
        summary="Set a target area and/or required facing for a room. Feeds the solver, not the geometry.",
        payload=(
            _id("roomId", "room", "Room to constrain."),
            OpFieldSpec(
                name="targetAreaMm2",
                type="int-mm2",
                required=False,
                units="mm2",
                nullable=True,
                description="Target clear area in mm2 (null clears it).",
            ),
            _enum("mustFace", DIRECTIONS_8, "Required facing (null clears it).", False, True),
        ),
        actions=None,
        creates=(),
        destroys=(),
        copilot=True,
        atomic=False,
        example=op(
            "room.set_target", roomId=EXAMPLE_IDS["room1"], targetAreaMm2=12000000, mustFace="NE"
        ),
    ),
    OpSpec(
        number=21,
        type="stair.add",
        category="stair",
        title="Add stair",
        summary=(
            "Add a stair. risersCount x riserMm must equal the storey height within +/-10mm, or "
            "the op is rejected."
        ),
        payload=(
            _id("id", "stair", "Id for the new stair."),
            _id("storeyId", "storey", "Storey the flight starts on."),
            _enum("kind", STAIR_KINDS, "Stair configuration."),
            _pt("origin", "Footprint origin (first riser)."),
            _enum("direction", DIRECTIONS_4, "Direction of travel going up."),
            _int_mm("riserMm", "Riser height (NBC <= 190)."),
            _int_mm("treadMm", "Tread depth (NBC >= 250)."),
            _int_mm("widthMm", "Clear flight width (NBC >= 900)."),
            _int("risersCount", "Number of risers."),
            OpFieldSpec(
                name="landing",
                type="landing",
                required=False,
                units="mm",
                nullable=True,
                description="Landing block { widthMm, depthMm }, or null for a single straight flight.",
            ),
        ),
        actions=None,
        creates=("stair",),
        destroys=(),
        copilot=True,
        atomic=False,
        example=op(
            "stair.add",
            id=EXAMPLE_IDS["stair1"],
            storeyId=EXAMPLE_IDS["storey0"],
            kind="dogleg",
            origin={"x": 1000, "y": 1000},
            direction="N",
            riserMm=167,
            treadMm=275,
            widthMm=1000,
            risersCount=18,
            landing={"widthMm": 2115, "depthMm": 1000},
        ),
    ),
    OpSpec(
        number=22,
        type="stair.edit",
        category="stair",
        title="Edit stair",
        summary="Patch stair fields (kind, origin, direction, riser, tread, width, risersCount, landing).",
        payload=(
            _id("stairId", "stair", "Stair to edit."),
            _json("patch", "Partial stair fields; omitted fields are unchanged."),
        ),
        actions=None,
        creates=(),
        destroys=(),
        copilot=True,
        atomic=False,
        example=op("stair.edit", stairId=EXAMPLE_IDS["stair1"], patch={"widthMm": 1050}),
    ),
    OpSpec(
        number=23,
        type="stair.delete",
        category="stair",
        title="Delete stair",
        summary="Remove a stair.",
        payload=(_id("stairId", "stair", "Stair to delete."),),
        actions=None,
        creates=(),
        destroys=("stair",),
        copilot=True,
        atomic=False,
        example=op("stair.delete", stairId=EXAMPLE_IDS["stair1"]),
    ),
    OpSpec(
        number=24,
        type="column.set",
        category="column",
        title="Add / move / delete column",
        summary=(
            "One op for all column edits; `action` selects add | move | delete. Columns are "
            "coordination-only (never affect rooms)."
        ),
        payload=(
            _enum("action", COLUMN_ACTIONS, "add | move | delete."),
            _id("id", "column", "Column id (new id for add, existing for move/delete)."),
            _id("storeyId", "storey", "Host storey (required for add).", False),
            _pt("pt", "Column centre (required for add and move).", False),
            OpFieldSpec(
                name="sizeMm",
                type="size-mm",
                required=False,
                units="mm",
                description="Column size { xMm, yMm }; defaults to 230x230 on add.",
            ),
        ),
        actions=COLUMN_ACTIONS,
        creates=("column",),
        destroys=("column",),
        copilot=True,
        atomic=False,
        example=op(
            "column.set",
            action="add",
            id=EXAMPLE_IDS["column1"],
            storeyId=EXAMPLE_IDS["storey0"],
            pt={"x": 3000, "y": 3000},
            sizeMm={"xMm": 230, "yMm": 230},
        ),
    ),
    OpSpec(
        number=25,
        type="furniture.set",
        category="furniture",
        title="Place / transform / delete furniture",
        summary="One op for all furniture edits; `action` selects place | transform | delete.",
        payload=(
            _enum("action", FURNITURE_ACTIONS, "place | transform | delete."),
            _id("id", "furniture", "Furniture instance id."),
            _id("storeyId", "storey", "Host storey (required for place).", False),
            _string("catalogId", "Furniture catalogue id (required for place).", False),
            _pt("pt", "Footprint centre.", False),
            OpFieldSpec(
                name="rotationDeg",
                type="int-deg",
                required=False,
                units="deg",
                description="Integer degrees CCW from the catalogue default orientation.",
            ),
        ),
        actions=FURNITURE_ACTIONS,
        creates=("furniture",),
        destroys=("furniture",),
        copilot=True,
        atomic=False,
        example=op(
            "furniture.set",
            action="place",
            id=EXAMPLE_IDS["furniture1"],
            storeyId=EXAMPLE_IDS["storey0"],
            catalogId="bed-queen-1900x1525",
            pt={"x": 2000, "y": 2000},
            rotationDeg=90,
        ),
    ),
    OpSpec(
        number=26,
        type="balcony.set",
        category="balcony",
        title="Add / edit / delete balcony",
        summary=(
            "One op for all balcony edits; `action` selects add | edit | delete. Projection is "
            "checked against the projection rules."
        ),
        payload=(
            _enum("action", BALCONY_ACTIONS, "add | edit | delete."),
            _id("id", "balcony", "Balcony id."),
            _id("storeyId", "storey", "Host storey (required for add).", False),
            _polygon("polygon", "Balcony slab outline (required for add).", False),
            _enum("railingKind", RAILING_KINDS, "Railing type.", False),
            _int_mm("railingHeightMm", "Railing height (1000 default).", False),
            _int_mm("projectionMm", "Projection beyond the building line.", False),
            _int_mm("slabThicknessMm", "Balcony slab thickness.", False),
        ),
        actions=BALCONY_ACTIONS,
        creates=("balcony",),
        destroys=("balcony",),
        copilot=True,
        atomic=False,
        example=op(
            "balcony.set",
            action="add",
            id=EXAMPLE_IDS["balcony1"],
            storeyId=EXAMPLE_IDS["storey0"],
            polygon=[
                {"x": 0, "y": 0},
                {"x": 2400, "y": 0},
                {"x": 2400, "y": 900},
                {"x": 0, "y": 900},
            ],
            railingKind="ms",
            railingHeightMm=1000,
            projectionMm=900,
        ),
    ),
    OpSpec(
        number=27,
        type="facade.apply_kit",
        category="facade",
        title="Apply facade kit",
        summary=(
            "Replace the whole facade sub-model with a kit instantiation. Cannot touch walls, "
            "rooms or areas."
        ),
        payload=(
            _string(
                "kitId",
                "Facade kit id: 'contemporary' | 'modern-minimal', or null to clear.",
                True,
                True,
            ),
            _int("seed", "Variation seed.", "count"),
            _string("colorwayId", "Colorway id, or null.", False, True),
            OpFieldSpec(
                name="components",
                type="facade-components",
                required=True,
                units=None,
                description="Components the kit generator produced (carried so replay is deterministic).",
            ),
        ),
        actions=None,
        creates=("facadecomp",),
        destroys=("facadecomp",),
        copilot=True,
        atomic=True,
        example=op(
            "facade.apply_kit", kitId="contemporary", seed=7, colorwayId="mono-wood", components=[]
        ),
    ),
    OpSpec(
        number=28,
        type="facade.edit_component",
        category="facade",
        title="Edit facade component",
        summary="RFC 7386 merge patch on one facade component's params (e.g. chajja projection).",
        payload=(
            _id("componentId", "facadecomp", "Component to edit."),
            _json("patch", "Merge patch on the component params."),
        ),
        actions=None,
        creates=(),
        destroys=(),
        copilot=True,
        atomic=False,
        example=op(
            "facade.edit_component",
            componentId=EXAMPLE_IDS["facadeComp1"],
            patch={"projectionMm": 750},
        ),
    ),
    OpSpec(
        number=29,
        type="material.assign",
        category="material",
        title="Assign material",
        summary=(
            "Assign a catalogue material to a surface group (optionally scoped to a storey or element)."
        ),
        payload=(
            _id("id", "material", "Assignment id."),
            OpFieldSpec(
                name="target",
                type="surface-group-ref",
                required=True,
                units=None,
                enum_values=SURFACE_GROUPS,
                description="Target { group, storeyId|null, elementId|null }.",
            ),
            _string("materialId", "Catalogue material id, or null to clear.", True, True),
        ),
        actions=None,
        creates=("material",),
        destroys=("material",),
        copilot=True,
        atomic=False,
        example=op(
            "material.assign",
            id=EXAMPLE_IDS["material1"],
            target={"group": "external_wall", "storeyId": None, "elementId": None},
            materialId="texture-paint-grey",
        ),
    ),
    OpSpec(
        number=30,
        type="levels.set",
        category="levels",
        title="Set levels",
        summary="Set plinth / default sill / default lintel / parapet heights (sections read these).",
        payload=(
            _int_mm("plinthMm", "Plinth height above ground.", False),
            _int_mm("sillDefaultMm", "Default window sill above FFL.", False),
            _int_mm("lintelDefaultMm", "Default lintel height above FFL.", False),
            _int_mm("parapetMm", "Terrace parapet height.", False),
            OpFieldSpec(
                name="fflPerStoreyMm",
                type="int-mm-array",
                required=False,
                units="mm",
                description="Explicit FFL per storey; normally derived from storey heights.",
            ),
        ),
        actions=None,
        creates=(),
        destroys=(),
        copilot=True,
        atomic=False,
        example=op("levels.set", plinthMm=600, parapetMm=1050),
    ),
    OpSpec(
        number=31,
        type="solver.apply_option",
        category="solver",
        title="Apply solver option",
        summary=(
            "Apply a generated plan option as ONE atomic group. Carries its own expansion so "
            "replay never re-runs the solver."
        ),
        payload=(
            _string("solverJobId", "Solver job the option came from."),
            _int("optionIndex", "Index of the chosen option.", "index"),
            OpFieldSpec(
                name="ops",
                type="ops",
                required=True,
                units=None,
                description="The expansion: the ops that build the option (applied atomically).",
            ),
            OpFieldSpec(
                name="lockedRoomIds",
                type="id-array",
                required=False,
                units=None,
                id_type="room",
                description="Room ids the user locked; the solver must return them untouched.",
            ),
        ),
        actions=None,
        creates=(),
        destroys=(),
        copilot=False,
        atomic=True,
        example=op("solver.apply_option", solverJobId="job_demo", optionIndex=0, ops=[]),
    ),
    OpSpec(
        number=32,
        type="annotation.set",
        category="annotation",
        title="Add / edit / delete sheet annotation",
        summary=(
            "One op for all sheet annotations; `action` selects add | edit | delete. Annotations "
            "anchor to element ids."
        ),
        payload=(
            _enum("action", ANNOTATION_ACTIONS, "add | edit | delete."),
            _id("id", "annotation", "Annotation id."),
            _id("sheetId", "sheet", "Sheet the annotation lives on (required for add).", False),
            _string(
                "anchorElementId",
                "Model element the annotation is anchored to, or null.",
                False,
                True,
            ),
            _enum(
                "anchorKind", ANNOTATION_ANCHOR_KINDS, "What kind of thing the anchor is.", False
            ),
            _json("payload", "Annotation content (text, leader, style).", False),
            _bool("orphaned", "Set true when a re-solve destroyed the anchor (Review Tray)."),
        ),
        actions=ANNOTATION_ACTIONS,
        creates=("annotation",),
        destroys=("annotation",),
        copilot=False,
        atomic=False,
        example=op(
            "annotation.set",
            action="add",
            id=EXAMPLE_IDS["annotation1"],
            sheetId=EXAMPLE_IDS["sheet1"],
            anchorElementId=EXAMPLE_IDS["wall1"],
            anchorKind="wall",
            payload={"text": "RCC beam over — refer structural"},
        ),
    ),
)

#: All 32 op type strings, in playbook order.
OP_TYPES: tuple[str, ...] = tuple(spec.type for spec in OP_CATALOG)

_OP_SPEC_BY_TYPE: Mapping[str, OpSpec] = {spec.type: spec for spec in OP_CATALOG}


def get_op_spec(op_type: str) -> OpSpec | None:
    """Catalogue entry for an op type, or ``None`` for an unknown type."""
    return _OP_SPEC_BY_TYPE.get(op_type)


def is_op_type(value: Any) -> bool:
    """Type guard for the op-type tag."""
    return isinstance(value, str) and value in _OP_SPEC_BY_TYPE


def is_op(value: Any) -> bool:
    """Shallow runtime guard: right envelope, known type, payload is an object.

    DEEP validation (units, ids, geometry, invariants) lives in
    :mod:`garh_model.validate` — this only decides "is this thing shaped like an
    op at all".
    """
    op_type = op_type_of(value)
    if op_type is None or not is_op_type(op_type):
        return False
    return payload_of(value) is not None


def copilot_op_specs() -> list[OpSpec]:
    """Ops the copilot is allowed to emit (prompt generation filters on this)."""
    return [spec for spec in OP_CATALOG if spec.copilot]


def render_op_catalog_for_prompt(copilot_only: bool | None = None) -> str:
    """Render the op catalogue as the copilot system-prompt section (section 10).

    Deliberately terse and unit-explicit: the LLM's whole job is to pick a type
    and fill integer-mm fields. Generated, never hand-written, so a new op is
    available to the copilot the moment it lands in :data:`OP_CATALOG`.

    ``copilot_only=False`` renders the whole catalogue; the default renders only
    the copilot-emittable ops (mirrors the TypeScript default).
    """
    specs = list(OP_CATALOG) if copilot_only is False else copilot_op_specs()
    lines: list[str] = []
    lines.append("# Op catalogue")
    lines.append("")
    lines.append(
        "All lengths are INTEGER MILLIMETRES. Areas are integer mm2. Angles are integer degrees."
    )
    lines.append(
        "Emit ops only from this list. Never emit coordinates you were not given or told to compute."
    )
    lines.append("")
    for spec in specs:
        lines.append(f"## {spec.type} — {spec.title}")
        lines.append(spec.summary)
        if spec.actions:
            lines.append("action: " + " | ".join(spec.actions))
        for f in spec.payload:
            bits: list[str] = [f.type]
            if f.units:
                bits.append(f.units)
            if f.id_type:
                bits.append(f"{f.id_type}_<ulid>")
            if f.enum_values:
                bits.append("|".join(f.enum_values))
            if f.nullable:
                bits.append("nullable")
            bits.append("required" if f.required else "optional")
            lines.append(f"- {f.name} ({', '.join(bits)}): {f.description}")
        lines.append(
            "example: "
            + json.dumps(spec.example.to_json(), separators=(",", ":"), ensure_ascii=False)
        )
        lines.append("")
    return "\n".join(lines)
