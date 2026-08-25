"""§7 elevations: level markers, the height chain, and hidden-line correctness.

What this file proves, and why each one is here rather than left to review:

* **Level markers are the model's own numbers.** Every marker value is checked against
  ``house.levels`` / ``Storey.level`` field by field — plinth, each FFL, each sill, each
  lintel, terrace, parapet top — and the per-storey override coalesce is checked against
  ``garh_model.model.effective_sill_mm``/``effective_lintel_mm``, the model core's own
  helpers. A section and an elevation that disagree about the first-floor FFL is the
  defect this module split exists to prevent.
* **The overall height chain sums exactly.** §7 step 5 is an equality, not a tolerance:
  Σ segments == overall, asserted here and at construction.
* **Far-face openings are excluded.** The north window appears on the north elevation and
  is absent from the south one; the entrance door is the mirror case. This is the most
  visible defect an elevation can carry, so it is pinned in both directions and again for
  a deliberately occluded opening behind a projecting wing.
* **Labels never overlap** (§16's collision-free assertion), at 1:100 and at 1:50.
* **The projection is deterministic** — same model, same bytes — because that is the
  precondition for the SVG/DXF goldens §16 asks for.
* **The dependency-free copies cannot drift.** ``vertical.point_in_ring`` is checked
  against ``garh_model.geometry.polygon_contains``, and the axis table against a
  hand-computed ``ẑ × n̂``.

Runnable two ways, like ``services/drawings/tests/test_schedules.py``::

    pytest -q services/drawings/tests/test_elevations.py     # CI
    python3 services/drawings/tests/test_elevations.py       # this machine (no pytest)

The house is the folded G+1 fixture from
:mod:`services.drawings.elevations.demo_house` — real ops through the real
``garh_model.fold``, so every number below came out of the production path rather than a
literal in a test.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _path in (str(_REPO_ROOT), str(_REPO_ROOT / "apps" / "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from services.dev_stubs import install_worker_dep_stubs  # noqa: E402

STUBBED = install_worker_dep_stubs()

from garh_model.geometry import Pt, polygon_contains  # noqa: E402
from garh_model.model import effective_lintel_mm, effective_sill_mm  # noqa: E402
from services.drawings.dimensions import assert_chains_sum, find_label_collisions  # noqa: E402
from services.drawings.layers import LAYER_NAMES  # noqa: E402
from services.drawings.projection.primitives import (  # noqa: E402
    Polyline,
    by_owner,
    find_unsafe_text,
    primitives_digest,
    validate_primitives,
)
from services.drawings.elevations.callouts import callout_text  # noqa: E402
from services.drawings.elevations.demo_house import (  # noqa: E402
    DEMO_IDS,
    demo_material_names,
    demo_project_doc,
)
from services.drawings.elevations.facade import (  # noqa: E402
    facade_faces,
    footprint_of,
    footprint_rings,
    outward_normal_of,
    visible_openings,
)
from services.drawings.elevations.project import (  # noqa: E402
    ElevationOptions,
    build_all_elevations,
    build_elevation,
    elevation_title,
    true_azimuth_deg,
)
from services.drawings.elevations.vertical import (  # noqa: E402
    DIRECTIONS_4,
    NORMALS,
    U_AXES,
    build_levels,
    format_level_mm,
    height_chain,
    merge_intervals,
    normals_of,
    point_in_ring,
    subtract_intervals,
)

# One fold, reused: folding is the slow part and every test wants the same house.
DOC = demo_project_doc()
HOUSE = DOC.house
OPTIONS = ElevationOptions(material_names=demo_material_names())
ELEVATIONS = build_all_elevations(HOUSE, OPTIONS)


# ---------------------------------------------------------------------------
# Levels
# ---------------------------------------------------------------------------
def test_levels_are_read_from_the_model_not_reconstructed() -> None:
    levels = build_levels(HOUSE)
    assert levels.plinth_mm == HOUSE.levels.plinth_mm
    assert levels.parapet_height_mm == HOUSE.levels.parapet_mm
    assert len(levels.storeys) == len(HOUSE.storeys)
    for index, storey in enumerate(HOUSE.storeys):
        read = levels.storeys[index]
        assert read.storey_id == storey.id
        assert read.ffl_mm == HOUSE.levels.ffl_per_storey_mm[index]
        assert read.ffl_mm == storey.level.ffl_mm
        assert read.height_mm == storey.height_mm
        # The sill/lintel coalesce must agree with the model core's own helpers, which is
        # the only defence against this module's duck typing drifting from the document.
        assert read.sill_above_ffl_mm == effective_sill_mm(HOUSE, storey.id)
        assert read.lintel_above_ffl_mm == effective_lintel_mm(HOUSE, storey.id)
    top = HOUSE.storeys[-1]
    assert levels.terrace_mm == top.level.ffl_mm + top.height_mm
    assert levels.parapet_top_mm == levels.terrace_mm + HOUSE.levels.parapet_mm


def test_level_markers_match_the_model_exactly() -> None:
    """Every marker is a model level; every model level has a marker. No extras."""
    levels = HOUSE.levels
    expected: Dict[int, List[str]] = {0: ["GROUND LVL (NGL)"], levels.plinth_mm: ["PLINTH LVL"]}
    for index, storey in enumerate(HOUSE.storeys):
        ffl = levels.ffl_per_storey_mm[index]
        for value in (
            ffl,
            ffl + effective_sill_mm(HOUSE, storey.id),
            ffl + effective_lintel_mm(HOUSE, storey.id),
        ):
            expected.setdefault(value, [])
    terrace = HOUSE.storeys[-1].level.ffl_mm + HOUSE.storeys[-1].height_mm
    expected.setdefault(terrace, [])
    expected.setdefault(terrace + levels.parapet_mm, [])

    for direction in DIRECTIONS_4:
        found = {marker.level_mm for marker in ELEVATIONS[direction].level_markers}
        assert found == set(expected), "%s: %s != %s" % (
            direction,
            sorted(found),
            sorted(expected),
        )
    # The two labels that must coincide, because the ground FFL *is* the plinth top.
    ground_marker = next(
        m for m in ELEVATIONS["N"].level_markers if m.level_mm == levels.plinth_mm
    )
    assert "PLINTH LVL" in ground_marker.labels
    assert any(label.endswith("FFL") for label in ground_marker.labels)


def test_level_text_is_millimetres_with_a_sign() -> None:
    """§7: dim text is mm regardless of the project's display units (this one is ft-in)."""
    assert DOC.house.meta.units_display == "ft-in"
    assert format_level_mm(0) == "±0"
    assert format_level_mm(3600) == "+3600"
    assert format_level_mm(-300) == "-300"
    marker = next(m for m in ELEVATIONS["N"].level_markers if m.level_mm == 3600)
    assert marker.text().startswith("+3600 ")
    assert "'" not in marker.text() and '"' not in marker.text()


# ---------------------------------------------------------------------------
# The one chain
# ---------------------------------------------------------------------------
def test_height_chain_sums_exactly_and_is_the_only_chain() -> None:
    for direction in DIRECTIONS_4:
        drawing = ELEVATIONS[direction]
        assert len(drawing.chains) == 1, "§7 gives an elevation one chain, not %d" % len(
            drawing.chains
        )
        chain = drawing.chains[0]
        assert_chains_sum(drawing.chains)
        assert chain.sum_of_segments() == chain.overall_mm
        assert chain.inconsistency() == 0
        assert chain.orientation == "vertical"
        # Plinth + every storey height + parapet, in that order.
        expected = (
            [HOUSE.levels.plinth_mm]
            + [storey.height_mm for storey in HOUSE.storeys]
            + [HOUSE.levels.parapet_mm]
        )
        assert [segment.length_mm for segment in chain.segments] == expected
        assert chain.overall_mm == sum(expected)


def test_height_chain_segments_stay_anchored_to_their_storeys() -> None:
    """Anchors are what let §7's annotations survive an edit; they must be real ids."""
    chain = ELEVATIONS["N"].chains[0]
    anchored = [s.anchor_element_id for s in chain.segments if s.anchor_element_id]
    assert anchored == [storey.id for storey in HOUSE.storeys]


def test_chain_still_sums_when_a_storey_height_changes() -> None:
    """The invariant is a property of the builder, not of this fixture's numbers.

    3005 rather than a round number on purpose: an odd storey height is where a chain
    built by halving or averaging would start losing a millimetre. (It also has to stay
    inside the model's ±10mm stair-rise tolerance — 18 × 167 = 3006 — or the op is
    rejected, which is itself the model core doing its job.)
    """
    from garh_model.fold import apply_group
    from garh_model.ops import op

    doc = apply_group(
        DOC, [op("storey.set_height", storeyId=HOUSE.storeys[1].id, heightMm=3005)]
    ).model
    levels = build_levels(doc.house)
    chain = height_chain(levels, chain_id="t", offset_mm=0)
    assert chain.sum_of_segments() == chain.overall_mm
    assert 3005 in [segment.length_mm for segment in chain.segments]
    assert chain.overall_mm == levels.parapet_top_mm
    # And the drawing built from it agrees.
    drawing = build_elevation(doc.house, "S", OPTIONS)
    assert drawing.chains[0].is_consistent()
    assert max(m.level_mm for m in drawing.level_markers) == levels.parapet_top_mm


# ---------------------------------------------------------------------------
# Hidden-line correctness
# ---------------------------------------------------------------------------
def test_far_face_openings_are_excluded() -> None:
    """A window on the north wall must not appear on the south elevation, or vice versa."""
    north_window = DEMO_IDS["gf_win_n"]
    south_door = DEMO_IDS["gf_door"]
    east_window = DEMO_IDS["gf_win_e"]
    west_window = DEMO_IDS["gf_win_w"]

    assert by_owner(ELEVATIONS["N"].primitives, north_window)
    assert not by_owner(ELEVATIONS["S"].primitives, north_window)
    assert not by_owner(ELEVATIONS["E"].primitives, north_window)
    assert not by_owner(ELEVATIONS["W"].primitives, north_window)

    assert by_owner(ELEVATIONS["S"].primitives, south_door)
    assert not by_owner(ELEVATIONS["N"].primitives, south_door)

    assert by_owner(ELEVATIONS["E"].primitives, east_window)
    assert not by_owner(ELEVATIONS["W"].primitives, east_window)
    assert by_owner(ELEVATIONS["W"].primitives, west_window)
    assert not by_owner(ELEVATIONS["E"].primitives, west_window)


def test_every_opening_appears_on_exactly_one_elevation() -> None:
    """Nine openings, each on one face: nothing lost, nothing duplicated."""
    for opening in HOUSE.openings:
        drawn = [d for d in DIRECTIONS_4 if by_owner(ELEVATIONS[d].primitives, opening.id)]
        assert len(drawn) == 1, "%s drawn on %s" % (opening.id, drawn)


def test_visible_openings_reports_what_it_hid() -> None:
    levels = build_levels(HOUSE)
    footprints = footprint_rings(HOUSE)
    normal, u_axis = normals_of("N")
    faces, _ = facade_faces(
        HOUSE,
        direction="N",
        normal=normal,
        u_axis=u_axis,
        footprints=footprints,
        storey_levels={s.storey_id: (s.ffl_mm, s.top_mm) for s in levels.storeys},
    )
    openings, notes = visible_openings(
        HOUSE, faces=faces, u_axis=u_axis, storey_ffl={s.storey_id: s.ffl_mm for s in levels.storeys}
    )
    assert len(openings) == 2, "one north window per storey"
    hidden = len(HOUSE.openings) - len(openings)
    assert any("%d opening(s) hidden" % hidden in note for note in notes)


def test_an_opening_behind_a_nearer_wall_is_occluded() -> None:
    """Depth ordering, not just orientation: a nearer facade hides what is behind it.

    A wing is added in front of the north wall over ``x = 4000..7000`` on both storeys —
    the half the north windows sit in (their centre is x = 5000). The main north wall still
    faces north over the *other* half, so orientation alone would keep its windows on the
    sheet; only the depth test removes them. The wing's own face must still be drawn, and
    the elevation must say what it hid.
    """
    from garh_model.fold import apply_group
    from garh_model.ops import op
    from garh_model.testing import fixed_id

    def pt(x: int, y: int) -> Dict[str, int]:
        return {"x": x, "y": y}

    wing_walls = []
    for storey_key, tag in (("gf", "WGA"), ("ff", "WGB")):
        storey = DEMO_IDS[storey_key]
        for suffix, a, b in (
            ("N", (7000, 11000), (4000, 11000)),
            ("E", (7000, 9000), (7000, 11000)),
            ("W", (4000, 11000), (4000, 9000)),
        ):
            wing_walls.append(
                op(
                    "wall.add",
                    id=fixed_id("wall", tag + suffix),
                    storeyId=storey,
                    a=pt(*a),
                    b=pt(*b),
                    thicknessMm=230,
                    kind="external",
                )
            )
    doc = apply_group(DOC, wing_walls).model
    drawing = build_elevation(doc.house, "N", OPTIONS)
    assert not by_owner(drawing.primitives, DEMO_IDS["gf_win_n"]), "occluded window drawn"
    assert not by_owner(drawing.primitives, DEMO_IDS["ff_win_n"]), "occluded window drawn"
    assert by_owner(drawing.primitives, fixed_id("wall", "WGAN")), "wing face missing"
    assert any("behind a nearer part" in note for note in drawing.notes)
    assert any("Facade is stepped" in note for note in drawing.notes)
    # The south door is on the far face throughout and is hidden for the other reason.
    assert not by_owner(drawing.primitives, DEMO_IDS["gf_door"])


def test_outward_normals_are_found_by_probing_not_by_winding() -> None:
    """Wall direction is user data; outwardness is geometry. Reversing a wall changes
    nothing about which way it faces."""
    from garh_model.fold import apply_group
    from garh_model.ops import op

    footprint = footprint_of(HOUSE, DEMO_IDS["gf"])
    assert footprint is not None
    south = next(w for w in HOUSE.walls if w.id == DEMO_IDS["gf_s"])
    assert outward_normal_of(south, footprint) == NORMALS["S"]
    spine = next(w for w in HOUSE.walls if w.id == DEMO_IDS["gf_spine"])
    assert outward_normal_of(spine, footprint) is None, "an internal wall faces nothing"

    reversed_doc = apply_group(
        DOC,
        [
            op(
                "wall.move",
                wallId=DEMO_IDS["gf_s"],
                a={"x": 7000, "y": 0},
                b={"x": 0, "y": 0},
            )
        ],
    ).model
    flipped = next(w for w in reversed_doc.house.walls if w.id == DEMO_IDS["gf_s"])
    footprint2 = footprint_of(reversed_doc.house, DEMO_IDS["gf"])
    assert footprint2 is not None
    assert outward_normal_of(flipped, footprint2) == NORMALS["S"]
    assert by_owner(build_elevation(reversed_doc.house, "S", OPTIONS).primitives, DEMO_IDS["gf_door"])


# ---------------------------------------------------------------------------
# Axes, titles, and the shape of the output
# ---------------------------------------------------------------------------
def test_u_axis_is_z_cross_n() -> None:
    """``u = ẑ × n̂``, computed by hand for all four directions."""
    for direction in DIRECTIONS_4:
        nx, ny = NORMALS[direction]
        assert U_AXES[direction] == (-ny, nx), direction
    # And the consequence a draughtsman would check: standing north, east is on the left.
    assert U_AXES["N"] == (-1, 0)
    assert U_AXES["S"] == (1, 0)


def test_titles_tell_the_truth_about_north() -> None:
    assert elevation_title("N", 0) == "NORTH ELEVATION"
    assert elevation_title("E", 0) == "EAST ELEVATION"
    # A plot rotated 90° clockwise: the plot-local +Y face genuinely faces west.
    assert true_azimuth_deg("N", 90) == 270
    assert elevation_title("N", 90) == "WEST ELEVATION"
    # A non-cardinal north cannot be renamed, so the azimuth is stated instead.
    title = elevation_title("N", 15)
    assert title == "NORTH ELEVATION (TRUE AZIMUTH 345°)"
    drawing = build_elevation(HOUSE, "N", ElevationOptions(north_deg=15))
    assert any("not square to true north" in note for note in drawing.notes)


def test_primitives_are_valid_and_on_the_nine_layers() -> None:
    for direction in DIRECTIONS_4:
        drawing = ELEVATIONS[direction]
        validate_primitives(drawing.primitives)
        assert not find_unsafe_text(drawing.primitives), "§13: no markup on a sheet"
        for layer, count in drawing.by_layer().items():
            assert layer in LAYER_NAMES, "%s is not one of the nine §7 layers" % layer
            assert count > 0
        # Every silhouette is a closed ring — a renderer must not have to guess.
        for item in drawing.primitives:
            if isinstance(item, Polyline) and item.kind == "elevation-silhouette":
                assert item.closed


def test_labels_never_overlap_at_either_scale() -> None:
    for scale in (100, 50, 200):
        drawings = build_all_elevations(HOUSE, ElevationOptions(scale_denominator=scale))
        for direction, drawing in drawings.items():
            collisions = find_label_collisions(drawing.label_boxes())
            assert not collisions, "1:%d %s: %s" % (scale, direction, collisions[:3])


def test_projection_is_deterministic() -> None:
    first = build_all_elevations(HOUSE, OPTIONS)
    second = build_all_elevations(HOUSE, OPTIONS)
    for direction in DIRECTIONS_4:
        assert primitives_digest(first[direction].primitives) == primitives_digest(
            second[direction].primitives
        )


def test_an_empty_house_draws_nothing_and_says_so() -> None:
    from garh_model.model import empty_project_doc

    drawing = build_elevation(empty_project_doc().house, "N")
    assert drawing.primitives == ()
    assert drawing.notes and "no facade" in drawing.notes[0]


# ---------------------------------------------------------------------------
# Callouts
# ---------------------------------------------------------------------------
def test_callouts_read_the_facade_model_and_stay_on_their_own_elevation() -> None:
    names = demo_material_names()
    cladding = next(c for c in HOUSE.facade.components if c.kind == "cladding_zone")
    text = callout_text("cladding_zone", cladding.params, material_names=names)
    assert "CLADDING" in text
    assert "WPC WOOD-FINISH CLADDING" in text, text
    assert "1200 WD." in text

    # The cladding is on the east wall, so it is called out there and nowhere else.
    east = [
        item.text
        for item in ELEVATIONS["E"].primitives
        if getattr(item, "kind", "") == "material-callout"
    ]
    assert any("CLADDING" in item for item in east)
    for direction in ("N", "S", "W"):
        others = [
            item.text
            for item in ELEVATIONS[direction].primitives
            if getattr(item, "kind", "") == "material-callout"
        ]
        assert not any("CLADDING" in item for item in others), direction
    # The parapet has no host wall, so every elevation carries it.
    for direction in DIRECTIONS_4:
        texts = [
            item.text
            for item in ELEVATIONS[direction].primitives
            if getattr(item, "kind", "") == "material-callout"
        ]
        assert any("PARAPET" in item for item in texts), direction


def test_no_facade_kit_means_no_callouts_and_a_note() -> None:
    from garh_model.fold import apply_group
    from garh_model.ops import op

    bare = apply_group(
        DOC, [op("facade.apply_kit", kitId=None, seed=0, colorwayId=None, components=[])]
    ).model
    drawing = build_elevation(bare.house, "S", OPTIONS)
    assert not [
        item for item in drawing.primitives if getattr(item, "kind", "") == "material-callout"
    ]
    assert any("No facade kit applied" in note for note in drawing.notes)


def test_material_ids_survive_a_missing_catalogue() -> None:
    """A missing catalogue degrades the label, never removes it."""
    cladding = next(c for c in HOUSE.facade.components if c.kind == "cladding_zone")
    assert "WPC-CLADDING" in callout_text("cladding_zone", cladding.params, material_names={})


# ---------------------------------------------------------------------------
# The dependency-free copies
# ---------------------------------------------------------------------------
def test_point_in_ring_agrees_with_the_model_core() -> None:
    """``vertical.point_in_ring`` is a local copy; this is the assertion that pins it."""
    ring = footprint_of(HOUSE, DEMO_IDS["gf"])
    assert ring is not None
    polygon = [Pt(x, y) for x, y in ring]
    probes = [
        (3500, 4500),
        (-500, 4500),
        (7500, 4500),
        (0, 0),
        (100, 100),
        (7114, 9114),
        (3500, -200),
    ]
    for x, y in probes:
        assert point_in_ring(ring, x, y) == polygon_contains(polygon, Pt(x, y)), (x, y)


def test_interval_arithmetic() -> None:
    assert merge_intervals([(0, 10), (10, 20), (30, 40)]) == ((0, 20), (30, 40))
    assert merge_intervals([(5, 5), (0, 3)]) == ((0, 3),)
    assert subtract_intervals([(0, 100)], [(20, 30)]) == ((0, 20), (30, 100))
    assert subtract_intervals([(0, 100)], [(0, 100)]) == ()
    assert subtract_intervals([(0, 100)], [(-10, 10), (90, 110)]) == ((10, 90),)


def test_everything_is_an_integer_millimetre() -> None:
    """No float may reach a primitive: ``canonical_json`` refuses one, and so do we."""
    from services.drawings.projection.primitives import points_of

    for drawing in ELEVATIONS.values():
        for item in drawing.primitives:
            for x, y in points_of(item):
                assert isinstance(x, int) and isinstance(y, int), item
        for chain in drawing.chains:
            assert isinstance(chain.overall_mm, int)
            for segment in chain.segments:
                assert isinstance(segment.length_mm, int)
        for marker in drawing.level_markers:
            assert isinstance(marker.level_mm, int)


def _report() -> None:
    print("\nelevation summary (1:100)")
    for direction in DIRECTIONS_4:
        drawing = ELEVATIONS[direction]
        print(
            "  %-22s %3d primitives  %d markers  chain %d = Σ %d"
            % (
                drawing.name,
                len(drawing.primitives),
                len(drawing.level_markers),
                drawing.chains[0].overall_mm,
                drawing.chains[0].sum_of_segments(),
            )
        )


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
