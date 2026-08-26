"""§5.2 room program — brief + reg profile + NBC pack values → what stage A packs.

**ortools-free.** This is the normalisation layer between "what the client asked for"
and "what CP-SAT is allowed to build":

* **NBC minimums come from the loaded rule pack** (``rulepacks/nbc-core.json``), read
  through the same ``garh_rules`` loader the §5.4 critic uses — ONE source of truth.
  9.5m² appears nowhere in this file; if the pack revises a minimum, the solver's
  bounds move with the critic's checks in the same breath.
* **Aspect bounds** are §5.2 verbatim: 1:1–1:2.2 for habitable rooms, up to 1:3 for
  baths/stores/utility. Circulation rooms get a wider 1:6 — a corridor IS long, and
  §5.2 bounds circulation by area (≤12% soft), not by shape.
* **Adjacency**: kitchen↔dining shared edge ≥900mm is REQUIRED (§5.2); the brief's
  ``adjacency`` wishes ride along as soft, weighted specs.
* **Floor assignment**: rooms with an explicit storey keep it; unassigned rooms get
  the Indian default (living/kitchen/dining/pooja/utility on ground; bedrooms and
  their baths distributed across upper floors) — every default is an assumption chip
  (golden rule 4).
* **Vastu zone allowances** are read from ``rulepacks/vastu.json`` per §5.2's modes:
  strict → allowed zones (primary ∪ fallback) become constraints and denied zones are
  excluded; advisory → only *hard* (severity ``fail``) denials constrain — the critic
  would discard those candidates anyway (§5.4/§5.6) — and preferred zones feed the
  objective bonus; off → nothing.

Two entry points build the same :class:`RoomProgram`:

* :func:`build_program_from_brief` — the corpus/BriefDoc shape (``fixtures/briefs/*``
  ``data`` object) — rooms carry ``type/count/targetAreaMm2/minWidthMm/storey``;
* :func:`program_from_params` — an already-parsed :class:`~services.solver.types.SolveParams`
  (the worker payload path via ``services.solver.handler``).

PACKED vs APPENDAGE: balconies, porches and terraces are not packed by stage A — they
hang off the envelope rather than tiling the footprint (the
:class:`services.solver.walls.CellLayout` contract requires rooms to TILE). They stay
in the program with ``packed=False`` so downstream stages know the brief asked for
them; a note records the deferral.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from services.common.assumptions import Assumption
from services.solver.types import RoomRequest, SolveParams

# ---------------------------------------------------------------------------
# §5.2 constants (shape bounds are the spec's; sizes are NEVER constants here)
# ---------------------------------------------------------------------------

#: §5.2: habitable rooms 1:1–1:2.2.
ASPECT_HABITABLE_X100 = 220
#: §5.2: baths / stores (and other compact service rooms) up to 1:3.
ASPECT_COMPACT_X100 = 300
#: Corridors are long by function; §5.2 bounds circulation by area, not shape.
ASPECT_CIRCULATION_X100 = 600

#: §5.2: "kitchen↔dining touch: shared edge ≥ 900mm".
KITCHEN_DINING_SHARED_EDGE_MM = 900
#: Minimum useful door frontage between a room and its distributor (§5.3 doors need
#: 800mm door + 115mm jambs; 900 keeps it on the coarse module).
DOOR_FRONTAGE_MM = 900

#: Rooms may shrink to 3/4 of target (never below the NBC floor) and grow to 8/5 —
#: the growth headroom is what lets the exact-tiling model absorb slack into rooms
#: instead of corridors. Integer ratios, applied with integer arithmetic.
#: 8/5 rather than the earlier 7/5 is an execution find: on the demo brief the
#: upper storey must tile the ground footprint EXACTLY, and at 7/5 the integer-
#: achievable room maxima left only passage areas no legal rectangle has —
#: every upper storey proved infeasible. Oversize is still §5.6's problem to
#: judge, not the packer's.
MIN_FRACTION_OF_TARGET = (3, 4)
MAX_FRACTION_OF_TARGET = (8, 5)

#: ``garh_model.model.Storey`` default (storey_height_mm = 3000).
DEFAULT_STOREY_HEIGHT_MM = 3000

#: Room types that distribute movement (mirrors services/solver/openings.py).
CIRCULATION_TYPES = frozenset({"passage", "corridor", "foyer", "lobby"})
#: The de-facto distributor in compact Indian plans (openings.py fallback).
FALLBACK_DISTRIBUTOR_TYPES = frozenset({"living", "living_dining"})
#: Appendages stage A does not pack (they do not tile the footprint).
UNPACKED_ROOM_TYPES = frozenset({"balcony", "porch", "terrace"})
#: Types that repeat at the same position on every storey.
REPEATING_ROOM_TYPES = frozenset({"staircase", "shaft"})

#: Indian defaults for rooms whose storey the brief left blank (assumption-chipped).
_UPPER_DEFAULT_TYPES = frozenset(
    {"bedroom", "bedroom_master", "master_bedroom", "guest_bedroom", "bath", "wc", "bath_wc", "dress"}
)

_KITCHEN_TYPES = frozenset({"kitchen", "kitchen_dining"})


# ---------------------------------------------------------------------------
# NBC minimums — read from the pack, cached per pack root
# ---------------------------------------------------------------------------


def _ensure_apps_api_on_path() -> None:
    """Make ``garh_rules`` importable from the repo checkout.

    In the worker image ``PYTHONPATH=/app:/app/apps/api`` already covers this; the
    fallback mirrors ``services/solver/openings.py`` so both modules find the same
    engine.
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


@dataclass(frozen=True)
class NbcRoomMinima:
    """Room-size floors from ``nbc-core``, plus the pack's own type vocabulary."""

    habitable_area_mm2: int
    habitable_width_mm: int
    kitchen_area_mm2: int
    kitchen_dining_area_mm2: int
    kitchen_width_mm: int
    bath_area_mm2: int
    bath_width_mm: int
    wc_area_mm2: int
    wc_width_mm: int
    bath_wc_area_mm2: int
    stair_width_mm: int
    habitable_types: Tuple[str, ...]
    wet_types: Tuple[str, ...]

    def floor_for(self, room_type: str) -> Tuple[int, int, Optional[str]]:
        """(min area mm², min least-width mm, citing rule id) for a room type.

        ``(0, 0, None)`` for types the pack does not bound (foyer, store, …).
        """
        normalised = _normalise(room_type)
        if normalised == "kitchen":
            return (self.kitchen_area_mm2, self.kitchen_width_mm, "nbc.room.kitchen.area.min")
        if normalised == "kitchen_dining":
            return (
                self.kitchen_dining_area_mm2,
                self.kitchen_width_mm,
                "nbc.room.kitchen_dining.area.min",
            )
        if normalised == "bath":
            return (self.bath_area_mm2, self.bath_width_mm, "nbc.room.bath.area.min")
        if normalised == "wc":
            return (self.wc_area_mm2, self.wc_width_mm, "nbc.room.wc.area.min")
        if normalised == "bath_wc":
            return (self.bath_wc_area_mm2, self.bath_width_mm, "nbc.room.bath_wc.area.min")
        if normalised in self.habitable_types:
            return (
                self.habitable_area_mm2,
                self.habitable_width_mm,
                "nbc.room.habitable.area.min",
            )
        if normalised == "staircase":
            return (0, self.stair_width_mm, "nbc.stair.width.min")
        return (0, 0, None)

    def is_wet(self, room_type: str) -> bool:
        return _normalise(room_type) in self.wet_types

    def is_habitable(self, room_type: str) -> bool:
        return _normalise(room_type) in self.habitable_types


def _normalise(room_type: str) -> str:
    """The engine's own alias table (``bedroom_master`` → ``master_bedroom``, …)."""
    _ensure_apps_api_on_path()
    from garh_rules.context import normalise_room_type

    return normalise_room_type(room_type)


_MINIMA_CACHE: Dict[str, NbcRoomMinima] = {}
_VASTU_CACHE: Dict[str, Tuple["VastuZoneRule", ...]] = {}


def _cache_key(root: Optional[str]) -> str:
    return os.path.abspath(root) if root else "<default>"


def load_room_minima(root: Optional[str] = None) -> NbcRoomMinima:
    """Pull every room floor out of the ``nbc-core`` pack. Lazy import + cached.

    The values live in the pack and only in the pack (§6: "keep values in pack,
    never in code"); this reader will crash loudly on a renamed rule id rather than
    fall back to a literal.
    """
    key = _cache_key(root)
    cached = _MINIMA_CACHE.get(key)
    if cached is not None:
        return cached
    _ensure_apps_api_on_path()
    from garh_rules.packs import load_pack_set

    packs = load_pack_set(("nbc-core",), root=root)

    def value_mm2(rule_id: str) -> int:
        return packs.require_rule(rule_id).check.int_param("valueMm2")

    def value_mm(rule_id: str) -> int:
        return packs.require_rule(rule_id).check.int_param("valueMm")

    minima = NbcRoomMinima(
        habitable_area_mm2=value_mm2("nbc.room.habitable.area.min"),
        habitable_width_mm=value_mm("nbc.room.habitable.width.min"),
        kitchen_area_mm2=value_mm2("nbc.room.kitchen.area.min"),
        kitchen_dining_area_mm2=value_mm2("nbc.room.kitchen_dining.area.min"),
        kitchen_width_mm=value_mm("nbc.room.kitchen.width.min"),
        bath_area_mm2=value_mm2("nbc.room.bath.area.min"),
        bath_width_mm=value_mm("nbc.room.bath.width.min"),
        wc_area_mm2=value_mm2("nbc.room.wc.area.min"),
        wc_width_mm=value_mm("nbc.room.wc.width.min"),
        bath_wc_area_mm2=value_mm2("nbc.room.bath_wc.area.min"),
        stair_width_mm=value_mm("nbc.stair.width.min"),
        habitable_types=tuple(sorted(packs.vocabulary.habitable_room_types)),
        wet_types=tuple(sorted(packs.vocabulary.wet_room_types)),
    )
    _MINIMA_CACHE[key] = minima
    return minima


# ---------------------------------------------------------------------------
# Vastu zone allowances — read from the pack, per §5.2 mode semantics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VastuZoneRule:
    """One ``zone_check`` rule from the vastu pack, solver-shaped."""

    rule_id: str
    room_types: Tuple[str, ...]
    allow: Tuple[str, ...]
    fallback_allow: Tuple[str, ...]
    deny: Tuple[str, ...]
    hard: bool
    weight: int


@dataclass(frozen=True)
class ZoneAllowance:
    """What stage A may enforce/reward for one room, already mode-resolved.

    ``allow`` non-empty ⇒ the room's centroid MUST sit in one of those zones (strict
    mode only). ``deny`` ⇒ the centroid must NOT (strict always; advisory only when
    the pack rule is hard — those candidates die at the §5.4 critic anyway).
    ``preferred`` ⇒ objective bonus of ``weight`` when the centroid lands there.
    """

    rule_id: str = ""
    allow: Tuple[str, ...] = ()
    deny: Tuple[str, ...] = ()
    preferred: Tuple[str, ...] = ()
    weight: int = 0


def load_vastu_zone_rules(root: Optional[str] = None) -> Tuple[VastuZoneRule, ...]:
    """Room/stair ``zone_check`` rules out of ``rulepacks/vastu.json``. Cached."""
    key = _cache_key(root)
    cached = _VASTU_CACHE.get(key)
    if cached is not None:
        return cached
    _ensure_apps_api_on_path()
    from garh_rules.packs import load_pack_set

    packs = load_pack_set(("vastu",), root=root)
    rules: List[VastuZoneRule] = []
    for rule in packs.rules:
        if rule.check.type != "zone_check":
            continue
        params = rule.check.params
        target = params.get("target") or {}
        kind = target.get("kind")
        if kind == "room":
            room_types = tuple(str(t) for t in target.get("roomTypes") or ())
        elif kind == "stair":
            room_types = ("staircase",)
        else:
            continue  # facing/service targets are stage-B/critic concerns
        fallback = params.get("fallback") or {}
        rules.append(
            VastuZoneRule(
                rule_id=rule.id,
                room_types=room_types,
                allow=tuple(str(z) for z in params.get("allow") or ()),
                fallback_allow=tuple(str(z) for z in fallback.get("allow") or ()),
                deny=tuple(str(z) for z in params.get("deny") or ()),
                hard=bool(rule.hard) or str(rule.severity) == "fail",
                weight=int(rule.weight or 0),
            )
        )
    out = tuple(rules)
    _VASTU_CACHE[key] = out
    return out


def zone_allowance_for(
    room_type: str, mode: str, rules: Sequence[VastuZoneRule]
) -> Optional[ZoneAllowance]:
    """Resolve every matching pack rule into one mode-aware allowance."""
    if mode == "off":
        return None
    normalised = _normalise(room_type)
    allow: List[str] = []
    deny: List[str] = []
    preferred: List[str] = []
    weight = 0
    rule_ids: List[str] = []
    for rule in rules:
        if normalised not in rule.room_types:
            continue
        rule_ids.append(rule.rule_id)
        if rule.deny:
            if mode == "strict" or rule.hard:
                deny.extend(z for z in rule.deny if z not in deny)
        if rule.allow:
            if mode == "strict":
                for zone in tuple(rule.allow) + tuple(rule.fallback_allow):
                    if zone not in allow:
                        allow.append(zone)
            for zone in rule.allow:
                if zone not in preferred:
                    preferred.append(zone)
            weight = max(weight, rule.weight)
    if not rule_ids:
        return None
    return ZoneAllowance(
        rule_id=",".join(rule_ids),
        allow=tuple(allow),
        deny=tuple(deny),
        preferred=tuple(preferred),
        weight=weight,
    )


def entrance_allowance(mode: str, root: Optional[str] = None) -> Tuple[str, ...]:
    """Allowed entrance sides (compass) from ``vastu.entrance.edge``; empty when off."""
    if mode == "off":
        return ()
    _ensure_apps_api_on_path()
    from garh_rules.packs import load_pack_set

    packs = load_pack_set(("vastu",), root=root)
    rule = packs.require_rule("vastu.entrance.edge")
    return tuple(str(z) for z in rule.check.params.get("allow") or ())


# ---------------------------------------------------------------------------
# the program itself
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdjacencySpec:
    """One adjacency fact stage A must honour (required) or reward (wish)."""

    a_key: str
    b_key: str
    #: 'required' | 'adjacent' (wish) | 'apart' (wish).
    kind: str
    min_shared_edge_mm: int = 0
    weight: int = 0
    source: str = "brief"


@dataclass(frozen=True)
class ProgramRoom:
    """One room, bounds resolved. All lengths mm, areas mm², ratios ×100."""

    key: str
    room_type: str
    min_area_mm2: int
    target_area_mm2: int
    #: 0 means "no cap" (circulation absorbs whatever tiling leaves over).
    max_area_mm2: int
    min_width_mm: int
    max_aspect_x100: int
    #: ``None`` + ``on_all_storeys`` ⇒ repeats identically on every storey.
    storey_index: Optional[int]
    needs_external_wall: bool
    is_wet: bool
    packed: bool = True
    on_all_storeys: bool = False
    vastu: Optional[ZoneAllowance] = None
    must_face: Optional[str] = None
    locked: bool = False
    room_id: Optional[str] = None

    @property
    def is_circulation(self) -> bool:
        return self.room_type in CIRCULATION_TYPES or self.room_type == "staircase"

    def to_room_request(self) -> RoomRequest:
        return RoomRequest(
            key=self.key,
            room_type=self.room_type,
            min_area_mm2=self.min_area_mm2,
            target_area_mm2=self.target_area_mm2,
            min_width_mm=self.min_width_mm,
            max_aspect_x100=self.max_aspect_x100,
            storey_index=self.storey_index,
            needs_external_wall=self.needs_external_wall,
            is_wet=self.is_wet,
            locked=self.locked,
        )


@dataclass(frozen=True)
class RoomProgram:
    """The full §5.2 program: rooms, adjacency, facing and Vastu — one object."""

    rooms: Tuple[ProgramRoom, ...]
    adjacency: Tuple[AdjacencySpec, ...]
    storeys: int
    vastu_mode: str
    entrance_allow: Tuple[str, ...] = ()
    plot_facing: Optional[str] = None
    assumptions: Tuple[Assumption, ...] = ()
    notes: Tuple[str, ...] = ()

    def by_key(self, key: str) -> ProgramRoom:
        for room in self.rooms:
            if room.key == key:
                return room
        raise KeyError(key)

    def packed_rooms_for_storey(self, index: int) -> Tuple[ProgramRoom, ...]:
        """The rooms stage A packs on one storey (repeating rooms included)."""
        return tuple(
            room
            for room in self.rooms
            if room.packed
            and (room.storey_index == index or (room.on_all_storeys and index < self.storeys))
        )

    def unpacked_rooms(self) -> Tuple[ProgramRoom, ...]:
        return tuple(room for room in self.rooms if not room.packed)


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def _scaled(value: int, fraction: Tuple[int, int]) -> int:
    return (value * fraction[0]) // fraction[1]


def _aspect_for(room_type: str, minima: NbcRoomMinima) -> int:
    if room_type in CIRCULATION_TYPES:
        return ASPECT_CIRCULATION_X100
    if minima.is_habitable(room_type) or room_type in _KITCHEN_TYPES:
        return ASPECT_HABITABLE_X100
    return ASPECT_COMPACT_X100


def _needs_external(room_type: str, minima: NbcRoomMinima, requested: bool) -> bool:
    """§5.2: habitable + kitchen must reach the outside; baths may be internal
    (shaft-adjacent); for types the packs don't classify, the brief's flag stands."""
    if minima.is_habitable(room_type) or room_type in _KITCHEN_TYPES:
        return True
    if room_type in CIRCULATION_TYPES or room_type in REPEATING_ROOM_TYPES:
        return False
    if _normalise(room_type) in ("bath", "wc", "bath_wc"):
        return False
    if room_type in ("store", "utility", "pooja", "dress"):
        return False
    return requested


def _resolve_bounds(
    *,
    key: str,
    room_type: str,
    brief_min_area: int,
    target_area: int,
    brief_min_width: int,
    minima: NbcRoomMinima,
    assumptions: List[Assumption],
) -> Tuple[int, int, int, int]:
    """(min_area, target_area, max_area, min_width) with NBC floors applied."""
    nbc_area, nbc_width, cite = minima.floor_for(room_type)
    min_area = max(brief_min_area, nbc_area)
    if target_area <= 0:
        target_area = max(min_area, nbc_area)
    if min_area == 0:
        min_area = _scaled(target_area, MIN_FRACTION_OF_TARGET)
    if target_area < min_area:
        assumptions.append(
            Assumption(
                field="brief.rooms.%s.targetAreaMm2" % key,
                value=min_area,
                reason=(
                    "The brief's %s target was below the code minimum, so we raised "
                    "it to the minimum." % key
                ),
                cite=cite,
                source="solver-program",
            )
        )
        target_area = min_area
    if room_type in CIRCULATION_TYPES:
        max_area = 0  # no cap: circulation absorbs tiling slack, gated by §5.6
    else:
        max_area = max(_scaled(max(target_area, min_area), MAX_FRACTION_OF_TARGET), min_area)
    min_width = max(brief_min_width, nbc_width)
    return (min_area, target_area, max_area, min_width)


def _assign_storeys(
    entries: List[Dict[str, Any]], storeys: int, assumptions: List[Assumption]
) -> None:
    """Fill in missing storey indexes, in place. Deterministic; chipped.

    Explicit assignments always win. Unassigned bedroom-ish rooms spread across the
    upper floors (fewest-rooms-first, then lowest floor); everything else lands on
    the ground floor — entry-adjacent is the safe Indian default.
    """
    if storeys <= 1:
        defaulted = [e["key"] for e in entries if e["storey"] is None and not e["repeat"]]
        for entry in entries:
            if entry["storey"] is None and not entry["repeat"]:
                entry["storey"] = 0
        if defaulted:
            assumptions.append(
                Assumption(
                    field="brief.rooms.storeyIndex",
                    value=0,
                    reason=(
                        "Single-storey plan: %s go on the ground floor."
                        % ", ".join(sorted(defaulted))
                    ),
                    source="solver-program",
                )
            )
        return

    counts: Dict[int, int] = {index: 0 for index in range(storeys)}
    for entry in entries:
        if entry["storey"] is not None:
            counts[entry["storey"]] = counts.get(entry["storey"], 0) + 1

    upper_defaults: List[str] = []
    ground_defaults: List[str] = []
    for entry in entries:
        if entry["storey"] is not None or entry["repeat"]:
            continue
        if entry["type"] in _UPPER_DEFAULT_TYPES:
            target = min(range(1, storeys), key=lambda i: (counts[i], i))
            entry["storey"] = target
            counts[target] += 1
            upper_defaults.append("%s→floor %d" % (entry["key"], target))
        else:
            entry["storey"] = 0
            counts[0] += 1
            ground_defaults.append(entry["key"])
    if upper_defaults:
        assumptions.append(
            Assumption(
                field="brief.rooms.storeyIndex",
                value=1,
                reason=(
                    "The brief didn't pick floors for the bedrooms, so we placed "
                    "them upstairs: %s." % ", ".join(sorted(upper_defaults))
                ),
                source="solver-program",
            )
        )
    if ground_defaults:
        assumptions.append(
            Assumption(
                field="brief.rooms.storeyIndex",
                value=0,
                reason=(
                    "%s go on the ground floor — entry-adjacent is the usual choice."
                    % ", ".join(sorted(ground_defaults))
                ),
                source="solver-program",
            )
        )


def _synthesise_support_rooms(
    entries: List[Dict[str, Any]],
    storeys: int,
    minima: NbcRoomMinima,
    assumptions: List[Assumption],
) -> None:
    """Every plan needs a distributor per storey; wet plans need a shaft; multi-storey
    plans need a staircase. Synthesised rooms are chips, never silent."""
    types_by_storey: Dict[Optional[int], List[str]] = {}
    for entry in entries:
        if entry["repeat"]:
            for index in range(storeys):
                types_by_storey.setdefault(index, []).append(entry["type"])
        else:
            types_by_storey.setdefault(entry["storey"], []).append(entry["type"])

    if storeys > 1 and not any(e["type"] == "staircase" for e in entries):
        entries.append(
            _entry(
                "staircase",
                "staircase",
                target=0,
                min_width=minima.stair_width_mm,
                storey=None,
                repeat=True,
            )
        )
        assumptions.append(
            Assumption(
                field="brief.rooms.staircase",
                value="added",
                reason="A %d-storey brief needs a staircase; we added one." % storeys,
                cite="nbc.stair.width.min",
                source="solver-program",
            )
        )

    for index in range(storeys):
        present = types_by_storey.get(index, [])
        if not any(t in CIRCULATION_TYPES for t in present):
            key = "passage" if index == 0 else "passage%d" % index
            entries.append(
                _entry(key, "passage", target=0, min_width=DOOR_FRONTAGE_MM, storey=index)
            )
            assumptions.append(
                Assumption(
                    field="brief.rooms.%s" % key,
                    value="added",
                    reason=(
                        "Floor %d had no corridor or lobby, so we added a passage to "
                        "connect its rooms." % index
                    ),
                    source="solver-program",
                )
            )

    if any(minima.is_wet(e["type"]) for e in entries) and not any(
        e["type"] == "shaft" for e in entries
    ):
        entries.append(
            _entry("shaft", "shaft", target=0, min_width=0, storey=None, repeat=True)
        )
        assumptions.append(
            Assumption(
                field="brief.rooms.shaft",
                value="added",
                reason=(
                    "Baths and the kitchen drain into a service shaft; we added one "
                    "so the wet rooms can cluster around it."
                ),
                source="solver-program",
            )
        )


def _entry(
    key: str,
    room_type: str,
    *,
    target: int,
    min_width: int,
    storey: Optional[int],
    min_area: int = 0,
    repeat: bool = False,
    must_face: Optional[str] = None,
    locked: bool = False,
    needs_external: bool = True,
) -> Dict[str, Any]:
    return {
        "key": key,
        "type": room_type,
        "minArea": min_area,
        "target": target,
        "minWidth": min_width,
        "storey": storey,
        "repeat": repeat,
        "mustFace": must_face,
        "locked": locked,
        "needsExternal": needs_external,
    }


def _finish(
    entries: List[Dict[str, Any]],
    *,
    storeys: int,
    vastu_mode: str,
    plot_facing: Optional[str],
    wishes: Sequence[AdjacencySpec],
    root: Optional[str],
    assumptions: List[Assumption],
    notes: List[str],
) -> RoomProgram:
    """The shared back half of both builders: floors, support rooms, bounds, Vastu."""
    minima = load_room_minima(root)
    vastu_rules = load_vastu_zone_rules(root) if vastu_mode != "off" else ()

    # Assign floors FIRST so the per-storey distributor check sees where the
    # brief's own circulation rooms actually landed; synthesised rooms arrive with
    # their storey already explicit (or repeating), so they need no assignment.
    _assign_storeys(entries, storeys, assumptions)
    _synthesise_support_rooms(entries, storeys, minima, assumptions)

    rooms: List[ProgramRoom] = []
    for entry in entries:
        room_type = entry["type"]
        packed = room_type not in UNPACKED_ROOM_TYPES
        min_area, target, max_area, min_width = _resolve_bounds(
            key=entry["key"],
            room_type=room_type,
            brief_min_area=entry["minArea"],
            target_area=entry["target"],
            brief_min_width=entry["minWidth"],
            minima=minima,
            assumptions=assumptions,
        )
        rooms.append(
            ProgramRoom(
                key=entry["key"],
                room_type=room_type,
                min_area_mm2=min_area,
                target_area_mm2=target,
                max_area_mm2=max_area,
                min_width_mm=min_width,
                max_aspect_x100=_aspect_for(room_type, minima),
                storey_index=None if entry["repeat"] else entry["storey"],
                needs_external_wall=_needs_external(room_type, minima, entry["needsExternal"]),
                is_wet=minima.is_wet(room_type),
                packed=packed,
                on_all_storeys=entry["repeat"],
                vastu=zone_allowance_for(room_type, vastu_mode, vastu_rules),
                must_face=entry["mustFace"],
                locked=entry["locked"],
            )
        )

    unpacked = sorted(room.key for room in rooms if not room.packed)
    if unpacked:
        notes.append(
            "Not packed by the topology stage (placed after the plan exists): %s."
            % ", ".join(unpacked)
        )

    adjacency: List[AdjacencySpec] = list(wishes)
    keys_by_type: Dict[str, str] = {}
    for room in rooms:
        keys_by_type.setdefault(room.room_type, room.key)
    if "kitchen" in keys_by_type and "dining" in keys_by_type:
        adjacency.insert(
            0,
            AdjacencySpec(
                a_key=keys_by_type["kitchen"],
                b_key=keys_by_type["dining"],
                kind="required",
                min_shared_edge_mm=KITCHEN_DINING_SHARED_EDGE_MM,
                source="§5.2",
            ),
        )

    return RoomProgram(
        rooms=tuple(rooms),
        adjacency=tuple(adjacency),
        storeys=max(1, storeys),
        vastu_mode=vastu_mode,
        entrance_allow=entrance_allowance(vastu_mode, root),
        plot_facing=plot_facing,
        assumptions=tuple(assumptions),
        notes=tuple(notes),
    )


def build_program_from_brief(
    brief_data: Mapping[str, Any],
    *,
    storeys: int,
    vastu_mode: str = "advisory",
    root: Optional[str] = None,
) -> RoomProgram:
    """Corpus/BriefDoc ``data`` → program. ``count > 1`` expands to keyed rooms
    (``bedroom``, ``bedroom2``, …); brief ``adjacency`` wishes ride along by type."""
    assumptions: List[Assumption] = []
    notes: List[str] = []
    entries: List[Dict[str, Any]] = []
    for raw in brief_data.get("rooms") or ():
        room_type = str(raw.get("type") or "other")
        count = int(raw.get("count") or 1)
        for occurrence in range(count):
            key = room_type if occurrence == 0 else "%s%d" % (room_type, occurrence + 1)
            storey = raw.get("storey")
            entries.append(
                _entry(
                    key,
                    room_type,
                    min_area=int(raw.get("minAreaMm2") or 0),
                    target=int(raw.get("targetAreaMm2") or 0),
                    min_width=int(raw.get("minWidthMm") or 0),
                    storey=int(storey) if storey is not None else None,
                    repeat=room_type in REPEATING_ROOM_TYPES and storeys > 1,
                    must_face=str(raw["mustFace"]) if raw.get("mustFace") else None,
                )
            )

    keys_by_type: Dict[str, str] = {}
    for entry in entries:
        keys_by_type.setdefault(entry["type"], entry["key"])
    wishes: List[AdjacencySpec] = []
    for wish in brief_data.get("adjacency") or ():
        a_key = keys_by_type.get(str(wish.get("a")))
        b_key = keys_by_type.get(str(wish.get("b")))
        kind = str(wish.get("wish") or "adjacent")
        if a_key is None or b_key is None or kind not in ("adjacent", "apart"):
            continue
        wishes.append(
            AdjacencySpec(
                a_key=a_key,
                b_key=b_key,
                kind=kind,
                min_shared_edge_mm=DOOR_FRONTAGE_MM if kind == "adjacent" else 0,
                weight=int(wish.get("weight") or 0),
            )
        )

    mode = str(brief_data.get("vastuMode") or vastu_mode)
    return _finish(
        entries,
        storeys=storeys,
        vastu_mode=mode if mode in ("off", "advisory", "strict") else vastu_mode,
        plot_facing=str(brief_data["plotFacing"]) if brief_data.get("plotFacing") else None,
        wishes=wishes,
        root=root,
        assumptions=assumptions,
        notes=notes,
    )


def program_from_params(params: SolveParams, *, root: Optional[str] = None) -> RoomProgram:
    """:class:`SolveParams` (the worker payload path) → the same program shape."""
    assumptions: List[Assumption] = []
    notes: List[str] = []
    entries: List[Dict[str, Any]] = []
    for request in params.rooms:
        entries.append(
            _entry(
                request.key,
                request.room_type,
                min_area=request.min_area_mm2,
                target=request.target_area_mm2,
                min_width=request.min_width_mm,
                storey=request.storey_index,
                repeat=request.room_type in REPEATING_ROOM_TYPES and params.storeys > 1,
                locked=request.locked,
                needs_external=request.needs_external_wall,
            )
        )
    return _finish(
        entries,
        storeys=params.storeys,
        vastu_mode=params.vastu_mode,
        plot_facing=None,
        wishes=(),
        root=root,
        assumptions=assumptions,
        notes=notes,
    )


def clear_program_caches() -> None:
    """Test hook: forget cached pack reads (mirrors ``garh_rules.clear_pack_cache``)."""
    _MINIMA_CACHE.clear()
    _VASTU_CACHE.clear()


__all__ = [
    "ASPECT_CIRCULATION_X100",
    "ASPECT_COMPACT_X100",
    "ASPECT_HABITABLE_X100",
    "CIRCULATION_TYPES",
    "DEFAULT_STOREY_HEIGHT_MM",
    "DOOR_FRONTAGE_MM",
    "FALLBACK_DISTRIBUTOR_TYPES",
    "KITCHEN_DINING_SHARED_EDGE_MM",
    "MAX_FRACTION_OF_TARGET",
    "MIN_FRACTION_OF_TARGET",
    "REPEATING_ROOM_TYPES",
    "UNPACKED_ROOM_TYPES",
    "AdjacencySpec",
    "NbcRoomMinima",
    "ProgramRoom",
    "RoomProgram",
    "VastuZoneRule",
    "ZoneAllowance",
    "build_program_from_brief",
    "clear_program_caches",
    "entrance_allowance",
    "load_room_minima",
    "load_vastu_zone_rules",
    "program_from_params",
    "zone_allowance_for",
]
