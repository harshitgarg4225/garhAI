"""Build the default municipal sheet set from a model. §7 / F7-A. **Real.**

    **MVP cut lines (binding):** municipal drawing set only (site plan, floor plans,
    4 elevations, 1 section, door/window schedule, area statement).

:data:`~services.drawings.sheets.model.DEFAULT_SHEET_PLAN` lists the six *kinds*; a real
project needs more sheets than kinds — one floor plan per storey and four elevations — so
this module expands the plan against the actual model and numbers the result.

SHEET IDS ARE DERIVED, NOT MINTED
---------------------------------
Every id here is a deterministic slug (``floor-plan-<storeyId>``, ``elevation-N``). That
is a §7 requirement in disguise: ``Annotation.sheet_id`` points at a sheet, and §7's
regeneration contract says annotations survive edits. If regenerating the set minted fresh
ULIDs, every annotation on every sheet would be orphaned by a regeneration that changed
nothing — the Review Tray would fill up with notes that had nowhere to go. Deterministic
ids make regeneration idempotent, so an annotation only orphans when its *element* really
disappears.

The section is cut through the stair (§7: "section line auto-chosen through stair flight"),
along the flight rather than across it, because a section across a flight shows one tread
and tells a reviewer nothing about the rise.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, List, Optional, Sequence, Tuple

from services.drawings.sheets.frame import sheet_title_block
from services.drawings.sheets.model import (
    DEFAULT_PAPER,
    DEFAULT_SCALE,
    Scale,
    Sheet,
    SheetKind,
    TitleBlock,
    Viewport,
    default_frame,
)

#: The four elevations, in the order a municipal set presents them.
ELEVATION_ORDER: Tuple[str, ...] = ("N", "E", "S", "W")

ELEVATION_NAMES = {
    "N": "North Elevation",
    "E": "East Elevation",
    "S": "South Elevation",
    "W": "West Elevation",
}

#: How far past the building the section cut line runs, in model mm.
SECTION_OVERRUN_MM = 1_500

#: Site plans show the plot, so they are drawn smaller than the floor plans.
SITE_PLAN_SCALE = Scale(200)


def build_sheet_set(
    house: Any,
    *,
    title_block: Optional[TitleBlock] = None,
    paper: str = DEFAULT_PAPER,
    scale: Scale = DEFAULT_SCALE,
    include: Optional[Sequence[SheetKind]] = None,
) -> Tuple[Sheet, ...]:
    """The six §7 sheet kinds, expanded over this model's storeys and elevations.

    ``include`` narrows the set to the kinds a job asked for (the drawings worker's
    ``payload.kinds``), while keeping the numbering of the sheets that *are* produced
    stable — a set regenerated with only the floor plans keeps the numbers those plans
    had in the full set, so a client comparing two PDFs is not renumbering in their head.
    """
    wanted = tuple(include) if include is not None else None
    base_frame = default_frame(paper, title_block=title_block or TitleBlock())
    storeys = list(house.storeys)

    entries: List[Tuple[SheetKind, str, str, Viewport, Scale]] = []
    entries.append(
        ("site-plan", "site-plan", "Site Plan", _site_viewport(storeys), SITE_PLAN_SCALE)
    )
    for storey in storeys:
        entries.append(
            (
                "floor-plan",
                "floor-plan-%s" % storey.id,
                "%s Plan" % storey.name,
                Viewport(storey_id=storey.id),
                scale,
            )
        )
    for direction in ELEVATION_ORDER:
        entries.append(
            (
                "elevation",
                "elevation-%s" % direction,
                ELEVATION_NAMES[direction],
                Viewport(elevation_direction=direction),
                scale,
            )
        )
    section_line = section_line_through_stair(house)
    if section_line is not None:
        entries.append(
            (
                "section",
                "section-A",
                "Section A-A",
                Viewport(section_line=section_line),
                scale,
            )
        )
    entries.append(
        (
            "door-window-schedule",
            "door-window-schedule",
            "Door & Window Schedule",
            Viewport(),
            scale,
        )
    )
    entries.append(("area-statement", "area-statement", "Area Statement", Viewport(), scale))

    sheets: List[Sheet] = []
    for index, (kind, slug, title, viewport, sheet_scale) in enumerate(entries, start=1):
        number = "A-%02d" % index
        if wanted is not None and kind not in wanted:
            continue
        frame = sheet_title_block(
            base_frame,
            drawing_title=title,
            sheet_number=number,
            scale_label=sheet_scale.label,
        )
        sheet = Sheet(
            id=slug,
            kind=kind,
            number=number,
            title=title,
            viewport=viewport,
            scale=sheet_scale,
            frame=frame,
        )
        sheet.validate()
        sheets.append(sheet)
    return tuple(sheets)


def _site_viewport(storeys: Sequence[Any]) -> Viewport:
    """A site plan is a plan, so it names the ground storey (§7 viewport = one selector).

    With no storeys at all — a project whose plot is drawn but nothing is built yet — the
    viewport would have no selector and ``validate`` would reject the sheet, so the site
    plan is omitted in that case by :func:`build_sheet_set`'s caller. Here it simply
    reports the ground storey when there is one.
    """
    if not storeys:
        return Viewport(storey_id="")
    return Viewport(storey_id=storeys[0].id)


def building_extent_mm(house: Any) -> Optional[Tuple[int, int, int, int]]:
    """Bounding box of every wall centreline in the building, across all storeys."""
    xs: List[int] = []
    ys: List[int] = []
    for wall in house.walls:
        xs.extend((wall.a.x, wall.b.x))
        ys.extend((wall.a.y, wall.b.y))
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def section_line_through_stair(
    house: Any,
) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """§7's auto-chosen section line: along the first stair's flight, through its centre.

    Returns the cut line in model mm, running past the building by
    :data:`SECTION_OVERRUN_MM` at both ends so the section shows the full envelope
    including the plinth and parapet.

    With no stair — a single-storey house — the cut falls back to the middle of the
    building running north, which is a defensible default a firm can drag. Returning
    ``None`` and omitting the section sheet would be worse: a submission set without a
    section is incomplete, and an architect would rather move a line than add a sheet.
    """
    from services.drawings.projection.symbols import STAIR_VECTORS

    extent = building_extent_mm(house)
    if extent is None:
        return None
    min_x, min_y, max_x, max_y = extent

    stairs = list(house.stairs)
    if not stairs:
        mid_x = (min_x + max_x) // 2
        return ((mid_x, min_y - SECTION_OVERRUN_MM), (mid_x, max_y + SECTION_OVERRUN_MM))

    stair = stairs[0]
    fx, fy, rx, ry = STAIR_VECTORS[stair.direction]
    half_width = stair.width_mm // 2
    # The cut runs along the flight (forward axis), offset across to the flight's centre.
    centre_x = stair.origin.x + rx * half_width
    centre_y = stair.origin.y + ry * half_width
    if fx != 0:
        return (
            (min_x - SECTION_OVERRUN_MM, centre_y),
            (max_x + SECTION_OVERRUN_MM, centre_y),
        )
    return (
        (centre_x, min_y - SECTION_OVERRUN_MM),
        (centre_x, max_y + SECTION_OVERRUN_MM),
    )


def section_markers_for(sheets: Sequence[Sheet]) -> Tuple[Any, ...]:
    """The section markers a floor plan should show, taken from the section sheets.

    The marker on the plan and the sheet it points to read the same
    ``Viewport.section_line``, so "Section A-A" cannot be cut somewhere other than where
    the plan says it is.
    """
    from services.drawings.projection.symbols import SectionMarker

    markers: List[Any] = []
    letters = "ABCDEFGH"
    index = 0
    for sheet in sheets:
        if sheet.kind != "section" or sheet.viewport.section_line is None:
            continue
        start, end = sheet.viewport.section_line
        markers.append(
            SectionMarker(
                a=(int(start[0]), int(start[1])),
                b=(int(end[0]), int(end[1])),
                label=letters[index % len(letters)],
            )
        )
        index += 1
    return tuple(markers)


def plan_options_for(
    sheets: Sequence[Sheet],
    *,
    north_deg: int = 0,
    show_column_grid: bool = True,
) -> Any:
    """:class:`~services.drawings.projection.plan.PlanOptions` for this set's floor plans.

    One place assembles the plan's options from the set and the plot, so every floor plan
    in a set carries the same north and the same section markers.
    """
    from services.drawings.projection.plan import PlanOptions

    return PlanOptions(
        north_deg=north_deg,
        section_markers=section_markers_for(sheets),
        show_column_grid=show_column_grid,
    )


def with_annotations(sheet: Sheet, annotations: Sequence[Any]) -> Sheet:
    """Attach the annotations whose ``sheet_id`` matches this sheet.

    Annotations live in the project document (``ProjectDoc.annotations``, mutated by op
    32) rather than inside the sheet, because they must survive a sheet-set regeneration.
    This is the join, and it is a filter rather than a merge: an annotation for a sheet
    that no longer exists stays where it is, for the Review Tray to deal with.
    """
    matching = tuple(item for item in annotations if getattr(item, "sheet_id", None) == sheet.id)
    return replace(sheet, annotations=matching)


__all__ = [
    "ELEVATION_NAMES",
    "ELEVATION_ORDER",
    "SECTION_OVERRUN_MM",
    "SITE_PLAN_SCALE",
    "building_extent_mm",
    "build_sheet_set",
    "plan_options_for",
    "section_line_through_stair",
    "section_markers_for",
    "with_annotations",
]
