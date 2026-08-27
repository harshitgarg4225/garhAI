"""§7 step 3 — inner dims: one width and one depth chain per room.

    3. **Inner dims:** per room, one width + one depth chain along the room's principal
       axes, placed near the door-side wall inner face; skip if duplicate of an adjacent
       chain (same value, shared wall).

Two judgements are encoded here.

**"Near the door-side wall inner face"** is a real drafting habit, not decoration: you
enter a room, and the dimension you look for is the one on the wall you came through,
because that is the wall you set out from. So the width chain hugs the door's wall when
the door is in a horizontal wall, and the depth chain hugs it when the door is in a
vertical one; the other chain falls back to the south / west face, the plan's origin
corner. A room with no door of its own (a passage, a stair well) uses the fallback for
both — recorded, so the sheet note can say so.

**The duplicate skip needs adjacency, not just equality.** Two identical bedrooms in
opposite corners of a plan produce identical width chains, and both are wanted — each
room needs its own number near it. The chain worth suppressing is the one an architect
would call redundant: the same span, measured again immediately across a shared wall.
So a chain is dropped only when an already-kept chain has the same span *and* the two
rooms touch across a wall. Everything else is kept, and every suppression is reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.drawings.autodim.chains import (
    KIND_INNER,
    DimChainInfo,
    chain_from_breakpoints,
)
from services.drawings.autodim.config import DEFAULT_CONFIG, AutoDimConfig
from services.drawings.autodim.extract import (
    HORIZONTAL,
    SIDES,
    VERTICAL,
    OpeningRef,
    RoomRef,
    StoreyPlan,
)

#: Slack when matching a wall face to a room edge. ``garh_model`` insets room faces by
#: ``thickness_mm // 2``, so an odd-thickness wall lands 1mm off its own arithmetic;
#: 2mm of tolerance absorbs that without ever matching the wrong wall (the next
#: candidate is at least 100mm away).
FACE_MATCH_TOLERANCE_MM = 2

#: Inner chains are level 4 — ``DimChain.level`` allows 1..4 and §7 uses 1..3 for the
#: outer chains, so the room chains take the fourth slot. Placement order follows the
#: level, which is what makes outer chains win a contested slot over inner ones.
INNER_LEVEL = 4

WIDTH = "W"
DEPTH = "D"

SUPPRESSED_DUPLICATE = "duplicate-across-shared-wall"


@dataclass(frozen=True)
class SuppressedChain:
    """A chain step 3 deliberately did not emit. Reported, never silent."""

    chain_id: str
    room_id: str
    axis: str
    reason: str
    duplicate_of: str | None = None
    value_mm: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "chainId": self.chain_id,
            "roomId": self.room_id,
            "axis": self.axis,
            "reason": self.reason,
            "duplicateOf": self.duplicate_of,
            "valueMm": self.value_mm,
        }


def _chain_id(storey_id: str, room_id: str, axis: str) -> str:
    return "dim.%s.room.%s.%s" % (storey_id, room_id, axis)


def door_side_of_room(plan: StoreyPlan, room: RoomRef) -> tuple[str | None, OpeningRef | None]:
    """Which side of the room its primary door is in, and the door.

    A door belongs to a room's side when its host wall's *inner face* coincides with
    that room edge and the door's centre lies within the room's span. Ties (a room with
    two doors) resolve in ``SIDES`` order then by opening id — arbitrary, but fixed, and
    a fixed arbitrary choice is what determinism needs.
    """
    best_side: str | None = None
    best_door: OpeningRef | None = None
    best_rank = (len(SIDES), "")

    for opening in plan.openings:
        if opening.kind != "door":
            continue
        wall = plan.wall_by_id(opening.wall_id)
        if wall is None:
            continue
        side: str | None = None
        if wall.orientation == HORIZONTAL:
            if abs(wall.face_hi_mm - room.min_y_mm) <= FACE_MATCH_TOLERANCE_MM:
                side = "S"
            elif abs(wall.face_lo_mm - room.max_y_mm) <= FACE_MATCH_TOLERANCE_MM:
                side = "N"
            if side and not (room.min_x_mm <= opening.centre_mm <= room.max_x_mm):
                side = None
        else:
            if abs(wall.face_hi_mm - room.min_x_mm) <= FACE_MATCH_TOLERANCE_MM:
                side = "W"
            elif abs(wall.face_lo_mm - room.max_x_mm) <= FACE_MATCH_TOLERANCE_MM:
                side = "E"
            if side and not (room.min_y_mm <= opening.centre_mm <= room.max_y_mm):
                side = None
        if side is None:
            continue
        rank = (SIDES.index(side), opening.id)
        if rank < best_rank:
            best_rank, best_side, best_door = rank, side, opening

    return best_side, best_door


def _rooms_adjacent(a: RoomRef, b: RoomRef, orientation: str, max_gap_mm: int) -> bool:
    """Do these two rooms share a wall, across the axis perpendicular to the chain?

    For a width chain (measured along x) the shared wall is horizontal, so the rooms are
    stacked in y: their y-gap is at most one wall thick and their x-spans overlap.
    """
    if orientation == HORIZONTAL:
        gap = max(a.min_y_mm, b.min_y_mm) - min(a.max_y_mm, b.max_y_mm)
        overlap = min(a.max_x_mm, b.max_x_mm) - max(a.min_x_mm, b.min_x_mm)
    else:
        gap = max(a.min_x_mm, b.min_x_mm) - min(a.max_x_mm, b.max_x_mm)
        overlap = min(a.max_y_mm, b.max_y_mm) - max(a.min_y_mm, b.min_y_mm)
    return 0 <= gap <= max_gap_mm and overlap > 0


def build_inner_chains(
    plan: StoreyPlan, config: AutoDimConfig = DEFAULT_CONFIG
) -> tuple[tuple[DimChainInfo, ...], tuple[SuppressedChain, ...]]:
    """§7 step 3. Returns ``(chains, suppressed)``, both deterministic.

    Rooms are visited in the plan's reading order (south-west first — see
    ``extract.collect_rooms``), which decides who keeps a shared-wall duplicate: the
    room nearer the plan origin.
    """
    if not plan.rooms:
        return (), ()

    thickest = max((w.thickness_mm for w in plan.walls), default=0)
    adjacency_gap = thickest + FACE_MATCH_TOLERANCE_MM
    offset = config.offset_for_level(INNER_LEVEL)

    chains: list[DimChainInfo] = []
    suppressed: list[SuppressedChain] = []
    # (orientation, origin, overall) -> (chain id, room)
    seen: dict[tuple[str, int, int], tuple[str, RoomRef]] = {}

    for room in plan.rooms:
        if room.width_mm <= 0 or room.depth_mm <= 0:
            continue
        door_side, _door = door_side_of_room(plan, room)

        for axis in (WIDTH, DEPTH):
            if axis == WIDTH:
                orientation = HORIZONTAL
                breakpoints = (room.min_x_mm, room.max_x_mm)
                # Hug the door wall when it is one of the horizontal pair, else the
                # south face — the plan's origin edge.
                near_high = door_side == "N"
                reference = room.max_y_mm if near_high else room.min_y_mm
                outward = -1 if near_high else +1
            else:
                orientation = VERTICAL
                breakpoints = (room.min_y_mm, room.max_y_mm)
                near_high = door_side == "E"
                reference = room.max_x_mm if near_high else room.min_x_mm
                outward = -1 if near_high else +1

            value = breakpoints[1] - breakpoints[0]
            key = (orientation, breakpoints[0], value)
            chain_id = _chain_id(plan.storey_id, room.id, axis)

            previous = seen.get(key)
            if previous is not None and _rooms_adjacent(
                previous[1], room, orientation, adjacency_gap
            ):
                suppressed.append(
                    SuppressedChain(
                        chain_id=chain_id,
                        room_id=room.id,
                        axis=axis,
                        reason=SUPPRESSED_DUPLICATE,
                        duplicate_of=previous[0],
                        value_mm=value,
                    )
                )
                continue

            chain = chain_from_breakpoints(
                chain_id=chain_id,
                orientation=orientation,
                level=INNER_LEVEL,
                offset_mm=offset,
                breakpoints=breakpoints,
                # Inward: the chain sits *inside* the room, off the reference face.
                line_mm=reference + outward * offset,
                reference_mm=reference,
                outward=outward,
                kind=KIND_INNER,
                storey_id=plan.storey_id,
                room_id=room.id,
                anchors={breakpoints[1]: room.id},
            )
            if chain is None:
                continue
            chains.append(chain)
            seen.setdefault(key, (chain_id, room))

    return tuple(chains), tuple(suppressed)


__all__ = [
    "DEPTH",
    "FACE_MATCH_TOLERANCE_MM",
    "INNER_LEVEL",
    "SUPPRESSED_DUPLICATE",
    "WIDTH",
    "SuppressedChain",
    "build_inner_chains",
    "door_side_of_room",
]
