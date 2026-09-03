"""§5.2 stage A — CP-SAT room topology on the 300mm module.

``ortools`` is imported **lazily inside functions**: this module imports cleanly on a
machine without it (tests for the pure helpers run on a bare interpreter; the
CP-SAT paths carry the ci-only marker). The CP-SAT API used is the long-stable
CamelCase surface — ``cp_model.CpModel``, ``NewIntVar``, ``NewIntervalVar``,
``AddNoOverlap2D``, ``NewBoolVar``, ``OnlyEnforceIf``, ``Minimize``, ``CpSolver``,
``solver.parameters...`` — consistently, no snake_case mixing.

THE MODEL (per stair candidate, per storey — §5.2 in order):

* interval vars per room in x and y (position + size, integer CELLS on the 300mm
  module), ``AddNoOverlap2D`` over rooms **plus the mandatory-void rectangles** that
  encode L/T notches (§5.2 "L/T handled by mandatory-void cells");
* sizes bounded by the program (NBC floors from the pack), aspect 1:1–1:2.2
  habitable / 1:3 baths+stores (ratios ×100, integer arithmetic);
* **exact tiling**: Σ room areas == footprint area − void overlap. This is the
  :class:`services.solver.walls.CellLayout` contract — "the rectangles must TILE the
  storey footprint" — enforced in the model rather than repaired after it, so stage B
  never sees a gap that would grow phantom external walls;
* **stairs first**: the anchor's dogleg well (NBC-sized by :mod:`services.solver.stairs`)
  is a FIXED rectangle; the footprint is flush with the boundary side the well hugs;
* **circulation spine**: every packed room shares ≥900mm of edge with a distributor
  (passage/foyer/lobby/corridor, living as the documented Indian fallback — the same
  sets ``services/solver/openings.py`` uses to place doors); the passage touches the
  stair well; the ground-floor distributor touches the entry side. Circulation area
  over the 12% target is penalised (soft, §5.2);
* required adjacency (kitchen↔dining shared edge ≥900mm) via interval-arithmetic
  booleans; brief wishes as soft bonuses;
* external face: habitable + kitchen rooms keep ≥1 edge on the footprint boundary;
  baths may be internal only when shaft-adjacent;
* Vastu per §5.2 modes: strict → allowed/denied zone bands constrain the room
  centre; advisory → hard denials only (the §5.4 critic discards those anyway) plus
  an objective bonus; zones come from the pack via
  :func:`services.solver.program.zone_allowance_for`. When true north is not a
  multiple of 90° the zone bands are not axis-aligned and stage A degrades to
  advisory scoring (documented in :func:`services.solver.grid.zone_bands_mm`);
* wet cluster: L1 distance from every wet room to the (synthesised) shaft, soft with
  a strong weight — §5.2 calls it the buildability signal;
* multi-floor per §5.2: ground first, then the footprint, stair well and shaft are
  FIXED and each upper storey solves with those continuity constraints.

Objective: the §5.2 weighted sum — target-area deviation, adjacency satisfaction,
circulation area, external-face bonus, Vastu (advisory), compactness (footprint
half-perimeter). Weights live in :class:`StageAWeights`; §5.6's relax-once pass uses
:meth:`StageAWeights.relaxed`.

DETERMINISM (why two profiles): production runs ``num_search_workers=8`` with a 15s
wall clock per stair candidate (§5.2 verbatim) — a portfolio race, fast but not
reproducible. The deterministic test profile runs one worker, a fixed
``random_seed``, and solution/conflict limits instead of wall-clock time, because
solution and conflict counts are machine-independent — that is what lets §16's plan
goldens compare with tolerance 0. Both arrive as ``SolverProfile`` values from the
pipeline; this module maps them onto ``solver.parameters``. NOTE: CP-SAT exposes
*conflicts*, not branches, as its deterministic effort limit, so the profile's
``max_branches`` maps to ``parameters.max_number_of_conflicts`` (same intent: stop
at the same search node everywhere).

CANDIDATE CONTRACT: circulation rooms (passage/staircase/…) ARE placements — the
tiling contract requires it — and ``Candidate.circulation_area_mm2`` reports their
area again as the §5.2 metric. Consumers computing a footprint must therefore use
Σ placement areas, NOT occupied + circulation (flagged to the pipeline owner).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from services.common.logging import get_logger
from services.solver import stairs as stairs_mod
from services.solver.diagnose import diagnose_storey
from services.solver.envelope import derive_envelope
from services.solver.geometry import Pt, bbox
from services.solver.grid import (
    CellRect,
    compass_to_grid_side,
    grid_side_to_compass,
    void_rects_of_mask,
    zone_bands_mm,
)
from services.solver.program import (
    CIRCULATION_TYPES,
    DEFAULT_STOREY_HEIGHT_MM,
    DOOR_FRONTAGE_MM,
    FALLBACK_DISTRIBUTOR_TYPES,
    ProgramRoom,
    RoomProgram,
    program_from_params,
    rebalance_off_storey,
)
from services.solver.types import (
    COARSE_MODULE_MM,
    RoomPlacement,
    SolveParams,
    StairAnchor,
)

if TYPE_CHECKING:  # imported lazily at runtime to stay cycle-proof with stages.py
    from services.solver.stages import Candidate
    from services.solver.walls import CellLayout

log = get_logger("solver.stage_a")

#: Wet room types that must be shaft-adjacent when internal (§5.2 external-face rule).
_BATH_TYPES = frozenset({"bath", "wc", "bath_wc"})
#: Types the connectivity spine treats as distributors (doors lead FROM these).
_DISTRIBUTOR_TYPES = frozenset(CIRCULATION_TYPES) | {"staircase"}

#: Per-storey circulation bound used INSIDE the CP-SAT model, as a percent of that
#: storey's net footprint.
#:
#: Deliberately looser than :data:`gates.MAX_CIRCULATION_PERCENT` (18), which is the
#: real §5.6 gate and is measured across the WHOLE BUILDING. This is a pruning bound
#: on ONE storey, and the two are not the same quantity: the staircase counts as
#: circulation on every floor it serves and does not shrink with the floor, so a small
#: storey spends most of an 18% budget on the stair alone. Setting this to the gate's
#: own number made every sparse storey infeasible — see ``add_circulation``.
#:
#: Raise it and junk candidates waste search budget; lower it toward 18 and real
#: layouts disappear. It must never be lowered to the gate's value.
MODEL_CIRCULATION_PRUNE_PERCENT = 30
#: Two rooms wished "apart" should keep centres ≥ this many cells of L1 distance.
APART_MIN_CELLS = 6


# ---------------------------------------------------------------------------
# weights — the §5.2 objective, named and relaxable (§5.6 relax-once)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageAWeights:
    """Integer weights of the §5.2 weighted-sum objective.

    Every term is integer cells (or booleans), so the objective is an exact integer
    and identical runs produce identical objective values — a precondition for the
    deterministic profile meaning anything.
    """

    #: Per cell a room falls short of its target area.
    area_shortfall: int = 3
    #: Per circulation cell above the 12% target (§5.2 soft cap).
    circulation_excess: int = 8
    #: Per cell of footprint half-perimeter (the §5.2 perimeter penalty).
    compactness: int = 2
    #: Reward per wet room placed on the footprint boundary (window feasibility).
    external_face_bonus: int = 5
    #: Multiplier on (brief wish weight // 10) per satisfied adjacency wish.
    adjacency_wish: int = 2
    #: Multiplier on the pack rule weight per room in a preferred Vastu zone.
    vastu_bonus: int = 1
    #: Per HALF-cell of L1 distance between a wet room's centre and the shaft
    #: (centres live in doubled coordinates, so this is 2× per cell — strong, §5.2).
    wet_distance: int = 2
    #: §5.2 circulation target, percent of the net footprint.
    circulation_target_percent: int = 12

    def relaxed(self) -> StageAWeights:
        """§5.6 "relax soft weights once": soft preferences halve, the circulation
        target loosens toward (but below) the §5.6 hard gate. Hard rules never move."""
        return replace(
            self,
            area_shortfall=max(1, self.area_shortfall // 2),
            circulation_excess=max(1, self.circulation_excess // 2),
            external_face_bonus=self.external_face_bonus // 2,
            adjacency_wish=self.adjacency_wish // 2,
            vastu_bonus=self.vastu_bonus // 2,
            wet_distance=max(1, self.wet_distance // 2),
            circulation_target_percent=15,
        )


DEFAULT_WEIGHTS = StageAWeights()


# ---------------------------------------------------------------------------
# pure helpers — provable without ortools
# ---------------------------------------------------------------------------


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def min_frontage_cells(
    required_mm: int,
    *,
    module_mm: int = COARSE_MODULE_MM,
    fine_mm: int = 115,
) -> int:
    """Smallest coarse-cell span whose WORST-CASE §5.3 fine-snap survivor is still
    ``required_mm``. Pure and exact — no safety-factor guessing.

    Stage B snaps every coordinate to the 115mm module (same origin as this
    grid), so a coarse span of ``c`` cells can shrink: 900mm → 805mm is real and
    killed every first-execution candidate whose bath frontage was the naive
    ``ceil(900/300)`` cells. The snap pattern repeats every lcm(module, fine)
    mm, so scanning one period gives the exact worst case for each ``c``.
    """
    if required_mm <= 0:
        return 1
    from services.solver.walls import snap_mm  # ortools-free; lazy keeps cycles out

    period = _lcm(module_mm, fine_mm) // module_mm
    for cells in range(1, 65):
        worst = min(
            snap_mm(module_mm * (k + cells)) - snap_mm(module_mm * k) for k in range(period)
        )
        if worst >= required_mm:
            return cells
    raise ValueError(
        "no coarse span up to 64 cells guarantees %dmm after the %dmm snap" % (required_mm, fine_mm)
    )


def _lcm(a: int, b: int) -> int:
    from math import gcd

    return a * b // gcd(a, b)


def snap_loss_table(
    max_cells: int, *, module_mm: int = COARSE_MODULE_MM, fine_mm: int = 115
) -> tuple[int, ...]:
    """``table[c]`` = worst mm a ``c``-cell dimension can LOSE to the §5.3 snap.

    Same arithmetic as :func:`min_frontage_cells`, tabulated so the CP model can
    look it up per size variable (``AddElement``): a 9-cell room (2700mm) can
    come out of the 115mm snap at 2645mm, and a clear-width bound that ignores
    that 55mm ships rooms the critic then fails on ``nbc.room.*.width.min`` —
    execution find, not speculation.
    """
    from services.solver.walls import snap_mm

    period = _lcm(module_mm, fine_mm) // module_mm
    table = [0]
    for cells in range(1, max_cells + 1):
        worst = min(
            snap_mm(module_mm * (k + cells)) - snap_mm(module_mm * k) for k in range(period)
        )
        table.append(cells * module_mm - worst)
    return tuple(table)


@dataclass(frozen=True)
class RoomBounds:
    """One program room converted to exact cell bounds (module = 300mm)."""

    room: ProgramRoom
    min_side_cells: int
    min_area_cells: int
    #: 0 means uncapped (circulation absorbs tiling slack; §5.6 gates the total).
    max_area_cells: int
    target_area_cells: int
    max_aspect_x100: int
    #: Fixed cell rectangle (stair well, shaft on upper floors), else ``None``.
    fixed_rect: tuple[int, int, int, int] | None = None

    @property
    def key(self) -> str:
        return self.room.key


def bounds_for(room: ProgramRoom, *, module_mm: int = COARSE_MODULE_MM) -> RoomBounds:
    """mm bounds → cell bounds. Minimums round UP (never under a code floor),
    targets round to nearest, maximums round up (generous, so tiling has slack)."""
    cell_area = module_mm * module_mm
    min_side = max(1, _ceil_div(room.min_width_mm, module_mm))
    min_area = max(min_side * min_side, _ceil_div(room.min_area_mm2, cell_area))
    target = max(min_area, (room.target_area_mm2 + cell_area // 2) // cell_area)
    if room.max_area_mm2 <= 0:
        max_area = 0
    else:
        max_area = max(min_area, target, _ceil_div(room.max_area_mm2, cell_area))
    return RoomBounds(
        room=room,
        min_side_cells=min_side,
        min_area_cells=min_area,
        max_area_cells=max_area,
        target_area_cells=target,
        max_aspect_x100=room.max_aspect_x100,
    )


def net_footprint_cap_cells(params: SolveParams, *, module_mm: int = COARSE_MODULE_MM) -> int:
    """The per-floor built-area ceiling in cells: min(coverage cap, FAR cap / floors).

    Floor division throughout — rounding DOWN a regulatory cap is the conservative
    direction (§5.1's envelope maths made the same call).
    """
    plot_area = params.plot_area_mm2()
    coverage = params.profile.allowed_footprint_mm2(plot_area)
    far_per_floor = params.profile.allowed_built_up_mm2(plot_area) // max(1, params.storeys)
    return min(coverage, far_per_floor) // (module_mm * module_mm)


def zone_bands_cells2(
    bands_mm: Mapping[str, tuple[int, int, int, int]] | None,
    grid_origin: Pt,
    *,
    module_mm: int = COARSE_MODULE_MM,
) -> dict[str, tuple[int, int, int, int]] | None:
    """Zone mm bands → inclusive bounds on the DOUBLED cell centre ``(x1+x2, y1+y2)``.

    A room's centre in cells is ``(x1+x2)/2``; keeping the doubled sum avoids the
    division. For zone band ``[zx1, zx2]`` mm the constraint is
    ``2*(zx1-ox) <= (x1+x2)*module <= 2*(zx2-ox)`` — returned here as inclusive
    integer bounds on ``x1+x2`` (ceil on the low side, floor on the high side).
    """
    if bands_mm is None:
        return None
    ox, oy = grid_origin
    out: dict[str, tuple[int, int, int, int]] = {}
    for zone in sorted(bands_mm):
        zx1, zy1, zx2, zy2 = bands_mm[zone]
        out[zone] = (
            _ceil_div(2 * (zx1 - ox), module_mm),
            (2 * (zx2 - ox)) // module_mm,
            _ceil_div(2 * (zy1 - oy), module_mm),
            (2 * (zy2 - oy)) // module_mm,
        )
    return out


def mm_rect_to_cells(
    rect_mm: tuple[int, int, int, int], grid_origin: Pt, *, module_mm: int = COARSE_MODULE_MM
) -> tuple[int, int, int, int]:
    """mm rectangle → covering cell rectangle (floor min corner, ceil max corner)."""
    ox, oy = grid_origin
    return (
        (rect_mm[0] - ox) // module_mm,
        (rect_mm[1] - oy) // module_mm,
        _ceil_div(rect_mm[2] - ox, module_mm),
        _ceil_div(rect_mm[3] - oy, module_mm),
    )


def minimum_cells_needed(rooms: Sequence[RoomBounds]) -> int:
    """The tightest provable cell floor for a room set: fixed rects count exactly,
    free rooms count their minimum area. Pure — the fast infeasibility precheck."""
    total = 0
    for bounds in rooms:
        if bounds.fixed_rect is not None:
            x1, y1, x2, y2 = bounds.fixed_rect
            total += (x2 - x1) * (y2 - y1)
        else:
            total += bounds.min_area_cells
    return total


def split_time_budget(total_seconds: int | None, storeys: int) -> tuple[int | None, ...]:
    """§5.2's per-candidate budget split across storeys: ground gets the lion's
    share (it decides the footprint), uppers share the rest. ``None`` stays ``None``
    (the deterministic profile budgets by limits, not by clocks)."""
    count = max(1, storeys)
    if total_seconds is None:
        return tuple([None] * count)
    if count == 1:
        return (max(1, total_seconds),)
    ground = max(1, (total_seconds * 3) // 5)
    upper = max(1, (total_seconds - ground) // (count - 1))
    return tuple([ground] + [upper] * (count - 1))


def entry_grid_side(params: SolveParams, program: RoomProgram) -> tuple[str, tuple[str, ...]]:
    """(entry side, notes). Plot-local side the ground distributor must touch.

    Strict Vastu prefers a side whose true-compass name is in the pack's allowed
    entrance set; when the road side already qualifies it wins, else the nearest
    allowed side is chosen and a note records the trade. Non-cardinal norths keep
    the road side (the critic scores the entrance zone on the final model)."""
    road_side = stairs_mod.entry_side(params)
    if program.vastu_mode != "strict" or not program.entrance_allow:
        return road_side, ()
    compass = grid_side_to_compass(road_side, params.north_deg)
    if compass is None:
        return road_side, (
            "True north is not cardinal here, so the entrance side follows the road; "
            "the Vastu entrance rule is scored on the finished plan.",
        )
    if compass in program.entrance_allow:
        return road_side, ()
    for candidate in ("N", "E", "S", "W"):  # deterministic preference order
        candidate_compass = grid_side_to_compass(candidate, params.north_deg)
        if candidate_compass in program.entrance_allow:
            return candidate, (
                "Strict Vastu wants the entrance on %s; the road faces %s, so the "
                "entry moved to the %s side."
                % ("/".join(program.entrance_allow), compass, candidate_compass),
            )
    return road_side, ()


#: walls.clear_polygon's exact insets: 230 on a footprint-boundary side; the
#: internal 115 wall splits 58 to the low (left/bottom) room and 57 to the high.
_INSET_EXTERNAL_MM = 230
_INSET_INTERNAL_LOW_MM = 58
_INSET_INTERNAL_HIGH_MM = 57


#: Insets one axis pays in walls.clear_polygon terms: both sides internal
#: (57 + 58), or the worst typical case of one external + one internal side.
_AXIS_INSET_INTERNAL_MM = _INSET_INTERNAL_LOW_MM + _INSET_INTERNAL_HIGH_MM
_AXIS_INSET_TYPICAL_MM = _INSET_EXTERNAL_MM + _INSET_INTERNAL_LOW_MM


def _clear_mm(
    cells: int, losses: tuple[int, ...], *, inset_mm: int = _AXIS_INSET_TYPICAL_MM
) -> int:
    """Clear mm of a dimension of ``cells``: gross − worst snap loss − insets."""
    cells = max(1, min(cells, len(losses) - 1))
    return cells * COARSE_MODULE_MM - losses[cells] - inset_mm


def _cells_for_clear(
    clear_needed_mm: int,
    floor_cells: int,
    losses: tuple[int, ...],
    *,
    inset_mm: int = _AXIS_INSET_TYPICAL_MM,
) -> int:
    cells = max(1, floor_cells)
    while cells < len(losses) - 1 and _clear_mm(cells, losses, inset_mm=inset_mm) < clear_needed_mm:
        cells += 1
    return cells


def gross_min_dims(
    bounds: RoomBounds,
    losses: tuple[int, ...],
    *,
    inset_mm: int = _AXIS_INSET_TYPICAL_MM,
) -> tuple[int, int]:
    """Smallest (depth, width) cells whose CLEAR geometry satisfies the room's
    minimum width and minimum area. Pure — shared by the hint, the footprint
    candidates and the multi-storey net floor, so they cannot disagree."""
    min_w = max(1, bounds.room.min_width_mm)
    min_a = max(1, bounds.room.min_area_mm2)
    depth = _cells_for_clear(min_w, bounds.min_side_cells, losses, inset_mm=inset_mm)
    width = _cells_for_clear(
        max(min_w, _ceil_div(min_a, max(1, _clear_mm(depth, losses, inset_mm=inset_mm)))),
        bounds.min_side_cells,
        losses,
        inset_mm=inset_mm,
    )
    while width > 2 * depth and depth < len(losses) - 1:  # keep rooms squarish
        depth += 1
        width = _cells_for_clear(
            max(min_w, _ceil_div(min_a, max(1, _clear_mm(depth, losses, inset_mm=inset_mm)))),
            bounds.min_side_cells,
            losses,
            inset_mm=inset_mm,
        )
    return depth, width


def storey_min_net_cells(
    rooms: Sequence[RoomBounds],
    losses: tuple[int, ...],
    *,
    inset_mm: int = _AXIS_INSET_INTERNAL_MM,
) -> int:
    """The smallest net footprint (cells) that could carry one storey's program,
    CLEAR minima included. Fixed rects count exactly; free solids count their
    :func:`gross_min_dims` area; circulation counts its raw minimum.

    Why this exists: §5.2 solves the ground first and FIXES its footprint for
    every storey above, but the ground brief can justify a smaller footprint
    than the upper program needs — first execution of the demo brief proved
    every upper storey infeasible that way. The ground model takes the MAX of
    this over all storeys as a net floor.

    The default inset is the INTERNAL one — the same arithmetic
    :func:`add_clear_bounds` enforces — and that match is load-bearing: sizing
    the floor at the typical external inset overshot the model's own minima by
    ~90 cells on the demo brief, forced the ground to swallow the entire
    envelope, and left every storey an exact-tiling knife edge the room maxima
    could not close (execution find, again).
    """
    total = 0
    for bounds in rooms:
        if bounds.fixed_rect is not None:
            x1, y1, x2, y2 = bounds.fixed_rect
            total += (x2 - x1) * (y2 - y1)
        elif bounds.room.is_circulation or bounds.room.room_type == "shaft":
            total += max(1, bounds.min_area_cells)
        else:
            depth, width = gross_min_dims(bounds, losses, inset_mm=inset_mm)
            total += depth * width
    return total


def band_hint(problem: _StoreyProblem) -> dict[str, tuple[int, int, int, int]]:
    """A greedy two-band layout used as a CP-SAT solution HINT. Pure, integer cells.

    First execution showed the honest model — exact tiling + clear-geometry
    minima + the §5.6 circulation cap — takes CP-SAT 5-25s to find ANY solution,
    which blows the 15s/candidate budget on a coin flip. A hint repairs that:
    rooms split into two horizontal bands with the circulation strip filling the
    lower one, each room sized near its target. The hint does NOT need to be
    feasible (CP-SAT repairs it); it needs to be in the right neighbourhood.

    Returns cell rects per room key plus ``__footprint__``; empty when the
    storey has no free rooms to hint.
    """
    free = [b for b in problem.rooms if b.fixed_rect is None]
    fixed = [b for b in problem.rooms if b.fixed_rect is not None]
    solids = sorted(
        (b for b in free if not b.room.is_circulation and b.room.room_type != "shaft"),
        key=lambda b: (-b.target_area_cells, b.key),
    )
    passages = sorted((b for b in free if b.room.is_circulation), key=lambda b: b.key)
    shafts = [b for b in free if b.room.room_type == "shaft"]
    if not solids:
        return {}

    # CLEAR-geometry-aware sizing: a dimension's usable width is its gross mm
    # minus the worst 115mm-snap loss minus one external + one internal wall
    # inset (230 + 58). Sizing bands from raw targets under-provisions every
    # room and produced provably-infeasible footprints (execution find).
    losses = snap_loss_table(max(problem.cols, problem.rows))
    dims = {b.key: gross_min_dims(b, losses) for b in solids}
    band_a: list[RoomBounds] = []
    band_b: list[RoomBounds] = []
    area_a = area_b = 0
    for bounds in solids:  # largest first, to the emptier band — keeps widths close
        d, w = dims[bounds.key]
        if area_a <= area_b:
            band_a.append(bounds)
            area_a += d * w
        else:
            band_b.append(bounds)
            area_b += d * w
    depth_a = max(dims[b.key][0] for b in band_a)
    depth_b = max((dims[b.key][0] for b in band_b), default=max(4, depth_a // 2))

    def widths(band: list[RoomBounds], depth: int) -> list[int]:
        out_w: list[int] = []
        for b in band:
            min_w = max(1, b.room.min_width_mm)
            min_a = max(1, b.room.min_area_mm2)
            out_w.append(
                _cells_for_clear(
                    max(min_w, _ceil_div(min_a, max(1, _clear_mm(depth, losses)))),
                    b.min_side_cells,
                    losses,
                )
            )
        return out_w

    widths_a = widths(band_a, depth_a)
    widths_b = widths(band_b, depth_b)
    passage_w = 4  # one door-frontage strip; the model corrects the exact width
    width = max(sum(widths_a), sum(widths_b) + (passage_w if passages else 0) + len(shafts))
    width = min(width, problem.cols)
    depth_total = min(depth_a + depth_b, problem.rows)

    # Anchor at the stair when there is one (the footprint is flush with the
    # side its well hugs); clamp so the hinted footprint stays on the grid.
    x0 = y0 = 0
    if problem.footprint_fixed is not None:
        x0, y0 = problem.footprint_fixed[0], problem.footprint_fixed[1]
        width = problem.footprint_fixed[2] - x0
        depth_total = problem.footprint_fixed[3] - y0
    elif fixed:
        rect = fixed[0].fixed_rect
        assert rect is not None
        x0 = max(0, min(rect[0], problem.cols - width))
        y0 = max(0, min(rect[1], problem.rows - depth_total))

    # Band B (service rooms + the passage strip + the corner shaft) sits LOW —
    # against the entry side and any south-edge stair anchor; band A above it.
    out: dict[str, tuple[int, int, int, int]] = {}
    y_split = y0 + max(1, depth_total - depth_a)
    y_top = y0 + depth_total
    cursor = x0
    for bounds, w in zip(band_b, widths_b, strict=False):
        out[bounds.key] = (cursor, y0, min(cursor + w, x0 + width), y_split)
        cursor = min(cursor + w, x0 + width - 1)
    remaining = max(1, x0 + width - cursor - len(shafts))
    for bounds in passages:  # the passage strip absorbs the rest of band B
        out[bounds.key] = (cursor, y0, min(cursor + remaining, x0 + width), y_split)
        cursor = min(cursor + remaining, x0 + width - 1)
    for bounds in shafts:  # one cell in the footprint's SE corner
        out[bounds.key] = (x0 + width - 1, y0, x0 + width, min(y0 + 1, y_top))
    cursor = x0
    for bounds, w in zip(band_a, widths_a, strict=False):
        out[bounds.key] = (cursor, y_split, min(cursor + w, x0 + width), y_top)
        cursor = min(cursor + w, x0 + width - 1)
    out["__footprint__"] = (x0, y0, x0 + width, y_top)
    return out


def _isqrt_ceil(value: int) -> int:
    from math import isqrt

    root = isqrt(value)
    return root if root * root == value else root + 1


def footprint_candidates(problem: _StoreyProblem) -> tuple[tuple[int, int, int, int], ...]:
    """Deterministic FIXED footprint rectangles for the ground-storey solve.

    Why fix the footprint at all: with free footprint edges, the exact-tiling
    equality plus the clear-geometry equivalences turn a six-room storey into a
    model CP-SAT could not crack in 60s single-worker (execution find, log in
    hand) — while upper storeys, which always solve against a FIXED footprint,
    were never the problem. So the ground floor now works like the uppers: try
    a few deterministic rectangles sized from the program (target areas grossed
    up by the §5.2 circulation target, shaped by the same two-band arithmetic
    the hint uses), flush with the stair anchor. The free-footprint model stays
    as the fallback when every candidate is infeasible, so no generality is
    lost — only the slow path is demoted.

    Every candidate satisfies the §5.6 arithmetic precondition
    ``82·net ≤ 100·Σ(non-circulation max areas)`` — a rectangle that fails it
    would force circulation over the hard cap before the solve even starts.
    """
    hint = band_hint(problem)
    if not hint:
        return ()
    x0, y0, x1, y1 = hint["__footprint__"]
    base_w, base_d = x1 - x0, y1 - y0

    total_min = minimum_cells_needed(problem.rooms)
    solid_max = 0
    for bounds in problem.rooms:
        if bounds.fixed_rect is not None:
            continue  # the stair well is circulation; fixed shafts count nothing
        if bounds.room.is_circulation:
            continue
        grid_area = problem.cols * problem.rows
        solid_max += bounds.max_area_cells if bounds.max_area_cells > 0 else grid_area
    net_hi = (100 * solid_max) // 82 if solid_max else problem.net_cap_cells
    net_hi = min(net_hi, problem.net_cap_cells)

    out: list[tuple[int, int, int, int]] = []
    seen: dict[tuple[int, int, int, int], bool] = {}
    for dw, dd in ((0, 0), (1, 0), (0, 1), (2, 1), (1, 2), (3, 2)):
        width = min(problem.cols, base_w + dw)
        depth = min(problem.rows, base_d + dd)
        net = width * depth
        if net < total_min or net > net_hi:
            continue
        fx0 = max(0, min(x0, problem.cols - width))
        fy0 = max(0, min(y0, problem.rows - depth))
        rect = (fx0, fy0, fx0 + width, fy0 + depth)
        # A fixed footprint must contain the fixed stair well, or the two
        # fixed rectangles contradict each other before the model exists.
        contains_fixed = all(
            b.fixed_rect is None
            or (
                rect[0] <= b.fixed_rect[0]
                and rect[1] <= b.fixed_rect[1]
                and b.fixed_rect[2] <= rect[2]
                and b.fixed_rect[3] <= rect[3]
            )
            for b in problem.rooms
        )
        if not contains_fixed or rect in seen:
            continue
        seen[rect] = True
        out.append(rect)
    return tuple(out)


# ---------------------------------------------------------------------------
# the CP-SAT model — every constraint family is its own small builder (§16)
# ---------------------------------------------------------------------------


@dataclass
class _StoreyProblem:
    """Everything one storey's CpModel needs. Plain data, ortools-free."""

    storey_index: int
    rooms: tuple[RoomBounds, ...]
    cols: int
    rows: int
    voids: tuple[CellRect, ...]
    net_cap_cells: int
    weights: StageAWeights
    adjacency: tuple[Any, ...] = ()  # program.AdjacencySpec, filtered to present rooms
    stair_side: str | None = None
    entry_side: str | None = None
    footprint_fixed: tuple[int, int, int, int] | None = None
    #: §5.2 multi-floor continuity, relaxed to CONTAINMENT: the storey's own
    #: footprint rectangle must lie inside this one (the ground's). Demanding
    #: equality made upper storeys tile the ground's exact cell count around
    #: inherited fixed rects — brittle to the point of constant infeasibility
    #: (execution find); stage B only needs each storey's own rooms to tile
    #: each storey's own outline, and the stair-side flush keeps that wall line
    #: shared. ``None`` when there is no storey below.
    footprint_within: tuple[int, int, int, int] | None = None
    #: The mirror bound: this storey's footprint must CONTAIN this rectangle
    #: (it is a storey BELOW an already-solved, more constrained one).
    footprint_contains: tuple[int, int, int, int] | None = None
    shaft_fixed_rect: tuple[int, int, int, int] | None = None
    zone_bands: dict[str, tuple[int, int, int, int]] | None = None
    vastu_mode: str = "advisory"
    north_deg: int = 0
    #: Per-room minimum door-frontage cells (room key → cells), sized from the
    #: pack's door widths + end margins + the 115mm snap worst case. ``None`` ⇒
    #: the flat DOOR_FRONTAGE_MM fallback (older callers, unit fixtures).
    door_cells_by_key: Mapping[str, int] | None = None
    #: Minimum cells of the entry room's side ON the entry boundary — the main
    #: door (pack width + margins + snap) must fit that external span. 0 ⇒ off.
    entry_frontage_cells: int = 0
    #: Floor on the span between two circulation rooms (passage-passage,
    #: passage-stair): the internal door width + both end margins, snap-proofed.
    #: Stage B places a door or archway there and discards the layout when it
    #: does not fit; before this floor existed the arrival span could be 900 mm.
    arrival_cells: int = 0
    #: Per-room CLEAR floors (room key → (min clear area mm², min clear width
    #: mm)) — the PACK's numbers, which are what the §5.4 critic hard-fails on.
    #: The brief's own min width/area stay GROSS domain floors (a brief's
    #: "3.0m bedroom" is a wall-to-wall wish; the code's 2.4m is clear) —
    #: enforcing brief numbers as clear made a standard 30×40ft brief
    #: arithmetically impossible (execution find). ``None`` ⇒ fall back to the
    #: room's own minima (unit-test callers).
    clear_floor_by_key: Mapping[str, tuple[int, int]] | None = None
    #: Net-footprint floor (cells): max over ALL storeys of the storey's minimum
    #: program (§5.2 multi-floor: the ground footprint is every storey's). 0 ⇒ off.
    net_floor_cells: int = 0
    #: Net-footprint ceiling (cells) for the LEAD storey of a multi-storey solve.
    #: Without it the first-solved storey sprawls to the whole envelope (its
    #: objective tolerates the first feasible sprawl the budget finds), and every
    #: other storey then has to tile the full grid EXACTLY — proven infeasible
    #: repeatedly on the demo brief. 0 ⇒ off.
    net_ceiling_cells: int = 0


@dataclass
class _RoomVars:
    """CP-SAT variables for one room. ``Any`` because ortools loads lazily."""

    bounds: RoomBounds
    x1: Any
    y1: Any
    x2: Any
    y2: Any
    w: Any
    h: Any
    area: Any
    ix: Any
    iy: Any
    cx2: Any  # x1 + x2 == centre × 2 (doubled coordinates, no division)
    cy2: Any


@dataclass
class _Objective:
    """Accumulated (coefficient, var) terms. Penalties minimise, rewards maximise."""

    penalties: list[tuple[int, Any]]
    rewards: list[tuple[int, Any]]

    def penalise(self, coefficient: int, var: Any) -> None:
        if coefficient:
            self.penalties.append((coefficient, var))

    def reward(self, coefficient: int, var: Any) -> None:
        if coefficient:
            self.rewards.append((coefficient, var))


def add_room_variables(model: Any, problem: _StoreyProblem) -> dict[str, _RoomVars]:
    """Interval vars per room in x and y (§5.2). Fixed rects become fixed vars so
    every downstream builder treats pinned and free rooms identically."""
    out: dict[str, _RoomVars] = {}
    grid_area = problem.cols * problem.rows
    for bounds in problem.rooms:
        key = bounds.key
        if bounds.fixed_rect is not None:
            fx1, fy1, fx2, fy2 = bounds.fixed_rect
            x1 = model.NewIntVar(fx1, fx1, "%s.x1" % key)
            y1 = model.NewIntVar(fy1, fy1, "%s.y1" % key)
            x2 = model.NewIntVar(fx2, fx2, "%s.x2" % key)
            y2 = model.NewIntVar(fy2, fy2, "%s.y2" % key)
            w = model.NewIntVar(fx2 - fx1, fx2 - fx1, "%s.w" % key)
            h = model.NewIntVar(fy2 - fy1, fy2 - fy1, "%s.h" % key)
            area_lo = area_hi = (fx2 - fx1) * (fy2 - fy1)
        else:
            min_side = bounds.min_side_cells
            x1 = model.NewIntVar(0, max(0, problem.cols - min_side), "%s.x1" % key)
            y1 = model.NewIntVar(0, max(0, problem.rows - min_side), "%s.y1" % key)
            x2 = model.NewIntVar(min_side, problem.cols, "%s.x2" % key)
            y2 = model.NewIntVar(min_side, problem.rows, "%s.y2" % key)
            w = model.NewIntVar(min_side, problem.cols, "%s.w" % key)
            h = model.NewIntVar(min_side, problem.rows, "%s.h" % key)
            area_lo = bounds.min_area_cells
            area_hi = bounds.max_area_cells if bounds.max_area_cells > 0 else grid_area
        area = model.NewIntVar(area_lo, max(area_lo, area_hi), "%s.area" % key)
        model.Add(x2 == x1 + w)
        model.Add(y2 == y1 + h)
        model.AddMultiplicationEquality(area, [w, h])
        ix = model.NewIntervalVar(x1, w, x2, "%s.ix" % key)
        iy = model.NewIntervalVar(y1, h, y2, "%s.iy" % key)
        cx2 = model.NewIntVar(0, 2 * problem.cols, "%s.cx2" % key)
        cy2 = model.NewIntVar(0, 2 * problem.rows, "%s.cy2" % key)
        model.Add(cx2 == x1 + x2)
        model.Add(cy2 == y1 + y2)
        out[key] = _RoomVars(bounds, x1, y1, x2, y2, w, h, area, ix, iy, cx2, cy2)
    return out


def add_no_overlap(model: Any, room_vars: dict[str, _RoomVars], problem: _StoreyProblem) -> None:
    """``AddNoOverlap2D`` over rooms + the mandatory-void rectangles (§5.2 L/T)."""
    xs = [vars_.ix for _, vars_ in sorted(room_vars.items())]
    ys = [vars_.iy for _, vars_ in sorted(room_vars.items())]
    for index, void in enumerate(problem.voids):
        xs.append(model.NewIntervalVar(void.col1, void.cols, void.col2, "void%d.ix" % index))
        ys.append(model.NewIntervalVar(void.row1, void.rows, void.row2, "void%d.iy" % index))
    model.AddNoOverlap2D(xs, ys)


def add_size_bounds(model: Any, room_vars: dict[str, _RoomVars], problem: _StoreyProblem) -> None:
    """Aspect-ratio bounds (§5.2: ×100 integers) — min sides/areas are var domains."""
    for key in sorted(room_vars):
        vars_ = room_vars[key]
        if vars_.bounds.fixed_rect is not None:
            continue
        lo = model.NewIntVar(1, max(problem.cols, problem.rows), "%s.side_lo" % key)
        hi = model.NewIntVar(1, max(problem.cols, problem.rows), "%s.side_hi" % key)
        model.AddMinEquality(lo, [vars_.w, vars_.h])
        model.AddMaxEquality(hi, [vars_.w, vars_.h])
        model.Add(100 * hi <= vars_.bounds.max_aspect_x100 * lo)


def add_footprint(
    model: Any, room_vars: dict[str, _RoomVars], problem: _StoreyProblem
) -> dict[str, Any]:
    """The storey footprint rectangle, its net (void-free) area, and the caps.

    The footprint is flush with the boundary side the stair hugs (§5.2 multi-floor
    fixes external walls from the ground solve; on uppers the whole rectangle is
    fixed). Net area = footprint area − Σ overlap with void rects, kept exact with
    min/max/product variables so the tiling equality can hold cell-for-cell.
    """
    if problem.footprint_fixed is not None:
        gx1, gy1, gx2, gy2 = problem.footprint_fixed
        fx1 = model.NewIntVar(gx1, gx1, "f.x1")
        fy1 = model.NewIntVar(gy1, gy1, "f.y1")
        fx2 = model.NewIntVar(gx2, gx2, "f.x2")
        fy2 = model.NewIntVar(gy2, gy2, "f.y2")
    elif problem.footprint_within is not None:
        wx1, wy1, wx2, wy2 = problem.footprint_within
        fx1 = model.NewIntVar(wx1, wx2, "f.x1")
        fy1 = model.NewIntVar(wy1, wy2, "f.y1")
        fx2 = model.NewIntVar(wx1, wx2, "f.x2")
        fy2 = model.NewIntVar(wy1, wy2, "f.y2")
    else:
        fx1 = model.NewIntVar(0, problem.cols, "f.x1")
        fy1 = model.NewIntVar(0, problem.rows, "f.y1")
        fx2 = model.NewIntVar(0, problem.cols, "f.x2")
        fy2 = model.NewIntVar(0, problem.rows, "f.y2")
    if problem.footprint_contains is not None:
        cx1, cy1, cx2, cy2 = problem.footprint_contains
        model.Add(fx1 <= cx1)
        model.Add(fy1 <= cy1)
        model.Add(fx2 >= cx2)
        model.Add(fy2 >= cy2)
    fw = model.NewIntVar(1, problem.cols, "f.w")
    fh = model.NewIntVar(1, problem.rows, "f.h")
    model.Add(fx2 == fx1 + fw)
    model.Add(fy2 == fy1 + fh)
    farea = model.NewIntVar(1, problem.cols * problem.rows, "f.area")
    model.AddMultiplicationEquality(farea, [fw, fh])

    for key in sorted(room_vars):
        vars_ = room_vars[key]
        model.Add(vars_.x1 >= fx1)
        model.Add(vars_.y1 >= fy1)
        model.Add(vars_.x2 <= fx2)
        model.Add(vars_.y2 <= fy2)

    void_overlaps: list[Any] = []
    for index, void in enumerate(problem.voids):
        lo_x = model.NewIntVar(0, problem.cols, "void%d.lox" % index)
        hi_x = model.NewIntVar(0, problem.cols, "void%d.hix" % index)
        model.AddMaxEquality(lo_x, [fx1, void.col1])
        model.AddMinEquality(hi_x, [fx2, void.col2])
        dx = model.NewIntVar(-problem.cols, problem.cols, "void%d.dx" % index)
        model.Add(dx == hi_x - lo_x)
        ow = model.NewIntVar(0, problem.cols, "void%d.ow" % index)
        model.AddMaxEquality(ow, [dx, 0])

        lo_y = model.NewIntVar(0, problem.rows, "void%d.loy" % index)
        hi_y = model.NewIntVar(0, problem.rows, "void%d.hiy" % index)
        model.AddMaxEquality(lo_y, [fy1, void.row1])
        model.AddMinEquality(hi_y, [fy2, void.row2])
        dy = model.NewIntVar(-problem.rows, problem.rows, "void%d.dy" % index)
        model.Add(dy == hi_y - lo_y)
        oh = model.NewIntVar(0, problem.rows, "void%d.oh" % index)
        model.AddMaxEquality(oh, [dy, 0])

        oa = model.NewIntVar(0, void.cell_count, "void%d.oa" % index)
        model.AddMultiplicationEquality(oa, [ow, oh])
        void_overlaps.append(oa)

    net = model.NewIntVar(1, problem.cols * problem.rows, "f.net")
    if void_overlaps:
        model.Add(net == farea - sum(void_overlaps))
    else:
        model.Add(net == farea)
    model.Add(net <= problem.net_cap_cells)
    if problem.net_floor_cells > 0:
        # §5.2 multi-floor: this rectangle is EVERY storey's footprint, so it
        # must carry the largest storey's minimum program, not just this one's.
        model.Add(net >= min(problem.net_floor_cells, problem.net_cap_cells))
    if problem.net_ceiling_cells > 0:
        model.Add(net <= problem.net_ceiling_cells)

    stair_vars = room_vars.get(_stair_key(problem))
    if stair_vars is not None and problem.stair_side and problem.footprint_fixed is None:
        rect = stair_vars.bounds.fixed_rect
        if rect is not None:
            sx1, sy1, sx2, sy2 = rect
            if problem.stair_side == "S":
                model.Add(fy1 == sy1)
            elif problem.stair_side == "N":
                model.Add(fy2 == sy2)
            elif problem.stair_side == "W":
                model.Add(fx1 == sx1)
            elif problem.stair_side == "E":
                model.Add(fx2 == sx2)

    return {"fx1": fx1, "fy1": fy1, "fx2": fx2, "fy2": fy2, "fw": fw, "fh": fh, "net": net}


def add_tiling(model: Any, room_vars: dict[str, _RoomVars], footprint: dict[str, Any]) -> None:
    """Σ room areas == net footprint area — the CellLayout tiling contract, in-model."""
    model.Add(sum(vars_.area for _, vars_ in sorted(room_vars.items())) == footprint["net"])


def add_clear_bounds(
    model: Any,
    room_vars: dict[str, _RoomVars],
    footprint: dict[str, Any],
    problem: _StoreyProblem,
    *,
    module_mm: int = COARSE_MODULE_MM,
) -> None:
    """§5.4 checks CLEAR geometry — room polygons inside the wall faces — so the
    walls must be priced into the model, not discovered at the critic.

    First execution proved the gap: every candidate met its gross minima and
    every candidate failed ``nbc.room.*.area.min``/``width.min`` once stage B's
    insets (230mm on external sides, 57/58 on internal) ate the clear polygon.

    The bound is :func:`_clear_mm` per dimension — gross minus the worst 115mm
    snap loss minus the INTERNAL wall insets (57 + 58) — looked up by a pure
    table over the size variable, NO boundary literals. Three deliberate calls
    live here:

    * no per-side boundary literals: an earlier equivalence formulation (exact
      insets per side) made CP-SAT probing blow up and mis-prove instances
      infeasible in presolve;
    * internal insets, not the external 230: taxing BOTH axes for a wall only
      one side of one axis carries turned a knife-edge brief (the demo!) from
      tight to arithmetically impossible. A room that ends up sized to the bone
      ON its external axis can still come up ~170mm short of clear width there
      — and the §5.4 critic discards exactly that candidate, which is the
      critic's job; the model's job is only to never propose a room that no
      wall arrangement could save;
    * the same :func:`snap_loss_table` the frontage floors use, so a dimension
      that survives the model also survives the §5.3 snap.
    """
    losses = snap_loss_table(max(problem.cols, problem.rows), module_mm=module_mm)
    internal_table = [
        _clear_mm(c, losses, inset_mm=_AXIS_INSET_INTERNAL_MM) if c else 0
        for c in range(len(losses))
    ]
    typical_table = [
        _clear_mm(c, losses, inset_mm=_AXIS_INSET_TYPICAL_MM) if c else 0
        for c in range(len(losses))
    ]
    lo = min(min(internal_table), min(typical_table))
    hi = max(max(internal_table), max(typical_table))
    for key in sorted(room_vars):
        vars_ = room_vars[key]
        room = vars_.bounds.room
        if vars_.bounds.fixed_rect is not None or room.room_type == "shaft":
            continue
        if problem.clear_floor_by_key is not None:
            min_area, min_width = problem.clear_floor_by_key.get(key, (0, 0))
        else:
            min_width = room.min_width_mm
            min_area = room.min_area_mm2
        if min_width <= 0 and min_area <= 0:
            continue
        prefix = "clr.%s" % key
        cw_int = model.NewIntVar(lo, hi, "%s.wi" % prefix)
        model.AddElement(vars_.w, internal_table, cw_int)
        ch_int = model.NewIntVar(lo, hi, "%s.hi" % prefix)
        model.AddElement(vars_.h, internal_table, ch_int)
        if min_width > 0:
            # Least clear width = min of the two dims; the internal table is the
            # optimistic reading of each — a width sized to the bone on its
            # external axis is the critic's catch, by design.
            model.Add(cw_int >= min_width)
            model.Add(ch_int >= min_width)
        if min_area > 0:
            # A boundary room carries the external inset on exactly ONE axis in
            # the typical case, but WHICH axis is the solver's choice — so the
            # area must clear the floor under BOTH orderings (internal×typical
            # and typical×internal). min(A1, A2) ≥ floor ⇔ both ≥ floor: exact
            # for one-external-axis rooms, optimistic only for a corner room,
            # whose shortfall the §5.4 critic still catches.
            cw_typ = model.NewIntVar(lo, hi, "%s.wt" % prefix)
            model.AddElement(vars_.w, typical_table, cw_typ)
            ch_typ = model.NewIntVar(lo, hi, "%s.ht" % prefix)
            model.AddElement(vars_.h, typical_table, ch_typ)
            bound = max(1, hi) * max(1, hi)
            area_a = model.NewIntVar(-bound, bound, "%s.area_a" % prefix)
            model.AddMultiplicationEquality(area_a, [cw_int, ch_typ])
            model.Add(area_a >= min_area)
            area_b = model.NewIntVar(-bound, bound, "%s.area_b" % prefix)
            model.AddMultiplicationEquality(area_b, [cw_typ, ch_int])
            model.Add(area_b >= min_area)


def _touch_literals(
    model: Any,
    a: _RoomVars,
    b: _RoomVars,
    min_shared_cells: int,
    prefix: str,
    rows: int,
    cols: int,
) -> list[Any]:
    """Four booleans, one per side ``a`` can touch ``b`` on, each implying the touch
    and a shared edge ≥ ``min_shared_cells`` — §5.2's interval-arithmetic booleans."""
    oy_lo = model.NewIntVar(0, rows, "%s.oy_lo" % prefix)
    oy_hi = model.NewIntVar(0, rows, "%s.oy_hi" % prefix)
    model.AddMaxEquality(oy_lo, [a.y1, b.y1])
    model.AddMinEquality(oy_hi, [a.y2, b.y2])
    oy = model.NewIntVar(-rows, rows, "%s.oy" % prefix)
    model.Add(oy == oy_hi - oy_lo)

    ox_lo = model.NewIntVar(0, cols, "%s.ox_lo" % prefix)
    ox_hi = model.NewIntVar(0, cols, "%s.ox_hi" % prefix)
    model.AddMaxEquality(ox_lo, [a.x1, b.x1])
    model.AddMinEquality(ox_hi, [a.x2, b.x2])
    ox = model.NewIntVar(-cols, cols, "%s.ox" % prefix)
    model.Add(ox == ox_hi - ox_lo)

    left = model.NewBoolVar("%s.left" % prefix)  # b to the left of a
    model.Add(a.x1 == b.x2).OnlyEnforceIf(left)
    model.Add(oy >= min_shared_cells).OnlyEnforceIf(left)
    right = model.NewBoolVar("%s.right" % prefix)
    model.Add(a.x2 == b.x1).OnlyEnforceIf(right)
    model.Add(oy >= min_shared_cells).OnlyEnforceIf(right)
    below = model.NewBoolVar("%s.below" % prefix)  # b below a
    model.Add(a.y1 == b.y2).OnlyEnforceIf(below)
    model.Add(ox >= min_shared_cells).OnlyEnforceIf(below)
    above = model.NewBoolVar("%s.above" % prefix)
    model.Add(a.y2 == b.y1).OnlyEnforceIf(above)
    model.Add(ox >= min_shared_cells).OnlyEnforceIf(above)
    return [left, right, below, above]


def add_required_adjacencies(
    model: Any,
    room_vars: dict[str, _RoomVars],
    problem: _StoreyProblem,
    *,
    module_mm: int = COARSE_MODULE_MM,
) -> None:
    """§5.2 required adjacency (kitchen↔dining ≥900mm shared edge): hard BoolOr."""
    for spec in problem.adjacency:
        if spec.kind != "required":
            continue
        a = room_vars.get(spec.a_key)
        b = room_vars.get(spec.b_key)
        if a is None or b is None:
            continue
        min_cells = max(1, _ceil_div(spec.min_shared_edge_mm, module_mm))
        literals = _touch_literals(
            model,
            a,
            b,
            min_cells,
            "req.%s-%s" % (spec.a_key, spec.b_key),
            problem.rows,
            problem.cols,
        )
        model.AddBoolOr(literals)


def add_adjacency_wishes(
    model: Any,
    room_vars: dict[str, _RoomVars],
    problem: _StoreyProblem,
    objective: _Objective,
    *,
    module_mm: int = COARSE_MODULE_MM,
) -> None:
    """Brief wishes as soft bonuses (§5.2): 'adjacent' rewards a shared edge,
    'apart' rewards centre separation ≥ :data:`APART_MIN_CELLS`."""
    for spec in problem.adjacency:
        if spec.kind not in ("adjacent", "apart"):
            continue
        a = room_vars.get(spec.a_key)
        b = room_vars.get(spec.b_key)
        if a is None or b is None:
            continue
        coefficient = problem.weights.adjacency_wish * max(1, spec.weight // 10)
        name = "wish.%s-%s" % (spec.a_key, spec.b_key)
        satisfied = model.NewBoolVar(name)
        if spec.kind == "adjacent":
            min_cells = max(1, _ceil_div(spec.min_shared_edge_mm or DOOR_FRONTAGE_MM, module_mm))
            literals = _touch_literals(model, a, b, min_cells, name, problem.rows, problem.cols)
            model.AddBoolOr(literals).OnlyEnforceIf(satisfied)
        else:
            dx = model.NewIntVar(0, 2 * problem.cols, "%s.dx" % name)
            diff_x = model.NewIntVar(-2 * problem.cols, 2 * problem.cols, "%s.diffx" % name)
            model.Add(diff_x == a.cx2 - b.cx2)
            model.AddAbsEquality(dx, diff_x)
            dy = model.NewIntVar(0, 2 * problem.rows, "%s.dy" % name)
            diff_y = model.NewIntVar(-2 * problem.rows, 2 * problem.rows, "%s.diffy" % name)
            model.Add(diff_y == a.cy2 - b.cy2)
            model.AddAbsEquality(dy, diff_y)
            model.Add(dx + dy >= 2 * APART_MIN_CELLS).OnlyEnforceIf(satisfied)
        objective.reward(coefficient, satisfied)


def _boundary_literals(
    model: Any, vars_: _RoomVars, footprint: dict[str, Any], prefix: str
) -> dict[str, Any]:
    """Side → boolean implying the room's edge lies ON that footprint side."""
    out: dict[str, Any] = {}
    for side, (room_edge, footprint_edge) in (
        ("W", (vars_.x1, footprint["fx1"])),
        ("E", (vars_.x2, footprint["fx2"])),
        ("S", (vars_.y1, footprint["fy1"])),
        ("N", (vars_.y2, footprint["fy2"])),
    ):
        literal = model.NewBoolVar("%s.on%s" % (prefix, side))
        model.Add(room_edge == footprint_edge).OnlyEnforceIf(literal)
        out[side] = literal
    return out


def add_external_face(
    model: Any,
    room_vars: dict[str, _RoomVars],
    footprint: dict[str, Any],
    problem: _StoreyProblem,
    objective: _Objective,
) -> tuple[str, ...]:
    """§5.2: habitable + kitchen must reach the footprint boundary; baths may stay
    internal only when shaft-adjacent; wet rooms on the boundary earn the
    external-face bonus. Returns notes for skipped must-face constraints."""
    notes: list[str] = []
    shaft = room_vars.get("shaft")
    for key in sorted(room_vars):
        vars_ = room_vars[key]
        room = vars_.bounds.room
        if room.room_type == "shaft":
            if vars_.bounds.fixed_rect is None:
                # An ELASTIC shaft goes on the footprint boundary. Sequencing
                # matters, learned twice over: a FIXED-size shaft cannot tile on
                # a boundary at all (its neighbours' edges can never align), but
                # an elastic strip on an edge tiles like any slim room — while
                # an INTERIOR shaft, elastic or not, forces a four-room pinwheel
                # that the storey above, inheriting the position, then fails.
                # Boundary + elastic is the one combination that works on both
                # storeys — and it is where a ventilation shaft vents anyway.
                shaft_literals = _boundary_literals(model, vars_, footprint, "ext.%s" % key)
                model.AddBoolOr([shaft_literals[side] for side in ("W", "E", "S", "N")])
            continue
        literals = _boundary_literals(model, vars_, footprint, "ext.%s" % key)
        ordered = [literals[side] for side in ("W", "E", "S", "N")]
        if room.needs_external_wall:
            model.AddBoolOr(ordered)
        elif room.room_type in _BATH_TYPES:
            options = list(ordered)
            if shaft is not None:
                near_shaft = _touch_literals(
                    model, vars_, shaft, 1, "shaft.%s" % key, problem.rows, problem.cols
                )
                options.extend(near_shaft)
            model.AddBoolOr(options)
        if room.is_wet:
            on_boundary = model.NewBoolVar("ext.%s.bonus" % key)
            model.AddBoolOr(ordered).OnlyEnforceIf(on_boundary)
            objective.reward(problem.weights.external_face_bonus, on_boundary)
        if room.must_face:
            side = compass_to_grid_side(room.must_face, problem.north_deg)
            if side is None:
                notes.append(
                    "%s asked to face %s, but true north is not cardinal; scored on "
                    "the finished plan instead." % (key, room.must_face)
                )
            else:
                model.Add(literals[side] == 1)
    return tuple(notes)


def add_vastu_zones(
    model: Any,
    room_vars: dict[str, _RoomVars],
    problem: _StoreyProblem,
    objective: _Objective,
) -> bool | None:
    """§5.2 facing/Vastu: strict → centre constrained to allowed zone bands, denials
    excluded; advisory → hard denials only + preferred-zone bonus. Returns ``False``
    when a strict allowance has no representable band (infeasible by construction)."""
    bands = problem.zone_bands
    if bands is None or problem.vastu_mode == "off":
        return True
    for key in sorted(room_vars):
        vars_ = room_vars[key]
        allowance = vars_.bounds.room.vastu
        if allowance is None:
            continue
        prefix = "vastu.%s" % key

        if problem.vastu_mode == "strict" and allowance.allow:
            literals = [
                lit
                for lit in (
                    _in_zone_literal(model, vars_, bands.get(zone), "%s.in.%s" % (prefix, zone))
                    for zone in allowance.allow
                )
                if lit is not None
            ]
            if not literals:
                return False
            model.AddBoolOr(literals)

        for zone in allowance.deny:
            band = bands.get(zone)
            if band is None:
                continue
            sx_lo, sx_hi, sy_lo, sy_hi = band
            escapes = []
            for tag, var, bound, keep_below in (
                ("wof", vars_.cx2, sx_lo - 1, True),
                ("eof", vars_.cx2, sx_hi + 1, False),
                ("sof", vars_.cy2, sy_lo - 1, True),
                ("nof", vars_.cy2, sy_hi + 1, False),
            ):
                literal = model.NewBoolVar("%s.deny%s.%s" % (prefix, zone, tag))
                if keep_below:
                    model.Add(var <= bound).OnlyEnforceIf(literal)
                else:
                    model.Add(var >= bound).OnlyEnforceIf(literal)
                escapes.append(literal)
            model.AddBoolOr(escapes)

        if allowance.preferred and allowance.weight:
            for zone in allowance.preferred:
                literal = _in_zone_literal(
                    model, vars_, bands.get(zone), "%s.pref.%s" % (prefix, zone)
                )
                if literal is not None:
                    objective.reward(problem.weights.vastu_bonus * allowance.weight, literal)
    return True


def _in_zone_literal(
    model: Any,
    vars_: _RoomVars,
    band: tuple[int, int, int, int] | None,
    name: str,
) -> Any | None:
    """Boolean implying the room's doubled centre lies inside one zone band."""
    if band is None:
        return None
    sx_lo, sx_hi, sy_lo, sy_hi = band
    if sx_lo > sx_hi or sy_lo > sy_hi:
        return None
    literal = model.NewBoolVar(name)
    model.Add(vars_.cx2 >= sx_lo).OnlyEnforceIf(literal)
    model.Add(vars_.cx2 <= sx_hi).OnlyEnforceIf(literal)
    model.Add(vars_.cy2 >= sy_lo).OnlyEnforceIf(literal)
    model.Add(vars_.cy2 <= sy_hi).OnlyEnforceIf(literal)
    return literal


def add_wet_cluster(
    model: Any,
    room_vars: dict[str, _RoomVars],
    problem: _StoreyProblem,
    objective: _Objective,
) -> None:
    """§5.2 wet clustering: L1 centre distance from each wet room to the shaft,
    penalised (soft, strong weight — the plumbing buildability signal)."""
    shaft = room_vars.get("shaft")
    if shaft is None:
        return
    for key in sorted(room_vars):
        vars_ = room_vars[key]
        if not vars_.bounds.room.is_wet or key == "shaft":
            continue
        prefix = "wet.%s" % key
        dx = model.NewIntVar(0, 2 * problem.cols, "%s.dx" % prefix)
        diff_x = model.NewIntVar(-2 * problem.cols, 2 * problem.cols, "%s.diffx" % prefix)
        model.Add(diff_x == vars_.cx2 - shaft.cx2)
        model.AddAbsEquality(dx, diff_x)
        dy = model.NewIntVar(0, 2 * problem.rows, "%s.dy" % prefix)
        diff_y = model.NewIntVar(-2 * problem.rows, 2 * problem.rows, "%s.diffy" % prefix)
        model.Add(diff_y == vars_.cy2 - shaft.cy2)
        model.AddAbsEquality(dy, diff_y)
        objective.penalise(problem.weights.wet_distance, dx)
        objective.penalise(problem.weights.wet_distance, dy)


def _stair_key(problem: _StoreyProblem) -> str:
    for bounds in problem.rooms:
        if bounds.room.room_type == "staircase":
            return bounds.key
    return "staircase"


def add_circulation(
    model: Any,
    room_vars: dict[str, _RoomVars],
    footprint: dict[str, Any],
    problem: _StoreyProblem,
    objective: _Objective,
    *,
    module_mm: int = COARSE_MODULE_MM,
) -> Any | None:
    """§5.2 circulation spine: entry → stair → every room's door zone.

    Topology form: every packed room shares a door-width edge with a distributor
    (circulation rooms; living as the openings.py fallback); the passage touches the
    stair well; the ground distributor touches the entry side of the footprint.
    Circulation area above the target percent is penalised. Returns the circulation
    area expression (cells) for the caller to read back, or ``None`` when the storey
    has no circulation rooms at all (the program synthesises one, so this is a bug
    guard, not a normal path)."""
    door_cells = max(1, _ceil_div(DOOR_FRONTAGE_MM, module_mm), problem.arrival_cells)
    per_room = problem.door_cells_by_key or {}
    distributors = {
        key for key, vars_ in room_vars.items() if vars_.bounds.room.room_type in _DISTRIBUTOR_TYPES
    }
    fallback = {
        key
        for key, vars_ in room_vars.items()
        if vars_.bounds.room.room_type in FALLBACK_DISTRIBUTOR_TYPES
    }
    if not distributors:
        return None

    for key in sorted(room_vars):
        vars_ = room_vars[key]
        room_type = vars_.bounds.room.room_type
        # The stair's own arrival span is the passage<->stair constraint below;
        # a second, wider floor on it here made small programs infeasible.
        if room_type == "shaft" or room_type == "staircase":
            continue
        # The serving span must survive stage B: the room's own door width plus
        # end margins plus the 115mm snap, not the bare §5.2 frontage figure —
        # a 900mm coarse span snaps as low as 805mm, under ANY legal door.
        serve_cells = max(door_cells, per_room.get(key, 0))
        reachable: list[Any] = []
        # A passage is itself entered from another distributor or the living
        # room by a door or archway — stage B's door walk needs that span too.
        serving = sorted((distributors | fallback) - {key})
        for other in serving:
            reachable.extend(
                _touch_literals(
                    model,
                    vars_,
                    room_vars[other],
                    serve_cells,
                    "circ.%s-%s" % (key, other),
                    problem.rows,
                    problem.cols,
                )
            )
        if reachable:
            model.AddBoolOr(reachable)

    stair_key = _stair_key(problem)
    passages = sorted(
        key for key, vars_ in room_vars.items() if vars_.bounds.room.room_type in CIRCULATION_TYPES
    )
    if stair_key in room_vars and passages:
        arrivals: list[Any] = []
        for passage in passages:
            arrivals.extend(
                _touch_literals(
                    model,
                    room_vars[passage],
                    room_vars[stair_key],
                    door_cells,
                    "circ.%s-stair" % passage,
                    problem.rows,
                    problem.cols,
                )
            )
        model.AddBoolOr(arrivals)

    if problem.entry_side is not None:
        entry_candidates = (
            [key for key in passages if room_vars[key].bounds.room.room_type == "foyer"]
            or passages
            or sorted(fallback)
        )
        if entry_candidates:
            key = entry_candidates[0]
            literals = _boundary_literals(model, room_vars[key], footprint, "entry.%s" % key)
            model.Add(literals[problem.entry_side] == 1)
            if problem.entry_frontage_cells > 0:
                # The main door lands on this room's external span on the entry
                # side, so the side ALONG that boundary must carry it (§5.3 main
                # door width + margins, snap-proofed like the serving spans).
                entry_vars = room_vars[key]
                along = entry_vars.w if problem.entry_side in ("N", "S") else entry_vars.h
                model.Add(along >= problem.entry_frontage_cells)

    circulation_keys = sorted(
        key for key, vars_ in room_vars.items() if vars_.bounds.room.is_circulation
    )
    if not circulation_keys:
        return None
    circulation_area = sum(room_vars[key].area for key in circulation_keys)
    # §5.6's circulation cap is a HARD gate, so pruning candidates that cannot pass
    # it is worth doing here rather than spending search budget on them. What this
    # bound must NOT do is prune a candidate the gate would have PASSED — a pruning
    # rule stricter than the gate does not save time, it deletes real answers.
    #
    # It was doing exactly that. The gate measures the WHOLE BUILDING
    # (``pipeline.py`` passes every storey's placements with `footprint = occupied`
    # summed across storeys), while this constraint is built per storey against one
    # storey's net. Those are different numbers, and the per-storey one is harsher
    # wherever a storey is small: the staircase is counted as circulation on every
    # floor it serves and it does not shrink, so on a 54 m² floor the stair alone is
    # ~13% and almost the whole budget is gone before a passage exists.
    #
    # That is what made a SPARSE storey infeasible. Exact tiling (§5.2) forces
    # Σ room areas == net footprint, the habitable rooms are capped at
    # MAX_FRACTION_OF_TARGET, so the leftover has nowhere to go but the passage —
    # and the passage is what this line caps. Measured on a 2BHK G+1, 50×80 ft:
    # INFEASIBLE at 18%, and at 25% the solution CP-SAT then finds uses 9.3% and
    # 3.3% circulation — comfortably inside the real gate. The constraint was
    # rejecting a layout that passes §5.6.
    #
    # So the in-model bound is deliberately looser than the gate. It still prunes
    # the junk the original note was written for (the 24% and 27% candidates), and
    # the objective below keeps pulling circulation toward the target, so a storey
    # only spends this headroom when tiling leaves it no choice. The 18% gate is
    # unchanged and still decides what an architect is shown.
    model.Add(100 * circulation_area <= MODEL_CIRCULATION_PRUNE_PERCENT * footprint["net"])
    excess = model.NewIntVar(0, problem.cols * problem.rows * 100, "circ.excess")
    model.Add(
        excess
        >= 100 * circulation_area - problem.weights.circulation_target_percent * footprint["net"]
    )
    # /100 back to cells, floored — an integer var tied by two inequalities.
    excess_cells = model.NewIntVar(0, problem.cols * problem.rows, "circ.excess_cells")
    model.Add(100 * excess_cells >= excess - 99)
    model.Add(100 * excess_cells <= excess + 99)
    objective.penalise(problem.weights.circulation_excess, excess_cells)
    return circulation_area


def add_area_targets(
    model: Any,
    room_vars: dict[str, _RoomVars],
    problem: _StoreyProblem,
    objective: _Objective,
) -> None:
    """§5.2 target-area deviation: shortfall only (growth is tiling slack, not sin)."""
    for key in sorted(room_vars):
        vars_ = room_vars[key]
        target = vars_.bounds.target_area_cells
        if vars_.bounds.fixed_rect is not None or target <= 0:
            continue
        shortfall = model.NewIntVar(0, target, "%s.shortfall" % key)
        model.Add(shortfall >= target - vars_.area)
        objective.penalise(problem.weights.area_shortfall, shortfall)


def add_compactness(
    model: Any, footprint: dict[str, Any], problem: _StoreyProblem, objective: _Objective
) -> None:
    """§5.2 compactness: penalise the footprint half-perimeter."""
    objective.penalise(problem.weights.compactness, footprint["fw"])
    objective.penalise(problem.weights.compactness, footprint["fh"])


def build_objective(model: Any, objective: _Objective, problem: _StoreyProblem) -> Any:
    """Minimise Σ penalties − Σ rewards, materialised as one integer variable so the
    solved objective is exact (goldens compare integers, never floats)."""
    bound = (problem.cols * problem.rows + 1) * 1000
    total = model.NewIntVar(-bound, bound, "objective")
    expression = sum(coefficient * var for coefficient, var in objective.penalties) - sum(
        coefficient * var for coefficient, var in objective.rewards
    )
    model.Add(total == expression)
    model.Minimize(total)
    return total


# ---------------------------------------------------------------------------
# solving
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StoreySolution:
    placements: tuple[RoomPlacement, ...]
    circulation_cells: int
    objective: int
    footprint: tuple[int, int, int, int]
    shaft_rect: tuple[int, int, int, int] | None


def _apply_profile(
    solver: Any, profile: Any, params: SolveParams, time_budget_seconds: int | None
) -> None:
    """Map a ``SolverProfile`` onto ``solver.parameters`` (see module docstring for
    the conflicts-for-branches note). Duck-typed so this module never imports the
    pipeline (stages.py may one day import this module; cycles stay impossible)."""
    solver.parameters.num_search_workers = int(getattr(profile, "num_search_workers", 8))
    if hasattr(profile, "seed_for"):
        seed = int(profile.seed_for(params))
    else:
        seed = int(getattr(profile, "random_seed", None) or params.seed)
    solver.parameters.random_seed = seed % 2147483647
    if time_budget_seconds:
        solver.parameters.max_time_in_seconds = float(time_budget_seconds)
    max_branches = getattr(profile, "max_branches", None)
    if max_branches:
        solver.parameters.max_number_of_conflicts = int(max_branches)


def _solve_storey(
    problem: _StoreyProblem,
    params: SolveParams,
    profile: Any,
    time_budget_seconds: int | None,
    grid_origin: Pt,
    module_mm: int,
) -> _StoreySolution | None:
    """Build and solve one storey's CpModel. ``None`` == infeasible (expected)."""
    from ortools.sat.python import cp_model

    # Pure feasibility floor first — cheaper than proving UNSAT with a solver.
    if minimum_cells_needed(problem.rooms) > problem.net_cap_cells:
        return None

    model = cp_model.CpModel()
    room_vars = add_room_variables(model, problem)
    if problem.shaft_fixed_rect is not None:
        # Vertical duct continuity: this storey's shaft must CONTAIN the rect
        # the storey below chose — the duct passes through — while keeping its
        # own elastic shape (equality re-created the pinwheel trap upstairs).
        sx1, sy1, sx2, sy2 = problem.shaft_fixed_rect
        for vars_ in room_vars.values():
            if vars_.bounds.room.room_type == "shaft" and vars_.bounds.fixed_rect is None:
                model.Add(vars_.x1 <= sx1)
                model.Add(vars_.y1 <= sy1)
                model.Add(vars_.x2 >= sx2)
                model.Add(vars_.y2 >= sy2)
    add_no_overlap(model, room_vars, problem)
    add_size_bounds(model, room_vars, problem)
    footprint = add_footprint(model, room_vars, problem)
    add_tiling(model, room_vars, footprint)
    add_clear_bounds(model, room_vars, footprint, problem, module_mm=module_mm)
    add_required_adjacencies(model, room_vars, problem, module_mm=module_mm)

    objective = _Objective(penalties=[], rewards=[])
    add_adjacency_wishes(model, room_vars, problem, objective, module_mm=module_mm)
    add_external_face(model, room_vars, footprint, problem, objective)
    if add_vastu_zones(model, room_vars, problem, objective) is False:
        return None
    add_wet_cluster(model, room_vars, problem, objective)
    add_circulation(model, room_vars, footprint, problem, objective, module_mm=module_mm)
    add_area_targets(model, room_vars, problem, objective)
    add_compactness(model, footprint, problem, objective)
    total = build_objective(model, objective, problem)

    # Solution hint (see band_hint): repairs first-feasible time from 5-25s to
    # sub-second on realistic programs. Hints are advisory — CP-SAT repairs or
    # abandons them — so a poor hint costs nothing but the milliseconds it took.
    hints = band_hint(problem)
    if hints:
        fx1, fy1, fx2, fy2 = hints.pop("__footprint__")
        if problem.footprint_fixed is None:
            model.AddHint(footprint["fx1"], fx1)
            model.AddHint(footprint["fy1"], fy1)
            model.AddHint(footprint["fx2"], fx2)
            model.AddHint(footprint["fy2"], fy2)
        for key, (hx1, hy1, hx2, hy2) in sorted(hints.items()):
            vars_ = room_vars.get(key)
            if vars_ is None or vars_.bounds.fixed_rect is not None:
                continue
            model.AddHint(vars_.x1, hx1)
            model.AddHint(vars_.y1, hy1)
            model.AddHint(vars_.w, max(1, hx2 - hx1))
            model.AddHint(vars_.h, max(1, hy2 - hy1))

    solver = cp_model.CpSolver()
    _apply_profile(solver, profile, params, time_budget_seconds)

    max_solutions = getattr(profile, "max_solutions", None)
    if max_solutions:

        class _StopAfterSolutions(cp_model.CpSolverSolutionCallback):
            def __init__(self, limit: int) -> None:
                cp_model.CpSolverSolutionCallback.__init__(self)
                self._left = limit

            def on_solution_callback(self) -> None:
                self._left -= 1
                if self._left <= 0:
                    self.StopSearch()

        status = solver.Solve(model, _StopAfterSolutions(int(max_solutions)))
    else:
        status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # WHICH failure matters, and this line is the only place that knows.
        # INFEASIBLE means the storey's program cannot be laid out in this
        # envelope at all — no amount of time helps, and the caller should say
        # so. UNKNOWN/MODEL_INVALID mean the budget ran out mid-search, where a
        # bigger budget or a different anchor might well succeed. Collapsing both
        # to a bare `return None` is why "Generate produced nothing" has never
        # been answerable: the one bit of information that separates "impossible"
        # from "slow" was discarded here.
        log.info(
            "solver.stage_a.no_layout",
            storey=problem.storey_index,
            status=solver.StatusName(status),
            wall_time_s=round(solver.WallTime(), 2),
            rooms=len(room_vars),
            net_cap_cells=problem.net_cap_cells,
            net_floor_cells=problem.net_floor_cells,
            net_ceiling_cells=problem.net_ceiling_cells,
        )
        return None

    ox, oy = grid_origin
    placements: list[RoomPlacement] = []
    circulation_cells = 0
    shaft_rect: tuple[int, int, int, int] | None = None
    for key in sorted(room_vars):
        vars_ = room_vars[key]
        x1 = solver.Value(vars_.x1)
        y1 = solver.Value(vars_.y1)
        x2 = solver.Value(vars_.x2)
        y2 = solver.Value(vars_.y2)
        if vars_.bounds.room.room_type == "shaft":
            shaft_rect = (x1, y1, x2, y2)
        if vars_.bounds.room.is_circulation:
            circulation_cells += (x2 - x1) * (y2 - y1)
        placements.append(
            RoomPlacement(
                room_key=key,
                room_type=vars_.bounds.room.room_type,
                storey_index=problem.storey_index,
                x_mm=ox + x1 * module_mm,
                y_mm=oy + y1 * module_mm,
                width_mm=(x2 - x1) * module_mm,
                depth_mm=(y2 - y1) * module_mm,
                room_id=vars_.bounds.room.room_id,
            )
        )
    return _StoreySolution(
        placements=tuple(placements),
        circulation_cells=circulation_cells,
        objective=solver.Value(total),
        footprint=(
            solver.Value(footprint["fx1"]),
            solver.Value(footprint["fy1"]),
            solver.Value(footprint["fx2"]),
            solver.Value(footprint["fy2"]),
        ),
        shaft_rect=shaft_rect,
    )


def _solve_storey_via_candidates(
    problem: _StoreyProblem,
    params: SolveParams,
    profile: Any,
    budget: int | None,
    grid_origin: Pt,
    module_mm: int,
) -> _StoreySolution | None:
    """Free-footprint model first, deterministic fixed rectangles as the rescue.

    The free model with the elastic shaft finds solutions in seconds; it gets
    two thirds of the storey budget. When it times out UNKNOWN, the fixed
    rectangles from :func:`footprint_candidates` — each a far easier model —
    spend the rest. Order is deterministic; the first feasible solution wins.
    """
    if problem.footprint_fixed is not None:
        return _solve_storey(problem, params, profile, budget, grid_origin, module_mm)
    if problem.net_ceiling_cells > 0:
        # Lead-storey sizing is an ESCALATION, not a guess: the tight ceiling
        # keeps the frame compact when the estimate is right, and a wrong
        # estimate costs one retry instead of an infeasible anchor.
        ceilings = (problem.net_ceiling_cells, (problem.net_ceiling_cells * 5) // 4, 0)
        per = None if budget is None else max(2, budget // len(ceilings))
        for ceiling in ceilings:
            attempt = replace(problem, net_ceiling_cells=ceiling)
            solution = _solve_storey(attempt, params, profile, per, grid_origin, module_mm)
            if solution is not None:
                return solution
        return None
    main_budget = None if budget is None else max(2, (budget * 2) // 3)
    solution = _solve_storey(problem, params, profile, main_budget, grid_origin, module_mm)
    if solution is not None:
        return solution
    rects = footprint_candidates(problem)
    if rects:
        per_rect = None if budget is None else max(2, (budget - (main_budget or 0)) // len(rects))
        for rect in rects:
            fixed = replace(problem, footprint_fixed=rect)
            solution = _solve_storey(fixed, params, profile, per_rect, grid_origin, module_mm)
            if solution is not None:
                return solution
    return None


def _bounds_for_storey(
    program: RoomProgram,
    storey_index: int,
    stair_rect: tuple[int, int, int, int] | None,
    shaft_rect: tuple[int, int, int, int] | None,
    module_mm: int,
) -> tuple[RoomBounds, ...]:
    out: list[RoomBounds] = []
    for room in sorted(program.packed_rooms_for_storey(storey_index), key=lambda r: r.key):
        bounds = bounds_for(room, module_mm=module_mm)
        if room.room_type == "staircase" and stair_rect is not None:
            bounds = replace(bounds, fixed_rect=stair_rect)
        elif room.room_type == "shaft":
            # ELASTIC on every storey, never 1×1 and never inherited as a fixed
            # rect. A 1×1 cell among normal-sized rectangles can only tile as a
            # four-room pinwheel — CP-SAT burned whole budgets hunting for one —
            # and freezing the ground's exact strip upstairs re-created the same
            # trap around whatever shape the ground picked (both execution
            # finds). Up to 12 cells ≈ 1.1m², aspect ≤ 6: a slim light-well
            # strip that fills a column end like any other room. Vertical duct
            # continuity is a CONTAINMENT constraint over the storey below's
            # rect (``_StoreyProblem.shaft_fixed_rect``), added in the model.
            bounds = replace(
                bounds,
                min_side_cells=1,
                min_area_cells=1,
                max_area_cells=12,
                max_aspect_x100=600,
            )
        out.append(bounds)
    return tuple(out)


#: How many times one anchor may move a room to another floor before giving up. Two
#: clears a bedroom and its bath off an overloaded storey; past that the programme is
#: the problem, not its distribution, and the honest answer is the shortfall banner.
MAX_REBALANCE_PASSES = 2


def stage_a_topology(
    grid: Any,
    params: SolveParams,
    anchor: StairAnchor,
    *,
    profile: Any = None,
    relaxed: bool = False,
    time_budget_seconds: int | None = None,
    num_search_workers: int | None = None,
    program: RoomProgram | None = None,
    rulepack_root: str | None = None,
    weights: StageAWeights | None = None,
    shortfalls: list[Any] | None = None,
) -> Candidate | None:
    """§5.2 stage A, with one rescue: move a room downstairs and try again.

    The default programme puts every bedroom-ish room upstairs. On a generous plot that
    is right. On a 30x40 ft G+1 it puts three bedrooms and two baths on one 7x9 m plate
    that cannot be tiled — while the ground floor sits half empty — and the run returns
    nothing at all. The areas fit; the arrangement does not.

    An architect's answer is not to shrink a bedroom. It is to put the guest room
    downstairs, which is the ordinary arrangement in an Indian G+1 anyway, and it is
    exactly what the seeded demo brief does by hand — the workaround that let the demo
    be the only project in this product that ever generated anything.

    The move happens only AFTER a storey has actually failed to tile, and only for an
    ``arrangement`` shortfall — never for ``area``, where the programme genuinely does
    not fit and moving a room merely relocates the problem. A plan that already solves
    is never reshaped by a rule that exists to rescue one that does not.
    """
    active = program if program is not None else program_from_params(params, root=rulepack_root)
    for _ in range(MAX_REBALANCE_PASSES + 1):
        local: list[Any] = []
        candidate = _stage_a_topology_once(
            grid,
            params,
            anchor,
            profile=profile,
            relaxed=relaxed,
            time_budget_seconds=time_budget_seconds,
            num_search_workers=num_search_workers,
            program=active,
            rulepack_root=rulepack_root,
            weights=weights,
            shortfalls=local,
        )
        if shortfalls is not None:
            shortfalls.extend(local)
        if candidate is not None:
            return candidate
        stuck = sorted(
            {s.storey_index for s in local if getattr(s, "kind", "") == "arrangement"},
            reverse=True,
        )
        moved = None
        for storey in stuck:
            moved = rebalance_off_storey(active, storey)
            if moved is not None:
                break
        if moved is None:
            return None
        log.info(
            "solver.stage_a.rebalanced",
            anchor=anchor.id,
            storey=stuck[0] if stuck else None,
            reason=moved.assumptions[-1].reason if moved.assumptions else "",
        )
        active = moved
    return None


def _stage_a_topology_once(
    grid: Any,
    params: SolveParams,
    anchor: StairAnchor,
    *,
    profile: Any = None,
    relaxed: bool = False,
    time_budget_seconds: int | None = None,
    num_search_workers: int | None = None,
    program: RoomProgram | None = None,
    rulepack_root: str | None = None,
    weights: StageAWeights | None = None,
    shortfalls: list[Any] | None = None,
) -> Candidate | None:
    """§5.2 stage A for one stair candidate. ``None`` == infeasible, not an error.

    Accepts both calling generations the pipeline supports: the ``profile`` keyword
    (preferred; a ``SolverProfile``) and the legacy ``time_budget_seconds`` /
    ``num_search_workers`` pair. ``grid`` is duck-typed (``GridSpec`` or
    :class:`services.solver.grid.Grid` — anything with origin/module_mm/cols/rows/mask),
    so §5.7's obstacle-transformed grids pass through unchanged.
    """
    from services.solver.stages import Candidate  # lazy: keeps stages↔stage_a cycle-proof

    if profile is None:
        profile = _LegacyProfile(
            num_search_workers=num_search_workers or 8,
            time_budget_seconds=time_budget_seconds,
        )
    active_weights = weights or DEFAULT_WEIGHTS
    if relaxed:
        active_weights = active_weights.relaxed()

    module_mm = int(getattr(grid, "module_mm", COARSE_MODULE_MM))
    grid_origin: Pt = tuple(grid.origin)  # type: ignore[assignment]
    cols = int(grid.cols)
    rows = int(grid.rows)
    mask = grid.mask

    if program is None:
        program = program_from_params(params, root=rulepack_root)

    envelope = derive_envelope(
        params.plot_polygon, params.edges, params.profile, storeys=params.storeys
    )
    voids = void_rects_of_mask(mask)
    net_cap = min(net_footprint_cap_cells(params, module_mm=module_mm), cols * rows)

    has_stair_room = any(room.room_type == "staircase" for room in program.rooms if room.packed)
    stair_rect: tuple[int, int, int, int] | None = None
    stair_side: str | None = None
    if has_stair_room:
        dogleg = stairs_mod.size_dogleg(DEFAULT_STOREY_HEIGHT_MM, root=rulepack_root)
        well_mm = stairs_mod.well_rect_for(anchor, envelope, stair=dogleg, module_mm=module_mm)
        base_rect = mm_rect_to_cells(well_mm, grid_origin, module_mm=module_mm)
        stair_side = stairs_mod.edge_outward_side(envelope.polygon, anchor.edge_index)
        # The stair room's CLEAR polygon — inside the wall faces, after the
        # 115mm snap — must still hold the dogleg. Sizing the room to the bare
        # well let stage B discard every candidate with STAIR_DOES_NOT_FIT
        # (a 2700×1800 room clears only ~2358×1553 — execution find), so the
        # cell rect grows by the per-axis insets and snap loss up front: the
        # axis NORMAL to the hugged edge carries the external wall (230+58),
        # the along-edge axis its internal walls (57+58) — except at a corner
        # anchor, whose along-edge end can be external too.
        stair_losses = snap_loss_table(max(cols, rows), module_mm=module_mm)
        along_inset = (
            _AXIS_INSET_INTERNAL_MM if anchor.id.endswith("-mid") else _AXIS_INSET_TYPICAL_MM
        )
        if stair_side in ("N", "S"):
            inset_x, inset_y = along_inset, _AXIS_INSET_TYPICAL_MM
        else:
            inset_x, inset_y = _AXIS_INSET_TYPICAL_MM, along_inset
        need_x = _cells_for_clear(well_mm[2] - well_mm[0], 1, stair_losses, inset_mm=inset_x)
        need_y = _cells_for_clear(well_mm[3] - well_mm[1], 1, stair_losses, inset_mm=inset_y)
        # Keep the flush edge where the anchor put it; clamp the free corner.
        sx1 = max(0, min(base_rect[0], cols - need_x))
        sy1 = max(0, min(base_rect[1], rows - need_y))
        stair_rect = (sx1, sy1, sx1 + need_x, sy1 + need_y)
        if not (
            0 <= stair_rect[0] < stair_rect[2] <= cols
            and 0 <= stair_rect[1] < stair_rect[3] <= rows
        ):
            log.info("solver.stage_a.stair_outside_grid", anchor=anchor.id)
            return None

    bands = zone_bands_cells2(
        zone_bands_mm(bbox(params.plot_polygon), params.north_deg),
        grid_origin,
        module_mm=module_mm,
    )
    entry, entry_notes = entry_grid_side(params, program)
    for note in entry_notes:
        log.info("solver.stage_a.entry_note", note=note)

    # Door-frontage floors, from the SAME pack rows stage B's door placement
    # reads — so the serving span this model guarantees is one a door provably
    # fits after the 115mm snap (width + 2×115 end margins, snap worst case).
    from services.solver.openings import (
        ARCHWAY_END_MARGIN_MM,
        WALL_END_MARGIN_MM,
        door_width_for,
        load_nbc_limits,
    )

    opening_limits = load_nbc_limits(root=rulepack_root)
    door_cells_by_key: dict[str, int] = {}
    for room in program.rooms:
        # Circulation rooms too: stage B wants a door or archway INTO a passage
        # or stair as much as into a bedroom, with the same end margins.
        if not room.packed or room.room_type == "shaft":
            continue
        width = door_width_for(room.room_type, opening_limits)
        # A framed door keeps a pier at each end; a cased archway into a
        # circulation room keeps only the model's minimum (openings.py).
        margin = ARCHWAY_END_MARGIN_MM if room.is_circulation else WALL_END_MARGIN_MM
        door_cells_by_key[room.key] = min_frontage_cells(width + 2 * margin, module_mm=module_mm)
    entry_frontage_cells = min_frontage_cells(
        opening_limits.door_main_min_mm + 2 * WALL_END_MARGIN_MM, module_mm=module_mm
    )
    arrival_cells = min_frontage_cells(
        opening_limits.door_internal_min_mm + 2 * ARCHWAY_END_MARGIN_MM, module_mm=module_mm
    )

    # CLEAR floors come from the PACK — the numbers the §5.4 critic hard-fails
    # on. Brief widths/areas remain gross domain floors (see the field comment).
    from services.solver.program import load_room_minima

    minima = load_room_minima(rulepack_root)
    clear_floor_by_key: dict[str, tuple[int, int]] = {}
    for room in program.rooms:
        if not room.packed or room.room_type == "shaft":
            continue
        nbc_area, nbc_width, _cite = minima.floor_for(room.room_type)
        if room.is_circulation:
            # No pack row for a passage/foyer; the §5.3 playbook's 900mm least
            # width is the clear floor. NOT the room's own min_width_mm — for a
            # brief-authored foyer that is a gross wish, and promoting it to a
            # clear bound re-tightened the model (execution find).
            nbc_area, nbc_width = 0, max(nbc_width, DOOR_FRONTAGE_MM)
        if nbc_area > 0 or nbc_width > 0:
            clear_floor_by_key[room.key] = (nbc_area, nbc_width)

    budgets = split_time_budget(getattr(profile, "time_budget_seconds", None), program.storeys)

    # §5.2 multi-floor, generalised by execution: solve the MOST CONSTRAINED
    # storey first — it defines the frame — then grow downward and shrink
    # upward. Ground-first repeatedly picked footprints the upper program could
    # not tile (and a net-floor patch just moved the contradiction); with the
    # tight storey leading, every storey below merely has to CONTAIN its
    # rectangle (easy: lower storeys hold the small flexible rooms) and every
    # storey above fits WITHIN the one below it. The stair rect, the stair-side
    # flush and the duct-containment carry the §5.2 continuity.
    floor_losses = snap_loss_table(max(cols, rows))
    min_net_by_storey = {
        index: storey_min_net_cells(
            _bounds_for_storey(program, index, stair_rect, None, module_mm),
            floor_losses,
        )
        for index in range(program.storeys)
    }
    solve_order = sorted(range(program.storeys), key=lambda i: (-min_net_by_storey[i], i))

    solutions: dict[int, _StoreySolution] = {}
    shaft_core: tuple[int, int, int, int] | None = None
    for position, storey_index in enumerate(solve_order):
        below = max((j for j in solutions if j < storey_index), default=None)
        above = min((j for j in solutions if j > storey_index), default=None)
        problem = _StoreyProblem(
            storey_index=storey_index,
            rooms=_bounds_for_storey(program, storey_index, stair_rect, None, module_mm),
            cols=cols,
            rows=rows,
            voids=voids,
            net_cap_cells=net_cap,
            weights=active_weights,
            adjacency=tuple(
                spec for spec in program.adjacency if _both_on_storey(program, spec, storey_index)
            ),
            stair_side=stair_side,
            entry_side=entry if storey_index == 0 else None,
            footprint_within=solutions[below].footprint if below is not None else None,
            footprint_contains=solutions[above].footprint if above is not None else None,
            # No HARD cross-storey shaft link: transplanting the lead storey's
            # shaft rect provably killed the follower (execution find), and the
            # §5.4 critic already scores duct alignment softly via
            # ``score_plumbing_stack`` — soft is the design's own lever here.
            shaft_fixed_rect=None,
            zone_bands=bands,
            vastu_mode=program.vastu_mode,
            north_deg=params.north_deg,
            door_cells_by_key=door_cells_by_key,
            entry_frontage_cells=entry_frontage_cells,
            arrival_cells=arrival_cells,
            clear_floor_by_key=clear_floor_by_key,
            # The lead storey of a multi-storey solve is kept close to its own
            # minimum program (110%); see the field comment. Later storeys are
            # sized by their containment relations instead.
            net_ceiling_cells=(
                (min_net_by_storey[storey_index] * 11) // 10
                if position == 0 and program.storeys > 1
                else 0
            ),
        )
        if not problem.rooms:
            log.info("solver.stage_a.empty_storey", storey=storey_index, anchor=anchor.id)
            return None
        solution = _solve_storey_via_candidates(
            problem, params, profile, budgets[position], grid_origin, module_mm
        )
        if solution is None:
            # Say WHY, not just that. "infeasible" was the only word this had, which
            # made the Options screen's "no workable layout" the end of the road for
            # the architect and a bisection for anyone debugging it.
            shortfall = diagnose_storey(
                storey_index=storey_index,
                rooms=problem.rooms,
                cols=problem.cols,
                rows=problem.rows,
                net_cap_cells=problem.net_cap_cells,
                module_mm=module_mm,
            )
            log.info(
                "solver.stage_a.infeasible",
                storey=storey_index,
                anchor=anchor.id,
                relaxed=relaxed,
                shortfall=shortfall.kind,
                proved=shortfall.proved,
                reason=shortfall.message,
            )
            if shortfalls is not None:
                shortfalls.append(shortfall)
            return None
        solutions[storey_index] = solution
        if shaft_core is None:
            # The lead storey's shaft is the duct core every other storey's
            # shaft must contain (vertical plumbing continuity).
            shaft_core = solution.shaft_rect

    cell_area = module_mm * module_mm
    placements: list[RoomPlacement] = []
    for storey_index in sorted(solutions):
        placements.extend(solutions[storey_index].placements)
    ordered = [solutions[index] for index in sorted(solutions)]
    return Candidate(
        stair_anchor=anchor,
        placements=tuple(placements),
        circulation_area_mm2=sum(s.circulation_cells for s in ordered) * cell_area,
        objective=sum(s.objective for s in ordered),
    )


def _both_on_storey(program: RoomProgram, spec: Any, storey_index: int) -> bool:
    keys = {room.key for room in program.packed_rooms_for_storey(storey_index)}
    return spec.a_key in keys and spec.b_key in keys


@dataclass(frozen=True)
class _LegacyProfile:
    """Adapter for the Phase-2 stub signature (no SolverProfile in sight)."""

    num_search_workers: int = 8
    time_budget_seconds: int | None = None
    random_seed: int | None = None
    max_solutions: int | None = None
    max_branches: int | None = None

    def seed_for(self, params: SolveParams) -> int:
        return self.random_seed if self.random_seed is not None else params.seed


# ---------------------------------------------------------------------------
# CellLayout surface — the typed per-storey view stage B consumes (ortools-free)
# ---------------------------------------------------------------------------


def layouts_for(candidate: Candidate, envelope_polygon: Sequence[Pt]) -> tuple[CellLayout, ...]:
    """One :class:`services.solver.walls.CellLayout` per storey of a candidate.

    Pure adapter: groups placements by storey and hands them to
    ``CellLayout.from_placements`` with the snap origin anchored at the envelope
    bbox minimum — the same origin the solve grid used, so the 115mm snap in stage B
    moves shared edges together and the tiling survives refinement.
    """
    from services.solver.walls import CellLayout

    min_x, min_y, _, _ = bbox(tuple(envelope_polygon))
    by_storey: dict[int, list[RoomPlacement]] = {}
    for placement in candidate.placements:
        by_storey.setdefault(placement.storey_index, []).append(placement)
    return tuple(
        CellLayout.from_placements(by_storey[index], snap_origin=(min_x, min_y))
        for index in sorted(by_storey)
    )


__all__ = [
    "APART_MIN_CELLS",
    "MODEL_CIRCULATION_PRUNE_PERCENT",
    "DEFAULT_WEIGHTS",
    "RoomBounds",
    "StageAWeights",
    "add_adjacency_wishes",
    "add_area_targets",
    "add_circulation",
    "add_clear_bounds",
    "add_compactness",
    "add_external_face",
    "add_footprint",
    "add_no_overlap",
    "add_required_adjacencies",
    "add_room_variables",
    "add_size_bounds",
    "add_tiling",
    "add_vastu_zones",
    "add_wet_cluster",
    "band_hint",
    "bounds_for",
    "build_objective",
    "footprint_candidates",
    "gross_min_dims",
    "storey_min_net_cells",
    "entry_grid_side",
    "layouts_for",
    "min_frontage_cells",
    "minimum_cells_needed",
    "mm_rect_to_cells",
    "net_footprint_cap_cells",
    "split_time_budget",
    "stage_a_topology",
    "zone_bands_cells2",
]
