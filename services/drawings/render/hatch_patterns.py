"""The standard CAD hatch pattern library, vendored as plain data.

Why this module exists
----------------------
A municipal drawing distinguishes materials by hatch: brick is not concrete is
not earth. Before this table the renderer knew four patterns, two of which were
indistinguishable on paper, and the SVG and DXF writers each invented their own
geometry for them. Three defects followed, all found by measuring real output:

1. ``earth`` and ``cross`` rendered as the *same* two-line tile in SVG, while
   DXF wrote genuinely different ACAD patterns. The same sheet said different
   things in a browser and in CAD.
2. DXF passed ``scale=spacing_mm / 100``. ACAD scales a pattern's *intrinsic*
   spacing, so an authored 150 mm hatch came out 4.76 mm — 31x too dense, a
   black smear where the PDF showed open hatching.
3. DXF passed ``angle=angle_deg`` on top of a pattern already defined at an
   angle, so the default 45 deg hatch was drawn at 90 deg.

All three are the same root cause: no shared definition of what a pattern *is*.
This module is that definition, and both writers now derive from it, so the
geometry cannot drift between SVG, PDF and DXF.

Provenance and licence
----------------------
The definitions are the standard ``acadiso.pat`` line families, extracted
verbatim from ezdxf 1.3.4's ISO pattern table (``ezdxf.tools.pattern.load(
measurement=1)``). ezdxf is MIT-licensed; the licence text sits beside this file
in ``HATCH-PATTERNS-LICENSE.txt``, the same way the Inter font carries its OFL.

Vendored rather than imported, for two reasons that are not stylistic:

* ``render/`` must import without ezdxf. ezdxf is an *optional* dependency
  (:func:`services.drawings.export.dxf._require_ezdxf` raises a typed error when
  it is absent) because SVG and PDF export run in deployments that never write
  DXF. Importing it here would make every PDF export depend on a DXF library.
* An upstream change to a definition would otherwise silently redraw every
  municipal sheet we have ever issued. ``test_hatch_patterns.py`` re-reads ezdxf
  and fails if this table drifts from it, so the pin is checked, not assumed.

Reading a definition
--------------------
Each :class:`PatternLine` is one ACAD pattern line: an angle, a base point, an
offset to the next line of that family, and a dash pattern — all in pattern
units (millimetres, ISO measurement). ``offset`` is in *pattern* space, not the
family's rotated frame, so the perpendicular line spacing is the component of
``offset`` normal to the line direction; see :attr:`PatternLine.perp_spacing`.
A family with an empty ``dashes`` tuple is continuous.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "HATCH_DEFS",
    "HatchDef",
    "HatchFamily",
    "MAX_HATCH_LINES",
    "PatternLine",
    "hatch_families",
    "is_solid",
    "pattern_keys",
]

#: A hatch is clipped to its outline, but the line family is generated across the
#: outline's bounding box first. A pathological ``spacing_mm`` over a plot-sized
#: region would otherwise emit unbounded geometry into a sheet. Generation stops
#: at this many lines per family and the caller gets what fits — a visibly coarse
#: hatch beats a 200 MB SVG, and :func:`hatch_families` is pure, so a test can
#: assert the cap fires rather than trusting it.
MAX_HATCH_LINES = 4_000


@dataclass(frozen=True)
class PatternLine:
    """One family of parallel lines within a pattern."""

    angle_deg: float
    base: tuple[float, float]
    offset: tuple[float, float]
    dashes: tuple[float, ...] = ()

    @property
    def perp_spacing(self) -> float:
        """Distance between adjacent lines of this family, in pattern units.

        ``offset`` is a vector in pattern space that steps from one line to the
        next; only its component normal to the line direction moves the line, so
        that component — not the vector's length — is the spacing. ANSI31 offsets
        by (-2.245, 2.245) at 45 deg, which is 3.175 mm of actual separation.
        """
        theta = math.radians(self.angle_deg)
        return abs(self.offset[0] * -math.sin(theta) + self.offset[1] * math.cos(theta))


@dataclass(frozen=True)
class HatchDef:
    """A named pattern: what DXF calls it, and the geometry both writers draw."""

    key: str
    acad_name: str
    label: str
    lines: tuple[PatternLine, ...]

    @property
    def base_spacing(self) -> float:
        """The pattern's characteristic spacing, used as the unit of scale.

        The first family's spacing, which is the CAD convention: scaling a
        pattern so this equals the authored ``spacing_mm`` is what makes an
        authored "150 mm hatch" measure 150 mm on the drawing.
        """
        return self.lines[0].perp_spacing if self.lines else 0.0

    @property
    def base_angle_deg(self) -> float:
        """The angle the pattern is *defined* at, which the author does not set.

        Subtracted from the authored angle before rotating, so ``angle_deg=45``
        on a pattern already drawn at 45 deg yields 45 deg, not 90.
        """
        return self.lines[0].angle_deg if self.lines else 0.0


#: ``solid`` has no line families — it is a fill, handled before any geometry is
#: generated. It is in the table so that "is this a known pattern?" has one
#: answer, and so the UI can list every fill in one place.
SOLID_KEY = "solid"


HATCH_DEFS: dict[str, HatchDef] = {
    SOLID_KEY: HatchDef(key=SOLID_KEY, acad_name="SOLID", label="Solid fill", lines=()),
    "diagonal": HatchDef(
        key="diagonal",
        acad_name="ANSI31",
        label="Diagonal / generic section",
        lines=(PatternLine(45.0, (0.0, 0.0), (-2.2450640303, 2.2450640303), ()),),
    ),
    "cross": HatchDef(
        key="cross",
        acad_name="ANSI37",
        label="Cross hatch",
        lines=(
            PatternLine(45.0, (0.0, 0.0), (-2.2450640303, 2.2450640303), ()),
            PatternLine(135.0, (0.0, 0.0), (-2.2450640303, -2.2450640303), ()),
        ),
    ),
    "earth": HatchDef(
        key="earth",
        acad_name="EARTH",
        label="Earth / soil",
        lines=(
            PatternLine(0.0, (0.0, 0.0), (6.35, 6.35), (6.35, -6.35)),
            PatternLine(0.0, (0.0, 2.38125), (6.35, 6.35), (6.35, -6.35)),
            PatternLine(0.0, (0.0, 4.7625), (6.35, 6.35), (6.35, -6.35)),
            PatternLine(90.0, (0.79375, 5.55625), (-6.35, 6.35), (6.35, -6.35)),
            PatternLine(90.0, (3.175, 5.55625), (-6.35, 6.35), (6.35, -6.35)),
            PatternLine(90.0, (5.55625, 5.55625), (-6.35, 6.35), (6.35, -6.35)),
        ),
    ),
    "brick": HatchDef(
        key="brick",
        acad_name="BRICK",
        label="Brick masonry",
        lines=(
            PatternLine(0.0, (0.0, 0.0), (0.0, 6.35), ()),
            PatternLine(90.0, (0.0, 0.0), (-12.7, 0.0), (6.35, -6.35)),
            PatternLine(90.0, (6.35, 0.0), (-12.7, 0.0), (-6.35, 6.35)),
        ),
    ),
    "concrete": HatchDef(
        key="concrete",
        acad_name="AR-CONC",
        label="Concrete (RCC)",
        lines=(
            PatternLine(50.0, (0.0, 0.0), (182.184668996, -15.9390855389), (19.05, -209.55)),
            PatternLine(355.0, (0.0, 0.0), (-35.243421425, 191.056845239), (15.24, -167.64058417)),
            PatternLine(
                100.4514447,
                (15.182007, -1.3282535),
                (146.9412470904, 175.1177519122),
                (16.1900088, -178.0902446),
            ),
            PatternLine(46.1842, (0.0, 50.8), (271.0790408921, -42.0423279327), (28.575, -314.325)),
            PatternLine(
                96.63555761,
                (22.5899, 47.2965),
                (237.4041340515, 247.4261245977),
                (24.28502314, -267.13560816),
            ),
            PatternLine(
                351.18415117,
                (0.0, 50.8),
                (237.404134065, 247.4261245855),
                (22.85996707, -251.45973192),
            ),
            PatternLine(21.0, (25.4, 38.1), (151.6143912691, -102.2651981871), (19.05, -209.55)),
            PatternLine(326.0, (25.4, 38.1), (61.8020283476, 184.1879661223), (15.24, -167.64)),
            PatternLine(
                71.451445,
                (38.0345326, 29.5779001),
                (213.4164192787, 81.9227688859),
                (16.1900088, -178.0899376),
            ),
            PatternLine(
                37.5,
                (0.0, 0.0),
                (3.0886032506, 84.5550388732),
                (0.0, -165.608, 0.0, -170.18, 0.0, -168.275),
            ),
            PatternLine(
                7.5,
                (0.0, 0.0),
                (66.8196625103, 100.1805748181),
                (0.0, -97.028, 0.0, -161.798, 0.0, -64.135),
            ),
            PatternLine(
                -32.5,
                (-56.642, 0.0),
                (135.5905951669, -5.7287439927),
                (0.0, -63.5, 0.0, -198.12, 0.0, -262.89),
            ),
            PatternLine(
                -42.5,
                (-82.042, 0.0),
                (148.129181386, 25.4264910333),
                (0.0, -82.55, 0.0, -131.572, 0.0, -186.69),
            ),
        ),
    ),
    "insulation": HatchDef(
        key="insulation",
        acad_name="INSUL",
        label="Insulation",
        lines=(
            PatternLine(0.0, (0.0, 0.0), (0.0, 9.525), ()),
            PatternLine(0.0, (0.0, 3.175), (0.0, 9.525), (3.175, -3.175)),
            PatternLine(0.0, (0.0, 6.35), (0.0, 9.525), (3.175, -3.175)),
        ),
    ),
    "plaster": HatchDef(
        key="plaster",
        acad_name="PLAST",
        label="Plaster",
        lines=(
            PatternLine(0.0, (0.0, 0.0), (0.0, 6.35), ()),
            PatternLine(0.0, (0.0, 0.79375), (0.0, 6.35), ()),
            PatternLine(0.0, (0.0, 1.5875), (0.0, 6.35), ()),
        ),
    ),
    "stone": HatchDef(
        key="stone",
        acad_name="BRSTONE",
        label="Stone masonry",
        lines=(
            PatternLine(0.0, (0.0, 0.0), (0.0, 8.382), ()),
            PatternLine(90.0, (22.86, 0.0), (-12.7, 8.382), (8.382, -8.382)),
            PatternLine(90.0, (20.32, 0.0), (-12.7, 8.382), (8.382, -8.382)),
            PatternLine(0.0, (22.86, 1.397), (12.7, 8.382), (-22.86, 2.54)),
            PatternLine(0.0, (22.86, 2.794), (12.7, 8.382), (-22.86, 2.54)),
            PatternLine(0.0, (22.86, 4.191), (12.7, 8.382), (-22.86, 2.54)),
            PatternLine(0.0, (22.86, 5.588), (12.7, 8.382), (-22.86, 2.54)),
            PatternLine(0.0, (22.86, 6.985), (12.7, 8.382), (-22.86, 2.54)),
        ),
    ),
    "steel": HatchDef(
        key="steel",
        acad_name="STEEL",
        label="Steel",
        lines=(
            PatternLine(45.0, (0.0, 0.0), (-5.3881536726, 5.3881536726), ()),
            PatternLine(45.0, (4.318, 0.0), (-5.3881536726, 5.3881536726), ()),
            PatternLine(45.0, (4.572, 0.0), (-5.3881536726, 5.3881536726), ()),
            PatternLine(45.0, (4.826, 0.0), (-5.3881536726, 5.3881536726), ()),
            PatternLine(45.0, (5.08, 0.0), (-5.3881536726, 5.3881536726), ()),
            PatternLine(45.0, (5.334, 0.0), (-5.3881536726, 5.3881536726), ()),
            PatternLine(45.0, (5.588, 0.0), (-5.3881536726, 5.3881536726), ()),
            PatternLine(45.0, (5.842, 0.0), (-5.3881536726, 5.3881536726), ()),
        ),
    ),
    "glass": HatchDef(
        key="glass",
        acad_name="GOST_GLASS",
        label="Glazing",
        lines=(
            PatternLine(45.0, (0.0, 0.0), (8.4852813742, 0.0), (5.0, -7.0)),
            PatternLine(45.0, (2.12132, 0.0), (8.4852813742, 0.0), (2.0, -10.0)),
            PatternLine(45.0, (0.0, 2.12132), (8.4852813742, 0.0), (2.0, -10.0)),
        ),
    ),
    "sand": HatchDef(
        key="sand",
        acad_name="AR-SAND",
        label="Sand filling",
        lines=(
            PatternLine(
                37.5,
                (0.0, 0.0),
                (-1.600031296, 48.9413237329),
                (0.0, -38.608, 0.0, -43.18, 0.0, -41.275),
            ),
            PatternLine(
                7.5,
                (0.0, 0.0),
                (44.9523283138, 71.6825100568),
                (0.0, -20.828, 0.0, -34.798, 0.0, -13.335),
            ),
            PatternLine(
                -32.5,
                (-31.242, 0.0),
                (79.0992370241, 0.1437184679),
                (0.0, -12.7, 0.0, -45.72, 0.0, -59.69),
            ),
            PatternLine(
                -42.5,
                (-31.242, 0.0),
                (76.3556452472, 22.2929323257),
                (0.0, -6.35, 0.0, -29.972, 0.0, -34.29),
            ),
        ),
    ),
    "timber": HatchDef(
        key="timber",
        acad_name="WOOD1",
        label="Timber",
        lines=(
            PatternLine(216.8699, (8.128, 20.32), (60.9599983302, 40.6400025047), (10.16, -91.44)),
            PatternLine(
                206.5651,
                (20.32, 14.224),
                (-20.3199828894, -20.3200266069),
                (22.71845088, -22.71845088),
            ),
            PatternLine(
                198.4349,
                (20.32, 4.064),
                (-40.6400088309, -20.3199675535),
                (12.85150592, -51.40598304),
            ),
        ),
    ),
    "tile": HatchDef(
        key="tile",
        acad_name="AR-HBONE",
        label="Tile flooring (herringbone)",
        lines=(
            PatternLine(45.0, (0.0, 0.0), (0.0, 143.6840979371), (304.8, -101.6)),
            PatternLine(135.0, (71.842, 71.842), (0.0, 143.6840979371), (304.8, -101.6)),
        ),
    ),
    "grass": HatchDef(
        key="grass",
        acad_name="GRASS",
        label="Grass / soft landscape",
        lines=(
            PatternLine(90.0, (0.0, 0.0), (-17.96051224, 17.96051224), (4.7625, -31.15852448)),
            PatternLine(45.0, (0.0, 0.0), (-17.9605122421, 17.9605122421), (4.7625, -20.6375)),
            PatternLine(135.0, (0.0, 0.0), (-17.9605122421, -17.9605122421), (4.7625, -20.6375)),
        ),
    ),
}


#: A zero-length ACAD dash is a *dot*. Neither SVG nor DXF has a dot, so it is
#: drawn as a very short dash. Expressed as a fraction of the dash cycle so it
#: stays dot-sized at every scale.
DOT_FRACTION = 0.02


def round_half_away(value: float) -> int:
    """Round to the nearest integer, halves away from zero.

    The model core's rounding contract, restated here rather than imported so
    ``render/`` keeps its one-way dependency on nothing but ``drawings``. Python's
    ``round`` goes to even, which is fine arithmetic and wrong for golden files.
    """
    return int(math.floor(value + 0.5)) if value >= 0 else -int(math.floor(-value + 0.5))


def pattern_keys() -> tuple[str, ...]:
    """Every pattern an author may name, sorted. The one list the UI reads."""
    return tuple(sorted(HATCH_DEFS))


def is_solid(key: str) -> bool:
    """True for fills that have no line geometry, so callers skip generation."""
    return not HATCH_DEFS[key].lines


@dataclass(frozen=True)
class HatchFamily:
    """Drawable lines sharing one dash cycle and one phase.

    Grouped by phase rather than emitted one element per line because a renderer
    states the cycle once per element: brick over a wall is three elements, not
    three hundred. Every length is integer model millimetres, like every other
    coordinate that reaches an output file.
    """

    #: ``((x0, y0), (x1, y1))`` pairs, integer model mm.
    segments: tuple[tuple[tuple[int, int], tuple[int, int]], ...]
    #: Positive on/off lengths in model mm, SVG ``stroke-dasharray`` order.
    #: Empty means a continuous line.
    dashes: tuple[int, ...]
    #: Where in the cycle each segment starts, model mm — SVG ``stroke-dashoffset``.
    dash_offset: int
    #: True when the cycle is dots, so the caller can round the line cap.
    dotted: bool


def _dash_cycle(dashes: tuple[float, ...], scale: float) -> tuple[tuple[int, ...], int, bool]:
    """Scale an ACAD dash list into positive on/off lengths in model mm.

    ACAD writes gaps as negatives and dots as zeros; SVG wants positive lengths
    alternating draw/skip. Returns the lengths, the cycle period, and whether the
    cycle is dots — a dot cycle needs a round line cap to look like dots.
    """
    if not dashes:
        return (), 0, False
    span = sum(abs(value) for value in dashes) * scale
    if span <= 0:
        return (), 0, False
    dot = max(1.0, span * DOT_FRACTION)
    lengths = tuple(max(1, round_half_away(abs(value) * scale or dot)) for value in dashes)
    return lengths, sum(lengths), all(value == 0 for value in dashes[::2])


def hatch_families(
    key: str,
    *,
    spacing: int,
    angle_deg: float,
    bbox: tuple[int, int, int, int],
    max_lines: int = MAX_HATCH_LINES,
) -> tuple[HatchFamily, ...]:
    """Generate a pattern's line geometry across ``bbox``, in model millimetres.

    ``spacing`` is what the author asked for: the distance between adjacent lines
    of the pattern's first family. ``angle_deg`` is the angle that first family
    should end up at — the pattern's own definition angle is removed first, so an
    author never has to know it.

    Lines are anchored at the model origin, not at ``bbox``, so two hatches that
    meet along a wall line up instead of stepping. The caller clips the result to
    the real outline; generating over the bounding box keeps this function pure
    and independent of how a format expresses clipping.
    """
    definition = HATCH_DEFS[key]
    base = definition.base_spacing
    if not definition.lines or base <= 0 or spacing <= 0:
        return ()
    scale = spacing / base
    rotation = math.radians(angle_deg - definition.base_angle_deg)
    cos_r, sin_r = math.cos(rotation), math.sin(rotation)

    def rotated(point: tuple[float, float]) -> tuple[float, float]:
        return (point[0] * cos_r - point[1] * sin_r, point[0] * sin_r + point[1] * cos_r)

    x0, y0, x1, y1 = bbox
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    out: list[HatchFamily] = []

    for family in definition.lines:
        angle = math.radians(family.angle_deg) + rotation
        along = (math.cos(angle), math.sin(angle))
        normal = (-along[1], along[0])
        anchor = rotated((family.base[0] * scale, family.base[1] * scale))
        step_vec = rotated((family.offset[0] * scale, family.offset[1] * scale))
        step = step_vec[0] * normal[0] + step_vec[1] * normal[1]
        if abs(step) < 1e-9:
            # Offset parallel to the family's own lines: every line would land on
            # top of the last. ACAD treats such a family as degenerate too.
            continue
        drift = step_vec[0] * along[0] + step_vec[1] * along[1]
        anchor_v = anchor[0] * normal[0] + anchor[1] * normal[1]
        anchor_u = anchor[0] * along[0] + anchor[1] * along[1]
        vs = [c[0] * normal[0] + c[1] * normal[1] for c in corners]
        us = [c[0] * along[0] + c[1] * along[1] for c in corners]
        lo = (min(vs) - anchor_v) / step
        hi = (max(vs) - anchor_v) / step
        first, last = math.floor(min(lo, hi)), math.ceil(max(lo, hi))
        if last - first + 1 > max_lines:
            last = first + max_lines - 1
        u_start, u_end = min(us), max(us)
        dashes, period, dotted = _dash_cycle(family.dashes, scale)

        by_phase: dict[int, list[tuple[tuple[int, int], tuple[int, int]]]] = {}
        for index in range(first, last + 1):
            v = anchor_v + index * step
            segment = (
                (
                    round_half_away(normal[0] * v + along[0] * u_start),
                    round_half_away(normal[1] * v + along[1] * u_start),
                ),
                (
                    round_half_away(normal[0] * v + along[0] * u_end),
                    round_half_away(normal[1] * v + along[1] * u_end),
                ),
            )
            phase = 0
            if period > 0:
                phase = round_half_away(u_start - (anchor_u + index * drift)) % period
            by_phase.setdefault(phase, []).append(segment)

        for phase in sorted(by_phase):
            out.append(
                HatchFamily(
                    segments=tuple(by_phase[phase]),
                    dashes=dashes,
                    dash_offset=phase,
                    dotted=dotted,
                )
            )
    return tuple(out)
