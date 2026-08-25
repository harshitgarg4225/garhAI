from __future__ import annotations

"""§14's budget: a full compliance run on a house in under 100 ms.

    | Compliance run | <100ms model, <=500ms debounce | pytest timing |

The number is a feature, not a nicety: the engine runs debounced on every canvas
edit (§15's live chip strip) and again inside the solver critic for every
candidate plan (§5.4), where thousands of runs sit inside a 60 s budget. So this
module also asserts the two design decisions that buy the budget:

* **packs load once.** ``load_pack_set`` memoises; the evaluator opens no file,
  reads no clock and touches no environment variable. A run against a
  pre-resolved ``PackSet`` must not be measurably slower than the second run
  against the same ids.
* **instances are built once per scope.** Sixty room rules share one list of room
  instances; without the memo the same twelve rooms would be rebuilt sixty times.

The fixture is a G+2 3BHK-scale model — 36 rooms, 72 openings, 3 storeys — run
against all five packs at once (118 rules), which is worse than production ever
sees (one city pack at a time). Timing on CI hardware is noisy, so the assertion
is on the **median of several runs** and the budget is quoted from
``PERFORMANCE_BUDGET_MS``.
"""

import statistics
import time
from typing import Any, Dict, List, Tuple

from garh_rules import PERFORMANCE_BUDGET_MS, evaluate, load_pack_set
from garh_rules.scope import CheckEnv, instances_for

from .conftest import PACK_IDS, RULEPACK_DIR, make_context, make_room, rect

#: One storey of a 3BHK: 12 rooms, sized so nothing is a violation (a failing run
#: does slightly more work rendering messages, and we want the common case).
STOREY_PLAN: Tuple[Tuple[str, int, int, int, int], ...] = (
    ("living", 0, 0, 4200, 4800),
    ("kitchen", 4400, 0, 2600, 3000),
    ("dining", 4400, 3200, 2600, 2600),
    ("master_bedroom", 0, 5000, 3600, 4200),
    ("bedroom", 3800, 6000, 3300, 3600),
    ("guest_bedroom", 7300, 6000, 3000, 3200),
    ("bath", 4000, 4200, 1500, 1700),
    ("wc", 2000, 4200, 1200, 1400),
    ("pooja", 6200, 4200, 1200, 1200),
    ("corridor", 3600, 4000, 1100, 5000),
    ("staircase", 7200, 0, 2400, 4500),
    ("store", 7200, 5000, 1800, 1800),
)


def _house(storey_count: int = 3) -> Dict[str, Any]:
    rooms: List[Dict[str, Any]] = []
    openings: List[Dict[str, Any]] = []
    stairs: List[Dict[str, Any]] = []
    storeys: List[Dict[str, Any]] = []
    for index in range(storey_count):
        storey_id = "storey_%d" % index
        storeys.append(
            {
                "id": storey_id,
                "index": index,
                "heightMm": 3000,
                "clearHeightMm": 2900,
                "builtUpAreaMm2": 62_000_000,
            }
        )
        for number, (room_type, x, y, width, depth) in enumerate(STOREY_PLAN):
            room_id = "room_%d_%d" % (index, number)
            rooms.append(
                make_room(
                    room_id,
                    room_type,
                    x=x,
                    y=y,
                    width=width,
                    depth=depth,
                    storey_id=storey_id,
                    ventilation_mm2=max(300_000, (width * depth) // 5),
                )
            )
            openings.append(
                {
                    "id": "door_%d_%d" % (index, number),
                    "storeyId": storey_id,
                    "kind": "door",
                    "role": "main-entrance" if (index == 0 and number == 0) else "internal",
                    "widthMm": 900,
                    "heightMm": 2100,
                    "roomIds": [room_id],
                    "centroidMm": [x, y],
                    "outwardNormalDeg": 0,
                }
            )
            openings.append(
                {
                    "id": "window_%d_%d" % (index, number),
                    "storeyId": storey_id,
                    "kind": "window",
                    "role": "internal",
                    "widthMm": 1200,
                    "heightMm": 1200,
                    "sillMm": 900,
                    "roomIds": [room_id],
                    "centroidMm": [x + width // 2, y],
                    "outwardNormalDeg": 90,
                }
            )
        stairs.append(
            {
                "id": "stair_%d" % index,
                "storeyId": storey_id,
                "kind": "dogleg",
                "riserMm": 165,
                "treadMm": 280,
                "widthMm": 1050,
                "headroomMm": 2200,
                "risersCount": 18,
                "centroidMm": [8400, 2250],
            }
        )
    return {
        "storeys": storeys,
        "rooms": rooms,
        "openings": openings,
        "stairs": stairs,
        "storeyCount": storey_count,
        "buildingHeightMm": 3000 * storey_count + 600,
        "heightComponentsMm": {"parapet": 900, "mumty": 2400, "oht": 1200},
        "footprintAreaMm2": 62_000_000,
        "builtUpAreaMm2": 62_000_000 * storey_count,
        "farCountableAreaMm2": 60_000_000 * storey_count,
    }


def house_context(packs: Tuple[str, ...] = PACK_IDS, vastu_mode: str = "advisory") -> Any:
    house = _house()
    return make_context(
        packs=packs,
        vastu_mode=vastu_mode,
        boundary=rect(0, 0, 12_000, 15_000),
        rooms=house.pop("rooms"),
        openings=house.pop("openings"),
        stairs=house.pop("stairs"),
        storeys=house.pop("storeys"),
        projections=[
            {
                "id": "proj_balcony",
                "storeyId": "storey_1",
                "element": "balcony",
                "edgeRole": "front",
                "projectionMm": 900,
                "intoSetback": True,
            },
            {
                "id": "proj_chajja",
                "storeyId": "storey_1",
                "element": "chajja",
                "edgeRole": "front",
                "projectionMm": 600,
                "intoSetback": True,
            },
        ],
        service_elements=[
            {"id": "svc_oht", "kind": "oht", "centroidMm": [10_500, 13_500]},
            {"id": "svc_sump", "kind": "sump", "centroidMm": [1000, 1000]},
        ],
        profile={"cityPack": "blr", "parkingSpacesProvided": 2},
        model=house,
    )


def _median_ms(context: Any, pack_set: Any, runs: int = 12) -> float:
    samples: List[float] = []
    for _ in range(runs):
        started = time.perf_counter()
        evaluate(context, packs=pack_set)
        samples.append((time.perf_counter() - started) * 1000.0)
    return statistics.median(samples)


def test_a_full_run_on_a_house_fits_the_budget() -> None:
    pack_set = load_pack_set(PACK_IDS, root=RULEPACK_DIR)
    context = house_context()
    report = evaluate(context, packs=pack_set)
    assert len(report.results) == len(pack_set.rules) == 118
    assert len(context.model.rooms) == 36
    elapsed = _median_ms(context, pack_set)
    assert elapsed < PERFORMANCE_BUDGET_MS, "%.1f ms exceeds the %d ms budget" % (
        elapsed,
        PERFORMANCE_BUDGET_MS,
    )


def test_one_city_pack_the_production_shape_is_well_inside_it() -> None:
    pack_set = load_pack_set(["blr", "vastu"], root=RULEPACK_DIR)
    context = house_context(packs=("blr", "vastu"))
    elapsed = _median_ms(context, pack_set)
    assert elapsed < PERFORMANCE_BUDGET_MS / 2, "%.1f ms" % elapsed


def test_the_evaluator_does_no_io() -> None:
    """The hot path must not open a file: it runs on every keystroke-debounce."""
    import builtins

    pack_set = load_pack_set(PACK_IDS, root=RULEPACK_DIR)
    context = house_context()
    opened: List[str] = []
    real_open = builtins.open

    def watching_open(*args: Any, **kwargs: Any) -> Any:
        opened.append(str(args[0]) if args else "")
        return real_open(*args, **kwargs)

    builtins.open = watching_open  # type: ignore[assignment]
    try:
        evaluate(context, packs=pack_set)
    finally:
        builtins.open = real_open  # type: ignore[assignment]
    assert opened == [], "the evaluator opened %s — packs must be loaded once" % opened


def test_instances_are_built_once_per_scope() -> None:
    """Sixty room rules share one room-instance list; without the memo the same 36
    rooms would be rebuilt sixty times, which is most of the budget."""
    pack_set = load_pack_set(PACK_IDS, root=RULEPACK_DIR)
    context = house_context()
    env = CheckEnv(context=context, vocabulary=pack_set.vocabulary)
    built = 0
    for rule in pack_set.rules:
        from garh_rules.checks import scope_of

        before = len(env._instances)
        instances_for(rule.check, scope_of(rule.check), env)
        built += len(env._instances) - before
    # 8 scopes at most, and the parameterised ones (edge / projection / zone) key on
    # their own selector — far fewer than one list per rule.
    assert built < 20, built
    assert built < len(pack_set.rules) // 4


def test_repeated_runs_do_not_accumulate_state() -> None:
    """Determinism under reuse: the same PackSet evaluated twice gives the same JSON,
    which is what makes the solver critic's thousands of runs comparable."""
    import json

    pack_set = load_pack_set(PACK_IDS, root=RULEPACK_DIR)
    context = house_context()
    first = json.dumps(evaluate(context, packs=pack_set).to_json(), sort_keys=True)
    for _ in range(3):
        assert json.dumps(evaluate(context, packs=pack_set).to_json(), sort_keys=True) == first
