"""The hatch pattern table, and the three drifts it exists to prevent.

Every test here is paired with a negative control, because the defects this
module fixed were all invisible to a passing suite: the old ``earth`` rendered as
a cross-hatch and no test noticed, and DXF drew hatches 31x too dense at twice
the authored angle for as long as the exporter existed. A gate that cannot go
red would have shipped all three again.
"""

from __future__ import annotations

import itertools
import math

import pytest

from services.drawings.export.dxf import DXF_HATCH_PATTERNS
from services.drawings.render.hatch_patterns import (
    HATCH_DEFS,
    MAX_HATCH_LINES,
    HatchDef,
    PatternLine,
    hatch_families,
    is_solid,
    pattern_keys,
)
from services.drawings.render.primitives import Hatch

BBOX = (0, 0, 4000, 4000)
DRAWN = [key for key in pattern_keys() if not is_solid(key)]


def _first_family_angle(key: str, *, angle_deg: float) -> float:
    families = hatch_families(key, spacing=200, angle_deg=angle_deg, bbox=BBOX)
    (ax, ay), (bx, by) = families[0].segments[0]
    return math.degrees(math.atan2(by - ay, bx - ax)) % 180.0


# ===========================================================================
# The vendored table must stay equal to its source
# ===========================================================================
def test_the_vendored_table_matches_ezdxf_exactly() -> None:
    """The copy is checked, not assumed.

    SVG draws the geometry in this table; DXF writes the ACAD pattern *name* and
    lets CAD look the geometry up. Those two only agree because this table is a
    faithful copy of the definition behind that name — so if ezdxf's table ever
    moves, this fails rather than letting the two formats quietly diverge.
    """
    pattern = pytest.importorskip("ezdxf.tools.pattern")
    upstream = pattern.load(measurement=1)
    for key, definition in HATCH_DEFS.items():
        if is_solid(key):
            continue
        source = upstream[definition.acad_name]
        assert len(source) == len(definition.lines), key
        for theirs, ours in zip(source, definition.lines, strict=True):
            angle, base, offset, dashes = theirs
            assert ours.angle_deg == pytest.approx(angle), key
            assert ours.base == pytest.approx(tuple(base)), key
            assert ours.offset == pytest.approx(tuple(offset)), key
            assert ours.dashes == pytest.approx(tuple(dashes)), key


def test_negative_control_a_perturbed_table_is_caught() -> None:
    """Prove the drift gate can fail: move one number and the comparison must reject it."""
    pattern = pytest.importorskip("ezdxf.tools.pattern")
    upstream = pattern.load(measurement=1)
    original = HATCH_DEFS["diagonal"]
    tampered = HatchDef(
        key=original.key,
        acad_name=original.acad_name,
        label=original.label,
        lines=(PatternLine(45.0, (0.0, 0.0), (-2.0, 2.0), ()),),
    )
    source = upstream[tampered.acad_name][0]
    assert tampered.lines[0].offset != pytest.approx(tuple(source[2]))


def test_every_pattern_name_is_one_acad_knows() -> None:
    pattern = pytest.importorskip("ezdxf.tools.pattern")
    upstream = pattern.load(measurement=1)
    for key, definition in HATCH_DEFS.items():
        if is_solid(key):
            assert definition.acad_name == "SOLID"
        else:
            assert definition.acad_name in upstream, key


# ===========================================================================
# The authored numbers must survive into the geometry
# ===========================================================================
@pytest.mark.parametrize("angle", [0, 30, 45, 90, 135])
def test_the_authored_angle_is_the_angle_drawn(angle: int) -> None:
    """``angle_deg`` means the angle on paper, whatever the pattern is defined at.

    ANSI31 is defined at 45 deg. Before the shared table, DXF added the authored
    angle to that and drew the default hatch at 90 deg while SVG drew 45.
    """
    # Endpoints are integer millimetres, so a long segment carries a fraction of a
    # degree of rounding error. The defect this guards was 45 degrees out.
    assert _first_family_angle("diagonal", angle_deg=angle) == pytest.approx(angle % 180, abs=0.05)


def test_negative_control_the_angle_assertion_can_fail() -> None:
    """The double-application bug, reproduced: adding the base angle is detectable."""
    doubled = _first_family_angle("diagonal", angle_deg=45 + HATCH_DEFS["diagonal"].base_angle_deg)
    assert doubled == pytest.approx(90.0, abs=0.05)
    assert doubled != pytest.approx(45.0, abs=0.05)


@pytest.mark.parametrize("spacing", [100, 250, 600])
def test_the_authored_spacing_is_the_spacing_drawn(spacing: int) -> None:
    """Adjacent lines of the first family sit ``spacing`` apart, in model mm."""
    families = hatch_families("diagonal", spacing=spacing, angle_deg=45, bbox=BBOX)
    normal = (-math.sin(math.radians(45)), math.cos(math.radians(45)))
    offsets = sorted(
        {round(a[0] * normal[0] + a[1] * normal[1], 3) for a, _ in families[0].segments}
    )
    gaps = [b - a for a, b in itertools.pairwise(offsets)]
    assert gaps, "a hatch over a 4 m box must produce more than one line"
    # +/- 2 mm: both endpoints are rounded to whole millimetres, so a measured gap
    # between two 45-degree lines can carry about 1.4 mm of that. The defect this
    # guards drew 150 mm hatches at 4.76 mm.
    assert min(gaps) == pytest.approx(spacing, abs=2.0)


# ===========================================================================
# Patterns must be distinguishable — the earth/cross defect
# ===========================================================================
def test_every_pattern_draws_something_different() -> None:
    """No two patterns may render the same geometry.

    ``earth`` and ``cross`` used to be byte-identical in SVG: both were emitted as
    the same two-line tile, so a section's soil was drawn as generic cross-hatch.
    """
    rendered: dict[str, tuple] = {}
    for key in DRAWN:
        families = hatch_families(key, spacing=250, angle_deg=45, bbox=BBOX)
        assert families, "%s produced no geometry at all" % key
        rendered[key] = tuple(
            (family.segments, family.dashes, family.dash_offset) for family in families
        )
    for key, geometry in rendered.items():
        twins = [other for other, shape in rendered.items() if other != key and shape == geometry]
        assert not twins, "%s renders identically to %s" % (key, twins)


def test_earth_is_not_cross_specifically() -> None:
    earth = hatch_families("earth", spacing=250, angle_deg=45, bbox=BBOX)
    cross = hatch_families("cross", spacing=250, angle_deg=45, bbox=BBOX)
    assert [f.segments for f in earth] != [f.segments for f in cross]
    assert any(family.dashes for family in earth), "earth is a broken-line pattern"
    assert not any(family.dashes for family in cross), "cross is continuous"


# ===========================================================================
# SVG and DXF must agree, because they are two views of one drawing
# ===========================================================================
def test_dxf_and_the_generated_geometry_agree_on_angle_and_spacing() -> None:
    """What CAD draws from the pattern name must match what SVG drew from the table.

    Measured out of a real ``HATCH`` entity rather than asserted about the call:
    the old code passed plausible-looking arguments and produced a 4.76 mm hatch
    where the PDF showed 150 mm, which no argument-level assertion would catch.
    """
    ezdxf = pytest.importorskip("ezdxf")
    from services.drawings.export.dxf import _add_hatch

    document = ezdxf.new(setup=True)
    msp = document.modelspace()
    ring = ((0, 0), (4000, 0), (4000, 4000), (0, 4000))
    for key in ("diagonal", "brick", "earth"):
        for spacing, angle in ((150, 45), (250, 0)):
            _add_hatch(
                msp,
                Hatch(ring, "A-WALL", pattern=key, spacing_mm=spacing, angle_deg=angle),
            )
            entity = list(msp.query("HATCH"))[-1]
            line = entity.pattern.lines[0]
            theta = math.radians(line.angle)
            drawn_spacing = abs(
                line.offset[0] * -math.sin(theta) + line.offset[1] * math.cos(theta)
            )
            # The authored angle, not the authored angle plus the pattern's own.
            assert line.angle == pytest.approx(angle % 360, abs=1e-6), key
            assert drawn_spacing == pytest.approx(spacing, rel=1e-6), key


def test_negative_control_the_old_dxf_arguments_would_fail_that() -> None:
    """The pre-fix call, reproduced, must miss the authored numbers by a mile."""
    ezdxf = pytest.importorskip("ezdxf")
    document = ezdxf.new(setup=True)
    hatch = document.modelspace().add_hatch()
    hatch.set_pattern_fill("ANSI31", scale=150 / 100.0, angle=45.0)
    line = hatch.pattern.lines[0]
    theta = math.radians(line.angle)
    spacing = abs(line.offset[0] * -math.sin(theta) + line.offset[1] * math.cos(theta))
    assert line.angle == pytest.approx(90.0), "the old call double-applied the angle"
    assert spacing < 10.0, "the old call drew a 150 mm hatch at under 10 mm"


def test_the_dxf_name_table_is_derived_not_restated() -> None:
    """One table, so the two writers cannot disagree about what a pattern is."""
    assert {key: d.acad_name for key, d in HATCH_DEFS.items()} == DXF_HATCH_PATTERNS
    assert set(pattern_keys()) == set(DXF_HATCH_PATTERNS)


# ===========================================================================
# Boundaries
# ===========================================================================
def test_a_hatch_may_only_name_a_pattern_the_writers_have() -> None:
    ring = ((0, 0), (1000, 0), (1000, 1000))
    for key in pattern_keys():
        Hatch(ring, "A-WALL", pattern=key)
    with pytest.raises(ValueError, match="unknown hatch pattern"):
        Hatch(ring, "A-WALL", pattern="terrazzo")


def test_solid_generates_no_geometry() -> None:
    assert is_solid("solid")
    assert hatch_families("solid", spacing=250, angle_deg=45, bbox=BBOX) == ()


def test_generation_is_capped_so_one_hatch_cannot_swallow_a_sheet() -> None:
    """A fine spacing over a plot-sized region must stop, not emit unbounded lines."""
    huge = (0, 0, 200_000, 200_000)
    capped = hatch_families("diagonal", spacing=1, angle_deg=45, bbox=huge, max_lines=50)
    assert sum(len(family.segments) for family in capped) == 50


def test_negative_control_without_the_cap_it_would_be_unbounded() -> None:
    huge = (0, 0, 200_000, 200_000)
    uncapped = hatch_families("diagonal", spacing=1, angle_deg=45, bbox=huge)
    assert sum(len(family.segments) for family in uncapped) == MAX_HATCH_LINES


def test_lines_are_anchored_at_the_origin_so_neighbours_align() -> None:
    """Two touching regions must continue one another's hatch, not restart it."""
    left = hatch_families("diagonal", spacing=250, angle_deg=45, bbox=(0, 0, 2000, 2000))
    right = hatch_families("diagonal", spacing=250, angle_deg=45, bbox=(2000, 0, 4000, 2000))
    normal = (-math.sin(math.radians(45)), math.cos(math.radians(45)))

    def offsets(families: tuple) -> set[int]:
        return {round(a[0] * normal[0] + a[1] * normal[1]) for a, _ in families[0].segments}

    shared = offsets(left) & offsets(right)
    assert shared, "the two regions share no hatch line; the pattern restarts at each bbox"


def test_every_coordinate_is_an_integer_millimetre() -> None:
    """The renderer's float-free guarantee starts here."""
    for key in DRAWN:
        for family in hatch_families(key, spacing=250, angle_deg=30, bbox=BBOX):
            for start, end in family.segments:
                assert all(isinstance(value, int) for value in (*start, *end)), key
            assert all(isinstance(length, int) for length in family.dashes), key
            assert isinstance(family.dash_offset, int), key


# ===========================================================================
# The library must be reachable, not merely present
# ===========================================================================
def test_a_material_pattern_reaches_a_rendered_sheet() -> None:
    """A projection naming BRICK must come out of the SVG writer as brick lines.

    This repository has shipped a module that tagged itself as integrated and was
    never actually called. Registration is not provable by reading, so this walks
    the whole path — projection name, adapter, primitive, SVG — and looks at the
    markup that comes out the far end.
    """
    from services.drawings.render.adapt import from_projection_one
    from services.drawings.render.primitives import DrawingGroup, Placement
    from services.drawings.render.svg import _emit_hatch, _emit_hatch_defs

    class _ProjectionHatch:
        layer = "A-WALL"
        boundary = ((0, 0), (3000, 0), (3000, 3000), (0, 3000))
        pattern = "BRICK"
        angle_deg = 0
        spacing_mm = 250
        holes = ()
        owner_id = None
        kind = "wall-hatch"

    primitive = from_projection_one(_ProjectionHatch(), scale_denominator=100)
    assert primitive.pattern == "brick", "the ACAD name did not survive the adapter"

    placement = Placement(scale_denominator=100)
    group = DrawingGroup(id="g", placement=placement, primitives=(primitive,))
    out: list[str] = []
    _emit_hatch_defs(out, (group,))
    _emit_hatch(out, placement, primitive)
    markup = "".join(out)

    assert "<clipPath" in markup, "the hatch was not clipped to its region"
    assert markup.count("<path") >= 4, "brick is three line families plus the clip outline"
    assert "stroke-dasharray" in markup, "brick's broken courses were not drawn"
    assert 'fill="none"' in markup, "a pattern must not come out as a solid fill"


def test_negative_control_solid_produces_no_hatch_lines() -> None:
    """Prove the reachability assertions discriminate: solid takes the other branch."""
    from services.drawings.render.primitives import Hatch, Placement
    from services.drawings.render.svg import _emit_hatch

    placement = Placement(scale_denominator=100)
    out: list[str] = []
    _emit_hatch(out, placement, Hatch(((0, 0), (3000, 0), (3000, 3000)), "A-WALL", pattern="solid"))
    markup = "".join(out)
    assert "<clipPath" not in markup
    assert "stroke-dasharray" not in markup
    assert 'fill="#' in markup, "a solid hatch is a filled path"
