"""Why a storey could not be tiled, in numbers an architect can act on.

Stage A had one word for failure: ``infeasible``. It is logged per anchor, it does not
reach the job, and it says nothing about which constraint bit. Downstream that becomes
"The plot, setbacks and brief left no workable layout" — true, unactionable, and the
first thing a new user sees after doing everything right.

It is also why finding the defects in ``docs/first-run-verification.md`` took a
bisection rather than a read: with no diagnostic, the only way to learn what the solver
objected to was to mutate the brief one field at a time and watch the option count.

## What this can and cannot prove

The checks here are **necessary conditions**, tested by arithmetic. When one fails,
infeasibility is *proved* and the cause is exact — no arrangement of anything could
have worked, so the architect can be told precisely what to change:

* the rooms' own minimum areas exceed the buildable envelope;
* a single room is wider than the envelope at its widest;
* there are more rooms than the grid has cells to give them.

When none fails, this says so plainly rather than inventing a cause. "The rooms fit by
area but no arrangement satisfied the adjacency, Vastu and circulation constraints" is
a different sentence pointing at a different fix, and conflating the two would send an
architect to shrink a bedroom that was never the problem.

Deliberately ortools-free and pure: it runs after a failed solve, on plain data, so it
cannot itself fail the job or change what the solver decided.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["StoreyShortfall", "diagnose_storey", "shortfall_banner"]

#: Circulation a plan needs beyond the rooms themselves — corridor, landing, the space
#: a door swings into. §5.6 caps circulation as a share of the floor; this is the other
#: direction, the floor a programme needs *before* any of it can be arranged. Modest on
#: purpose: overstating it would blame the envelope for a programme that does fit.
CIRCULATION_ALLOWANCE = 0.12


@dataclass(frozen=True)
class StoreyShortfall:
    """The proved reason a storey could not be tiled, or the honest absence of one."""

    storey_index: int
    #: ``area`` | ``width`` | ``cells`` | ``arrangement``
    kind: str
    #: One sentence for the architect. Names the numbers, not the internals.
    message: str
    #: What to change. Empty for ``arrangement``, where this module cannot say.
    action: str = ""
    #: True when arithmetic proves no layout exists. False for ``arrangement``.
    proved: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "storeyIndex": self.storey_index,
            "kind": self.kind,
            "message": self.message,
            "action": self.action,
            "proved": self.proved,
        }


def _m2(cells: int, module_mm: int) -> float:
    return cells * module_mm * module_mm / 1_000_000.0


def diagnose_storey(
    *,
    storey_index: int,
    rooms: Sequence[Any],
    cols: int,
    rows: int,
    net_cap_cells: int,
    module_mm: int,
) -> StoreyShortfall:
    """Why this storey did not tile. Always returns something; never raises.

    ``rooms`` are :class:`~services.solver.stage_a.RoomBounds`. Only three of their
    fields are read — ``min_area_cells``, ``min_side_cells`` and the room's key — so
    this stays usable from a test without building a whole Stage A problem.
    """
    if not rooms:
        return StoreyShortfall(
            storey_index=storey_index,
            kind="arrangement",
            message="This floor has no rooms assigned to it.",
            action="Move a room onto this floor, or reduce the number of floors.",
        )

    # 1. Area. The one an architect can act on immediately, and the only one that
    #    stays true however clever the tiler gets.
    demand = sum(int(getattr(room, "min_area_cells", 0)) for room in rooms)
    with_circulation = int(demand * (1.0 + CIRCULATION_ALLOWANCE))
    capacity = int(net_cap_cells) if net_cap_cells > 0 else cols * rows
    if with_circulation > capacity:
        short = _m2(with_circulation - capacity, module_mm)
        return StoreyShortfall(
            storey_index=storey_index,
            kind="area",
            proved=True,
            message=(
                "The rooms on this floor need about %.1f m² once circulation is allowed "
                "for, and the buildable area after setbacks is %.1f m² — about %.1f m² "
                "short." % (_m2(with_circulation, module_mm), _m2(capacity, module_mm), short)
            ),
            action=(
                "Add a floor, move a room upstairs, or reduce a room's minimum size. "
                "Relaxing a setback needs the authority's consent, not a design change."
            ),
        )

    # 2. Width. A room wider than the envelope can never be placed, whatever the area
    #    arithmetic says — and it is invisible in an area total.
    widest = max(cols, rows)
    for room in rooms:
        side = int(getattr(room, "min_side_cells", 0))
        if side > widest:
            key = str(getattr(room, "key", "a room"))
            return StoreyShortfall(
                storey_index=storey_index,
                kind="width",
                proved=True,
                message=(
                    "%s needs at least %d mm across, and the buildable area is only "
                    "%d mm at its widest."
                    % (key.replace("_", " "), side * module_mm, widest * module_mm)
                ),
                action="Reduce that room's minimum width, or widen the plot's buildable area.",
            )

    # 3. Cells. Fewer cells than rooms is arithmetic, not tiling.
    if len(rooms) > capacity:
        return StoreyShortfall(
            storey_index=storey_index,
            kind="cells",
            proved=True,
            message=(
                "There are %d rooms on this floor and only %d usable cells to place "
                "them in." % (len(rooms), capacity)
            ),
            action="Move rooms to another floor, or reduce the room count.",
        )

    # 4. Nothing arithmetic rules it out. Say exactly that rather than guess.
    return StoreyShortfall(
        storey_index=storey_index,
        kind="arrangement",
        proved=False,
        message=(
            "The rooms fit this floor by area (%.1f m² needed, %.1f m² available), but "
            "no arrangement satisfied every constraint at once."
            % (_m2(with_circulation, module_mm), _m2(capacity, module_mm))
        ),
        action=(
            "Loosening one thing usually unlocks it: a must-face in the brief, a room's "
            "minimum width, or an adjacency you asked for."
        ),
    )


def shortfall_banner(shortfalls: Sequence[StoreyShortfall]) -> str | None:
    """One line for the Options screen, from every storey's diagnosis.

    A proved cause wins over an unproved one: "you are 8 m² short" is worth saying even
    when another floor merely failed to arrange, because it is the one the architect can
    act on with certainty.
    """
    if not shortfalls:
        return None
    proved = [item for item in shortfalls if item.proved]
    chosen = proved[0] if proved else shortfalls[0]
    return "%s %s" % (chosen.message, chosen.action) if chosen.action else chosen.message
