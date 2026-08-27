"""validate.py — the fold invariants (playbook section 3) with MACHINE-READABLE
rejection reasons.

Mirror of ``packages/model/src/validate.ts``. The codes in
:data:`VALIDATION_CODES` are a CROSS-LANGUAGE API: the copilot pipeline (section
10) dry-run-folds candidate ops and feeds the rejection reasons back to the LLM
for one self-correction pass, and the API surfaces the same strings in its
problem+json ``code``. Never raise a bare string out of this module; never change
a code without changing the copilot fixtures on both sides.

Invariants enforced (section 3, verbatim):

* walls have non-zero length
* openings fit within the host wall length minus 115mm end margins
* opening sill + height <= storey height
* stairs' ``risersCount * riserMm`` ~= storey height +/-10mm
* no two walls exactly overlap
* rooms closed

plus the structural preconditions an op needs to be applicable at all
(referenced element exists, id not already taken, integer mm everywhere).

ABSENT vs NULL: an op payload key that is missing means "unchanged"
(TypeScript ``undefined``); a key present with ``None`` means JSON ``null``.
``_has()`` below is membership only, exactly like the TypeScript ``has()`` which
treats ``null`` as present.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .geometry import (
    Pt,
    Seg,
    collinear_overlap,
    polygon_area_mm2,
    polygon_is_closed_ring,
    pt_eq,
    segment_length_mm,
)
from .ids import is_id_of, try_parse_id
from .model import (
    ANNOTATION_ANCHOR_KINDS,
    DIRECTIONS_4,
    DIRECTIONS_8,
    FACADE_COMPONENT_KINDS,
    OPENING_KINDS,
    OPENING_SWINGS,
    RAILING_KINDS,
    ROOM_TYPES,
    STAIR_KINDS,
    SURFACE_GROUPS,
    VASTU_MODES,
    WALL_KINDS,
    ProjectDoc,
)
from .ops import (
    ANNOTATION_ACTIONS,
    BALCONY_ACTIONS,
    COLUMN_ACTIONS,
    FURNITURE_ACTIONS,
    Op,
    get_op_spec,
    op_type_of,
    payload_of,
)
from .units import is_int_mm, round_half_away_from_zero

__all__ = [
    "WALL_END_MARGIN_MM",
    "STAIR_RISE_TOLERANCE_MM",
    "MAX_WALL_THICKNESS_MM",
    "MIN_ROOM_AREA_MM2",
    "VALIDATION_CODES",
    "ValidationCode",
    "Severity",
    "ValidationIssue",
    "OpRejectedError",
    "validate_op_shape",
    "validate_op_against_doc",
    "validate_model",
    "is_acceptable",
    "issues_by_code",
    "render_issues_for_llm",
    "MODEL_INVARIANT_CODES",
    "assert_valid_model",
    "is_pt_like",
    "pt_of",
    "polygon_of",
]

#: Section 3: openings must keep this much solid wall at each end.
WALL_END_MARGIN_MM = 115

#: Section 3: risersCount * riserMm must match storey height within this.
STAIR_RISE_TOLERANCE_MM = 10

#: Sanity ceiling on a wall thickness (a 1m wall is a data-entry error).
MAX_WALL_THICKNESS_MM = 1000

#: Rooms smaller than this are noise from the planar subdivision, not rooms.
MIN_ROOM_AREA_MM2 = 500_000  # 0.5 m^2

#: Every rejection code. STABLE API — the copilot, the UI error copy map and the
#: API problem+json ``code`` field all key off these strings.
VALIDATION_CODES: tuple[str, ...] = (
    # --- op envelope / payload
    "OP_UNKNOWN_TYPE",
    "OP_PAYLOAD_NOT_OBJECT",
    "OP_FIELD_MISSING",
    "OP_FIELD_NOT_INT_MM",
    "OP_FIELD_NOT_INT",
    "OP_FIELD_NOT_STRING",
    "OP_FIELD_NOT_OBJECT",
    "OP_FIELD_BAD_ENUM",
    "OP_FIELD_BAD_ID",
    "OP_FIELD_BAD_POINT",
    "OP_FIELD_BAD_POLYGON",
    "OP_FIELD_OUT_OF_RANGE",
    "OP_ACTION_UNKNOWN",
    "OP_ID_ALREADY_EXISTS",
    # --- referenced elements
    "STOREY_UNKNOWN",
    "STOREY_INDEX_OUT_OF_RANGE",
    "WALL_UNKNOWN",
    "OPENING_UNKNOWN",
    "ROOM_UNKNOWN",
    "STAIR_UNKNOWN",
    "COLUMN_UNKNOWN",
    "FURNITURE_UNKNOWN",
    "BALCONY_UNKNOWN",
    "FACADE_COMPONENT_UNKNOWN",
    "MATERIAL_ASSIGNMENT_UNKNOWN",
    "ANNOTATION_UNKNOWN",
    "PLOT_EDGE_UNKNOWN",
    # --- model invariants (section 3)
    "WALL_ZERO_LENGTH",
    "WALL_THICKNESS_INVALID",
    "WALL_DUPLICATE",
    "WALL_SPLIT_OUT_OF_RANGE",
    "OPENING_DIMENSION_INVALID",
    "OPENING_OUT_OF_WALL",
    "OPENING_EXCEEDS_STOREY_HEIGHT",
    "OPENING_SILL_INVALID",
    "STAIR_RISE_MISMATCH",
    "STAIR_DIMENSION_INVALID",
    "ROOM_NOT_CLOSED",
    "STOREY_HEIGHT_INVALID",
    "PLOT_BOUNDARY_NOT_CLOSED",
    "PLOT_NORTH_INVALID",
    "LEVELS_INVALID",
    "BALCONY_POLYGON_INVALID",
    "COLUMN_SIZE_INVALID",
    "DUPLICATE_ELEMENT_ID",
    "SCHEMA_VERSION_UNSUPPORTED",
)

ValidationCode = str
Severity = str


@dataclass(frozen=True)
class ValidationIssue:
    """One machine-readable rejection reason.

    ``message`` is user-facing copy (Golden Rule 9: say what to do next).

    ``field`` / ``actual`` / ``limit`` / ``fix`` are ``None`` when absent. The
    TypeScript version can also carry an explicit ``actual: null``; this mirror
    treats ``None`` as "not present" (nothing in either implementation emits an
    explicit null, so the two serialise identically).
    """

    code: ValidationCode
    message: str
    severity: Severity = "error"
    #: Element ids the issue is about — drives canvas highlighting.
    element_ids: tuple[str, ...] = ()
    #: Payload path the issue is about, e.g. ``payload.widthMm``.
    field: str | None = None
    actual: Any | None = None
    limit: Any | None = None
    #: One-line suggestion the copilot can act on.
    fix: str | None = None

    def to_json(self) -> dict[str, Any]:
        """The wire shape (matches ``schema/validation-issue.schema.json``)."""
        out: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "elementIds": list(self.element_ids),
        }
        if self.field is not None:
            out["field"] = self.field
        if self.actual is not None:
            out["actual"] = self.actual
        if self.limit is not None:
            out["limit"] = self.limit
        if self.fix is not None:
            out["fix"] = self.fix
        return out


class OpRejectedError(Exception):
    """Raised by :func:`garh_model.fold.fold` when an op cannot be applied."""

    code = "OP_REJECTED"
    http_status = 422

    def __init__(self, op_type: str, issues: Sequence[ValidationIssue]) -> None:
        first = issues[0] if issues else None
        detail = f"{first.code} — {first.message}" if first is not None else "unknown reason"
        super().__init__(f"Op {op_type} rejected: {detail}")
        self.op_type = op_type
        self.issues: tuple[ValidationIssue, ...] = tuple(issues)

    def as_problem(self) -> dict[str, Any]:
        """RFC 7807 problem+json body (the API returns this verbatim)."""
        return {
            "type": "https://garh.ai/problems/op-rejected",
            "title": "Op rejected",
            "status": self.http_status,
            "code": self.code,
            "opType": self.op_type,
            "issues": [i.to_json() for i in self.issues],
        }


def _issue(
    code: ValidationCode,
    message: str,
    *,
    severity: Severity = "error",
    element_ids: Sequence[str] = (),
    field: str | None = None,
    actual: Any | None = None,
    limit: Any | None = None,
    fix: str | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        message=message,
        severity=severity,
        element_ids=tuple(element_ids),
        field=field,
        actual=actual,
        limit=limit,
        fix=fix,
    )


# ---------------------------------------------------------------------------
# Small field checkers (shared by op-shape validation)
# ---------------------------------------------------------------------------


def _js_string(value: Any) -> str:
    """What JavaScript's ``String(value)`` would print — keeps messages identical."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, list | tuple):
        return ",".join(_js_string(v) for v in value)
    return "[object Object]"


def _actual(value: Any) -> Any:
    """Numbers survive as numbers; everything else becomes its JS string form."""
    if isinstance(value, bool):
        return _js_string(value)
    if isinstance(value, int | float):
        return value
    return _js_string(value)


def _is_plain_object(v: Any) -> bool:
    return isinstance(v, Mapping)


def is_pt_like(v: Any) -> bool:
    """True for ``{x, y}`` in whole millimetres (or a :class:`Pt`)."""
    if isinstance(v, Pt):
        return True
    if not _is_plain_object(v):
        return False
    return is_int_mm(v.get("x")) and is_int_mm(v.get("y"))


def pt_of(v: Any) -> Pt:
    """Coerce the wire form ``{x, y}`` (or a :class:`Pt`) to a :class:`Pt`."""
    if isinstance(v, Pt):
        return v
    return Pt(int(v["x"]), int(v["y"]))


def polygon_of(v: Any) -> list[Pt]:
    """Coerce a wire polygon (list of ``{x, y}``) to a list of :class:`Pt`."""
    return [pt_of(p) for p in v]


def _check_int_mm(
    out: list[ValidationIssue],
    value: Any,
    field: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> bool:
    if not is_int_mm(value):
        out.append(
            _issue(
                "OP_FIELD_NOT_INT_MM",
                f"{field} must be a whole number of millimetres.",
                field=field,
                actual=_actual(value),
                fix=f"Send {field} as an integer count of millimetres (e.g. 3810 for 12'-6\").",
            )
        )
        return False
    if minimum is not None and value < minimum:
        out.append(
            _issue(
                "OP_FIELD_OUT_OF_RANGE",
                f"{field} must be at least {minimum}mm.",
                field=field,
                actual=value,
                limit=minimum,
            )
        )
        return False
    if maximum is not None and value > maximum:
        out.append(
            _issue(
                "OP_FIELD_OUT_OF_RANGE",
                f"{field} must be at most {maximum}mm.",
                field=field,
                actual=value,
                limit=maximum,
            )
        )
        return False
    return True


def _is_safe_int(value: Any) -> bool:
    return is_int_mm(value)


def _check_int(
    out: list[ValidationIssue],
    value: Any,
    field: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> bool:
    if not _is_safe_int(value):
        out.append(
            _issue(
                "OP_FIELD_NOT_INT",
                f"{field} must be an integer.",
                field=field,
                actual=_actual(value),
            )
        )
        return False
    if minimum is not None and value < minimum:
        out.append(
            _issue(
                "OP_FIELD_OUT_OF_RANGE",
                f"{field} must be at least {minimum}.",
                field=field,
                actual=value,
                limit=minimum,
            )
        )
        return False
    if maximum is not None and value > maximum:
        out.append(
            _issue(
                "OP_FIELD_OUT_OF_RANGE",
                f"{field} must be at most {maximum}.",
                field=field,
                actual=value,
                limit=maximum,
            )
        )
        return False
    return True


def _check_string(out: list[ValidationIssue], value: Any, field: str) -> bool:
    if not isinstance(value, str):
        out.append(_issue("OP_FIELD_NOT_STRING", f"{field} must be a string.", field=field))
        return False
    return True


def _check_enum(out: list[ValidationIssue], value: Any, field: str, allowed: Sequence[str]) -> bool:
    if not isinstance(value, str) or value not in allowed:
        out.append(
            _issue(
                "OP_FIELD_BAD_ENUM",
                f"{field} must be one of: {', '.join(allowed)}.",
                field=field,
                actual=_actual(value),
                limit="|".join(allowed),
            )
        )
        return False
    return True


def _check_id(out: list[ValidationIssue], value: Any, field: str, element_type: str) -> bool:
    if not is_id_of(element_type, value):
        out.append(
            _issue(
                "OP_FIELD_BAD_ID",
                f"{field} must be a {element_type} id of the form {element_type}_<ulid>.",
                field=field,
                actual=_actual(value),
                limit=f"{element_type}_<ulid>",
            )
        )
        return False
    return True


def _check_pt(out: list[ValidationIssue], value: Any, field: str) -> bool:
    if not is_pt_like(value):
        out.append(
            _issue(
                "OP_FIELD_BAD_POINT",
                f"{field} must be {{ x, y }} in whole millimetres.",
                field=field,
            )
        )
        return False
    return True


def _check_polygon(out: list[ValidationIssue], value: Any, field: str) -> bool:
    if (
        not isinstance(value, list | tuple)
        or len(value) < 3
        or not all(is_pt_like(p) for p in value)
    ):
        out.append(
            _issue(
                "OP_FIELD_BAD_POLYGON",
                f"{field} must be at least 3 integer-mm points.",
                field=field,
            )
        )
        return False
    if not polygon_is_closed_ring(polygon_of(value)):
        out.append(
            _issue(
                "OP_FIELD_BAD_POLYGON",
                f"{field} must be a closed simple ring with non-zero area (no self-intersections, "
                "no repeated vertices).",
                field=field,
                fix="Remove crossing edges or duplicate points.",
            )
        )
        return False
    return True


def _check_object(out: list[ValidationIssue], value: Any, field: str) -> bool:
    if not _is_plain_object(value):
        out.append(_issue("OP_FIELD_NOT_OBJECT", f"{field} must be an object.", field=field))
        return False
    return True


def _check_json_integral(out: list[ValidationIssue], value: Any, field: str) -> bool:
    """Free-form JSON must hold no floats.

    Brief patches, reg-profile overrides, facade params and annotation payloads
    end up inside the document, and ``canonical_json`` refuses to serialise a
    float. Catch it HERE, where we can name the field and suggest a fix, instead
    of at hash time where the only symptom is a raised exception.
    """
    ok = True

    def walk(v: Any, path: str) -> None:
        nonlocal ok
        if v is None or isinstance(v, str | bool):
            return
        if isinstance(v, int | float):
            if not is_int_mm(v):
                ok = False
                out.append(
                    _issue(
                        "OP_FIELD_NOT_INT",
                        f"{path} must be a whole number — this document holds no floats.",
                        field=path,
                        actual=_actual(v),
                        fix=(
                            "Scale the value to an integer (whole rupees, mm, tenths of a degree, "
                            "basis points)."
                        ),
                    )
                )
            return
        if isinstance(v, list | tuple):
            for i, item in enumerate(v):
                walk(item, f"{path}[{i}]")
            return
        if _is_plain_object(v):
            for key in v:
                walk(v[key], f"{path}.{key}")
            return
        ok = False
        out.append(
            _issue(
                "OP_FIELD_NOT_OBJECT",
                f"{path} must be JSON (null, boolean, integer, string, array or object).",
                field=path,
            )
        )

    walk(value, field)
    return ok


def _has(payload: Mapping[str, Any], key: str) -> bool:
    """Key present at all — an explicit ``null`` counts, exactly like the TS ``has``."""
    return key in payload


def _require_field(
    out: list[ValidationIssue], payload: Mapping[str, Any], key: str, op_type: str
) -> bool:
    if not _has(payload, key):
        out.append(
            _issue(
                "OP_FIELD_MISSING",
                f"{op_type} needs payload.{key}.",
                field=f"payload.{key}",
                fix=f"Add {key} to the payload.",
            )
        )
        return False
    return True


def _coalesce(value: Any, fallback: Any) -> Any:
    """Mirror of the ``??`` operator: ``None`` (null/undefined) falls back."""
    return fallback if value is None else value


# ---------------------------------------------------------------------------
# Op shape validation (no document needed)
# ---------------------------------------------------------------------------


def validate_op_shape(candidate: Any) -> list[ValidationIssue]:
    """Validate an op's SHAPE: known type, required fields present, ids
    well-formed, lengths integer mm, enums legal. Does not look at the document.

    Accepts an :class:`~garh_model.ops.Op` or the raw wire dict.
    """
    out: list[ValidationIssue] = []
    if not isinstance(candidate, Op) and not _is_plain_object(candidate):
        return [_issue("OP_PAYLOAD_NOT_OBJECT", "An op must be an object { type, payload }.")]
    op_type = op_type_of(candidate)
    if op_type is None or get_op_spec(op_type) is None:
        raw_type = candidate.type if isinstance(candidate, Op) else candidate.get("type")
        printable = json.dumps(raw_type) if isinstance(raw_type, str) else _js_string(raw_type)
        return [
            _issue(
                "OP_UNKNOWN_TYPE",
                f"Unknown op type {printable}.",
                field="type",
                actual=_actual(raw_type),
                fix="Use one of the op types in OP_CATALOG.",
            )
        ]
    p = payload_of(candidate)
    if p is None:
        return [
            _issue("OP_PAYLOAD_NOT_OBJECT", f"{op_type} needs an object payload.", field="payload")
        ]

    def f(k: str) -> str:
        return f"payload.{k}"

    def g(k: str) -> Any:
        return p.get(k, None)

    t = op_type
    if t == "plot.set_boundary":
        # An empty polygon is the legal "clear the boundary" form — it is what the
        # inverse of the FIRST plot.set_boundary has to be.
        if _require_field(out, p, "polygon", t):
            if not isinstance(g("polygon"), list | tuple):
                out.append(
                    _issue(
                        "OP_FIELD_BAD_POLYGON",
                        "payload.polygon must be an array of points.",
                        field=f("polygon"),
                    )
                )
            elif len(g("polygon")) > 0:
                _check_polygon(out, g("polygon"), f("polygon"))
        if _has(p, "source"):
            _check_string(out, g("source"), f("source"))
    elif t == "plot.set_north":
        if _require_field(out, p, "deg", t):
            _check_int(out, g("deg"), f("deg"), 0, 359)
    elif t == "plot.set_road":
        if _require_field(out, p, "edgeIndex", t):
            _check_int(out, g("edgeIndex"), f("edgeIndex"), 0)
        if _require_field(out, p, "widthMm", t) and g("widthMm") is not None:
            _check_int_mm(out, g("widthMm"), f("widthMm"), 1)
        if _has(p, "name") and g("name") is not None:
            _check_string(out, g("name"), f("name"))
    elif t == "plot.set_reg_profile":
        if _require_field(out, p, "cityPack", t) and g("cityPack") is not None:
            _check_string(out, g("cityPack"), f("cityPack"))
        if _require_field(out, p, "overrides", t) and _check_object(
            out, g("overrides"), f("overrides")
        ):
            _check_json_integral(out, g("overrides"), f("overrides"))
    elif t == "brief.update":
        if _require_field(out, p, "patch", t) and _check_object(out, g("patch"), f("patch")):
            _check_json_integral(out, g("patch"), f("patch"))
        if _has(p, "vastuMode"):
            _check_enum(out, g("vastuMode"), f("vastuMode"), VASTU_MODES)
        if _has(p, "completeness"):
            _check_int(out, g("completeness"), f("completeness"), 0, 100)
    elif t == "storey.add":
        if _require_field(out, p, "id", t):
            _check_id(out, g("id"), f("id"), "storey")
        if _require_field(out, p, "index", t):
            _check_int(out, g("index"), f("index"), 0)
        if _require_field(out, p, "heightMm", t):
            _check_int_mm(out, g("heightMm"), f("heightMm"), 1800, 12000)
        if _has(p, "name"):
            _check_string(out, g("name"), f("name"))
        if _has(p, "level") and _check_object(out, g("level"), f("level")):
            lvl = g("level")
            _check_int_mm(out, lvl.get("fflMm"), f("level.fflMm"))
            _check_int_mm(out, lvl.get("slabThicknessMm"), f("level.slabThicknessMm"), 1)
    elif t == "storey.remove":
        if _require_field(out, p, "index", t):
            _check_int(out, g("index"), f("index"), 0)
    elif t == "storey.set_height":
        if _require_field(out, p, "storeyId", t):
            _check_id(out, g("storeyId"), f("storeyId"), "storey")
        if _require_field(out, p, "heightMm", t):
            _check_int_mm(out, g("heightMm"), f("heightMm"), 1800, 12000)
    elif t == "wall.add":
        if _require_field(out, p, "id", t):
            _check_id(out, g("id"), f("id"), "wall")
        if _require_field(out, p, "storeyId", t):
            _check_id(out, g("storeyId"), f("storeyId"), "storey")
        ok_a = _require_field(out, p, "a", t) and _check_pt(out, g("a"), f("a"))
        ok_b = _require_field(out, p, "b", t) and _check_pt(out, g("b"), f("b"))
        if ok_a and ok_b and pt_eq(pt_of(g("a")), pt_of(g("b"))):
            out.append(
                _issue(
                    "WALL_ZERO_LENGTH",
                    "A wall needs two different endpoints.",
                    element_ids=[_js_string(g("id"))],
                    field=f("b"),
                    fix="Give the wall a non-zero length.",
                )
            )
        if _require_field(out, p, "thicknessMm", t):
            _check_int_mm(out, g("thicknessMm"), f("thicknessMm"), 1, MAX_WALL_THICKNESS_MM)
        if _require_field(out, p, "kind", t):
            _check_enum(out, g("kind"), f("kind"), WALL_KINDS)
    elif t == "wall.move":
        if _require_field(out, p, "wallId", t):
            _check_id(out, g("wallId"), f("wallId"), "wall")
        ok_a = _require_field(out, p, "a", t) and _check_pt(out, g("a"), f("a"))
        ok_b = _require_field(out, p, "b", t) and _check_pt(out, g("b"), f("b"))
        if ok_a and ok_b and pt_eq(pt_of(g("a")), pt_of(g("b"))):
            out.append(
                _issue(
                    "WALL_ZERO_LENGTH",
                    "A wall needs two different endpoints.",
                    element_ids=[_js_string(g("wallId"))],
                    field=f("b"),
                )
            )
    elif t == "wall.split":
        if _require_field(out, p, "wallId", t):
            _check_id(out, g("wallId"), f("wallId"), "wall")
        if _require_field(out, p, "newWallId", t):
            _check_id(out, g("newWallId"), f("newWallId"), "wall")
        if _require_field(out, p, "atMm", t):
            _check_int_mm(out, g("atMm"), f("atMm"), 1)
    elif t == "wall.delete":
        if _require_field(out, p, "wallId", t):
            _check_id(out, g("wallId"), f("wallId"), "wall")
    elif t == "wall.set_thickness":
        if _require_field(out, p, "wallId", t):
            _check_id(out, g("wallId"), f("wallId"), "wall")
        if _require_field(out, p, "thicknessMm", t):
            _check_int_mm(out, g("thicknessMm"), f("thicknessMm"), 1, MAX_WALL_THICKNESS_MM)
    elif t == "opening.add":
        if _require_field(out, p, "id", t):
            _check_id(out, g("id"), f("id"), "opening")
        if _require_field(out, p, "wallId", t):
            _check_id(out, g("wallId"), f("wallId"), "wall")
        if _require_field(out, p, "kind", t):
            _check_enum(out, g("kind"), f("kind"), OPENING_KINDS)
        if _require_field(out, p, "widthMm", t):
            _check_int_mm(out, g("widthMm"), f("widthMm"), 1)
        if _require_field(out, p, "heightMm", t):
            _check_int_mm(out, g("heightMm"), f("heightMm"), 1)
        if _require_field(out, p, "sillMm", t):
            _check_int_mm(out, g("sillMm"), f("sillMm"), 0)
        if _require_field(out, p, "offsetMm", t):
            _check_int_mm(out, g("offsetMm"), f("offsetMm"), 0)
        if _require_field(out, p, "swing", t):
            _check_enum(out, g("swing"), f("swing"), OPENING_SWINGS)
        if _has(p, "tag") and g("tag") is not None:
            _check_string(out, g("tag"), f("tag"))
    elif t == "opening.move":
        if _require_field(out, p, "openingId", t):
            _check_id(out, g("openingId"), f("openingId"), "opening")
        if _require_field(out, p, "offsetMm", t):
            _check_int_mm(out, g("offsetMm"), f("offsetMm"), 0)
        if _has(p, "wallId"):
            _check_id(out, g("wallId"), f("wallId"), "wall")
    elif t == "opening.resize":
        if _require_field(out, p, "openingId", t):
            _check_id(out, g("openingId"), f("openingId"), "opening")
        if _has(p, "widthMm"):
            _check_int_mm(out, g("widthMm"), f("widthMm"), 1)
        if _has(p, "heightMm"):
            _check_int_mm(out, g("heightMm"), f("heightMm"), 1)
        if _has(p, "sillMm"):
            _check_int_mm(out, g("sillMm"), f("sillMm"), 0)
        if not _has(p, "widthMm") and not _has(p, "heightMm") and not _has(p, "sillMm"):
            out.append(
                _issue(
                    "OP_FIELD_MISSING",
                    "opening.resize needs at least one of widthMm, heightMm, sillMm.",
                    field="payload",
                )
            )
    elif t == "opening.flip":
        if _require_field(out, p, "openingId", t):
            _check_id(out, g("openingId"), f("openingId"), "opening")
        if _require_field(out, p, "swing", t):
            _check_enum(out, g("swing"), f("swing"), OPENING_SWINGS)
    elif t == "opening.delete":
        if _require_field(out, p, "openingId", t):
            _check_id(out, g("openingId"), f("openingId"), "opening")
    elif t == "room.assign":
        if _require_field(out, p, "roomId", t):
            _check_id(out, g("roomId"), f("roomId"), "room")
        if _require_field(out, p, "type", t):
            _check_enum(out, g("type"), f("type"), ROOM_TYPES)
        if _has(p, "name"):
            _check_string(out, g("name"), f("name"))
        if _has(p, "tags"):
            tags = g("tags")
            if not isinstance(tags, list | tuple) or not all(isinstance(x, str) for x in tags):
                out.append(
                    _issue(
                        "OP_FIELD_NOT_STRING",
                        "payload.tags must be an array of strings.",
                        field=f("tags"),
                    )
                )
    elif t == "room.set_target":
        if _require_field(out, p, "roomId", t):
            _check_id(out, g("roomId"), f("roomId"), "room")
        if _has(p, "targetAreaMm2") and g("targetAreaMm2") is not None:
            _check_int(out, g("targetAreaMm2"), f("targetAreaMm2"), 1)
        if _has(p, "mustFace") and g("mustFace") is not None:
            _check_enum(out, g("mustFace"), f("mustFace"), DIRECTIONS_8)
    elif t == "stair.add":
        if _require_field(out, p, "id", t):
            _check_id(out, g("id"), f("id"), "stair")
        if _require_field(out, p, "storeyId", t):
            _check_id(out, g("storeyId"), f("storeyId"), "storey")
        if _require_field(out, p, "kind", t):
            _check_enum(out, g("kind"), f("kind"), STAIR_KINDS)
        if _require_field(out, p, "origin", t):
            _check_pt(out, g("origin"), f("origin"))
        if _require_field(out, p, "direction", t):
            _check_enum(out, g("direction"), f("direction"), DIRECTIONS_4)
        if _require_field(out, p, "riserMm", t):
            _check_int_mm(out, g("riserMm"), f("riserMm"), 50, 400)
        if _require_field(out, p, "treadMm", t):
            _check_int_mm(out, g("treadMm"), f("treadMm"), 100, 600)
        if _require_field(out, p, "widthMm", t):
            _check_int_mm(out, g("widthMm"), f("widthMm"), 300)
        if _require_field(out, p, "risersCount", t):
            _check_int(out, g("risersCount"), f("risersCount"), 2, 60)
        if (
            _has(p, "landing")
            and g("landing") is not None
            and _check_object(out, g("landing"), f("landing"))
        ):
            landing = g("landing")
            _check_int_mm(out, landing.get("widthMm"), f("landing.widthMm"), 1)
            _check_int_mm(out, landing.get("depthMm"), f("landing.depthMm"), 1)
    elif t == "stair.edit":
        if _require_field(out, p, "stairId", t):
            _check_id(out, g("stairId"), f("stairId"), "stair")
        if _require_field(out, p, "patch", t) and _check_object(out, g("patch"), f("patch")):
            patch = g("patch")
            if _has(patch, "kind"):
                _check_enum(out, patch["kind"], f("patch.kind"), STAIR_KINDS)
            if _has(patch, "origin"):
                _check_pt(out, patch["origin"], f("patch.origin"))
            if _has(patch, "direction"):
                _check_enum(out, patch["direction"], f("patch.direction"), DIRECTIONS_4)
            if _has(patch, "riserMm"):
                _check_int_mm(out, patch["riserMm"], f("patch.riserMm"), 50, 400)
            if _has(patch, "treadMm"):
                _check_int_mm(out, patch["treadMm"], f("patch.treadMm"), 100, 600)
            if _has(patch, "widthMm"):
                _check_int_mm(out, patch["widthMm"], f("patch.widthMm"), 300)
            if _has(patch, "risersCount"):
                _check_int(out, patch["risersCount"], f("patch.risersCount"), 2, 60)
    elif t == "stair.delete":
        if _require_field(out, p, "stairId", t):
            _check_id(out, g("stairId"), f("stairId"), "stair")
    elif t == "column.set":
        if _require_field(out, p, "action", t):
            _check_enum(out, g("action"), f("action"), COLUMN_ACTIONS)
        if _require_field(out, p, "id", t):
            _check_id(out, g("id"), f("id"), "column")
        if g("action") == "add":
            if _require_field(out, p, "storeyId", t):
                _check_id(out, g("storeyId"), f("storeyId"), "storey")
            if _require_field(out, p, "pt", t):
                _check_pt(out, g("pt"), f("pt"))
        if g("action") == "move" and _require_field(out, p, "pt", t):
            _check_pt(out, g("pt"), f("pt"))
        if _has(p, "sizeMm") and _check_object(out, g("sizeMm"), f("sizeMm")):
            size = g("sizeMm")
            _check_int_mm(out, size.get("xMm"), f("sizeMm.xMm"), 1)
            _check_int_mm(out, size.get("yMm"), f("sizeMm.yMm"), 1)
    elif t == "furniture.set":
        if _require_field(out, p, "action", t):
            _check_enum(out, g("action"), f("action"), FURNITURE_ACTIONS)
        if _require_field(out, p, "id", t):
            _check_id(out, g("id"), f("id"), "furniture")
        if g("action") == "place":
            if _require_field(out, p, "storeyId", t):
                _check_id(out, g("storeyId"), f("storeyId"), "storey")
            if _require_field(out, p, "catalogId", t):
                _check_string(out, g("catalogId"), f("catalogId"))
            if _require_field(out, p, "pt", t):
                _check_pt(out, g("pt"), f("pt"))
        if _has(p, "pt") and g("action") != "place":
            _check_pt(out, g("pt"), f("pt"))
        if _has(p, "rotationDeg"):
            _check_int(out, g("rotationDeg"), f("rotationDeg"), -359, 359)
    elif t == "balcony.set":
        if _require_field(out, p, "action", t):
            _check_enum(out, g("action"), f("action"), BALCONY_ACTIONS)
        if _require_field(out, p, "id", t):
            _check_id(out, g("id"), f("id"), "balcony")
        if g("action") == "add":
            if _require_field(out, p, "storeyId", t):
                _check_id(out, g("storeyId"), f("storeyId"), "storey")
            if _require_field(out, p, "polygon", t):
                _check_polygon(out, g("polygon"), f("polygon"))
        elif _has(p, "polygon"):
            _check_polygon(out, g("polygon"), f("polygon"))
        if _has(p, "railingKind"):
            _check_enum(out, g("railingKind"), f("railingKind"), RAILING_KINDS)
        if _has(p, "railingHeightMm"):
            _check_int_mm(out, g("railingHeightMm"), f("railingHeightMm"), 0)
        if _has(p, "projectionMm"):
            _check_int_mm(out, g("projectionMm"), f("projectionMm"), 0)
        if _has(p, "slabThicknessMm"):
            _check_int_mm(out, g("slabThicknessMm"), f("slabThicknessMm"), 1)
    elif t == "facade.apply_kit":
        if _require_field(out, p, "kitId", t) and g("kitId") is not None:
            _check_string(out, g("kitId"), f("kitId"))
        if _require_field(out, p, "seed", t):
            _check_int(out, g("seed"), f("seed"), 0)
        if _has(p, "colorwayId") and g("colorwayId") is not None:
            _check_string(out, g("colorwayId"), f("colorwayId"))
        if _require_field(out, p, "components", t):
            components = g("components")
            if not isinstance(components, list | tuple):
                out.append(
                    _issue(
                        "OP_FIELD_NOT_OBJECT",
                        "payload.components must be an array.",
                        field=f("components"),
                    )
                )
            else:
                for i, c in enumerate(components):
                    if not _is_plain_object(c):
                        out.append(
                            _issue(
                                "OP_FIELD_NOT_OBJECT",
                                f"payload.components[{i}] must be an object.",
                                field=f"{f('components')}[{i}]",
                            )
                        )
                        continue
                    _check_id(out, c.get("id"), f"{f('components')}[{i}].id", "facadecomp")
                    _check_enum(
                        out,
                        c.get("kind"),
                        f"{f('components')}[{i}].kind",
                        FACADE_COMPONENT_KINDS,
                    )
                    if _has(c, "params") and _check_object(
                        out, c["params"], f"{f('components')}[{i}].params"
                    ):
                        _check_json_integral(out, c["params"], f"{f('components')}[{i}].params")
    elif t == "facade.edit_component":
        if _require_field(out, p, "componentId", t):
            _check_id(out, g("componentId"), f("componentId"), "facadecomp")
        if _require_field(out, p, "patch", t) and _check_object(out, g("patch"), f("patch")):
            _check_json_integral(out, g("patch"), f("patch"))
    elif t == "material.assign":
        if _require_field(out, p, "id", t):
            _check_id(out, g("id"), f("id"), "material")
        if _require_field(out, p, "target", t) and _check_object(out, g("target"), f("target")):
            target = g("target")
            _check_enum(out, target.get("group"), f("target.group"), SURFACE_GROUPS)
            if target.get("storeyId") is not None:
                _check_id(out, target.get("storeyId"), f("target.storeyId"), "storey")
            element_id = target.get("elementId")
            if element_id is not None and try_parse_id(element_id) is None:
                out.append(
                    _issue(
                        "OP_FIELD_BAD_ID",
                        "payload.target.elementId must be an element id or null.",
                        field=f("target.elementId"),
                    )
                )
        if _require_field(out, p, "materialId", t) and g("materialId") is not None:
            _check_string(out, g("materialId"), f("materialId"))
    elif t == "levels.set":
        any_field = False
        for k in ("plinthMm", "sillDefaultMm", "lintelDefaultMm", "parapetMm"):
            if _has(p, k):
                any_field = True
                _check_int_mm(out, g(k), f(k), 0, 6000)
        if _has(p, "fflPerStoreyMm"):
            any_field = True
            ffls = g("fflPerStoreyMm")
            if not isinstance(ffls, list | tuple) or not all(is_int_mm(v) for v in ffls):
                out.append(
                    _issue(
                        "OP_FIELD_NOT_INT_MM",
                        "payload.fflPerStoreyMm must be integer millimetres.",
                        field=f("fflPerStoreyMm"),
                    )
                )
        if not any_field:
            out.append(
                _issue(
                    "OP_FIELD_MISSING",
                    "levels.set needs at least one field to set.",
                    field="payload",
                )
            )
    elif t == "solver.apply_option":
        if _require_field(out, p, "solverJobId", t):
            _check_string(out, g("solverJobId"), f("solverJobId"))
        if _require_field(out, p, "optionIndex", t):
            _check_int(out, g("optionIndex"), f("optionIndex"), 0)
        if _require_field(out, p, "ops", t):
            inner_ops = g("ops")
            if not isinstance(inner_ops, list | tuple):
                out.append(
                    _issue(
                        "OP_FIELD_NOT_OBJECT",
                        "payload.ops must be an array of ops.",
                        field=f("ops"),
                    )
                )
            else:
                for i, inner in enumerate(inner_ops):
                    for sub in validate_op_shape(inner):
                        out.append(
                            ValidationIssue(
                                code=sub.code,
                                message=sub.message,
                                severity=sub.severity,
                                element_ids=sub.element_ids,
                                field=f"{f('ops')}[{i}].{sub.field if sub.field is not None else ''}",
                                actual=sub.actual,
                                limit=sub.limit,
                                fix=sub.fix,
                            )
                        )
    elif t == "annotation.set":
        if _require_field(out, p, "action", t):
            _check_enum(out, g("action"), f("action"), ANNOTATION_ACTIONS)
        if _require_field(out, p, "id", t):
            _check_id(out, g("id"), f("id"), "annotation")
        if g("action") == "add":
            if _require_field(out, p, "sheetId", t):
                _check_id(out, g("sheetId"), f("sheetId"), "sheet")
            if _require_field(out, p, "anchorKind", t):
                _check_enum(out, g("anchorKind"), f("anchorKind"), ANNOTATION_ANCHOR_KINDS)
        elif _has(p, "anchorKind"):
            _check_enum(out, g("anchorKind"), f("anchorKind"), ANNOTATION_ANCHOR_KINDS)
        if (
            _has(p, "anchorElementId")
            and g("anchorElementId") is not None
            and try_parse_id(g("anchorElementId")) is None
        ):
            out.append(
                _issue(
                    "OP_FIELD_BAD_ID",
                    "payload.anchorElementId must be an element id or null.",
                    field=f("anchorElementId"),
                )
            )
        if _has(p, "payload") and _check_object(out, g("payload"), f("payload")):
            _check_json_integral(out, g("payload"), f("payload"))
    else:  # pragma: no cover - a new op type without a shape validator lands here
        out.append(_issue("OP_UNKNOWN_TYPE", f"No shape validator for op type {t}.", field="type"))
    return out


# ---------------------------------------------------------------------------
# Document preconditions for an op
# ---------------------------------------------------------------------------


def _missing(code: ValidationCode, kind: str, element_id: Any, fix: str) -> ValidationIssue:
    return _issue(
        code,
        f"No {kind} with id {_js_string(element_id)} in this design.",
        element_ids=[_js_string(element_id)],
        actual=_js_string(element_id),
        fix=fix,
    )


def validate_op_against_doc(doc: ProjectDoc, op: Op) -> list[ValidationIssue]:
    """Validate that an op can be applied to THIS document: referenced elements
    exist, new ids are free, indices are in range, openings fit their host wall.

    ``fold()`` calls :func:`validate_op_shape` then this; if either returns
    issues the op is rejected with :class:`OpRejectedError` and the document is
    untouched.

    PRECONDITION: ``validate_op_shape(op)`` returned no issues. This function
    trusts the payload's shape and only asks document questions.
    """
    out: list[ValidationIssue] = []
    h = doc.house
    p = op.payload

    def g(k: str) -> Any:
        return p.get(k, None)

    all_ids: set[str] = set()
    for element in (
        list(h.storeys)
        + list(h.walls)
        + list(h.openings)
        + list(h.rooms)
        + list(h.stairs)
        + list(h.slabs)
        + list(h.columns)
        + list(h.furniture)
        + list(h.balconies)
        + list(h.facade.components)
        + list(h.materials)
        + list(doc.annotations)
    ):
        all_ids.add(element.id)

    def require_free_id(element_id: Any) -> None:
        if isinstance(element_id, str) and element_id in all_ids:
            out.append(
                _issue(
                    "OP_ID_ALREADY_EXISTS",
                    f"Id {element_id} is already used in this design.",
                    element_ids=[element_id],
                    fix="Mint a fresh id with new_id().",
                )
            )

    def require_storey(storey_id: Any) -> bool:
        ok = any(s.id == storey_id for s in h.storeys)
        if not ok:
            out.append(
                _missing(
                    "STOREY_UNKNOWN",
                    "storey",
                    storey_id,
                    "Add the storey first, or use an existing storeyId.",
                )
            )
        return ok

    t = op.type
    if t == "plot.set_road":
        edges = len(doc.plot.boundary)
        idx = g("edgeIndex")
        if edges == 0:
            out.append(
                _issue(
                    "PLOT_BOUNDARY_NOT_CLOSED",
                    "Set the plot boundary before assigning roads to edges.",
                    fix="Send plot.set_boundary first.",
                )
            )
        elif idx >= edges:
            out.append(
                _issue(
                    "PLOT_EDGE_UNKNOWN",
                    f"The plot has {edges} edges; edge {idx} does not exist.",
                    field="payload.edgeIndex",
                    actual=idx,
                    limit=edges - 1,
                )
            )
    elif t == "storey.add":
        require_free_id(g("id"))
        if g("index") > len(h.storeys):
            out.append(
                _issue(
                    "STOREY_INDEX_OUT_OF_RANGE",
                    f"Cannot insert a storey at index {g('index')}; there are {len(h.storeys)}.",
                    field="payload.index",
                    actual=g("index"),
                    limit=len(h.storeys),
                )
            )
    elif t == "storey.remove":
        if g("index") >= len(h.storeys):
            out.append(
                _issue(
                    "STOREY_INDEX_OUT_OF_RANGE",
                    f"There is no storey at index {g('index')}.",
                    field="payload.index",
                    actual=g("index"),
                    limit=len(h.storeys) - 1,
                )
            )
    elif t == "storey.set_height":
        require_storey(g("storeyId"))
    elif t == "wall.add":
        require_free_id(g("id"))
        require_storey(g("storeyId"))
        new_seg = Seg(pt_of(g("a")), pt_of(g("b")))
        dup = next(
            (
                w
                for w in h.walls
                if w.storey_id == g("storeyId") and _overlaps_wall(Seg(w.a, w.b), new_seg)
            ),
            None,
        )
        if dup is not None:
            out.append(
                _issue(
                    "WALL_DUPLICATE",
                    "There is already a wall along that line.",
                    element_ids=[dup.id],
                    fix="Move or delete the existing wall instead of adding a duplicate.",
                )
            )
    elif t in ("wall.move", "wall.set_thickness", "wall.delete"):
        wall_id = g("wallId") if isinstance(g("wallId"), str) else ""
        wall = next((w for w in h.walls if w.id == wall_id), None)
        if wall is None:
            out.append(
                _missing(
                    "WALL_UNKNOWN", "wall", wall_id, "Use a wallId that exists on this storey."
                )
            )
        elif t == "wall.move":
            moved = Seg(pt_of(g("a")), pt_of(g("b")))
            dup = next(
                (
                    w
                    for w in h.walls
                    if w.id != wall.id
                    and w.storey_id == wall.storey_id
                    and _overlaps_wall(Seg(w.a, w.b), moved)
                ),
                None,
            )
            if dup is not None:
                out.append(
                    _issue(
                        "WALL_DUPLICATE",
                        "Moving the wall there would make it overlap another wall.",
                        element_ids=[wall.id, dup.id],
                        fix="Offset the wall by at least its thickness, or delete the other wall.",
                    )
                )
            new_len = segment_length_mm(moved)
            for o in [x for x in h.openings if x.wall_id == wall.id]:
                fit = _opening_fit_issue(o.id, o.offset_mm, o.width_mm, new_len)
                if fit is not None:
                    out.append(fit)
    elif t == "wall.split":
        require_free_id(g("newWallId"))
        wall = next((w for w in h.walls if w.id == g("wallId")), None)
        if wall is None:
            out.append(_missing("WALL_UNKNOWN", "wall", g("wallId"), "Use an existing wallId."))
        else:
            length = segment_length_mm(Seg(wall.a, wall.b))
            if g("atMm") <= 0 or g("atMm") >= length:
                out.append(
                    _issue(
                        "WALL_SPLIT_OUT_OF_RANGE",
                        f"Split point must be between 1mm and {length - 1}mm along the wall.",
                        element_ids=[wall.id],
                        field="payload.atMm",
                        actual=g("atMm"),
                        limit=length,
                    )
                )
    elif t == "opening.add":
        require_free_id(g("id"))
        wall = next((w for w in h.walls if w.id == g("wallId")), None)
        if wall is None:
            out.append(
                _missing(
                    "WALL_UNKNOWN", "wall", g("wallId"), "Host the opening on an existing wall."
                )
            )
        else:
            length = segment_length_mm(Seg(wall.a, wall.b))
            fit = _opening_fit_issue(g("id"), g("offsetMm"), g("widthMm"), length)
            if fit is not None:
                out.append(fit)
            storey = next((s for s in h.storeys if s.id == wall.storey_id), None)
            if storey is not None and g("sillMm") + g("heightMm") > storey.height_mm:
                out.append(_height_issue(g("id"), g("sillMm") + g("heightMm"), storey.height_mm))
    elif t in ("opening.move", "opening.resize", "opening.flip", "opening.delete"):
        opening_id = g("openingId") if isinstance(g("openingId"), str) else ""
        opening = next((o for o in h.openings if o.id == opening_id), None)
        if opening is None:
            out.append(
                _missing("OPENING_UNKNOWN", "opening", opening_id, "Use an openingId that exists.")
            )
        elif t == "opening.move":
            target_wall_id = _coalesce(g("wallId"), opening.wall_id)
            wall = next((w for w in h.walls if w.id == target_wall_id), None)
            if wall is None:
                out.append(
                    _missing(
                        "WALL_UNKNOWN", "wall", target_wall_id, "Re-host onto an existing wall."
                    )
                )
            else:
                length = segment_length_mm(Seg(wall.a, wall.b))
                fit = _opening_fit_issue(opening.id, g("offsetMm"), opening.width_mm, length)
                if fit is not None:
                    out.append(fit)
        elif t == "opening.resize":
            wall = next((w for w in h.walls if w.id == opening.wall_id), None)
            width = _coalesce(g("widthMm"), opening.width_mm)
            height = _coalesce(g("heightMm"), opening.height_mm)
            sill = _coalesce(g("sillMm"), opening.sill_mm)
            if wall is not None:
                length = segment_length_mm(Seg(wall.a, wall.b))
                fit = _opening_fit_issue(opening.id, opening.offset_mm, width, length)
                if fit is not None:
                    out.append(fit)
                storey = next((s for s in h.storeys if s.id == wall.storey_id), None)
                if storey is not None and sill + height > storey.height_mm:
                    out.append(_height_issue(opening.id, sill + height, storey.height_mm))
    elif t in ("room.assign", "room.set_target"):
        room_id = g("roomId") if isinstance(g("roomId"), str) else ""
        if not any(r.id == room_id for r in h.rooms):
            out.append(
                _missing(
                    "ROOM_UNKNOWN",
                    "room",
                    room_id,
                    "Rooms are detected from walls — enclose the space first, then assign it.",
                )
            )
    elif t == "stair.add":
        require_free_id(g("id"))
        if require_storey(g("storeyId")):
            storey = next((s for s in h.storeys if s.id == g("storeyId")), None)
            if storey is not None:
                rise = g("risersCount") * g("riserMm")
                rise_issue = _stair_rise_issue(g("id"), rise, storey.height_mm, g("risersCount"))
                if rise_issue is not None:
                    out.append(rise_issue)
    elif t in ("stair.edit", "stair.delete"):
        stair_id = g("stairId") if isinstance(g("stairId"), str) else ""
        stair = next((s for s in h.stairs if s.id == stair_id), None)
        if stair is None:
            out.append(_missing("STAIR_UNKNOWN", "stair", stair_id, "Use an existing stairId."))
        elif t == "stair.edit":
            storey = next((s for s in h.storeys if s.id == stair.storey_id), None)
            patch = g("patch") or {}
            risers = _coalesce(patch.get("risersCount"), stair.risers_count)
            riser = _coalesce(patch.get("riserMm"), stair.riser_mm)
            if storey is not None:
                rise_issue = _stair_rise_issue(stair.id, risers * riser, storey.height_mm, risers)
                if rise_issue is not None:
                    out.append(rise_issue)
    elif t == "column.set":
        if g("action") == "add":
            require_free_id(g("id"))
            require_storey(g("storeyId"))
        elif not any(c.id == g("id") for c in h.columns):
            out.append(
                _missing(
                    "COLUMN_UNKNOWN",
                    "column",
                    g("id"),
                    "Add the column before moving or deleting it.",
                )
            )
    elif t == "furniture.set":
        if g("action") == "place":
            require_free_id(g("id"))
            require_storey(g("storeyId"))
        elif not any(fi.id == g("id") for fi in h.furniture):
            out.append(
                _missing(
                    "FURNITURE_UNKNOWN",
                    "furniture item",
                    g("id"),
                    "Place the item before transforming it.",
                )
            )
    elif t == "balcony.set":
        if g("action") == "add":
            require_free_id(g("id"))
            require_storey(g("storeyId"))
        elif not any(b.id == g("id") for b in h.balconies):
            out.append(
                _missing(
                    "BALCONY_UNKNOWN", "balcony", g("id"), "Add the balcony before editing it."
                )
            )
    elif t == "facade.edit_component":
        if not any(c.id == g("componentId") for c in h.facade.components):
            out.append(
                _missing(
                    "FACADE_COMPONENT_UNKNOWN",
                    "facade component",
                    g("componentId"),
                    "Apply a facade kit first.",
                )
            )
    elif t == "material.assign":
        existing = any(m.id == g("id") for m in h.materials)
        if not existing and g("materialId") is None:
            out.append(
                _missing(
                    "MATERIAL_ASSIGNMENT_UNKNOWN",
                    "material assignment",
                    g("id"),
                    "Nothing to clear — this assignment does not exist.",
                )
            )
        target = g("target") or {}
        if target.get("storeyId") is not None:
            require_storey(target.get("storeyId"))
    elif t == "levels.set":
        ffls = g("fflPerStoreyMm")
        if ffls is not None and len(ffls) != len(h.storeys):
            out.append(
                _issue(
                    "LEVELS_INVALID",
                    f"fflPerStoreyMm has {len(ffls)} entries but there are "
                    f"{len(h.storeys)} storeys.",
                    field="payload.fflPerStoreyMm",
                    actual=len(ffls),
                    limit=len(h.storeys),
                )
            )
    elif t == "annotation.set":
        if g("action") == "add":
            require_free_id(g("id"))
        elif not any(a.id == g("id") for a in doc.annotations):
            out.append(
                _missing("ANNOTATION_UNKNOWN", "annotation", g("id"), "Add the annotation first.")
            )
    elif t == "solver.apply_option":
        # Each inner op is validated as it is folded (fold applies the group
        # transactionally), so nothing to pre-check here beyond the shape.
        pass
    return out


def _overlaps_wall(a: Seg, b: Seg) -> bool:
    """True when two wall centrelines lie on top of each other over a length."""
    ov = collinear_overlap(a, b)
    if ov is None:
        return False
    # only an overlap of non-zero length counts as "exactly overlapping"
    if pt_eq(ov.a, ov.b):
        return False
    # and only when they are actually collinear (collinear_overlap projects, so
    # confirm both endpoints of b lie on a's infinite line)
    cross1 = (a.b.x - a.a.x) * (b.a.y - a.a.y) - (a.b.y - a.a.y) * (b.a.x - a.a.x)
    cross2 = (a.b.x - a.a.x) * (b.b.y - a.a.y) - (a.b.y - a.a.y) * (b.b.x - a.a.x)
    return cross1 == 0 and cross2 == 0


def _opening_fit_issue(
    opening_id: str, offset_mm: int, width_mm: int, wall_length_mm: int
) -> ValidationIssue | None:
    usable = wall_length_mm - 2 * WALL_END_MARGIN_MM
    if width_mm > usable:
        return _issue(
            "OPENING_OUT_OF_WALL",
            f"This opening is {width_mm}mm wide but the wall only offers {max(0, usable)}mm "
            f"between the {WALL_END_MARGIN_MM}mm end margins.",
            element_ids=[opening_id],
            field="payload.widthMm",
            actual=width_mm,
            limit=max(0, usable),
            fix=f"Narrow the opening to {max(0, usable)}mm or host it on a longer wall.",
        )
    # floor / ceil of width/2, matching Math.floor and Math.ceil for both signs
    start = offset_mm - (width_mm // 2)
    end = offset_mm + (-((-width_mm) // 2))
    if start < WALL_END_MARGIN_MM or end > wall_length_mm - WALL_END_MARGIN_MM:
        min_offset = WALL_END_MARGIN_MM + (width_mm // 2)
        max_offset = wall_length_mm - WALL_END_MARGIN_MM - (-((-width_mm) // 2))
        return _issue(
            "OPENING_OUT_OF_WALL",
            f"The opening must sit between {min_offset}mm and {max_offset}mm along the wall to "
            f"keep {WALL_END_MARGIN_MM}mm at each end.",
            element_ids=[opening_id],
            field="payload.offsetMm",
            actual=offset_mm,
            limit=f"{min_offset}..{max_offset}",
            fix=f"Set offsetMm between {min_offset} and {max_offset}.",
        )
    return None


def _height_issue(opening_id: str, top_mm: int, storey_height_mm: int) -> ValidationIssue:
    return _issue(
        "OPENING_EXCEEDS_STOREY_HEIGHT",
        f"Sill + height is {top_mm}mm, taller than the {storey_height_mm}mm storey.",
        element_ids=[opening_id],
        field="payload.heightMm",
        actual=top_mm,
        limit=storey_height_mm,
        fix=f"Reduce the height or sill so they total at most {storey_height_mm}mm.",
    )


def _stair_rise_issue(
    stair_id: str, total_rise_mm: int, storey_height_mm: int, risers_count: int
) -> ValidationIssue | None:
    delta = abs(total_rise_mm - storey_height_mm)
    if delta <= STAIR_RISE_TOLERANCE_MM:
        return None
    suggested = round_half_away_from_zero(storey_height_mm / max(1, risers_count))
    return _issue(
        "STAIR_RISE_MISMATCH",
        f"{risers_count} risers total {total_rise_mm}mm but the storey is {storey_height_mm}mm "
        f"(+/-{STAIR_RISE_TOLERANCE_MM}mm allowed).",
        element_ids=[stair_id],
        field="payload.riserMm",
        actual=total_rise_mm,
        limit=storey_height_mm,
        fix=f"Use riserMm {suggested} with {risers_count} risers, or change risersCount.",
    )


# ---------------------------------------------------------------------------
# Whole-document invariants
# ---------------------------------------------------------------------------


def validate_model(
    doc: ProjectDoc,
    storey_ids: Iterable[str] | None = None,
    include_warnings: bool = True,
) -> list[ValidationIssue]:
    """The section-3 fold invariants over a whole document.

    ``fold()`` runs this on the candidate next state (scoped to the touched
    storeys via ``storey_ids``) and refuses to return a document that breaks it.
    """
    out: list[ValidationIssue] = []
    h = doc.house
    scope: set[str] | None = None if storey_ids is None else set(storey_ids)

    def in_scope(storey_id: str) -> bool:
        return scope is None or storey_id in scope

    if h.schema_version != doc.schema_version:
        out.append(
            _issue(
                "SCHEMA_VERSION_UNSUPPORTED",
                f"House schemaVersion {h.schema_version} does not match document "
                f"{doc.schema_version}.",
            )
        )

    # --- duplicate ids across the whole document
    seen: dict[str, int] = {}
    for element in (
        list(h.storeys)
        + list(h.walls)
        + list(h.openings)
        + list(h.rooms)
        + list(h.stairs)
        + list(h.slabs)
        + list(h.columns)
        + list(h.furniture)
        + list(h.balconies)
        + list(h.facade.components)
        + list(h.materials)
        + list(doc.annotations)
    ):
        seen[element.id] = seen.get(element.id, 0) + 1
    for element_id, count in seen.items():
        if count > 1:
            out.append(
                _issue(
                    "DUPLICATE_ELEMENT_ID",
                    f"Id {element_id} appears {count} times.",
                    element_ids=[element_id],
                    actual=count,
                    limit=1,
                )
            )

    # --- plot
    if len(doc.plot.boundary) > 0 and not polygon_is_closed_ring(doc.plot.boundary):
        out.append(
            _issue(
                "PLOT_BOUNDARY_NOT_CLOSED",
                "The plot boundary must be a closed ring with non-zero area.",
                fix="Fix the boundary vertices so the outline closes without crossing itself.",
            )
        )
    if not is_int_mm(doc.plot.north_deg) or doc.plot.north_deg < 0 or doc.plot.north_deg > 359:
        out.append(
            _issue(
                "PLOT_NORTH_INVALID",
                "North must be an integer 0-359 degrees.",
                actual=doc.plot.north_deg,
                limit="0..359",
            )
        )

    # --- storeys & levels
    for s in h.storeys:
        if not is_int_mm(s.height_mm) or s.height_mm <= 0:
            out.append(
                _issue(
                    "STOREY_HEIGHT_INVALID",
                    f"Storey {s.name or s.id} needs a positive height in mm.",
                    element_ids=[s.id],
                    actual=s.height_mm,
                )
            )
    if len(h.levels.ffl_per_storey_mm) != len(h.storeys):
        out.append(
            _issue(
                "LEVELS_INVALID",
                f"levels.fflPerStoreyMm has {len(h.levels.ffl_per_storey_mm)} entries for "
                f"{len(h.storeys)} storeys.",
                actual=len(h.levels.ffl_per_storey_mm),
                limit=len(h.storeys),
            )
        )

    # --- walls
    storey_by_id = {s.id: s for s in h.storeys}
    walls_in_scope = [w for w in h.walls if in_scope(w.storey_id)]
    for w in walls_in_scope:
        if pt_eq(w.a, w.b):
            out.append(
                _issue(
                    "WALL_ZERO_LENGTH",
                    "A wall has zero length.",
                    element_ids=[w.id],
                    fix="Delete the wall or give it two different endpoints.",
                )
            )
        if (
            not is_int_mm(w.thickness_mm)
            or w.thickness_mm <= 0
            or w.thickness_mm > MAX_WALL_THICKNESS_MM
        ):
            out.append(
                _issue(
                    "WALL_THICKNESS_INVALID",
                    f"Wall thickness {w.thickness_mm}mm is out of range.",
                    element_ids=[w.id],
                    actual=w.thickness_mm,
                    limit=f"1..{MAX_WALL_THICKNESS_MM}",
                )
            )
        if w.storey_id not in storey_by_id:
            out.append(
                _missing(
                    "STOREY_UNKNOWN",
                    "storey",
                    w.storey_id,
                    "Re-parent the wall to an existing storey.",
                )
            )
    # "no two walls exactly overlapping" — quadratic, so scope it per storey
    by_storey: dict[str, list[Any]] = {}
    for w in walls_in_scope:
        by_storey.setdefault(w.storey_id, []).append(w)
    for walls in by_storey.values():
        for i in range(len(walls)):
            for j in range(i + 1, len(walls)):
                if _overlaps_wall(Seg(walls[i].a, walls[i].b), Seg(walls[j].a, walls[j].b)):
                    out.append(
                        _issue(
                            "WALL_DUPLICATE",
                            "Two walls lie on top of each other.",
                            element_ids=[walls[i].id, walls[j].id],
                            fix="Delete one of them, or offset it by at least its thickness.",
                        )
                    )

    # --- openings
    wall_by_id = {w.id: w for w in h.walls}
    for o in h.openings:
        wall = wall_by_id.get(o.wall_id)
        if wall is None:
            out.append(
                _missing(
                    "OPENING_UNKNOWN", "host wall", o.wall_id, "Re-host or delete the opening."
                )
            )
            continue
        if not in_scope(wall.storey_id):
            continue
        if o.width_mm <= 0 or o.height_mm <= 0:
            out.append(
                _issue(
                    "OPENING_DIMENSION_INVALID",
                    "An opening must have positive width and height.",
                    element_ids=[o.id],
                    actual=f"{o.width_mm}x{o.height_mm}",
                )
            )
        if o.sill_mm < 0:
            out.append(
                _issue(
                    "OPENING_SILL_INVALID",
                    "Sill height cannot be negative.",
                    element_ids=[o.id],
                    actual=o.sill_mm,
                )
            )
        length = segment_length_mm(Seg(wall.a, wall.b))
        fit = _opening_fit_issue(o.id, o.offset_mm, o.width_mm, length)
        if fit is not None:
            out.append(fit)
        storey = storey_by_id.get(wall.storey_id)
        if storey is not None and o.sill_mm + o.height_mm > storey.height_mm:
            out.append(_height_issue(o.id, o.sill_mm + o.height_mm, storey.height_mm))

    # --- stairs
    for stair in h.stairs:
        if not in_scope(stair.storey_id):
            continue
        storey = storey_by_id.get(stair.storey_id)
        if storey is None:
            out.append(
                _missing("STOREY_UNKNOWN", "storey", stair.storey_id, "Re-parent the stair.")
            )
            continue
        if (
            stair.riser_mm <= 0
            or stair.tread_mm <= 0
            or stair.width_mm <= 0
            or stair.risers_count <= 1
        ):
            out.append(
                _issue(
                    "STAIR_DIMENSION_INVALID",
                    "Stair riser, tread, width and riser count must all be positive.",
                    element_ids=[stair.id],
                )
            )
            continue
        rise_issue = _stair_rise_issue(
            stair.id, stair.risers_count * stair.riser_mm, storey.height_mm, stair.risers_count
        )
        if rise_issue is not None:
            out.append(rise_issue)

    # --- rooms closed
    for r in h.rooms:
        if not in_scope(r.storey_id):
            continue
        if not polygon_is_closed_ring(r.polygon):
            out.append(
                _issue(
                    "ROOM_NOT_CLOSED",
                    f"Room {r.name or r.id} is not a closed area.",
                    element_ids=[r.id],
                    fix="Close the surrounding walls; rooms are detected from enclosed space.",
                )
            )
            continue
        area = polygon_area_mm2(r.polygon)
        if area != r.area_mm2:
            out.append(
                _issue(
                    "ROOM_NOT_CLOSED",
                    f"Room {r.name or r.id} has a stale area.",
                    element_ids=[r.id],
                    actual=r.area_mm2,
                    limit=area,
                    severity="warning",
                    fix="Re-run room detection (fold recomputes it).",
                )
            )

    # --- balconies
    for b in h.balconies:
        if not in_scope(b.storey_id):
            continue
        if not polygon_is_closed_ring(b.polygon):
            out.append(
                _issue(
                    "BALCONY_POLYGON_INVALID",
                    "A balcony outline must be a closed ring.",
                    element_ids=[b.id],
                )
            )

    # --- columns
    for c in h.columns:
        if not in_scope(c.storey_id):
            continue
        if c.size_mm.x_mm <= 0 or c.size_mm.y_mm <= 0:
            out.append(
                _issue("COLUMN_SIZE_INVALID", "Column size must be positive.", element_ids=[c.id])
            )

    if not include_warnings:
        return [i for i in out if i.severity == "error"]
    return out


def is_acceptable(issues: Sequence[ValidationIssue]) -> bool:
    """True when nothing in ``issues`` is an error."""
    return not any(i.severity == "error" for i in issues)


def issues_by_code(issues: Sequence[ValidationIssue]) -> dict[str, list[ValidationIssue]]:
    """Group issues by code — handy for the compliance strip and copilot feedback."""
    grouped: dict[str, list[ValidationIssue]] = {}
    for i in issues:
        grouped.setdefault(i.code, []).append(i)
    return grouped


def render_issues_for_llm(issues: Sequence[ValidationIssue]) -> str:
    """Compact, LLM-friendly rendering of rejection reasons (section 10).

    One line per issue: ``CODE field=... actual=... limit=... message (fix)``.
    """
    lines: list[str] = []
    for i in issues:
        parts: list[str] = [i.code]
        if i.field:
            parts.append(f"field={i.field}")
        if i.actual is not None:
            parts.append(f"actual={_js_string(i.actual)}")
        if i.limit is not None:
            parts.append(f"limit={_js_string(i.limit)}")
        parts.append(f"— {i.message}")
        if i.fix:
            parts.append(f"FIX: {i.fix}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


#: All model-invariant codes (as opposed to op-shape codes) — used by the UI copy map.
MODEL_INVARIANT_CODES: tuple[str, ...] = (
    "WALL_ZERO_LENGTH",
    "WALL_THICKNESS_INVALID",
    "WALL_DUPLICATE",
    "OPENING_DIMENSION_INVALID",
    "OPENING_OUT_OF_WALL",
    "OPENING_EXCEEDS_STOREY_HEIGHT",
    "OPENING_SILL_INVALID",
    "STAIR_RISE_MISMATCH",
    "STAIR_DIMENSION_INVALID",
    "ROOM_NOT_CLOSED",
    "STOREY_HEIGHT_INVALID",
    "PLOT_BOUNDARY_NOT_CLOSED",
    "PLOT_NORTH_INVALID",
    "LEVELS_INVALID",
    "BALCONY_POLYGON_INVALID",
    "COLUMN_SIZE_INVALID",
    "DUPLICATE_ELEMENT_ID",
)


def assert_valid_model(doc: ProjectDoc) -> None:
    """Convenience: raise unless the document satisfies the section-3 invariants."""
    issues = validate_model(doc, include_warnings=False)
    if issues:
        raise OpRejectedError("model", issues)
