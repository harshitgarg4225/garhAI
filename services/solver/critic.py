"""§5.4 — the critic. Composite scoring is real; the sub-scores are Phase 3.

The split is deliberate. :func:`composite_score` is pure arithmetic over the eight
component scores, and it is the function everything downstream branches on — §5.5's
ranking and §5.6's gates both read ``composite``. Getting the weighting written down
and tested now means Phase 3 only has to produce honest components.

Each sub-score is a separately testable function with its own contract. The ones that
need geometry the solver has not built yet (furniture fit, plumbing stacks, privacy
sightlines) raise ``NotImplementedError``; the ones computable from a placement list
alone are implemented here.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from services.solver.geometry import ZONE_NAMES, Pt, bbox, zone_for_point
from services.solver.types import (
    BuildableEnvelope,
    RoomPlacement,
    RoomRequest,
    ScoreBreakdown,
    SolveParams,
)

PHASE = "Phase 3 (Layout solver)"

#: Rooms with plumbing. Stacking these across storeys is the buildability signal
#: §5.4 asks for — one shaft instead of a drop through a habitable ceiling.
WET_TYPES = frozenset(
    {"bath", "bath_wc", "wc", "kitchen", "kitchen_dining", "utility"}
)

#: Rooms you pass through rather than occupy. They do not screen a sightline —
#: a passage between the door and the master bedroom is exactly how you see in.
CIRCULATION_ROOM_TYPES = frozenset(
    {"passage", "corridor", "foyer", "lobby", "staircase", "porch"}
)

#: §5.4 weights. They sum to 100 so the composite is directly a 0-100 score, and
#: ``assert sum(COMPONENT_WEIGHTS.values()) == 100`` below keeps that true.
COMPONENT_WEIGHTS: dict[str, int] = {
    "target_area_fit": 20,
    "adjacency": 15,
    "circulation": 15,
    "daylight": 10,
    "vastu": 10,
    "furniture_fit": 15,
    "plumbing_stack": 5,
    "privacy": 5,
    "compactness": 5,
}

assert sum(COMPONENT_WEIGHTS.values()) == 100, "critic weights must sum to 100"


def composite_score(parts: Mapping[str, int]) -> int:
    """Weighted mean of the components, rounded to an integer 0-100. **Implemented.**

    Missing components count as 0 rather than being skipped: a plan whose furniture fit
    was never computed must not outrank one that was actually checked.
    """
    total = 0
    for name, weight in COMPONENT_WEIGHTS.items():
        value = int(parts.get(name, 0))
        total += max(0, min(100, value)) * weight
    return (total + 50) // 100


def score_target_area_fit(
    placements: Sequence[RoomPlacement], requests: Sequence[RoomRequest]
) -> int:
    """How close each room is to its target area. **Implemented.**

    100 when every room hits its target; falls off with the mean relative shortfall.
    Rooms *larger* than target are not penalised — extra space is not a defect, and
    penalising it would push the solver to waste area on circulation instead.
    """
    by_key = {request.key: request for request in requests}
    if not by_key:
        return 100
    penalties: list[int] = []
    for placement in placements:
        request = by_key.get(placement.room_key)
        if request is None or request.target_area_mm2 <= 0:
            continue
        shortfall = max(0, request.target_area_mm2 - placement.area_mm2)
        penalties.append(min(100, (shortfall * 100) // request.target_area_mm2))
    if not penalties:
        return 100
    return max(0, 100 - sum(penalties) // len(penalties))


def circulation_percent(
    placements: Sequence[RoomPlacement], footprint_mm2: int
) -> int:
    """Circulation as an integer percent of the footprint. **Implemented.**

    Circulation is the footprint the rooms do not occupy PLUS the rooms whose whole
    job is circulation (:data:`CIRCULATION_ROOM_TYPES`). The second term is not
    optional: stage A's tiling contract represents passages and stair wells AS
    placements, so a plan that reaches here fully tiled has zero unoccupied
    footprint — counting only the remainder would report 0% for every real
    candidate and silently disable the §5.6 cap, the exact failure mode of the
    ground-floor-denominator bug this repo already shipped once.
    """
    if footprint_mm2 <= 0:
        return 0
    occupied = sum(placement.area_mm2 for placement in placements)
    explicit = sum(
        placement.area_mm2
        for placement in placements
        if placement.room_type in CIRCULATION_ROOM_TYPES
    )
    circulation = max(0, footprint_mm2 - occupied) + explicit
    return min(100, (circulation * 100) // footprint_mm2)


def score_circulation(percent: int) -> int:
    """Turn a circulation percentage into a 0-100 score. **Implemented.**

    §5.2 targets <=12% and §5.6 hard-fails above 18%. Anything at or under the target
    scores full marks; between target and cap the score falls linearly to zero.
    """
    target, cap = 12, 18
    if percent <= target:
        return 100
    if percent >= cap:
        return 0
    return 100 - ((percent - target) * 100) // (cap - target)


def score_compactness(placements: Sequence[RoomPlacement], footprint_mm2: int) -> int:
    """Reward a compact footprint. **Implemented.**

    Ratio of occupied area to the bounding box of all rooms: a plan that sprawls into
    an L when a rectangle would do scores lower. Cheap, and it correlates well with the
    perimeter penalty §5.2 puts in the objective.
    """
    if not placements or footprint_mm2 <= 0:
        return 0
    min_x = min(placement.x_mm for placement in placements)
    min_y = min(placement.y_mm for placement in placements)
    max_x = max(placement.x_mm + placement.width_mm for placement in placements)
    max_y = max(placement.y_mm + placement.depth_mm for placement in placements)
    bounding = (max_x - min_x) * (max_y - min_y)
    if bounding <= 0:
        return 0
    occupied = sum(placement.area_mm2 for placement in placements)
    return max(0, min(100, (occupied * 100) // bounding))


def zone_of(placement: RoomPlacement, envelope: BuildableEnvelope, north_deg: int) -> str:
    """Which of the 9 plot zones a room's centroid falls in. **Implemented.**"""
    return zone_for_point(placement.centroid(), bbox(envelope.polygon), north_deg)


def score_daylight(
    placements: Sequence[RoomPlacement],
    envelope: BuildableEnvelope,
    north_deg: int,
    *,
    habitable_types: frozenset[str] = frozenset(
        {"living", "dining", "living_dining", "bedroom", "bedroom_master", "guest_bedroom",
         "study", "kitchen", "kitchen_dining"}
    ),
) -> int:
    """§5.4's daylight orientation bonus: habitable rooms facing E/N/NE. **Implemented.**"""
    relevant = [item for item in placements if item.room_type in habitable_types]
    if not relevant:
        return 100
    favoured = {"N", "NE", "E"}
    neutral = {"NW", "SE", "C"}
    total = 0
    for placement in relevant:
        zone = zone_of(placement, envelope, north_deg)
        total += 100 if zone in favoured else 60 if zone in neutral else 25
    return total // len(relevant)


def _rects_overlap(a: RoomPlacement, b: RoomPlacement) -> bool:
    """Do two room rectangles overlap in plan (touching edges do not count)?"""
    return (
        a.x_mm < b.x_mm + b.width_mm
        and b.x_mm < a.x_mm + a.width_mm
        and a.y_mm < b.y_mm + b.depth_mm
        and b.y_mm < a.y_mm + a.depth_mm
    )


def _entry_point(
    params: SolveParams, placements: Sequence[RoomPlacement]
) -> Pt | None:
    """Where the front door is, approximately: the midpoint of the front edge.

    Falls back to the first road-facing edge, then to ``None`` — an unknown entry
    means privacy is unscoreable, and :func:`score_privacy` returns 100 rather than
    inventing a penalty from a guess.
    """
    edges = params.edges
    if not edges:
        return None

    front = next((edge for edge in edges if edge.role == "front"), None)
    if front is None:
        front = next((edge for edge in edges if edge.road_width_mm > 0), None)
    if front is None:
        return None

    polygon = params.plot_polygon
    if not polygon:
        return None
    start = polygon[front.index % len(polygon)]
    end = polygon[(front.index + 1) % len(polygon)]
    return ((start[0] + end[0]) // 2, (start[1] + end[1]) // 2)


def _orient(ax: int, ay: int, bx: int, by: int, cx: int, cy: int) -> int:
    """Sign of the cross product (b-a) x (c-a): +1 left, -1 right, 0 collinear."""
    value = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    return (value > 0) - (value < 0)


def _on_span(ax: int, ay: int, bx: int, by: int, cx: int, cy: int) -> bool:
    """Is collinear point c within the bounding box of segment ab?"""
    return min(ax, bx) <= cx <= max(ax, bx) and min(ay, by) <= cy <= max(ay, by)


def _segments_cross(p1: Pt, p2: Pt, p3: Pt, p4: Pt) -> bool:
    """Do segments p1p2 and p3p4 intersect, touching included? Integer-exact."""
    (x1, y1), (x2, y2), (x3, y3), (x4, y4) = p1, p2, p3, p4
    d1 = _orient(x3, y3, x4, y4, x1, y1)
    d2 = _orient(x3, y3, x4, y4, x2, y2)
    d3 = _orient(x1, y1, x2, y2, x3, y3)
    d4 = _orient(x1, y1, x2, y2, x4, y4)

    if d1 * d2 < 0 and d3 * d4 < 0:
        return True
    if d1 == 0 and _on_span(x3, y3, x4, y4, x1, y1):
        return True
    if d2 == 0 and _on_span(x3, y3, x4, y4, x2, y2):
        return True
    if d3 == 0 and _on_span(x1, y1, x2, y2, x3, y3):
        return True
    if d4 == 0 and _on_span(x1, y1, x2, y2, x4, y4):
        return True
    return False


def _segment_crosses_rect(origin: Pt, target: Pt, rect: RoomPlacement) -> bool:
    """Does the segment origin->target pass through a room rectangle?

    Either endpoint inside the rectangle counts, as does crossing any of its four
    sides. All integer arithmetic — no division, no float, so the answer is exact
    and identical on every machine.
    """
    left, bottom = rect.x_mm, rect.y_mm
    right, top = rect.x_mm + rect.width_mm, rect.y_mm + rect.depth_mm

    for point in (origin, target):
        if left <= point[0] <= right and bottom <= point[1] <= top:
            return True

    corners = (
        (left, bottom),
        (right, bottom),
        (right, top),
        (left, top),
    )
    for index in range(4):
        if _segments_cross(origin, target, corners[index], corners[(index + 1) % 4]):
            return True
    return False


def shared_edge_mm(a: RoomPlacement, b: RoomPlacement) -> int:
    """Length of the wall two rooms share, in mm. **Implemented.**

    Rectangles share an edge when they abut on one axis and overlap on the other.
    Corner contact yields 0, which is the point: §5.2 requires kitchen and dining to
    share at least 900mm so a door can exist between them, and two rooms meeting at a
    corner cannot host a door.

    Rooms on different storeys never share an edge.
    """
    if a.storey_index != b.storey_index:
        return 0

    a_x2, a_y2 = a.x_mm + a.width_mm, a.y_mm + a.depth_mm
    b_x2, b_y2 = b.x_mm + b.width_mm, b.y_mm + b.depth_mm

    # Vertical wall: one room's right face is the other's left face.
    if a_x2 == b.x_mm or b_x2 == a.x_mm:
        return max(0, min(a_y2, b_y2) - max(a.y_mm, b.y_mm))
    # Horizontal wall.
    if a_y2 == b.y_mm or b_y2 == a.y_mm:
        return max(0, min(a_x2, b_x2) - max(a.x_mm, b.x_mm))
    return 0


def score_adjacency(
    placements: Sequence[RoomPlacement],
    specs: Sequence[Any] = (),
) -> int:
    """Satisfaction of the program's required and preferred adjacencies. **Implemented.**

    ``specs`` are :class:`services.solver.program.AdjacencySpec` records — ``a_key``,
    ``b_key``, ``kind`` in ``required``/``adjacent``/``apart``, ``min_shared_edge_mm``
    and ``weight``. They are taken as a parameter rather than re-derived so the critic
    scores exactly what stage A was asked to honour.

    Measured on stage A's rectangles, before stage B dedupes shared walls. That is the
    right stage: the rectangle edge two rooms share *is* the wall stage B will build
    between them, so the measurement does not change, and scoring here lets a candidate
    be rejected before the expensive refinement runs.

    A missed ``required`` adjacency scores 0 for that pair — it is a brief violation,
    not a preference. Wishes contribute their weight proportionally. With no specs at
    all the score is 100: nothing was asked for, so nothing was missed.
    """
    if not specs:
        return 100

    by_key = {item.room_key: item for item in placements}
    earned = 0
    available = 0

    for spec in specs:
        weight = max(1, int(getattr(spec, "weight", 0) or 1))
        kind = str(getattr(spec, "kind", "adjacent"))
        required_mm = int(getattr(spec, "min_shared_edge_mm", 0) or 0)

        a = by_key.get(getattr(spec, "a_key", ""))
        b = by_key.get(getattr(spec, "b_key", ""))
        if a is None or b is None:
            # A room the brief mentioned is not on this plan (e.g. it lives on
            # another storey). Not scoreable, so it neither helps nor hurts.
            continue

        shared = shared_edge_mm(a, b)
        available += weight

        if kind == "apart":
            earned += weight if shared == 0 else 0
        elif kind == "required":
            earned += weight if shared >= max(required_mm, 1) else 0
        else:  # 'adjacent' — a wish
            if required_mm > 0:
                earned += weight * min(shared, required_mm) // required_mm
            else:
                earned += weight if shared > 0 else 0

    if available <= 0:
        return 100
    return max(0, min(100, (earned * 100) // available))


def score_vastu(
    placements: Sequence[RoomPlacement],
    envelope: BuildableEnvelope,
    params: SolveParams,
    *,
    rulepack_dir: str | None = None,
) -> int:
    """§6's Vastu score, 0-100, weighted by the pack's own rule weights. **Implemented.**

    The zone rules are *read from* ``rulepacks/vastu.json`` via
    :func:`services.solver.program.load_vastu_zone_rules` — never re-derived here. That
    matters: the compass wheel in the UI renders the same pack, and a solver that scored
    Vastu by its own private table would disagree with the wheel about the same plan.

    In ``strict`` mode stage A has already constrained placements to allowed zones, so
    this should come back at or near 100; it is still computed rather than assumed,
    because a constraint that silently failed to bind would otherwise go unnoticed.
    Returns 100 when the mode is ``off`` — an un-asked-for score must not drag the
    composite down.
    """
    if params.vastu_mode == "off":
        return 100

    from services.solver.program import load_vastu_zone_rules, zone_allowance_for

    rules = load_vastu_zone_rules(rulepack_dir) if rulepack_dir else load_vastu_zone_rules()
    plot_bbox = bbox(envelope.polygon)

    earned = 0
    available = 0
    for placement in placements:
        allowance = zone_allowance_for(placement.room_type, params.vastu_mode, rules)
        if allowance is None:
            continue
        weight = max(1, int(allowance.weight or 1))
        available += weight
        zone = zone_for_point(placement.centroid(), plot_bbox, params.north_deg)

        if zone in tuple(allowance.deny or ()):
            continue  # zero for this rule — e.g. a toilet in the NE, hard-never
        if zone in tuple(allowance.preferred or ()):
            earned += weight
        elif zone in tuple(allowance.allow or ()):
            # The pack's documented fallback zones (kitchen NW behind SE) score half.
            earned += weight // 2

    if available <= 0:
        return 100
    return max(0, min(100, (earned * 100) // available))


def score_furniture_fit(
    placements: Sequence[RoomPlacement],
    params: SolveParams,
    *,
    catalog: Mapping[str, Any] | None = None,
) -> int:
    """Whether a standard furniture set fits each room (§5.4; §5.6 gates on it).

    **Implemented** — delegates to :mod:`services.solver.furniture_fit`, which owns the
    catalogue, the required sets and the (deliberately conservative) packer.
    """
    from services.solver import furniture_fit

    resolved = catalog if catalog is not None else furniture_fit.load_catalog()
    return furniture_fit.score(furniture_fit.fit_all(placements, resolved))


def score_plumbing_stack(placements: Sequence[RoomPlacement]) -> int:
    """How well wet areas stack across storeys (§5.4 buildability signal). **Implemented.**

    A bath directly above a bath shares one plumbing shaft; a bath above a bedroom needs
    its own drop through a habitable ceiling, which Indian builders avoid and clients
    notice. Score is the share of upper-storey wet rooms whose footprint overlaps a wet
    room on the storey below.

    A single-storey plan scores 100: there is nothing to stack, and penalising that would
    make every bungalow look worse than every duplex.
    """
    wet_by_storey: dict[int, list[RoomPlacement]] = {}
    for placement in placements:
        if placement.room_type in WET_TYPES:
            wet_by_storey.setdefault(placement.storey_index, []).append(placement)

    upper = [index for index in sorted(wet_by_storey) if index > 0]
    if not upper:
        return 100

    stacked = 0
    total = 0
    for index in upper:
        below = wet_by_storey.get(index - 1, [])
        for placement in wet_by_storey[index]:
            total += 1
            if any(_rects_overlap(placement, other) for other in below):
                stacked += 1

    if total <= 0:
        return 100
    return (stacked * 100) // total


def score_privacy(
    placements: Sequence[RoomPlacement],
    params: SolveParams,
    *,
    entry_point: Any = None,
) -> int:
    """Master bedroom not on a straight sightline from the entrance (§5.4). **Implemented.**

    Approximated at stage A resolution: a sightline is the straight segment from the
    entry point to the master bedroom's centroid, and the master is considered screened
    when that segment passes through any other room's rectangle. Stage B's door
    positions would sharpen this, but the coarse test already separates the plan where
    you open the front door onto the master from the plan where you do not — which is
    the distinction the score exists to make.

    Documented approximation, deliberately: refining it once doors exist changes the
    number, and :mod:`services.solver.pipeline` records the score with the stage that
    produced it.
    """
    masters = [item for item in placements if item.room_type == "bedroom_master"]
    if not masters:
        return 100

    origin = entry_point if entry_point is not None else _entry_point(params, placements)
    if origin is None:
        return 100

    screened = 0
    for master in masters:
        target = master.centroid()
        blockers = [
            item
            for item in placements
            if item is not master
            and item.storey_index == master.storey_index
            and item.room_type not in CIRCULATION_ROOM_TYPES
        ]
        if any(_segment_crosses_rect(origin, target, item) for item in blockers):
            screened += 1
        elif master.storey_index > 0:
            # Upstairs is screened by the staircase itself.
            screened += 1

    return (screened * 100) // len(masters)


def critique(
    placements: Sequence[RoomPlacement],
    params: SolveParams,
    envelope: BuildableEnvelope,
    footprint_mm2: int,
    *,
    adjacency: Sequence[Any] = (),
    catalog: Mapping[str, Any] | None = None,
    rulepack_dir: str | None = None,
) -> ScoreBreakdown:
    """Run every sub-score and assemble the breakdown. **Implemented.**

    The composite is computed by :func:`composite_score` from the parts, never assigned
    independently, so the breakdown the UI explains and the number the gates read cannot
    disagree.
    """
    percent = circulation_percent(placements, footprint_mm2)

    parts = {
        "target_area_fit": score_target_area_fit(placements, params.rooms),
        "adjacency": score_adjacency(placements, adjacency),
        "circulation": score_circulation(percent),
        "daylight": score_daylight(placements, envelope, params.north_deg),
        "vastu": score_vastu(placements, envelope, params, rulepack_dir=rulepack_dir),
        "furniture_fit": score_furniture_fit(placements, params, catalog=catalog),
        "plumbing_stack": score_plumbing_stack(placements),
        "privacy": score_privacy(placements, params),
        "compactness": score_compactness(placements, footprint_mm2),
    }

    return ScoreBreakdown(
        target_area_fit=parts["target_area_fit"],
        adjacency=parts["adjacency"],
        circulation=parts["circulation"],
        daylight=parts["daylight"],
        vastu=parts["vastu"],
        furniture_fit=parts["furniture_fit"],
        plumbing_stack=parts["plumbing_stack"],
        privacy=parts["privacy"],
        compactness=parts["compactness"],
        composite=composite_score(parts),
        circulation_percent=percent,
    )


def evaluate_compliance(
    model: Mapping[str, Any], params: SolveParams
) -> tuple[Mapping[str, Any], ...]:
    """§5.4 hard-rule pass: run the garh_rules engine over a stage-B model. Rows out.

    Delegates to ``garh_api.compliance.evaluate_document`` — the SAME projection and
    engine call the compliance panel and the area statement use, because two sources
    of truth for FAR is a liability bug in a product selling citable compliance.
    The document wrapper is :func:`services.solver.repair.wrap_project_doc`, so the
    plot, north, roads and Vastu mode all come from the solve params rather than
    from defaults that would quietly turn rules ``not_applicable``.

    Raises rather than returning an empty row set when the engine or the packs are
    unavailable: a compliance pass that silently checks nothing would wave every
    candidate through the §5.6 hard-rule gate.
    """
    from services.solver.repair import ensure_model_importable, wrap_project_doc

    ensure_model_importable()
    from garh_api.compliance import evaluate_document

    overrides = dict(params.profile.overrides) if params.profile.overrides else None
    payload, _pack_versions = evaluate_document(
        wrap_project_doc(model, params), overrides=overrides
    )
    return tuple(payload.get("results") or ())


__all__ = [
    "CIRCULATION_ROOM_TYPES",
    "COMPONENT_WEIGHTS",
    "PHASE",
    "WET_TYPES",
    "circulation_percent",
    "evaluate_compliance",
    "shared_edge_mm",
    "composite_score",
    "critique",
    "score_adjacency",
    "score_circulation",
    "score_compactness",
    "score_daylight",
    "score_furniture_fit",
    "score_plumbing_stack",
    "score_privacy",
    "score_target_area_fit",
    "score_vastu",
    "zone_of",
]
