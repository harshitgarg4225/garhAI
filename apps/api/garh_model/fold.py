"""fold.py — the op engine: ``fold``, ``replay``, ``apply_group``, undo/redo,
``canonical_json`` and ``state_hash``.

Mirror of ``packages/model/src/fold.ts``.

``fold(model, op)`` is PURE and DETERMINISTIC:

* it never reads the clock, never generates a random id, never mutates its input
  (creation ops carry their ids; see :mod:`garh_model.ops`),
* the same op log always folds to the same document and therefore the same
  ``state_hash``, in Python and in the TypeScript mirror alike,
* it returns an INVERSE op list, so undo is "apply the inverse", not "restore a
  snapshot".

============================================================================
state_hash — CROSS-LANGUAGE CONTRACT (must match ``packages/model`` byte for
byte, because ``design_versions.snapshot_hash`` is compared across languages).

    state_hash(v) = lowercase_hex( sha256( utf8( canonical_json(v) ) ) )

canonical_json rules, exactly:

1. ``None`` -> ``null``; ``True``/``False`` -> ``true``/``false``.
2. Numbers MUST be safe integers (``|v| <= 2**53 - 1``). Anything else — a
   fractional float, inf, nan, an int beyond the safe range — raises
   :class:`CanonicalJsonError`. There are no floats in this document by
   construction (geometry is integer mm), and that is what makes the hash
   portable. A float that is integral (``3.0``, as JSON decoding can produce)
   is written as the integer ``3``, because JavaScript cannot tell the two
   apart and the hash must not depend on which language parsed the JSON.
   ``-0.0`` serialises as ``0``. Integers are written in plain decimal: no
   ``+``, no exponent, no padding.
3. Strings are quoted with ``"`` and escaped MINIMALLY:
   ``\\`` -> ``\\\\``, ``"`` -> ``\\"``, U+0008 -> ``\\b``, U+0009 -> ``\\t``,
   U+000A -> ``\\n``, U+000C -> ``\\f``, U+000D -> ``\\r``, any other code
   point < 0x20 -> ``\\u00xx`` with LOWERCASE hex. Everything else (including
   all non-ASCII) is emitted literally as UTF-8 — no ``\\uXXXX`` escaping, no
   escaping of ``/``, U+2028 or U+2029. A lone surrogate raises.
4. Arrays keep their order.
5. Object keys are sorted ASCENDING BY UNICODE CODE POINT. Python's ``str``
   comparison is already code-point ordered, which is exactly what the
   TypeScript ``compareCodePoints`` hand-rolls (JavaScript's default sort
   compares UTF-16 code units and would disagree above U+FFFF).
6. No whitespace anywhere: separators are exactly ``,`` and ``:``.
7. Any other value (a set, a date, a class instance) raises. Dataclasses are
   converted by :func:`~garh_model.model.to_jsonable` before they get here.

ELEMENT ORDER is part of the canonical form: :func:`_finalize` sorts every
element array by id ascending (byte compare) before the document is returned, so
two documents with the same content hash the same regardless of insertion order.
``storeys`` and ``levels.ffl_per_storey_mm`` keep their semantic order
(ground = 0).
============================================================================
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, TypeGuard

from .geometry import Pt, Seg, point_along_seg, polygon_area_mm2, rect_polygon, segment_length_mm
from .ids import derived_id
from .model import (
    DEFAULTS,
    SCHEMA_VERSION,
    Annotation,
    Balcony,
    BriefDoc,
    Column,
    FacadeComponent,
    FacadeModel,
    FurnitureInstance,
    HouseModel,
    JsonObject,
    LevelData,
    Levels,
    MaterialAssignment,
    ModelMeta,
    Opening,
    PlotDoc,
    ProjectDoc,
    RegProfile,
    Road,
    Room,
    SizeMm,
    Slab,
    Stair,
    StairLanding,
    Storey,
    SurfaceGroupRef,
    Wall,
    default_level_data,
    empty_project_doc,
    to_jsonable,
)
from .ops import Op, op, op_type_of
from .rooms import detect_rooms
from .sha256 import sha256_utf8
from .validate import (
    OpRejectedError,
    ValidationIssue,
    polygon_of,
    pt_of,
    validate_model,
    validate_op_against_doc,
    validate_op_shape,
)

__all__ = [
    "CANONICAL_JSON_SPEC",
    "STATE_HASH_ALGORITHM",
    "CanonicalJsonError",
    "compare_code_points",
    "canonical_json",
    "state_hash",
    "doc_hash",
    "apply_merge_patch",
    "invert_merge_patch",
    "stair_footprint_polygon",
    "FoldResult",
    "FoldOutcome",
    "fold",
    "try_fold",
    "GroupResult",
    "apply_group",
    "replay",
    "UndoEntry",
    "UndoStack",
    "wall_length_mm",
    "storey_carpet_area_mm2",
    "storey_built_up_area_mm2",
    "locked_room_ids",
    "assert_schema_version",
]

# ---------------------------------------------------------------------------
# Canonical JSON + state hash
# ---------------------------------------------------------------------------

#: Version tag of the canonicalisation rules. Bump => every stored hash changes.
CANONICAL_JSON_SPEC = "garh-canonical-json/v1"

#: Human-readable name of the hash algorithm, for logs and DB comments.
STATE_HASH_ALGORITHM = f"sha256({CANONICAL_JSON_SPEC})"

_MAX_SAFE_INTEGER = 2**53 - 1


class CanonicalJsonError(ValueError):
    """Raised when a value cannot appear in the canonical form."""

    code = "CANONICAL_JSON_INVALID"

    def __init__(self, message: str, path: str) -> None:
        super().__init__(f"{message} (at {path if path else '$'})")
        self.path = path


def compare_code_points(a: str, b: str) -> int:
    """Compare two strings by Unicode CODE POINT (Python's native ordering)."""
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


_ESCAPES: Mapping[int, str] = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
    0x22: '\\"',
    0x5C: "\\\\",
}


def _canonical_string(s: str, path: str) -> str:
    out: list[str] = ['"']
    for ch in s:
        code = ord(ch)
        esc = _ESCAPES.get(code)
        if esc is not None:
            out.append(esc)
            continue
        if code < 0x20:
            out.append(f"\\u{code:04x}")
            continue
        if 0xD800 <= code <= 0xDFFF:
            # Python holds astral characters as single code points, so any
            # surrogate here is an unpaired one — the TypeScript throws too.
            raise CanonicalJsonError("Lone surrogate in string", path)
        out.append(ch)
    out.append('"')
    return "".join(out)


def _canonical_number(value: Any, path: str) -> str:
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")) or not value.is_integer():
            raise CanonicalJsonError(
                f"Only safe integers may be serialised (got {value!r}). Geometry is integer mm; "
                "brief/override JSON must use integers too.",
                path,
            )
        value = int(value)
    if abs(value) > _MAX_SAFE_INTEGER:
        raise CanonicalJsonError(
            f"Only safe integers may be serialised (got {value!r}); "
            f"|v| must not exceed {_MAX_SAFE_INTEGER}.",
            path,
        )
    return str(value + 0)  # `+ 0` normalises -0 to 0


def _canonical_write(value: Any, path: str) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return _canonical_number(value, path)
    if isinstance(value, str):
        return _canonical_string(value, path)
    if isinstance(value, list | tuple):
        parts = [_canonical_write(v, f"{path}[{i}]") for i, v in enumerate(value)]
        return "[" + ",".join(parts) + "]"
    if isinstance(value, Mapping):
        for k in value:
            if not isinstance(k, str):
                # JavaScript object keys are always strings; a non-string key here
                # would hash differently depending on how it stringified.
                raise CanonicalJsonError(
                    f"Object keys must be strings (got {type(k).__name__})", path
                )
        # Python's str ordering IS code-point ordering — the same order the
        # TypeScript hand-rolls in compareCodePoints.
        parts = [
            f"{_canonical_string(k, path)}:{_canonical_write(value[k], f'{path}.{k}')}"
            for k in sorted(value.keys())
        ]
        return "{" + ",".join(parts) + "}"
    raise CanonicalJsonError(f"Cannot serialise a {type(value).__name__}", path)


def canonical_json(value: Any) -> str:
    """Canonical JSON per the rules in this module's docstring."""
    return _canonical_write(value, "")


def state_hash(value: Any) -> str:
    """sha256 of the canonical JSON — 64 lowercase hex chars.

    Dataclasses (a :class:`~garh_model.model.ProjectDoc`, a
    :class:`~garh_model.model.Room`, ...) are converted with
    :func:`~garh_model.model.to_jsonable` first, which is the one and only way
    Python attribute names become the camelCase wire keys the hash is defined
    over.
    """
    return sha256_utf8(canonical_json(to_jsonable(value)))


def doc_hash(doc: ProjectDoc) -> str:
    """The document hash stored in ``design_versions.snapshot_hash``."""
    return state_hash(doc)


# ---------------------------------------------------------------------------
# RFC 7386 JSON merge patch (brief.update, facade.edit_component)
# ---------------------------------------------------------------------------


def _is_json_object(v: Any) -> TypeGuard[dict[str, Any]]:
    # Merge patches arrive from JSON, where every mapping is a plain dict; the
    # narrowed type is what apply/invert_merge_patch take.
    return isinstance(v, Mapping)


def apply_merge_patch(target: JsonObject, patch: JsonObject) -> JsonObject:
    """RFC 7386: ``null`` deletes a key, objects merge recursively, else replace."""
    out: dict[str, Any] = dict(target)
    for key in patch:
        pv = patch[key]
        if pv is None:
            out.pop(key, None)
            continue
        tv = out.get(key)
        if _is_json_object(pv):
            out[key] = apply_merge_patch(tv if _is_json_object(tv) else {}, pv)
        else:
            out[key] = pv
    return out


def invert_merge_patch(target: JsonObject, patch: JsonObject) -> JsonObject:
    """The patch that undoes ``patch`` applied to ``apply_merge_patch(target, patch)``."""
    out: dict[str, Any] = {}
    for key in patch:
        pv = patch[key]
        had = key in target
        tv = target.get(key)
        if pv is None:
            out[key] = tv if had else None
        elif _is_json_object(pv) and _is_json_object(tv):
            out[key] = invert_merge_patch(tv, pv)
        else:
            out[key] = tv if had else None
    return out


# ---------------------------------------------------------------------------
# Draft (a mutable working copy of a ProjectDoc)
# ---------------------------------------------------------------------------


class _OrderedSet:
    """Insertion-ordered set of strings.

    A plain ``set`` iterates in hash order, which PYTHONHASHSEED randomises —
    that would make the order in which dirty storeys are recomputed vary between
    processes. JavaScript's ``Set`` is insertion-ordered, so this is what keeps
    the two implementations (and two Python runs) in lockstep.
    """

    __slots__ = ("_items",)

    def __init__(self) -> None:
        self._items: dict[str, None] = {}

    def add(self, value: str) -> None:
        self._items[value] = None

    def __contains__(self, value: object) -> bool:
        return value in self._items

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def to_list(self) -> list[str]:
        return list(self._items)


@dataclass
class _Draft:
    schema_version: int
    plot: PlotDoc
    brief: BriefDoc
    annotations: list[Annotation]
    storeys: list[Storey]
    walls: list[Wall]
    openings: list[Opening]
    rooms: list[Room]
    stairs: list[Stair]
    slabs: list[Slab]
    columns: list[Column]
    furniture: list[FurnitureInstance]
    facade: FacadeModel
    materials: list[MaterialAssignment]
    levels: Levels
    balconies: list[Balcony]
    meta: ModelMeta
    #: Storeys whose walls changed => rooms and slabs must be recomputed.
    dirty_storeys: _OrderedSet = field(default_factory=_OrderedSet)
    #: Storeys the op touched at all. The post-apply invariant check is scoped to
    #: these so the quadratic wall-overlap scan stays inside the <10ms op budget.
    #: Empty => validate the whole document (plot/levels/brief ops).
    touched_storeys: _OrderedSet = field(default_factory=_OrderedSet)
    #: True when FFLs should be re-derived from plinth + storey heights.
    derive_levels: bool = False


def _to_draft(doc: ProjectDoc) -> _Draft:
    h = doc.house
    return _Draft(
        schema_version=doc.schema_version,
        plot=doc.plot,
        brief=doc.brief,
        annotations=list(doc.annotations),
        storeys=list(h.storeys),
        walls=list(h.walls),
        openings=list(h.openings),
        rooms=list(h.rooms),
        stairs=list(h.stairs),
        slabs=list(h.slabs),
        columns=list(h.columns),
        furniture=list(h.furniture),
        facade=h.facade,
        materials=list(h.materials),
        levels=h.levels,
        balconies=list(h.balconies),
        meta=h.meta,
    )


def _derive_ffl_per_storey(storeys: Sequence[Storey], plinth_mm: int) -> list[int]:
    """Derive FFL per storey from plinth + storey heights (ground FFL = plinth)."""
    out: list[int] = []
    ffl = plinth_mm
    for s in storeys:
        out.append(ffl)
        ffl += s.height_mm
    return out


_STAIR_VECTORS: Mapping[str, tuple[int, int, int, int]] = {
    # direction: (forward x, forward y, right x, right y) — right is 90 deg CW
    "N": (0, 1, 1, 0),
    "E": (1, 0, 0, -1),
    "S": (0, -1, -1, 0),
    "W": (-1, 0, 0, 1),
}


def stair_footprint_polygon(stair: Stair) -> list[Pt]:
    """Footprint of a stair, used for slab cut-outs.

    EXACT for ``straight``. For ``dogleg`` / ``L`` / ``U`` this is the BOUNDING
    RECTANGLE of the flights plus landing — good enough for a slab void and for
    the "UP 15R" arrow block, and deliberately not pretending to be the true
    outline.
    """

    def going_of(risers: int) -> int:
        return max(1, risers - 1) * stair.tread_mm

    if stair.kind == "straight":
        depth_mm = going_of(stair.risers_count)
        width_mm = stair.width_mm
    else:
        per_flight = -((-stair.risers_count) // 2)  # Math.ceil(risersCount / 2)
        landing_depth = stair.width_mm if stair.landing is None else stair.landing.depth_mm
        depth_mm = going_of(per_flight) + landing_depth
        if stair.kind == "L":
            landing_width = stair.width_mm if stair.landing is None else stair.landing.width_mm
            width_mm = stair.width_mm + landing_width
        else:
            # dogleg and U: two parallel flights either side of the landing
            width_mm = 2 * stair.width_mm + 100 if stair.landing is None else stair.landing.width_mm

    fx, fy, rx, ry = _STAIR_VECTORS[stair.direction]
    x = stair.origin.x
    y = stair.origin.y
    xs = [x, x + rx * width_mm, x + rx * width_mm + fx * depth_mm, x + fx * depth_mm]
    ys = [y, y + ry * width_mm, y + ry * width_mm + fy * depth_mm, y + fy * depth_mm]
    return rect_polygon(min(xs), min(ys), max(xs), max(ys))


def _recompute_derived(draft: _Draft) -> None:
    """Rebuild the derived rooms and slabs of every dirty storey."""
    if len(draft.dirty_storeys) == 0:
        return
    for storey_id in draft.dirty_storeys.to_list():
        storey = next((s for s in draft.storeys if s.id == storey_id), None)
        if storey is None:
            continue

        other_rooms = [r for r in draft.rooms if r.storey_id != storey_id]
        taken_ids: set[str] = set()
        for element in (
            list(draft.storeys)
            + list(draft.walls)
            + list(draft.openings)
            + list(other_rooms)
            + list(draft.stairs)
            + list(draft.columns)
            + list(draft.furniture)
            + list(draft.balconies)
            + list(draft.materials)
            + list(draft.annotations)
        ):
            taken_ids.add(element.id)

        detection = detect_rooms(
            draft.walls,
            storey_id,
            [r for r in draft.rooms if r.storey_id == storey_id],
            taken_ids,
        )
        draft.rooms = other_rooms + list(detection.rooms)

        # --- slab: outline of this storey's walls, with stair wells from below
        storey_idx = next((i for i, s in enumerate(draft.storeys) if s.id == storey_id), -1)
        below = draft.storeys[storey_idx - 1] if storey_idx > 0 else None
        cutouts: list[tuple[Pt, ...]] = (
            [tuple(stair_footprint_polygon(s)) for s in draft.stairs if s.storey_id == below.id]
            if below is not None
            else []
        )
        draft.slabs = [s for s in draft.slabs if s.storey_id != storey_id]
        if detection.outline is not None and len(detection.outline) >= 3:
            draft.slabs.append(
                Slab(
                    id=derived_id("slab", f"{storey_id}|floor"),
                    storey_id=storey_id,
                    kind="floor",
                    polygon=tuple(detection.outline),
                    thickness_mm=storey.level.slab_thickness_mm,
                    cutouts=tuple(cutouts),
                )
            )


def _finalize(draft: _Draft) -> ProjectDoc:
    if draft.derive_levels:
        draft.levels = replace(
            draft.levels,
            ffl_per_storey_mm=tuple(_derive_ffl_per_storey(draft.storeys, draft.levels.plinth_mm)),
        )
        ffls = draft.levels.ffl_per_storey_mm
        draft.storeys = [
            replace(
                s,
                level=replace(s.level, ffl_mm=ffls[i] if i < len(ffls) else s.level.ffl_mm),
            )
            for i, s in enumerate(draft.storeys)
        ]
    _recompute_derived(draft)

    house = HouseModel(
        schema_version=draft.schema_version,
        storeys=tuple(draft.storeys),
        walls=tuple(sorted(draft.walls, key=lambda e: e.id)),
        openings=tuple(sorted(draft.openings, key=lambda e: e.id)),
        rooms=tuple(sorted(draft.rooms, key=lambda e: e.id)),
        stairs=tuple(sorted(draft.stairs, key=lambda e: e.id)),
        slabs=tuple(sorted(draft.slabs, key=lambda e: e.id)),
        columns=tuple(sorted(draft.columns, key=lambda e: e.id)),
        furniture=tuple(sorted(draft.furniture, key=lambda e: e.id)),
        facade=replace(
            draft.facade, components=tuple(sorted(draft.facade.components, key=lambda e: e.id))
        ),
        materials=tuple(sorted(draft.materials, key=lambda e: e.id)),
        levels=draft.levels,
        balconies=tuple(sorted(draft.balconies, key=lambda e: e.id)),
        meta=draft.meta,
    )
    return ProjectDoc(
        schema_version=draft.schema_version,
        plot=draft.plot,
        brief=draft.brief,
        house=house,
        annotations=tuple(sorted(draft.annotations, key=lambda e: e.id)),
    )


# ---------------------------------------------------------------------------
# fold
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FoldResult:
    #: The next document. The input is never mutated.
    model: ProjectDoc
    #: Ops that, applied IN ORDER to :attr:`model`, restore the input document.
    #: Usually one op; destructive ops return several (e.g. deleting a wall
    #: returns ``wall.add`` followed by an ``opening.add`` per hosted opening).
    inverse: tuple[Op, ...]


@dataclass(frozen=True)
class FoldOutcome:
    """Non-throwing fold outcome, for the copilot dry-run loop (section 10)."""

    ok: bool
    model: ProjectDoc | None = None
    inverse: tuple[Op, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()


def _as_op(value: Any) -> Op:
    """Accept an :class:`Op` or the raw wire dict."""
    return value if isinstance(value, Op) else Op.from_json(value)


def fold(
    model: ProjectDoc,
    op_in: Any,
    compute_inverse: bool = True,
    validate_result: bool = True,
) -> FoldResult:
    """Apply one op. Pure: returns a new document plus the inverse ops.

    :raises OpRejectedError: when the op is invalid or inapplicable.
    """
    shape_issues = validate_op_shape(op_in)
    if shape_issues:
        raise OpRejectedError(op_type_of(op_in) or "unknown", shape_issues)
    the_op = _as_op(op_in)
    doc_issues = validate_op_against_doc(model, the_op)
    if doc_issues:
        raise OpRejectedError(the_op.type, doc_issues)

    # A solver option is its own atomic group: delegate so that every expanded op
    # is validated against the intermediate state and the inverse is the reversed
    # concatenation of the inner inverses.
    if the_op.type == "solver.apply_option":
        inner = [_as_op(x) for x in the_op.payload["ops"]]
        group = apply_group(model, inner, the_op.group_id)
        return FoldResult(model=group.model, inverse=group.inverse)

    draft = _to_draft(model)
    inverse: list[Op] = []

    _apply_op(draft, the_op, inverse, compute_inverse)

    nxt = _finalize(draft)

    if validate_result:
        touched = draft.touched_storeys.to_list()
        issues = validate_model(
            nxt, storey_ids=touched if touched else None, include_warnings=False
        )
        if issues:
            raise OpRejectedError(the_op.type, issues)

    final_inverse = (
        _with_room_metadata_restore(model, nxt, inverse)
        if compute_inverse and the_op.type in _DESTRUCTIVE_OPS
        else inverse
    )

    return FoldResult(model=nxt, inverse=tuple(final_inverse))


def try_fold(
    model: ProjectDoc,
    op_in: Any,
    compute_inverse: bool = True,
    validate_result: bool = True,
) -> FoldOutcome:
    """:func:`fold` without exceptions — the shape the copilot loop wants."""
    try:
        r = fold(model, op_in, compute_inverse, validate_result)
    except OpRejectedError as e:
        return FoldOutcome(ok=False, issues=e.issues)
    return FoldOutcome(ok=True, model=r.model, inverse=r.inverse)


#: Ops that can destroy a room (and therefore its type/name/lock metadata).
#: For these, the inverse is topped up with ``room.assign`` / ``room.set_target``
#: ops so undo restores the metadata of every room whose id comes back.
_DESTRUCTIVE_OPS = frozenset(
    {"wall.delete", "wall.move", "wall.split", "wall.set_thickness", "storey.remove"}
)


def _with_room_metadata_restore(
    before: ProjectDoc, after: ProjectDoc, inverse_ops: list[Op]
) -> list[Op]:
    """Top up a geometric inverse with the room metadata it cannot restore.

    Rooms are derived, so an inverse that restores walls also restores room
    GEOMETRY — but a room destroyed by a merge comes back as a fresh room with no
    type or name. Fix that honestly: dry-run the inverse, see which room ids
    actually reappear, and append ``room.assign`` / ``room.set_target`` for
    exactly those. Ops are only emitted for rooms proven to exist after the undo,
    so the inverse group can never fail to apply.

    Limitation: if a room id does NOT reappear (its polygon was re-derived under
    a different id), its name is lost. That is visible in the returned inverse —
    no op is emitted for it — rather than silently mis-attached.
    """
    if not inverse_ops:
        return inverse_ops
    try:
        dry = after
        for inv in inverse_ops:
            dry = fold(dry, inv, compute_inverse=False).model
    except OpRejectedError:
        return inverse_ops  # cannot predict; leave the geometric inverse alone
    restored_by_id = {r.id: r for r in dry.house.rooms}
    extra: list[Op] = []
    for room in before.house.rooms:
        restored = restored_by_id.get(room.id)
        if restored is None:
            continue
        tags_differ = tuple(restored.tags) != tuple(room.tags)
        if (
            restored.type != room.type
            or restored.name != room.name
            or restored.locked != room.locked
            or tags_differ
        ):
            extra.append(
                op(
                    "room.assign",
                    roomId=room.id,
                    type=room.type,
                    name=room.name,
                    tags=list(room.tags),
                    locked=room.locked,
                )
            )
        if restored.target_area_mm2 != room.target_area_mm2 or restored.must_face != room.must_face:
            extra.append(
                op(
                    "room.set_target",
                    roomId=room.id,
                    targetAreaMm2=room.target_area_mm2,
                    mustFace=room.must_face,
                )
            )
    return inverse_ops if not extra else inverse_ops + extra


# ---------------------------------------------------------------------------
# The op switch
# ---------------------------------------------------------------------------


def _coalesce(value: Any, fallback: Any) -> Any:
    """Mirror of the ``??`` operator: ``None`` (null/undefined) falls back."""
    return fallback if value is None else value


def _index_of(items: Sequence[Any], element_id: Any) -> int:
    for i, e in enumerate(items):
        if e.id == element_id:
            return i
    return -1


def _apply_op(draft: _Draft, the_op: Op, inverse: list[Op], want_inverse: bool) -> None:
    p = the_op.payload

    def g(key: str, fallback: Any = None) -> Any:
        return p.get(key, fallback)

    def push(inv: Op) -> None:
        if want_inverse:
            inverse.append(inv)

    t = the_op.type

    # ------------------------------------------------------------------ plot
    if t == "plot.set_boundary":
        prev = draft.plot
        polygon = polygon_of(g("polygon"))
        # Roads reference edge indices, so a boundary with fewer edges drops the
        # roads that no longer have an edge. The inverse restores the boundary
        # AND re-adds those roads, so undo is lossless.
        kept = tuple(r for r in prev.roads if r.edge_index < len(polygon))
        dropped = [r for r in prev.roads if r.edge_index >= len(polygon)]
        push(
            op(
                "plot.set_boundary",
                polygon=[{"x": q.x, "y": q.y} for q in prev.boundary],
                source=prev.source,
            )
        )
        for road in dropped:
            push(
                op(
                    "plot.set_road",
                    edgeIndex=road.edge_index,
                    widthMm=road.width_mm,
                    name=road.name,
                )
            )
        draft.plot = replace(
            prev,
            boundary=tuple(polygon),
            source=_coalesce(g("source"), prev.source),
            roads=kept,
        )

    elif t == "plot.set_north":
        push(op("plot.set_north", deg=draft.plot.north_deg))
        draft.plot = replace(draft.plot, north_deg=g("deg"))

    elif t == "plot.set_road":
        prev_road = next((r for r in draft.plot.roads if r.edge_index == g("edgeIndex")), None)
        push(
            op(
                "plot.set_road",
                edgeIndex=g("edgeIndex"),
                widthMm=None if prev_road is None else prev_road.width_mm,
                name=None if prev_road is None else prev_road.name,
            )
        )
        roads = [r for r in draft.plot.roads if r.edge_index != g("edgeIndex")]
        if g("widthMm") is not None:
            roads.append(
                Road(
                    edge_index=g("edgeIndex"),
                    width_mm=g("widthMm"),
                    name=_coalesce(g("name"), None),
                )
            )
        roads.sort(key=lambda r: r.edge_index)
        draft.plot = replace(draft.plot, roads=tuple(roads))

    elif t == "plot.set_reg_profile":
        prev_profile = draft.plot.reg_profile
        push(
            op(
                "plot.set_reg_profile",
                cityPack=prev_profile.city_pack,
                overrides=dict(prev_profile.overrides),
            )
        )
        draft.plot = replace(
            draft.plot,
            reg_profile=RegProfile(city_pack=g("cityPack"), overrides=dict(g("overrides"))),
        )
        draft.meta = replace(draft.meta, reg_profile_ref=g("cityPack"))

    # ----------------------------------------------------------------- brief
    elif t == "brief.update":
        prev_brief = draft.brief
        inv_patch = invert_merge_patch(prev_brief.data, g("patch"))
        push(
            op(
                "brief.update",
                patch=inv_patch,
                vastuMode=prev_brief.vastu_mode,
                completeness=prev_brief.completeness,
            )
        )
        draft.brief = BriefDoc(
            data=apply_merge_patch(prev_brief.data, g("patch")),
            vastu_mode=_coalesce(g("vastuMode"), prev_brief.vastu_mode),
            completeness=_coalesce(g("completeness"), prev_brief.completeness),
        )

    # --------------------------------------------------------------- storeys
    elif t == "storey.add":
        index = min(g("index"), len(draft.storeys))
        level_raw = g("level")
        storey = Storey(
            id=g("id"),
            name=_coalesce(g("name"), _default_storey_name(index)),
            level=default_level_data(0) if level_raw is None else LevelData.from_json(level_raw),
            height_mm=g("heightMm"),
        )
        draft.storeys.insert(index, storey)
        _touch(draft, storey.id)
        draft.derive_levels = True
        push(op("storey.remove", index=index))

    elif t == "storey.remove":
        index = g("index")
        if 0 <= index < len(draft.storeys):
            storey = draft.storeys[index]
            wall_ids_of_storey = {w.id for w in draft.walls if w.storey_id == storey.id}
            if want_inverse:
                inverse.append(
                    op(
                        "storey.add",
                        id=storey.id,
                        index=index,
                        name=storey.name,
                        heightMm=storey.height_mm,
                        level=to_jsonable(storey.level),
                    )
                )
                for w in [x for x in draft.walls if x.storey_id == storey.id]:
                    inverse.append(
                        op(
                            "wall.add",
                            id=w.id,
                            storeyId=w.storey_id,
                            a={"x": w.a.x, "y": w.a.y},
                            b={"x": w.b.x, "y": w.b.y},
                            thicknessMm=w.thickness_mm,
                            kind=w.kind,
                            loadBearing=w.load_bearing,
                        )
                    )
                for o in [x for x in draft.openings if x.wall_id in wall_ids_of_storey]:
                    inverse.append(Op(type="opening.add", payload=_opening_add_payload(o)))
                for s in [x for x in draft.stairs if x.storey_id == storey.id]:
                    inverse.append(Op(type="stair.add", payload=_stair_add_payload(s)))
                for c in [x for x in draft.columns if x.storey_id == storey.id]:
                    inverse.append(
                        op(
                            "column.set",
                            action="add",
                            id=c.id,
                            storeyId=c.storey_id,
                            pt={"x": c.pt.x, "y": c.pt.y},
                            sizeMm={"xMm": c.size_mm.x_mm, "yMm": c.size_mm.y_mm},
                        )
                    )
                for fi in [x for x in draft.furniture if x.storey_id == storey.id]:
                    inverse.append(
                        op(
                            "furniture.set",
                            action="place",
                            id=fi.id,
                            storeyId=fi.storey_id,
                            catalogId=fi.catalog_id,
                            pt={"x": fi.pt.x, "y": fi.pt.y},
                            rotationDeg=fi.rotation_deg,
                        )
                    )
                for b in [x for x in draft.balconies if x.storey_id == storey.id]:
                    inverse.append(
                        op(
                            "balcony.set",
                            action="add",
                            id=b.id,
                            storeyId=b.storey_id,
                            polygon=[{"x": q.x, "y": q.y} for q in b.polygon],
                            railingKind=b.railing_kind,
                            railingHeightMm=b.railing_height_mm,
                            projectionMm=b.projection_mm,
                            slabThicknessMm=b.slab_thickness_mm,
                        )
                    )
                if any(c.storey_id == storey.id for c in draft.facade.components):
                    # Facade components on this storey go with it; facade.apply_kit
                    # replaces the whole sub-model, so it restores them exactly.
                    inverse.append(
                        op(
                            "facade.apply_kit",
                            kitId=draft.facade.kit_id,
                            seed=draft.facade.seed,
                            colorwayId=draft.facade.colorway_id,
                            components=[to_jsonable(c) for c in draft.facade.components],
                        )
                    )
            draft.walls = [x for x in draft.walls if x.storey_id != storey.id]
            draft.openings = [x for x in draft.openings if x.wall_id not in wall_ids_of_storey]
            draft.rooms = [x for x in draft.rooms if x.storey_id != storey.id]
            draft.stairs = [x for x in draft.stairs if x.storey_id != storey.id]
            draft.slabs = [x for x in draft.slabs if x.storey_id != storey.id]
            draft.columns = [x for x in draft.columns if x.storey_id != storey.id]
            draft.furniture = [x for x in draft.furniture if x.storey_id != storey.id]
            draft.balconies = [x for x in draft.balconies if x.storey_id != storey.id]
            draft.facade = replace(
                draft.facade,
                components=tuple(c for c in draft.facade.components if c.storey_id != storey.id),
            )
            draft.storeys.pop(index)
            draft.derive_levels = True

    elif t == "storey.set_height":
        idx = _index_of(draft.storeys, g("storeyId"))
        if idx >= 0:
            prev_storey = draft.storeys[idx]
            push(op("storey.set_height", storeyId=prev_storey.id, heightMm=prev_storey.height_mm))
            draft.storeys[idx] = replace(prev_storey, height_mm=g("heightMm"))
            draft.derive_levels = True

    # ----------------------------------------------------------------- walls
    elif t == "wall.add":
        draft.walls.append(
            Wall(
                id=g("id"),
                storey_id=g("storeyId"),
                a=pt_of(g("a")),
                b=pt_of(g("b")),
                thickness_mm=g("thicknessMm"),
                kind=g("kind"),
                load_bearing=_coalesce(g("loadBearing"), g("kind") == "external"),
            )
        )
        _dirty(draft, g("storeyId"))
        push(op("wall.delete", wallId=g("id")))

    elif t == "wall.move":
        idx = _index_of(draft.walls, g("wallId"))
        if idx >= 0:
            prev_wall = draft.walls[idx]
            push(
                op(
                    "wall.move",
                    wallId=prev_wall.id,
                    a={"x": prev_wall.a.x, "y": prev_wall.a.y},
                    b={"x": prev_wall.b.x, "y": prev_wall.b.y},
                )
            )
            draft.walls[idx] = replace(prev_wall, a=pt_of(g("a")), b=pt_of(g("b")))
            _dirty(draft, prev_wall.storey_id)

    elif t == "wall.split":
        idx = _index_of(draft.walls, g("wallId"))
        if idx >= 0:
            wall = draft.walls[idx]
            split_pt = point_along_seg(Seg(wall.a, wall.b), g("atMm"))
            moved_openings = [
                o for o in draft.openings if o.wall_id == wall.id and o.offset_mm >= g("atMm")
            ]
            if want_inverse:
                # Order matters: drop the new wall first (its openings go with
                # it), then restore the original geometry, then re-add the
                # openings by their ids.
                inverse.append(op("wall.delete", wallId=g("newWallId")))
                inverse.append(
                    op(
                        "wall.move",
                        wallId=wall.id,
                        a={"x": wall.a.x, "y": wall.a.y},
                        b={"x": wall.b.x, "y": wall.b.y},
                    )
                )
                for o in moved_openings:
                    inverse.append(Op(type="opening.add", payload=_opening_add_payload(o)))
            draft.walls[idx] = replace(wall, b=split_pt)
            draft.walls.append(replace(wall, id=g("newWallId"), a=split_pt, b=wall.b))
            moved_ids = {o.id for o in moved_openings}
            draft.openings = [
                replace(o, wall_id=g("newWallId"), offset_mm=o.offset_mm - g("atMm"))
                if o.id in moved_ids
                else o
                for o in draft.openings
            ]
            _dirty(draft, wall.storey_id)

    elif t == "wall.delete":
        idx = _index_of(draft.walls, g("wallId"))
        if idx >= 0:
            wall = draft.walls[idx]
            hosted = [o for o in draft.openings if o.wall_id == wall.id]
            if want_inverse:
                inverse.append(
                    op(
                        "wall.add",
                        id=wall.id,
                        storeyId=wall.storey_id,
                        a={"x": wall.a.x, "y": wall.a.y},
                        b={"x": wall.b.x, "y": wall.b.y},
                        thicknessMm=wall.thickness_mm,
                        kind=wall.kind,
                        loadBearing=wall.load_bearing,
                    )
                )
                for o in hosted:
                    inverse.append(Op(type="opening.add", payload=_opening_add_payload(o)))
            draft.walls.pop(idx)
            draft.openings = [o for o in draft.openings if o.wall_id != wall.id]
            draft.facade = replace(
                draft.facade,
                components=tuple(c for c in draft.facade.components if c.wall_id != wall.id),
            )
            _dirty(draft, wall.storey_id)

    elif t == "wall.set_thickness":
        idx = _index_of(draft.walls, g("wallId"))
        if idx >= 0:
            prev_wall = draft.walls[idx]
            push(
                op(
                    "wall.set_thickness",
                    wallId=prev_wall.id,
                    thicknessMm=prev_wall.thickness_mm,
                )
            )
            draft.walls[idx] = replace(prev_wall, thickness_mm=g("thicknessMm"))
            _dirty(draft, prev_wall.storey_id)

    # -------------------------------------------------------------- openings
    elif t == "opening.add":
        draft.openings.append(
            Opening(
                id=g("id"),
                wall_id=g("wallId"),
                kind=g("kind"),
                width_mm=g("widthMm"),
                height_mm=g("heightMm"),
                sill_mm=g("sillMm"),
                offset_mm=g("offsetMm"),
                swing=g("swing"),
                tag=_coalesce(g("tag"), None),
            )
        )
        _touch_wall(draft, g("wallId"))
        push(op("opening.delete", openingId=g("id")))

    elif t == "opening.move":
        idx = _index_of(draft.openings, g("openingId"))
        if idx >= 0:
            prev_opening = draft.openings[idx]
            push(
                op(
                    "opening.move",
                    openingId=prev_opening.id,
                    offsetMm=prev_opening.offset_mm,
                    wallId=prev_opening.wall_id,
                )
            )
            draft.openings[idx] = replace(
                prev_opening,
                offset_mm=g("offsetMm"),
                wall_id=_coalesce(g("wallId"), prev_opening.wall_id),
            )
            _touch_wall(draft, prev_opening.wall_id)
            _touch_wall(draft, g("wallId"))

    elif t == "opening.resize":
        idx = _index_of(draft.openings, g("openingId"))
        if idx >= 0:
            prev_opening = draft.openings[idx]
            push(
                op(
                    "opening.resize",
                    openingId=prev_opening.id,
                    widthMm=prev_opening.width_mm,
                    heightMm=prev_opening.height_mm,
                    sillMm=prev_opening.sill_mm,
                )
            )
            draft.openings[idx] = replace(
                prev_opening,
                width_mm=_coalesce(g("widthMm"), prev_opening.width_mm),
                height_mm=_coalesce(g("heightMm"), prev_opening.height_mm),
                sill_mm=_coalesce(g("sillMm"), prev_opening.sill_mm),
            )
            _touch_wall(draft, prev_opening.wall_id)

    elif t == "opening.flip":
        idx = _index_of(draft.openings, g("openingId"))
        if idx >= 0:
            prev_opening = draft.openings[idx]
            push(op("opening.flip", openingId=prev_opening.id, swing=prev_opening.swing))
            draft.openings[idx] = replace(prev_opening, swing=g("swing"))
            _touch_wall(draft, prev_opening.wall_id)

    elif t == "opening.delete":
        idx = _index_of(draft.openings, g("openingId"))
        if idx >= 0:
            prev_opening = draft.openings[idx]
            push(Op(type="opening.add", payload=_opening_add_payload(prev_opening)))
            _touch_wall(draft, prev_opening.wall_id)
            draft.openings.pop(idx)
            draft.facade = replace(
                draft.facade,
                components=tuple(
                    c for c in draft.facade.components if c.opening_id != prev_opening.id
                ),
            )

    # ----------------------------------------------------------------- rooms
    elif t == "room.assign":
        idx = _index_of(draft.rooms, g("roomId"))
        if idx >= 0:
            prev_room = draft.rooms[idx]
            push(
                op(
                    "room.assign",
                    roomId=prev_room.id,
                    type=prev_room.type,
                    name=prev_room.name,
                    tags=list(prev_room.tags),
                    locked=prev_room.locked,
                )
            )
            _touch(draft, prev_room.storey_id)
            draft.rooms[idx] = replace(
                prev_room,
                type=g("type"),
                name=_coalesce(g("name"), prev_room.name),
                tags=tuple(g("tags")) if g("tags") is not None else prev_room.tags,
                locked=_coalesce(g("locked"), prev_room.locked),
            )

    elif t == "room.set_target":
        idx = _index_of(draft.rooms, g("roomId"))
        if idx >= 0:
            prev_room = draft.rooms[idx]
            push(
                op(
                    "room.set_target",
                    roomId=prev_room.id,
                    targetAreaMm2=prev_room.target_area_mm2,
                    mustFace=prev_room.must_face,
                )
            )
            _touch(draft, prev_room.storey_id)
            draft.rooms[idx] = replace(
                prev_room,
                target_area_mm2=(p.get("targetAreaMm2", prev_room.target_area_mm2)),
                must_face=(p.get("mustFace", prev_room.must_face)),
            )

    # ---------------------------------------------------------------- stairs
    elif t == "stair.add":
        landing_raw = g("landing")
        draft.stairs.append(
            Stair(
                id=g("id"),
                storey_id=g("storeyId"),
                kind=g("kind"),
                origin=pt_of(g("origin")),
                direction=g("direction"),
                riser_mm=g("riserMm"),
                tread_mm=g("treadMm"),
                width_mm=g("widthMm"),
                risers_count=g("risersCount"),
                landing=None if landing_raw is None else StairLanding.from_json(landing_raw),
            )
        )
        _mark_storey_above_dirty(draft, g("storeyId"))
        push(op("stair.delete", stairId=g("id")))

    elif t == "stair.edit":
        idx = _index_of(draft.stairs, g("stairId"))
        if idx >= 0:
            prev_stair = draft.stairs[idx]
            patch: Mapping[str, Any] = g("patch")
            if want_inverse:
                inv_patch = {}
                if "kind" in patch:
                    inv_patch["kind"] = prev_stair.kind
                if "origin" in patch:
                    inv_patch["origin"] = {"x": prev_stair.origin.x, "y": prev_stair.origin.y}
                if "direction" in patch:
                    inv_patch["direction"] = prev_stair.direction
                if "riserMm" in patch:
                    inv_patch["riserMm"] = prev_stair.riser_mm
                if "treadMm" in patch:
                    inv_patch["treadMm"] = prev_stair.tread_mm
                if "widthMm" in patch:
                    inv_patch["widthMm"] = prev_stair.width_mm
                if "risersCount" in patch:
                    inv_patch["risersCount"] = prev_stair.risers_count
                if "landing" in patch:
                    inv_patch["landing"] = (
                        None if prev_stair.landing is None else to_jsonable(prev_stair.landing)
                    )
                inverse.append(op("stair.edit", stairId=prev_stair.id, patch=inv_patch))
            landing_patch = patch.get("landing") if "landing" in patch else None
            draft.stairs[idx] = replace(
                prev_stair,
                kind=_coalesce(patch.get("kind"), prev_stair.kind),
                origin=pt_of(patch["origin"])
                if patch.get("origin") is not None
                else prev_stair.origin,
                direction=_coalesce(patch.get("direction"), prev_stair.direction),
                riser_mm=_coalesce(patch.get("riserMm"), prev_stair.riser_mm),
                tread_mm=_coalesce(patch.get("treadMm"), prev_stair.tread_mm),
                width_mm=_coalesce(patch.get("widthMm"), prev_stair.width_mm),
                risers_count=_coalesce(patch.get("risersCount"), prev_stair.risers_count),
                landing=(
                    prev_stair.landing
                    if "landing" not in patch
                    else (None if landing_patch is None else StairLanding.from_json(landing_patch))
                ),
            )
            _mark_storey_above_dirty(draft, prev_stair.storey_id)

    elif t == "stair.delete":
        idx = _index_of(draft.stairs, g("stairId"))
        if idx >= 0:
            prev_stair = draft.stairs[idx]
            push(Op(type="stair.add", payload=_stair_add_payload(prev_stair)))
            draft.stairs.pop(idx)
            _mark_storey_above_dirty(draft, prev_stair.storey_id)

    # --------------------------------------------------------------- columns
    elif t == "column.set":
        idx = _index_of(draft.columns, g("id"))
        action = g("action")
        if action == "add":
            size_raw = g("sizeMm")
            column = Column(
                id=g("id"),
                storey_id=g("storeyId"),
                pt=pt_of(g("pt")),
                size_mm=DEFAULTS.column_size_mm if size_raw is None else SizeMm.from_json(size_raw),
            )
            draft.columns.append(column)
            push(op("column.set", action="delete", id=column.id))
        elif action == "move" and idx >= 0:
            prev_column = draft.columns[idx]
            push(
                op(
                    "column.set",
                    action="move",
                    id=prev_column.id,
                    pt={"x": prev_column.pt.x, "y": prev_column.pt.y},
                    sizeMm={"xMm": prev_column.size_mm.x_mm, "yMm": prev_column.size_mm.y_mm},
                )
            )
            size_raw = g("sizeMm")
            draft.columns[idx] = replace(
                prev_column,
                pt=pt_of(g("pt")) if g("pt") is not None else prev_column.pt,
                size_mm=prev_column.size_mm if size_raw is None else SizeMm.from_json(size_raw),
            )
        elif action == "delete" and idx >= 0:
            prev_column = draft.columns[idx]
            push(
                op(
                    "column.set",
                    action="add",
                    id=prev_column.id,
                    storeyId=prev_column.storey_id,
                    pt={"x": prev_column.pt.x, "y": prev_column.pt.y},
                    sizeMm={"xMm": prev_column.size_mm.x_mm, "yMm": prev_column.size_mm.y_mm},
                )
            )
            draft.columns.pop(idx)

    # ------------------------------------------------------------- furniture
    elif t == "furniture.set":
        idx = _index_of(draft.furniture, g("id"))
        action = g("action")
        if action == "place":
            item = FurnitureInstance(
                id=g("id"),
                storey_id=g("storeyId"),
                catalog_id=_coalesce(g("catalogId"), ""),
                pt=pt_of(g("pt")),
                rotation_deg=_coalesce(g("rotationDeg"), 0),
            )
            draft.furniture.append(item)
            push(op("furniture.set", action="delete", id=item.id))
        elif action == "transform" and idx >= 0:
            prev_item = draft.furniture[idx]
            push(
                op(
                    "furniture.set",
                    action="transform",
                    id=prev_item.id,
                    pt={"x": prev_item.pt.x, "y": prev_item.pt.y},
                    rotationDeg=prev_item.rotation_deg,
                )
            )
            draft.furniture[idx] = replace(
                prev_item,
                pt=pt_of(g("pt")) if g("pt") is not None else prev_item.pt,
                rotation_deg=_coalesce(g("rotationDeg"), prev_item.rotation_deg),
            )
        elif action == "delete" and idx >= 0:
            prev_item = draft.furniture[idx]
            push(
                op(
                    "furniture.set",
                    action="place",
                    id=prev_item.id,
                    storeyId=prev_item.storey_id,
                    catalogId=prev_item.catalog_id,
                    pt={"x": prev_item.pt.x, "y": prev_item.pt.y},
                    rotationDeg=prev_item.rotation_deg,
                )
            )
            draft.furniture.pop(idx)

    # ------------------------------------------------------------- balconies
    elif t == "balcony.set":
        idx = _index_of(draft.balconies, g("id"))
        action = g("action")
        if action == "add":
            balcony = Balcony(
                id=g("id"),
                storey_id=g("storeyId"),
                polygon=tuple(polygon_of(g("polygon") or [])),
                railing_kind=_coalesce(g("railingKind"), "ms"),
                railing_height_mm=_coalesce(g("railingHeightMm"), DEFAULTS.railing_height_mm),
                projection_mm=_coalesce(g("projectionMm"), DEFAULTS.balcony_projection_mm),
                slab_thickness_mm=_coalesce(g("slabThicknessMm"), DEFAULTS.slab_thickness_mm),
            )
            draft.balconies.append(balcony)
            push(op("balcony.set", action="delete", id=balcony.id))
        elif action == "edit" and idx >= 0:
            prev_balcony = draft.balconies[idx]
            push(
                op(
                    "balcony.set",
                    action="edit",
                    id=prev_balcony.id,
                    polygon=[{"x": q.x, "y": q.y} for q in prev_balcony.polygon],
                    railingKind=prev_balcony.railing_kind,
                    railingHeightMm=prev_balcony.railing_height_mm,
                    projectionMm=prev_balcony.projection_mm,
                    slabThicknessMm=prev_balcony.slab_thickness_mm,
                )
            )
            draft.balconies[idx] = replace(
                prev_balcony,
                polygon=(
                    tuple(polygon_of(g("polygon")))
                    if g("polygon") is not None
                    else prev_balcony.polygon
                ),
                railing_kind=_coalesce(g("railingKind"), prev_balcony.railing_kind),
                railing_height_mm=_coalesce(g("railingHeightMm"), prev_balcony.railing_height_mm),
                projection_mm=_coalesce(g("projectionMm"), prev_balcony.projection_mm),
                slab_thickness_mm=_coalesce(g("slabThicknessMm"), prev_balcony.slab_thickness_mm),
            )
        elif action == "delete" and idx >= 0:
            prev_balcony = draft.balconies[idx]
            push(
                op(
                    "balcony.set",
                    action="add",
                    id=prev_balcony.id,
                    storeyId=prev_balcony.storey_id,
                    polygon=[{"x": q.x, "y": q.y} for q in prev_balcony.polygon],
                    railingKind=prev_balcony.railing_kind,
                    railingHeightMm=prev_balcony.railing_height_mm,
                    projectionMm=prev_balcony.projection_mm,
                    slabThicknessMm=prev_balcony.slab_thickness_mm,
                )
            )
            draft.balconies.pop(idx)

    # ---------------------------------------------------------------- facade
    elif t == "facade.apply_kit":
        prev_facade = draft.facade
        push(
            op(
                "facade.apply_kit",
                kitId=prev_facade.kit_id,
                seed=prev_facade.seed,
                colorwayId=prev_facade.colorway_id,
                components=[to_jsonable(c) for c in prev_facade.components],
            )
        )
        components = tuple(FacadeComponent.from_json(c) for c in g("components"))
        draft.facade = FacadeModel(
            kit_id=g("kitId"),
            seed=g("seed"),
            colorway_id=_coalesce(g("colorwayId"), None),
            components=components,
        )

    elif t == "facade.edit_component":
        idx = _index_of(draft.facade.components, g("componentId"))
        if idx >= 0:
            prev_component = draft.facade.components[idx]
            push(
                op(
                    "facade.edit_component",
                    componentId=prev_component.id,
                    patch=invert_merge_patch(prev_component.params, g("patch")),
                )
            )
            components_list = list(draft.facade.components)
            components_list[idx] = replace(
                prev_component, params=apply_merge_patch(prev_component.params, g("patch"))
            )
            draft.facade = replace(draft.facade, components=tuple(components_list))

    # ------------------------------------------------------------- materials
    elif t == "material.assign":
        idx = _index_of(draft.materials, g("id"))
        if g("materialId") is None:
            if idx >= 0:
                prev_material = draft.materials[idx]
                push(
                    op(
                        "material.assign",
                        id=prev_material.id,
                        target=to_jsonable(prev_material.target),
                        materialId=prev_material.material_id,
                    )
                )
                draft.materials.pop(idx)
        elif idx >= 0:
            prev_material = draft.materials[idx]
            push(
                op(
                    "material.assign",
                    id=prev_material.id,
                    target=to_jsonable(prev_material.target),
                    materialId=prev_material.material_id,
                )
            )
            draft.materials[idx] = replace(
                prev_material,
                target=SurfaceGroupRef.from_json(g("target")),
                material_id=g("materialId"),
            )
        else:
            assignment = MaterialAssignment(
                id=g("id"),
                target=SurfaceGroupRef.from_json(g("target")),
                material_id=g("materialId"),
            )
            draft.materials.append(assignment)
            push(
                op(
                    "material.assign",
                    id=assignment.id,
                    target=to_jsonable(assignment.target),
                    materialId=None,
                )
            )

    # ---------------------------------------------------------------- levels
    elif t == "levels.set":
        prev_levels = draft.levels
        inv_payload: dict[str, Any] = {}
        if "plinthMm" in p:
            inv_payload["plinthMm"] = prev_levels.plinth_mm
        if "sillDefaultMm" in p:
            inv_payload["sillDefaultMm"] = prev_levels.sill_default_mm
        if "lintelDefaultMm" in p:
            inv_payload["lintelDefaultMm"] = prev_levels.lintel_default_mm
        if "parapetMm" in p:
            inv_payload["parapetMm"] = prev_levels.parapet_mm
        if "fflPerStoreyMm" in p:
            inv_payload["fflPerStoreyMm"] = list(prev_levels.ffl_per_storey_mm)
        push(Op(type="levels.set", payload=inv_payload))
        draft.levels = Levels(
            plinth_mm=_coalesce(g("plinthMm"), prev_levels.plinth_mm),
            ffl_per_storey_mm=(
                tuple(g("fflPerStoreyMm"))
                if g("fflPerStoreyMm") is not None
                else prev_levels.ffl_per_storey_mm
            ),
            sill_default_mm=_coalesce(g("sillDefaultMm"), prev_levels.sill_default_mm),
            lintel_default_mm=_coalesce(g("lintelDefaultMm"), prev_levels.lintel_default_mm),
            parapet_mm=_coalesce(g("parapetMm"), prev_levels.parapet_mm),
        )
        # An explicit FFL array wins; otherwise plinth changes re-derive them.
        draft.derive_levels = "fflPerStoreyMm" not in p

    # ---------------------------------------------------------------- solver
    elif t == "solver.apply_option":
        # Unreachable: fold() intercepts this op and delegates the expansion to
        # apply_group, so every inner op is validated against the intermediate
        # state instead of being trusted. Kept for switch completeness.
        pass

    # ----------------------------------------------------------- annotations
    elif t == "annotation.set":
        idx = _index_of(draft.annotations, g("id"))
        action = g("action")
        if action == "add":
            annotation = Annotation(
                id=g("id"),
                sheet_id=g("sheetId"),
                anchor_element_id=_coalesce(g("anchorElementId"), None),
                anchor_kind=_coalesce(g("anchorKind"), "sheet"),
                payload=dict(_coalesce(g("payload"), {})),
                orphaned=_coalesce(g("orphaned"), False),
            )
            draft.annotations.append(annotation)
            push(op("annotation.set", action="delete", id=annotation.id))
        elif action == "edit" and idx >= 0:
            prev_annotation = draft.annotations[idx]
            push(
                op(
                    "annotation.set",
                    action="edit",
                    id=prev_annotation.id,
                    anchorElementId=prev_annotation.anchor_element_id,
                    anchorKind=prev_annotation.anchor_kind,
                    payload=dict(prev_annotation.payload),
                    orphaned=prev_annotation.orphaned,
                )
            )
            draft.annotations[idx] = replace(
                prev_annotation,
                anchor_element_id=(p.get("anchorElementId", prev_annotation.anchor_element_id)),
                anchor_kind=_coalesce(g("anchorKind"), prev_annotation.anchor_kind),
                payload=dict(_coalesce(g("payload"), prev_annotation.payload)),
                orphaned=_coalesce(g("orphaned"), prev_annotation.orphaned),
            )
        elif action == "delete" and idx >= 0:
            prev_annotation = draft.annotations[idx]
            push(
                op(
                    "annotation.set",
                    action="add",
                    id=prev_annotation.id,
                    sheetId=prev_annotation.sheet_id,
                    anchorElementId=prev_annotation.anchor_element_id,
                    anchorKind=prev_annotation.anchor_kind,
                    payload=dict(prev_annotation.payload),
                    orphaned=prev_annotation.orphaned,
                )
            )
            draft.annotations.pop(idx)


def _dirty(draft: _Draft, storey_id: str) -> None:
    """Mark a storey as needing room+slab recomputation (and validation scope)."""
    draft.dirty_storeys.add(storey_id)
    draft.touched_storeys.add(storey_id)


def _touch(draft: _Draft, storey_id: str | None) -> None:
    """Mark a storey as touched (validation scope only — geometry did not change)."""
    if storey_id is not None:
        draft.touched_storeys.add(storey_id)


def _touch_wall(draft: _Draft, wall_id: str | None) -> None:
    """Validation scope for an op that edits an opening: the host wall's storey."""
    if wall_id is None:
        return
    wall = next((w for w in draft.walls if w.id == wall_id), None)
    _touch(draft, None if wall is None else wall.storey_id)


def _mark_storey_above_dirty(draft: _Draft, storey_id: str) -> None:
    """A stair penetrates the slab of the storey ABOVE it."""
    idx = _index_of(draft.storeys, storey_id)
    draft.touched_storeys.add(storey_id)
    if idx >= 0 and idx + 1 < len(draft.storeys):
        _dirty(draft, draft.storeys[idx + 1].id)


_STOREY_NAMES = ("Ground Floor", "First Floor", "Second Floor", "Third Floor", "Fourth Floor")


def _default_storey_name(index: int) -> str:
    if 0 <= index < len(_STOREY_NAMES):
        return _STOREY_NAMES[index]
    return f"Floor {index}"


def _opening_add_payload(o: Opening) -> dict[str, Any]:
    return {
        "id": o.id,
        "wallId": o.wall_id,
        "kind": o.kind,
        "widthMm": o.width_mm,
        "heightMm": o.height_mm,
        "sillMm": o.sill_mm,
        "offsetMm": o.offset_mm,
        "swing": o.swing,
        "tag": o.tag,
    }


def _stair_add_payload(s: Stair) -> dict[str, Any]:
    return {
        "id": s.id,
        "storeyId": s.storey_id,
        "kind": s.kind,
        "origin": {"x": s.origin.x, "y": s.origin.y},
        "direction": s.direction,
        "riserMm": s.riser_mm,
        "treadMm": s.tread_mm,
        "widthMm": s.width_mm,
        "risersCount": s.risers_count,
        "landing": None if s.landing is None else to_jsonable(s.landing),
    }


# ---------------------------------------------------------------------------
# Groups, replay, undo/redo
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupResult:
    model: ProjectDoc
    #: Inverse of the WHOLE group: reversed concatenation of per-op inverses.
    inverse: tuple[Op, ...]
    #: The ops as applied, with ``group_id`` stamped on each.
    ops: tuple[Op, ...]


def apply_group(model: ProjectDoc, ops: Sequence[Any], group_id: str | None = None) -> GroupResult:
    """Apply several ops ATOMICALLY.

    If any op is rejected, nothing is applied and the :class:`OpRejectedError`
    propagates. Undo/redo works on the group, not the ops.
    """
    current = model
    inverses: list[tuple[Op, ...]] = []
    applied: list[Op] = []
    for raw in ops:
        candidate = _as_op(raw)
        stamped = candidate if group_id is None else candidate.with_group(group_id)
        result = fold(current, stamped)
        current = result.model
        inverses.append(result.inverse)
        applied.append(stamped)
    inverse: list[Op] = []
    for i in range(len(inverses) - 1, -1, -1):
        for inv_op in inverses[i]:
            inverse.append(inv_op if group_id is None else inv_op.with_group(group_id))
    return GroupResult(model=current, inverse=tuple(inverse), ops=tuple(applied))


def replay(ops: Sequence[Any], initial: ProjectDoc | None = None) -> ProjectDoc:
    """Fold an op log from ``initial`` (default: an empty document)."""
    current = empty_project_doc() if initial is None else initial
    for raw in ops:
        current = fold(current, raw, compute_inverse=False).model
    return current


@dataclass(frozen=True)
class UndoEntry:
    """One undoable unit of work."""

    group_id: str
    #: The ops as applied (redo replays these).
    ops: tuple[Op, ...]
    #: The ops that undo them, in order.
    inverse: tuple[Op, ...]
    #: Short label for the undo toast: "Wall deleted".
    label: str | None = None


class UndoStack:
    """Undo/redo over GROUPS (section 4 batching).

    Holds only op lists, never snapshots, so a 1000-step history costs kilobytes.
    The stack is a value object: :meth:`undo`/:meth:`redo` take the current model
    and return the new one, so the caller stays the single writer.
    """

    def __init__(self, limit: int = 200) -> None:
        self._undo: list[UndoEntry] = []
        self._redo: list[UndoEntry] = []
        self._limit = limit

    def push(self, entry: UndoEntry) -> None:
        """Record an applied group. Clears the redo stack (new branch of history)."""
        self._undo.append(entry)
        if len(self._undo) > self._limit:
            self._undo.pop(0)
        self._redo = []

    @property
    def can_undo(self) -> bool:
        return len(self._undo) > 0

    @property
    def can_redo(self) -> bool:
        return len(self._redo) > 0

    @property
    def undo_depth(self) -> int:
        return len(self._undo)

    @property
    def redo_depth(self) -> int:
        return len(self._redo)

    @property
    def next_undo_label(self) -> str | None:
        """Label of the group that would be undone next (for the toast/menu)."""
        return self._undo[-1].label if self._undo else None

    @property
    def next_redo_label(self) -> str | None:
        return self._redo[-1].label if self._redo else None

    def undo(self, model: ProjectDoc) -> tuple[ProjectDoc, UndoEntry] | None:
        """Apply the inverse of the last group, or ``None`` when there is nothing."""
        if not self._undo:
            return None
        entry = self._undo.pop()
        result = apply_group(model, entry.inverse, entry.group_id)
        self._redo.append(entry)
        return result.model, entry

    def redo(self, model: ProjectDoc) -> tuple[ProjectDoc, UndoEntry] | None:
        """Re-apply the last undone group, or ``None`` when there is nothing."""
        if not self._redo:
            return None
        entry = self._redo.pop()
        result = apply_group(model, entry.ops, entry.group_id)
        self._undo.append(entry)
        return result.model, entry

    def clear(self) -> None:
        self._undo = []
        self._redo = []

    def to_json(self) -> dict[str, list[dict[str, Any]]]:
        """Serialisable snapshot of the history (for session restore)."""
        return {
            "undo": [_undo_entry_json(e) for e in self._undo],
            "redo": [_undo_entry_json(e) for e in self._redo],
        }

    @staticmethod
    def from_json(data: Mapping[str, Any], limit: int = 200) -> UndoStack:
        stack = UndoStack(limit)
        stack._undo = [_undo_entry_from_json(e) for e in data.get("undo", [])]
        stack._redo = [_undo_entry_from_json(e) for e in data.get("redo", [])]
        return stack


def _undo_entry_json(entry: UndoEntry) -> dict[str, Any]:
    out: dict[str, Any] = {
        "groupId": entry.group_id,
        "ops": [o.to_json() for o in entry.ops],
        "inverse": [o.to_json() for o in entry.inverse],
    }
    if entry.label is not None:
        out["label"] = entry.label
    return out


def _undo_entry_from_json(raw: Mapping[str, Any]) -> UndoEntry:
    return UndoEntry(
        group_id=str(raw["groupId"]),
        ops=tuple(Op.from_json(o) for o in raw.get("ops", [])),
        inverse=tuple(Op.from_json(o) for o in raw.get("inverse", [])),
        label=raw.get("label"),
    )


# ---------------------------------------------------------------------------
# Misc derived reads that need fold's helpers
# ---------------------------------------------------------------------------


def wall_length_mm(wall: Wall) -> int:
    """Clear length of a wall centreline in mm."""
    return segment_length_mm(Seg(wall.a, wall.b))


def storey_carpet_area_mm2(doc: ProjectDoc, storey_id: str) -> int:
    """Total floor area of a storey's rooms in mm^2 (carpet area)."""
    return sum(r.area_mm2 for r in doc.house.rooms if r.storey_id == storey_id)


def storey_built_up_area_mm2(doc: ProjectDoc, storey_id: str) -> int:
    """Built-up area of a storey in mm^2 (slab minus cut-outs)."""
    total = 0
    for slab in doc.house.slabs:
        if slab.storey_id != storey_id or slab.kind != "floor":
            continue
        total += polygon_area_mm2(slab.polygon)
        for cut in slab.cutouts:
            total -= polygon_area_mm2(cut)
    return total


def locked_room_ids(doc: ProjectDoc) -> list[str]:
    """Ids of rooms currently locked against solver re-solve (section 5.7)."""
    return [r.id for r in doc.house.rooms if r.locked]


def assert_schema_version(doc: ProjectDoc) -> None:
    """Assert the document is at the schema version this build understands."""
    if doc.schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"Document schemaVersion {doc.schema_version} is not supported by this build "
            f"(expected {SCHEMA_VERSION})."
        )
