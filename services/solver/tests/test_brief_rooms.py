"""Reading the brief's room list into the solver's request list.

Two fields the brief has always carried were read by nothing, and both failed
silently — the worst kind, because the job still finished "succeeded":

* **``count``**. ``{"type": "bedroom", "count": 2}`` produced ONE RoomRequest. A
  client who asked for two identical bedrooms got one, and a brief listing the same
  type twice lost a room to a key collision. The seeded demo brief works around it by
  giving every room a distinct type — a second bedroom is a ``guest_bedroom``, a
  second toilet a ``wc`` — and its own docstring explains the workaround. That is the
  tell: the only brief in the product that generated anything was the one authored
  around the bug.
* **``storey``**. The parser read ``storeyIndex``; the brief document — which the API
  forwards verbatim — writes ``storey``. So every storey pin an architect set was
  discarded, including the demo's own two.
"""

from __future__ import annotations

import pytest

from services.common.errors import InvalidJobError
from services.solver.handler import MAX_ROOM_COUNT, _parse_rooms


def test_a_count_of_one_is_one_room() -> None:
    rooms = _parse_rooms([{"type": "kitchen", "count": 1}])
    assert [r.key for r in rooms] == ["kitchen"]


def test_a_count_of_two_is_two_rooms() -> None:
    """The defect, stated as an assertion. This returned one room."""
    rooms = _parse_rooms([{"type": "bedroom", "count": 2}])
    assert [r.key for r in rooms] == ["bedroom", "bedroom2"]
    assert [r.room_type for r in rooms] == ["bedroom", "bedroom"]


def test_a_missing_count_means_one() -> None:
    assert len(_parse_rooms([{"type": "kitchen"}])) == 1


def test_every_key_is_unique_across_the_whole_list() -> None:
    """A duplicate key is a lost room: the program layer maps by key, so the second
    entry silently replaces the first."""
    rooms = _parse_rooms([{"type": "bedroom", "count": 3}, {"type": "bath_wc", "count": 2}])
    keys = [r.key for r in rooms]
    assert len(keys) == len(set(keys)) == 5


def test_two_entries_of_the_same_type_both_survive() -> None:
    """The case the demo brief was authored around — a brief may legitimately list
    ``bedroom`` twice with different sizes."""
    rooms = _parse_rooms(
        [
            {"type": "bedroom", "count": 1, "minAreaMm2": 12_000_000},
            {"type": "bedroom", "count": 1, "minAreaMm2": 9_500_000},
        ]
    )
    assert len({r.key for r in rooms}) == 2
    assert sorted(r.min_area_mm2 for r in rooms) == [9_500_000, 12_000_000]


def test_the_keys_match_the_other_path_into_the_solver() -> None:
    """``build_program_from_brief`` names expanded rooms ``bedroom``/``bedroom2``.
    Two paths naming the same rooms differently is how a lock or an adjacency wish
    silently stops matching."""
    from services.solver.program import build_program_from_brief

    brief = {"rooms": [{"type": "bedroom", "count": 2, "minAreaMm2": 9_500_000}]}
    program = build_program_from_brief(brief, storeys=1)
    handler_keys = [r.key for r in _parse_rooms(brief["rooms"])]
    assert handler_keys == [entry.key for entry in program.rooms][: len(handler_keys)]


# ===========================================================================
# The storey pin
# ===========================================================================
def test_the_briefs_own_spelling_is_read() -> None:
    """``storey`` is what the brief document writes, and it was going nowhere."""
    rooms = _parse_rooms([{"type": "wc", "count": 1, "storey": 0}])
    assert rooms[0].storey_index == 0


def test_the_payload_spelling_still_works() -> None:
    """The worker's own vocabulary must keep working — this is an addition, not a
    replacement, and a payload built by the job layer uses ``storeyIndex``."""
    rooms = _parse_rooms([{"type": "wc", "count": 1, "storeyIndex": 1}])
    assert rooms[0].storey_index == 1


def test_an_unpinned_room_stays_unpinned() -> None:
    """Negative control: if this ever returns 0 instead of None, every room in every
    brief is silently pinned to the ground floor."""
    assert _parse_rooms([{"type": "kitchen"}])[0].storey_index is None


def test_a_pin_applies_to_every_room_the_count_expands_to() -> None:
    rooms = _parse_rooms([{"type": "bedroom", "count": 2, "storey": 1}])
    assert [r.storey_index for r in rooms] == [1, 1]


# ===========================================================================
# Refusals
# ===========================================================================
def test_an_absurd_count_is_refused_rather_than_expanded() -> None:
    """A typo of 400 bedrooms must not become 400 RoomRequests and a solver run
    nobody cancels."""
    with pytest.raises(InvalidJobError):
        _parse_rooms([{"type": "bedroom", "count": MAX_ROOM_COUNT + 1}])


def test_the_cap_itself_is_allowed() -> None:
    """Negative control on the refusal: it is a boundary, not a blanket."""
    assert len(_parse_rooms([{"type": "bedroom", "count": MAX_ROOM_COUNT}])) == MAX_ROOM_COUNT


def test_a_malformed_entry_is_refused_rather_than_skipped() -> None:
    """Dropping it would lose a room the client asked for, silently — and silence is
    the failure mode this whole file exists to close."""
    with pytest.raises(InvalidJobError):
        _parse_rooms([{"type": "kitchen"}, "not a room"])


def test_sizes_survive_the_expansion() -> None:
    """Every expanded room carries the entry's sizes — an expansion that dropped them
    would hand Stage A a zero-area room, which is the failure this whole area is
    about."""
    rooms = _parse_rooms(
        [
            {
                "type": "bedroom",
                "count": 2,
                "minAreaMm2": 9_500_000,
                "targetAreaMm2": 11_500_000,
                "minWidthMm": 3_000,
            }
        ]
    )
    for room in rooms:
        assert room.min_area_mm2 == 9_500_000
        assert room.target_area_mm2 == 11_500_000
        assert room.min_width_mm == 3_000
