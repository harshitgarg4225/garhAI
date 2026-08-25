"""Sheet border, title block and revision table -> primitives. **Fully implemented.**

F7-A: *"Title block: firm logo/fields template; sheet numbering; auto revision table."*

Everything here is drawn in **paper space**: a :class:`~...primitives.Placement`
created with :meth:`~...primitives.Placement.paper`, where one input unit is one
millimetre of paper measured from the sheet's top-left corner. That is why this module
never mentions a scale — the frame is the same size on an A2 sheet whether the plan
inside it is 1:50 or 1:200.

Coordinates come from :class:`~services.drawings.sheets.Frame`, which owns the margins
and title-block size, so the renderer cannot disagree with the persisted sheet record
about where the border is.

The layer is ``A-TITL`` throughout except field values, which go on ``A-TEXT``: a
reviewer's CAD session turns text layers on and off independently of the frame, and a
title block whose values vanish with the room labels is a nuisance nobody expects.
"""

from __future__ import annotations

from typing import Any, List, Sequence, Tuple

from services.drawings.layers import A_TEXT, A_TITL
from services.drawings.render.primitives import (
    TEXT_HEIGHT_LABEL_PAPER_UM,
    TEXT_HEIGHT_SMALL_PAPER_UM,
    TEXT_HEIGHT_TITLE_PAPER_UM,
    DrawingGroup,
    Line,
    Placement,
    Polyline,
    Primitive,
    Text,
)

__all__ = [
    "NOTES_BAND_HEIGHT_MM",
    "REVISION_ROW_HEIGHT_MM",
    "RevisionRow",
    "frame_group",
    "title_block_primitives",
]

#: Height of one revision row in paper mm. Four rows fit a 60 mm title block with the
#: firm block above them, which is the standard Indian practice sheet.
REVISION_ROW_HEIGHT_MM = 6

#: Reserved band for the title block's notes line, when there is one.
NOTES_BAND_HEIGHT_MM = 5

#: Rule thicknesses are carried by the layer (A-TITL is 0.35 mm), so the frame needs no
#: per-line weight. The outer trim line is drawn twice, 2 mm apart, which is the
#: conventional double border on a submission sheet.
_TRIM_INSET_MM = 2


class RevisionRow(tuple):
    """One revision line: ``(revision, date, description)``.

    A tuple subclass rather than a dataclass so a caller can hand in plain tuples read
    straight out of the ``sheets`` table without an import.
    """

    __slots__ = ()

    def __new__(cls, revision: str, date: str, description: str) -> "RevisionRow":
        return super().__new__(cls, (revision, date, description))

    @property
    def revision(self) -> str:
        return self[0]

    @property
    def date(self) -> str:
        return self[1]

    @property
    def description(self) -> str:
        return self[2]


def _rect(x: int, y: int, width: int, height: int, layer: str) -> Polyline:
    return Polyline(
        vertices=(
            (x, y),
            (x + width, y),
            (x + width, y + height),
            (x, y + height),
        ),
        layer=layer,
        closed=True,
    )


def title_block_primitives(
    frame: Any,
    *,
    revisions: Sequence[Tuple[str, str, str]] = (),
) -> Tuple[Primitive, ...]:
    """The title block: box, field rules, labels, values and the revision table.

    ``frame`` is a :class:`services.drawings.sheets.Frame`. Field values come from its
    :class:`~services.drawings.sheets.TitleBlock`; empty fields print their label and an
    empty value rather than collapsing, because a municipal reviewer reads a blank
    "CHECKED BY" as "not checked" and an absent one as "we hid something".
    """
    paper = frame.paper
    block = frame.title_block
    width = int(frame.title_block_width_mm)
    height = int(frame.title_block_height_mm)
    # Frame.title_block_origin_mm() is bottom-left in a Y-up paper convention; the
    # renderer's paper space is Y-down from the top-left, so convert once, here.
    left = paper.width_mm - frame.margin_right_mm - width
    top = paper.height_mm - frame.margin_bottom_mm - height

    out: List[Primitive] = [_rect(left, top, width, height, A_TITL)]

    # -- upper band: drawing title + sheet number ---------------------------
    title_band_height = 14
    out.append(Line((left, top + title_band_height), (left + width, top + title_band_height), A_TITL))
    number_column = width - 40
    out.append(
        Line(
            (left + number_column, top),
            (left + number_column, top + title_band_height),
            A_TITL,
        )
    )
    out.append(
        Text(
            at=(left + 3, top + 9),
            text=block.drawing_title or "DRAWING",
            layer=A_TEXT,
            height_paper_um=TEXT_HEIGHT_TITLE_PAPER_UM,
            bold=True,
        )
    )
    out.append(
        Text(
            at=(left + number_column + 20, top + 6),
            text=block.sheet_number,
            layer=A_TEXT,
            height_paper_um=TEXT_HEIGHT_TITLE_PAPER_UM,
            anchor="middle",
            bold=True,
        )
    )
    out.append(
        Text(
            at=(left + number_column + 20, top + 11),
            text="SHEET",
            layer=A_TEXT,
            height_paper_um=TEXT_HEIGHT_LABEL_PAPER_UM,
            anchor="middle",
        )
    )

    # -- field grid: two columns of labelled fields -------------------------
    fields: Tuple[Tuple[str, str], ...] = (
        ("PROJECT", block.project_name),
        ("CLIENT", block.client_name),
        ("ARCHITECT", block.firm_name),
        ("SCALE", block.scale_label),
        ("DATE", block.date),
        ("DRAWN", block.drawn_by),
        ("CHECKED", block.checked_by),
        ("REV", block.revision),
    )
    grid_top = top + title_band_height
    revision_table_height = REVISION_ROW_HEIGHT_MM * (len(revisions) + 1) if revisions else 0
    # The notes line gets its own reserved band. It used to be drawn at the block's
    # bottom edge, which put it straight on top of the last revision row — the kind of
    # overprint that only shows up once a project actually has revisions.
    notes_height = NOTES_BAND_HEIGHT_MM if block.notes else 0
    grid_height = height - title_band_height - revision_table_height - notes_height
    rows = (len(fields) + 1) // 2
    row_height = max(1, grid_height // max(1, rows))
    column_width = width // 2
    # A field cell stacks a small label over its value. That needs
    # label height (1.8) + value height (2.0) + separation (1.5) = 5.3 mm; below that the
    # two texts overlap, which is how this started out and is exactly the defect §16's
    # collision assertion exists to catch. When the cell is too short we fall back to a
    # single line, "LABEL  value", which is legible rather than overprinted.
    stacked = row_height >= 6
    out.append(Line((left + column_width, grid_top), (left + column_width, grid_top + grid_height), A_TITL))
    for index, (label, value) in enumerate(fields):
        column, row = divmod(index, rows)
        x = left + column * column_width
        y = grid_top + row * row_height
        if row > 0:
            out.append(Line((x, y), (x + column_width, y), A_TITL))
        if stacked:
            out.append(
                Text(
                    at=(x + 2, y + 1),
                    text=label,
                    layer=A_TEXT,
                    height_paper_um=TEXT_HEIGHT_LABEL_PAPER_UM,
                    baseline="hanging",
                )
            )
            out.append(
                Text(
                    at=(x + 2, y + row_height - 1),
                    text=value,
                    layer=A_TEXT,
                    height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM,
                )
            )
        else:
            out.append(
                Text(
                    at=(x + 2, y + row_height - 1),
                    text="%s  %s" % (label, value) if value else label,
                    layer=A_TEXT,
                    height_paper_um=TEXT_HEIGHT_LABEL_PAPER_UM,
                )
            )

    # -- notes band --------------------------------------------------------
    if block.notes:
        notes_top = grid_top + grid_height
        out.append(Line((left, notes_top), (left + width, notes_top), A_TITL))
        out.append(
            Text(
                at=(left + 2, notes_top + notes_height - 1),
                text=block.notes,
                layer=A_TEXT,
                height_paper_um=TEXT_HEIGHT_LABEL_PAPER_UM,
            )
        )

    # -- revision table (F7-A "auto revision table") ------------------------
    if revisions:
        table_top = grid_top + grid_height + notes_height
        columns = (10, 24, width - 34)
        header = ("REV", "DATE", "DESCRIPTION")
        for row_index, row in enumerate((header,) + tuple(revisions)):
            y = table_top + row_index * REVISION_ROW_HEIGHT_MM
            out.append(Line((left, y), (left + width, y), A_TITL))
            x = left
            for column_index, cell in enumerate(row):
                out.append(
                    Text(
                        at=(x + 2, y + REVISION_ROW_HEIGHT_MM - 2),
                        text=str(cell),
                        layer=A_TEXT,
                        height_paper_um=TEXT_HEIGHT_LABEL_PAPER_UM,
                        bold=row_index == 0,
                    )
                )
                x += columns[column_index]
                if column_index < len(columns) - 1:
                    out.append(
                        Line(
                            (x, y),
                            (x, y + REVISION_ROW_HEIGHT_MM),
                            A_TITL,
                        )
                    )

    return tuple(out)


def frame_group(
    frame: Any,
    *,
    revisions: Sequence[Tuple[str, str, str]] = (),
    group_id: str = "frame",
) -> DrawingGroup:
    """Border + trim line + title block as one paper-space drawing group."""
    paper = frame.paper
    out: List[Primitive] = []
    # Trim line, then the border. Two lines is the convention; the outer one is the
    # cut line and the inner one is the drawing border.
    out.append(
        _rect(
            _TRIM_INSET_MM,
            _TRIM_INSET_MM,
            paper.width_mm - 2 * _TRIM_INSET_MM,
            paper.height_mm - 2 * _TRIM_INSET_MM,
            A_TITL,
        )
    )
    out.append(
        _rect(
            frame.margin_left_mm,
            frame.margin_top_mm,
            frame.drawable_width_mm(),
            frame.drawable_height_mm(),
            A_TITL,
        )
    )
    out.extend(title_block_primitives(frame, revisions=revisions))
    return DrawingGroup(id=group_id, placement=Placement.paper(), primitives=tuple(out))
