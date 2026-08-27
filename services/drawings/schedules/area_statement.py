"""The §7 area statement sheet — a *rendering* of the rules engine's numbers.

Read this before changing anything here:

    Area statement per municipal format: plot area, per-storey built-up, total, FAR
    achieved vs allowed, coverage achieved vs allowed, setbacks provided vs required
    (**from rules results — same numbers, one source**).

``apps/api/garh_rules/areas.py`` is that one source, and it already exists — Phase 2
built it precisely so the drawings engine and the UI would call one function. This
module therefore contains **no FAR arithmetic, no coverage arithmetic and no setback
arithmetic at all**. It calls
:func:`garh_rules.areas.area_statement` (or accepts an :class:`AreaStatement` a caller
already has, e.g. from ``report.areas``, so a sheet job does not re-run the engine),
and turns it into rows, a table and JSON.

Grep this file for ``/`` outside a docstring and you will find one division: none in a
regulatory number. That is deliberate and it is the point of the task being split out.
Two sources of truth for FAR is not a style problem — it is the bug that ships a
drawing whose area statement contradicts its own compliance annexure, and that drawing
comes back from the counter.

**Carpet area** is the one number §7 asks for that the helper does not carry yet
(``StoreyAreaRow`` has ``built_up_area_mm2`` and no carpet). It is not a regulatory
number — no pack bands anything on carpet area — so it is *derived* here from the room
areas of the same projection the compliance results were computed from
(``context.model.rooms``), or from ``garh_model.fold.storey_carpet_area_mm2`` when the
caller has a folded document. Same rooms, same integers, one definition, stated in
:data:`CARPET_EXCLUDED_ROOM_TYPES`. The right long-term home for it is
``StoreyAreaRow.carpet_area_mm2`` in the helper; see this module's note in the phase
return notes.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from services.drawings.schedules.display import (
    DASH,
    area_cell,
    count_cell,
    mm_cell,
    percent_cell,
    plot_area_cell,
    ratio_cell,
    storey_row_label,
)
from services.drawings.schedules.openings import StoreyRef, normalise_storeys
from services.drawings.schedules.sheet_primitives import AreaStatementRow
from services.drawings.schedules.table import Column, Table, TableStyle

__all__ = [
    "CARPET_EXCLUDED_ROOM_TYPES",
    "AreaStatementSheet",
    "CarpetRow",
    "SetbackLine",
    "StoreyLine",
    "build_area_statement_sheet",
    "carpet_by_storey",
]

#: Room types that are covered area but not *carpet* area.
#:
#: Carpet area is net usable enclosed floor area. Balconies, terraces, porches and
#: stilts are covered-but-open; shafts, ducts and double-height voids are not floor at
#: all. Both vocabularies are listed because the model core and the rule packs spell
#: some of these differently (``garh_model.ROOM_TYPES`` vs the packs' ``roomType``), and
#: a statement that silently counted a terrace as carpet would overstate the number the
#: client is buying.
CARPET_EXCLUDED_ROOM_TYPES: tuple[str, ...] = (
    "balcony",
    "courtyard",
    "duct",
    "mumty",
    "porch",
    "shaft",
    "stilt",
    "terrace",
    "void",
)


def _ensure_apps_api_on_path() -> None:
    try:
        import garh_rules  # noqa: F401

        return
    except ImportError:
        pass
    root = Path(__file__).resolve().parents[3]
    candidate = root / "apps" / "api"
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.append(str(candidate))


# ---------------------------------------------------------------------------
# Carpet area — the one derived number, from the same rooms
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CarpetRow:
    """Carpet area of one storey, and what was left out of it."""

    storey_id: str
    carpet_area_mm2: int
    #: Rooms counted, and rooms skipped as covered-but-not-carpet.
    counted_rooms: int
    excluded_rooms: int

    def to_json(self) -> dict[str, Any]:
        return {
            "storeyId": self.storey_id,
            "carpetAreaMm2": self.carpet_area_mm2,
            "countedRooms": self.counted_rooms,
            "excludedRooms": self.excluded_rooms,
        }


def carpet_by_storey(
    source: Any, *, exclude_types: Sequence[str] = CARPET_EXCLUDED_ROOM_TYPES
) -> dict[str, CarpetRow]:
    """Carpet area per storey, from the rooms of ``source``.

    ``source`` is a ``garh_rules`` ``EvaluationContext`` (the same object the engine
    evaluated — its ``model.rooms`` carry the areas the room-minimum rules were checked
    against), a ``garh_model`` ``ProjectDoc``/``HouseModel``, or the JSON form of
    either. Whichever it is, the arithmetic is one sum of integers — the model layer
    already derived every room's ``areaMm2`` from its clear polygon, and re-deriving it
    here would be a second geometry implementation to keep in step.
    """
    excluded = frozenset(exclude_types)
    out: dict[str, CarpetRow] = {}
    for storey_id, room_type, area_mm2 in _rooms_of(source):
        row = out.get(storey_id)
        if row is None:
            row = CarpetRow(
                storey_id=storey_id, carpet_area_mm2=0, counted_rooms=0, excluded_rooms=0
            )
        if room_type in excluded:
            out[storey_id] = CarpetRow(
                storey_id=storey_id,
                carpet_area_mm2=row.carpet_area_mm2,
                counted_rooms=row.counted_rooms,
                excluded_rooms=row.excluded_rooms + 1,
            )
            continue
        out[storey_id] = CarpetRow(
            storey_id=storey_id,
            carpet_area_mm2=row.carpet_area_mm2 + int(area_mm2),
            counted_rooms=row.counted_rooms + 1,
            excluded_rooms=row.excluded_rooms,
        )
    return out


def _rooms_of(source: Any) -> tuple[tuple[str, str, int], ...]:
    """``(storey_id, room_type, area_mm2)`` for every room, from any supported source."""
    if isinstance(source, Mapping):
        model = source.get("model") if isinstance(source.get("model"), Mapping) else source
        house = model.get("house") if isinstance(model.get("house"), Mapping) else model
        return tuple(
            (str(r["storeyId"]), str(r["type"]), int(r["areaMm2"]))
            for r in (house.get("rooms") or ())
        )
    summary = getattr(source, "model", None)
    if summary is not None and hasattr(summary, "rooms") and not hasattr(summary, "walls"):
        # rules EvaluationContext: raw_type is the model's spelling, type the pack's.
        return tuple((r.storey_id, (r.raw_type or r.type), r.area_mm2) for r in summary.rooms)
    house = getattr(source, "house", source)
    rooms = getattr(house, "rooms", None)
    if rooms is None:
        raise TypeError(
            "Cannot read rooms from %r. Pass a garh_rules EvaluationContext, a "
            "garh_model ProjectDoc/HouseModel, or their JSON form." % type(source).__name__
        )
    return tuple((r.storey_id, r.type, r.area_mm2) for r in rooms)


# ---------------------------------------------------------------------------
# The sheet
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StoreyLine:
    """One floor's line: built-up (from the helper) and carpet (from the rooms)."""

    storey_id: str
    index: int
    label: str
    built_up_area_mm2: int | None
    carpet_area_mm2: int | None

    @property
    def efficiency(self) -> Fraction | None:
        """Carpet ÷ built-up as an exact rational — the "usable %" clients ask for."""
        if self.carpet_area_mm2 is None or not self.built_up_area_mm2:
            return None
        return Fraction(self.carpet_area_mm2, self.built_up_area_mm2)

    def to_json(self) -> dict[str, Any]:
        return {
            "storeyId": self.storey_id,
            "index": self.index,
            "label": self.label,
            "builtUpAreaMm2": self.built_up_area_mm2,
            "carpetAreaMm2": self.carpet_area_mm2,
        }


@dataclass(frozen=True)
class SetbackLine:
    """One plot edge: provided vs required. Both numbers come from the helper."""

    edge_index: int
    role: str
    provided_mm: int
    required_mm: int | None
    shortfall_mm: int
    status: str
    rule_ids: tuple[str, ...]

    @property
    def label(self) -> str:
        return self.role.replace("-", " ").title()

    def to_json(self) -> dict[str, Any]:
        return {
            "edgeIndex": self.edge_index,
            "role": self.role,
            "providedMm": self.provided_mm,
            "requiredMm": self.required_mm,
            "shortfallMm": self.shortfall_mm,
            "status": self.status,
            "ruleIds": list(self.rule_ids),
        }


@dataclass(frozen=True)
class AreaStatementSheet:
    """The municipal area statement, ready to print.

    ``statement`` is the ``garh_rules`` :class:`AreaStatement` this was rendered from —
    kept on the object so a caller can always get back to the source numbers (and so
    the "one source" test can compare them without a second evaluation).
    """

    statement: Any
    storeys: tuple[StoreyLine, ...]
    setbacks: tuple[SetbackLine, ...]
    warnings: tuple[str, ...] = ()

    # -- pass-through numbers (never recomputed) ---------------------------
    @property
    def plot_area_mm2(self) -> int:
        return self.statement.plot_area_mm2

    @property
    def total_built_up_area_mm2(self) -> int:
        return self.statement.total_built_up_area_mm2

    @property
    def total_carpet_area_mm2(self) -> int | None:
        values = [line.carpet_area_mm2 for line in self.storeys]
        if any(value is None for value in values):
            return None
        return sum(value for value in values if value is not None)

    @property
    def far_achieved(self) -> Fraction:
        return self.statement.far_achieved

    @property
    def far_allowed(self) -> Fraction | None:
        return self.statement.far_allowed

    @property
    def coverage_achieved(self) -> Fraction:
        return self.statement.coverage_achieved

    @property
    def coverage_allowed(self) -> Fraction | None:
        return self.statement.coverage_allowed

    # -- rows --------------------------------------------------------------
    def area_rows(self) -> tuple[AreaStatementRow, ...]:
        """The area-valued lines as the shared ``sheets.AreaStatementRow`` primitive.

        Only the mm² lines: setbacks (mm), floors and parking (counts) are not areas and
        do not belong in a row type whose field is ``area_mm2``. The full municipal
        table, including those, is :meth:`table`.
        """
        rows: list[AreaStatementRow] = [
            AreaStatementRow(
                label="Plot area", area_mm2=self.plot_area_mm2, note="As per document"
            ),
            AreaStatementRow(
                label="Ground coverage",
                area_mm2=self.statement.footprint_area_mm2,
                allowed_mm2=self.statement.coverage_allowed_mm2,
                note=self._coverage_note(),
            ),
        ]
        for line in self.storeys:
            rows.append(
                AreaStatementRow(
                    label="%s built-up" % line.label,
                    area_mm2=line.built_up_area_mm2 if line.built_up_area_mm2 is not None else 0,
                    note="" if line.built_up_area_mm2 is not None else "not reported by the model",
                )
            )
        rows.append(AreaStatementRow(label="Total built-up", area_mm2=self.total_built_up_area_mm2))
        for line in self.storeys:
            if line.carpet_area_mm2 is None:
                continue
            rows.append(
                AreaStatementRow(
                    label="%s carpet" % line.label,
                    area_mm2=line.carpet_area_mm2,
                    note=(
                        "%s of built-up" % percent_cell(line.efficiency)
                        if line.efficiency is not None
                        else ""
                    ),
                )
            )
        total_carpet = self.total_carpet_area_mm2
        if total_carpet is not None:
            rows.append(AreaStatementRow(label="Total carpet", area_mm2=total_carpet))
        rows.append(
            AreaStatementRow(
                label="FAR-countable area",
                area_mm2=self.statement.far_countable_area_mm2,
                allowed_mm2=self.statement.far_allowed_mm2,
                note=self._far_note(),
            )
        )
        return tuple(rows)

    def _far_note(self) -> str:
        allowed = self.far_allowed
        if allowed is None:
            return "FAR %s achieved; no FAR rule applied" % ratio_cell(self.far_achieved)
        return "FAR %s achieved against %s permissible" % (
            ratio_cell(self.far_achieved),
            ratio_cell(allowed),
        )

    def _coverage_note(self) -> str:
        allowed = self.coverage_allowed
        if allowed is None:
            return "%s achieved; no coverage rule applied" % percent_cell(self.coverage_achieved)
        return "%s achieved against %s permissible" % (
            percent_cell(self.coverage_achieved),
            percent_cell(allowed),
        )

    # -- tables ------------------------------------------------------------
    def table(
        self,
        *,
        title: str = "AREA STATEMENT",
        style: TableStyle | None = None,
        origin_mm: tuple[int, int] = (0, 0),
    ) -> Table:
        """The municipal statement: description · provided · permissible/required."""
        columns = (
            Column("item", "DESCRIPTION", "left"),
            Column("value", "PROVIDED", "right"),
            Column("limit", "PERMISSIBLE / REQUIRED", "right"),
            Column("note", "REMARKS", "left"),
        )
        rows: list[tuple[str, str, str, str]] = [
            ("Plot area", plot_area_cell(self.plot_area_mm2), DASH, "As per document"),
            (
                "Ground coverage",
                area_cell(self.statement.footprint_area_mm2),
                area_cell(self.statement.coverage_allowed_mm2),
                self._coverage_note(),
            ),
        ]
        rule_after = [len(rows) - 1]
        for line in self.storeys:
            rows.append(
                (
                    "%s built-up" % line.label,
                    area_cell(line.built_up_area_mm2),
                    DASH,
                    "",
                )
            )
        rows.append(("Total built-up", area_cell(self.total_built_up_area_mm2), DASH, ""))
        bold = [len(rows) - 1]
        rule_after.append(len(rows) - 1)
        for line in self.storeys:
            rows.append(
                (
                    "%s carpet" % line.label,
                    area_cell(line.carpet_area_mm2),
                    DASH,
                    "%s of built-up" % percent_cell(line.efficiency)
                    if line.efficiency is not None
                    else "",
                )
            )
        total_carpet = self.total_carpet_area_mm2
        rows.append(("Total carpet", area_cell(total_carpet), DASH, ""))
        bold.append(len(rows) - 1)
        rule_after.append(len(rows) - 1)
        rows.append(
            (
                "FAR-countable area",
                area_cell(self.statement.far_countable_area_mm2),
                area_cell(self.statement.far_allowed_mm2),
                self._far_note(),
            )
        )
        bold.append(len(rows) - 1)
        rows.append(
            (
                "Floor area ratio (FAR)",
                ratio_cell(self.far_achieved),
                ratio_cell(self.far_allowed),
                self._rule_note("far"),
            )
        )
        rule_after.append(len(rows) - 1)
        for line in self.setbacks:
            rows.append(
                (
                    "%s setback (mm)" % line.label,
                    mm_cell(line.provided_mm),
                    mm_cell(line.required_mm),
                    self._setback_note(line),
                )
            )
        rule_after.append(len(rows) - 1)
        rows.append(
            (
                "Floors above ground",
                count_cell(
                    self.statement.floors_counted
                    if self.statement.floors_counted is not None
                    else self.statement.storey_count
                ),
                count_cell(self.statement.floors_allowed),
                self._rule_note("floors"),
            )
        )
        rows.append(
            (
                "Building height (mm)",
                mm_cell(
                    self.statement.height_counted_mm
                    if self.statement.height_counted_mm is not None
                    else self.statement.building_height_mm
                ),
                mm_cell(self.statement.height_allowed_mm),
                self._rule_note("height"),
            )
        )
        rows.append(
            (
                "Car parking spaces",
                count_cell(self.statement.parking_provided),
                count_cell(self.statement.parking_required),
                self._rule_note("parking"),
            )
        )
        return Table(
            title=title,
            columns=columns,
            rows=tuple(rows),
            style=style or TableStyle(),
            origin_mm=origin_mm,
            rule_after=tuple(sorted(set(rule_after))),
            bold_rows=tuple(sorted(set(bold))),
            footnotes=self.footnotes(),
        )

    def setback_table(
        self,
        *,
        title: str = "SETBACKS (mm)",
        style: TableStyle | None = None,
        origin_mm: tuple[int, int] = (0, 0),
    ) -> Table:
        """The setback table on its own — the site plan prints it next to the plot."""
        columns = (
            Column("edge", "EDGE", "left"),
            Column("provided", "PROVIDED", "right"),
            Column("required", "REQUIRED", "right"),
            Column("status", "STATUS", "left"),
            Column("rules", "RULE", "left"),
        )
        rows = tuple(
            (
                line.label,
                mm_cell(line.provided_mm),
                mm_cell(line.required_mm),
                _setback_status_text(line),
                ", ".join(line.rule_ids) if line.rule_ids else DASH,
            )
            for line in self.setbacks
        )
        return Table(
            title=title,
            columns=columns,
            rows=rows,
            style=style or TableStyle(),
            origin_mm=origin_mm,
        )

    def _setback_note(self, line: SetbackLine) -> str:
        if line.required_mm is None:
            return "no setback rule applied"
        if line.status == "short":
            return "short by %d mm — %s" % (line.shortfall_mm, ", ".join(line.rule_ids) or "-")
        return ", ".join(line.rule_ids)

    def _rule_note(self, key: str) -> str:
        ids = self.statement.rule_ids.get(key, ())
        return ", ".join(ids)

    def footnotes(self) -> tuple[str, ...]:
        notes = [
            "Areas: m2 · sq ft (1 decimal). Lengths in mm. Plot area also in gaj "
            "(1 gaj = 1 sq yd = 9 sq ft).",
            "FAR, coverage and setback figures are the compliance engine's own results "
            "(one source, §7) — this sheet does not recompute them.",
            "Carpet area = enclosed room floor areas; balcony, terrace, porch, stilt, "
            "shaft, duct and void excluded.",
        ]
        notes.extend(self.warnings)
        return tuple(notes)

    def to_json(self) -> dict[str, Any]:
        return {
            "statement": self.statement.to_json(),
            "storeys": [line.to_json() for line in self.storeys],
            "setbacks": [line.to_json() for line in self.setbacks],
            "totalCarpetAreaMm2": self.total_carpet_area_mm2,
            "areaRows": [row.to_json() for row in self.area_rows()],
            "warnings": list(self.warnings),
        }


def _setback_status_text(line: SetbackLine) -> str:
    if line.required_mm is None:
        return "not regulated"
    if line.status == "short":
        return "SHORT %d" % line.shortfall_mm
    return "OK"


# ---------------------------------------------------------------------------
# Building it
# ---------------------------------------------------------------------------
def build_area_statement_sheet(
    source: Any,
    *,
    statement: Any = None,
    packs: Any = None,
    rulepack_root: str | None = None,
    carpet_source: Any = None,
    exclude_types: Sequence[str] = CARPET_EXCLUDED_ROOM_TYPES,
) -> AreaStatementSheet:
    """Render the area statement for ``source``.

    ``source`` is a ``garh_rules`` ``EvaluationContext`` (or its JSON form).

    ``statement`` lets a caller that has already evaluated pass ``report.areas`` in, so
    a sheet job that just ran the compliance check does not run it twice. When it is
    ``None`` this calls :func:`garh_rules.areas.area_statement`, which runs the same
    evaluation — either way the numbers come from the engine, never from here.

    ``carpet_source`` overrides where carpet area is read from; pass a folded
    ``ProjectDoc`` when you have one (its rooms are the same rooms, and the model core's
    ``storey_carpet_area_mm2`` is the same sum). Default: ``source``.
    """
    _ensure_apps_api_on_path()

    if statement is None:
        from garh_rules.areas import area_statement as _area_statement
        from garh_rules.errors import ContextError

        try:
            statement = _area_statement(source, packs=packs, root=rulepack_root)
        except ContextError as error:
            # The commonest mistake here is handing this a folded ProjectDoc. It cannot
            # work: the regulatory numbers come from an evaluation, and an evaluation
            # needs the plot, the profile and a derived projection — not just a house.
            # Say so, and say what to pass instead (§9: errors say what to do next).
            raise ContextError(
                "The area statement needs a garh_rules EvaluationContext (plot + profile "
                "+ derived model), not a bare model: %s. Build the context with "
                "garh_rules.context_from_parts(...) — or pass an already-evaluated "
                "statement as statement=report.areas." % error,
                field=getattr(error, "field", None),
            ) from error

    warnings: list[str] = list(getattr(statement, "warnings", ()) or ())

    carpet = carpet_by_storey(
        carpet_source if carpet_source is not None else source, exclude_types=exclude_types
    )
    storey_refs = {ref.id: ref for ref in _storey_refs(source)}

    lines: list[StoreyLine] = []
    for row in statement.per_storey:
        ref = storey_refs.get(row.storey_id)
        carpet_row = carpet.get(row.storey_id)
        if carpet_row is None:
            warnings.append(
                "%s has no rooms in the projection, so its carpet area is not stated." % row.label
            )
        built_up = row.built_up_area_mm2
        carpet_mm2 = carpet_row.carpet_area_mm2 if carpet_row is not None else None
        if built_up is not None and carpet_mm2 is not None and carpet_mm2 > built_up:
            # Physically impossible: carpet is a subset of built-up. Say so on the
            # sheet rather than printing an efficiency above 100%.
            warnings.append(
                "%s carpet area (%d mm2) exceeds its built-up area (%d mm2) — the room "
                "polygons and the floor slab disagree; check the storey's projection."
                % (row.label, carpet_mm2, built_up)
            )
        lines.append(
            StoreyLine(
                storey_id=row.storey_id,
                index=row.index,
                label=storey_row_label(ref.name) if ref is not None else row.label,
                built_up_area_mm2=built_up,
                carpet_area_mm2=carpet_mm2,
            )
        )

    setbacks = tuple(
        SetbackLine(
            edge_index=row.edge_index,
            role=row.role,
            provided_mm=row.provided_mm,
            required_mm=row.required_mm,
            shortfall_mm=row.shortfall_mm,
            status=row.status,
            rule_ids=tuple(row.rule_ids),
        )
        for row in statement.setbacks
    )

    return AreaStatementSheet(
        statement=statement,
        storeys=tuple(lines),
        setbacks=setbacks,
        warnings=tuple(warnings),
    )


def _storey_refs(source: Any) -> tuple[StoreyRef, ...]:
    """Storey names for the row labels; falls back to the helper's own labels."""
    try:
        return normalise_storeys(source)
    except TypeError:
        return ()
