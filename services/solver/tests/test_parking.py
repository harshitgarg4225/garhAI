"""Car bays the solver places, and what happens when it cannot.

Pure geometry on synthetic houses (no OR-Tools), plus the pipeline wiring through
the fake stage set: the requirement is read off the rules pass, bays land in the
front strip clear of the door where the frontage allows, the option's ops carry the
``furniture.set`` placements, and a strip that cannot hold the requirement is a
typed discard whose sentence reaches the banner — the NEGATIVE CONTROL that keeps
this from being a pass that cannot fail.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from services.solver import parking
from services.solver.pipeline import DETERMINISTIC_TEST_PROFILE, SolveContext, run_solver
from services.solver.stages import Candidate
from services.solver.tests.test_pipeline import FakeSolver, Recorder, make_params
from services.solver.types import PlotEdge, RegProfile, RoomPlacement, RoomRequest, SolveParams

FT = 304.8
BAY = (2500, 5000)
BLR = RegProfile(
    city_pack="blr", coverage_percent=60, far_x100=175, max_height_mm=11000, max_floors=3
)


def _params(
    width_ft: float, depth_ft: float, *, front_setback: int, road: int = 9000
) -> SolveParams:
    w, d = int(width_ft * FT), int(depth_ft * FT)
    return SolveParams(
        plot_polygon=((0, 0), (w, 0), (w, d), (0, d)),
        edges=(
            PlotEdge(index=0, role="front", setback_mm=front_setback, road_width_mm=road),
            PlotEdge(index=1, role="side", setback_mm=1000),
            PlotEdge(index=2, role="rear", setback_mm=1500),
            PlotEdge(index=3, role="side", setback_mm=1000),
        ),
        profile=BLR,
        rooms=(RoomRequest("living", "living", 12_000_000, 16_000_000, 3000),),
        storeys=1,
        seed=7,
    )


def _house(params: SolveParams, *, door_x: int | None = None) -> dict[str, Any]:
    """A rectangular house filling the envelope, its main door on the south wall."""
    w = params.plot_polygon[1][0]
    d = params.plot_polygon[2][1]
    front = params.edges[0].setback_mm
    x1, x2 = 1000, w - 1000
    y1, y2 = front, d - 1500
    walls = [
        {
            "id": "wall_s",
            "storeyId": "storey_g",
            "kind": "external",
            "thicknessMm": 230,
            "a": {"x": x1, "y": y1 + 115},
            "b": {"x": x2, "y": y1 + 115},
        },
        {
            "id": "wall_e",
            "storeyId": "storey_g",
            "kind": "external",
            "thicknessMm": 230,
            "a": {"x": x2 - 115, "y": y1},
            "b": {"x": x2 - 115, "y": y2},
        },
        {
            "id": "wall_n",
            "storeyId": "storey_g",
            "kind": "external",
            "thicknessMm": 230,
            "a": {"x": x2, "y": y2 - 115},
            "b": {"x": x1, "y": y2 - 115},
        },
        {
            "id": "wall_w",
            "storeyId": "storey_g",
            "kind": "external",
            "thicknessMm": 230,
            "a": {"x": x1 + 115, "y": y2},
            "b": {"x": x1 + 115, "y": y1},
        },
    ]
    openings: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"facts": ["doors:1"], "entryOutward": "S"}
    if door_x is not None:
        openings.append(
            {
                "id": "door_main",
                "wallId": "wall_s",
                "kind": "door",
                "widthMm": 1000,
                "heightMm": 2100,
                "sillMm": 0,
                "offsetMm": door_x - x1,
                "swing": "in-left",
            }
        )
        meta["mainDoorId"] = "door_main"
    return {
        "storeys": [{"id": "storey_g", "name": "Ground Floor", "heightMm": 3000}],
        "walls": walls,
        "openings": openings,
        "rooms": [],
        "furniture": [],
        "solverMeta": meta,
    }


# ---------------------------------------------------------------------------
# the requirement
# ---------------------------------------------------------------------------


def test_requirement_is_the_largest_applicable_parking_limit() -> None:
    rows = [
        {
            "ruleId": "blr.parking.plot.le240",
            "checkType": "parking_min",
            "status": "fail",
            "limit": 1,
        },
        {
            "ruleId": "blr.parking.plot.gt240",
            "checkType": "parking_min",
            "status": "not_applicable",
            "limit": 2,
        },
        {"ruleId": "ncr.parking.ecs", "checkType": "parking_min", "status": "pass", "limit": 3},
        {"ruleId": "blr.coverage", "checkType": "coverage_max", "status": "pass", "limit": 99},
    ]
    requirement = parking.requirement_from_rows(rows)
    assert requirement.count == 3 and requirement.applies
    assert requirement.rule_ids == ("blr.parking.plot.le240", "ncr.parking.ecs")
    assert not parking.requirement_from_rows([]).applies


def test_the_bay_size_is_the_catalogues() -> None:
    assert parking.bay_size_mm() == BAY


# ---------------------------------------------------------------------------
# placement geometry
# ---------------------------------------------------------------------------


def test_a_30x40_parks_one_bay_parallel_to_the_road_in_its_3m_setback() -> None:
    params = _params(30, 40, front_setback=3000)
    plan = parking.plan_parking(_house(params, door_x=4572), params, required=1, bay_mm=BAY)
    assert plan.orientation == "parallel" and plan.strip_depth_mm == 3000
    assert len(plan.bays) == 1 and plan.satisfied
    (bay,) = plan.bays
    assert bay.rotation_deg == 90 and (bay.x2 - bay.x1, bay.y2 - bay.y1) == (5000, 2500)
    assert bay.y1 == 0, "against the road edge, reachable by construction"
    assert bay.x1 >= parking.SIDE_MARGIN_MM and bay.x2 <= 9144 - parking.SIDE_MARGIN_MM
    # A central door on a 7.9 m usable frontage cannot keep a 5 m bay clear of it.
    assert plan.clear_of_door is False
    assert plan.fact() == "parking:1@front:2500x5000"


def test_the_bay_keeps_clear_of_the_door_when_the_frontage_allows() -> None:
    params = _params(30, 40, front_setback=3000)
    plan = parking.plan_parking(_house(params, door_x=7500), params, required=1, bay_mm=BAY)
    (bay,) = plan.bays
    assert plan.clear_of_door is True
    # Packed from the end farther from the door: the west end.
    assert bay.x1 == parking.SIDE_MARGIN_MM
    assert bay.x2 <= 7500 - 500 - parking.DOOR_APPROACH_MM


def test_a_deep_setback_parks_perpendicular_and_skips_the_door_approach() -> None:
    params = _params(50, 80, front_setback=6000)
    plan = parking.plan_parking(_house(params, door_x=7620), params, required=3, bay_mm=BAY)
    assert plan.orientation == "perpendicular"
    assert len(plan.bays) == 3 and plan.clear_of_door is True
    for bay in plan.bays:
        assert bay.rotation_deg == 0 and (bay.x2 - bay.x1, bay.y2 - bay.y1) == (2500, 5000)
        assert bay.y1 == 0
        assert (
            bay.x2 <= 7620 - 500 - parking.DOOR_APPROACH_MM
            or bay.x1 >= 7620 + 500 + parking.DOOR_APPROACH_MM
        )


def test_the_briefs_wish_is_honoured_above_the_requirement() -> None:
    params = _params(40, 60, front_setback=3000)
    plan = parking.plan_parking(
        _house(params, door_x=6096), params, required=1, wanted=2, bay_mm=BAY
    )
    assert plan.wanted == 2 and len(plan.bays) == 2
    assert plan.bays[0].x2 <= plan.bays[1].x1 or plan.bays[1].x2 <= plan.bays[0].x1


def test_a_shallow_setback_places_nothing_and_says_why() -> None:
    """NEGATIVE CONTROL for the geometry: 2 m cannot hold a 2.5 m bay."""
    params = _params(30, 40, front_setback=2000)
    plan = parking.plan_parking(_house(params, door_x=4572), params, required=1, bay_mm=BAY)
    assert plan.bays == () and plan.orientation == "none" and not plan.satisfied
    assert plan.shortfall_message() == (
        "The bye-law needs 1 car space(s) of 2.5 × 5.0 m and only 0 fit in the 2.0 m front setback."
    )
    assert "stilt" in plan.shortfall_action()


def _rotated_params(front_index: int, *, front_setback: int = 3000) -> SolveParams:
    """The same 30 x 40 plot with the ROAD on a different boundary each time.

    Index 0 is the south edge (y = 0), which is the only one every other test in this
    file uses. Indexes 1 and 2 are the east and north edges, where `inward` is -1.
    """
    w, d = int(30 * FT), int(40 * FT)
    roles: list[str] = ["side", "side", "side", "side"]
    roles[front_index] = "front"
    roles[(front_index + 2) % 4] = "rear"
    edges = tuple(
        PlotEdge(
            index=i,
            role=roles[i],
            setback_mm=front_setback
            if i == front_index
            else (1500 if roles[i] == "rear" else 1000),
            **({"road_width_mm": 9000} if i == front_index else {}),
        )
        for i in range(4)
    )
    return SolveParams(
        plot_polygon=((0, 0), (w, 0), (w, d), (0, d)),
        edges=edges,
        profile=BLR,
        rooms=(RoomRequest("living", "living", 12_000_000, 16_000_000, 3000),),
        storeys=1,
        seed=7,
    )


def _rotated_house(params: SolveParams, front_index: int) -> dict[str, Any]:
    """A rectangular house set back from whichever edge carries the road."""
    w = params.plot_polygon[1][0]
    d = params.plot_polygon[2][1]
    front = params.edges[front_index].setback_mm
    if front_index == 0:
        x1, x2, y1, y2 = 1000, w - 1000, front, d - 1500
    elif front_index == 2:
        x1, x2, y1, y2 = 1000, w - 1000, 1500, d - front
    elif front_index == 1:
        x1, x2, y1, y2 = 1500, w - front, 1000, d - 1000
    else:
        x1, x2, y1, y2 = front, w - 1500, 1000, d - 1000
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    walls: list[dict[str, Any]] = []
    for i, (ax, ay) in enumerate(corners):
        bx, by = corners[(i + 1) % 4]
        # Centrelines inset half the thickness, so the OUTER faces sit on the box.
        if ay == by:
            offset = 115 if ay == y1 else -115
            a, b = {"x": ax, "y": ay + offset}, {"x": bx, "y": by + offset}
        else:
            offset = 115 if ax == x1 else -115
            a, b = {"x": ax + offset, "y": ay}, {"x": bx + offset, "y": by}
        walls.append(
            {
                "id": "wall_%d" % i,
                "storeyId": "storey_g",
                "kind": "external",
                "thicknessMm": 230,
                "a": a,
                "b": b,
            }
        )
    return {
        "storeys": [{"id": "storey_g", "name": "Ground Floor", "heightMm": 3000}],
        "walls": walls,
        "openings": [],
        "rooms": [],
        "furniture": [],
        "solverMeta": {"facts": ["doors:1"]},
        "_box": (x1, y1, x2, y2),
    }


def test_the_front_strip_is_the_setback_whichever_boundary_the_road_is_on() -> None:
    """The road is not always on the south edge, and the bay must not enter the house.

    `_front_strip` aggregated the candidate wall faces with `min` regardless of which
    way `inward` pointed. With the road north or east that returned the FAR wall, so a
    3 m setback measured as 7.4 m or 10.5 m, the orientation flipped to perpendicular,
    and the bay was drawn metres inside the living room — with `satisfied` true, so the
    option shipped and the compliance tab then failed it as unreachable. Every other
    test here puts the front edge at y = 0, so nothing executed the other branch.

    An east-facing plot is not an edge case in India; it is the one many clients ask for.
    """
    for front_index in range(4):
        params = _rotated_params(front_index)
        house = _rotated_house(params, front_index)
        box = house.pop("_box")
        strip = parking._front_strip(params, house)
        assert strip is not None, "no strip for front edge %d" % front_index
        assert strip.depth == 3000, (
            "front edge %d measured a %d mm strip for a 3000 mm setback — it is "
            "measuring past the near wall" % (front_index, strip.depth)
        )

        plan = parking.plan_parking(house, params, required=1, bay_mm=(2500, 5000))
        assert plan.satisfied, "front edge %d placed nothing" % front_index
        hx1, hy1, hx2, hy2 = box
        for bay in plan.bays:
            overlap_x = min(bay.x2, hx2) - max(bay.x1, hx1)
            overlap_y = min(bay.y2, hy2) - max(bay.y1, hy1)
            assert overlap_x <= 0 or overlap_y <= 0, (
                "front edge %d drew a bay %s overlapping the house %s"
                % (front_index, (bay.x1, bay.y1, bay.x2, bay.y2), box)
            )


def test_bays_stay_inside_an_l_shaped_plot() -> None:
    # A 30 x 40 with its front-west corner notched out (1.5 m x 1.5 m): the bay
    # must sit on the plot that is left, with the side margin measured from the
    # notch, and every corner inside the polygon.
    params = _params(30, 40, front_setback=3000)
    notched = SolveParams(
        plot_polygon=((1500, 0), (9144, 0), (9144, 12192), (0, 12192), (0, 1500), (1500, 1500)),
        edges=(
            PlotEdge(index=0, role="front", setback_mm=3000, road_width_mm=9000),
            PlotEdge(index=1, role="side", setback_mm=1000),
            PlotEdge(index=2, role="rear", setback_mm=1500),
            PlotEdge(index=3, role="side", setback_mm=1000),
            PlotEdge(index=4, role="side", setback_mm=0),
            PlotEdge(index=5, role="side", setback_mm=0),
        ),
        profile=BLR,
        rooms=params.rooms,
        storeys=1,
        seed=7,
    )
    plan = parking.plan_parking(_house(params, door_x=4000), notched, required=1, bay_mm=BAY)
    assert len(plan.bays) == 1
    (bay,) = plan.bays
    assert bay.x1 >= 1500 + parking.SIDE_MARGIN_MM, "the notch is not plot"
    from services.solver.geometry import point_in_polygon

    for corner in ((bay.x1, bay.y1), (bay.x2, bay.y1), (bay.x2, bay.y2), (bay.x1, bay.y2)):
        assert point_in_polygon(corner, notched.plot_polygon)


def test_with_parking_adds_catalogue_bays_to_the_ground_storey() -> None:
    params = _params(30, 40, front_setback=3000)
    house, plan = parking.with_parking(_house(params, door_x=4572), params, required=1, bay_mm=BAY)
    assert plan.satisfied
    (item,) = house["furniture"]
    assert item["catalogId"] == parking.PARKING_BAY_CATALOG_ID
    assert item["storeyId"] == "storey_g" and item["rotationDeg"] == 90
    assert item["id"].startswith("furniture_")
    assert "parking:1@front:2500x5000" in house["solverMeta"]["facts"]
    assert house["solverMeta"]["parking"]["placed"] == 1
    assert house["solverMeta"]["parking"]["clearOfDoor"] is False


# ---------------------------------------------------------------------------
# the pipeline: requirement in, bays out, shortfall on the banner
# ---------------------------------------------------------------------------


def _pipeline_params(front_setback: int) -> SolveParams:
    """The fake stage set's 18 m plot (its room placements are laid out for it),
    with a front road and the setback under test."""
    base = make_params()
    return SolveParams(
        plot_polygon=base.plot_polygon,
        edges=(
            PlotEdge(index=0, role="front", setback_mm=front_setback, road_width_mm=9000),
            PlotEdge(index=1, role="side", setback_mm=1500),
            PlotEdge(index=2, role="rear", setback_mm=1500),
            PlotEdge(index=3, role="side", setback_mm=1500),
        ),
        profile=base.profile,
        rooms=base.rooms,
        storeys=1,
        seed=7,
    )


class _ParkingFake(FakeSolver):
    """A stage set whose rules pass demands one car and whose stage B builds walls."""

    def stage_b(
        self, candidate: Candidate, params: SolveParams, envelope: Any
    ) -> Mapping[str, Any] | None:
        base = super().stage_b(candidate, params, envelope)
        if base is None:
            return None
        house = _house(params, door_x=params.plot_polygon[1][0] // 2)
        return {**base, **house}

    def compliance(self, model: Mapping[str, Any], params: SolveParams) -> list[dict[str, Any]]:
        return [
            {"ruleId": "nbc.room.area", "status": "pass", "checkType": "room_area_min"},
            {
                "ruleId": "blr.parking.plot.le240",
                "status": "fail" if not model.get("furniture") else "pass",
                "checkType": "parking_min",
                "actual": len(model.get("furniture") or []),
                "limit": 1,
            },
        ]

    def build_ops(
        self, placements: Sequence[RoomPlacement], params: SolveParams, *, model: Any = None
    ) -> list[dict[str, Any]]:
        ops = super().build_ops(placements, params, model=model)
        for item in (model or {}).get("furniture") or []:
            ops.append({"type": "furniture.set", "payload": {"action": "place", **item}})
        return ops


def _context(fake: FakeSolver, params: SolveParams) -> tuple[SolveContext, Recorder]:
    recorder = Recorder()
    context = SolveContext(
        params=params,
        progress=recorder,
        check_cancelled=lambda: None,
        profile=DETERMINISTIC_TEST_PROFILE,
        stages=fake.stage_set(),
    )
    return context, recorder


def test_the_pipeline_places_the_bays_the_packs_demand() -> None:
    params = _pipeline_params(front_setback=3000)
    fake = _ParkingFake()
    context, _ = _context(fake, params)
    result = asyncio.run(run_solver(context))
    assert len(result.options) == 3
    for option in result.options:
        bays = [op for op in option.ops if op["type"] == "furniture.set"]
        assert len(bays) == 1 and bays[0]["payload"]["catalogId"] == "parking-bay"
        assert "parking:1@front:2500x5000" in option.rationale_facts
        parking_rows = [r for r in option.compliance if r["ruleId"] == "blr.parking.plot.le240"]
        assert parking_rows and parking_rows[0]["status"] == "pass"


def test_a_setback_that_cannot_hold_the_bay_discards_and_names_it_on_the_banner() -> None:
    """NEGATIVE CONTROL: 2 m of front setback, one car required — nothing clears,
    and the architect reads the reason, not 'no plan cleared'."""
    params = _pipeline_params(front_setback=2000)
    fake = _ParkingFake()
    context, recorder = _context(fake, params)
    result = asyncio.run(run_solver(context))
    assert result.options == ()
    assert result.banner is not None
    assert result.banner.startswith(
        "The bye-law needs 1 car space(s) of 2.5 × 5.0 m and only 0 fit"
    )
    discards = [
        d
        for e in recorder.events
        for d in (e["data"].get("discards") or [])
        if d["stage"] == "parking"
    ]
    assert discards, "the discard is on the diversity event, never silent"


def test_a_pack_without_a_parking_rule_places_nothing() -> None:
    fake = FakeSolver()  # its rules pass carries no parking_min row
    context, _ = _context(fake, make_params())
    result = asyncio.run(run_solver(context))
    assert len(result.options) == 3
    assert all(not any(op["type"] == "furniture.set" for op in o.ops) for o in result.options)
