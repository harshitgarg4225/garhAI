"""Electrical symbols in plan: switch, socket, light point, fan point, DB.

These are **notation**, not fabric. Their size is a property of the printed sheet — a
switch is drawn about 3 mm across whether the plan is at 1:50 or 1:200 — while the
primitives here carry model millimetres, so every block takes a ``size_mm`` and every
default is stated for **1:100**. Use
:func:`~services.drawings.blocks.base.paper_mm_to_model_mm` to size them at any other
scale; that is the only correct way to change them, and multiplying the default by hand
is how one symbol on a sheet ends up twice the size of the rest.

Local frame: the origin is where the symbol is fixed. For a switch, socket and DB that
is the point on the **wall**, with ``+Y`` into the room, so a caller rotates by the
wall's direction like any other block. For a light or fan point it is the position on
the ceiling projected into plan, and rotation only affects which way the blades read.

Layer: **A-TEXT**. An electrical symbol is a callout — the layer's own description is
"room names, notes and callouts" — and the §7 nine have no services layer. The precedent
is ``projection.symbols.north_arrow``, which puts its dart polyline on A-TEXT for the
same reason: adding a tenth layer would break every downstream consumer of the DXF.
"""

from __future__ import annotations

from services.drawings.blocks.base import (
    Insertion,
    arc_endpoint,
    label_text,
    place,
    require_positive,
    span,
)
from services.drawings.layers import A_TEXT
from services.drawings.render.primitives import (
    HATCH_SOLID,
    STYLE_DASHED,
    TEXT_HEIGHT_SMALL_PAPER_UM,
    Arc,
    Circle,
    Hatch,
    Line,
    Polyline,
    Primitive,
    Text,
)

__all__ = [
    "DB_DEPTH_MM",
    "DB_WIDTH_MM",
    "SYMBOL_SIZE_MM",
    "distribution_board",
    "fan_point",
    "light_point",
    "socket",
    "switch",
]

#: Symbol diameter in model mm **at 1:100** — 3 mm on paper, the size BS 3939 symbols
#: are conventionally plotted at.
SYMBOL_SIZE_MM = 300
#: A DB is drawn as a labelled box: 6 mm x 2 mm on paper at 1:100.
DB_WIDTH_MM = 600
DB_DEPTH_MM = 200

#: Degrees between the levers of a multi-gang switch, and the angle a single lever sits
#: at. Fanned rather than stacked so a 3-gang plate is countable on the sheet.
_LEVER_BASE_DEG = 45.0
_LEVER_SPREAD_DEG = 25.0


def switch(
    *,
    element_id: str,
    gang: int = 1,
    two_way: bool = False,
    size_mm: int = SYMBOL_SIZE_MM,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """A switch: the body dot plus one lever per gang, and a bar for a two-way."""
    require_positive("size_mm", size_mm)
    if gang < 1:
        raise ValueError("a switch has at least one gang, got %d" % gang)
    radius = size_mm // 2
    body = max(1, radius // 3)
    local: list[Primitive] = [Circle(centre=(0, 0), radius_mm=body, layer=A_TEXT)]
    for index in range(gang):
        angle = _LEVER_BASE_DEG + (index - (gang - 1) / 2.0) * _LEVER_SPREAD_DEG
        tip = arc_endpoint((0, 0), radius, angle)
        local.append(Line(a=arc_endpoint((0, 0), body, angle), b=tip, layer=A_TEXT))
        if two_way:
            # The second contact: a short bar across the lever tip, which is how a
            # two-way is told from a one-way on a plan at a glance.
            bar = max(1, radius // 4)
            local.append(
                Line(
                    a=arc_endpoint(tip, bar, angle + 90),
                    b=arc_endpoint(tip, bar, angle - 90),
                    layer=A_TEXT,
                )
            )
    return place(tuple(local), insertion, element_id)


def socket(
    *,
    element_id: str,
    size_mm: int = SYMBOL_SIZE_MM,
    label: str | None = None,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """A socket outlet: the flat side on the wall, the semicircle in the room.

    ``label`` prints the rating beside it ("6A", "16A", "AC 20A") when the caller has
    one. It is bounded by :func:`~services.drawings.blocks.base.label_text` because it
    reaches the sheet verbatim.
    """
    require_positive("size_mm", size_mm)
    radius = size_mm // 2
    local: list[Primitive] = [
        Line(a=(-radius, 0), b=(radius, 0), layer=A_TEXT),
        Arc(centre=(0, 0), radius_mm=radius, start_deg=0, end_deg=180, layer=A_TEXT),
        # The stem runs back into the wall: it is the circuit, and it is what tells a
        # socket from a light point when both are printed at 3 mm.
        Line(a=(0, 0), b=(0, -radius), layer=A_TEXT),
    ]
    if label:
        local.append(
            Text(
                at=(0, radius + max(1, radius // 2)),
                text=label_text(label),
                layer=A_TEXT,
                height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM,
                anchor="middle",
                baseline="middle",
            )
        )
    return place(tuple(local), insertion, element_id)


def light_point(
    *,
    element_id: str,
    size_mm: int = SYMBOL_SIZE_MM,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """A light point: a circle with the diagonal cross through it."""
    require_positive("size_mm", size_mm)
    radius = size_mm // 2
    arm = max(1, radius // 2)
    local = (
        Circle(centre=(0, 0), radius_mm=arm, layer=A_TEXT),
        Line(a=arc_endpoint((0, 0), radius, 45), b=arc_endpoint((0, 0), radius, 225), layer=A_TEXT),
        Line(
            a=arc_endpoint((0, 0), radius, 135), b=arc_endpoint((0, 0), radius, 315), layer=A_TEXT
        ),
    )
    return place(local, insertion, element_id)


def fan_point(
    *,
    element_id: str,
    blades: int = 3,
    size_mm: int = SYMBOL_SIZE_MM,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """A ceiling fan point: hub, blades, and the swept circle drawn dashed.

    The sweep is dashed because it is not an object — it is the clearance the fan needs,
    and drawing it solid puts a 1200 mm circle on the plan that a reviewer will read as
    a built thing.
    """
    require_positive("size_mm", size_mm)
    if blades < 2:
        raise ValueError("a fan symbol needs at least 2 blades, got %d" % blades)
    radius = size_mm // 2
    hub = max(1, radius // 4)
    local: list[Primitive] = [
        Circle(centre=(0, 0), radius_mm=hub, layer=A_TEXT),
        Circle(centre=(0, 0), radius_mm=radius, layer=A_TEXT, style=STYLE_DASHED),
    ]
    for index in range(blades):
        angle = 90.0 + index * (360.0 / blades)
        local.append(
            Line(
                a=arc_endpoint((0, 0), hub, angle),
                b=arc_endpoint((0, 0), radius, angle),
                layer=A_TEXT,
            )
        )
    return place(tuple(local), insertion, element_id)


def distribution_board(
    *,
    element_id: str,
    width_mm: int = DB_WIDTH_MM,
    depth_mm: int = DB_DEPTH_MM,
    label: str = "DB",
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """A distribution board: the box, its half-filled end, and its name."""
    require_positive("width_mm", width_mm)
    require_positive("depth_mm", depth_mm)
    x_lo, x_hi = span(width_mm)
    x_mid = x_lo + width_mm // 2
    local = (
        Polyline(
            vertices=((x_lo, 0), (x_hi, 0), (x_hi, depth_mm), (x_lo, depth_mm)),
            layer=A_TEXT,
            closed=True,
        ),
        Hatch(
            outline=((x_lo, 0), (x_mid, 0), (x_mid, depth_mm), (x_lo, depth_mm)),
            layer=A_TEXT,
            pattern=HATCH_SOLID,
        ),
        Text(
            at=((x_lo + x_hi) // 2, depth_mm + max(1, depth_mm // 2)),
            text=label_text(label),
            layer=A_TEXT,
            height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM,
            anchor="middle",
            baseline="middle",
        ),
    )
    return place(local, insertion, element_id)
