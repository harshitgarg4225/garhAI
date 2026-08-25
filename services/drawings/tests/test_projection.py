"""Tests for the §7 plan projection: counts, layers, breaks, rotation, formatting.

    python "services/drawings/tests/test_projection.py"     # no pytest needed
    pytest services/drawings/tests/test_projection.py        # in CI

The bootstrap at the top installs stand-ins for the worker dependencies that are absent
on a bare machine (``structlog``/``pydantic``, imported at module scope by
``services.common`` and therefore by ``services/drawings/__init__.py``). It mirrors
``services/solver/tests/conftest.py``; a real installed package always wins, so this is
inert in Docker and in CI. It is also what lets the file run under a plain interpreter,
which matters here — the projection engine is pure integer arithmetic and there is no
excuse for it to be untested on a machine that cannot install ezdxf.

WHAT THESE TESTS ARE FOR
------------------------
Every one of them pins a claim §7 makes, in the form a failure can be read from:

* the layer a thing lands on (the contract with AutoCAD);
* openings genuinely **breaking** the wall, with the split summing exactly;
* the north arrow rotating with the plot, not with something that only looks like it;
* label text formatted by the model core's formatter, not by a local ``%.1f``;
* stair geometry agreeing with ``garh_model.fold``, so the drawing and the slab void
  cannot disagree;
* the door swing matching the on-screen canvas, so a door does not swing one way in the
  editor and the other on the sheet.
"""

from __future__ import annotations

import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_APPS_API = os.path.join(_REPO_ROOT, "apps", "api")
for _path in (_REPO_ROOT, _APPS_API):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from services.dev_stubs import install_worker_dep_stubs  # noqa: E402

STUBBED = install_worker_dep_stubs()

from garh_model.fold import apply_group, stair_footprint_polygon  # noqa: E402
from garh_model.model import DEFAULTS, Stair  # noqa: E402
from garh_model.ops import op  # noqa: E402
from garh_model.testing import (  # noqa: E402
    FIXTURE_IDS,
    fixed_id,
    make_two_room_plan_with_openings,
)
from garh_model.units import format_sqft, round_half_away_from_zero  # noqa: E402
from services.drawings.dimensions import DEFAULT_DIM_TO_JAMB  # noqa: E402
from services.drawings.layers import (  # noqa: E402
    A_AREA,
    A_DIM,
    A_DOOR,
    A_STAIR,
    A_TEXT,
    A_WALL,
    A_WALL_PART,
    A_WIND,
    LAYER_NAMES,
)
from services.drawings.projection import (  # noqa: E402
    Arc,
    Hatch,
    Line,
    PlanOptions,
    Polyline,
    PrimitiveError,
    SectionMarker,
    Text,
    by_kind,
    by_layer,
    by_owner,
    count_by_kind,
    count_by_layer,
    find_unsafe_text,
    north_arrow,
    opening_dim_stations,
    point,
    primitives_digest,
    project_plan,
    project_plan_detail,
    round_half_away,
    sanitise_text,
    split_span,
    stair_symbol,
    style_of,
    validate_primitives,
)
from services.drawings.projection.plan import DEFAULT_DIM_TO_JAMB as PLAN_DIM_TO_JAMB  # noqa: E402
from services.drawings.projection.symbols import STAIR_VECTORS  # noqa: E402
from services.drawings.projection.walls import (  # noqa: E402
    Span,
    clipped_gap_total,
    opening_span,
    wall_bands,
)

#: The shared 2-room fixture: 6.0 × 4.0m envelope, 230 external / 115 spine, a 900 door
#: on the south wall and a 1200 window on the west wall. Same document the model core's
#: own tests and the seed script use.
STOREY_ID = FIXTURE_IDS["groundStorey"]
WALL_SOUTH = FIXTURE_IDS["wallSouth"]
WALL_SPINE = FIXTURE_IDS["wallSpine"]
DOOR = FIXTURE_IDS["doorMain"]
WINDOW = FIXTURE_IDS["windowWest"]


def two_room_house():
    return make_two_room_plan_with_openings().house


# ---------------------------------------------------------------------------
# The rounding mirror
# ---------------------------------------------------------------------------
def test_round_half_away_agrees_with_the_model_core():
    """The projection's copy of the rounding rule is not allowed to drift."""
    values = [-2.5, -1.5, -0.5, -0.4, 0.0, 0.4, 0.5, 1.5, 2.5, 57.5, -57.5, 1234.5]
    for value in values:
        assert round_half_away(value) == round_half_away_from_zero(value), value
    # 115mm walls: the two faces must be the same distance from the centreline.
    assert round_half_away(57.5) == 58
    assert round_half_away(-57.5) == -58


# ---------------------------------------------------------------------------
# Counts and layers on the known 2-room plan
# ---------------------------------------------------------------------------
def test_two_room_plan_layer_assignment():
    """Every primitive lands on one of the nine §7 layers, and on the right one."""
    house = two_room_house()
    primitives = project_plan(house, STOREY_ID, 100)
    validate_primitives(primitives)

    counts = count_by_layer(primitives)
    for layer in counts:
        assert layer in LAYER_NAMES, layer

    # Walls, jambs and poché on A-WALL; the 115 spine is a full-height internal wall, so
    # it is A-WALL too. A-WALL-PART is for parapets/columns/balconies — none here.
    assert A_WALL in counts
    assert A_WALL_PART not in counts
    assert counts[A_DOOR] == 4, "door: 2 jambs + leaf + swing arc"
    assert counts[A_WIND] == 5, "window: 2 jambs + 3 lines (§7 triple line)"
    assert counts[A_AREA] == 2, "one dashed boundary per detected room"
    assert A_STAIR not in counts, "the fixture has no stair"
    # A-TEXT holds two label lines per room, the level text, and the north symbol —
    # which is two primitives, the dart and its 'N' (see the layer table in
    # ``projection/__init__.py`` for why a drawn symbol sits on a text layer).
    assert counts[A_TEXT] == 2 * 2 + 1 + 2
    # A-DIM holds the level triangle only: no chains (that is the dim engine) and no
    # grid (no columns).
    assert counts[A_DIM] == 1


def test_two_room_plan_primitive_kinds():
    """The kind census is the readable form of "did the projector draw everything?"."""
    house = two_room_house()
    kinds = count_by_kind(project_plan(house, STOREY_ID, 100))

    # 5 walls × 2 faces = 10 face runs, +1 extra run each on the two faces of the two
    # walls that host an opening (south, west) = 14.
    assert kinds["wall-face"] == 14
    # Closed envelope + T-junctions: every wall end is mitred against a neighbour, so
    # nothing gets an end cap.
    assert "wall-end" not in kinds
    # Poché breaks at the openings too: 4 external walls, two of them split in two.
    assert kinds["wall-hatch"] == 6
    assert kinds["wall-jamb"] == 4
    assert kinds["door-leaf"] == 1
    assert kinds["door-swing"] == 1
    assert kinds["window-glazing"] == 3
    assert kinds["room-name"] == 2
    assert kinds["room-area"] == 2
    assert kinds["room-outline"] == 2
    assert kinds["level-marker"] == 1
    assert kinds["north-arrow"] == 1


def test_internal_wall_is_trimmed_at_the_external_wall_face():
    """The spine wall must stop at the external wall's inner face, not at its centreline.

    §7 does not spell this out; a drawing that gets it wrong shows two stray lines inside
    the poché of every external wall it meets, which is the first thing a draughtsman
    notices and the reason plans get sent back.
    """
    house = two_room_house()
    bands = {band.wall.id: band for band in wall_bands(house.walls, house.openings)}
    spine = bands[WALL_SPINE]
    # South and north walls are 230 thick, so their inner faces are 115 in from the
    # spine's ends (0 and 4000).
    assert spine.extents.left_start_mm == 115
    assert spine.extents.right_start_mm == 115
    assert spine.extents.left_end_mm == 4000 - 115
    assert spine.extents.right_end_mm == 4000 - 115

    south = bands[WALL_SOUTH]
    # The south wall's outer face runs long to close the corner; the inner face stops.
    assert south.extents.right_start_mm == -115
    assert south.extents.left_start_mm == 115


# ---------------------------------------------------------------------------
# Openings BREAK the wall — the headline invariant
# ---------------------------------------------------------------------------
def test_opening_breaks_the_host_wall_lines():
    """The door's wall is split in two per face; no line is drawn across the opening."""
    house = two_room_house()
    detail = project_plan_detail(house, STOREY_ID, 100)
    south = next(band for band in detail.bands if band.wall.id == WALL_SOUTH)
    span = opening_span(south.frame, next(o for o in house.openings if o.id == DOOR))
    assert span == Span(1050, 1950), "900 door centred on offset 1500"

    faces = [
        item
        for item in by_owner(detail.primitives, WALL_SOUTH)
        if isinstance(item, Line) and item.kind == "wall-face"
    ]
    assert len(faces) == 4, "two faces, each split in two by the door"

    # The south wall runs along +X at y=0 with faces at y=±115. No face segment may
    # overlap the door's x range — that is the difference between a break and a line
    # with something painted over it.
    for face in faces:
        x_low, x_high = sorted((face.a[0], face.b[0]))
        assert x_high <= span.start_mm or x_low >= span.end_mm, (
            "face segment %s..%s crosses the opening %s" % (x_low, x_high, span)
        )

    # And the gap really is where the door is: the two inner segments end/start at it.
    ends = sorted({face.b[0] for face in faces} | {face.a[0] for face in faces})
    assert span.start_mm in ends and span.end_mm in ends


def test_every_wall_face_split_sums_exactly():
    """§7 step 5's discipline, applied to the wall break: parts == whole, exactly.

    Integer millimetres are what make this an equality. A tolerance here would hide the
    kind of half-millimetre leak that shows up 40 walls later as a 20mm error in an
    overall dimension.
    """
    house = two_room_house()
    for band in wall_bands(house.walls, house.openings):
        for start, end in (
            (band.extents.left_start_mm, band.extents.left_end_mm),
            (band.extents.right_start_mm, band.extents.right_end_mm),
        ):
            runs = split_span(start, end, band.gaps)
            solid = sum(run.length_mm for run in runs)
            gaps = clipped_gap_total(start, end, band.gaps)
            assert solid + gaps == end - start, (band.wall.id, start, end, solid, gaps)
            # Runs are ordered, disjoint and inside the extent.
            previous = start
            for run in runs:
                assert start <= run.start_mm < run.end_mm <= end
                assert run.start_mm >= previous
                previous = run.end_mm


def test_split_span_is_exhaustive_on_random_gaps():
    """The split holds for overlapping, touching, out-of-range and duplicate gaps.

    Seeded, so a failure is reproducible — ``fold``'s property tests take the same line.
    """
    rng = random.Random(20260821)
    for _ in range(400):
        start = rng.randint(-500, 500)
        end = start + rng.randint(1, 8000)
        gaps = []
        for _index in range(rng.randint(0, 6)):
            gap_start = rng.randint(start - 600, end + 600)
            gaps.append(Span(gap_start, gap_start + rng.randint(1, 1500)))
        runs = split_span(start, end, gaps)
        solid = sum(run.length_mm for run in runs)
        assert solid + clipped_gap_total(start, end, gaps) == end - start
        for run in runs:
            assert start <= run.start_mm < run.end_mm <= end
            for gap in gaps:
                assert run.end_mm <= gap.start_mm or run.start_mm >= gap.end_mm


def test_window_is_a_triple_line_and_a_ventilator_is_distinct():
    """§7: "window triple line". A ventilator gets the same frame with a dashed centre."""
    house = two_room_house()
    vent_house = apply_group(
        make_two_room_plan_with_openings(),
        [
            op(
                "opening.add",
                id=fixed_id("opening", "V9"),
                wallId=FIXTURE_IDS["wallNorth"],
                kind="ventilator",
                widthMm=DEFAULTS.ventilator_width_mm,
                heightMm=DEFAULTS.ventilator_height_mm,
                sillMm=DEFAULTS.ventilator_sill_mm,
                offsetMm=3000,
                swing="in-left",
            )
        ],
    ).model.house

    window_lines = by_kind(project_plan(house, STOREY_ID, 100), "window-glazing")
    assert len(window_lines) == 3
    assert all(not line.dashed for line in window_lines)

    vent_lines = by_kind(project_plan(vent_house, STOREY_ID, 100), "ventilator-glazing")
    assert len(vent_lines) == 3
    assert [line.dashed for line in vent_lines].count(True) == 1, (
        "the centre line is dashed — a ventilator sits above the cut plane"
    )


def test_door_swing_matches_the_canvas_convention():
    """Hinge hand and swing side must match ``planGeometry.ts``'s ``openingSymbol``.

    The canvas hinges at the ``a`` end for ``*-left`` and swings to the ``+n`` side for
    ``in-*``, where ``n`` is the left normal of ``a→b``. A sheet that disagrees shows a
    door opening into a wall the architect just watched it clear on screen.
    """
    house = two_room_house()
    primitives = project_plan(house, STOREY_ID, 100)
    leaf = by_kind(primitives, "door-leaf")[0]
    arc = by_kind(primitives, "door-swing")[0]

    # South wall: a=(0,0), b=(6000,0) → u=+X, left normal n=+Y (the building's inside).
    # Door is 'in-left': hinge at the a-side jamb (x=1050), leaf swings to +Y.
    assert leaf.a == (1050, 0)
    assert leaf.b == (1050, 900)
    assert arc.centre == (1050, 0)
    assert arc.radius_mm == 900
    # CCW from the closed position (+X, 0°) to the open leaf (+Y, 90°).
    assert (arc.start_deg, arc.end_deg) == (0, 90)


def test_door_swing_flips_with_the_swing_field():
    """``out-right`` hinges at the b end and swings to the −n side."""
    doc = apply_group(
        make_two_room_plan_with_openings(),
        [op("opening.flip", openingId=DOOR, swing="out-right")],
    ).model
    primitives = project_plan(doc.house, STOREY_ID, 100)
    leaf = by_kind(primitives, "door-leaf")[0]
    arc = by_kind(primitives, "door-swing")[0]
    assert leaf.a == (1950, 0), "hinge moved to the b-side jamb"
    assert leaf.b == (1950, -900), "leaf swings to the outside"
    # Closed position points back along the wall (−X, 180°); open is −Y (270°).
    assert (arc.start_deg, arc.end_deg) == (180, 270)


# ---------------------------------------------------------------------------
# North arrow
# ---------------------------------------------------------------------------
def test_north_arrow_rotates_with_the_plot():
    """``north_deg`` is measured **clockwise from +Y** (§3), so 90° points east."""
    style = style_of(100)
    centre = point(0, 0)
    length = style.north_arrow_length_mm

    tips = {}
    for deg in (0, 90, 180, 270):
        arrow = north_arrow(centre, deg, style)
        polyline = next(item for item in arrow if isinstance(item, Polyline))
        tips[deg] = polyline.points[0]

    assert tips[0] == (0, length), "north up"
    assert tips[90] == (length, 0), "north east — not west, and not still up"
    assert tips[180] == (0, -length)
    assert tips[270] == (-length, 0)

    # A non-cardinal angle must land in the right quadrant with both components moving.
    tip_20 = next(
        item for item in north_arrow(centre, 20, style) if isinstance(item, Polyline)
    ).points[0]
    assert tip_20[0] > 0 and tip_20[1] > 0
    assert tip_20[0] < tip_20[1], "20° from north is mostly north"


def test_north_label_sits_beyond_the_tip():
    style = style_of(100)
    label = next(item for item in north_arrow(point(0, 0), 0, style) if isinstance(item, Text))
    assert label.text == "N"
    assert label.position[1] > style.north_arrow_length_mm


# ---------------------------------------------------------------------------
# Labels and text
# ---------------------------------------------------------------------------
def test_room_label_text_formatting():
    """§7: "room label block (name, area in sqft one decimal)" — via the shared formatter."""
    doc = make_two_room_plan_with_openings()
    rooms = sorted(doc.house.rooms, key=lambda room: room.polygon[0].x)
    doc = apply_group(
        doc,
        [op("room.assign", roomId=rooms[0].id, type="living_dining", name="Living & Dining")],
    ).model
    primitives = project_plan(doc.house, STOREY_ID, 100)

    names = sorted(item.text for item in by_kind(primitives, "room-name"))
    assert "LIVING & DINING" in names, "explicit name wins, upper-cased for the drawing"
    assert any(name.startswith("ROOM ") for name in names), (
        "an unassigned room falls back to the model core's 'Room N'"
    )

    areas = by_kind(primitives, "room-area")
    for text in areas:
        room = next(room for room in doc.house.rooms if room.id == text.owner_id)
        assert text.text == format_sqft(room.area_mm2, 1)
        assert text.text.endswith(" sq ft")
        assert len(text.text.split(".")[1].split(" ")[0]) == 1, "exactly one decimal"
    assert "114.8 sq ft" in {text.text for text in areas}, (
        "the fixture's 10,661,560mm² room, formatted the way the UI formats it"
    )


def test_text_is_sanitised_and_the_unsafe_check_is_an_invariant():
    """§13. Angle brackets never reach a primitive, so the backstop stays empty."""
    doc = make_two_room_plan_with_openings()
    room = doc.house.rooms[0]
    doc = apply_group(
        doc,
        [
            op(
                "room.assign",
                roomId=room.id,
                type="bedroom",
                name="<script>alert('x')</script> Bed\nroom",
            )
        ],
    ).model
    primitives = project_plan(doc.house, STOREY_ID, 100)
    assert find_unsafe_text(primitives) == ()
    label = next(item for item in by_kind(primitives, "room-name") if item.owner_id == room.id)
    assert "<" not in label.text and ">" not in label.text
    assert "\n" not in label.text

    assert sanitise_text("a\tb\nc") == "a b c"
    assert sanitise_text("x" * 200).endswith("…")
    assert len(sanitise_text("x" * 200)) == 120


def test_validation_rejects_floats_bad_layers_and_degenerates():
    """The invariants are assertions, not comments (§16)."""
    good = Line(layer=A_WALL, a=(0, 0), b=(100, 0))
    validate_primitives([good])

    for bad in (
        Line(layer="A-NOPE", a=(0, 0), b=(1, 0)),
        Line(layer=A_WALL, a=(0.5, 0), b=(1, 0)),
        Line(layer=A_WALL, a=(0, 0), b=(0, 0)),
        Text(layer=A_TEXT, position=(0, 0), text="", height_mm=250),
        Text(layer=A_TEXT, position=(0, 0), text="x", height_mm=0),
        Arc(layer=A_DOOR, centre=(0, 0), radius_mm=0, start_deg=0, end_deg=90),
        Hatch(layer=A_WALL, boundary=((0, 0), (1, 1))),
        Polyline(layer=A_AREA, points=((0, 0),)),
    ):
        try:
            validate_primitives([bad])
        except PrimitiveError:
            continue
        raise AssertionError("validate_primitives accepted %r" % (bad,))


# ---------------------------------------------------------------------------
# Stairs
# ---------------------------------------------------------------------------
def _stair(direction: str, kind: str = "straight") -> Stair:
    return Stair(
        id=fixed_id("stair", "ST1"),
        storey_id=STOREY_ID,
        kind=kind,
        origin=point_to_pt(1000, 1000),
        direction=direction,
        riser_mm=167,
        tread_mm=275,
        width_mm=900,
        risers_count=18,
        landing=None,
    )


def point_to_pt(x: int, y: int):
    from garh_model.geometry import Pt

    return Pt(x, y)


def test_stair_footprint_matches_the_model_core_in_every_direction():
    """The drawing's stair frame must be the model core's, or the slab void moves.

    ``STAIR_VECTORS`` mirrors ``garh_model.fold._STAIR_VECTORS``; this reproduces the
    footprint from the mirror and compares it with the real function.
    """
    style = style_of(100)
    for direction in ("N", "E", "S", "W"):
        stair = _stair(direction)
        expected = [(p.x, p.y) for p in stair_footprint_polygon(stair)]
        outline = next(
            item for item in stair_symbol(stair, style) if item.kind == "stair-outline"
        )
        assert sorted(outline.points) == sorted(expected), direction

        fx, fy, rx, ry = STAIR_VECTORS[direction]
        going = (stair.risers_count - 1) * stair.tread_mm
        corner = (
            stair.origin.x + fx * going + rx * stair.width_mm,
            stair.origin.y + fy * going + ry * stair.width_mm,
        )
        assert corner in expected, "the mirrored vectors reach the far corner"


def test_stair_arrow_and_up_label():
    """§7: "stairs w/ arrow + ``UP 15R``" — the count comes from the model."""
    style = style_of(100)
    stair = _stair("N")
    primitives = stair_symbol(stair, style)
    label = next(item for item in primitives if item.kind == "stair-label")
    assert label.text == "UP 18R"
    assert label.layer == A_TEXT

    arrow = [item for item in primitives if item.kind == "stair-arrow"]
    assert len(arrow) == 3, "shaft + two barbs"
    shaft = arrow[0]
    assert shaft.a[0] == shaft.b[0], "travel is north, so the shaft is vertical"
    assert shaft.b[1] > shaft.a[1], "the arrow points the way up"

    treads = [item for item in primitives if item.kind == "stair-tread"]
    assert len(treads) == stair.risers_count - 2, (
        "18 risers → 17 treads → 16 internal nosing lines; the outer two are the outline"
    )
    assert all(item.layer == A_STAIR for item in primitives if item.kind != "stair-label")


def test_non_straight_stair_draws_no_invented_treads():
    """A dogleg's footprint is a bounding box in the model, so no treads are drawn."""
    style = style_of(100)
    primitives = stair_symbol(_stair("N", kind="dogleg"), style)
    assert [item for item in primitives if item.kind == "stair-tread"] == []
    assert any(item.kind == "stair-outline" for item in primitives)
    assert any(item.kind == "stair-label" for item in primitives)


# ---------------------------------------------------------------------------
# Section markers, scale-dependence, ordering, digests
# ---------------------------------------------------------------------------
def test_section_marker_is_drawn_where_the_section_is_cut():
    house = two_room_house()
    marker = SectionMarker(a=(3000, -1000), b=(3000, 5000), label="A")
    primitives = project_plan(
        house, STOREY_ID, 100, options=PlanOptions(section_markers=(marker,))
    )
    line = by_kind(primitives, "section-line")[0]
    assert line.a[0] == line.b[0] == 3000
    assert line.dashed
    assert line.layer == A_DIM
    labels = by_kind(primitives, "section-label")
    assert len(labels) == 2 and {item.text for item in labels} == {"A"}


def test_symbol_sizes_are_paper_scaled_but_walls_are_not():
    """The §7 trap: geometry is model mm, symbols are paper mm. Both at once."""
    house = two_room_house()
    at_100 = project_plan(house, STOREY_ID, 100)
    at_50 = project_plan(house, STOREY_ID, 50)

    def face_length(primitives):
        faces = [
            item
            for item in by_layer(primitives, A_WALL)
            if isinstance(item, Line) and item.kind == "wall-face"
        ]
        return max(abs(item.b[0] - item.a[0]) + abs(item.b[1] - item.a[1]) for item in faces)

    assert face_length(at_100) == face_length(at_50), "the building does not change size"

    name_100 = by_kind(at_100, "room-name")[0].height_mm
    name_50 = by_kind(at_50, "room-name")[0].height_mm
    assert name_100 == 250 and name_50 == 125, "2.5mm of paper at both scales"


def test_paint_order_is_hatches_then_lines_then_text():
    """List order is draw order: poché behind, labels in front."""
    primitives = project_plan(two_room_house(), STOREY_ID, 100)
    ranks = []
    for item in primitives:
        if isinstance(item, Hatch):
            ranks.append(0)
        elif isinstance(item, Text):
            ranks.append(2)
        else:
            ranks.append(1)
    assert ranks == sorted(ranks)


def test_projection_is_deterministic_and_hashes_stably():
    """A generated golden: the digest of the shared fixture at 1:100.

    Generated by running this code — never hand-written. If a projector change is
    intended, regenerate this line in the same commit and say why (golden rule 10).
    """
    house = two_room_house()
    first = project_plan(house, STOREY_ID, 100)
    second = project_plan(two_room_house(), STOREY_ID, 100)
    assert primitives_digest(first) == primitives_digest(second)
    assert primitives_digest(first) == (
        "786364c65c4ec943c6e490f6996c13cce5c232d5528ce2c5cb6ae245b31af120"
    )


def test_dim_to_jamb_flag_agrees_with_the_dimension_engine():
    """One flag, one meaning: §7's ``dimToJamb`` firm preference."""
    assert PLAN_DIM_TO_JAMB == DEFAULT_DIM_TO_JAMB is False

    house = two_room_house()
    detail = project_plan_detail(house, STOREY_ID, 100)
    south = next(band for band in detail.bands if band.wall.id == WALL_SOUTH)

    centre = opening_dim_stations(south, house.openings)
    assert centre == ((DOOR, 1500),), "centreline by default"
    jambs = opening_dim_stations(south, house.openings, dim_to_jamb=True)
    assert jambs == ((DOOR, 1050), (DOOR, 1950)), "the jambs the wall was actually broken at"


def test_projection_of_an_unknown_storey_is_empty_not_an_error():
    """An empty sheet is a legitimate state (a storey with nothing built yet)."""
    assert project_plan(two_room_house(), "storey_does_not_exist", 100) == ()


TESTS = [value for name, value in sorted(globals().items()) if name.startswith("test_")]


def _main() -> int:
    """Run every test without pytest, and report honestly what was stubbed."""
    failures = []
    for test in TESTS:
        try:
            test()
        except Exception as exc:  # noqa: BLE001 - a test runner reports everything
            failures.append((test.__name__, exc))
            print("FAIL %s: %s: %s" % (test.__name__, type(exc).__name__, exc))
        else:
            print("ok   %s" % test.__name__)
    print("")
    print(
        "%d passed, %d failed (stubbed: %s)"
        % (len(TESTS) - len(failures), len(failures), ", ".join(STUBBED) or "nothing")
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
