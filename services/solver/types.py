"""Solver job inputs and outputs (playbook §5).

Every length is an ``int`` count of millimetres and every area an ``int`` count of
square millimetres — including inside score breakdowns, where a float would be
harmless but would also be the first crack in a rule that has to hold everywhere.

Scores are the one deliberate exception in spirit: they are 0-100 **integers**, not
floats. A composite of ``73`` is as meaningful as ``73.418`` and compares exactly,
which matters because §5.5's diversity filter and §5.6's gates both branch on them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from services.common.assumptions import Assumption
from services.solver.geometry import Polygon, Pt

#: Which edge of the plot an edge is. Drives setbacks and entrance rules.
EdgeRole = Literal["front", "rear", "side", "side-left", "side-right"]

#: §5.2 solves on a 300mm module; §5.3 snaps the result to the 115mm brick module.
COARSE_MODULE_MM = 300
FINE_MODULE_MM = 115

#: §5.3 wall thicknesses.
EXTERNAL_WALL_MM = 230
INTERNAL_WALL_MM = 115


@dataclass(frozen=True)
class PlotEdge:
    """One boundary edge, with its role and the road it faces (if any)."""

    index: int
    role: EdgeRole
    #: Required inward setback for this edge, from the regulatory profile.
    setback_mm: int
    #: Width of the abutting road, or 0 when the edge does not face one.
    road_width_mm: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "role": self.role,
            "setbackMm": self.setback_mm,
            "roadWidthMm": self.road_width_mm,
        }


@dataclass(frozen=True)
class RegProfile:
    """The regulatory envelope numbers the solver needs (§6 supplies them)."""

    city_pack: str
    #: Allowed ground coverage as an integer percent of plot area.
    coverage_percent: int
    #: Allowed FAR as a ratio ×100 (175 == 1.75). Integers all the way down.
    far_x100: int
    max_height_mm: int
    max_floors: int
    #: Extra profile values, passed through untouched for the rules engine.
    overrides: Mapping[str, Any] = field(default_factory=dict)

    def allowed_footprint_mm2(self, plot_area_mm2: int) -> int:
        """Ground-coverage cap in mm². Integer arithmetic, floored."""
        return (plot_area_mm2 * self.coverage_percent) // 100

    def allowed_built_up_mm2(self, plot_area_mm2: int) -> int:
        """FAR cap in mm²."""
        return (plot_area_mm2 * self.far_x100) // 100


@dataclass(frozen=True)
class RoomRequest:
    """One room the brief asks for, with the bounds the solver must respect."""

    key: str
    room_type: str
    min_area_mm2: int
    target_area_mm2: int
    min_width_mm: int
    #: Aspect ratio bound ×100 (220 == 1:2.2 for habitable rooms, §5.2).
    max_aspect_x100: int = 220
    storey_index: int | None = None
    needs_external_wall: bool = True
    is_wet: bool = False
    locked: bool = False


@dataclass(frozen=True)
class SolveParams:
    """Everything one solver job needs. Self-contained — workers hold no DB."""

    plot_polygon: Polygon
    edges: tuple[PlotEdge, ...]
    profile: RegProfile
    rooms: tuple[RoomRequest, ...]
    storeys: int
    north_deg: int = 0
    vastu_mode: Literal["off", "advisory", "strict"] = "advisory"
    #: §5.7 partial re-solve: these room ids keep their exact geometry.
    locked_room_ids: tuple[str, ...] = ()
    #: Deterministic seed. The same params + seed must always give the same options.
    seed: int = 0
    target_option_count: int = 3
    time_budget_seconds: int = 15
    #: Brief declarations the §5.4 rules pass reads but no geometry uses —
    #: ``carParking``, ``rainwaterHarvesting``, ``dwellingUnits``. An allowlist,
    #: never the whole brief: free brief text must not ride worker payloads (§13).
    brief_data: Mapping[str, Any] = field(default_factory=dict)

    def plot_area_mm2(self) -> int:
        from services.solver.geometry import area_mm2

        return area_mm2(self.plot_polygon)


@dataclass(frozen=True)
class BuildableEnvelope:
    """§5.1 output: where the building may stand, and what that permits."""

    polygon: Polygon
    area_mm2: int
    #: Ground-coverage cap from the profile.
    allowed_footprint_mm2: int
    #: min(envelope area, coverage cap) — the real ceiling for a footprint.
    effective_footprint_mm2: int
    allowed_built_up_mm2: int
    #: What the brief asked for, after any shrink.
    target_footprint_mm2: int
    #: Chips for every default or reduction applied (golden rule 4).
    assumptions: tuple[Assumption, ...] = ()
    #: Non-fatal notes for the UI (e.g. "setbacks bind harder than coverage here").
    notes: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "polygon": [{"x": x, "y": y} for x, y in self.polygon],
            "areaMm2": self.area_mm2,
            "allowedFootprintMm2": self.allowed_footprint_mm2,
            "effectiveFootprintMm2": self.effective_footprint_mm2,
            "allowedBuiltUpMm2": self.allowed_built_up_mm2,
            "targetFootprintMm2": self.target_footprint_mm2,
            "assumptions": [item.to_json() for item in self.assumptions],
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class StairAnchor:
    """One candidate staircase position (§5.2 "stairs first")."""

    id: str
    origin: Pt
    width_mm: int
    #: Which envelope edge the flight runs along.
    edge_index: int
    #: Cheap pre-score used to order candidates before the expensive solve.
    prior: int = 0


@dataclass(frozen=True)
class RoomPlacement:
    """A placed room. Integer mm rectangle in plot-local coordinates."""

    room_key: str
    room_type: str
    storey_index: int
    x_mm: int
    y_mm: int
    width_mm: int
    depth_mm: int
    #: Stable id, preserved across re-solves for locked rooms (§5.7).
    room_id: str | None = None

    @property
    def area_mm2(self) -> int:
        return self.width_mm * self.depth_mm

    def centroid(self) -> Pt:
        return (self.x_mm + self.width_mm // 2, self.y_mm + self.depth_mm // 2)


@dataclass(frozen=True)
class ScoreBreakdown:
    """§5.4's composite, kept as its parts so the UI can explain the number.

    Every component is 0-100. ``composite`` is their weighted mean, computed by
    :func:`services.solver.critic.composite_score` — never assigned independently, so
    the parts and the total cannot disagree.
    """

    target_area_fit: int = 0
    adjacency: int = 0
    circulation: int = 0
    daylight: int = 0
    vastu: int = 0
    furniture_fit: int = 0
    plumbing_stack: int = 0
    privacy: int = 0
    compactness: int = 0
    composite: int = 0
    #: Circulation as an integer percent of built-up area — §5.6 gates on it.
    circulation_percent: int = 0

    def to_json(self) -> dict[str, int]:
        return {
            "targetAreaFit": self.target_area_fit,
            "adjacency": self.adjacency,
            "circulation": self.circulation,
            "daylight": self.daylight,
            "vastu": self.vastu,
            "furnitureFit": self.furniture_fit,
            "plumbingStack": self.plumbing_stack,
            "privacy": self.privacy,
            "compactness": self.compactness,
            "composite": self.composite,
            "circulationPercent": self.circulation_percent,
        }


@dataclass(frozen=True)
class PlanOption:
    """One presentable plan (§5.5). This is what the options screen renders.

    ``ops`` is the option expressed as the op log that produces it — the solver, like
    everything else, mutates the model only through §4 ops. ``solver.apply_option``
    carries them verbatim so replay never has to re-run CP-SAT.
    """

    id: str
    #: Rank among returned options, 0 = best.
    rank: int
    scores: ScoreBreakdown
    placements: tuple[RoomPlacement, ...]
    ops: tuple[Mapping[str, Any], ...]
    #: §5.5 diversity signature: (roomType → zone) multiset + stair anchor.
    signature: tuple[str, ...]
    stair_anchor_id: str
    built_up_mm2: int
    footprint_mm2: int
    #: Structured facts for the rationale writer. The LLM only verbalises these.
    rationale_facts: tuple[str, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    #: Rules-engine results for this option, already computed by the critic.
    compliance: tuple[Mapping[str, Any], ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rank": self.rank,
            "scores": self.scores.to_json(),
            "ops": [dict(op) for op in self.ops],
            "signature": list(self.signature),
            "stairAnchorId": self.stair_anchor_id,
            "builtUpMm2": self.built_up_mm2,
            "footprintMm2": self.footprint_mm2,
            "rationaleFacts": list(self.rationale_facts),
            "assumptions": [item.to_json() for item in self.assumptions],
            "compliance": [dict(item) for item in self.compliance],
        }


@dataclass(frozen=True)
class SolveResult:
    """What the solver job returns.

    ``banner`` carries §5.6's honest message when fewer than three options cleared the
    gates — "2 strong options found for this plot" rather than padding the list.
    """

    options: tuple[PlanOption, ...]
    envelope: BuildableEnvelope
    banner: str | None = None
    #: Candidates generated vs kept, for the job card and for tuning.
    considered: int = 0
    rejected_by_gates: int = 0

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "options": [option.to_json() for option in self.options],
            "envelope": self.envelope.to_json(),
            "considered": self.considered,
            "rejectedByGates": self.rejected_by_gates,
        }
        if self.banner:
            out["banner"] = self.banner
        return out


__all__ = [
    "COARSE_MODULE_MM",
    "EXTERNAL_WALL_MM",
    "FINE_MODULE_MM",
    "INTERNAL_WALL_MM",
    "BuildableEnvelope",
    "EdgeRole",
    "PlanOption",
    "PlotEdge",
    "RegProfile",
    "RoomPlacement",
    "RoomRequest",
    "ScoreBreakdown",
    "SolveParams",
    "SolveResult",
    "StairAnchor",
]
