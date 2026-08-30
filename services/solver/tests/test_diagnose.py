"""Why a storey could not be tiled, in numbers an architect can act on.

Stage A had one word for failure — ``infeasible`` — logged per anchor and reaching
nobody. Downstream that became "The plot, setbacks and brief left no workable layout":
true, unactionable, and the first thing a new user sees after doing everything right.
It is also why finding the defects in ``docs/first-run-verification.md`` took a
bisection: with no diagnostic, the only way to learn what the solver objected to was to
mutate the brief one field at a time and watch the option count.

The property that matters most here is the one that is easiest to get wrong: a cause
must only be claimed when arithmetic PROVES it. Guessing "you are short on area" at a
plan that fits by area sends an architect to shrink a bedroom that was never the
problem, and they will not trust the next message either.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.solver.diagnose import (
    CIRCULATION_ALLOWANCE,
    diagnose_storey,
    shortfall_banner,
)


@dataclass(frozen=True)
class FakeRoom:
    """The three fields `diagnose_storey` reads off a RoomBounds."""

    key: str
    min_area_cells: int
    min_side_cells: int


MODULE = 300  # mm, the coarse module Stage A tiles on


def diagnose(rooms, *, cols=40, rows=40, cap=0):
    return diagnose_storey(
        storey_index=0,
        rooms=rooms,
        cols=cols,
        rows=rows,
        net_cap_cells=cap,
        module_mm=MODULE,
    )


# ===========================================================================
# Area — the cause an architect can act on immediately
# ===========================================================================
def test_a_programme_bigger_than_the_envelope_is_proved_short_on_area() -> None:
    # 400 cells of rooms into a 100-cell envelope: no tiling saves this.
    result = diagnose([FakeRoom("bedroom", 400, 10)], cap=100)
    assert result.kind == "area"
    assert result.proved is True


def test_the_area_message_names_both_numbers_and_the_gap() -> None:
    """A number without its counterpart is not actionable. "You need 82 m² and have
    64" tells an architect what to do; "infeasible" does not."""
    result = diagnose([FakeRoom("bedroom", 400, 10)], cap=100)
    assert "m²" in result.message
    assert "short" in result.message
    assert result.action


def test_circulation_is_counted_in_the_demand() -> None:
    """A programme that exactly fills the envelope still has nowhere for the corridor.
    Ignoring circulation would report "fits" for a plan that cannot be walked through."""
    # 100 cells of rooms into exactly 100 cells: fits on paper, not once you can move.
    result = diagnose([FakeRoom("bedroom", 100, 5)], cap=100)
    assert result.kind == "area"
    assert CIRCULATION_ALLOWANCE > 0


def test_negative_control_a_programme_that_fits_is_not_blamed_on_area() -> None:
    """The half that keeps the diagnosis honest. Claiming an area shortfall on a plan
    that fits sends the architect to shrink a room that was never the problem."""
    result = diagnose([FakeRoom("bedroom", 100, 5)], cap=1_000)
    assert result.kind == "arrangement"
    assert result.proved is False


# ===========================================================================
# Width — invisible in an area total
# ===========================================================================
def test_a_room_wider_than_the_envelope_is_proved_impossible() -> None:
    """Area arithmetic cannot see this: a 6 m room in a 4 m envelope has plenty of
    total area available and still cannot be placed."""
    result = diagnose([FakeRoom("living_dining", 4, 20)], cols=10, rows=10, cap=1_000)
    assert result.kind == "width"
    assert result.proved is True
    assert "living dining" in result.message


def test_the_width_check_measures_the_envelope_at_its_WIDEST() -> None:
    """A long thin envelope can still take a wide room along its length. Comparing
    against the short side would refuse plans that are perfectly buildable."""
    room = FakeRoom("living_dining", 4, 30)
    assert diagnose([room], cols=40, rows=5, cap=1_000).kind != "width"
    assert diagnose([room], cols=10, rows=10, cap=1_000).kind == "width"


# ===========================================================================
# Cells
# ===========================================================================
def test_more_rooms_than_cells_is_arithmetic_not_tiling() -> None:
    rooms = [FakeRoom("r%d" % i, 1, 1) for i in range(20)]
    result = diagnose(rooms, cols=4, rows=4, cap=16)
    assert result.kind in ("area", "cells")
    assert result.proved is True


def test_an_empty_storey_says_so_rather_than_blaming_the_plot() -> None:
    result = diagnose([])
    assert result.kind == "arrangement"
    assert "no rooms" in result.message


# ===========================================================================
# The honest non-answer
# ===========================================================================
def test_when_nothing_is_proved_it_says_the_rooms_fit_and_points_elsewhere() -> None:
    """ "I could not find an arrangement" is a different sentence from "it does not
    fit", and it points at a different fix. Conflating them is how a diagnostic starts
    lying."""
    result = diagnose([FakeRoom("bedroom", 50, 4)], cap=1_000)
    assert result.kind == "arrangement"
    assert result.proved is False
    assert "fit this floor by area" in result.message
    assert "adjacency" in result.action or "must-face" in result.action


def test_it_never_raises_whatever_it_is_handed() -> None:
    """It runs after a failed solve. A diagnostic that can crash turns a bad result
    into no result."""
    for rooms in ([], [FakeRoom("x", 0, 0)], [FakeRoom("y", -1, -1)]):
        assert diagnose(rooms, cols=0, rows=0, cap=0) is not None


# ===========================================================================
# The banner
# ===========================================================================
def test_a_proved_cause_beats_an_unproved_one_in_the_banner() -> None:
    """With nothing to show, the banner is all the architect gets — so it must carry
    the floor that can be fixed with certainty, not the one that merely failed to
    arrange."""
    unproved = diagnose([FakeRoom("bedroom", 50, 4)], cap=1_000)
    proved = diagnose([FakeRoom("bedroom", 400, 10)], cap=100)
    banner = shortfall_banner([unproved, proved])
    assert banner is not None
    assert "short" in banner


def test_the_banner_carries_the_action_too() -> None:
    proved = diagnose([FakeRoom("bedroom", 400, 10)], cap=100)
    banner = shortfall_banner([proved])
    assert banner is not None and "Add a floor" in banner


def test_no_shortfalls_means_no_banner() -> None:
    """Negative control: a run that produced options must not grow a failure banner."""
    assert shortfall_banner([]) is None
