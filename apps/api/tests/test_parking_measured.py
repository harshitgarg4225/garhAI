"""Parking compliance is measured off the plan, never off the brief.

Every city pack's parking rule used to pass on ``brief.data.carParking`` — an integer
the architect typed — while its message said "{actual} car space(s) are shown". The
context builder now measures ``parking-bay`` furniture on the ground storey
(``garh_api.parking_geometry``) and the engine counts only the bays that are in the
plot, clear of the house, and reachable from a road in a straight run.

The first test is the NEGATIVE CONTROL and the reason the module exists: a brief
that promises one car over a plan that draws none must FAIL ``blr.parking.plot.le240``.
Before this it passed.
"""

from __future__ import annotations

from typing import Any

from garh_api.compliance import build_evaluation_context, evaluate_document
from garh_api.parking_geometry import PARKING_BAY_CATALOG_ID, measure_parking_spaces
from garh_model import replay
from garh_model.model import DEFAULTS
from garh_model.ops import Op, example_id, op
from garh_model.testing import DEMO_PLOT_POLYGON

STOREY = example_id("storey", "GF")
RULE = "blr.parking.plot.le240"  # the 30 x 40 demo plot is 111 m², under 240


def _pt(x: int, y: int) -> dict[str, int]:
    return {"x": x, "y": y}


def _house_ops(*, car_parking: int = 1) -> list[Op]:
    """A 6 x 4 m two-room house standing 3 m back from the 9 m road on the south.

    .. code-block:: text

        y=7000 +-----------+-----------+
               |  living   |  kitchen  |
        y=3000 +-----------+-----------+   x = 1000 .. 7000, spine at 4000
                       front yard (3 m deep)
        y=0    ============ 9 m road ============
    """
    return [
        op("plot.set_boundary", polygon=list(DEMO_PLOT_POLYGON), source="seed"),
        op("plot.set_north", deg=0),
        op("plot.set_road", edgeIndex=0, widthMm=9000, name="9m Road"),
        op("plot.set_reg_profile", cityPack="blr", overrides={}),
        op(
            "brief.update",
            patch={"rooms": [{"type": "living", "count": 1}], "carParking": car_parking},
        ),
        op(
            "storey.add",
            id=STOREY,
            index=0,
            name="Ground Floor",
            heightMm=DEFAULTS.storey_height_mm,
        ),
        op(
            "wall.add",
            id=example_id("wall", "S"),
            storeyId=STOREY,
            a=_pt(1000, 3000),
            b=_pt(7000, 3000),
            thicknessMm=230,
            kind="external",
        ),
        op(
            "wall.add",
            id=example_id("wall", "E"),
            storeyId=STOREY,
            a=_pt(7000, 3000),
            b=_pt(7000, 7000),
            thicknessMm=230,
            kind="external",
        ),
        op(
            "wall.add",
            id=example_id("wall", "N"),
            storeyId=STOREY,
            a=_pt(7000, 7000),
            b=_pt(1000, 7000),
            thicknessMm=230,
            kind="external",
        ),
        op(
            "wall.add",
            id=example_id("wall", "W"),
            storeyId=STOREY,
            a=_pt(1000, 7000),
            b=_pt(1000, 3000),
            thicknessMm=230,
            kind="external",
        ),
        op(
            "wall.add",
            id=example_id("wall", "SPINE"),
            storeyId=STOREY,
            a=_pt(4000, 3000),
            b=_pt(4000, 7000),
            thicknessMm=115,
            kind="internal",
        ),
    ]


def _bay(tag: str, x: int, y: int, rotation: int = 0) -> Op:
    return op(
        "furniture.set",
        action="place",
        id=example_id("furniture", tag),
        storeyId=STOREY,
        catalogId=PARKING_BAY_CATALOG_ID,
        pt=_pt(x, y),
        rotationDeg=rotation,
    )


def _doc(*extra: Op, car_parking: int = 1) -> dict[str, Any]:
    ops = _house_ops(car_parking=car_parking)
    # Name the rooms the fold detected so they read as habitable to the packs.
    house = replay(ops).house
    for index, room in enumerate(sorted(house.rooms, key=lambda r: r.id)):
        ops.append(op("room.assign", roomId=room.id, type="living" if index == 0 else "kitchen"))
    return replay([*ops, *extra]).to_json()


def _parking_row(doc: dict[str, Any]) -> dict[str, Any]:
    payload, _versions = evaluate_document(doc, city_pack="blr")
    rows = [r for r in payload["results"] if r["ruleId"] == RULE]
    assert len(rows) == 1, "the 30 x 40 plot must hit exactly the <=240 m² parking rule"
    return rows[0]


# ---------------------------------------------------------------------------
# the rule, end to end through the real projection and engine
# ---------------------------------------------------------------------------


def test_a_declared_car_with_no_bay_drawn_fails_the_parking_rule() -> None:
    """NEGATIVE CONTROL: the brief promises one car; the plan shows none."""
    row = _parking_row(_doc(car_parking=1))
    assert row["status"] == "fail", row["message"]
    assert row["actual"] == 0 and row["limit"] == 1
    assert "0 car space(s) are shown" in row["message"]


def test_a_bay_in_the_front_yard_passes_and_the_brief_is_irrelevant() -> None:
    # 5 000 along the road, 2 500 deep, centred in the 3 m front yard — a bay
    # parked parallel to the road, the way a 30 x 40 house actually does it.
    bay = _bay("FRONT", 4000, 1500, rotation=90)
    row = _parking_row(_doc(bay, car_parking=0))  # the brief declares NONE
    assert row["status"] == "pass", row["message"]
    assert row["actual"] == 1 and row["limit"] == 1
    # The area statement's "provided" is the measured count too — one source.
    payload, _ = evaluate_document(_doc(bay, car_parking=0), city_pack="blr")
    assert payload["areas"]["parkingProvided"] == 1


def test_a_bay_behind_the_house_is_unreachable_and_named() -> None:
    row = _parking_row(_doc(_bay("REAR", 4000, 9000, rotation=90)))
    assert row["status"] == "fail"
    assert row["actual"] == 0
    assert row["elements"] == [example_id("furniture", "REAR")]


def test_a_bay_inside_a_living_room_is_not_a_parking_space() -> None:
    row = _parking_row(_doc(_bay("INSIDE", 2500, 5000)))
    assert row["status"] == "fail" and row["actual"] == 0


def test_two_bays_stacked_on_each_other_count_once() -> None:
    doc = _doc(_bay("A", 4000, 1500, rotation=90), _bay("B", 4200, 1500, rotation=90))
    context = build_evaluation_context(doc, packs=("nbc-core", "blr"))
    rows = context["model"]["parkingSpaces"]
    assert [r["reachable"] for r in rows] == [True, False]
    assert context["profile"]["parkingSpacesProvided"] == 1


def test_a_bay_poking_outside_the_plot_does_not_count() -> None:
    # Centred 600 mm from the west boundary with 2 500 mm of width: 650 mm outside.
    context = build_evaluation_context(_doc(_bay("EDGE", 600, 1500)), packs=("nbc-core", "blr"))
    assert context["model"]["parkingSpaces"][0]["reachable"] is False


def test_the_stdlib_reader_and_the_served_catalogue_agree_on_the_bay() -> None:
    """One source for the number, two readers — assert they cannot drift.

    ``build_evaluation_context`` reads the catalogue with the stdlib (``make bare``
    evaluates rule fixtures on an interpreter with no FastAPI, and importing the
    router to read a JSON file broke that gate). The route's own loader must see
    the same rectangle.
    """
    from garh_api.parking_geometry import catalog_bay_size_mm, catalog_furniture_items
    from garh_api.routers.catalog import _load_catalog

    _source, served = _load_catalog("furniture")
    served_bay = next(i for i in served if str(i.get("id")) == PARKING_BAY_CATALOG_ID)
    assert catalog_bay_size_mm() == (int(served_bay["widthMm"]), int(served_bay["depthMm"]))
    assert len(catalog_furniture_items()) == len(served)


def test_the_context_measures_even_when_the_house_has_no_furniture() -> None:
    """An empty measurement is a fail, never a fallback to the declaration."""
    context = build_evaluation_context(_doc(car_parking=2), packs=("nbc-core", "blr"))
    assert context["model"]["parkingSpaces"] == []
    assert context["profile"]["parkingSpacesProvided"] == 0


# ---------------------------------------------------------------------------
# the geometry, in isolation
# ---------------------------------------------------------------------------

PLOT = [(0, 0), (9144, 0), (9144, 12192), (0, 12192)]
HOUSE = [("living", [(1115, 3115), (6885, 3115), (6885, 6885), (1115, 6885)])]


def _measure(furniture: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return measure_parking_spaces(
        furniture=furniture,
        boundary=PLOT,
        road_edge_indexes=[0],
        ground_storey_id="g",
        ground_rooms=HOUSE,
        bay_size_mm=(2500, 5000),
    )


def _item(bay_id: str, x: int, y: int, rot: int = 0, storey: str = "g") -> dict[str, Any]:
    return {
        "id": bay_id,
        "storeyId": storey,
        "catalogId": PARKING_BAY_CATALOG_ID,
        "pt": {"x": x, "y": y},
        "rotationDeg": rot,
    }


def test_rotation_swaps_the_sides_exactly() -> None:
    (row,) = _measure([_item("r", 4000, 1500, 90)])
    assert row["widthMm"] == 5000 and row["lengthMm"] == 2500
    assert row["polygonMm"] == [[1500, 250], [6500, 250], [6500, 2750], [1500, 2750]]
    assert row["reachable"] is True


def test_a_bay_on_an_upper_storey_is_not_a_space() -> None:
    (row,) = _measure([_item("up", 4000, 1500, 90, storey="first")])
    assert row["reachable"] is False


def test_a_bay_inside_a_garage_room_counts_when_the_garage_faces_the_road() -> None:
    garage = ("garage", [(1115, 3115), (6885, 3115), (6885, 6885), (1115, 6885)])
    rows = measure_parking_spaces(
        furniture=[_item("g1", 4000, 5000)],
        boundary=PLOT,
        road_edge_indexes=[0],
        ground_storey_id="g",
        ground_rooms=[garage],
        bay_size_mm=(2500, 5000),
    )
    assert rows[0]["reachable"] is True


def test_other_furniture_is_not_a_parking_space() -> None:
    assert _measure([{**_item("car", 4000, 1500), "catalogId": "car-sedan"}]) == []


def test_a_road_on_a_skewed_edge_cannot_prove_reach() -> None:
    skew = [(0, 0), (9144, 800), (9144, 12192), (0, 12192)]
    rows = measure_parking_spaces(
        furniture=[_item("s", 4000, 2000, 90)],
        boundary=skew,
        road_edge_indexes=[0],
        ground_storey_id="g",
        ground_rooms=HOUSE,
        bay_size_mm=(2500, 5000),
    )
    assert rows[0]["reachable"] is False
