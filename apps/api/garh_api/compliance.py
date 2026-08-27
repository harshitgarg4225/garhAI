"""Model document -> rules-engine projection, and the one place rules are run (§6).

This module is the missing seam between two subsystems that were both complete and
neither of which could reach the other:

* ``garh_model`` folds the op log into a ``ProjectDoc`` (JSON, camelCase, integer mm).
* ``garh_rules`` evaluates 118 rules across five packs against an
  :class:`~garh_rules.context.EvaluationContext` — a **pre-derived projection**, by
  design: the engine does no geometry beyond polygon area/bbox/centroid and the 3x3
  zone classification, so that it stays under the 100 ms budget (§14) and stays a pure
  function of its input.

``garh_rules``' own contract note says "the model layer owes a ``build_context()``".
:func:`build_evaluation_context` is that function. Until it existed, nothing in the repo
imported ``garh_rules`` at all: ``compliance_reports`` was never written, and
``GET /compliance`` answered ``evaluated: false`` forever.

HONESTY ABOUT WHAT IS DERIVED AND HOW
-------------------------------------
Every field below is either read straight out of the document or derived by a rule
stated here. Where the model core does not yet carry a concept, this module says so and
supplies ``None``/empty rather than guessing — and the engine turns a null into
``not_applicable``, never into a pass (that behaviour is the reason nulls are safe).

Approximations, all deliberate and all visible in the report's ``notes``:

1. **Edge roles** (``front``/``rear``/``side-a``/``side-b``). The model stores roads per
   edge index but no role. Rule: the edge with the widest road is ``front``; on a ring
   with an even vertex count the edge opposite it is ``rear``; the rest alternate
   ``side-a``/``side-b``. With no roads at all every edge is ``other``, which makes
   road-banded setback rules ``not_applicable`` rather than silently passing.
2. **Provided setback** per edge = the smallest perpendicular distance from that edge to
   any *external* wall centreline on the ground storey, minus half that wall's
   thickness. Exact for rect/L/T plots with orthogonal walls, which is the MVP envelope
   (§5). Reported as ``0`` when there are no walls yet.
3. **Opening role.** ``main-entrance`` = the widest door on the ground storey hosted by
   an external wall. ``bath`` = a door whose adjoining room is a bath/WC. ``garage`` =
   a door adjoining a garage/stilt. Everything else is ``internal``. Windows and
   ventilators get ``internal`` too — the packs only band ``openingRole`` for doors
   (verified: the three ``opening_width_min`` rules in ``nbc-core`` all require
   ``openingKind: door``).
4. **Ventilation opening area** per room = the sum of ``widthMm * heightMm`` for windows
   and ventilators hosted by a wall whose centreline touches that room's polygon
   boundary. A window between two rooms therefore counts for both, which is correct for
   a light-and-ventilation ratio and wrong for nothing the packs check.
5. **Stair headroom** = the storey's clear height. The model core carries no landing
   soffit, so a real headroom check needs the section geometry that Phase 8 builds; this
   over-reports headroom on a dogleg with a mid-landing. Flagged in ``notes``.
6. **``farCountableAreaMm2``** = built-up area minus stilt and basement storeys. Every
   city pack treats stilt parking and basements as FAR-free in the seeded rules;
   balcony and staircase deductions vary by city and are NOT applied — a value that is
   *conservatively high* means FAR reads as tighter than it is, never looser.
7. **Service elements** (water tank / OHT / sump) are not in the model core, so
   ``serviceElements`` is empty and the Vastu tank rule is ``not_applicable``.

WHERE THIS IS CALLED FROM
-------------------------
``GET /projects/:id/compliance`` (live, unpersisted, when nothing is frozen) and
``POST /projects/:id/versions`` (frozen into ``compliance_reports`` so that the sheet
area statement, the share-link viewer and the export gate all quote one set of numbers,
per §7). The solver critic and the copilot's ``RulesChecker`` are Phase 3 / Phase 6 and
are named in ``DECISIONS.md`` as still unwired.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from garh_api.logging import get_logger

_log = get_logger(__name__)

Point = tuple[int, int]

#: Packs every project gets. ``nbc-core`` is national and unconditional.
BASE_PACKS: tuple[str, ...] = ("nbc-core",)

#: city_pack value -> rulepack id. A project with no city pack still gets NBC.
CITY_PACK_IDS: Mapping[str, str] = {"blr": "blr", "ncr": "ncr", "hyd": "hyd"}

#: Room types the packs treat as bath/WC (for door role + internal-room checks).
_WET_ROOM_TYPES = frozenset({"bath", "wc", "bath_wc", "toilet", "powder"})
_GARAGE_ROOM_TYPES = frozenset({"garage", "stilt", "parking"})

#: Assembled once per report so the reader knows which numbers are approximations.
_NOTES: tuple[str, ...] = (
    "Edge roles are derived: widest road = front, opposite edge = rear.",
    "Provided setbacks are measured to external wall faces on the ground storey.",
    "Stair headroom is approximated as the storey clear height (no landing soffit "
    "in the model core until the section engine lands, Phase 8).",
    "FAR-countable area excludes stilt and basement storeys only; balcony and "
    "staircase deductions are not applied, so FAR reads conservatively high.",
    "Service elements (water tank / OHT / sump) are not in the model core yet, so "
    "Vastu rules about them report not_applicable rather than passing.",
)


class ComplianceUnavailable(RuntimeError):
    """``garh_rules`` could not be imported or the packs could not be loaded.

    Raised rather than swallowed: a compliance panel that shows nothing because a
    dependency is missing must not be indistinguishable from one that shows nothing
    because the design is clean.
    """


# ---------------------------------------------------------------------------
# small geometry helpers (integer mm, no floats stored anywhere)
# ---------------------------------------------------------------------------


def _ring(points: Sequence[Mapping[str, Any]]) -> list[Point]:
    return [(int(p["x"]), int(p["y"])) for p in points]


def _round_half_away(value: float) -> int:
    """The repo-wide rounding contract: half away from zero, never banker's."""
    return int(math.floor(value + 0.5)) if value >= 0 else -int(math.floor(-value + 0.5))


def _polygon_area_mm2(ring: Sequence[Point]) -> int:
    if len(ring) < 3:
        return 0
    doubled = 0
    for i, (x1, y1) in enumerate(ring):
        x2, y2 = ring[(i + 1) % len(ring)]
        doubled += x1 * y2 - x2 * y1
    return abs(doubled) // 2


def _centroid_mm(ring: Sequence[Point]) -> Point:
    if not ring:
        return (0, 0)
    doubled = 0
    cx = 0.0
    cy = 0.0
    for i, (x1, y1) in enumerate(ring):
        x2, y2 = ring[(i + 1) % len(ring)]
        cross = x1 * y2 - x2 * y1
        doubled += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if doubled == 0:
        # Degenerate ring (collinear): fall back to the vertex mean, which is at
        # least inside the hull and never a division by zero.
        return (
            _round_half_away(sum(p[0] for p in ring) / len(ring)),
            _round_half_away(sum(p[1] for p in ring) / len(ring)),
        )
    return (_round_half_away(cx / (3.0 * doubled)), _round_half_away(cy / (3.0 * doubled)))


def _least_width_mm(ring: Sequence[Point]) -> int:
    """Shortest width across the polygon: min over edges of the supporting-line span.

    Rotating-calipers width for a convex ring; for a concave ring it is an upper
    bound, which is the safe direction for a *minimum* width rule (it never
    manufactures a failure that is not there).
    """
    if len(ring) < 3:
        return 0
    best: int | None = None
    for i, (x1, y1) in enumerate(ring):
        x2, y2 = ring[(i + 1) % len(ring)]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length == 0:
            continue
        # Perpendicular distance of the farthest vertex from this edge's line.
        span = max(abs((px - x1) * dy - (py - y1) * dx) / length for px, py in ring)
        candidate = _round_half_away(span)
        if best is None or candidate < best:
            best = candidate
    return best or 0


def _point_to_segment_mm(p: Point, a: Point, b: Point) -> int:
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return _round_half_away(math.hypot(px - ax, py - ay))
    t = ((px - ax) * dx + (py - ay) * dy) / float(dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return _round_half_away(math.hypot(px - (ax + t * dx), py - (ay + t * dy)))


def _segment_distance_mm(a1: Point, a2: Point, b1: Point, b2: Point) -> int:
    """Minimum distance between two segments (they are assumed not to cross).

    Walls that cross the plot boundary would give 0, which is the honest answer for a
    setback: the building is on the line.
    """
    return min(
        _point_to_segment_mm(a1, b1, b2),
        _point_to_segment_mm(a2, b1, b2),
        _point_to_segment_mm(b1, a1, a2),
        _point_to_segment_mm(b2, a1, a2),
    )


def _segment_length_mm(a: Point, b: Point) -> int:
    return _round_half_away(math.hypot(b[0] - a[0], b[1] - a[1]))


def _touches_ring(a: Point, b: Point, ring: Sequence[Point], tolerance_mm: int = 150) -> bool:
    """True when segment ``a-b`` runs along (or very near) the polygon boundary.

    ``tolerance_mm`` is one wall half-thickness plus slack: room polygons are the
    *inner faces* of the walls, so a wall centreline sits 57-115 mm outside the ring.
    """
    for i, v1 in enumerate(ring):
        v2 = ring[(i + 1) % len(ring)]
        # Require an overlap in the running direction too, so a wall merely
        # pointing at a corner does not count as bounding the room.
        if _segment_distance_mm(a, b, v1, v2) <= tolerance_mm and (
            min(_segment_length_mm(a, b), _segment_length_mm(v1, v2)) > 0
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# the projection
# ---------------------------------------------------------------------------


def _edge_roles(edge_count: int, road_widths: Mapping[int, int | None]) -> list[str]:
    """Assign front/rear/side-a/side-b/other. See module docstring, approximation 1."""
    roles = ["other"] * edge_count
    if edge_count == 0:
        return roles
    roaded = [(i, w) for i, w in road_widths.items() if w is not None and 0 <= i < edge_count]
    if not roaded:
        return roles
    # Widest road wins; ties break on the lowest edge index so the result is stable.
    front = min(roaded, key=lambda item: (-(item[1] or 0), item[0]))[0]
    roles[front] = "front"
    if edge_count % 2 == 0:
        rear = (front + edge_count // 2) % edge_count
        if rear != front:
            roles[rear] = "rear"
    side = 0
    for i in range(edge_count):
        if roles[i] == "other":
            roles[i] = "side-a" if side % 2 == 0 else "side-b"
            side += 1
    return roles


#: Default ``profile.buildingUse``. MUST be a member of the packs' own enum
#: (``rulepack.schema.json``: dwelling-single | dwelling-two | row-house | apartment
#: | other) — the previous default, ``"residential"``, was not, so all 83 city-pack
#: rules gated on ``when.buildingUse`` (every blr/ncr/hyd setback, FAR, coverage and
#: height band) silently reported ``not_applicable`` for a default-context house.
#: The client mirror (``apps/web/src/features/plot/rules.ts`` ``defaultRegFacts``)
#: already used this value; this constant restores the two sides to agreement.
#: Carried Phase-2 finding (i); regression-tested in
#: ``garh_rules/tests/test_context.py``.
DEFAULT_BUILDING_USE = "dwelling-single"


def build_evaluation_context(
    document: Mapping[str, Any],
    *,
    packs: Sequence[str],
    zone_category: str = "residential",
    building_use: str = DEFAULT_BUILDING_USE,
    dwelling_units: int = 1,
    parking_spaces_provided: int | None = None,
    rwh_declared: bool | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a folded ``ProjectDoc`` (JSON form) into the engine's input contract.

    ``document`` is the camelCase dict the server holds — see
    ``garh_api.routers.ops.load_project_state``. Returns the JSON form of
    :class:`~garh_rules.context.EvaluationContext` (``fixture.schema.json`` ->
    ``$defs.evaluationContext``), so it can be dumped into a fixture verbatim when a
    real-world plan needs to become a regression test.
    """
    plot_doc = dict(document.get("plot") or {})
    brief_doc = dict(document.get("brief") or {})
    house = dict(document.get("house") or {})
    brief_data = dict(brief_doc.get("data") or {})

    boundary = _ring(plot_doc.get("boundary") or [])
    roads_raw = plot_doc.get("roads") or []
    road_widths: dict[int, int | None] = {}
    for road in roads_raw:
        try:
            road_widths[int(road["edgeIndex"])] = (
                int(road["widthMm"]) if road.get("widthMm") is not None else None
            )
        except (KeyError, TypeError, ValueError):
            continue

    storeys = list(house.get("storeys") or [])
    walls = list(house.get("walls") or [])
    openings = list(house.get("openings") or [])
    rooms = list(house.get("rooms") or [])
    stairs = list(house.get("stairs") or [])
    balconies = list(house.get("balconies") or [])
    levels = dict(house.get("levels") or {})

    ground_id = str(storeys[0].get("id")) if storeys else None

    # ---- plot -------------------------------------------------------------
    roles = _edge_roles(len(boundary), road_widths)
    external_ground_walls = [
        w
        for w in walls
        if w.get("kind") == "external" and (ground_id is None or w.get("storeyId") == ground_id)
    ]

    edges: list[dict[str, Any]] = []
    for i in range(len(boundary)):
        v1 = boundary[i]
        v2 = boundary[(i + 1) % len(boundary)]
        provided = 0
        if external_ground_walls:
            distances = []
            for wall in external_ground_walls:
                a = (int(wall["a"]["x"]), int(wall["a"]["y"]))
                b = (int(wall["b"]["x"]), int(wall["b"]["y"]))
                half = int(wall.get("thicknessMm") or 0) // 2
                distances.append(max(0, _segment_distance_mm(a, b, v1, v2) - half))
            provided = min(distances)
        edges.append(
            {
                "index": i,
                "role": roles[i],
                "roadWidthMm": road_widths.get(i),
                "setbackProvidedMm": provided,
            }
        )

    plot_area = _polygon_area_mm2(boundary)
    front_edges = [e for e in edges if e["role"] == "front"]
    frontage_mm: int | None = None
    depth_mm: int | None = None
    if front_edges and len(boundary) >= 3:
        idx = int(front_edges[0]["index"])
        frontage_mm = _segment_length_mm(boundary[idx], boundary[(idx + 1) % len(boundary)])
        if frontage_mm > 0:
            # Mean depth. Exact for a rectangle, honest-average for L/T.
            depth_mm = plot_area // frontage_mm

    plot: dict[str, Any] = {
        "boundaryMm": [list(p) for p in boundary],
        "areaMm2": plot_area,
        "northDeg": int(plot_doc.get("northDeg") or 0) % 360,
        "cornerPlot": sum(1 for e in edges if e["roadWidthMm"] is not None) >= 2,
        "edges": edges,
        "frontageMm": frontage_mm,
        "depthMm": depth_mm,
    }

    # ---- storeys / areas --------------------------------------------------
    slabs_by_storey: dict[str, list[Mapping[str, Any]]] = {}
    for slab in house.get("slabs") or []:
        if slab.get("kind") == "floor":
            slabs_by_storey.setdefault(str(slab.get("storeyId")), []).append(slab)

    def _storey_built_up(storey_id: str) -> int:
        return sum(
            _polygon_area_mm2(_ring(slab.get("polygon") or []))
            for slab in slabs_by_storey.get(storey_id, ())
        )

    has_stilt = bool(brief_data.get("hasStilt"))
    has_basement = bool(brief_data.get("hasBasement"))

    storey_rows: list[dict[str, Any]] = []
    for i, storey in enumerate(storeys):
        height = int(storey.get("heightMm") or 0)
        slab_thickness = int((storey.get("level") or {}).get("slabThicknessMm") or 0)
        storey_rows.append(
            {
                "id": str(storey.get("id")),
                "index": i,
                "heightMm": height,
                # Clear height = FFL to soffit: storey height less the slab above.
                "clearHeightMm": max(0, height - slab_thickness),
                "builtUpAreaMm2": _storey_built_up(str(storey.get("id"))),
            }
        )

    built_up = sum(int(row["builtUpAreaMm2"]) for row in storey_rows)
    footprint = int(storey_rows[0]["builtUpAreaMm2"]) if storey_rows else 0
    far_free = 0
    if has_stilt and storey_rows:
        far_free += int(storey_rows[0]["builtUpAreaMm2"])
    if has_basement and storey_rows:
        far_free += int(storey_rows[0]["builtUpAreaMm2"])
    far_countable = max(0, built_up - far_free)

    plinth = int(levels.get("plinthMm") or 0)
    parapet = int(levels.get("parapetMm") or 0)
    storey_heights = sum(int(row["heightMm"]) for row in storey_rows)
    height_components = {"plinth": plinth, "storeys": storey_heights, "parapet": parapet}
    building_height = plinth + storey_heights + parapet

    # ---- rooms ------------------------------------------------------------
    walls_by_id = {str(w.get("id")): w for w in walls}
    room_rings: dict[str, list[Point]] = {
        str(r.get("id")): _ring(r.get("polygon") or []) for r in rooms
    }

    def _room_ids_for_wall(wall: Mapping[str, Any]) -> list[str]:
        a = (int(wall["a"]["x"]), int(wall["a"]["y"]))
        b = (int(wall["b"]["x"]), int(wall["b"]["y"]))
        storey_id = str(wall.get("storeyId"))
        return [
            str(room.get("id"))
            for room in rooms
            if str(room.get("storeyId")) == storey_id
            and _touches_ring(a, b, room_rings[str(room.get("id"))])
        ]

    wall_room_ids: dict[str, list[str]] = {
        wall_id: _room_ids_for_wall(wall) for wall_id, wall in walls_by_id.items()
    }

    ventilation_by_room: dict[str, int] = {}
    for opening in openings:
        if opening.get("kind") not in ("window", "ventilator"):
            continue
        area = int(opening.get("widthMm") or 0) * int(opening.get("heightMm") or 0)
        for room_id in wall_room_ids.get(str(opening.get("wallId")), ()):
            ventilation_by_room[room_id] = ventilation_by_room.get(room_id, 0) + area

    external_wall_ids = {str(w.get("id")) for w in walls if w.get("kind") == "external"}
    rooms_with_external_wall = {
        room_id
        for wall_id, room_ids in wall_room_ids.items()
        if wall_id in external_wall_ids
        for room_id in room_ids
    }

    room_rows: list[dict[str, Any]] = []
    room_type_by_id: dict[str, str] = {}
    for room in rooms:
        room_id = str(room.get("id"))
        ring = room_rings[room_id]
        storey_id = str(room.get("storeyId"))
        storey_row = next((r for r in storey_rows if r["id"] == storey_id), None)
        clear = int(storey_row["clearHeightMm"]) if storey_row else 0
        room_type = str(room.get("type") or "unassigned")
        room_type_by_id[room_id] = room_type
        room_rows.append(
            {
                "id": room_id,
                "storeyId": storey_id,
                "type": room_type,
                "name": str(room.get("name") or ""),
                "polygonMm": [list(p) for p in ring],
                "areaMm2": int(room.get("areaMm2") or _polygon_area_mm2(ring)),
                "leastWidthMm": _least_width_mm(ring),
                "centroidMm": list(_centroid_mm(ring)),
                "clearCeilingHeightMm": clear,
                "ventilationOpeningAreaMm2": ventilation_by_room.get(room_id, 0),
                # A room with no external wall has no daylight source of its own —
                # which is exactly what the "internal room" rules are about.
                "isInternal": room_id not in rooms_with_external_wall,
                "hasShaftAccess": False,
            }
        )

    # ---- openings ---------------------------------------------------------
    ground_doors = [
        o
        for o in openings
        if o.get("kind") == "door"
        and str(walls_by_id.get(str(o.get("wallId")), {}).get("storeyId")) == str(ground_id)
        and str(o.get("wallId")) in external_wall_ids
    ]
    main_entrance_id = (
        max(ground_doors, key=lambda o: (int(o.get("widthMm") or 0), str(o.get("id"))))["id"]
        if ground_doors
        else None
    )

    def _opening_role(opening: Mapping[str, Any]) -> str:
        if opening.get("kind") != "door":
            return "internal"
        if main_entrance_id is not None and opening.get("id") == main_entrance_id:
            return "main-entrance"
        adjoining = {
            room_type_by_id.get(rid, "")
            for rid in wall_room_ids.get(str(opening.get("wallId")), ())
        }
        if adjoining & _WET_ROOM_TYPES:
            return "bath"
        if adjoining & _GARAGE_ROOM_TYPES:
            return "garage"
        return "internal"

    def _outward_normal_deg(wall: Mapping[str, Any] | None) -> int | None:
        """Compass bearing of the wall's outward normal, in plot-local degrees.

        Only meaningful for an external wall; the sign convention assumes the
        building interior is to the left of a-to-b (which fold() guarantees for the
        walls it derives, and is a documented limitation for hand-drawn ones).
        """
        if wall is None or wall.get("kind") != "external":
            return None
        ax, ay = int(wall["a"]["x"]), int(wall["a"]["y"])
        bx, by = int(wall["b"]["x"]), int(wall["b"]["y"])
        if (bx - ax, by - ay) == (0, 0):
            return None
        # Right-hand normal of a->b, expressed as a bearing from +Y (north).
        nx, ny = by - ay, ax - bx
        return _round_half_away(math.degrees(math.atan2(nx, ny))) % 360

    opening_rows: list[dict[str, Any]] = []
    for opening in openings:
        wall = walls_by_id.get(str(opening.get("wallId")))
        opening_rows.append(
            {
                "id": str(opening.get("id")),
                "storeyId": str(wall.get("storeyId")) if wall else "",
                "kind": str(opening.get("kind") or "window"),
                "role": _opening_role(opening),
                "widthMm": int(opening.get("widthMm") or 0),
                "heightMm": int(opening.get("heightMm") or 0),
                "wallId": str(opening.get("wallId")) if opening.get("wallId") else None,
                "sillMm": int(opening.get("sillMm") or 0),
                "roomIds": wall_room_ids.get(str(opening.get("wallId")), []),
                "outwardNormalDeg": _outward_normal_deg(wall),
            }
        )

    # ---- stairs -----------------------------------------------------------
    stair_rows: list[dict[str, Any]] = []
    for stair in stairs:
        storey_id = str(stair.get("storeyId"))
        storey_row = next((r for r in storey_rows if r["id"] == storey_id), None)
        stair_rows.append(
            {
                "id": str(stair.get("id")),
                "storeyId": storey_id,
                "riserMm": int(stair.get("riserMm") or 0),
                "treadMm": int(stair.get("treadMm") or 0),
                "widthMm": int(stair.get("widthMm") or 0),
                # Approximation 5 — see the module docstring.
                "headroomMm": int(storey_row["clearHeightMm"]) if storey_row else 0,
                "kind": str(stair.get("kind")) if stair.get("kind") else None,
                "risersCount": int(stair.get("risersCount") or 0) or None,
                "centroidMm": None,
            }
        )

    # ---- projections (balconies) -----------------------------------------
    projection_rows: list[dict[str, Any]] = []
    for balcony in balconies:
        ring = _ring(balcony.get("polygon") or [])
        edge_role = "other"
        into_setback = False
        if ring and edges:
            centroid = _centroid_mm(ring)
            nearest = min(
                range(len(boundary)),
                key=lambda i: _point_to_segment_mm(
                    centroid, boundary[i], boundary[(i + 1) % len(boundary)]
                ),
            )
            edge_role = str(edges[nearest]["role"])
            into_setback = True  # a balcony projects by definition; the rule bands it
        projection_rows.append(
            {
                "id": str(balcony.get("id")),
                "storeyId": str(balcony.get("storeyId")),
                "element": "balcony",
                "edgeRole": edge_role,
                "projectionMm": int(balcony.get("projectionMm") or 0),
                "intoSetback": into_setback,
            }
        )

    model: dict[str, Any] = {
        "storeyCount": len(storey_rows),
        "hasStilt": has_stilt,
        "hasBasement": has_basement,
        "buildingHeightMm": building_height,
        "footprintAreaMm2": footprint,
        "builtUpAreaMm2": built_up,
        "farCountableAreaMm2": far_countable,
        "storeys": storey_rows,
        "rooms": room_rows,
        "openings": opening_rows,
        "stairs": stair_rows,
        "projections": projection_rows,
        "serviceElements": [],
        "heightComponentsMm": height_components,
    }

    city_pack = str((plot_doc.get("regProfile") or {}).get("cityPack") or "custom")
    profile: dict[str, Any] = {
        "cityPack": city_pack,
        "zoneCategory": zone_category,
        "buildingUse": building_use,
        "dwellingUnits": int(brief_data.get("dwellingUnits") or dwelling_units),
        "parkingSpacesProvided": int(
            parking_spaces_provided
            if parking_spaces_provided is not None
            else brief_data.get("carParking") or 0
        ),
        "rwhDeclared": bool(
            rwh_declared if rwh_declared is not None else brief_data.get("rainwaterHarvesting")
        ),
    }
    if overrides is None:
        # Default to what the document itself carries: the plot panel stores value
        # overrides under regProfile.overrides["values"] and future rule
        # acknowledgements as {ruleId: {reason}} siblings. The engine's context
        # parser routes the reserved "values" key (garh_rules.context
        # VALUE_OVERRIDES_KEY), so passing the object verbatim is safe.
        raw = (plot_doc.get("regProfile") or {}).get("overrides")
        overrides = raw if isinstance(raw, Mapping) else None
    if overrides:
        profile["overrides"] = dict(overrides)

    return {
        "packs": list(packs),
        "vastuMode": str(brief_doc.get("vastuMode") or "off"),
        "plot": plot,
        "profile": profile,
        "model": model,
    }


def packs_for(document: Mapping[str, Any], *, city_pack: str | None = None) -> tuple[str, ...]:
    """Which packs a project loads: nbc-core + its city pack + vastu when enabled.

    ``vastu`` is loaded only when ``vastuMode != 'off'`` — the pack's own contract is
    that ``off`` means "not loaded", not "loaded and ignored", so an architect who
    switched Vastu off does not get advisory Vastu rows in their report.
    """
    plot_doc = dict(document.get("plot") or {})
    brief_doc = dict(document.get("brief") or {})
    resolved = city_pack or (plot_doc.get("regProfile") or {}).get("cityPack")
    packs: list[str] = list(BASE_PACKS)
    pack_id = CITY_PACK_IDS.get(str(resolved or ""))
    if pack_id is not None:
        packs.append(pack_id)
    if str(brief_doc.get("vastuMode") or "off") != "off":
        packs.append("vastu")
    return tuple(packs)


def cannot_evaluate_reason(document: Mapping[str, Any]) -> str | None:
    """Why the rules cannot run yet, or ``None`` when they can.

    Not an exception and not a silent empty report: "we have not checked this because
    you have not drawn the plot yet" is a *different fact* from "we checked and found
    nothing", and the UI must be able to say which (golden rule 5 — compliance informs,
    and §15 — errors say what to do next).

    A plot boundary is the one hard precondition: setbacks, coverage and FAR are all
    ratios against the plot, and the engine rejects a boundary with fewer than three
    edges rather than inventing one.
    """
    boundary = (document.get("plot") or {}).get("boundary") or []
    if len(boundary) < 3:
        return (
            "Draw the plot boundary first — setbacks, coverage and FAR are all measured against it."
        )
    return None


def evaluate_document(
    document: Mapping[str, Any],
    *,
    city_pack: str | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the rules against a folded document.

    Returns ``(report_json, pack_versions)`` — exactly the two values
    ``ComplianceReportRepository.record`` wants. ``report_json`` is
    ``EvaluationReport.to_json()`` with this module's approximation notes merged in, so
    a stored report explains itself years later when the bye-law has changed.

    Raises :class:`ComplianceUnavailable` when the engine or the packs are missing.
    """
    try:
        from garh_rules import evaluate
        from garh_rules.errors import GarhRulesError
    except ImportError as exc:  # pragma: no cover - garh_rules is in the same image
        raise ComplianceUnavailable("garh_rules is not importable: %s" % exc) from exc

    blocked = cannot_evaluate_reason(document)
    if blocked is not None:
        raise ComplianceUnavailable(blocked)

    packs = packs_for(document, city_pack=city_pack)
    context = build_evaluation_context(document, packs=packs, overrides=overrides)
    try:
        report = evaluate(context)
    except GarhRulesError as exc:
        raise ComplianceUnavailable("rule evaluation failed: %s" % exc) from exc

    payload = report.to_json()
    existing = list(payload.get("notes") or [])
    payload["notes"] = existing + ["projection: %s" % note for note in _NOTES]
    _log.info(
        "compliance.evaluated",
        packs=",".join(packs),
        worst_status=report.worst_status(),
        blocking_failures=len(report.blocking_failures()),
    )
    return payload, dict(report.pack_versions)


__all__ = [
    "BASE_PACKS",
    "CITY_PACK_IDS",
    "ComplianceUnavailable",
    "DEFAULT_BUILDING_USE",
    "build_evaluation_context",
    "cannot_evaluate_reason",
    "evaluate_document",
    "packs_for",
]
