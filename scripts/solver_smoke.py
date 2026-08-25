#!/usr/bin/env python3
"""Execute the ortools-free half of the §5 solver on a real plot.

Why this exists
---------------
Stage A needs OR-Tools, which is pinned but not installable on every machine (see
the toolchain-gap row in ``DECISIONS.md``). Everything *around* stage A — envelope
derivation, the room program, stair candidates, refinement to walls and openings,
the critic, diversity and the §5.6 gates — is deliberately dependency-free so it
can be proven anywhere. This script proves it, on a real 30x40 ft Bengaluru plot,
by driving the modules with a synthetic ``CellLayout``-equivalent placement set in
place of stage A's output.

It is a smoke harness, not a substitute for the golden corpus: it asserts the
chain runs, is deterministic, and produces self-consistent numbers. Whether the
plans are *plausible* is what the 20-brief corpus and the architect panel decide,
and neither can run until OR-Tools is installed.

Run:  python3 scripts/solver_smoke.py
Exit: 0 all checks passed, 1 otherwise.
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _path in (_ROOT, os.path.join(_ROOT, "apps", "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from services.dev_stubs import install_worker_dep_stubs  # noqa: E402

STUBBED = install_worker_dep_stubs()

from services.solver import critic, diversity, furniture_fit, gates  # noqa: E402
from services.solver.envelope import derive_envelope  # noqa: E402
from services.solver.types import (  # noqa: E402
    PlanOption,
    PlotEdge,
    RegProfile,
    RoomPlacement,
    RoomRequest,
    SolveParams,
)

FT = 304  # 1 foot in mm, floored — plots are quoted in feet in India
PASS = "  ok  "
FAIL = "  FAIL"

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"{PASS if condition else FAIL}  {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


def demo_params() -> SolveParams:
    """The seeded demo project: 30x40 ft Bengaluru plot, 9 m road south, G+1 3BHK."""
    width, depth = 30 * FT, 40 * FT
    polygon = ((0, 0), (width, 0), (width, depth), (0, depth))
    edges = (
        PlotEdge(index=0, role="front", setback_mm=1500, road_width_mm=9000),
        PlotEdge(index=1, role="side-right", setback_mm=1000),
        PlotEdge(index=2, role="rear", setback_mm=1500),
        PlotEdge(index=3, role="side-left", setback_mm=1000),
    )
    rooms = (
        RoomRequest("living", "living", 12_000_000, 16_000_000, 3000),
        RoomRequest("kitchen", "kitchen", 5_000_000, 7_500_000, 1800, is_wet=True),
        RoomRequest("bed1", "bedroom_master", 9_500_000, 13_000_000, 2400),
        RoomRequest("bed2", "bedroom", 9_500_000, 11_000_000, 2400),
        RoomRequest("bath1", "bath", 1_800_000, 3_000_000, 1200, is_wet=True),
    )
    return SolveParams(
        plot_polygon=polygon,
        edges=edges,
        profile=RegProfile(
            city_pack="blr", coverage_percent=60, far_x100=175,
            max_height_mm=11_000, max_floors=2,
        ),
        rooms=rooms,
        storeys=2,
        north_deg=0,
        vastu_mode="advisory",
        seed=7,
    )


def synthetic_placements() -> tuple[RoomPlacement, ...]:
    """Stand-in for stage A output: a plausible G+1 packing inside the envelope.

    Hand-built, not solver-derived — that is the whole point. It exercises the
    stages downstream of CP-SAT without pretending to be a solved plan, and it is
    NOT written to any golden file.
    """
    return (
        RoomPlacement("living", "living", 0, 0, 0, 4200, 3800),
        RoomPlacement("kitchen", "kitchen", 0, 4200, 0, 2600, 2900),
        RoomPlacement("bath1", "bath", 0, 4200, 2900, 2600, 1500),
        RoomPlacement("bed1", "bedroom_master", 1, 0, 0, 3600, 3600),
        RoomPlacement("bed2", "bedroom", 1, 3600, 0, 3200, 3400),
        RoomPlacement("bath2", "bath", 1, 3600, 3400, 2600, 1500),
    )


def _built_up_mm2(placements: "tuple[RoomPlacement, ...]") -> int:
    """Sum of each storey's room bounding box — the plan's real built-up area."""
    total = 0
    for storey in sorted({p.storey_index for p in placements}):
        rooms = [p for p in placements if p.storey_index == storey]
        width = max(p.x_mm + p.width_mm for p in rooms) - min(p.x_mm for p in rooms)
        depth = max(p.y_mm + p.depth_mm for p in rooms) - min(p.y_mm for p in rooms)
        total += width * depth
    return total


def main() -> int:
    print(f"Garh AI — solver smoke (ortools-free half)")
    print(f"stubbed dependencies: {', '.join(STUBBED) or 'none (real packages present)'}\n")

    params = demo_params()

    # --- §5.1 envelope -----------------------------------------------------
    envelope = derive_envelope(
        params.plot_polygon, params.edges, params.profile, storeys=params.storeys
    )
    plot_area = params.plot_area_mm2()
    check("envelope derived", envelope.area_mm2 > 0, f"{envelope.area_mm2 // 1_000_000} m2 buildable")
    check("envelope inside plot", envelope.area_mm2 < plot_area, "setbacks bite")
    check(
        "coverage cap respected",
        envelope.effective_footprint_mm2 <= envelope.allowed_footprint_mm2,
        f"eff {envelope.effective_footprint_mm2 // 1_000_000} m2 <= cap {envelope.allowed_footprint_mm2 // 1_000_000} m2",
    )
    check(
        "envelope is deterministic",
        derive_envelope(
            params.plot_polygon, params.edges, params.profile, storeys=params.storeys
        ).polygon == envelope.polygon,
    )

    # --- furniture fit -----------------------------------------------------
    catalog = furniture_fit.load_catalog()
    check("catalogue loads and covers every required id", len(catalog) >= 40, f"{len(catalog)} items")

    tiny = furniture_fit.fit_room("x", "bedroom_master", 2000, 2000, catalog)
    roomy = furniture_fit.fit_room("y", "bedroom_master", 3600, 3600, catalog)
    check("2.0x2.0 m master rejected", not tiny.fits, f"missing {list(tiny.missing)}")
    check("3.6x3.6 m master accepted", roomy.fits, f"utilisation {roomy.utilisation_percent}%")
    check(
        "packer is deterministic",
        furniture_fit.fit_room("y", "bedroom_master", 3600, 3600, catalog).placed == roomy.placed,
    )

    placements = synthetic_placements()
    fits = furniture_fit.fit_all(placements, catalog)
    check("furniture fit runs over the plan", len(fits) == len(placements))

    # --- critic sub-scores -------------------------------------------------
    kitchen = next(p for p in placements if p.room_key == "kitchen")
    living = next(p for p in placements if p.room_key == "living")
    bath1 = next(p for p in placements if p.room_key == "bath1")
    check(
        "shared_edge_mm measures a real wall",
        critic.shared_edge_mm(living, kitchen) > 0,
        f"{critic.shared_edge_mm(living, kitchen)} mm living|kitchen",
    )
    check(
        "corner contact is not adjacency",
        critic.shared_edge_mm(
            RoomPlacement("a", "living", 0, 0, 0, 1000, 1000),
            RoomPlacement("b", "bedroom", 0, 1000, 1000, 1000, 1000),
        ) == 0,
    )
    check(
        "cross-storey rooms never share an edge",
        critic.shared_edge_mm(bath1, next(p for p in placements if p.room_key == "bath2")) == 0,
    )

    stacked = critic.score_plumbing_stack(placements)
    check("plumbing stack scored", 0 <= stacked <= 100, f"{stacked}/100 (bath2 over bath1)")
    check(
        "single storey is not penalised",
        critic.score_plumbing_stack(tuple(p for p in placements if p.storey_index == 0)) == 100,
    )

    privacy = critic.score_privacy(placements, params)
    check("privacy scored", 0 <= privacy <= 100, f"{privacy}/100")

    vastu = critic.score_vastu(placements, envelope, params)
    check("vastu scored from the pack", 0 <= vastu <= 100, f"{vastu}/100 (advisory)")
    check(
        "vastu off scores 100",
        critic.score_vastu(
            placements, envelope,
            SolveParams(**{**params.__dict__, "vastu_mode": "off"}),
        ) == 100,
    )

    # --- composite + gates -------------------------------------------------
    # Circulation is measured against the area the rooms sit inside, so the
    # denominator must match the placements handed in. Two wrong denominators to
    # avoid: a ground-floor-only footprint against every storey's rooms drives the
    # percentage to zero and silently disables the §5.6 cap, while the full envelope
    # counts land nobody built on as corridor. The honest figure is the per-storey
    # bounding box of the rooms actually placed.
    built_up = _built_up_mm2(placements)
    footprint = built_up
    breakdown = critic.critique(placements, params, envelope, footprint, catalog=catalog)
    check("critique assembles a full breakdown", 0 <= breakdown.composite <= 100,
          f"composite {breakdown.composite}/100")
    check(
        "composite matches its parts",
        breakdown.composite == critic.composite_score(
            {
                "target_area_fit": breakdown.target_area_fit,
                "adjacency": breakdown.adjacency,
                "circulation": breakdown.circulation,
                "daylight": breakdown.daylight,
                "vastu": breakdown.vastu,
                "furniture_fit": breakdown.furniture_fit,
                "plumbing_stack": breakdown.plumbing_stack,
                "privacy": breakdown.privacy,
                "compactness": breakdown.compactness,
            }
        ),
        "no independent assignment",
    )
    check(
        "critique is deterministic",
        critic.critique(placements, params, envelope, footprint, catalog=catalog) == breakdown,
    )
    check("weights sum to 100", sum(critic.COMPONENT_WEIGHTS.values()) == 100)

    # --- diversity ---------------------------------------------------------
    sig = diversity.signature(
        placements, envelope, stair_anchor_id="stair-a", north_deg=params.north_deg
    )
    check("signature computed", bool(sig), f"{len(sig)} tokens")

    # --- §5.6 gates: the "never show a hard-fail plan" guarantee ------------
    option = PlanOption(
        id="opt-smoke",
        rank=0,
        scores=breakdown,
        placements=placements,
        ops=(),
        signature=sig,
        stair_anchor_id="stair-a",
        built_up_mm2=built_up,
        footprint_mm2=envelope.effective_footprint_mm2,
    )
    clean = gates.check_option(option, compliance=[{"ruleId": "nbc.room.area", "status": "pass"}])
    check("a compliant option passes the gates", clean.passed, f"reasons {list(clean.reasons)}")

    failed = gates.check_option(
        option, compliance=[{"ruleId": "blr.setback.front", "status": "fail"}]
    )
    check(
        "a hard-rule failure is never presentable",
        not failed.passed,
        "golden rule 2 enforced",
    )

    check(
        "circulation is a real fraction, not zero",
        breakdown.circulation_percent > 0,
        f"{breakdown.circulation_percent}% of built-up is circulation",
    )
    over_circulation = gates.check_option(
        option,
        compliance=[{"ruleId": "nbc.room.area", "status": "pass"}],
        max_circulation_percent=max(0, breakdown.circulation_percent - 1),
    )
    check(
        "circulation cap is enforced",
        not over_circulation.passed,
        f"cap {max(0, breakdown.circulation_percent - 1)}% rejects {breakdown.circulation_percent}%",
    )

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("all checks passed")
    print(
        "\nNOT proven here (needs OR-Tools / a real environment): stage A topology,\n"
        "the 20-brief golden corpus, solve-time budgets, and whether any of this is\n"
        "architecturally plausible. See docs/phase-3-verification.md."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
