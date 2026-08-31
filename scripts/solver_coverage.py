"""How much of Indian residential practice can the solver actually plan?

    python scripts/solver_coverage.py

One configuration solving is not a product. `scripts/first_run_journey.py` proves a
3BHK G+1 on a 30 x 40 ft Bengaluru plot goes end to end; this asks the next question —
what fraction of the plots and briefs an architect actually meets produce a plan at all.
Stage A only, offline: no API, no queue, no rate limit, about ten minutes.

## Read the result carefully

**Setbacks here are fixed at 1.5 / 1.5 / 1.0 m, not derived from the rule pack.** The
real API asks the rules engine for a per-edge setback that depends on plot size, and
small plots get smaller setbacks than these. So the smallest plots are judged more
harshly here than in production and their failures are NOT evidence of a defect.

What the fixed setbacks cannot explain — and what makes this worth running — is a
failure on a large plot, where a metre of setback either way changes nothing.

## The finding this was written for (2026-08-31)

26 of 60 configurations produced a plan. Two shapes in the failures:

* every 20 x 30 ft row failed, which the fixed setbacks above may fully explain;
* **2BHK at G+1 and G+2 failed on every plot size, including 50 x 80 ft.** A two-bedroom
  house on a 371 m² plot is not a hard problem, and no setback assumption explains it.
  Its ground floor gets the living room, the kitchen and a passage — about 20 m² of
  rooms — and Stage A cannot lay that floor out. The seed's own comment predicts it:
  "a sparse floor is as infeasible as an overfull one".

That is unfixed. Moving a bedroom downstairs — the rescue that fixed the overfull case —
was tried and did not help, so the cause is inside the CP-SAT model rather than the
storey assignment, and the honest record is this script plus that sentence.
"""

import itertools
import sys

sys.path[:0] = ["/home/user/garhAI", "/home/user/garhAI/apps/api"]
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


def edges(w, h):
    return (
        PlotEdge(index=0, role="front", setback_mm=1500),
        PlotEdge(index=1, role="side", setback_mm=1000),
        PlotEdge(index=2, role="rear", setback_mm=1500),
        PlotEdge(index=3, role="side", setback_mm=1000),
    )


def run(plot_key, brief_key, pack_key, storeys):
    wf, hf = PLOTS[plot_key]
    w, h = int(wf * FT), int(hf * FT)
    poly = ((0, 0), (w, 0), (w, h), (0, h))
    sized, _ = size_rooms(BRIEFS[brief_key])
    params = SolveParams(
        plot_polygon=poly,
        edges=edges(w, h),
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
    print("  %-8s %-11s %-3s  %s" % ("plot", "brief", "st", "result"))
    for plot, br, _pack, st, res in rows:
        mark = "ok " if res is True else ("   " if res is False else "ERR")
        print("  %-8s %-11s G+%d  %s" % (plot, br, st - 1, mark if res is not True else "ok"))
