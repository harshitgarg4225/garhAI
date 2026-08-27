"""Stair geometry for the section — and an explicit account of what the model omits.

READ THIS BEFORE DRAWING A DOGLEG
---------------------------------
``garh_model.model.Stair`` stores a stair as ``{kind, origin, direction, riser, tread,
width, risersCount, landing}``: **one** origin, **one** direction, **one** landing block.
That is enough to place a footprint and a slab void, and it is enough to draw a straight
flight exactly. It is *not* enough to draw a true dogleg, L or U, because the model does
not say where the second flight starts or which way it runs — that is a modelling gap, not
a rendering one.

So this module draws what the model actually carries:

======================  =====================================================
``straight``            every riser, exactly — the profile is fully determined
``dogleg`` / ``U``      the **first** flight (``ceil(risersCount / 2)`` risers) and
                        the landing; the return flight is not drawn
``L``                   the first flight and the landing; the turn is not drawn
======================  =====================================================

In every partial case the section carries a note saying so, and the level reached by the
drawn part is labelled, so a reader can see the section stops at the landing rather than
inferring a stair that climbs half a storey. The alternative — inventing a return flight
from a convention — would put geometry on a municipal drawing that the model, the 3D view
and the plan do not agree with, and §7's whole value is that they agree.

The footprint maths mirrors ``garh_model.fold.stair_footprint_polygon`` (which the model
core uses for slab voids) rather than importing it, so this module stays dependency-free
like the rest of the drawings engine. ``tests/test_sections.py`` asserts the two agree on
the fixture; if the model core changes its convention, that test fails loudly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "FLIGHT_GAP_MM",
    "Rect",
    "StairGeometry",
    "stair_geometry",
    "STAIR_VECTORS",
]

#: ``direction -> (forward, right)``. Right is 90° clockwise from forward, matching
#: ``garh_model.fold._STAIR_VECTORS`` exactly.
STAIR_VECTORS: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {
    "N": ((0, 1), (1, 0)),
    "E": ((1, 0), (0, -1)),
    "S": ((0, -1), (-1, 0)),
    "W": ((-1, 0), (0, 1)),
}

#: Gap between the two flights of a dogleg/U when no landing width is given — the model
#: core uses the same 100mm when it derives a footprint.
FLIGHT_GAP_MM = 100

#: An axis-aligned model-space rectangle ``(x_lo, y_lo, x_hi, y_hi)``.
Rect = tuple[int, int, int, int]


def _rect_of(
    origin: tuple[int, int],
    forward: tuple[int, int],
    right: tuple[int, int],
    along_lo: int,
    along_hi: int,
    across_lo: int,
    across_hi: int,
) -> Rect:
    """Rectangle from stair-local (along, across) bounds, in model coordinates."""
    xs: list[int] = []
    ys: list[int] = []
    for along in (along_lo, along_hi):
        for across in (across_lo, across_hi):
            xs.append(origin[0] + forward[0] * along + right[0] * across)
            ys.append(origin[1] + forward[1] * along + right[1] * across)
    return (min(xs), min(ys), max(xs), max(ys))


@dataclass(frozen=True)
class StairGeometry:
    """Everything the section needs about one stair, in model space and stair-local mm."""

    stair_id: str
    storey_id: str
    kind: str
    direction: str
    origin: tuple[int, int]
    forward: tuple[int, int]
    right: tuple[int, int]
    riser_mm: int
    tread_mm: int
    width_mm: int
    risers_count: int
    #: Risers in the flight this module can place (all of them for a straight stair).
    drawn_risers: int
    #: Going of the drawn flight: ``(drawn_risers - 1) * tread``, the model's convention.
    going_mm: int
    landing_depth_mm: int
    footprint: Rect
    flight_rect: Rect
    landing_rect: Rect | None
    #: True when the model cannot describe the whole stair (see the module docstring).
    partial: bool

    @property
    def drawn_rise_mm(self) -> int:
        """Height the drawn flight reaches above the storey FFL."""
        return self.drawn_risers * self.riser_mm

    @property
    def total_rise_mm(self) -> int:
        return self.risers_count * self.riser_mm

    def note(self) -> str | None:
        """The honest sentence a partial stair puts on the sheet."""
        if not self.partial:
            return None
        return (
            "Stair %s is a %s: the model stores one origin, direction and landing, so the "
            "section shows the first flight (%d of %d risers, +%d) and the landing. The "
            "return flight is not drawn."
            % (
                self.stair_id,
                self.kind,
                self.drawn_risers,
                self.risers_count,
                self.drawn_rise_mm,
            )
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "stairId": self.stair_id,
            "storeyId": self.storey_id,
            "kind": self.kind,
            "direction": self.direction,
            "riserMm": self.riser_mm,
            "treadMm": self.tread_mm,
            "widthMm": self.width_mm,
            "risersCount": self.risers_count,
            "drawnRisers": self.drawn_risers,
            "footprint": list(self.footprint),
            "partial": self.partial,
        }


def stair_geometry(stair: Any) -> StairGeometry:
    """Derive the section geometry of a model ``Stair``. Duck-typed, integer, pure."""
    direction = str(stair.direction)
    if direction not in STAIR_VECTORS:
        raise ValueError("stair direction %r is not one of N/E/S/W" % (direction,))
    forward, right = STAIR_VECTORS[direction]
    origin = (int(stair.origin.x), int(stair.origin.y))
    riser = int(stair.riser_mm)
    tread = int(stair.tread_mm)
    width = int(stair.width_mm)
    risers = int(stair.risers_count)
    kind = str(stair.kind)
    landing = getattr(stair, "landing", None)

    def going_of(count: int) -> int:
        # Mirror of garh_model.fold.stair_footprint_polygon: the last riser lands on the
        # floor above, so a flight of n risers has (n-1) treads.
        return max(1, count - 1) * tread

    if kind == "straight":
        drawn = risers
        going = going_of(drawn)
        landing_depth = 0
        depth = going
        footprint_width = width
        landing_rect: Rect | None = None
        partial = False
    else:
        drawn = -((-risers) // 2)  # ceil(risers / 2)
        going = going_of(drawn)
        landing_depth = width if landing is None else int(landing.depth_mm)
        depth = going + landing_depth
        if kind == "L":
            landing_width = width if landing is None else int(landing.width_mm)
            footprint_width = width + landing_width
        else:
            footprint_width = (
                2 * width + FLIGHT_GAP_MM if landing is None else int(landing.width_mm)
            )
        landing_rect = _rect_of(origin, forward, right, going, depth, 0, footprint_width)
        partial = True

    return StairGeometry(
        stair_id=str(stair.id),
        storey_id=str(stair.storey_id),
        kind=kind,
        direction=direction,
        origin=origin,
        forward=forward,
        right=right,
        riser_mm=riser,
        tread_mm=tread,
        width_mm=width,
        risers_count=risers,
        drawn_risers=drawn,
        going_mm=going,
        landing_depth_mm=landing_depth,
        footprint=_rect_of(origin, forward, right, 0, depth, 0, footprint_width),
        flight_rect=_rect_of(origin, forward, right, 0, going, 0, width),
        landing_rect=landing_rect,
        partial=partial,
    )
