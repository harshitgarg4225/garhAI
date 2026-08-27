"""Chain construction from breakpoints — where §7 step 5 is *made* true.

    5. Values from integer mm — chains must sum exactly (assert in tests:
       Σ segments == overall, every chain).

The tests assert it. This module is why it holds.

Every chain in the engine is built from a **sorted list of integer breakpoints**, and
its segments are the consecutive differences. That makes the invariant structural:

    Σ (b[i+1] - b[i])  ==  b[-1] - b[0]  ==  overall

for any integers whatsoever. There is no accumulation, no rounding step, and no place
for a discrepancy to enter — which is a stronger guarantee than computing lengths
independently and then checking they happen to add up. Dropping a breakpoint (the
too-close merge below) changes the segment values but cannot break the sum, because the
first and last breakpoints are never dropped.

The alternative design — "measure each room, then measure the whole thing" — is how
dimension chains in real drawing sets end up off by a millimetre. We do not use it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from services.drawings.autodim.extract import HORIZONTAL, VERTICAL
from services.drawings.dimensions import DimChain, DimSegment

#: Two breakpoints closer than this collapse into one. 50mm is under half the thinnest
#: wall in the catalogue: nothing real is that narrow, so a sub-50mm segment means two
#: features effectively coincide, and printing "12" between two ticks 0.12mm apart on
#: paper is noise a draughtsman would delete. The *first* breakpoint of a cluster wins,
#: and the chain's ends are never merged away, so the sum invariant is untouched.
MIN_BREAKPOINT_GAP_MM = 50

KIND_OUTER = "outer"
KIND_INNER = "inner"


@dataclass(frozen=True)
class DimChainInfo:
    """A :class:`DimChain` plus where on the sheet it lives.

    ``DimChain`` is the shared contract (``services.drawings.dimensions``) that the DXF
    writer and the annotation store consume; it carries the §7 *offset* but not the
    absolute position, because the offset is the thing an architect edits. The renderer
    needs the absolute line, so it is derived once here and passed along rather than
    recomputed by every consumer from a sign convention it might get backwards.
    """

    chain: DimChain
    kind: str
    #: 'S' | 'E' | 'N' | 'W' for outer chains; None for inner ones.
    side: str | None
    #: The room an inner chain measures; None for outer chains.
    room_id: str | None
    #: Absolute coordinate of the dimension line on the perpendicular axis.
    line_mm: int
    #: The coordinate the offset was measured from (the building line, or a room face).
    reference_mm: int
    #: +1 or -1: which way is "away from the thing being measured". Text prefers this
    #: side; §7 step 4's "flip" tries the other one.
    outward: int

    @property
    def id(self) -> str:
        return self.chain.id

    @property
    def level(self) -> int:
        return int(self.chain.level)

    @property
    def orientation(self) -> str:
        return str(self.chain.orientation)

    def absolute_start_mm(self) -> int:
        return self.chain.origin_mm

    def absolute_end_mm(self) -> int:
        return self.chain.origin_mm + self.chain.overall_mm

    def segment_bounds(self, index: int) -> tuple[int, int]:
        """Absolute ``(start, end)`` of segment ``index`` along the measuring axis."""
        segment = self.chain.segments[index]
        start = self.chain.origin_mm + segment.start_mm
        return (start, start + segment.length_mm)

    def to_json(self) -> dict[str, Any]:
        payload = self.chain.to_json()
        payload.update(
            {
                "kind": self.kind,
                "side": self.side,
                "roomId": self.room_id,
                "lineMm": self.line_mm,
                "referenceMm": self.reference_mm,
                "outward": self.outward,
            }
        )
        return payload


def merge_breakpoints(
    breakpoints: Iterable[int],
    *,
    min_gap_mm: int = MIN_BREAKPOINT_GAP_MM,
    keep: Sequence[int] = (),
) -> tuple[int, ...]:
    """Sort, de-duplicate, and collapse breakpoints closer together than ``min_gap_mm``.

    ``keep`` names breakpoints that must survive regardless (the chain's two ends).
    Deterministic: sorted input, first-wins on a cluster.
    """
    protected = set(keep)
    ordered = sorted({int(b) for b in breakpoints})
    out: list[int] = []
    for value in ordered:
        if not out:
            out.append(value)
            continue
        if value - out[-1] >= min_gap_mm:
            out.append(value)
            continue
        # Too close to the previous survivor.
        if value not in protected:
            continue  # an interior breakpoint: drop it
        if out[-1] in protected:
            out.append(value)  # both ends protected: a genuinely tiny chain, kept whole
        else:
            out[-1] = value  # a protected end outranks an interior breakpoint
    return tuple(out)


def chain_from_breakpoints(
    *,
    chain_id: str,
    orientation: str,
    level: int,
    offset_mm: int,
    breakpoints: Sequence[int],
    line_mm: int,
    reference_mm: int,
    outward: int,
    kind: str,
    storey_id: str | None = None,
    side: str | None = None,
    room_id: str | None = None,
    anchors: Mapping[int, str] | None = None,
    labels: Mapping[int, str] | None = None,
) -> DimChainInfo | None:
    """Build one chain. Returns ``None`` when there is nothing to measure.

    ``anchors`` maps a breakpoint value to the model element id that put it there. Each
    segment is anchored to the element at its **far end** — "this dimension measures to
    that door" — falling back to the element at its near end. That is what annotation
    anchoring (§7) re-attaches to after an edit, so it has to name a real element, not
    a position.
    """
    if orientation not in (HORIZONTAL, VERTICAL):
        raise ValueError("orientation must be horizontal or vertical, got %r" % orientation)
    points = tuple(breakpoints)
    if len(points) < 2:
        return None

    anchor_map = dict(anchors or {})
    label_map = dict(labels or {})
    origin = points[0]
    segments: list[DimSegment] = []
    for index in range(len(points) - 1):
        lo, hi = points[index], points[index + 1]
        anchor = anchor_map.get(hi) or anchor_map.get(lo)
        segments.append(
            DimSegment(
                start_mm=lo - origin,
                length_mm=hi - lo,
                anchor_element_id=anchor,
                label_override=label_map.get(hi),
            )
        )

    chain = DimChain(
        id=chain_id,
        orientation=orientation,  # type: ignore[arg-type]
        level=level,  # type: ignore[arg-type]
        offset_mm=offset_mm,
        origin_mm=origin,
        segments=tuple(segments),
        overall_mm=points[-1] - points[0],
        storey_id=storey_id,
    )
    # Cheap, and it fires at the point of construction rather than in a test three
    # layers away. Construction from breakpoints makes it unreachable — which is
    # exactly why it is safe to assert unconditionally.
    if not chain.is_consistent():  # pragma: no cover - structurally impossible
        raise AssertionError(
            "chain %s built from breakpoints does not sum: %d segments vs overall %d"
            % (chain_id, chain.sum_of_segments(), chain.overall_mm)
        )
    return DimChainInfo(
        chain=chain,
        kind=kind,
        side=side,
        room_id=room_id,
        line_mm=line_mm,
        reference_mm=reference_mm,
        outward=outward,
    )


__all__ = [
    "KIND_INNER",
    "KIND_OUTER",
    "MIN_BREAKPOINT_GAP_MM",
    "DimChainInfo",
    "chain_from_breakpoints",
    "merge_breakpoints",
]
