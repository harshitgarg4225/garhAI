"""Auto-dimensioning configuration — every tunable number in one place (§7).

Two rules govern this file.

**Everything is an integer.** Text heights are stored in *tenths of a paper
millimetre* rather than as floats, because the only way a 2.5mm ISO text height can
become a model-space size without touching a float is to multiply by the scale
denominator and divide by ten: ``25 * 100 // 10 == 250``. The moment a float enters the
pipeline, two runs can disagree in the last bit and §16's tolerance-0 golden comparison
becomes a coin toss.

**The numbers that are also CAD numbers are copied from the DIMSTYLE**, not re-invented:
``services.drawings.dxf.setup_dimstyle`` writes ``dimtxt``, ``dimgap``, ``dimexe``,
``dimexo`` and ``dimtsz`` into the DXF, and a viewer that re-renders the dimension from
the DIMSTYLE must land on top of the primitives we emitted. Where the DIMSTYLE has a
value, this file mirrors it in paper tenths and :meth:`AutoDimConfig.paper_to_model`
scales it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

#: §7: chain offsets from the building line, in **paper-scaled model mm**.
#: "Offsets: L1 at 2400mm from building line (paper-scaled), L2 1800, L3 1200."
#: Note the counter-intuitive ordering: L3 (opening centrelines) is the *innermost*
#: chain, nearest the building, and the overall extent sits furthest out.
LEVEL_1_OFFSET_MM = 2_400
LEVEL_2_OFFSET_MM = 1_800
LEVEL_3_OFFSET_MM = 1_200

#: Inner (per-room) chains are level 4 and are offset *inward* from a room face.
LEVEL_4_OFFSET_MM = 400

#: §7 step 6: "openings dimensioned to centreline (config flag ``dimToJamb`` for firm
#: preference)". Centreline is the default because that is what an Indian municipal
#: reviewer expects; firms that build from jamb-to-jamb flip it per firm.
DEFAULT_DIM_TO_JAMB = False

#: ISO 3098 text heights, in tenths of a paper mm. Step 0 is 2.5mm — the same value
#: ``dxf.DIM_TEXT_HEIGHT_PAPER_MM`` puts in the DIMSTYLE. §7 step 4 allows shrinking
#: "one step" on collision; the third step exists for the pathological cases and is
#: reported in the stats so a human can see it happened.
TEXT_HEIGHT_STEPS_PAPER_TENTHS: Tuple[int, ...] = (25, 18, 14)

#: Advance width per character as a fraction (numerator/denominator) of text height.
#: The CAD default ``txt``/ISO fonts sit near 0.7 of height once inter-character spacing
#: is counted; digits are the only glyphs a dimension label contains, so one ratio is
#: honest here. Kept as a rational pair so the arithmetic stays integral.
CHAR_ADVANCE_NUM = 7
CHAR_ADVANCE_DEN = 10


@dataclass(frozen=True)
class AutoDimConfig:
    """Everything the engine needs to know that is not in the model.

    Frozen, so a chain-generation run cannot mutate its own settings half-way and
    produce a layout that no later run can reproduce.
    """

    #: Sheet scale denominator: 100 for 1:100. Drives every paper→model conversion.
    scale_denominator: int = 100

    #: §7 step 6. False = dimension openings to their centreline (default);
    #: True = dimension to the jambs, which yields alternating pier/opening segments.
    dim_to_jamb: bool = DEFAULT_DIM_TO_JAMB

    level_1_offset_mm: int = LEVEL_1_OFFSET_MM
    level_2_offset_mm: int = LEVEL_2_OFFSET_MM
    level_3_offset_mm: int = LEVEL_3_OFFSET_MM
    inner_offset_mm: int = LEVEL_4_OFFSET_MM

    #: Paper tenths, mirroring the DIMSTYLE: text gap, extension beyond the dim line,
    #: gap between the feature and the start of its witness line, oblique tick size.
    text_gap_paper_tenths: int = 10
    witness_extend_paper_tenths: int = 12
    witness_offset_paper_tenths: int = 10
    tick_size_paper_tenths: int = 10

    #: Collision-grid cell, in paper tenths. 25 (2.5mm) is one text height: big enough
    #: that a label touches few cells, small enough that a cell rarely holds many.
    grid_cell_paper_tenths: int = 25

    #: How far along the chain a blocked label may be shifted, as a multiple of the
    #: shift step (a quarter text height). §7 step 4's "shift along the chain".
    max_shift_steps: int = 12

    #: How far outward a leader may search for free space, in text-height multiples.
    max_leader_steps: int = 40

    #: Walls thinner than this are ignored when deciding whether a wall "meets" a
    #: facade. Nothing in the catalogue is thinner than 115mm (one brick on edge).
    min_wall_thickness_mm: int = 50

    def __post_init__(self) -> None:
        if self.scale_denominator <= 0:
            raise ValueError(
                "scale_denominator must be positive, got %d" % self.scale_denominator
            )

    # -- paper → model -----------------------------------------------------
    def paper_to_model(self, paper_tenths: int) -> int:
        """Tenths of a paper mm → model mm at this scale. Exact integer division.

        ``25`` tenths (2.5mm of ink) at 1:100 is ``25 * 100 // 10 = 250`` model mm.
        Floor division, not rounding: it is monotonic, and at 1:100 or 1:50 every
        value in this file divides exactly anyway.
        """
        return paper_tenths * self.scale_denominator // 10

    def text_height_mm(self, step: int = 0) -> int:
        """Model-mm text height for shrink ``step`` (0 = full size)."""
        clamped = max(0, min(step, len(TEXT_HEIGHT_STEPS_PAPER_TENTHS) - 1))
        return self.paper_to_model(TEXT_HEIGHT_STEPS_PAPER_TENTHS[clamped])

    @property
    def text_height_steps(self) -> int:
        return len(TEXT_HEIGHT_STEPS_PAPER_TENTHS)

    def text_gap_mm(self) -> int:
        return self.paper_to_model(self.text_gap_paper_tenths)

    def witness_extend_mm(self) -> int:
        return self.paper_to_model(self.witness_extend_paper_tenths)

    def witness_offset_mm(self) -> int:
        return self.paper_to_model(self.witness_offset_paper_tenths)

    def tick_size_mm(self) -> int:
        return self.paper_to_model(self.tick_size_paper_tenths)

    def grid_cell_mm(self) -> int:
        return max(1, self.paper_to_model(self.grid_cell_paper_tenths))

    def offset_for_level(self, level: int) -> int:
        """§7's offset for a chain level. Level 4 is the inner (in-room) offset."""
        if level == 1:
            return self.level_1_offset_mm
        if level == 2:
            return self.level_2_offset_mm
        if level == 3:
            return self.level_3_offset_mm
        if level == 4:
            return self.inner_offset_mm
        raise ValueError("chain level must be 1..4, got %r" % (level,))

    def text_width_mm(self, text: str, step: int = 0) -> int:
        """Bounding width of ``text`` at shrink ``step``, in model mm.

        Monospaced-digit approximation. A dimension label is digits (and, for a label
        override, the odd letter), so a per-glyph metric table would buy nothing but a
        font dependency — and the collision grid only needs a bound that is never
        *under* the truth.
        """
        height = self.text_height_mm(step)
        advance = max(1, height * CHAR_ADVANCE_NUM // CHAR_ADVANCE_DEN)
        return advance * max(1, len(text))


#: The default an architect gets: 1:100, dimensions to opening centrelines.
DEFAULT_CONFIG = AutoDimConfig()


__all__ = [
    "CHAR_ADVANCE_DEN",
    "CHAR_ADVANCE_NUM",
    "DEFAULT_CONFIG",
    "DEFAULT_DIM_TO_JAMB",
    "LEVEL_1_OFFSET_MM",
    "LEVEL_2_OFFSET_MM",
    "LEVEL_3_OFFSET_MM",
    "LEVEL_4_OFFSET_MM",
    "TEXT_HEIGHT_STEPS_PAPER_TENTHS",
    "AutoDimConfig",
]
