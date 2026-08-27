"""The §16 golden brief corpus — 20 briefs that will gate the solver in Phase 3.

*"Solver: 20-brief golden corpus (`fixtures/briefs/`) → assert gates (§5.6), determinism per
seed, time budget, locked-room preservation."*

Phase 3 does not exist yet, so this file cannot assert that the solver solves them. What it
*can* assert — and what has to be true before the solver is written, not after — is that the
corpus is a usable input:

* it really is **20** briefs, and really is **stratified** (city / shape / floors / plot
  size). A corpus of twenty 30x40 ft Bengaluru plots would pass every gate and prove
  nothing about a T-shaped Hyderabad plot with a stilt;
* every brief is **language-neutral JSON** matching the Brief schema, with **no floats**;
* every declared ``stratum`` matches the brief's own contents, and every polygon's area
  matches its own vertices — so a fixture cannot claim to be a 50x80 ft L-shape while
  holding a rectangle;
* the demo project's brief (seed-authored since brief 01's 16-room program proved
  CP-SAT-infeasible on its own plot — see ``garh_api.seed.demo``) still describes the
  §17 house and speaks the model's room vocabulary exactly.

Rejected briefs (a solver failure caused by bad input) are the most expensive kind of
Phase 3 debugging there is, which is why these are checked now.
"""

from __future__ import annotations

import glob
import json
import os
from collections import Counter
from typing import Any

import pytest
from garh_api.seed.catalog import fixtures_dir
from garh_api.seed.demo import DEMO_BRIEF_CORPUS_SIBLING, load_demo_brief
from garh_model.model import ROOM_TYPES

BRIEF_DIR = os.path.join(fixtures_dir(), "briefs")

#: §16 says twenty. Not "about twenty".
REQUIRED_COUNT = 20

#: The three MVP city packs, and the shapes §5 supports ("rect/L/T plots only in MVP").
CITY_PACKS = ("blr", "ncr", "hyd")
SHAPES = ("rect", "L", "T")

#: Vertices a shape must have. A "T" with four corners is a rectangle with a label.
SHAPE_VERTEX_COUNT = {"rect": 4, "L": 6, "T": 8}


def _brief_paths() -> list[str]:
    return sorted(glob.glob(os.path.join(BRIEF_DIR, "brief-*.json")))


@pytest.fixture(scope="module")
def briefs() -> list[dict[str, Any]]:
    paths = _brief_paths()
    assert paths, "no briefs found in %s" % BRIEF_DIR
    loaded = []
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        document["__path"] = path
        loaded.append(document)
    return loaded


@pytest.fixture(scope="module")
def index() -> dict[str, Any]:
    with open(os.path.join(BRIEF_DIR, "index.json"), encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# Size and stratification
# ---------------------------------------------------------------------------


def test_corpus_has_twenty_briefs(briefs: list[dict[str, Any]]) -> None:
    assert len(briefs) == REQUIRED_COUNT, "§16 asks for %d briefs" % REQUIRED_COUNT


def test_index_agrees_with_the_files(briefs: list[dict[str, Any]], index: dict[str, Any]) -> None:
    """A manifest that disagrees with the directory is worse than no manifest."""
    assert index["count"] == index["required"] == REQUIRED_COUNT
    assert len(index["briefs"]) == len(briefs)
    assert {entry["id"] for entry in index["briefs"]} == {b["id"] for b in briefs}


def test_ids_match_filenames(briefs: list[dict[str, Any]]) -> None:
    """The id is how a Phase 3 golden file names its input; it must be findable."""
    for brief in briefs:
        expected = os.path.basename(brief["__path"])[: -len(".json")]
        assert brief["id"] == expected, (brief["id"], expected)


@pytest.mark.parametrize("dimension", ["city", "shape", "floorsAboveGround", "plotAreaBand"])
def test_declared_strata_match_the_files(
    briefs: list[dict[str, Any]], index: dict[str, Any], dimension: str
) -> None:
    actual = Counter(str(brief["stratum"][dimension]) for brief in briefs)
    declared = {str(key): value for key, value in index["strata"][dimension].items()}
    assert dict(actual) == declared, (dimension, dict(actual), declared)


def test_all_three_city_packs_are_represented(briefs: list[dict[str, Any]]) -> None:
    """The MVP ships three city packs; a corpus that exercises one tests one."""
    cities = Counter(brief["stratum"]["city"] for brief in briefs)
    for pack in CITY_PACKS:
        assert cities[pack] >= 5, (
            "only %d brief(s) for %s — too few to catch a bye-law regression" % (cities[pack], pack)
        )
    assert set(cities) == set(CITY_PACKS), set(cities)


def test_all_three_envelope_shapes_are_represented(briefs: list[dict[str, Any]]) -> None:
    """§5: "Envelope: rect/L/T plots only in MVP" — so all three must appear."""
    shapes = Counter(brief["stratum"]["shape"] for brief in briefs)
    assert set(shapes) == set(SHAPES), set(shapes)
    for shape in SHAPES:
        assert shapes[shape] >= 3, (shape, shapes[shape])


def test_floor_counts_span_the_mvp_range(briefs: list[dict[str, Any]]) -> None:
    """G+0 through G+3: a single-storey plan and a G+3 exercise different constraints."""
    floors = {brief["stratum"]["floorsAboveGround"] for brief in briefs}
    assert floors == {0, 1, 2, 3}, floors


def test_plot_sizes_span_small_to_large(briefs: list[dict[str, Any]]) -> None:
    """Setbacks bind hardest on small plots and FAR on large ones — both must be in."""
    bands = {brief["stratum"]["plotAreaBand"] for brief in briefs}
    assert bands == {"le120", "121-240", "241-500", "gt500"}, bands

    areas = sorted(brief["stratum"]["plotAreaSqm"] for brief in briefs)
    assert areas[0] <= 120, "no genuinely small plot: smallest is %d m2" % areas[0]
    assert areas[-1] > 500, "no genuinely large plot: largest is %d m2" % areas[-1]


# ---------------------------------------------------------------------------
# Internal consistency
# ---------------------------------------------------------------------------


def test_every_polygon_is_integer_millimetres_and_matches_its_area(
    briefs: list[dict[str, Any]],
) -> None:
    """The declared area must be the shoelace area of the declared vertices.

    A fixture whose area disagrees with its own polygon would make every FAR and coverage
    assertion in Phase 3 wrong in a way that looks like a solver bug.
    """
    for brief in briefs:
        plot = brief["plot"]
        polygon = plot["polygon"]
        for point in polygon:
            assert isinstance(point["x"], int) and not isinstance(point["x"], bool), brief["id"]
            assert isinstance(point["y"], int) and not isinstance(point["y"], bool), brief["id"]

        doubled = 0
        for index_ in range(len(polygon)):
            current = polygon[index_]
            following = polygon[(index_ + 1) % len(polygon)]
            doubled += current["x"] * following["y"] - following["x"] * current["y"]
        area_mm2 = abs(doubled) // 2
        assert area_mm2 == plot["areaMm2"], (brief["id"], area_mm2, plot["areaMm2"])
        # areaSqm is the rounded display value; 1 m2 = 1_000_000 mm2.
        assert abs(plot["areaSqm"] - area_mm2 // 1_000_000) <= 1, brief["id"]


def test_shape_matches_the_vertex_count(briefs: list[dict[str, Any]]) -> None:
    for brief in briefs:
        expected = SHAPE_VERTEX_COUNT[brief["stratum"]["shape"]]
        actual = len(brief["plot"]["polygon"])
        assert actual == expected, "%s claims shape %r but has %d vertices (expected %d)" % (
            brief["id"],
            brief["stratum"]["shape"],
            actual,
            expected,
        )


def test_stratum_matches_the_brief_data(briefs: list[dict[str, Any]]) -> None:
    """The stratum is an index, not a second source of truth."""
    for brief in briefs:
        stratum = brief["stratum"]
        data = brief["data"]
        assert stratum["floorsAboveGround"] == data["floorsAboveGround"], brief["id"]
        assert stratum["bedrooms"] == data["bedrooms"], brief["id"]
        assert stratum["vastuMode"] == brief["vastuMode"], brief["id"]
        assert stratum["city"] == brief["plot"]["cityPack"], brief["id"]


def test_no_floats_anywhere(briefs: list[dict[str, Any]]) -> None:
    """§3, and a hard requirement of ``canonicalJson``: the corpus feeds op payloads."""
    offenders: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, float):
            offenders.append("%s = %r" % (path, value))
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(item, "%s.%s" % (path, key))
        elif isinstance(value, list):
            for position, item in enumerate(value):
                walk(item, "%s[%d]" % (path, position))

    for brief in briefs:
        walk({k: v for k, v in brief.items() if k != "__path"}, brief["id"])
    assert not offenders, offenders


def test_every_room_type_is_a_model_room_type(briefs: list[dict[str, Any]]) -> None:
    """A typo here is a room the solver silently never places."""
    for brief in briefs:
        for room in brief["data"].get("rooms", []):
            assert room["type"] in ROOM_TYPES, (brief["id"], room["type"])


def test_room_targets_are_integer_mm2(briefs: list[dict[str, Any]]) -> None:
    for brief in briefs:
        for room in brief["data"].get("rooms", []):
            assert isinstance(room["count"], int) and room["count"] >= 1, brief["id"]
            for field in ("targetAreaMm2", "minWidthMm"):
                value = room.get(field)
                if value is None:
                    continue
                assert isinstance(value, int) and value > 0, (brief["id"], field, value)


def test_completeness_and_vastu_mode_are_in_range(briefs: list[dict[str, Any]]) -> None:
    for brief in briefs:
        assert isinstance(brief["completeness"], int)
        assert 0 <= brief["completeness"] <= 100, brief["id"]
        assert brief["vastuMode"] in ("off", "advisory", "strict"), brief["id"]


def test_every_brief_has_at_least_one_road(briefs: list[dict[str, Any]]) -> None:
    """Setback tables are banded on road width; a plot with no road cannot be checked."""
    for brief in briefs:
        roads = brief["plot"]["roads"]
        assert roads, brief["id"]
        for road in roads:
            assert isinstance(road["widthMm"], int) and road["widthMm"] > 0, brief["id"]
            assert isinstance(road["edgeIndex"], int), brief["id"]
            assert 0 <= road["edgeIndex"] < len(brief["plot"]["polygon"]), brief["id"]


def test_bedroom_counts_are_plausible_for_the_plot(briefs: list[dict[str, Any]]) -> None:
    """A 4BHK on 60 m2 would fail every gate for reasons that are not the solver's fault.

    Deliberately generous: this catches a fixture typo (40 bedrooms), not a design opinion.
    """
    for brief in briefs:
        area = brief["stratum"]["plotAreaSqm"]
        floors = brief["data"]["floorsAboveGround"] + 1
        bedrooms = brief["data"]["bedrooms"]
        assert 1 <= bedrooms <= 8, (brief["id"], bedrooms)
        assert bedrooms <= max(2, area * floors // 25), (
            "%s wants %d bedrooms on %d m2 x %d floor(s)" % (brief["id"], bedrooms, area, floors)
        )


# ---------------------------------------------------------------------------
# The demo brief still describes the §17 house — in the model's own vocabulary
# ---------------------------------------------------------------------------


def test_the_demo_brief_matches_the_17_house(briefs: list[dict[str, Any]]) -> None:
    """The demo brief is seed-authored (brief 01's program is CP-SAT-infeasible on
    this plot — the whole story lives in ``garh_api.seed.demo``), but it must stay
    the house §17 describes, share brief 01's plot, and use only model room types:
    the solver's program layer silently drops any type it does not know."""
    loaded = load_demo_brief()
    assert loaded.source == "garh_api.seed.demo"

    # The §17 headline: G+1, 3BHK, a declared car space (the blr parking gate
    # rejects every solver candidate without one).
    assert loaded.data["bedrooms"] == 3, "3BHK"
    assert loaded.data["floorsAboveGround"] == 1, "G+1"
    assert loaded.data["carParking"] >= 1

    rooms = loaded.data["rooms"]
    assert rooms, "a demo brief with no rooms cannot generate plans"
    for room in rooms:
        assert room["type"] in ROOM_TYPES, (
            "%r is not a garh_model room type — the solver would silently drop it" % room["type"]
        )
    # The sleeping-room program agrees with the headline number.
    beds = sum(
        room.get("count", 1)
        for room in rooms
        if room["type"] in ("bedroom", "bedroom_master", "guest_bedroom")
    )
    assert beds == loaded.data["bedrooms"], (beds, loaded.data["bedrooms"])

    # And the corpus sibling still pins the same plot, so the two cannot drift
    # into different houses even though their room programs now differ.
    fixture = next(brief for brief in briefs if brief["id"] == DEMO_BRIEF_CORPUS_SIBLING)
    assert fixture["stratum"]["city"] == "blr"
    assert fixture["stratum"]["floorsAboveGround"] == 1, "G+1"
    assert fixture["data"]["bedrooms"] == 3, "3BHK"
    assert fixture["plot"]["polygon"][2] == {"x": 9144, "y": 12192}, "30 x 40 ft"
    assert fixture["plot"]["roads"][0]["widthMm"] == 9000, "9 m road"


def test_the_corpus_is_ready_for_phase_3(index: dict[str, Any]) -> None:
    """A standing note that the solver assertions are the missing half of §16.

    Phase 3's DoD is "20-brief golden corpus solves <=60s each with >=3 options". That test
    belongs next to the solver; this one exists so the corpus cannot be quietly reduced to
    five briefs before it is written.
    """
    assert index["count"] == REQUIRED_COUNT
    assert index["schemaVersion"] == 1
