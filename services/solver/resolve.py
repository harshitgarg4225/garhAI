"""§5.7 — partial re-solve with locked rooms. Pure logic; CP-SAT stays in stages.py.

    Locked room ids → their polygons become fixed obstacles (exact geometry
    preserved); stage A solves remaining rooms in residual space; stage B re-runs but
    never touches locked walls except shared-wall dedupe (locked side wins).

The three §5.7 obligations, and where each is enforced:

* **Fixed obstacles** — :func:`mask_locked_cells` removes every coarse-grid cell whose
  centre lies inside a locked polygon, so stage A *cannot* place a residual room
  there. Same centre test as :func:`services.solver.stages.grid_envelope`, so the
  obstacle boundary and the envelope boundary quantise identically.
* **Locked walls survive byte-identical** — :func:`merge_walls_locked_wins` returns
  the locked walls as the *same objects* it was given (asserted by
  :func:`locked_walls_untouched`), and trims the freshly-synthesised walls where they
  overlap a locked one. A stage-B output that would have modified a locked wall is
  discarded as a candidate, never repaired — a silently moved locked wall is exactly
  the betrayal a lock exists to prevent.
* **Locked ids byte-preserved in output** — the raw ``lockedRooms`` payload entries
  are carried through :class:`ResolveOutcome` untouched (the very same dicts, never
  re-parsed and re-serialised), and locked rooms re-join every option's placements
  with their ``room_id`` set, so scoring and diversity see the whole plan.

Diff-matching for the **unlocked** rooms reuses the model core's Jaccard matcher
(``garh_model.geometry.jaccard`` — the same primitive room re-detection uses, §3), so
the "Bedroom 2 moved" story the diff UI tells is computed by the same overlap maths
that keeps room ids alive during manual edits.

Budget: a partial re-solve must feel like an edit, not a generate — ≤15s of solve
across all candidates (:data:`RESOLVE_BUDGET_SECONDS`), enforced by capping candidates
at :data:`RESOLVE_MAX_CANDIDATES` and dividing the budget among them.

Everything here is stdlib + integer geometry: fully testable on Python 3.9 with fake
cell layouts and fake stage-B wall lists, no OR-Tools anywhere in the import graph.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from services.common.errors import InvalidJobError
from services.common.logging import get_logger
from services.solver.geometry import (
    Polygon,
    Pt,
    as_polygon,
    bbox,
    point_in_polygon,
)
from services.solver.pipeline import (
    SolveContext,
    SolveResult,
    SolverProfile,
    run_solver,
)
from services.solver.stages import GridSpec
from services.solver.types import FINE_MODULE_MM, RoomPlacement, SolveParams

log = get_logger("solver.resolve")

#: §5.7: the whole partial re-solve gets at most this much CP-SAT time.
RESOLVE_BUDGET_SECONDS = 15
#: Fewer anchors than a full generate: the stair is usually locked or already placed.
RESOLVE_MAX_CANDIDATES = 3
#: A trimmed wall fragment shorter than one brick module is construction noise.
MIN_WALL_FRAGMENT_MM = FINE_MODULE_MM

#: Crockford base32 — the ULID alphabet element ids use ({type}_{26 chars}).
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


# ---------------------------------------------------------------------------
# Payload parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LockedWall:
    """A wall bounding a locked room. Stage B must return it untouched."""

    id: str
    a: Pt
    b: Pt
    thickness_mm: int
    kind: str
    storey_index: int = 0


@dataclass(frozen=True)
class LockedRoom:
    """One locked room: exact geometry in, exact geometry out (§5.7)."""

    id: str
    storey_index: int
    room_type: str
    polygon: Polygon
    name: str | None = None
    walls: tuple[LockedWall, ...] = ()
    #: The raw payload entry, kept verbatim so the output is *byte*-preserved —
    #: the same mapping object goes back out, never a re-serialisation.
    raw: Mapping[str, Any] | None = None

    def bounding_placement(self) -> RoomPlacement:
        """The locked room as a placement rect, ``room_id`` preserved.

        Placements are axis-aligned rects (stage A's vocabulary); a locked room
        re-joins scoring and the diversity signature through its bounding box. The
        exact polygon still travels via :attr:`raw` — this rect is for scoring
        only, never for output geometry.
        """
        min_x, min_y, max_x, max_y = bbox(self.polygon)
        return RoomPlacement(
            room_key="locked:%s" % self.id,
            room_type=self.room_type,
            storey_index=self.storey_index,
            x_mm=min_x,
            y_mm=min_y,
            width_mm=max(1, max_x - min_x),
            depth_mm=max(1, max_y - min_y),
            room_id=self.id,
        )


def parse_locked_rooms(payload: Mapping[str, Any]) -> tuple[LockedRoom, ...]:
    """Read ``payload["lockedRooms"]`` and cross-check ``payload["lockedRoomIds"]``.

    Every locked id must arrive with geometry: a lock without a polygon cannot be an
    obstacle, and guessing would violate the one promise a lock makes. Failures name
    the field (golden rule 9).
    """
    raw_rooms = payload.get("lockedRooms") or []
    if not isinstance(raw_rooms, list):
        raise InvalidJobError(
            "The locked rooms for this re-solve could not be read.",
            detail="lockedRooms must be a list, got %s" % type(raw_rooms).__name__,
        )
    rooms: list[LockedRoom] = []
    for index, entry in enumerate(raw_rooms):
        if not isinstance(entry, Mapping):
            raise InvalidJobError(
                "The locked rooms for this re-solve could not be read.",
                detail="lockedRooms[%d] is %r" % (index, entry),
            )
        room_id = entry.get("id")
        if not isinstance(room_id, str) or not room_id:
            raise InvalidJobError(
                "A locked room is missing its id.",
                detail="lockedRooms[%d].id=%r" % (index, room_id),
            )
        try:
            polygon = as_polygon(_points(entry.get("polygon"), "lockedRooms[%d].polygon" % index))
        except ValueError as exc:
            raise InvalidJobError(
                "A locked room's shape could not be read.",
                detail="lockedRooms[%d]: %s" % (index, exc),
            ) from exc
        if len(polygon) < 3:
            raise InvalidJobError(
                "A locked room's shape could not be read.",
                detail="lockedRooms[%d].polygon has %d points" % (index, len(polygon)),
            )
        rooms.append(
            LockedRoom(
                id=room_id,
                storey_index=_index(
                    entry.get("storeyIndex", 0), "lockedRooms[%d].storeyIndex" % index
                ),
                room_type=str(entry.get("type") or entry.get("roomType") or "unassigned"),
                polygon=polygon,
                name=str(entry["name"]) if isinstance(entry.get("name"), str) else None,
                walls=_parse_walls(entry.get("walls"), index),
                raw=entry,
            )
        )

    declared = [str(item) for item in payload.get("lockedRoomIds") or [] if isinstance(item, str)]
    parsed_ids = {room.id for room in rooms}
    missing = [room_id for room_id in declared if room_id not in parsed_ids]
    if missing:
        raise InvalidJobError(
            "Some locked rooms arrived without their geometry.",
            action="Re-run the re-solve from the app.",
            detail="lockedRoomIds without lockedRooms entries: %s" % ", ".join(missing),
        )
    return tuple(rooms)


def parse_previous_rooms(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """``payload["previousRooms"]``: the pre-solve room set the diff is told against."""
    raw = payload.get("previousRooms") or []
    if not isinstance(raw, list):
        return ()
    return tuple(entry for entry in raw if isinstance(entry, Mapping) and entry.get("id"))


def _parse_walls(raw: Any, room_index: int) -> tuple[LockedWall, ...]:
    if not raw:
        return ()
    if not isinstance(raw, list):
        raise InvalidJobError(
            "A locked room's walls could not be read.",
            detail="lockedRooms[%d].walls is %s" % (room_index, type(raw).__name__),
        )
    walls: list[LockedWall] = []
    for windex, entry in enumerate(raw):
        where = "lockedRooms[%d].walls[%d]" % (room_index, windex)
        if not isinstance(entry, Mapping) or not isinstance(entry.get("id"), str):
            raise InvalidJobError(
                "A locked room's walls could not be read.", detail="%s=%r" % (where, entry)
            )
        walls.append(
            LockedWall(
                id=entry["id"],
                a=_point(entry.get("a"), where + ".a"),
                b=_point(entry.get("b"), where + ".b"),
                thickness_mm=_index(entry.get("thicknessMm", 115), where + ".thicknessMm"),
                kind=str(entry.get("kind") or "internal"),
                storey_index=_index(entry.get("storeyIndex", 0), where + ".storeyIndex"),
            )
        )
    return tuple(walls)


def _points(raw: Any, where: str) -> list[tuple[int, int]]:
    if not isinstance(raw, list):
        raise InvalidJobError(
            "A locked room's shape could not be read.",
            detail="%s must be a list of points" % where,
        )
    out: list[tuple[int, int]] = []
    for index, item in enumerate(raw):
        out.append(_point(item, "%s[%d]" % (where, index)))
    return out


def _point(raw: Any, where: str) -> Pt:
    if isinstance(raw, Mapping):
        x, y = raw.get("x"), raw.get("y")
    elif isinstance(raw, list | tuple) and len(raw) == 2:
        x, y = raw[0], raw[1]
    else:
        raise InvalidJobError("A point could not be read.", detail="%s=%r" % (where, raw))
    if (
        isinstance(x, bool)
        or isinstance(y, bool)
        or not isinstance(x, int)
        or not isinstance(y, int)
    ):
        raise InvalidJobError(
            "Geometry must be integer millimetres.", detail="%s=(%r, %r)" % (where, x, y)
        )
    return (x, y)


def _index(raw: Any, where: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise InvalidJobError(
            "This re-solve request could not be read.",
            detail="%s must be a non-negative integer, got %r" % (where, raw),
        )
    return raw


# ---------------------------------------------------------------------------
# Fixed obstacles (§5.7: locked polygons block the residual grid)
# ---------------------------------------------------------------------------


def mask_locked_cells(
    grid: GridSpec, locked: Sequence[LockedRoom], *, storey_index: int = 0
) -> GridSpec:
    """A copy of ``grid`` with cells under a locked polygon marked unbuildable.

    A cell is blocked when its **centre** lies inside (or on the boundary of) a
    locked polygon on this storey — the same quantisation rule as
    :func:`services.solver.stages.grid_envelope`, so the obstacle cannot leak a
    half-module past the locked geometry in either direction.
    """
    polygons = [room.polygon for room in locked if room.storey_index == storey_index]
    if not polygons:
        return grid
    half = grid.module_mm // 2
    mask = tuple(
        tuple(
            cell
            and not any(
                point_in_polygon(
                    (
                        grid.origin[0] + col * grid.module_mm + half,
                        grid.origin[1] + row * grid.module_mm + half,
                    ),
                    polygon,
                )
                for polygon in polygons
            )
            for col, cell in enumerate(mask_row)
        )
        for row, mask_row in enumerate(grid.mask)
    )
    return GridSpec(
        origin=grid.origin,
        module_mm=grid.module_mm,
        cols=grid.cols,
        rows=grid.rows,
        mask=mask,
    )


def residual_params(params: SolveParams, locked: Sequence[LockedRoom]) -> SolveParams:
    """The residual program: brief rooms minus the ones the lock already answers.

    A room request is satisfied by a lock when it is flagged ``locked`` or when its
    key names a locked room id (``locked:{roomId}`` or the id itself).
    """
    locked_ids = {room.id for room in locked}
    residual = tuple(
        request
        for request in params.rooms
        if not request.locked
        and request.key not in locked_ids
        and not (request.key.startswith("locked:") and request.key[7:] in locked_ids)
    )
    return replace(
        params,
        rooms=residual,
        locked_room_ids=tuple(sorted(locked_ids)),
    )


# ---------------------------------------------------------------------------
# Shared-wall dedupe — the locked side wins (§5.7 / §5.3)
# ---------------------------------------------------------------------------


def strip_relocked_walls(
    locked_walls: Sequence[LockedWall],
    new_walls: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]] | None:
    """Remove stage-B re-emissions of locked walls; ``None`` when one was *modified*.

    Stage B synthesises the whole wall network, so it may innocently re-emit a wall
    it was handed. Re-emitted **identical** → dropped (the locked original is already
    in the output). Re-emitted with the same id but different geometry or thickness →
    stage B touched a locked wall, which §5.7 forbids: the candidate is discarded,
    never repaired, because a silently moved locked wall is the betrayal a lock
    exists to prevent.
    """
    locked_by_id = {wall.id: wall for wall in locked_walls}
    survivors: list[Mapping[str, Any]] = []
    for wall in new_walls:
        wall_id = _wall_get(wall, "id")
        locked = locked_by_id.get(wall_id) if isinstance(wall_id, str) else None
        if locked is None:
            survivors.append(wall)
            continue
        thickness = _wall_get(wall, "thicknessMm")
        if _wall_geometry(wall) != (locked.a, locked.b) or (
            thickness is not None and int(thickness) != locked.thickness_mm
        ):
            log.info(
                "resolve.locked_wall_touched",
                wall_id=wall_id,
                reason="stage B changed a locked wall's geometry or thickness",
            )
            return None
        # Identical re-emission: drop it; the locked original represents it.
    return survivors


def merge_walls_locked_wins(
    locked_walls: Sequence[Any],
    new_walls: Sequence[Mapping[str, Any]],
) -> tuple[Any, ...]:
    """Locked walls verbatim + new walls trimmed where they overlap a locked one.

    Orthogonal walls only (the MVP is orthogonal, §5/§7): a new horizontal/vertical
    wall that overlaps a *collinear* locked wall loses the overlapped interval — two
    rooms sharing an edge get ONE wall, and the one they get is the locked one. A
    trim can split a wall in two; fragments get **deterministic** derived ids (same
    input ⇒ same id, §16 goldens) and fragments shorter than one brick module are
    dropped. Diagonal walls pass through untouched.

    ``locked_walls`` entries are returned **by reference** — the caller can (and the
    tests do) check ``result[i] is locked_walls[i]``.
    """
    locked_spans = [_axis_span(_wall_geometry(wall)) for wall in locked_walls]
    out: list[Any] = list(locked_walls)

    for wall in new_walls:
        geometry = _wall_geometry(wall)
        span = _axis_span(geometry)
        if span is None:
            out.append(wall)  # diagonal: not ours to trim in an orthogonal MVP
            continue
        axis, line, lo, hi = span
        keep: list[tuple[int, int]] = [(lo, hi)]
        for locked_span in locked_spans:
            if locked_span is None:
                continue
            l_axis, l_line, l_lo, l_hi = locked_span
            if l_axis != axis or l_line != line:
                continue
            keep = _subtract_interval(keep, l_lo, l_hi)
        if len(keep) == 1 and keep[0] == (lo, hi):
            out.append(wall)  # untouched
            continue
        fragments = [
            interval for interval in keep if interval[1] - interval[0] >= MIN_WALL_FRAGMENT_MM
        ]
        if not fragments:
            log.info(
                "resolve.wall_deduped",
                wall_id=str(_wall_get(wall, "id")),
                reason="fully covered by a locked wall (locked side wins)",
            )
            continue
        for findex, (frag_lo, frag_hi) in enumerate(fragments):
            out.append(_rebuild_wall(wall, axis, line, frag_lo, frag_hi, fragment_index=findex))
    return tuple(out)


def locked_walls_untouched(locked_walls: Sequence[Any], merged: Sequence[Any]) -> bool:
    """True when every locked wall survived **identically** (same object or equal)."""
    if len(merged) < len(locked_walls):
        return False
    for expected, actual in zip(locked_walls, merged, strict=False):
        if actual is expected:
            continue
        if actual != expected:
            return False
    return True


def _wall_geometry(wall: Any) -> tuple[Pt, Pt]:
    if isinstance(wall, LockedWall):
        return (wall.a, wall.b)
    a, b = _wall_get(wall, "a"), _wall_get(wall, "b")
    return (_point(a, "wall.a"), _point(b, "wall.b"))


def _wall_get(wall: Any, key: str) -> Any:
    if isinstance(wall, Mapping):
        return wall.get(key)
    return getattr(wall, key, None)


def _axis_span(geometry: tuple[Pt, Pt]) -> tuple[str, int, int, int] | None:
    """``(axis, fixed-coordinate, lo, hi)`` for an orthogonal segment, else None."""
    (ax, ay), (bx, by) = geometry
    if ay == by and ax != bx:
        return ("h", ay, min(ax, bx), max(ax, bx))
    if ax == bx and ay != by:
        return ("v", ax, min(ay, by), max(ay, by))
    return None


def _subtract_interval(keep: Sequence[tuple[int, int]], lo: int, hi: int) -> list[tuple[int, int]]:
    """Subtract ``[lo, hi]`` from every interval. Exact integer arithmetic."""
    out: list[tuple[int, int]] = []
    for start, end in keep:
        if hi <= start or lo >= end:
            out.append((start, end))
            continue
        if start < lo:
            out.append((start, lo))
        if hi < end:
            out.append((hi, end))
    return out


def _rebuild_wall(
    wall: Mapping[str, Any], axis: str, line: int, lo: int, hi: int, *, fragment_index: int
) -> dict[str, Any]:
    """A trimmed copy of ``wall`` covering ``[lo, hi]``, with a deterministic id."""
    rebuilt = dict(wall)
    if axis == "h":
        rebuilt["a"] = {"x": lo, "y": line}
        rebuilt["b"] = {"x": hi, "y": line}
    else:
        rebuilt["a"] = {"x": line, "y": lo}
        rebuilt["b"] = {"x": line, "y": hi}
    rebuilt["id"] = _derived_wall_id(str(wall.get("id") or "wall"), lo, hi, fragment_index)
    return rebuilt


def _derived_wall_id(parent_id: str, lo: int, hi: int, fragment_index: int) -> str:
    """A valid ``wall_{26 Crockford chars}`` id, derived (not random) from the trim.

    Content-addressed, like the model core's ``derived_id``: the same parent wall
    trimmed the same way yields the same fragment id on every run, which keeps plan
    JSON goldens byte-stable across re-solves.
    """
    digest = hashlib.sha256(
        ("%s|%d|%d|%d" % (parent_id, lo, hi, fragment_index)).encode("utf-8")
    ).digest()
    body = "".join(_CROCKFORD[b % 32] for b in digest[:26])
    return "wall_%s" % body


# ---------------------------------------------------------------------------
# Diff-matching for unlocked rooms (reuses the garh_model Jaccard primitive)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoomDiff:
    """How one room of the previous plan relates to the re-solved plan."""

    #: Placement key in the new plan, or None for a removed old room.
    new_key: str | None
    #: Old room id this placement inherits the story of, or None for a new room.
    room_id: str | None
    #: kept (same footprint) | moved | new | removed.
    relation: str
    #: Jaccard overlap ×100 (integer — no floats in wire JSON).
    jaccard_x100: int = 0

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"relation": self.relation, "jaccardX100": self.jaccard_x100}
        if self.new_key is not None:
            out["newKey"] = self.new_key
        if self.room_id is not None:
            out["roomId"] = self.room_id
        return out


def _model_jaccard() -> Callable[[Sequence[Mapping[str, int]], Sequence[Mapping[str, int]]], float]:
    """The model core's Jaccard, adapted to ``[{x, y}, …]`` polygons.

    Imported lazily: ``garh_model`` ships with the API package, and the §3 contract
    is explicit that room-identity matching uses *this* primitive — re-implementing
    the overlap maths here is how the solver's diff and the editor's room
    re-detection would come to disagree about whether Bedroom 2 "moved".
    """
    try:
        from garh_model.geometry import Pt as ModelPt
        from garh_model.geometry import jaccard as model_jaccard
    except ImportError as exc:  # pragma: no cover - environment misconfiguration
        raise RuntimeError(
            "Room diff-matching needs garh_model (the model core mirror, shipped in "
            "apps/api). Install it alongside garh-services in the solver worker image."
        ) from exc

    def compute(a: Sequence[Mapping[str, int]], b: Sequence[Mapping[str, int]]) -> float:
        return model_jaccard(
            [ModelPt(int(p["x"]), int(p["y"])) for p in a],
            [ModelPt(int(p["x"]), int(p["y"])) for p in b],
        )

    return compute


#: Same threshold the model core's room matcher uses (garh_model.rooms).
DIFF_JACCARD_THRESHOLD_X100 = 30


def match_unlocked_rooms(
    previous_rooms: Sequence[Mapping[str, Any]],
    placements: Sequence[RoomPlacement],
    *,
    locked_ids: Sequence[str] = (),
) -> tuple[RoomDiff, ...]:
    """Greedy max-Jaccard matching of old unlocked rooms onto new placements.

    Mirrors ``garh_model.rooms.match_rooms`` semantics (greedy, one-to-one, best
    overlap first, deterministic tie-breaks) on top of the same Jaccard primitive.
    Locked rooms never appear here — their ids are preserved, not matched.
    """
    jaccard = _model_jaccard()
    locked = set(locked_ids)
    olds = [
        room
        for room in previous_rooms
        if str(room.get("id")) not in locked and isinstance(room.get("polygon"), list)
    ]
    news = [p for p in placements if p.room_id is None or p.room_id not in locked]

    pairs: list[tuple[int, str, int, int]] = []  # (-jx100, old_id, old_idx, new_idx)
    for new_index, placement in enumerate(news):
        rect = _placement_polygon(placement)
        for old_index, room in enumerate(olds):
            value = jaccard(room["polygon"], rect)
            jx100 = int(value * 100)
            if jx100 < DIFF_JACCARD_THRESHOLD_X100:
                continue
            pairs.append((-jx100, str(room["id"]), old_index, new_index))
    pairs.sort()

    taken_old: set[int] = set()
    taken_new: set[int] = set()
    diffs: list[RoomDiff] = []
    for negative_j, old_id, old_index, new_index in pairs:
        if old_index in taken_old or new_index in taken_new:
            continue
        taken_old.add(old_index)
        taken_new.add(new_index)
        jx100 = -negative_j
        diffs.append(
            RoomDiff(
                new_key=news[new_index].room_key,
                room_id=old_id,
                relation="kept" if jx100 >= 100 else "moved",
                jaccard_x100=jx100,
            )
        )
    for new_index, placement in enumerate(news):
        if new_index not in taken_new:
            diffs.append(RoomDiff(new_key=placement.room_key, room_id=None, relation="new"))
    for old_index, room in enumerate(olds):
        if old_index not in taken_old:
            diffs.append(RoomDiff(new_key=None, room_id=str(room["id"]), relation="removed"))
    diffs.sort(key=lambda diff: (diff.relation, diff.new_key or "", diff.room_id or ""))
    return tuple(diffs)


def _placement_polygon(placement: RoomPlacement) -> list[dict[str, int]]:
    return [
        {"x": placement.x_mm, "y": placement.y_mm},
        {"x": placement.x_mm + placement.width_mm, "y": placement.y_mm},
        {"x": placement.x_mm + placement.width_mm, "y": placement.y_mm + placement.depth_mm},
        {"x": placement.x_mm, "y": placement.y_mm + placement.depth_mm},
    ]


# ---------------------------------------------------------------------------
# The §5.7 driver
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolveOutcome:
    """A partial re-solve's result plus the §5.7 bookkeeping the API persists."""

    result: SolveResult
    #: The raw ``lockedRooms`` payload entries — the same objects, byte-preserved.
    locked_rooms_raw: tuple[Mapping[str, Any], ...]
    locked_room_ids: tuple[str, ...]
    room_diffs: tuple[RoomDiff, ...]

    def to_extra_data(self) -> dict[str, Any]:
        """Merged into the job's result JSON next to ``SolveResult.to_json()``."""
        return {
            "lockedRoomIds": list(self.locked_room_ids),
            "lockedRooms": list(self.locked_rooms_raw),
            "roomDiffs": [diff.to_json() for diff in self.room_diffs],
        }


def resolve_profile(base: SolverProfile) -> SolverProfile:
    """The base profile squeezed into the §5.7 budget.

    Wall-clock budget divides across candidates (15s total, ≥3s each so CP-SAT can
    do more than parse the model). A deterministic profile (``time_budget_seconds
    is None``) keeps its solution/branch limits — those are already bounded work.
    """
    if base.time_budget_seconds is None:
        return replace(base, candidate_parallelism=1)
    per_candidate = max(3, RESOLVE_BUDGET_SECONDS // RESOLVE_MAX_CANDIDATES)
    return replace(
        base,
        time_budget_seconds=min(base.time_budget_seconds, per_candidate),
        candidate_parallelism=min(base.candidate_parallelism, RESOLVE_MAX_CANDIDATES),
    )


async def run_resolve(
    context: SolveContext,
    locked: Sequence[LockedRoom],
    *,
    previous_rooms: Sequence[Mapping[str, Any]] = (),
) -> ResolveOutcome:
    """§5.7 end to end: residual solve around fixed obstacles, locks inviolate."""
    if not locked:
        raise InvalidJobError(
            "This re-solve has no locked rooms.",
            action="Lock at least one room, or generate fresh options instead.",
            detail="solver.resolve requires lockedRooms; use solver.generate otherwise",
        )

    locked = tuple(locked)
    locked_ids = tuple(sorted(room.id for room in locked))
    locked_walls: tuple[LockedWall, ...] = tuple(wall for room in locked for wall in room.walls)
    locked_placements = tuple(
        room.bounding_placement() for room in sorted(locked, key=lambda item: item.id)
    )

    def grid_transform(grid: GridSpec) -> GridSpec:
        masked = mask_locked_cells(grid, locked)
        log.info(
            "resolve.grid_masked",
            locked_rooms=len(locked),
            cells_before=grid.buildable_cells(),
            cells_after=masked.buildable_cells(),
        )
        return masked

    def stage_b_post(model: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """Shared-wall dedupe (locked side wins) + the never-touch-a-lock check."""
        walls = model.get("walls")
        if not isinstance(walls, list) or not locked_walls:
            return model
        stripped = strip_relocked_walls(locked_walls, walls)
        if stripped is None:
            return None  # discard: run_solver logs the §5.7 reason per candidate
        merged = merge_walls_locked_wins(locked_walls, stripped)
        if not locked_walls_untouched(locked_walls, merged):
            return None  # belt-and-braces; merge_walls_locked_wins guarantees this
        out = dict(model)
        out["walls"] = list(merged)
        return out

    def placements_augment(
        placements: tuple[RoomPlacement, ...],
    ) -> tuple[RoomPlacement, ...]:
        """Locked rooms re-join the plan for scoring, ids preserved."""
        return placements + locked_placements

    resolve_context = replace(
        context,
        params=residual_params(context.params, locked),
        profile=resolve_profile(context.effective_profile()),
        grid_transform=grid_transform,
        stage_b_post=stage_b_post,
        placements_augment=placements_augment,
        max_stair_candidates=RESOLVE_MAX_CANDIDATES,
    )
    result = await run_solver(resolve_context)

    diffs: tuple[RoomDiff, ...] = ()
    if previous_rooms and result.options:
        best = result.options[0]
        try:
            diffs = match_unlocked_rooms(previous_rooms, best.placements, locked_ids=locked_ids)
        except RuntimeError as exc:
            # Degraded, not broken: the plan is still valid without the diff story.
            log.warning("resolve.diff_unavailable", error=str(exc))

    return ResolveOutcome(
        result=result,
        locked_rooms_raw=tuple(room.raw for room in locked if room.raw is not None),
        locked_room_ids=locked_ids,
        room_diffs=diffs,
    )


__all__ = [
    "DIFF_JACCARD_THRESHOLD_X100",
    "MIN_WALL_FRAGMENT_MM",
    "RESOLVE_BUDGET_SECONDS",
    "RESOLVE_MAX_CANDIDATES",
    "LockedRoom",
    "LockedWall",
    "ResolveOutcome",
    "RoomDiff",
    "locked_walls_untouched",
    "mask_locked_cells",
    "match_unlocked_rooms",
    "merge_walls_locked_wins",
    "parse_locked_rooms",
    "parse_previous_rooms",
    "residual_params",
    "resolve_profile",
    "run_resolve",
    "strip_relocked_walls",
]
