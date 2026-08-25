from __future__ import annotations

"""Data-side gate for rulepacks/ and fixtures/rules/.

Run this in CI BEFORE the rules-engine tests. It catches the failure modes that
would otherwise show up as a green suite over rules nobody checked:

  * a rule id that exists in two packs, or whose prefix does not match its pack
  * a rule with no failing fixture (so nothing proves the check can go red)
  * a fixture on disk that index.json does not list, or vice versa
  * a `when` field or check type that the DSL schema does not define
  * a float anywhere in pack or fixture data (geometry must be integer mm)
  * fixture room geometry whose stored areaMm2 / leastWidthMm / centroidMm
    disagrees with its own polygon
  * a pack value presented as authoritative -- every seed pack rule must carry
    confidence "seed" and a citation

Exit code 0 = clean, 1 = findings printed.

    python3 fixtures/rules/_tools/verify_fixtures.py
"""

import json
import math
import os
import sys
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_ROOT = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(FIXTURE_ROOT))
PACK_DIR = os.path.join(REPO, "rulepacks")
SCHEMA = os.path.join(PACK_DIR, "schema", "rulepack.schema.json")

PACK_IDS = ("nbc-core", "blr", "ncr", "hyd", "vastu")

problems = []


def fail(where, msg):
    problems.append("%s: %s" % (where, msg))


def load(path):
    with open(path) as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def walk_floats(node, path, where):
    if isinstance(node, float):
        fail(where, "float at %s (%r) -- pack and fixture data are integer-only" % (path, node))
    elif isinstance(node, dict):
        for k, v in node.items():
            walk_floats(v, "%s.%s" % (path, k), where)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk_floats(v, "%s[%d]" % (path, i), where)


def shoelace_x2(poly):
    """Twice the signed area, exact integer."""
    total = 0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        total += x0 * y1 - x1 * y0
    return total


def poly_area(poly):
    return abs(shoelace_x2(poly)) // 2, abs(shoelace_x2(poly)) % 2


def poly_centroid(poly):
    """Round-half-up integer centroid, matching the engine contract."""
    a2 = shoelace_x2(poly)
    if a2 == 0:
        return None
    cx = cy = 0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    fx = Fraction(cx, 3 * a2)
    fy = Fraction(cy, 3 * a2)
    return [math.floor(fx + Fraction(1, 2)), math.floor(fy + Fraction(1, 2))]


def poly_bbox_least_width(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(max(xs) - min(xs), max(ys) - min(ys))


# ---------------------------------------------------------------------------
# 1. schema-derived closed sets
# ---------------------------------------------------------------------------

schema = load(SCHEMA)
WHEN_FIELDS = set(schema["$defs"]["predicate"]["properties"].keys())
CHECK_TYPES = set(schema["$defs"]["check"]["properties"]["type"]["enum"])
CUSTOM_FNS = set(schema["$defs"]["check_custom"]["properties"]["fn"]["enum"])
STRATEGIES = set(schema["$defs"]["autofix"]["properties"]["strategy"]["enum"])
ROOM_TYPES = set(schema["$defs"]["roomType"]["enum"])
CONFIDENCES = set(schema["$defs"]["confidence"]["enum"])
OPERATORS = {"lt", "lte", "gt", "gte", "eq", "in"}

# ---------------------------------------------------------------------------
# 2. packs
# ---------------------------------------------------------------------------

packs = {}
rule_owner = {}
for pid in PACK_IDS:
    path = os.path.join(PACK_DIR, pid + ".json")
    if not os.path.isfile(path):
        fail("rulepacks", "missing pack file %s.json" % pid)
        continue
    p = load(path)
    packs[pid] = p
    where = "rulepacks/%s.json" % pid
    walk_floats(p, "$", where)

    if p["pack"] != pid:
        fail(where, "pack field %r does not match filename" % p["pack"])
    ext = p.get("extends")
    if ext is not None and ext not in PACK_IDS:
        fail(where, "extends unknown pack %r" % ext)
    scoring = p.get("scoring")
    group_ids = {g["id"] for g in scoring.get("groups", [])} if scoring else set()

    for r in p["rules"]:
        rid = r["id"]
        if rid in rule_owner:
            fail(where, "rule id %s is also defined in %s -- ids must be globally unique"
                 % (rid, rule_owner[rid]))
        rule_owner[rid] = pid
        rwhere = "%s :: %s" % (where, rid)

        if rid.split(".")[0] != p["idPrefix"]:
            fail(rwhere, "id prefix does not match pack idPrefix %r" % p["idPrefix"])
        for field, pred in r.get("when", {}).items():
            if field not in WHEN_FIELDS:
                fail(rwhere, "`when` field %r is not in the closed context field set" % field)
            for op in pred:
                if op not in OPERATORS:
                    fail(rwhere, "`when.%s` uses unknown operator %r" % (field, op))
            for key in ("roomType",):
                if field == key:
                    vals = pred.get("in", []) + ([pred["eq"]] if "eq" in pred else [])
                    for v in vals:
                        if v not in ROOM_TYPES:
                            fail(rwhere, "unknown roomType %r" % v)
        ct = r["check"]["type"]
        if ct not in CHECK_TYPES:
            fail(rwhere, "unknown check type %r" % ct)
        if ct == "custom" and r["check"]["fn"] not in CUSTOM_FNS:
            fail(rwhere, "unknown custom fn %r" % r["check"]["fn"])
        if "autofix" in r and r["autofix"]["strategy"] not in STRATEGIES:
            fail(rwhere, "unknown autofix strategy %r" % r["autofix"]["strategy"])
        if r.get("confidence") not in CONFIDENCES:
            fail(rwhere, "confidence %r is not on the ladder" % r.get("confidence"))
        if p["confidenceDefault"] == "seed" and r.get("confidence") != "seed":
            fail(rwhere, "pack is seed but rule claims confidence %r" % r.get("confidence"))
        if not r.get("cite", "").strip():
            fail(rwhere, "empty cite -- every rule must be traceable to a clause")
        if not r.get("fix", "").strip():
            fail(rwhere, "empty fix -- every rule must say what to do next")
        if scoring:
            if "weight" not in r:
                fail(rwhere, "scoring pack rule has no weight")
            if r.get("group") not in group_ids:
                fail(rwhere, "group %r is not declared in scoring.groups" % r.get("group"))

    if scoring:
        total = sum(r.get("weight", 0) for r in p["rules"])
        if total != scoring["scale"]["max"]:
            fail(where, "rule weights sum to %d, not scale.max %d -- the score would not span "
                        "the full range when every rule applies"
                 % (total, scoring["scale"]["max"]))

# ---------------------------------------------------------------------------
# 2b. cross-table consistency: floors_max vs height_max
#
# A pack that permits N floors but caps height below N x (NBC minimum clear
# height + slab) + plinth is internally unsatisfiable: the architect gets a
# green floor count and a red height whatever they draw. Cheap to check, and it
# only shows up if you look across two tables that were transcribed separately.
# ---------------------------------------------------------------------------

MIN_FLOOR_TO_FLOOR = 2900   # NBC habitable clear height 2750 + 150 slab
PLINTH = 600

NUMERIC_WHEN = {"plotAreaSqm", "plotAreaMm2", "plotFrontageMm", "plotDepthMm", "roadWidthMm",
                "edgeRoadWidthMm", "abuttingRoadCount", "storeys", "buildingHeightMm",
                "builtUpAreaMm2", "farCountableAreaMm2", "dwellingUnits", "storeyIndex"}


def _domain(pred):
    """('set', {...}) or ('range', lo, hi) with None for unbounded."""
    if "eq" in pred:
        return ("set", {pred["eq"]})
    if "in" in pred:
        return ("set", set(pred["in"]))
    lo = pred.get("gte")
    if "gt" in pred:
        g = pred["gt"] + 1
        lo = g if lo is None else max(lo, g)
    hi = pred.get("lte")
    if "lt" in pred:
        l = pred["lt"] - 1
        hi = l if hi is None else min(hi, l)
    return ("range", lo, hi)


def _intersects(d1, d2, numeric):
    if d1[0] == "set" and d2[0] == "set":
        return bool(d1[1] & d2[1])
    if d1[0] == "range" and d2[0] == "range":
        lo = max([v for v in (d1[1], d2[1]) if v is not None], default=None)
        hi = min([v for v in (d1[2], d2[2]) if v is not None], default=None)
        return lo is None or hi is None or lo <= hi
    rng, st = (d1, d2) if d1[0] == "range" else (d2, d1)
    if not numeric:
        return True
    return any((rng[1] is None or v >= rng[1]) and (rng[2] is None or v <= rng[2])
               for v in st[1] if isinstance(v, int))


def whens_overlap(w1, w2):
    for field in set(w1) & set(w2):
        if not _intersects(_domain(w1[field]), _domain(w2[field]), field in NUMERIC_WHEN):
            return False
    return True


for pid, p in packs.items():
    floors = [r for r in p["rules"] if r["check"]["type"] == "floors_max"]
    heights = [r for r in p["rules"] if r["check"]["type"] == "height_max"]
    for fr in floors:
        for hr in heights:
            if not whens_overlap(fr.get("when", {}), hr.get("when", {})):
                continue
            needed = PLINTH + fr["check"]["value"] * MIN_FLOOR_TO_FLOOR
            if needed > hr["check"]["valueMm"]:
                fail("rulepacks/%s.json" % pid,
                     "%s permits %d floors but %s caps height at %d mm; %d floors need at least "
                     "%d mm (plinth %d + %d x %d). The two tables are not simultaneously "
                     "satisfiable -- re-check both against the primary document."
                     % (fr["id"], fr["check"]["value"], hr["id"], hr["check"]["valueMm"],
                        fr["check"]["value"], needed, PLINTH, fr["check"]["value"],
                        MIN_FLOOR_TO_FLOOR))

# ---------------------------------------------------------------------------
# 3. fixtures on disk vs index.json
# ---------------------------------------------------------------------------

index_path = os.path.join(FIXTURE_ROOT, "index.json")
if not os.path.isfile(index_path):
    fail("fixtures/rules", "index.json is missing")
    print("\n".join(problems))
    sys.exit(1)
index = load(index_path)

on_disk = set()
for pid in PACK_IDS:
    d = os.path.join(FIXTURE_ROOT, pid)
    if not os.path.isdir(d):
        fail("fixtures/rules", "no fixture directory for pack %s" % pid)
        continue
    for name in sorted(os.listdir(d)):
        if name.endswith(".json"):
            on_disk.add("%s/%s" % (pid, name))

listed = {f["path"] for f in index["fixtures"]}
for extra in sorted(on_disk - listed):
    fail("fixtures/rules/index.json", "%s exists on disk but is not listed" % extra)
for missing in sorted(listed - on_disk):
    fail("fixtures/rules/index.json", "%s is listed but missing on disk" % missing)

coverage = {}
for entry in index["packs"]:
    for r in entry["rules"]:
        coverage[r["ruleId"]] = r

for rid, owner in sorted(rule_owner.items()):
    c = coverage.get(rid)
    if c is None:
        fail("fixtures/rules/index.json", "rule %s has no fixture entry" % rid)
        continue
    if not c["pass"]:
        fail("fixtures/rules", "rule %s has no passing fixture" % rid)
    if not c["fail"]:
        fail("fixtures/rules", "rule %s has no failing fixture" % rid)
    if c["packId"] != owner:
        fail("fixtures/rules/index.json", "rule %s filed under %s but owned by %s"
             % (rid, c["packId"], owner))

for rid in sorted(set(coverage) - set(rule_owner)):
    fail("fixtures/rules/index.json", "fixtures exist for unknown rule %s" % rid)

# ---------------------------------------------------------------------------
# 4. each fixture: shape, geometry self-consistency, expected sanity
# ---------------------------------------------------------------------------

severity_of = {r["id"]: r["severity"] for p in packs.values() for r in p["rules"]}

for rel in sorted(on_disk):
    path = os.path.join(FIXTURE_ROOT, rel)
    doc = load(path)
    where = "fixtures/rules/%s" % rel
    walk_floats(doc, "$", where)

    base = os.path.basename(rel)[:-len(".json")]
    if doc["fixtureId"] != base:
        fail(where, "fixtureId %r does not match filename" % doc["fixtureId"])
    if not base.startswith(doc["ruleId"] + "."):
        fail(where, "filename does not start with its ruleId")
    if doc["ruleId"] not in severity_of:
        fail(where, "references unknown rule %s" % doc["ruleId"])
        continue
    if doc["packId"] != rel.split("/")[0]:
        fail(where, "packId %r does not match its directory" % doc["packId"])

    exp = doc["expected"]
    if doc["kind"] == "pass" and exp["status"] != "pass":
        fail(where, "kind is pass but expected.status is %r" % exp["status"])
    if doc["kind"] == "fail" and exp["status"] not in ("warn", "fail"):
        fail(where, "kind is fail but expected.status is %r" % exp["status"])
    if doc["kind"] == "fail":
        sev = severity_of[doc["ruleId"]]
        mode = doc["context"]["vastuMode"]
        allowed = {sev}
        if doc["packId"] == "vastu" and mode == "advisory":
            allowed = {"warn"}
        if exp["status"] not in allowed:
            fail(where, "expected.status %r does not match rule severity %r (mode %s)"
                 % (exp["status"], sev, mode))

    ctx = doc["context"]
    plot = ctx["plot"]
    area, rem = poly_area(plot["boundaryMm"])
    if rem:
        fail(where, "plot boundary has a half-mm2 area -- polygon is not on the mm grid")
    if area != plot["areaMm2"]:
        fail(where, "plot areaMm2 %d disagrees with its boundary (%d)" % (plot["areaMm2"], area))
    for e in plot["edges"]:
        if not 0 <= e["index"] < len(plot["boundaryMm"]):
            fail(where, "edge index %d out of range" % e["index"])
    roles = [e["role"] for e in plot["edges"]]
    if roles.count("front") != 1:
        fail(where, "plot must have exactly one front edge, found %d" % roles.count("front"))

    storey_ids = {s["id"] for s in ctx["model"]["storeys"]}
    if len(ctx["model"]["storeys"]) != ctx["model"]["storeyCount"]:
        fail(where, "storeys[] has %d entries but storeyCount is %d"
             % (len(ctx["model"]["storeys"]), ctx["model"]["storeyCount"]))
    tot = sum(s.get("builtUpAreaMm2", 0) for s in ctx["model"]["storeys"])
    if tot != ctx["model"]["builtUpAreaMm2"]:
        fail(where, "per-storey built-up sums to %d but builtUpAreaMm2 is %d"
             % (tot, ctx["model"]["builtUpAreaMm2"]))
    if ctx["model"]["farCountableAreaMm2"] > ctx["model"]["builtUpAreaMm2"]:
        fail(where, "farCountableAreaMm2 exceeds builtUpAreaMm2")

    room_ids = set()
    for r in ctx["model"]["rooms"]:
        rw = "%s :: room %s" % (where, r["id"])
        if r["id"] in room_ids:
            fail(rw, "duplicate room id")
        room_ids.add(r["id"])
        if r["storeyId"] not in storey_ids:
            fail(rw, "storeyId %r is not in storeys[]" % r["storeyId"])
        if r["type"] not in ROOM_TYPES:
            fail(rw, "unknown room type %r" % r["type"])
        a, rem = poly_area(r["polygonMm"])
        if rem or a != r["areaMm2"]:
            fail(rw, "areaMm2 %d disagrees with polygon (%d)" % (r["areaMm2"], a))
        lw = poly_bbox_least_width(r["polygonMm"])
        if lw != r["leastWidthMm"]:
            fail(rw, "leastWidthMm %d disagrees with polygon bbox (%d)" % (r["leastWidthMm"], lw))
        c = poly_centroid(r["polygonMm"])
        if c != r["centroidMm"]:
            fail(rw, "centroidMm %r disagrees with polygon (%r)" % (r["centroidMm"], c))

    for o in ctx["model"]["openings"]:
        if o["storeyId"] not in storey_ids:
            fail(where, "opening %s references unknown storey %r" % (o["id"], o["storeyId"]))
        for rid2 in o.get("roomIds", []):
            if rid2 not in room_ids:
                fail(where, "opening %s references unknown room %r" % (o["id"], rid2))
    for s in ctx["model"]["stairs"]:
        if s["storeyId"] not in storey_ids:
            fail(where, "stair %s references unknown storey %r" % (s["id"], s["storeyId"]))
    for pr in ctx["model"].get("projections", []):
        if pr["storeyId"] not in storey_ids:
            fail(where, "projection %s references unknown storey %r" % (pr["id"], pr["storeyId"]))

    for eid in exp.get("elements", []):
        if eid.startswith("plot.edge."):
            continue
        known = room_ids | {o["id"] for o in ctx["model"]["openings"]} \
            | {s["id"] for s in ctx["model"]["stairs"]} \
            | {p2["id"] for p2 in ctx["model"].get("projections", [])} \
            | {s["id"] for s in ctx["model"].get("serviceElements", [])}
        if eid not in known:
            fail(where, "expected.elements names unknown element %r" % eid)

# ---------------------------------------------------------------------------
# 5. index counts
# ---------------------------------------------------------------------------

if index["counts"]["rules"] != len(rule_owner):
    fail("fixtures/rules/index.json", "counts.rules %d != %d rules in packs"
         % (index["counts"]["rules"], len(rule_owner)))
if index["counts"]["fixtures"] != len(on_disk):
    fail("fixtures/rules/index.json", "counts.fixtures %d != %d files on disk"
         % (index["counts"]["fixtures"], len(on_disk)))

# ---------------------------------------------------------------------------

if problems:
    print("FAIL -- %d problem(s):" % len(problems))
    for p in problems:
        print("  " + p)
    sys.exit(1)

print("OK")
print("  packs      : %d" % len(packs))
print("  rules      : %d (all ids unique, all prefixed by their pack)" % len(rule_owner))
print("  fixtures   : %d (every rule has >=1 pass and >=1 fail)" % len(on_disk))
print("  check types: %d in use" % len({r["check"]["type"] for p in packs.values() for r in p["rules"]}))
sys.exit(0)
