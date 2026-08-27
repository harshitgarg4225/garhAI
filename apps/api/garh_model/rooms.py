"""rooms.py — planar-subdivision room detection with ID PRESERVATION.

Mirror of ``packages/model/src/rooms.ts``.

The playbook calls this load-bearing (section 3), and it is: annotations anchor
to room ids, room locks drive solver partial re-solve, the copilot refers to
rooms by id, and the drawing set labels them. If a room id changes when a wall
moves 100mm, an architect loses their annotations. So:

1. Build a planar straight-line graph from the wall CENTRELINES for one storey:
   split every wall at every intersection and T-junction.
2. Walk half-edges into faces. Bounded faces (positive area, interior on the
   left) are room candidates; the biggest negative-area face is the outside.
3. Inset each face by HALF THE THICKNESS of the wall on each edge, so the room
   polygon is the CLEAR (inside-face) area — the number NBC checks.
4. Match every new face to the previous room set by maximum Jaccard overlap,
   greedily, one-to-one. A matched face KEEPS the old room's id, type, name,
   tags, lock and targets. An unmatched face becomes a new room with a
   DETERMINISTIC id (derived from storey + polygon), so ``replay(ops)`` is
   reproducible. A room id dies only when no face matches it at all.

ANGLE ORDERING IS EXACT (:func:`~garh_model.geometry.compare_angle_around` uses
integer cross products), so face traversal has no floating-point input and this
mirror produces the same faces as the TypeScript.

KNOWN MVP LIMITS (documented, not hidden):

* Holes are ignored: a free-standing island of walls inside a room does not
  punch a hole in that room's polygon.
* Two non-orthogonal walls whose intersection is not an integer mm point are not
  split at that crossing (the rounded point would not lie exactly on either
  centreline). MVP walls are orthogonal, so this cannot happen in practice;
  ``non_integral_crossings`` reports it if it ever does.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from .geometry import (
    Bbox,
    Pt,
    Seg,
    bbox,
    canonical_ring,
    compare_angle_around,
    compare_pt,
    cross,
    jaccard,
    offset_polygon,
    point_on_segment,
    polygon_area_mm2,
    polygon_doubled_area_mm2,
    polygon_intersection_area_mm2,
    polygon_is_closed_ring,
    polygon_key,
    pt_eq,
    pt_key,
    reverse_polygon,
    segment_intersection,
)
from .ids import derived_id_unique
from .model import Room, Wall
from .validate import MIN_ROOM_AREA_MM2

__all__ = [
    "WallLike",
    "HalfEdge",
    "HalfEdgeGraph",
    "build_half_edge_graph",
    "PlanarFace",
    "planar_faces",
    "RoomCandidate",
    "RoomCandidatesResult",
    "DEFAULT_JACCARD_THRESHOLD",
    "room_candidates",
    "RoomMatch",
    "match_rooms",
    "RoomDetectionResult",
    "detect_rooms",
]


@dataclass(frozen=True)
class WallLike:
    """Minimal wall input — anything with a centreline and a thickness works."""

    id: str
    a: Pt
    b: Pt
    thickness_mm: int


@dataclass
class HalfEdge:
    #: Index into :attr:`HalfEdgeGraph.half_edges`.
    id: int
    #: Node index of the origin.
    from_node: int
    #: Node index of the destination.
    to_node: int
    #: Index of the opposite half-edge.
    twin: int
    #: Wall this half-edge came from.
    wall_id: str
    thickness_mm: int
    #: Next half-edge of the same face (filled by the ``next`` pass).
    next: int = -1
    #: Face index (filled by :func:`planar_faces`).
    face: int = -1


@dataclass(frozen=True)
class HalfEdgeGraph:
    nodes: tuple[Pt, ...]
    half_edges: tuple[HalfEdge, ...]
    #: Outgoing half-edge ids per node, sorted counter-clockwise from +X.
    outgoing: tuple[tuple[int, ...], ...]
    #: Count of intersections that could not be split exactly (see module docs).
    non_integral_crossings: int


def _cmp_to_key(compare: Any) -> Any:
    """``functools.cmp_to_key`` without the import — keeps the comparator visible."""

    class _Key:
        __slots__ = ("obj",)

        def __init__(self, obj: Any) -> None:
            self.obj = obj

        def __lt__(self, other: Any) -> bool:
            return bool(compare(self.obj, other.obj) < 0)

    return _Key


def build_half_edge_graph(walls: Sequence[WallLike]) -> HalfEdgeGraph:
    """Build the planar graph for ONE storey's walls.

    Overlapping duplicate edges are collapsed to a single edge carrying the
    thickest wall.
    """
    segs: list[tuple[WallLike, Seg]] = []
    for w in walls:
        if pt_eq(w.a, w.b):
            continue
        segs.append((w, Seg(w.a, w.b)))

    # --- 1. split points per segment
    non_integral_crossings = 0
    split_points: list[list[Pt]] = [[s.a, s.b] for _, s in segs]
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            r = segment_intersection(segs[i][1], segs[j][1])
            if r.kind == "point" and r.point is not None:
                if not r.exact:
                    non_integral_crossings += 1
                if point_on_segment(r.point, segs[i][1]):
                    split_points[i].append(r.point)
                if point_on_segment(r.point, segs[j][1]):
                    split_points[j].append(r.point)
            elif r.kind == "collinear" and r.overlap is not None:
                for p in (r.overlap.a, r.overlap.b):
                    if point_on_segment(p, segs[i][1]):
                        split_points[i].append(p)
                    if point_on_segment(p, segs[j][1]):
                        split_points[j].append(p)

    # --- 2. nodes
    node_index: dict[str, int] = {}
    nodes: list[Pt] = []

    def node_of(p: Pt) -> int:
        k = pt_key(p)
        existing = node_index.get(k)
        if existing is not None:
            return existing
        idx = len(nodes)
        nodes.append(p)
        node_index[k] = idx
        return idx

    # --- 3. edges: consecutive split points along each segment
    edge_by_key: dict[str, tuple[int, int, str, int]] = {}
    for i in range(len(segs)):
        wall, seg = segs[i]
        ordered = _order_along(seg, split_points[i])
        for k in range(len(ordered) - 1):
            u = node_of(ordered[k])
            v = node_of(ordered[k + 1])
            if u == v:
                continue
            key = f"{u}-{v}" if u < v else f"{v}-{u}"
            prev = edge_by_key.get(key)
            if prev is None or wall.thickness_mm > prev[3]:
                edge_by_key[key] = (u, v, wall.id, wall.thickness_mm)

    # --- 4. half-edges
    half_edges: list[HalfEdge] = []
    for u, v, wall_id, thickness_mm in edge_by_key.values():
        id_a = len(half_edges)
        id_b = id_a + 1
        half_edges.append(
            HalfEdge(
                id=id_a,
                from_node=u,
                to_node=v,
                twin=id_b,
                wall_id=wall_id,
                thickness_mm=thickness_mm,
            )
        )
        half_edges.append(
            HalfEdge(
                id=id_b,
                from_node=v,
                to_node=u,
                twin=id_a,
                wall_id=wall_id,
                thickness_mm=thickness_mm,
            )
        )

    # --- 5. CCW-sorted outgoing lists
    outgoing: list[list[int]] = [[] for _ in nodes]
    for he in half_edges:
        outgoing[he.from_node].append(he.id)
    for n in range(len(outgoing)):
        origin = nodes[n]

        def compare(x: int, y: int, origin: Pt = origin) -> int:
            c = compare_angle_around(
                origin, nodes[half_edges[x].to_node], nodes[half_edges[y].to_node]
            )
            return c if c != 0 else x - y

        outgoing[n].sort(key=_cmp_to_key(compare))

    # --- 6. next pointers: at the destination, take the half-edge immediately
    # CLOCKWISE from the twin. This walks bounded faces counter-clockwise, i.e.
    # with the face interior on the left.
    position_in_outgoing: dict[int, int] = {}
    for n in range(len(outgoing)):
        for pos, he_id in enumerate(outgoing[n]):
            position_in_outgoing[he_id] = pos
    for he in half_edges:
        lst = outgoing[he.to_node]
        twin_pos = position_in_outgoing.get(he.twin)
        if twin_pos is None or len(lst) == 0:
            he.next = he.twin
            continue
        he.next = lst[(twin_pos - 1 + len(lst)) % len(lst)]

    return HalfEdgeGraph(
        nodes=tuple(nodes),
        half_edges=tuple(half_edges),
        outgoing=tuple(tuple(lst) for lst in outgoing),
        non_integral_crossings=non_integral_crossings,
    )


def _sign(v: int) -> int:
    return (v > 0) - (v < 0)


def _order_along(seg: Seg, points: Sequence[Pt]) -> list[Pt]:
    """Order points along a segment (dominant axis, from ``a`` to ``b``), deduped."""
    dx = seg.b.x - seg.a.x
    dy = seg.b.y - seg.a.y
    use_x = abs(dx) >= abs(dy)
    direction = _sign(dx) if use_x else _sign(dy)
    seen: set[str] = set()
    unique: list[Pt] = []
    for p in points:
        k = pt_key(p)
        if k in seen:
            continue
        seen.add(k)
        unique.append(p)

    def compare(p: Pt, q: Pt) -> int:
        pv = p.x if use_x else p.y
        qv = q.x if use_x else q.y
        if pv != qv:
            return pv - qv if direction >= 0 else qv - pv
        return compare_pt(p, q)

    unique.sort(key=_cmp_to_key(compare))
    return unique


# ---------------------------------------------------------------------------
# Faces
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanarFace:
    index: int
    #: Half-edge ids in traversal order.
    half_edge_ids: tuple[int, ...]
    #: Ring vertices (one per half-edge origin), before any inset.
    ring: tuple[Pt, ...]
    #: Per-edge inset distance = half the thickness of the wall on that edge.
    inset_mm: tuple[int, ...]
    #: EXACT signed doubled area: positive = bounded interior face.
    doubled_area_mm2: int
    wall_ids: tuple[str, ...]


def planar_faces(graph: HalfEdgeGraph) -> list[PlanarFace]:
    """Walk every face of the graph. Bounded faces come back with positive area."""
    half_edges = graph.half_edges
    nodes = graph.nodes
    visited: set[int] = set()
    faces: list[PlanarFace] = []
    for start in half_edges:
        if start.id in visited:
            continue
        ids: list[int] = []
        cursor = start.id
        guard = 0
        limit = len(half_edges) * 2 + 8
        while cursor not in visited and guard < limit:
            guard += 1
            visited.add(cursor)
            ids.append(cursor)
            cursor = half_edges[cursor].next
            if cursor < 0:
                break
        if len(ids) < 3:
            continue
        ring = tuple(nodes[half_edges[i].from_node] for i in ids)
        inset_mm = tuple(half_edges[i].thickness_mm // 2 for i in ids)
        wall_ids = tuple(dict.fromkeys(half_edges[i].wall_id for i in ids))
        faces.append(
            PlanarFace(
                index=len(faces),
                half_edge_ids=tuple(ids),
                ring=ring,
                inset_mm=inset_mm,
                doubled_area_mm2=polygon_doubled_area_mm2(list(ring)),
                wall_ids=wall_ids,
            )
        )
    return faces


# ---------------------------------------------------------------------------
# Ring simplification that keeps per-edge distances aligned
# ---------------------------------------------------------------------------


@dataclass
class _RingEntry:
    pt: Pt
    #: Inset for the edge leaving ``pt``.
    dist: int


def _drop_spurs(entries: list[_RingEntry]) -> list[_RingEntry]:
    """Drop out-and-back spurs (v -> w -> v) left by dangling walls."""
    lst = list(entries)
    changed = True
    while changed and len(lst) >= 3:
        changed = False
        for i in range(len(lst)):
            n = len(lst)
            a = lst[i]
            c = lst[(i + 2) % n]
            if pt_eq(a.pt, c.pt):
                a.dist = c.dist
                drop = {(i + 1) % n, (i + 2) % n}
                lst = [e for idx, e in enumerate(lst) if idx not in drop]
                changed = True
                break
    return lst


def _merge_collinear(entries: list[_RingEntry]) -> list[_RingEntry]:
    """Merge consecutive collinear edges, keeping the LARGER inset (thicker wall)."""
    lst = list(entries)
    changed = True
    while changed and len(lst) > 3:
        changed = False
        for i in range(len(lst)):
            n = len(lst)
            prev = lst[(i - 1 + n) % n]
            cur = lst[i]
            nxt = lst[(i + 1) % n]
            if cross(prev.pt, cur.pt, nxt.pt) == 0:
                prev.dist = max(prev.dist, cur.dist)
                lst = [e for idx, e in enumerate(lst) if idx != i]
                changed = True
                break
    return lst


# ---------------------------------------------------------------------------
# Room candidates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoomCandidate:
    #: Clear (inside-face) polygon, CCW, integer mm.
    polygon: tuple[Pt, ...]
    #: Area of ``polygon`` in mm^2.
    area_mm2: int
    #: Walls bounding this room.
    wall_ids: tuple[str, ...]
    #: True when the inset failed and the centreline face was used instead.
    inset_failed: bool


#: Minimum Jaccard overlap to consider a face "the same room".
DEFAULT_JACCARD_THRESHOLD = 0.3


@dataclass(frozen=True)
class RoomCandidatesResult:
    candidates: tuple[RoomCandidate, ...]
    #: Outer boundary of the wall network, offset OUTWARD by half thickness — the
    #: per-storey slab footprint. ``None`` when the walls do not enclose anything.
    outline: tuple[Pt, ...] | None
    non_integral_crossings: int


def room_candidates(
    walls: Sequence[WallLike],
    min_room_area_mm2: int | None = None,
) -> RoomCandidatesResult:
    """Faces of the wall network for one storey, inset to clear polygons.

    Deterministic: candidates are returned sorted by ``polygon_key``.
    """
    min_area = MIN_ROOM_AREA_MM2 if min_room_area_mm2 is None else min_room_area_mm2
    graph = build_half_edge_graph(walls)
    faces = planar_faces(graph)

    candidates: list[RoomCandidate] = []
    outer_face: PlanarFace | None = None

    for face in faces:
        if face.doubled_area_mm2 <= 0:
            # negative-area faces are outside the walls; the largest is THE outside
            if outer_face is None or abs(face.doubled_area_mm2) > abs(outer_face.doubled_area_mm2):
                outer_face = face
            continue
        entries = [_RingEntry(pt=p, dist=face.inset_mm[i]) for i, p in enumerate(face.ring)]
        simplified = _merge_collinear(_drop_spurs(entries))
        if len(simplified) < 3:
            continue
        ring = [e.pt for e in simplified]
        if not polygon_is_closed_ring(ring):
            continue
        inset = offset_polygon(ring, [e.dist for e in simplified])
        polygon = canonical_ring(ring if inset is None else inset)
        if len(polygon) < 3:
            continue
        area_mm2 = polygon_area_mm2(polygon)
        if area_mm2 < min_area:
            continue
        candidates.append(
            RoomCandidate(
                polygon=tuple(polygon),
                area_mm2=area_mm2,
                wall_ids=tuple(sorted(face.wall_ids)),
                inset_failed=inset is None,
            )
        )

    candidates.sort(key=lambda c: polygon_key(list(c.polygon)))

    outline: tuple[Pt, ...] | None = None
    if outer_face is not None:
        entries = [
            _RingEntry(pt=p, dist=outer_face.inset_mm[i]) for i, p in enumerate(outer_face.ring)
        ]
        simplified = _merge_collinear(_drop_spurs(entries))
        if len(simplified) >= 3:
            # The outer face is CW; reverse to CCW and offset OUTWARD (negative inset)
            ccw = reverse_polygon([e.pt for e in simplified])
            dists = _reverse_edge_aligned([-e.dist for e in simplified])
            grown = offset_polygon(ccw, dists)
            ring_out = canonical_ring(ccw if grown is None else grown)
            outline = tuple(ring_out) if len(ring_out) >= 3 else None

    return RoomCandidatesResult(
        candidates=tuple(candidates),
        outline=outline,
        non_integral_crossings=graph.non_integral_crossings,
    )


def _reverse_edge_aligned(distances: Sequence[int]) -> list[int]:
    """Edge distances follow the ring when it is reversed: edge i -> edge n-2-i."""
    n = len(distances)
    return [distances[(n - 2 - i) % n] for i in range(n)]


# ---------------------------------------------------------------------------
# Matching (this is the part that keeps ids alive)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoomMatch:
    candidate_index: int
    #: Existing room whose id the candidate inherits, or ``None`` for a new room.
    room_id: str | None
    jaccard: float


def match_rooms(
    candidates: Sequence[RoomCandidate],
    existing: Sequence[Room],
    jaccard_threshold: float | None = None,
) -> list[RoomMatch]:
    """Greedy maximum-Jaccard one-to-one matching between new faces and rooms.

    DETERMINISTIC TIE-BREAKING, in order: higher Jaccard, larger intersection
    area, SMALLER EXISTING POLYGON KEY, lower candidate index, lower id.

    The polygon-key tie-break is not cosmetic. A symmetric split (one room
    becomes two equal halves) and the merge that undoes it both produce exact
    ties, and the two directions must agree or an undo silently renames a room:
    on a split, candidates are already sorted by polygon key, so the spatially
    first half inherits the id; breaking the merge tie the same way sends the id
    back to the same room. Breaking it on the id instead would pick an arbitrary
    winner and make ``wall.delete`` + undo lossy.
    """
    threshold = DEFAULT_JACCARD_THRESHOLD if jaccard_threshold is None else jaccard_threshold
    existing_keys = [polygon_key(list(r.polygon)) for r in existing]
    pairs: list[tuple[int, int, float, int]] = []  # (ci, ei, jaccard, intersection)
    for ci, cand in enumerate(candidates):
        cb: Bbox = bbox(list(cand.polygon))
        for ei, room in enumerate(existing):
            if len(room.polygon) < 3:
                continue
            rb = bbox(list(room.polygon))
            if (
                cb.max_x < rb.min_x
                or rb.max_x < cb.min_x
                or cb.max_y < rb.min_y
                or rb.max_y < cb.min_y
            ):
                continue
            j = jaccard(list(cand.polygon), list(room.polygon))
            if j < threshold:
                continue
            pairs.append(
                (ci, ei, j, polygon_intersection_area_mm2(list(cand.polygon), list(room.polygon)))
            )

    pairs.sort(key=lambda p: (-p[2], -p[3], existing_keys[p[1]], p[0], existing[p[1]].id))

    taken_candidates: set[int] = set()
    taken_existing: set[int] = set()
    matched: dict[int, tuple[int, int, float, int]] = {}
    for pair in pairs:
        if pair[0] in taken_candidates or pair[1] in taken_existing:
            continue
        taken_candidates.add(pair[0])
        taken_existing.add(pair[1])
        matched[pair[0]] = pair

    out: list[RoomMatch] = []
    for ci in range(len(candidates)):
        pair_opt = matched.get(ci)
        out.append(
            RoomMatch(
                candidate_index=ci,
                room_id=existing[pair_opt[1]].id if pair_opt is not None else None,
                jaccard=pair_opt[2] if pair_opt is not None else 0.0,
            )
        )
    return out


# ---------------------------------------------------------------------------
# detect_rooms — the function fold() calls
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoomDetectionResult:
    #: The new room set for this storey (ids preserved where possible).
    rooms: tuple[Room, ...]
    #: Per-storey slab footprint (outer wall face).
    outline: tuple[Pt, ...] | None
    candidates: tuple[RoomCandidate, ...]
    matches: tuple[RoomMatch, ...]
    #: Ids of rooms that genuinely disappeared (nothing matched them).
    removed_room_ids: tuple[str, ...]
    non_integral_crossings: int


def detect_rooms(
    walls: Sequence[Wall],
    storey_id: str,
    existing_rooms: Sequence[Room],
    taken_ids: set[str] | None = None,
    min_room_area_mm2: int | None = None,
    jaccard_threshold: float | None = None,
) -> RoomDetectionResult:
    """Recompute the rooms of ONE storey from its walls, preserving ids.

    :param walls: walls of this storey (others are ignored defensively)
    :param storey_id: the storey being recomputed
    :param existing_rooms: rooms currently recorded for this storey
    :param taken_ids: every id already in the document, so derived ids stay unique
    """
    mine = [
        WallLike(id=w.id, a=w.a, b=w.b, thickness_mm=w.thickness_mm)
        for w in walls
        if w.storey_id == storey_id
    ]
    prior_rooms = [r for r in existing_rooms if r.storey_id == storey_id]
    result = room_candidates(mine, min_room_area_mm2)
    matches = match_rooms(result.candidates, prior_rooms, jaccard_threshold)
    prior_by_id = {r.id: r for r in prior_rooms}

    used: set[str] = set(taken_ids or set())
    rooms: list[Room] = []
    matched_ids: set[str] = set()

    for match in matches:
        cand = result.candidates[match.candidate_index]
        prior = None if match.room_id is None else prior_by_id.get(match.room_id)
        if prior is not None:
            matched_ids.add(prior.id)
            used.add(prior.id)
            rooms.append(replace(prior, polygon=cand.polygon, area_mm2=cand.area_mm2))
        else:
            room_id = derived_id_unique(
                "room", f"{storey_id}|{polygon_key(list(cand.polygon))}", used
            )
            used.add(room_id)
            rooms.append(
                Room(
                    id=room_id,
                    storey_id=storey_id,
                    type="unassigned",
                    name="",
                    polygon=cand.polygon,
                    area_mm2=cand.area_mm2,
                    tags=(),
                    locked=False,
                    target_area_mm2=None,
                    must_face=None,
                )
            )

    removed_room_ids = tuple(r.id for r in prior_rooms if r.id not in matched_ids)

    return RoomDetectionResult(
        rooms=tuple(rooms),
        outline=result.outline,
        candidates=result.candidates,
        matches=tuple(matches),
        removed_room_ids=removed_room_ids,
        non_integral_crossings=result.non_integral_crossings,
    )
