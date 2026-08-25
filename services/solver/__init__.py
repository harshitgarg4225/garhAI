"""The layout solver (playbook §5) — plot + brief → 3-5 compliant plan options.

Pipeline, in order, one module each:

===========================  ==================================================
``envelope``   §5.1          inward per-edge setback offset + coverage — **real**
``stages``     §5.2 / §5.3   CP-SAT topology, then 115mm refinement — Phase 3
``critic``     §5.4          composite scoring — arithmetic real, sub-scores Phase 3
``diversity``  §5.5          signatures, Hamming filter, ranking — **real**
``gates``      §5.6          presentability gates + honest banner — **real**
``pipeline``   §5            orchestration + §15 progress events — **real**
===========================  ==================================================

What is deliberately NOT here: any path that lets a plan reach an architect without
passing §5.6. Golden rule 2 — "feasible is not plausible; never show a hard-fail plan"
— is enforced in :mod:`services.solver.gates`, which runs before ranking, and nothing
downstream may re-admit a rejected option.

Determinism is a feature, not a coincidence: the same ``SolveParams`` and ``seed`` must
produce byte-identical plan JSON, because §16 golden files compare it with tolerance 0.
"""

from __future__ import annotations

from services.solver.envelope import EnvelopeError, derive_envelope, offset_polygon_inward
from services.solver.gates import GateResult, banner_for, check_option, filter_presentable
from services.solver.types import (
    BuildableEnvelope,
    PlanOption,
    PlotEdge,
    RegProfile,
    RoomPlacement,
    RoomRequest,
    ScoreBreakdown,
    SolveParams,
    SolveResult,
    StairAnchor,
)

__all__ = [
    "BuildableEnvelope",
    "EnvelopeError",
    "GateResult",
    "PlanOption",
    "PlotEdge",
    "RegProfile",
    "RoomPlacement",
    "RoomRequest",
    "ScoreBreakdown",
    "SolveParams",
    "SolveResult",
    "StairAnchor",
    "banner_for",
    "check_option",
    "derive_envelope",
    "filter_presentable",
    "offset_polygon_inward",
]
