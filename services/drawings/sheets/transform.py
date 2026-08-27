"""The model→paper transform. **The only place a coordinate changes space.** §7.

    Frame/title-block geometry in mm at paper scale, with the mm-to-paper transform in
    ONE place and documented: drawing coordinates are model mm; paper offsets like the
    2400/1800/1200 dimension-chain offsets are paper-scaled — do not confuse them; a
    wrong scale here silently ruins every sheet.

THREE SPACES, NAMED APART
-------------------------
``model mm``
    The building. Plot-local integer millimetres, origin at the plot's SW corner,
    +X east, +Y north. Projectors emit this and never anything else.
``paper mm``
    Human sheet numbers: A2 is 594 × 420, the margins are 20/10/10/10, the title block
    is 180 × 60. Declared as ``int``/``float`` constants in ``frame.py`` because that is
    how a drafter talks about a sheet.
``paper µm``
    What composed geometry is measured in — paper micrometres, 1000 to the millimetre.

WHY MICROMETRES, AND WHY IT MATTERS
-----------------------------------
A composed sheet has to keep integer coordinates: floats in an SVG make golden files
platform-dependent (``repr`` differs, and §16 byte-diffs them), and floats in geometry
are the drift that integer millimetres exist to prevent. But whole paper millimetres are
far too coarse — at 1:100 one paper mm *is* 100mm of building, so rounding a paper
coordinate to a millimetre would shift a wall by half a brick.

Micrometres solve both: 1µm on paper is well under a plotter's 25µm resolution, and at
every scale this project uses the conversion is exact — 1:50 → ×20, 1:100 → ×10,
1:200 → ×5. Nothing rounds, nothing drifts, and the golden files are byte-stable.

THE OTHER HALF OF THE TRANSFORM
-------------------------------
This module owns *coordinates*. The **scalar** relation (a size in paper mm ↔ a size in
model mm, which is what sets text heights and hatch spacing) lives in
``services.drawings.projection.style``, on the projection side of the dependency arrow,
because the projectors need it and must not import the sheet model. Both are the same
one-line relation ``paper = model / denominator``; neither restates the other.

WHAT IS PAPER-SCALED AND WHAT IS NOT
------------------------------------
§7's dimension-chain offsets — 2400 / 1800 / 1200 mm from the building line — are
*paper-scaled numbers written as model mm at 1:100*. They mean 24 / 18 / 12 mm of paper,
so at 1:50 the model offsets halve. :func:`dim_chain_offset_model_mm` is the conversion,
derived from the constants in ``services.drawings.dimensions`` so there is one set of
numbers, not two.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.drawings.projection.primitives import Point, round_half_away
from services.drawings.sheets.model import Frame, Scale

#: Paper micrometres per paper millimetre.
PAPER_UM_PER_MM = 1000

#: The scale §7's dimension offsets are quoted at. 2400mm of model = 24mm of paper here.
DIM_REFERENCE_DENOMINATOR = 100


def paper_mm_to_um(paper_mm: float) -> int:
    """Paper millimetres (a human sheet number) → paper micrometres."""
    return round_half_away(paper_mm * PAPER_UM_PER_MM)


def paper_um_to_mm(paper_um: int) -> float:
    """Paper micrometres → paper millimetres. Lossy on purpose: for display and logs."""
    return paper_um / PAPER_UM_PER_MM


def scale_denominator_of(scale: Any) -> int:
    """Accept a :class:`Scale` or a bare int denominator.

    ``int`` is tested first: every Python int has a ``.denominator`` attribute (ints are
    Rational, and ``(100).denominator == 1``), so reading the attribute blind turns
    1:100 into 1:1.
    """
    if isinstance(scale, bool):
        raise TypeError("scale must be a Scale or an int denominator, got %r" % (scale,))
    denominator = scale if isinstance(scale, int) else getattr(scale, "denominator", None)
    if isinstance(denominator, bool) or not isinstance(denominator, int) or denominator <= 0:
        raise TypeError(
            "scale must be a Scale or a positive int denominator (100 for 1:100), got %r" % (scale,)
        )
    return denominator


@dataclass(frozen=True)
class PaperTransform:
    """Model mm → paper µm for one sheet.

    An affine map with no rotation and no mirroring: ``paper = origin + (model - anchor)
    / denominator``. Plans are drawn north-up and elevations are drawn as projected, so a
    sheet never rotates its content — a rotated plan is a different projection, not a
    different transform.

    Paper Y points **up**, matching DXF and PDF. The SVG renderer is the one place that
    flips it (SVG's Y grows downward), and it does that in its own viewBox rather than
    here, so DXF and PDF are not paying for SVG's convention.
    """

    scale_denominator: int
    #: The model point that lands on :attr:`paper_origin_um`.
    model_anchor_mm: Point = (0, 0)
    #: Where that point sits on the sheet, in paper µm from the sheet's bottom-left.
    paper_origin_um: Point = (0, 0)

    def __post_init__(self) -> None:
        if not isinstance(self.scale_denominator, int) or self.scale_denominator <= 0:
            raise ValueError(
                "scale denominator must be a positive int, got %r" % (self.scale_denominator,)
            )

    @property
    def scale(self) -> Scale:
        return Scale(self.scale_denominator)

    @property
    def label(self) -> str:
        return "1:%d" % self.scale_denominator

    # -- lengths ---------------------------------------------------------
    def length_to_paper_um(self, model_mm: int) -> int:
        """A model length as paper µm. Exact at 1:50/1:100/1:200."""
        return round_half_away(model_mm * PAPER_UM_PER_MM / self.scale_denominator)

    def length_to_model_mm(self, paper_um: int) -> int:
        """A paper length back to model mm — for hit-testing a click on a sheet."""
        return round_half_away(paper_um * self.scale_denominator / PAPER_UM_PER_MM)

    # -- points ----------------------------------------------------------
    def point_to_paper(self, model_point: Point) -> Point:
        """Model mm → paper µm."""
        ax, ay = self.model_anchor_mm
        ox, oy = self.paper_origin_um
        return (
            ox + self.length_to_paper_um(model_point[0] - ax),
            oy + self.length_to_paper_um(model_point[1] - ay),
        )

    def point_to_model(self, paper_point: Point) -> Point:
        """Paper µm → model mm. The inverse, to within the µm grid."""
        ax, ay = self.model_anchor_mm
        ox, oy = self.paper_origin_um
        return (
            ax + self.length_to_model_mm(paper_point[0] - ox),
            ay + self.length_to_model_mm(paper_point[1] - oy),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "scale": self.scale_denominator,
            "scaleLabel": self.label,
            "modelAnchorMm": list(self.model_anchor_mm),
            "paperOriginUm": list(self.paper_origin_um),
            "paperUmPerMm": PAPER_UM_PER_MM,
        }


# ---------------------------------------------------------------------------
# Fitting a drawing into a frame
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Fit:
    """The result of fitting a model extent into a frame at a fixed scale.

    ``fits`` is honest rather than corrective. §7 sheets are **print-true**: the scale is
    a promise a reviewer measures against, so a drawing that does not fit must not be
    silently shrunk. The sheet generator reports it and suggests the next scale down —
    golden rule 9, "errors say what to do next".
    """

    transform: PaperTransform
    fits: bool
    required_paper_mm: tuple[float, float]
    available_paper_mm: tuple[float, float]

    def suggested_denominator(self) -> int:
        """The smallest standard denominator that would fit, or the current one."""
        if self.fits:
            return self.transform.scale_denominator
        need_x, need_y = self.required_paper_mm
        have_x, have_y = self.available_paper_mm
        if have_x <= 0 or have_y <= 0:
            return self.transform.scale_denominator
        factor = max(need_x / have_x, need_y / have_y)
        for candidate in (50, 100, 150, 200, 250, 500):
            if candidate >= self.transform.scale_denominator * factor:
                return candidate
        return self.transform.scale_denominator


#: Clear space left between the drawing and the frame/title block when auto-fitting.
#: 10mm of paper is enough for the outermost dimension chain's text to breathe.
FIT_PADDING_MM = 10.0


def drawable_area_paper_mm(
    frame: Frame, *, reserve_title_block: bool = True
) -> tuple[float, float, float, float]:
    """``(x, y, width, height)`` of the usable area in paper mm, origin bottom-left.

    The title block sits in the bottom-right corner, so reserving it means the drawing
    area stops above it — the full width stays usable higher up the sheet, which is why
    the reserved height is subtracted rather than the reserved width.
    """
    x = float(frame.margin_left_mm)
    y = float(frame.margin_bottom_mm)
    width = float(frame.drawable_width_mm())
    height = float(frame.drawable_height_mm())
    if reserve_title_block:
        height -= float(frame.title_block_height_mm)
        y += float(frame.title_block_height_mm)
    return (x, y, width, height)


def fit_to_frame(
    extent_model_mm: tuple[int, int, int, int] | None,
    frame: Frame,
    scale: Any,
    *,
    reserve_title_block: bool = True,
    padding_mm: float = FIT_PADDING_MM,
    offset_mm: tuple[int, int] = (0, 0),
) -> Fit:
    """Centre a model extent in a frame at a fixed scale, and say whether it fits.

    ``offset_mm`` is the sheet's ``Viewport.offset_mm`` — a paper-mm nudge the architect
    applies when a sheet holds two drawings side by side.
    """
    denominator = scale_denominator_of(scale)
    area_x, area_y, area_w, area_h = drawable_area_paper_mm(
        frame, reserve_title_block=reserve_title_block
    )
    available = (max(0.0, area_w - 2 * padding_mm), max(0.0, area_h - 2 * padding_mm))

    if extent_model_mm is None:
        transform = PaperTransform(
            scale_denominator=denominator,
            model_anchor_mm=(0, 0),
            paper_origin_um=(
                paper_mm_to_um(area_x + area_w / 2 + offset_mm[0]),
                paper_mm_to_um(area_y + area_h / 2 + offset_mm[1]),
            ),
        )
        return Fit(transform, True, (0.0, 0.0), available)

    min_x, min_y, max_x, max_y = extent_model_mm
    required = ((max_x - min_x) / denominator, (max_y - min_y) / denominator)
    centre_model = ((min_x + max_x) // 2, (min_y + max_y) // 2)
    transform = PaperTransform(
        scale_denominator=denominator,
        model_anchor_mm=centre_model,
        paper_origin_um=(
            paper_mm_to_um(area_x + area_w / 2 + offset_mm[0]),
            paper_mm_to_um(area_y + area_h / 2 + offset_mm[1]),
        ),
    )
    fits = required[0] <= available[0] and required[1] <= available[1]
    return Fit(transform, fits, required, available)


# ---------------------------------------------------------------------------
# §7's paper-scaled dimension offsets
# ---------------------------------------------------------------------------
def dim_chain_paper_offset_mm(level: int) -> float:
    """The §7 chain offset for a level, in **paper** mm (24 / 18 / 12).

    Derived from ``services.drawings.dimensions`` rather than restated, so the dimension
    engine and the sheet cannot hold different numbers.
    """
    from services.drawings.dimensions import (
        LEVEL_1_OFFSET_MM,
        LEVEL_2_OFFSET_MM,
        LEVEL_3_OFFSET_MM,
    )

    table = {1: LEVEL_1_OFFSET_MM, 2: LEVEL_2_OFFSET_MM, 3: LEVEL_3_OFFSET_MM}
    if level not in table:
        raise ValueError("§7 defines chain levels 1, 2 and 3; got %r" % (level,))
    return table[level] / DIM_REFERENCE_DENOMINATOR


def dim_chain_offset_model_mm(level: int, scale: Any) -> int:
    """The §7 chain offset for a level, in model mm **at this scale**.

    At 1:100 this returns §7's own numbers exactly — 2400, 1800, 1200 — which is the
    assertion in ``tests/test_sheets.py``. At 1:50 they halve, so the chain still sits
    24mm from the building line on paper. Getting this backwards puts the first chain
    2.4m from the wall on a 1:50 sheet, over the neighbouring plot.
    """
    denominator = scale_denominator_of(scale)
    return round_half_away(dim_chain_paper_offset_mm(level) * denominator)


__all__ = [
    "DIM_REFERENCE_DENOMINATOR",
    "FIT_PADDING_MM",
    "PAPER_UM_PER_MM",
    "Fit",
    "PaperTransform",
    "dim_chain_offset_model_mm",
    "dim_chain_paper_offset_mm",
    "drawable_area_paper_mm",
    "fit_to_frame",
    "paper_mm_to_um",
    "paper_um_to_mm",
    "scale_denominator_of",
]
