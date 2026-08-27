"""The 3x3 Vastu grid and the 8 compass sectors, oriented to TRUE north.

Derivation, verbatim from ``rulepacks/README.md`` (the tiebreaker) and the
schema's ``$defs.zone``:

  Rotate the plot boundary **counter-clockwise by ``plot.northDeg``** so true
  north becomes ``+Y``; take the axis-aligned bounding box; split into 3 equal
  columns x 3 equal rows, cell boundaries rounded **half-up** to whole
  millimetres. Top row ``NW N NE``, middle ``W C E``, bottom ``SW S SE``. An
  element's zone is the cell containing its centroid; a centroid exactly on a
  boundary belongs to the **more-north / more-east** cell. ``C`` is the
  brahmasthan.

And for facing:

  ``azimuth = (outwardNormalDeg - northDeg) mod 360``, sectors 45 degrees wide
  centred on the cardinals: ``N = [337.5, 22.5)``, ``NE = [22.5, 67.5)``, ...

The half-degree sector edges are handled in doubled integer degrees, so the
classification never touches a float. The *rotation* does need trigonometry for a
non-cardinal north, but its output is rounded to whole millimetres before
anything is classified — so which cell a room lands in is reproducible even
though the intermediate is a float64 (the four cardinal bearings are exact
integer swaps and skip it entirely).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .context import PlotSummary
from .geometry import Point, Ring, polygon_bbox, rotate_ccw_deg

__all__ = [
    "ZONES",
    "COMPASS8",
    "ZoneGrid",
    "facing_of",
    "zone_grid_for",
    "format_zone_list",
]

#: The nine cells. Declaration order is the schema's; :func:`sorted` is what the
#: engine uses when a rule's ``actual`` lists several (see ``engine.py``).
ZONES: tuple[str, ...] = ("N", "NE", "E", "SE", "S", "SW", "W", "NW", "C")

#: The eight facing sectors. ``C`` is not a facing.
COMPASS8: tuple[str, ...] = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

#: (row, col) -> zone. row 2 is the most northerly, col 2 the most easterly.
_CELL_NAMES: tuple[tuple[str, str, str], ...] = (
    ("SW", "S", "SE"),
    ("W", "C", "E"),
    ("NW", "N", "NE"),
)


def _third(span: int) -> int:
    """``round_half_up(span / 3)`` in integers: ``floor((2*span + 3) / 6)``."""
    return (2 * span + 3) // 6


def _two_thirds(span: int) -> int:
    """``round_half_up(2 * span / 3)`` in integers: ``floor((4*span + 3) / 6)``."""
    return (4 * span + 3) // 6


@dataclass(frozen=True)
class ZoneGrid:
    """A plot's 3x3 grid, in the north-oriented (rotated) coordinate frame.

    Every classification takes a **plot-local** point and rotates it here, so
    callers never have to remember which frame they are in.
    """

    north_deg: int
    min_x: int
    min_y: int
    max_x: int
    max_y: int
    col_split_1: int
    col_split_2: int
    row_split_1: int
    row_split_2: int

    @classmethod
    def from_ring(cls, boundary_mm: Ring, north_deg: int) -> ZoneGrid:
        rotated = [rotate_ccw_deg(p, north_deg) for p in boundary_mm]
        min_x, min_y, max_x, max_y = polygon_bbox(rotated)
        width = max_x - min_x
        depth = max_y - min_y
        return cls(
            north_deg=north_deg % 360,
            min_x=min_x,
            min_y=min_y,
            max_x=max_x,
            max_y=max_y,
            col_split_1=min_x + _third(width),
            col_split_2=min_x + _two_thirds(width),
            row_split_1=min_y + _third(depth),
            row_split_2=min_y + _two_thirds(depth),
        )

    # -- classification ----------------------------------------------------
    def rotate(self, point: Point) -> Point:
        """Plot-local -> north-oriented."""
        return rotate_ccw_deg(point, self.north_deg)

    def zone_of(self, point_plot_local: Point) -> str:
        """Which cell a plot-local point sits in."""
        x, y = self.rotate(point_plot_local)
        return self.zone_of_rotated((x, y))

    def zone_of_rotated(self, point: Point) -> str:
        x, y = point
        # `<` on the low side means a coordinate exactly on a split belongs to the
        # higher-index cell — the more-north / more-east one, as specified.
        col = 0 if x < self.col_split_1 else (1 if x < self.col_split_2 else 2)
        row = 0 if y < self.row_split_1 else (1 if y < self.row_split_2 else 2)
        return _CELL_NAMES[row][col]

    def cell_rect(self, zone: str) -> tuple[int, int, int, int]:
        """``(x0, y0, x1, y1)`` of one cell, in the **rotated** frame."""
        for row, names in enumerate(_CELL_NAMES):
            for col, name in enumerate(names):
                if name == zone:
                    xs = (self.min_x, self.col_split_1, self.col_split_2, self.max_x)
                    ys = (self.min_y, self.row_split_1, self.row_split_2, self.max_y)
                    return (xs[col], ys[row], xs[col + 1], ys[row + 1])
        raise KeyError("unknown zone %r" % (zone,))

    def centre_cell_rect(self) -> tuple[int, int, int, int]:
        """The brahmasthan, in the rotated frame."""
        return self.cell_rect("C")

    def rotate_ring(self, ring: Ring) -> tuple[Point, ...]:
        """Rotate a whole polygon into the grid's frame (rotation preserves area)."""
        if self.north_deg == 0:
            return tuple(ring)
        return tuple(self.rotate(p) for p in ring)


def zone_grid_for(plot: PlotSummary) -> ZoneGrid:
    return ZoneGrid.from_ring(plot.boundary_mm, plot.north_deg)


def facing_of(outward_normal_deg: int, north_deg: int) -> str:
    """The 45-degree compass sector an outward normal points into.

    Integer arithmetic on doubled degrees, so the half-degree sector edges are
    exact: sector index = ``((2*azimuth + 45) // 90) mod 8``. That puts 22 degrees
    in ``N`` and 23 in ``NE``, which is the ``[22.5, 67.5)`` half-open interval the
    spec asks for.
    """
    azimuth = (outward_normal_deg - north_deg) % 360
    index = ((2 * azimuth + 45) // 90) % 8
    return COMPASS8[index]


def format_zone_list(zones: Sequence[str]) -> str:
    """ "N, NE or E" — for the ``{limit}`` placeholder in a Vastu chip."""
    items = list(zones)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return "%s or %s" % (", ".join(items[:-1]), items[-1])
