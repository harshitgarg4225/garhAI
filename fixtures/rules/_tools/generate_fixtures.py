from __future__ import annotations

"""Generate the pass/fail fixture corpus for every rule in rulepacks/.

Playbook §16 requires at least one passing and one violating fixture per rule.
There are 117 rules, so the corpus is generated rather than typed -- but it is
generated to be *tight*: a passing fixture sits EXACTLY on the limit and a
failing fixture misses it by one unit, for the one input the rule measures,
inside a context whose other values are deliberately compliant. That is what
makes a red fixture diagnostic: it can only have gone red for the reason the
filename says.

This script is a SECOND, independent statement of the check semantics (the
first is rulepacks/README.md, the third will be the engine). When a fixture and
the engine disagree, README.md §"Check semantics" is the tiebreaker -- one of
the other two has a bug.

Run:  python3 fixtures/rules/_tools/generate_fixtures.py
It rewrites fixtures/rules/<packId>/*.json and fixtures/rules/index.json.
Committed output is the artefact; review the diff, do not trust the script.
"""

import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_ROOT = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(FIXTURE_ROOT))
PACK_DIR = os.path.join(REPO, "rulepacks")

M2 = 1_000_000

# --- baseline building constants (integer mm) -------------------------------
FLOOR_HEIGHT = 3000
SLAB = 150
PLINTH = 600
MUMTY_H = 2400
OHT_H = 1200
ABOVE_TERRACE = MUMTY_H + OHT_H   # what buildingHeightMm carries above the terrace slab
PARAPET = 1000                    # lower than the mumty, so it never sets buildingHeightMm

# Area minima per room type, used to keep a width fixture from tripping an area rule.
AREA_MIN = {
    "living": 9_500_000, "dining": 9_500_000, "living_dining": 9_500_000,
    "bedroom": 9_500_000, "master_bedroom": 9_500_000, "guest_bedroom": 9_500_000,
    "servant_room": 9_500_000, "study": 9_500_000,
    "kitchen": 5_000_000, "kitchen_dining": 7_500_000,
    "bath": 1_800_000, "wc": 1_100_000, "bath_wc": 2_800_000,
}
WIDTH_MIN = {
    "living": 2400, "dining": 2400, "living_dining": 2400, "bedroom": 2400,
    "master_bedroom": 2400, "guest_bedroom": 2400, "servant_room": 2400, "study": 2400,
    "kitchen": 1800, "kitchen_dining": 1800,
    "bath": 1200, "wc": 900, "bath_wc": 1200,
}


# ===========================================================================
# formatting helpers
# ===========================================================================

def gmm(n):
    """1234567 -> '1 234 567' (space-grouped, ASCII only)."""
    s = str(abs(int(n)))
    out = []
    while len(s) > 3:
        out.insert(0, s[-3:])
        s = s[:-3]
    out.insert(0, s)
    return ("-" if n < 0 else "") + " ".join(out)


def as_m2(mm2):
    # 6 dp so a 1 mm2 delta is visible in the prose, not rounded away
    return ("%.6f" % (mm2 / 1_000_000.0)).rstrip("0").rstrip(".")


def as_m(mm):
    return ("%.3f" % (mm / 1000.0)).rstrip("0").rstrip(".")


def mm2_txt(v):
    return "%s mm2 (%s m2)" % (gmm(v), as_m2(v))


def mm_txt(v):
    return "%s mm" % gmm(v)


# ===========================================================================
# predicate evaluation (mirrors the engine's closed field set)
# ===========================================================================

# Fields whose pack-side threshold is in a different unit from the context value.
FIELD_SCALE = {"plotAreaSqm": M2}


def field_value(field, ctx):
    """ctx is a flat dict of the closed context fields."""
    if field == "plotAreaSqm":
        return ctx.get("plotAreaMm2")
    return ctx.get(field)


def pred_ok(field, pred, ctx):
    val = field_value(field, ctx)
    if val is None:
        return False
    scale = FIELD_SCALE.get(field, 1)
    for op, arg in pred.items():
        if op == "eq":
            if val != (arg * scale if isinstance(arg, int) and scale != 1 else arg):
                return False
        elif op == "in":
            opts = [a * scale for a in arg] if scale != 1 else arg
            if val not in opts:
                return False
        elif op == "lt":
            if not val < arg * scale:
                return False
        elif op == "lte":
            if not val <= arg * scale:
                return False
        elif op == "gt":
            if not val > arg * scale:
                return False
        elif op == "gte":
            if not val >= arg * scale:
                return False
        else:
            raise ValueError("unknown operator %r" % op)
    return True


def when_ok(rule, ctx):
    for field, pred in rule.get("when", {}).items():
        if not pred_ok(field, pred, ctx):
            return False
    return True


def pick_int(pred, kind):
    """Choose a representative, human-round value satisfying `pred`."""
    if "eq" in pred:
        return pred["eq"]
    if "in" in pred:
        return sorted(pred["in"])[0]
    lo = pred.get("gte")
    if "gt" in pred:
        lo = pred["gt"] + 1 if lo is None else max(lo, pred["gt"] + 1)
    hi = pred.get("lte")
    if "lt" in pred:
        hi = pred["lt"] - 1 if hi is None else min(hi, pred["lt"] - 1)
    grain = {"sqm": 10, "mm": 500, "mm2": 1_000_000}.get(kind, 1)
    if hi is not None and hi % grain == 0 and (lo is None or hi >= lo):
        return hi
    if lo is not None:
        up = ((lo + grain - 1) // grain) * grain
        if hi is None or up <= hi:
            return up
        return lo
    if hi is not None:
        down = (hi // grain) * grain
        return down if down > 0 else hi
    return None


# ===========================================================================
# pack loading
# ===========================================================================

def load_packs():
    packs = {}
    for name in ("nbc-core", "blr", "ncr", "hyd", "vastu"):
        with open(os.path.join(PACK_DIR, name + ".json")) as fh:
            packs[name] = json.load(fh)
    return packs


def chain_of(packs, pack_id):
    out = []
    cur = pack_id
    seen = set()
    while cur is not None:
        if cur in seen:
            raise ValueError("extends cycle at %s" % cur)
        seen.add(cur)
        out.insert(0, cur)
        cur = packs[cur].get("extends")
    return out


def chain_rules(packs, pack_id):
    out = []
    for p in chain_of(packs, pack_id):
        out.extend(packs[p]["rules"])
    return out


# ===========================================================================
# geometry helpers
# ===========================================================================

def rect(x0, y0, w, d):
    return [[x0, y0], [x0 + w, y0], [x0 + w, y0 + d], [x0, y0 + d]]


def rect_props(x0, y0, w, d):
    # centroid rounded HALF-UP to whole mm, matching the engine contract
    return {
        "polygonMm": rect(x0, y0, w, d),
        "areaMm2": w * d,
        "leastWidthMm": min(w, d),
        "centroidMm": [x0 + (w + 1) // 2, y0 + (d + 1) // 2],
    }


def plot_rect(area_mm2):
    """Pick a plausible rectangle of exactly `area_mm2`: depth closest to sqrt."""
    best = None
    target = int(math.sqrt(area_mm2))
    for depth in range(6000, 60001, 500):
        if area_mm2 % depth == 0:
            width = area_mm2 // depth
            if 6000 <= width <= 90000:
                score = abs(depth - target)
                if best is None or score < best[0]:
                    best = (score, width, depth)
    if best is None:
        depth = 10000
        return area_mm2 // depth, depth
    return best[1], best[2]


def factor_rect(area, min_width):
    """Split `area` exactly into (w, d) with min_width <= w <= d."""
    start = int(math.sqrt(area))
    for w in range(start, min_width - 1, -1):
        if area % w == 0:
            return w, area // w
    d = area // min_width
    return min_width, d


# ===========================================================================
# safe-value resolution: build a context that satisfies every OTHER rule
# ===========================================================================

EDGE_ROLES = ("front", "rear", "side-a", "side-b")


def edges_covered(selector):
    if selector == "sides":
        return ("side-a", "side-b")
    if selector == "all":
        return EDGE_ROLES
    return (selector,)


def required_setbacks(rules, ctx):
    req = {}
    for r in rules:
        if r["check"]["type"] != "setback_min" or not when_ok(r, ctx):
            continue
        for role in edges_covered(r["check"]["edge"]):
            req[role] = max(req.get(role, 0), r["check"]["valueMm"])
    return req


def ratio_limit(ratio, base):
    return (ratio["num"] * base) // ratio["den"]


def min_ratio_limit(rules, ctx, check_type, base):
    lim = None
    for r in rules:
        if r["check"]["type"] != check_type or not when_ok(r, ctx):
            continue
        v = ratio_limit(r["check"]["ratio"], base)
        lim = v if lim is None else min(lim, v)
    return lim


def min_scalar_limit(rules, ctx, check_type, key):
    lim = None
    for r in rules:
        if r["check"]["type"] != check_type or not when_ok(r, ctx):
            continue
        v = r["check"][key]
        lim = v if lim is None else min(lim, v)
    return lim


def parking_required(rules, ctx, built_up_mm2):
    need = 0
    for r in rules:
        c = r["check"]
        if c["type"] != "parking_min" or not when_ok(r, ctx):
            continue
        base = ctx["dwellingUnits"] if c["basis"] == "dwelling" else built_up_mm2
        n = -((-c["rate"]["num"] * base) // c["rate"]["den"])  # ceil
        need = max(need, n, c.get("minSpaces", 0))
    return need


def projection_limits(rules, ctx):
    lims = {}
    for r in rules:
        c = r["check"]
        if c["type"] != "projection_max" or not when_ok(r, ctx):
            continue
        el = c["element"]
        lims[el] = min(lims.get(el, c["valueMm"]), c["valueMm"])
    return lims


# ===========================================================================
# baseline context
# ===========================================================================

def base_rooms(storey_id, full):
    """full=True gives the whole room programme (for room-scope fixtures)."""
    spec = [
        ("room_livdin", "living_dining", "Living / Dining", 4000, 5000, 2900, 2_400_000),
        ("room_bed2", "bedroom", "Bedroom 2", 3000, 4000, 2900, 1_400_000),
        ("room_kitchen", "kitchen", "Kitchen", 2400, 3000, 2900, 800_000),
        ("room_bath1", "bath", "Bathroom 1", 1500, 2100, 2500, 400_000),
    ]
    if full:
        spec += [
            ("room_master", "master_bedroom", "Master Bedroom", 3600, 4200, 2900, 1_700_000),
            ("room_wc1", "wc", "WC", 1100, 1500, 2500, 350_000),
            ("room_bathwc1", "bath_wc", "Bath + WC", 1500, 2400, 2500, 400_000),
            ("room_pooja", "pooja", "Pooja", 1200, 1500, 2900, 200_000),
            ("room_kitdin", "kitchen_dining", "Kitchen-Dining", 2500, 3200, 2900, 900_000),
            ("room_stair", "staircase", "Staircase", 1200, 4000, 2900, 300_000),
            ("room_corr", "corridor", "Corridor", 1100, 4000, 2900, 0),
        ]
    rooms = []
    x = 300
    for rid, rtype, name, w, d, ceil_h, vent in spec:
        r = {"id": rid, "storeyId": storey_id, "type": rtype, "name": name}
        r.update(rect_props(x, 300, w, d))
        r["clearCeilingHeightMm"] = ceil_h
        r["ventilationOpeningAreaMm2"] = vent
        r["isInternal"] = False
        rooms.append(r)
        x += w + 200
    return rooms


def base_openings(storey_id, full, front_normal_deg):
    ops = [
        {"id": "open_main", "storeyId": storey_id, "wallId": "wall_ext_s", "kind": "door",
         "role": "main-entrance", "widthMm": 1000, "heightMm": 2100, "sillMm": 0,
         "roomIds": ["room_livdin"], "outwardNormalDeg": front_normal_deg},
        {"id": "open_int_bed2", "storeyId": storey_id, "wallId": "wall_int_1", "kind": "door",
         "role": "internal", "widthMm": 900, "heightMm": 2100, "sillMm": 0,
         "roomIds": ["room_livdin", "room_bed2"], "outwardNormalDeg": None},
        {"id": "open_bath1", "storeyId": storey_id, "wallId": "wall_int_2", "kind": "door",
         "role": "bath", "widthMm": 800, "heightMm": 2100, "sillMm": 0,
         "roomIds": ["room_bath1"], "outwardNormalDeg": None},
        {"id": "open_win_livdin", "storeyId": storey_id, "wallId": "wall_ext_s", "kind": "window",
         "role": "internal", "widthMm": 1800, "heightMm": 1350, "sillMm": 900,
         "roomIds": ["room_livdin"], "outwardNormalDeg": front_normal_deg},
    ]
    if full:
        ops += [
            {"id": "open_int_kitchen", "storeyId": storey_id, "wallId": "wall_int_3", "kind": "door",
             "role": "internal", "widthMm": 900, "heightMm": 2100, "sillMm": 0,
             "roomIds": ["room_livdin", "room_kitchen"], "outwardNormalDeg": None},
            {"id": "open_bathwc1", "storeyId": storey_id, "wallId": "wall_int_4", "kind": "door",
             "role": "bath", "widthMm": 800, "heightMm": 2100, "sillMm": 0,
             "roomIds": ["room_bathwc1"], "outwardNormalDeg": None},
            {"id": "open_vent_bath1", "storeyId": storey_id, "wallId": "wall_ext_e", "kind": "ventilator",
             "role": "service", "widthMm": 600, "heightMm": 600, "sillMm": 1800,
             "roomIds": ["room_bath1"], "outwardNormalDeg": 90},
        ]
    return ops


def base_stairs(storey_id):
    return [{"id": "stair_main", "storeyId": storey_id, "kind": "dogleg", "riserMm": 165,
             "treadMm": 280, "widthMm": 1050, "headroomMm": 2200, "risersCount": 18,
             "centroidMm": [2000, 2000]}]


def build_context(packs, pack_id, area_mm2, road_mm, *, full_rooms, storeys_hint=2,
                  vastu_mode="off"):
    rules = chain_rules(packs, pack_id)
    city = pack_id if pack_id in ("blr", "ncr", "hyd") else (
        "nbc-core" if pack_id == "nbc-core" else "custom")

    width, depth = plot_rect(area_mm2)
    ctx_fields = {
        "cityPack": city,
        "zoneCategory": "residential",
        "buildingUse": "dwelling-single",
        "plotAreaMm2": area_mm2,
        "plotFrontageMm": width,
        "plotDepthMm": depth,
        "roadWidthMm": road_mm,
        "edgeRoadWidthMm": road_mm,
        "cornerPlot": False,
        "abuttingRoadCount": 1,
        "dwellingUnits": 1,
        "hasStilt": False,
        "hasBasement": False,
        "vastuMode": vastu_mode,
    }

    # setbacks: leave 100mm more than the strictest requirement on every edge
    req_sb = required_setbacks(rules, ctx_fields)
    provided = {}
    for role in EDGE_ROLES:
        provided[role] = req_sb.get(role, 1500) + 100

    # floors, then coverage, then FAR -- each clamped by the previous
    floors_allowed = min_scalar_limit(rules, ctx_fields, "floors_max", "value")
    storeys = storeys_hint if floors_allowed is None else min(storeys_hint, floors_allowed)
    storeys = max(1, storeys)
    ctx_fields["storeys"] = storeys

    cov_allowed = min_ratio_limit(rules, ctx_fields, "coverage_max", area_mm2)
    if cov_allowed is None:
        cov_allowed = (area_mm2 * 60) // 100
    envelope = max(0, width - provided["side-a"] - provided["side-b"]) * \
        max(0, depth - provided["front"] - provided["rear"])

    far_allowed = min_ratio_limit(rules, ctx_fields, "far_max", area_mm2)
    if far_allowed is None:
        far_allowed = area_mm2 * 2

    footprint = min((cov_allowed * 80) // 100, envelope, (far_allowed * 80) // 100 // storeys)
    footprint = max(footprint, 15_000_000)
    built_up = footprint * storeys

    height_allowed = min_scalar_limit(rules, ctx_fields, "height_max", "valueMm")
    building_height = PLINTH + storeys * FLOOR_HEIGHT + ABOVE_TERRACE
    if height_allowed is not None:
        # actual = buildingHeightMm - excluded(mumty, oht) = PLINTH + storeys*FLOOR_HEIGHT
        while PLINTH + storeys * FLOOR_HEIGHT > height_allowed and storeys > 1:
            storeys -= 1
            ctx_fields["storeys"] = storeys
            built_up = footprint * storeys
        building_height = PLINTH + storeys * FLOOR_HEIGHT + ABOVE_TERRACE

    ctx_fields["buildingHeightMm"] = building_height
    ctx_fields["builtUpAreaMm2"] = built_up
    ctx_fields["farCountableAreaMm2"] = built_up

    # at least one space even where no rule demands it -- a house with zero parking
    # is not a plausible baseline, and parking fixtures override this anyway
    parking = max(1, parking_required(rules, ctx_fields, built_up))
    proj_lims = projection_limits(rules, ctx_fields)

    storey_id = "storey_g"
    storey_list = []
    for i in range(storeys):
        storey_list.append({
            "id": "storey_g" if i == 0 else "storey_%d" % i,
            "index": i,
            "heightMm": FLOOR_HEIGHT,
            "clearHeightMm": FLOOR_HEIGHT - SLAB,
            "builtUpAreaMm2": footprint,
        })

    projections = []
    for el, default in (("balcony", 900), ("chajja", 600)):
        lim = proj_lims.get(el)
        val = default if lim is None else max(0, lim - 100)
        projections.append({
            "id": "proj_%s_1" % el, "storeyId": storey_id,
            "element": el, "edgeRole": "front", "projectionMm": val, "intoSetback": True,
        })

    # front edge carries the road; boundary is CCW from the SW corner, so
    # edge 0 = south (front), 1 = east (side-a), 2 = north (rear), 3 = west (side-b)
    ctx = {
        "packs": chain_of(packs, pack_id),
        "vastuMode": vastu_mode,
        "plot": {
            "boundaryMm": rect(0, 0, width, depth),
            "areaMm2": area_mm2,
            "northDeg": 0,
            "frontageMm": width,
            "depthMm": depth,
            "cornerPlot": False,
            "edges": [
                {"index": 0, "role": "front", "roadWidthMm": road_mm, "setbackProvidedMm": provided["front"]},
                {"index": 1, "role": "side-a", "roadWidthMm": None, "setbackProvidedMm": provided["side-a"]},
                {"index": 2, "role": "rear", "roadWidthMm": None, "setbackProvidedMm": provided["rear"]},
                {"index": 3, "role": "side-b", "roadWidthMm": None, "setbackProvidedMm": provided["side-b"]},
            ],
        },
        "profile": {
            "cityPack": city,
            "zoneCategory": "residential",
            "buildingUse": "dwelling-single",
            "dwellingUnits": 1,
            "parkingSpacesProvided": parking,
            "rwhDeclared": True,
        },
        "model": {
            "storeyCount": storeys,
            "hasStilt": False,
            "hasBasement": False,
            "buildingHeightMm": building_height,
            "heightComponentsMm": {"mumty": MUMTY_H, "oht": OHT_H},
            "footprintAreaMm2": footprint,
            "builtUpAreaMm2": built_up,
            "farCountableAreaMm2": built_up,
            "storeys": storey_list,
            "rooms": base_rooms(storey_id, full_rooms),
            "openings": base_openings(storey_id, full_rooms, 180),
            "stairs": base_stairs(storey_id),
            "projections": projections,
        },
    }
    return ctx, ctx_fields, rules


def sync_areas(ctx, ctx_fields, footprint, storeys):
    """Keep footprint / built-up / height / storeys internally consistent."""
    ctx["model"]["footprintAreaMm2"] = footprint
    ctx["model"]["storeyCount"] = storeys
    ctx["model"]["builtUpAreaMm2"] = footprint * storeys
    ctx["model"]["farCountableAreaMm2"] = footprint * storeys
    ctx["model"]["buildingHeightMm"] = PLINTH + storeys * FLOOR_HEIGHT + ABOVE_TERRACE
    ctx["model"]["storeys"] = [
        {"id": "storey_g" if i == 0 else "storey_%d" % i, "index": i,
         "heightMm": FLOOR_HEIGHT, "clearHeightMm": FLOOR_HEIGHT - SLAB,
         "builtUpAreaMm2": footprint}
        for i in range(storeys)
    ]
    ctx_fields["storeys"] = storeys
    ctx_fields["builtUpAreaMm2"] = footprint * storeys
    ctx_fields["farCountableAreaMm2"] = footprint * storeys
    ctx_fields["buildingHeightMm"] = ctx["model"]["buildingHeightMm"]


# ===========================================================================
# room / opening helpers used by the mutators
# ===========================================================================

def room_type_for(rule, pack_vocab):
    w = rule.get("when", {})
    if "roomType" in w:
        p = w["roomType"]
        return p.get("eq") or sorted(p["in"])[0]
    if w.get("roomIsHabitable", {}).get("eq") is True:
        return "bedroom"
    return "bedroom"


def ensure_room(ctx, rtype, storey_id="storey_g"):
    for r in ctx["model"]["rooms"]:
        if r["type"] == rtype:
            return r
    r = {"id": "room_target", "storeyId": storey_id, "type": rtype,
         "name": "Target %s" % rtype.replace("_", " ").title()}
    r.update(rect_props(20000, 300, 2500, 3000))
    r["clearCeilingHeightMm"] = 2900
    r["ventilationOpeningAreaMm2"] = 2_000_000
    r["isInternal"] = False
    ctx["model"]["rooms"].append(r)
    return r


def set_room_rect(room, w, d):
    x0, y0 = room["polygonMm"][0]
    room.update(rect_props(x0, y0, w, d))


def ensure_opening(ctx, kind, role):
    for o in ctx["model"]["openings"]:
        if o["kind"] == kind and o["role"] == role:
            return o
    o = {"id": "open_target", "storeyId": "storey_g", "wallId": "wall_int_9", "kind": kind,
         "role": role, "widthMm": 900, "heightMm": 2100, "sillMm": 0,
         "roomIds": [], "outwardNormalDeg": None}
    ctx["model"]["openings"].append(o)
    return o


# ===========================================================================
# 3x3 zone geometry (vastu contexts)
# ===========================================================================

VASTU_PLOT = 12000          # square plot, northDeg 0
CELL = VASTU_PLOT // 3      # 4000

ZONE_COL = {"W": 0, "SW": 0, "NW": 0, "S": 1, "C": 1, "N": 1, "E": 2, "SE": 2, "NE": 2}
ZONE_ROW = {"SW": 0, "S": 0, "SE": 0, "W": 1, "C": 1, "E": 1, "NW": 2, "N": 2, "NE": 2}
FACING_DEG = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}


def zone_rect(zone, size=2000, dx=0, dy=0):
    """A `size` square inside `zone`, offset by (dx,dy) to avoid coincident centroids."""
    col, row = ZONE_COL[zone], ZONE_ROW[zone]
    x0 = col * CELL + (CELL - size) // 2 + dx
    y0 = row * CELL + (CELL - size) // 2 + dy
    return x0, y0, size, size


def vastu_room(rid, rtype, name, zone, dx=0, dy=0, size=2000):
    x0, y0, w, d = zone_rect(zone, size, dx, dy)
    r = {"id": rid, "storeyId": "storey_g", "type": rtype, "name": name}
    r.update(rect_props(x0, y0, w, d))
    r["clearCeilingHeightMm"] = 2900
    r["ventilationOpeningAreaMm2"] = max(300_000, (w * d) // 10)
    r["isInternal"] = False
    return r


def vastu_context(packs, *, entrance_zone="N", pooja="NE", kitchen="SE", master="SW",
                  toilet="W", stair="S", tank="NE", centre_room=None, mode="strict"):
    """Baseline: every Vastu target in its ideal position. Mutators move exactly one."""
    area = VASTU_PLOT * VASTU_PLOT
    rooms = [
        vastu_room("room_pooja", "pooja", "Pooja", pooja, dx=-400, dy=-400, size=1600),
        vastu_room("room_kitchen", "kitchen", "Kitchen", kitchen, size=2400),
        vastu_room("room_master", "master_bedroom", "Master Bedroom", master, size=3000),
        vastu_room("room_bathwc", "bath_wc", "Bath + WC", toilet, size=1600),
    ]
    if centre_room is None:
        rooms.append(vastu_room("room_living", "living_dining", "Living / Dining", "C", size=4000))
        entry_room = "room_living"
    else:
        rooms.append(centre_room)
        entry_room = centre_room["id"]

    sx, sy, sw, sd = zone_rect(stair, 2000, dx=400, dy=400)
    tx, ty, tw, td = zone_rect(tank, 1200, dx=600, dy=600)

    ctx = {
        "packs": ["vastu"],
        "vastuMode": mode,
        "plot": {
            "boundaryMm": rect(0, 0, VASTU_PLOT, VASTU_PLOT),
            "areaMm2": area,
            "northDeg": 0,
            "frontageMm": VASTU_PLOT,
            "depthMm": VASTU_PLOT,
            "cornerPlot": False,
            "edges": [
                {"index": 0, "role": "rear", "roadWidthMm": None, "setbackProvidedMm": 1500},
                {"index": 1, "role": "side-a", "roadWidthMm": None, "setbackProvidedMm": 1200},
                {"index": 2, "role": "front", "roadWidthMm": 9000, "setbackProvidedMm": 3000},
                {"index": 3, "role": "side-b", "roadWidthMm": None, "setbackProvidedMm": 1200},
            ],
        },
        "profile": {
            "cityPack": "custom",
            "zoneCategory": "residential",
            "buildingUse": "dwelling-single",
            "dwellingUnits": 1,
            "parkingSpacesProvided": 1,
            "rwhDeclared": True,
        },
        "model": {
            "storeyCount": 2,
            "hasStilt": False,
            "hasBasement": False,
            "buildingHeightMm": PLINTH + 2 * FLOOR_HEIGHT + ABOVE_TERRACE,
            "heightComponentsMm": {"mumty": MUMTY_H, "oht": OHT_H},
            "footprintAreaMm2": 86_000_000,
            "builtUpAreaMm2": 172_000_000,
            "farCountableAreaMm2": 172_000_000,
            "storeys": [
                {"id": "storey_g", "index": 0, "heightMm": FLOOR_HEIGHT,
                 "clearHeightMm": FLOOR_HEIGHT - SLAB, "builtUpAreaMm2": 86_000_000},
                {"id": "storey_1", "index": 1, "heightMm": FLOOR_HEIGHT,
                 "clearHeightMm": FLOOR_HEIGHT - SLAB, "builtUpAreaMm2": 86_000_000},
            ],
            "rooms": rooms,
            "openings": [{
                "id": "open_main", "storeyId": "storey_g", "wallId": "wall_ext_n", "kind": "door",
                "role": "main-entrance", "widthMm": 1050, "heightMm": 2100, "sillMm": 0,
                "roomIds": [entry_room], "outwardNormalDeg": FACING_DEG[entrance_zone],
            }],
            "stairs": [{
                "id": "stair_main", "storeyId": "storey_g", "kind": "dogleg", "riserMm": 165,
                "treadMm": 280, "widthMm": 1050, "headroomMm": 2200, "risersCount": 18,
                "centroidMm": [sx + sw // 2, sy + sd // 2],
            }],
            "serviceElements": [{
                "id": "svc_oht", "kind": "oht", "storeyId": "storey_1",
                "centroidMm": [tx + tw // 2, ty + td // 2],
            }],
        },
    }
    return ctx


def zone_limit(check):
    lim = {}
    if "allow" in check:
        lim["allow"] = check["allow"]
    if "deny" in check:
        lim["deny"] = check["deny"]
    if "fallback" in check:
        lim["fallback"] = check["fallback"]
    return lim


# ===========================================================================
# mutators: one per check type, returning (context, expected, description)
# ===========================================================================

def build_fixture(packs, rule, kind, variant=None):
    """kind in {'pass','fail'}; variant names an `extra` behaviour case."""
    check = rule["check"]
    ctype = check["type"]
    pack_id = rule["id"].split(".")[0]
    pack_id = {"nbc": "nbc-core", "blr": "blr", "ncr": "ncr", "hyd": "hyd", "vastu": "vastu"}[pack_id]

    if ctype == "zone_check" or (ctype == "custom" and check["fn"] == "brahmasthan_open"):
        return _vastu_fixture(packs, rule, kind, variant)

    when = rule.get("when", {})
    if "plotAreaMm2" in when:
        area = pick_int(when["plotAreaMm2"], "mm2")
    elif "plotAreaSqm" in when:
        area = pick_int(when["plotAreaSqm"], "sqm") * M2
    else:
        area = 300 * M2
    road = pick_int(when["roadWidthMm"], "mm") if "roadWidthMm" in when else 9000

    full = ctype in ("room_area_min", "room_width_min", "ceiling_height_min",
                     "ventilation_ratio_min", "opening_width_min")
    ctx, cf, rules = build_context(packs, pack_id, area, road, full_rooms=full)
    exp = {"status": None, "actual": None, "limit": None, "elements": []}
    violated = (kind == "fail")
    status = ("pass" if not violated else rule["severity"])
    notes = None
    envelope_area = max(0, cf["plotFrontageMm"] - ctx["plot"]["edges"][1]["setbackProvidedMm"]
                        - ctx["plot"]["edges"][3]["setbackProvidedMm"]) * \
        max(0, cf["plotDepthMm"] - ctx["plot"]["edges"][0]["setbackProvidedMm"]
            - ctx["plot"]["edges"][2]["setbackProvidedMm"])

    # ---------------------------------------------------------------- edges --
    if ctype == "setback_min":
        limit = check["valueMm"]
        roles = edges_covered(check["edge"])
        offender = roles[0]
        for e in ctx["plot"]["edges"]:
            if e["role"] in roles:
                e["setbackProvidedMm"] = limit
        if violated:
            for e in ctx["plot"]["edges"]:
                if e["role"] == offender:
                    e["setbackProvidedMm"] = limit - 1
        actual = limit - 1 if violated else limit
        exp.update(status=status, actual=actual, limit=limit,
                   elements=["plot.edge.%s" % offender] if violated else [])
        desc = ("The %s setback is %s against a %s minimum -- short by 1 mm, the smallest "
                "possible violation." % (offender, mm_txt(actual), mm_txt(limit))) if violated else \
               ("The %s setback is exactly the %s minimum, which is compliant." %
                (offender, mm_txt(limit)))

    # -------------------------------------------------------------- project --
    elif ctype == "coverage_max":
        limit = ratio_limit(check["ratio"], area)
        footprint = limit + (1 if violated else 0)
        far_allowed = min_ratio_limit(rules, cf, "far_max", area) or area * 2
        floors_allowed = min_scalar_limit(rules, cf, "floors_max", "value") or 4
        storeys = max(1, min(floors_allowed, far_allowed // max(1, footprint)))
        sync_areas(ctx, cf, footprint, storeys)
        ctx["profile"]["parkingSpacesProvided"] = parking_required(rules, cf, footprint * storeys)
        exp.update(status=status, actual=footprint, limit=limit)
        desc = ("The footprint is %s against a permitted %s -- over by 1 mm2." %
                (mm2_txt(footprint), mm2_txt(limit))) if violated else \
               ("The footprint is exactly the permitted %s of ground coverage." % mm2_txt(limit))
        if footprint > envelope_area:
            notes = ("On this plot size the seed setback table leaves only %s of buildable "
                     "envelope, so the coverage cap is not simultaneously reachable -- the setback "
                     "table is the binding constraint here. The fixture isolates the coverage rule "
                     "and does not assert geometric consistency with the setbacks. Worth flagging "
                     "to the reviewing architect." % mm2_txt(envelope_area))

    elif ctype == "far_max":
        limit = ratio_limit(check["ratio"], area)
        target = limit + (1 if violated else 0)
        cov_allowed = min_ratio_limit(rules, cf, "coverage_max", area) or (area * 60) // 100
        floors_allowed = min_scalar_limit(rules, cf, "floors_max", "value") or 4
        storeys = max(1, min(floors_allowed, -(-target // max(1, cov_allowed))))
        # split `target` across the storeys so the per-storey areas sum EXACTLY to it
        per = [target // storeys] * storeys
        per[0] += target - sum(per)
        footprint = per[0]
        sync_areas(ctx, cf, footprint, storeys)
        for s, a in zip(ctx["model"]["storeys"], per):
            s["builtUpAreaMm2"] = a
        ctx["model"]["builtUpAreaMm2"] = target
        ctx["model"]["farCountableAreaMm2"] = target
        cf["builtUpAreaMm2"] = target
        cf["farCountableAreaMm2"] = target
        ctx["profile"]["parkingSpacesProvided"] = parking_required(rules, cf, target)
        exp.update(status=status, actual=target, limit=limit)
        desc = ("FAR-countable area is %s over %d floors against a permitted %s -- over by 1 mm2." %
                (mm2_txt(target), storeys, mm2_txt(limit))) if violated else \
               ("FAR-countable area is exactly the permitted %s, spread over %d floors." %
                (mm2_txt(limit), storeys))
        if footprint > envelope_area:
            notes = ("The ground-floor footprint needed to reach this FAR (%s) exceeds the %s of "
                     "buildable envelope the seed setback table leaves on this plot, so FAR and "
                     "setbacks are not simultaneously satisfiable here. The fixture isolates the FAR "
                     "rule. Worth flagging to the reviewing architect."
                     % (mm2_txt(footprint), mm2_txt(envelope_area)))

    elif ctype == "height_max":
        limit = check["valueMm"]
        target = limit + (1 if violated else 0)
        excluded = sum(ctx["model"]["heightComponentsMm"].get(k, 0) for k in check.get("excludes", []))
        floors_allowed = min_scalar_limit(rules, cf, "floors_max", "value") or 4
        storeys = max(1, min(floors_allowed, (target - PLINTH) // FLOOR_HEIGHT))
        fh = (target - PLINTH) // storeys
        plinth = target - storeys * fh
        ctx["model"]["storeyCount"] = storeys
        ctx["model"]["buildingHeightMm"] = target + excluded
        ctx["model"]["storeys"] = [
            {"id": "storey_g" if i == 0 else "storey_%d" % i, "index": i, "heightMm": fh,
             "clearHeightMm": fh - SLAB,
             "builtUpAreaMm2": ctx["model"]["footprintAreaMm2"]} for i in range(storeys)]
        ctx["model"]["builtUpAreaMm2"] = ctx["model"]["footprintAreaMm2"] * storeys
        ctx["model"]["farCountableAreaMm2"] = ctx["model"]["builtUpAreaMm2"]
        cf["storeys"] = storeys
        ctx["profile"]["parkingSpacesProvided"] = parking_required(
            rules, cf, ctx["model"]["builtUpAreaMm2"])
        exp.update(status=status, actual=target, limit=limit)
        desc = ("Height to terrace level is %s against a %s cap (%s of mumty and tank are "
                "excluded) -- over by 1 mm." % (mm_txt(target), mm_txt(limit), mm_txt(excluded))
                ) if violated else \
               ("Height to terrace level is exactly the %s cap; %s of mumty and overhead tank "
                "are excluded by this rule." % (mm_txt(limit), mm_txt(excluded)))

    elif ctype == "floors_max":
        limit = check["value"]
        storeys = limit + (1 if violated else 0)
        far_allowed = min_ratio_limit(rules, cf, "far_max", area) or area * 2
        footprint = min(ctx["model"]["footprintAreaMm2"], far_allowed // storeys)
        sync_areas(ctx, cf, footprint, storeys)
        ctx["profile"]["parkingSpacesProvided"] = parking_required(rules, cf, footprint * storeys)
        exp.update(status=status, actual=storeys, limit=limit)
        desc = ("The design has %d floors above ground against a limit of %d -- one floor over." %
                (storeys, limit)) if violated else \
               ("The design has exactly the %d floors this road width permits." % limit)
        if violated:
            height_allowed = min_scalar_limit(rules, cf, "height_max", "valueMm")
            notes = ("The footprint was reduced to %s so FAR still passes and the extra floor is "
                     "the only thing wrong with the area figures." % mm2_txt(footprint))
            if height_allowed is not None and PLINTH + storeys * FLOOR_HEIGHT > height_allowed:
                notes += (" The extra floor unavoidably also breaks the height cap of %s on this "
                          "road width -- an extra storey cannot be added without height, so no "
                          "context can isolate the floor-count rule any further. This fixture "
                          "asserts the floor-count rule only." % mm_txt(height_allowed))

    elif ctype == "parking_min":
        base = cf["dwellingUnits"] if check["basis"] == "dwelling" else ctx["model"]["builtUpAreaMm2"]
        need = max(-((-check["rate"]["num"] * base) // check["rate"]["den"]),
                   check.get("minSpaces", 0))
        provided = need - 1 if violated else need
        provided = max(0, provided)
        ctx["profile"]["parkingSpacesProvided"] = provided
        exp.update(status=status, actual=provided, limit=need)
        desc = ("%d car space(s) are shown against %d required -- one short." % (provided, need)
                ) if violated else \
               ("Exactly the %d required car space(s) are shown." % need)

    elif ctype == "projection_max":
        limit = check["valueMm"]
        target_el = check["element"]
        hit = None
        for p in ctx["model"]["projections"]:
            if p["element"] == target_el:
                p["projectionMm"] = limit + (1 if violated else 0)
                p["intoSetback"] = True
                hit = p
        if hit is None:
            hit = {"id": "proj_%s_1" % target_el, "storeyId": "storey_g", "element": target_el,
                   "edgeRole": "front", "projectionMm": limit + (1 if violated else 0),
                   "intoSetback": True}
            ctx["model"]["projections"].append(hit)
        exp.update(status=status, actual=hit["projectionMm"], limit=limit,
                   elements=[hit["id"]] if violated else [])
        desc = ("The %s projects %s into the front setback against a %s limit -- over by 1 mm." %
                (target_el, mm_txt(hit["projectionMm"]), mm_txt(limit))) if violated else \
               ("The %s projects exactly the %s permitted into the front setback." %
                (target_el, mm_txt(limit)))

    elif ctype == "custom" and check["fn"] == "rwh_required":
        declared = not violated
        ctx["profile"]["rwhDeclared"] = declared
        exp.update(status=status, actual=declared, limit=True)
        desc = ("No rainwater harvesting structure is declared on a plot where it is mandatory."
                ) if violated else \
               ("A rainwater harvesting structure is declared, which is what this plot size requires.")

    # ------------------------------------------------------------------ room --
    elif ctype == "room_area_min":
        limit = check["valueMm2"]
        rtype = room_type_for(rule, None)
        room = ensure_room(ctx, rtype)
        w, d = factor_rect(limit, WIDTH_MIN.get(rtype, 900))
        if violated:
            set_room_rect(room, w, d - 1)
        else:
            set_room_rect(room, w, d)
        room["ventilationOpeningAreaMm2"] = max(300_000, -(-room["areaMm2"] // 10) + 50_000)
        actual = room["areaMm2"]
        exp.update(status=status, actual=actual, limit=limit,
                   elements=[room["id"]] if violated else [])
        desc = ("%s is %s x %s = %s, short of the %s minimum by %s -- and still %s wide, so it "
                "trips the area rule only." % (room["name"], mm_txt(w), mm_txt(d - 1),
                                               mm2_txt(actual), mm2_txt(limit), mm2_txt(limit - actual),
                                               mm_txt(room["leastWidthMm"]))) if violated else \
               ("%s is %s x %s = exactly the %s minimum." % (room["name"], mm_txt(w), mm_txt(d),
                                                            mm2_txt(limit)))

    elif ctype == "room_width_min":
        limit = check["valueMm"]
        rtype = room_type_for(rule, None)
        room = ensure_room(ctx, rtype)
        w = limit - 1 if violated else limit
        need_area = AREA_MIN.get(rtype, 1_000_000) + 500_000
        d = -(-need_area // w)
        set_room_rect(room, w, d)
        room["ventilationOpeningAreaMm2"] = max(300_000, -(-room["areaMm2"] // 10) + 50_000)
        exp.update(status=status, actual=w, limit=limit,
                   elements=[room["id"]] if violated else [])
        desc = ("%s is %s wide against a %s minimum -- 1 mm short. Its area is %s, comfortably "
                "over the area minimum, so only the width rule can go red." %
                (room["name"], mm_txt(w), mm_txt(limit), mm2_txt(room["areaMm2"]))) if violated else \
               ("%s is exactly %s wide, the permitted minimum, with %s of area." %
                (room["name"], mm_txt(limit), mm2_txt(room["areaMm2"])))

    elif ctype == "ceiling_height_min":
        limit = check["valueMm"]
        rtype = room_type_for(rule, None)
        room = ensure_room(ctx, rtype)
        room["clearCeilingHeightMm"] = limit - 1 if violated else limit
        exp.update(status=status, actual=room["clearCeilingHeightMm"], limit=limit,
                   elements=[room["id"]] if violated else [])
        desc = ("%s has %s of clear height against a %s minimum -- 1 mm short. The storey's own "
                "clear height is %s, so this is a dropped-ceiling problem in one room, not a "
                "storey-height problem." %
                (room["name"], mm_txt(room["clearCeilingHeightMm"]), mm_txt(limit),
                 mm_txt(FLOOR_HEIGHT - SLAB))) if violated else \
               ("%s has exactly the %s of clear height required." % (room["name"], mm_txt(limit)))

    elif ctype == "ventilation_ratio_min":
        rtype = room_type_for(rule, None)
        room = ensure_room(ctx, rtype)
        need = 0
        if "ratio" in check:
            need = -((-check["ratio"]["num"] * room["areaMm2"]) // check["ratio"]["den"])
        need = max(need, check.get("minAreaMm2", 0))
        room["ventilationOpeningAreaMm2"] = need - 1 if violated else need
        exp.update(status=status, actual=room["ventilationOpeningAreaMm2"], limit=need,
                   elements=[room["id"]] if violated else [])
        basis = ("one tenth of its %s floor area" % mm2_txt(room["areaMm2"])) if "ratio" in check \
            else "the absolute minimum"
        desc = ("%s has %s of openable area against %s required (%s) -- 1 mm2 short." %
                (room["name"], mm2_txt(room["ventilationOpeningAreaMm2"]), mm2_txt(need), basis)
                ) if violated else \
               ("%s has exactly the %s of openable area required (%s)." %
                (room["name"], mm2_txt(need), basis))

    # ----------------------------------------------------------------- stair --
    elif ctype in ("stair_riser_max", "stair_tread_min", "stair_width_min", "headroom_min"):
        limit = check["valueMm"]
        key = {"stair_riser_max": "riserMm", "stair_tread_min": "treadMm",
               "stair_width_min": "widthMm", "headroom_min": "headroomMm"}[ctype]
        stair = ctx["model"]["stairs"][0]
        if ctype == "stair_riser_max":
            stair[key] = limit + (1 if violated else 0)
        else:
            stair[key] = limit - (1 if violated else 0)
        if key == "riserMm":
            stair["risersCount"] = -(-(FLOOR_HEIGHT) // stair["riserMm"])
        exp.update(status=status, actual=stair[key], limit=limit,
                   elements=[stair["id"]] if violated else [])
        word = {"riserMm": "riser", "treadMm": "tread", "widthMm": "flight width",
                "headroomMm": "headroom"}[key]
        desc = ("The stair %s is %s against a %s limit -- over/under by 1 mm; every other stair "
                "parameter stays compliant." % (word, mm_txt(stair[key]), mm_txt(limit))
                ) if violated else \
               ("The stair %s is exactly the permitted %s." % (word, mm_txt(limit)))

    # --------------------------------------------------------------- opening --
    elif ctype == "opening_width_min":
        limit = check["valueMm"]
        w = rule.get("when", {})
        okind = w.get("openingKind", {}).get("eq", "door")
        orole = w.get("openingRole", {}).get("eq", "internal")
        op = ensure_opening(ctx, okind, orole)
        op["widthMm"] = limit - (1 if violated else 0)
        exp.update(status=status, actual=op["widthMm"], limit=limit,
                   elements=[op["id"]] if violated else [])
        desc = ("The %s %s is %s wide against a %s minimum -- 1 mm short." %
                (orole.replace("-", " "), okind, mm_txt(op["widthMm"]), mm_txt(limit))
                ) if violated else \
               ("The %s %s is exactly the %s minimum width." %
                (orole.replace("-", " "), okind, mm_txt(limit)))

    else:
        raise ValueError("no mutator for check type %r" % ctype)

    fid = "%s.%s" % (rule["id"], kind)
    doc = {
        "$schema": "../../../rulepacks/schema/fixture.schema.json",
        "fixtureId": fid,
        "packId": pack_id,
        "ruleId": rule["id"],
        "kind": kind,
        "description": desc,
        "context": ctx,
        "expected": exp,
    }
    if notes:
        doc["notes"] = notes
    return pack_id, fid, doc


# ---------------------------------------------------------------------------
# Vastu fixtures
# ---------------------------------------------------------------------------

TARGET_NOUN = {
    "vastu.entrance.edge": "the main entrance",
    "vastu.pooja.zone": "the pooja room",
    "vastu.kitchen.zone": "the kitchen",
    "vastu.master.zone": "the master bedroom",
    "vastu.toilet.zone": "the toilet",
    "vastu.toilet.never_ne": "the toilet",
    "vastu.stair.zone": "the staircase",
    "vastu.water_tank.zone": "the overhead water tank",
}

BAD_ZONE_FOR = {
    "vastu.entrance.edge": "S",
    "vastu.pooja.zone": "SW",
    "vastu.kitchen.zone": "NE",
    "vastu.master.zone": "NE",
    "vastu.toilet.zone": "SE",
    "vastu.stair.zone": "NE",
    "vastu.water_tank.zone": "SW",
}


def _vastu_fixture(packs, rule, kind, variant):
    check = rule["check"]
    rid = rule["id"]
    mode = "strict"
    if variant == "advisory":
        mode = "advisory"

    ceiling = packs["vastu"]["scoring"]["modes"][mode]["severityCeiling"]
    sev = rule["severity"]
    clamped = "warn" if (ceiling == "warn" and sev == "fail") else sev

    kwargs = {"mode": mode}
    sat = {"num": 1, "den": 1}
    actual_zone = None

    if check["type"] == "custom":       # brahmasthan_open
        limit_bp = (check["args"]["maxEnclosedRatio"]["num"] * 10000) // \
            check["args"]["maxEnclosedRatio"]["den"]
        cell_area = CELL * CELL
        # a bedroom straddling the centre cell: overlap height chosen for an exact bp
        overlap_d = (limit_bp * cell_area) // (10000 * CELL)      # 2000 mm -> exactly 5000 bp
        d = overlap_d + (1 if kind == "fail" else 0)
        x0, y0 = CELL, CELL
        room = {"id": "room_bed_centre", "storeyId": "storey_g", "type": "bedroom",
                "name": "Bedroom 1"}
        room.update(rect_props(x0, y0, CELL, d))
        room["clearCeilingHeightMm"] = 2900
        room["ventilationOpeningAreaMm2"] = 1_400_000
        room["isInternal"] = False
        ctx = vastu_context(packs, centre_room=room, **kwargs)
        actual_bp = (10000 * CELL * d) // cell_area
        status = "pass" if actual_bp <= limit_bp else clamped
        sat = {"num": 1, "den": 1} if actual_bp <= limit_bp else {"num": 0, "den": 1}
        exp = {"status": status, "actual": actual_bp, "limit": limit_bp,
               "elements": [] if status == "pass" else [room["id"]], "satisfaction": sat}
        desc = ("Bedroom 1 covers %d/10000 of the centre cell against a %d/10000 ceiling -- over "
                "by the smallest step a whole millimetre of depth allows." % (actual_bp, limit_bp)
                ) if status != "pass" else \
               ("Bedroom 1 covers exactly %d/10000 of the centre cell, the most the brahmasthan "
                "rule allows, so the centre still reads as open." % actual_bp)
        fid = "%s.%s" % (rid, kind if variant is None else "extra-" + variant)
        return "vastu", fid, _wrap(fid, rid, kind if variant is None else "extra", desc, ctx, exp)

    # --- zone_check ---------------------------------------------------------
    allow = check.get("allow", [])
    deny = check.get("deny", [])
    fb = check.get("fallback", {}).get("allow", [])

    if variant == "fallback":
        target_zone = fb[0]
    elif kind == "pass":
        target_zone = allow[0] if allow else ("W" if "NE" in deny else "N")
    else:
        target_zone = deny[0] if deny else BAD_ZONE_FOR[rid]

    if check["mode"] == "facing":
        kwargs["entrance_zone"] = target_zone
    else:
        tk = check["target"]["kind"]
        if tk == "room":
            rtypes = check["target"]["roomTypes"]
            if "pooja" in rtypes:
                kwargs["pooja"] = target_zone
            elif "kitchen" in rtypes:
                kwargs["kitchen"] = target_zone
            elif "master_bedroom" in rtypes:
                kwargs["master"] = target_zone
            else:
                kwargs["toilet"] = target_zone
        elif tk == "stair":
            kwargs["stair"] = target_zone
        elif tk == "service":
            kwargs["tank"] = target_zone

    ctx = vastu_context(packs, **kwargs)
    actual_zone = target_zone

    if target_zone in deny:
        status, sat = clamped, {"num": 0, "den": 1}
    elif allow and target_zone in allow:
        status, sat = "pass", {"num": 1, "den": 1}
    elif fb and target_zone in fb:
        status, sat = "warn", check["fallback"]["scoreRatio"]
    elif not allow:
        # deny-only rule: anything outside the forbidden set is compliant
        status, sat = "pass", {"num": 1, "den": 1}
    else:
        status, sat = clamped, {"num": 0, "den": 1}

    el_ids = []
    if status != "pass":
        if check["mode"] == "facing":
            el_ids = ["open_main"]
        else:
            tk = check["target"]["kind"]
            el_ids = {"room": None, "stair": ["stair_main"], "service": ["svc_oht"]}[tk] or []
            if tk == "room":
                rtypes = check["target"]["roomTypes"]
                el_ids = ["room_pooja"] if "pooja" in rtypes else \
                    ["room_kitchen"] if "kitchen" in rtypes else \
                    ["room_master"] if "master_bedroom" in rtypes else ["room_bathwc"]

    exp = {"status": status, "actual": [actual_zone], "limit": zone_limit(check),
           "elements": el_ids, "satisfaction": sat}

    noun = TARGET_NOUN[rid]
    verb = "faces" if check["mode"] == "facing" else "sits in"
    if status == "pass" and not allow:
        desc = ("%s %s %s, outside the %s this rule forbids outright, so the rule is satisfied "
                "(%s mode)." % (noun, verb, actual_zone, "/".join(deny), ctx["vastuMode"]))
    elif status == "pass":
        desc = ("%s %s %s, one of the preferred directions, so the rule scores full marks "
                "(%s mode)." % (noun, verb, actual_zone, ctx["vastuMode"]))
    elif target_zone in fb:
        desc = ("%s %s %s, the accepted fallback, so the rule scores %d/%d and reports a "
                "warning rather than a clean pass." % (noun, verb, actual_zone,
                                                      sat["num"], sat["den"]))
    elif target_zone in deny:
        desc = ("%s %s %s, the one direction this rule forbids outright; in %s mode that is "
                "reported at severity %s." % (noun, verb, actual_zone, ctx["vastuMode"], status))
    else:
        desc = ("%s %s %s, which is neither preferred nor an accepted fallback, so the rule "
                "scores zero (%s mode)." % (noun, verb, actual_zone, ctx["vastuMode"]))
    desc = desc[0].upper() + desc[1:]

    fk = kind if variant is None else "extra"
    fid = "%s.%s" % (rid, kind if variant is None else "extra-" + variant)
    return "vastu", fid, _wrap(fid, rid, fk, desc, ctx, exp)


def _wrap(fid, rid, kind, desc, ctx, exp):
    return {
        "$schema": "../../../rulepacks/schema/fixture.schema.json",
        "fixtureId": fid,
        "packId": "vastu",
        "ruleId": rid,
        "kind": kind,
        "description": desc,
        "context": ctx,
        "expected": exp,
    }


# ===========================================================================
# main
# ===========================================================================

EXTRAS = [
    ("vastu.kitchen.zone", "pass", "fallback"),
    ("vastu.toilet.never_ne", "fail", "advisory"),
]


def main():
    packs = load_packs()
    written = []

    jobs = []
    for pack_id in ("nbc-core", "blr", "ncr", "hyd", "vastu"):
        for rule in packs[pack_id]["rules"]:
            jobs.append((rule, "pass", None))
            jobs.append((rule, "fail", None))
    by_id = {r["id"]: r for p in packs.values() for r in p["rules"]}
    for rid, kind, variant in EXTRAS:
        jobs.append((by_id[rid], kind, variant))

    for rule, kind, variant in jobs:
        pack_id, fid, doc = build_fixture(packs, rule, kind, variant)
        d = os.path.join(FIXTURE_ROOT, pack_id)
        if not os.path.isdir(d):
            os.makedirs(d)
        path = os.path.join(d, fid + ".json")
        with open(path, "w") as fh:
            json.dump(doc, fh, indent=2, ensure_ascii=True)
            fh.write("\n")
        written.append((pack_id, rule["id"], doc["kind"], fid,
                        "%s/%s.json" % (pack_id, fid), doc["expected"]["status"]))

    # ---- manifest --------------------------------------------------------
    per_rule = {}
    for pack_id, rid, kind, fid, rel, status in written:
        e = per_rule.setdefault(rid, {"packId": pack_id, "ruleId": rid,
                                      "pass": [], "fail": [], "extra": []})
        e[kind].append(rel)

    pack_entries = []
    for pack_id in ("nbc-core", "blr", "ncr", "hyd", "vastu"):
        rids = [r["id"] for r in packs[pack_id]["rules"]]
        pack_entries.append({
            "packId": pack_id,
            "packVersion": packs[pack_id]["version"],
            "ruleCount": len(rids),
            "rules": [per_rule[r] for r in rids],
        })

    manifest = {
        "$comment": ("Enumerate fixtures from here, never by globbing: a rule whose fixtures were "
                     "deleted must break the suite loudly instead of silently dropping out of it."),
        "schemaVersion": 1,
        "generatedBy": "fixtures/rules/_tools/generate_fixtures.py",
        "fixtureSchema": "rulepacks/schema/fixture.schema.json",
        "invariants": [
            "every rule id in rulepacks/*.json appears exactly once in exactly one pack entry",
            "every rule has at least one 'pass' and at least one 'fail' fixture",
            "fixtureId == filename without .json, and == '<ruleId>.<kind-suffix>'",
        ],
        "counts": {
            "packs": len(pack_entries),
            "rules": sum(p["ruleCount"] for p in pack_entries),
            "fixtures": len(written),
        },
        "packs": pack_entries,
        "fixtures": [
            {"fixtureId": fid, "packId": pack_id, "ruleId": rid, "kind": kind,
             "path": rel, "expectStatus": status}
            for pack_id, rid, kind, fid, rel, status in written
        ],
    }
    with open(os.path.join(FIXTURE_ROOT, "index.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=True)
        fh.write("\n")

    print("fixtures written: %d across %d rules" % (len(written), len(per_rule)))
    for p in pack_entries:
        print("  %-10s %3d rules" % (p["packId"], p["ruleCount"]))


if __name__ == "__main__":
    main()
