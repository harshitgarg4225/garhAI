"""The 2D projection primitives — §7's renderer-agnostic intermediate. **Real.**

    Rendering pipeline: model → 2D projection primitives (lines/arcs/text/hatches
    with layer tags) → SVG (screen + PDF via headless print) and DXF (ezdxf, mm
    units, layers A-WALL, ...).

THIS MODULE IS THE NARROW WAIST OF THE DRAWING ENGINE. Every projector (plan,
elevation, section) emits *only* these five types, and every renderer (SVG, DXF, PDF)
consumes *only* these five types. That is not tidiness for its own sake: it is the one
structural rule that makes "the DXF and the PDF show the same building" a property of
the architecture rather than a thing somebody has to keep checking. A renderer that
reaches back into the ``HouseModel`` to draw one extra line has broken the guarantee,
and the next golden-file diff will be the least of the problems.

Three deliberate decisions
--------------------------
**Nothing but the standard library.** Not ezdxf, not the model core, not
``services.common``. The primitive stream is the thing that gets JSON-serialised into
golden files and byte-diffed in CI (§16), so the module that defines it has to be
importable in the leanest possible environment. Points are therefore plain
``(x, y)`` integer tuples and not ``garh_model.geometry.Pt``: the model's document type
belongs to the document, and the moment a DXF writer needs to import the house model to
understand a line, the waist is no longer narrow. :func:`point_of` converts at the one
boundary where it matters.

**Integer millimetres, checked.** :func:`point` refuses a float. Symbol geometry that
needs trigonometry (a rotated north arrow, a door swing) rounds *once*, half away from
zero, at the point of construction — mirroring the model core's rounding rule — and
every primitive that leaves a projector holds integers only. :func:`validate_primitives`
is the assertion, and it runs in the test suite rather than living in a comment.

**``layer`` is the contract; ``kind`` is a hint.** ``layer`` is one of the nine §7 DXF
layer names and it is what a municipal reviewer sees in AutoCAD, so it is validated
against :mod:`services.drawings.layers`. ``kind`` is a short semantic tag
("wall-face", "door-swing") that exists for tests, debugging and CSS classes in the SVG.
A renderer must never make a geometry decision from ``kind`` — if a distinction matters
to the output, it belongs in the geometry or on a layer.

Coordinate spaces
-----------------
A primitive carries no unit tag, so the caller has to know which space a stream is in.
There are exactly two, and they never mix inside one list:

``model mm``
    What a projector emits. Plot-local millimetres, origin at the plot SW corner,
    +X east, +Y north — the model core's space, unscaled.
``paper mm``
    What a renderer draws. Millimetres on the physical sheet, origin at the sheet's
    bottom-left, +Y **up**.

``services.drawings.sheets.compose`` is the only code allowed to turn the first into
the second, because it is the only place that knows the sheet's scale. See
``sheets/transform.py`` for why that is a single-module rule.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Points
# ---------------------------------------------------------------------------

#: A point in integer millimetres. A bare tuple on purpose — see the module docstring.
Point = tuple[int, int]


def round_half_away(value: float) -> int:
    """Round half away from zero — the model core's rule, mirrored.

    ``garh_model.units.round_half_away_from_zero`` is the source of truth (and
    ``services.solver.geometry.round_half_away`` is the other mirror). It is repeated
    here rather than imported because this module deliberately has no dependencies;
    ``tests/test_projection.py`` asserts the three implementations agree, so the copy
    cannot drift silently.
    """
    if value >= 0:
        return int(math.floor(value + 0.5))
    return -int(math.floor(-value + 0.5))


def point(x: int, y: int) -> Point:
    """Build a point, refusing anything that is not an integer millimetre."""
    if (
        isinstance(x, bool)
        or isinstance(y, bool)
        or not isinstance(x, int)
        or not isinstance(y, int)
    ):
        raise TypeError(
            "primitive coordinates are integer millimetres, got (%r, %r). Round with "
            "round_half_away() at the point of construction, not in the renderer." % (x, y)
        )
    return (x, y)


def point_round(x: float, y: float) -> Point:
    """Round a computed (float) position into an integer-mm point."""
    return (round_half_away(x), round_half_away(y))


def point_of(value: Any) -> Point:
    """Accept a model ``Pt``, a mapping, or a 2-sequence, and return a ``Point``.

    This is the single conversion boundary between the document's geometry type and the
    primitive stream. Projectors call it; renderers never need it.
    """
    x = getattr(value, "x", None)
    y = getattr(value, "y", None)
    if x is None and isinstance(value, Mapping):
        x, y = value.get("x"), value.get("y")
    if x is None:
        seq = list(value)
        if len(seq) != 2:
            raise TypeError("cannot read a point from %r" % (value,))
        x, y = seq[0], seq[1]
    return point(int(x), int(y))


# ---------------------------------------------------------------------------
# Semantic kinds — hints, never contracts. Keep the list short and spelled out.
# ---------------------------------------------------------------------------
K_WALL_FACE = "wall-face"
K_WALL_END = "wall-end"
K_WALL_HATCH = "wall-hatch"
K_WALL_JAMB = "wall-jamb"
K_DOOR_LEAF = "door-leaf"
K_DOOR_SWING = "door-swing"
K_WINDOW_GLAZING = "window-glazing"
K_VENT_GLAZING = "ventilator-glazing"
K_STAIR_OUTLINE = "stair-outline"
K_STAIR_TREAD = "stair-tread"
K_STAIR_ARROW = "stair-arrow"
K_STAIR_LABEL = "stair-label"
K_ROOM_OUTLINE = "room-outline"
K_ROOM_NAME = "room-name"
K_ROOM_AREA = "room-area"
K_LEVEL_MARKER = "level-marker"
K_LEVEL_LABEL = "level-label"
K_NORTH_ARROW = "north-arrow"
K_NORTH_LABEL = "north-label"
K_SECTION_LINE = "section-line"
K_SECTION_FLAG = "section-flag"
K_SECTION_LABEL = "section-label"
K_GRID_LINE = "grid-line"
K_GRID_BUBBLE = "grid-bubble"
K_GRID_LABEL = "grid-label"
K_COLUMN = "column"
K_COLUMN_HATCH = "column-hatch"
K_BALCONY = "balcony"
K_BALCONY_RAILING = "balcony-railing"
K_OPENING_TAG = "opening-tag"
K_SHEET_BORDER = "sheet-border"
K_TITLE_BLOCK = "title-block"
K_TITLE_RULE = "title-block-rule"
K_TITLE_LABEL = "title-block-label"
K_TITLE_VALUE = "title-block-value"

# ---------------------------------------------------------------------------
# Hatch patterns. ezdxf ships the ANSI set; the SVG renderer emulates them with
# <pattern>. Named here so the two cannot pick different fills for the same wall.
# ---------------------------------------------------------------------------
#: 45° single hatch — brick/masonry poché for external walls.
PATTERN_MASONRY = "ANSI31"
#: Crossed 45° hatch — reinforced concrete (columns, cut slabs in section).
PATTERN_CONCRETE = "ANSI37"
#: Solid fill.
PATTERN_SOLID = "SOLID"

#: Text alignment tokens, kept as plain strings so the JSON form needs no mapping.
H_ALIGNS = ("left", "center", "right")
V_ALIGNS = ("top", "middle", "bottom", "baseline")


# ---------------------------------------------------------------------------
# The five primitives
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Line:
    """A straight segment. The workhorse: wall faces, jambs, treads, leaders."""

    layer: str
    a: Point
    b: Point
    #: Drawn with the layer's dashes overridden to a dashed pattern.
    dashed: bool = False
    #: Model element this came from — how annotations stay anchored (§7) and how a
    #: click on a rendered sheet finds its way back to a wall.
    owner_id: str | None = None
    kind: str = ""

    def to_json(self) -> dict[str, Any]:
        return _strip(
            {
                "t": "line",
                "layer": self.layer,
                "a": list(self.a),
                "b": list(self.b),
                "dashed": self.dashed,
                "ownerId": self.owner_id,
                "kind": self.kind,
            }
        )


@dataclass(frozen=True)
class Arc:
    """A circular arc, swept **counter-clockwise from start_deg to end_deg**.

    CCW is DXF's convention for an ``ARC`` entity, and matching it means the DXF writer
    passes the numbers straight through instead of reinterpreting them — one fewer place
    for a mirrored door swing to appear in one format and not the other. A full circle
    (a column bubble) is ``start_deg == 0, end_deg == 360``.
    """

    layer: str
    centre: Point
    radius_mm: int
    start_deg: int
    end_deg: int
    dashed: bool = False
    owner_id: str | None = None
    kind: str = ""

    @property
    def sweep_deg(self) -> int:
        """Positive CCW sweep in degrees."""
        return (self.end_deg - self.start_deg) % 360 or (
            360 if self.end_deg != self.start_deg else 0
        )

    def flatten(self, steps: int = 12) -> tuple[Point, ...]:
        """Approximate the arc as a polyline — for renderers without an arc entity.

        ``steps`` is per quadrant-ish; 12 over a 90° door swing reads smooth at 1:50,
        which is what the canvas twin uses (``planGeometry.ts``, ``ARC_STEPS``).
        """
        if steps < 1:
            raise ValueError("steps must be >= 1")
        sweep = self.sweep_deg
        out: list[Point] = []
        for index in range(steps + 1):
            angle = math.radians(self.start_deg + sweep * index / steps)
            out.append(
                point_round(
                    self.centre[0] + self.radius_mm * math.cos(angle),
                    self.centre[1] + self.radius_mm * math.sin(angle),
                )
            )
        return tuple(out)

    def to_json(self) -> dict[str, Any]:
        return _strip(
            {
                "t": "arc",
                "layer": self.layer,
                "c": list(self.centre),
                "r": self.radius_mm,
                "start": self.start_deg,
                "end": self.end_deg,
                "dashed": self.dashed,
                "ownerId": self.owner_id,
                "kind": self.kind,
            }
        )


@dataclass(frozen=True)
class Text:
    """A single line of text.

    ``height_mm`` is a **model** millimetre height in a model-space stream: a 2.5mm
    paper letter is 250mm tall at 1:100. Getting this wrong is the silent sheet-ruining
    mistake §7 warns about, which is why no projector picks a height itself — they all
    go through ``sheets.transform.paper_to_model_mm``.
    """

    layer: str
    position: Point
    text: str
    height_mm: int
    #: Integer degrees CCW. Text on a drawing is horizontal or reads along a wall.
    rotation_deg: int = 0
    h_align: str = "center"
    v_align: str = "middle"
    owner_id: str | None = None
    kind: str = ""

    def to_json(self) -> dict[str, Any]:
        return _strip(
            {
                "t": "text",
                "layer": self.layer,
                "p": list(self.position),
                "s": self.text,
                "h": self.height_mm,
                "rot": self.rotation_deg,
                "ha": self.h_align,
                "va": self.v_align,
                "ownerId": self.owner_id,
                "kind": self.kind,
            }
        )


@dataclass(frozen=True)
class Hatch:
    """A filled/hatched region: one outer boundary, optional holes.

    Used for external-wall poché, column fills and (in section) cut concrete. The
    boundary is a closed ring **without** a repeated last vertex, matching the model
    core's ``Polygon`` convention so a room polygon can be handed over unchanged.
    """

    layer: str
    boundary: tuple[Point, ...]
    pattern: str = PATTERN_MASONRY
    #: Pattern rotation, integer degrees.
    angle_deg: int = 0
    #: Pattern line spacing in **model** mm (paper-scaled by the projector).
    spacing_mm: int = 250
    holes: tuple[tuple[Point, ...], ...] = ()
    owner_id: str | None = None
    kind: str = ""

    def to_json(self) -> dict[str, Any]:
        return _strip(
            {
                "t": "hatch",
                "layer": self.layer,
                "b": [list(p) for p in self.boundary],
                "pattern": self.pattern,
                "angle": self.angle_deg,
                "spacing": self.spacing_mm,
                "holes": [[list(p) for p in ring] for ring in self.holes] or None,
                "ownerId": self.owner_id,
                "kind": self.kind,
            }
        )


@dataclass(frozen=True)
class Polyline:
    """A connected run of segments, optionally closed.

    Kept distinct from a bag of :class:`Line` s because a DXF ``LWPOLYLINE`` is one
    entity a CAD user can select and offset as a unit — a room outline or a plot
    boundary should not arrive in AutoCAD as four unrelated lines.
    """

    layer: str
    points: tuple[Point, ...]
    closed: bool = False
    dashed: bool = False
    owner_id: str | None = None
    kind: str = ""

    def to_json(self) -> dict[str, Any]:
        return _strip(
            {
                "t": "polyline",
                "layer": self.layer,
                "pts": [list(p) for p in self.points],
                "closed": self.closed,
                "dashed": self.dashed,
                "ownerId": self.owner_id,
                "kind": self.kind,
            }
        )


#: What every projector returns and every renderer accepts. Nothing else.
Primitive = Line | Arc | Text | Hatch | Polyline

PRIMITIVE_TYPES: tuple[type, ...] = (Line, Arc, Text, Hatch, Polyline)


def _strip(raw: dict[str, Any]) -> dict[str, Any]:
    """Drop empty optional keys so the JSON (and the golden diff) stays readable."""
    return {
        key: value
        for key, value in raw.items()
        if value is not None and value is not False and value != ""
    }


# ---------------------------------------------------------------------------
# Text hygiene (§13)
# ---------------------------------------------------------------------------
#: §13: the SVG is sanitised — "no scripts, no foreignObject". Escaping is the SVG
#: renderer's job; the primitive stream's job is never to carry markup in the first
#: place. These are the tokens :func:`find_unsafe_text` refuses. Every one of them needs
#: a ``<``, which :func:`sanitise_text` has already replaced — so a non-empty result
#: means some text reached a primitive without being sanitised, which is the bug worth
#: catching.
_UNSAFE_TOKENS = ("<script", "</script", "<foreignobject", "<svg", "<iframe", "<!--", "<![cdata[")

#: Room names come from users. A label is one line, and 120 characters is already far
#: more than fits in a room at 1:100 — truncating here beats a label crossing the sheet.
MAX_TEXT_LENGTH = 120


def sanitise_text(raw: str, *, max_length: int = MAX_TEXT_LENGTH) -> str:
    """Make user text safe to place on a drawing: one line, no controls, bounded.

    Deliberately NOT an XML escaper. Escaping is the renderer's responsibility (each
    format escapes differently) and doing it here would put ``&amp;`` into the DXF that
    a reviewer reads as literal text. What this does remove is the class of input that
    has no business on a sheet at all: control characters, newlines, unbounded length —
    and angle brackets.

    Angle brackets are *replaced with parentheses* rather than escaped, which is a
    decision worth stating: no room name, opening tag, firm name or drawing note needs
    ``<`` or ``>``, and removing them at the source turns :func:`find_unsafe_text` from a
    warning that legitimate data can trip into an invariant a renderer test can assert.
    Ampersands stay — "Living & Dining" is a real room name — and each renderer escapes
    them in its own syntax.
    """
    cleaned = "".join(" " if ch in "\t\r\n\v\f" else ch for ch in raw)
    cleaned = "".join(ch for ch in cleaned if ord(ch) >= 0x20 and ord(ch) != 0x7F)
    cleaned = cleaned.replace("<", "(").replace(">", ")")
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 1].rstrip() + "…"
    return cleaned


def find_unsafe_text(primitives: Sequence[Primitive]) -> tuple[tuple[str, str], ...]:
    """Every ``(kind, text)`` that carries markup-ish content. Empty is the only pass.

    A cheap §13 backstop the renderer tests can assert on: if this is ever non-empty,
    something bypassed :func:`sanitise_text` on its way to a sheet.
    """
    found: list[tuple[str, str]] = []
    for item in primitives:
        if not isinstance(item, Text):
            continue
        lowered = item.text.lower()
        if any(token in lowered for token in _UNSAFE_TOKENS):
            found.append((item.kind, item.text))
    return tuple(found)


# ---------------------------------------------------------------------------
# Inspection: what the tests, the smoke run and the golden harness all use
# ---------------------------------------------------------------------------
def count_by_layer(primitives: Sequence[Primitive]) -> dict[str, int]:
    """Primitive count per layer, in the canonical §7 layer order.

    The one number that tells you at a glance whether a projection did its job: a plan
    with no A-DOOR entities has no doors, whatever the wall count says.
    """
    from services.drawings.layers import LAYER_NAMES

    counts: dict[str, int] = {}
    for item in primitives:
        counts[item.layer] = counts.get(item.layer, 0) + 1
    ordered = {name: counts[name] for name in LAYER_NAMES if name in counts}
    # Anything not in the nine is a bug, but report it rather than hide it.
    for name in sorted(counts):
        if name not in ordered:
            ordered[name] = counts[name]
    return ordered


def count_by_kind(primitives: Sequence[Primitive]) -> dict[str, int]:
    """Primitive count per semantic kind, alphabetical."""
    counts: dict[str, int] = {}
    for item in primitives:
        counts[item.kind] = counts.get(item.kind, 0) + 1
    return {key: counts[key] for key in sorted(counts)}


def by_layer(primitives: Sequence[Primitive], layer: str) -> tuple[Primitive, ...]:
    return tuple(item for item in primitives if item.layer == layer)


def by_kind(primitives: Sequence[Primitive], kind: str) -> tuple[Primitive, ...]:
    return tuple(item for item in primitives if item.kind == kind)


def by_owner(primitives: Sequence[Primitive], owner_id: str) -> tuple[Primitive, ...]:
    return tuple(item for item in primitives if item.owner_id == owner_id)


def points_of(item: Primitive) -> tuple[Point, ...]:
    """Every defining point of a primitive. Arcs report their circle's extent box.

    The arc case is deliberately conservative: bounding an arc exactly means case-work
    on which axis crossings the sweep contains, and a slightly generous plan extent
    only ever means slightly more margin on the sheet.
    """
    if isinstance(item, Line):
        return (item.a, item.b)
    if isinstance(item, Arc):
        cx, cy = item.centre
        r = item.radius_mm
        return ((cx - r, cy - r), (cx + r, cy + r))
    if isinstance(item, Text):
        return (item.position,)
    if isinstance(item, Hatch):
        return tuple(item.boundary)
    if isinstance(item, Polyline):
        return tuple(item.points)
    raise TypeError("not a primitive: %r" % (item,))


def bbox_of(primitives: Sequence[Primitive]) -> tuple[int, int, int, int] | None:
    """``(min_x, min_y, max_x, max_y)`` over every primitive, or None when empty."""
    xs: list[int] = []
    ys: list[int] = []
    for item in primitives:
        for px, py in points_of(item):
            xs.append(px)
            ys.append(py)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def translate(primitives: Iterable[Primitive], dx: int, dy: int) -> tuple[Primitive, ...]:
    """Move a whole stream. Integer only — no scaling, no rotation, no drift."""
    if not isinstance(dx, int) or not isinstance(dy, int):
        raise TypeError("translate takes integer millimetres, got (%r, %r)" % (dx, dy))
    out: list[Primitive] = []
    for item in primitives:
        out.append(_translate_one(item, dx, dy))
    return tuple(out)


def _translate_one(item: Primitive, dx: int, dy: int) -> Primitive:
    from dataclasses import replace

    def shift(p: Point) -> Point:
        return (p[0] + dx, p[1] + dy)

    if isinstance(item, Line):
        return replace(item, a=shift(item.a), b=shift(item.b))
    if isinstance(item, Arc):
        return replace(item, centre=shift(item.centre))
    if isinstance(item, Text):
        return replace(item, position=shift(item.position))
    if isinstance(item, Hatch):
        return replace(
            item,
            boundary=tuple(shift(p) for p in item.boundary),
            holes=tuple(tuple(shift(p) for p in ring) for ring in item.holes),
        )
    if isinstance(item, Polyline):
        return replace(item, points=tuple(shift(p) for p in item.points))
    raise TypeError("not a primitive: %r" % (item,))


# ---------------------------------------------------------------------------
# Validation & goldens
# ---------------------------------------------------------------------------
class PrimitiveError(AssertionError):
    """A primitive stream violates an invariant. Always a bug in a projector."""


def validate_primitives(primitives: Sequence[Primitive]) -> None:
    """Assert the invariants every stream must hold. **Used by the test suite.**

    1. every primitive is one of the five types;
    2. its layer is one of the nine §7 layers (typos fail loudly here, not in AutoCAD);
    3. every coordinate is an ``int`` — no float has crept in through a trig call;
    4. no degenerate geometry: zero-length lines, zero-radius arcs, rings under three
       points, empty or oversized text.
    """
    from services.drawings.layers import layer_for

    problems: list[str] = []
    for index, item in enumerate(primitives):
        if not isinstance(item, PRIMITIVE_TYPES):
            problems.append("#%d is %s, not a primitive" % (index, type(item).__name__))
            continue
        try:
            layer_for(item.layer)
        except KeyError as exc:
            problems.append("#%d %s" % (index, exc.args[0]))
        for px, py in points_of(item):
            if (
                isinstance(px, bool)
                or isinstance(py, bool)
                or not isinstance(px, int)
                or not isinstance(py, int)
            ):
                problems.append(
                    "#%d %s has non-integer point (%r, %r)" % (index, item.kind, px, py)
                )
                break
        if isinstance(item, Line) and item.a == item.b:
            problems.append("#%d %s is a zero-length line at %s" % (index, item.kind, item.a))
        elif isinstance(item, Arc) and item.radius_mm <= 0:
            problems.append("#%d %s has radius %d" % (index, item.kind, item.radius_mm))
        elif isinstance(item, Text):
            if item.text == "":
                problems.append("#%d %s is empty text" % (index, item.kind))
            if item.height_mm <= 0:
                problems.append("#%d %s has text height %d" % (index, item.kind, item.height_mm))
            if item.h_align not in H_ALIGNS or item.v_align not in V_ALIGNS:
                problems.append(
                    "#%d %s has alignment (%r, %r)" % (index, item.kind, item.h_align, item.v_align)
                )
        elif isinstance(item, Hatch) and len(item.boundary) < 3:
            problems.append(
                "#%d %s hatch boundary has %d points" % (index, item.kind, len(item.boundary))
            )
        elif isinstance(item, Polyline) and len(item.points) < 2:
            problems.append("#%d %s polyline has %d points" % (index, item.kind, len(item.points)))
    if problems:
        raise PrimitiveError(
            "%d invalid primitive(s):\n  %s" % (len(problems), "\n  ".join(problems[:12]))
        )


def primitives_to_json(primitives: Sequence[Primitive]) -> list[dict[str, Any]]:
    """The JSON form. Stable field order, integers only — golden-file ready."""
    return [item.to_json() for item in primitives]


def canonical_json(primitives: Sequence[Primitive]) -> str:
    """Canonical serialisation of a stream: sorted keys, no whitespace, no floats.

    Mirrors the model core's ``canonical_json`` discipline (``garh_model.fold``) for the
    same reason: a hash is only useful if two runs of the same input produce the same
    bytes.
    """
    payload = primitives_to_json(primitives)
    for entry in payload:
        for key, value in entry.items():
            if isinstance(value, float):
                raise PrimitiveError("float in primitive JSON: %s=%r" % (key, value))
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def primitives_digest(primitives: Sequence[Primitive]) -> str:
    """SHA-256 of :func:`canonical_json` — a one-line golden for a whole projection.

    Cheaper than an SVG golden and it fails for exactly the same reasons, so a
    projector change that alters geometry cannot slip through while the renderers are
    still being written.
    """
    return hashlib.sha256(canonical_json(primitives).encode("utf-8")).hexdigest()


__all__ = [
    "H_ALIGNS",
    "MAX_TEXT_LENGTH",
    "PATTERN_CONCRETE",
    "PATTERN_MASONRY",
    "PATTERN_SOLID",
    "PRIMITIVE_TYPES",
    "V_ALIGNS",
    "Arc",
    "Hatch",
    "Line",
    "Point",
    "Polyline",
    "Primitive",
    "PrimitiveError",
    "Text",
    "bbox_of",
    "by_kind",
    "by_layer",
    "by_owner",
    "canonical_json",
    "count_by_kind",
    "count_by_layer",
    "find_unsafe_text",
    "point",
    "point_of",
    "point_round",
    "points_of",
    "primitives_digest",
    "primitives_to_json",
    "round_half_away",
    "sanitise_text",
    "translate",
    "validate_primitives",
    # semantic kinds
    "K_BALCONY",
    "K_BALCONY_RAILING",
    "K_COLUMN",
    "K_COLUMN_HATCH",
    "K_DOOR_LEAF",
    "K_DOOR_SWING",
    "K_GRID_BUBBLE",
    "K_GRID_LABEL",
    "K_GRID_LINE",
    "K_LEVEL_LABEL",
    "K_LEVEL_MARKER",
    "K_NORTH_ARROW",
    "K_NORTH_LABEL",
    "K_OPENING_TAG",
    "K_ROOM_AREA",
    "K_ROOM_NAME",
    "K_ROOM_OUTLINE",
    "K_SECTION_FLAG",
    "K_SECTION_LABEL",
    "K_SECTION_LINE",
    "K_SHEET_BORDER",
    "K_STAIR_ARROW",
    "K_STAIR_LABEL",
    "K_STAIR_OUTLINE",
    "K_STAIR_TREAD",
    "K_TITLE_BLOCK",
    "K_TITLE_LABEL",
    "K_TITLE_RULE",
    "K_TITLE_VALUE",
    "K_VENT_GLAZING",
    "K_WALL_END",
    "K_WALL_FACE",
    "K_WALL_HATCH",
    "K_WALL_JAMB",
    "K_WINDOW_GLAZING",
]
