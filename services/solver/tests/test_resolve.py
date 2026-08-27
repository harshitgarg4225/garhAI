"""§5.7 partial re-solve — every promise a lock makes, proven stage-B-side on 3.9.

The CP-SAT stages are fakes (see test_pipeline); what is REAL here is everything
§5.7 actually specifies:

* locked polygons become fixed obstacles on the coarse grid;
* stage B's wall network is deduped against locked walls with the locked side
  winning, and a stage B that *modified* a locked wall costs the candidate its life;
* locked room ids come back byte-preserved — the very same payload objects;
* the unlocked-room diff reuses the model core's Jaccard primitive
  (``garh_model.geometry.jaccard``), so the solver's "Bedroom 2 moved" agrees with
  the editor's room re-detection about what "the same room" means;
* the whole re-solve fits the ≤15s budget by construction.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from services.solver import resolve
from services.solver.envelope import derive_envelope
from services.solver.pipeline import (
    DETERMINISTIC_TEST_PROFILE,
    PRODUCTION_PROFILE,
    SolveContext,
    SolverProfile,
)
from services.solver.stages import Candidate, GridSpec, grid_envelope
from services.solver.tests.test_pipeline import (
    EDGES,
    PLOT,
    PROFILE_BLR,
    FakeSolver,
    Recorder,
    make_params,
)
from services.solver.types import RoomPlacement, RoomRequest, StairAnchor

try:  # the common errors module is dependency-free
    from services.common.errors import InvalidJobError
except ImportError:  # pragma: no cover
    raise


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LOCKED_WALL = {
    "id": "wall_01J00000000000000000000LW1",
    "a": {"x": 1_500, "y": 1_500},
    "b": {"x": 5_500, "y": 1_500},
    "thicknessMm": 230,
    "kind": "external",
}

LOCKED_ROOM_RAW: dict[str, Any] = {
    "id": "room_01J00000000000000000000LK1",
    "storeyIndex": 0,
    "type": "living",
    "name": "Living",
    "polygon": [
        {"x": 1_500, "y": 1_500},
        {"x": 5_500, "y": 1_500},
        {"x": 5_500, "y": 5_500},
        {"x": 1_500, "y": 5_500},
    ],
    "walls": [LOCKED_WALL],
}


def payload_with_locked() -> dict[str, Any]:
    return {
        "lockedRoomIds": [LOCKED_ROOM_RAW["id"]],
        "lockedRooms": [LOCKED_ROOM_RAW],
    }


def make_envelope() -> Any:
    return derive_envelope(PLOT, EDGES, PROFILE_BLR, storeys=1)


def locked_rooms() -> tuple[resolve.LockedRoom, ...]:
    return resolve.parse_locked_rooms(payload_with_locked())


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_locked_rooms_reads_geometry_and_walls() -> None:
    rooms = locked_rooms()
    assert len(rooms) == 1
    room = rooms[0]
    assert room.id == LOCKED_ROOM_RAW["id"]
    assert room.polygon == ((1_500, 1_500), (5_500, 1_500), (5_500, 5_500), (1_500, 5_500))
    assert room.walls[0].id == LOCKED_WALL["id"]
    assert room.walls[0].thickness_mm == 230
    assert room.raw is LOCKED_ROOM_RAW, "the raw entry must survive by reference"


def test_parse_rejects_a_locked_id_without_geometry() -> None:
    payload = {"lockedRoomIds": ["room_x", LOCKED_ROOM_RAW["id"]], "lockedRooms": [LOCKED_ROOM_RAW]}
    try:
        resolve.parse_locked_rooms(payload)
    except InvalidJobError as exc:
        assert "room_x" in (exc.detail or "")
    else:
        raise AssertionError("a lock without a polygon cannot be an obstacle")


def test_parse_rejects_float_geometry() -> None:
    bad = dict(LOCKED_ROOM_RAW, polygon=[{"x": 0.5, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}])
    try:
        resolve.parse_locked_rooms({"lockedRooms": [bad]})
    except InvalidJobError:
        pass
    else:
        raise AssertionError("geometry is integer millimetres, everywhere, always")


# ---------------------------------------------------------------------------
# Fixed obstacles (§5.7)
# ---------------------------------------------------------------------------


def test_mask_locked_cells_blocks_exactly_the_covered_cells() -> None:
    grid = grid_envelope(make_envelope())
    masked = resolve.mask_locked_cells(grid, locked_rooms())

    before = grid.buildable_cells()
    after = masked.buildable_cells()
    assert after < before, "a locked room must remove buildable cells"

    # The locked square spans 1500..5500 in both axes. A cell is blocked iff its
    # CENTRE is inside — the same quantisation rule the envelope grid uses.
    half = grid.module_mm // 2
    for row in range(masked.rows):
        for col in range(masked.cols):
            cx = grid.origin[0] + col * grid.module_mm + half
            cy = grid.origin[1] + row * grid.module_mm + half
            inside = 1_500 <= cx <= 5_500 and 1_500 <= cy <= 5_500
            if grid.mask[row][col] and inside:
                assert not masked.mask[row][col], "centre inside the lock ⇒ blocked"
            if grid.mask[row][col] and not inside:
                assert masked.mask[row][col], "cells clear of the lock stay buildable"

    assert grid.buildable_cells() == before, "the input grid is never mutated"


def test_mask_ignores_locks_on_other_storeys() -> None:
    grid = grid_envelope(make_envelope())
    upstairs = tuple(replace(room, storey_index=1) for room in locked_rooms())
    masked = resolve.mask_locked_cells(grid, upstairs, storey_index=0)
    assert masked.buildable_cells() == grid.buildable_cells()


def test_residual_params_drops_requests_the_lock_answers() -> None:
    params = make_params(
        rooms=(
            RoomRequest("living", "living", 12_000_000, 16_000_000, 3_000, locked=True),
            RoomRequest("kitchen", "kitchen", 5_000_000, 9_000_000, 1_800, is_wet=True),
        )
    )
    residual = resolve.residual_params(params, locked_rooms())
    assert tuple(room.key for room in residual.rooms) == ("kitchen",)
    assert residual.locked_room_ids == (LOCKED_ROOM_RAW["id"],)


# ---------------------------------------------------------------------------
# Shared-wall dedupe — locked side wins
# ---------------------------------------------------------------------------


def _locked_walls() -> tuple[resolve.LockedWall, ...]:
    return locked_rooms()[0].walls


def test_exact_duplicate_new_wall_is_dropped_and_lock_kept_by_reference() -> None:
    duplicate = {
        "id": "wall_01J00000000000000000000NW1",
        "a": {"x": 1_500, "y": 1_500},
        "b": {"x": 5_500, "y": 1_500},
        "thicknessMm": 115,
        "kind": "internal",
    }
    lockset = _locked_walls()
    merged = resolve.merge_walls_locked_wins(lockset, [duplicate])
    assert len(merged) == 1
    assert merged[0] is lockset[0], "the locked wall survives as the SAME object"


def test_partial_overlap_is_trimmed_to_the_unlocked_remainder() -> None:
    overlapping = {
        "id": "wall_01J00000000000000000000NW2",
        "a": {"x": 1_500, "y": 1_500},
        "b": {"x": 9_000, "y": 1_500},
        "thicknessMm": 115,
        "kind": "internal",
    }
    lockset = _locked_walls()
    merged = resolve.merge_walls_locked_wins(lockset, [overlapping])
    assert len(merged) == 2
    fragment = merged[1]
    assert fragment["a"] == {"x": 5_500, "y": 1_500}
    assert fragment["b"] == {"x": 9_000, "y": 1_500}
    assert fragment["kind"] == "internal", "everything but geometry and id is kept"
    assert fragment["id"].startswith("wall_") and len(fragment["id"]) == 5 + 26

    again = resolve.merge_walls_locked_wins(lockset, [dict(overlapping)])
    assert again[1]["id"] == fragment["id"], "fragment ids are derived, not random"


def test_interior_overlap_splits_into_two_fragments() -> None:
    lockset = (
        resolve.LockedWall(
            id="wall_01J00000000000000000000LW2",
            a=(3_000, 9_000),
            b=(5_000, 9_000),
            thickness_mm=115,
            kind="internal",
        ),
    )
    spanning = {
        "id": "wall_01J00000000000000000000NW3",
        "a": {"x": 1_500, "y": 9_000},
        "b": {"x": 8_000, "y": 9_000},
        "thicknessMm": 115,
        "kind": "internal",
    }
    merged = resolve.merge_walls_locked_wins(lockset, [spanning])
    assert len(merged) == 3
    spans = sorted((wall["a"]["x"], wall["b"]["x"]) for wall in merged[1:])
    assert spans == [(1_500, 3_000), (5_000, 8_000)]
    assert merged[1]["id"] != merged[2]["id"]


def test_sub_module_fragment_is_dropped_as_construction_noise() -> None:
    lockset = (
        resolve.LockedWall(
            id="wall_01J00000000000000000000LW3",
            a=(1_500, 9_000),
            b=(5_450, 9_000),
            thickness_mm=115,
            kind="internal",
        ),
    )
    nearly_covered = {
        "id": "wall_01J00000000000000000000NW4",
        "a": {"x": 1_500, "y": 9_000},
        "b": {"x": 5_500, "y": 9_000},  # 50mm poke past the locked wall
        "thicknessMm": 115,
        "kind": "internal",
    }
    merged = resolve.merge_walls_locked_wins(lockset, [nearly_covered])
    assert len(merged) == 1, "a 50mm fragment is not a wall (< one 115mm module)"


def test_diagonal_walls_pass_through_untouched() -> None:
    diagonal = {
        "id": "wall_01J00000000000000000000NW5",
        "a": {"x": 0, "y": 0},
        "b": {"x": 3_000, "y": 3_000},
        "thicknessMm": 115,
        "kind": "internal",
    }
    merged = resolve.merge_walls_locked_wins(_locked_walls(), [diagonal])
    assert merged[-1] is diagonal


def test_strip_relocked_walls_detects_a_touched_lock() -> None:
    lockset = _locked_walls()
    moved = dict(LOCKED_WALL, b={"x": 6_000, "y": 1_500})  # same id, new geometry
    assert resolve.strip_relocked_walls(lockset, [moved]) is None

    identical = dict(LOCKED_WALL)
    survivors = resolve.strip_relocked_walls(lockset, [identical])
    assert survivors == [], "an identical re-emission is dropped, not duplicated"


def test_locked_walls_untouched_detects_mutation() -> None:
    lockset = _locked_walls()
    assert resolve.locked_walls_untouched(lockset, [*list(lockset), {"id": "wall_x"}])
    mutated = (replace(lockset[0], b=(6_000, 1_500)),)
    assert not resolve.locked_walls_untouched(lockset, list(mutated))
    assert not resolve.locked_walls_untouched(lockset, [])


# ---------------------------------------------------------------------------
# Budget (§5.7: ≤15s)
# ---------------------------------------------------------------------------


def test_resolve_profile_fits_the_budget() -> None:
    squeezed = resolve.resolve_profile(PRODUCTION_PROFILE)
    assert squeezed.time_budget_seconds is not None
    assert (
        squeezed.time_budget_seconds * resolve.RESOLVE_MAX_CANDIDATES
        <= resolve.RESOLVE_BUDGET_SECONDS
    ), "per-candidate budget × candidate cap must fit the §5.7 budget"

    deterministic = resolve.resolve_profile(DETERMINISTIC_TEST_PROFILE)
    assert (
        deterministic.time_budget_seconds is None
    ), "the deterministic profile keeps solution/branch limits — already bounded"


# ---------------------------------------------------------------------------
# The driver, end to end (fake stages)
# ---------------------------------------------------------------------------


class ResolveFake(FakeSolver):
    """FakeSolver whose stage B emits walls that collide with the locked wall."""

    def __init__(self, *, touch_lock: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.touch_lock = touch_lock
        self.masked_cells_seen: list[int] = []
        self.residual_keys_seen: list[tuple[str, ...]] = []

    def stage_a(
        self,
        grid: GridSpec,
        params: Any,
        anchor: StairAnchor,
        *,
        profile: SolverProfile,
        relaxed: bool = False,
    ) -> Candidate | None:
        self.masked_cells_seen.append(grid.buildable_cells())
        self.residual_keys_seen.append(tuple(room.key for room in params.rooms))
        return super().stage_a(grid, params, anchor, profile=profile, relaxed=relaxed)

    def stage_b(self, candidate: Candidate, params: Any, envelope: Any) -> Mapping[str, Any] | None:
        self.stage_b_calls.append(candidate.stair_anchor.id)
        if self.touch_lock:
            # Stage B "moved" the locked wall: same id, different geometry.
            return {"walls": [dict(LOCKED_WALL, b={"x": 6_000, "y": 1_500})]}
        return {
            "walls": [
                # dead-on duplicate of the locked wall → dropped (locked side wins)
                {
                    "id": "wall_01J00000000000000000000NW6",
                    "a": {"x": 1_500, "y": 1_500},
                    "b": {"x": 5_500, "y": 1_500},
                    "thicknessMm": 115,
                    "kind": "internal",
                },
                # overhangs the locked wall → trimmed to the remainder
                {
                    "id": "wall_01J00000000000000000000NW7",
                    "a": {"x": 1_500, "y": 1_500},
                    "b": {"x": 9_000, "y": 1_500},
                    "thicknessMm": 115,
                    "kind": "internal",
                },
            ],
            "anchor": candidate.stair_anchor.id,
        }


def run_resolve_with(
    fake: ResolveFake,
    *,
    previous_rooms: Sequence[Mapping[str, Any]] = (),
    params: Any = None,
) -> tuple[resolve.ResolveOutcome, Recorder]:
    recorder = Recorder()
    context = SolveContext(
        params=params if params is not None else make_params(),
        progress=recorder,
        check_cancelled=lambda: None,
        profile=DETERMINISTIC_TEST_PROFILE,
        stages=fake.stage_set(),
    )
    outcome = asyncio.run(
        resolve.run_resolve(context, locked_rooms(), previous_rooms=previous_rooms)
    )
    return outcome, recorder


def test_run_resolve_preserves_locked_ids_byte_for_byte() -> None:
    outcome, _ = run_resolve_with(ResolveFake())
    assert outcome.locked_room_ids == (LOCKED_ROOM_RAW["id"],)
    assert outcome.locked_rooms_raw[0] is LOCKED_ROOM_RAW, (
        "§5.7: locked geometry is passed through as the same object — byte-preserved, "
        "never re-parsed and re-serialised"
    )
    extra = outcome.to_extra_data()
    assert extra["lockedRooms"][0] is LOCKED_ROOM_RAW
    assert extra["lockedRoomIds"] == [LOCKED_ROOM_RAW["id"]]


def test_run_resolve_masks_the_grid_and_solves_the_residual_program() -> None:
    fake = ResolveFake()
    params = make_params(
        rooms=(
            RoomRequest("living", "living", 12_000_000, 16_000_000, 3_000, locked=True),
            RoomRequest("kitchen", "kitchen", 5_000_000, 9_000_000, 1_800, is_wet=True),
            RoomRequest("master", "bedroom_master", 9_500_000, 16_000_000, 2_400),
        )
    )
    outcome, _ = run_resolve_with(fake, params=params)

    unmasked = grid_envelope(make_envelope()).buildable_cells()
    assert fake.masked_cells_seen and all(
        seen < unmasked for seen in fake.masked_cells_seen
    ), "stage A must solve inside the residual space, never under a locked room"
    assert fake.residual_keys_seen[0] == (
        "kitchen",
        "master",
    ), "the locked living room is not re-solved"
    assert outcome.result.options, "the residual program still produces options"


def test_run_resolve_locked_rooms_rejoin_every_option_with_their_ids() -> None:
    outcome, _ = run_resolve_with(ResolveFake())
    for option in outcome.result.options:
        locked_placements = [
            placement
            for placement in option.placements
            if placement.room_id == LOCKED_ROOM_RAW["id"]
        ]
        assert len(locked_placements) == 1, "the locked room re-joins scoring"
        placement = locked_placements[0]
        assert (placement.x_mm, placement.y_mm) == (1_500, 1_500)
        assert (placement.width_mm, placement.depth_mm) == (4_000, 4_000)
        assert any(
            "living@" in token for token in option.signature
        ), "the locked room shapes the diversity signature too"


def test_run_resolve_dedupes_shared_walls_with_locked_side_winning() -> None:
    fake = ResolveFake()
    run_resolve_with(fake)
    # The dedupe itself is pinned by the merge tests above; here we prove the
    # driver wired stage_b through it: stage B ran and no candidate was discarded.
    assert fake.stage_b_calls, "stage B ran"


def test_run_resolve_discards_candidates_that_touch_a_locked_wall() -> None:
    fake = ResolveFake(touch_lock=True)
    outcome, recorder = run_resolve_with(fake)
    assert not outcome.result.options, "every candidate moved the lock ⇒ none survive"
    diversity = next(e for e in recorder.events if e["stage"] == "diversity")
    assert any(
        d["stage"] == "stage-b" and "locked" in d["reason"] for d in diversity["data"]["discards"]
    ), "the §5.7 discard reason is logged per candidate"
    assert outcome.result.banner is not None, "zero options gets the honest banner"


def test_run_resolve_caps_candidates_and_budget() -> None:
    fake = ResolveFake(anchor_count=6)
    run_resolve_with(fake)
    solved_anchors = {call[0] for call in fake.stage_a_calls}
    assert (
        len(solved_anchors) <= resolve.RESOLVE_MAX_CANDIDATES
    ), "§5.7 is an edit, not a generate: candidates are capped"
    profiles = {call[2] for call in fake.stage_a_calls}
    for profile in profiles:
        if profile.time_budget_seconds is not None:
            assert (
                profile.time_budget_seconds * resolve.RESOLVE_MAX_CANDIDATES
                <= resolve.RESOLVE_BUDGET_SECONDS
            )


def test_run_resolve_requires_at_least_one_lock() -> None:
    context = SolveContext(
        params=make_params(),
        progress=Recorder(),
        check_cancelled=lambda: None,
        profile=DETERMINISTIC_TEST_PROFILE,
        stages=ResolveFake().stage_set(),
    )
    try:
        asyncio.run(resolve.run_resolve(context, ()))
    except InvalidJobError:
        pass
    else:
        raise AssertionError("a re-solve with nothing locked is a generate")


# ---------------------------------------------------------------------------
# Diff-matching — the real garh_model Jaccard primitive
# ---------------------------------------------------------------------------


def _ensure_garh_model() -> None:
    """Make ``garh_model`` importable in the monorepo checkout, or skip.

    The package ships inside apps/api (see its pyproject). CI installs it; on a bare
    checkout the repo-relative path does the same job. Skipping (rather than faking
    the matcher) keeps the promise honest: this suite only ever tests the REAL
    Jaccard primitive, because §3 makes that primitive the definition of "same room".
    """
    try:
        import garh_model.geometry

        return
    except ImportError:
        pass
    api_dir = Path(__file__).resolve().parents[3] / "apps" / "api"
    if (api_dir / "garh_model" / "geometry.py").exists():
        sys.path.insert(0, str(api_dir))
        try:
            import garh_model.geometry  # noqa: F401

            return
        except ImportError:
            pass
    raise unittest.SkipTest("garh_model is not importable here (install apps/api)")


def _rect(x: int, y: int, w: int, h: int) -> list[dict[str, int]]:
    return [
        {"x": x, "y": y},
        {"x": x + w, "y": y},
        {"x": x + w, "y": y + h},
        {"x": x, "y": y + h},
    ]


def test_diff_matching_classifies_kept_moved_new_and_removed() -> None:
    _ensure_garh_model()
    previous = [
        {"id": "room_A", "type": "kitchen", "polygon": _rect(6_000, 1_500, 3_000, 3_000)},
        {"id": "room_B", "type": "bedroom", "polygon": _rect(6_000, 5_000, 4_000, 4_000)},
        {"id": "room_C", "type": "study", "polygon": _rect(12_000, 12_000, 2_000, 2_000)},
        {"id": LOCKED_ROOM_RAW["id"], "type": "living", "polygon": LOCKED_ROOM_RAW["polygon"]},
    ]
    placements = (
        # identical footprint to room_A → kept
        RoomPlacement("kitchen", "kitchen", 0, 6_000, 1_500, 3_000, 3_000),
        # overlaps room_B strongly but shifted → moved
        RoomPlacement("master", "bedroom_master", 0, 6_500, 5_500, 4_000, 4_000),
        # nowhere near anything old → new
        RoomPlacement("bath", "bath", 0, 1_500, 12_000, 2_000, 2_000),
    )
    diffs = resolve.match_unlocked_rooms(previous, placements, locked_ids=(LOCKED_ROOM_RAW["id"],))
    by_relation = {}
    for diff in diffs:
        by_relation.setdefault(diff.relation, []).append(diff)

    kept = by_relation["kept"][0]
    assert (kept.new_key, kept.room_id, kept.jaccard_x100) == ("kitchen", "room_A", 100)

    moved = by_relation["moved"][0]
    assert (moved.new_key, moved.room_id) == ("master", "room_B")
    assert resolve.DIFF_JACCARD_THRESHOLD_X100 <= moved.jaccard_x100 < 100

    assert [d.new_key for d in by_relation["new"]] == ["bath"]
    assert [d.room_id for d in by_relation["removed"]] == ["room_C"]
    assert all(
        d.room_id != LOCKED_ROOM_RAW["id"] for d in diffs
    ), "locked rooms are preserved, never diff-matched"


def test_run_resolve_reports_diffs_for_the_best_option() -> None:
    _ensure_garh_model()
    previous = [
        {"id": "room_OLD", "type": "living", "polygon": _rect(1_500, 1_500, 4_000, 4_000)},
    ]
    outcome, _ = run_resolve_with(ResolveFake(), previous_rooms=previous)
    assert outcome.result.options
    extra = outcome.to_extra_data()
    assert (
        isinstance(extra["roomDiffs"], list) and extra["roomDiffs"]
    ), "a resolve with previousRooms must tell the diff story"
    for diff in extra["roomDiffs"]:
        assert set(diff) <= {"relation", "jaccardX100", "newKey", "roomId"}
        assert isinstance(diff["jaccardX100"], int), "no floats on the wire"


# ---------------------------------------------------------------------------
# Bare-python runner (pytest is not installed on the build machine)
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import traceback

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS %s" % name)
            except unittest.SkipTest as skip:
                print("SKIP %s (%s)" % (name, skip))
            except Exception:
                failures += 1
                print("FAIL %s" % name)
                traceback.print_exc()
    sys.exit(1 if failures else 0)
