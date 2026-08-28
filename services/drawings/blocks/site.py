"""Site symbols: north arrow, tree, parked car, scale bar.

Two of these are notation and two are objects, and they are on different layers for that
reason:

* the **north arrow** and the **scale bar** are properties of the sheet. The arrow goes
  on A-TEXT (matching ``projection.symbols.north_arrow``, which a site plan already
  carries) and the bar on A-DIM, which is where every measuring construct lives —
  ``projection.symbols.column_bubbles`` documents the same choice for grid lines.
* a **tree** and a **parked car** are things on the plot. They go on A-WALL-PART, the
  layer that already carries the compound wall and the plot boundary in
  ``render.reference_sheets``: site fabric that is not a full-height building wall.
  Putting them on A-TEXT would say a mango tree is a note.

Sizes. The arrow and the bar are **paper-sized**, so their defaults are stated for 1:100
and :func:`~services.drawings.blocks.base.paper_mm_to_model_mm` converts for any other
scale. A tree and a car are real objects and their defaults are real dimensions — the
car's from ``fixtures/catalog/furniture.json``.
"""

from __future__ import annotations

import math

from services.drawings.blocks.base import (
    Insertion,
    arc_endpoint,
    place,
    require_choice,
    require_int,
    require_positive,
    round_half_away,
    span,
)
from services.drawings.layers import A_DIM, A_TEXT, A_WALL_PART
from services.drawings.render.primitives import (
    HATCH_SOLID,
    TEXT_HEIGHT_SMALL_PAPER_UM,
    Circle,
    Hatch,
    Line,
    Polyline,
    Primitive,
    Pt2,
    Text,
)

__all__ = [
    "CAR_LENGTH_MM",
    "CAR_WIDTH_MM",
    "CATALOGUE_SOURCE",
    "CLOUD_LOBES",
    "NORTH_ARROW_LENGTH_MM",
    "SPIKY_POINTS",
    "TREE_STYLES",
    "north_arrow",
    "parked_car",
    "scale_bar",
    "tree",
]

#: Tip-to-origin length of the north dart in model mm **at 1:100** (12 mm on paper).
NORTH_ARROW_LENGTH_MM = 1_200

#: Catalogue entry the car's default size is taken from; the test re-reads it.
CATALOGUE_SOURCE: dict[str, str] = {"parked_car": "car-sedan"}
CAR_WIDTH_MM = 1_800
CAR_LENGTH_MM = 4_800

TREE_STYLES: tuple[str, ...] = ("circle", "cloud", "spiky")
#: Lobes in the cloud canopy. Eight, and starting at 0°, so a lobe sits on each of the
#: four cardinals and the canopy's drawn extent is exactly its stated diameter.
CLOUD_LOBES = 8
#: Points on the spiky (conifer) canopy.
SPIKY_POINTS = 6


def north_arrow(
    *,
    element_id: str,
    north_deg: int = 0,
    length_mm: int = NORTH_ARROW_LENGTH_MM,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """The north dart and its "N", pointing at the plot's true north.

    ``north_deg`` is ``PlotDoc.north_deg``: the rotation of TRUE north from ``+Y``,
    measured **clockwise** (§3). The dart is built pointing ``+Y`` and the bearing is
    applied as a ``−north_deg`` CCW turn composed into the caller's own rotation, so the
    tip lands at ``length × (sin θ, cos θ)``. The other reading — ``(cos θ, sin θ)`` —
    draws an arrow pointing east on a plot that points north, and every other thing on
    the sheet is right, so nobody catches it.
    """
    require_positive("length_mm", length_mm)
    require_int("north_deg", north_deg)
    half_width = max(1, length_mm // 4)
    tail = max(1, length_mm // 4)
    gap = max(1, length_mm // 6)
    local = (
        Polyline(
            vertices=((0, length_mm), (half_width, 0), (0, -tail), (-half_width, 0)),
            layer=A_TEXT,
            closed=True,
        ),
        Text(
            at=(0, length_mm + gap),
            text="N",
            layer=A_TEXT,
            height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM,
            anchor="middle",
            baseline="middle",
        ),
    )
    return place(local, insertion.rotated(-north_deg), element_id)


def tree(
    *,
    element_id: str,
    canopy_radius_mm: int = 1_500,
    style: str = "cloud",
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """A tree in plan at its real canopy radius, in one of three drawing styles.

    The canopy is drawn at the radius given, not at a symbol size: on a site plan the
    canopy is what shades the setback and what the neighbour objects to, so it is
    measured. ``style`` is presentation only — ``"circle"`` for a survey drawing,
    ``"cloud"`` for a landscape layout, ``"spiky"`` for a conifer.
    """
    require_positive("canopy_radius_mm", canopy_radius_mm)
    require_choice("style", style, TREE_STYLES)
    trunk = max(1, canopy_radius_mm // 10)
    local: list[Primitive] = [Circle(centre=(0, 0), radius_mm=trunk, layer=A_WALL_PART)]

    if style == "circle":
        local.append(Circle(centre=(0, 0), radius_mm=canopy_radius_mm, layer=A_WALL_PART))
    elif style == "cloud":
        # Lobes of radius L centred on a circle of radius R−L: adjacent lobes touch when
        # L = R·sin(π/n)/(1+sin(π/n)), which is what makes the outline read as a canopy
        # rather than as a ring of separate bushes.
        share = math.sin(math.pi / CLOUD_LOBES)
        lobe = max(1, round_half_away(canopy_radius_mm * share / (1.0 + share)))
        ring = canopy_radius_mm - lobe
        if ring <= 0:
            raise ValueError(
                "a %d mm canopy is too small for a cloud outline; use style='circle'"
                % canopy_radius_mm
            )
        for index in range(CLOUD_LOBES):
            local.append(
                Circle(
                    centre=arc_endpoint((0, 0), ring, index * (360.0 / CLOUD_LOBES)),
                    radius_mm=lobe,
                    layer=A_WALL_PART,
                )
            )
    else:
        inner = max(1, canopy_radius_mm * 3 // 5)
        step = 180.0 / SPIKY_POINTS
        vertices: list[Pt2] = []
        for index in range(SPIKY_POINTS * 2):
            radius = canopy_radius_mm if index % 2 == 0 else inner
            vertices.append(arc_endpoint((0, 0), radius, 90.0 + index * step))
        local.append(Polyline(vertices=tuple(vertices), layer=A_WALL_PART, closed=True))
    return place(tuple(local), insertion, element_id)


def parked_car(
    *,
    element_id: str,
    width_mm: int = CAR_WIDTH_MM,
    length_mm: int = CAR_LENGTH_MM,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """A car in plan, centred on its own footprint with ``+Y`` towards the nose.

    Drawn at the vehicle's real size so a stilt parking that measures 2.4 m clear on the
    drawing is seen to be 2.4 m clear. The nose is chamfered harder than the tail, which
    is the whole reason the symbol is not just a rectangle: it says which way the car
    faces, and therefore whether the driveway works.
    """
    require_positive("width_mm", width_mm)
    require_positive("length_mm", length_mm)
    x_lo, x_hi = span(width_mm)
    y_lo, y_hi = span(length_mm)
    tail_chamfer = max(1, width_mm // 5)
    nose_chamfer = max(1, width_mm // 3)
    cabin_inset = max(1, width_mm // 8)
    cabin_from = y_lo + length_mm // 5
    cabin_to = y_hi - (2 * length_mm) // 5
    if cabin_to <= cabin_from or nose_chamfer * 2 >= width_mm or tail_chamfer * 2 >= width_mm:
        raise ValueError("a %d x %d mm vehicle is too small to draw" % (width_mm, length_mm))
    local = (
        Polyline(
            vertices=(
                (x_lo, y_lo + tail_chamfer),
                (x_lo + tail_chamfer, y_lo),
                (x_hi - tail_chamfer, y_lo),
                (x_hi, y_lo + tail_chamfer),
                (x_hi, y_hi - nose_chamfer),
                (x_hi - nose_chamfer, y_hi),
                (x_lo + nose_chamfer, y_hi),
                (x_lo, y_hi - nose_chamfer),
            ),
            layer=A_WALL_PART,
            closed=True,
        ),
        Polyline(
            vertices=(
                (x_lo + cabin_inset, cabin_from),
                (x_hi - cabin_inset, cabin_from),
                (x_hi - cabin_inset, cabin_to),
                (x_lo + cabin_inset, cabin_to),
            ),
            layer=A_WALL_PART,
            closed=True,
        ),
    )
    return place(local, insertion, element_id)


def _metre_label(mm: int) -> str:
    """A scale-bar tick label in metres: "5", or "2.5" when the division is not whole."""
    if mm % 1_000 == 0:
        return "%d" % (mm // 1_000)
    tenths = round_half_away(mm / 100.0)
    return "%d.%d" % (tenths // 10, tenths % 10)


def scale_bar(
    *,
    element_id: str,
    division_mm: int = 1_000,
    divisions: int = 5,
    bar_height_mm: int = 300,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """The alternating bar a reviewer measures a photocopied sheet with.

    Origin at the bar's left end, on its base line. ``division_mm`` is in **model**
    millimetres — a 1 m division of the building — so the bar is drawn at the same scale
    as the plan beside it and stays true through any reduction, which is the entire
    point of printing one.
    """
    require_positive("division_mm", division_mm)
    require_positive("bar_height_mm", bar_height_mm)
    if divisions < 1:
        raise ValueError("a scale bar needs at least one division, got %d" % divisions)
    total = division_mm * divisions
    local: list[Primitive] = [
        Polyline(
            vertices=((0, 0), (total, 0), (total, bar_height_mm), (0, bar_height_mm)),
            layer=A_DIM,
            closed=True,
        )
    ]
    for index in range(divisions):
        start = index * division_mm
        # Odd cells are filled; the alternation is what makes a half-division readable
        # by eye on a bar that has been photocopied twice.
        if index % 2 == 1:
            local.append(
                Hatch(
                    outline=(
                        (start, 0),
                        (start + division_mm, 0),
                        (start + division_mm, bar_height_mm),
                        (start, bar_height_mm),
                    ),
                    layer=A_DIM,
                    pattern=HATCH_SOLID,
                )
            )
        if index > 0:
            local.append(Line(a=(start, 0), b=(start, bar_height_mm), layer=A_DIM))

    label_y = -max(1, bar_height_mm // 2)
    for index in range(divisions + 1):
        value = index * division_mm
        text = _metre_label(value)
        if index == divisions:
            text = "%s m" % text
        local.append(
            Text(
                at=(value, label_y),
                text=text,
                layer=A_TEXT,
                height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM,
                anchor="middle",
                baseline="middle",
            )
        )
    return place(tuple(local), insertion, element_id)
