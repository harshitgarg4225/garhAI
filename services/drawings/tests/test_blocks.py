"""Tests for the parametric 2D block library.

Every geometric claim here is measured, not counted. "It returned four primitives" is
the kind of assertion that passed while the furniture layer was invisible to clicks and
while 83 rules reported ``not_applicable``; what a door symbol has to satisfy is that
its swing arc is centred on the hinge and sweeps exactly the leaf, and that is what is
asserted.

Each such assertion is paired with a ``test_negative_control_*`` that feeds it the
plausible wrong answer — the swing centred on the opening instead of the hinge, the run
computed from the riser count instead of the tread count, the north bearing read as
``(cos, sin)`` instead of ``(sin, cos)`` — and proves the gate rejects it. A green check
that cannot go red is worse than no check.

Runs two ways, like every other test in this package: under pytest, and under
``python3 services/drawings/tests/test_blocks.py`` on a machine with nothing installed.
The block library is pure integer arithmetic, so its test must not need a toolchain.
"""

from __future__ import annotations

import inspect
import itertools
import json
import math
import os
import sys
from collections.abc import Callable, Sequence
from types import ModuleType
from typing import Any

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from services.drawings.blocks import (  # noqa: E402
    doors,
    electrical,
    sanitary,
    site,
    stairs,
    windows,
)
from services.drawings.blocks.base import (  # noqa: E402
    Insertion,
    arc_endpoint,
    block_extent,
    label_text,
    paper_mm_to_model_mm,
    place,
    readable_rotation,
    round_half_away,
    span,
)
from services.drawings.blocks.catalog import (  # noqa: E402
    BLOCK_NAMES,
    BLOCK_REGISTRY,
    BlockSpec,
    block_spec,
    build_block,
)
from services.drawings.layers import A_TITL, A_WALL, A_WALL_PART, A_WIND  # noqa: E402
from services.drawings.render.primitives import (  # noqa: E402
    STYLE_DASHED,
    STYLE_HIDDEN,
    STYLE_SOLID,
    Arc,
    Circle,
    Hatch,
    Line,
    Polyline,
    Primitive,
    Pt2,
    Text,
)

with open(
    os.path.join(_REPO_ROOT, "fixtures", "catalog", "furniture.json"), encoding="utf-8"
) as _catalogue_file:
    CATALOGUE = json.load(_catalogue_file)
CATALOGUE_BY_ID = {item["id"]: item for item in CATALOGUE}

BLOCK_MODULES: tuple[ModuleType, ...] = (doors, windows, stairs, sanitary, electrical, site)


# ===========================================================================
# helpers — no pytest, so this file runs on a bare interpreter too
# ===========================================================================
def _expect_error(
    kind: type[BaseException], call: Callable[[], Any], *, contains: str = ""
) -> None:
    try:
        call()
    except kind as exc:
        assert contains in str(exc), "expected %r in %r" % (contains, str(exc))
        return
    raise AssertionError("expected %s, nothing was raised" % kind.__name__)


def _anchor_points(primitives: Sequence[Primitive]) -> list[Pt2]:
    """Every point a primitive is positioned by, in emission order."""
    out: list[Pt2] = []
    for prim in primitives:
        if isinstance(prim, Line):
            out.extend((prim.a, prim.b))
        elif isinstance(prim, Polyline):
            out.extend(prim.vertices)
        elif isinstance(prim, Arc | Circle):
            out.append(prim.centre)
        elif isinstance(prim, Text):
            out.append(prim.at)
        elif isinstance(prim, Hatch):
            out.extend(prim.outline)
        else:  # pragma: no cover - the registry has nothing else in it
            raise AssertionError("unexpected primitive %s" % type(prim).__name__)
    return out


def _of_kind(primitives: Sequence[Primitive], kind: type) -> list[Any]:
    return [prim for prim in primitives if isinstance(prim, kind)]


def _length(a: Pt2, b: Pt2) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _layers_used(primitives: Sequence[Primitive]) -> set[str]:
    return {prim.layer for prim in primitives}


def _sample(name: str, **overrides: object) -> tuple[Primitive, ...]:
    return build_block(name, element_id="blk-%s" % name, **overrides)


# ===========================================================================
# the rounding contract, and the sanitiser, agree with their other mirrors
# ===========================================================================
_ROUNDING_CORPUS = (
    0.0,
    0.5,
    -0.5,
    1.5,
    -1.5,
    2.5,
    -2.5,
    0.49999,
    -0.49999,
    123.5,
    -123.5,
    1000.4999,
)


def test_rounding_agrees_with_every_other_mirror() -> None:
    """The fourth copy of ``round_half_away`` must equal the three that came first."""
    from services.drawings.projection.primitives import round_half_away as projection_round
    from services.drawings.render.hatch_patterns import round_half_away as hatch_round
    from services.solver.geometry import round_half_away as solver_round

    for value in _ROUNDING_CORPUS:
        mine = round_half_away(value)
        assert mine == projection_round(value) == hatch_round(value) == solver_round(value), value


def test_negative_control_pythons_round_would_not_pass_that() -> None:
    """Python rounds halves to even. If the corpus could not see that, it proves nothing."""
    disagreements = [value for value in _ROUNDING_CORPUS if round_half_away(value) != round(value)]
    assert disagreements, "a corpus with no half-values cannot catch a banker's rounder"
    assert round_half_away(2.5) == 3 and round(2.5) == 2


def test_label_text_matches_the_projection_sanitiser() -> None:
    from services.drawings.projection.primitives import sanitise_text

    corpus = ("DB-1", " GEYSER\n25A ", "<script>", "x" * 60, "a\tb")
    for raw in corpus:
        assert label_text(raw) == sanitise_text(raw, max_length=24), raw


def test_negative_control_the_sanitiser_corpus_actually_bites() -> None:
    """Every string in that corpus must be one the raw value would have failed on."""
    assert label_text(" GEYSER\n25A ") == "GEYSER 25A"
    assert label_text("<script>") == "(script)"
    assert len(label_text("x" * 60)) == 24


# ===========================================================================
# every block: registered, stamped, integer, and on a declared layer
# ===========================================================================
def _public_block_functions(module: ModuleType) -> dict[str, Callable[..., Any]]:
    """The block factories a module exports: public, and taking an ``element_id``."""
    found: dict[str, Callable[..., Any]] = {}
    for name in module.__all__:
        obj = getattr(module, name)
        if not inspect.isfunction(obj):
            continue
        if "element_id" in inspect.signature(obj).parameters:
            found[name] = obj
    return found


def _missing_from_registry(functions: dict[str, Callable[..., Any]]) -> list[str]:
    registered = {spec.factory for spec in BLOCK_REGISTRY.values()}
    return sorted(name for name, fn in functions.items() if fn not in registered)


def test_every_public_block_is_registered() -> None:
    """A block that exists but is not in the catalogue is a block nothing can find.

    The failure this guards is the one the canvas furniture layer shipped: a module that
    believed it was registered, was documented as registered, and never called the
    registry. There is no compile-time signal for it, so there is this.
    """
    discovered: dict[str, Callable[..., Any]] = {}
    for module in BLOCK_MODULES:
        discovered.update(_public_block_functions(module))
    assert len(discovered) >= 22, "expected the whole library, found %d" % len(discovered)
    assert _missing_from_registry(discovered) == []
    assert len(BLOCK_NAMES) == len(discovered)


def test_negative_control_an_unregistered_block_is_detected() -> None:
    def door_secret_panel(*, element_id: str, insertion: Insertion = Insertion()) -> tuple[()]:
        return ()

    assert _missing_from_registry({"door_secret_panel": door_secret_panel}) == ["door_secret_panel"]


def test_every_primitive_carries_the_element_id_it_was_built_with() -> None:
    for name in BLOCK_NAMES:
        primitives = _sample(name)
        assert primitives, name
        for prim in primitives:
            assert prim.element_id == "blk-%s" % name, (name, prim)


def test_negative_control_an_unstamped_primitive_would_be_caught() -> None:
    """The same check, against a primitive built the way a block would if it forgot."""
    stray = Line(a=(0, 0), b=(100, 0), layer=A_WALL_PART)
    assert stray.element_id is None
    assert not all(prim.element_id == "x" for prim in (stray,))


def test_every_coordinate_is_an_integer_millimetre() -> None:
    for name in BLOCK_NAMES:
        for point in _anchor_points(_sample(name)):
            for value in point:
                assert isinstance(value, int) and not isinstance(value, bool), (name, point)


def test_no_block_draws_outside_the_layers_it_declared() -> None:
    """Layer discipline, checked per block rather than described in a docstring."""
    for name, spec in BLOCK_REGISTRY.items():
        used = _layers_used(_sample(name))
        assert used <= set(spec.layers), (name, sorted(used), spec.layers)


def test_no_block_touches_a_wall_or_the_title_layer() -> None:
    """A-WALL is where a reviewer measures setbacks; A-TITL is the sheet's own frame.

    A bathtub on A-WALL is a wall as far as any downstream consumer is concerned, and a
    door on A-TITL prints on top of the title block. Neither is recoverable downstream,
    so neither is allowed upstream.
    """
    for name in BLOCK_NAMES:
        used = _layers_used(_sample(name))
        assert A_WALL not in used, name
        assert A_TITL not in used, name


def test_negative_control_a_block_on_the_wrong_layer_is_caught() -> None:
    tampered = (Line(a=(0, 0), b=(1, 0), layer=A_WALL, element_id="x"),)
    spec = block_spec("sanitary-wc")
    assert not _layers_used(tampered) <= set(spec.layers)
    assert A_WALL in _layers_used(tampered)


def test_a_spec_must_declare_its_layers() -> None:
    _expect_error(
        ValueError,
        lambda: BlockSpec(
            name="nowhere",
            category="test",
            title="Undeclared",
            factory=sanitary.wc,
            sample={},
        ),
        contains="must declare the layers",
    )


def test_an_unknown_block_name_fails_loudly() -> None:
    _expect_error(KeyError, lambda: block_spec("door-teleport"), contains="not a block")


# ===========================================================================
# place(): one rotation, exact at the cardinals, total over the vocabulary
# ===========================================================================
def test_a_cardinal_rotation_is_exact_for_every_block() -> None:
    """Turning a block 90° must map every point by ``(x, y) -> (-y, x)``, exactly.

    That is the property that says the rotation happened once, as a rigid transform, and
    not per-primitive with a rounding step each time.
    """
    for name in BLOCK_NAMES:
        flat = _anchor_points(_sample(name))
        turned = _anchor_points(_sample(name, insertion=Insertion(rotation_deg=90)))
        assert turned == [(-y, x) for x, y in flat], name


def test_negative_control_the_other_rotation_sense_fails_that() -> None:
    """Clockwise is a different drawing. If the check could not see it, it is not a check."""
    name = "site-parked-car"
    flat = _anchor_points(_sample(name))
    turned = _anchor_points(_sample(name, insertion=Insertion(rotation_deg=90)))
    assert turned != [(y, -x) for x, y in flat]


def test_translation_and_rotation_compose_without_drift() -> None:
    for name in BLOCK_NAMES:
        at = (123_456, -7_890)
        flat = _anchor_points(_sample(name, insertion=Insertion(rotation_deg=180)))
        moved = _anchor_points(_sample(name, insertion=Insertion(at=at, rotation_deg=180)))
        assert moved == [(x + at[0], y + at[1]) for x, y in flat], name


def test_an_arbitrary_rotation_preserves_every_distance_from_the_origin() -> None:
    """37° is not a cardinal, so this is the rounding case; 1 mm is the whole budget."""
    for name in BLOCK_NAMES:
        flat = _anchor_points(_sample(name))
        turned = _anchor_points(_sample(name, insertion=Insertion(rotation_deg=37)))
        for before, after in zip(flat, turned, strict=True):
            assert abs(math.hypot(*before) - math.hypot(*after)) <= 1.0, (name, before, after)


def test_place_refuses_anything_it_cannot_position() -> None:
    """Totality. A primitive silently dropped here is a missing leaf on a submission set."""
    _expect_error(
        TypeError,
        lambda: place([object()], Insertion(), "e1"),  # type: ignore[list-item]
        contains="cannot position",
    )


def test_place_refuses_an_empty_element_id() -> None:
    _expect_error(
        ValueError,
        lambda: place([Line(a=(0, 0), b=(1, 1), layer=A_WALL_PART)], Insertion(), ""),
        contains="element_id",
    )


def test_rotation_turns_a_hatch_angle_with_the_block() -> None:
    flat = _of_kind(_sample("site-scale-bar"), Hatch)
    turned = _of_kind(_sample("site-scale-bar", insertion=Insertion(rotation_deg=30)), Hatch)
    assert flat and len(flat) == len(turned)
    for before, after in zip(flat, turned, strict=True):
        assert after.angle_deg == (before.angle_deg + 30) % 360


def test_labels_are_flipped_to_stay_readable() -> None:
    """A stair running west must not print ``UP 16R`` upside down."""
    upside_down = _of_kind(_sample("stair-straight", insertion=Insertion(rotation_deg=180)), Text)
    assert upside_down
    for text in upside_down:
        assert text.rotation_deg <= 90 or text.rotation_deg > 270, text


def test_negative_control_without_the_flip_a_turned_label_reads_backwards() -> None:
    assert readable_rotation(180) == 0
    assert (0 + 180) % 360 == 180, "the un-flipped value is exactly the one that fails"


def test_the_insertion_normalises_its_rotation() -> None:
    assert Insertion(rotation_deg=-90).rotation_deg == 270
    assert Insertion(rotation_deg=450).rotation_deg == 90
    _expect_error(TypeError, lambda: Insertion(at=(0, 0), rotation_deg=1.5), contains="integer")
    _expect_error(TypeError, lambda: Insertion(at=(0.5, 0)), contains="integer")


def test_block_extent_measures_an_arc_not_its_radius_box() -> None:
    """A 180° nose arc must not report the fixture as extending behind its own wall."""
    basin = _sample("sanitary-washbasin")
    nose = _of_kind(basin, Arc)[0]
    box = nose.points()
    assert min(y for _x, y in box) < 0, "the coarse radius box does reach behind the wall"
    assert block_extent(basin) == (-275, 0, 275, 450)


def test_paper_sized_symbols_scale_with_the_sheet() -> None:
    assert paper_mm_to_model_mm(3, 100) == 300
    assert paper_mm_to_model_mm(3, 50) == 150
    small = block_extent(_sample("electrical-fan-point", size_mm=paper_mm_to_model_mm(3, 50)))
    large = block_extent(_sample("electrical-fan-point", size_mm=paper_mm_to_model_mm(3, 100)))
    assert small is not None and large is not None
    assert large[2] == 2 * small[2], "a 1:100 symbol is drawn twice the model size of a 1:50 one"


# ===========================================================================
# doors
# ===========================================================================
def _assert_swing_is_a_hinged_quarter_circle(
    primitives: Sequence[Primitive],
    *,
    leaf_width_mm: int,
    wall_thickness_mm: int,
    hand: str,
    swing: str,
) -> None:
    """The claim: the arc is centred on the hinge and sweeps exactly the leaf width.

    Expectations are recomputed from the door's own parameters here — not read back out
    of ``doors.py`` — so this fails if the module changes its mind about where a hinge is.
    """
    x_lo, x_hi = span(leaf_width_mm)
    y_lo, y_hi = span(wall_thickness_mm)
    hinge = (x_lo if hand == doors.HAND_LEFT else x_hi, y_hi if swing == doors.SWING_IN else y_lo)
    far_jamb = (x_hi if hand == doors.HAND_LEFT else x_lo, hinge[1])
    leaf_tip = (hinge[0], hinge[1] + (leaf_width_mm if swing == doors.SWING_IN else -leaf_width_mm))

    arcs = _of_kind(primitives, Arc)
    assert len(arcs) == 1, "a single-leaf door draws exactly one swing"
    arc = arcs[0]
    assert arc.centre == hinge, "the swing is centred on the hinge, not on the opening"
    assert arc.radius_mm == leaf_width_mm, "the swing radius is the leaf it sweeps"
    assert (arc.end_deg - arc.start_deg) % 360 == 90, "a door swings a quarter circle"
    ends = {
        arc_endpoint(arc.centre, arc.radius_mm, arc.start_deg),
        arc_endpoint(arc.centre, arc.radius_mm, arc.end_deg),
    }
    assert ends == {
        far_jamb,
        leaf_tip,
    }, "the sweep runs from the leaf open at 90° to the leaf closed against the far jamb"

    leaf = next(p for p in _of_kind(primitives, Polyline) if p.closed and len(p.vertices) == 4)
    assert hinge in leaf.vertices, "the leaf hangs off the hinge"
    edges = [_length(leaf.vertices[i], leaf.vertices[(i + 1) % 4]) for i in range(4)]
    assert sorted(edges)[-1] == float(leaf_width_mm), "the leaf is as long as the opening is wide"


def test_a_single_door_swings_from_its_hinge_in_every_hand_and_direction() -> None:
    for hand in doors.HANDS:
        for swing in doors.SWINGS:
            primitives = doors.door_single_swing(
                leaf_width_mm=900,
                wall_thickness_mm=230,
                hand=hand,
                swing=swing,
                element_id="d-1",
            )
            _assert_swing_is_a_hinged_quarter_circle(
                primitives,
                leaf_width_mm=900,
                wall_thickness_mm=230,
                hand=hand,
                swing=swing,
            )


def test_negative_control_a_swing_centred_on_the_opening_is_rejected() -> None:
    """The plausible mistake: centre the arc on the insertion point. It misses the jamb."""
    good = doors.door_single_swing(leaf_width_mm=900, wall_thickness_mm=230, element_id="d-1")
    tampered = tuple(
        Arc(
            centre=(0, 0),
            radius_mm=prim.radius_mm,
            start_deg=prim.start_deg,
            end_deg=prim.end_deg,
            layer=prim.layer,
            element_id=prim.element_id,
        )
        if isinstance(prim, Arc)
        else prim
        for prim in good
    )
    _expect_error(
        AssertionError,
        lambda: _assert_swing_is_a_hinged_quarter_circle(
            tampered,
            leaf_width_mm=900,
            wall_thickness_mm=230,
            hand=doors.HAND_LEFT,
            swing=doors.SWING_IN,
        ),
        contains="centred on the hinge",
    )


def test_negative_control_a_half_width_swing_is_rejected() -> None:
    """The other plausible mistake: sweep half the leaf, which looks fine at 1:100."""
    good = doors.door_single_swing(leaf_width_mm=900, wall_thickness_mm=230, element_id="d-1")
    tampered = tuple(
        Arc(
            centre=prim.centre,
            radius_mm=prim.radius_mm // 2,
            start_deg=prim.start_deg,
            end_deg=prim.end_deg,
            layer=prim.layer,
            element_id=prim.element_id,
        )
        if isinstance(prim, Arc)
        else prim
        for prim in good
    )
    _expect_error(
        AssertionError,
        lambda: _assert_swing_is_a_hinged_quarter_circle(
            tampered,
            leaf_width_mm=900,
            wall_thickness_mm=230,
            hand=doors.HAND_LEFT,
            swing=doors.SWING_IN,
        ),
        contains="the leaf it sweeps",
    )


def test_a_rotated_door_still_meets_the_jamb_it_was_built_against() -> None:
    """The rounding case: on a wall at 37°, the closed leaf must still land on the jamb."""
    at = (10_000, -4_000)
    primitives = doors.door_single_swing(
        leaf_width_mm=900,
        wall_thickness_mm=230,
        element_id="d-1",
        insertion=Insertion(at=at, rotation_deg=37),
    )
    x_lo, x_hi = span(900)
    _y_lo, y_hi = span(230)
    theta = math.radians(37)
    expected = (
        at[0] + x_hi * math.cos(theta) - y_hi * math.sin(theta),
        at[1] + x_hi * math.sin(theta) + y_hi * math.cos(theta),
    )
    arc = _of_kind(primitives, Arc)[0]
    ends = [
        arc_endpoint(arc.centre, arc.radius_mm, arc.start_deg),
        arc_endpoint(arc.centre, arc.radius_mm, arc.end_deg),
    ]
    closest = min(_length(end, (round(expected[0]), round(expected[1]))) for end in ends)
    assert closest <= 2.0, (ends, expected)


def test_the_hand_and_swing_arguments_are_enum_checked() -> None:
    """A value outside the enum must raise, never fall back to a default.

    This is the ``buildingUse="residential"`` failure in miniature: a string that is not
    a member, accepted quietly, and 83 rules go inert while the report stays green.
    """
    for bad in ("LEFT", "Left", "hinge-left", ""):
        _expect_error(
            ValueError,
            lambda bad=bad: doors.door_single_swing(
                leaf_width_mm=900, wall_thickness_mm=230, hand=bad, element_id="d"
            ),
            contains="hand must be one of",
        )
    for bad in ("IN", "inward", "inside"):
        _expect_error(
            ValueError,
            lambda bad=bad: doors.door_single_swing(
                leaf_width_mm=900, wall_thickness_mm=230, swing=bad, element_id="d"
            ),
            contains="swing must be one of",
        )


def test_a_double_doors_two_leaves_add_up_to_the_opening() -> None:
    """Including an odd opening, where the millimetre has to go somewhere and be counted."""
    for width in (1500, 1501):
        primitives = doors.door_double_swing(
            leaf_width_mm=width, wall_thickness_mm=230, element_id="d-2"
        )
        arcs = _of_kind(primitives, Arc)
        assert len(arcs) == 2
        assert sum(arc.radius_mm for arc in arcs) == width
        x_lo, x_hi = span(width)
        assert {arc.centre[0] for arc in arcs} == {x_lo, x_hi}, "one leaf per jamb"
        for arc in arcs:
            assert (arc.end_deg - arc.start_deg) % 360 == 90


def test_a_sliding_door_shows_a_full_leaf_of_track_on_the_side_it_parks() -> None:
    for hand, sign in ((doors.HAND_LEFT, -1), (doors.HAND_RIGHT, 1)):
        primitives = doors.door_sliding(
            leaf_width_mm=1200, wall_thickness_mm=115, hand=hand, element_id="d-3"
        )
        tracks = [line for line in _of_kind(primitives, Line) if line.style == STYLE_DASHED]
        assert len(tracks) == 1
        track = tracks[0]
        assert _length(track.a, track.b) == 2 * 1200, "track = the opening plus its pocket"
        parked_end = min(track.a[0], track.b[0]) if sign < 0 else max(track.a[0], track.b[0])
        assert parked_end == sign * (1200 // 2 + 1200), "the pocket is on the hand side"


def test_a_folding_doors_panels_each_measure_their_share_of_the_opening() -> None:
    width, panels = 1800, 4
    primitives = doors.door_folding(
        leaf_width_mm=width, wall_thickness_mm=115, panels=panels, element_id="d-4"
    )
    zigzag = next(p for p in _of_kind(primitives, Polyline) if not p.closed)
    assert len(zigzag.vertices) == panels + 1
    expected = [(width * (i + 1)) // panels - (width * i) // panels for i in range(panels)]
    assert sum(expected) == width
    for index, want in enumerate(expected):
        got = _length(zigzag.vertices[index], zigzag.vertices[index + 1])
        # Two independent roundings (the along-wall advance and the projection out of it)
        # put at most a millimetre each into a panel's drawn length.
        assert abs(got - want) <= 2.0, (index, got, want)


def test_negative_control_panels_that_did_not_partition_the_opening_are_caught() -> None:
    width, panels = 1801, 4
    naive = [width // panels] * panels
    assert sum(naive) != width, "floor division loses the remainder — that is the defect"
    exact = [(width * (i + 1)) // panels - (width * i) // panels for i in range(panels)]
    assert sum(exact) == width


def test_a_folding_door_refuses_a_single_panel() -> None:
    _expect_error(
        ValueError,
        lambda: doors.door_folding(
            leaf_width_mm=900, wall_thickness_mm=115, panels=1, element_id="d"
        ),
        contains="at least 2 panels",
    )


# ===========================================================================
# windows
# ===========================================================================
def test_a_windows_sill_is_on_the_partial_wall_layer_and_projects_outside() -> None:
    """``layers.py`` says A-WALL-PART carries sills. This is that sentence, executed."""
    primitives = windows.window_fixed(width_mm=1200, wall_thickness_mm=230, element_id="w-1")
    sills = [prim for prim in primitives if prim.layer == A_WALL_PART]
    assert len(sills) == 1
    sill = sills[0]
    assert isinstance(sill, Polyline)
    _y_lo, _y_hi = span(230)
    assert min(y for _x, y in sill.vertices) == _y_lo - windows.DEFAULT_SILL_PROJECTION_MM
    assert max(y for _x, y in sill.vertices) == _y_lo, "the sill sits on the outer face"
    assert all(prim.layer in (A_WIND, A_WALL_PART) for prim in primitives)


def test_a_casements_leaves_partition_its_opening_exactly() -> None:
    for leaves in (1, 2, 3, 4):
        width = 1235
        primitives = windows.window_casement(
            width_mm=width, wall_thickness_mm=230, leaves=leaves, element_id="w-2"
        )
        glazing = [
            line
            for line in _of_kind(primitives, Line)
            if line.a[1] == 0 and line.b[1] == 0 and line.layer == A_WIND
        ]
        assert len(glazing) == leaves
        assert sum(_length(line.a, line.b) for line in glazing) == float(width)
        x_lo, x_hi = span(width)
        mullions = [
            line
            for line in _of_kind(primitives, Line)
            if line.a[0] == line.b[0] and line.a[0] not in (x_lo, x_hi) and line.layer == A_WIND
        ]
        assert len(mullions) == leaves - 1, "one mullion between each pair of leaves"


def test_a_ventilator_is_drawn_entirely_in_hidden_line() -> None:
    """It is above the plan's cut plane. Solid lines would say it is at eye level."""
    primitives = windows.window_ventilator(width_mm=600, wall_thickness_mm=115, element_id="w-3")
    assert primitives
    for prim in primitives:
        assert getattr(prim, "style", None) == STYLE_HIDDEN, prim


def test_negative_control_the_other_windows_are_not_hidden() -> None:
    """If every window came out hidden, the ventilator test would prove nothing."""
    for factory in (windows.window_fixed, windows.window_casement, windows.window_sliding):
        primitives = factory(width_mm=900, wall_thickness_mm=230, element_id="w")
        assert all(getattr(p, "style", STYLE_SOLID) == STYLE_SOLID for p in primitives), factory


def test_sliding_sashes_sit_on_two_tracks_and_overlap_at_the_meeting_stile() -> None:
    width, overlap = 1500, 50
    primitives = windows.window_sliding(
        width_mm=width, wall_thickness_mm=230, overlap_mm=overlap, element_id="w-4"
    )
    _y_lo, y_hi = span(230)
    sashes = [
        line
        for line in _of_kind(primitives, Line)
        if line.a[1] == line.b[1] and 0 < abs(line.a[1]) < y_hi and line.layer == A_WIND
    ]
    assert len(sashes) == 2
    assert {line.a[1] for line in sashes} == {-38, 38}, "one sash per track, either side of centre"
    for line in sashes:
        assert _length(line.a, line.b) == float(width // 2 + overlap)
    _expect_error(
        ValueError,
        lambda: windows.window_sliding(
            width_mm=100, wall_thickness_mm=230, overlap_mm=400, element_id="w"
        ),
        contains="does not fit",
    )


# ===========================================================================
# stairs
# ===========================================================================
def test_tread_count_times_tread_depth_is_the_run_that_is_drawn() -> None:
    """§ the arithmetic: a flight of n risers has n−1 treads, and that is its run."""
    tread, risers, width = 280, 16, 1000
    assert stairs.tread_count(risers) == 15
    assert stairs.straight_flight_run_mm(tread, risers) == 15 * 280

    primitives = stairs.stair_straight(
        tread_mm=tread, riser_count=risers, width_mm=width, element_id="s-1"
    )
    outline = next(p for p in _of_kind(primitives, Polyline) if p.closed)
    run = max(x for x, _y in outline.vertices) - min(x for x, _y in outline.vertices)
    assert run == stairs.tread_count(risers) * tread

    risers_lines = [
        line
        for line in _of_kind(primitives, Line)
        if line.a[0] == line.b[0] and {line.a[1], line.b[1]} == {0, width}
    ]
    assert len(risers_lines) == stairs.tread_count(risers) - 1, "interior nosings only"
    assert sorted(line.a[0] for line in risers_lines) == [
        index * tread for index in range(1, stairs.tread_count(risers))
    ]


def test_negative_control_a_run_measured_off_the_riser_count_is_wrong() -> None:
    """The off-by-one this arithmetic exists to prevent, measured: one whole tread."""
    tread, risers = 280, 16
    drawn = stairs.straight_flight_run_mm(tread, risers)
    naive = risers * tread
    assert naive - drawn == tread
    assert drawn != naive


def test_a_flight_needs_a_tread_to_be_a_flight() -> None:
    _expect_error(ValueError, lambda: stairs.tread_count(1), contains="at least 2 risers")


def test_up_and_dn_reverse_both_the_arrow_and_the_label() -> None:
    common = {"tread_mm": 280, "riser_count": 16, "width_mm": 1000, "element_id": "s-2"}
    up = stairs.stair_straight(direction=stairs.DIRECTION_UP, **common)  # type: ignore[arg-type]
    dn = stairs.stair_straight(direction=stairs.DIRECTION_DN, **common)  # type: ignore[arg-type]
    up_text = _of_kind(up, Text)[0].text
    dn_text = _of_kind(dn, Text)[0].text
    assert up_text == "UP 16R" and dn_text == "DN 16R"

    def shaft(primitives: Sequence[Primitive]) -> Line:
        centre_y = 1000 // 2
        return next(
            line
            for line in _of_kind(primitives, Line)
            if line.a[1] == line.b[1] == centre_y and abs(line.b[0] - line.a[0]) > 280
        )

    assert shaft(up).b[0] > shaft(up).a[0], "you walk up towards the top of the flight"
    assert shaft(dn).b[0] < shaft(dn).a[0], "and down the other way"


def test_the_break_line_is_a_pair_of_strokes_across_the_flight() -> None:
    tread, risers, width = 280, 16, 1000
    plain = stairs.stair_straight(
        tread_mm=tread, riser_count=risers, width_mm=width, element_id="s-3"
    )
    broken = stairs.stair_straight(
        tread_mm=tread,
        riser_count=risers,
        width_mm=width,
        break_after_treads=8,
        element_id="s-3",
    )
    added = [
        line
        for line in _of_kind(broken, Line)
        if line not in _of_kind(plain, Line) and min(line.a[1], line.b[1]) < 0
    ]
    assert len(added) == 2, "the convention is a pair of parallel strokes"
    for line in added:
        assert (
            min(line.a[1], line.b[1]) < 0 < width < max(line.a[1], line.b[1])
        ), "a break line oversails both edges of the flight"
        assert line.a[0] != line.b[0], "and is slanted, or it reads as another nosing"
    assert abs(added[0].a[0] - added[1].a[0]) == max(2, tread // 2)
    _expect_error(
        ValueError,
        lambda: stairs.stair_straight(
            tread_mm=tread,
            riser_count=risers,
            width_mm=width,
            break_after_treads=15,
            element_id="s",
        ),
        contains="break_after_treads must be between",
    )


def test_a_dogleg_lands_where_its_first_flight_ends() -> None:
    tread, risers, width, landing, well = 280, 19, 1000, 1200, 150
    primitives = stairs.stair_dogleg(
        tread_mm=tread,
        riser_count=risers,
        width_mm=width,
        landing_depth_mm=landing,
        well_mm=well,
        element_id="s-4",
    )
    risers_up = (risers + 1) // 2
    risers_down = risers - risers_up
    assert risers_up + risers_down == risers, "no riser is lost in the turn"
    run_a = stairs.straight_flight_run_mm(tread, risers_up)
    run_b = stairs.straight_flight_run_mm(tread, risers_down)

    rects = [p for p in _of_kind(primitives, Polyline) if p.closed]
    assert len(rects) == 3, "two flights and a landing"
    boxes = {
        (
            min(x for x, _y in r.vertices),
            min(y for _x, y in r.vertices),
            max(x for x, _y in r.vertices),
            max(y for _x, y in r.vertices),
        )
        for r in rects
    }
    assert (0, 0, run_a, width) in boxes, "the first flight"
    assert (run_a, 0, run_a + landing, 2 * width + well) in boxes, "the landing, at its head"
    assert (run_a - run_b, width + well, run_a, 2 * width + well) in boxes, "the return"


def test_a_dogleg_needs_two_real_flights() -> None:
    _expect_error(
        ValueError,
        lambda: stairs.stair_dogleg(
            tread_mm=280,
            riser_count=3,
            width_mm=1000,
            landing_depth_mm=1000,
            element_id="s",
        ),
        contains="at least 4 risers",
    )


def test_a_spirals_treads_divide_its_sweep_evenly() -> None:
    for sweep, risers in ((360, 13), (270, 10), (180, 9)):
        outer, inner = 900, 120
        primitives = stairs.stair_spiral(
            outer_radius_mm=outer,
            inner_radius_mm=inner,
            riser_count=risers,
            sweep_deg=sweep,
            element_id="s-5",
        )
        treads = stairs.tread_count(risers)
        radials = [
            line
            for line in _of_kind(primitives, Line)
            if abs(math.hypot(*line.a) - inner) <= 1 and abs(math.hypot(*line.b) - outer) <= 1
        ]
        assert len(radials) == (treads if sweep == 360 else treads + 1), sweep
        angles = sorted(math.degrees(math.atan2(line.b[1], line.b[0])) % 360 for line in radials)
        gaps = [b - a for a, b in itertools.pairwise(angles)]
        for gap in gaps:
            # A 0.5 mm rounding at r=900 is 0.03 deg; 0.2 is generous and still an order
            # of magnitude below the 20-28 deg pitch it has to distinguish.
            assert abs(gap - sweep / treads) <= 0.2, (sweep, gaps)


def test_negative_control_an_uneven_spiral_pitch_would_be_caught() -> None:
    sweep, treads = 360.0, 12
    even = [index * sweep / treads for index in range(treads)]
    uneven = list(even)
    uneven[5] += 4.0
    gaps = [b - a for a, b in itertools.pairwise(uneven)]
    assert any(abs(gap - sweep / treads) > 0.2 for gap in gaps)


def test_a_spiral_refuses_geometry_that_cannot_be_walked() -> None:
    _expect_error(
        ValueError,
        lambda: stairs.stair_spiral(
            outer_radius_mm=500, inner_radius_mm=600, riser_count=13, element_id="s"
        ),
        contains="must be smaller than",
    )
    _expect_error(
        ValueError,
        lambda: stairs.stair_spiral(
            outer_radius_mm=900,
            inner_radius_mm=100,
            riser_count=13,
            sweep_deg=540,
            element_id="s",
        ),
        contains="one revolution at most",
    )


# ===========================================================================
# sanitary — the defaults are the catalogue's, and the drawing is its size
# ===========================================================================
def test_fixture_defaults_are_the_catalogue_dimensions() -> None:
    """Cited by id, and the citation is checked against the file the schedule reads."""
    expected = {
        "wc": (sanitary.WC_WIDTH_MM, sanitary.WC_DEPTH_MM),
        "washbasin": (sanitary.WASHBASIN_WIDTH_MM, sanitary.WASHBASIN_DEPTH_MM),
        "shower": (sanitary.SHOWER_WIDTH_MM, sanitary.SHOWER_DEPTH_MM),
        "bathtub": (sanitary.BATHTUB_WIDTH_MM, sanitary.BATHTUB_DEPTH_MM),
        "sink": (sanitary.SINK_WIDTH_MM, sanitary.SINK_DEPTH_MM),
    }
    for block, catalogue_id in sanitary.CATALOGUE_SOURCE.items():
        item = CATALOGUE_BY_ID[catalogue_id]
        assert (item["widthMm"], item["depthMm"]) == expected[block], (block, item)


def test_negative_control_the_catalogue_check_discriminates() -> None:
    """A different entry for the same fixture has different numbers — so the check bites."""
    other = CATALOGUE_BY_ID["wc-floor"]
    assert (other["widthMm"], other["depthMm"]) != (
        sanitary.WC_WIDTH_MM,
        sanitary.WC_DEPTH_MM,
    ), "if every WC in the catalogue were 380x680, this gate could not fail"


def test_every_fixture_is_drawn_at_exactly_its_stated_size() -> None:
    cases = (
        (sanitary.wc, sanitary.WC_WIDTH_MM, sanitary.WC_DEPTH_MM),
        (sanitary.washbasin, sanitary.WASHBASIN_WIDTH_MM, sanitary.WASHBASIN_DEPTH_MM),
        (sanitary.shower, sanitary.SHOWER_WIDTH_MM, sanitary.SHOWER_DEPTH_MM),
        (sanitary.bathtub, sanitary.BATHTUB_WIDTH_MM, sanitary.BATHTUB_DEPTH_MM),
        (sanitary.sink, sanitary.SINK_WIDTH_MM, sanitary.SINK_DEPTH_MM),
    )
    for factory, width, depth in cases:
        extent = block_extent(factory(element_id="f-1"))
        assert extent is not None
        min_x, min_y, max_x, max_y = extent
        assert (max_x - min_x, max_y - min_y) == (width, depth), factory
        assert min_y == 0, "a fixture's back sits on the wall it is fixed to"
        assert (min_x, max_x) == span(width), "and is centred on its insertion point"


def test_negative_control_a_fixture_drawn_short_is_caught() -> None:
    narrow = sanitary.wc(element_id="f-2", width_mm=sanitary.WC_WIDTH_MM - 40)
    extent = block_extent(narrow)
    assert extent is not None
    assert extent[2] - extent[0] != sanitary.WC_WIDTH_MM


def test_a_fixture_too_shallow_to_round_off_refuses_to_draw() -> None:
    _expect_error(
        ValueError,
        lambda: sanitary.wc(element_id="f", width_mm=800, depth_mm=300),
        contains="cannot be rounded off",
    )


def test_a_sink_draws_one_well_per_bowl() -> None:
    for bowls in (1, 2, 3):
        primitives = sanitary.sink(element_id="f-3", width_mm=1200, depth_mm=500, bowls=bowls)
        assert len(_of_kind(primitives, Polyline)) == bowls + 1, "the tray plus one well each"
        assert len(_of_kind(primitives, Circle)) == bowls, "one waste per bowl"


# ===========================================================================
# electrical
# ===========================================================================
def test_a_switch_draws_one_lever_per_gang() -> None:
    for gang in (1, 2, 3):
        primitives = electrical.switch(element_id="e-1", gang=gang)
        levers = [
            line
            for line in _of_kind(primitives, Line)
            if abs(math.hypot(*line.b) - electrical.SYMBOL_SIZE_MM // 2) <= 1
        ]
        assert len(levers) == gang


def test_a_two_way_switch_is_told_from_a_one_way() -> None:
    one = electrical.switch(element_id="e-2", gang=1, two_way=False)
    two = electrical.switch(element_id="e-2", gang=1, two_way=True)
    assert len(two) == len(one) + 1, "the second contact is drawn, or the symbol lies"


def test_fan_blades_are_evenly_spaced_around_the_hub() -> None:
    for blades in (3, 4):
        primitives = electrical.fan_point(element_id="e-3", blades=blades)
        spokes = [
            line
            for line in _of_kind(primitives, Line)
            if abs(math.hypot(*line.b) - electrical.SYMBOL_SIZE_MM // 2) <= 1
        ]
        assert len(spokes) == blades
        angles = sorted(math.degrees(math.atan2(line.b[1], line.b[0])) % 360 for line in spokes)
        gaps = [b - a for a, b in itertools.pairwise(angles)]
        for gap in gaps:
            assert abs(gap - 360.0 / blades) <= 1.0, (blades, angles)


def test_a_socket_label_reaches_the_sheet_bounded() -> None:
    primitives = electrical.socket(element_id="e-4", label="  16A\nRING  ")
    texts = _of_kind(primitives, Text)
    assert len(texts) == 1
    assert texts[0].text == "16A RING"
    plain = electrical.socket(element_id="e-4")
    assert _of_kind(plain, Text) == [], "no label, no text"


def test_a_db_is_drawn_half_filled_and_named() -> None:
    primitives = electrical.distribution_board(element_id="e-5", label="DB-1")
    hatches = _of_kind(primitives, Hatch)
    assert len(hatches) == 1
    filled = max(x for x, _y in hatches[0].outline) - min(x for x, _y in hatches[0].outline)
    assert filled == electrical.DB_WIDTH_MM // 2
    assert _of_kind(primitives, Text)[0].text == "DB-1"


# ===========================================================================
# site
# ===========================================================================
def _north_tip(primitives: Sequence[Primitive]) -> Pt2:
    dart = _of_kind(primitives, Polyline)[0]
    return max(dart.vertices, key=lambda v: math.hypot(*v))


def test_the_north_arrow_points_where_you_asked() -> None:
    """§3: ``north_deg`` is measured clockwise from +Y, so north is ``(sin θ, cos θ)``."""
    length = site.NORTH_ARROW_LENGTH_MM
    for north_deg in (0, 30, 45, 90, 135, 180, 270, 315):
        primitives = site.north_arrow(element_id="n-1", north_deg=north_deg)
        theta = math.radians(north_deg)
        want = (length * math.sin(theta), length * math.cos(theta))
        got = _north_tip(primitives)
        assert _length(got, (round(want[0]), round(want[1]))) <= 1.0, (north_deg, got, want)


def test_negative_control_the_transposed_bearing_is_rejected() -> None:
    """``(cos θ, sin θ)`` is the reading that draws east on a plot that points north."""
    length = site.NORTH_ARROW_LENGTH_MM
    theta = math.radians(30)
    wrong = (length * math.cos(theta), length * math.sin(theta))
    got = _north_tip(site.north_arrow(element_id="n-2", north_deg=30))
    assert _length(got, (round(wrong[0]), round(wrong[1]))) > 100.0


def test_the_north_arrow_composes_with_the_blocks_own_rotation() -> None:
    """A site plan drawn rotated must still point at true north."""
    length = site.NORTH_ARROW_LENGTH_MM
    primitives = site.north_arrow(
        element_id="n-3", north_deg=20, insertion=Insertion(rotation_deg=90)
    )
    theta = math.radians(20 - 90)
    want = (length * math.sin(theta), length * math.cos(theta))
    got = _north_tip(primitives)
    assert _length(got, (round(want[0]), round(want[1]))) <= 1.0, (got, want)


def test_every_tree_style_fits_the_canopy_it_was_given() -> None:
    radius = 2_000
    for style in site.TREE_STYLES:
        primitives = site.tree(element_id="t-1", canopy_radius_mm=radius, style=style)
        extent = block_extent(primitives)
        assert extent is not None
        assert max(abs(value) for value in extent) <= radius, style
        assert max(abs(value) for value in extent) >= radius * 3 // 5, style
    _expect_error(
        ValueError,
        lambda: site.tree(element_id="t", canopy_radius_mm=1000, style="bonsai"),
        contains="style must be one of",
    )


def test_the_cloud_canopy_is_lobes_that_touch() -> None:
    radius = 2_000
    primitives = site.tree(element_id="t-2", canopy_radius_mm=radius, style="cloud")
    lobes = [c for c in _of_kind(primitives, Circle) if math.hypot(*c.centre) > 0]
    assert len(lobes) == site.CLOUD_LOBES
    # The lobe on the +X cardinal is exact; the diagonals carry the millimetre their
    # rounded centres cost, which is why the canopy is drawn with a lobe on each
    # cardinal in the first place.
    on_axis = [lobe for lobe in lobes if lobe.centre[1] == 0 and lobe.centre[0] > 0]
    assert len(on_axis) == 1
    ring = on_axis[0].centre[0]
    assert ring + on_axis[0].radius_mm == radius, "the canopy reaches its stated radius"
    for lobe in lobes:
        assert abs(math.hypot(*lobe.centre) - ring) <= 1.0, lobe


def test_a_parked_car_is_drawn_at_the_catalogue_size() -> None:
    item = CATALOGUE_BY_ID[site.CATALOGUE_SOURCE["parked_car"]]
    assert (item["widthMm"], item["depthMm"]) == (site.CAR_WIDTH_MM, site.CAR_LENGTH_MM)
    extent = block_extent(site.parked_car(element_id="c-1"))
    assert extent is not None
    min_x, min_y, max_x, max_y = extent
    assert (max_x - min_x, max_y - min_y) == (site.CAR_WIDTH_MM, site.CAR_LENGTH_MM)


def test_the_scale_bars_labels_are_the_distances_they_mark() -> None:
    primitives = site.scale_bar(element_id="sb-1", division_mm=1_000, divisions=5)
    texts = _of_kind(primitives, Text)
    assert [text.text for text in texts] == ["0", "1", "2", "3", "4", "5 m"]
    assert [text.at[0] for text in texts] == [0, 1_000, 2_000, 3_000, 4_000, 5_000]
    fills = _of_kind(primitives, Hatch)
    assert len(fills) == 2, "alternate cells are filled"
    halves = site.scale_bar(element_id="sb-2", division_mm=2_500, divisions=2)
    assert [text.text for text in _of_kind(halves, Text)] == ["0", "2.5", "5 m"]


def test_negative_control_a_mislabelled_scale_bar_is_caught() -> None:
    """The bar is in model mm; labelling ticks by index instead of distance is the bug."""
    primitives = site.scale_bar(element_id="sb-3", division_mm=2_000, divisions=3)
    labels = [text.text for text in _of_kind(primitives, Text)]
    assert labels == ["0", "2", "4", "6 m"]
    assert labels != ["0", "1", "2", "3 m"], "index labels would be a 3x error on the sheet"


# ---------------------------------------------------------------------------
# bare-python runner (pytest is not installed on every machine this must run on)
# ---------------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    import traceback

    failures = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print("PASS %s" % _name)
            except Exception:
                failures += 1
                print("FAIL %s" % _name)
                traceback.print_exc()
    print("\n%d failure(s)" % failures)
    sys.exit(1 if failures else 0)
