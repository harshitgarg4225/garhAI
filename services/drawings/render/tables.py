"""Schedule and area-statement tables -> primitives. **Fully implemented, pure.**

§7's last two sheets are tables, not projections:

    Schedules & area statement: door/window schedule = group openings by (kind, w, h) ->
    tags D1.., W1.., V1..; counts per storey. Area statement per municipal format: plot
    area, per-storey built-up, total, FAR achieved vs allowed, coverage achieved vs
    allowed, setbacks provided vs required (from rules results — same numbers, one
    source).

**"Same numbers, one source" is enforced structurally here.** :func:`area_statement_table`
takes a :class:`garh_rules.areas.AreaStatement` — the object the rules engine already
produced while evaluating compliance — and *formats* it. It does not add, divide or
compare anything. There is no code path in this module that could compute a FAR
differently from the compliance chip, because there is no code path in this module that
computes a FAR at all. The one thing it does compute is the ratio *display* (via the
statement's own ``far_achieved``/``coverage_achieved`` ``Fraction`` properties, which are
exact), and the label for an achieved-vs-allowed pair comes from
:attr:`~garh_rules.areas.AreaRow.limit_label` so a minimum is never printed as
"permissible".

Tables are drawn in **paper space** (see :mod:`services.drawings.render.frame`): a table
cell is 8 mm tall on paper whatever the drawing scale is, because a reviewer's eyes do
not rescale.

Units on the sheet: areas print in m² with ft² alongside, and every raw length prints in
millimetres — §7's "All dim text in mm on drawings regardless of display units" applies
to dimensions, and the area statement is the one place a municipal format wants m², so
both appear and neither is guessed. Formatting comes from
:mod:`garh_model.units`, the same golden-tested pair the UI uses.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from services.drawings.layers import A_AREA, A_TEXT, A_TITL
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
    "AREA_ROW_HEIGHT_MM",
    "SCHEDULE_ROW_HEIGHT_MM",
    "Column",
    "area_statement_group",
    "area_statement_table",
    "format_area_dual",
    "schedule_group",
    "schedule_table",
    "table_primitives",
]

#: Paper mm. 8 mm rows read comfortably on an A2 print and let a 3BHK schedule fit.
SCHEDULE_ROW_HEIGHT_MM = 8
AREA_ROW_HEIGHT_MM = 7
_HEADER_HEIGHT_MM = 9
_CELL_PAD_MM = 2


class Column(tuple):
    """``(title, width_mm, align)`` where align is ``left`` | ``right`` | ``centre``."""

    __slots__ = ()

    def __new__(cls, title: str, width_mm: int, align: str = "left") -> Column:
        if align not in ("left", "right", "centre"):
            raise ValueError("column align must be left|right|centre, got %r" % align)
        return super().__new__(cls, (title, int(width_mm), align))

    @property
    def title(self) -> str:
        return self[0]

    @property
    def width_mm(self) -> int:
        return self[1]

    @property
    def align(self) -> str:
        return self[2]


_ANCHOR = {"left": "start", "right": "end", "centre": "middle"}


def _cell_text(
    x: int,
    y: int,
    column: Column,
    value: str,
    *,
    row_height_mm: int,
    height_paper_um: int,
    bold: bool = False,
    layer: str = A_TEXT,
) -> Text:
    if column.align == "left":
        at_x = x + _CELL_PAD_MM
    elif column.align == "right":
        at_x = x + column.width_mm - _CELL_PAD_MM
    else:
        at_x = x + column.width_mm // 2
    return Text(
        at=(at_x, y + row_height_mm - _CELL_PAD_MM - 1),
        text=value,
        layer=layer,
        height_paper_um=height_paper_um,
        anchor=_ANCHOR[column.align],
        bold=bold,
    )


def table_primitives(
    columns: Sequence[Column],
    rows: Sequence[Sequence[str]],
    *,
    origin_mm: tuple[int, int],
    row_height_mm: int = SCHEDULE_ROW_HEIGHT_MM,
    header_height_mm: int = _HEADER_HEIGHT_MM,
    title: str = "",
    grid_layer: str = A_TITL,
    value_layer: str = A_TEXT,
    emphasise_rows: Sequence[int] = (),
) -> tuple[Primitive, ...]:
    """A ruled table. Grid lines on ``grid_layer``, cell text on ``value_layer``.

    Grid lines go on ``A-TITL`` by default rather than a geometry layer: a table is
    sheet furniture, and a reviewer who freezes ``A-TITL`` expects the frame *and* the
    tables to go with it. The area statement overrides ``value_layer`` to ``A-AREA`` so
    its figures can be isolated — that is what ``A-AREA`` is for on an Indian sheet.

    ``emphasise_rows`` bolds whole rows (totals, the FAR line).
    """
    if not columns:
        raise ValueError("a table needs at least one column")
    width = sum(column.width_mm for column in columns)
    x0, y0 = origin_mm
    out: list[Primitive] = []
    top = y0

    if title:
        out.append(
            Text(
                at=(x0, top),
                text=title,
                layer=A_TEXT,
                height_paper_um=TEXT_HEIGHT_TITLE_PAPER_UM,
                baseline="hanging",
                bold=True,
            )
        )
        top += 9

    height = header_height_mm + row_height_mm * len(rows)
    out.append(
        Polyline(
            vertices=((x0, top), (x0 + width, top), (x0 + width, top + height), (x0, top + height)),
            layer=grid_layer,
            closed=True,
        )
    )
    # Header rule, then one rule under each body row.
    out.append(Line((x0, top + header_height_mm), (x0 + width, top + header_height_mm), grid_layer))
    for index in range(1, len(rows)):
        y = top + header_height_mm + index * row_height_mm
        out.append(Line((x0, y), (x0 + width, y), grid_layer))
    # Column rules.
    x = x0
    for column in columns[:-1]:
        x += column.width_mm
        out.append(Line((x, top), (x, top + height), grid_layer))

    # Header cells.
    x = x0
    for column in columns:
        out.append(
            _cell_text(
                x,
                top,
                column,
                column.title,
                row_height_mm=header_height_mm,
                height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM,
                bold=True,
            )
        )
        x += column.width_mm

    emphasised = set(emphasise_rows)
    for row_index, row in enumerate(rows):
        if len(row) != len(columns):
            raise ValueError(
                "table row %d has %d cells but there are %d columns — a schedule with a "
                "ragged row prints a blank a contractor will read as zero."
                % (row_index, len(row), len(columns))
            )
        y = top + header_height_mm + row_index * row_height_mm
        x = x0
        for column, value in zip(columns, row, strict=False):
            out.append(
                _cell_text(
                    x,
                    y,
                    column,
                    value,
                    row_height_mm=row_height_mm,
                    height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM,
                    bold=row_index in emphasised,
                    layer=value_layer,
                )
            )
            x += column.width_mm
    return tuple(out)


# ---------------------------------------------------------------------------
# Door / window schedule
# ---------------------------------------------------------------------------
_KIND_LABELS = {"door": "Door", "window": "Window", "ventilator": "Ventilator"}


def schedule_table(
    rows: Sequence[Any],
    *,
    storey_labels: Sequence[tuple[str, str]] = (),
    origin_mm: tuple[int, int] = (25, 25),
    title: str = "DOOR & WINDOW SCHEDULE",
) -> tuple[Primitive, ...]:
    """Render :class:`~services.drawings.sheets.ScheduleRow` records.

    ``storey_labels`` is ``[(storey_id, label)]`` in building order and decides both the
    per-storey count columns and their order. Passing it explicitly (rather than reading
    ``counts_by_storey``' keys) is deliberate: dict order would make the column order
    depend on insertion history, and the goldens are byte-compared.
    """
    columns: list[Column] = [
        Column("TAG", 20, "centre"),
        Column("TYPE", 36),
        Column("WIDTH (mm)", 30, "right"),
        Column("HEIGHT (mm)", 30, "right"),
        Column("SILL (mm)", 26, "right"),
    ]
    for _storey_id, label in storey_labels:
        columns.append(Column(label.upper(), 30, "right"))
    columns.append(Column("TOTAL", 22, "right"))
    columns.append(Column("REMARKS", 60))

    body: list[list[str]] = []
    for row in rows:
        cells = [
            row.tag,
            _KIND_LABELS.get(row.kind, str(row.kind).title()),
            str(row.width_mm),
            str(row.height_mm),
            str(row.sill_mm),
        ]
        for storey_id, _label in storey_labels:
            cells.append(str(row.counts_by_storey.get(storey_id, 0)))
        cells.append(str(row.total))
        cells.append(row.notes)
        body.append(cells)

    if not body:
        body.append([""] * len(columns))

    return table_primitives(
        columns,
        body,
        origin_mm=origin_mm,
        row_height_mm=SCHEDULE_ROW_HEIGHT_MM,
        title=title,
    )


def schedule_group(
    rows: Sequence[Any],
    *,
    storey_labels: Sequence[tuple[str, str]] = (),
    origin_mm: tuple[int, int] = (25, 25),
    group_id: str = "schedule",
) -> DrawingGroup:
    return DrawingGroup(
        id=group_id,
        placement=Placement.paper(),
        primitives=schedule_table(rows, storey_labels=storey_labels, origin_mm=origin_mm),
    )


# ---------------------------------------------------------------------------
# Area statement
# ---------------------------------------------------------------------------
def format_area_dual(mm2: int | None) -> str:
    """``"245.20 m² (2,639.3 ft²)"`` — m² for the municipality, ft² for the client.

    Both come from :mod:`garh_model.units`, whose formatters are golden-tested against
    their TypeScript twins, so a number on a sheet and the same number in the UI cannot
    drift.
    """
    if mm2 is None:
        return "—"
    from garh_model.units import format_sqft, format_sqm

    return "%s (%s)" % (format_sqm(mm2, 2), format_sqft(mm2, 1))


def _format_value(row: Any) -> str:
    if row.unit == "mm2":
        return format_area_dual(row.value if isinstance(row.value, int) else None)
    if row.unit == "mm":
        return "—" if row.value is None else "%d mm" % int(row.value)
    if row.unit == "count":
        return "—" if row.value is None else str(int(row.value))
    return "—" if row.value is None else str(row.value)


def _format_allowed(row: Any) -> str:
    if row.allowed is None:
        return "—"
    if row.unit == "mm2":
        return format_area_dual(int(row.allowed))
    if row.unit == "mm":
        return "%d mm" % int(row.allowed)
    if row.unit == "count":
        return str(int(row.allowed))
    return str(row.allowed)


def area_statement_table(
    statement: Any,
    *,
    origin_mm: tuple[int, int] = (25, 25),
    title: str = "AREA STATEMENT",
) -> tuple[Primitive, ...]:
    """Render a :class:`garh_rules.areas.AreaStatement`. Formats only — never computes.

    The ``note`` column carries the statement's own achieved-vs-allowed ratio note (for
    FAR and coverage) and the rule ids behind a requirement, so the sheet cites the
    bye-law it was checked against — golden rule 4, assumptions and citations visible.

    Warnings on the statement (e.g. "per-storey built-up areas do not sum to the total")
    are printed under the table rather than swallowed. A statement whose rows do not add
    up is a rejected drawing; the sheet says so out loud instead of letting a reviewer
    find it.
    """
    # 400 mm of the 594 mm A2 width: wide enough that a dual-unit area never wraps,
    # narrow enough to clear the title-block column on the right.
    columns = [
        Column("DESCRIPTION", 95),
        Column("PROVIDED / ACHIEVED", 92, "right"),
        Column("PERMISSIBLE / REQUIRED", 92, "right"),
        Column("NOTE / AUTHORITY", 121),
    ]
    body: list[list[str]] = []
    emphasise: list[int] = []
    for row in statement.rows():
        note_parts: list[str] = []
        if row.note:
            note_parts.append(str(row.note))
        if row.rule_ids:
            note_parts.append(", ".join(row.rule_ids))
        limit_label = row.limit_label
        if limit_label and row.allowed is not None:
            note_parts.insert(0, limit_label)
        body.append([row.label, _format_value(row), _format_allowed(row), " · ".join(note_parts)])
        if row.key in ("built_up_total", "far", "coverage"):
            emphasise.append(len(body) - 1)

    out: list[Primitive] = list(
        table_primitives(
            columns,
            body,
            origin_mm=origin_mm,
            row_height_mm=AREA_ROW_HEIGHT_MM,
            title=title,
            value_layer=A_AREA,
            emphasise_rows=emphasise,
        )
    )

    warnings = tuple(getattr(statement, "warnings", ()) or ())
    if warnings:
        x0, y0 = origin_mm
        y = y0 + 9 + _HEADER_HEIGHT_MM + AREA_ROW_HEIGHT_MM * len(body) + 6
        out.append(
            Text(
                at=(x0, y),
                text="NOTES",
                layer=A_TEXT,
                height_paper_um=TEXT_HEIGHT_SMALL_PAPER_UM,
                baseline="hanging",
                bold=True,
            )
        )
        for index, warning in enumerate(warnings):
            out.append(
                Text(
                    at=(x0, y + 5 + index * 4),
                    text="- %s" % warning,
                    layer=A_TEXT,
                    height_paper_um=TEXT_HEIGHT_LABEL_PAPER_UM,
                    baseline="hanging",
                )
            )
    return tuple(out)


def area_statement_group(
    statement: Any,
    *,
    origin_mm: tuple[int, int] = (25, 25),
    group_id: str = "area-statement",
) -> DrawingGroup:
    return DrawingGroup(
        id=group_id,
        placement=Placement.paper(),
        primitives=area_statement_table(statement, origin_mm=origin_mm),
    )
