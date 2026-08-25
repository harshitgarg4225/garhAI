"""§7's section: where the cut goes, what it shows, and what it refuses to invent.

What this file proves, and why each one is here rather than left to review:

* **The chosen line really cuts the stair.** Not "a stair exists somewhere" — the winning
  line's position is inside the stair footprint *and* inside the flight, and the projector
  independently rediscovers the same stair when it draws. §7 says "through stair"; this is
  the assertion that keeps it true after any edit to the scoring weights.
* **The scoring function is a function.** Deterministic, explained, and ordered the way its
  weights say: an along-the-flight cut beats a cross-cut, a cut that also reaches a wet area
  beats one that does not, a cut lying inside a wall is penalised. Each weight is exercised
  by a case that isolates it, so nobody has to trust the table in the docstring.
* **The foundation line is a liability boundary.** Dashed, exactly 900mm below the plinth
  level the model carries, labelled with exactly ``"INDICATIVE — REFER STRUCTURAL"``.
  Character-for-character: a reworded label is a different legal claim.
* **The storey-height chain sums exactly** (§7 step 5), and the level markers are the
  model's own numbers — plus one clearly-labelled INDICATIVE marker for the derived mumty.
* **The stair is drawn only as far as the model describes it.** A dogleg's return flight
  does not exist in the document, so it is not drawn and the omission is in the notes; a
  straight flight is drawn riser by riser, and the profile's top equals FFL + the risers
  actually drawn.
* **The dependency-free copies cannot drift**: the stair footprint against
  ``garh_model.fold.stair_footprint_polygon``, the wet-room set against
  ``garh_model.model.WET_ROOM_TYPES``, the parapet thickness against ``DEFAULTS``.

Runnable two ways::

    pytest -q services/drawings/tests/test_sections.py       # CI
    python3 services/drawings/tests/test_sections.py         # this machine (no pytest)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _path in (str(_REPO_ROOT), str(_REPO_ROOT / "apps" / "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from services.dev_stubs import install_worker_dep_stubs  # noqa: E402

STUBBED = install_worker_dep_stubs()

from garh_model.fold import apply_group, stair_footprint_polygon  # noqa: E402
from garh_model.model import DEFAULTS, WET_ROOM_TYPES  # noqa: E402
from garh_model.ops import op  # noqa: E402
from services.drawings.dimensions import assert_chains_sum, find_label_collisions  # noqa: E402
from services.drawings.layers import A_STAIR, A_TEXT, A_WALL_PART, LAYER_NAMES  # noqa: E402
from services.drawings.projection.primitives import (  # noqa: E402
    Hatch,
    Line,
    Polyline,
    Text,
    by_kind,
    find_unsafe_text,
    points_of,
    primitives_digest,
    validate_primitives,
)
from services.drawings.elevations.demo_house import DEMO_IDS, demo_project_doc  # noqa: E402
from services.drawings.elevations.vertical import (  # noqa: E402
    K_FOUNDATION_LABEL,
    K_FOUNDATION_LINE,
    K_STAIR_PROFILE,
    build_levels,
)
from services.drawings.sections.choose import (  # noqa: E402
    PENALTY_ALONG_WALL,
    SCORE_ALONG_FLIGHT,
    SCORE_THROUGH_FLIGHT,
    SCORE_WET_AREA,
    WET_ROOM_TYPES as SECTION_WET_ROOM_TYPES,
    CutLine,
    choose_section_line,
    score_candidate,
)
from services.drawings.sections.project import (  # noqa: E402
    FOUNDATION_DEPTH_BELOW_PLINTH_MM,
    FOUNDATION_LABEL,
    PARAPET_THICKNESS_MM,
    SectionOptions,
    build_section,
)
from services.drawings.sections.stair import STAIR_VECTORS, stair_geometry  # noqa: E402

DOC = demo_project_doc()
HOUSE = DOC.house
CHOICE = choose_section_line(HOUSE)
RESULT = build_section(HOUSE)
DRAWING = RESULT.drawing


# ---------------------------------------------------------------------------
# Choosing the line
# ---------------------------------------------------------------------------
def test_the_chosen_line_intersects_the_stair() -> None:
    """The §7 requirement, checked three ways: footprint, flight, and the projector."""
    assert CHOICE.best is not None
    line = CHOICE.best.line
    stair = next(s for s in HOUSE.stairs if s.id == CHOICE.best.stair_id)
    geometry = stair_geometry(stair)
    assert line.straddles(geometry.footprint), "%s misses %s" % (line, geometry.footprint)
    assert line.straddles(geometry.flight_rect), "cut misses the flight itself"
    x_lo, y_lo, x_hi, y_hi = geometry.footprint
    if line.axis == "x":
        assert x_lo < line.position_mm < x_hi
    else:
        assert y_lo < line.position_mm < y_hi
    # The projector rediscovers the same stair independently when it draws.
    assert CHOICE.best.stair_id in {g.stair_id for g in RESULT.stairs}
    assert by_kind(DRAWING.primitives, K_STAIR_PROFILE), "no stair drawn on the section"


def test_the_chosen_line_prefers_the_ground_stair_and_a_wet_area() -> None:
    assert CHOICE.best is not None
    ground_storey = HOUSE.storeys[0].id
    chosen_stair = next(s for s in HOUSE.stairs if s.id == CHOICE.best.stair_id)
    assert chosen_stair.storey_id == ground_storey, "the section climbs from the entrance"
    assert CHOICE.best.along_flight
    assert CHOICE.best.wet_room_ids, "§7 asks for one wet area if possible"
    wet_types = {
        r.type for r in HOUSE.rooms if r.id in CHOICE.best.wet_room_ids
    }
    assert wet_types <= set(WET_ROOM_TYPES), wet_types


def test_scores_are_ordered_by_the_documented_weights() -> None:
    """Along-the-flight beats across it; through the flight beats past the landing."""
    stair = next(s for s in HOUSE.stairs if s.storey_id == HOUSE.storeys[0].id)
    geometry = stair_geometry(stair)
    # The stair travels north, so "x" runs along the flight and "y" cuts across it.
    along = score_candidate(HOUSE, CutLine(axis="x", position_mm=4800), geometry)
    across = score_candidate(HOUSE, CutLine(axis="y", position_mm=1500), geometry)
    assert along.along_flight and not across.along_flight
    assert along.score - across.score >= SCORE_ALONG_FLIGHT - SCORE_THROUGH_FLIGHT
    assert dict(along.breakdown)["runs along the stair flight"] == SCORE_ALONG_FLIGHT
    assert dict(along.breakdown)["reaches a wet area"] == SCORE_WET_AREA
    # Through the flight (x 4300..5300) versus past it, over the landing only.
    through = score_candidate(HOUSE, CutLine(axis="x", position_mm=4800), geometry)
    landing_only = score_candidate(HOUSE, CutLine(axis="x", position_mm=6200), geometry)
    assert through.through_flight and not landing_only.through_flight
    assert through.score > landing_only.score


def test_a_cut_running_inside_a_wall_is_penalised() -> None:
    """Cutting lengthwise along a wall shows masonry, not rooms."""
    stair = next(s for s in HOUSE.stairs if s.storey_id == HOUSE.storeys[0].id)
    geometry = stair_geometry(stair)
    # A cut at constant y through the stair that lies on the y=5000 spine wall would be
    # useless; the stair does not reach y=5000, so build the case explicitly on x instead:
    # the cross wall at x=4000 is parallel to an "x" cut.
    inside = score_candidate(HOUSE, CutLine(axis="x", position_mm=4050), geometry)
    clear = score_candidate(HOUSE, CutLine(axis="x", position_mm=4800), geometry)
    assert ("runs lengthwise inside a wall", PENALTY_ALONG_WALL) in inside.breakdown
    assert inside.score < clear.score


def test_choosing_is_deterministic_and_explains_itself() -> None:
    again = choose_section_line(HOUSE)
    assert again.best is not None and CHOICE.best is not None
    assert again.best.line.to_json() == CHOICE.best.line.to_json()
    assert again.best.score == CHOICE.best.score
    assert CHOICE.best.breakdown, "a score with no breakdown cannot be reviewed"
    assert sum(points for _reason, points in CHOICE.best.breakdown) == CHOICE.best.score
    assert any("chosen by score" in note for note in CHOICE.notes)


def test_no_stair_means_no_section_and_an_honest_note() -> None:
    doc = apply_group(
        DOC,
        [op("stair.delete", stairId=DEMO_IDS["ff_stair"]), op("stair.delete", stairId=DEMO_IDS["gf_stair"])],
    ).model
    choice = choose_section_line(doc.house)
    assert choice.best is None
    assert choice.candidates == ()
    assert any("No stair in the model" in note for note in choice.notes)
    result = build_section(doc.house)
    assert result.drawing.primitives == ()
    assert result.line is None


def test_an_explicit_line_overrides_the_chooser() -> None:
    """A firm that wants its own section line gets it, with no scoring run at all."""
    result = build_section(HOUSE, line=CutLine(axis="x", position_mm=4500, label="B"))
    assert result.choice is None
    assert result.line is not None and result.line.position_mm == 4500
    assert result.drawing.name.startswith("SECTION B-B")


def test_cut_line_round_trips_into_a_sheet_viewport() -> None:
    """The endpoints are the form ``sheets.Viewport.section_line`` stores.

    They are also the two points the plan projector draws §7's section marker from, so the
    result hands them over instead of letting the plan recompute them from a sign
    convention it might get backwards.
    """
    assert CHOICE.best is not None
    a, b = CHOICE.best.line.endpoints((0, 0, 7000, 9000))
    assert len(a) == 2 and len(b) == 2
    assert all(isinstance(value, int) for value in a + b)
    if CHOICE.best.line.axis == "x":
        assert a[0] == b[0] == CHOICE.best.line.position_mm
        assert a[1] < 0 < 9000 < b[1], "the cut must run past the building both ways"

    assert RESULT.viewport_line is not None
    va, vb = RESULT.viewport_line
    assert va[0] == vb[0] == CHOICE.best.line.position_mm
    assert va[1] < 0 and vb[1] > 9000
    assert RESULT.to_json()["sectionLine"] == [list(va), list(vb)]


# ---------------------------------------------------------------------------
# The foundation line: §7's liability boundary
# ---------------------------------------------------------------------------
def test_foundation_line_is_dashed_900_below_plinth_and_labelled_exactly() -> None:
    lines = [item for item in by_kind(DRAWING.primitives, K_FOUNDATION_LINE) if isinstance(item, Line)]
    labels = [item for item in by_kind(DRAWING.primitives, K_FOUNDATION_LABEL) if isinstance(item, Text)]
    assert len(lines) == 1, "exactly one indicative foundation line"
    assert len(labels) == 1
    line = lines[0]
    assert line.dashed is True, "§7: dashed"
    assert line.layer == A_WALL_PART
    expected_z = HOUSE.levels.plinth_mm - FOUNDATION_DEPTH_BELOW_PLINTH_MM
    assert line.a[1] == expected_z and line.b[1] == expected_z
    assert FOUNDATION_DEPTH_BELOW_PLINTH_MM == 900
    # Character-for-character. A paraphrase is a different claim about who is liable.
    assert labels[0].text == "INDICATIVE — REFER STRUCTURAL"
    assert labels[0].text == FOUNDATION_LABEL
    assert labels[0].layer == A_TEXT
    assert any(FOUNDATION_LABEL in note for note in DRAWING.notes)


def test_foundation_line_follows_the_plinth_the_model_carries() -> None:
    doc = apply_group(DOC, [op("levels.set", plinthMm=1200)]).model
    drawing = build_section(doc.house).drawing
    line = next(
        item for item in by_kind(drawing.primitives, K_FOUNDATION_LINE) if isinstance(item, Line)
    )
    assert line.a[1] == 1200 - 900 == 300
    assert line.dashed


# ---------------------------------------------------------------------------
# Levels, the chain, and the mumty
# ---------------------------------------------------------------------------
def test_storey_height_chain_sums_exactly() -> None:
    assert len(DRAWING.chains) == 1
    chain = DRAWING.chains[0]
    assert_chains_sum(DRAWING.chains)
    assert chain.sum_of_segments() == chain.overall_mm
    expected = (
        [HOUSE.levels.plinth_mm]
        + [storey.height_mm for storey in HOUSE.storeys]
        + [HOUSE.levels.parapet_mm]
    )
    assert [segment.length_mm for segment in chain.segments] == expected


def test_section_and_elevation_agree_about_every_level() -> None:
    """The reason ``vertical.py`` is shared: two drawings, one set of levels."""
    from services.drawings.elevations.project import build_elevation

    elevation = build_elevation(HOUSE, "S")
    elevation_levels = {m.level_mm for m in elevation.level_markers}
    section_levels = {m.level_mm for m in DRAWING.level_markers}
    derived = {m.level_mm for m in DRAWING.level_markers if "INDICATIVE" in " ".join(m.labels)}
    assert section_levels - derived == elevation_levels
    assert derived, "the mumty top is the one derived level in this fixture"


def test_sill_and_lintel_heights_are_shown() -> None:
    levels = build_levels(HOUSE)
    marker_levels = {m.level_mm for m in DRAWING.level_markers}
    for storey in levels.storeys:
        assert storey.sill_mm in marker_levels
        assert storey.lintel_mm in marker_levels
    # And the cut wall's opening void is bounded by lines at exactly those heights.
    void_lines = [
        item
        for item in DRAWING.primitives
        if isinstance(item, Line) and item.kind == "sill-lintel-line"
    ]
    assert void_lines, "an opening in a cut wall shows its sill and lintel"
    heights = {item.a[1] for item in void_lines}
    assert heights & {storey.sill_mm for storey in levels.storeys}


def test_mumty_is_derived_over_the_stair_and_labelled_indicative() -> None:
    mumty = by_kind(DRAWING.primitives, "mumty")
    assert mumty, "a stair on the top storey means a mumty over it"
    labels = [item.text for item in mumty if isinstance(item, Text)]
    assert labels == ["MUMTY (INDICATIVE)"]
    assert any("Mumty derived" in note for note in DRAWING.notes)
    top = max(m.level_mm for m in DRAWING.level_markers)
    levels = build_levels(HOUSE)
    assert top == levels.terrace_mm + 2100 + 125
    # And it can be switched off.
    off = build_section(HOUSE, options=SectionOptions(include_derived_mumty=False)).drawing
    assert not by_kind(off.primitives, "mumty")
    assert max(m.level_mm for m in off.level_markers) == levels.parapet_top_mm


def test_parapet_is_cut_at_the_two_terrace_edges() -> None:
    parapets = [item for item in by_kind(DRAWING.primitives, "parapet") if isinstance(item, Polyline)]
    assert len(parapets) == 2, "a cut crosses the parapet at both ends of the terrace"
    levels = build_levels(HOUSE)
    for item in parapets:
        zs = {point[1] for point in item.points}
        assert zs == {levels.terrace_mm, levels.parapet_top_mm}
        us = sorted({point[0] for point in item.points})
        assert us[1] - us[0] == PARAPET_THICKNESS_MM
    assert PARAPET_THICKNESS_MM == DEFAULTS.parapet_thickness_mm


def test_slab_void_over_the_stair_well() -> None:
    """The stair well is a hole in the slab, so the cut slab arrives in two pieces."""
    slabs = [
        item
        for item in by_kind(DRAWING.primitives, "slab-edge")
        if isinstance(item, Polyline)
    ]
    levels = build_levels(HOUSE)
    first_floor = levels.storeys[1]
    at_ffl = [
        item
        for item in slabs
        if {point[1] for point in item.points}
        == {first_floor.ffl_mm - first_floor.slab_thickness_mm, first_floor.ffl_mm}
    ]
    assert len(at_ffl) == 2, "the first-floor slab is interrupted by the stair well"


# ---------------------------------------------------------------------------
# The stair, and what the model cannot say
# ---------------------------------------------------------------------------
def test_stair_footprint_matches_the_model_core() -> None:
    """The local mirror of ``fold.stair_footprint_polygon``, pinned."""
    for stair in HOUSE.stairs:
        polygon = stair_footprint_polygon(stair)
        xs = [point.x for point in polygon]
        ys = [point.y for point in polygon]
        assert stair_geometry(stair).footprint == (min(xs), min(ys), max(xs), max(ys))


def test_stair_vectors_match_the_model_core() -> None:
    from garh_model.fold import _STAIR_VECTORS

    for direction, (forward, right) in STAIR_VECTORS.items():
        fx, fy, rx, ry = _STAIR_VECTORS[direction]
        assert (forward, right) == ((fx, fy), (rx, ry)), direction


def test_wet_room_types_match_the_model_core() -> None:
    assert SECTION_WET_ROOM_TYPES == WET_ROOM_TYPES


def test_a_dogleg_draws_its_first_flight_and_says_the_rest_is_not_drawn() -> None:
    geometry = stair_geometry(next(s for s in HOUSE.stairs if s.id == DEMO_IDS["gf_stair"]))
    assert geometry.kind == "dogleg"
    assert geometry.partial
    assert geometry.drawn_risers == 9 and geometry.risers_count == 18
    note = geometry.note()
    assert note is not None and "return flight is not drawn" in note
    assert any("return flight is not drawn" in item for item in DRAWING.notes)

    profiles = [
        item
        for item in by_kind(DRAWING.primitives, K_STAIR_PROFILE)
        if isinstance(item, Polyline)
    ]
    assert len(profiles) == 2, "one profile per storey's flight"
    for item in profiles:
        assert item.layer == A_STAIR
    ground = min(profiles, key=lambda item: min(point[1] for point in item.points))
    ffl = build_levels(HOUSE).storeys[0].ffl_mm
    assert min(point[1] for point in ground.points) == ffl
    # The profile climbs exactly the risers it drew, and no further.
    assert max(point[1] for point in ground.points) == ffl + geometry.drawn_rise_mm


def test_a_straight_flight_is_drawn_riser_by_riser() -> None:
    """With no landing to stop at, every riser is real geometry and all of it is drawn."""
    doc = apply_group(
        DOC,
        [
            op(
                "stair.edit",
                stairId=stair_id,
                patch={"kind": "straight", "landing": None},
            )
            for stair_id in (DEMO_IDS["gf_stair"], DEMO_IDS["ff_stair"])
        ],
    ).model
    stair = next(s for s in doc.house.stairs if s.id == DEMO_IDS["gf_stair"])
    geometry = stair_geometry(stair)
    assert not geometry.partial
    assert geometry.drawn_risers == geometry.risers_count == 18
    assert geometry.note() is None
    result = build_section(doc.house, line=CutLine(axis="x", position_mm=4800))
    profile = next(
        item
        for item in by_kind(result.drawing.primitives, K_STAIR_PROFILE)
        if isinstance(item, Polyline)
        and min(point[1] for point in item.points) == geometry_ffl(doc.house)
    )
    ffl = geometry_ffl(doc.house)
    assert max(point[1] for point in profile.points) == ffl + 18 * stair.riser_mm
    # A step is a tread then a riser: 2 points per riser, plus the starting point.
    assert len(profile.points) == 1 + 2 * 18
    assert not any("return flight" in note for note in result.drawing.notes)


def geometry_ffl(house: Any) -> int:
    return build_levels(house).storeys[0].ffl_mm


def test_a_cross_cut_flight_is_dashed_and_declared() -> None:
    """When the only cut available crosses the flight, say so rather than fake a profile."""
    stair = next(s for s in HOUSE.stairs if s.id == DEMO_IDS["gf_stair"])
    geometry = stair_geometry(stair)
    result = build_section(HOUSE, line=CutLine(axis="y", position_mm=1500))
    profiles = [
        item
        for item in by_kind(result.drawing.primitives, K_STAIR_PROFILE)
        if isinstance(item, Polyline)
    ]
    assert profiles and all(item.dashed for item in profiles)
    assert any("cut across its flight" in note for note in result.drawing.notes)
    assert geometry.direction in STAIR_VECTORS


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------
def test_primitives_are_valid_hatched_and_on_the_nine_layers() -> None:
    validate_primitives(DRAWING.primitives)
    assert not find_unsafe_text(DRAWING.primitives)
    for layer in DRAWING.by_layer():
        assert layer in LAYER_NAMES
    hatches = [item for item in DRAWING.primitives if isinstance(item, Hatch)]
    assert hatches, "a section without poché is a diagram, not a section"
    outlines = [
        item
        for item in DRAWING.primitives
        if isinstance(item, Polyline)
        and item.kind in ("wall-face", "plinth", "parapet", "mumty", "slab-edge")
    ]
    assert len(hatches) == len(outlines), "every cut solid gets exactly one hatch: %d vs %d" % (
        len(hatches),
        len(outlines),
    )


def test_labels_never_overlap_at_either_scale() -> None:
    for scale in (100, 50, 200):
        drawing = build_section(HOUSE, options=SectionOptions(scale_denominator=scale)).drawing
        collisions = find_label_collisions(drawing.label_boxes())
        assert not collisions, "1:%d: %s" % (scale, collisions[:3])


def test_section_is_deterministic() -> None:
    first = build_section(HOUSE).drawing
    second = build_section(HOUSE).drawing
    assert primitives_digest(first.primitives) == primitives_digest(second.primitives)


def test_everything_is_an_integer_millimetre() -> None:
    for item in DRAWING.primitives:
        for x, y in points_of(item):
            assert isinstance(x, int) and isinstance(y, int), item
    assert isinstance(DRAWING.chains[0].overall_mm, int)


def _report() -> None:
    assert CHOICE.best is not None
    print("\nsection summary (1:100)")
    print(
        "  cut          : %s at %d, looking %s (score %d of %d candidates)"
        % (
            CHOICE.best.line.axis,
            CHOICE.best.line.position_mm,
            CHOICE.best.line.looking,
            CHOICE.best.score,
            len(CHOICE.candidates),
        )
    )
    print("  primitives   : %d" % len(DRAWING.primitives))
    print("  levels       : %s" % ", ".join(str(m.level_mm) for m in DRAWING.level_markers))
    print(
        "  chain        : %s = %d"
        % (
            " + ".join(str(s.length_mm) for s in DRAWING.chains[0].segments),
            DRAWING.chains[0].overall_mm,
        )
    )
    for note in DRAWING.notes:
        print("  note         : %s" % note)


if __name__ == "__main__":  # pragma: no cover
    import traceback

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS %s" % name)
            except Exception:  # noqa: BLE001
                failures += 1
                print("FAIL %s" % name)
                traceback.print_exc()
    _report()
    print(
        "\n%d test(s) failed. Stubbed dependencies: %s"
        % (failures, ", ".join(STUBBED) or "none")
    )
    sys.exit(1 if failures else 0)
