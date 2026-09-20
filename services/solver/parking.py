"""Car parking as geometry — bays the solver places, the rules engine measures.

An Indian residential plan on a 30 × 40 ft site is not sanctionable without its car
space, and until 2026-09 the solver never drew one: every city pack's parking rule
passed on ``brief.carParking``, an integer the architect typed, while the message
said "{actual} car space(s) are shown". The rules engine now counts only bays it can
measure (``garh_api.parking_geometry``), so a plan that draws none fails — and this
module is where the solver draws them.

**Where a bay goes.** In the front setback: the strip between the plot's front edge
(the ``front`` role, else the widest road) and the ground floor's nearest external
wall face. That strip is the one place a car can reach without crossing the house,
and it is where a Bengaluru or Hyderabad house actually parks. Bays run
*perpendicular* to the road when the strip is at least a bay long (5 m front setbacks
on large plots) and *parallel* to it otherwise (2.5 m of a 3 m setback on the common
30 × 40); 600 mm is kept from each side boundary for the compound wall, and the
main door's approach — the door width plus 900 mm each side — stays clear. Bays are
packed from the end farther from the door towards it.

**How many.** The pack's requirement (the ``limit`` of the applicable ``parking_min``
rows, read off a first rules pass over the refined house), or the brief's wish if
that is higher — the wish is honoured, the requirement is enforced. A strip that
cannot hold the requirement is a typed shortfall the pipeline turns into a discard
and, when nothing else clears, into the banner: "needs 2 car bays; 1 fits in the
3.0 m front setback". Stilt parking and a porch bay under the first floor are the
honest next answers and are not attempted here — a bay this module cannot place is
reported, never faked.

**What comes out.** ``furniture.set`` placements of the catalogue's ``parking-bay``
(2 500 × 5 000 mm) on the ground storey — the same rectangle the rule measures and
the site plan draws a car in. Integer millimetres throughout; the placement is a
pure function of the house, the plot and the requirement, so a candidate's bays are
as deterministic as its walls.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from services.common.logging import get_logger
from services.solver.geometry import Pt, ensure_ccw, point_in_polygon
from services.solver.types import SolveParams

log = get_logger("solver.parking")

__all__ = [
    "PARKING_BAY_CATALOG_ID",
    "SIDE_MARGIN_MM",
    "DOOR_APPROACH_MM",
    "BayPlacement",
    "ParkingPlan",
    "ParkingRequirement",
    "bay_size_mm",
    "plan_parking",
    "requirement_from_rows",
    "with_parking",
]

#: The catalogue id the rules engine measures and the site plan draws a car in.
PARKING_BAY_CATALOG_ID = "parking-bay"
#: Clear of each side boundary: the compound wall and a door's swing.
SIDE_MARGIN_MM = 600
#: Kept clear either side of the main door: a person and a gate leaf.
DOOR_APPROACH_MM = 900
#: The catalogue's bay when the file cannot be read (the worker image carries
#: ``fixtures/``; this is the last line, and the size every seeded pack names).
_FALLBACK_BAY_MM = (2500, 5000)


@dataclass(frozen=True)
class ParkingRequirement:
    """What the packs demand of this house, read off their own result rows."""

    #: Spaces required — the largest ``limit`` among applicable ``parking_min`` rows.
    count: int
    #: The rule ids that demanded them, for the discard reason.
    rule_ids: tuple[str, ...] = ()

    @property
    def applies(self) -> bool:
        return self.count > 0


@dataclass(frozen=True)
class BayPlacement:
    """One bay: centre, footprint and the rotation the furniture op carries."""

    centre: Pt
    #: 0 = the bay's 2 500 side runs along X (length along Y); 90 = the other way.
    rotation_deg: int
    x1: int
    y1: int
    x2: int
    y2: int

    def to_furniture(self, *, item_id: str, storey_id: str) -> dict[str, Any]:
        return {
            "id": item_id,
            "storeyId": storey_id,
            "catalogId": PARKING_BAY_CATALOG_ID,
            "pt": {"x": self.centre[0], "y": self.centre[1]},
            "rotationDeg": self.rotation_deg,
        }


@dataclass(frozen=True)
class ParkingPlan:
    """The outcome: what was placed, what was wanted, and why the difference."""

    bays: tuple[BayPlacement, ...]
    required: int
    wanted: int
    #: Where the bays went, for the rationale ("front").
    where: str
    #: Depth of the strip the bays were fitted into, mm.
    strip_depth_mm: int
    #: 'perpendicular' | 'parallel' | 'none'.
    orientation: str
    bay_mm: tuple[int, int]
    #: False when a bay had to sit across the main door's approach — the 30 x 40
    #: reality, where the car parks in front of the house and one walks past it.
    clear_of_door: bool = True

    @property
    def satisfied(self) -> bool:
        return len(self.bays) >= self.required

    def fact(self) -> str:
        return "parking:%d@%s:%dx%d" % (len(self.bays), self.where, self.bay_mm[0], self.bay_mm[1])

    def shortfall_message(self) -> str:
        """One sentence for the architect when the requirement was not met."""
        return (
            "The bye-law needs %d car space(s) of %.1f × %.1f m and only %d fit in the "
            "%.1f m front setback."
            % (
                self.required,
                self.bay_mm[0] / 1000.0,
                self.bay_mm[1] / 1000.0,
                len(self.bays),
                self.strip_depth_mm / 1000.0,
            )
        )

    @staticmethod
    def shortfall_action() -> str:
        return (
            "A deeper front setback, a wider frontage, or stilt parking under the house "
            "would carry them; the solver does not draw a stilt floor yet."
        )


# ---------------------------------------------------------------------------
# the requirement, from the rules pass
# ---------------------------------------------------------------------------


def requirement_from_rows(rows: Sequence[Mapping[str, Any]]) -> ParkingRequirement:
    """The parking the packs demand, read off ``parking_min`` result rows.

    Applicable rows only (``pass``/``warn``/``fail``); a ``not_applicable`` row is a
    band the plot is not in. The largest limit governs when two rules stack.
    """
    count = 0
    rule_ids: list[str] = []
    for row in rows:
        if str(row.get("checkType")) != "parking_min":
            continue
        if str(row.get("status")) not in ("pass", "warn", "fail"):
            continue
        limit = row.get("limit")
        if isinstance(limit, bool) or not isinstance(limit, int):
            continue
        rule_ids.append(str(row.get("ruleId") or "?"))
        count = max(count, limit)
    return ParkingRequirement(count=count, rule_ids=tuple(rule_ids))


def bay_size_mm(catalog_path: str | None = None) -> tuple[int, int]:
    """``(widthMm, depthMm)`` of the catalogue's ``parking-bay``.

    Read from ``fixtures/catalog/furniture.json`` — the file the API serves and
    the furniture-fit gate opens — so the bay the solver places is the bay the rule
    measures. Falls back to the seeded 2 500 × 5 000 only when the file is unreadable,
    and says so in the log.
    """
    path = catalog_path or _default_catalog_path()
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        log.warning("solver.parking.catalogue_unreadable", path=path)
        return _FALLBACK_BAY_MM
    entries = raw["items"] if isinstance(raw, dict) else raw
    for entry in entries:
        if isinstance(entry, Mapping) and str(entry.get("id")) == PARKING_BAY_CATALOG_ID:
            return (int(entry["widthMm"]), int(entry["depthMm"]))
    log.warning("solver.parking.catalogue_has_no_bay", path=path)
    return _FALLBACK_BAY_MM


#: Where the catalogue lives, in the same precedence the API uses
#: (garh_api.catalog_data): GARH_CATALOG_DIR first, then <root>/catalog, then
#: <root>/fixtures/catalog. Resolved here with the stdlib rather than imported,
#: because the solver worker's image carries fixtures but not the API package.
_CATALOG_DIR_CANDIDATES: tuple[str, ...] = ("catalog", os.path.join("fixtures", "catalog"))


def _default_catalog_path() -> str:
    """The furniture catalogue the API serves — NOT a hardcoded repo path.

    docker-compose sets GARH_CATALOG_DIR. Reading past it would let the solver place
    a bay from one catalogue while the rule measured the bay of another, and the two
    disagreeing by a hundred millimetres is a plan that draws a car the compliance tab
    then refuses. ``test_parking_catalogue_is_one_source.py`` holds the three readers
    to the same answer under an override.
    """
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    override = os.environ.get("GARH_CATALOG_DIR")
    if override:
        directory = override if os.path.isabs(override) else os.path.join(root, override)
        return os.path.join(directory, "furniture.json")
    for candidate in _CATALOG_DIR_CANDIDATES:
        path = os.path.join(root, candidate, "furniture.json")
        if os.path.isfile(path):
            return path
    return os.path.join(root, _CATALOG_DIR_CANDIDATES[1], "furniture.json")


# ---------------------------------------------------------------------------
# the front strip
# ---------------------------------------------------------------------------


def _front_edge_index(params: SolveParams) -> int | None:
    fronts = [edge for edge in params.edges if edge.role == "front"]
    if fronts:
        return fronts[0].index
    roads = sorted(
        (edge for edge in params.edges if edge.road_width_mm > 0),
        key=lambda e: (-e.road_width_mm, e.index),
    )
    return roads[0].index if roads else None


def _ground_external_walls(house: Mapping[str, Any]) -> list[tuple[Pt, Pt, int]]:
    storeys = list(house.get("storeys") or [])
    if not storeys:
        return []
    ground = str(storeys[0].get("id"))
    out: list[tuple[Pt, Pt, int]] = []
    for wall in house.get("walls") or []:
        if str(wall.get("storeyId")) != ground or wall.get("kind") != "external":
            continue
        a = (int(wall["a"]["x"]), int(wall["a"]["y"]))
        b = (int(wall["b"]["x"]), int(wall["b"]["y"]))
        out.append((a, b, int(wall.get("thicknessMm") or 0)))
    return out


def _main_door_centre(house: Mapping[str, Any]) -> Pt | None:
    """Centre of the main entrance on the wall it sits in, from stage B's own note."""
    meta = house.get("solverMeta") or {}
    door_id = meta.get("mainDoorId")
    if not door_id:
        return None
    opening = next((o for o in house.get("openings") or [] if o.get("id") == door_id), None)
    if opening is None:
        return None
    wall = next((w for w in house.get("walls") or [] if w.get("id") == opening.get("wallId")), None)
    if wall is None:
        return None
    ax, ay = int(wall["a"]["x"]), int(wall["a"]["y"])
    bx, by = int(wall["b"]["x"]), int(wall["b"]["y"])
    length = abs(bx - ax) + abs(by - ay)
    if length == 0:
        return None
    offset = int(opening.get("offsetMm") or 0)
    # Axis-aligned walls only (stage B emits nothing else): step along the axis.
    if ax == bx:
        return (ax, ay + (offset if by > ay else -offset))
    return (ax + (offset if bx > ax else -offset), ay)


@dataclass(frozen=True)
class _Strip:
    """The front strip in a local frame: ``u`` runs along the road edge, ``v`` from
    the edge line inward to the building face."""

    #: Which plot-local axis is ``u``: 'x' (edge horizontal) or 'y' (edge vertical).
    along: str
    #: The edge line's coordinate on the other axis, and the sign towards the house.
    line: int
    inward: int
    #: Usable ``u`` range after the side margins.
    u_lo: int
    u_hi: int
    #: Depth from the edge line to the nearest external wall face, mm.
    depth: int

    def to_plot(self, u: int, v: int) -> Pt:
        w = self.line + self.inward * v
        return (u, w) if self.along == "x" else (w, u)


def _front_strip(params: SolveParams, house: Mapping[str, Any]) -> _Strip | None:
    polygon = ensure_ccw(params.plot_polygon)
    index = _front_edge_index(params)
    if index is None:
        return None
    count = len(polygon)
    a = polygon[index % count]
    b = polygon[(index + 1) % count]
    if a[0] != b[0] and a[1] != b[1]:
        return None  # a skewed frontage: no strip this module can lay bays in
    walls = _ground_external_walls(house)
    if not walls:
        return None
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    centre = ((min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2)
    if a[1] == b[1]:
        along, line = "x", a[1]
        inward = 1 if centre[1] > line else -1
        # Nearest external wall FACE to the edge line, measured inward.
        #
        # `nearest` is min or max by the sign of `inward`, and it has to be BOTH the
        # per-wall pick and the across-walls pick. It used to be `min` across walls
        # whatever the sign, which is right only when the road is on the low side of
        # the plot. With the road north or east (inward -1) it returned the FAR wall,
        # so the strip measured the whole house: a 3 m front setback read as 7.4 m,
        # the orientation flipped to perpendicular, and the bay was drawn two metres
        # inside the living room — with `satisfied` true, so nothing upstream
        # objected. Every test built its plot with the front edge at y = 0, so the
        # branch had never run.
        nearest = min if inward > 0 else max
        face = (
            nearest(
                nearest(wa[1], wb[1]) - inward * (t // 2) for wa, wb, t in walls if wa[1] == wb[1]
            )
            if any(wa[1] == wb[1] for wa, wb, _t in walls)
            else None
        )
        u_lo, u_hi = min(a[0], b[0]), max(a[0], b[0])
    else:
        along, line = "y", a[0]
        inward = 1 if centre[0] > line else -1
        nearest = min if inward > 0 else max  # see the note on the other axis
        face = (
            nearest(
                nearest(wa[0], wb[0]) - inward * (t // 2) for wa, wb, t in walls if wa[0] == wb[0]
            )
            if any(wa[0] == wb[0] for wa, wb, _t in walls)
            else None
        )
        u_lo, u_hi = min(a[1], b[1]), max(a[1], b[1])
    if face is None:
        return None
    depth = (face - line) * inward
    if depth <= 0:
        return None
    return _Strip(
        along=along,
        line=line,
        inward=inward,
        u_lo=u_lo + SIDE_MARGIN_MM,
        u_hi=u_hi - SIDE_MARGIN_MM,
        depth=depth,
    )


# ---------------------------------------------------------------------------
# the placement
# ---------------------------------------------------------------------------


def plan_parking(
    house: Mapping[str, Any],
    params: SolveParams,
    *,
    required: int,
    wanted: int | None = None,
    bay_mm: tuple[int, int] | None = None,
    door_width_mm: int = 1000,
) -> ParkingPlan:
    """Lay ``max(required, wanted)`` bays in the front strip; place what fits.

    Pure: the same house and params give the same bays. Orientation is chosen once
    for the strip (perpendicular when it is a bay long, else parallel). Bays are
    packed from the end farther from the main door towards it, first keeping the
    door's approach clear; when that leaves the requirement short, a second pass
    packs the whole frontage — a car in front of the entrance that one walks past
    is how the common 30 × 40 actually parks, and the plan says so
    (``clear_of_door``). Every bay is checked against the plot polygon, so a notch
    in an L-shaped plot cannot receive one.
    """
    size = bay_mm or bay_size_mm()
    short, long = min(size), max(size)
    target = max(required, wanted or 0)
    strip = _front_strip(params, house)
    if strip is None or target <= 0:
        return ParkingPlan((), required, target, "front", 0, "none", size)

    if strip.depth >= long:
        orientation, along_u, into_v = "perpendicular", short, long
    elif strip.depth >= short:
        orientation, along_u, into_v = "parallel", long, short
    else:
        return ParkingPlan((), required, target, "front", strip.depth, "none", size)

    # The door's approach, in strip coordinates, as a forbidden u-interval.
    door = _main_door_centre(house)
    forbidden: tuple[int, int] | None = None
    door_u: int | None = None
    if door is not None:
        door_u = door[0] if strip.along == "x" else door[1]
        half = door_width_mm // 2 + DOOR_APPROACH_MM
        forbidden = (door_u - half, door_u + half)

    polygon = ensure_ccw(params.plot_polygon)
    from_low = door_u is None or (door_u - strip.u_lo) > (strip.u_hi - door_u)
    bays = _pack(
        strip, polygon, target, along_u, into_v, short, forbidden=forbidden, from_low=from_low
    )
    clear = True
    if len(bays) < required and forbidden is not None:
        across = _pack(
            strip, polygon, target, along_u, into_v, short, forbidden=None, from_low=from_low
        )
        if len(across) > len(bays):
            bays, clear = across, False
    return ParkingPlan(
        tuple(bays), required, target, "front", strip.depth, orientation, size, clear_of_door=clear
    )


def _pack(
    strip: _Strip,
    polygon: Sequence[Pt],
    target: int,
    along_u: int,
    into_v: int,
    short: int,
    *,
    forbidden: tuple[int, int] | None,
    from_low: bool,
) -> list[BayPlacement]:
    """Bays along the strip from one end, skipping the forbidden interval."""
    bays: list[BayPlacement] = []
    cursor = strip.u_lo if from_low else strip.u_hi
    step = along_u if from_low else -along_u
    while len(bays) < target:
        u_a = cursor if from_low else cursor - along_u
        u_b = u_a + along_u
        if u_a < strip.u_lo or u_b > strip.u_hi:
            break
        if forbidden is not None and u_a < forbidden[1] and u_b > forbidden[0]:
            # Skip past the door approach and keep packing on its other side.
            cursor = forbidden[1] if from_low else forbidden[0]
            continue
        # The bay sits against the edge line (v = 0 .. into_v).
        p1 = strip.to_plot(u_a, 0)
        p2 = strip.to_plot(u_b, into_v)
        x1, x2 = sorted((p1[0], p2[0]))
        y1, y2 = sorted((p1[1], p2[1]))
        corners = ((x1, y1), (x2, y1), (x2, y2), (x1, y2))
        if not all(point_in_polygon(c, polygon) for c in corners):
            cursor += step
            continue
        rotation = 0 if (x2 - x1) == short else 90
        bays.append(
            BayPlacement(
                centre=((x1 + x2) // 2, (y1 + y2) // 2),
                rotation_deg=rotation,
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
            )
        )
        cursor += step
    return bays


def with_parking(
    house: Mapping[str, Any],
    params: SolveParams,
    *,
    required: int,
    wanted: int | None = None,
    bay_mm: tuple[int, int] | None = None,
) -> tuple[dict[str, Any], ParkingPlan]:
    """The house with its bays as furniture on the ground storey, plus the plan.

    Ids are minted through ``garh_model.ids.new_id`` like every other stage-B
    element, so a seeded id factory makes the output byte-stable in tests.
    """
    from services.solver.repair import ensure_model_importable

    ensure_model_importable()
    from garh_model.ids import new_id

    plan = plan_parking(house, params, required=required, wanted=wanted, bay_mm=bay_mm)
    storeys = list(house.get("storeys") or [])
    if not plan.bays or not storeys:
        return dict(house), plan
    ground = str(storeys[0]["id"])
    furniture = list(house.get("furniture") or [])
    furniture.extend(
        bay.to_furniture(item_id=new_id("furniture"), storey_id=ground) for bay in plan.bays
    )
    out = dict(house)
    out["furniture"] = furniture
    meta = dict(out.get("solverMeta") or {})
    meta["facts"] = sorted([*(meta.get("facts") or []), plan.fact()])
    meta["parking"] = {
        "required": plan.required,
        "wanted": plan.wanted,
        "placed": len(plan.bays),
        "orientation": plan.orientation,
        "stripDepthMm": plan.strip_depth_mm,
        "clearOfDoor": plan.clear_of_door,
    }
    out["solverMeta"] = meta
    return out, plan


def is_parking_bay(item: Mapping[str, Any]) -> bool:
    return str(item.get("catalogId")) == PARKING_BAY_CATALOG_ID
