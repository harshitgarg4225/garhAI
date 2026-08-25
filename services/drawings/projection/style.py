"""Paper-scaled sizes for drawn symbols — and the scalar half of the §7 transform.

WHY THIS FILE EXISTS AND WHAT IT IS NOT
---------------------------------------
§7 warns about exactly one confusion, and this file is the answer to it:

    drawing coordinates are model mm; paper offsets are paper-scaled — do not confuse
    them; a wrong scale here silently ruins every sheet.

A wall is 230mm thick and stays 230mm at every scale. A letter is 2.5mm tall **on
paper** and therefore 250 model mm at 1:100, 125 at 1:50 and 500 at 1:200. Both numbers
are "mm". Mixing them produces a sheet that is subtly, expensively wrong: text the size
of a bathroom, or a north arrow you need a magnifier to find. So every size that belongs
to the *paper* is declared here, once, in paper millimetres, as a float — and the only
way to get a model-mm value out of it is :meth:`Style.paper_to_model_mm`, which is an
exact integer multiply.

This module owns the **scalar** relation (a length in paper mm ↔ a length in model mm).
``services.drawings.sheets.transform`` owns the **coordinate** transform (a point in the
building ↔ a point on the sheet) and builds on the two functions here, so there is still
exactly one definition of ``paper_mm = model_mm / scale``. It lives on this side of the
fence because the projectors need it to size their text and cannot depend on the sheet
model — the dependency arrow runs sheets → projection and never back.

The heights follow ISO 3098 (2.5mm body text), which is also what
``services/drawings/dxf.py`` sets ``dimtxt`` to, so dimension text and room labels print
at the same size.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.drawings.projection.primitives import round_half_away

# ---------------------------------------------------------------------------
# Text — paper mm. ISO 3098: 2.5mm is the body size on an A2 sheet.
# ---------------------------------------------------------------------------
TEXT_ROOM_NAME_MM = 2.5
TEXT_ROOM_AREA_MM = 2.0
TEXT_LABEL_MM = 2.0
TEXT_TAG_MM = 1.8
TEXT_GRID_MM = 2.5
TEXT_SECTION_MM = 3.5
TEXT_NORTH_MM = 3.5

#: Vertical gap between the two lines of a room label block.
LABEL_LINE_GAP_MM = 3.2

# ---------------------------------------------------------------------------
# Symbols — paper mm
# ---------------------------------------------------------------------------
#: Poché line spacing. 2.5mm keeps a hatched 230 wall readable at 1:100 and stops a
#: 1:50 sheet from turning solid black.
HATCH_SPACING_MM = 2.5

NORTH_ARROW_LENGTH_MM = 16.0
NORTH_ARROW_HALF_WIDTH_MM = 4.0
NORTH_ARROW_TAIL_MM = 4.0
NORTH_LABEL_GAP_MM = 3.0

STAIR_ARROW_HEAD_MM = 3.0
STAIR_LABEL_GAP_MM = 2.5

LEVEL_MARKER_SIZE_MM = 3.0
LEVEL_LABEL_GAP_MM = 1.5

SECTION_OVERSHOOT_MM = 10.0
SECTION_FLAG_MM = 6.0
SECTION_ARROW_MM = 4.0
SECTION_LABEL_GAP_MM = 4.0

GRID_BUBBLE_RADIUS_MM = 4.0
GRID_EXTENSION_MM = 14.0

TAG_OFFSET_MM = 3.0

#: Inward offset of a balcony railing line from the slab edge.
RAILING_INSET_MM = 1.0


@dataclass(frozen=True)
class Style:
    """Paper sizes resolved for one drawing scale.

    Constructed once per projection from the sheet's scale, then asked for model-mm
    sizes. Nothing else in the projection package is allowed to multiply by a scale.
    """

    scale_denominator: int

    def __post_init__(self) -> None:
        if not isinstance(self.scale_denominator, int) or self.scale_denominator <= 0:
            raise ValueError(
                "scale denominator must be a positive int (100 for 1:100), got %r"
                % (self.scale_denominator,)
            )

    # -- the scalar transform, both ways ---------------------------------
    def paper_to_model_mm(self, paper_mm: float) -> int:
        """A paper-millimetre size as model millimetres at this scale.

        Exact for the sizes above at 1:100 and 1:50 (2.5 × 100 = 250); rounded half away
        from zero otherwise, because a primitive coordinate is always an integer.
        """
        return round_half_away(paper_mm * self.scale_denominator)

    def model_to_paper_mm(self, model_mm: int) -> int:
        """A model-millimetre length as paper millimetres — the inverse, rounded."""
        return round_half_away(model_mm / self.scale_denominator)

    # -- the sizes a projector actually asks for -------------------------
    @property
    def room_name_height_mm(self) -> int:
        return self.paper_to_model_mm(TEXT_ROOM_NAME_MM)

    @property
    def room_area_height_mm(self) -> int:
        return self.paper_to_model_mm(TEXT_ROOM_AREA_MM)

    @property
    def label_height_mm(self) -> int:
        return self.paper_to_model_mm(TEXT_LABEL_MM)

    @property
    def tag_height_mm(self) -> int:
        return self.paper_to_model_mm(TEXT_TAG_MM)

    @property
    def grid_text_height_mm(self) -> int:
        return self.paper_to_model_mm(TEXT_GRID_MM)

    @property
    def section_text_height_mm(self) -> int:
        return self.paper_to_model_mm(TEXT_SECTION_MM)

    @property
    def north_text_height_mm(self) -> int:
        return self.paper_to_model_mm(TEXT_NORTH_MM)

    @property
    def label_line_gap_mm(self) -> int:
        return self.paper_to_model_mm(LABEL_LINE_GAP_MM)

    @property
    def hatch_spacing_mm(self) -> int:
        return self.paper_to_model_mm(HATCH_SPACING_MM)

    @property
    def north_arrow_length_mm(self) -> int:
        return self.paper_to_model_mm(NORTH_ARROW_LENGTH_MM)

    @property
    def north_arrow_half_width_mm(self) -> int:
        return self.paper_to_model_mm(NORTH_ARROW_HALF_WIDTH_MM)

    @property
    def north_arrow_tail_mm(self) -> int:
        return self.paper_to_model_mm(NORTH_ARROW_TAIL_MM)

    @property
    def north_label_gap_mm(self) -> int:
        return self.paper_to_model_mm(NORTH_LABEL_GAP_MM)

    @property
    def stair_arrow_head_mm(self) -> int:
        return self.paper_to_model_mm(STAIR_ARROW_HEAD_MM)

    @property
    def stair_label_gap_mm(self) -> int:
        return self.paper_to_model_mm(STAIR_LABEL_GAP_MM)

    @property
    def level_marker_size_mm(self) -> int:
        return self.paper_to_model_mm(LEVEL_MARKER_SIZE_MM)

    @property
    def level_label_gap_mm(self) -> int:
        return self.paper_to_model_mm(LEVEL_LABEL_GAP_MM)

    @property
    def section_overshoot_mm(self) -> int:
        return self.paper_to_model_mm(SECTION_OVERSHOOT_MM)

    @property
    def section_flag_mm(self) -> int:
        return self.paper_to_model_mm(SECTION_FLAG_MM)

    @property
    def section_arrow_mm(self) -> int:
        return self.paper_to_model_mm(SECTION_ARROW_MM)

    @property
    def section_label_gap_mm(self) -> int:
        return self.paper_to_model_mm(SECTION_LABEL_GAP_MM)

    @property
    def grid_bubble_radius_mm(self) -> int:
        return self.paper_to_model_mm(GRID_BUBBLE_RADIUS_MM)

    @property
    def grid_extension_mm(self) -> int:
        return self.paper_to_model_mm(GRID_EXTENSION_MM)

    @property
    def tag_offset_mm(self) -> int:
        return self.paper_to_model_mm(TAG_OFFSET_MM)

    @property
    def railing_inset_mm(self) -> int:
        return self.paper_to_model_mm(RAILING_INSET_MM)


def style_of(scale: object) -> Style:
    """Accept a ``Scale``, a bare denominator, or an existing :class:`Style`.

    The projection API is documented as ``(HouseModel, storeyId, scale)``; taking either
    the sheet model's ``Scale`` or a plain ``100`` is what keeps the projection package
    free of any dependency on the sheet model.
    """
    if isinstance(scale, Style):
        return scale
    # `int` is checked FIRST because every Python int carries a `.denominator`
    # attribute (ints are Rational, and 100 .denominator == 1). Reading the attribute
    # before the type check turned 1:100 into 1:1 and made every letter on the sheet
    # 2.5mm tall in model space — a third of a millimetre on paper. Caught by the smoke
    # run printing a 3mm room label.
    if isinstance(scale, bool):
        raise TypeError("scale must be a Scale, a Style or an int denominator, got %r" % (scale,))
    denominator = scale if isinstance(scale, int) else getattr(scale, "denominator", None)
    if isinstance(denominator, bool) or not isinstance(denominator, int):
        raise TypeError(
            "scale must be a Scale, a Style or an int denominator (100 for 1:100), got %r"
            % (scale,)
        )
    return Style(denominator)


__all__ = [
    "GRID_BUBBLE_RADIUS_MM",
    "GRID_EXTENSION_MM",
    "HATCH_SPACING_MM",
    "LABEL_LINE_GAP_MM",
    "LEVEL_LABEL_GAP_MM",
    "LEVEL_MARKER_SIZE_MM",
    "NORTH_ARROW_HALF_WIDTH_MM",
    "NORTH_ARROW_LENGTH_MM",
    "NORTH_ARROW_TAIL_MM",
    "NORTH_LABEL_GAP_MM",
    "RAILING_INSET_MM",
    "SECTION_ARROW_MM",
    "SECTION_FLAG_MM",
    "SECTION_LABEL_GAP_MM",
    "SECTION_OVERSHOOT_MM",
    "STAIR_ARROW_HEAD_MM",
    "STAIR_LABEL_GAP_MM",
    "TAG_OFFSET_MM",
    "TEXT_GRID_MM",
    "TEXT_LABEL_MM",
    "TEXT_NORTH_MM",
    "TEXT_ROOM_AREA_MM",
    "TEXT_ROOM_NAME_MM",
    "TEXT_SECTION_MM",
    "TEXT_TAG_MM",
    "Style",
    "style_of",
]
