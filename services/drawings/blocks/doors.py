"""Door symbols in plan: single-leaf swing, double, sliding, folding.

A plan door is a drawing *convention*, not artwork — a leaf drawn open at 90° plus the
quarter circle it sweeps — which is why this library generates them from the leaf width
and the wall thickness instead of shipping a licensed block set.

Local frame (every door block, before :func:`~services.drawings.blocks.base.place`):

* the origin is the **centre of the structural opening, on the wall centre line**;
* ``+X`` runs along the wall, so the opening occupies ``span(leaf_width_mm)``;
* ``+Y`` is the face the door opens towards when ``swing="in"``, and the wall occupies
  ``span(wall_thickness_mm)``.

``hand`` says which jamb the hinge is on — ``"left"`` is the ``−X`` jamb — and ``swing``
says which face it opens across. Both are stated as geometry rather than as "viewed from
outside", because "outside" is a property of the room the caller knows and the block does
not, and a symbol that guesses it is a door that opens into a staircase on the drawing
and into the room on site.

Everything lands on **A-DOOR**: leaves, swing arcs, jambs, tracks. A reviewer who freezes
A-DOOR expects the door to disappear, all of it.
"""

from __future__ import annotations

import math

from services.drawings.blocks.base import (
    Insertion,
    arrow,
    place,
    require_choice,
    require_positive,
    round_half_away,
    span,
)
from services.drawings.layers import A_DOOR
from services.drawings.render.primitives import (
    STYLE_DASHED,
    Arc,
    Line,
    Polyline,
    Primitive,
    Pt2,
)

__all__ = [
    "DEFAULT_LEAF_THICKNESS_MM",
    "FOLD_ANGLE_DEG",
    "HANDS",
    "HAND_LEFT",
    "HAND_RIGHT",
    "SWINGS",
    "SWING_IN",
    "SWING_OUT",
    "door_double_swing",
    "door_folding",
    "door_single_swing",
    "door_sliding",
]

HAND_LEFT = "left"
HAND_RIGHT = "right"
#: The enum, so a typo is a ValueError and never a silently mirrored door.
HANDS: tuple[str, ...] = (HAND_LEFT, HAND_RIGHT)

SWING_IN = "in"
SWING_OUT = "out"
SWINGS: tuple[str, ...] = (SWING_IN, SWING_OUT)

#: A flush shutter is 35–40 mm; 40 is what an Indian door schedule quotes.
DEFAULT_LEAF_THICKNESS_MM = 40

#: Bi-fold panels are drawn part-folded so both the panels and the opening read. 75°
#: from the wall is the angle the symbol is conventionally drawn at: steep enough that
#: the panels stack near the jamb, shallow enough that they do not overlap into one line.
FOLD_ANGLE_DEG = 75


def _jambs(x_lo: int, x_hi: int, y_lo: int, y_hi: int) -> tuple[Primitive, ...]:
    """The two reveals that close the wall at the opening."""
    return (
        Line(a=(x_lo, y_lo), b=(x_lo, y_hi), layer=A_DOOR),
        Line(a=(x_hi, y_lo), b=(x_hi, y_hi), layer=A_DOOR),
    )


def _leaf_and_swing(
    *,
    hinge: Pt2,
    leaf_width_mm: int,
    leaf_thickness_mm: int,
    towards_x: int,
    towards_y: int,
) -> tuple[Primitive, ...]:
    """One leaf drawn open at 90°, plus the quarter circle back to its closed position.

    ``towards_x`` is the direction the leaf closes in (along the wall) and ``towards_y``
    the direction it opens in. The arc is centred on the hinge with the leaf's own width
    as its radius, so the closed end of the sweep lands exactly on the far jamb — the
    property ``test_blocks.py`` asserts, because an arc drawn from the opening centre
    (the mistake that looks right at a glance) misses that jamb by half the leaf.
    """
    hx, hy = hinge
    tip_y = hy + towards_y * leaf_width_mm
    edge_x = hx + towards_x * leaf_thickness_mm
    closed_deg = 0 if towards_x > 0 else 180
    open_deg = 90 if towards_y > 0 else 270
    # CCW from whichever of the two bounds is 90° behind the other.
    if (open_deg - closed_deg) % 360 == 90:
        start_deg, end_deg = closed_deg, open_deg
    else:
        start_deg, end_deg = open_deg, closed_deg
    return (
        Polyline(
            vertices=((hx, hy), (edge_x, hy), (edge_x, tip_y), (hx, tip_y)),
            layer=A_DOOR,
            closed=True,
        ),
        Arc(
            centre=hinge,
            radius_mm=leaf_width_mm,
            start_deg=start_deg,
            end_deg=end_deg,
            layer=A_DOOR,
        ),
    )


def door_single_swing(
    *,
    leaf_width_mm: int,
    wall_thickness_mm: int,
    element_id: str,
    hand: str = HAND_LEFT,
    swing: str = SWING_IN,
    leaf_thickness_mm: int = DEFAULT_LEAF_THICKNESS_MM,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """One leaf, hinged at ``hand``'s jamb, opening across the ``swing`` face."""
    require_positive("leaf_width_mm", leaf_width_mm)
    require_positive("wall_thickness_mm", wall_thickness_mm)
    require_positive("leaf_thickness_mm", leaf_thickness_mm)
    require_choice("hand", hand, HANDS)
    require_choice("swing", swing, SWINGS)

    x_lo, x_hi = span(leaf_width_mm)
    y_lo, y_hi = span(wall_thickness_mm)
    towards_x = 1 if hand == HAND_LEFT else -1
    towards_y = 1 if swing == SWING_IN else -1
    hinge = (x_lo if hand == HAND_LEFT else x_hi, y_hi if swing == SWING_IN else y_lo)

    local = (
        *_jambs(x_lo, x_hi, y_lo, y_hi),
        *_leaf_and_swing(
            hinge=hinge,
            leaf_width_mm=leaf_width_mm,
            leaf_thickness_mm=leaf_thickness_mm,
            towards_x=towards_x,
            towards_y=towards_y,
        ),
    )
    return place(local, insertion, element_id)


def door_double_swing(
    *,
    leaf_width_mm: int,
    wall_thickness_mm: int,
    element_id: str,
    swing: str = SWING_IN,
    leaf_thickness_mm: int = DEFAULT_LEAF_THICKNESS_MM,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """Two leaves meeting at the centre, each hinged on its own jamb.

    ``leaf_width_mm`` is the **whole** opening, as a door schedule quotes it. An odd
    opening splits with the extra millimetre on the right leaf rather than being rounded
    twice, so the two arcs' radii still add up to the opening exactly. No ``hand``: a
    pair of leaves is symmetric, and inventing an "active leaf" would put a distinction
    on the drawing that the model does not carry.
    """
    require_positive("leaf_width_mm", leaf_width_mm)
    require_positive("wall_thickness_mm", wall_thickness_mm)
    require_positive("leaf_thickness_mm", leaf_thickness_mm)
    require_choice("swing", swing, SWINGS)
    if leaf_width_mm < 2:
        raise ValueError("a double door needs at least 2 mm of opening to split")

    x_lo, x_hi = span(leaf_width_mm)
    y_lo, y_hi = span(wall_thickness_mm)
    towards_y = 1 if swing == SWING_IN else -1
    face_y = y_hi if swing == SWING_IN else y_lo
    x_mid = x_lo + leaf_width_mm // 2

    local = (
        *_jambs(x_lo, x_hi, y_lo, y_hi),
        *_leaf_and_swing(
            hinge=(x_lo, face_y),
            leaf_width_mm=x_mid - x_lo,
            leaf_thickness_mm=leaf_thickness_mm,
            towards_x=1,
            towards_y=towards_y,
        ),
        *_leaf_and_swing(
            hinge=(x_hi, face_y),
            leaf_width_mm=x_hi - x_mid,
            leaf_thickness_mm=leaf_thickness_mm,
            towards_x=-1,
            towards_y=towards_y,
        ),
    )
    return place(local, insertion, element_id)


def door_sliding(
    *,
    leaf_width_mm: int,
    wall_thickness_mm: int,
    element_id: str,
    hand: str = HAND_LEFT,
    swing: str = SWING_IN,
    leaf_thickness_mm: int = DEFAULT_LEAF_THICKNESS_MM,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """A leaf on a track, drawn closed, with the track it parks along.

    ``hand`` is the side the leaf slides **to**: ``"left"`` parks it beyond the ``−X``
    jamb. ``swing`` keeps its meaning as the face the leaf runs on — a sliding door has
    no swing, but which side of the wall the track is fixed to is exactly as real, and
    reusing the argument keeps every door in this module callable the same way.

    The track is dashed and twice the leaf width long, because that is what the symbol
    is *for*: it shows the wall the leaf needs clear behind it, which is the thing a
    reviewer checks and the thing a client discovers too late otherwise.
    """
    require_positive("leaf_width_mm", leaf_width_mm)
    require_positive("wall_thickness_mm", wall_thickness_mm)
    require_positive("leaf_thickness_mm", leaf_thickness_mm)
    require_choice("hand", hand, HANDS)
    require_choice("swing", swing, SWINGS)

    x_lo, x_hi = span(leaf_width_mm)
    y_lo, y_hi = span(wall_thickness_mm)
    towards_y = 1 if swing == SWING_IN else -1
    face_y = y_hi if swing == SWING_IN else y_lo
    outer_y = face_y + towards_y * leaf_thickness_mm
    track_y = face_y + towards_y * (leaf_thickness_mm // 2)
    park = -1 if hand == HAND_LEFT else 1

    track_far = (x_lo - leaf_width_mm) if park < 0 else (x_hi + leaf_width_mm)
    shaft_from = (x_lo + x_hi) // 2
    shaft_to = shaft_from + park * (leaf_width_mm // 2)

    local = (
        *_jambs(x_lo, x_hi, y_lo, y_hi),
        Polyline(
            vertices=((x_lo, face_y), (x_hi, face_y), (x_hi, outer_y), (x_lo, outer_y)),
            layer=A_DOOR,
            closed=True,
        ),
        Line(
            a=(track_far, track_y),
            b=(x_hi if park < 0 else x_lo, track_y),
            layer=A_DOOR,
            style=STYLE_DASHED,
        ),
        *arrow(
            (shaft_from, track_y),
            (shaft_to, track_y),
            barb_mm=max(1, leaf_width_mm // 10),
            layer=A_DOOR,
        ),
    )
    return place(local, insertion, element_id)


def door_folding(
    *,
    leaf_width_mm: int,
    wall_thickness_mm: int,
    element_id: str,
    panels: int = 2,
    hand: str = HAND_LEFT,
    swing: str = SWING_IN,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """A bi-fold: ``panels`` panels zig-zagged part-folded from the ``hand`` jamb.

    The panels partition the opening exactly — ``panel i`` spans
    ``w*(i+1)//panels - w*i//panels`` — so a 3-panel 1000 mm door is 334/333/333 and not
    three 333s that leave the drawing a millimetre short of its own schedule.
    """
    require_positive("leaf_width_mm", leaf_width_mm)
    require_positive("wall_thickness_mm", wall_thickness_mm)
    require_choice("hand", hand, HANDS)
    require_choice("swing", swing, SWINGS)
    if panels < 2:
        raise ValueError("a folding door has at least 2 panels, got %d" % panels)
    if leaf_width_mm < panels:
        raise ValueError(
            "%d panels cannot be drawn across a %d mm opening" % (panels, leaf_width_mm)
        )

    x_lo, x_hi = span(leaf_width_mm)
    y_lo, y_hi = span(wall_thickness_mm)
    towards_x = 1 if hand == HAND_LEFT else -1
    towards_y = 1 if swing == SWING_IN else -1
    face_y = y_hi if swing == SWING_IN else y_lo
    hinge_x = x_lo if hand == HAND_LEFT else x_hi

    theta = math.radians(FOLD_ANGLE_DEG)
    vertices: list[Pt2] = [(hinge_x, face_y)]
    x = hinge_x
    for index in range(panels):
        length = (leaf_width_mm * (index + 1)) // panels - (leaf_width_mm * index) // panels
        x += towards_x * round_half_away(length * math.cos(theta))
        out_mm = round_half_away(length * math.sin(theta))
        vertices.append((x, face_y + towards_y * out_mm if index % 2 == 0 else face_y))

    local = (
        *_jambs(x_lo, x_hi, y_lo, y_hi),
        Line(a=(x_lo, face_y), b=(x_hi, face_y), layer=A_DOOR, style=STYLE_DASHED),
        Polyline(vertices=tuple(vertices), layer=A_DOOR),
    )
    return place(local, insertion, element_id)
