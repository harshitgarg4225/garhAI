"""Sanitary fixtures in plan: WC, washbasin, shower, bathtub, sink.

Local frame (before :func:`~services.drawings.blocks.base.place`): the origin is the
**centre of the fixture's back edge, against the wall it is fixed to**. ``+X`` runs
along that wall and ``+Y`` projects into the room, so a fixture occupies
``span(width_mm) × [0, depth_mm]`` and a caller places it by rotating the wall's
direction, not by working out a corner.

Every default here is a real catalogue dimension, cited by id from
``fixtures/catalog/furniture.json``, and ``test_blocks.py`` re-reads that file and fails
if the two drift. A symbol drawn 550 mm wide for a basin the schedule calls 650 is worse
than no symbol: it is a clearance check that passes on the drawing and fails on site.

Layer: **A-WALL-PART**. There is no A-FURN among the nine §7 layers, and adding a tenth
would break every downstream consumer of the DXF for one symbol's sake (the same
decision ``projection.symbols.column_bubbles`` documents for grid bubbles). Of the nine,
A-WALL-PART — "partial-height walls, parapets, sills" — is the one that means "built
fabric that is not a full-height wall", which is exactly what a WC pan is. It is
emphatically not A-WALL: a reviewer measuring the setback off A-WALL must not pick up a
bathtub.
"""

from __future__ import annotations

from services.drawings.blocks.base import (
    Insertion,
    place,
    require_positive,
    span,
)
from services.drawings.layers import A_WALL_PART
from services.drawings.render.primitives import (
    Arc,
    Circle,
    Line,
    Polyline,
    Primitive,
    Pt2,
)

__all__ = [
    "BATHTUB_DEPTH_MM",
    "BATHTUB_WIDTH_MM",
    "CATALOGUE_SOURCE",
    "SHOWER_DEPTH_MM",
    "SHOWER_WIDTH_MM",
    "SINK_DEPTH_MM",
    "SINK_WIDTH_MM",
    "WASHBASIN_DEPTH_MM",
    "WASHBASIN_WIDTH_MM",
    "WC_DEPTH_MM",
    "WC_WIDTH_MM",
    "bathtub",
    "shower",
    "sink",
    "washbasin",
    "wc",
]

#: Which catalogue entry each default is taken from. The test asserts these ids still
#: carry these dimensions, so the citation is checked rather than decorative.
CATALOGUE_SOURCE: dict[str, str] = {
    "wc": "wc-floor-mounted-s-trap",
    "washbasin": "washbasin-pedestal-550",
    "shower": "shower-enclosure-900",
    "bathtub": "bathtub-1700",
    "sink": "kitchen-sink-ss-single",
}

WC_WIDTH_MM = 380
WC_DEPTH_MM = 680
WASHBASIN_WIDTH_MM = 550
WASHBASIN_DEPTH_MM = 450
SHOWER_WIDTH_MM = 900
SHOWER_DEPTH_MM = 900
BATHTUB_WIDTH_MM = 1700
BATHTUB_DEPTH_MM = 800
SINK_WIDTH_MM = 600
SINK_DEPTH_MM = 450


def _rect(x0: int, y0: int, x1: int, y1: int) -> Polyline:
    return Polyline(
        vertices=((x0, y0), (x1, y0), (x1, y1), (x0, y1)),
        layer=A_WALL_PART,
        closed=True,
    )


def _rounded_end(
    *, half_width_mm: int, from_y: int, to_y: int, centre_x: int = 0
) -> tuple[Primitive, ...]:
    """Two parallel sides closed by a semicircular nose at ``to_y``.

    The shape a WC pan, a washbasin and a tub end all are. The nose is a real
    :class:`Arc` rather than a polygon of chords: at 1:50 a twelve-segment approximation
    of a 275 mm radius is visibly faceted, and the DXF hands a CAD user a polyline where
    they expect an arc to snap to.
    """
    nose_y = to_y - half_width_mm
    if nose_y <= from_y:
        raise ValueError(
            "a fixture %d mm across cannot be rounded off within %d mm of depth"
            % (half_width_mm * 2, to_y - from_y)
        )
    return (
        Line(
            a=(centre_x - half_width_mm, from_y),
            b=(centre_x - half_width_mm, nose_y),
            layer=A_WALL_PART,
        ),
        Line(
            a=(centre_x + half_width_mm, from_y),
            b=(centre_x + half_width_mm, nose_y),
            layer=A_WALL_PART,
        ),
        Arc(
            centre=(centre_x, nose_y),
            radius_mm=half_width_mm,
            start_deg=0,
            end_deg=180,
            layer=A_WALL_PART,
        ),
    )


def wc(
    *,
    element_id: str,
    width_mm: int = WC_WIDTH_MM,
    depth_mm: int = WC_DEPTH_MM,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """Cistern against the wall, pan projecting into the room with a rounded nose."""
    require_positive("width_mm", width_mm)
    require_positive("depth_mm", depth_mm)
    x_lo, x_hi = span(width_mm)
    # A cistern is about a quarter of the fixture's projection — 170 mm of a 680 mm
    # S-trap WC, which is what a low-level flush tank measures.
    cistern_depth = max(1, depth_mm // 4)
    # The pan is narrower than the cistern — 90% of it — and is set out from its own
    # half-width so the nose arc stays symmetric about the fixture's centre line.
    pan_half = width_mm * 9 // 20
    local = (
        _rect(x_lo, 0, x_hi, cistern_depth),
        *_rounded_end(half_width_mm=pan_half, from_y=cistern_depth, to_y=depth_mm),
    )
    return place(local, insertion, element_id)


def washbasin(
    *,
    element_id: str,
    width_mm: int = WASHBASIN_WIDTH_MM,
    depth_mm: int = WASHBASIN_DEPTH_MM,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """Rim against the wall, bowl inside it, waste at the bowl's centre.

    The rim's straight back is drawn at the fixture's nominal width, while the curved
    part is set out on ``width_mm // 2`` — an odd width therefore leaves a ≤ 1 mm jog
    where they meet, which is a tenth of a plotted line's width at 1:50 and keeps the
    fixture's stated size exact, which matters more.
    """
    require_positive("width_mm", width_mm)
    require_positive("depth_mm", depth_mm)
    x_lo, x_hi = span(width_mm)
    half = width_mm // 2
    rim = max(1, width_mm // 11)
    bowl_half = half - rim
    if bowl_half <= 0:
        raise ValueError("a %d mm basin is too narrow to draw a bowl inside its rim" % width_mm)
    local = (
        Line(a=(x_lo, 0), b=(x_hi, 0), layer=A_WALL_PART),
        *_rounded_end(half_width_mm=half, from_y=0, to_y=depth_mm),
        *_rounded_end(half_width_mm=bowl_half, from_y=rim, to_y=depth_mm - rim),
        Circle(
            centre=(0, depth_mm - rim - bowl_half),
            radius_mm=max(1, width_mm // 20),
            layer=A_WALL_PART,
        ),
    )
    return place(local, insertion, element_id)


def shower(
    *,
    element_id: str,
    width_mm: int = SHOWER_WIDTH_MM,
    depth_mm: int = SHOWER_DEPTH_MM,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """Tray, the two diagonals that say "shower", and the drain where they cross."""
    require_positive("width_mm", width_mm)
    require_positive("depth_mm", depth_mm)
    x_lo, x_hi = span(width_mm)
    centre: Pt2 = ((x_lo + x_hi) // 2, depth_mm // 2)
    local = (
        _rect(x_lo, 0, x_hi, depth_mm),
        Line(a=(x_lo, 0), b=(x_hi, depth_mm), layer=A_WALL_PART),
        Line(a=(x_lo, depth_mm), b=(x_hi, 0), layer=A_WALL_PART),
        Circle(
            centre=centre,
            radius_mm=max(1, min(width_mm, depth_mm) // 18),
            layer=A_WALL_PART,
        ),
    )
    return place(local, insertion, element_id)


def bathtub(
    *,
    element_id: str,
    width_mm: int = BATHTUB_WIDTH_MM,
    depth_mm: int = BATHTUB_DEPTH_MM,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """Outer rim at the tub's stated size, the well inside it, waste at the tap end.

    ``width_mm`` runs **along the wall** — 1700 for a standard tub — and ``depth_mm`` is
    what it projects, which is the axis a bathroom's clear width is checked against.
    """
    require_positive("width_mm", width_mm)
    require_positive("depth_mm", depth_mm)
    x_lo, x_hi = span(width_mm)
    rim = max(1, depth_mm // 12)
    inner_lo, inner_hi = x_lo + rim, x_hi - rim
    if inner_hi - inner_lo < 4 or depth_mm - 2 * rim < 4:
        raise ValueError("a %d x %d mm tub is too small to draw a well in" % (width_mm, depth_mm))
    chamfer = min((inner_hi - inner_lo) // 4, (depth_mm - 2 * rim) // 4)
    top = depth_mm - rim
    local = (
        _rect(x_lo, 0, x_hi, depth_mm),
        Polyline(
            vertices=(
                (inner_lo, rim + chamfer),
                (inner_lo + chamfer, rim),
                (inner_hi - chamfer, rim),
                (inner_hi, rim + chamfer),
                (inner_hi, top - chamfer),
                (inner_hi - chamfer, top),
                (inner_lo + chamfer, top),
                (inner_lo, top - chamfer),
            ),
            layer=A_WALL_PART,
            closed=True,
        ),
        Circle(
            centre=(inner_hi - (depth_mm // 2), depth_mm // 2),
            radius_mm=max(1, depth_mm // 20),
            layer=A_WALL_PART,
        ),
    )
    return place(local, insertion, element_id)


def sink(
    *,
    element_id: str,
    width_mm: int = SINK_WIDTH_MM,
    depth_mm: int = SINK_DEPTH_MM,
    bowls: int = 1,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """A kitchen or utility sink: the outer tray and one well per bowl, each with a waste."""
    require_positive("width_mm", width_mm)
    require_positive("depth_mm", depth_mm)
    if bowls < 1:
        raise ValueError("a sink has at least one bowl, got %d" % bowls)
    x_lo, x_hi = span(width_mm)
    rim = max(1, depth_mm // 9)
    local: list[Primitive] = [_rect(x_lo, 0, x_hi, depth_mm)]
    for index in range(bowls):
        bowl_lo = x_lo + (width_mm * index) // bowls + rim
        bowl_hi = x_lo + (width_mm * (index + 1)) // bowls - rim
        if bowl_hi - bowl_lo < 2 or depth_mm - 2 * rim < 2:
            raise ValueError("%d bowls do not fit a %d x %d mm sink" % (bowls, width_mm, depth_mm))
        local.append(_rect(bowl_lo, rim, bowl_hi, depth_mm - rim))
        local.append(
            Circle(
                centre=((bowl_lo + bowl_hi) // 2, depth_mm // 2),
                radius_mm=max(1, depth_mm // 12),
                layer=A_WALL_PART,
            )
        )
    return place(tuple(local), insertion, element_id)
