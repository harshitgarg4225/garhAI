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

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence, Tuple

from services.common.logging import get_logger
from services.solver import stairs as stairs_mod
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
    FALLBACK_DISTRIBUTOR_TYPES,
    DEFAULT_STOREY_HEIGHT_MM,
    DOOR_FRONTAGE_MM,
    ProgramRoom,
    RoomProgram,
    program_from_params,
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

    def relaxed(self) -> "StageAWeights":
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
    fixed_rect: Optional[Tuple[int, int, int, int]] = None

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


def net_footprint_cap_cells(
    params: SolveParams, *, module_mm: int = COARSE_MODULE_MM
) -> int:
    """The per-floor built-area ceiling in cells: min(coverage cap, FAR cap / floors).

    Floor division throughout — rounding DOWN a regulatory cap is the conservative
    direction (§5.1's envelope maths made the same call).
    """
    plot_area = params.plot_area_mm2()
    coverage = params.profile.allowed_footprint_mm2(plot_area)
    far_per_floor = params.profile.allowed_built_up_mm2(plot_area) // max(1, params.storeys)
    return min(coverage, far_per_floor) // (module_mm * module_mm)


def zone_bands_cells2(
    bands_mm: Optional[Mapping[str, Tuple[int, int, int, int]]],
    grid_origin: Pt,
    *,
    module_mm: int = COARSE_MODULE_MM,
) -> Optional[Dict[str, Tuple[int, int, int, int]]]:
    """Zone mm bands → inclusive bounds on the DOUBLED cell centre ``(x1+x2, y1+y2)``.

    A room's centre in cells is ``(x1+x2)/2``; keeping the doubled sum avoids the
    division. For zone band ``[zx1, zx2]`` mm the constraint is
    ``2*(zx1-ox) <= (x1+x2)*module <= 2*(zx2-ox)`` — returned here as inclusive
    integer bounds on ``x1+x2`` (ceil on the low side, floor on the high side).
    """
    if bands_mm is None:
        return None
    ox, oy = grid_origin
    out: Dict[str, Tuple[int, int, int, int]] = {}
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
    rect_mm: Tuple[int, int, int, int], grid_origin: Pt, *, module_mm: int = COARSE_MODULE_MM
) -> Tuple[int, int, int, int]:
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


def split_time_budget(total_seconds: Optional[int], storeys: int) -> Tuple[Optional[int], ...]:
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


def entry_grid_side(params: SolveParams, program: RoomProgram) -> Tuple[str, Tuple[str, ...]]:
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


# ---------------------------------------------------------------------------
# the CP-SAT model — every constraint family is its own small builder (§16)
# ---------------------------------------------------------------------------


@dataclass
class _StoreyProblem:
    """Everything one storey's CpModel needs. Plain data, ortools-free."""

    storey_index: int
    rooms: Tuple[RoomBounds, ...]
    cols: int
    rows: int
    voids: Tuple[CellRect, ...]
    net_cap_cells: int
    weights: StageAWeights
    adjacency: Tuple[Any, ...] = ()  # program.AdjacencySpec, filtered to present rooms
    stair_side: Optional[str] = None
    entry_side: Optional[str] = None
    footprint_fixed: Optional[Tuple[int, int, int, int]] = None
    shaft_fixed_rect: Optional[Tuple[int, int, int, int]] = None
    zone_bands: Optional[Dict[str, Tuple[int, int, int, int]]] = None
    vastu_mode: str = "advisory"
    north_deg: int = 0


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

    penalties: List[Tuple[int, Any]]
    rewards: List[Tuple[int, Any]]

    def penalise(self, coefficient: int, var: Any) -> None:
        if coefficient:
            self.penalties.append((coefficient, var))

    def reward(self, coefficient: int, var: Any) -> None:
        if coefficient:
            self.rewards.append((coefficient, var))


def add_room_variables(model: Any, problem: _StoreyProblem) -> Dict[str, _RoomVars]:
    """Interval vars per room in x and y (§5.2). Fixed rects become fixed vars so
    every downstream builder treats pinned and free rooms identically."""
    out: Dict[str, _RoomVars] = {}
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


def add_no_overlap(model: Any, room_vars: Dict[str, _RoomVars], problem: _StoreyProblem) -> None:
    """``AddNoOverlap2D`` over rooms + the mandatory-void rectangles (§5.2 L/T)."""
    xs = [vars_.ix for _, vars_ in sorted(room_vars.items())]
    ys = [vars_.iy for _, vars_ in sorted(room_vars.items())]
    for index, void in enumerate(problem.voids):
        xs.append(
            model.NewIntervalVar(void.col1, void.cols, void.col2, "void%d.ix" % index)
        )
        ys.append(
            model.NewIntervalVar(void.row1, void.rows, void.row2, "void%d.iy" % index)
        )
    model.AddNoOverlap2D(xs, ys)


def add_size_bounds(model: Any, room_vars: Dict[str, _RoomVars], problem: _StoreyProblem) -> None:
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
    model: Any, room_vars: Dict[str, _RoomVars], problem: _StoreyProblem
) -> Dict[str, Any]:
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
    else:
        fx1 = model.NewIntVar(0, problem.cols, "f.x1")
        fy1 = model.NewIntVar(0, problem.rows, "f.y1")
        fx2 = model.NewIntVar(0, problem.cols, "f.x2")
        fy2 = model.NewIntVar(0, problem.rows, "f.y2")
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

    void_overlaps: List[Any] = []
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


def add_tiling(
    model: Any, room_vars: Dict[str, _RoomVars], footprint: Dict[str, Any]
) -> None:
    """Σ room areas == net footprint area — the CellLayout tiling contract, in-model."""
    model.Add(sum(vars_.area for _, vars_ in sorted(room_vars.items())) == footprint["net"])


def _touch_literals(
    model: Any,
    a: _RoomVars,
    b: _RoomVars,
    min_shared_cells: int,
    prefix: str,
    rows: int,
    cols: int,
) -> List[Any]:
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
    room_vars: Dict[str, _RoomVars],
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
            model, a, b, min_cells, "req.%s-%s" % (spec.a_key, spec.b_key),
            problem.rows, problem.cols,
        )
        model.AddBoolOr(literals)


def add_adjacency_wishes(
    model: Any,
    room_vars: Dict[str, _RoomVars],
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
            literals = _touch_literals(
                model, a, b, min_cells, name, problem.rows, problem.cols
            )
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
    model: Any, vars_: _RoomVars, footprint: Dict[str, Any], prefix: str
) -> Dict[str, Any]:
    """Side → boolean implying the room's edge lies ON that footprint side."""
    out: Dict[str, Any] = {}
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
    room_vars: Dict[str, _RoomVars],
    footprint: Dict[str, Any],
    problem: _StoreyProblem,
    objective: _Objective,
) -> Tuple[str, ...]:
    """§5.2: habitable + kitchen must reach the footprint boundary; baths may stay
    internal only when shaft-adjacent; wet rooms on the boundary earn the
    external-face bonus. Returns notes for skipped must-face constraints."""
    notes: List[str] = []
    shaft = room_vars.get("shaft")
    for key in sorted(room_vars):
        vars_ = room_vars[key]
        room = vars_.bounds.room
        if room.room_type == "shaft":
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
    room_vars: Dict[str, _RoomVars],
    problem: _StoreyProblem,
    objective: _Objective,
) -> Optional[bool]:
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
    band: Optional[Tuple[int, int, int, int]],
    name: str,
) -> Optional[Any]:
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
    room_vars: Dict[str, _RoomVars],
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
    room_vars: Dict[str, _RoomVars],
    footprint: Dict[str, Any],
    problem: _StoreyProblem,
    objective: _Objective,
    *,
    module_mm: int = COARSE_MODULE_MM,
) -> Optional[Any]:
    """§5.2 circulation spine: entry → stair → every room's door zone.

    Topology form: every packed room shares a door-width edge with a distributor
    (circulation rooms; living as the openings.py fallback); the passage touches the
    stair well; the ground distributor touches the entry side of the footprint.
    Circulation area above the target percent is penalised. Returns the circulation
    area expression (cells) for the caller to read back, or ``None`` when the storey
    has no circulation rooms at all (the program synthesises one, so this is a bug
    guard, not a normal path)."""
    door_cells = max(1, _ceil_div(DOOR_FRONTAGE_MM, module_mm))
    distributors = {
        key
        for key, vars_ in room_vars.items()
        if vars_.bounds.room.room_type in _DISTRIBUTOR_TYPES
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
        if key in distributors or vars_.bounds.room.room_type == "shaft":
            continue
        reachable: List[Any] = []
        serving = sorted(distributors | (fallback - {key}))
        for other in serving:
            reachable.extend(
                _touch_literals(
                    model, vars_, room_vars[other], door_cells,
                    "circ.%s-%s" % (key, other), problem.rows, problem.cols,
                )
            )
        if reachable:
            model.AddBoolOr(reachable)

    stair_key = _stair_key(problem)
    passages = sorted(
        key
        for key, vars_ in room_vars.items()
        if vars_.bounds.room.room_type in CIRCULATION_TYPES
    )
    if stair_key in room_vars and passages:
        arrivals: List[Any] = []
        for passage in passages:
            arrivals.extend(
                _touch_literals(
                    model, room_vars[passage], room_vars[stair_key], door_cells,
                    "circ.%s-stair" % passage, problem.rows, problem.cols,
                )
            )
        model.AddBoolOr(arrivals)

    if problem.entry_side is not None:
        entry_candidates = [
            key for key in passages if room_vars[key].bounds.room.room_type == "foyer"
        ] or passages or sorted(fallback)
        if entry_candidates:
            key = entry_candidates[0]
            literals = _boundary_literals(model, room_vars[key], footprint, "entry.%s" % key)
            model.Add(literals[problem.entry_side] == 1)

    circulation_keys = sorted(
        key
        for key, vars_ in room_vars.items()
        if vars_.bounds.room.is_circulation
    )
    if not circulation_keys:
        return None
    circulation_area = sum(room_vars[key].area for key in circulation_keys)
    excess = model.NewIntVar(0, problem.cols * problem.rows * 100, "circ.excess")
    model.Add(
        excess
        >= 100 * circulation_area
        - problem.weights.circulation_target_percent * footprint["net"]
    )
    # /100 back to cells, floored — an integer var tied by two inequalities.
    excess_cells = model.NewIntVar(0, problem.cols * problem.rows, "circ.excess_cells")
    model.Add(100 * excess_cells >= excess - 99)
    model.Add(100 * excess_cells <= excess + 99)
    objective.penalise(problem.weights.circulation_excess, excess_cells)
    return circulation_area


def add_area_targets(
    model: Any,
    room_vars: Dict[str, _RoomVars],
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
    model: Any, footprint: Dict[str, Any], problem: _StoreyProblem, objective: _Objective
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
    placements: Tuple[RoomPlacement, ...]
    circulation_cells: int
    objective: int
    footprint: Tuple[int, int, int, int]
    shaft_rect: Optional[Tuple[int, int, int, int]]


def _apply_profile(
    solver: Any, profile: Any, params: SolveParams, time_budget_seconds: Optional[int]
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
    time_budget_seconds: Optional[int],
    grid_origin: Pt,
    module_mm: int,
) -> Optional[_StoreySolution]:
    """Build and solve one storey's CpModel. ``None`` == infeasible (expected)."""
    from ortools.sat.python import cp_model

    # Pure feasibility floor first — cheaper than proving UNSAT with a solver.
    if minimum_cells_needed(problem.rooms) > problem.net_cap_cells:
        return None

    model = cp_model.CpModel()
    room_vars = add_room_variables(model, problem)
    add_no_overlap(model, room_vars, problem)
    add_size_bounds(model, room_vars, problem)
    footprint = add_footprint(model, room_vars, problem)
    add_tiling(model, room_vars, footprint)
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
        return None

    ox, oy = grid_origin
    placements: List[RoomPlacement] = []
    circulation_cells = 0
    shaft_rect: Optional[Tuple[int, int, int, int]] = None
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


def _bounds_for_storey(
    program: RoomProgram,
    storey_index: int,
    stair_rect: Optional[Tuple[int, int, int, int]],
    shaft_rect: Optional[Tuple[int, int, int, int]],
    module_mm: int,
) -> Tuple[RoomBounds, ...]:
    out: List[RoomBounds] = []
    for room in sorted(program.packed_rooms_for_storey(storey_index), key=lambda r: r.key):
        bounds = bounds_for(room, module_mm=module_mm)
        if room.room_type == "staircase" and stair_rect is not None:
            bounds = replace(bounds, fixed_rect=stair_rect)
        elif room.room_type == "shaft":
            if shaft_rect is not None:
                bounds = replace(bounds, fixed_rect=shaft_rect)
            else:
                bounds = replace(bounds, min_side_cells=1, min_area_cells=1, max_area_cells=1)
        out.append(bounds)
    return tuple(out)


def stage_a_topology(
    grid: Any,
    params: SolveParams,
    anchor: StairAnchor,
    *,
    profile: Any = None,
    relaxed: bool = False,
    time_budget_seconds: Optional[int] = None,
    num_search_workers: Optional[int] = None,
    program: Optional[RoomProgram] = None,
    rulepack_root: Optional[str] = None,
    weights: Optional[StageAWeights] = None,
) -> Optional["Candidate"]:
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
    grid_origin: Pt = tuple(getattr(grid, "origin"))  # type: ignore[assignment]
    cols = int(getattr(grid, "cols"))
    rows = int(getattr(grid, "rows"))
    mask = getattr(grid, "mask")

    if program is None:
        program = program_from_params(params, root=rulepack_root)

    envelope = derive_envelope(
        params.plot_polygon, params.edges, params.profile, storeys=params.storeys
    )
    voids = void_rects_of_mask(mask)
    net_cap = min(net_footprint_cap_cells(params, module_mm=module_mm), cols * rows)

    has_stair_room = any(
        room.room_type == "staircase" for room in program.rooms if room.packed
    )
    stair_rect: Optional[Tuple[int, int, int, int]] = None
    stair_side: Optional[str] = None
    if has_stair_room:
        dogleg = stairs_mod.size_dogleg(
            DEFAULT_STOREY_HEIGHT_MM, root=rulepack_root
        )
        well_mm = stairs_mod.well_rect_for(anchor, envelope, stair=dogleg, module_mm=module_mm)
        stair_rect = mm_rect_to_cells(well_mm, grid_origin, module_mm=module_mm)
        stair_side = stairs_mod.edge_outward_side(envelope.polygon, anchor.edge_index)
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

    budgets = split_time_budget(
        getattr(profile, "time_budget_seconds", None), program.storeys
    )

    solutions: List[_StoreySolution] = []
    footprint_fixed: Optional[Tuple[int, int, int, int]] = None
    shaft_fixed: Optional[Tuple[int, int, int, int]] = None
    for storey_index in range(program.storeys):
        problem = _StoreyProblem(
            storey_index=storey_index,
            rooms=_bounds_for_storey(program, storey_index, stair_rect, shaft_fixed, module_mm),
            cols=cols,
            rows=rows,
            voids=voids,
            net_cap_cells=net_cap,
            weights=active_weights,
            adjacency=tuple(
                spec
                for spec in program.adjacency
                if _both_on_storey(program, spec, storey_index)
            ),
            stair_side=stair_side,
            entry_side=entry if storey_index == 0 else None,
            footprint_fixed=footprint_fixed,
            shaft_fixed_rect=shaft_fixed,
            zone_bands=bands,
            vastu_mode=program.vastu_mode,
            north_deg=params.north_deg,
        )
        if not problem.rooms:
            log.info(
                "solver.stage_a.empty_storey", storey=storey_index, anchor=anchor.id
            )
            return None
        solution = _solve_storey(
            problem, params, profile, budgets[storey_index], grid_origin, module_mm
        )
        if solution is None:
            log.info(
                "solver.stage_a.infeasible",
                storey=storey_index,
                anchor=anchor.id,
                relaxed=relaxed,
            )
            return None
        solutions.append(solution)
        if storey_index == 0:
            # §5.2 multi-floor: fix stair + shafts + external walls, solve uppers.
            footprint_fixed = solution.footprint
            shaft_fixed = solution.shaft_rect

    cell_area = module_mm * module_mm
    placements: List[RoomPlacement] = []
    for solution in solutions:
        placements.extend(solution.placements)
    return Candidate(
        stair_anchor=anchor,
        placements=tuple(placements),
        circulation_area_mm2=sum(s.circulation_cells for s in solutions) * cell_area,
        objective=sum(s.objective for s in solutions),
    )


def _both_on_storey(program: RoomProgram, spec: Any, storey_index: int) -> bool:
    keys = {room.key for room in program.packed_rooms_for_storey(storey_index)}
    return spec.a_key in keys and spec.b_key in keys


@dataclass(frozen=True)
class _LegacyProfile:
    """Adapter for the Phase-2 stub signature (no SolverProfile in sight)."""

    num_search_workers: int = 8
    time_budget_seconds: Optional[int] = None
    random_seed: Optional[int] = None
    max_solutions: Optional[int] = None
    max_branches: Optional[int] = None

    def seed_for(self, params: SolveParams) -> int:
        return self.random_seed if self.random_seed is not None else params.seed


# ---------------------------------------------------------------------------
# CellLayout surface — the typed per-storey view stage B consumes (ortools-free)
# ---------------------------------------------------------------------------


def layouts_for(candidate: "Candidate", envelope_polygon: Sequence[Pt]) -> Tuple["CellLayout", ...]:
    """One :class:`services.solver.walls.CellLayout` per storey of a candidate.

    Pure adapter: groups placements by storey and hands them to
    ``CellLayout.from_placements`` with the snap origin anchored at the envelope
    bbox minimum — the same origin the solve grid used, so the 115mm snap in stage B
    moves shared edges together and the tiling survives refinement.
    """
    from services.solver.walls import CellLayout

    min_x, min_y, _, _ = bbox(tuple(envelope_polygon))
    by_storey: Dict[int, List[RoomPlacement]] = {}
    for placement in candidate.placements:
        by_storey.setdefault(placement.storey_index, []).append(placement)
    return tuple(
        CellLayout.from_placements(by_storey[index], snap_origin=(min_x, min_y))
        for index in sorted(by_storey)
    )


__all__ = [
    "APART_MIN_CELLS",
    "DEFAULT_WEIGHTS",
    "RoomBounds",
    "StageAWeights",
    "add_adjacency_wishes",
    "add_area_targets",
    "add_circulation",
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
    "bounds_for",
    "build_objective",
    "entry_grid_side",
    "layouts_for",
    "minimum_cells_needed",
    "mm_rect_to_cells",
    "net_footprint_cap_cells",
    "split_time_budget",
    "stage_a_topology",
    "zone_bands_cells2",
]
