"""Auto-dimensioning engine (playbook §7 steps 1-6) — the from-scratch IP of Phase 8.

The product spec calls the drawing set the moat and puts a launch gate on this module in
particular: **">=90% of dimension chains accepted unedited on 10 golden plans"**. An
architect's fee is earned on drawings, and drawings are earned on dimensions.

Six steps, one module each, in the order §7 states them:

======  ==================================================  ==========================
step    what it does                                        module
======  ==================================================  ==========================
1       wall axes per storey, clustered H/V, ortho only     :mod:`.extract`
2       outer chains: 4 sides x 3 levels (2400/1800/1200)   :mod:`.outer`
3       inner dims: width + depth per room, deduped         :mod:`.inner`
4       label placement: flip -> shift -> shrink -> leader   :mod:`.placement`
5       integer mm; every chain sums exactly                :mod:`.chains`
6       mm text always; centreline or jamb per firm          :mod:`.config`
======  ==================================================  ==========================

:mod:`.render` turns chains and labels into ``A-DIM`` primitives — the projection
module's ``Line`` and ``Text``, not a dialect of our own (:mod:`.primitives` explains
the coordination) — and :mod:`.engine` runs the whole sequence for a storey.

Two properties are worth knowing before reading the code.

**It is pure integer arithmetic with no dependencies.** No ezdxf, no pydantic, no
structlog, no numpy — the engine runs on a stock interpreter and is tested there. ezdxf
lives one module away at the output boundary (``services.drawings.dxf``) and is imported
lazily, so its absence can never block the part of Phase 8 that is actually testable.

**Every chain is built from integer breakpoints, so Σ segments == overall is
structural**, not checked-and-hoped. :func:`services.drawings.dimensions.assert_chains_sum`
runs on every call anyway, because a contractor builds from these numbers.

Typical use::

    from services.drawings.autodim import dimension_storey

    result = dimension_storey(house, storey_id)        # asserts both invariants
    result.counts_by_level()                            # ((1, 4), (2, 4), (3, 4), (4, 11))
    result.stats.leaders                                # how many labels needed a leader
    dxf_writer.write(result.primitives)                 # A-DIM Line/Text, integer mm
"""

from __future__ import annotations

from services.drawings.autodim.chains import (
    KIND_INNER,
    KIND_OUTER,
    MIN_BREAKPOINT_GAP_MM,
    DimChainInfo,
    chain_from_breakpoints,
    merge_breakpoints,
)
from services.drawings.autodim.config import (
    DEFAULT_CONFIG,
    DEFAULT_DIM_TO_JAMB,
    LEVEL_1_OFFSET_MM,
    LEVEL_2_OFFSET_MM,
    LEVEL_3_OFFSET_MM,
    LEVEL_4_OFFSET_MM,
    TEXT_HEIGHT_STEPS_PAPER_TENTHS,
    AutoDimConfig,
)
from services.drawings.autodim.engine import (
    DimensionResult,
    LabelOverlapError,
    assert_no_label_overlaps,
    dimension_model,
    dimension_storey,
)
from services.drawings.autodim.extract import (
    HORIZONTAL,
    SIDES,
    VERTICAL,
    Extents,
    FacadeRun,
    OpeningRef,
    RoomRef,
    SkippedWall,
    StoreyPlan,
    WallAxis,
    build_storey_plan,
    collect_wall_axes,
    facade_runs,
)
from services.drawings.autodim.inner import SuppressedChain, build_inner_chains
from services.drawings.autodim.outer import build_outer_chains
from services.drawings.autodim.placement import (
    STRATEGIES,
    STRATEGY_BASE,
    STRATEGY_FLIP,
    STRATEGY_LEADER,
    STRATEGY_SHIFT,
    STRATEGY_SHRINK,
    CollisionGrid,
    PlacedLabel,
    PlacementError,
    PlacementStats,
    place_labels,
)
from services.drawings.autodim.primitives import (
    DIM_KINDS,
    KIND_DIM,
    KIND_LEADER,
    KIND_TEXT,
    KIND_TICK,
    KIND_WITNESS,
    Line,
    Point,
    Primitive,
    Text,
    primitives_to_json,
    validate_primitives,
)
from services.drawings.autodim.render import render_primitives

__all__ = [
    "DEFAULT_CONFIG",
    "DEFAULT_DIM_TO_JAMB",
    "DIM_KINDS",
    "HORIZONTAL",
    "KIND_DIM",
    "KIND_INNER",
    "KIND_LEADER",
    "KIND_OUTER",
    "KIND_TEXT",
    "KIND_TICK",
    "KIND_WITNESS",
    "LEVEL_1_OFFSET_MM",
    "LEVEL_2_OFFSET_MM",
    "LEVEL_3_OFFSET_MM",
    "LEVEL_4_OFFSET_MM",
    "MIN_BREAKPOINT_GAP_MM",
    "SIDES",
    "STRATEGIES",
    "STRATEGY_BASE",
    "STRATEGY_FLIP",
    "STRATEGY_LEADER",
    "STRATEGY_SHIFT",
    "STRATEGY_SHRINK",
    "TEXT_HEIGHT_STEPS_PAPER_TENTHS",
    "VERTICAL",
    "AutoDimConfig",
    "CollisionGrid",
    "DimChainInfo",
    "DimensionResult",
    "Extents",
    "FacadeRun",
    "LabelOverlapError",
    "Line",
    "OpeningRef",
    "PlacedLabel",
    "PlacementError",
    "PlacementStats",
    "Point",
    "Primitive",
    "RoomRef",
    "SkippedWall",
    "StoreyPlan",
    "SuppressedChain",
    "Text",
    "WallAxis",
    "assert_no_label_overlaps",
    "build_inner_chains",
    "build_outer_chains",
    "build_storey_plan",
    "chain_from_breakpoints",
    "collect_wall_axes",
    "dimension_model",
    "dimension_storey",
    "facade_runs",
    "merge_breakpoints",
    "place_labels",
    "primitives_to_json",
    "render_primitives",
    "validate_primitives",
]
