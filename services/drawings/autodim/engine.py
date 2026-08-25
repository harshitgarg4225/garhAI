"""The auto-dimensioning engine: §7 steps 1-6, in order, for one storey.

One public entry point — :func:`dimension_storey` — and a result object that carries
everything a sheet, a test and a golden file need: the chains, the placed labels, the
``A-DIM`` primitive stream, the placement statistics, and an honest list of what was
skipped and why.

**Why this module imports nothing heavy.** ezdxf is pinned but not installed on every
machine, structlog and pydantic are absent from a bare interpreter, and OR-Tools is a
half-gigabyte. None of them appear anywhere in ``autodim``: the engine is integer
arithmetic over the model, so it runs — and is *proven to run* — on a stock Python. The
ezdxf dependency lives exactly one module away, at the output boundary
(``services.drawings.dxf``), and is imported lazily there. That is the structural
decision the whole package is arranged around, because a dimension engine that can only
be tested in a full environment is a dimension engine that stops being tested.

**Invariants this module asserts, every run, not only in tests:**

* every chain's segments sum exactly to its overall (§7 step 5),
* no two label boxes overlap (§7 step 4), and
* the primitive stream satisfies the projection module's own validator (integer
  coordinates, real ``A-DIM`` layer, no degenerate geometry).

They are cheap — a sum, a swept comparison over a few hundred boxes, and a walk of the
stream — and a drawing set that violates any of them is not worth shipping, so
:func:`dimension_storey` refuses to return one. ``verify=False`` exists for benchmarking
only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.drawings.dimensions import (
    ChainConsistencyError,
    DimChain,
    LabelBox,
    assert_chains_sum,
    find_label_collisions,
)

from services.drawings.autodim.chains import DimChainInfo
from services.drawings.autodim.config import DEFAULT_CONFIG, AutoDimConfig
from services.drawings.autodim.extract import (
    SKIP_NON_ORTHOGONAL,
    SkippedWall,
    StoreyPlan,
    build_storey_plan,
    storey_ids,
)
from services.drawings.autodim.inner import SuppressedChain, build_inner_chains
from services.drawings.autodim.outer import build_outer_chains
from services.drawings.autodim.placement import (
    CollisionGrid,
    PlacedLabel,
    PlacementStats,
    place_labels,
    summarise,
)
from services.drawings.autodim.primitives import (
    Primitive,
    primitives_digest,
    primitives_to_json,
    validate_primitives,
)
from services.drawings.autodim.render import render_primitives


class LabelOverlapError(AssertionError):
    """Two label boxes overlap. §7 step 4 says never; §16 asserts it."""


@dataclass(frozen=True)
class DimensionResult:
    """Everything the engine produced for one storey."""

    storey_id: str
    chains: Tuple[DimChainInfo, ...]
    labels: Tuple[PlacedLabel, ...]
    primitives: Tuple[Primitive, ...]
    stats: PlacementStats
    skipped_walls: Tuple[SkippedWall, ...]
    suppressed_chains: Tuple[SuppressedChain, ...]
    plan: Optional[StoreyPlan] = None
    notes: Tuple[str, ...] = ()

    # -- convenience views used by tests, the sheet renderer and the report ----
    @property
    def dim_chains(self) -> Tuple[DimChain, ...]:
        """The shared-contract chains, for ``assert_chains_sum`` and the DXF writer."""
        return tuple(info.chain for info in self.chains)

    def chains_at_level(self, level: int) -> Tuple[DimChainInfo, ...]:
        return tuple(info for info in self.chains if info.level == level)

    def counts_by_level(self) -> Tuple[Tuple[int, int], ...]:
        return tuple(
            (level, len(self.chains_at_level(level))) for level in (1, 2, 3, 4)
        )

    def segments_by_level(self) -> Tuple[Tuple[int, int], ...]:
        return tuple(
            (
                level,
                sum(len(info.chain.segments) for info in self.chains_at_level(level)),
            )
            for level in (1, 2, 3, 4)
        )

    def label_boxes(self) -> Tuple[LabelBox, ...]:
        return tuple(label.box for label in self.labels)

    def digest(self) -> str:
        """SHA-256 of the primitive stream — a one-line golden for a whole storey."""
        return primitives_digest(self.primitives)

    def to_json(self) -> Dict[str, Any]:
        """The golden-file form. Stable ordering, integers only, no timestamps."""
        return {
            "storeyId": self.storey_id,
            "chains": [info.to_json() for info in self.chains],
            "labels": [label.to_json() for label in self.labels],
            "primitives": primitives_to_json(self.primitives),
            "stats": self.stats.to_json(),
            "skippedWalls": [skip.to_json() for skip in self.skipped_walls],
            "suppressedChains": [item.to_json() for item in self.suppressed_chains],
            "notes": list(self.notes),
        }


def assert_no_label_overlaps(labels: Sequence[PlacedLabel]) -> None:
    """§7 step 4's "never overlap", as an assertion over the placed boxes."""
    collisions = find_label_collisions([label.box for label in labels])
    if collisions:
        detail = "; ".join("%s <-> %s" % pair for pair in collisions[:5])
        raise LabelOverlapError(
            "%d overlapping dimension label(s): %s" % (len(collisions), detail)
        )


def _notes_for(plan: StoreyPlan, suppressed: Sequence[SuppressedChain]) -> Tuple[str, ...]:
    """Sheet notes: the honest record of what the engine did not dimension.

    These are not log lines — they belong on the sheet (or in the review tray), because
    the architect is the one who has to dimension a diagonal wall by hand.
    """
    notes: List[str] = []
    non_ortho = [
        skip for skip in plan.skipped_walls if skip.reason == SKIP_NON_ORTHOGONAL
    ]
    if non_ortho:
        notes.append(
            "%d non-orthogonal wall(s) not auto-dimensioned (MVP is orthogonal-only) — "
            "dimension by hand: %s"
            % (len(non_ortho), ", ".join(skip.id for skip in non_ortho))
        )
    other_skips = [
        skip for skip in plan.skipped_walls if skip.reason != SKIP_NON_ORTHOGONAL
    ]
    if other_skips:
        notes.append(
            "%d wall(s) ignored (%s)"
            % (
                len(other_skips),
                ", ".join(sorted({skip.reason for skip in other_skips})),
            )
        )
    non_rect = [room for room in plan.rooms if not room.is_rectangular]
    if non_rect:
        notes.append(
            "%d non-rectangular room(s) dimensioned to their bounding box — check by "
            "hand: %s" % (len(non_rect), ", ".join(room.id for room in non_rect))
        )
    if suppressed:
        notes.append(
            "%d inner chain(s) suppressed as duplicates across a shared wall"
            % len(suppressed)
        )
    if plan.extents is None:
        notes.append("no envelope walls on this storey — no outer chains generated")
    return tuple(notes)


def dimension_storey(
    model: Any,
    storey_id: str,
    *,
    config: AutoDimConfig = DEFAULT_CONFIG,
    obstacles: Sequence[LabelBox] = (),
    grid: Optional[CollisionGrid] = None,
    verify: bool = True,
) -> DimensionResult:
    """Dimension one storey of a plan. §7 steps 1-6.

    ``model`` may be a folded ``garh_model.HouseModel``, a ``ProjectDoc``, or the wire
    JSON of either — see ``extract._field``. ``obstacles`` are the boxes the plan
    projector has already committed to (room name blocks, door tags, the north arrow),
    so dimension labels can avoid them.

    Pure: no I/O, no logging, no globals. Same inputs, same output, byte for byte.
    """
    plan = build_storey_plan(
        model, storey_id, min_thickness_mm=config.min_wall_thickness_mm
    )
    outer = build_outer_chains(plan, config)
    inner, suppressed = build_inner_chains(plan, config)
    chains = tuple(outer) + tuple(inner)

    labels, filled_grid = place_labels(
        chains, config=config, obstacles=obstacles, grid=grid
    )
    primitives = render_primitives(chains, labels, config)

    result = DimensionResult(
        storey_id=storey_id,
        chains=chains,
        labels=labels,
        primitives=primitives,
        stats=summarise(labels),
        skipped_walls=plan.skipped_walls,
        suppressed_chains=suppressed,
        plan=plan,
        notes=_notes_for(plan, suppressed),
    )

    if verify:
        assert_chains_sum(result.dim_chains)
        assert_no_label_overlaps(result.labels)
        validate_primitives(result.primitives)
    return result


def dimension_model(
    model: Any,
    *,
    config: AutoDimConfig = DEFAULT_CONFIG,
    verify: bool = True,
) -> Tuple[DimensionResult, ...]:
    """Every storey, in model order. Each storey gets its own collision grid.

    Separate grids because separate sheets: a first-floor plan is drawn on its own sheet
    (or its own viewport), so a ground-floor label cannot collide with it.
    """
    return tuple(
        dimension_storey(model, sid, config=config, verify=verify)
        for sid in storey_ids(model)
    )


__all__ = [
    "ChainConsistencyError",
    "DimensionResult",
    "LabelOverlapError",
    "assert_no_label_overlaps",
    "dimension_model",
    "dimension_storey",
]
