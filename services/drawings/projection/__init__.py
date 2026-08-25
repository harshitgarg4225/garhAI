"""Projection: the model → 2D primitives stage of §7. **Real, and dependency-free.**

    Rendering pipeline: model → 2D projection primitives (lines/arcs/text/hatches with
    layer tags) → SVG (screen + PDF via headless print) and DXF.

THE STRUCTURAL RULE OF §7
-------------------------
Every renderer consumes **only** the primitive list. SVG, DXF and PDF therefore cannot
show different buildings, because there is one piece of code that decides what a door
looks like and it runs before any of them. The corollary is the part that takes
discipline: a renderer may never reach back into the ``HouseModel``. If a format needs
something more, the projector emits it.

=======================  ==================================================  ==========
``primitives``           Line / Arc / Text / Hatch / Polyline, layer-tagged   **real**
``style``                paper-mm sizes + the scalar paper↔model transform    **real**
``walls``                double lines, mitred junctions, opening breaks       **real**
``symbols``              north, stairs, levels, sections, grids, labels       **real**
``plan``                 ``(HouseModel, storeyId, scale) → primitives``       **real**
``smoke``                ``python -m services.drawings.projection.smoke``     **real**
=======================  ==================================================  ==========

Elevations and sections are the sibling projectors; they emit the same primitives and
consume the same :class:`~services.drawings.projection.style.Style`.

WHICH LAYER GETS WHAT
---------------------
The nine §7 layer names are a contract with AutoCAD (see
:mod:`services.drawings.layers`), so the mapping is decided once, here, rather than at
each call site:

==============  ==========================================================================
``A-WALL``      wall faces, end caps and the external-wall poché hatch (full-height walls)
``A-WALL-PART`` parapets, balcony slab edges and railings, column rectangles and fill
``A-DOOR``      door jambs, leaf, swing arc
``A-WIND``      window/ventilator jambs, frame lines, glazing line, opening tags
``A-STAIR``     stair footprint, treads, UP arrow
``A-DIM``       level markers, section cut lines and flags, structural grid + bubbles
``A-TEXT``      room names and areas, UP labels, level text, grid labels, north symbol
``A-AREA``      room boundary polylines (dashed) — what the area statement measures
``A-TITL``      sheet frame and title block (emitted by ``sheets.frame``, not here)
==============  ==========================================================================

Two of those deserve their reasoning stated. The **north arrow** is drawn geometry with
no layer of its own in §7's nine; it goes on A-TEXT because it is a symbol block that
travels with the plan and prints at text weight. The **structural grid** goes on A-DIM
because it is a referencing construct like a witness line. Neither justifies a tenth
layer: adding one changes what every downstream consumer of our DXF sees.
"""

from __future__ import annotations

from services.drawings.projection.plan import (
    DEFAULT_DIM_TO_JAMB,
    PlanOptions,
    PlanProjection,
    opening_dim_stations,
    project_plan,
    project_plan_detail,
)
from services.drawings.projection.primitives import (
    Arc,
    Hatch,
    Line,
    Point,
    Polyline,
    Primitive,
    PrimitiveError,
    Text,
    bbox_of,
    by_kind,
    by_layer,
    by_owner,
    canonical_json,
    count_by_kind,
    count_by_layer,
    find_unsafe_text,
    point,
    point_of,
    point_round,
    primitives_digest,
    primitives_to_json,
    round_half_away,
    sanitise_text,
    translate,
    validate_primitives,
)
from services.drawings.projection.style import Style, style_of
from services.drawings.projection.symbols import (
    SectionMarker,
    column_grid_lines,
    level_marker,
    north_arrow,
    room_label,
    section_marker,
    stair_symbol,
)
from services.drawings.projection.walls import (
    FaceExtents,
    Span,
    WallBand,
    WallFrame,
    clipped_gap_total,
    opening_span,
    split_span,
    wall_band,
    wall_bands,
    wall_frame,
    wall_primitives,
)

__all__ = [
    "DEFAULT_DIM_TO_JAMB",
    "Arc",
    "FaceExtents",
    "Hatch",
    "Line",
    "PlanOptions",
    "PlanProjection",
    "Point",
    "Polyline",
    "Primitive",
    "PrimitiveError",
    "SectionMarker",
    "Span",
    "Style",
    "Text",
    "WallBand",
    "WallFrame",
    "bbox_of",
    "by_kind",
    "by_layer",
    "by_owner",
    "canonical_json",
    "clipped_gap_total",
    "column_grid_lines",
    "count_by_kind",
    "count_by_layer",
    "find_unsafe_text",
    "level_marker",
    "north_arrow",
    "opening_dim_stations",
    "opening_span",
    "point",
    "point_of",
    "point_round",
    "primitives_digest",
    "primitives_to_json",
    "project_plan",
    "project_plan_detail",
    "room_label",
    "round_half_away",
    "sanitise_text",
    "section_marker",
    "split_span",
    "stair_symbol",
    "style_of",
    "translate",
    "validate_primitives",
    "wall_band",
    "wall_bands",
    "wall_frame",
    "wall_primitives",
]
