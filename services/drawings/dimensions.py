"""§7 auto-dimensioning. **Types and the chain invariant are real; the engine is Phase 8.**

The algorithm §7 specifies has five steps; the one that must never be got wrong is
step 5:

    Values from integer mm — chains must sum exactly (assert in tests: Σ segments ==
    overall, every chain).

:meth:`DimChain.is_consistent` and :func:`assert_chains_sum` implement that invariant
now, because it is the property the golden tests assert and it costs nothing to have
before the engine that produces the chains. A dimension chain whose parts do not add up
to its whole is the single most embarrassing defect a drawing set can ship — a
contractor builds from those numbers.

The offsets and thresholds below are §7's, written once so the engine, the renderer and
the tests share them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

PHASE = "Phase 8 (Drawings & exports)"

#: §7 chain levels, offset from the building line in **paper-scaled model mm**.
LEVEL_1_OFFSET_MM = 2_400  # overall extent
LEVEL_2_OFFSET_MM = 1_800  # external wall segment breakpoints
LEVEL_3_OFFSET_MM = 1_200  # opening centrelines

#: §7: "openings dimensioned to centreline (config flag `dimToJamb` for firm preference)".
DEFAULT_DIM_TO_JAMB = False

Orientation = Literal["horizontal", "vertical"]
ChainLevel = Literal[1, 2, 3, 4]


@dataclass(frozen=True)
class DimSegment:
    """One measured span within a chain. Integer millimetres, always."""

    #: Distance along the chain axis where this segment starts.
    start_mm: int
    length_mm: int
    #: Element this segment measures, so the annotation can follow it across edits.
    anchor_element_id: str | None = None
    label_override: str | None = None

    @property
    def end_mm(self) -> int:
        return self.start_mm + self.length_mm

    def label(self) -> str:
        """§7: dim text is in millimetres regardless of the project's display units."""
        return self.label_override or str(self.length_mm)


@dataclass(frozen=True)
class DimChain:
    """A run of segments along one axis, at one offset level."""

    id: str
    orientation: Orientation
    level: ChainLevel
    #: Perpendicular offset from the building line, in model mm.
    offset_mm: int
    #: Where the chain starts along its axis.
    origin_mm: int
    segments: tuple[DimSegment, ...]
    #: The overall dimension this chain's segments must add up to.
    overall_mm: int
    storey_id: str | None = None
    layer: str = "A-DIM"

    def sum_of_segments(self) -> int:
        return sum(segment.length_mm for segment in self.segments)

    def is_consistent(self) -> bool:
        """§7 step 5: the parts must equal the whole, exactly.

        Exact integer equality — no tolerance. Integer millimetres exist precisely so
        this can be an equality rather than an approximation, and a 1mm discrepancy in
        a dimension chain is a real defect, not a rounding artefact.
        """
        return self.sum_of_segments() == self.overall_mm

    def inconsistency(self) -> int:
        """Signed error, for the assertion message. 0 when consistent."""
        return self.sum_of_segments() - self.overall_mm

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "orientation": self.orientation,
            "level": self.level,
            "offsetMm": self.offset_mm,
            "originMm": self.origin_mm,
            "overallMm": self.overall_mm,
            "storeyId": self.storey_id,
            "layer": self.layer,
            "segments": [
                {
                    "startMm": segment.start_mm,
                    "lengthMm": segment.length_mm,
                    "label": segment.label(),
                    "anchorElementId": segment.anchor_element_id,
                }
                for segment in self.segments
            ],
        }


@dataclass(frozen=True)
class LabelBox:
    """A placed label's bounding box on the collision grid (§7 step 4)."""

    x_mm: int
    y_mm: int
    width_mm: int
    height_mm: int
    owner_id: str = ""

    def overlaps(self, other: LabelBox) -> bool:
        """Touching is allowed; overlapping is not. §16 asserts no overlaps."""
        return not (
            self.x_mm + self.width_mm <= other.x_mm
            or other.x_mm + other.width_mm <= self.x_mm
            or self.y_mm + self.height_mm <= other.y_mm
            or other.y_mm + other.height_mm <= self.y_mm
        )


class ChainConsistencyError(AssertionError):
    """A dimension chain's segments do not sum to its overall. Always a bug."""


def assert_chains_sum(chains: Sequence[DimChain]) -> None:
    """Raise unless every chain's segments sum exactly to its overall. **Implemented.**

    §16 runs this over the golden-file corpus. It is deliberately an assertion that
    fails the build rather than a warning: a wrong dimension is worse than no drawing.
    """
    broken = [chain for chain in chains if not chain.is_consistent()]
    if broken:
        details = "; ".join(
            "%s: segments sum to %d but overall is %d (off by %+d)"
            % (chain.id, chain.sum_of_segments(), chain.overall_mm, chain.inconsistency())
            for chain in broken[:5]
        )
        raise ChainConsistencyError(
            "%d dimension chain(s) do not add up: %s" % (len(broken), details)
        )


def find_label_collisions(boxes: Sequence[LabelBox]) -> tuple[tuple[str, str], ...]:
    """Every overlapping pair of labels. **Implemented.**

    §16: "collision-free assertion (no overlapping text bboxes)". O(n²), which is fine —
    a sheet has hundreds of labels, not millions.
    """
    collisions: list[tuple[str, str]] = []
    for index, box in enumerate(boxes):
        for other in boxes[index + 1 :]:
            if box.overlaps(other):
                collisions.append((box.owner_id, other.owner_id))
    return tuple(collisions)


# ---------------------------------------------------------------------------
# The engine itself (§7 steps 1-4) — Phase 8
# ---------------------------------------------------------------------------
def collect_wall_axes(model: Mapping[str, Any], storey_id: str) -> Any:
    """§7 step 1: gather wall axes for a storey and cluster them by orientation.

    Deferred to %s.
    """ % PHASE
    raise NotImplementedError(
        "collect_wall_axes is implemented in %s (§7 step 1; MVP is orthogonal-only)." % PHASE
    )


def build_outer_chains(model: Mapping[str, Any], storey_id: str) -> tuple[DimChain, ...]:
    """§7 step 2: three chain levels per side — overall, segments, opening centrelines.

    Deferred to %s. The offsets are already defined above, and
    :func:`assert_chains_sum` is the test this must satisfy.
    """ % PHASE
    raise NotImplementedError(
        "build_outer_chains is implemented in %s (§7 step 2, using LEVEL_1/2/3_OFFSET_MM)." % PHASE
    )


def build_inner_chains(model: Mapping[str, Any], storey_id: str) -> tuple[DimChain, ...]:
    """§7 step 3: one width + one depth chain per room, deduped against neighbours.

    Deferred to %s.
    """ % PHASE
    raise NotImplementedError(
        "build_inner_chains is implemented in %s (§7 step 3, including the "
        "shared-wall duplicate skip)." % PHASE
    )


def place_labels(chains: Sequence[DimChain], obstacles: Sequence[LabelBox]) -> tuple[LabelBox, ...]:
    """§7 step 4: greedy placement on a collision grid — flip, shift, shrink, leader.

    Deferred to %s. :func:`find_label_collisions` is the assertion it must satisfy.
    """ % PHASE
    raise NotImplementedError(
        "place_labels is implemented in %s (§7 step 4: flip side → shift along chain → "
        "shrink one step → leader line, never overlap)." % PHASE
    )


__all__ = [
    "DEFAULT_DIM_TO_JAMB",
    "LEVEL_1_OFFSET_MM",
    "LEVEL_2_OFFSET_MM",
    "LEVEL_3_OFFSET_MM",
    "PHASE",
    "ChainConsistencyError",
    "ChainLevel",
    "DimChain",
    "DimSegment",
    "LabelBox",
    "Orientation",
    "assert_chains_sum",
    "build_inner_chains",
    "build_outer_chains",
    "collect_wall_axes",
    "find_label_collisions",
    "place_labels",
]
