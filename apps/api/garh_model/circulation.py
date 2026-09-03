"""Door-graph reachability — can a person actually get to every room?

Pure geometry over a folded :class:`HouseModel`: rooms are nodes, every passable
opening (a door, or any opening kind that is not a window/ventilator) is an edge
between the rooms on either side of its host wall, and the outside is a node too.
Ground storeys are entered from OUTSIDE through the main door; upper storeys are
entered by the staircase (the room a stair stands in, or any room typed
``staircase``).

Why this exists: the solver's first library plan had a front door opening into a
dead-end vestibule, seven of eight ground-floor rooms unreachable from the entrance
and a kitchen entered only through the bath — and no loaded rule looks at doors, so
the compliance report was green. A green report and an unlivable plan is exactly
the class of failure this repository keeps finding; this module makes the gate
exist. Integer millimetres throughout, exact point-in-polygon, no tolerance guesses.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .geometry import Seg, point_along_seg, point_in_polygon, pt, seg_normal_offset
from .model import HouseModel, Room, Wall

OUTSIDE = "outside"
#: Opening kinds a person walks through. Anything else is light or air.
PASSABLE_KINDS: frozenset[str] = frozenset({"door", "opening", "arch", "archway", "sliding"})
#: Rooms nobody should have to cross to reach a habitable room.
TRANSIT_FORBIDDEN_TYPES: frozenset[str] = frozenset({"bath", "wc", "bath_wc", "toilet", "powder"})
#: Rooms that are reached only THROUGH a bedroom by design (the en-suite pattern).
BEDROOM_PRIVATE_TYPES: frozenset[str] = frozenset(
    {"bath", "wc", "bath_wc", "toilet", "dress", "balcony"}
)
#: Service voids: not places, so unreachable is fine.
VOID_TYPES: frozenset[str] = frozenset({"shaft", "duct", "void", "lift", "chimney"})
#: How far past the wall face a probe lands: half the wall plus a hair, so it sits
#: inside the CLEAR room polygon on that side (rooms are inset by half thickness).
PROBE_PAST_FACE_MM = 20


@dataclass(frozen=True)
class DoorEdge:
    opening_id: str
    wall_id: str
    a: str  # room id, OUTSIDE, or "void:<wall_id>" when the far side is no room
    b: str


@dataclass
class StoreyReachability:
    storey_id: str
    root: str
    reachable: set[str] = field(default_factory=set)
    unreachable: list[str] = field(default_factory=list)  # room ids, sorted
    only_via_bath: list[str] = field(
        default_factory=list
    )  # habitable rooms whose every path crosses a bath
    edges: list[DoorEdge] = field(default_factory=list)


def _rooms_by_storey(house: HouseModel) -> dict[str, list[Room]]:
    out: dict[str, list[Room]] = {}
    for room in house.rooms:
        out.setdefault(room.storey_id, []).append(room)
    return out


def _room_at(rooms: Iterable[Room], x: int, y: int) -> Room | None:
    p = pt(x, y)
    for room in rooms:
        if point_in_polygon(p, list(room.polygon)) != "outside":
            return room
    return None


def door_edges(house: HouseModel, storey_id: str) -> list[DoorEdge]:
    """Every passable opening on the storey as an edge between the rooms it joins."""
    walls: Mapping[str, Wall] = {w.id: w for w in house.walls if w.storey_id == storey_id}
    rooms = _rooms_by_storey(house).get(storey_id, [])
    edges: list[DoorEdge] = []
    for opening in sorted(house.openings, key=lambda o: o.id):
        if opening.kind not in PASSABLE_KINDS:
            continue
        wall = walls.get(opening.wall_id)
        if wall is None:
            continue
        seg = Seg(wall.a, wall.b)
        centre = point_along_seg(seg, opening.offset_mm)
        reach = wall.thickness_mm // 2 + PROBE_PAST_FACE_MM
        normal = seg_normal_offset(seg, reach)
        sides = []
        for sign in (1, -1):
            probe = _room_at(rooms, centre.x + sign * normal.x, centre.y + sign * normal.y)
            if probe is not None:
                sides.append(probe.id)
            elif wall.kind == "external":
                sides.append(OUTSIDE)
            else:
                sides.append("void:%s" % wall.id)
        edges.append(DoorEdge(opening_id=opening.id, wall_id=wall.id, a=sides[0], b=sides[1]))
    return edges


def _stair_rooms(house: HouseModel, storey_id: str, rooms: list[Room]) -> set[str]:
    found = {r.id for r in rooms if r.type == "staircase"}
    for stair in house.stairs:
        if stair.storey_id != storey_id:
            continue
        room = _room_at(rooms, stair.origin.x, stair.origin.y)
        if room is not None:
            found.add(room.id)
    return found


def storey_reachability(house: HouseModel, storey_id: str) -> StoreyReachability:
    """BFS from the storey's entrance over passable openings."""
    storeys = list(house.storeys)
    index = next((i for i, s in enumerate(storeys) if s.id == storey_id), 0)
    rooms = _rooms_by_storey(house).get(storey_id, [])
    edges = door_edges(house, storey_id)
    graph: dict[str, set[str]] = {}
    for e in edges:
        graph.setdefault(e.a, set()).add(e.b)
        graph.setdefault(e.b, set()).add(e.a)
    by_id = {r.id: r for r in rooms}
    if index == 0:
        roots = {OUTSIDE}
        root_label = OUTSIDE
    else:
        roots = _stair_rooms(house, storey_id, rooms)
        root_label = "staircase"
    result = StoreyReachability(storey_id=storey_id, root=root_label, edges=edges)

    def bfs(start: set[str], forbid_transit: bool) -> set[str]:
        seen = set(start)
        queue = deque(start)
        while queue:
            node = queue.popleft()
            room = by_id.get(node)
            # Nobody continues THROUGH a bath; it is a destination, not a corridor.
            if forbid_transit and room is not None and room.type in TRANSIT_FORBIDDEN_TYPES:
                continue
            for nxt in graph.get(node, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return seen

    reachable = bfs(roots, forbid_transit=False)
    clean = bfs(roots, forbid_transit=True)
    result.reachable = {n for n in reachable if n in by_id}
    result.unreachable = sorted(
        r.id for r in rooms if r.id not in reachable and r.type not in VOID_TYPES
    )
    result.only_via_bath = sorted(
        r.id
        for r in rooms
        if r.id in reachable
        and r.id not in clean
        and r.type not in VOID_TYPES
        and r.type not in TRANSIT_FORBIDDEN_TYPES
    )
    return result


def house_reachability(house: HouseModel) -> dict[str, StoreyReachability]:
    return {s.id: storey_reachability(house, s.id) for s in house.storeys}


def reachability_problems(house: HouseModel) -> list[str]:
    """Human sentences, empty when every room can be walked to. One per storey issue."""
    problems: list[str] = []
    names = {r.id: (r.name or r.type or r.id) for r in house.rooms}
    for storey in house.storeys:
        res = storey_reachability(house, storey.id)
        if res.unreachable:
            problems.append(
                "%s: no door path from the %s reaches %s"
                % (
                    storey.name,
                    "entrance" if res.root == OUTSIDE else "stair",
                    ", ".join(names[i] for i in res.unreachable),
                )
            )
        if res.only_via_bath:
            problems.append(
                "%s: %s can only be reached through a bath"
                % (storey.name, ", ".join(names[i] for i in res.only_via_bath))
            )
    return problems


__all__ = [
    "OUTSIDE",
    "PASSABLE_KINDS",
    "DoorEdge",
    "StoreyReachability",
    "door_edges",
    "house_reachability",
    "reachability_problems",
    "storey_reachability",
]
