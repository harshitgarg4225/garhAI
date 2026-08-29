"""The §7 sheet model, its frame geometry, and the model→paper bridge. **Real.**

    **Sheet model:** ``Sheet { kind, scale (1:100 default), frame A2 landscape default,
    viewport (storeyId | elevation dir | section line), annotations[] }``

=================  =====================================================  ==========
``model``          Sheet / Frame / Viewport / Scale / schedule shapes      **real**
``transform``      model mm ↔ paper µm — the ONE coordinate bridge         **real**
``frame``          border + title block as primitives (paper µm)           **real**
``compose``        frame + projected geometry in one paper-space stream    **real**
``builder``        the default six-kind set, expanded over the model       **real**
=================  =====================================================  ==========

This package replaced the single ``services/drawings/sheets.py`` module. Everything that
module exported is re-exported here unchanged (``model.py`` is that file, with one
documented edit), so ``from services.drawings.sheets import Sheet`` still means what it
meant.

THE UNIT RULE, ONCE MORE
------------------------
Model millimetres describe the building. Paper millimetres describe the sheet. They are
both "mm" and they are not interchangeable, so:

* a projector emits **model mm** and never thinks about paper;
* ``frame`` emits **paper µm** (from paper-mm constants) and never thinks about the
  building;
* ``compose`` is the only code that multiplies by a scale;
* §7's 2400/1800/1200 dimension offsets are paper-scaled numbers quoted at 1:100 —
  :func:`~services.drawings.sheets.transform.dim_chain_offset_model_mm` converts them for
  any other scale.
"""

from __future__ import annotations

from services.drawings.sheets.builder import (
    ELEVATION_NAMES,
    ELEVATION_ORDER,
    SECTION_OVERRUN_MM,
    SITE_PLAN_SCALE,
    build_sheet_set,
    building_extent_mm,
    plan_options_for,
    section_line_through_stair,
    section_markers_for,
    with_annotations,
)
from services.drawings.sheets.compose import (
    ComposedSheet,
    compose_plan_sheet,
    compose_sheet,
    transform_primitive,
    transform_primitives,
)
from services.drawings.sheets.frame import (
    LABEL_TEXT_MM,
    VALUE_TEXT_MM,
    TitleCell,
    frame_primitives,
    sheet_title_block,
    title_block_cells,
)
from services.drawings.sheets.model import (
    DEFAULT_PAPER,
    DEFAULT_SCALE,
    DEFAULT_SHEET_PLAN,
    PAPER_SIZES,
    SCALE_1_50,
    SCALE_1_100,
    SCALE_1_200,
    SHEET_KINDS,
    SUBMISSION_SHEET_KINDS,
    WORKING_SHEET_KINDS,
    AreaStatementRow,
    Direction4,
    Frame,
    PaperSize,
    Scale,
    ScheduleRow,
    Sheet,
    SheetAnnotation,
    SheetKind,
    TitleBlock,
    Viewport,
    default_frame,
    sheet_numbers,
)
from services.drawings.sheets.transform import (
    DIM_REFERENCE_DENOMINATOR,
    FIT_PADDING_MM,
    PAPER_UM_PER_MM,
    Fit,
    PaperTransform,
    dim_chain_offset_model_mm,
    dim_chain_paper_offset_mm,
    drawable_area_paper_mm,
    fit_to_frame,
    paper_mm_to_um,
    paper_um_to_mm,
    scale_denominator_of,
)

__all__ = [
    # -- model (the pre-existing sheets.py surface, unchanged) ------------
    "DEFAULT_PAPER",
    "DEFAULT_SCALE",
    "DEFAULT_SHEET_PLAN",
    "PAPER_SIZES",
    "SCALE_1_50",
    "SCALE_1_100",
    "SCALE_1_200",
    "SHEET_KINDS",
    "SUBMISSION_SHEET_KINDS",
    "WORKING_SHEET_KINDS",
    "AreaStatementRow",
    "Direction4",
    "Frame",
    "PaperSize",
    "Scale",
    "ScheduleRow",
    "Sheet",
    "SheetAnnotation",
    "SheetKind",
    "TitleBlock",
    "Viewport",
    "default_frame",
    "sheet_numbers",
    # -- transform -------------------------------------------------------
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
    # -- frame -----------------------------------------------------------
    "LABEL_TEXT_MM",
    "VALUE_TEXT_MM",
    "TitleCell",
    "frame_primitives",
    "sheet_title_block",
    "title_block_cells",
    # -- compose ---------------------------------------------------------
    "ComposedSheet",
    "compose_plan_sheet",
    "compose_sheet",
    "transform_primitive",
    "transform_primitives",
    # -- builder ---------------------------------------------------------
    "ELEVATION_NAMES",
    "ELEVATION_ORDER",
    "SECTION_OVERRUN_MM",
    "SITE_PLAN_SCALE",
    "build_sheet_set",
    "building_extent_mm",
    "plan_options_for",
    "section_line_through_stair",
    "section_markers_for",
    "with_annotations",
]
