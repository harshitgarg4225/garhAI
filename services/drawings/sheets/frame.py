"""Sheet border and title block, as primitives in paper µm. **Real.** §7.

    Frame/title-block geometry in mm at paper scale ... **Title block:** firm
    logo/fields template; sheet numbering; auto revision table. (F7-A)

The frame is the one part of a sheet that is drawn entirely in paper space: a border is
10mm from the sheet edge whether the building inside is at 1:50 or 1:200. So every number
in this module is a **paper millimetre**, and the only conversion is
:func:`~services.drawings.sheets.transform.paper_mm_to_um` at emit time.

THE LAYOUT IS PROPORTIONAL, NOT HARD-CODED
------------------------------------------
``Frame.title_block_width_mm`` / ``height_mm`` are editable per firm (§7 "title block
editor"), so the rows and columns below are *fractions* of the box. A firm that widens
its title block to 200mm gets a wider block with the same proportions, not a block with
a 20mm gap on the end.

WHAT IS DELIBERATELY MISSING
----------------------------
The **logo** is a raster (``TitleBlock.logo_url``); the primitive stream is vector-only,
so the logo is placed by the renderers that can carry an image (SVG, PDF) from the field
in the sheet model. Emitting a placeholder box for it here would print an empty rectangle
on a submission set.

The **revision table** (F7-A "auto revision table") needs revision *history*, which lives
in the project's version log rather than in the sheet model — the single ``revision`` cell
below is what the current model can honestly fill in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from services.drawings.layers import A_TITL
from services.drawings.projection.primitives import (
    K_SHEET_BORDER,
    K_TITLE_BLOCK,
    K_TITLE_LABEL,
    K_TITLE_RULE,
    K_TITLE_VALUE,
    Line,
    Point,
    Polyline,
    Primitive,
    Text,
    sanitise_text,
)
from services.drawings.sheets.model import Frame, TitleBlock
from services.drawings.sheets.transform import paper_mm_to_um

# ---------------------------------------------------------------------------
# Text sizes — paper mm, ISO 3098 steps
# ---------------------------------------------------------------------------
FIRM_TEXT_MM = 5.0
VALUE_TEXT_MM = 3.0
SMALL_VALUE_TEXT_MM = 2.5
LABEL_TEXT_MM = 1.8

#: Padding inside a title-block cell.
CELL_PAD_MM = 1.5

#: Row heights as fractions of the title block height, top row first. They sum to 1, and
#: the assertion below is what keeps that true if somebody edits the table.
#: Whole units out of 60, so the arithmetic below stays exact until the final divide.
#: Floats here would leave the bottom row sitting at -3.6e-15 instead of 0 once a
#: statutory row renormalises them, and the tiling invariant would fail on a rounding
#: artefact rather than on anything a reader could see.
ROW_UNITS: tuple[int, ...] = (14, 12, 12, 12, 10)
ROW_FRACTIONS: tuple[float, ...] = tuple(u / sum(ROW_UNITS) for u in ROW_UNITS)
assert abs(sum(ROW_FRACTIONS) - 1.0) < 1e-9, "title block rows must fill the block"

#: Height of one statutory row, in the same 60-unit currency as ROW_UNITS. When a
#: submission template supplies statutory identifiers (D-4) they get rows of their own
#: BELOW the administrative row, and every fraction is renormalised so the block still
#: fills exactly — the block does not grow, the existing rows compress.
STATUTORY_ROW_UNITS = 9

#: Statutory cells per row. Four fits the labels these templates use ("KHATA NO.",
#: "ARCHITECT REG. NO."); a fifth would collide at A3.
STATUTORY_PER_ROW = 4

#: Longest label the title block can print without an ellipsis. Named because D-4's
#: template loader checks statutory labels against it: a truncated "ARCHITECT REG.…" on
#: a sanction drawing is a defect nobody sees until the drawing is on a counter, and
#: the place to catch it is where the template is authored.
LABEL_MAX_CHARS = 16


@dataclass(frozen=True)
class TitleCell:
    """One labelled cell of the title block, in paper mm from the block's bottom-left."""

    label: str
    value: str
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
    value_text_mm: float = VALUE_TEXT_MM


def title_block_cells(frame: Frame) -> tuple[TitleCell, ...]:
    """The title block's cells, laid out proportionally for this frame.

    Reading order top to bottom is what a reviewer scans: who drew it, for what project,
    what this sheet is, then the administrative facts.
    """
    block = frame.title_block
    width = float(frame.title_block_width_mm)
    height = float(frame.title_block_height_mm)

    # Statutory identifiers (D-4) come in rows of their own. Chunked rather than
    # squeezed: a template with six fields gets two rows, not six slivers.
    statutory_rows: list[tuple[tuple[str, str], ...]] = [
        tuple(block.statutory[i : i + STATUTORY_PER_ROW])
        for i in range(0, len(block.statutory), STATUTORY_PER_ROW)
    ]
    units = list(ROW_UNITS) + [STATUTORY_ROW_UNITS] * len(statutory_rows)
    total_units = sum(units)
    heights = [height * unit / total_units for unit in units]
    # y of each row's bottom, top row first — computed from the units REMAINING below
    # it rather than by subtracting float heights, so the bottom row lands on exactly 0.
    tops: list[float] = []
    remaining = total_units
    for unit in units:
        remaining -= unit
        tops.append(height * remaining / total_units)

    rows: Sequence[Sequence[tuple[str, str, float, float]]] = (
        # (label, value, column fraction, value text size)
        (("", block.firm_name, 1.0, FIRM_TEXT_MM),),
        (("PROJECT", block.project_name, 1.0, VALUE_TEXT_MM),),
        (
            ("DRAWING", block.drawing_title, 2 / 3, VALUE_TEXT_MM),
            ("CLIENT", block.client_name, 1 / 3, SMALL_VALUE_TEXT_MM),
        ),
        (
            ("SHEET", block.sheet_number, 0.25, VALUE_TEXT_MM),
            ("SCALE", block.scale_label, 0.25, VALUE_TEXT_MM),
            ("DATE", block.date, 0.3, SMALL_VALUE_TEXT_MM),
            ("REV", block.revision, 0.2, VALUE_TEXT_MM),
        ),
        (
            ("DRAWN", block.drawn_by, 0.25, SMALL_VALUE_TEXT_MM),
            ("CHECKED", block.checked_by, 0.25, SMALL_VALUE_TEXT_MM),
            ("NOTES", block.notes, 0.5, SMALL_VALUE_TEXT_MM),
        ),
    )
    # A statutory box prints even when its value is blank: the label is the reminder,
    # and a requirement that disappears when unfilled is a requirement nobody meets.
    # `value or " "` defeats frame_primitives' skip-empty-values rule for these cells
    # only — the box must be visibly there, waiting.
    rows = tuple(rows) + tuple(
        tuple((label, value or " ", 1.0 / len(row), SMALL_VALUE_TEXT_MM) for label, value in row)
        for row in statutory_rows
    )

    cells: list[TitleCell] = []
    for row_index, row in enumerate(rows):
        y = tops[row_index]
        row_height = heights[row_index]
        x = 0.0
        for label, value, fraction, text_mm in row:
            cell_width = width * fraction
            cells.append(
                TitleCell(
                    label=label,
                    value=value,
                    x_mm=x,
                    y_mm=y,
                    width_mm=cell_width,
                    height_mm=row_height,
                    value_text_mm=text_mm,
                )
            )
            x += cell_width
    return tuple(cells)


def frame_primitives(frame: Frame) -> tuple[Primitive, ...]:
    """Border, title-block box, its rules, and every non-empty label/value.

    Empty fields print their label but not an empty value: a blank "CHECKED BY" line is
    a line a firm signs, while a stray dash is something they have to erase.
    """
    border_x0 = float(frame.margin_left_mm)
    border_y0 = float(frame.margin_bottom_mm)
    border_x1 = border_x0 + float(frame.drawable_width_mm())
    border_y1 = border_y0 + float(frame.drawable_height_mm())

    out: list[Primitive] = [
        Polyline(
            layer=A_TITL,
            points=(
                _at(border_x0, border_y0),
                _at(border_x1, border_y0),
                _at(border_x1, border_y1),
                _at(border_x0, border_y1),
            ),
            closed=True,
            kind=K_SHEET_BORDER,
        )
    ]

    block_x, block_y = frame.title_block_origin_mm()
    block_x = float(block_x)
    block_y = float(block_y)
    width = float(frame.title_block_width_mm)
    height = float(frame.title_block_height_mm)
    out.append(
        Polyline(
            layer=A_TITL,
            points=(
                _at(block_x, block_y),
                _at(block_x + width, block_y),
                _at(block_x + width, block_y + height),
                _at(block_x, block_y + height),
            ),
            closed=True,
            kind=K_TITLE_BLOCK,
        )
    )

    cells = title_block_cells(frame)
    drawn_rules = set()
    for cell in cells:
        # Row rule along the cell's top edge, drawn once per distinct y.
        rule_y = round(cell.y_mm + cell.height_mm, 6)
        if rule_y not in drawn_rules and rule_y < height - 1e-9:
            drawn_rules.add(rule_y)
            out.append(
                Line(
                    layer=A_TITL,
                    a=_at(block_x, block_y + rule_y),
                    b=_at(block_x + width, block_y + rule_y),
                    kind=K_TITLE_RULE,
                )
            )
        # Vertical rule at the cell's left edge, except at the block's own edge.
        if cell.x_mm > 1e-9:
            out.append(
                Line(
                    layer=A_TITL,
                    a=_at(block_x + cell.x_mm, block_y + cell.y_mm),
                    b=_at(block_x + cell.x_mm, block_y + cell.y_mm + cell.height_mm),
                    kind=K_TITLE_RULE,
                )
            )
        out.extend(_cell_text(cell, block_x, block_y))
    return tuple(out)


def _cell_text(cell: TitleCell, block_x: float, block_y: float) -> list[Primitive]:
    out: list[Primitive] = []
    label = sanitise_text(cell.label, max_length=LABEL_MAX_CHARS)
    value = sanitise_text(cell.value, max_length=90)
    text_x = block_x + cell.x_mm + CELL_PAD_MM
    if label:
        out.append(
            Text(
                layer=A_TITL,
                position=_at(text_x, block_y + cell.y_mm + cell.height_mm - CELL_PAD_MM),
                text=label,
                height_mm=paper_mm_to_um(LABEL_TEXT_MM),
                h_align="left",
                v_align="top",
                kind=K_TITLE_LABEL,
            )
        )
    if value:
        # Sits on the cell's baseline padding, below the label band.
        out.append(
            Text(
                layer=A_TITL,
                position=_at(text_x, block_y + cell.y_mm + CELL_PAD_MM),
                text=value,
                height_mm=paper_mm_to_um(cell.value_text_mm),
                h_align="left",
                v_align="bottom",
                kind=K_TITLE_VALUE,
            )
        )
    return out


def _at(x_mm: float, y_mm: float) -> Point:
    """A paper-mm position as an integer paper-µm point."""
    return (paper_mm_to_um(x_mm), paper_mm_to_um(y_mm))


def sheet_title_block(
    frame: Frame,
    *,
    drawing_title: str | None = None,
    sheet_number: str | None = None,
    scale_label: str | None = None,
) -> Frame:
    """A copy of ``frame`` whose title block carries this sheet's own three fields.

    The firm-level fields (name, project, client, date, signatures) are set once per
    project; these three change per sheet, and copying rather than mutating keeps
    ``Frame`` frozen and shareable across the whole set.
    """
    from dataclasses import replace

    block: TitleBlock = frame.title_block
    updated = replace(
        block,
        drawing_title=drawing_title if drawing_title is not None else block.drawing_title,
        sheet_number=sheet_number if sheet_number is not None else block.sheet_number,
        scale_label=scale_label if scale_label is not None else block.scale_label,
    )
    return replace(frame, title_block=updated)


__all__ = [
    "CELL_PAD_MM",
    "FIRM_TEXT_MM",
    "LABEL_MAX_CHARS",
    "ROW_UNITS",
    "LABEL_TEXT_MM",
    "ROW_FRACTIONS",
    "SMALL_VALUE_TEXT_MM",
    "VALUE_TEXT_MM",
    "TitleCell",
    "frame_primitives",
    "sheet_title_block",
    "title_block_cells",
]
