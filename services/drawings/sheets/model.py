"""The §7 sheet model — the data half of ``services.drawings.sheets``. **Real.**

    Sheet { kind, scale (1:100 default), frame A2 landscape default, viewport
    (storeyId | elevation dir | section line), annotations[] }

Everything here is data: paper sizes, scales, frames, viewports, the six MVP sheets and
the schedule/area-statement shapes. This is what the API persists in the ``sheets``
table. The geometry that goes *on* a sheet lives in the sibling modules — ``transform``
(the model→paper bridge), ``frame`` (border and title block), ``compose`` (the two
joined) and ``builder`` (the default six-sheet set).

Two units live in this file and they must not be confused, so they are named apart
everywhere: **model millimetres** (the building) and **paper millimetres** (the sheet).
``sheets.transform`` is the only place a *coordinate* crosses between them;
:meth:`Scale.to_paper_mm` here converts a bare *length*.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

#: Every sheet kind the engine can draw: the six municipal sheets of the MVP cut
#: line (spec F8) plus the working drawings issued to site. Which is which is
#: SUBMISSION_SHEET_KINDS / WORKING_SHEET_KINDS below — never a count.
SheetKind = Literal[
    "site-plan",
    "floor-plan",
    "setting-out",
    "elevation",
    "section",
    "door-window-schedule",
    "area-statement",
    "structural-grid",
]

SHEET_KINDS: tuple[SheetKind, ...] = (
    "site-plan",
    "floor-plan",
    "setting-out",
    "elevation",
    "section",
    "door-window-schedule",
    "area-statement",
    "structural-grid",
)

#: Working drawings: issued to SITE, during construction, and numbered in their own
#: W-series. They are sheet kinds like any other — same frame, same layers, same
#: exporters — but they are not part of the municipal submission, so anything that
#: means "the set an architect files" must use :data:`SUBMISSION_SHEET_KINDS`.
WORKING_SHEET_KINDS: tuple[SheetKind, ...] = ("setting-out", "structural-grid")

#: The §7 submission set. Derived, never restated: a new kind added above lands in
#: exactly one of these two tuples and cannot end up in neither or both.
SUBMISSION_SHEET_KINDS: tuple[SheetKind, ...] = tuple(
    kind for kind in SHEET_KINDS if kind not in WORKING_SHEET_KINDS
)

Direction4 = Literal["N", "E", "S", "W"]


@dataclass(frozen=True)
class PaperSize:
    """A sheet of paper, in **paper millimetres**."""

    name: str
    width_mm: int
    height_mm: int

    def landscape(self) -> PaperSize:
        if self.width_mm >= self.height_mm:
            return self
        return PaperSize(self.name, self.height_mm, self.width_mm)

    def portrait(self) -> PaperSize:
        if self.height_mm >= self.width_mm:
            return self
        return PaperSize(self.name, self.height_mm, self.width_mm)


#: ISO A sizes. A2 landscape is the §7 default for a municipal set.
PAPER_SIZES: Mapping[str, PaperSize] = {
    "A0": PaperSize("A0", 1189, 841),
    "A1": PaperSize("A1", 841, 594),
    "A2": PaperSize("A2", 594, 420),
    "A3": PaperSize("A3", 420, 297),
    "A4": PaperSize("A4", 297, 210),
}
DEFAULT_PAPER = "A2"


@dataclass(frozen=True)
class Scale:
    """A drawing scale as an exact integer ratio, 1:``denominator``.

    Integer-only on purpose. "1:100" is exact; storing 0.01 would reintroduce the
    floating-point drift that integer millimetres exist to avoid, and print-true scale
    is the whole point of a submission drawing.
    """

    denominator: int

    def __post_init__(self) -> None:
        if self.denominator <= 0:
            raise ValueError("scale denominator must be positive, got %d" % self.denominator)

    @property
    def label(self) -> str:
        return "1:%d" % self.denominator

    def to_paper_mm(self, model_mm: int) -> int:
        """Model millimetres → paper millimetres, rounded half away from zero.

        Whole paper millimetres, so this is for human-scale questions ("does a 12m
        building fit across 574mm of paper?") and never for placing geometry: 1mm of
        paper is 100mm of building at 1:100, and quantising coordinates to that would
        move a wall by half a brick. Geometry goes through
        ``services.drawings.sheets.transform``, which works in paper micrometres for
        exactly this reason.

        The rounding rule is the model core's, taken from the projection package rather
        than from ``services.solver`` (which is where this used to import it): a sheet
        should not have to load the CP-SAT solver's package — and on a machine without
        ``structlog`` installed, importing it fails outright.
        """
        from services.drawings.projection.primitives import round_half_away

        return round_half_away(model_mm / self.denominator)

    def to_model_mm(self, paper_mm: int) -> int:
        """Paper millimetres → model millimetres. Exact (multiplication)."""
        return paper_mm * self.denominator


SCALE_1_100 = Scale(100)
SCALE_1_50 = Scale(50)
SCALE_1_200 = Scale(200)
DEFAULT_SCALE = SCALE_1_100


@dataclass(frozen=True)
class TitleBlock:
    """Title block content. Editable per firm (§7 "title block editor")."""

    firm_name: str = ""
    project_name: str = ""
    client_name: str = ""
    drawing_title: str = ""
    sheet_number: str = ""
    revision: str = "A"
    #: DD-MM-YYYY — §15's Indian date format, formatted by the caller.
    date: str = ""
    scale_label: str = DEFAULT_SCALE.label
    drawn_by: str = ""
    checked_by: str = ""
    notes: str = ""
    logo_url: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "firmName": self.firm_name,
            "projectName": self.project_name,
            "clientName": self.client_name,
            "drawingTitle": self.drawing_title,
            "sheetNumber": self.sheet_number,
            "revision": self.revision,
            "date": self.date,
            "scaleLabel": self.scale_label,
            "drawnBy": self.drawn_by,
            "checkedBy": self.checked_by,
            "notes": self.notes,
            "logoUrl": self.logo_url,
        }


@dataclass(frozen=True)
class Frame:
    """The sheet border and title block position, in **paper millimetres**."""

    paper: PaperSize = field(default_factory=lambda: PAPER_SIZES[DEFAULT_PAPER].landscape())
    #: Margins. The left one is wider by convention, for binding.
    margin_left_mm: int = 20
    margin_right_mm: int = 10
    margin_top_mm: int = 10
    margin_bottom_mm: int = 10
    title_block_width_mm: int = 180
    title_block_height_mm: int = 60
    title_block: TitleBlock = field(default_factory=TitleBlock)

    def drawable_width_mm(self) -> int:
        return self.paper.width_mm - self.margin_left_mm - self.margin_right_mm

    def drawable_height_mm(self) -> int:
        return self.paper.height_mm - self.margin_top_mm - self.margin_bottom_mm

    def title_block_origin_mm(self) -> tuple[int, int]:
        """Bottom-left of the title block: bottom-right of the drawable area."""
        return (
            self.paper.width_mm - self.margin_right_mm - self.title_block_width_mm,
            self.margin_bottom_mm,
        )


@dataclass(frozen=True)
class Viewport:
    """What a sheet looks at. Exactly one of the three selectors is set.

    A floor plan names a storey, an elevation names a direction, a section names a cut
    line. :meth:`validate` enforces the "exactly one" rule so an ambiguous sheet cannot
    reach the renderer.
    """

    storey_id: str | None = None
    elevation_direction: Direction4 | None = None
    #: Section cut line in model mm: ``((x1, y1), (x2, y2))``.
    section_line: tuple[tuple[int, int], tuple[int, int]] | None = None
    #: Paper-mm offset of the drawing origin within the drawable area.
    offset_mm: tuple[int, int] = (0, 0)

    def validate(self) -> None:
        selectors = [
            self.storey_id is not None,
            self.elevation_direction is not None,
            self.section_line is not None,
        ]
        if sum(selectors) != 1:
            raise ValueError(
                "A viewport must name exactly one of storey_id, elevation_direction or "
                "section_line — got %d." % sum(selectors)
            )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"offsetMm": list(self.offset_mm)}
        if self.storey_id:
            out["storeyId"] = self.storey_id
        if self.elevation_direction:
            out["elevationDirection"] = self.elevation_direction
        if self.section_line:
            out["sectionLine"] = [list(point) for point in self.section_line]
        return out


@dataclass(frozen=True)
class SheetAnnotation:
    """A user or generated annotation, anchored to a model element (§7).

    ``anchor_element_id`` is what makes annotations survive edits: it follows the
    element, and a solver re-run that does not preserve the id marks the annotation
    ``orphaned`` for the Review Tray rather than silently relocating it.
    """

    id: str
    kind: str
    text: str
    #: Position in model mm.
    position_mm: tuple[int, int]
    anchor_element_id: str | None = None
    anchor_kind: str | None = None
    layer: str = "A-TEXT"
    orphaned: bool = False
    leader_to_mm: tuple[int, int] | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "positionMm": list(self.position_mm),
            "layer": self.layer,
            "orphaned": self.orphaned,
        }
        if self.anchor_element_id:
            out["anchorElementId"] = self.anchor_element_id
        if self.anchor_kind:
            out["anchorKind"] = self.anchor_kind
        if self.leader_to_mm:
            out["leaderToMm"] = list(self.leader_to_mm)
        return out


@dataclass(frozen=True)
class Sheet:
    """One drawing sheet — the §7 sheet model."""

    id: str
    kind: SheetKind
    number: str
    title: str
    viewport: Viewport
    scale: Scale = DEFAULT_SCALE
    frame: Frame = field(default_factory=Frame)
    annotations: tuple[SheetAnnotation, ...] = ()

    def validate(self) -> None:
        if self.kind not in SHEET_KINDS:
            raise ValueError(
                "%r is not a sheet kind. Expected one of: %s." % (self.kind, ", ".join(SHEET_KINDS))
            )
        # Schedules and area statements are tables, not projections — they legitimately
        # have no viewport selector, so the "exactly one" rule does not apply to them.
        if self.kind not in ("door-window-schedule", "area-statement"):
            self.viewport.validate()

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "number": self.number,
            "title": self.title,
            "scale": self.scale.denominator,
            "scaleLabel": self.scale.label,
            "paper": self.frame.paper.name,
            "viewport": self.viewport.to_json(),
            "annotations": [item.to_json() for item in self.annotations],
        }


# ---------------------------------------------------------------------------
# Schedules & area statement (§7) — shapes real, generators Phase 8
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScheduleRow:
    """One row of the door/window schedule: a (kind, w, h) group with its tag."""

    tag: str
    kind: str
    width_mm: int
    height_mm: int
    sill_mm: int
    #: Count per storey, keyed by storey id, plus a total.
    counts_by_storey: Mapping[str, int] = field(default_factory=dict)
    total: int = 0
    notes: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "kind": self.kind,
            "widthMm": self.width_mm,
            "heightMm": self.height_mm,
            "sillMm": self.sill_mm,
            "countsByStorey": dict(self.counts_by_storey),
            "total": self.total,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class AreaStatementRow:
    """One line of the municipal area statement."""

    label: str
    #: Areas stay integer mm²; the renderer formats to m²/sqft at the boundary.
    area_mm2: int
    #: Present for the achieved-vs-allowed rows (FAR, coverage).
    allowed_mm2: int | None = None
    note: str = ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"label": self.label, "areaMm2": self.area_mm2, "note": self.note}
        if self.allowed_mm2 is not None:
            out["allowedMm2"] = self.allowed_mm2
        return out


#: §7's six MVP sheets, in submission order. The generator walks this.
DEFAULT_SHEET_PLAN: tuple[tuple[SheetKind, str, str], ...] = (
    ("site-plan", "A-01", "Site Plan"),
    ("floor-plan", "A-02", "Floor Plans"),
    ("elevation", "A-03", "Elevations"),
    ("section", "A-04", "Section"),
    ("door-window-schedule", "A-05", "Door & Window Schedule"),
    ("area-statement", "A-06", "Area Statement"),
)


def default_frame(paper: str = DEFAULT_PAPER, *, title_block: TitleBlock | None = None) -> Frame:
    """An A2-landscape frame with the standard margins (§7 default)."""
    size = PAPER_SIZES.get(paper)
    if size is None:
        raise ValueError(
            "%r is not a known paper size. Expected one of: %s."
            % (paper, ", ".join(sorted(PAPER_SIZES)))
        )
    return Frame(paper=size.landscape(), title_block=title_block or TitleBlock())


def sheet_numbers(
    plan: Sequence[tuple[SheetKind, str, str]] = DEFAULT_SHEET_PLAN,
) -> tuple[str, ...]:
    return tuple(number for _kind, number, _title in plan)


__all__ = [
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
]
