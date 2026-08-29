"""The area statement in the form an Indian municipal office expects (D-6).

    Area statement per municipal format: plot area, per-storey built-up, total, FAR
    achieved vs allowed, coverage achieved vs allowed, setbacks provided vs required
    (**from rules results — same numbers, one source**).                        -- §7

The numbers were already right. The *form* was not, and a sanction drawing is rejected on
form before anyone reads a number. What a scrutiny clerk is trained to scan is a numbered
statement with a fixed column order:

===========  ==================================================================
SL. NO.      the serial, with sub-serials inside a section (``6.1``, ``6.2``).
             The clerk's query sheet cites it: "clarify item 6.2".
DESCRIPTION  the item, in sections — coverage, FAR, built-up, setbacks — with a
             band row naming each section.
PERMISSIBLE  what the bye-law allows or requires. **This column comes first**,
/ REQUIRED   before the proposal. Every sanction proforma in the country reads
             left to right as "rule, then what you did"; the generic table this
             replaces had it the other way round.
PROPOSED     what the drawing does.
/ PROVIDED
REMARKS      the rule id it was checked against, and the shortfall when short.
===========  ==================================================================

WHERE THE NUMBERS COME FROM — ALL OF THEM
-----------------------------------------
:meth:`statement.rows() <garh_rules.areas.AreaStatement.rows>`, and nothing else. Every
cell in the PERMISSIBLE and PROPOSED columns is a formatted
:class:`~garh_rules.areas.AreaRow` field. There is no addition, no division and no
comparison against a rule pack in this module — grep it for ``/`` outside a docstring.

Two things here are *derived from the engine's own numbers* rather than read from a field,
and both are named so a reviewer of this file can check them:

1. **The FAR and coverage ratios** come from the statement's own
   ``far_achieved`` / ``far_allowed`` / ``coverage_achieved`` / ``coverage_allowed``
   properties — exact ``Fraction``s the engine computes. They are read, never rebuilt from
   the areas; a statement that does not expose them gets a warning row rather than a
   locally-computed number.
2. **A setback's OK/SHORT verdict** is the comparison ``provided < required`` of two
   integers the engine supplied. That is a *relation between* engine numbers, not a second
   source for either of them, and
   ``test_area_statement.py::test_setback_verdicts_agree_with_the_rules_engine`` pins it
   against ``garh_rules.areas.SetbackRow.status`` so the two cannot drift.

Formatting is :mod:`services.drawings.schedules.display`'s job and only its job — m²/sq ft
(and gaj on the plot line), millimetres for lengths, and ``garh_rules.formatting`` for
every ratio, so the sheet and the compliance chip print the same digits.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from services.drawings.schedules.display import gaj_text, percent_cell, ratio_cell
from services.drawings.schedules.table import Column, Table, TableStyle

__all__ = [
    "CERTIFICATION_NOTES",
    "COLUMN_FIELDS",
    "MISSING",
    "MUNICIPAL_COLUMNS",
    "FormRow",
    "MunicipalAreaForm",
    "municipal_form",
]

#: What an absent figure prints as — the em dash sheet A-06 has always used. Never "0":
#: "no FAR rule applied" and "a FAR of zero" are different facts and a municipal sheet
#: must not conflate them.
MISSING = "\u2014"

#: The five columns, in the order a scrutiny clerk reads them. ``(key, header, align)``.
#:
#: **This tuple is the only statement of the column order in the product.** Everything
#: else derives from it: :meth:`FormRow.cells` emits its cells in this order (via
#: :data:`COLUMN_FIELDS`), :meth:`MunicipalAreaForm.table` builds the text/JSON table's
#: headers from it, and ``render.tables.area_statement_table`` builds the drawn sheet's
#: ruled columns from it (widths keyed by ``key``, never by position). A second, unlinked
#: list of columns is the exact shape that produced this repo's three hatch drifts — the
#: sheet would print PROPOSED under the PERMISSIBLE heading and no test would notice.
MUNICIPAL_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("sl", "SL. NO.", "left"),
    ("item", "DESCRIPTION", "left"),
    ("limit", "PERMISSIBLE / REQUIRED", "right"),
    ("value", "PROPOSED / PROVIDED", "right"),
    ("remarks", "REMARKS", "left"),
)

#: Column key -> the :class:`FormRow` attribute that fills it. The one place the two
#: vocabularies meet; a column with no entry here is a column nothing can fill, and
#: :meth:`FormRow.cells` says so rather than printing a blank a reviewer reads as "nil".
COLUMN_FIELDS: dict[str, str] = {
    "sl": "number",
    "item": "description",
    "limit": "limit",
    "value": "value",
    "remarks": "remarks",
}

#: The strip under the table. The signature lines are blank fields on a form for the
#: people who sign it — this software certifies nothing, and the third note says so in as
#: many words, because every rule-pack value in this repository is graded ``seed`` until an
#: empaneled local architect has checked it against the current bye-laws.
CERTIFICATION_NOTES: tuple[str, ...] = (
    "Permissible and required figures are the compliance engine's results for the rule "
    "packs loaded, cited by rule id in REMARKS. This sheet does not recompute them.",
    "Rule-pack values carry a confidence grade and must be verified against the "
    "sanctioning authority's bye-laws in force before submission.",
    "SIGNATURE OF OWNER: ______________________     "
    "SIGNATURE & SEAL OF ARCHITECT / LICENSED ENGINEER: ______________________",
)


@dataclass(frozen=True)
class FormRow:
    """One printed line. ``band`` rows are section headings and carry no figures."""

    number: str
    description: str
    limit: str = MISSING
    value: str = MISSING
    remarks: str = ""
    band: bool = False
    #: Totals and the two ratio lines — the rows a reviewer's eye is sent to.
    emphasis: bool = False

    def cells(self) -> tuple[str, ...]:
        """The row's cells in :data:`MUNICIPAL_COLUMNS` order — derived, never listed.

        Reordering the columns therefore reorders the cells, in the text table, the JSON
        and the drawn sheet, together. A hand-written tuple here would be a second
        statement of the order that a column swap would silently leave behind.
        """
        cells: list[str] = []
        for key, header, _align in MUNICIPAL_COLUMNS:
            try:
                field_name = COLUMN_FIELDS[key]
            except KeyError as error:
                raise KeyError(
                    "municipal column %r (%s) has no FormRow field in COLUMN_FIELDS, so "
                    "nothing can fill it. Add the mapping rather than printing a blank "
                    "cell a scrutiny clerk reads as nil." % (key, header)
                ) from error
            cells.append(getattr(self, field_name))
        return tuple(cells)

    def to_json(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "description": self.description,
            "limit": self.limit,
            "value": self.value,
            "remarks": self.remarks,
            "band": self.band,
            "emphasis": self.emphasis,
        }


@dataclass(frozen=True)
class MunicipalAreaForm:
    """The numbered statement: rows, notes, and the two renderings of them."""

    rows: tuple[FormRow, ...]
    warnings: tuple[str, ...] = ()

    def cells(self) -> tuple[tuple[str, ...], ...]:
        return tuple(row.cells() for row in self.rows)

    def emphasis_indices(self) -> tuple[int, ...]:
        """Row indices to print bold: section bands, totals and the ratio lines."""
        return tuple(index for index, row in enumerate(self.rows) if row.band or row.emphasis)

    def band_indices(self) -> tuple[int, ...]:
        """Row indices that start a section — where the sheet draws a rule."""
        return tuple(index for index, row in enumerate(self.rows) if row.band)

    def rule_after(self) -> tuple[int, ...]:
        """Rows to rule *under*: every row that ends a serial item.

        A row ends an item when the next row starts a new top-level serial — a number
        with no dot in it, i.e. a section band (``6``) or a plain item (``7``). The last
        row is deliberately absent: :meth:`Table.emit` already draws the table's bottom
        border there, and a second line on the same coordinates is a duplicate DXF entity
        a CAD user sees as a stubborn double-thickness rule they cannot delete.
        """
        return tuple(
            index for index in range(len(self.rows) - 1) if "." not in self.rows[index + 1].number
        )

    def notes(self) -> tuple[str, ...]:
        """Footnotes: the units legend, the warnings, then the certification strip."""
        return (
            "Areas in m2 with sq ft alongside; plot area also in gaj (1 gaj = 1 sq yd = "
            "9 sq ft). All lengths in millimetres.",
            *self.warnings,
            *CERTIFICATION_NOTES,
        )

    def table(
        self,
        *,
        title: str = "AREA STATEMENT",
        style: TableStyle | None = None,
        origin_mm: tuple[int, int] = (0, 0),
    ) -> Table:
        """The text/JSON/standalone-SVG rendering (what a golden diffs)."""
        return Table(
            title=title,
            columns=tuple(Column(key, header, align) for key, header, align in MUNICIPAL_COLUMNS),
            rows=self.cells(),
            style=style or TableStyle(),
            origin_mm=origin_mm,
            rule_after=self.rule_after(),
            bold_rows=self.emphasis_indices(),
            footnotes=self.notes(),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "columns": [
                {"key": key, "header": header, "align": align}
                for key, header, align in MUNICIPAL_COLUMNS
            ],
            "rows": [row.to_json() for row in self.rows],
            "notes": list(self.notes()),
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# Building it — formatting only
# ---------------------------------------------------------------------------
def _area(mm2: int) -> str:
    """The printed dual-unit area string, defined once for the whole sheet set.

    Imported here rather than re-spelled because ``render.tables.format_area_dual`` is
    already what the door/window schedule and every previous A-06 printed; a second
    spelling of "245.20 m2 (2,639.3 ft2)" in this module would be two ways of writing one
    number on one set. The import is function-level to keep the package graph acyclic —
    ``render.tables`` reaches back into this module to lay the proforma out.
    """
    from services.drawings.render.tables import format_area_dual

    return format_area_dual(mm2)


def _format(value: Any, unit: str) -> str:
    """One engine value -> one cell. The only place a unit decides a formatter."""
    if value is None:
        return MISSING
    if unit == "mm2":
        return _area(int(value))
    if unit == "mm":
        return "%d mm" % int(value)
    if unit == "count":
        return "%d" % int(value)
    return str(value)


def _remarks(row: Any, extra: str = "") -> str:
    parts = [part for part in (extra, ", ".join(getattr(row, "rule_ids", ()) or ())) if part]
    return " · ".join(parts)


def _ratio(statement: Any, name: str) -> Fraction | None | object:
    """Read an engine ratio property, or :data:`_MISSING` when it does not carry one."""
    return getattr(statement, name, _MISSING)


_MISSING = object()


def municipal_form(
    statement: Any,
    *,
    carpet_lines: Sequence[Any] = (),
) -> MunicipalAreaForm:
    """Lay a :class:`garh_rules.areas.AreaStatement` out as the municipal proforma.

    ``statement`` is anything that answers ``rows()`` and the four ratio properties — the
    rules engine's own :class:`~garh_rules.areas.AreaStatement`, the worker's
    ``TransportStatement`` codec, or a
    :class:`~services.drawings.schedules.area_statement.AreaStatementSheet` (which
    delegates to the statement it was rendered from).

    ``carpet_lines`` are
    :class:`~services.drawings.schedules.area_statement.StoreyLine` records. Carpet area is
    not a regulatory number — no pack bands anything on it — so it is optional, and it is
    printed in its own section rather than mixed into the built-up figures a reviewer is
    checking against FAR.
    """
    source = getattr(statement, "statement", statement)
    rows = tuple(source.rows())
    by_key = {row.key: row for row in rows}
    warnings: list[str] = list(getattr(source, "warnings", ()) or ())

    out: list[FormRow] = []
    serial = 0

    def section(title: str) -> str:
        nonlocal serial
        serial += 1
        number = str(serial)
        out.append(FormRow(number=number, description=title, limit="", value="", band=True))
        return number

    def single(title: str, row: Any, *, extra: str = "") -> None:
        nonlocal serial
        serial += 1
        out.append(
            FormRow(
                number=str(serial),
                description=title,
                limit=_format(row.allowed, row.unit),
                value=_format(row.value, row.unit),
                remarks=_remarks(row, extra),
            )
        )

    # 1 — plot area
    plot_row = by_key.get("plot_area")
    if plot_row is not None:
        # §15: a plot is quoted in gaj in north India, and only a plot — a built-up area
        # in gaj is not a number anybody uses. It goes in REMARKS rather than into the
        # figure cell, so the PROPOSED column stays one area in one pair of units all the
        # way down and a reader can scan it.
        gaj = (
            gaj_text(int(plot_row.value))
            if isinstance(plot_row.value, int) and not isinstance(plot_row.value, bool)
            else ""
        )
        single("PLOT AREA (as per document)", plot_row, extra=gaj)

    # 2 — ground coverage: area, then the percentage the clerk actually checks
    coverage_row = by_key.get("coverage")
    if coverage_row is not None:
        parent = section("GROUND COVERAGE")
        out.append(
            FormRow(
                number="%s.1" % parent,
                description="Covered area on ground floor",
                limit=_format(coverage_row.allowed, coverage_row.unit),
                value=_format(coverage_row.value, coverage_row.unit),
                remarks=_remarks(coverage_row),
            )
        )
        achieved = _ratio(source, "coverage_achieved")
        allowed = _ratio(source, "coverage_allowed")
        if achieved is _MISSING or allowed is _MISSING:
            warnings.append(
                "This statement does not carry coverage ratios, so the percentage row is "
                "omitted rather than computed here."
            )
        else:
            out.append(
                FormRow(
                    number="%s.2" % parent,
                    description="Ground coverage (%)",
                    limit=percent_cell(allowed),  # type: ignore[arg-type]
                    value=percent_cell(achieved),  # type: ignore[arg-type]
                    remarks=_remarks(coverage_row),
                    emphasis=True,
                )
            )

    # 3 — FAR: countable area, then the ratio
    far_row = by_key.get("far")
    if far_row is not None:
        parent = section("FLOOR AREA RATIO (FAR)")
        out.append(
            FormRow(
                number="%s.1" % parent,
                description="FAR-countable built-up area",
                limit=_format(far_row.allowed, far_row.unit),
                value=_format(far_row.value, far_row.unit),
                remarks=_remarks(far_row),
            )
        )
        achieved = _ratio(source, "far_achieved")
        allowed = _ratio(source, "far_allowed")
        if achieved is _MISSING or allowed is _MISSING:
            warnings.append(
                "This statement does not carry FAR ratios, so the FAR row is omitted "
                "rather than computed here."
            )
        else:
            out.append(
                FormRow(
                    number="%s.2" % parent,
                    description="Floor area ratio (FAR)",
                    limit=ratio_cell(allowed),  # type: ignore[arg-type]
                    value=ratio_cell(achieved),  # type: ignore[arg-type]
                    remarks=_remarks(far_row),
                    emphasis=True,
                )
            )

    # 4 — built-up, floor by floor, then the total
    storey_rows = [row for row in rows if row.key.startswith("built_up.")]
    total_row = by_key.get("built_up_total")
    if storey_rows or total_row is not None:
        parent = section("BUILT-UP AREA")
        index = 0
        for row in storey_rows:
            index += 1
            out.append(
                FormRow(
                    number="%s.%d" % (parent, index),
                    description=row.label,
                    limit=MISSING,
                    value=_format(row.value, row.unit),
                    remarks="" if row.value is not None else "not reported by the model",
                )
            )
        if total_row is not None:
            index += 1
            out.append(
                FormRow(
                    number="%s.%d" % (parent, index),
                    description="TOTAL BUILT-UP AREA",
                    limit=MISSING,
                    value=_format(total_row.value, total_row.unit),
                    emphasis=True,
                )
            )

    # 5 — carpet, when the caller has it. Not regulatory; its own section for that reason.
    known_carpet = [line for line in carpet_lines if line.carpet_area_mm2 is not None]
    if known_carpet:
        parent = section("CARPET AREA (not a regulatory figure)")
        for index, line in enumerate(known_carpet, start=1):
            out.append(
                FormRow(
                    number="%s.%d" % (parent, index),
                    description=line.label,
                    limit=MISSING,
                    value=_area(line.carpet_area_mm2),
                    remarks=(
                        "%s of built-up" % percent_cell(line.efficiency)
                        if line.efficiency is not None
                        else ""
                    ),
                )
            )
        total_carpet = sum(line.carpet_area_mm2 for line in known_carpet)
        if len(known_carpet) == len(carpet_lines):
            out.append(
                FormRow(
                    number="%s.%d" % (parent, len(known_carpet) + 1),
                    description="TOTAL CARPET AREA",
                    limit=MISSING,
                    value=_area(total_carpet),
                    emphasis=True,
                )
            )
        else:
            warnings.append(
                "Carpet area is not stated for every storey, so no carpet total is given."
            )

    # 6 — setbacks
    setback_rows = [row for row in rows if row.key.startswith("setback.")]
    if setback_rows:
        parent = section("SETBACKS (mm)")
        for index, row in enumerate(setback_rows, start=1):
            # A comparison of two engine integers, not a second source for either. See
            # the module docstring; the verdict is pinned against the engine by test.
            short = (
                row.allowed is not None
                and row.value is not None
                and int(row.value) < int(row.allowed)
            )
            verdict = (
                "SHORT BY %d mm" % (int(row.allowed) - int(row.value))
                if short
                else ("OK" if row.allowed is not None else "not regulated")
            )
            out.append(
                FormRow(
                    number="%s.%d" % (parent, index),
                    description=row.label,
                    limit=_format(row.allowed, row.unit),
                    value=_format(row.value, row.unit),
                    remarks=_remarks(row, verdict),
                    emphasis=short,
                )
            )

    # 7.. — the scalar rows, in proforma order
    for key, title in (
        ("height", "HEIGHT OF BUILDING (mm)"),
        ("floors", "NUMBER OF FLOORS ABOVE GROUND"),
        ("parking", "CAR PARKING SPACES (nos.)"),
    ):
        row = by_key.get(key)
        if row is not None:
            single(title, row)

    unrendered = sorted(
        set(by_key)
        - {"plot_area", "coverage", "far", "built_up_total", "height", "floors", "parking"}
        - {row.key for row in storey_rows}
        - {row.key for row in setback_rows}
    )
    if unrendered:
        # The engine grew a row this form does not know where to put. Saying so beats
        # dropping it: a statement that silently omits a regulated quantity is the
        # defect this whole module exists to prevent.
        warnings.append(
            "The compliance engine reported %d figure(s) this form has no section for "
            "(%s); they are not printed above." % (len(unrendered), ", ".join(unrendered))
        )

    return MunicipalAreaForm(rows=tuple(out), warnings=tuple(warnings))
