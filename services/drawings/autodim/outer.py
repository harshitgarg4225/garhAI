"""§7 step 2 — the outer chains: three levels on each of the four sides.

    2. **Outer chains** per side of building (4 sides): level 1 = overall extent;
       level 2 = external wall segment breakpoints; level 3 = opening centrelines on
       that facade. Offsets: L1 at 2400mm from building line (paper-scaled), L2 1800,
       L3 1200.

Read the offsets carefully: **L3 is the innermost chain**. Nearest the building are the
openings, then the wall grid, and the overall extent sits furthest out — the way a
draughtsman builds it up, because the reviewer's eye reads outward from detail to total.

What "external wall segment breakpoints" means here, concretely, since the phrase admits
two readings:

* On a rectangle, the external run has no breaks of its own, so a literal reading gives
  a level 2 chain identical to level 1 — a duplicate, and duplicates are exactly what
  step 3 tells us to suppress. The useful reading, and the one every municipal drawing
  uses, is *the positions along this facade where the plan is divided*: the centrelines
  of the walls that meet this facade (external cross-walls and internal partitions
  alike), plus any jog in the facade itself.
* Centrelines, not faces, for those cross walls. Both are defensible drafting; the
  centreline is the one that is an exact model integer, so a chain built from them
  carries no rounding at all. Wall *thicknesses* are called out separately (F7-A lists
  them as their own annotation), which is where a face-to-face number belongs.

The two ends of every chain are the building's outer faces — F7-A's "dims to unfinished
faces". A cross wall sitting at the corner is dropped rather than producing a 115mm
sliver segment next to the corner tick.
"""

from __future__ import annotations

from services.drawings.autodim.chains import (
    KIND_OUTER,
    DimChainInfo,
    chain_from_breakpoints,
    merge_breakpoints,
)
from services.drawings.autodim.config import DEFAULT_CONFIG, AutoDimConfig
from services.drawings.autodim.extract import (
    HORIZONTAL,
    SIDES,
    VERTICAL,
    Extents,
    StoreyPlan,
    WallAxis,
)

#: Perpendicular orientation lookup: the walls that can *cross* a facade.
_CROSS_ORIENTATION = {HORIZONTAL: VERTICAL, VERTICAL: HORIZONTAL}

#: Which way is "out" from the building on each side, on the perpendicular axis.
_OUTWARD = {"S": -1, "N": +1, "W": -1, "E": +1}

#: The facade orientation of each side: a south facade is a horizontal run of wall, and
#: its chains measure along x.
_SIDE_ORIENTATION = {"S": HORIZONTAL, "N": HORIZONTAL, "W": VERTICAL, "E": VERTICAL}


def _chain_id(storey_id: str, side: str, level: int) -> str:
    return "dim.%s.%s.L%d" % (storey_id, side, level)


def _corner_slack_mm(plan: StoreyPlan) -> int:
    """How near an extent end a breakpoint has to be to count as "the corner".

    Half the thickest envelope wall, plus one: the corner wall's own centreline sits
    exactly its half-thickness inside the outer face, and floating it by 1mm absorbs the
    odd-thickness floor division.
    """
    halves = [w.half_mm for w in plan.walls if w.is_envelope] or [w.half_mm for w in plan.walls]
    return (max(halves) if halves else 0) + 1


def _cross_walls_on_side(plan: StoreyPlan, side: str, config: AutoDimConfig) -> list[WallAxis]:
    """Walls that meet this side's visible facade runs, id-ordered.

    A wall qualifies when it is perpendicular to the facade, its span reaches the run's
    axis, and its own axis falls inside the run. The reach test carries slack of one
    envelope half-thickness plus its own: partitions are modelled either centreline-to-
    centreline (touching the facade axis exactly) or face-to-face (stopping short by the
    external wall's half-thickness), and both are common in real op logs.
    """
    facade_orientation = _SIDE_ORIENTATION[side]
    cross_orientation = _CROSS_ORIENTATION[facade_orientation]
    envelope_half = _corner_slack_mm(plan)
    found: dict[str, WallAxis] = {}

    for run in plan.runs.get(side, ()):
        for wall in plan.walls_of(cross_orientation):
            if wall.thickness_mm < config.min_wall_thickness_mm:
                continue
            slack = envelope_half + wall.half_mm
            if not wall.contains_along(run.axis_mm, slack_mm=slack):
                continue
            if not (run.lo_mm - slack <= wall.axis_mm <= run.hi_mm + slack):
                continue
            found[wall.id] = wall

    return [found[key] for key in sorted(found)]


def build_level_1(
    plan: StoreyPlan, side: str, extents: Extents, config: AutoDimConfig
) -> DimChainInfo | None:
    """Overall extent: one segment, face to face."""
    span_lo, span_hi = extents.span_for(side)
    building_line = extents.building_line_for(side)
    outward = _OUTWARD[side]
    offset = config.offset_for_level(1)
    return chain_from_breakpoints(
        chain_id=_chain_id(plan.storey_id, side, 1),
        orientation=_SIDE_ORIENTATION[side],
        level=1,
        offset_mm=offset,
        breakpoints=(span_lo, span_hi),
        line_mm=building_line + outward * offset,
        reference_mm=building_line,
        outward=outward,
        kind=KIND_OUTER,
        storey_id=plan.storey_id,
        side=side,
    )


def build_level_2(
    plan: StoreyPlan, side: str, extents: Extents, config: AutoDimConfig
) -> DimChainInfo | None:
    """Wall grid: the extent, broken at every wall that meets this facade."""
    span_lo, span_hi = extents.span_for(side)
    building_line = extents.building_line_for(side)
    outward = _OUTWARD[side]
    offset = config.offset_for_level(2)
    slack = _corner_slack_mm(plan)

    raw: list[int] = [span_lo, span_hi]
    anchors: dict[int, str] = {}

    for wall in _cross_walls_on_side(plan, side, config):
        position = wall.axis_mm
        if position - span_lo <= slack or span_hi - position <= slack:
            continue  # the corner itself, not a division of the facade
        raw.append(position)
        anchors[position] = wall.id

    # Jogs in the facade: a recessed leg's return shows up as a run endpoint. Usually
    # the returning wall above already supplied it; a storey missing that wall (a
    # cantilevered slab edge, say) still gets the break.
    for run in plan.runs.get(side, ()):
        for endpoint in (run.lo_mm, run.hi_mm):
            if endpoint - span_lo > slack and span_hi - endpoint > slack:
                raw.append(endpoint)

    breakpoints = merge_breakpoints(raw, keep=(span_lo, span_hi))
    if len(breakpoints) < 3:
        return None  # identical to level 1 — §7 step 3's "skip the duplicate", applied here
    return chain_from_breakpoints(
        chain_id=_chain_id(plan.storey_id, side, 2),
        orientation=_SIDE_ORIENTATION[side],
        level=2,
        offset_mm=offset,
        breakpoints=breakpoints,
        line_mm=building_line + outward * offset,
        reference_mm=building_line,
        outward=outward,
        kind=KIND_OUTER,
        storey_id=plan.storey_id,
        side=side,
        anchors=anchors,
    )


def build_level_3(
    plan: StoreyPlan, side: str, extents: Extents, config: AutoDimConfig
) -> DimChainInfo | None:
    """Openings on this facade — to centreline, or to jambs when ``dimToJamb`` is set.

    §7 step 6: "openings dimensioned to centreline (config flag ``dimToJamb`` for firm
    preference)". Centreline mode gives one breakpoint per opening, so the chain reads
    "corner → door centre → window centre → corner", which is what a mason sets out
    from. Jamb mode gives two, so the chain alternates pier / opening width and the
    printed opening width is exact.
    """
    span_lo, span_hi = extents.span_for(side)
    building_line = extents.building_line_for(side)
    outward = _OUTWARD[side]
    offset = config.offset_for_level(3)

    wall_ids: list[str] = []
    for run in plan.runs.get(side, ()):
        wall_ids.extend(run.wall_ids)
    openings = plan.openings_on(wall_ids)
    if not openings:
        return None

    raw: list[int] = [span_lo, span_hi]
    anchors: dict[int, str] = {}
    for opening in openings:
        if config.dim_to_jamb:
            positions = (opening.jamb_lo_mm, opening.jamb_hi_mm)
        else:
            positions = (opening.centre_mm,)
        for position in positions:
            if position <= span_lo or position >= span_hi:
                continue  # an opening at the very corner: nothing to measure to
            raw.append(position)
            anchors[position] = opening.id

    breakpoints = merge_breakpoints(raw, keep=(span_lo, span_hi))
    if len(breakpoints) < 3:
        return None
    return chain_from_breakpoints(
        chain_id=_chain_id(plan.storey_id, side, 3),
        orientation=_SIDE_ORIENTATION[side],
        level=3,
        offset_mm=offset,
        breakpoints=breakpoints,
        line_mm=building_line + outward * offset,
        reference_mm=building_line,
        outward=outward,
        kind=KIND_OUTER,
        storey_id=plan.storey_id,
        side=side,
        anchors=anchors,
    )


def build_outer_chains(
    plan: StoreyPlan, config: AutoDimConfig = DEFAULT_CONFIG
) -> tuple[DimChainInfo, ...]:
    """§7 step 2 for all four sides. Ordered side-major, level-minor: S1 S2 S3 E1 ...

    Deterministic by construction: ``SIDES`` is a fixed tuple and every breakpoint list
    is sorted before it becomes a chain.
    """
    if plan.extents is None:
        return ()
    out: list[DimChainInfo] = []
    for side in SIDES:
        for builder in (build_level_1, build_level_2, build_level_3):
            chain = builder(plan, side, plan.extents, config)
            if chain is not None:
                out.append(chain)
    return tuple(out)


__all__ = [
    "build_level_1",
    "build_level_2",
    "build_level_3",
    "build_outer_chains",
]
