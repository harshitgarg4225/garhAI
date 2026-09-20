"""How much of Indian residential practice can the solver actually plan?

    python scripts/solver_coverage.py

One configuration solving is not a product. `scripts/first_run_journey.py` proves a
3BHK G+1 on a 30 x 40 ft Bengaluru plot goes end to end; this asks the next question —
what fraction of the plots and briefs an architect actually meets produce a plan at all.
Stage A only, offline: no API, no queue, no rate limit, about ten minutes.

## Read the result carefully

**Setbacks are DERIVED from the city pack, the way production derives them** (since
2026-09-20). Each edge takes the maximum of every ``setback_min`` rule whose ``when``
the plot satisfies — the plot-area band and the road-width band both apply and the
stricter governs, which is exactly what ``garh_rules.areas`` does for the real API.
Before this the script used a fixed 1.5 / 1.5 / 1.0 m and its own docstring warned
that small plots were therefore judged unfairly; the warning was true and the fix is
to measure the real thing rather than to explain the discrepancy away.

**The old caveat was backwards, and that is worth stating.** It warned that fixed
setbacks judged small plots "more harshly than in production". They judged them more
LENIENTLY: BBMP's front setback off a 9 m road is 3 m (the road-width band), and the
script applied 1.5 m — the plot-area band alone, which the road band overrides. A
20 x 30 really keeps 21.1 m² of ground floor, not the 25.2 m² the sweep assumed. Every
row was flattered, not just the small ones, and nobody noticed because the caveat
sounded conservative. Coverage therefore FELL when the derivation landed
(40/60 → 37/60) and the new number is the true one.

## The findings this has produced

**2026-08-31 (26/60).** Two shapes in the failures: every 20 x 30 ft row, and 2BHK at
G+1/G+2 on every plot size including 50 x 80 ft. The second had no setback explanation
— a sparse ground floor (living, kitchen, passage: about 20 m²) was as infeasible as an
overfull one.

**2026-09-01 (42/60).** The sparse-storey cause was found and fixed: stage A capped
circulation per storey at the §5.6 gate's 18%, but the gate measures the whole
building, and a staircase does not shrink with the floor it sits on. See
``docs/trial-readiness.md``.

**2026-09-20 (40/60 on the old assumption, 37/60 measured).** Re-run after the parking
pass landed: 40/60, identical to 2026-09-01, because bays are placed in stage B and
this sweep only exercises the stage-A packer. Then re-measured with pack-derived
setbacks: **37 of 60**, and the three rows that changed are all 30 x 40 single-storey
(G+0), whose front setback went 1.5 m → 3.0 m.

Where the 23 failures sit, and which are the product's fault:

* **12 are the whole 20 x 30 column.** Under the seeded BBMP numbers a 20 x 30 on a 9 m
  road keeps 4.1 x 5.1 m — 21.1 m² of ground floor — against the ~28 m² the smallest
  two-bedroom programme needs once circulation is allowed for. Stage A proves that
  arithmetically (``services.solver.diagnose``) and says so in a sentence the architect
  reads. This is the PACK's number, not the solver's, and the answer is the empanelled
  review of small-plot setbacks that is already a launch gate.
* **8 are single-storey (G+0) briefs** that want three or four bedrooms plus utility and
  pooja on one plate. A 3BHK G+0 on a 30 x 40 is not something an Indian architect
  usually draws; where it is genuinely wanted, the plot is bigger, and 40 x 60 and
  50 x 80 solve every G+0 row.
* **3 are 4BHK at G+1** on 40 x 60 and 50 x 80 — the sparse-upper-storey shape, the same
  family as the 2026-08-31 finding. Worth another look; not attempted here.
"""

import itertools
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [_ROOT, os.path.join(_ROOT, "apps", "api")]
from services.dev_stubs import install_worker_dep_stubs  # noqa: E402

install_worker_dep_stubs()
from services.llm.room_defaults import size_rooms  # noqa: E402
from services.solver import stage_a as sa  # noqa: E402
from services.solver import stairs as stairs_mod  # noqa: E402
from services.solver.envelope import derive_envelope  # noqa: E402
from services.solver.handler import _parse_rooms  # noqa: E402
from services.solver.stages import grid_envelope  # noqa: E402
from services.solver.types import PlotEdge, RegProfile, SolveParams  # noqa: E402

FT = 304.8
# Plot sizes an Indian architect actually meets, in feet.
PLOTS = {
    "20x30": (20, 30),
    "30x40": (30, 40),
    "30x50": (30, 50),
    "40x60": (40, 60),
    "50x80": (50, 80),
}
PACKS = {
    "blr": RegProfile(
        city_pack="blr", coverage_percent=60, far_x100=175, max_height_mm=11000, max_floors=3
    ),
    "ncr": RegProfile(
        city_pack="ncr", coverage_percent=60, far_x100=200, max_height_mm=11000, max_floors=3
    ),
    "hyd": RegProfile(
        city_pack="hyd", coverage_percent=60, far_x100=175, max_height_mm=11000, max_floors=3
    ),
}


def brief(beds, baths, extras=()):
    out = [{"type": "living_dining", "count": 1}, {"type": "kitchen", "count": 1}]
    out += [{"type": t, "count": 1} for t in extras]
    out += [{"type": "bedroom_master", "count": 1}]
    if beds > 1:
        out += [{"type": "bedroom", "count": beds - 1}]
    out += [{"type": "bath_wc", "count": baths}]
    return out


BRIEFS = {
    "2BHK": brief(2, 2),
    "3BHK": brief(3, 2, ("utility", "pooja")),
    "3BHK+study": brief(3, 3, ("utility", "pooja", "study")),
    "4BHK": brief(4, 3, ("utility", "pooja")),
}
STOREYS = (1, 2, 3)


#: Road on the front edge, in mm. The setback bands key on it, so it has to be stated
#: rather than assumed: 9 m is the commonest residential street in the seeded packs.
ROAD_WIDTH_MM = 9000

_PACK_CACHE = {}


def _pack_rules(pack_key):
    """Every ``setback_min`` rule in the pack and its parents, as raw JSON."""
    if pack_key not in _PACK_CACHE:
        rules = []
        for name in ("nbc-core", pack_key):
            path = os.path.join(_ROOT, "rulepacks", "%s.json" % name)
            with open(path, encoding="utf-8") as handle:
                rules.extend(json.load(handle).get("rules") or [])
        _PACK_CACHE[pack_key] = [
            r for r in rules if r.get("check", {}).get("type") == "setback_min"
        ]
    return _PACK_CACHE[pack_key]


def _when_matches(rule, *, area_sqm, road_mm):
    """The subset of ``when`` these rules actually use: zone, use, plot area, road."""
    when = rule.get("when") or {}
    for field, ops in when.items():
        if field == "zoneCategory":
            value = "residential"
        elif field == "buildingUse":
            value = "dwelling-single"
        elif field == "plotAreaSqm":
            value = area_sqm
        elif field == "roadWidthMm":
            value = road_mm
        else:
            return False  # an unknown selector: refuse rather than guess it matches
        for op, operand in ops.items():
            if op == "eq" and value != operand:
                return False
            if op == "in" and value not in operand:
                return False
            if op == "lt" and not value < operand:
                return False
            if op == "lte" and not value <= operand:
                return False
            if op == "gt" and not value > operand:
                return False
            if op == "gte" and not value >= operand:
                return False
    return True


def derived_setbacks(pack_key, w_mm, h_mm, road_mm=ROAD_WIDTH_MM):
    """``{role: mm}`` the way production derives it: the strictest applicable rule wins.

    ``garh_rules.areas`` takes the MAXIMUM over every matching ``setback_min`` rule for
    an edge, because the city tables are indexed by plot size AND road width and both
    apply. Same arithmetic here, straight off the pack JSON, so the sweep measures the
    envelope the product would really build in.
    """
    area_sqm = (w_mm * h_mm) // 1_000_000
    out = {"front": 0, "rear": 0, "side": 0}
    for rule in _pack_rules(pack_key):
        if not _when_matches(rule, area_sqm=area_sqm, road_mm=road_mm):
            continue
        # The pack's own selector vocabulary: "sides" covers both side edges, "all"
        # every edge (fixtures/rules/_tools/generate_fixtures.edges_covered).
        edge = str(rule["check"].get("edge") or "all")
        value = int(rule["check"]["valueMm"])
        if edge == "sides":
            roles = ("side",)
        elif edge == "all":
            roles = ("front", "rear", "side")
        else:
            roles = (edge,)
        for role in roles:
            if role in out:
                out[role] = max(out[role], value)
    return out


def edges(w, h, pack_key="blr"):
    setbacks = derived_setbacks(pack_key, w, h)
    return (
        PlotEdge(index=0, role="front", setback_mm=setbacks["front"], road_width_mm=ROAD_WIDTH_MM),
        PlotEdge(index=1, role="side", setback_mm=setbacks["side"]),
        PlotEdge(index=2, role="rear", setback_mm=setbacks["rear"]),
        PlotEdge(index=3, role="side", setback_mm=setbacks["side"]),
    )


def run(plot_key, brief_key, pack_key, storeys):
    wf, hf = PLOTS[plot_key]
    w, h = int(wf * FT), int(hf * FT)
    poly = ((0, 0), (w, 0), (w, h), (0, h))
    sized, _ = size_rooms(BRIEFS[brief_key])
    params = SolveParams(
        plot_polygon=poly,
        edges=edges(w, h, pack_key),
        profile=PACKS[pack_key],
        rooms=_parse_rooms(sized),
        storeys=storeys,
        seed=7,
    )
    try:
        env = derive_envelope(poly, params.edges, params.profile, storeys=storeys)
        grid = grid_envelope(env)
        anchors = list(stairs_mod.enumerate_stair_candidates(env, params, limit=3))
        for a in anchors:
            if (
                sa.stage_a_topology(
                    grid,
                    params,
                    a,
                    profile=None,
                    relaxed=False,
                    time_budget_seconds=8,
                    num_search_workers=4,
                )
                is not None
            ):
                return True
        return False
    except Exception as exc:
        return "ERR:%s" % type(exc).__name__


if __name__ == "__main__":
    rows = []
    for plot, br, pack, st in itertools.product(PLOTS, BRIEFS, ("blr",), STOREYS):
        rows.append((plot, br, pack, st, run(plot, br, pack, st)))
    ok = sum(1 for r in rows if r[4] is True)
    print(
        "\n=== SOLVER MATRIX (blr) — %d of %d configurations produce a plan ===\n" % (ok, len(rows))
    )
    print("  setbacks derived from the pack, %d mm road on the front edge:" % ROAD_WIDTH_MM)
    for plot_key in PLOTS:
        wf, hf = PLOTS[plot_key]
        derived = derived_setbacks("blr", int(wf * FT), int(hf * FT))
        print(
            "    %-8s front %d  rear %d  side %d"
            % (plot_key, derived["front"], derived["rear"], derived["side"])
        )
    print()
    print("  %-8s %-11s %-3s  %s" % ("plot", "brief", "st", "result"))
    for plot, br, _pack, st, res in rows:
        mark = "ok " if res is True else ("   " if res is False else "ERR")
        print("  %-8s %-11s G+%d  %s" % (plot, br, st - 1, mark if res is not True else "ok"))
