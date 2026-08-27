"""§5.2 coarse grid — envelope polygon → 300mm cell grid + 3×3 plot zones. **ortools-free.**

    "Grid the envelope (rect/L/T = union of ≤3 rects; solve on rect cells with L/T
     handled by mandatory-void cells)" — engineering playbook §5.2.

This module is the pure-geometry half of stage A: everything in it is exact integer
arithmetic, provable on a bare Python interpreter, so the CP-SAT model that consumes it
(:mod:`services.solver.stage_a`) starts from facts rather than estimates.

COORDINATE CONVENTIONS (shared with :func:`services.solver.stages.grid_envelope`):

* Cell space is ``(col, row)`` with cell ``(0, 0)`` at the envelope bbox MINIMUM —
  plot-local south-west. ``mask[row][col]`` is ``True`` where the cell is buildable.
* A cell is buildable only when **all four corners and the centre** lie inside the
  envelope (inclusive). Corners make the test conservative — a buildable cell is
  *provably* inside the setbacks, because a room the solver packs onto it will be too.
  (:func:`services.solver.stages.grid_envelope` tests centres only; that mask feeds
  logging and §5.7 obstacle transforms, where erring outward is harmless. This one
  feeds compliance-bearing geometry, where it is not.)
* ``rects``/``voids`` are half-open cell rectangles: ``[col1, col2) × [row1, row2)``.
* All mm↔cell transforms are integer: ``mm = origin + cell * module``; centres are
  ``+ module // 2`` (the 300mm module is even, so centres are exact).

ZONES: the 3×3 Vastu/facing grid is oriented to TRUE north via
:func:`services.solver.geometry.zone_for_point` — one implementation, shared with the
critic and the diversity signature, so stage A can never disagree with the compass
wheel about which third a room is in. When true north is a multiple of 90° the nine
zones are axis-aligned mm bands (:func:`zone_bands_mm`) that CP-SAT can constrain
linearly; for any other bearing stage A degrades to advisory scoring and says so.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from services.solver.geometry import (
    Polygon,
    Pt,
    bbox,
    dedupe_collinear,
    ensure_ccw,
    point_in_polygon,
    zone_for_point,
)
from services.solver.types import COARSE_MODULE_MM

#: MVP envelope shapes are rect/L/T — a union of at most this many rectangles (§5.2).
MAX_ENVELOPE_RECTS = 3

#: Grid directions (plot-local, +Y = drawing up) → unit vectors. Compass conversion
#: lives in :func:`grid_side_to_compass` / :func:`compass_to_grid_side`.
GRID_SIDES: tuple[str, ...] = ("E", "N", "W", "S")
_SIDE_VECTORS: dict[str, tuple[int, int]] = {
    "E": (1, 0),
    "N": (0, 1),
    "W": (-1, 0),
    "S": (0, -1),
}
_VECTOR_SIDES: dict[tuple[int, int], str] = {v: k for k, v in _SIDE_VECTORS.items()}


class GridError(ValueError):
    """An envelope this grid cannot honestly represent. Typed, never silent."""

    def __init__(self, code: str, message: str, *, detail: str | None = None) -> None:
        super().__init__("%s — %s" % (code, message))
        self.code = code
        self.message = message
        self.detail = detail


@dataclass(frozen=True)
class CellRect:
    """Half-open cell rectangle ``[col1, col2) × [row1, row2)``."""

    col1: int
    row1: int
    col2: int
    row2: int

    def __post_init__(self) -> None:
        if self.col2 <= self.col1 or self.row2 <= self.row1:
            raise GridError(
                "DEGENERATE_RECT",
                "A cell rectangle must span at least one cell.",
                detail=repr((self.col1, self.row1, self.col2, self.row2)),
            )

    @property
    def cols(self) -> int:
        return self.col2 - self.col1

    @property
    def rows(self) -> int:
        return self.row2 - self.row1

    @property
    def cell_count(self) -> int:
        return self.cols * self.rows

    def contains_cell(self, col: int, row: int) -> bool:
        return self.col1 <= col < self.col2 and self.row1 <= row < self.row2


@dataclass(frozen=True)
class Grid:
    """The §5.2 solve grid: cells, buildable rects, and mandatory-void rects.

    ``rects`` union == the buildable cells (≤ :data:`MAX_ENVELOPE_RECTS` of them);
    ``voids`` are the complement within the bbox — the "mandatory-void cells" §5.2
    says handle L and T plots. ``rects`` and ``voids`` partition ``cols × rows``.
    """

    origin: Pt
    module_mm: int
    cols: int
    rows: int
    mask: tuple[tuple[bool, ...], ...]
    rects: tuple[CellRect, ...]
    voids: tuple[CellRect, ...]

    # -- transforms (exact integers, both directions) -----------------------
    def cell_to_mm(self, col: int, row: int) -> Pt:
        """Minimum (SW) corner of the cell, in plot mm."""
        return (self.origin[0] + col * self.module_mm, self.origin[1] + row * self.module_mm)

    def cell_centre_mm(self, col: int, row: int) -> Pt:
        half = self.module_mm // 2
        x, y = self.cell_to_mm(col, row)
        return (x + half, y + half)

    def rect_to_mm(self, rect: CellRect) -> tuple[int, int, int, int]:
        x1, y1 = self.cell_to_mm(rect.col1, rect.row1)
        x2, y2 = self.cell_to_mm(rect.col2, rect.row2)
        return (x1, y1, x2, y2)

    def mm_to_cell(self, x_mm: int, y_mm: int) -> tuple[int, int]:
        """The cell whose half-open extent contains the point. Floor division —
        a point exactly on a module line belongs to the higher cell, matching the
        half-open rect convention."""
        return (
            (x_mm - self.origin[0]) // self.module_mm,
            (y_mm - self.origin[1]) // self.module_mm,
        )

    def is_buildable(self, col: int, row: int) -> bool:
        if 0 <= row < self.rows and 0 <= col < self.cols:
            return self.mask[row][col]
        return False

    def buildable_cell_count(self) -> int:
        return sum(1 for row in self.mask for cell in row if cell)

    def bbox_mm(self) -> tuple[int, int, int, int]:
        return (
            self.origin[0],
            self.origin[1],
            self.origin[0] + self.cols * self.module_mm,
            self.origin[1] + self.rows * self.module_mm,
        )


# ---------------------------------------------------------------------------
# building the grid
# ---------------------------------------------------------------------------


def is_rectilinear(polygon: Polygon) -> bool:
    """Every edge axis-aligned. The MVP envelope contract (§5.2 rect/L/T)."""
    count = len(polygon)
    if count < 4:
        return False
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if x1 != x2 and y1 != y2:
            return False
    return True


def build_grid(polygon: Polygon, *, module_mm: int = COARSE_MODULE_MM) -> Grid:
    """Overlay the coarse grid on a rect/L/T envelope polygon.

    Raises :class:`GridError` (typed codes, §15-honest copy) rather than returning a
    grid that misrepresents the envelope:

    * ``NOT_RECTILINEAR`` — a slanted boundary edge (not an MVP envelope);
    * ``TOO_SMALL`` — the envelope holds no whole cell;
    * ``UNSUPPORTED_SHAPE`` — buildable cells are not a union of ≤3 rects (not rect/L/T).
    """
    if module_mm <= 0:
        raise ValueError("module_mm must be positive, got %d" % module_mm)
    ring = dedupe_collinear(ensure_ccw(polygon))
    if len(ring) < 3:
        raise GridError("DEGENERATE", "The envelope isn't a closed shape.")
    if not is_rectilinear(ring):
        raise GridError(
            "NOT_RECTILINEAR",
            "This envelope has a slanted edge; the solver handles rect, L and T plots.",
            detail="vertices: %r" % (ring[:8],),
        )

    min_x, min_y, max_x, max_y = bbox(ring)
    cols = (max_x - min_x) // module_mm
    rows = (max_y - min_y) // module_mm
    if cols < 1 or rows < 1:
        raise GridError(
            "TOO_SMALL",
            "After setbacks the buildable area is smaller than one %dmm module." % module_mm,
            detail="bbox %dx%d mm" % (max_x - min_x, max_y - min_y),
        )

    half = module_mm // 2
    mask_rows: list[tuple[bool, ...]] = []
    for row in range(rows):
        y1 = min_y + row * module_mm
        cells: list[bool] = []
        for col in range(cols):
            x1 = min_x + col * module_mm
            probes = (
                (x1, y1),
                (x1 + module_mm, y1),
                (x1 + module_mm, y1 + module_mm),
                (x1, y1 + module_mm),
                (x1 + half, y1 + half),
            )
            cells.append(all(point_in_polygon(p, ring) for p in probes))
        mask_rows.append(tuple(cells))
    mask = tuple(mask_rows)

    if not any(cell for row_cells in mask for cell in row_cells):
        raise GridError(
            "TOO_SMALL",
            "No whole %dmm cell fits inside this envelope." % module_mm,
        )

    rects = buildable_rects_of_mask(mask)
    voids = void_rects_of_mask(mask)
    return Grid(
        origin=(min_x, min_y),
        module_mm=module_mm,
        cols=cols,
        rows=rows,
        mask=mask,
        rects=rects,
        voids=voids,
    )


def _runs(cells: Sequence[bool], *, value: bool) -> list[tuple[int, int]]:
    """Half-open runs of ``value`` in a row of cells."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, cell in enumerate(cells):
        if cell == value and start is None:
            start = index
        elif cell != value and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(cells)))
    return runs


def _bands(mask: Sequence[Sequence[bool]]) -> list[CellRect] | None:
    """Merge rows into rect bands. ``None`` when a row has 2+ buildable runs or
    the non-empty rows are not contiguous (not representable this way)."""
    bands: list[CellRect] = []
    previous_run: tuple[int, int] | None = None
    seen_any = False
    for row, cells in enumerate(mask):
        runs = _runs(cells, value=True)
        if len(runs) > 1:
            return None
        if not runs:
            if seen_any and any(any(r) for r in mask[row:]):
                return None  # a void row splitting the shape: not rect/L/T
            previous_run = None
            continue
        seen_any = True
        run = runs[0]
        if previous_run == run and bands:
            last = bands[-1]
            bands[-1] = CellRect(last.col1, last.row1, last.col2, row + 1)
        else:
            bands.append(CellRect(run[0], row, run[1], row + 1))
            previous_run = run
    return bands


def _transpose(mask: Sequence[Sequence[bool]]) -> tuple[tuple[bool, ...], ...]:
    rows = len(mask)
    cols = len(mask[0]) if rows else 0
    return tuple(tuple(mask[r][c] for r in range(rows)) for c in range(cols))


def buildable_rects_of_mask(mask: Sequence[Sequence[bool]]) -> tuple[CellRect, ...]:
    """Decompose the buildable cells into ≤3 rects (row bands, else column bands).

    Rect → 1, L → 2, T → 2 or 3, depending on orientation; anything needing more is
    outside the MVP envelope contract and raises ``UNSUPPORTED_SHAPE``.
    """
    row_bands = _bands(mask)
    transposed = _bands(_transpose(mask))
    col_bands: list[CellRect] | None = None
    if transposed is not None:
        col_bands = [CellRect(b.row1, b.col1, b.row2, b.col2) for b in transposed]

    best: list[CellRect] | None = None
    for candidate in (row_bands, col_bands):
        if candidate is not None and (best is None or len(candidate) < len(best)):
            best = candidate
    if best is None or len(best) > MAX_ENVELOPE_RECTS:
        raise GridError(
            "UNSUPPORTED_SHAPE",
            "This envelope isn't a rectangle, L or T; the solver handles those three.",
            detail=("buildable cells need %s rects" % ("?" if best is None else str(len(best)))),
        )
    return tuple(best)


def void_rects_of_mask(mask: Sequence[Sequence[bool]]) -> tuple[CellRect, ...]:
    """The mandatory-void cells (§5.2) as merged rects — the mask's complement.

    Works on ANY mask (including :class:`services.solver.stages.GridSpec` masks), so
    §5.7's obstacle-transformed grids reuse it unchanged.
    """
    voids: list[CellRect] = []
    open_bands: dict[tuple[int, int], CellRect] = {}
    for row, cells in enumerate(mask):
        current: dict[tuple[int, int], CellRect] = {}
        for run in _runs(cells, value=False):
            previous = open_bands.get(run)
            if previous is not None and previous.row2 == row:
                current[run] = CellRect(previous.col1, previous.row1, previous.col2, row + 1)
            else:
                current[run] = CellRect(run[0], row, run[1], row + 1)
        for run, band in open_bands.items():
            if run not in current:
                voids.append(band)
        open_bands = current
    voids.extend(open_bands.values())
    voids.sort(key=lambda r: (r.row1, r.col1, r.row2, r.col2))
    return tuple(voids)


# ---------------------------------------------------------------------------
# 3×3 plot zones, oriented to true north
# ---------------------------------------------------------------------------


def bbox_thirds(plot_bbox: tuple[int, int, int, int]) -> tuple[tuple[int, int, int, int], ...]:
    """The nine axis-aligned thirds of the plot bbox, row-major from the SW corner.

    Integer boundaries at ``min + extent * k // 3`` — exact, and consistent between
    calls because there is no float in sight.
    """
    min_x, min_y, max_x, max_y = plot_bbox
    width = max_x - min_x
    height = max_y - min_y
    xs = (min_x, min_x + width // 3, min_x + (2 * width) // 3, max_x)
    ys = (min_y, min_y + height // 3, min_y + (2 * height) // 3, max_y)
    out: list[tuple[int, int, int, int]] = []
    for row in range(3):
        for col in range(3):
            out.append((xs[col], ys[row], xs[col + 1], ys[row + 1]))
    return tuple(out)


def zone_bands_mm(
    plot_bbox: tuple[int, int, int, int], north_deg: int
) -> dict[str, tuple[int, int, int, int]] | None:
    """Zone name → axis-aligned mm band, or ``None`` when north is not cardinal.

    Each of the nine bbox thirds is labelled by running its centre through the ONE
    zone implementation (:func:`zone_for_point`), so the labels cannot drift from
    what the critic and the Vastu pack will later score. For ``north_deg % 90 != 0``
    the zones are rotated squares, not bands — stage A then skips the linear
    constraints and scores Vastu in the critic instead (documented degradation).
    """
    if north_deg % 90 != 0:
        return None
    bands: dict[str, tuple[int, int, int, int]] = {}
    for rect in bbox_thirds(plot_bbox):
        centre = ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
        bands[zone_for_point(centre, plot_bbox, north_deg)] = rect
    if len(bands) != 9:
        # Degenerate bbox (zero-width third). Refuse rather than mislabel.
        return None
    return bands


def zone_of_cell(
    grid: Grid, col: int, row: int, plot_bbox: tuple[int, int, int, int], north_deg: int
) -> str:
    """Which zone a cell's centre falls in. Thin, so nothing re-derives zones."""
    return zone_for_point(grid.cell_centre_mm(col, row), plot_bbox, north_deg)


def cells_by_zone(
    grid: Grid, plot_bbox: tuple[int, int, int, int], north_deg: int
) -> dict[str, tuple[tuple[int, int], ...]]:
    """Buildable cells grouped by zone — the advisory-mode scoring input."""
    out: dict[str, list[tuple[int, int]]] = {}
    for row in range(grid.rows):
        for col in range(grid.cols):
            if grid.mask[row][col]:
                out.setdefault(zone_of_cell(grid, col, row, plot_bbox, north_deg), []).append(
                    (col, row)
                )
    return {zone: tuple(cells) for zone, cells in out.items()}


# ---------------------------------------------------------------------------
# grid-side ↔ compass (cardinal north only)
# ---------------------------------------------------------------------------


def _rotate_quarter(vector: tuple[int, int], quarters: int) -> tuple[int, int]:
    x, y = vector
    for _ in range(quarters % 4):
        x, y = -y, x
    return (x, y)


def grid_side_to_compass(side: str, north_deg: int) -> str | None:
    """Plot-local grid side ('N' = +Y) → true-compass side, cardinal norths only.

    Matches :func:`zone_for_point`'s rotation convention exactly (its own test pins
    that): ``north_deg`` rotates true north clockwise from +Y, so the +X direction
    is true north when ``north_deg == 90``.
    """
    if north_deg % 90 != 0:
        return None
    if side not in _SIDE_VECTORS:
        raise ValueError("unknown grid side %r" % side)
    return _VECTOR_SIDES[_rotate_quarter(_SIDE_VECTORS[side], north_deg // 90)]


def compass_to_grid_side(compass: str, north_deg: int) -> str | None:
    """True-compass side → plot-local grid side. Inverse of :func:`grid_side_to_compass`."""
    if north_deg % 90 != 0:
        return None
    if compass not in _SIDE_VECTORS:
        raise ValueError("unknown compass side %r" % compass)
    return _VECTOR_SIDES[_rotate_quarter(_SIDE_VECTORS[compass], -(north_deg // 90))]


__all__ = [
    "GRID_SIDES",
    "MAX_ENVELOPE_RECTS",
    "CellRect",
    "Grid",
    "GridError",
    "bbox_thirds",
    "build_grid",
    "buildable_rects_of_mask",
    "cells_by_zone",
    "compass_to_grid_side",
    "grid_side_to_compass",
    "is_rectilinear",
    "void_rects_of_mask",
    "zone_bands_mm",
    "zone_of_cell",
]
