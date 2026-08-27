"""Table primitives for the two §7 table sheets. **Fully implemented, pure integers.**

§7's door/window schedule and area statement are the only two sheets that are *tables*
rather than projections (``Sheet.validate`` already knows this: it skips the viewport
rule for exactly those two kinds). They still have to sit on a sheet next to drawings,
so they are laid out here in the same **paper millimetres** the frame uses, on the same
nine layers, and emitted as the same flavour of primitive the projection pipeline
produces: text items and line items with a layer tag.

Three deliberate properties:

* **Integer paper mm, no floats.** Text height 3 mm, character width 2 mm, row height
  7 mm. Column widths come from character counts, so the layout of a given table is a
  pure function of its strings — which is what makes a byte-diffed golden meaningful.
  A float row height would make two runs on two machines disagree in the last digit.
* **No layer invented here.** Every layer goes through
  :func:`services.drawings.layers.layer_for`, so a typo fails loudly at construction
  instead of quietly adding a tenth layer to a municipal DXF.
* **One geometry, three outputs.** :meth:`Table.primitives` (for DXF/SVG renderers),
  :meth:`Table.to_text` (the golden format — ASCII, no coordinates, so a golden diff
  reads like a table) and :meth:`Table.to_svg` (§13-sanitised: escaped text, no
  script, no ``foreignObject``).

The seam for the sheet renderer is :meth:`Table.emit`: hand it the projection
pipeline's own text/line constructors and it builds them instead of the local
dataclasses, so nothing here has to import a module that Phase 8 is still writing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from services.drawings.layers import A_TEXT, A_TITL, layer_for

__all__ = [
    "ALIGNMENTS",
    "Column",
    "LineItem",
    "Table",
    "TableStyle",
    "TextItem",
    "column_map",
    "svg_escape",
]

ALIGNMENTS: tuple[str, ...] = ("left", "right", "centre")


@dataclass(frozen=True)
class TableStyle:
    """Paper-millimetre metrics. All integers; see the module docstring for why.

    ``char_width_mm`` is the *estimate* used for column widths. Real CAD text is
    proportional, so 2 mm per character at 3 mm height (a 0.67 width factor) is a
    deliberate slight over-estimate: a column that is 1 mm too wide looks fine, and a
    column 1 mm too narrow puts a tag on top of a size.
    """

    text_height_mm: int = 3
    char_width_mm: int = 2
    padding_mm: int = 2
    row_height_mm: int = 7
    header_height_mm: int = 8
    title_height_mm: int = 5
    title_gap_mm: int = 3
    text_layer: str = A_TEXT
    grid_layer: str = A_TITL

    def __post_init__(self) -> None:
        for name in ("text_height_mm", "char_width_mm", "row_height_mm", "header_height_mm"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(
                    "TableStyle.%s must be a positive integer of paper millimetres, got %r. "
                    "Fractional paper units reintroduce the drift integer mm exist to avoid."
                    % (name, value)
                )
        if self.text_height_mm > self.row_height_mm:
            raise ValueError(
                "text_height_mm (%d) must fit inside row_height_mm (%d)."
                % (self.text_height_mm, self.row_height_mm)
            )
        # Fail at construction, not at render time, and name the nine layers.
        layer_for(self.text_layer)
        layer_for(self.grid_layer)


@dataclass(frozen=True)
class Column:
    """One column: its key, printed header, alignment and a floor on its width."""

    key: str
    header: str
    align: str = "left"
    min_width_mm: int = 0

    def __post_init__(self) -> None:
        if self.align not in ALIGNMENTS:
            raise ValueError(
                "Column %r alignment must be one of %s, got %r"
                % (self.key, ", ".join(ALIGNMENTS), self.align)
            )


@dataclass(frozen=True)
class TextItem:
    """A placed string. ``x_mm``/``y_mm`` is the anchor, in paper mm, y up."""

    x_mm: int
    y_mm: int
    text: str
    height_mm: int
    layer: str
    align: str = "left"
    bold: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "type": "text",
            "xMm": self.x_mm,
            "yMm": self.y_mm,
            "text": self.text,
            "heightMm": self.height_mm,
            "layer": self.layer,
            "align": self.align,
            "bold": self.bold,
        }


@dataclass(frozen=True)
class LineItem:
    """A grid line, in paper mm, y up."""

    x1_mm: int
    y1_mm: int
    x2_mm: int
    y2_mm: int
    layer: str

    def to_json(self) -> dict[str, Any]:
        return {
            "type": "line",
            "x1Mm": self.x1_mm,
            "y1Mm": self.y1_mm,
            "x2Mm": self.x2_mm,
            "y2Mm": self.y2_mm,
            "layer": self.layer,
        }


def svg_escape(text: str) -> str:
    """Escape for SVG text content and attributes (§13: nothing executable ships).

    Escapes ``&``, ``<``, ``>``, ``"`` and ``'``. A room name is user input, and a room
    called ``</text><script>`` must render as those characters, not as markup.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


@dataclass(frozen=True)
class Table:
    """A titled table, laid out from its own strings.

    ``origin_mm`` is the bottom-left corner of the table block in paper mm; rows print
    top-down from the header, which is how a schedule reads.
    """

    title: str
    columns: tuple[Column, ...]
    rows: tuple[tuple[str, ...], ...]
    style: TableStyle = field(default_factory=TableStyle)
    origin_mm: tuple[int, int] = (0, 0)
    #: Rows the reader should see as separators/subtotals — indices into ``rows``.
    rule_after: tuple[int, ...] = ()
    #: Row indices printed bold (totals, and the FAR/coverage lines that get queried).
    bold_rows: tuple[int, ...] = ()
    footnotes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        width = len(self.columns)
        if width == 0:
            raise ValueError("A table needs at least one column.")
        for index, row in enumerate(self.rows):
            if len(row) != width:
                raise ValueError(
                    "Row %d has %d cells but the table has %d columns (%s). A ragged "
                    "table means a caller built cells positionally against the wrong "
                    "column list."
                    % (index, len(row), width, ", ".join(c.key for c in self.columns))
                )

    # -- layout ------------------------------------------------------------
    def cell_widths_mm(self) -> tuple[int, ...]:
        """Column widths from the longest string in each column, plus padding."""
        widths: list[int] = []
        for index, column in enumerate(self.columns):
            longest = len(column.header)
            for row in self.rows:
                longest = max(longest, len(row[index]))
            widths.append(
                max(
                    column.min_width_mm,
                    longest * self.style.char_width_mm + 2 * self.style.padding_mm,
                )
            )
        return tuple(widths)

    def width_mm(self) -> int:
        return sum(self.cell_widths_mm())

    def body_height_mm(self) -> int:
        return self.style.header_height_mm + len(self.rows) * self.style.row_height_mm

    def height_mm(self) -> int:
        total = self.body_height_mm()
        if self.title:
            total += self.style.title_height_mm + self.style.title_gap_mm
        if self.footnotes:
            total += len(self.footnotes) * self.style.row_height_mm
        return total

    def fits_within(self, drawable_width_mm: int, drawable_height_mm: int) -> bool:
        """Does this table fit the frame's drawable area? (A2 landscape: 564 × 400.)"""
        return self.width_mm() <= drawable_width_mm and self.height_mm() <= drawable_height_mm

    def at(self, x_mm: int, y_mm: int) -> Table:
        """The same table placed elsewhere on the sheet."""
        return replace(self, origin_mm=(x_mm, y_mm))

    def _column_x_mm(self) -> tuple[int, ...]:
        x = self.origin_mm[0]
        out: list[int] = []
        for width in self.cell_widths_mm():
            out.append(x)
            x += width
        return tuple(out)

    def _top_y_mm(self) -> int:
        """Y of the top edge of the header band."""
        return (
            self.origin_mm[1]
            + self.body_height_mm()
            + len(self.footnotes) * self.style.row_height_mm
        )

    # -- primitives --------------------------------------------------------
    def emit(
        self,
        *,
        text: Callable[..., Any] | None = None,
        line: Callable[..., Any] | None = None,
    ) -> tuple[Any, ...]:
        """Build the primitive stream, optionally with the caller's constructors.

        The sheet renderer passes its own factories —
        ``text(x_mm=, y_mm=, text=, height_mm=, layer=, align=, bold=)`` and
        ``line(x1_mm=, y1_mm=, x2_mm=, y2_mm=, layer=)`` — and gets its own primitive
        objects back, so this module never has to import the projection pipeline.
        Called with no arguments it returns :class:`TextItem` / :class:`LineItem`.
        """
        make_text = text or TextItem
        make_line = line or LineItem
        style = self.style
        widths = self.cell_widths_mm()
        xs = self._column_x_mm()
        table_width = self.width_mm()
        left = self.origin_mm[0]
        top = self._top_y_mm()
        items: list[Any] = []

        if self.title:
            items.append(
                make_text(
                    x_mm=left,
                    y_mm=top + style.title_gap_mm,
                    text=self.title,
                    height_mm=style.title_height_mm,
                    layer=style.text_layer,
                    align="left",
                    bold=True,
                )
            )

        # Header band.
        header_baseline = (
            top - style.header_height_mm + (style.header_height_mm - style.text_height_mm) // 2
        )
        for index, column in enumerate(self.columns):
            items.append(
                make_text(
                    x_mm=_anchor_x(xs[index], widths[index], column.align, style.padding_mm),
                    y_mm=header_baseline,
                    text=column.header,
                    height_mm=style.text_height_mm,
                    layer=style.text_layer,
                    align=_svg_anchor(column.align),
                    bold=True,
                )
            )

        # Rows, top-down.
        row_tops = [
            top - style.header_height_mm - i * style.row_height_mm for i in range(len(self.rows))
        ]
        for row_index, row in enumerate(self.rows):
            row_top = row_tops[row_index]
            baseline = (
                row_top - style.row_height_mm + (style.row_height_mm - style.text_height_mm) // 2
            )
            for col_index, column in enumerate(self.columns):
                cell = row[col_index]
                if not cell:
                    continue
                items.append(
                    make_text(
                        x_mm=_anchor_x(
                            xs[col_index], widths[col_index], column.align, style.padding_mm
                        ),
                        y_mm=baseline,
                        text=cell,
                        height_mm=style.text_height_mm,
                        layer=style.text_layer,
                        align=_svg_anchor(column.align),
                        bold=row_index in self.bold_rows,
                    )
                )

        # Grid: outer box, one line under the header, one under each ``rule_after`` row,
        # and the column separators. Not a full grid — a municipal schedule is read in
        # rows, and a line under every row turns into visual noise at 1:100.
        bottom = top - self.body_height_mm()
        items.append(
            make_line(
                x1_mm=left, y1_mm=top, x2_mm=left + table_width, y2_mm=top, layer=style.grid_layer
            )
        )
        items.append(
            make_line(
                x1_mm=left,
                y1_mm=top - style.header_height_mm,
                x2_mm=left + table_width,
                y2_mm=top - style.header_height_mm,
                layer=style.grid_layer,
            )
        )
        items.append(
            make_line(
                x1_mm=left,
                y1_mm=bottom,
                x2_mm=left + table_width,
                y2_mm=bottom,
                layer=style.grid_layer,
            )
        )
        for row_index in self.rule_after:
            if not 0 <= row_index < len(self.rows):
                raise ValueError(
                    "rule_after references row %d but the table has %d rows."
                    % (row_index, len(self.rows))
                )
            if row_index == len(self.rows) - 1:
                # The bottom border is already there; a second line on the same
                # coordinates is a duplicate entity in the DXF, which a CAD user sees
                # as a stubborn double-thickness rule they cannot delete.
                continue
            y = row_tops[row_index] - style.row_height_mm
            items.append(
                make_line(
                    x1_mm=left, y1_mm=y, x2_mm=left + table_width, y2_mm=y, layer=style.grid_layer
                )
            )
        for x in [*list(xs), left + table_width]:
            items.append(
                make_line(x1_mm=x, y1_mm=bottom, x2_mm=x, y2_mm=top, layer=style.grid_layer)
            )

        for index, note in enumerate(self.footnotes):
            items.append(
                make_text(
                    x_mm=left,
                    y_mm=bottom
                    - (index + 1) * style.row_height_mm
                    + (style.row_height_mm - style.text_height_mm) // 2,
                    text=note,
                    height_mm=style.text_height_mm,
                    layer=style.text_layer,
                    align="start",
                    bold=False,
                )
            )
        return tuple(items)

    def primitives(self) -> tuple[Any, ...]:
        """The local-dataclass primitive stream (text items then line items, as emitted)."""
        return self.emit()

    def text_items(self) -> tuple[TextItem, ...]:
        return tuple(item for item in self.emit() if isinstance(item, TextItem))

    def line_items(self) -> tuple[LineItem, ...]:
        return tuple(item for item in self.emit() if isinstance(item, LineItem))

    # -- serialisations ----------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "columns": [{"key": c.key, "header": c.header, "align": c.align} for c in self.columns],
            "rows": [list(row) for row in self.rows],
            "columnWidthsMm": list(self.cell_widths_mm()),
            "widthMm": self.width_mm(),
            "heightMm": self.height_mm(),
            "originMm": list(self.origin_mm),
            "footnotes": list(self.footnotes),
        }

    def to_text(self) -> str:
        """Fixed-width ASCII rendering — the golden format.

        Coordinates are deliberately absent: a golden that pins pixel positions fails
        on every cosmetic nudge, while this one fails exactly when a *number*, a *tag*
        or a *row order* changes, which is what §16's tolerance-0 diff is for.
        """
        widths = [
            max(
                len(column.header),
                max((len(row[index]) for row in self.rows), default=0),
            )
            for index, column in enumerate(self.columns)
        ]
        lines: list[str] = []
        if self.title:
            lines.append(self.title)
            lines.append("=" * len(self.title))
        rule = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
        lines.append(rule)
        lines.append(_text_row([c.header for c in self.columns], widths, self.columns))
        lines.append(rule)
        for index, row in enumerate(self.rows):
            lines.append(_text_row(list(row), widths, self.columns))
            if index in self.rule_after:
                lines.append(rule)
        lines.append(rule)
        for note in self.footnotes:
            lines.append(note)
        return "\n".join(lines) + "\n"

    def to_svg(self, *, margin_mm: int = 6) -> str:
        """A standalone SVG of this table, in paper mm (1 unit = 1 mm).

        §13: no ``<script>``, no ``foreignObject``, no external references, every string
        escaped. That is testable, and :mod:`services.drawings.tests.test_schedules`
        tests it.
        """
        width = self.width_mm() + 2 * margin_mm
        height = self.height_mm() + 2 * margin_mm
        placed = self.at(margin_mm, margin_mm)
        parts: list[str] = [
            '<svg xmlns="http://www.w3.org/2000/svg" width="%dmm" height="%dmm" '
            'viewBox="0 0 %d %d" fill="none" stroke="none">' % (width, height, width, height),
            "<!-- Garh AI schedule table. Paper millimetres; y flipped for SVG. -->",
            # Paper. A submission drawing is black on white by definition, so the sheet
            # paints its own ground rather than inheriting a viewer's theme.
            '<rect x="0" y="0" width="%d" height="%d" fill="#fff"/>' % (width, height),
        ]
        for item in placed.emit():
            if isinstance(item, TextItem):
                y = height - item.y_mm
                parts.append(
                    '<text x="%d" y="%d" font-family="monospace" font-size="%d" '
                    'font-weight="%s" text-anchor="%s" fill="#000">%s</text>'
                    % (
                        item.x_mm,
                        y,
                        item.height_mm,
                        "bold" if item.bold else "normal",
                        item.align if item.align in ("start", "middle", "end") else "start",
                        svg_escape(item.text),
                    )
                )
            else:
                parts.append(
                    '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#000" stroke-width="0.25"/>'
                    % (
                        item.x1_mm,
                        height - item.y1_mm,
                        item.x2_mm,
                        height - item.y2_mm,
                    )
                )
        parts.append("</svg>")
        return "\n".join(parts) + "\n"


def _anchor_x(cell_x_mm: int, cell_width_mm: int, align: str, padding_mm: int) -> int:
    if align == "right":
        return cell_x_mm + cell_width_mm - padding_mm
    if align == "centre":
        return cell_x_mm + cell_width_mm // 2
    return cell_x_mm + padding_mm


def _svg_anchor(align: str) -> str:
    return {"left": "start", "centre": "middle", "right": "end"}[align]


def _text_row(cells: Sequence[str], widths: Sequence[int], columns: Sequence[Column]) -> str:
    out: list[str] = []
    for index, cell in enumerate(cells):
        width = widths[index]
        if columns[index].align == "right":
            out.append(" %s " % cell.rjust(width))
        elif columns[index].align == "centre":
            out.append(" %s " % cell.center(width))
        else:
            out.append(" %s " % cell.ljust(width))
    return "|" + "|".join(out) + "|"


def column_map(columns: Sequence[Column]) -> Mapping[str, int]:
    """``key -> index``, for callers building rows by name."""
    return {column.key: index for index, column in enumerate(columns)}
