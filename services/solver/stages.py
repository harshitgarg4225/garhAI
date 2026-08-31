"""§5.2 / §5.3 — CP-SAT topology and refinement. **Phase 3: the bodies are live.**

Every stage is a named, typed, individually testable function with its full signature
and contract written down. The bodies here are THIN ADAPTERS onto the real
implementations — :mod:`services.solver.stairs`, :mod:`services.solver.stage_a`,
:mod:`services.solver.stage_b` — which own the geometry, the CP-SAT model and their
own tests. Nothing is duplicated here: forking even a slice of stage logic into this
module would give the pipeline a second source of truth for the same plan.

The real modules are imported lazily inside each body, for two reasons that both
bite if ignored:

* ``stage_b`` imports :class:`Candidate` from this module at import time, so a
  module-top import the other way is a cycle;
* ``stage_a`` needs ``ortools`` only when it actually solves, so this module still
  imports cleanly (and :func:`grid_envelope` still runs) on a bare interpreter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from services.solver.geometry import Pt, bbox, point_in_polygon
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


def grid_envelope(envelope: BuildableEnvelope, *, module_mm: int = COARSE_MODULE_MM) -> GridSpec:
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
    return GridSpec(origin=(min_x, min_y), module_mm=module_mm, cols=cols, rows=rows, mask=mask)


def enumerate_stair_anchors(
    envelope: BuildableEnvelope, params: SolveParams, *, limit: int = 6
) -> tuple[StairAnchor, ...]:
    """§5.2 "stairs first": 3-6 candidate staircase positions, best-first.

    Delegates to :func:`services.solver.stairs.enumerate_stair_candidates` — pure
    integer geometry with NBC-sized dogleg wells read from the pack; the priors
    (entry side first, corners over midpoints, Vastu bonus) live there.
    """
    from services.solver import stairs

    return stairs.enumerate_stair_candidates(envelope, params, limit=limit)


def stage_a_topology(
    grid: GridSpec,
    params: SolveParams,
    anchor: StairAnchor,
    *,
    profile: Any = None,
    relaxed: bool = False,
    time_budget_seconds: int | None = None,
    num_search_workers: int | None = None,
    shortfalls: list[Any] | None = None,
    program: Any = None,
) -> Candidate | None:
    """§5.2 stage A: CP-SAT room topology on the 300mm module.

    Returns ``None`` when the model is infeasible for this stair anchor — an expected
    outcome, not an error, since the pipeline tries several anchors.

    ``shortfalls``, when given, collects a :class:`~services.solver.diagnose.
    StoreyShortfall` per storey that failed, so the pipeline can tell the architect
    WHY rather than only that. Opt-in, so every existing caller is unaffected.

    Delegates to :func:`services.solver.stage_a.stage_a_topology` (needs ``ortools``),
    which accepts both calling generations: the pipeline's ``profile``/``relaxed``
    keywords and the legacy ``time_budget_seconds``/``num_search_workers`` pair —
    the latter only applies when no ``profile`` is given.
    """
    from services.solver import stage_a

    return stage_a.stage_a_topology(
        grid,
        params,
        anchor,
        profile=profile,
        relaxed=relaxed,
        time_budget_seconds=time_budget_seconds,
        num_search_workers=num_search_workers,
        shortfalls=shortfalls,
        program=program,
    )


def stage_b_refine(
    candidate: Candidate, params: SolveParams, envelope: BuildableEnvelope
) -> Mapping[str, Any] | None:
    """§5.3 stage B: snap to 115mm, build the wall network, insert openings.

    Returns the folded model document, or ``None`` when the candidate could not be
    repaired into a valid one (§5.3: "auto-repair trivial violations … else discard").

    Delegates to :func:`services.solver.stage_b.stage_b_refine` (ortools-free), which
    logs every discard with its typed code before returning ``None``.
    """
    from services.solver import stage_b

    return stage_b.stage_b_refine(candidate, params, envelope)


def placements_to_ops(
    placements: Sequence[RoomPlacement],
    params: SolveParams,
    *,
    model: Mapping[str, Any] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Express a refined layout as §4 ops — the only way the solver touches the model.

    Delegates to :func:`services.solver.stage_b.house_to_ops`. The ops are derived
    from the wall network stage B built (``model``), NOT from the coarse
    ``placements`` — the parameter stays for the ``BuildOpsFn`` contract, but a call
    without the stage-B document is a caller bug, and inventing walls from coarse
    rectangles here would fork stage B's geometry.
    """
    if model is None:
        raise ValueError(
            "placements_to_ops needs the stage-B model document (model=...): the op "
            "list is derived from the refined wall network, not the coarse placements."
        )
    from services.solver import stage_b

    return tuple(stage_b.house_to_ops(model, params))


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
