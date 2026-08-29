"""The revision register: the table that makes a set traceable. **Pure, no arithmetic.**

The title block already carries a compact ``REV | DATE | DESCRIPTION`` strip on every
sheet (``render.frame.title_block_primitives``) — three columns is all that fits beside a
title block. That strip answers "which issue am I holding?".

The **register** answers the other question, the one asked at the counter: *what has
happened to this drawing set, in order, and who signed each issue off?* It carries the
author column the strip has no room for, it lives once in the set (on the first sheet),
and it is the thing an architect points at when a reviewer says "I asked for this in
March". Both are rendered from the same :class:`~.record.RevisionHistory`, so they cannot
disagree.

Two renderings, exactly as the area statement and the door/window schedule already do it:

* :func:`revision_register_table` returns a :class:`services.drawings.schedules.table.Table`
  — the ASCII/JSON/standalone-SVG form, which is what a golden diffs and what an API
  serialises;
* :func:`revision_register_primitives` returns sheet primitives through
  ``render.tables.table_primitives``, the same ruled-table drawing code the schedule and
  area-statement sheets use, so the register looks like the rest of the set rather than
  like a table from a different program.
"""

from __future__ import annotations

from collections.abc import Sequence

from services.drawings.render.primitives import DrawingGroup, Placement, Primitive
from services.drawings.render.tables import AREA_ROW_HEIGHT_MM, Column, table_primitives
from services.drawings.revisions.record import RevisionHistory
from services.drawings.schedules.table import Column as TextColumn
from services.drawings.schedules.table import Table, TableStyle

__all__ = [
    "REGISTER_COLUMNS_PAPER_MM",
    "REGISTER_TITLE",
    "revision_register_group",
    "revision_register_primitives",
    "revision_register_table",
]

REGISTER_TITLE = "REVISION REGISTER"

#: Paper-mm widths for REV / DATE / DESCRIPTION / BY. The description gets the slack
#: because it is the only column whose content varies; a 120-character description (the
#: record's own limit) fits at 2 mm per character.
REGISTER_COLUMNS_PAPER_MM: tuple[int, int, int, int] = (18, 30, 250, 40)


def revision_register_table(
    history: RevisionHistory,
    *,
    title: str = REGISTER_TITLE,
    style: TableStyle | None = None,
    origin_mm: tuple[int, int] = (0, 0),
) -> Table:
    """The register as a self-laying-out table (text, JSON and standalone SVG)."""
    columns = (
        TextColumn("rev", "REV", "left"),
        TextColumn("date", "DATE", "left"),
        TextColumn("description", "DESCRIPTION", "left"),
        TextColumn("author", "BY", "left"),
    )
    return Table(
        title=title,
        columns=columns,
        rows=history.register_rows(),
        style=style or TableStyle(),
        origin_mm=origin_mm,
        # Rule under the latest issue: it is the row a reviewer reads first, and a set
        # whose current revision is not visually distinct gets read as the previous one.
        bold_rows=(len(history) - 1,) if history else (),
    )


def revision_register_primitives(
    history: RevisionHistory,
    *,
    origin_mm: tuple[int, int] = (25, 25),
    title: str = REGISTER_TITLE,
    column_widths_mm: Sequence[int] = REGISTER_COLUMNS_PAPER_MM,
) -> tuple[Primitive, ...]:
    """The register drawn on a sheet, in paper mm, through the shared table renderer."""
    if len(column_widths_mm) != 4:
        raise ValueError(
            "the register has four columns (REV, DATE, DESCRIPTION, BY); got %d widths"
            % len(column_widths_mm)
        )
    columns = [
        Column("REV", int(column_widths_mm[0])),
        Column("DATE", int(column_widths_mm[1])),
        Column("DESCRIPTION", int(column_widths_mm[2])),
        Column("BY", int(column_widths_mm[3])),
    ]
    rows = [list(row) for row in history.register_rows()]
    if not rows:
        # An empty register is drawn, not skipped: "REV 1, no revisions issued" and a
        # missing table read very differently to a reviewer, and only one of them is
        # honest about a first issue.
        rows = [["-", "-", "First issue — no revisions", "-"]]
    return table_primitives(
        columns,
        rows,
        origin_mm=origin_mm,
        row_height_mm=AREA_ROW_HEIGHT_MM,
        title=title,
        emphasise_rows=(len(rows) - 1,) if history else (),
    )


def revision_register_group(
    history: RevisionHistory,
    *,
    origin_mm: tuple[int, int] = (25, 25),
    group_id: str = "revision-register",
    title: str = REGISTER_TITLE,
) -> DrawingGroup:
    """The register as a paper-space drawing group, ready to add to a sheet."""
    return DrawingGroup(
        id=group_id,
        placement=Placement.paper(),
        primitives=revision_register_primitives(history, origin_mm=origin_mm, title=title),
    )
