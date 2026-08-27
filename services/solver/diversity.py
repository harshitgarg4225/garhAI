"""§5.5 — diversity signatures and ranking. **Fully implemented.**

    Signature = multiset of (roomType → plot zone) + stair anchor. Reject candidates
    within Hamming distance 2 of an already-kept signature. Keep top 3-5 by composite.

Pure set arithmetic over data the critic already produced, so it is real code today
rather than a Phase 3 stub. It is also the difference between an options screen that
offers three genuinely different plans and one that offers the same plan three times
with a wall nudged — which is the failure mode this section exists to prevent.

Determinism matters as much as correctness here: the same candidates must always
produce the same three options, so every tie is broken explicitly (by composite, then
by signature, then by id) and nothing iterates over an unordered set.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from services.solver.geometry import bbox, zone_for_point
from services.solver.types import BuildableEnvelope, PlanOption, RoomPlacement

#: §5.5: reject a candidate within this Hamming distance of one already kept.
MIN_SIGNATURE_DISTANCE = 3

#: Room types whose placement genuinely characterises a plan. Passages and shafts move
#: with everything else, so including them would make near-identical plans look
#: different and defeat the filter.
SIGNIFICANT_ROOM_TYPES: frozenset[str] = frozenset(
    {
        "living",
        "dining",
        "living_dining",
        "kitchen",
        "kitchen_dining",
        "bedroom_master",
        "bedroom",
        "guest_bedroom",
        "study",
        "pooja",
        "staircase",
        "garage",
    }
)


def signature(
    placements: Sequence[RoomPlacement],
    envelope: BuildableEnvelope,
    *,
    stair_anchor_id: str,
    north_deg: int = 0,
) -> tuple[str, ...]:
    """The §5.5 signature: sorted ``roomType@zone`` tokens plus the stair anchor.

    Sorted so it is order-independent (the same plan described in a different order is
    the same plan) and comparable as a multiset.
    """
    plot_bbox = bbox(envelope.polygon)
    tokens = [
        "%s@%s"
        % (
            placement.room_type,
            zone_for_point(placement.centroid(), plot_bbox, north_deg),
        )
        for placement in placements
        if placement.room_type in SIGNIFICANT_ROOM_TYPES
    ]
    tokens.sort()
    return (*tuple(tokens), "stair:%s" % stair_anchor_id)


def hamming_distance(first: Sequence[str], second: Sequence[str]) -> int:
    """Multiset symmetric difference — how many tokens the two do not share.

    A true multiset difference, not a set one: two plans with three bedrooms in the
    north and one with two must differ by the bedroom that moved.
    """
    counts: dict[str, int] = {}
    for token in first:
        counts[token] = counts.get(token, 0) + 1
    for token in second:
        counts[token] = counts.get(token, 0) - 1
    return sum(abs(value) for value in counts.values())


def is_diverse_enough(
    candidate: Sequence[str],
    kept: Iterable[Sequence[str]],
    *,
    minimum: int = MIN_SIGNATURE_DISTANCE,
) -> bool:
    """True when ``candidate`` differs from every kept signature by >= ``minimum``."""
    return all(hamming_distance(candidate, existing) >= minimum for existing in kept)


def select_diverse(
    options: Sequence[PlanOption],
    *,
    limit: int = 3,
    minimum_distance: int = MIN_SIGNATURE_DISTANCE,
) -> tuple[PlanOption, ...]:
    """Rank by composite, then keep the best options that are meaningfully different.

    Greedy and deterministic: sort best-first (ties broken by signature then id, never
    by input order), then walk the list keeping anything far enough from what is
    already kept. Greedy is the right call — the alternative is a max-diversity subset
    search whose extra quality no architect would notice.

    If fewer than ``limit`` options survive, that is the honest answer and §5.6's
    caller turns it into the "2 strong options found" banner rather than padding with
    a near-duplicate.
    """
    ranked = sorted(
        options,
        key=lambda option: (-option.scores.composite, option.signature, option.id),
    )
    kept: list[PlanOption] = []
    kept_signatures: list[Sequence[str]] = []
    for option in ranked:
        if len(kept) >= limit:
            break
        if is_diverse_enough(option.signature, kept_signatures, minimum=minimum_distance):
            kept.append(option)
            kept_signatures.append(option.signature)
    return tuple(_reranked(option, rank) for rank, option in enumerate(kept))


def _reranked(option: PlanOption, rank: int) -> PlanOption:
    """Stamp the final 0-based rank onto a kept option."""
    if option.rank == rank:
        return option
    return PlanOption(
        id=option.id,
        rank=rank,
        scores=option.scores,
        placements=option.placements,
        ops=option.ops,
        signature=option.signature,
        stair_anchor_id=option.stair_anchor_id,
        built_up_mm2=option.built_up_mm2,
        footprint_mm2=option.footprint_mm2,
        rationale_facts=option.rationale_facts,
        assumptions=option.assumptions,
        compliance=option.compliance,
    )


def signature_summary(signature_tokens: Sequence[str]) -> Mapping[str, str]:
    """``{roomType: zone}`` view of a signature, for the "why this plan" panel."""
    out: dict[str, str] = {}
    for token in signature_tokens:
        if token.startswith("stair:"):
            out["stair"] = token.split(":", 1)[1]
            continue
        room_type, _, zone = token.partition("@")
        out.setdefault(room_type, zone)
    return out


__all__ = [
    "MIN_SIGNATURE_DISTANCE",
    "SIGNIFICANT_ROOM_TYPES",
    "hamming_distance",
    "is_diverse_enough",
    "select_diverse",
    "signature",
    "signature_summary",
]
