"""The 2D primitive vocabulary every §7 renderer consumes. **Fully implemented, pure.**

§7's pipeline is::

    model -> 2D projection primitives (lines/arcs/text/hatches with layer tags)
          -> SVG (screen + PDF via headless print) and DXF (ezdxf, mm units, layers ...)

This module is the arrow in the middle. It is the **only** place where a geometry
decision is allowed to be made once and shared: the SVG renderer and the DXF writer
both consume these dataclasses, so a sheet cannot come out of one format with a
different door swing than the other. That is not a style preference — DXF cannot be
executed on the build machine (``ezdxf`` is pinned but not installed), so every
geometric decision that lives in the DXF writer is a decision that ships untested.
Keeping them here, in pure integer arithmetic, is what makes the DXF path thin enough
to trust.

Three rules hold throughout and are load-bearing:

1. **Model geometry is integer millimetres.** Positions, radii, lengths: ``int`` mm,
   never float. Same reason the model core is integer mm — a dimension chain that has
   to sum exactly cannot be built on values that drifted.
2. **Paper sizes are integer micrometres of paper** (``2500`` = 2.5 mm on the printed
   sheet). Text height, line weight and dash patterns are properties of the *print*,
   not of the building: ISO 3098 says 2.5 mm text whatever the scale. Two units in one
   file is a hazard, so every paper-space number carries ``_paper_um`` in its name.
3. **Nothing here knows about SVG, DXF or PDF.** A primitive that needed to know would
   be a primitive in the wrong place.

:class:`Placement` is the single bridge between the two units, and it is integer-exact:
model mm -> paper µm with the Y flip that turns building coordinates (Y up) into sheet
coordinates (Y down) baked in, so no renderer has to remember to flip and no text ends
up mirrored.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from services.drawings.dimensions import DimChain
from services.drawings.layers import LAYER_NAMES, LAYERS_BY_NAME, layer_for

#: A point in **model millimetres**.
Pt2 = tuple[int, int]

# ---------------------------------------------------------------------------
# Paper-space constants (§7 / ISO 3098). All in micrometres of paper.
# ---------------------------------------------------------------------------
#: ISO 3098 body text. Room names, dimension text, schedule cells.
TEXT_HEIGHT_PAPER_UM = 2_500
#: Smaller run: opening tags, level markers, table sub-cells.
TEXT_HEIGHT_SMALL_PAPER_UM = 2_000
#: Sheet title, area-statement heading.
TEXT_HEIGHT_TITLE_PAPER_UM = 5_000
#: Title-block field labels.
TEXT_HEIGHT_LABEL_PAPER_UM = 1_800

#: §7 step 4's "shrink text one step" — the ladder, largest first.
TEXT_HEIGHT_LADDER_PAPER_UM: tuple[int, ...] = (2_500, 2_000, 1_800, 1_400)

#: Dimension tick (the oblique architectural stroke) half-length, paper µm.
DIM_TICK_PAPER_UM = 1_250
#: How far a witness line overshoots the dimension line (ezdxf ``dimexe``).
DIM_EXTENSION_PAPER_UM = 1_250
#: Gap between the measured object and the start of its witness line (``dimexo``).
DIM_OFFSET_PAPER_UM = 1_000
#: Gap between dimension line and its text (``dimgap``).
DIM_TEXT_GAP_PAPER_UM = 1_000

LineStyle = str
#: Line styles, mapped to dash patterns in **paper µm** by the renderers.
STYLE_SOLID: LineStyle = "solid"
STYLE_DASHED: LineStyle = "dashed"
STYLE_HIDDEN: LineStyle = "hidden"
STYLE_CENTRE: LineStyle = "centre"

#: Dash patterns keyed by style. Empty tuple = continuous.
DASH_PATTERNS_PAPER_UM: Mapping[LineStyle, tuple[int, ...]] = {
    STYLE_SOLID: (),
    STYLE_DASHED: (3_000, 1_500),
    STYLE_HIDDEN: (1_500, 1_000),
    STYLE_CENTRE: (8_000, 1_500, 1_500, 1_500),
}

#: Hatch patterns §7 needs: solid fill for cut walls, 45° for concrete/section fill.
HatchPattern = str
HATCH_SOLID: HatchPattern = "solid"
HATCH_DIAGONAL: HatchPattern = "diagonal"
HATCH_CROSS: HatchPattern = "cross"
HATCH_EARTH: HatchPattern = "earth"

#: Text anchoring. ``start``/``middle``/``end`` along the text direction.
TextAnchor = str
#: Vertical placement relative to the insertion point.
TextBaseline = str


def _check_layer(name: str) -> str:
    """Every primitive's layer must be one of the nine. Typos fail here, loudly."""
    layer_for(name)
    return name


def _check_style(style: LineStyle) -> LineStyle:
    if style not in DASH_PATTERNS_PAPER_UM:
        raise ValueError(
            "%r is not a line style. Expected one of: %s."
            % (style, ", ".join(sorted(DASH_PATTERNS_PAPER_UM)))
        )
    return style


# ---------------------------------------------------------------------------
# The primitives
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Line:
    """A straight segment in model mm."""

    a: Pt2
    b: Pt2
    layer: str
    style: LineStyle = STYLE_SOLID
    #: Model element this came from, so annotations and pickers can trace back.
    element_id: str | None = None

    def __post_init__(self) -> None:
        _check_layer(self.layer)
        _check_style(self.style)

    def points(self) -> tuple[Pt2, ...]:
        return (self.a, self.b)


@dataclass(frozen=True)
class Polyline:
    """An open or closed run of segments in model mm."""

    vertices: tuple[Pt2, ...]
    layer: str
    closed: bool = False
    style: LineStyle = STYLE_SOLID
    element_id: str | None = None

    def __post_init__(self) -> None:
        _check_layer(self.layer)
        _check_style(self.style)
        if len(self.vertices) < 2:
            raise ValueError("a polyline needs at least 2 vertices, got %d" % len(self.vertices))

    def points(self) -> tuple[Pt2, ...]:
        return self.vertices


@dataclass(frozen=True)
class Arc:
    """A circular arc, CCW from ``start_deg`` to ``end_deg`` (integer degrees).

    Integer degrees on purpose: a door swing is drawn at 0/90/180/270 and the
    quarter-circle it sweeps is exactly 90°. Nothing §7 draws needs a fraction of a
    degree, and integers keep the SVG bytes stable.
    """

    centre: Pt2
    radius_mm: int
    start_deg: int
    end_deg: int
    layer: str
    style: LineStyle = STYLE_SOLID
    element_id: str | None = None

    def __post_init__(self) -> None:
        _check_layer(self.layer)
        _check_style(self.style)
        if self.radius_mm <= 0:
            raise ValueError("arc radius must be positive, got %d" % self.radius_mm)

    def points(self) -> tuple[Pt2, ...]:
        """Bounding points for extent purposes — the centre plus the radius box."""
        cx, cy = self.centre
        r = self.radius_mm
        return ((cx - r, cy - r), (cx + r, cy + r))


@dataclass(frozen=True)
class Circle:
    centre: Pt2
    radius_mm: int
    layer: str
    style: LineStyle = STYLE_SOLID
    element_id: str | None = None

    def __post_init__(self) -> None:
        _check_layer(self.layer)
        _check_style(self.style)
        if self.radius_mm <= 0:
            raise ValueError("circle radius must be positive, got %d" % self.radius_mm)

    def points(self) -> tuple[Pt2, ...]:
        cx, cy = self.centre
        r = self.radius_mm
        return ((cx - r, cy - r), (cx + r, cy + r))


@dataclass(frozen=True)
class Text:
    """A single line of text placed at a model-mm point, sized in paper µm.

    ``text`` is raw: it has *not* been escaped. Escaping is the renderer's job (each
    output format escapes differently) and doing it here would double-escape in one of
    them. :mod:`services.drawings.render.sanitize` is where that happens for SVG.
    """

    at: Pt2
    text: str
    layer: str
    height_paper_um: int = TEXT_HEIGHT_PAPER_UM
    anchor: TextAnchor = "start"
    baseline: TextBaseline = "baseline"
    #: Integer degrees CCW. §7 only ever needs 0 and 90 (vertical dimension runs).
    rotation_deg: int = 0
    element_id: str | None = None
    bold: bool = False

    def __post_init__(self) -> None:
        _check_layer(self.layer)
        if self.height_paper_um <= 0:
            raise ValueError("text height must be positive, got %d" % self.height_paper_um)
        if self.anchor not in ("start", "middle", "end"):
            raise ValueError("text anchor must be start|middle|end, got %r" % self.anchor)
        if self.baseline not in ("baseline", "middle", "hanging"):
            raise ValueError(
                "text baseline must be baseline|middle|hanging, got %r" % self.baseline
            )
        if "\n" in self.text or "\r" in self.text:
            raise ValueError(
                "Text is one line. Split multi-line content into separate Text "
                "primitives so the collision grid can see each line's box."
            )

    def points(self) -> tuple[Pt2, ...]:
        return (self.at,)


@dataclass(frozen=True)
class Hatch:
    """A filled/hatched region. ``outline`` is a closed ring in model mm.

    §7: "walls as double lines w/ thickness (fill hatch external)". A cut wall on a
    municipal plan is solid-filled; a section's earth line is hatched.
    """

    outline: tuple[Pt2, ...]
    layer: str
    pattern: HatchPattern = HATCH_SOLID
    #: Hatch line spacing in **model mm** — a hatch is drawn at model scale, unlike text.
    spacing_mm: int = 200
    #: Integer degrees for the hatch line direction.
    angle_deg: int = 45
    holes: tuple[tuple[Pt2, ...], ...] = ()
    element_id: str | None = None

    def __post_init__(self) -> None:
        _check_layer(self.layer)
        if len(self.outline) < 3:
            raise ValueError("a hatch outline needs at least 3 vertices")
        if self.pattern not in (HATCH_SOLID, HATCH_DIAGONAL, HATCH_CROSS, HATCH_EARTH):
            raise ValueError("unknown hatch pattern %r" % self.pattern)
        if self.spacing_mm <= 0:
            raise ValueError("hatch spacing must be positive, got %d" % self.spacing_mm)

    def points(self) -> tuple[Pt2, ...]:
        return self.outline


@dataclass(frozen=True)
class Dim:
    """A dimension chain, carried whole rather than pre-exploded into lines.

    Why whole: DXF has a native ``DIMENSION`` entity that a CAD user can select, edit
    and re-associate. Exploding to lines here would throw that away and hand a
    municipal reviewer a drawing whose dimensions are dumb geometry. SVG has no such
    entity, so :func:`dim_geometry` explodes it — one function, so both formats put the
    witness lines and the text in the same place.

    The chain itself is a :class:`~services.drawings.dimensions.DimChain`, which owns
    the §7 step-5 invariant (segments sum exactly to the overall).
    """

    chain: DimChain
    layer: str = "A-DIM"
    text_height_paper_um: int = TEXT_HEIGHT_PAPER_UM
    #: True when the chain also prints the overall dimension as a second run.
    show_overall: bool = False

    def __post_init__(self) -> None:
        _check_layer(self.layer)

    def points(self) -> tuple[Pt2, ...]:
        """Chain extent, for sheet-extent purposes."""
        chain = self.chain
        start = chain.origin_mm
        end = chain.origin_mm + chain.overall_mm
        if chain.orientation == "horizontal":
            return ((start, chain.offset_mm), (end, chain.offset_mm))
        return ((chain.offset_mm, start), (chain.offset_mm, end))


#: Everything a renderer must know how to draw.
Primitive = Line | Polyline | Arc | Circle | Text | Hatch | Dim

PRIMITIVE_KINDS: tuple[str, ...] = (
    "Line",
    "Polyline",
    "Arc",
    "Circle",
    "Text",
    "Hatch",
    "Dim",
)


# ---------------------------------------------------------------------------
# Exploding a dimension chain — the ONE implementation both formats share
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DimTick:
    """One tick + witness line + label of an exploded chain, all in model mm."""

    #: Along-axis position of the tick.
    at_mm: int
    #: Witness line from the measured face to just past the dimension line.
    witness_a: Pt2
    witness_b: Pt2


@dataclass(frozen=True)
class DimGeometry:
    """A chain exploded into drawable pieces. Model mm, integers throughout."""

    #: The continuous dimension line.
    line_a: Pt2
    line_b: Pt2
    ticks: tuple[DimTick, ...]
    #: ``(centre_point, text, rotation_deg)`` per segment.
    labels: tuple[tuple[Pt2, str, int], ...]

    def all_points(self) -> tuple[Pt2, ...]:
        pts: list[Pt2] = [self.line_a, self.line_b]
        for tick in self.ticks:
            pts.extend((tick.witness_a, tick.witness_b))
        pts.extend(point for point, _text, _rot in self.labels)
        return tuple(pts)


def dim_geometry(
    dim: Dim,
    *,
    scale_denominator: int,
    witness_from_mm: int | None = None,
) -> DimGeometry:
    """Explode a chain into dimension line, witness lines, ticks and labels.

    Paper-space sizes (tick length, witness overshoot, text gap) are multiplied by
    ``scale_denominator`` so a tick is the same physical length on paper at 1:50 and
    1:200 — the whole reason those constants are in paper µm.

    ``witness_from_mm`` is the perpendicular coordinate of the measured face (the
    building line); witness lines run from there to just past the dimension line. When
    omitted they start at the chain's own offset, which draws a bare chain — correct
    for inner room dims, where the face is the chain.
    """
    chain = dim.chain
    if not chain.is_consistent():
        raise ValueError(
            "Refusing to draw chain %s: its segments sum to %d but the overall is %d. "
            "§7 step 5 is an exact equality, and a drawing is where a wrong number "
            "becomes a built mistake." % (chain.id, chain.sum_of_segments(), chain.overall_mm)
        )
    horizontal = chain.orientation == "horizontal"
    overshoot = _paper_to_model(DIM_EXTENSION_PAPER_UM, scale_denominator)
    gap = _paper_to_model(DIM_TEXT_GAP_PAPER_UM, scale_denominator)

    offset = chain.offset_mm
    start = chain.origin_mm
    end = chain.origin_mm + chain.overall_mm
    face = offset if witness_from_mm is None else witness_from_mm
    # The witness line runs from the face, past the dimension line, by `overshoot`.
    beyond = offset + (overshoot if offset >= face else -overshoot)

    if horizontal:
        line_a: Pt2 = (start, offset)
        line_b: Pt2 = (end, offset)
    else:
        line_a = (offset, start)
        line_b = (offset, end)

    positions: list[int] = [start]
    for segment in chain.segments:
        positions.append(chain.origin_mm + segment.end_mm)

    ticks: list[DimTick] = []
    for position in positions:
        if horizontal:
            ticks.append(DimTick(position, (position, face), (position, beyond)))
        else:
            ticks.append(DimTick(position, (face, position), (beyond, position)))

    labels: list[tuple[Pt2, str, int]] = []
    for segment in chain.segments:
        centre = chain.origin_mm + segment.start_mm + segment.length_mm // 2
        if horizontal:
            labels.append(((centre, offset + gap), segment.label(), 0))
        else:
            labels.append(((offset + gap, centre), segment.label(), 90))

    # Note what is NOT computed here: the tick mark itself. Its length is
    # :data:`DIM_TICK_PAPER_UM` in paper space, and each format draws it its own way — SVG
    # as part of the witness line, DXF via the DIMSTYLE's ``dimtsz``. Baking a tick
    # polyline into the shared geometry would make the DXF draw ticks twice.
    return DimGeometry(line_a=line_a, line_b=line_b, ticks=tuple(ticks), labels=tuple(labels))


def _paper_to_model(paper_um: int, scale_denominator: int) -> int:
    """Paper µm -> model mm at a scale. ``2500 µm`` at 1:100 is ``250 mm``."""
    if scale_denominator <= 0:
        raise ValueError("scale denominator must be positive, got %d" % scale_denominator)
    return div_round(paper_um * scale_denominator, 1000)


# ---------------------------------------------------------------------------
# Integer rounding, used everywhere a division happens
# ---------------------------------------------------------------------------
def div_round(numerator: int, denominator: int) -> int:
    """Integer division, rounding halves away from zero. Deterministic on every host.

    Every coordinate that reaches an output file goes through here. ``round()`` would
    not do: Python rounds halves to even, which is fine arithmetic and terrible for
    golden files, because a 0.5 that lands differently in a future Python is a byte
    diff nobody can explain.
    """
    if denominator == 0:
        raise ZeroDivisionError("div_round denominator is zero")
    if denominator < 0:
        numerator, denominator = -numerator, -denominator
    if numerator >= 0:
        return (numerator * 2 + denominator) // (denominator * 2)
    return -((-numerator * 2 + denominator) // (denominator * 2))


# ---------------------------------------------------------------------------
# Placement: model mm -> paper µm, with the Y flip
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Placement:
    """Where a model-space drawing sits on the sheet, and at what scale.

    The transform is::

        paper_x_um = origin_paper_um[0] + (model_x - origin_model_mm[0]) * 1000 / scale
        paper_y_um = origin_paper_um[1] - (model_y - origin_model_mm[1]) * 1000 / scale

    Note the minus. Building coordinates run Y-up; every sheet format this project
    writes runs Y-down from the top-left. Flipping *here*, numerically, rather than
    with an output-format transform, is deliberate: a ``scale(1,-1)`` in SVG mirrors
    the text too, and remembering to counter-flip every text element is exactly the
    kind of thing that gets forgotten in one branch.

    ``scale_denominator`` of 1 with ``flip_y=False`` gives a paper-space placement:
    model mm are already paper mm. That is how the frame, title block and schedule
    tables share one pipeline with the drawings.
    """

    scale_denominator: int
    #: Model point that maps to ``origin_paper_um``.
    origin_model_mm: Pt2 = (0, 0)
    #: Its position on the sheet, in paper µm from the sheet's top-left.
    origin_paper_um: Pt2 = (0, 0)
    flip_y: bool = True

    def __post_init__(self) -> None:
        if self.scale_denominator <= 0:
            raise ValueError("scale denominator must be positive, got %d" % self.scale_denominator)

    @classmethod
    def paper(cls, *, origin_paper_um: Pt2 = (0, 0)) -> Placement:
        """A 1:1, unflipped placement: input is already paper mm from the top-left."""
        return cls(
            scale_denominator=1,
            origin_model_mm=(0, 0),
            origin_paper_um=origin_paper_um,
            flip_y=False,
        )

    def to_paper_um(self, point: Pt2) -> Pt2:
        x_mm, y_mm = point
        ox_mm, oy_mm = self.origin_model_mm
        ox_um, oy_um = self.origin_paper_um
        dx = div_round((x_mm - ox_mm) * 1000, self.scale_denominator)
        dy = div_round((y_mm - oy_mm) * 1000, self.scale_denominator)
        return (ox_um + dx, oy_um - dy if self.flip_y else oy_um + dy)

    def length_to_paper_um(self, length_mm: int) -> int:
        """A length, with no origin and no flip."""
        return div_round(length_mm * 1000, self.scale_denominator)

    def paper_um_to_model_mm(self, paper_um: int) -> int:
        """Inverse of a length, for placing paper-sized things in model space."""
        return div_round(paper_um * self.scale_denominator, 1000)


# ---------------------------------------------------------------------------
# Grouping primitives into a sheet
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DrawingGroup:
    """One placed drawing on a sheet: a plan, an elevation, a table, the frame.

    A sheet has several. §7's "Elevations (all 4)" is one sheet with four groups, and
    "Floor Plans" is one sheet with one group per storey — each with its own placement,
    so a G+2 does not need three sheets and the ground floor does not have to know
    where the first floor sits.
    """

    id: str
    placement: Placement
    primitives: tuple[Primitive, ...]
    #: Printed under the drawing ("GROUND FLOOR PLAN — 1:100"). Empty for the frame.
    label: str = ""
    #: Where the label goes, in the group's own coordinate space.
    label_at: Pt2 | None = None

    def extent_model_mm(self) -> tuple[int, int, int, int] | None:
        """``(min_x, min_y, max_x, max_y)`` over every primitive, or None if empty."""
        xs: list[int] = []
        ys: list[int] = []
        for primitive in self.primitives:
            if isinstance(primitive, Dim):
                points: Iterable[Pt2] = primitive.points()
            else:
                points = primitive.points()
            for x, y in points:
                xs.append(x)
                ys.append(y)
        if not xs:
            return None
        return (min(xs), min(ys), max(xs), max(ys))


@dataclass(frozen=True)
class SheetDrawing:
    """A sheet ready to be written out in any format.

    This is the hand-off point between the §7 sheet engine and this package: whoever
    builds sheets produces one of these, and every renderer and exporter in
    ``services.drawings.render`` / ``.export`` consumes it without knowing how it was
    built.
    """

    #: The :class:`~services.drawings.sheets.Sheet` this draws.
    sheet: object
    groups: tuple[DrawingGroup, ...]
    #: Chains asserted by :func:`~services.drawings.dimensions.assert_chains_sum`.
    chains: tuple[DimChain, ...] = ()
    #: Free-form provenance, e.g. ``{"modelStateHash": "..."}``. Never a timestamp:
    #: goldens are byte-compared and a timestamp makes every run a diff.
    meta: Mapping[str, str] = field(default_factory=dict)

    def group(self, group_id: str) -> DrawingGroup:
        for group in self.groups:
            if group.id == group_id:
                return group
        raise KeyError(
            "no drawing group %r on sheet (have: %s)"
            % (group_id, ", ".join(g.id for g in self.groups))
        )

    def primitive_count(self) -> int:
        return sum(len(group.primitives) for group in self.groups)

    def layers_used(self) -> tuple[str, ...]:
        """Layers actually drawn on, in :data:`LAYERS` order."""
        used = set()
        for group in self.groups:
            for primitive in group.primitives:
                used.add(primitive.layer)
        return tuple(name for name in LAYER_NAMES if name in used)


def sort_by_layer(primitives: Sequence[Primitive]) -> tuple[Primitive, ...]:
    """Stable sort into :data:`LAYERS` order, preserving insertion order within a layer.

    Determinism requirement, not aesthetics: SVG goldens are byte-compared, so the
    emission order has to be a function of the content and nothing else. Draw order
    also happens to matter — hatches must go down before the lines that bound them, and
    :data:`~services.drawings.layers.LAYERS` is already in that order.
    """
    order = {name: index for index, name in enumerate(LAYER_NAMES)}
    return tuple(sorted(primitives, key=lambda p: order[p.layer]))


def layer_lineweight_paper_um(layer_name: str) -> int:
    """A layer's printed line weight in paper µm (``LayerSpec.lineweight`` is 1/100 mm)."""
    spec = LAYERS_BY_NAME[layer_name]
    if spec.lineweight < 0:
        return 130
    return spec.lineweight * 10


__all__ = [
    "DASH_PATTERNS_PAPER_UM",
    "DIM_EXTENSION_PAPER_UM",
    "DIM_OFFSET_PAPER_UM",
    "DIM_TEXT_GAP_PAPER_UM",
    "DIM_TICK_PAPER_UM",
    "HATCH_CROSS",
    "HATCH_DIAGONAL",
    "HATCH_EARTH",
    "HATCH_SOLID",
    "PRIMITIVE_KINDS",
    "STYLE_CENTRE",
    "STYLE_DASHED",
    "STYLE_HIDDEN",
    "STYLE_SOLID",
    "TEXT_HEIGHT_LABEL_PAPER_UM",
    "TEXT_HEIGHT_LADDER_PAPER_UM",
    "TEXT_HEIGHT_PAPER_UM",
    "TEXT_HEIGHT_SMALL_PAPER_UM",
    "TEXT_HEIGHT_TITLE_PAPER_UM",
    "Arc",
    "Circle",
    "Dim",
    "DimGeometry",
    "DimTick",
    "DrawingGroup",
    "Hatch",
    "HatchPattern",
    "Line",
    "LineStyle",
    "Placement",
    "Polyline",
    "Primitive",
    "Pt2",
    "SheetDrawing",
    "Text",
    "TextAnchor",
    "TextBaseline",
    "dim_geometry",
    "div_round",
    "layer_lineweight_paper_um",
    "sort_by_layer",
]
