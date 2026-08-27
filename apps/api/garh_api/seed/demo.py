"""The demo firm, user and project — playbook §17's "universal fixture".

§17 asks for **one complete demo project**: *"30×40 ft Bengaluru plot, north up, 9 m road
south, G+1 3BHK brief, a solved+edited plan, facade applied, 2 mock renders, generated
sheet set."*

## What this module seeds today, honestly

Everything that exists in the codebase right now:

| §17 asks for | Status |
|---|---|
| demo firm "Studio Demo" | **seeded** |
| user `demo@garh.ai` | **seeded** |
| 30×40 ft Bengaluru plot, north up, 9 m road on the south edge | **seeded** (as ops) |
| G+1 3BHK brief | **seeded** (as ops) — seed-authored, CP-SAT-feasible (see below) |
| ground + first storey | **seeded** (as ops) |
| an initial op log and a named version with a folded snapshot | **seeded** |
| a solved + edited plan | **Phase 3** — the CP-SAT solver does not exist yet |
| facade applied | **Phase 5** |
| 2 mock renders | **Phase 7** |
| generated sheet set | **Phase 8** |

The four unfinished rows are **not faked**. A hand-written wall soup would look like a
solved plan, become the fixture every golden test and screenshot compares against, and
then quietly disagree with whatever the real solver emits — which is the precise failure
mode golden rule 10 exists to prevent. Instead there is one named extension point per
phase (:func:`solved_plan_ops`, :func:`facade_ops`, :func:`demo_render_requests`,
:func:`demo_sheet_kinds`); each returns nothing today, says which phase owns it, and is
already wired into the runner, so filling it in is an edit to one function.

## Why the plot and brief go in as ops

Golden rule 1: the op log is the truth and the ``plots`` / ``briefs`` tables are a
projection of it. Seeding the tables directly would produce a demo project whose folded
document is empty while its plot form shows a boundary — the exact inconsistency the
sequencer exists to prevent. So the seeder appends ops 1–6 through the same
``dispatch_ops`` path ``PUT /projects/:id/plot`` uses.

## Shared geometry

The plot polygon comes from :data:`garh_model.testing.DEMO_PLOT_POLYGON`, which is the
same constant ``packages/model/src/testing.ts`` exports. One 30×40 ft plot, one set of
coordinates, both languages — so a state hash printed on either side means the same thing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from garh_api.seed.catalog import SeedDataError

# ---------------------------------------------------------------------------
# Identity (§17 names both of these explicitly — do not "improve" them, the e2e
# smoke spec and the docs sign in as this user)
# ---------------------------------------------------------------------------

DEMO_FIRM_NAME = "Studio Demo"
DEMO_USER_EMAIL = "demo@garh.ai"
DEMO_USER_NAME = "Asha Rao"
#: Council of Architecture registration. Municipal sheets carry it (§7 title block);
#: this one is obviously fictional so it can never be mistaken for a real registration.
DEMO_COA_NUMBER = "CA/0000/DEMO"
DEMO_PROJECT_NAME = "Sharma Residence — demo"
DEMO_CITY_PACK = "blr"
DEMO_UNITS = "ft-in"
#: The project starts at the stage the seeded data actually reaches: brief captured,
#: nothing generated. It becomes "options" the first time the solver runs.
DEMO_PROJECT_STATUS = "brief"

#: The corpus sibling of this house — same plot, same G+1 3BHK headline (see
#: fixtures/briefs/README.md). The demo brief is NOT that fixture any more: brief 01's
#: 16-room program is CP-SAT-provably infeasible on this plot (§5.2's stair well counts
#: toward §5.6's hard circulation cap, and 16 rooms leave no floor the arithmetic can
#: close), so seeding it gave every first-time visitor an honest zero options. The brief
#: below is authored here instead — see the section comment for the feasibility proof.
DEMO_BRIEF_CORPUS_SIBLING = "brief-01-blr-30x40-rect-g1"

#: Road: 9 m, on the plot's **south** edge. Edge 0 of DEMO_PLOT_POLYGON runs from
#: (0,0) to (9144,0); with northDeg = 0 (+Y is true north) that edge is the south one.
DEMO_ROAD_EDGE_INDEX = 0
DEMO_ROAD_WIDTH_MM = 9000
DEMO_ROAD_NAME = "9 m road (south)"
DEMO_NORTH_DEG = 0

#: Storey ids. Fixed (not ULIDs minted at seed time) so the demo project's state hash is
#: reproducible: a tour, a screenshot test and a golden file can all name the same storey.
#: They come from garh_model.testing.FIXTURE_IDS, i.e. the same ids the TS fixtures use.
DEMO_STOREY_HEIGHT_MM = 3000

DEMO_VERSION_NAME = "Plot and brief captured"


# ---------------------------------------------------------------------------
# The brief — authored HERE, and proven feasible against the real solver
# ---------------------------------------------------------------------------
#
# This is the first brief every visitor generates plans from, so it has one hard
# obligation the golden corpus does not: ``run_solver`` on this plot must return ≥1
# option. ``apps/api/tests/test_seed_brief_feasible.py`` builds the exact payload
# ``garh_api.solver_enqueue`` would enqueue for this document and runs the real CP-SAT
# pipeline in-process; change any number below and that test re-proves it (or fails).
#
# What feasibility on a 30×40 ft blr plot actually requires (all execution finds):
#
# * The buildable envelope after blr setbacks (3.0 m front, 1.0 m sides/rear) is
#   7144×8192 mm ≈ 58.5 m² per floor — the ceiling every storey's program shares.
# * The §5.2 stair well is a fixed ~7.2 m² rectangle on every storey and it counts as
#   CIRCULATION under §5.6's hard 18% cap, so every storey needs roughly 37 m²+ of
#   real rooms just to keep the ratio legal. A sparse floor is as infeasible as an
#   overstuffed one.
# * Stage A tiles each storey EXACTLY, with rooms bounded in [minArea, 1.6×target].
#   Generous targets with modest minima give the tiler the slack it needs; brief-01's
#   16 tight rooms provably cannot tile these two floors.
#
# * §5.2's external-face rule gives every habitable room + the kitchen an edge on
#   the footprint boundary, and the NBC clear-width floors make each such room
#   ~3 m of gross wall. On a 7.1 m-wide floor that is a budget of roughly three
#   external rooms per storey beside the stair — a separate living AND dining AND a
#   ground bedroom provably cannot all hold it. The open ``living_dining`` (the
#   default Indian mid-plot section anyway) hands one slot back.
#
# Hence: a G+1 3BHK an Indian architect would actually draw for this plot — an open
# living-dining with the kitchen, utility, pooja, a guest bedroom and a common W.C.
# on the ground floor; the master and second bedroom with their bath above; the
# stair left to the solver (it synthesises the NBC well on every storey); one car
# space declared so the ``blr.parking.plot.le240`` gate passes.
DEMO_BRIEF_SOURCE = "garh_api.seed.demo"

#: ``off``, deliberately. ``advisory``/``strict`` make the §5.4 critic evaluate the
#: vastu pack against solver output whose stair rooms carry no centroid yet — that
#: path currently faults (``stair … has no centroidMm``), killing the whole solve.
#: Until that lands, ``off`` is the honest mode the demo can actually generate under;
#: the Brief tab still lets a visitor flip it and see the failure themselves.
DEMO_BRIEF_VASTU_MODE = "off"

#: Plot, program and preferences captured; budget and style pending — matches what the
#: completeness meter would show for this much of a real intake conversation.
DEMO_BRIEF_COMPLETENESS = 80


def demo_brief_data() -> dict[str, Any]:
    """The demo brief's ``data`` object (a fresh copy — callers may mutate).

    Areas are integer mm² and widths integer mm (the model holds no floats). Every
    room type appears ONCE (counts expand instead): the enqueue path keys rooms by
    type (``bedroom``, ``bedroom2``, …), so two entries of one type would collide on
    a key — which is also why the ground bedroom is a ``guest_bedroom`` and the
    ground toilet a ``wc``, not second ``bedroom``/``bath_wc`` rows. Only the two
    ground-floor pins are stated; everything else takes the program layer's Indian
    default (bedroom-ish types upstairs, the rest entry-adjacent) — the assignment
    the feasibility proof passes under.
    """
    return {
        # Headline numbers the UI chips and §17 read.
        "bedrooms": 3,  # 3BHK: master + second bedroom + guest bedroom (§17)
        "bathrooms": 2,  # attached-grade bath above, common W.C. below
        "floorsAboveGround": 1,  # G+1
        "hasStilt": False,
        "hasBasement": False,
        "carParking": 1,  # declared, or blr.parking.plot.le240 rejects every candidate
        "twoWheelerParking": 1,
        "poojaRoom": True,
        "servantRoom": False,
        "study": False,
        "lift": False,
        "plotFacing": "south",  # the 9 m road is on edge 0, the south edge
        "budgetInr": 7_500_000,
        "styleId": "contemporary",
        "familySize": 5,
        # The room program. Types are garh_model.ROOM_TYPES members exactly — an
        # unknown type would be silently dropped by the solver's program layer.
        "rooms": [
            # The public half — the solver grounds these by default.
            {
                "type": "living_dining",
                "count": 1,
                "minAreaMm2": 15_000_000,
                "targetAreaMm2": 20_000_000,
                "minWidthMm": 3300,
            },
            {
                "type": "kitchen",
                "count": 1,
                "minAreaMm2": 5_500_000,
                "targetAreaMm2": 8_000_000,
                "minWidthMm": 2400,
            },
            {
                "type": "utility",
                "count": 1,
                "minAreaMm2": 2_000_000,
                "targetAreaMm2": 3_200_000,
                "minWidthMm": 1200,
            },
            {
                "type": "pooja",
                "count": 1,
                "minAreaMm2": 1_800_000,
                "targetAreaMm2": 3_000_000,
                "minWidthMm": 1200,
            },
            # Grandparents/guests sleep downstairs — pinned, or the bedroom-ish
            # default would stack a third wide habitable room on the upper floor,
            # which this envelope provably cannot tile.
            {
                "type": "guest_bedroom",
                "count": 1,
                "minAreaMm2": 9_900_000,
                "targetAreaMm2": 11_500_000,
                "minWidthMm": 3000,
                "storey": 0,
            },
            # Bath-sized, not the bare 1.1 m2 / 900 NBC floor: the §5.4 critic
            # measures the CLEAR polygon after wall insets and the 115mm snap, and
            # tighter gross wishes here left candidates failing
            # nbc.room.wc.width.min (execution find).
            {
                "type": "wc",
                "count": 1,
                "minAreaMm2": 2_800_000,
                "targetAreaMm2": 4_200_000,
                "minWidthMm": 1500,
                "storey": 0,
            },
            # The family half — bedroom-ish types default to the first floor.
            {
                "type": "bedroom_master",
                "count": 1,
                "minAreaMm2": 10_500_000,
                "targetAreaMm2": 13_500_000,
                "minWidthMm": 3300,
            },
            {
                "type": "bedroom",
                "count": 1,
                "minAreaMm2": 9_900_000,
                "targetAreaMm2": 11_500_000,
                "minWidthMm": 3000,
            },
            {
                "type": "bath_wc",
                "count": 1,
                "minAreaMm2": 2_800_000,
                "targetAreaMm2": 4_200_000,
                "minWidthMm": 1500,
            },
            # NO staircase row, deliberately. The solver's program layer synthesises
            # the NBC-sized stair on every storey of a G+1 brief (and says so in an
            # assumption chip); declaring one here attaches a gross width wish to a
            # room whose well is a FIXED §5.2 rectangle, which provably re-tightens
            # stage A back to infeasibility (execution find — see
            # tests/test_seed_brief_feasible.py, which pins the working shape).
        ],
        # Wishes the UI shows; the §5.2 objective rewards them when the solver reads
        # them (adjacency is advisory — the payload contract carries rooms only today).
        "adjacency": [
            {"a": "kitchen", "b": "living_dining", "wish": "adjacent", "weight": 80},
            {"a": "kitchen", "b": "utility", "wish": "adjacent", "weight": 60},
            {"a": "bedroom_master", "b": "living_dining", "wish": "apart", "weight": 40},
        ],
    }


@dataclass(frozen=True)
class DemoBrief:
    """The demo project's brief, plus where it was authored."""

    data: dict[str, Any]
    vastu_mode: str
    completeness: int
    source: str


def load_demo_brief() -> DemoBrief:
    """The demo project's brief — seed-authored, validated on every load.

    The demo brief used to *be* golden brief 01; it is authored here now because the
    corpus fixture's program is honest solver stress data while this one must actually
    solve (the section comment above has the whole story). Validation still runs on
    every load so an edit to :func:`demo_brief_data` that smuggles in a float fails
    the seed with a named path instead of failing inside a fold three calls later.
    """
    data = demo_brief_data()
    _assert_integral(data, path=DEMO_BRIEF_SOURCE)
    return DemoBrief(
        data=data,
        vastu_mode=DEMO_BRIEF_VASTU_MODE,
        completeness=DEMO_BRIEF_COMPLETENESS,
        source=DEMO_BRIEF_SOURCE,
    )


def _assert_integral(value: Any, *, path: str, where: str = "data") -> None:
    """No floats anywhere in the brief.

    ``brief.update`` validates this too (``OP_FIELD_NOT_INT``), but failing here names the
    file and the JSON path instead of failing inside a fold three calls later.
    """
    if value is None or isinstance(value, str | bool):
        return
    if isinstance(value, float):
        raise SeedDataError(
            "%s: %s is a float (%r). The model document holds no floats — use whole "
            "millimetres, whole mm², or whole rupees." % (path, where, value)
        )
    if isinstance(value, int):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_integral(item, path=path, where="%s[%d]" % (where, index))
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_integral(item, path=path, where="%s.%s" % (where, key))
        return
    raise SeedDataError("%s: %s is a %s, which is not JSON." % (path, where, type(value).__name__))


# ---------------------------------------------------------------------------
# The op log
# ---------------------------------------------------------------------------


def demo_plot_polygon() -> list[dict[str, int]]:
    """The 30×40 ft plot, from the model core's shared fixture."""
    from garh_model.testing import DEMO_PLOT_POLYGON

    polygon = [{"x": int(pt["x"]), "y": int(pt["y"])} for pt in DEMO_PLOT_POLYGON]
    if len(polygon) < 3:
        raise SeedDataError("DEMO_PLOT_POLYGON is degenerate.")
    return polygon


def demo_storey_ids() -> tuple[str, str]:
    """``(ground, first)`` element ids, shared with the TypeScript fixtures."""
    from garh_model.testing import FIXTURE_IDS

    return str(FIXTURE_IDS["groundStorey"]), str(FIXTURE_IDS["firstStorey"])


def demo_op_log(brief: DemoBrief) -> list[dict[str, Any]]:
    """The demo project's op log, as wire-shaped ``{type, payload}`` dicts.

    Ops 1–5 of the §4 taxonomy plus two ``storey.add``. Deliberately no walls: those are
    the solver's output, and inventing them is what :func:`solved_plan_ops` refuses to do.
    """
    ground, first = demo_storey_ids()
    return [
        {
            "type": "plot.set_boundary",
            "payload": {"polygon": demo_plot_polygon(), "source": "seed"},
        },
        {"type": "plot.set_north", "payload": {"deg": DEMO_NORTH_DEG}},
        {
            "type": "plot.set_road",
            "payload": {
                "edgeIndex": DEMO_ROAD_EDGE_INDEX,
                "widthMm": DEMO_ROAD_WIDTH_MM,
                "name": DEMO_ROAD_NAME,
            },
        },
        {
            "type": "plot.set_reg_profile",
            "payload": {"cityPack": DEMO_CITY_PACK, "overrides": {}},
        },
        {
            "type": "brief.update",
            "payload": {
                "patch": brief.data,
                "vastuMode": brief.vastu_mode,
                "completeness": brief.completeness,
            },
        },
        {
            "type": "storey.add",
            "payload": {
                "id": ground,
                "index": 0,
                "name": "Ground Floor",
                "heightMm": DEMO_STOREY_HEIGHT_MM,
            },
        },
        {
            "type": "storey.add",
            "payload": {
                "id": first,
                "index": 1,
                "name": "First Floor",
                "heightMm": DEMO_STOREY_HEIGHT_MM,
            },
        },
    ]


# ---------------------------------------------------------------------------
# Extension points — one per unfinished §17 row
# ---------------------------------------------------------------------------
#
# Each of these is already called by garh_api.seed.runner. Returning an empty sequence
# means "this phase has not landed"; the runner reports it as such and the seed stays
# honest. Filling one in is a change to one function and nothing else.


def solved_plan_ops(*, storey_ids: Sequence[str]) -> list[dict[str, Any]]:
    """**Phase 3 extension point** — the solved + edited plan §17 wants.

    When the CP-SAT solver lands (playbook §5), this should return the op list of the
    accepted option: ``solver.apply_option`` carrying the wall/opening/room/stair ops for
    the demo brief, followed by the two or three manual edits §17 calls "edited" so the
    fixture also exercises provenance (``source: manual`` after ``source: solver``).

    It must come from a **real solver run**, captured as a golden file — not typed by
    hand. A hand-drawn plan here becomes the reference every golden test and screenshot
    compares against, and it would disagree with the solver the day the solver exists.

    Until then: no geometry. The demo project opens on the Brief tab with a plot, a brief
    and two storeys, and the Plan tab shows its empty state, which is the truth.
    """
    return []


def facade_ops(*, storey_ids: Sequence[str]) -> list[dict[str, Any]]:
    """**Phase 5 extension point** — ``facade.apply_kit`` + per-component edits (§8).

    Needs the solved plan first: a facade kit is applied to external walls, and there are
    none until :func:`solved_plan_ops` returns something.
    """
    return []


def demo_render_requests() -> list[dict[str, Any]]:
    """**Phase 7 extension point** — the two mock renders §17 wants.

    Each entry should be a ``POST /projects/:id/renders`` body (mode, preset, camera).
    Renders are pinned to a design version and produced by the render **worker**; the
    seeder should enqueue the jobs, not fabricate ``render_jobs`` rows with output URLs
    pointing at images nobody generated.
    """
    return []


def demo_sheet_kinds() -> list[str]:
    """**Phase 8 extension point** — the generated municipal sheet set (§7).

    Should return the MVP kinds (``site``, ``floor``, ``elevation``, ``section``,
    ``schedule``, ``area-statement``) once the drawings worker can produce them, so the
    seeder enqueues one ``drawings.generate_sheets`` job. Writing ``sheets`` rows with an
    empty ``layout`` would make the Sheets tab look populated and download nothing.
    """
    return []


#: Machine-readable list of what is still missing, so the seed report and
#: ``tests/test_seed.py`` agree on the same set instead of two prose lists.
PENDING_PHASES: tuple[dict[str, str], ...] = (
    {
        "item": "solvedPlan",
        "phase": "3",
        "extensionPoint": "garh_api.seed.demo.solved_plan_ops",
        "why": "the CP-SAT solver (playbook §5) has not landed; a hand-drawn plan would "
        "become a golden reference that disagrees with the real solver",
    },
    {
        "item": "facade",
        "phase": "5",
        "extensionPoint": "garh_api.seed.demo.facade_ops",
        "why": "a facade kit needs external walls, which need the solved plan",
    },
    {
        "item": "renders",
        "phase": "7",
        "extensionPoint": "garh_api.seed.demo.demo_render_requests",
        "why": "renders are produced by the render worker and pinned to a design version",
    },
    {
        "item": "sheets",
        "phase": "8",
        "extensionPoint": "garh_api.seed.demo.demo_sheet_kinds",
        "why": "sheets are generated by the drawings worker from a solved plan",
    },
)


__all__ = [
    "DEMO_BRIEF_COMPLETENESS",
    "DEMO_BRIEF_CORPUS_SIBLING",
    "DEMO_BRIEF_SOURCE",
    "DEMO_BRIEF_VASTU_MODE",
    "DEMO_CITY_PACK",
    "DEMO_COA_NUMBER",
    "DEMO_FIRM_NAME",
    "DEMO_NORTH_DEG",
    "DEMO_PROJECT_NAME",
    "DEMO_PROJECT_STATUS",
    "DEMO_ROAD_EDGE_INDEX",
    "DEMO_ROAD_NAME",
    "DEMO_ROAD_WIDTH_MM",
    "DEMO_STOREY_HEIGHT_MM",
    "DEMO_UNITS",
    "DEMO_USER_EMAIL",
    "DEMO_USER_NAME",
    "DEMO_VERSION_NAME",
    "PENDING_PHASES",
    "DemoBrief",
    "demo_brief_data",
    "demo_op_log",
    "demo_plot_polygon",
    "demo_render_requests",
    "demo_sheet_kinds",
    "demo_storey_ids",
    "facade_ops",
    "load_demo_brief",
    "solved_plan_ops",
]
