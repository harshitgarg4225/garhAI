"""Fitting a model extent onto a sheet. **Fully implemented, pure integer arithmetic.**

Small module, one job: given a building's extent in model millimetres and a rectangle of
paper to put it in, produce the :class:`~...primitives.Placement` that centres it — and,
when asked, pick the largest *standard* scale that fits.

Why standard scales only: a municipal drawing is measured with a scale rule. 1:100 and
1:200 are on the rule; 1:137 is not, and a sheet at an arbitrary ratio is not a
submission drawing, it is a picture of one. So :func:`choose_scale` walks
:data:`PREFERRED_SCALES` and returns the first that fits, rather than solving for the
ratio that fills the page.

All arithmetic is integer. The paper rectangle is in paper millimetres (the unit
:class:`~services.drawings.sheets.Frame` uses); placements come out in paper
micrometres, matching the primitive vocabulary.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence, Tuple

from services.drawings.render.primitives import Placement, div_round

__all__ = [
    "PREFERRED_SCALES",
    "PaperRect",
    "content_rect",
    "choose_scale",
    "fit_placement",
]

#: Scales a scale rule actually carries, largest drawing first. §7's default is 1:100
#: for floor plans; site plans usually land on 1:200 or 1:250.
PREFERRED_SCALES: Tuple[int, ...] = (20, 25, 50, 75, 100, 125, 150, 200, 250, 500, 1000)


class PaperRect(tuple):
    """``(x_mm, y_mm, width_mm, height_mm)`` on the sheet, from the **top-left**.

    Top-left origin, matching the renderers' paper space. The
    :class:`~services.drawings.sheets.Frame` helpers are bottom-left (a CAD convention);
    :func:`content_rect` is the one place that conversion happens.
    """

    __slots__ = ()

    def __new__(cls, x_mm: int, y_mm: int, width_mm: int, height_mm: int) -> "PaperRect":
        if width_mm <= 0 or height_mm <= 0:
            raise ValueError(
                "paper rect must have positive size, got %dx%d" % (width_mm, height_mm)
            )
        return super().__new__(cls, (int(x_mm), int(y_mm), int(width_mm), int(height_mm)))

    @property
    def x_mm(self) -> int:
        return self[0]

    @property
    def y_mm(self) -> int:
        return self[1]

    @property
    def width_mm(self) -> int:
        return self[2]

    @property
    def height_mm(self) -> int:
        return self[3]

    def centre_mm(self) -> Tuple[int, int]:
        return (self.x_mm + self.width_mm // 2, self.y_mm + self.height_mm // 2)

    def inset(self, mm: int) -> "PaperRect":
        return PaperRect(
            self.x_mm + mm, self.y_mm + mm, self.width_mm - 2 * mm, self.height_mm - 2 * mm
        )


def content_rect(frame: Any, *, avoid_title_block: bool = True, gutter_mm: int = 4) -> PaperRect:
    """The drawable area, minus the title block's column when asked.

    The title block sits bottom-right, so avoiding it means narrowing the content
    rectangle rather than shortening it — that keeps a wide plan wide, which matters
    because plans are wider than they are tall far more often than the reverse.
    """
    width = frame.drawable_width_mm()
    if avoid_title_block:
        width -= frame.title_block_width_mm + gutter_mm
    return PaperRect(
        frame.margin_left_mm,
        frame.margin_top_mm,
        max(1, width),
        max(1, frame.drawable_height_mm()),
    )


def choose_scale(
    extent_model_mm: Tuple[int, int, int, int],
    rect: PaperRect,
    *,
    margin_mm: int = 6,
    scales: Sequence[int] = PREFERRED_SCALES,
    preferred: Optional[int] = None,
) -> int:
    """Largest standard scale at which the extent fits inside ``rect``.

    ``preferred`` is tried first and returned when it fits, so §7's "1:100 default"
    stays the answer for anything that can be drawn at 1:100 and the fallback only
    kicks in for a plot that genuinely cannot.
    """
    min_x, min_y, max_x, max_y = extent_model_mm
    model_width = max(1, max_x - min_x)
    model_height = max(1, max_y - min_y)
    available_width = max(1, rect.width_mm - 2 * margin_mm)
    available_height = max(1, rect.height_mm - 2 * margin_mm)

    def fits(denominator: int) -> bool:
        return (
            div_round(model_width, denominator) <= available_width
            and div_round(model_height, denominator) <= available_height
        )

    if preferred is not None and fits(preferred):
        return preferred
    for denominator in scales:
        if fits(denominator):
            return denominator
    # Nothing standard fits. Return the coarsest rather than raising: a sheet that
    # overflows its border is a visible, fixable problem; a job that dies at export
    # time on a big plot is a support ticket.
    return scales[-1]


def fit_placement(
    extent_model_mm: Tuple[int, int, int, int],
    rect: PaperRect,
    scale_denominator: int,
) -> Placement:
    """Centre ``extent_model_mm`` inside ``rect`` at the given scale.

    The returned placement maps the extent's **bottom-left** model corner to a paper
    point, and flips Y — so the model's +Y (north, usually) points up the sheet, which
    is what the north arrow on the drawing promises.
    """
    min_x, min_y, max_x, max_y = extent_model_mm
    model_width = max_x - min_x
    model_height = max_y - min_y
    paper_width_um = div_round(model_width * 1000, scale_denominator)
    paper_height_um = div_round(model_height * 1000, scale_denominator)

    left_um = rect.x_mm * 1000 + (rect.width_mm * 1000 - paper_width_um) // 2
    top_um = rect.y_mm * 1000 + (rect.height_mm * 1000 - paper_height_um) // 2
    # Y flips, so the model's minimum Y lands at the BOTTOM of the paper slot.
    bottom_um = top_um + paper_height_um
    return Placement(
        scale_denominator=scale_denominator,
        origin_model_mm=(min_x, min_y),
        origin_paper_um=(left_um, bottom_um),
        flip_y=True,
    )
