"""The drawn symbols of a plan: north, stairs, levels, sections, grids, labels. **Real.**

§7's plan projection asks for these by name:

    ... stairs w/ arrow + ``UP 15R``, room label block (name, area in sqft one decimal),
    FFL markers, section markers, north arrow, grid of column bubbles if columns exist.

Every function here is pure, integer-mm, and takes its sizes from
:class:`services.drawings.projection.style.Style` — never from a literal — because every
one of these symbols is sized in **paper** millimetres and must come out the same
physical size at 1:50 as at 1:100.

Two rules this module keeps
---------------------------
**Nothing is invented.** The riser count in "UP 18R" is ``stair.risers_count``; the room
area in a label is ``room.area_mm2``; the north angle is ``plot.north_deg``. A drawing is
a *view* of the model, and a number that appears only on the sheet is a number nobody
can trace, defend to a reviewer, or keep in step with a compliance result. Where the
model genuinely does not contain the geometry — the turn of a dogleg stair, which is
stored as a bounding footprint and not as a path — the symbol stops rather than guessing.

**Geometry comes from the model core where the model core has it.** The stair footprint
is ``garh_model.fold.stair_footprint_polygon``, the same function that cuts the slab void
for that stair. If the drawing computed its own, a stair could sit over a hole in one
view and beside it in the other.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.drawings.layers import (
    A_AREA,
    A_DIM,
    A_STAIR,
    A_TEXT,
    A_WALL_PART,
)
from services.drawings.projection.primitives import (
    K_BALCONY,
    K_BALCONY_RAILING,
    K_COLUMN,
    K_COLUMN_HATCH,
    K_GRID_BUBBLE,
    K_GRID_LABEL,
    K_GRID_LINE,
    K_LEVEL_LABEL,
    K_LEVEL_MARKER,
    K_NORTH_ARROW,
    K_NORTH_LABEL,
    K_OPENING_TAG,
    K_ROOM_AREA,
    K_ROOM_NAME,
    K_ROOM_OUTLINE,
    K_SECTION_FLAG,
    K_SECTION_LABEL,
    K_SECTION_LINE,
    K_STAIR_ARROW,
    K_STAIR_LABEL,
    K_STAIR_OUTLINE,
    K_STAIR_TREAD,
    PATTERN_CONCRETE,
    Arc,
    Hatch,
    Line,
    Point,
    Polyline,
    Primitive,
    Text,
    point,
    point_of,
    point_round,
    round_half_away,
    sanitise_text,
)
from services.drawings.projection.style import Style
from services.drawings.projection.walls import WallBand, opening_span

#: Forward and right unit vectors per direction of travel.
#:
#: MIRROR of ``garh_model.fold._STAIR_VECTORS`` (right is 90° clockwise of forward), and
#: ``tests/test_projection.py`` asserts this table reproduces
#: ``stair_footprint_polygon``'s corners for all four directions — so the drawing and the
#: slab cut-out can never disagree about which way a stair runs.
STAIR_VECTORS: Dict[str, Tuple[int, int, int, int]] = {
    "N": (0, 1, 1, 0),
    "E": (1, 0, 0, -1),
    "S": (0, -1, -1, 0),
    "W": (-1, 0, 0, 1),
}


# ---------------------------------------------------------------------------
# North arrow
# ---------------------------------------------------------------------------
def north_arrow(centre: Point, north_deg: int, style: Style) -> Tuple[Primitive, ...]:
    """A north dart rotated by the plot's ``north_deg``, plus its "N".

    ``PlotDoc.north_deg`` is "rotation of TRUE north from +Y, measured **clockwise**"
    (§3), so the north unit vector is ``(sin θ, cos θ)`` — not ``(cos θ, sin θ)``. The
    difference is a drawing that points east when the plot points north, which is the
    kind of error that survives review because everything else on the sheet is right.

    The dart is a closed polyline: tip, right wing, tail notch, left wing.
    """
    theta = math.radians(north_deg)
    nx, ny = math.sin(theta), math.cos(theta)
    # Right-hand side of north = north rotated 90° clockwise.
    rx, ry = ny, -nx
    length = style.north_arrow_length_mm
    half_width = style.north_arrow_half_width_mm
    tail = style.north_arrow_tail_mm
    cx, cy = centre

    def at(forward: float, side: float) -> Point:
        return point_round(cx + nx * forward + rx * side, cy + ny * forward + ry * side)

    return (
        Polyline(
            layer=A_TEXT,
            points=(at(length, 0), at(0, half_width), at(-tail, 0), at(0, -half_width)),
            closed=True,
            kind=K_NORTH_ARROW,
        ),
        Text(
            layer=A_TEXT,
            position=at(length + style.north_label_gap_mm + style.north_text_height_mm // 2, 0),
            text="N",
            height_mm=style.north_text_height_mm,
            kind=K_NORTH_LABEL,
        ),
    )


# ---------------------------------------------------------------------------
# Stairs
# ---------------------------------------------------------------------------
def stair_symbol(stair: Any, style: Style) -> Tuple[Primitive, ...]:
    """Footprint, treads, UP arrow and the "UP 18R" label.

    Treads are drawn only for a ``straight`` flight. For ``dogleg``/``L``/``U`` the model
    stores one origin, one direction and a landing block — a bounding footprint, not a
    path — so the outline, the arrow and the label are everything that is genuinely
    known. Drawing an invented turn would put a shape on a municipal submission that the
    model does not contain, and somebody would build it.
    """
    from garh_model.fold import stair_footprint_polygon

    footprint = [point_of(p) for p in stair_footprint_polygon(stair)]
    if len(footprint) < 3:
        return ()

    fx, fy, rx, ry = STAIR_VECTORS[stair.direction]
    going_mm = max(1, stair.risers_count - 1) * stair.tread_mm
    width_mm = stair.width_mm
    ox, oy = stair.origin.x, stair.origin.y

    def at(along_mm: float, across_mm: float) -> Point:
        return point_round(ox + fx * along_mm + rx * across_mm, oy + fy * along_mm + ry * across_mm)

    out: List[Primitive] = [
        Polyline(
            layer=A_STAIR,
            points=tuple(footprint),
            closed=True,
            owner_id=stair.id,
            kind=K_STAIR_OUTLINE,
        )
    ]

    if stair.kind == "straight":
        index = 1
        while index * stair.tread_mm < going_mm:
            along = index * stair.tread_mm
            out.append(
                Line(
                    layer=A_STAIR,
                    a=at(along, 0),
                    b=at(along, width_mm),
                    owner_id=stair.id,
                    kind=K_STAIR_TREAD,
                )
            )
            index += 1

    # -- the UP arrow, on the flight's centre line ------------------------
    tail_mm = min(stair.tread_mm // 2, going_mm // 4)
    head_mm = max(tail_mm + stair.tread_mm, going_mm - stair.tread_mm // 2)
    centre_across = width_mm // 2
    tail_pt = at(tail_mm, centre_across)
    head_pt = at(head_mm, centre_across)
    barb = style.stair_arrow_head_mm
    out.extend(
        (
            Line(layer=A_STAIR, a=tail_pt, b=head_pt, owner_id=stair.id, kind=K_STAIR_ARROW),
            Line(
                layer=A_STAIR,
                a=head_pt,
                b=at(head_mm - barb, centre_across + barb // 2),
                owner_id=stair.id,
                kind=K_STAIR_ARROW,
            ),
            Line(
                layer=A_STAIR,
                a=head_pt,
                b=at(head_mm - barb, centre_across - barb // 2),
                owner_id=stair.id,
                kind=K_STAIR_ARROW,
            ),
        )
    )

    # -- "UP 18R", reading along the flight ------------------------------
    label_across = centre_across + style.stair_label_gap_mm + style.label_height_mm // 2
    out.append(
        Text(
            layer=A_TEXT,
            position=at((tail_mm + head_mm) // 2, label_across),
            text="UP %dR" % stair.risers_count,
            height_mm=style.label_height_mm,
            rotation_deg=readable_rotation(round_half_away(math.degrees(math.atan2(fy, fx)))),
            owner_id=stair.id,
            kind=K_STAIR_LABEL,
        )
    )
    return tuple(out)


def readable_rotation(deg: int) -> int:
    """Flip text that would print upside down. 0–90 and 271–360 read left-to-right."""
    normalised = deg % 360
    if 90 < normalised <= 270:
        return (normalised + 180) % 360
    return normalised


# ---------------------------------------------------------------------------
# Level (FFL) markers
# ---------------------------------------------------------------------------
def level_marker(
    position: Point,
    level_mm: int,
    style: Style,
    *,
    prefix: str = "FFL",
    owner_id: Optional[str] = None,
) -> Tuple[Primitive, ...]:
    """A level triangle plus its text — ``FFL +600``, in millimetres per §7.

    §7: "All dim text in mm on drawings regardless of the project's display units."
    A level is a dimension, so it obeys that rule even on a project displaying ft-in,
    and it carries an explicit sign because a sunken court at −150 and a plinth at +150
    must not read the same.
    """
    size = style.level_marker_size_mm
    x, y = position
    return (
        Polyline(
            layer=A_DIM,
            points=(point(x - size, y + size), point(x + size, y + size), point(x, y)),
            closed=True,
            owner_id=owner_id,
            kind=K_LEVEL_MARKER,
        ),
        Text(
            layer=A_TEXT,
            position=point(x + size + style.level_label_gap_mm, y + size),
            text="%s %+d" % (prefix, level_mm),
            height_mm=style.label_height_mm,
            h_align="left",
            v_align="bottom",
            owner_id=owner_id,
            kind=K_LEVEL_LABEL,
        ),
    )


# ---------------------------------------------------------------------------
# Section markers
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SectionMarker:
    """Where a section is cut, and which way the viewer looks.

    ``a``/``b`` are the cut line's ends in model mm — the same
    ``Viewport.section_line`` the section sheet stores, so the marker on the plan and the
    sheet it refers to cannot drift apart. ``view_left`` picks the side the arrows point
    to (the left normal of ``a→b``), which is the direction the section is drawn looking.
    """

    a: Point
    b: Point
    label: str = "A"
    view_left: bool = True


def section_marker(marker: SectionMarker, style: Style) -> Tuple[Primitive, ...]:
    """The cut line, an arrow flag at each end, and the letter at both ends."""
    (ax, ay), (bx, by) = marker.a, marker.b
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length == 0:
        return ()
    ux, uy = dx / length, dy / length
    sign = 1 if marker.view_left else -1
    vx, vy = -uy * sign, ux * sign

    overshoot = style.section_overshoot_mm
    flag = style.section_flag_mm
    arrow = style.section_arrow_mm
    gap = style.section_label_gap_mm
    label = sanitise_text(marker.label, max_length=4)

    start = point_round(ax - ux * overshoot, ay - uy * overshoot)
    end = point_round(bx + ux * overshoot, by + uy * overshoot)
    out: List[Primitive] = [
        Line(layer=A_DIM, a=start, b=end, dashed=True, kind=K_SECTION_LINE),
    ]

    for origin, outward in ((start, -1), (end, 1)):
        ox, oy = origin
        tip = point_round(ox + vx * flag, oy + vy * flag)
        out.append(Line(layer=A_DIM, a=origin, b=tip, kind=K_SECTION_FLAG))
        # Arrowhead barbs, opening back towards the cut line.
        for side in (1, -1):
            out.append(
                Line(
                    layer=A_DIM,
                    a=tip,
                    b=point_round(
                        tip[0] - vx * arrow + ux * side * arrow // 2,
                        tip[1] - vy * arrow + uy * side * arrow // 2,
                    ),
                    kind=K_SECTION_FLAG,
                )
            )
        out.append(
            Text(
                layer=A_TEXT,
                position=point_round(ox + ux * outward * gap, oy + uy * outward * gap),
                text=label,
                height_mm=style.section_text_height_mm,
                kind=K_SECTION_LABEL,
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# Room label block
# ---------------------------------------------------------------------------
def room_label(room: Any, style: Style, *, ordinal: Optional[int] = None) -> Tuple[Primitive, ...]:
    """Dashed room outline on A-AREA, name and area on A-TEXT.

    The area string is ``garh_model.units.format_sqft(room.area_mm2, 1)`` — §7's "area in
    sqft one decimal", formatted by the same function the UI and the area statement use,
    reading the same ``area_mm2`` the rules engine reads. One number, one formatter, one
    source: a room that prints 118.4 sq ft on the plan cannot print 118.5 in the area
    statement.
    """
    from garh_model.geometry import polygon_centroid
    from garh_model.model import room_display_name
    from garh_model.units import format_sqft

    if len(room.polygon) < 3:
        return ()
    centre = point_of(polygon_centroid(room.polygon))
    gap = style.label_line_gap_mm
    return (
        Polyline(
            layer=A_AREA,
            points=tuple(point_of(p) for p in room.polygon),
            closed=True,
            dashed=True,
            owner_id=room.id,
            kind=K_ROOM_OUTLINE,
        ),
        Text(
            layer=A_TEXT,
            position=point(centre[0], centre[1] + gap // 2),
            text=sanitise_text(room_display_name(room, ordinal)).upper(),
            height_mm=style.room_name_height_mm,
            v_align="bottom",
            owner_id=room.id,
            kind=K_ROOM_NAME,
        ),
        Text(
            layer=A_TEXT,
            position=point(centre[0], centre[1] - gap // 2),
            text=format_sqft(room.area_mm2, 1),
            height_mm=style.room_area_height_mm,
            v_align="top",
            owner_id=room.id,
            kind=K_ROOM_AREA,
        ),
    )


# ---------------------------------------------------------------------------
# Opening tags (D1 / W2 / V1)
# ---------------------------------------------------------------------------
def opening_tag(band: WallBand, opening: Any, style: Style) -> Tuple[Primitive, ...]:
    """The schedule tag beside an opening, when the schedule has assigned one.

    No tag is invented here: ``Opening.tag`` is written by the door/window schedule
    generator (§7), and a plan drawn before the schedule ran simply shows no tags rather
    than a numbering that the schedule would later contradict.
    """
    if not opening.tag:
        return ()
    span = opening_span(band.frame, opening)
    if span is None:
        return ()
    across = band.frame.half_mm + style.tag_offset_mm
    # All tags on A-TEXT, doors included: a tag is a callout, not glazing, and splitting
    # them across A-DOOR/A-WIND would mean turning off the window layer also hides half
    # the schedule references.
    return (
        Text(
            layer=A_TEXT,
            position=band.frame.at((span.start_mm + span.end_mm) // 2, across),
            text=sanitise_text(opening.tag, max_length=8),
            height_mm=style.tag_height_mm,
            rotation_deg=readable_rotation(band.frame.angle_deg),
            owner_id=opening.id,
            kind=K_OPENING_TAG,
        ),
    )


# ---------------------------------------------------------------------------
# Columns and the grid of bubbles
# ---------------------------------------------------------------------------
#: Columns within this distance of each other count as being on the same grid line.
#: Half a brick module: closer than that and they are the same line drawn twice; further
#: and they are genuinely two lines a structural engineer will want numbered separately.
GRID_TOLERANCE_MM = 60


@dataclass(frozen=True)
class GridLine:
    """One numbered/lettered structural grid line."""

    label: str
    #: 'x' — a vertical line at this x; 'y' — a horizontal line at this y.
    axis: str
    at_mm: int


def column_ring(column: Any) -> Tuple[Point, ...]:
    """A column's rectangle in plan. Mirrors ``columnRingMm`` in the canvas twin."""
    half_x = round_half_away(column.size_mm.x_mm / 2)
    half_y = round_half_away(column.size_mm.y_mm / 2)
    cx, cy = column.pt.x, column.pt.y
    return (
        point(cx - half_x, cy - half_y),
        point(cx + half_x, cy - half_y),
        point(cx + half_x, cy + half_y),
        point(cx - half_x, cy + half_y),
    )


def column_grid_lines(columns: Sequence[Any], *, tolerance_mm: int = GRID_TOLERANCE_MM) -> Tuple[GridLine, ...]:
    """Cluster column centres into grid lines: numbers across X, letters up Y.

    Numbered 1, 2, 3 … left to right and lettered A, B, C … bottom to top, which is the
    convention an Indian structural drawing uses and therefore what the engineer
    receiving this DXF expects.
    """
    x_values = _cluster(sorted(column.pt.x for column in columns), tolerance_mm)
    y_values = _cluster(sorted(column.pt.y for column in columns), tolerance_mm)
    lines: List[GridLine] = []
    for index, value in enumerate(x_values):
        lines.append(GridLine(label=str(index + 1), axis="x", at_mm=value))
    for index, value in enumerate(y_values):
        lines.append(GridLine(label=_letters(index), axis="y", at_mm=value))
    return tuple(lines)


def _cluster(values: Sequence[int], tolerance_mm: int) -> List[int]:
    """Group near-equal coordinates and return one representative each."""
    groups: List[List[int]] = []
    for value in values:
        if groups and value - groups[-1][-1] <= tolerance_mm:
            groups[-1].append(value)
        else:
            groups.append([value])
    # The rounded mean, so a grid line sits through the middle of its columns rather
    # than on whichever one happened to be first.
    return [round_half_away(sum(group) / len(group)) for group in groups]


def _letters(index: int) -> str:
    """0 → A, 25 → Z, 26 → AA. Grids past Z exist on big plots."""
    label = ""
    value = index
    while True:
        label = chr(ord("A") + value % 26) + label
        value = value // 26 - 1
        if value < 0:
            return label


def column_symbols(columns: Sequence[Any], style: Style) -> Tuple[Primitive, ...]:
    """Column rectangles with a concrete hatch. Coordination only — never affects areas."""
    out: List[Primitive] = []
    for column in columns:
        ring = column_ring(column)
        out.append(
            Polyline(
                layer=A_WALL_PART,
                points=ring,
                closed=True,
                owner_id=column.id,
                kind=K_COLUMN,
            )
        )
        out.append(
            Hatch(
                layer=A_WALL_PART,
                boundary=ring,
                pattern=PATTERN_CONCRETE,
                angle_deg=45,
                spacing_mm=style.hatch_spacing_mm,
                owner_id=column.id,
                kind=K_COLUMN_HATCH,
            )
        )
    return tuple(out)


def column_bubbles(
    columns: Sequence[Any],
    style: Style,
    *,
    tolerance_mm: int = GRID_TOLERANCE_MM,
) -> Tuple[Primitive, ...]:
    """§7's "grid of column bubbles if columns exist" — lines, bubbles, labels.

    Grid lines and bubbles go on **A-DIM**: they are a measuring and referencing
    construct, like a witness line, not built fabric. The nine §7 layers are a contract
    with AutoCAD and adding a tenth (A-GRID) to hold this would break every downstream
    consumer for one symbol's sake — so the choice is documented here instead.
    """
    if not columns:
        return ()
    xs = [column.pt.x for column in columns]
    ys = [column.pt.y for column in columns]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    extension = style.grid_extension_mm
    radius = style.grid_bubble_radius_mm

    out: List[Primitive] = []
    for line in column_grid_lines(columns, tolerance_mm=tolerance_mm):
        if line.axis == "x":
            start = point(line.at_mm, min_y - extension)
            end = point(line.at_mm, max_y + extension)
            bubble = point(line.at_mm, min_y - extension - radius)
        else:
            start = point(min_x - extension, line.at_mm)
            end = point(max_x + extension, line.at_mm)
            bubble = point(min_x - extension - radius, line.at_mm)
        out.append(Line(layer=A_DIM, a=start, b=end, dashed=True, kind=K_GRID_LINE))
        out.append(
            Arc(
                layer=A_DIM,
                centre=bubble,
                radius_mm=radius,
                start_deg=0,
                end_deg=360,
                kind=K_GRID_BUBBLE,
            )
        )
        out.append(
            Text(
                layer=A_TEXT,
                position=bubble,
                text=line.label,
                height_mm=style.grid_text_height_mm,
                kind=K_GRID_LABEL,
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# Balconies
# ---------------------------------------------------------------------------
def balcony_symbol(
    balcony: Any, style: Style, *, walls: Sequence[Any] = ()
) -> Tuple[Primitive, ...]:
    """Slab edge plus a railing line inside each **open** edge.

    The railing is drawn per edge rather than as an offset polygon: an inset ring needs
    mitre handling at every corner, and a 1mm-on-paper gap at a balcony corner is
    invisible while a wrongly mitred ring is not.

    The edge a balcony shares with the building gets no railing — drawing one there puts
    a handrail across the french door you step out of. ``walls`` is the host storey's
    walls; an edge whose midpoint sits inside a wall's thickness is taken to be that
    shared edge.
    """
    from garh_model.geometry import ensure_ccw

    if len(balcony.polygon) < 3:
        return ()
    ring = [point_of(p) for p in ensure_ccw(balcony.polygon)]
    out: List[Primitive] = [
        Polyline(
            layer=A_WALL_PART,
            points=tuple(ring),
            closed=True,
            owner_id=balcony.id,
            kind=K_BALCONY,
        )
    ]
    if balcony.railing_kind == "none":
        return tuple(out)
    inset = max(1, style.railing_inset_mm)
    count = len(ring)
    for index in range(count):
        (px, py) = ring[index]
        (qx, qy) = ring[(index + 1) % count]
        length = math.hypot(qx - px, qy - py)
        if length == 0:
            continue
        if _edge_meets_a_wall((px + qx) // 2, (py + qy) // 2, walls):
            continue
        # Interior of a CCW ring is to the left of each directed edge.
        inx, iny = -(qy - py) / length, (qx - px) / length
        out.append(
            Line(
                layer=A_WALL_PART,
                a=point_round(px + inx * inset, py + iny * inset),
                b=point_round(qx + inx * inset, qy + iny * inset),
                owner_id=balcony.id,
                kind=K_BALCONY_RAILING,
            )
        )
    return tuple(out)


def _edge_meets_a_wall(mid_x: int, mid_y: int, walls: Sequence[Any]) -> bool:
    """Is this edge midpoint inside some wall's thickness? Then the edge is not open."""
    for wall in walls:
        ax, ay = wall.a.x, wall.a.y
        dx, dy = wall.b.x - ax, wall.b.y - ay
        length_sq = dx * dx + dy * dy
        if length_sq == 0:
            continue
        t = ((mid_x - ax) * dx + (mid_y - ay) * dy) / length_sq
        t = min(1.0, max(0.0, t))
        near_x, near_y = ax + dx * t, ay + dy * t
        if math.hypot(mid_x - near_x, mid_y - near_y) <= wall.thickness_mm / 2:
            return True
    return False


__all__ = [
    "GRID_TOLERANCE_MM",
    "STAIR_VECTORS",
    "GridLine",
    "SectionMarker",
    "balcony_symbol",
    "column_bubbles",
    "column_grid_lines",
    "column_ring",
    "column_symbols",
    "level_marker",
    "north_arrow",
    "opening_tag",
    "readable_rotation",
    "room_label",
    "section_marker",
    "stair_symbol",
]
