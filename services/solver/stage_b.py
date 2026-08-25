"""§5.3 stage B — coarse cell layout → buildable multi-storey HouseModel fragment.

    "Snap all coordinates to 115mm module; convert cell layout → wall network …;
     insert doors …, windows …, ventilators for internal baths on shafts. Run
     model invariants; auto-repair trivial violations (nudge by one module) else
     discard candidate." — engineering playbook §5.3.

**ortools-free by design.** Everything here is integer geometry and pack lookups,
so this half of the solver is fully provable on a machine with only a Python
interpreter. The CP-SAT half (§5.2) hands over a :class:`~services.solver.stages.
Candidate`; this module turns it into the model fragment the critic (§5.4), the
gates (§5.6) and ``solver.apply_option`` consume.

THE PIPELINE ORDER inside :func:`refine` (fixed — tests pin it):

1. group placements per storey, snap to the 115mm module anchored at the
   envelope bbox minimum (:class:`~services.solver.walls.CellLayout`);
2. walls: deduped network per storey (:func:`~services.solver.walls.
   build_wall_network`) — two rooms sharing an edge get ONE wall;
3. openings: doors → windows → ventilators (:mod:`services.solver.openings`),
   NBC limits read from the pack, never hard-coded;
4. stairs: risers derived from the storey height under the pack's riser/tread/
   width limits (:func:`riser_schedule`, :func:`plan_stair`); one stair on every
   storey that has a floor above it, hosted in that storey's ``staircase`` room;
5. slabs: one floor slab per storey; shaft/duct/void rooms and the arriving
   stair flight become cutouts;
6. repair: :func:`services.solver.repair.repair_house` — the same §3 invariants
   ``fold()`` enforces; trivial fixes are applied, anything else discards the
   candidate with a typed reason.

DETERMINISM. Same candidate + params ⇒ same geometry, byte for byte. Element ids
are minted through ``garh_model.ids.new_id`` (the repo's ULID helper) in one
documented order — storeys ascending, then per storey: storey id, walls in
network order, main door, internal doors, windows, stair, then all slabs — so a
test that installs ``seeded_ulid_factory`` gets byte-identical JSON, and
production gets globally unique ids from the same code path.

FAILURE IS TYPED, NEVER SILENT. Wall synthesis raises
:class:`~services.solver.walls.WallSynthesisError`, openings raise
:class:`~services.solver.openings.OpeningError`, stairs and storey shape
problems raise :class:`StageBError`, and repair returns a typed
:class:`~services.solver.repair.DiscardReason`. :func:`stage_b_refine` — the
pipeline's ``StageBFn`` — converts any of these into ``None`` after logging the
real reason, which is what §15's honest generation theater shows the user.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from services.common.logging import get_logger
from services.solver.geometry import Polygon, Pt, bbox, point_in_polygon, round_half_away, signed_area2
from services.solver.openings import (
    NbcOpeningLimits,
    OpeningError,
    OpeningSpec,
    load_nbc_limits,
    place_doors,
    place_windows,
)
from services.solver.repair import DiscardReason, RepairAction, repair_house
from services.solver.stages import Candidate
from services.solver.types import (
    BuildableEnvelope,
    RoomPlacement,
    SolveParams,
)
from services.solver.walls import (
    CellLayout,
    WallNetwork,
    WallSynthesisError,
    build_wall_network,
    clear_polygon,
)

log = get_logger("solver.stage_b")

#: Room types whose floor area is a slab void, not a floor (§5.2 shafts, wells).
CUTOUT_ROOM_TYPES = frozenset({"shaft", "duct", "void"})

#: Mirror of ``garh_model.validate.STAIR_RISE_TOLERANCE_MM`` (asserted in tests).
STAIR_RISE_TOLERANCE_MM = 10

#: Storey display names, index-aligned; beyond these it's "Floor N".
_STOREY_NAMES = ("Ground Floor", "First Floor", "Second Floor", "Third Floor")


class StageBError(ValueError):
    """A candidate stage B cannot refine. Typed discard reason (§15)."""

    def __init__(self, code: str, message: str, *, detail: Optional[str] = None) -> None:
        super().__init__("%s — %s" % (code, message))
        self.code = code
        self.message = message
        self.detail = detail


# ---------------------------------------------------------------------------
# pack-sourced stair limits (§6: values live in the pack, never in code)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StairLimits:
    """NBC stair limits, read from ``rulepacks/nbc-core.json``."""

    riser_max_mm: int
    tread_min_mm: int
    width_min_mm: int
    headroom_min_mm: int


def load_stair_limits(root: Optional[str] = None) -> StairLimits:
    """Pull riser/tread/width/headroom from the nbc-core pack.

    Lazy import, same reasoning as ``openings.load_nbc_limits``: the §5.4 critic
    checks these exact rules, so reading the same pack rows means the geometry
    this module emits and the compliance verdict cannot fork.
    """
    from services.solver.openings import _ensure_apps_api_on_path

    _ensure_apps_api_on_path()
    from garh_rules.packs import load_pack_set

    packs = load_pack_set(("nbc-core",), root=root)

    def value(rule_id: str) -> int:
        return packs.require_rule(rule_id).check.int_param("valueMm")

    return StairLimits(
        riser_max_mm=value("nbc.stair.riser.max"),
        tread_min_mm=value("nbc.stair.tread.min"),
        width_min_mm=value("nbc.stair.width.min"),
        headroom_min_mm=value("nbc.stair.headroom.min"),
    )


# ---------------------------------------------------------------------------
# stairs (§5.3: "risers from storey height, NBC riser/tread")
# ---------------------------------------------------------------------------


def riser_schedule(
    storey_height_mm: int,
    riser_max_mm: int,
    *,
    tolerance_mm: int = STAIR_RISE_TOLERANCE_MM,
) -> Tuple[int, int]:
    """``(risers_count, riser_mm)`` for one storey. Exact integers.

    The smallest riser count that (a) keeps every riser at or under the pack
    maximum and (b) lands ``risers_count × riser_mm`` within the model core's
    ±10mm rise tolerance. Fewest risers = shortest flight = easiest fit, so the
    search ascends and the first hit is the answer. Riser is rounded half-away
    (the model core's rounding contract).
    """
    if storey_height_mm <= 0:
        raise StageBError(
            "STOREY_HEIGHT_INVALID",
            "A storey height of %dmm cannot carry a stair." % storey_height_mm,
        )
    count = max(2, -((-storey_height_mm) // riser_max_mm))  # ceil division
    while count <= 60:  # op-validation ceiling for risersCount
        riser = round_half_away(storey_height_mm / count)
        if riser <= riser_max_mm and abs(riser * count - storey_height_mm) <= tolerance_mm:
            return count, riser
        count += 1
    raise StageBError(
        "STAIR_RISE_UNSOLVABLE",
        "No riser count up to 60 divides a %dmm storey within ±%dmm."
        % (storey_height_mm, tolerance_mm),
    )


def plan_stair(
    clear_poly: Polygon,
    storey_height_mm: int,
    limits: StairLimits,
) -> Dict[str, Any]:
    """A Stair JSON body (minus ``id``/``storeyId``) inside the stair room.

    Deterministic placement rules:

    * the flight runs along the clear rectangle's LONGER axis — up-direction
      ``N`` for a vertical run, ``E`` for a horizontal one, origin at the
      south-west corner of the footprint;
    * ``straight`` is tried first (one flight, ``(n-1) × tread`` going);
      ``dogleg`` second (two flights beside a half landing of depth = flight
      width, width = 2 × flight width) — the standard Indian residential stair;
    * tread and width are the pack minimums — the compact end of legal, which is
      what a first-cut plan should show; the architect widens, never shrinks;
    * the footprint's corners, edge midpoints and centre must all lie inside
      the room's clear polygon (clear polygons can be rectilinear, not just
      rectangles). A stair that cannot fit is a typed discard, never a squeeze.
    """
    count, riser = riser_schedule(storey_height_mm, limits.riser_max_mm)
    tread = limits.tread_min_mm
    width = limits.width_min_mm

    min_x, min_y, max_x, max_y = bbox(clear_poly)
    room_w = max_x - min_x
    room_d = max_y - min_y
    vertical = room_d >= room_w
    long_len = room_d if vertical else room_w
    short_len = room_w if vertical else room_d

    candidates: List[Tuple[str, int, int, Optional[Dict[str, int]]]] = []
    straight_going = (count - 1) * tread
    if straight_going <= long_len and width <= short_len:
        candidates.append(("straight", width, straight_going, None))
    flights = -((-count) // 2)  # ceil(count / 2) risers in the longer flight
    dogleg_going = (flights - 1) * tread + width  # + half landing (depth = width)
    if dogleg_going <= long_len and 2 * width <= short_len:
        candidates.append(
            ("dogleg", 2 * width, dogleg_going, {"widthMm": 2 * width, "depthMm": width})
        )
    if not candidates:
        raise StageBError(
            "STAIR_DOES_NOT_FIT",
            "Neither a straight nor a dogleg stair fits a %d×%dmm stair room "
            "(need %dmm going straight / %dmm dogleg at %dmm tread, %dmm width)."
            % (room_w, room_d, straight_going, dogleg_going, tread, width),
        )
    kind, breadth, going, landing = candidates[0]

    if vertical:
        direction = "N"  # forward +Y, right +X: footprint grows E and N of origin
        origin = (min_x, min_y)
        rect = (min_x, min_y, min_x + breadth, min_y + going)
    else:
        direction = "E"  # forward +X, right -Y: origin at the footprint's NW corner
        origin = (min_x, min_y + breadth)
        rect = (min_x, min_y, min_x + going, min_y + breadth)

    x1, y1, x2, y2 = rect
    probes = (
        (x1, y1), (x2, y1), (x2, y2), (x1, y2),
        ((x1 + x2) // 2, y1), ((x1 + x2) // 2, y2),
        (x1, (y1 + y2) // 2), (x2, (y1 + y2) // 2),
        ((x1 + x2) // 2, (y1 + y2) // 2),
    )
    if not all(point_in_polygon(p, clear_poly) for p in probes):
        raise StageBError(
            "STAIR_DOES_NOT_FIT",
            "The stair footprint pokes out of the stair room's clear polygon.",
            detail="footprint %s in room bbox %s" % (rect, (min_x, min_y, max_x, max_y)),
        )
    return {
        "kind": kind,
        "origin": {"x": origin[0], "y": origin[1]},
        "direction": direction,
        "riserMm": riser,
        "treadMm": tread,
        "widthMm": width,
        "risersCount": count,
        "landing": landing,
    }


# ---------------------------------------------------------------------------
# plot-edge compass helpers (road facing / entry side, plot-local axes)
# ---------------------------------------------------------------------------


def edge_outward_compass(polygon: Polygon, edge_index: int) -> Optional[str]:
    """Dominant compass direction of a plot edge's OUTWARD normal.

    Plot-local axes (+Y north on the drawing), matching the ``outward`` field of
    :class:`~services.solver.walls.ExternalSpan` — so "this bath faces the road"
    is one string comparison. Winding-aware: works for CW and CCW rings.
    """
    count = len(polygon)
    if count < 3:
        return None
    ax, ay = polygon[edge_index % count]
    bx, by = polygon[(edge_index + 1) % count]
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return None
    if signed_area2(polygon) > 0:  # CCW: interior left ⇒ outward is right of travel
        nx, ny = dy, -dx
    else:
        nx, ny = -dy, dx
    if abs(nx) >= abs(ny):
        return "E" if nx > 0 else "W"
    return "N" if ny > 0 else "S"


def road_outwards(params: SolveParams) -> FrozenSet[str]:
    """Compass directions that face a road — a bath window never faces these."""
    out = set()
    for edge in params.edges:
        if edge.road_width_mm > 0:
            compass = edge_outward_compass(params.plot_polygon, edge.index)
            if compass is not None:
                out.add(compass)
    return frozenset(out)


def entry_outward(params: SolveParams) -> Optional[str]:
    """Which side the main door prefers: the front edge, else the widest road."""
    fronts = [edge for edge in params.edges if edge.role == "front"]
    if fronts:
        return edge_outward_compass(params.plot_polygon, fronts[0].index)
    roads = sorted(
        (edge for edge in params.edges if edge.road_width_mm > 0),
        key=lambda e: (-e.road_width_mm, e.index),
    )
    if roads:
        return edge_outward_compass(params.plot_polygon, roads[0].index)
    return None


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OptionDraft:
    """Stage B's output: the model fragment plus the honest paper trail."""

    #: Full HouseModel JSON (camelCase, integer mm) — ``HouseModel.from_json``
    #: parses it verbatim; ``repair_house`` already validated it.
    house: Dict[str, Any]
    #: Trivial repairs that were applied (usually empty).
    repairs: Tuple[RepairAction, ...]
    #: Structured rationale seed facts — the Phase-6 LLM verbalises, never adds.
    facts: Tuple[str, ...]


def _storey_name(index: int) -> str:
    return _STOREY_NAMES[index] if index < len(_STOREY_NAMES) else "Floor %d" % index


def _pt_json(point: Pt) -> Dict[str, int]:
    return {"x": point[0], "y": point[1]}


def _poly_json(polygon: Polygon) -> List[Dict[str, int]]:
    return [_pt_json(p) for p in polygon]


def _opening_json(spec: OpeningSpec, opening_id: str, wall_ids: Sequence[str]) -> Dict[str, Any]:
    return {
        "id": opening_id,
        "wallId": wall_ids[spec.wall_index],
        "kind": spec.kind,
        "widthMm": spec.width_mm,
        "heightMm": spec.height_mm,
        "sillMm": spec.sill_mm,
        "offsetMm": spec.offset_mm,
        "swing": spec.swing,
        "tag": None,
    }


def refine(
    candidate: Candidate,
    params: SolveParams,
    envelope: BuildableEnvelope,
    *,
    storey_height_mm: Optional[int] = None,
    rulepack_root: Optional[str] = None,
) -> OptionDraft:
    """§5.3 end to end. Raises typed errors; never returns a broken fragment.

    ``storey_height_mm`` defaults to the model core's Indian residential default
    (3000) — recorded as a fact so the assumption stays visible upstream.
    """
    from services.solver.repair import ensure_model_importable

    ensure_model_importable()
    from garh_model.fold import stair_footprint_polygon
    from garh_model.geometry import Pt as MPt, polygon_area_mm2
    from garh_model.ids import new_id
    from garh_model.model import DEFAULTS, ROOM_TYPES, SCHEMA_VERSION, Stair

    height = storey_height_mm if storey_height_mm is not None else DEFAULTS.storey_height_mm
    opening_limits: NbcOpeningLimits = load_nbc_limits(root=rulepack_root)
    stair_limits = load_stair_limits(root=rulepack_root)

    by_storey: Dict[int, List[RoomPlacement]] = {}
    for placement in candidate.placements:
        if placement.room_type not in ROOM_TYPES:
            raise StageBError(
                "UNKNOWN_ROOM_TYPE",
                "Stage A emitted room type %r, which the model does not know."
                % placement.room_type,
            )
        by_storey.setdefault(placement.storey_index, []).append(placement)
    if not by_storey:
        raise StageBError("EMPTY_CANDIDATE", "The candidate places no rooms at all.")
    storey_indexes = sorted(by_storey)
    if storey_indexes != list(range(len(storey_indexes))):
        raise StageBError(
            "STOREYS_NOT_CONTIGUOUS",
            "Storey indexes %s are not contiguous from 0." % storey_indexes,
        )

    min_x, min_y, _, _ = bbox(envelope.polygon)
    snap_origin: Pt = (min_x, min_y)
    entry_side = entry_outward(params)
    roads = road_outwards(params)
    locked_ids = frozenset(params.locked_room_ids)

    storeys_json: List[Dict[str, Any]] = []
    walls_json: List[Dict[str, Any]] = []
    openings_json: List[Dict[str, Any]] = []
    rooms_json: List[Dict[str, Any]] = []
    stairs_json: List[Dict[str, Any]] = []
    slabs_json: List[Dict[str, Any]] = []
    doors_by_room: Dict[str, str] = {}
    windows_by_room: Dict[str, List[str]] = {}
    facts: List[str] = ["storeyHeightMm:%d(default)" % height]

    target_by_key = {room.key: room.target_area_mm2 for room in params.rooms}
    per_storey: List[Tuple[str, CellLayout, WallNetwork, Dict[str, Polygon]]] = []

    for index in storey_indexes:
        layout = CellLayout.from_placements(by_storey[index], snap_origin=snap_origin)
        network = build_wall_network(layout)

        storey_id = new_id("storey")
        storeys_json.append(
            {
                "id": storey_id,
                "name": _storey_name(index),
                "level": {
                    "fflMm": DEFAULTS.plinth_mm + index * height,
                    "slabThicknessMm": DEFAULTS.slab_thickness_mm,
                    "sillDefaultMm": None,
                    "lintelDefaultMm": None,
                },
                "heightMm": height,
            }
        )

        wall_ids: List[str] = []
        for wall in network.walls:
            wall_id = new_id("wall")
            wall_ids.append(wall_id)
            walls_json.append(
                {
                    "id": wall_id,
                    "storeyId": storey_id,
                    "a": _pt_json(wall.a),
                    "b": _pt_json(wall.b),
                    "thicknessMm": wall.thickness_mm,
                    "kind": wall.kind,
                    "loadBearing": wall.kind == "external",
                }
            )

        clear_polys: Dict[str, Polygon] = {}
        clear_areas: Dict[str, int] = {}
        for room in layout.rooms:
            poly = clear_polygon(layout, network, room.key)
            clear_polys[room.key] = poly
            clear_areas[room.key] = polygon_area_mm2([MPt(x, y) for x, y in poly])

        doors, main_door, occupancy = place_doors(
            layout,
            network,
            limits=opening_limits,
            door_height_mm=DEFAULTS.lintel_default_mm,
            entry_outward=entry_side if index == 0 else None,
        )
        windows = place_windows(
            layout,
            network,
            occupancy,
            clear_areas,
            limits=opening_limits,
            sill_mm=DEFAULTS.sill_default_mm,
            lintel_mm=DEFAULTS.lintel_default_mm,
            road_outwards=roads,
        )
        ordered_doors = ((main_door,) if main_door is not None else ()) + tuple(doors)
        for spec in ordered_doors:
            opening_id = new_id("opening")
            openings_json.append(_opening_json(spec, opening_id, wall_ids))
            doors_by_room[
                "%d:%s" % (index, spec.room_key)
            ] = opening_id
            if spec.role == "main-entrance":
                facts.append("mainDoor:%s" % (entry_side or "any"))
        for spec in windows:
            opening_id = new_id("opening")
            openings_json.append(_opening_json(spec, opening_id, wall_ids))
            windows_by_room.setdefault("%d:%s" % (index, spec.room_key), []).append(
                opening_id
            )

        for room in layout.rooms:
            rooms_json.append(
                {
                    "id": room.room_id if room.room_id is not None else new_id("room"),
                    "storeyId": storey_id,
                    "type": room.room_type,
                    "name": "",
                    "polygon": _poly_json(clear_polys[room.key]),
                    "areaMm2": clear_areas[room.key],
                    "tags": [],
                    "locked": room.room_id is not None and room.room_id in locked_ids,
                    "targetAreaMm2": target_by_key.get(room.key),
                    "mustFace": None,
                }
            )

        # -- stair: every storey with a floor above needs a flight going up.
        if index < len(storey_indexes) - 1:
            stair_rooms = sorted(
                r.key for r in layout.rooms if r.room_type == "staircase"
            )
            if not stair_rooms:
                raise StageBError(
                    "STAIR_ROOM_MISSING",
                    "Storey %d has a floor above but no staircase room." % index,
                )
            body = plan_stair(clear_polys[stair_rooms[0]], height, stair_limits)
            stair_json = dict(body)
            stair_json["id"] = new_id("stair")
            stair_json["storeyId"] = storey_id
            stairs_json.append(stair_json)
            facts.append(
                "stair:%s@storey%d:%dx%dmm"
                % (body["kind"], index, body["risersCount"], body["riserMm"])
            )

        per_storey.append((storey_id, layout, network, clear_polys))

    # -- slabs: one floor slab per storey; shafts + the arriving flight are voids.
    for position, (storey_id, layout, network, clear_polys) in enumerate(per_storey):
        cutouts: List[List[Dict[str, int]]] = []
        for room in layout.rooms:
            if room.room_type in CUTOUT_ROOM_TYPES:
                cutouts.append(_poly_json(clear_polys[room.key]))
        if position > 0:
            below = [s for s in stairs_json if s["storeyId"] == per_storey[position - 1][0]]
            for stair_json in below:
                footprint = stair_footprint_polygon(Stair.from_json(stair_json))
                cutouts.append([{"x": p.x, "y": p.y} for p in footprint])
        slabs_json.append(
            {
                "id": new_id("slab"),
                "storeyId": storey_id,
                "kind": "floor",
                "polygon": _poly_json(network.outline),
                "thicknessMm": DEFAULTS.slab_thickness_mm,
                "cutouts": cutouts,
            }
        )

    house: Dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "storeys": storeys_json,
        "walls": walls_json,
        "openings": openings_json,
        "rooms": rooms_json,
        "stairs": stairs_json,
        "slabs": slabs_json,
        "columns": [],
        "furniture": [],
        "facade": {"kitId": None, "seed": 0, "colorwayId": None, "components": []},
        "materials": [],
        "levels": {
            "plinthMm": DEFAULTS.plinth_mm,
            "fflPerStoreyMm": [
                DEFAULTS.plinth_mm + i * height for i in range(len(storeys_json))
            ],
            "sillDefaultMm": DEFAULTS.sill_default_mm,
            "lintelDefaultMm": DEFAULTS.lintel_default_mm,
            "parapetMm": DEFAULTS.parapet_mm,
        },
        "balconies": [],
        "meta": {
            "unitsDisplay": "ft-in",
            "regProfileRef": params.profile.city_pack,
            "briefRef": None,
        },
    }

    outcome = repair_house(house, params)
    if not outcome.ok:
        reason: DiscardReason = outcome.discard  # type: ignore[assignment]
        raise StageBError(reason.code, reason.message, detail=reason.detail)

    doors_count = sum(1 for o in openings_json if o["kind"] == "door")
    facts.extend(
        [
            "doors:%d" % doors_count,
            "windows:%d" % sum(1 for o in openings_json if o["kind"] == "window"),
            "ventilators:%d" % sum(1 for o in openings_json if o["kind"] == "ventilator"),
            "storeys:%d" % len(storeys_json),
            "repairs:%d" % len(outcome.actions),
        ]
    )
    repaired = outcome.house
    assert repaired is not None  # ok=True guarantees it; keeps mypy honest
    repaired = dict(repaired)
    repaired["solverMeta"] = {
        "facts": sorted(facts),
        "repairs": [action.to_json() for action in outcome.actions],
        "doorsByRoom": {k: doors_by_room[k] for k in sorted(doors_by_room)},
        "windowsByRoom": {
            k: list(windows_by_room[k]) for k in sorted(windows_by_room)
        },
        "entryOutward": entry_side,
    }
    return OptionDraft(
        house=repaired,
        repairs=outcome.actions,
        facts=tuple(sorted(facts)),
    )


def stage_b_refine(
    candidate: Candidate, params: SolveParams, envelope: BuildableEnvelope
) -> Optional[Mapping[str, Any]]:
    """The pipeline's ``StageBFn``: model fragment, or ``None`` = discard.

    Every discard is logged with its typed code BEFORE returning ``None`` — the
    pipeline's generic "could not be refined" line is the fallback, this log
    line is the truth the job card shows.
    """
    try:
        return refine(candidate, params, envelope).house
    except (WallSynthesisError, OpeningError, StageBError) as exc:
        log.info(
            "solver.stage_b_discard",
            anchor=candidate.stair_anchor.id,
            code=exc.code,
            reason=exc.message,
            detail=getattr(exc, "detail", None),
        )
        return None


__all__ = [
    "CUTOUT_ROOM_TYPES",
    "STAIR_RISE_TOLERANCE_MM",
    "OptionDraft",
    "StageBError",
    "StairLimits",
    "edge_outward_compass",
    "entry_outward",
    "load_stair_limits",
    "plan_stair",
    "refine",
    "riser_schedule",
    "road_outwards",
    "stage_b_refine",
]
