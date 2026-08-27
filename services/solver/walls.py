"""§5.3 step 1 — cell layout → deduped, buildable wall network. **ortools-free.**

    "Snap all coordinates to 115mm module; convert cell layout → wall network
     (dedupe shared walls: two rooms sharing an edge get ONE wall, 115mm internal /
     230mm external)" — engineering playbook §5.3.

This module is pure integer geometry so that every line of it is provable on a
machine with nothing but a Python interpreter (the CP-SAT stages cannot be).

THE COORDINATE CONVENTIONS (load-bearing, read before touching):

* A :class:`CellLayout` holds **layout rectangles**: the room's extent measured to
  the *shared layout line* between rooms, and to the *building outer face* on the
  footprint boundary. Adjacent rooms therefore share edge coordinates exactly,
  which is what makes wall dedupe a set operation instead of a tolerance hunt.
* Every layout coordinate is snapped to the 115mm brick module relative to
  ``snap_origin`` (the envelope bbox minimum, so the module grid is anchored to
  the plot, not to absolute zero).
* **Internal walls** (115mm) sit centred on the shared layout line: centreline ==
  the line, which keeps centrelines integer. 115 is odd, so the two clear faces
  cannot both be 57.5mm away; the split is fixed asymmetrically and forever:
  the room on the LOW-coordinate side keeps a clear face ``line - 57``, the room
  on the HIGH side ``line + 58``. The wall band is exactly 115 and dimension
  chains sum exactly: ``clear spans + wall bands == overall extent`` with zero
  drift. The 1mm skew lives inside the plaster, not in the chains.
* **External walls** (230mm) keep their OUTER face on the footprint boundary —
  the boundary is what stage A packed inside the buildable envelope, so poking
  the wall outward would eat the setbacks. Centreline = boundary offset 115
  inward (integer, since 230 is even); clear face 230 inward.
* External wall centrelines are the footprint outline offset inward by 115 with
  mitred corners, so corner junctions meet at a point instead of overlapping.
  Internal walls that reach the outline are trimmed 115 so they stop at the
  external centreline — touching, never collinear-overlapping, which is exactly
  the ``WALL_DUPLICATE`` invariant of ``garh_model.validate``.

Failure is typed, never silent: anything the layout does that cannot become a
buildable wall network raises :class:`WallSynthesisError` with a stable ``code``
(the worker logs these — §15's generation theater shows *real* discard reasons).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from services.solver.geometry import (
    Polygon,
    Pt,
    area_mm2,
    dedupe_collinear,
    ensure_ccw,
    round_half_away,
)
from services.solver.types import (
    EXTERNAL_WALL_MM,
    FINE_MODULE_MM,
    INTERNAL_WALL_MM,
    RoomPlacement,
)

#: Clear-face inset from an internal layout line, LOW-coordinate side (max edge
#: of the lower room). 115 = 57 + 58; see the module docstring.
INSET_INTERNAL_LOW = 57
#: Clear-face inset from an internal layout line, HIGH-coordinate side.
INSET_INTERNAL_HIGH = 58
#: Clear-face inset from a footprint boundary (full external wall thickness).
INSET_EXTERNAL = EXTERNAL_WALL_MM

#: Compass name of an axis-aligned outward normal, +Y = plot north.
_OUTWARD_BY_NORMAL: dict[tuple[int, int], str] = {
    (0, 1): "N",
    (0, -1): "S",
    (1, 0): "E",
    (-1, 0): "W",
}


class WallSynthesisError(ValueError):
    """A cell layout that cannot become a buildable wall network.

    ``code`` is a stable machine-readable discard reason (§15 honest generation
    theater); ``message`` is one plain sentence for the job log.
    """

    def __init__(self, code: str, message: str, *, detail: str | None = None) -> None:
        super().__init__("%s — %s" % (code, message))
        self.code = code
        self.message = message
        self.detail = detail


def snap_mm(value: int, *, origin: int = 0, module_mm: int = FINE_MODULE_MM) -> int:
    """Snap ``value`` to the nearest module line anchored at ``origin``.

    Round-half-away, matching the model core's rounding contract, so the same
    coordinate snapped in TypeScript and Python lands on the same line.
    """
    if module_mm <= 0:
        raise ValueError("module_mm must be positive, got %d" % module_mm)
    steps = round_half_away((value - origin) / module_mm)
    return origin + steps * module_mm


@dataclass(frozen=True)
class RoomRect:
    """One room as a snapped layout rectangle (see module docstring)."""

    key: str
    room_type: str
    x1: int
    y1: int
    x2: int
    y2: int
    #: Preserved id for §5.7 locked rooms; ``None`` mints a fresh one downstream.
    room_id: str | None = None

    @property
    def width_mm(self) -> int:
        return self.x2 - self.x1

    @property
    def depth_mm(self) -> int:
        return self.y2 - self.y1

    def contains_cell(self, x1: int, y1: int, x2: int, y2: int) -> bool:
        return self.x1 <= x1 and x2 <= self.x2 and self.y1 <= y1 and y2 <= self.y2


@dataclass(frozen=True)
class CellLayout:
    """One storey of stage-A output, normalised for refinement.

    CONTRACT WITH STAGE A: the rectangles must TILE the storey footprint —
    circulation is a room (``passage``/``lobby``/``foyer``), the stair well is a
    ``staircase`` room, shafts are ``shaft`` rooms. Unoccupied space inside the
    footprint would read as "outside" here and grow external walls around every
    room, which is then rejected as ``FOOTPRINT_SPLIT``.
    """

    storey_index: int
    rooms: tuple[RoomRect, ...]
    snap_origin: Pt = (0, 0)

    @classmethod
    def from_placements(
        cls,
        placements: Sequence[RoomPlacement],
        *,
        snap_origin: Pt = (0, 0),
    ) -> CellLayout:
        """Snap and normalise one storey's placements. Deterministic: rooms are
        sorted by key, so input order can never leak into wall ordering or ids.
        """
        if not placements:
            raise WallSynthesisError("EMPTY_STOREY", "A storey has no rooms at all.")
        storey_indices = {p.storey_index for p in placements}
        if len(storey_indices) != 1:
            raise WallSynthesisError(
                "MIXED_STOREYS",
                "CellLayout.from_placements takes one storey at a time.",
                detail="storey indices %s" % sorted(storey_indices),
            )
        ox, oy = snap_origin
        rooms: list[RoomRect] = []
        seen: dict[str, bool] = {}
        for p in placements:
            if p.room_key in seen:
                raise WallSynthesisError(
                    "DUPLICATE_ROOM_KEY",
                    "Two placements share the key %r." % p.room_key,
                )
            seen[p.room_key] = True
            x1 = snap_mm(p.x_mm, origin=ox)
            y1 = snap_mm(p.y_mm, origin=oy)
            x2 = snap_mm(p.x_mm + p.width_mm, origin=ox)
            y2 = snap_mm(p.y_mm + p.depth_mm, origin=oy)
            if p.room_id is not None and (
                x1 != p.x_mm
                or y1 != p.y_mm
                or x2 != p.x_mm + p.width_mm
                or y2 != p.y_mm + p.depth_mm
            ):
                # §5.7: a locked room's geometry is exact. If snapping would move
                # it, the input was not module-aligned and honouring the lock is
                # impossible — refuse rather than shift a wall the user froze.
                raise WallSynthesisError(
                    "LOCKED_ROOM_MOVED",
                    "Locked room %r is not on the 115mm module; refusing to move it." % p.room_key,
                )
            if x2 - x1 < FINE_MODULE_MM or y2 - y1 < FINE_MODULE_MM:
                raise WallSynthesisError(
                    "DEGENERATE_ROOM",
                    "Room %r collapses below one 115mm module after snapping." % p.room_key,
                    detail="%dx%d mm" % (x2 - x1, y2 - y1),
                )
            rooms.append(
                RoomRect(
                    key=p.room_key,
                    room_type=p.room_type,
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                    room_id=p.room_id,
                )
            )
        rooms.sort(key=lambda r: r.key)
        return cls(
            storey_index=next(iter(storey_indices)), rooms=tuple(rooms), snap_origin=snap_origin
        )

    def room(self, key: str) -> RoomRect:
        for r in self.rooms:
            if r.key == key:
                return r
        raise KeyError(key)

    def room_at(self, x: int, y: int) -> RoomRect | None:
        """The room whose half-open cell ``[x1,x2) × [y1,y2)`` contains the point."""
        for r in self.rooms:
            if r.x1 <= x < r.x2 and r.y1 <= y < r.y2:
                return r
        return None


@dataclass(frozen=True)
class WallSpec:
    """One deduped wall, centreline in integer mm. Ids are minted downstream."""

    axis: str  # 'h' | 'v'
    a: Pt
    b: Pt
    thickness_mm: int
    kind: str  # 'external' | 'internal'
    #: The LAYOUT line this wall realises (== centreline for internal walls;
    #: the footprint boundary line for external walls, whose centreline is
    #: offset 115 inward).
    line_mm: int

    @property
    def length_mm(self) -> int:
        return abs(self.b[0] - self.a[0]) + abs(self.b[1] - self.a[1])


@dataclass(frozen=True)
class AdjacencySpan:
    """Two rooms sharing one wall over ``[lo, hi]`` (absolute mm along the axis)."""

    low_room: str  # room on the low-coordinate side of the line
    high_room: str
    wall_index: int
    lo: int
    hi: int


@dataclass(frozen=True)
class ExternalSpan:
    """One room's frontage on the footprint boundary over ``[lo, hi]``."""

    room_key: str
    wall_index: int
    lo: int
    hi: int
    outward: str  # 'N' | 'S' | 'E' | 'W'


@dataclass(frozen=True)
class WallNetwork:
    """The deduped wall set plus the adjacency facts openings placement needs."""

    layout: CellLayout
    #: Footprint outline, CCW, on the building OUTER face (== layout lines).
    outline: Polygon
    walls: tuple[WallSpec, ...]
    adjacencies: tuple[AdjacencySpan, ...]
    external_spans: tuple[ExternalSpan, ...]

    def wall(self, index: int) -> WallSpec:
        return self.walls[index]

    def adjacencies_of(self, room_key: str) -> tuple[AdjacencySpan, ...]:
        return tuple(s for s in self.adjacencies if room_key in (s.low_room, s.high_room))

    def external_spans_of(self, room_key: str) -> tuple[ExternalSpan, ...]:
        return tuple(s for s in self.external_spans if s.room_key == room_key)


# ---------------------------------------------------------------------------
# the cut grid
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Grid:
    xs: tuple[int, ...]
    ys: tuple[int, ...]
    #: occ[col][row] = index into layout.rooms, or -1 for outside.
    occ: tuple[tuple[int, ...], ...]


def _build_grid(layout: CellLayout) -> _Grid:
    xs = tuple(sorted({v for r in layout.rooms for v in (r.x1, r.x2)}))
    ys = tuple(sorted({v for r in layout.rooms for v in (r.y1, r.y2)}))
    occ: list[list[int]] = [[-1] * (len(ys) - 1) for _ in range(len(xs) - 1)]
    for index, room in enumerate(layout.rooms):
        for col in range(len(xs) - 1):
            if not (room.x1 <= xs[col] and xs[col + 1] <= room.x2):
                continue
            for row in range(len(ys) - 1):
                if not (room.y1 <= ys[row] and ys[row + 1] <= room.y2):
                    continue
                if occ[col][row] != -1:
                    raise WallSynthesisError(
                        "ROOM_OVERLAP",
                        "Rooms %r and %r overlap after snapping."
                        % (layout.rooms[occ[col][row]].key, room.key),
                    )
                occ[col][row] = index
    return _Grid(xs=xs, ys=ys, occ=tuple(tuple(col) for col in occ))


@dataclass(frozen=True)
class _Fragment:
    """One cut-grid fragment of a candidate wall line."""

    axis: str  # 'v': line x=line, span in y; 'h': line y=line, span in x
    line: int
    lo: int
    hi: int
    #: Room index on the low-coordinate side of the line (-1 = outside).
    low: int
    #: Room index on the high side.
    high: int


def _fragments(layout: CellLayout, grid: _Grid) -> list[_Fragment]:
    out: list[_Fragment] = []
    cols = len(grid.xs) - 1
    rows = len(grid.ys) - 1
    for i, x in enumerate(grid.xs):
        for j in range(rows):
            low = grid.occ[i - 1][j] if i > 0 else -1
            high = grid.occ[i][j] if i < cols else -1
            if low != high:
                out.append(
                    _Fragment(
                        axis="v", line=x, lo=grid.ys[j], hi=grid.ys[j + 1], low=low, high=high
                    )
                )
    for j, y in enumerate(grid.ys):
        for i in range(cols):
            low = grid.occ[i][j - 1] if j > 0 else -1
            high = grid.occ[i][j] if j < rows else -1
            if low != high:
                out.append(
                    _Fragment(
                        axis="h", line=y, lo=grid.xs[i], hi=grid.xs[i + 1], low=low, high=high
                    )
                )
    return out


# ---------------------------------------------------------------------------
# footprint outline
# ---------------------------------------------------------------------------


def _outline_ring(fragments: Sequence[_Fragment]) -> Polygon:
    """Chain the boundary fragments into ONE CCW ring, or refuse loudly."""
    # Directed so the interior is on the LEFT (CCW convention).
    segments: list[tuple[Pt, Pt]] = []
    for f in fragments:
        if f.low != -1 and f.high != -1:
            continue
        if f.axis == "v":
            if f.high != -1:  # interior east of the line -> walk south
                segments.append(((f.line, f.hi), (f.line, f.lo)))
            else:  # interior west -> walk north
                segments.append(((f.line, f.lo), (f.line, f.hi)))
        else:
            if f.high != -1:  # interior north -> walk east
                segments.append(((f.lo, f.line), (f.hi, f.line)))
            else:  # interior south -> walk west
                segments.append(((f.hi, f.line), (f.lo, f.line)))
    if not segments:
        raise WallSynthesisError("EMPTY_STOREY", "No boundary found — no rooms?")

    by_start: dict[Pt, list[tuple[Pt, Pt]]] = {}
    for seg in segments:
        by_start.setdefault(seg[0], []).append(seg)
    for start, outgoing in by_start.items():
        if len(outgoing) > 1:
            raise WallSynthesisError(
                "FOOTPRINT_PINCH",
                "The footprint touches itself at a single point.",
                detail="pinch at %s" % (start,),
            )

    start = min(seg[0] for seg in segments)
    ring: list[Pt] = [start]
    cursor = start
    used = 0
    while True:
        outgoing = by_start.get(cursor)
        if not outgoing:
            raise WallSynthesisError(
                "FOOTPRINT_SPLIT",
                "The rooms do not close into one footprint.",
                detail="dangling boundary at %s" % (cursor,),
            )
        seg = outgoing[0]
        used += 1
        cursor = seg[1]
        if cursor == start:
            break
        ring.append(cursor)
        if used > len(segments):
            raise WallSynthesisError("FOOTPRINT_SPLIT", "Boundary chaining did not close.")
    if used != len(segments):
        # A second ring exists: either an island of rooms or a courtyard hole.
        raise WallSynthesisError(
            "FOOTPRINT_SPLIT",
            "The rooms form more than one footprint (island or courtyard).",
            detail="%d of %d boundary fragments in the outer ring" % (used, len(segments)),
        )
    outline = dedupe_collinear(tuple(ring))
    return ensure_ccw(outline)


def _inset_ring(outline: Polygon, inset_mm: int) -> Polygon:
    """Inward offset of an axis-aligned CCW ring, mitred corners, exact ints."""
    count = len(outline)
    lines: list[tuple[str, int]] = []  # per edge: ('v', x') or ('h', y')
    for i in range(count):
        ax, ay = outline[i]
        bx, by = outline[(i + 1) % count]
        if ax == bx and ay != by:
            direction = 1 if by > ay else -1
            # CCW: interior is left of travel; left of (0,±1) is (∓1,0).
            lines.append(("v", ax - direction * inset_mm))
        elif ay == by and ax != bx:
            direction = 1 if bx > ax else -1
            # left of (±1,0) is (0,±1)
            lines.append(("h", ay + direction * inset_mm))
        else:
            raise WallSynthesisError(
                "FOOTPRINT_NOT_ORTHOGONAL",
                "The footprint outline has a non-axis-aligned edge.",
                detail="edge %s -> %s" % ((ax, ay), (bx, by)),
            )
    vertices: list[Pt] = []
    for i in range(count):
        prev_axis, prev_coord = lines[i - 1]
        cur_axis, cur_coord = lines[i]
        if prev_axis == cur_axis:
            raise WallSynthesisError(
                "FOOTPRINT_NOT_ORTHOGONAL",
                "Consecutive outline edges are parallel after dedupe (degenerate corner).",
            )
        x = prev_coord if prev_axis == "v" else cur_coord
        y = prev_coord if prev_axis == "h" else cur_coord
        vertices.append((x, y))
    ring = dedupe_collinear(tuple(vertices))
    if len(ring) < 4 or area_mm2(ring) <= 0:
        raise WallSynthesisError(
            "FOOTPRINT_TOO_THIN",
            "The footprint is thinner than two external walls somewhere.",
        )
    return ring


def _point_on_ring(point: Pt, outline: Polygon) -> bool:
    count = len(outline)
    px, py = point
    for i in range(count):
        ax, ay = outline[i]
        bx, by = outline[(i + 1) % count]
        if ax == bx:
            if px == ax and min(ay, by) <= py <= max(ay, by):
                return True
        else:
            if py == ay and min(ax, bx) <= px <= max(ax, bx):
                return True
    return False


# ---------------------------------------------------------------------------
# the network
# ---------------------------------------------------------------------------


def build_wall_network(layout: CellLayout) -> WallNetwork:
    """Cell layout → the §5.3 wall network. Deterministic: same layout, same output.

    Wall ordering (stable, documented, tests rely on it):

    1. external walls in outline-ring order, starting at the ring's
       lexicographically smallest vertex;
    2. internal walls sorted by (axis 'h' before 'v', line coordinate, span start).
    """
    grid = _build_grid(layout)
    fragments = _fragments(layout, grid)
    outline = _outline_ring(fragments)
    centre_ring = _inset_ring(outline, INTERNAL_WALL_MM)  # 115 inward

    walls: list[WallSpec] = []
    adjacencies: list[AdjacencySpan] = []
    external_spans: list[ExternalSpan] = []

    # ---- external walls: one per outline edge, centreline on the inset ring.
    # outline and centre_ring are index-aligned only if dedupe removed the same
    # vertices from both; recompute the pairing from the outline directly.
    count = len(outline)
    ring2 = _inset_ring(outline, INTERNAL_WALL_MM)
    if len(ring2) != count:
        raise WallSynthesisError(
            "FOOTPRINT_TOO_THIN",
            "Inward offset collapsed part of the footprint outline.",
            detail="%d outline edges vs %d centreline edges" % (count, len(ring2)),
        )
    del centre_ring

    # Rotate both rings to start at the outline's smallest vertex for stable order.
    start_index = min(range(count), key=lambda i: outline[i])
    outline = tuple(outline[(start_index + i) % count] for i in range(count))
    ring2 = tuple(ring2[(start_index + i) % count] for i in range(count))

    wall_index_by_edge: dict[tuple[str, int], int] = {}
    for i in range(count):
        oa, ob = outline[i], outline[(i + 1) % count]
        ca, cb = ring2[i], ring2[(i + 1) % count]
        if oa[0] == ob[0]:
            axis, line = "v", oa[0]
        else:
            axis, line = "h", oa[1]
        walls.append(
            WallSpec(
                axis=axis,
                a=ca,
                b=cb,
                thickness_mm=EXTERNAL_WALL_MM,
                kind="external",
                line_mm=line,
            )
        )
        wall_index_by_edge[_edge_key(axis, line, oa, ob)] = len(walls) - 1

    # ---- boundary fragments → per-room external frontage records.
    for f in fragments:
        if (f.low == -1) == (f.high == -1):
            continue
        room_index = f.high if f.low == -1 else f.low
        if f.axis == "v":
            normal = (-1, 0) if f.low == -1 else (1, 0)
        else:
            normal = (0, -1) if f.low == -1 else (0, 1)
        wall_idx = _find_edge_wall(wall_index_by_edge, walls, f)
        external_spans.append(
            ExternalSpan(
                room_key=layout.rooms[room_index].key,
                wall_index=wall_idx,
                lo=f.lo,
                hi=f.hi,
                outward=_OUTWARD_BY_NORMAL[normal],
            )
        )

    # ---- internal walls: maximal same-line runs of shared fragments.
    internal = [f for f in fragments if f.low != -1 and f.high != -1]
    internal.sort(key=lambda f: (f.axis, f.line, f.lo))
    runs: list[list[_Fragment]] = []
    for f in internal:
        if runs and _continues_run(runs[-1][-1], f):
            runs[-1].append(f)
        else:
            runs.append([f])
    # 'h' sorts before 'v' — matches the documented ordering above.
    runs.sort(key=lambda run: (run[0].axis, run[0].line, run[0].lo))

    for run in runs:
        axis, line = run[0].axis, run[0].line
        lo, hi = run[0].lo, run[-1].hi
        trimmed_lo, trimmed_hi = lo, hi
        lo_point = (line, lo) if axis == "v" else (lo, line)
        hi_point = (line, hi) if axis == "v" else (hi, line)
        if _point_on_ring(lo_point, outline):
            trimmed_lo += INTERNAL_WALL_MM
        if _point_on_ring(hi_point, outline):
            trimmed_hi -= INTERNAL_WALL_MM
        if trimmed_hi <= trimmed_lo:
            raise WallSynthesisError(
                "SLIVER_WALL",
                "An internal wall between rooms is shorter than the walls it meets.",
                detail="axis %s line %d span [%d, %d]" % (axis, line, lo, hi),
            )
        if axis == "v":
            a: Pt = (line, trimmed_lo)
            b: Pt = (line, trimmed_hi)
        else:
            a = (trimmed_lo, line)
            b = (trimmed_hi, line)
        walls.append(
            WallSpec(
                axis=axis,
                a=a,
                b=b,
                thickness_mm=INTERNAL_WALL_MM,
                kind="internal",
                line_mm=line,
            )
        )
        wall_idx = len(walls) - 1
        for f in run:
            span_lo = max(f.lo, trimmed_lo)
            span_hi = min(f.hi, trimmed_hi)
            if span_hi <= span_lo:
                continue
            adjacencies.append(
                AdjacencySpan(
                    low_room=layout.rooms[f.low].key,
                    high_room=layout.rooms[f.high].key,
                    wall_index=wall_idx,
                    lo=span_lo,
                    hi=span_hi,
                )
            )

    merged = _merge_adjacent_spans(adjacencies)
    return WallNetwork(
        layout=layout,
        outline=outline,
        walls=tuple(walls),
        adjacencies=tuple(merged),
        external_spans=tuple(_merge_external_spans(external_spans)),
    )


def _edge_key(axis: str, line: int, a: Pt, b: Pt) -> tuple[str, int]:
    return (axis, line)


def _find_edge_wall(
    wall_index_by_edge: dict[tuple[str, int], int],
    walls: Sequence[WallSpec],
    f: _Fragment,
) -> int:
    """The external wall realising a boundary fragment.

    Two outline edges can share (axis, line) on an L/T plot (e.g. two south-facing
    runs at the same y), so the dict is only a fast path; on a miss-by-span we
    scan for the external wall on that line whose extent covers the fragment.
    """
    candidates = [
        (i, w)
        for i, w in enumerate(walls)
        if w.kind == "external" and w.axis == f.axis and w.line_mm == f.line
    ]
    if len(candidates) == 1:
        return candidates[0][0]
    for i, w in candidates:
        w_lo, w_hi = _span_of(w)
        # The centreline is trimmed 115 at mitred corners; a boundary fragment
        # belongs to this wall if the spans overlap at all.
        if min(f.hi, w_hi + INTERNAL_WALL_MM) - max(f.lo, w_lo - INTERNAL_WALL_MM) > 0:
            return i
    raise WallSynthesisError(
        "FOOTPRINT_SPLIT",
        "A boundary fragment matched no external wall.",
        detail="axis %s line %d span [%d, %d]" % (f.axis, f.line, f.lo, f.hi),
    )


def _span_of(wall: WallSpec) -> tuple[int, int]:
    if wall.axis == "v":
        lo, hi = wall.a[1], wall.b[1]
    else:
        lo, hi = wall.a[0], wall.b[0]
    return (min(lo, hi), max(lo, hi))


def _continues_run(prev: _Fragment, nxt: _Fragment) -> bool:
    return prev.axis == nxt.axis and prev.line == nxt.line and prev.hi == nxt.lo


def _merge_adjacent_spans(spans: Sequence[AdjacencySpan]) -> list[AdjacencySpan]:
    out: list[AdjacencySpan] = []
    for s in sorted(spans, key=lambda s: (s.wall_index, s.lo)):
        if (
            out
            and out[-1].wall_index == s.wall_index
            and out[-1].hi == s.lo
            and out[-1].low_room == s.low_room
            and out[-1].high_room == s.high_room
        ):
            out[-1] = AdjacencySpan(
                low_room=s.low_room,
                high_room=s.high_room,
                wall_index=s.wall_index,
                lo=out[-1].lo,
                hi=s.hi,
            )
        else:
            out.append(s)
    return out


def _merge_external_spans(spans: Sequence[ExternalSpan]) -> list[ExternalSpan]:
    out: list[ExternalSpan] = []
    for s in sorted(spans, key=lambda s: (s.wall_index, s.lo, s.room_key)):
        if (
            out
            and out[-1].wall_index == s.wall_index
            and out[-1].hi == s.lo
            and out[-1].room_key == s.room_key
            and out[-1].outward == s.outward
        ):
            out[-1] = ExternalSpan(
                room_key=s.room_key,
                wall_index=s.wall_index,
                lo=out[-1].lo,
                hi=s.hi,
                outward=s.outward,
            )
        else:
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# clear polygons
# ---------------------------------------------------------------------------


def clear_polygon(layout: CellLayout, network: WallNetwork, room_key: str) -> Polygon:
    """The room's clear (inside-face) polygon, CCW, exact integer mm.

    Insets per side fragment: 230 where the fragment lies on the footprint
    boundary, else the fixed 57/58 internal split (top/right sides of the room
    are the LOW side of their shared line, so they take 57; bottom/left take 58).
    A side that is part external, part internal yields a rectilinear polygon —
    supported, not approximated.
    """
    room = layout.room(room_key)
    grid = _build_grid(layout)
    room_index = layout.rooms.index(room)

    # Directed CCW boundary of the rect, fragment by fragment:
    # bottom (W->E), right (S->N), top (E->W), left (N->S).
    def classify(axis: str, line: int, lo: int, hi: int, *, room_is_high: bool) -> int:
        f_low, f_high = _sides_of(grid, axis, line, lo)
        other = f_low if room_is_high else f_high
        if other == -1:
            return INSET_EXTERNAL
        return INSET_INTERNAL_HIGH if room_is_high else INSET_INTERNAL_LOW

    def side_fragments(
        axis: str, line: int, lo: int, hi: int, *, room_is_high: bool
    ) -> list[tuple[str, int, int, int, int]]:
        cuts = [c for c in (grid.xs if axis == "h" else grid.ys) if lo < c < hi]
        bounds = [lo, *cuts, hi]
        frags: list[tuple[str, int, int, int, int]] = []
        for i in range(len(bounds) - 1):
            inset = classify(axis, line, bounds[i], bounds[i + 1], room_is_high=room_is_high)
            if frags and frags[-1][4] == inset:
                prev = frags[-1]
                frags[-1] = (axis, line, prev[2], bounds[i + 1], inset)
            else:
                frags.append((axis, line, bounds[i], bounds[i + 1], inset))
        return frags

    bottom = side_fragments("h", room.y1, room.x1, room.x2, room_is_high=True)
    right = side_fragments("v", room.x2, room.y1, room.y2, room_is_high=False)
    top = side_fragments("h", room.y2, room.x1, room.x2, room_is_high=False)
    left = side_fragments("v", room.x1, room.y1, room.y2, room_is_high=True)
    del room_index

    ordered: list[tuple[str, int, int, int, int, int]] = []  # + direction
    for frag in bottom:
        ordered.append((*frag, 1))
    for frag in right:
        ordered.append((*frag, 1))
    for frag in reversed(top):
        ordered.append((*frag, -1))
    for frag in reversed(left):
        ordered.append((*frag, -1))

    def displaced(frag: tuple[str, int, int, int, int, int]) -> tuple[str, int]:
        axis, line, lo, hi, inset, direction = frag
        if axis == "h":
            return ("h", line + inset if line == room.y1 else line - inset)
        return ("v", line + inset if line == room.x1 else line - inset)

    vertices: list[Pt] = []
    total = len(ordered)
    for i in range(total):
        cur = ordered[i]
        nxt = ordered[(i + 1) % total]
        cur_axis, cur_coord = displaced(cur)
        nxt_axis, nxt_coord = displaced(nxt)
        if cur_axis != nxt_axis:
            x = cur_coord if cur_axis == "v" else nxt_coord
            y = cur_coord if cur_axis == "h" else nxt_coord
            vertices.append((x, y))
        else:
            junction = cur[3] if cur[5] == 1 else cur[2]
            if cur_axis == "h":
                vertices.append((junction, cur_coord))
                vertices.append((junction, nxt_coord))
            else:
                vertices.append((cur_coord, junction))
                vertices.append((cur_coord, nxt_coord))
    ring = dedupe_collinear(tuple(vertices))
    if len(ring) < 4 or area_mm2(ring) <= 0:
        raise WallSynthesisError(
            "DEGENERATE_ROOM",
            "Room %r has no clear area once its walls are taken out." % room_key,
        )
    return ensure_ccw(ring)


def _sides_of(grid: _Grid, axis: str, line: int, lo: int) -> tuple[int, int]:
    """(low-side, high-side) occupants of the fragment starting at ``lo``."""
    if axis == "v":
        cols = len(grid.xs) - 1
        i = grid.xs.index(line)
        j = _interval_index(grid.ys, lo)
        low = grid.occ[i - 1][j] if i > 0 else -1
        high = grid.occ[i][j] if i < cols else -1
    else:
        rows = len(grid.ys) - 1
        j = grid.ys.index(line)
        i = _interval_index(grid.xs, lo)
        low = grid.occ[i][j - 1] if j > 0 else -1
        high = grid.occ[i][j] if j < rows else -1
    return (low, high)


def _interval_index(cuts: tuple[int, ...], lo: int) -> int:
    for i in range(len(cuts) - 1):
        if cuts[i] <= lo < cuts[i + 1]:
            return i
    raise WallSynthesisError("FOOTPRINT_SPLIT", "Fragment outside the cut grid.")


__all__ = [
    "INSET_EXTERNAL",
    "INSET_INTERNAL_HIGH",
    "INSET_INTERNAL_LOW",
    "AdjacencySpan",
    "CellLayout",
    "ExternalSpan",
    "RoomRect",
    "WallNetwork",
    "WallSpec",
    "WallSynthesisError",
    "build_wall_network",
    "clear_polygon",
    "snap_mm",
]
