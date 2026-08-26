"""§5.3 step 2 — deterministic door / window / ventilator placement. **ortools-free.**

    "insert doors (from circulation into each room, swing into room, clear of
     fixtures; 900mm bedrooms/main, 750mm baths), windows (on external edges,
     area ≥ 1/10 room floor area, sill 900mm, avoid road-facing baths),
     ventilators for internal baths on shafts" — engineering playbook §5.3.

PLACEMENT RULES (deterministic; same layout in, byte-identical plan out):

Doors
  * Every non-circulation room gets exactly one door, hosted on the shared wall
    with a circulation room (``passage``/``lobby``/``foyer``/``staircase``; the
    living room counts as circulation in small Indian plans — documented
    fallback). Rooms with no circulation frontage may chain through an adjacent
    reachable room (the en-suite pattern); anything still unreachable is a typed
    discard, never a silent omission.
  * The serving room is chosen by longest shared span (tie → lower wall index,
    then lower span start).
  * The leaf is hinged at the span end nearer the host wall's ``a`` — i.e. in a
    room corner. Door centre = span start + 115 (jamb) + width/2, nudged in
    whole 115 modules until it clears the wall end margins and every opening
    already on that wall (min 115 clear between openings, so there is always a
    buildable pier). This corner-hinged convention is also the furniture
    contract: the §5.4 critic packs furniture from the corner DIAGONALLY
    OPPOSITE the door, and :func:`swing_clearance_rect` hands it the exact
    keep-out square to test against.
  * Widths: 900 for habitable rooms and the main entrance, 750 for bath/WC,
    800 otherwise — each floored by the NBC pack minimums, which are READ FROM
    THE PACK (`nbc.door.*.width.min`), not hard-coded.
  * Swing is INTO the served room: ``in-left`` when the room lies to the left
    of the host wall's a→b direction, else ``in-right``.

Windows
  * Habitable rooms and kitchens: aggregate openable area ≥ the NBC ratio of
    the CLEAR floor area. The ratio comes from `nbc.ventilation.habitable.min`
    in the pack (1/10 today — but the pack is the authority, so a pack revision
    changes the geometry without touching this file). Requirement arithmetic is
    ``Ratio.ceil_of`` — exact integers, rounded against the design.
  * Window height = lintel − sill (2100 − 900 default ⇒ 1200); width = what the
    requirement needs, floored at 1200mm and capped by the frontage. Longest
    external span first; more windows on further spans until the ratio is met,
    else a typed VENTILATION_SHORT discard.
  * Baths/WCs: a high ventilator-window (sill 1800, height 450) sized to the
    pack's 0.3m² minimum on a NON-ROAD-FACING external edge; a bath's window
    NEVER faces the road (privacy is absolute, so the road test is by outward
    direction and deliberately conservative). Internal baths take a ventilator
    on a shared shaft wall; no shaft ⇒ typed BATH_VENTILATION discard.

Non-door openings carry ``swing='in-left'`` — the enum demands a value and a
fixed one keeps the JSON byte-stable.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from services.solver.walls import (
    AdjacencySpan,
    CellLayout,
    ExternalSpan,
    WallNetwork,
    WallSpec,
)

#: Section-3 invariant: openings keep this much solid wall at each end
#: (mirrors ``garh_model.validate.WALL_END_MARGIN_MM``).
WALL_END_MARGIN_MM = 115
#: Minimum solid pier kept between two openings on the same wall.
OPENING_GAP_MM = 115
#: One nudge step when resolving collisions — the brick module.
NUDGE_MM = 115

#: Rooms that distribute movement. Doors lead FROM these INTO rooms.
CIRCULATION_ROOM_TYPES = frozenset({"passage", "lobby", "foyer", "staircase"})
#: In compact Indian plans the living room is the de-facto distributor.
FALLBACK_CIRCULATION_TYPES = frozenset({"living", "living_dining"})
#: Service voids: slab cutouts, not rooms you walk into. They get ventilators
#: (a bath vents INTO a shaft), never a door — demanding one would discard every
#: layout whose shaft is correctly sized at one grid cell.
SERVICE_VOID_TYPES = frozenset({"shaft", "duct", "void"})

_HABITABLE_TYPES = frozenset(
    {
        "living",
        "dining",
        "living_dining",
        "bedroom_master",
        "bedroom",
        "guest_bedroom",
        "servant_room",
        "study",
    }
)
_BATH_TYPES = frozenset({"bath", "wc", "bath_wc"})
_KITCHEN_TYPES = frozenset({"kitchen"})
_DOOR_900_TYPES = _HABITABLE_TYPES

#: Bath ventilator leaf height; the width is derived from the pack minimum.
VENT_HEIGHT_MM = 450
#: Bath ventilator sill — above head height for privacy.
VENT_SILL_MM = 1800
#: Preferred single-window width before the requirement forces it wider.
DEFAULT_WINDOW_WIDTH_MM = 1200


class OpeningError(ValueError):
    """A layout whose openings cannot satisfy §5.3. Typed discard reason."""

    def __init__(self, code: str, message: str, *, detail: Optional[str] = None) -> None:
        super().__init__("%s — %s" % (code, message))
        self.code = code
        self.message = message
        self.detail = detail


@dataclass(frozen=True)
class OpeningSpec:
    """One opening, wall-relative, ready to become an ``opening.add`` payload."""

    wall_index: int
    kind: str  # 'door' | 'window' | 'ventilator'
    width_mm: int
    height_mm: int
    sill_mm: int
    #: Distance from the host wall's ``a`` to the opening CENTRE (§3 contract).
    offset_mm: int
    swing: str
    #: The room this opening serves (ventilates / leads into).
    room_key: str
    #: 'main-entrance' | 'internal' | 'bath' for doors; kind name otherwise.
    role: str
    #: For doors: the room the door leads FROM (circulation side), else None.
    from_key: Optional[str] = None


@dataclass(frozen=True)
class NbcOpeningLimits:
    """Every §5.3 opening limit, read out of the nbc-core pack — never literals."""

    vent_ratio_num: int
    vent_ratio_den: int
    bath_vent_min_mm2: int
    door_main_min_mm: int
    door_internal_min_mm: int
    door_bath_min_mm: int


def _ensure_apps_api_on_path() -> None:
    """Make ``garh_rules``/``garh_model`` importable when run from the repo.

    In the worker image ``PYTHONPATH=/app:/app/apps/api`` already covers this;
    locally the repo layout is discovered relative to this file.
    """
    try:
        import garh_rules  # noqa: F401

        return
    except ImportError:
        pass
    root = Path(__file__).resolve().parents[2]
    candidate = root / "apps" / "api"
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.append(str(candidate))


def load_nbc_limits(root: Optional[str] = None) -> NbcOpeningLimits:
    """Pull the opening limits from the nbc-core rule pack.

    Lazy import: the pack loader is pure stdlib, but keeping it out of module
    scope means this module imports even where ``apps/api`` is absent, and the
    pack values cannot silently fork from what the §5.4 critic will enforce —
    both read the same rules.
    """
    _ensure_apps_api_on_path()
    from garh_rules.packs import load_pack_set

    packs = load_pack_set(("nbc-core",), root=root)
    vent = packs.require_rule("nbc.ventilation.habitable.min").check.ratio_param("ratio")
    bath_vent = packs.require_rule("nbc.ventilation.bath.min").check.opt_int_param("minAreaMm2")
    return NbcOpeningLimits(
        vent_ratio_num=vent.num,
        vent_ratio_den=vent.den,
        bath_vent_min_mm2=bath_vent,
        door_main_min_mm=packs.require_rule("nbc.door.main.width.min").check.int_param("valueMm"),
        door_internal_min_mm=packs.require_rule("nbc.door.internal.width.min").check.int_param(
            "valueMm"
        ),
        door_bath_min_mm=packs.require_rule("nbc.door.bath.width.min").check.int_param("valueMm"),
    )


# ---------------------------------------------------------------------------
# wall-local arithmetic
# ---------------------------------------------------------------------------


def _axis_of(wall: WallSpec) -> int:
    """0 when offsets run along x, 1 along y."""
    return 1 if wall.axis == "v" else 0


def _wall_length(wall: WallSpec) -> int:
    return wall.length_mm


def _to_local(wall: WallSpec, value_on_axis: int) -> int:
    """Absolute axis coordinate → distance along the wall from ``a``."""
    a = wall.a[_axis_of(wall)]
    b = wall.b[_axis_of(wall)]
    return value_on_axis - a if b >= a else a - value_on_axis


def _span_local(wall: WallSpec, lo: int, hi: int) -> Tuple[int, int]:
    """Absolute span → wall-local, clipped to the wall's own extent."""
    s1 = _to_local(wall, lo)
    s2 = _to_local(wall, hi)
    lo_l, hi_l = min(s1, s2), max(s1, s2)
    return max(0, lo_l), min(_wall_length(wall), hi_l)


class _WallOccupancy:
    """Occupied intervals per wall, so openings never collide or crowd."""

    def __init__(self) -> None:
        self._used: Dict[int, List[Tuple[int, int]]] = {}

    def blocked(self, wall_index: int, lo: int, hi: int) -> bool:
        for u1, u2 in self._used.get(wall_index, []):
            if min(hi + OPENING_GAP_MM, u2 + OPENING_GAP_MM) - max(lo - OPENING_GAP_MM, u1 - OPENING_GAP_MM) > 0:
                if hi > u1 - OPENING_GAP_MM and lo < u2 + OPENING_GAP_MM:
                    return True
        return False

    def claim(self, wall_index: int, lo: int, hi: int) -> None:
        self._used.setdefault(wall_index, []).append((lo, hi))

    def free_intervals(self, wall_index: int, lo: int, hi: int) -> List[Tuple[int, int]]:
        """Sub-intervals of [lo, hi] clear of existing openings (+gap)."""
        blocks = sorted(
            (u1 - OPENING_GAP_MM, u2 + OPENING_GAP_MM)
            for u1, u2 in self._used.get(wall_index, [])
        )
        out: List[Tuple[int, int]] = []
        cursor = lo
        for b1, b2 in blocks:
            if b2 <= lo or b1 >= hi:
                continue
            if b1 > cursor:
                out.append((cursor, min(b1, hi)))
            cursor = max(cursor, b2)
        if cursor < hi:
            out.append((cursor, hi))
        return out


def _fit_opening(
    wall: WallSpec,
    occupancy: _WallOccupancy,
    wall_index: int,
    span_lo_local: int,
    span_hi_local: int,
    width_mm: int,
) -> Optional[int]:
    """Deterministic centre offset for an opening, or None when nothing fits.

    Preference order: hinge corner (span start) first, then +115 steps.
    """
    length = _wall_length(wall)
    lo = max(span_lo_local + WALL_END_MARGIN_MM, WALL_END_MARGIN_MM)
    hi = min(span_hi_local - WALL_END_MARGIN_MM, length - WALL_END_MARGIN_MM)
    if hi - lo < width_mm:
        return None
    centre = lo + width_mm // 2
    last = hi - (width_mm - width_mm // 2)
    while centre <= last:
        if not occupancy.blocked(wall_index, centre - width_mm // 2, centre + (width_mm - width_mm // 2)):
            return centre
        centre += NUDGE_MM
    return None


def _room_side_is_left(wall: WallSpec, room_centre: Tuple[int, int]) -> bool:
    ax, ay = wall.a
    bx, by = wall.b
    cx, cy = room_centre
    cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    return cross > 0


# ---------------------------------------------------------------------------
# doors
# ---------------------------------------------------------------------------


def place_doors(
    layout: CellLayout,
    network: WallNetwork,
    *,
    limits: NbcOpeningLimits,
    door_height_mm: int = 2100,
    entry_outward: Optional[str] = None,
    occupancy: Optional[_WallOccupancy] = None,
) -> Tuple[Tuple[OpeningSpec, ...], Optional[OpeningSpec], _WallOccupancy]:
    """All doors for one storey: ``(internal doors, main door or None, occupancy)``.

    Ground storey (``storey_index == 0``) gets a main entrance door on the entry
    room's external wall — preferring the ``entry_outward`` (road) side. Upper
    storeys are entered by the stair, so their root is the ``staircase`` room.
    """
    occ = occupancy if occupancy is not None else _WallOccupancy()
    rooms = {r.key: r for r in layout.rooms}

    circulation = {
        r.key for r in layout.rooms if r.room_type in CIRCULATION_ROOM_TYPES
    }
    if not circulation:
        circulation = {
            r.key for r in layout.rooms if r.room_type in FALLBACK_CIRCULATION_TYPES
        }
    if not circulation:
        raise OpeningError(
            "UNREACHABLE_ROOM",
            "No circulation room (passage/lobby/foyer/staircase or living) on storey %d."
            % layout.storey_index,
        )

    # Reachability root.
    main_door: Optional[OpeningSpec] = None
    if layout.storey_index == 0:
        entry_key = _pick_entry_room(layout, network, circulation, entry_outward)
        main_door = _place_main_door(
            layout, network, occ, entry_key, limits, door_height_mm, entry_outward
        )
        roots = {entry_key}
    else:
        stair_keys = sorted(
            r.key for r in layout.rooms if r.room_type == "staircase"
        )
        if not stair_keys:
            raise OpeningError(
                "UNREACHABLE_ROOM",
                "Storey %d has no staircase room to arrive by." % layout.storey_index,
            )
        roots = {stair_keys[0]}

    # Circulation rooms connect to each other openly (archways, stair arrivals).
    reached = set(roots)
    changed = True
    while changed:
        changed = False
        for span in network.adjacencies:
            a, b = span.low_room, span.high_room
            if a in circulation and b in circulation:
                if a in reached and b not in reached:
                    reached.add(b)
                    changed = True
                elif b in reached and a not in reached:
                    reached.add(a)
                    changed = True

    doors: List[OpeningSpec] = []
    pending = sorted(
        k for k, r in rooms.items() if k not in reached and r.room_type not in SERVICE_VOID_TYPES
    )
    # Pass 1: door from circulation. Pass 2+: en-suite chaining off reached rooms.
    #: room key → why its last attempt failed, for the honest discard message.
    unfit: Dict[str, str] = {}
    progress = True
    while pending and progress:
        progress = False
        still_pending: List[str] = []
        for key in pending:
            spans = _serving_spans(network, key, reached, circulation)
            if not spans:
                still_pending.append(key)
                continue
            door = _place_room_door(layout, network, occ, key, spans, limits, door_height_mm)
            if door is None:
                # No span fits TODAY; a room reached in a later round may open a
                # new, wider span, so this is retryable — not yet a discard.
                width = door_width_for(rooms[key].room_type, limits)
                longest = max(s.hi - s.lo for s in spans)
                unfit[key] = "a %dmm door into %r does not fit any of its %d shared span(s) (longest %dmm)" % (
                    width, key, len(spans), longest,
                )
                still_pending.append(key)
                continue
            doors.append(door)
            reached.add(key)
            unfit.pop(key, None)
            progress = True
        pending = still_pending
    if pending:
        blocked = [key for key in pending if key in unfit]
        if blocked:
            raise OpeningError(
                "DOOR_DOES_NOT_FIT",
                "; ".join(unfit[key] for key in blocked) + ".",
                detail="storey %d" % layout.storey_index,
            )
        raise OpeningError(
            "UNREACHABLE_ROOM",
            "No door path reaches: %s." % ", ".join(pending),
            detail="storey %d" % layout.storey_index,
        )
    return tuple(doors), main_door, occ


def _pick_entry_room(
    layout: CellLayout,
    network: WallNetwork,
    circulation: set,
    entry_outward: Optional[str],
) -> str:
    """The room the main door opens into. Deterministic preference order:
    circulation room with frontage on the entry side, then any frontage,
    then any circulation room; ties by type priority then key."""
    priority = {"foyer": 0, "lobby": 1, "passage": 2, "staircase": 3, "living": 4, "living_dining": 5}

    def type_rank(key: str) -> Tuple[int, str]:
        return (priority.get(layout.room(key).room_type, 9), key)

    with_entry_frontage = sorted(
        {
            s.room_key
            for s in network.external_spans
            if s.room_key in circulation and entry_outward is not None and s.outward == entry_outward
        },
        key=type_rank,
    )
    if with_entry_frontage:
        return with_entry_frontage[0]
    with_frontage = sorted(
        {s.room_key for s in network.external_spans if s.room_key in circulation},
        key=type_rank,
    )
    if with_frontage:
        return with_frontage[0]
    return sorted(circulation, key=type_rank)[0]


def _place_main_door(
    layout: CellLayout,
    network: WallNetwork,
    occ: _WallOccupancy,
    entry_key: str,
    limits: NbcOpeningLimits,
    door_height_mm: int,
    entry_outward: Optional[str],
) -> Optional[OpeningSpec]:
    spans = network.external_spans_of(entry_key)
    if not spans:
        return None
    preferred = [s for s in spans if entry_outward is not None and s.outward == entry_outward]
    ordered = sorted(
        preferred or list(spans), key=lambda s: (-(s.hi - s.lo), s.wall_index, s.lo)
    )
    width = limits.door_main_min_mm
    room = layout.room(entry_key)
    centre = ((room.x1 + room.x2) // 2, (room.y1 + room.y2) // 2)
    for span in ordered:
        wall = network.wall(span.wall_index)
        lo_l, hi_l = _span_local(wall, span.lo, span.hi)
        offset = _fit_opening(wall, occ, span.wall_index, lo_l, hi_l, width)
        if offset is None:
            continue
        occ.claim(span.wall_index, offset - width // 2, offset + (width - width // 2))
        swing = "in-left" if _room_side_is_left(wall, centre) else "in-right"
        return OpeningSpec(
            wall_index=span.wall_index,
            kind="door",
            width_mm=width,
            height_mm=door_height_mm,
            sill_mm=0,
            offset_mm=offset,
            swing=swing,
            room_key=entry_key,
            role="main-entrance",
            from_key=None,
        )
    raise OpeningError(
        "DOOR_DOES_NOT_FIT",
        "The main entrance door does not fit on any external wall of %r." % entry_key,
    )


def _serving_spans(
    network: WallNetwork,
    room_key: str,
    reached: set,
    circulation: set,
) -> List[AdjacencySpan]:
    """Every usable shared span with a reached room, best first: circulation
    rooms before en-suite chaining, longest span first, ties by wall then start.

    A LIST, not the single best: the §5.2 frontage constraint guarantees one
    door-sized span exists, but occupancy (an earlier door on the same wall) or
    the 115mm snap can pinch the best span — falling through to the next honest
    candidate is placement preference, giving up is a discard. First execution
    of the pipeline showed exactly that failure.
    """

    def usable(span: AdjacencySpan) -> Optional[Tuple[int, AdjacencySpan]]:
        other = span.high_room if span.low_room == room_key else span.low_room
        if other not in reached:
            return None
        rank = 0 if other in circulation else 1
        return (rank, span)

    candidates: List[Tuple[int, int, int, int, AdjacencySpan]] = []
    for span in network.adjacencies_of(room_key):
        entry = usable(span)
        if entry is None:
            continue
        rank, s = entry
        candidates.append((rank, -(s.hi - s.lo), s.wall_index, s.lo, s))
    candidates.sort(key=lambda c: c[:4])
    return [c[4] for c in candidates]


def door_width_for(room_type: str, limits: NbcOpeningLimits) -> int:
    """§5.3 door widths, floored by the pack minimums."""
    if room_type in _BATH_TYPES:
        return limits.door_bath_min_mm
    if room_type in _DOOR_900_TYPES:
        return max(900, limits.door_internal_min_mm)
    return max(800, limits.door_internal_min_mm)


def _place_room_door(
    layout: CellLayout,
    network: WallNetwork,
    occ: _WallOccupancy,
    room_key: str,
    spans: Sequence[AdjacencySpan],
    limits: NbcOpeningLimits,
    door_height_mm: int,
) -> Optional[OpeningSpec]:
    """The room's door on the first span (in preference order) it fits.

    ``None`` when no span can take it — the caller decides whether that is
    retryable (more rooms may be reached later) or a typed discard.
    """
    room = layout.room(room_key)
    width = door_width_for(room.room_type, limits)
    centre = ((room.x1 + room.x2) // 2, (room.y1 + room.y2) // 2)
    for span in spans:
        wall = network.wall(span.wall_index)
        lo_l, hi_l = _span_local(wall, span.lo, span.hi)
        offset = _fit_opening(wall, occ, span.wall_index, lo_l, hi_l, width)
        if offset is None:
            continue
        occ.claim(span.wall_index, offset - width // 2, offset + (width - width // 2))
        swing = "in-left" if _room_side_is_left(wall, centre) else "in-right"
        other = span.high_room if span.low_room == room_key else span.low_room
        role = "bath" if room.room_type in _BATH_TYPES else "internal"
        return OpeningSpec(
            wall_index=span.wall_index,
            kind="door",
            width_mm=width,
            height_mm=door_height_mm,
            sill_mm=0,
            offset_mm=offset,
            swing=swing,
            room_key=room_key,
            role=role,
            from_key=other,
        )
    return None


# ---------------------------------------------------------------------------
# windows & ventilators
# ---------------------------------------------------------------------------


def required_vent_area_mm2(clear_area_mm2: int, limits: NbcOpeningLimits) -> int:
    """``ceil(area * num / den)`` — the pack's requirement form, exact integers."""
    return -((-clear_area_mm2 * limits.vent_ratio_num) // limits.vent_ratio_den)


def place_windows(
    layout: CellLayout,
    network: WallNetwork,
    occupancy: _WallOccupancy,
    clear_areas: Mapping[str, int],
    *,
    limits: NbcOpeningLimits,
    sill_mm: int = 900,
    lintel_mm: int = 2100,
    road_outwards: frozenset = frozenset(),
) -> Tuple[OpeningSpec, ...]:
    """Windows for habitable rooms + kitchens, ventilators for baths.

    ``clear_areas`` maps room key → clear floor area (mm²) — the same number the
    rules engine will divide by, so requirement and check cannot disagree.
    """
    out: List[OpeningSpec] = []
    for room in layout.rooms:  # already sorted by key — deterministic
        if room.room_type in _HABITABLE_TYPES or room.room_type in _KITCHEN_TYPES:
            out.extend(
                _room_windows(
                    layout, network, occupancy, room.key, clear_areas[room.key],
                    limits=limits, sill_mm=sill_mm, lintel_mm=lintel_mm,
                )
            )
        elif room.room_type in _BATH_TYPES:
            out.append(
                _bath_ventilation(
                    layout, network, occupancy, room.key,
                    limits=limits, road_outwards=road_outwards,
                )
            )
    return tuple(out)


def _room_windows(
    layout: CellLayout,
    network: WallNetwork,
    occ: _WallOccupancy,
    room_key: str,
    clear_area_mm2: int,
    *,
    limits: NbcOpeningLimits,
    sill_mm: int,
    lintel_mm: int,
) -> List[OpeningSpec]:
    height = lintel_mm - sill_mm
    if height <= 0:
        raise OpeningError(
            "VENTILATION_SHORT",
            "Sill %dmm is at or above lintel %dmm — no window band exists." % (sill_mm, lintel_mm),
        )
    required = required_vent_area_mm2(clear_area_mm2, limits)
    spans = sorted(
        network.external_spans_of(room_key),
        key=lambda s: (-(s.hi - s.lo), s.wall_index, s.lo),
    )
    if not spans:
        raise OpeningError(
            "NO_EXTERNAL_FACE",
            "Room %r has no external wall to put a window on." % room_key,
        )
    placed: List[OpeningSpec] = []
    remaining = required
    for span in spans:
        if remaining <= 0:
            break
        wall = network.wall(span.wall_index)
        span_lo, span_hi = _span_local(wall, span.lo, span.hi)
        for free_lo, free_hi in occ.free_intervals(
            span.wall_index,
            max(span_lo + WALL_END_MARGIN_MM, WALL_END_MARGIN_MM),
            min(span_hi - WALL_END_MARGIN_MM, _wall_length(wall) - WALL_END_MARGIN_MM),
        ):
            if remaining <= 0:
                break
            usable = free_hi - free_lo
            needed = -((-remaining) // height)
            width = min(max(needed, DEFAULT_WINDOW_WIDTH_MM), usable)
            if width < 450:  # a slit narrower than this is a detail, not a window
                continue
            offset = free_lo + width // 2
            occ.claim(span.wall_index, offset - width // 2, offset + (width - width // 2))
            placed.append(
                OpeningSpec(
                    wall_index=span.wall_index,
                    kind="window",
                    width_mm=width,
                    height_mm=height,
                    sill_mm=sill_mm,
                    offset_mm=offset,
                    swing="in-left",
                    room_key=room_key,
                    role="window",
                )
            )
            remaining -= width * height
    if remaining > 0:
        raise OpeningError(
            "VENTILATION_SHORT",
            "Room %r ends %d mm² short of its %d mm² openable-area requirement."
            % (room_key, remaining, required),
        )
    return placed


def _bath_ventilation(
    layout: CellLayout,
    network: WallNetwork,
    occ: _WallOccupancy,
    room_key: str,
    *,
    limits: NbcOpeningLimits,
    road_outwards: frozenset,
) -> OpeningSpec:
    width = -((-limits.bath_vent_min_mm2) // VENT_HEIGHT_MM)
    # External, non-road frontage first (never a road-facing bath window — §5.3).
    spans = sorted(
        (s for s in network.external_spans_of(room_key) if s.outward not in road_outwards),
        key=lambda s: (-(s.hi - s.lo), s.wall_index, s.lo),
    )
    for span in spans:
        wall = network.wall(span.wall_index)
        lo_l, hi_l = _span_local(wall, span.lo, span.hi)
        offset = _fit_opening(wall, occ, span.wall_index, lo_l, hi_l, width)
        if offset is None:
            continue
        occ.claim(span.wall_index, offset - width // 2, offset + (width - width // 2))
        return OpeningSpec(
            wall_index=span.wall_index,
            kind="ventilator",
            width_mm=width,
            height_mm=VENT_HEIGHT_MM,
            sill_mm=VENT_SILL_MM,
            offset_mm=offset,
            swing="in-left",
            room_key=room_key,
            role="ventilator",
        )
    # Internal bath: ventilator onto an adjacent shaft (§5.3, NBC bath rule fix text).
    shaft_spans = sorted(
        (
            s
            for s in network.adjacencies_of(room_key)
            if layout.room(s.high_room if s.low_room == room_key else s.low_room).room_type
            == "shaft"
        ),
        key=lambda s: (-(s.hi - s.lo), s.wall_index, s.lo),
    )
    for span in shaft_spans:
        wall = network.wall(span.wall_index)
        lo_l, hi_l = _span_local(wall, span.lo, span.hi)
        offset = _fit_opening(wall, occ, span.wall_index, lo_l, hi_l, width)
        if offset is None:
            continue
        occ.claim(span.wall_index, offset - width // 2, offset + (width - width // 2))
        return OpeningSpec(
            wall_index=span.wall_index,
            kind="ventilator",
            width_mm=width,
            height_mm=VENT_HEIGHT_MM,
            sill_mm=VENT_SILL_MM,
            offset_mm=offset,
            swing="in-left",
            room_key=room_key,
            role="ventilator",
        )
    raise OpeningError(
        "BATH_VENTILATION",
        "Bath %r has no non-road external wall and no shaft to ventilate into." % room_key,
    )


# ---------------------------------------------------------------------------
# the furniture contract
# ---------------------------------------------------------------------------


def swing_clearance_rect(
    door: OpeningSpec, network: WallNetwork, layout: CellLayout
) -> Tuple[int, int, int, int]:
    """The keep-out square a door leaf sweeps, ``(x1, y1, x2, y2)`` in plot mm.

    width × width, inside the served room, starting at the hinge jamb. The §5.4
    furniture-fit critic subtracts these before packing furniture — this
    function IS the contract between door placement and furniture placement.
    """
    wall = network.wall(door.wall_index)
    room = layout.room(door.room_key)
    w = door.width_mm
    axis = _axis_of(wall)
    a = wall.a[axis]
    b = wall.b[axis]
    direction = 1 if b >= a else -1
    hinge = a + direction * (door.offset_mm - w // 2)
    lo = min(hinge, hinge + direction * w)
    hi = max(hinge, hinge + direction * w)
    room_centre = ((room.x1 + room.x2) // 2, (room.y1 + room.y2) // 2)
    if wall.axis == "v":
        line = wall.line_mm
        if room_centre[0] >= line:
            return (line, lo, line + w, hi)
        return (line - w, lo, line, hi)
    line = wall.line_mm
    if room_centre[1] >= line:
        return (lo, line, hi, line + w)
    return (lo, line - w, hi, line)


__all__ = [
    "CIRCULATION_ROOM_TYPES",
    "DEFAULT_WINDOW_WIDTH_MM",
    "FALLBACK_CIRCULATION_TYPES",
    "NBC_LIMITS_DOC",
    "NbcOpeningLimits",
    "OpeningError",
    "OpeningSpec",
    "SERVICE_VOID_TYPES",
    "VENT_HEIGHT_MM",
    "VENT_SILL_MM",
    "WALL_END_MARGIN_MM",
    "door_width_for",
    "load_nbc_limits",
    "place_doors",
    "place_windows",
    "required_vent_area_mm2",
    "swing_clearance_rect",
]

#: Pointer for reviewers: which pack rules the limits come from.
NBC_LIMITS_DOC = (
    "nbc.ventilation.habitable.min (ratio), nbc.ventilation.bath.min (minAreaMm2), "
    "nbc.door.main.width.min, nbc.door.internal.width.min, nbc.door.bath.width.min"
)
