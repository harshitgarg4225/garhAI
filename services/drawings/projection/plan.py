"""The plan projector: ``(HouseModel, storey_id, scale) → primitives``. **Real.**

This is the §7 sentence, implemented:

    **Plan projection:** walls as double lines w/ thickness (fill hatch external),
    openings break walls (door arc + leaf, window triple line), stairs w/ arrow +
    ``UP 15R``, room label block (name, area in sqft one decimal), FFL markers, section
    markers, north arrow, grid of column bubbles if columns exist.

PURE, AND DELIBERATELY CHEAP TO RUN
-----------------------------------
No I/O, no ezdxf, no database, no rules engine, no ``services.common``. The only import
outside this package is the model core, and it is imported lazily inside the functions
that need it (the convention ``services/solver`` follows, because ``apps/api`` is on the
path in deployment rather than a package dependency). A projection of a G+2 house is a
few thousand integer operations, which means the whole plan pipeline is testable on a
bare interpreter and a golden diff costs milliseconds — the property that lets §16's
"10 plan fixtures → goldens" run on every commit instead of nightly.

WHAT IT RETURNS
---------------
:func:`project_plan` returns the primitive stream, which is the documented §7 contract.
:func:`project_plan_detail` returns that plus the resolved :class:`WallBand` s, the extent
and the :class:`Style`. The auto-dimensioning engine (§7 steps 1–4) wants exactly those
bands: they already carry each wall's mitred face extents and each opening's span along
its host wall, and a dimension chain derived from a *second*, independently computed set
of wall geometry is a dimension chain that can disagree with the drawing it annotates.

DRAW ORDER
----------
The stream is emitted back-to-front — hatches, then line work, then text — so a renderer
can paint it in list order and get poché behind walls and labels on top of everything.
:data:`PAINT_ORDER` is the documented key; the sort is stable, so within a band the order
stays the order the projectors produced.

NOT ON A MUNICIPAL PLAN
-----------------------
Furniture is not projected. It belongs to the editor and to interior renders; a
submission floor plan shows the building, and a sofa on it is noise a reviewer has to
look past. Dimension chains are not projected here either — they are the auto-dim
engine's output (``services.drawings.dimensions``), added to the same stream by the sheet
composer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from services.drawings.projection.primitives import (
    Arc,
    Hatch,
    Line,
    Point,
    Polyline,
    Primitive,
    Text,
    bbox_of,
    point,
)
from services.drawings.projection.style import Style, style_of
from services.drawings.projection.symbols import (
    SectionMarker,
    balcony_symbol,
    column_bubbles,
    column_symbols,
    level_marker,
    north_arrow,
    opening_tag,
    room_label,
    stair_symbol,
)
from services.drawings.projection.walls import (
    WallBand,
    opening_primitives,
    opening_span,
    wall_bands,
    wall_primitives,
)

#: Back-to-front paint order by primitive type. Hatch behind, text in front.
PAINT_ORDER: dict[type, int] = {Hatch: 0, Line: 1, Polyline: 1, Arc: 1, Text: 2}

#: §7: "openings dimensioned to centreline (config flag ``dimToJamb`` for firm
#: preference)". Mirrors ``services.drawings.dimensions.DEFAULT_DIM_TO_JAMB``; the test
#: asserts the two agree, so the flag cannot end up meaning different things in the
#: projector and in the dimension engine.
DEFAULT_DIM_TO_JAMB = False


@dataclass(frozen=True)
class PlanOptions:
    """Everything about a plan that is a choice rather than a fact of the model."""

    #: ``PlotDoc.north_deg`` — integer degrees of true north from +Y, clockwise.
    north_deg: int = 0
    show_north: bool = True
    #: Where the north dart goes, in model mm. None = just outside the plan's top-right.
    north_position: Point | None = None
    show_room_labels: bool = True
    show_ffl_marker: bool = True
    show_opening_tags: bool = True
    #: Column rectangles are always drawn when columns exist; the numbered/lettered
    #: grid is what this switches off (a plan with two columns does not need a grid).
    show_column_grid: bool = True
    section_markers: tuple[SectionMarker, ...] = ()
    #: §7 firm preference. Consumed by the dimension engine via
    #: :func:`opening_dim_stations`; the projection itself does not dimension anything.
    dim_to_jamb: bool = DEFAULT_DIM_TO_JAMB


@dataclass(frozen=True)
class PlanProjection:
    """A projected plan and the working geometry that produced it."""

    storey_id: str
    style: Style
    primitives: tuple[Primitive, ...]
    bands: tuple[WallBand, ...]
    #: ``(min_x, min_y, max_x, max_y)`` of the built fabric, before outside symbols.
    extent: tuple[int, int, int, int] | None = None
    options: PlanOptions = field(default_factory=PlanOptions)


def project_plan(
    house: Any,
    storey_id: str,
    scale: Any = 100,
    *,
    options: PlanOptions | None = None,
) -> tuple[Primitive, ...]:
    """Project one storey to primitives. The §7 signature, and the one renderers call."""
    return project_plan_detail(house, storey_id, scale, options=options).primitives


def project_plan_detail(
    house: Any,
    storey_id: str,
    scale: Any = 100,
    *,
    options: PlanOptions | None = None,
) -> PlanProjection:
    """Project one storey, keeping the intermediate geometry for the dimension engine."""
    style = style_of(scale)
    opts = options or PlanOptions()

    walls = [wall for wall in house.walls if wall.storey_id == storey_id]
    wall_ids = {wall.id for wall in walls}
    openings = [opening for opening in house.openings if opening.wall_id in wall_ids]
    rooms = [room for room in house.rooms if room.storey_id == storey_id]
    stairs = [stair for stair in house.stairs if stair.storey_id == storey_id]
    columns = [column for column in house.columns if column.storey_id == storey_id]
    balconies = [balcony for balcony in house.balconies if balcony.storey_id == storey_id]

    bands = wall_bands(walls, openings)
    by_wall = {band.wall.id: band for band in bands}

    fabric: list[Primitive] = []
    for band in bands:
        fabric.extend(wall_primitives(band, hatch_spacing_mm=style.hatch_spacing_mm))
    for opening in openings:
        band = by_wall.get(opening.wall_id)
        if band is None:
            continue
        fabric.extend(opening_primitives(band, opening))
    for stair in stairs:
        fabric.extend(stair_symbol(stair, style))
    for balcony in balconies:
        fabric.extend(balcony_symbol(balcony, style, walls=walls))
    fabric.extend(column_symbols(columns, style))

    # The extent is measured on the built fabric only. Symbols that sit *outside* the
    # plan (the north dart, the FFL marker, grid bubbles) are placed relative to it, so
    # letting them widen it first would push each one further out than the last.
    extent = bbox_of(fabric)

    if extent is None:
        # Nothing is built on this storey — a new upper floor, or a storey id that is not
        # in this model. The honest projection is empty: a lone north arrow floating on a
        # blank sheet reads as a drawing failure rather than as "nothing here yet", and
        # the empty state belongs to the UI (golden rule 8), not to the geometry.
        return PlanProjection(
            storey_id=storey_id,
            style=style,
            primitives=(),
            bands=bands,
            extent=None,
            options=opts,
        )

    annotations: list[Primitive] = []
    if opts.show_room_labels:
        for ordinal, room in enumerate(rooms, start=1):
            annotations.extend(room_label(room, style, ordinal=ordinal))
    if opts.show_opening_tags:
        for opening in openings:
            band = by_wall.get(opening.wall_id)
            if band is not None:
                annotations.extend(opening_tag(band, opening, style))
    if columns and opts.show_column_grid:
        annotations.extend(column_bubbles(columns, style))
    for marker in opts.section_markers:
        from services.drawings.projection.symbols import section_marker

        annotations.extend(section_marker(marker, style))
    if opts.show_ffl_marker:
        annotations.extend(_ffl_marker(house, storey_id, style, extent))
    if opts.show_north:
        annotations.extend(
            north_arrow(
                opts.north_position or _default_north_position(style, extent),
                opts.north_deg,
                style,
            )
        )

    ordered = _paint_ordered(fabric + annotations)
    return PlanProjection(
        storey_id=storey_id,
        style=style,
        primitives=ordered,
        bands=bands,
        extent=extent,
        options=opts,
    )


def _paint_ordered(primitives: Sequence[Primitive]) -> tuple[Primitive, ...]:
    """Stable sort into :data:`PAINT_ORDER` groups. Deterministic — goldens depend on it."""
    return tuple(sorted(primitives, key=lambda item: PAINT_ORDER.get(type(item), 1)))


def _ffl_marker(
    house: Any,
    storey_id: str,
    style: Style,
    extent: tuple[int, int, int, int] | None,
) -> tuple[Primitive, ...]:
    """The storey's FFL, placed just below the plan.

    The level comes from the model's own levels — ``storey.level.ffl_mm``, falling back
    to ``levels.ffl_per_storey_mm`` by index, which is how ``fold`` derives it. Below the
    plan rather than inside it: a marker dropped in the middle of a room lands on
    whatever happens to be there, and §7's collision grid belongs to the dimension
    engine, not to a symbol that has a free margin available.
    """
    from garh_model.model import find_storey, storey_index

    storey = find_storey(house, storey_id)
    if storey is None or extent is None:
        return ()
    ffl_mm = storey.level.ffl_mm
    if ffl_mm == 0:
        index = storey_index(house, storey_id)
        per_storey = house.levels.ffl_per_storey_mm
        if 0 <= index < len(per_storey):
            ffl_mm = per_storey[index]
    min_x, min_y, _max_x, _max_y = extent
    size = style.level_marker_size_mm
    return level_marker(
        point(min_x, min_y - 3 * size),
        ffl_mm,
        style,
        owner_id=storey_id,
    )


def _default_north_position(style: Style, extent: tuple[int, int, int, int] | None) -> Point:
    """Just off the plan's top-right corner — the corner a title block never occupies."""
    length = style.north_arrow_length_mm
    if extent is None:
        return point(0, 0)
    _min_x, _min_y, max_x, max_y = extent
    return point(max_x + 2 * length, max_y - length)


# ---------------------------------------------------------------------------
# Handover to the auto-dimensioning engine (§7 steps 2–3)
# ---------------------------------------------------------------------------
def opening_dim_stations(
    band: WallBand,
    openings: Sequence[Any],
    *,
    dim_to_jamb: bool = DEFAULT_DIM_TO_JAMB,
) -> tuple[tuple[str, int], ...]:
    """``(opening_id, station_mm)`` along a wall, for a level-3 dimension chain.

    §7: "openings dimensioned to centreline (config flag ``dimToJamb`` for firm
    preference)". Both readings come from the *same* span the drawing broke the wall
    with, so the dimension lands on the jamb that is drawn and not 1mm away from it.
    Stations are returned sorted along the wall, which is the order a chain consumes.
    """
    stations: list[tuple[str, int]] = []
    for opening in openings:
        if opening.wall_id != band.wall.id:
            continue
        span = opening_span(band.frame, opening)
        if span is None:
            continue
        if dim_to_jamb:
            stations.append((opening.id, span.start_mm))
            stations.append((opening.id, span.end_mm))
        else:
            stations.append((opening.id, (span.start_mm + span.end_mm) // 2))
    return tuple(sorted(stations, key=lambda entry: (entry[1], entry[0])))


__all__ = [
    "DEFAULT_DIM_TO_JAMB",
    "PAINT_ORDER",
    "PlanOptions",
    "PlanProjection",
    "opening_dim_stations",
    "project_plan",
    "project_plan_detail",
]
