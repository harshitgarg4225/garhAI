"""§5.2 / §5.3 — CP-SAT topology and refinement. **Phase 3 implements the bodies.**

Every stage is a named, typed, individually testable function with its full signature
and contract written down. The bodies that need OR-Tools raise
``NotImplementedError`` naming Phase 3 rather than returning plausible-looking
placeholder geometry: a stub that returns a fake plan would sail through the pipeline,
the gates and the golden files, and the first honest signal would be an architect
looking at a wrong drawing.

Phase 3's job is to fill in three functions here. The envelope (§5.1) is already real
in :mod:`services.solver.envelope`, and the critic's composite, the diversity filter and
the §5.6 gates are already real in their own modules — so when these three land, the
pipeline is complete rather than half-wired.

Implementation notes for whoever picks this up, drawn from §5.2/§5.3:

* Stage A is a ``CpModel`` per stair candidate: interval vars per room in x and y,
  ``add_no_overlap_2d``, sizes bounded by the brief, aspect ratio 1:1-1:2.2 for
  habitable rooms and 1:3 for baths/stores. Solve them in parallel and keep the best.
* The objective is a weighted sum: target-area deviation, adjacency satisfaction,
  circulation area, external-face bonus, Vastu score (advisory), compactness.
  ``num_search_workers=8``, 15s per stair candidate — both already in ``WorkerSettings``.
* L/T plots are handled as a bounding rectangle with the void cells forced empty, which
  is why :func:`grid_envelope` returns a mask rather than a plain size.
* Stage B snaps to the 115mm module, dedupes shared walls (two rooms sharing an edge get
  ONE wall) and inserts openings; it must run the model invariants and either repair a
  candidate by one module or discard it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from services.solver.geometry import Polygon, Pt, bbox, point_in_polygon
from services.solver.types import (
    COARSE_MODULE_MM,
    BuildableEnvelope,
    RoomPlacement,
    SolveParams,
    StairAnchor,
)

PHASE = "Phase 3 (Layout solver)"


@dataclass(frozen=True)
class GridSpec:
    """The §5.2 coarse grid: a rectangle plus a buildable mask.

    ``mask[row][col]`` is ``True`` where a cell is inside the envelope. L and T plots
    are a rectangle with the notch cells masked out, which is exactly how §5.2 says to
    handle them ("L/T handled by mandatory-void cells").
    """

    origin: Pt
    module_mm: int
    cols: int
    rows: int
    mask: tuple[tuple[bool, ...], ...]

    def buildable_cells(self) -> int:
        return sum(1 for row in self.mask for cell in row if cell)

    def cell_origin(self, col: int, row: int) -> Pt:
        return (
            self.origin[0] + col * self.module_mm,
            self.origin[1] + row * self.module_mm,
        )


@dataclass(frozen=True)
class Candidate:
    """One stage-A solution, before refinement and scoring."""

    stair_anchor: StairAnchor
    placements: tuple[RoomPlacement, ...]
    circulation_area_mm2: int
    objective: int


def grid_envelope(
    envelope: BuildableEnvelope, *, module_mm: int = COARSE_MODULE_MM
) -> GridSpec:
    """Overlay the §5.2 coarse grid on the envelope. **Implemented.**

    Pure integer geometry, so it is real today: a cell is buildable when its centre
    lies inside the envelope polygon. Testing the centre (rather than all four corners)
    is the deliberate choice — it keeps an L-plot's notch crisp instead of eroding the
    envelope by a module on every diagonal.
    """
    if module_mm <= 0:
        raise ValueError("module_mm must be positive, got %d" % module_mm)
    min_x, min_y, max_x, max_y = bbox(envelope.polygon)
    cols = max(0, (max_x - min_x) // module_mm)
    rows = max(0, (max_y - min_y) // module_mm)
    half = module_mm // 2
    mask = tuple(
        tuple(
            point_in_polygon(
                (min_x + col * module_mm + half, min_y + row * module_mm + half),
                envelope.polygon,
            )
            for col in range(cols)
        )
        for row in range(rows)
    )
    return GridSpec(
        origin=(min_x, min_y), module_mm=module_mm, cols=cols, rows=rows, mask=mask
    )


def enumerate_stair_anchors(
    envelope: BuildableEnvelope, params: SolveParams, *, limit: int = 6
) -> tuple[StairAnchor, ...]:
    """§5.2 "stairs first": 3-6 candidate staircase positions, best-first.

    Deferred to %s. Placing a stair well needs the entry point, the circulation spine
    and the per-storey repeat check — all of which are stage-A concerns. Guessing here
    would fix the single most consequential decision in the plan before the solver has
    a say in it.
    """ % PHASE
    raise NotImplementedError(
        "enumerate_stair_anchors is implemented in %s. Envelope derivation (§5.1) and "
        "the coarse grid are already available to build on." % PHASE
    )


def stage_a_topology(
    grid: GridSpec,
    params: SolveParams,
    anchor: StairAnchor,
    *,
    time_budget_seconds: int,
    num_search_workers: int = 8,
) -> Candidate | None:
    """§5.2 stage A: CP-SAT room topology on the 300mm module.

    Returns ``None`` when the model is infeasible for this stair anchor — an expected
    outcome, not an error, since the pipeline tries several anchors.

    Deferred to %s (needs ``ortools``).
    """ % PHASE
    raise NotImplementedError(
        "stage_a_topology is implemented in %s. It builds a CpModel with interval vars "
        "per room, add_no_overlap_2d, and the §5.2 weighted objective." % PHASE
    )


def stage_b_refine(
    candidate: Candidate, params: SolveParams, envelope: BuildableEnvelope
) -> Mapping[str, Any] | None:
    """§5.3 stage B: snap to 115mm, build the wall network, insert openings.

    Returns the folded model document, or ``None`` when the candidate could not be
    repaired into a valid one (§5.3: "auto-repair trivial violations … else discard").

    Deferred to %s.
    """ % PHASE
    raise NotImplementedError(
        "stage_b_refine is implemented in %s. It snaps coordinates to the 115mm module, "
        "dedupes shared walls (115mm internal / 230mm external), and inserts doors and "
        "windows per §5.3." % PHASE
    )


def placements_to_ops(
    placements: Sequence[RoomPlacement], params: SolveParams
) -> tuple[Mapping[str, Any], ...]:
    """Express a refined layout as §4 ops — the only way the solver touches the model.

    Deferred to %s, alongside stage B, because the op list is derived from the wall
    network that stage B builds rather than from the coarse placements.
    """ % PHASE
    raise NotImplementedError(
        "placements_to_ops is implemented in %s, together with stage_b_refine." % PHASE
    )


__all__ = [
    "PHASE",
    "Candidate",
    "GridSpec",
    "enumerate_stair_anchors",
    "grid_envelope",
    "placements_to_ops",
    "stage_a_topology",
    "stage_b_refine",
]
