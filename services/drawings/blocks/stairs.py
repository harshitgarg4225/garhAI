"""Stair symbols in plan: straight flight, dog-leg, spiral.

Local frame (before :func:`~services.drawings.blocks.base.place`), for the two
rectilinear stairs:

* the origin is the **bottom of the first flight, on its ``−Y`` edge**;
* ``+X`` is the direction of travel and ``+Y`` is across the width, so the flight
  occupies ``x ∈ [0, run]``, ``y ∈ [0, width_mm]``.

The spiral is centred on its newel instead, because that is the point a spiral is set
out from on site.

The arithmetic that has to be right
-----------------------------------
A flight of ``n`` risers has ``n − 1`` treads: the last riser lands on the floor (or the
landing) above, and there is no tread on it. :func:`tread_count` is that rule, stated
once, and :func:`straight_flight_run_mm` is the run it implies. This mirrors
``projection.symbols.stair_symbol``'s ``going_mm``, deliberately — two modules drawing
the same stair a tread apart is how a plan and a section stop agreeing.

Direction is an argument, and it changes both the arrow and the text: a flight drawn
``UP 18R`` on the ground floor is the same geometry drawn ``DN 18R`` on the first, and
getting that backwards is the classic set-out error a reviewer looks for first.
"""

from __future__ import annotations

import math

from services.drawings.blocks.base import (
    Insertion,
    arc_endpoint,
    arrow,
    place,
    require_choice,
    require_positive,
    round_half_away,
)
from services.drawings.layers import A_STAIR, A_TEXT
from services.drawings.render.primitives import (
    TEXT_HEIGHT_SMALL_PAPER_UM,
    Arc,
    Circle,
    Line,
    Polyline,
    Primitive,
    Pt2,
    Text,
)

__all__ = [
    "DIRECTIONS",
    "DIRECTION_DN",
    "DIRECTION_UP",
    "stair_dogleg",
    "stair_spiral",
    "stair_straight",
    "straight_flight_run_mm",
    "tread_count",
]

DIRECTION_UP = "up"
DIRECTION_DN = "dn"
DIRECTIONS: tuple[str, ...] = (DIRECTION_UP, DIRECTION_DN)


def tread_count(riser_count: int) -> int:
    """Treads in a flight of ``riser_count`` risers. The top riser has no tread."""
    require_positive("riser_count", riser_count)
    if riser_count < 2:
        raise ValueError("a flight needs at least 2 risers to have a tread, got %d" % riser_count)
    return riser_count - 1


def straight_flight_run_mm(tread_mm: int, riser_count: int) -> int:
    """The run a straight flight occupies: ``tread_count × tread_mm``, exactly."""
    require_positive("tread_mm", tread_mm)
    return tread_count(riser_count) * tread_mm


def _label(direction: str, riser_count: int) -> str:
    return "%s %dR" % ("UP" if direction == DIRECTION_UP else "DN", riser_count)


def _rect(x0: int, y0: int, x1: int, y1: int) -> Polyline:
    return Polyline(vertices=((x0, y0), (x1, y0), (x1, y1), (x0, y1)), layer=A_STAIR, closed=True)


def _flight_arrow(
    *,
    from_x: int,
    to_x: int,
    centre_y: int,
    width_mm: int,
    direction: str,
) -> tuple[Primitive, ...]:
    """The travel arrow along one flight, pointing the way you walk."""
    tail: Pt2 = (from_x, centre_y)
    head: Pt2 = (to_x, centre_y)
    if direction == DIRECTION_DN:
        tail, head = head, tail
    return arrow(tail, head, barb_mm=max(1, width_mm // 5), layer=A_STAIR)


def _break_lines(*, at_x: int, width_mm: int, tread_mm: int) -> tuple[Primitive, ...]:
    """The conventional pair of slanted parallel lines where a flight is cut.

    A plan is cut at about 1200 mm, so a flight that keeps rising leaves the view. The
    break line is what says "this continues"; without it a G+1 stair reads as a stair
    that stops halfway up, which is what the reviewer will write on the drawing.
    """
    over = max(1, width_mm // 20)
    slant = max(1, width_mm // 3)
    gap = max(2, tread_mm // 2)
    out: list[Primitive] = []
    for centre in (at_x - gap // 2, at_x + gap // 2):
        out.append(
            Line(
                a=(centre - slant // 2, -over),
                b=(centre + slant // 2, width_mm + over),
                layer=A_STAIR,
            )
        )
    return tuple(out)


def stair_straight(
    *,
    tread_mm: int,
    riser_count: int,
    width_mm: int,
    element_id: str,
    direction: str = DIRECTION_UP,
    break_after_treads: int | None = None,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """One straight flight: footprint, treads, travel arrow, ``UP 18R``.

    ``break_after_treads`` draws the break line after that many treads; omit it for a
    flight drawn whole (a stair to a mumty, or the top flight of a section).
    """
    require_positive("tread_mm", tread_mm)
    require_positive("width_mm", width_mm)
    require_choice("direction", direction, DIRECTIONS)
    treads = tread_count(riser_count)
    run = treads * tread_mm

    local: list[Primitive] = [_rect(0, 0, run, width_mm)]
    for index in range(1, treads):
        local.append(Line(a=(index * tread_mm, 0), b=(index * tread_mm, width_mm), layer=A_STAIR))

    centre_y = width_mm // 2
    local.extend(
        _flight_arrow(
            from_x=tread_mm // 2,
            to_x=run - tread_mm // 2,
            centre_y=centre_y,
            width_mm=width_mm,
            direction=direction,
        )
    )
    if break_after_treads is not None:
        if not 1 <= break_after_treads < treads:
            raise ValueError(
                "break_after_treads must be between 1 and %d for a %d-riser flight, got %d"
                % (treads - 1, riser_count, break_after_treads)
            )
        local.extend(
            _break_lines(at_x=break_after_treads * tread_mm, width_mm=width_mm, tread_mm=tread_mm)
        )
    local.append(
        Text(
            at=(run // 2, centre_y + max(1, width_mm // 4)),
            text=_label(direction, riser_count),
            layer=A_TEXT,
            height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM,
            anchor="middle",
            baseline="middle",
        )
    )
    return place(tuple(local), insertion, element_id)


def stair_dogleg(
    *,
    tread_mm: int,
    riser_count: int,
    width_mm: int,
    landing_depth_mm: int,
    element_id: str,
    well_mm: int = 0,
    direction: str = DIRECTION_UP,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """Two flights and a half-landing, the 180° turn an Indian house stair is.

    The risers split with the odd one on the lower flight — ``(n + 1) // 2`` up, the
    rest down the return — because that is where a builder puts it when the landing has
    to arrive at half the storey height.

    ``well_mm`` is the gap between the two flights: 0 for a solid dog-leg, positive for
    an open well the handrail returns around.
    """
    require_positive("tread_mm", tread_mm)
    require_positive("width_mm", width_mm)
    require_positive("landing_depth_mm", landing_depth_mm)
    require_choice("direction", direction, DIRECTIONS)
    if well_mm < 0:
        raise ValueError("well_mm cannot be negative, got %d" % well_mm)
    if riser_count < 4:
        raise ValueError(
            "a dog-leg needs at least 4 risers (2 per flight) to have a tread in each "
            "flight, got %d" % riser_count
        )

    risers_up = (riser_count + 1) // 2
    risers_down = riser_count - risers_up
    run_a = tread_count(risers_up) * tread_mm
    run_b = tread_count(risers_down) * tread_mm
    far_y = 2 * width_mm + well_mm

    local: list[Primitive] = [
        _rect(0, 0, run_a, width_mm),
        _rect(run_a, 0, run_a + landing_depth_mm, far_y),
        _rect(run_a - run_b, width_mm + well_mm, run_a, far_y),
    ]
    for index in range(1, tread_count(risers_up)):
        local.append(Line(a=(index * tread_mm, 0), b=(index * tread_mm, width_mm), layer=A_STAIR))
    for index in range(1, tread_count(risers_down)):
        x = run_a - index * tread_mm
        local.append(Line(a=(x, width_mm + well_mm), b=(x, far_y), layer=A_STAIR))

    local.extend(
        _flight_arrow(
            from_x=tread_mm // 2,
            to_x=run_a - tread_mm // 2,
            centre_y=width_mm // 2,
            width_mm=width_mm,
            direction=direction,
        )
    )
    # The return flight walks the other way, so its arrow is the first one's mirror —
    # and it flips with `direction` too, or a DN stair would show you walking up it.
    local.extend(
        _flight_arrow(
            from_x=run_a - tread_mm // 2,
            to_x=run_a - run_b + tread_mm // 2,
            centre_y=width_mm + well_mm + width_mm // 2,
            width_mm=width_mm,
            direction=direction,
        )
    )
    local.append(
        Text(
            at=(run_a // 2, width_mm // 2 + max(1, width_mm // 4)),
            text=_label(direction, riser_count),
            layer=A_TEXT,
            height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM,
            anchor="middle",
            baseline="middle",
        )
    )
    return place(tuple(local), insertion, element_id)


def stair_spiral(
    *,
    outer_radius_mm: int,
    inner_radius_mm: int,
    riser_count: int,
    element_id: str,
    sweep_deg: int = 360,
    start_deg: int = 0,
    direction: str = DIRECTION_UP,
    insertion: Insertion = Insertion(),
) -> tuple[Primitive, ...]:
    """A spiral: newel, outer edge, wedge treads at an even angular pitch, walking line.

    Centred on the newel. ``sweep_deg`` is the total turn — 360 for a full revolution,
    270 for the quarter-landing version that fits an Indian mumty — and the treads
    divide it evenly, so the pitch is ``sweep_deg / tread_count`` and the radial edges
    fall on exact multiples of it.
    """
    require_positive("outer_radius_mm", outer_radius_mm)
    require_positive("inner_radius_mm", inner_radius_mm)
    require_positive("sweep_deg", sweep_deg)
    require_choice("direction", direction, DIRECTIONS)
    if inner_radius_mm >= outer_radius_mm:
        raise ValueError(
            "the newel (%d mm) must be smaller than the outer edge (%d mm)"
            % (inner_radius_mm, outer_radius_mm)
        )
    if sweep_deg > 360:
        raise ValueError(
            "a plan shows one revolution at most; %d° would draw treads over treads "
            "with nothing to say which is which" % sweep_deg
        )
    treads = tread_count(riser_count)
    centre: Pt2 = (0, 0)
    pitch = sweep_deg / treads
    full_circle = sweep_deg == 360

    local: list[Primitive] = [Circle(centre=centre, radius_mm=inner_radius_mm, layer=A_STAIR)]
    if full_circle:
        local.append(Circle(centre=centre, radius_mm=outer_radius_mm, layer=A_STAIR))
    else:
        local.append(
            Arc(
                centre=centre,
                radius_mm=outer_radius_mm,
                start_deg=start_deg % 360,
                end_deg=(start_deg + sweep_deg) % 360,
                layer=A_STAIR,
            )
        )

    # One radial edge per tread; the closing edge is drawn only when it is not the
    # opening edge come round again.
    edges = treads if full_circle else treads + 1
    for index in range(edges):
        angle = start_deg + index * pitch
        local.append(
            Line(
                a=arc_endpoint(centre, inner_radius_mm, angle),
                b=arc_endpoint(centre, outer_radius_mm, angle),
                layer=A_STAIR,
            )
        )

    # The walking line runs from the middle of the first tread to the middle of the
    # last, always drawn CCW because an Arc is CCW; which way you *walk* it is the
    # arrow's job, not the arc's.
    walk_radius = (inner_radius_mm + outer_radius_mm) // 2
    walk_lo = start_deg + pitch / 2
    walk_hi = start_deg + sweep_deg - pitch / 2
    local.append(
        Arc(
            centre=centre,
            radius_mm=walk_radius,
            start_deg=round_half_away(walk_lo) % 360,
            end_deg=round_half_away(walk_hi) % 360,
            layer=A_STAIR,
        )
    )
    walk_to, tangent_back = (
        (walk_hi, walk_hi - pitch / 2)
        if direction == DIRECTION_UP
        else (walk_lo, walk_lo + pitch / 2)
    )
    local.extend(
        arrow(
            arc_endpoint(centre, walk_radius, tangent_back),
            arc_endpoint(centre, walk_radius, walk_to),
            barb_mm=max(1, (outer_radius_mm - inner_radius_mm) // 4),
            layer=A_STAIR,
        )
    )
    label_angle = math.radians(start_deg + sweep_deg / 2)
    local.append(
        Text(
            at=(
                round_half_away(walk_radius * math.cos(label_angle)),
                round_half_away(walk_radius * math.sin(label_angle)),
            ),
            text=_label(direction, riser_count),
            layer=A_TEXT,
            height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM,
            anchor="middle",
            baseline="middle",
        )
    )
    return place(tuple(local), insertion, element_id)
