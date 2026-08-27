"""Tests for the §7 auto-dimensioning engine.

Run either way:

    pytest -q services/drawings/tests/test_autodim.py
    python3 services/drawings/tests/test_autodim.py            # no pytest needed
    python3 services/drawings/tests/test_autodim.py --regen     # rewrite the goldens

The second form matters on a machine with no dev dependencies installed (this repo's
``DECISIONS.md`` toolchain-gap row): the engine is pure integer arithmetic precisely so
that it can be executed and proven anywhere, and a test suite that can only run under a
full toolchain would waste that property. The ``__main__`` path discovers every
``test_*`` function in this module, runs it, and then prints the §7 dimension report —
chains per level, and how many labels needed a flip, a shift, a shrink or a leader.

What is asserted, and why each one is load-bearing:

* **Chain sums** (§7 step 5) on every fixture, in both centreline and jamb modes. A
  chain whose parts do not add up to its whole is the defect that gets a building built
  wrong; it is checked structurally *and* asserted here.
* **Segment contiguity.** Sums can be right while segments overlap or leave gaps, which
  would draw a chain that does not match its own numbers.
* **No overlapping label boxes** (§7 step 4's "never overlap") on every fixture, with the
  room-label obstacles the plan projector will really place.
* **Duplicate inner-chain suppression** (§7 step 3), including the *negative* case: two
  rooms with the same width that do not share a wall keep both chains.
* **Centreline vs jamb** (§7 step 6), including that jamb mode prints the opening's exact
  width.
* **Determinism** — two runs, and two independently folded builds of the same op log,
  produce byte-identical JSON. §16 diffs goldens with tolerance 0.
* **Non-orthogonal walls are skipped and reported**, never approximated.
* **The escalation ladder** — synthetic obstacles that force flip, then shift, then
  shrink, then leader, proving each rung exists and is used in the order §7 states.
* **Goldens** for the whole primitive stream, byte-compared.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from collections.abc import Callable, Sequence
from typing import Any

# -- bootstrap --------------------------------------------------------------
# Self-contained on purpose: this module is both a pytest module and a script, and
# ``services.common`` pulls structlog/pydantic at import time through the drawings
# package. Same pattern as services/solver/tests/conftest.py and scripts/solver_smoke.py.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
for _path in (_REPO_ROOT, os.path.join(_REPO_ROOT, "apps", "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from services.dev_stubs import install_worker_dep_stubs  # noqa: E402

STUBBED = install_worker_dep_stubs()

from services.drawings.autodim import testing as fixtures  # noqa: E402
from services.drawings.autodim.chains import chain_from_breakpoints, merge_breakpoints  # noqa: E402
from services.drawings.autodim.config import AutoDimConfig  # noqa: E402
from services.drawings.autodim.engine import (  # noqa: E402
    LabelOverlapError,
    assert_no_label_overlaps,
    dimension_storey,
)
from services.drawings.autodim.extract import (  # noqa: E402
    HORIZONTAL,
    SIDE_NORTH,
    SKIP_NON_ORTHOGONAL,
    build_storey_plan,
    facade_runs,
)
from services.drawings.autodim.inner import SUPPRESSED_DUPLICATE  # noqa: E402
from services.drawings.autodim.placement import (  # noqa: E402
    STRATEGY_FLIP,
    STRATEGY_LEADER,
    STRATEGY_SHIFT,
    STRATEGY_SHRINK,
    CollisionGrid,
    place_labels,
)
from services.drawings.autodim.primitives import (  # noqa: E402
    DIM_KINDS,
    KIND_TEXT,
    Line,
    Text,
    validate_primitives,
)
from services.drawings.autodim.svg_debug import render_svg  # noqa: E402
from services.drawings.dimensions import (  # noqa: E402
    ChainConsistencyError,
    DimChain,
    DimSegment,
    LabelBox,
    assert_chains_sum,
    find_label_collisions,
)
from services.drawings.layers import A_DIM  # noqa: E402

GOLDEN_DIR = os.path.join(_REPO_ROOT, "services", "drawings", "autodim", "goldens")

CENTRELINE = AutoDimConfig()
JAMB = AutoDimConfig(dim_to_jamb=True)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _plans() -> tuple[tuple[str, Any, str], ...]:
    return fixtures.all_plans()


def _results(config: AutoDimConfig = CENTRELINE) -> tuple[tuple[str, Any], ...]:
    """Every fixture, dimensioned with the room-label obstacles the projector places."""
    out = []
    for name, plan, storey_id in _plans():
        obstacles = fixtures.room_label_obstacles(plan, storey_id)
        out.append((name, dimension_storey(plan, storey_id, config=config, obstacles=obstacles)))
    return tuple(out)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# ---------------------------------------------------------------------------
# §7 step 5 — the sum invariant
# ---------------------------------------------------------------------------
def test_every_chain_sums_exactly() -> None:
    """Σ segments == overall, every chain, every fixture, both dim modes."""
    for config, mode in ((CENTRELINE, "centreline"), (JAMB, "jamb")):
        for name, result in _results(config):
            _assert(result.chains, "%s/%s produced no chains at all" % (name, mode))
            # The engine already asserted this; assert it again from the outside, over
            # the shared-contract objects the DXF writer will consume.
            assert_chains_sum(result.dim_chains)
            for info in result.chains:
                chain = info.chain
                total = sum(segment.length_mm for segment in chain.segments)
                _assert(
                    total == chain.overall_mm,
                    "%s/%s chain %s: segments sum to %d, overall is %d"
                    % (name, mode, chain.id, total, chain.overall_mm),
                )


def test_segments_are_contiguous_and_positive() -> None:
    """No gaps, no overlaps, no zero-length segments — a chain you could draw."""
    for name, result in _results():
        for info in result.chains:
            segments = info.chain.segments
            _assert(segments, "%s: chain %s has no segments" % (name, info.id))
            _assert(
                segments[0].start_mm == 0,
                "%s: chain %s starts at %d, not 0" % (name, info.id, segments[0].start_mm),
            )
            for index, segment in enumerate(segments):
                _assert(
                    segment.length_mm > 0,
                    "%s: chain %s segment %d has length %d"
                    % (name, info.id, index, segment.length_mm),
                )
                if index:
                    previous = segments[index - 1]
                    _assert(
                        previous.end_mm == segment.start_mm,
                        "%s: chain %s segment %d starts at %d but %d ended at %d"
                        % (
                            name,
                            info.id,
                            index,
                            segment.start_mm,
                            index - 1,
                            previous.end_mm,
                        ),
                    )
            _assert(
                segments[-1].end_mm == info.chain.overall_mm,
                "%s: chain %s last segment ends at %d, overall is %d"
                % (name, info.id, segments[-1].end_mm, info.chain.overall_mm),
            )


def test_sum_assertion_actually_fires() -> None:
    """Guard the guard: a hand-built inconsistent chain must be rejected."""
    broken = DimChain(
        id="dim.broken",
        orientation=HORIZONTAL,
        level=1,
        offset_mm=2400,
        origin_mm=0,
        segments=(DimSegment(0, 1000), DimSegment(1000, 1000)),
        overall_mm=2001,
    )
    try:
        assert_chains_sum([broken])
    except ChainConsistencyError:
        return
    raise AssertionError("assert_chains_sum accepted a chain that does not add up")


def test_breakpoint_construction_cannot_break_the_sum() -> None:
    """The structural guarantee, exercised on adversarial breakpoints."""
    for points in (
        (0, 1),
        (-5000, -1, 0, 7, 999_999),
        (1085, 2700, 6100, 8065),
        (0, 3, 5, 8, 13, 21, 34),
    ):
        info = chain_from_breakpoints(
            chain_id="dim.probe",
            orientation=HORIZONTAL,
            level=2,
            offset_mm=1800,
            breakpoints=points,
            line_mm=0,
            reference_mm=0,
            outward=-1,
            kind="outer",
        )
        _assert(info is not None, "breakpoints %r produced no chain" % (points,))
        chain = info.chain
        _assert(
            chain.sum_of_segments() == chain.overall_mm == points[-1] - points[0],
            "breakpoints %r: %d vs %d" % (points, chain.sum_of_segments(), chain.overall_mm),
        )
    _assert(
        chain_from_breakpoints(
            chain_id="dim.empty",
            orientation=HORIZONTAL,
            level=1,
            offset_mm=0,
            breakpoints=(42,),
            line_mm=0,
            reference_mm=0,
            outward=1,
            kind="outer",
        )
        is None,
        "a single breakpoint is not a chain",
    )


def test_merge_breakpoints_protects_the_ends() -> None:
    """Collapsing near-coincident breakpoints must never move the chain's ends."""
    merged = merge_breakpoints((0, 5, 10, 3000, 3010, 6000), keep=(0, 6000))
    _assert(merged[0] == 0 and merged[-1] == 6000, "ends moved: %r" % (merged,))
    _assert(merged == (0, 3000, 6000), "unexpected merge result %r" % (merged,))
    # A protected end inside a cluster wins the cluster.
    _assert(
        merge_breakpoints((0, 20, 40), keep=(0, 40)) == (0, 40),
        "protected end lost its cluster",
    )


# ---------------------------------------------------------------------------
# §7 step 2 — outer chains
# ---------------------------------------------------------------------------
def test_outer_chains_cover_four_sides_at_spec_offsets() -> None:
    """Level 1 on all four sides, at 2400/1800/1200 from the building line."""
    expected = {1: 2400, 2: 1800, 3: 1200}
    for name, result in _results():
        level_1 = result.chains_at_level(1)
        _assert(
            len(level_1) == 4,
            "%s: expected 4 overall chains (one per side), got %d" % (name, len(level_1)),
        )
        _assert(
            sorted(info.side for info in level_1) == ["E", "N", "S", "W"],
            "%s: level 1 sides are %r" % (name, [i.side for i in level_1]),
        )
        for info in result.chains:
            if info.kind != "outer":
                continue
            _assert(
                info.chain.offset_mm == expected[info.level],
                "%s: %s is at offset %d, §7 says %d"
                % (name, info.id, info.chain.offset_mm, expected[info.level]),
            )
            _assert(
                info.line_mm == info.reference_mm + info.outward * info.chain.offset_mm,
                "%s: %s line %d does not match reference %d + %d*%d"
                % (
                    name,
                    info.id,
                    info.line_mm,
                    info.reference_mm,
                    info.outward,
                    info.chain.offset_mm,
                ),
            )
        # Opposite sides measure the same overall extent.
        by_side = {info.side: info.chain.overall_mm for info in level_1}
        _assert(
            by_side["S"] == by_side["N"] and by_side["E"] == by_side["W"],
            "%s: opposite sides disagree on the overall extent: %r" % (name, by_side),
        )


def test_level_1_is_outer_face_to_outer_face() -> None:
    """The overall dimension is the envelope's outer faces (F7-A: unfinished faces)."""
    house = fixtures.demo_3bhk_ground()
    storey_id = fixtures.storey_id_of(house)
    result = dimension_storey(house, storey_id)
    # 6750 centreline x 7650 centreline footprint, 230mm external walls: +115 each side.
    widths = {info.side: info.chain.overall_mm for info in result.chains_at_level(1)}
    _assert(widths["S"] == 6750 + 230, "south overall is %d, expected 6980" % widths["S"])
    _assert(widths["W"] == 7650 + 230, "west overall is %d, expected 7880" % widths["W"])


def test_level_2_breaks_at_cross_walls_not_at_corners() -> None:
    """The wall grid: partitions appear, the corner walls do not."""
    house = fixtures.demo_3bhk_ground()
    storey_id = fixtures.storey_id_of(house)
    result = dimension_storey(house, storey_id)
    south = [i for i in result.chains_at_level(2) if i.side == "S"]
    _assert(len(south) == 1, "expected one south level-2 chain, got %d" % len(south))
    breaks = [south[0].chain.origin_mm + segment.end_mm for segment in south[0].chain.segments[:-1]]
    _assert(breaks == [4300], "south level-2 breakpoints are %r, expected [4300]" % breaks)

    north = [i for i in result.chains_at_level(2) if i.side == "N"]
    breaks_n = [
        north[0].chain.origin_mm + segment.end_mm for segment in north[0].chain.segments[:-1]
    ]
    _assert(
        breaks_n == [4300, 6100],
        "north level-2 breakpoints are %r, expected [4300, 6100]" % breaks_n,
    )


def test_level_2_suppressed_when_it_would_duplicate_level_1() -> None:
    """A facade with no cross wall gets no level 2 chain — a duplicate is noise.

    The two-room fixture's east and west facades have no horizontal partition meeting
    them, so only the south and north sides carry a wall-grid chain.
    """
    house = fixtures.two_room_plan()
    result = dimension_storey(house, fixtures.storey_id_of(house))
    sides = sorted(info.side for info in result.chains_at_level(2))
    _assert(sides == ["N", "S"], "level-2 sides are %r, expected [N, S]" % sides)


def test_facade_occlusion_puts_recessed_openings_on_the_right_side() -> None:
    """An L footprint: the recessed leg is still north-facing, and its window says so."""
    house = fixtures.l_shaped_plan()
    storey_id = fixtures.storey_id_of(house)
    plan = build_storey_plan(house, storey_id)

    north_runs = facade_runs(plan.walls, SIDE_NORTH)
    _assert(
        len(north_runs) == 2,
        "the L's north side has 2 visible runs, got %d" % len(north_runs),
    )
    _assert(
        [run.axis_mm for run in north_runs] == [10650, 8500],
        "north runs are at %r, expected [10650, 8500]" % [run.axis_mm for run in north_runs],
    )

    result = dimension_storey(house, storey_id)
    north_l3 = [i for i in result.chains_at_level(3) if i.side == "N"]
    _assert(len(north_l3) == 1, "expected one north level-3 chain")
    centres = [
        north_l3[0].chain.origin_mm + segment.end_mm for segment in north_l3[0].chain.segments[:-1]
    ]
    _assert(
        centres == [3000, 6700],
        "north opening centres are %r, expected [3000, 6700] (the recessed leg's window "
        "at 6700 must not be lost)" % centres,
    )


def test_level_2_breaks_at_a_jog() -> None:
    """The L's north wall grid breaks where the plan steps back."""
    house = fixtures.l_shaped_plan()
    result = dimension_storey(house, fixtures.storey_id_of(house))
    north = [i for i in result.chains_at_level(2) if i.side == "N"]
    _assert(len(north) == 1, "expected one north level-2 chain")
    breaks = [north[0].chain.origin_mm + segment.end_mm for segment in north[0].chain.segments[:-1]]
    _assert(breaks == [5500], "north jog breakpoint is %r, expected [5500]" % breaks)


# ---------------------------------------------------------------------------
# §7 step 3 — inner dims
# ---------------------------------------------------------------------------
def test_inner_chains_one_width_and_one_depth_per_room() -> None:
    """Every room gets at most one width and one depth chain, and never a third."""
    for name, result in _results():
        seen: dict[tuple[str, str], int] = {}
        for info in result.chains:
            if info.kind != "inner":
                continue
            axis = "W" if info.orientation == HORIZONTAL else "D"
            key = (str(info.room_id), axis)
            seen[key] = seen.get(key, 0) + 1
        for key, count in seen.items():
            _assert(count == 1, "%s: room %s has %d %s chains" % (name, key[0], count, key[1]))


def test_inner_chain_measures_the_clear_room() -> None:
    """A room's width chain equals its clear bbox width, to the millimetre."""
    house = fixtures.demo_3bhk_ground()
    storey_id = fixtures.storey_id_of(house)
    plan = build_storey_plan(house, storey_id)
    rooms = {room.id: room for room in plan.rooms}
    result = dimension_storey(house, storey_id)
    for info in result.chains:
        if info.kind != "inner":
            continue
        room = rooms[str(info.room_id)]
        expected = room.width_mm if info.orientation == HORIZONTAL else room.depth_mm
        _assert(
            info.chain.overall_mm == expected,
            "room %s %s chain is %d, clear size is %d"
            % (room.id, info.orientation, info.chain.overall_mm, expected),
        )


def test_duplicate_inner_chains_suppressed_across_shared_walls() -> None:
    """§7 step 3's skip rule: same value, shared wall → one chain, and it is reported."""
    house = fixtures.demo_3bhk_ground()
    storey_id = fixtures.storey_id_of(house)
    plan = build_storey_plan(house, storey_id)
    rooms = {room.id: room for room in plan.rooms}
    result = dimension_storey(house, storey_id)

    _assert(result.suppressed_chains, "nothing was suppressed on a 7-room plan")
    kept = {info.id: info for info in result.chains}
    for item in result.suppressed_chains:
        _assert(
            item.reason == SUPPRESSED_DUPLICATE,
            "unexpected suppression reason %r" % item.reason,
        )
        _assert(item.chain_id not in kept, "%s was suppressed and kept" % item.chain_id)
        survivor = kept.get(str(item.duplicate_of))
        _assert(
            survivor is not None,
            "%s was suppressed in favour of %s, which is not in the output"
            % (item.chain_id, item.duplicate_of),
        )
        _assert(
            survivor.chain.overall_mm == item.value_mm,
            "%s (%d) suppressed in favour of a chain measuring %d"
            % (item.chain_id, item.value_mm, survivor.chain.overall_mm),
        )
        # And the two rooms really do touch: gap no wider than one wall.
        room_a, room_b = rooms[str(survivor.room_id)], rooms[item.room_id]
        if survivor.orientation == HORIZONTAL:
            gap = max(room_a.min_y_mm, room_b.min_y_mm) - min(room_a.max_y_mm, room_b.max_y_mm)
        else:
            gap = max(room_a.min_x_mm, room_b.min_x_mm) - min(room_a.max_x_mm, room_b.max_x_mm)
        _assert(
            0 <= gap <= 232,
            "%s suppressed against a room %dmm away — that is not a shared wall"
            % (item.chain_id, gap),
        )


def test_equal_but_non_adjacent_inner_chains_are_both_kept() -> None:
    """The negative case. Two same-width rooms that do not touch keep both chains.

    Living, kitchen and dining are all 2928mm wide in the demo plan. Living and kitchen
    share a wall, so one chain goes; dining is two rooms away and keeps its own — an
    architect wants the number *near the room*, and suppressing it would leave the
    dining room undimensioned.
    """
    house = fixtures.demo_3bhk_ground()
    storey_id = fixtures.storey_id_of(house)
    result = dimension_storey(house, storey_id)
    widths = [
        info
        for info in result.chains
        if info.kind == "inner" and info.orientation == HORIZONTAL and info.chain.overall_mm == 2928
    ]
    _assert(
        len(widths) == 2,
        "expected 2 surviving 2928mm width chains (living + dining), got %d" % len(widths),
    )
    lines = sorted(info.line_mm for info in widths)
    _assert(
        lines[1] - lines[0] > 2000,
        "the two surviving chains are %dmm apart — they should be in different rooms"
        % (lines[1] - lines[0]),
    )


def test_inner_chain_hugs_the_door_side_wall() -> None:
    """The width chain sits off the wall the room's door is in."""
    house = fixtures.demo_3bhk_ground()
    storey_id = fixtures.storey_id_of(house)
    plan = build_storey_plan(house, storey_id)
    result = dimension_storey(house, storey_id)
    rooms = {room.id: room for room in plan.rooms}
    for info in result.chains:
        if info.kind != "inner":
            continue
        room = rooms[str(info.room_id)]
        faces = (
            (room.min_y_mm, room.max_y_mm)
            if info.orientation == HORIZONTAL
            else (room.min_x_mm, room.max_x_mm)
        )
        _assert(
            info.reference_mm in faces,
            "room %s chain references %d, which is neither face (%r)"
            % (room.id, info.reference_mm, faces),
        )
        _assert(
            min(faces) < info.line_mm < max(faces),
            "room %s chain line %d is outside the room (%r)" % (room.id, info.line_mm, faces),
        )


# ---------------------------------------------------------------------------
# §7 step 4 — placement
# ---------------------------------------------------------------------------
def test_no_two_label_boxes_overlap() -> None:
    """§16: "collision-free assertion (no overlapping text bboxes)"."""
    for config in (CENTRELINE, JAMB):
        for name, result in _results(config):
            collisions = find_label_collisions(result.label_boxes())
            _assert(
                not collisions,
                "%s: %d overlapping label(s): %r" % (name, len(collisions), collisions[:3]),
            )
            assert_no_label_overlaps(result.labels)


def test_labels_avoid_the_obstacles_they_were_given() -> None:
    """Room-name blocks are obstacles, not suggestions."""
    house = fixtures.demo_3bhk_ground()
    storey_id = fixtures.storey_id_of(house)
    obstacles = fixtures.room_label_obstacles(house, storey_id)
    _assert(len(obstacles) == 7, "expected 7 room labels, got %d" % len(obstacles))
    result = dimension_storey(house, storey_id, obstacles=obstacles)
    for label in result.labels:
        for obstacle in obstacles:
            _assert(
                not label.box.overlaps(obstacle),
                "label %s overlaps %s" % (label.id, obstacle.owner_id),
            )


def test_overlap_assertion_actually_fires() -> None:
    """Guard the guard, again: a known-overlapping pair must be rejected."""
    from services.drawings.autodim.placement import PlacedLabel

    def label(index: int) -> Any:
        return PlacedLabel(
            id="probe#%d" % index,
            chain_id="probe",
            segment_index=index,
            text="1000",
            box=LabelBox(0, 0, 500, 300, "probe#%d" % index),
            anchor=(250, 150),
            height_mm=250,
            rotation_deg=0,
            strategy="base",
            flipped=False,
            shift_mm=0,
            shrink_step=0,
        )

    try:
        assert_no_label_overlaps([label(0), label(1)])
    except LabelOverlapError:
        return
    raise AssertionError("assert_no_label_overlaps accepted two identical boxes")


def _synthetic_chain(overall_mm: int = 4000) -> Any:
    """One horizontal chain, one segment, at the origin — a placement test bench."""
    info = chain_from_breakpoints(
        chain_id="dim.bench",
        orientation=HORIZONTAL,
        level=1,
        offset_mm=2400,
        breakpoints=(0, overall_mm),
        line_mm=0,
        reference_mm=2400,
        outward=-1,
        kind="outer",
    )
    assert info is not None
    return info


def _band(x0: int, x1: int, *, y0: int = -500, y1: int = 500, tag: str = "wall") -> LabelBox:
    return LabelBox(x_mm=x0, y_mm=y0, width_mm=x1 - x0, height_mm=y1 - y0, owner_id=tag)


def test_placement_escalates_flip_then_shift_then_shrink_then_leader() -> None:
    """Each rung of §7 step 4's ladder exists, and the cheaper ones are preferred.

    Text is "4000": 700mm wide at 2.5mm/1:100, 504mm at 1.8mm, 392mm at 1.4mm, each
    padded by 50mm on every side. The obstacles below leave exactly enough room for one
    rung at a time.
    """
    chain = _synthetic_chain()
    config = AutoDimConfig()

    # 1. The preferred side blocked, the other free → flip. A horizontal chain's text
    #    sits *above* the line by default (DIMSTYLE ``dimtad = 1``), so block above it.
    labels, _ = place_labels([chain], config=config, obstacles=[_band(0, 4000, y0=20, y1=500)])
    _assert(
        labels[0].strategy == STRATEGY_FLIP and labels[0].flipped,
        "expected a flip, got %r" % labels[0].strategy,
    )

    # 2. Both sides blocked at the midpoint, free 1200mm to the right → shift.
    labels, _ = place_labels([chain], config=config, obstacles=[_band(0, 2600)])
    _assert(
        labels[0].strategy == STRATEGY_SHIFT and labels[0].shift_mm != 0,
        "expected a shift, got %r (shift %d)" % (labels[0].strategy, labels[0].shift_mm),
    )

    # 3. A 700mm window: too narrow for 800mm of padded full-size text, wide enough for
    #    the 604mm of one step down → shrink.
    labels, _ = place_labels(
        [chain],
        config=config,
        obstacles=[_band(-20_000, 1700), _band(2400, 20_000)],
    )
    _assert(
        labels[0].strategy == STRATEGY_SHRINK and labels[0].shrink_step == 1,
        "expected a shrink, got %r (step %d)" % (labels[0].strategy, labels[0].shrink_step),
    )
    _assert(
        labels[0].height_mm == config.text_height_mm(1),
        "shrunk label is %dmm tall, expected %d" % (labels[0].height_mm, config.text_height_mm(1)),
    )

    # 4. A 400mm window: too narrow for every text size → leader.
    labels, _ = place_labels(
        [chain],
        config=config,
        obstacles=[_band(-20_000, 1800), _band(2200, 20_000)],
    )
    label = labels[0]
    _assert(
        label.strategy == STRATEGY_LEADER and label.has_leader,
        "expected a leader, got %r" % label.strategy,
    )
    _assert(
        label.leader_from is not None and label.leader_from[1] == chain.line_mm,
        "the leader must start on the dimension line",
    )
    _assert(
        abs(label.box.y_mm) > 500,
        "the leader's text is still inside the blocked band (y=%d)" % label.box.y_mm,
    )


def test_outer_labels_never_land_on_the_plan() -> None:
    """No dimension text sits inside the building. An architect would move it, and
    moving it is exactly what the ">=90% accepted unedited" gate counts against us.

    This is a property of the offsets and the text side, not of the collision pass: the
    innermost chain is 1200mm out and its text is at most 350mm tall, so the worst case
    still clears the wall by 850mm at 1:100.
    """
    for name, result in _results():
        plan = result.plan
        _assert(plan is not None and plan.extents is not None, "%s has no extents" % name)
        extents = plan.extents
        footprint = LabelBox(
            x_mm=extents.min_x_mm,
            y_mm=extents.min_y_mm,
            width_mm=extents.width_mm,
            height_mm=extents.depth_mm,
            owner_id="footprint",
        )
        outer_ids = {info.id for info in result.chains if info.kind == "outer"}
        for label in result.labels:
            if label.chain_id not in outer_ids:
                continue
            _assert(
                not label.box.overlaps(footprint),
                "%s: outer label %s (%r) is drawn over the plan" % (name, label.id, label.text),
            )


def test_dimension_model_covers_every_storey_including_empty_ones() -> None:
    """A terrace storey with no walls must produce a note, not an exception."""
    from services.drawings.autodim.engine import dimension_model

    plan = dict(fixtures.json_plan_with_diagonal())
    plan["storeys"] = [
        *list(plan["storeys"]),
        {"id": "storey_TERRACE", "name": "Terrace", "heightMm": 2400},
    ]
    results = dimension_model(plan)
    _assert(len(results) == 2, "expected one result per storey, got %d" % len(results))
    terrace = results[1]
    _assert(terrace.storey_id == "storey_TERRACE", "wrong storey order")
    _assert(not terrace.chains, "an empty storey produced %d chains" % len(terrace.chains))
    _assert(
        any("no envelope walls" in note for note in terrace.notes),
        "the empty storey has no explanatory note: %r" % (terrace.notes,),
    )


def test_collision_grid_agrees_with_brute_force() -> None:
    """The grid is an index, not a second opinion: same answers as O(n²) overlap."""
    grid = CollisionGrid(250)
    boxes: list[LabelBox] = []
    # A deterministic pseudo-random spread, negative coordinates included.
    value = 12345
    for index in range(120):
        value = (value * 1103515245 + 12345) % 2147483648
        x = (value % 9000) - 3000
        value = (value * 1103515245 + 12345) % 2147483648
        y = (value % 9000) - 3000
        box = LabelBox(x, y, 700, 350, "b%d" % index)
        expected = any(box.overlaps(other) for other in boxes)
        _assert(
            grid.collides(box) == expected,
            "grid disagreed with brute force on box %d at (%d,%d)" % (index, x, y),
        )
        if not expected:
            grid.add(box)
            boxes.append(box)
    _assert(len(grid) == len(boxes), "grid holds %d boxes, expected %d" % (len(grid), len(boxes)))


def test_outer_chains_are_placed_before_inner_ones() -> None:
    """Greedy order = importance order: the overall dimension never yields to a room."""
    house = fixtures.demo_3bhk_ground()
    storey_id = fixtures.storey_id_of(house)
    result = dimension_storey(
        house, storey_id, obstacles=fixtures.room_label_obstacles(house, storey_id)
    )
    levels = [
        next(info.level for info in result.chains if info.id == label.chain_id)
        for label in result.labels
    ]
    _assert(levels == sorted(levels), "labels were not placed in level order: %r" % levels)
    for level in (1, 2, 3):
        for label in result.labels:
            info = next(i for i in result.chains if i.id == label.chain_id)
            if info.level == level:
                _assert(
                    not label.has_leader,
                    "outer chain label %s needed a leader — that should be rare enough "
                    "to investigate, not routine" % label.id,
                )


# ---------------------------------------------------------------------------
# §7 step 6 — mm text, centreline vs jamb
# ---------------------------------------------------------------------------
def test_all_dim_text_is_plain_millimetres() -> None:
    """§7 step 6: mm on the drawing regardless of display units. No units, no commas."""
    for name, result in _results():
        for info in result.chains:
            for segment in info.chain.segments:
                text = segment.label()
                _assert(
                    text.isdigit(),
                    "%s: label %r is not a plain millimetre integer" % (name, text),
                )
                _assert(
                    int(text) == segment.length_mm,
                    "%s: label %r does not match its %dmm segment"
                    % (name, text, segment.length_mm),
                )
        for label in result.labels:
            _assert(label.text.isdigit(), "%s: placed label %r is not digits" % (name, label.text))


def test_openings_dimension_to_centreline_by_default() -> None:
    """Default mode puts one breakpoint at each opening centre."""
    house = fixtures.demo_3bhk_ground()
    storey_id = fixtures.storey_id_of(house)
    result = dimension_storey(house, storey_id, config=CENTRELINE)
    south = [i for i in result.chains_at_level(3) if i.side == "S"]
    _assert(len(south) == 1, "expected one south level-3 chain")
    breaks = [south[0].chain.origin_mm + segment.end_mm for segment in south[0].chain.segments[:-1]]
    _assert(
        breaks == [2700, 6100],
        "south opening centrelines are %r, expected [2700, 6100]" % breaks,
    )


def test_dim_to_jamb_flag_switches_to_jambs_and_prints_exact_widths() -> None:
    """``dimToJamb`` gives alternating pier / opening segments, widths exact."""
    house = fixtures.demo_3bhk_ground()
    storey_id = fixtures.storey_id_of(house)
    result = dimension_storey(house, storey_id, config=JAMB)
    south = [i for i in result.chains_at_level(3) if i.side == "S"]
    _assert(len(south) == 1, "expected one south level-3 chain")
    chain = south[0].chain
    breaks = [chain.origin_mm + segment.end_mm for segment in chain.segments[:-1]]
    # D1: 900 wide centred on 2700 → 2250..3150. W1: 1500 centred on 6100 → 5350..6850.
    _assert(
        breaks == [2250, 3150, 5350, 6850],
        "jamb breakpoints are %r, expected [2250, 3150, 5350, 6850]" % breaks,
    )
    widths = [segment.length_mm for segment in chain.segments]
    _assert(
        900 in widths and 1500 in widths,
        "jamb chain %r does not print the openings' exact widths (900, 1500)" % widths,
    )
    _assert(
        chain.sum_of_segments() == chain.overall_mm,
        "jamb chain does not sum",
    )


# ---------------------------------------------------------------------------
# step 1 — orthogonal only, honestly
# ---------------------------------------------------------------------------
def test_non_orthogonal_walls_are_skipped_and_reported() -> None:
    """A diagonal wall is never approximated onto an axis; it is skipped, with a note."""
    plan_json = fixtures.json_plan_with_diagonal()
    result = dimension_storey(plan_json, "storey_JSON")
    skipped = [s for s in result.skipped_walls if s.reason == SKIP_NON_ORTHOGONAL]
    _assert(len(skipped) == 1, "expected 1 skipped wall, got %r" % (result.skipped_walls,))
    _assert(skipped[0].id == "wall_JDIAG", "skipped the wrong wall: %r" % skipped[0].id)
    _assert(
        any("non-orthogonal" in note for note in result.notes),
        "the skip is not in the sheet notes: %r" % (result.notes,),
    )
    for info in result.chains:
        for segment in info.chain.segments:
            _assert(
                segment.anchor_element_id != "wall_JDIAG",
                "a chain segment is anchored to the diagonal wall",
            )


def test_wire_json_and_dataclass_models_agree() -> None:
    """The adapter is shape-agnostic: attributes or camelCase keys, same chains."""
    house = fixtures.two_room_plan()
    storey_id = fixtures.storey_id_of(house)
    from_dataclass = dimension_storey(house, storey_id)

    def to_wire(model: Any) -> dict[str, Any]:
        return {
            "storeys": [
                {"id": s.id, "name": s.name, "heightMm": s.height_mm} for s in model.storeys
            ],
            "walls": [
                {
                    "id": w.id,
                    "storeyId": w.storey_id,
                    "a": {"x": w.a.x, "y": w.a.y},
                    "b": {"x": w.b.x, "y": w.b.y},
                    "thicknessMm": w.thickness_mm,
                    "kind": w.kind,
                }
                for w in model.walls
            ],
            "openings": [
                {
                    "id": o.id,
                    "wallId": o.wall_id,
                    "kind": o.kind,
                    "widthMm": o.width_mm,
                    "heightMm": o.height_mm,
                    "sillMm": o.sill_mm,
                    "offsetMm": o.offset_mm,
                }
                for o in model.openings
            ],
            "rooms": [
                {
                    "id": r.id,
                    "storeyId": r.storey_id,
                    "type": r.type,
                    "name": r.name,
                    "polygon": [{"x": p.x, "y": p.y} for p in r.polygon],
                    "areaMm2": r.area_mm2,
                }
                for r in model.rooms
            ],
        }

    from_json = dimension_storey(to_wire(house), storey_id)
    _assert(
        json.dumps(from_dataclass.to_json(), sort_keys=True)
        == json.dumps(from_json.to_json(), sort_keys=True),
        "the dataclass and wire-JSON paths produced different dimensions",
    )


# ---------------------------------------------------------------------------
# determinism & primitives
# ---------------------------------------------------------------------------
def test_two_runs_are_byte_identical() -> None:
    """§16 compares goldens with tolerance 0, so the engine must be reproducible."""
    for name, plan, storey_id in _plans():
        obstacles = fixtures.room_label_obstacles(plan, storey_id)
        first = dimension_storey(plan, storey_id, obstacles=obstacles).to_json()
        second = dimension_storey(plan, storey_id, obstacles=obstacles).to_json()
        _assert(
            json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True),
            "%s: two runs of the same input disagreed" % name,
        )


def test_two_independent_builds_of_the_same_op_log_agree() -> None:
    """Rebuild the plan from its op log and dimension it again: same output.

    This catches state that leaks between calls (a module-level grid, a mutable default,
    a cached id counter) — the class of bug that makes goldens flap in CI and pass
    locally.
    """
    first_house = fixtures.demo_3bhk_ground()
    second_house = fixtures.demo_3bhk_ground()
    first = dimension_storey(first_house, fixtures.storey_id_of(first_house)).to_json()
    second = dimension_storey(second_house, fixtures.storey_id_of(second_house)).to_json()
    _assert(
        json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True),
        "two independent folds of the same op log produced different dimensions",
    )


def test_primitives_are_shared_contract_lines_and_text_on_a_dim() -> None:
    """The stream is the projection module's own ``Line``/``Text``, all on ``A-DIM``.

    A dimension must be indistinguishable from a wall to the SVG and DXF writers — that
    is the point of §7's "narrow waist". This test is what stops the engine growing a
    private primitive dialect, and it borrows the projection module's own validator so
    the two cannot drift on what "valid" means.
    """
    for name, result in _results():
        _assert(result.primitives, "%s produced no primitives" % name)
        validate_primitives(result.primitives)  # their invariants, not a copy of them
        text_count = 0
        for primitive in result.primitives:
            _assert(
                isinstance(primitive, Line | Text),
                "%s: unexpected primitive %r" % (name, type(primitive)),
            )
            _assert(
                primitive.layer == A_DIM,
                "%s: primitive on layer %r, expected %s" % (name, primitive.layer, A_DIM),
            )
            _assert(
                primitive.kind in DIM_KINDS,
                "%s: primitive kind %r is not one of %r" % (name, primitive.kind, DIM_KINDS),
            )
            _assert(primitive.owner_id, "%s: primitive with no owner id" % name)
            if isinstance(primitive, Text):
                text_count += 1
                _assert(
                    primitive.kind == KIND_TEXT,
                    "%s: text tagged %r" % (name, primitive.kind),
                )
                _assert(
                    primitive.rotation_deg in (0, 90),
                    "%s: text rotated %d degrees" % (name, primitive.rotation_deg),
                )
                _assert(primitive.height_mm > 0, "%s: zero-height text" % name)
                _assert(
                    (primitive.h_align, primitive.v_align) == ("center", "middle"),
                    "%s: dim text aligned (%r, %r)" % (name, primitive.h_align, primitive.v_align),
                )
            else:
                for coordinate in primitive.a + primitive.b:
                    _assert(
                        isinstance(coordinate, int),
                        "%s: non-integer coordinate %r" % (name, coordinate),
                    )
        _assert(
            text_count == len(result.labels),
            "%s: %d text primitives for %d labels" % (name, text_count, len(result.labels)),
        )


def test_primitive_digest_is_stable() -> None:
    """The one-line golden: same input, same SHA-256 of the canonical stream."""
    for name, plan, storey_id in _plans():
        obstacles = fixtures.room_label_obstacles(plan, storey_id)
        first = dimension_storey(plan, storey_id, obstacles=obstacles).digest()
        second = dimension_storey(plan, storey_id, obstacles=obstacles).digest()
        _assert(first == second, "%s: digest changed between runs" % name)
        _assert(len(first) == 64, "%s: digest is not a sha256 hex string" % name)


def test_every_chain_has_a_label_for_every_segment() -> None:
    """No segment is drawn without its number."""
    for name, result in _results():
        expected = sum(len(info.chain.segments) for info in result.chains)
        _assert(
            len(result.labels) == expected,
            "%s: %d labels for %d segments" % (name, len(result.labels), expected),
        )


# ---------------------------------------------------------------------------
# goldens
# ---------------------------------------------------------------------------
def _golden_path(name: str, suffix: str = "json") -> str:
    return os.path.join(GOLDEN_DIR, "autodim-%s.%s" % (name, suffix))


def _golden_payload(name: str, plan: Any, storey_id: str) -> dict[str, Any]:
    obstacles = fixtures.room_label_obstacles(plan, storey_id)
    result = dimension_storey(plan, storey_id, obstacles=obstacles)
    return {
        "fixture": name,
        "config": {"scaleDenominator": 100, "dimToJamb": False},
        "obstacles": [
            [box.x_mm, box.y_mm, box.width_mm, box.height_mm, box.owner_id] for box in obstacles
        ],
        "result": result.to_json(),
    }


def _golden_svg(name: str, plan: Any, storey_id: str) -> str:
    obstacles = fixtures.room_label_obstacles(plan, storey_id)
    return render_svg(dimension_storey(plan, storey_id, obstacles=obstacles))


def regenerate_goldens() -> list[str]:
    """Write the goldens. Run via ``--regen``, in the same commit as the change."""
    if not os.path.isdir(GOLDEN_DIR):
        os.makedirs(GOLDEN_DIR)
    written = []
    for name, plan, storey_id in _plans():
        path = _golden_path(name)
        with open(path, "w") as handle:
            json.dump(_golden_payload(name, plan, storey_id), handle, indent=2, sort_keys=True)
            handle.write("\n")
        written.append(path)
        svg_path = _golden_path(name, "svg")
        with open(svg_path, "w") as handle:
            handle.write(_golden_svg(name, plan, storey_id))
        written.append(svg_path)
    return written


def test_goldens_match() -> None:
    """Golden rule 10: golden files gate merges. Tolerance 0, integer mm."""
    for name, plan, storey_id in _plans():
        path = _golden_path(name)
        _assert(
            os.path.exists(path),
            "missing golden %s — regenerate with "
            "`python3 services/drawings/tests/test_autodim.py --regen`" % path,
        )
        with open(path) as handle:
            expected = handle.read()
        actual = json.dumps(_golden_payload(name, plan, storey_id), indent=2, sort_keys=True) + "\n"
        if actual != expected:
            expected_lines = expected.splitlines()
            actual_lines = actual.splitlines()
            for index, (left, right) in enumerate(zip(expected_lines, actual_lines, strict=False)):
                if left != right:
                    raise AssertionError(
                        "golden %s differs at line %d:\n  golden: %s\n  actual: %s\n"
                        "If the change is intended, regenerate the goldens in the same "
                        "commit with --regen and say why." % (path, index + 1, left, right)
                    )
            raise AssertionError(
                "golden %s differs in length: %d vs %d lines"
                % (path, len(expected_lines), len(actual_lines))
            )


def test_svg_goldens_match_and_are_sanitised() -> None:
    """§16's SVG golden, byte-compared — and §13's "no scripts, no foreignObject"."""
    forbidden = ("<script", "foreignObject", "xlink:href", "href=", "<image", "onload")
    for name, plan, storey_id in _plans():
        path = _golden_path(name, "svg")
        _assert(
            os.path.exists(path),
            "missing SVG golden %s — regenerate with --regen" % path,
        )
        actual = _golden_svg(name, plan, storey_id)
        with open(path) as handle:
            expected = handle.read()
        _assert(
            actual == expected,
            "SVG golden %s differs; regenerate with --regen in the same commit" % path,
        )
        for token in forbidden:
            _assert(
                token not in actual,
                "%s: emitted SVG contains %r, which §13 forbids" % (name, token),
            )
        _assert(actual.startswith("<svg "), "%s: SVG does not start with <svg" % name)


# ---------------------------------------------------------------------------
# script mode: run every test, then print the §7 dimension report
# ---------------------------------------------------------------------------
def _report() -> None:
    print("")
    print("§7 auto-dimensioning report — 1:100, dims to opening centrelines")
    print("")
    header = "%-18s %5s %5s %5s %5s %5s   %6s %5s %5s %5s %5s" % (
        "fixture",
        "L1",
        "L2",
        "L3",
        "L4",
        "segs",
        "labels",
        "flip",
        "shift",
        "shrnk",
        "lead",
    )
    print(header)
    print("-" * len(header))
    totals = [0] * 10
    for name, result in _results():
        levels = dict(result.counts_by_level())
        segments = sum(count for _, count in result.segments_by_level())
        stats = result.stats
        print(
            "%-18s %5d %5d %5d %5d %5d   %6d %5d %5d %5d %5d"
            % (
                name,
                levels[1],
                levels[2],
                levels[3],
                levels[4],
                segments,
                stats.labels,
                stats.flipped,
                stats.shifted,
                stats.shrunk,
                stats.leaders,
            )
        )
        for index, value in enumerate(
            (
                levels[1],
                levels[2],
                levels[3],
                levels[4],
                segments,
                stats.labels,
                stats.flipped,
                stats.shifted,
                stats.shrunk,
                stats.leaders,
            )
        ):
            totals[index] += value
    print("-" * len(header))
    print("%-18s %5d %5d %5d %5d %5d   %6d %5d %5d %5d %5d" % ("TOTAL", *totals))
    print("")
    for name, result in _results():
        print("%s — every chain, Σ segments = overall:" % name)
        for info in result.chains:
            parts = " + ".join(str(s.length_mm) for s in info.chain.segments)
            label = info.side if info.side else "room %s" % str(info.room_id)[-8:]
            print(
                "   L%d %-14s %6d = %s  %s"
                % (
                    info.level,
                    label,
                    info.chain.overall_mm,
                    parts,
                    "OK" if info.chain.is_consistent() else "MISMATCH",
                )
            )
        for note in result.notes:
            print("   note: %s" % note)
        print("")


def _main(argv: Sequence[str]) -> int:
    if "--regen" in argv:
        for path in regenerate_goldens():
            print("wrote %s" % os.path.relpath(path, _REPO_ROOT))
        return 0

    tests: list[tuple[str, Callable[[], None]]] = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failures: list[str] = []
    for name, fn in tests:
        try:
            fn()
        except Exception:
            failures.append(name)
            print("FAIL  %s" % name)
            traceback.print_exc()
        else:
            print("ok    %s" % name)

    print("")
    print(
        "%d/%d tests passed%s"
        % (
            len(tests) - len(failures),
            len(tests),
            "" if not failures else "  FAILURES: " + ", ".join(failures),
        )
    )
    if STUBBED:
        print("(worker deps stubbed for this run: %s)" % ", ".join(STUBBED))
    if not failures:
        _report()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
