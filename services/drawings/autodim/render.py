"""Chains + placed labels → ``A-DIM`` primitives.

The last mile: everything above this module is arithmetic on the model, and everything
below it is a file format. This is where a chain becomes ink — a dimension line, one
witness line per breakpoint, an oblique tick at each breakpoint, a text run per segment,
and a leader for the few labels that needed one.

The primitives are the projection module's (:mod:`services.drawings.autodim.primitives`
explains why they are not this package's own), so the SVG and DXF writers consume a
dimension exactly the way they consume a wall — no dimension-shaped special case anywhere
downstream.

The geometry mirrors the DIMSTYLE that ``services.drawings.dxf.setup_dimstyle`` writes
(``dimexo`` gap before the witness line starts, ``dimexe`` extension past the dimension
line, ``dimtsz`` tick size, ``dimgap`` around the text, ``dimtad = 1`` text above the
line), so a DXF consumer that re-renders the dimension from the style lands on top of
these primitives instead of beside them. That agreement is the reason those numbers live
in :mod:`services.drawings.autodim.config` as paper tenths rather than being invented
here.

Primitive order is fixed — chains by level then id, and within a chain line → witness →
ticks, then all labels by chain and segment. §16 diffs goldens with tolerance 0, and a
stream that emits the same primitives in a different order every run is not a golden, it
is a lottery ticket.
"""

from __future__ import annotations

from collections.abc import Sequence

from services.drawings.autodim.chains import DimChainInfo
from services.drawings.autodim.config import DEFAULT_CONFIG, AutoDimConfig
from services.drawings.autodim.extract import HORIZONTAL
from services.drawings.autodim.placement import PlacedLabel
from services.drawings.autodim.primitives import (
    KIND_DIM,
    KIND_LEADER,
    KIND_TEXT,
    KIND_TICK,
    KIND_WITNESS,
    Line,
    Point,
    Primitive,
    Text,
)
from services.drawings.layers import A_DIM


def _pt(chain: DimChainInfo, along_mm: int, perpendicular_mm: int) -> Point:
    """Chain-local (along, perpendicular) → plan (x, y)."""
    if chain.orientation == HORIZONTAL:
        return (along_mm, perpendicular_mm)
    return (perpendicular_mm, along_mm)


def breakpoints_of(chain: DimChainInfo) -> tuple[int, ...]:
    """The absolute breakpoints the chain was built from.

    Recovered from the segments rather than stored twice: the chain start, plus the far
    end of every segment. Segments are contiguous by construction, so this is exact.
    """
    origin = chain.chain.origin_mm
    return (
        origin,
        *tuple(origin + segment.start_mm + segment.length_mm for segment in chain.chain.segments),
    )


def chain_primitives(
    chain: DimChainInfo, config: AutoDimConfig = DEFAULT_CONFIG
) -> tuple[Primitive, ...]:
    """The lines of one chain: dimension line, witness lines, ticks."""
    out: list[Primitive] = []
    start, end = chain.absolute_start_mm(), chain.absolute_end_mm()

    out.append(
        Line(
            layer=A_DIM,
            a=_pt(chain, start, chain.line_mm),
            b=_pt(chain, end, chain.line_mm),
            owner_id=chain.id,
            kind=KIND_DIM,
        )
    )

    witness_from = chain.reference_mm + chain.outward * config.witness_offset_mm()
    witness_to = chain.line_mm + chain.outward * config.witness_extend_mm()
    half_tick = max(1, config.tick_size_mm() // 2)

    for position in breakpoints_of(chain):
        out.append(
            Line(
                layer=A_DIM,
                a=_pt(chain, position, witness_from),
                b=_pt(chain, position, witness_to),
                owner_id=chain.id,
                kind=KIND_WITNESS,
            )
        )
        out.append(
            Line(
                layer=A_DIM,
                a=_pt(chain, position - half_tick, chain.line_mm - half_tick),
                b=_pt(chain, position + half_tick, chain.line_mm + half_tick),
                owner_id=chain.id,
                kind=KIND_TICK,
            )
        )

    return tuple(out)


def label_primitives(label: PlacedLabel) -> tuple[Primitive, ...]:
    """The text of one label, plus its leader when it needed one."""
    out: list[Primitive] = []
    if label.leader_from is not None and label.leader_to is not None:
        out.append(
            Line(
                layer=A_DIM,
                a=label.leader_from,
                b=label.leader_to,
                owner_id=label.id,
                kind=KIND_LEADER,
            )
        )
    out.append(
        Text(
            layer=A_DIM,
            position=label.anchor,
            text=label.text,
            height_mm=label.height_mm,
            rotation_deg=label.rotation_deg,
            h_align="center",
            v_align="middle",
            owner_id=label.id,
            kind=KIND_TEXT,
        )
    )
    return tuple(out)


def render_primitives(
    chains: Sequence[DimChainInfo],
    labels: Sequence[PlacedLabel],
    config: AutoDimConfig = DEFAULT_CONFIG,
) -> tuple[Primitive, ...]:
    """The full ``A-DIM`` stream for a sheet, in a fixed order."""
    out: list[Primitive] = []
    for chain in sorted(chains, key=lambda c: (c.level, c.id)):
        out.extend(chain_primitives(chain, config))
    for label in sorted(labels, key=lambda item: (item.chain_id, item.segment_index)):
        out.extend(label_primitives(label))
    return tuple(out)


__all__ = ["breakpoints_of", "chain_primitives", "label_primitives", "render_primitives"]
