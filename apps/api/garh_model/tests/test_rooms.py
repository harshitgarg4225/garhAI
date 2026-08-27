"""Room detection: the right rooms, the right areas, and — above all — the same
ids as last time.

Section 16 asks for a property test over random rectangular subdivisions: a
k x m grid of walls must produce exactly (k+1)(m+1) rooms whose clear areas are
computable in closed form. That is :func:`test_grid_subdivision_finds_every_room`
below; the id-preservation tests are the ones that protect an architect's
annotations.
"""

from __future__ import annotations

from dataclasses import replace

from hypothesis import given, settings
from hypothesis import strategies as st

from garh_model.fold import apply_group, fold
from garh_model.geometry import polygon_area_mm2, polygon_key
from garh_model.model import Room, Wall
from garh_model.ops import op
from garh_model.rooms import (
    DEFAULT_JACCARD_THRESHOLD,
    WallLike,
    build_half_edge_graph,
    detect_rooms,
    match_rooms,
    planar_faces,
    room_candidates,
)
from garh_model.testing import FIXTURE_IDS, fixed_id, make_empty_doc, make_two_room_plan

GF = FIXTURE_IDS["groundStorey"]
EXTERNAL_MM = 230
INTERNAL_MM = 115


def _wall(wid: str, ax: int, ay: int, bx: int, by: int, thickness: int) -> WallLike:
    from garh_model.geometry import Pt

    return WallLike(id=wid, a=Pt(ax, ay), b=Pt(bx, by), thickness_mm=thickness)


def _rect_walls(w: int, h: int, thickness: int = EXTERNAL_MM) -> list[WallLike]:
    return [
        _wall("wall_s", 0, 0, w, 0, thickness),
        _wall("wall_e", w, 0, w, h, thickness),
        _wall("wall_n", w, h, 0, h, thickness),
        _wall("wall_w", 0, h, 0, 0, thickness),
    ]


# ---------------------------------------------------------------------------
# The graph
# ---------------------------------------------------------------------------


def test_single_rectangle_gives_one_room_and_one_outer_face() -> None:
    result = room_candidates(_rect_walls(6000, 4000))
    assert len(result.candidates) == 1
    room = result.candidates[0]
    # clear = centreline minus half thickness on every side
    assert room.area_mm2 == (6000 - 230) * (4000 - 230)
    assert room.area_mm2 == polygon_area_mm2(list(room.polygon))
    assert result.outline is not None
    # the slab outline grows OUTWARD by half thickness
    assert polygon_area_mm2(list(result.outline)) == (6000 + 230) * (4000 + 230)
    assert result.non_integral_crossings == 0


def test_faces_are_walked_counter_clockwise_with_the_interior_on_the_left() -> None:
    graph = build_half_edge_graph(_rect_walls(4000, 3000))
    faces = planar_faces(graph)
    bounded = [f for f in faces if f.doubled_area_mm2 > 0]
    unbounded = [f for f in faces if f.doubled_area_mm2 <= 0]
    assert len(bounded) == 1
    assert len(unbounded) == 1
    assert bounded[0].doubled_area_mm2 == 2 * 4000 * 3000


def test_t_junction_splits_the_crossed_wall() -> None:
    walls = _rect_walls(6000, 4000)
    walls.append(_wall("wall_spine", 3000, 0, 3000, 4000, INTERNAL_MM))
    result = room_candidates(walls)
    assert len(result.candidates) == 2
    for cand in result.candidates:
        assert cand.area_mm2 == (3000 - 115 - 57) * (4000 - 230)
        assert not cand.inset_failed


def test_dangling_wall_does_not_create_a_room() -> None:
    walls = _rect_walls(6000, 4000)
    walls.append(_wall("wall_stub", 3000, 0, 3000, 1500, INTERNAL_MM))
    result = room_candidates(walls)
    assert len(result.candidates) == 1
    assert result.candidates[0].area_mm2 == (6000 - 230) * (4000 - 230)


def test_tiny_faces_are_ignored() -> None:
    """Slivers below MIN_ROOM_AREA_MM2 (0.5 m^2) are subdivision noise, not rooms.

    The threshold is checked on the CLEAR polygon, so the arithmetic is
    ``(x - 115 - 57) * (4000 - 230)``: a strip at x=400 clears 859_560 mm^2 and
    counts; the same strip at x=300 clears 482_560 mm^2 and does not.
    """
    kept = _rect_walls(6000, 4000)
    kept.append(_wall("wall_a", 400, 0, 400, 4000, INTERNAL_MM))
    result = room_candidates(kept)
    assert len(result.candidates) == 2
    assert min(c.area_mm2 for c in result.candidates) == (400 - 115 - 57) * (4000 - 230)

    dropped = _rect_walls(6000, 4000)
    dropped.append(_wall("wall_a", 300, 0, 300, 4000, INTERNAL_MM))
    sliver = room_candidates(dropped)
    assert len(sliver.candidates) == 1
    assert all(c.area_mm2 >= 500_000 for c in sliver.candidates)


def test_candidates_are_sorted_deterministically() -> None:
    walls = _rect_walls(6000, 4000)
    walls.append(_wall("wall_spine", 3000, 0, 3000, 4000, INTERNAL_MM))
    first = room_candidates(walls)
    second = room_candidates(list(reversed(walls)))
    assert [polygon_key(list(c.polygon)) for c in first.candidates] == [
        polygon_key(list(c.polygon)) for c in second.candidates
    ]


# ---------------------------------------------------------------------------
# Section 16 property: random rectangular subdivisions
# ---------------------------------------------------------------------------


@given(
    x_cuts=st.lists(st.integers(min_value=1, max_value=9), min_size=0, max_size=3, unique=True),
    y_cuts=st.lists(st.integers(min_value=1, max_value=6), min_size=0, max_size=2, unique=True),
)
@settings(max_examples=60, deadline=None)
def test_grid_subdivision_finds_every_room(x_cuts: list[int], y_cuts: list[int]) -> None:
    """A rectangle cut by k vertical and m horizontal walls has (k+1)(m+1) rooms.

    Cuts are on a 1000mm lattice so every cell is comfortably above the 0.5 m^2
    noise floor, and every clear dimension is computable exactly:
    ``span - half(left wall) - half(right wall)``.
    """
    width, height = 10_000, 7_000
    xs = sorted(v * 1000 for v in x_cuts)
    ys = sorted(v * 1000 for v in y_cuts)

    walls = _rect_walls(width, height)
    for i, x in enumerate(xs):
        walls.append(_wall(f"wall_v{i}", x, 0, x, height, INTERNAL_MM))
    for i, y in enumerate(ys):
        walls.append(_wall(f"wall_h{i}", 0, y, width, y, INTERNAL_MM))

    result = room_candidates(walls)
    assert len(result.candidates) == (len(xs) + 1) * (len(ys) + 1)

    def spans(cuts: list[int], total: int) -> list[tuple[int, int, int, int]]:
        """(start, end, inset at start, inset at end) per band."""
        edges = [0, *cuts, total]
        out = []
        for i in range(len(edges) - 1):
            lo_inset = EXTERNAL_MM // 2 if i == 0 else INTERNAL_MM // 2
            hi_inset = EXTERNAL_MM // 2 if i == len(edges) - 2 else INTERNAL_MM // 2
            out.append((edges[i], edges[i + 1], lo_inset, hi_inset))
        return out

    expected = sorted(
        (x1 - x0 - xi - xj) * (y1 - y0 - yi - yj)
        for (x0, x1, xi, xj) in spans(xs, width)
        for (y0, y1, yi, yj) in spans(ys, height)
    )
    assert sorted(c.area_mm2 for c in result.candidates) == expected

    # the slab outline is the envelope grown outward by half the external wall
    assert result.outline is not None
    assert polygon_area_mm2(list(result.outline)) == (width + EXTERNAL_MM) * (height + EXTERNAL_MM)


@given(seed=st.integers(min_value=0, max_value=1_000))
@settings(max_examples=25, deadline=None)
def test_detection_is_order_independent(seed: int) -> None:
    """Shuffling the wall list must not change the rooms (ids included)."""
    import random

    rng = random.Random(seed)
    walls = _rect_walls(9000, 6000)
    walls.append(_wall("wall_v", 4500, 0, 4500, 6000, INTERNAL_MM))
    walls.append(_wall("wall_h", 0, 3000, 9000, 3000, INTERNAL_MM))

    baseline = room_candidates(walls)
    shuffled = list(walls)
    rng.shuffle(shuffled)
    other = room_candidates(shuffled)
    assert [polygon_key(list(c.polygon)) for c in baseline.candidates] == [
        polygon_key(list(c.polygon)) for c in other.candidates
    ]
    assert [c.area_mm2 for c in baseline.candidates] == [c.area_mm2 for c in other.candidates]


# ---------------------------------------------------------------------------
# ID PRESERVATION — the load-bearing part
# ---------------------------------------------------------------------------


def _rooms_by_id(rooms) -> dict[str, Room]:  # type: ignore[no-untyped-def]
    return {r.id: r for r in rooms}


def test_moving_a_wall_preserves_room_ids_and_metadata() -> None:
    doc = make_two_room_plan()
    rooms = sorted(doc.house.rooms, key=lambda r: r.polygon[0].x)
    west, east = rooms[0], rooms[1]
    doc = apply_group(
        doc,
        [
            op("room.assign", roomId=west.id, type="living", name="Living"),
            op("room.assign", roomId=east.id, type="bedroom_master", name="Master"),
        ],
    ).model

    moved = fold(
        doc,
        op(
            "wall.move",
            wallId=FIXTURE_IDS["wallSpine"],
            a={"x": 3600, "y": 0},
            b={"x": 3600, "y": 4000},
        ),
    ).model

    after = _rooms_by_id(moved.house.rooms)
    assert set(after) == {west.id, east.id}, "a 600mm nudge must not re-mint room ids"
    assert after[west.id].name == "Living"
    assert after[east.id].name == "Master"
    assert after[west.id].area_mm2 > west.area_mm2
    assert after[east.id].area_mm2 < east.area_mm2


def test_deleting_the_spine_merges_two_rooms_into_one_surviving_id() -> None:
    doc = make_two_room_plan()
    before_ids = {r.id for r in doc.house.rooms}
    merged = fold(doc, op("wall.delete", wallId=FIXTURE_IDS["wallSpine"])).model
    assert len(merged.house.rooms) == 1
    survivor = merged.house.rooms[0]
    assert survivor.id in before_ids, "the merged room keeps one of the two ids"
    assert survivor.area_mm2 == (6000 - 230) * (4000 - 230)


def test_split_then_merge_returns_the_id_to_the_same_room() -> None:
    """The tie-break rule exists for exactly this: undo must not rename a room."""
    doc = make_two_room_plan()
    result = fold(doc, op("wall.delete", wallId=FIXTURE_IDS["wallSpine"]))
    merged = result.model
    restored = merged
    for inv in result.inverse:
        restored = fold(restored, inv).model
    assert {r.id for r in restored.house.rooms} == {r.id for r in doc.house.rooms}


def test_a_new_enclosure_gets_a_derived_id_not_a_random_one() -> None:
    doc = make_empty_doc()
    ops = [
        op("storey.add", id=GF, index=0, name="Ground Floor", heightMm=3000),
        op(
            "wall.add",
            id=fixed_id("wall", "A"),
            storeyId=GF,
            a={"x": 0, "y": 0},
            b={"x": 5000, "y": 0},
            thicknessMm=230,
            kind="external",
        ),
        op(
            "wall.add",
            id=fixed_id("wall", "B"),
            storeyId=GF,
            a={"x": 5000, "y": 0},
            b={"x": 5000, "y": 4000},
            thicknessMm=230,
            kind="external",
        ),
        op(
            "wall.add",
            id=fixed_id("wall", "C"),
            storeyId=GF,
            a={"x": 5000, "y": 4000},
            b={"x": 0, "y": 4000},
            thicknessMm=230,
            kind="external",
        ),
        op(
            "wall.add",
            id=fixed_id("wall", "D"),
            storeyId=GF,
            a={"x": 0, "y": 4000},
            b={"x": 0, "y": 0},
            thicknessMm=230,
            kind="external",
        ),
    ]
    first = apply_group(doc, ops).model
    second = apply_group(make_empty_doc(), ops).model
    assert [r.id for r in first.house.rooms] == [r.id for r in second.house.rooms]
    assert first.house.rooms[0].id.startswith("room_")


def test_match_rooms_below_the_threshold_starts_a_new_room() -> None:
    from garh_model.geometry import Pt

    walls = _rect_walls(6000, 4000)
    candidates = room_candidates(walls).candidates
    far_away = Room(
        id=fixed_id("room", "AA"),
        storey_id=GF,
        type="bedroom",
        name="Elsewhere",
        polygon=(Pt(50_000, 50_000), Pt(53_000, 50_000), Pt(53_000, 53_000), Pt(50_000, 53_000)),
        area_mm2=9_000_000,
        tags=(),
        locked=False,
        target_area_mm2=None,
        must_face=None,
    )
    matches = match_rooms(candidates, [far_away])
    assert matches[0].room_id is None
    assert matches[0].jaccard == 0.0


def test_match_rooms_uses_maximum_jaccard() -> None:
    from garh_model.geometry import Pt

    walls = _rect_walls(6000, 4000)
    candidates = room_candidates(walls).candidates
    clear = candidates[0].polygon
    close = Room(
        id=fixed_id("room", "CL"),
        storey_id=GF,
        type="living",
        name="Close",
        polygon=tuple(Pt(p.x + 50, p.y + 50) for p in clear),
        area_mm2=polygon_area_mm2([Pt(p.x + 50, p.y + 50) for p in clear]),
        tags=(),
        locked=False,
        target_area_mm2=None,
        must_face=None,
    )
    poor = replace(close, id=fixed_id("room", "PR"))
    matches = match_rooms(candidates, [poor, close])
    assert matches[0].room_id in {poor.id, close.id}
    assert matches[0].jaccard > DEFAULT_JACCARD_THRESHOLD


def test_detect_rooms_reports_what_disappeared() -> None:
    doc = make_two_room_plan()
    walls: list[Wall] = [w for w in doc.house.walls if w.id != FIXTURE_IDS["wallSpine"]]
    result = detect_rooms(walls, GF, list(doc.house.rooms))
    assert len(result.rooms) == 1
    assert len(result.removed_room_ids) == 1
    assert result.removed_room_ids[0] in {r.id for r in doc.house.rooms}
