"""Choosing where to cut. A scoring function, deliberately, and not a hardcoded line.

§7 asks for one section and says how to place it:

    Section (through stair): section line auto-chosen through stair flight + one wet area
    if possible.

"Auto-chosen" is the requirement that makes this a scoring problem. A single hardcoded
line — "cut through the middle of the plot" — is right for the demo plan and wrong for
every real one, and worse, it is wrong *invisibly*: nobody notices a section that misses
the staircase until a reviewer does. So candidates are generated from the model, scored by
the rules below, and the winner carries its own score breakdown so the sheet (and the
"why this drawing" UI) can show the reasoning rather than assert it.

The rules, in the order they matter
-----------------------------------
============================================  =========  =========================================
Rule                                          Weight     Why
============================================  =========  =========================================
Crosses the stair footprint                   *required*  §7: the section is *through the stair*
Runs **along** the flight, not across it      +1000       A cross-cut shows one tread, not a stair
Passes through the flight itself              +300        Landing-only cuts miss the risers
Reaches a wet area                            +600        §7: "+ one wet area if possible"
Each further room crossed (max 5)             +40         A section that crosses more is more useful
Each opening crossed (max 6)                  +15         Shows sill and lintel in section
Runs lengthwise inside a wall                 -800        Cutting along a wall shows masonry, not rooms
Within 100mm of the stair footprint edge       -250       Fragile: a small edit misses the stair
============================================  =========  =========================================

"One wet area if possible" is read literally: the bonus is paid once, not per wet room, so
the score cannot be gamed by a cut that skewers four toilets and no bedrooms.

Everything is integer arithmetic on the model's own geometry. Ties break by axis then by
coordinate, so the choice is deterministic — the same model always yields the same section,
which is what makes it golden-file-able and what stops a sheet regenerating differently
after an unrelated edit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from services.drawings.elevations.facade import wall_rect
from services.drawings.elevations.vertical import U_AXES, u_of
from services.drawings.sections.stair import StairGeometry, stair_geometry

__all__ = [
    "CANDIDATE_EDGE_MARGIN_MM",
    "CutLine",
    "SCORE_ALONG_FLIGHT",
    "SCORE_PER_OPENING",
    "SCORE_PER_ROOM",
    "SCORE_THROUGH_FLIGHT",
    "SCORE_WET_AREA",
    "SectionCandidate",
    "SectionChoice",
    "WET_ROOM_TYPES",
    "choose_section_line",
    "score_candidate",
]

# ---------------------------------------------------------------------------
# Weights. Named constants because a magic number in a scoring function is a
# decision nobody can review.
# ---------------------------------------------------------------------------
SCORE_ALONG_FLIGHT = 1_000
SCORE_THROUGH_FLIGHT = 300
SCORE_WET_AREA = 600
SCORE_PER_ROOM = 40
MAX_SCORED_ROOMS = 5
SCORE_PER_OPENING = 15
MAX_SCORED_OPENINGS = 6
PENALTY_ALONG_WALL = -800
PENALTY_NEAR_STAIR_EDGE = -250

#: How far inside the stair footprint a candidate must sit.
CANDIDATE_EDGE_MARGIN_MM = 150
#: Distance from the footprint edge below which the cut is called fragile.
STAIR_EDGE_TOLERANCE_MM = 100
#: Extra clearance around a parallel wall's face before "runs inside a wall" trips.
WALL_CLEARANCE_MM = 100

#: Mirror of ``garh_model.model.WET_ROOM_TYPES``. Duplicated rather than imported to keep
#: this module dependency-free; ``tests/test_sections.py`` asserts the two agree.
WET_ROOM_TYPES: tuple[str, ...] = ("kitchen", "bath", "wc", "bath_wc", "utility")

#: ``axis`` → the compass direction of the cut plane's outward normal (where the viewer
#: stands). A constant-``x`` cut is viewed from the east looking west; a constant-``y`` cut
#: from the north looking south. Both then use the elevations' ``u = ẑ × n̂`` rule, so a
#: section and an elevation never disagree about which way is screen-right.
_AXIS_VIEW: dict[str, str] = {"x": "E", "y": "N"}
_OPPOSITE: dict[str, str] = {"N": "SOUTH", "E": "WEST", "S": "NORTH", "W": "EAST"}


@dataclass(frozen=True)
class CutLine:
    """A section cut plane: an axis, a position, and which way the viewer faces."""

    axis: str
    position_mm: int
    label: str = "A"

    @property
    def view_direction(self) -> str:
        return _AXIS_VIEW[self.axis]

    @property
    def looking(self) -> str:
        """Human direction of view, for the sheet title ("LOOKING WEST")."""
        return _OPPOSITE[self.view_direction]

    @property
    def u_axis(self) -> tuple[int, int]:
        return U_AXES[self.view_direction]

    def name(self) -> str:
        return "SECTION %s-%s" % (self.label, self.label)

    def endpoints(
        self, bbox: tuple[int, int, int, int], *, overrun_mm: int = 2_000
    ) -> tuple[tuple[int, int], tuple[int, int]]:
        """The two model-space ends of the cut, run past the building both ways.

        This is the form ``services.drawings.sheets.Viewport.section_line`` stores, so a
        chosen line round-trips into a persisted sheet unchanged.
        """
        x_lo, y_lo, x_hi, y_hi = bbox
        if self.axis == "x":
            return (
                (self.position_mm, y_lo - overrun_mm),
                (self.position_mm, y_hi + overrun_mm),
            )
        return (
            (x_lo - overrun_mm, self.position_mm),
            (x_hi + overrun_mm, self.position_mm),
        )

    def straddles(self, rect: tuple[int, int, int, int]) -> bool:
        """Does the cut pass through this axis-aligned model rectangle?"""
        x_lo, y_lo, x_hi, y_hi = rect
        if self.axis == "x":
            return x_lo < self.position_mm < x_hi
        return y_lo < self.position_mm < y_hi

    def to_json(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "positionMm": self.position_mm,
            "label": self.label,
            "viewDirection": self.view_direction,
            "looking": self.looking,
        }


@dataclass(frozen=True)
class SectionCandidate:
    """One scored candidate, with the arithmetic that produced the score."""

    line: CutLine
    stair_id: str
    score: int
    breakdown: tuple[tuple[str, int], ...]
    room_ids: tuple[str, ...]
    wet_room_ids: tuple[str, ...]
    opening_ids: tuple[str, ...]
    along_flight: bool
    through_flight: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "line": self.line.to_json(),
            "stairId": self.stair_id,
            "score": self.score,
            "breakdown": [{"reason": r, "points": p} for r, p in self.breakdown],
            "roomIds": list(self.room_ids),
            "wetRoomIds": list(self.wet_room_ids),
            "openingIds": list(self.opening_ids),
            "alongFlight": self.along_flight,
            "throughFlight": self.through_flight,
        }


@dataclass(frozen=True)
class SectionChoice:
    """The winner, the field it beat, and what the reader should be told."""

    best: SectionCandidate | None
    candidates: tuple[SectionCandidate, ...]
    notes: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "best": self.best.to_json() if self.best else None,
            "candidateCount": len(self.candidates),
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Model reading helpers (duck-typed, no imports from the model core)
# ---------------------------------------------------------------------------
def _rect_of_polygon(polygon: Sequence[Any]) -> tuple[int, int, int, int]:
    xs = [int(p.x) for p in polygon]
    ys = [int(p.y) for p in polygon]
    return (min(xs), min(ys), max(xs), max(ys))


def _wall_rect(wall: Any) -> tuple[int, int, int, int] | None:
    """The wall's axis-aligned footprint — the elevations' reader, reused verbatim."""
    return wall_rect(wall)


def _opening_rect(house: Any, opening: Any) -> tuple[int, int, int, int] | None:
    """Model-space rectangle of an opening: jamb to jamb along its wall, wall-thick across."""
    wall = next((w for w in house.walls if str(w.id) == str(opening.wall_id)), None)
    if wall is None:
        return None
    rect = _wall_rect(wall)
    if rect is None:
        return None
    a = (int(wall.a.x), int(wall.a.y))
    b = (int(wall.b.x), int(wall.b.y))
    length = abs(b[0] - a[0]) + abs(b[1] - a[1])
    if length <= 0:
        return None
    step = ((b[0] - a[0]) // length, (b[1] - a[1]) // length)
    width = int(opening.width_mm)
    near = int(opening.offset_mm) - width // 2
    p1 = (a[0] + step[0] * near, a[1] + step[1] * near)
    p2 = (a[0] + step[0] * (near + width), a[1] + step[1] * (near + width))
    if step[1] == 0:  # wall runs along X: opening spans x, wall thickness spans y
        return (min(p1[0], p2[0]), rect[1], max(p1[0], p2[0]), rect[3])
    return (rect[0], min(p1[1], p2[1]), rect[2], max(p1[1], p2[1]))


def _axis_of_travel(direction: str) -> str:
    """The cut axis that runs **along** a stair travelling in ``direction``.

    A stair going north climbs along ``+Y``, so the cut plane that follows the flight is at
    constant ``x``.
    """
    return "x" if direction in ("N", "S") else "y"


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(value, hi))


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def score_candidate(house: Any, line: CutLine, geometry: StairGeometry) -> SectionCandidate:
    """Score one candidate cut against one stair. Pure integer arithmetic."""
    breakdown: list[tuple[str, int]] = []
    total = 0

    along_flight = line.axis == _axis_of_travel(geometry.direction)
    if along_flight:
        total += SCORE_ALONG_FLIGHT
        breakdown.append(("runs along the stair flight", SCORE_ALONG_FLIGHT))
    through_flight = line.straddles(geometry.flight_rect)
    if through_flight:
        total += SCORE_THROUGH_FLIGHT
        breakdown.append(("passes through the flight", SCORE_THROUGH_FLIGHT))

    room_ids: list[str] = []
    wet_ids: list[str] = []
    for room in sorted(house.rooms, key=lambda r: str(r.id)):
        if len(room.polygon) < 3:
            continue
        if not line.straddles(_rect_of_polygon(room.polygon)):
            continue
        room_ids.append(str(room.id))
        if str(room.type) in WET_ROOM_TYPES:
            wet_ids.append(str(room.id))
    if wet_ids:
        total += SCORE_WET_AREA
        breakdown.append(("reaches a wet area", SCORE_WET_AREA))
    scored_rooms = min(len(room_ids), MAX_SCORED_ROOMS)
    if scored_rooms:
        points = scored_rooms * SCORE_PER_ROOM
        total += points
        breakdown.append(("crosses %d room(s)" % len(room_ids), points))

    opening_ids: list[str] = []
    for opening in sorted(house.openings, key=lambda o: str(o.id)):
        rect = _opening_rect(house, opening)
        if rect is not None and line.straddles(rect):
            opening_ids.append(str(opening.id))
    scored_openings = min(len(opening_ids), MAX_SCORED_OPENINGS)
    if scored_openings:
        points = scored_openings * SCORE_PER_OPENING
        total += points
        breakdown.append(("crosses %d opening(s)" % len(opening_ids), points))

    # Lengthwise inside a wall: the cut is parallel to the wall and lands within its
    # thickness plus a clearance. Charged once however many walls qualify.
    inside_wall = False
    for wall in house.walls:
        rect = _wall_rect(wall)
        if rect is None:
            continue
        if line.axis == "x":
            parallel = rect[3] - rect[1] > rect[2] - rect[0]
            lo, hi = rect[0] - WALL_CLEARANCE_MM, rect[2] + WALL_CLEARANCE_MM
        else:
            parallel = rect[2] - rect[0] > rect[3] - rect[1]
            lo, hi = rect[1] - WALL_CLEARANCE_MM, rect[3] + WALL_CLEARANCE_MM
        if parallel and lo < line.position_mm < hi:
            inside_wall = True
            break
    if inside_wall:
        total += PENALTY_ALONG_WALL
        breakdown.append(("runs lengthwise inside a wall", PENALTY_ALONG_WALL))

    x_lo, y_lo, x_hi, y_hi = geometry.footprint
    edge_lo, edge_hi = (x_lo, x_hi) if line.axis == "x" else (y_lo, y_hi)
    if min(line.position_mm - edge_lo, edge_hi - line.position_mm) < STAIR_EDGE_TOLERANCE_MM:
        total += PENALTY_NEAR_STAIR_EDGE
        breakdown.append(("close to the stair footprint edge", PENALTY_NEAR_STAIR_EDGE))

    return SectionCandidate(
        line=line,
        stair_id=geometry.stair_id,
        score=total,
        breakdown=tuple(breakdown),
        room_ids=tuple(room_ids),
        wet_room_ids=tuple(wet_ids),
        opening_ids=tuple(opening_ids),
        along_flight=along_flight,
        through_flight=through_flight,
    )


def _candidate_positions(house: Any, axis: str, geometry: StairGeometry) -> tuple[int, ...]:
    """Positions worth scoring for one axis: inside the stair, aimed at something useful.

    The generator is small on purpose. Every candidate must cross the stair, so the search
    space is the stair's own span; within it, the interesting places are the middle of the
    flight, the middle of the footprint, and the coordinate of anything the cut might also
    want to reach — a wet room, any room, an opening — clamped into the stair's span. That
    is a handful of integers, scored exhaustively, rather than a sweep over millimetres.
    """
    x_lo, y_lo, x_hi, y_hi = geometry.footprint
    lo, hi = (x_lo, x_hi) if axis == "x" else (y_lo, y_hi)
    lo += CANDIDATE_EDGE_MARGIN_MM
    hi -= CANDIDATE_EDGE_MARGIN_MM
    if hi < lo:
        return ()

    f_lo, f_hi = (
        (geometry.flight_rect[0], geometry.flight_rect[2])
        if axis == "x"
        else (geometry.flight_rect[1], geometry.flight_rect[3])
    )
    raw: list[int] = [(f_lo + f_hi) // 2, (lo + hi) // 2]
    for room in house.rooms:
        if len(room.polygon) < 3:
            continue
        rect = _rect_of_polygon(room.polygon)
        raw.append((rect[0] + rect[2]) // 2 if axis == "x" else (rect[1] + rect[3]) // 2)
    for opening in house.openings:
        rect = _opening_rect(house, opening)
        if rect is None:
            continue
        raw.append((rect[0] + rect[2]) // 2 if axis == "x" else (rect[1] + rect[3]) // 2)

    out: list[int] = []
    for value in raw:
        clamped = _clamp(value, lo, hi)
        if clamped not in out:
            out.append(clamped)
    return tuple(sorted(out))


def choose_section_line(house: Any, *, label: str = "A") -> SectionChoice:
    """Pick the §7 section line. Deterministic, explained, and honest when it cannot.

    Returns a choice whose ``best`` is None when the model has no stair — a house with no
    stair has no "section through the staircase", and inventing one somewhere else would be
    a drawing nobody asked for. The caller decides what to do (skip the sheet, or pass an
    explicit line).
    """
    # Lowest storey first: "the section through the staircase" conventionally means the
    # flight you climb from the entrance, and this also makes tie-breaks intuitive when a
    # G+1 has stacked flights at the same position.
    storey_order = {str(s.id): index for index, s in enumerate(getattr(house, "storeys", ()) or ())}
    stairs = sorted(
        getattr(house, "stairs", ()) or (),
        key=lambda s: (storey_order.get(str(s.storey_id), 1_000), str(s.id)),
    )
    if not stairs:
        return SectionChoice(
            best=None,
            candidates=(),
            notes=(
                "No stair in the model, so §7's section-through-the-staircase has nothing "
                "to cut. Add a stair, or pass an explicit cut line.",
            ),
        )

    candidates: list[SectionCandidate] = []
    for stair in stairs:
        geometry = stair_geometry(stair)
        for axis in ("x", "y"):
            for position in _candidate_positions(house, axis, geometry):
                line = CutLine(axis=axis, position_mm=position, label=label)
                if not line.straddles(geometry.footprint):
                    continue
                candidates.append(score_candidate(house, line, geometry))

    if not candidates:
        return SectionChoice(
            best=None,
            candidates=(),
            notes=("Every candidate cut missed the stair footprint — check the stair geometry.",),
        )

    ordered = sorted(candidates, key=lambda c: (-c.score, c.line.axis, c.line.position_mm))
    best = ordered[0]
    notes: list[str] = [
        "Section line chosen by score: %s at %d (%d points, %d candidates considered)."
        % (best.line.axis, best.line.position_mm, best.score, len(candidates))
    ]
    if not best.wet_room_ids:
        notes.append(
            "No cut through this stair also reaches a wet area, so §7's optional wet-area "
            "requirement is not met by this section."
        )
    if not best.along_flight:
        notes.append(
            "The best available cut runs across the flight rather than along it: the stair "
            "reads as a cut tread, not a profile."
        )
    return SectionChoice(best=best, candidates=tuple(ordered), notes=tuple(notes))


def u_of_point(x: int, y: int, line: CutLine) -> int:
    """``u`` of a model point in this section's drawing space (before the origin shift)."""
    return u_of(x, y, line.u_axis)
