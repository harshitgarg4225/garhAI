"""Window symbols in plan: casement, sliding, fixed, ventilator.

The convention a municipal reviewer expects is the double line: the wall is cut at the
opening, both faces are closed by the frame, the glazing runs down the middle, and the
sill shows outside. That is what these emit.

Local frame (before :func:`~services.drawings.blocks.base.place`):

* the origin is the **centre of the opening, on the wall centre line**;
* ``+X`` runs along the wall — the opening occupies ``span(width_mm)``;
* ``+Y`` is **inside**. The sill therefore projects towards ``−Y``, and a caller placing
  a window on a wall must rotate so that ``+Y`` faces the room, exactly as for a door.

Two layer decisions, both taken from the §7 layer table rather than invented:

* frame, glazing and jamb reveals are **A-WIND**;
* the sill is **A-WALL-PART**, whose own description in ``layers.py`` reads "Partial-
  height walls, parapets, sills". It is not glazing, and a reviewer freezing A-WIND to
  read the plan should still see what projects into the setback.

The ventilator is drawn entirely in the **hidden** line style. A ventilator sits above
the plan's cut plane, so every line of it is something seen beyond the cut — drawing it
solid says "this opening is at eye level", which is what the setback and the light-and-
ventilation check both then read wrongly.
"""

from __future__ import annotations

from services.drawings.blocks.base import (
    Insertion,
    place,
    require_positive,
    span,
)
from services.drawings.layers import A_WALL_PART, A_WIND
from services.drawings.render.primitives import (
    STYLE_HIDDEN,
    STYLE_SOLID,
    Line,
    LineStyle,
    Polyline,
    Primitive,
)

__all__ = [
    "DEFAULT_SILL_PROJECTION_MM",
    "DEFAULT_SILL_RETURN_MM",
    "window_casement",
    "window_fixed",
    "window_sliding",
    "window_ventilator",
]

#: How far a sill noses past the outer wall face, and how far past each jamb. 60 mm is
#: the projection an Indian granite/RCC sill is cast at; both are arguments because a
#: chajja-integrated sill is not.
DEFAULT_SILL_PROJECTION_MM = 60
DEFAULT_SILL_RETURN_MM = 60


def _carcass(
    *,
    width_mm: int,
    wall_thickness_mm: int,
    sill_projection_mm: int,
    sill_return_mm: int,
    style: LineStyle,
) -> tuple[Primitive, ...]:
    """Jamb reveals, both wall faces across the opening, and the sill outside."""
    x_lo, x_hi = span(width_mm)
    y_lo, y_hi = span(wall_thickness_mm)
    out: list[Primitive] = [
        Line(a=(x_lo, y_lo), b=(x_lo, y_hi), layer=A_WIND, style=style),
        Line(a=(x_hi, y_lo), b=(x_hi, y_hi), layer=A_WIND, style=style),
        Line(a=(x_lo, y_lo), b=(x_hi, y_lo), layer=A_WIND, style=style),
        Line(a=(x_lo, y_hi), b=(x_hi, y_hi), layer=A_WIND, style=style),
    ]
    if sill_projection_mm > 0:
        out.append(
            Polyline(
                vertices=(
                    (x_lo - sill_return_mm, y_lo),
                    (x_lo - sill_return_mm, y_lo - sill_projection_mm),
                    (x_hi + sill_return_mm, y_lo - sill_projection_mm),
                    (x_hi + sill_return_mm, y_lo),
                ),
                layer=A_WALL_PART,
                style=style,
            )
        )
    return tuple(out)


def _leaf_bounds(width_mm: int, leaves: int) -> tuple[int, ...]:
    """Exact leaf boundaries across the opening, including both jambs.

    Integer partition, so ``leaves`` leaves of a 1235 mm window still measure 1235 mm
    end to end rather than ``leaves * (1235 // leaves)``.
    """
    x_lo, _ = span(width_mm)
    return tuple(x_lo + (width_mm * index) // leaves for index in range(leaves + 1))


def _common(
    *,
    width_mm: int,
    wall_thickness_mm: int,
    sill_projection_mm: int,
    sill_return_mm: int,
) -> None:
    require_positive("width_mm", width_mm)
    require_positive("wall_thickness_mm", wall_thickness_mm)
    if sill_projection_mm < 0:
        raise ValueError("sill_projection_mm cannot be negative, got %d" % sill_projection_mm)
    if sill_return_mm < 0:
        raise ValueError("sill_return_mm cannot be negative, got %d" % sill_return_mm)


def window_fixed(
    *,
    width_mm: int,
    wall_thickness_mm: int,
    element_id: str,
    sill_projection_mm: int = DEFAULT_SILL_PROJECTION_MM,
    sill_return_mm: int = DEFAULT_SILL_RETURN_MM,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """A fixed light: frame, one pane, sill. No opening leaf, so no mullion."""
    _common(
        width_mm=width_mm,
        wall_thickness_mm=wall_thickness_mm,
        sill_projection_mm=sill_projection_mm,
        sill_return_mm=sill_return_mm,
    )
    x_lo, x_hi = span(width_mm)
    local = (
        *_carcass(
            width_mm=width_mm,
            wall_thickness_mm=wall_thickness_mm,
            sill_projection_mm=sill_projection_mm,
            sill_return_mm=sill_return_mm,
            style=STYLE_SOLID,
        ),
        Line(a=(x_lo, 0), b=(x_hi, 0), layer=A_WIND),
    )
    return place(local, insertion, element_id)


def window_casement(
    *,
    width_mm: int,
    wall_thickness_mm: int,
    element_id: str,
    leaves: int = 2,
    sill_projection_mm: int = DEFAULT_SILL_PROJECTION_MM,
    sill_return_mm: int = DEFAULT_SILL_RETURN_MM,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """A casement: one glazing line per leaf, a mullion across the reveal between each.

    The leaves' swing is *not* drawn. In plan a casement sash swings above the cut plane
    and the arc a door needs would be a line through the room that nothing is on; the
    schedule and the elevation carry the hand. Drawing an arc here would put a
    clearance on the plan that the building does not have.
    """
    _common(
        width_mm=width_mm,
        wall_thickness_mm=wall_thickness_mm,
        sill_projection_mm=sill_projection_mm,
        sill_return_mm=sill_return_mm,
    )
    if leaves < 1:
        raise ValueError("a casement has at least 1 leaf, got %d" % leaves)
    if width_mm < leaves:
        raise ValueError("%d leaves cannot be drawn across a %d mm window" % (leaves, width_mm))

    y_lo, y_hi = span(wall_thickness_mm)
    bounds = _leaf_bounds(width_mm, leaves)
    local: list[Primitive] = list(
        _carcass(
            width_mm=width_mm,
            wall_thickness_mm=wall_thickness_mm,
            sill_projection_mm=sill_projection_mm,
            sill_return_mm=sill_return_mm,
            style=STYLE_SOLID,
        )
    )
    for index in range(leaves):
        local.append(Line(a=(bounds[index], 0), b=(bounds[index + 1], 0), layer=A_WIND))
    for index in range(1, leaves):
        local.append(Line(a=(bounds[index], y_lo), b=(bounds[index], y_hi), layer=A_WIND))
    return place(tuple(local), insertion, element_id)


def window_sliding(
    *,
    width_mm: int,
    wall_thickness_mm: int,
    element_id: str,
    overlap_mm: int = 50,
    sill_projection_mm: int = DEFAULT_SILL_PROJECTION_MM,
    sill_return_mm: int = DEFAULT_SILL_RETURN_MM,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """Two sashes on two tracks, drawn on either side of the frame centre line.

    The offset is what distinguishes this from a casement on a plan at 1:100: the sashes
    pass each other, so they cannot be on one line. They overlap at the meeting stile by
    ``overlap_mm``, which is what makes the window weatherproof and what makes the
    drawing readable.
    """
    _common(
        width_mm=width_mm,
        wall_thickness_mm=wall_thickness_mm,
        sill_projection_mm=sill_projection_mm,
        sill_return_mm=sill_return_mm,
    )
    if overlap_mm < 0:
        raise ValueError("overlap_mm cannot be negative, got %d" % overlap_mm)
    x_lo, x_hi = span(width_mm)
    x_mid = x_lo + width_mm // 2
    if x_mid + overlap_mm > x_hi or x_mid - overlap_mm < x_lo:
        raise ValueError(
            "a %d mm overlap does not fit a %d mm sliding window" % (overlap_mm, width_mm)
        )
    # A track sits a third of the way through the reveal from each face, which is where
    # a 2-track aluminium section actually puts them.
    offset = max(1, wall_thickness_mm // 6)

    local = (
        *_carcass(
            width_mm=width_mm,
            wall_thickness_mm=wall_thickness_mm,
            sill_projection_mm=sill_projection_mm,
            sill_return_mm=sill_return_mm,
            style=STYLE_SOLID,
        ),
        Line(a=(x_lo, -offset), b=(x_mid + overlap_mm, -offset), layer=A_WIND),
        Line(a=(x_mid - overlap_mm, offset), b=(x_hi, offset), layer=A_WIND),
    )
    return place(local, insertion, element_id)


def window_ventilator(
    *,
    width_mm: int,
    wall_thickness_mm: int,
    element_id: str,
    sill_projection_mm: int = DEFAULT_SILL_PROJECTION_MM,
    sill_return_mm: int = DEFAULT_SILL_RETURN_MM,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """A high-level ventilator, every line hidden because it is above the cut plane."""
    _common(
        width_mm=width_mm,
        wall_thickness_mm=wall_thickness_mm,
        sill_projection_mm=sill_projection_mm,
        sill_return_mm=sill_return_mm,
    )
    x_lo, x_hi = span(width_mm)
    local = (
        *_carcass(
            width_mm=width_mm,
            wall_thickness_mm=wall_thickness_mm,
            sill_projection_mm=sill_projection_mm,
            sill_return_mm=sill_return_mm,
            style=STYLE_HIDDEN,
        ),
        Line(a=(x_lo, 0), b=(x_hi, 0), layer=A_WIND, style=STYLE_HIDDEN),
    )
    return place(local, insertion, element_id)
