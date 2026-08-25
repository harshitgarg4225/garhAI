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
| G+1 3BHK brief | **seeded** (as ops) — it *is* golden brief 01 |
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

import json
import os
from dataclasses import dataclass
from typing import Any, Sequence

from garh_api.logging import get_logger
from garh_api.seed.catalog import SeedDataError, fixtures_dir

_log = get_logger(__name__)

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

#: The golden brief this project's brief is taken from — one source of truth for
#: "30×40 ft Bengaluru, G+1, 3BHK" (see fixtures/briefs/README.md).
DEMO_BRIEF_FIXTURE_ID = "brief-01-blr-30x40-rect-g1"

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
# The brief
# ---------------------------------------------------------------------------

#: Compiled-in fallback, used only when ``fixtures/`` is not in the image. Kept small and
#: marked, rather than a second full copy of the golden brief that could silently diverge.
_FALLBACK_BRIEF: dict[str, Any] = {
    "bedrooms": 3,
    "bathrooms": 2,
    "floorsAboveGround": 1,
    "hasStilt": False,
    "hasBasement": False,
    "carParking": 1,
    "twoWheelerParking": 1,
    "poojaRoom": True,
    "servantRoom": False,
    "study": False,
    "lift": False,
    "vastuMode": "advisory",
    "plotFacing": "east",
    "budgetInr": 6_500_000,
    "styleId": "contemporary",
    "familySize": 6,
    "source": "seed-fallback",
}


@dataclass(frozen=True)
class DemoBrief:
    """The demo project's brief, plus where it was read from."""

    data: dict[str, Any]
    vastu_mode: str
    completeness: int
    source: str

    @property
    def from_fixture(self) -> bool:
        return self.source.endswith(".json")


def _brief_fixture_path() -> str:
    return os.path.join(fixtures_dir(), "briefs", "%s.json" % DEMO_BRIEF_FIXTURE_ID)


def load_demo_brief() -> DemoBrief:
    """Load golden brief 01 as the demo project's brief.

    The demo project and golden brief 01 describe the *same house* — 30×40 ft Bengaluru,
    G+1, 3BHK — so they must not be two hand-maintained copies. Reading the fixture also
    means the seed exercises the corpus: a malformed brief file fails the seed instead of
    waiting for Phase 3.
    """
    path = _brief_fixture_path()
    if not os.path.isfile(path):
        _log.warning(
            "seed.brief_fixture_absent",
            path=path,
            consequence="using the small compiled-in fallback brief",
        )
        return DemoBrief(
            data=dict(_FALLBACK_BRIEF),
            vastu_mode="advisory",
            completeness=60,
            source="compiled-in fallback",
        )
    try:
        with open(path, encoding="utf-8") as handle:
            fixture = json.load(handle)
    except (OSError, ValueError) as exc:
        raise SeedDataError("Could not read the demo brief fixture %s: %s" % (path, exc)) from exc
    data = fixture.get("data")
    if not isinstance(data, dict) or not data:
        raise SeedDataError("%s has no 'data' object." % path)
    vastu = str(fixture.get("vastuMode") or data.get("vastuMode") or "off")
    completeness = fixture.get("completeness")
    if not isinstance(completeness, int) or not 0 <= completeness <= 100:
        raise SeedDataError("%s: completeness must be an integer 0–100." % path)
    _assert_integral(data, path=path)
    return DemoBrief(
        data=data,
        vastu_mode=vastu,
        completeness=completeness,
        source=os.path.basename(path),
    )


def _assert_integral(value: Any, *, path: str, where: str = "data") -> None:
    """No floats anywhere in the brief.

    ``brief.update`` validates this too (``OP_FIELD_NOT_INT``), but failing here names the
    file and the JSON path instead of failing inside a fold three calls later.
    """
    if value is None or isinstance(value, (str, bool)):
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
    "DEMO_BRIEF_FIXTURE_ID",
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
    "demo_op_log",
    "demo_plot_polygon",
    "demo_render_requests",
    "demo_sheet_kinds",
    "demo_storey_ids",
    "facade_ops",
    "load_demo_brief",
    "solved_plan_ops",
]
