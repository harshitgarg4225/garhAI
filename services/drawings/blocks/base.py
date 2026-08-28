"""Insertion, rotation, and the integer helpers every block shares.

The one rule this module exists to enforce: **a block is authored in its own local
frame, at the origin, and is moved exactly once.** Every block function builds its
geometry around ``(0, 0)`` in whatever orientation is natural for it, then hands the
list to :func:`place`. Nothing else rotates, translates or stamps an id.

Why that is worth a module. Rotation of integer millimetres is lossy — ``cos 37°`` is
not a rational number — so a block that rotated its own pieces would round twice for a
door drawn on a skewed wall, and the leaf would no longer meet the jamb it was built to
meet. Rotating once, at the end, means every block's internal geometry is exact by
construction and the only rounding is the single transform a reviewer can see.

:func:`place` also stamps ``element_id`` on every primitive it returns. That is
deliberate rather than tidy: the canvas furniture layer once tagged its meshes for
hit-testing, documented itself as integrated, and never called the registry, so every
placed item was invisible to clicks. A block cannot make that mistake here, because a
block never sets an id at all — the shared helper does, on everything, or the primitive
does not leave the library.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from services.drawings.render.primitives import (
    Arc,
    Circle,
    Hatch,
    Line,
    Polyline,
    Primitive,
    Pt2,
    Text,
)

__all__ = [
    "MAX_LABEL_LENGTH",
    "Insertion",
    "arc_endpoint",
    "arrow",
    "block_extent",
    "label_text",
    "paper_mm_to_model_mm",
    "place",
    "readable_rotation",
    "require_choice",
    "require_int",
    "require_positive",
    "round_half_away",
    "span",
]

#: Caller-supplied labels (a socket rating, a DB name) are bounded before they reach a
#: sheet. Long enough for "GEYSER 25A", short enough that it cannot push a title block
#: apart.
MAX_LABEL_LENGTH = 24


# ---------------------------------------------------------------------------
# Integer arithmetic
# ---------------------------------------------------------------------------
def round_half_away(value: float) -> int:
    """Round to the nearest integer, halves away from zero.

    The model core's rounding contract, restated here for the same reason
    ``render/hatch_patterns.py``, ``projection/primitives.py`` and
    ``solver/geometry.py`` each restate it: this package's dependency is the primitive
    vocabulary, not a rounding utility, and the copies are held together by a test
    (``test_blocks.py::test_rounding_agrees_with_every_other_mirror``) rather than by
    hope. Python's ``round`` goes to even, which is fine arithmetic and wrong for a
    golden file.
    """
    if value >= 0:
        return int(math.floor(value + 0.5))
    return -int(math.floor(-value + 0.5))


def span(size_mm: int) -> tuple[int, int]:
    """Centre a size on the origin: ``(low, high)`` with ``high - low == size_mm``.

    Exactly, for odd sizes too — a 115 mm wall gives ``(-57, 58)``. The alternative,
    ``±size//2``, loses a millimetre off an odd opening, and an opening that is 1 mm
    narrower than its schedule says is exactly the kind of drift the integer-mm rule
    exists to prevent.
    """
    low = size_mm // 2
    return (-low, size_mm - low)


def require_int(name: str, value: object) -> int:
    """Accept an integer millimetre, refuse a float, a bool or anything else."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            "%s must be an integer number of millimetres, got %r (%s). Model geometry "
            "is integer mm; parse at the boundary, not here." % (name, value, type(value).__name__)
        )
    return value


def require_positive(name: str, value: object) -> int:
    """An integer millimetre that must be > 0."""
    number = require_int(name, value)
    if number <= 0:
        raise ValueError("%s must be positive, got %d" % (name, number))
    return number


def require_choice(name: str, value: str, allowed: Sequence[str]) -> str:
    """Refuse a value outside the enum, loudly.

    This repository has already shipped the quiet version: an evaluation context
    defaulted ``buildingUse`` to a string that was not a member of the rule packs' own
    enum, and 83 rules reported ``not_applicable`` while the report stayed green. A
    block handed ``hand="LEFT"`` must fail here, not silently draw a right-hand door.
    """
    if value not in allowed:
        raise ValueError("%s must be one of %s, got %r" % (name, ", ".join(allowed), value))
    return value


def label_text(raw: str, *, max_length: int = MAX_LABEL_LENGTH) -> str:
    """Make caller text safe to put on a drawing: one line, no controls, bounded.

    Mirrors ``services.drawings.projection.primitives.sanitise_text`` — including its
    decision to *replace* angle brackets rather than escape them, because escaping is
    each renderer's own job and an escaped entity in a DXF reads as literal ``&amp;`` to
    the reviewer. ``test_blocks.py`` asserts the two agree on a corpus.
    """
    cleaned = "".join(" " if ch in "\t\r\n\v\f" else ch for ch in raw)
    cleaned = "".join(ch for ch in cleaned if ord(ch) >= 0x20 and ord(ch) != 0x7F)
    cleaned = cleaned.replace("<", "(").replace(">", ")")
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 1].rstrip() + "…"
    return cleaned


def paper_mm_to_model_mm(paper_mm: int, scale_denominator: int) -> int:
    """How big a paper-sized symbol has to be drawn in model space at a given scale.

    A north arrow, a switch and a scale bar are *notation*: they are the same physical
    size on the sheet whatever the building's scale, exactly like ISO 3098 text. The
    primitives here carry model millimetres, so the caller — which is the only code that
    knows the sheet scale — converts through this. Every notation block's default size
    is stated for 1:100, so this is what changes it.
    """
    require_positive("paper_mm", paper_mm)
    require_positive("scale_denominator", scale_denominator)
    return paper_mm * scale_denominator


# ---------------------------------------------------------------------------
# Insertion and the single rotation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Insertion:
    """Where a block lands: an origin in model mm and a rotation in integer degrees CCW.

    Integer degrees because :class:`~services.drawings.render.primitives.Arc` carries
    integer degrees — a door swing is 90° exactly, and a fractional degree would have to
    be rounded somewhere less visible than here.
    """

    at: Pt2 = (0, 0)
    rotation_deg: int = 0

    def __post_init__(self) -> None:
        require_int("at[0]", self.at[0])
        require_int("at[1]", self.at[1])
        require_int("rotation_deg", self.rotation_deg)
        # Normalise so an Arc built at 270° + a -90° insertion still lands in 0..359 and
        # a reader of the DXF sees the angles a CAD tool would have written.
        object.__setattr__(self, "rotation_deg", self.rotation_deg % 360)

    def rotated(self, extra_deg: int) -> Insertion:
        """The same origin, turned further CCW. Used where a block owns an angle of its
        own (a north arrow's bearing) that must compose with the caller's rotation."""
        return Insertion(
            at=self.at, rotation_deg=self.rotation_deg + require_int("extra_deg", extra_deg)
        )


#: Exact ``(cos, sin)`` at the four cardinal angles. Float trig gives ``cos 90° =
#: 6.1e-17``, which rounds to zero today at every size this library draws — but a block
#: is a thing other code multiplies, and "correct because the error is small" is how a
#: 1 mm drift enters a golden file. The four angles every wall-mounted block actually
#: uses are exact.
_CARDINALS: dict[int, tuple[int, int]] = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}


def _rotate(point: Pt2, rotation_deg: int) -> Pt2:
    x, y = point
    cardinal = _CARDINALS.get(rotation_deg)
    if cardinal is not None:
        cos_t, sin_t = cardinal
        return (x * cos_t - y * sin_t, x * sin_t + y * cos_t)
    theta = math.radians(rotation_deg)
    cos_f, sin_f = math.cos(theta), math.sin(theta)
    return (
        round_half_away(x * cos_f - y * sin_f),
        round_half_away(x * sin_f + y * cos_f),
    )


def readable_rotation(deg: int) -> int:
    """Flip text that would print upside down. 0–90 and 271–360 read left to right.

    Same rule as ``projection.symbols.readable_rotation``; a sheet where half the labels
    read upside down because the stair happened to run west is not a drawing anyone
    signs.
    """
    normalised = deg % 360
    if 90 < normalised <= 270:
        return (normalised + 180) % 360
    return normalised


def place(
    primitives: Sequence[Primitive],
    insertion: Insertion,
    element_id: str,
) -> tuple[Primitive, ...]:
    """Move a locally-authored block to its insertion point and stamp its id.

    Text is turned with the block, then flipped upright by :func:`readable_rotation` —
    but only when it is ``middle``-anchored, because flipping a ``start``-anchored label
    swings it to the far side of the point it was placed against and off its leader.
    Every label in this package is middle-anchored for that reason.
    """
    if not element_id:
        raise ValueError(
            "a block needs an element_id: it is what a picker maps a clicked primitive "
            "back to, and a block whose primitives carry none is invisible to selection."
        )
    rotation = insertion.rotation_deg
    dx, dy = insertion.at

    def moved(point: Pt2) -> Pt2:
        x, y = _rotate(point, rotation)
        return (x + dx, y + dy)

    out: list[Primitive] = []
    for prim in primitives:
        if isinstance(prim, Line):
            out.append(
                Line(
                    a=moved(prim.a),
                    b=moved(prim.b),
                    layer=prim.layer,
                    style=prim.style,
                    element_id=element_id,
                )
            )
        elif isinstance(prim, Polyline):
            out.append(
                Polyline(
                    vertices=tuple(moved(v) for v in prim.vertices),
                    layer=prim.layer,
                    closed=prim.closed,
                    style=prim.style,
                    element_id=element_id,
                )
            )
        elif isinstance(prim, Arc):
            out.append(
                Arc(
                    centre=moved(prim.centre),
                    radius_mm=prim.radius_mm,
                    start_deg=(prim.start_deg + rotation) % 360,
                    end_deg=(prim.end_deg + rotation) % 360,
                    layer=prim.layer,
                    style=prim.style,
                    element_id=element_id,
                )
            )
        elif isinstance(prim, Circle):
            out.append(
                Circle(
                    centre=moved(prim.centre),
                    radius_mm=prim.radius_mm,
                    layer=prim.layer,
                    style=prim.style,
                    element_id=element_id,
                )
            )
        elif isinstance(prim, Text):
            turned = (prim.rotation_deg + rotation) % 360
            out.append(
                Text(
                    at=moved(prim.at),
                    text=prim.text,
                    layer=prim.layer,
                    height_paper_um=prim.height_paper_um,
                    anchor=prim.anchor,
                    baseline=prim.baseline,
                    rotation_deg=readable_rotation(turned) if prim.anchor == "middle" else turned,
                    element_id=element_id,
                    bold=prim.bold,
                )
            )
        elif isinstance(prim, Hatch):
            out.append(
                Hatch(
                    outline=tuple(moved(v) for v in prim.outline),
                    layer=prim.layer,
                    pattern=prim.pattern,
                    spacing_mm=prim.spacing_mm,
                    # A hatch angle is measured in world space, so turning the block has
                    # to turn the hatch with it or a rotated wall's brick runs the wrong
                    # way against its neighbour's.
                    angle_deg=(prim.angle_deg + rotation) % 360,
                    holes=tuple(tuple(moved(v) for v in hole) for hole in prim.holes),
                    element_id=element_id,
                )
            )
        else:
            # Deliberately total. A silently dropped primitive is a missing door leaf on
            # a submission drawing, and Dim in particular cannot be rotated at all: a
            # DimChain is axis-aligned by construction, so a block that wants a dimension
            # must hand the chain to the sheet, not to place().
            raise TypeError(
                "place() cannot position a %s. Blocks emit Line, Polyline, Arc, Circle, "
                "Text and Hatch; a Dim belongs to the sheet's dimension pass." % type(prim).__name__
            )
    return tuple(out)


# ---------------------------------------------------------------------------
# Extents
# ---------------------------------------------------------------------------
def arc_endpoint(centre: Pt2, radius_mm: int, deg: float) -> Pt2:
    """Where an arc's ray at ``deg`` meets its radius, in integer mm.

    Takes a float because a spiral's tread pitch is ``sweep / treads`` and 360/13 is not
    an integer; the four cardinal angles stay exact whichever type they arrive as.
    """
    normalised = deg % 360
    whole = int(normalised)
    cardinal = _CARDINALS.get(whole) if whole == normalised else None
    cx, cy = centre
    if cardinal is not None:
        cos_t, sin_t = cardinal
        return (cx + radius_mm * cos_t, cy + radius_mm * sin_t)
    theta = math.radians(deg)
    return (
        cx + round_half_away(radius_mm * math.cos(theta)),
        cy + round_half_away(radius_mm * math.sin(theta)),
    )


def _arc_extreme_points(prim: Arc) -> tuple[Pt2, ...]:
    """An arc's true extent points: both ends, plus the cardinals it actually sweeps.

    ``Arc.points()`` returns the whole radius box, which is right for a sheet that must
    not clip anything and wrong for asserting that a washbasin is 450 mm deep — a 180°
    nose arc would report the fixture as extending 275 mm *behind* the wall it is fixed
    to. This is the exact version, and it is what the layout of a block library needs.
    """
    sweep = (prim.end_deg - prim.start_deg) % 360
    points = [
        arc_endpoint(prim.centre, prim.radius_mm, prim.start_deg),
        arc_endpoint(prim.centre, prim.radius_mm, prim.end_deg),
    ]
    for cardinal in (0, 90, 180, 270):
        # Zero sweep is a full circle — the SVG renderer treats it as one, so extents
        # must agree with what actually gets drawn.
        if sweep == 0 or (cardinal - prim.start_deg) % 360 <= sweep:
            points.append(arc_endpoint(prim.centre, prim.radius_mm, cardinal))
    return tuple(points)


def block_extent(primitives: Sequence[Primitive]) -> tuple[int, int, int, int] | None:
    """``(min_x, min_y, max_x, max_y)`` over a block, or None when it is empty.

    Text contributes its insertion point only: the glyph box depends on the font and the
    sheet scale, and guessing it here would be a second source of truth for something
    ``render.frame``'s collision grid already owns.
    """
    xs: list[int] = []
    ys: list[int] = []
    for prim in primitives:
        points = _arc_extreme_points(prim) if isinstance(prim, Arc) else prim.points()
        for x, y in points:
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


# ---------------------------------------------------------------------------
# The one arrow head
# ---------------------------------------------------------------------------
def arrow(
    tail: Pt2, head: Pt2, *, barb_mm: int, layer: str, style: str = "solid"
) -> tuple[Line, ...]:
    """Shaft plus two barbs, from ``tail`` to ``head``. One implementation, four users.

    A stair's direction arrow, a sliding door's travel arrow and a section's look
    direction are the same symbol; drawing them three times is how two of them end up
    with different barb angles on the same sheet.
    """
    require_positive("barb_mm", barb_mm)
    (ax, ay), (bx, by) = tail, head
    length = math.hypot(bx - ax, by - ay)
    if length == 0:
        raise ValueError("an arrow needs a direction: tail and head are the same point")
    ux, uy = (bx - ax) / length, (by - ay) / length
    # Barbs open back along the shaft at ~26° each side (half-width = barb/2), the
    # proportion an architectural arrowhead is drawn at.
    px, py = -uy, ux
    half = barb_mm / 2.0
    return (
        Line(a=tail, b=head, layer=layer, style=style),
        Line(
            a=head,
            b=(
                round_half_away(bx - ux * barb_mm + px * half),
                round_half_away(by - uy * barb_mm + py * half),
            ),
            layer=layer,
            style=style,
        ),
        Line(
            a=head,
            b=(
                round_half_away(bx - ux * barb_mm - px * half),
                round_half_away(by - uy * barb_mm - py * half),
            ),
            layer=layer,
            style=style,
        ),
    )
