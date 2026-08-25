"""§7 schedules: the door/window schedule and the municipal area statement.

Two of the six municipal sheets are tables rather than projections, and both are pure
integer arithmetic over data the rest of the system already produced. That is why they
live here, apart from the projection pipeline, and why every line of this package runs
on a bare interpreter with no ezdxf, no OR-Tools and no GPU:

========================  ======================================================
:mod:`openings`           one normalised opening shape from model / rules / JSON
:mod:`door_window`        §7 grouping by ``(kind, w, h)``, the D/W/V tag series,
                          per-storey counts, the tag map the plan reads
:mod:`area_statement`     the municipal statement, **rendered from**
                          ``garh_rules.areas`` — no FAR/coverage/setback maths here
:mod:`display`            the Indian formatting boundary (m² · sq ft · gaj)
:mod:`table`              integer paper-mm table layout → primitives / text / SVG
:mod:`sheet_primitives`   resolves the shared ``ScheduleRow``/``AreaStatementRow``
:mod:`projection_adapter` a table → the projection pipeline's ``Text``/``Line``
========================  ======================================================

The two contracts other Phase-8 modules depend on:

**Tags.** :func:`~services.drawings.schedules.door_window.opening_tags` returns
``{opening_id: tag}``. The plan projection labels its openings from that mapping and
must not derive tags of its own — that is how "the plan's labels and the schedule
agree" becomes a property of the code instead of a review checklist item.

**One source for regulatory numbers.** The area statement's FAR, coverage and setback
figures are the rules engine's results, read through ``garh_rules.areas.AreaStatement``.
Nothing in this package recomputes them, and
``services/drawings/tests/test_schedules.py`` asserts equality against an independent
``garh_rules.evaluate`` run so a future edit cannot quietly reintroduce a second source.
"""

from __future__ import annotations

from services.drawings.schedules.area_statement import (
    CARPET_EXCLUDED_ROOM_TYPES,
    AreaStatementSheet,
    CarpetRow,
    SetbackLine,
    StoreyLine,
    build_area_statement_sheet,
    carpet_by_storey,
)
from services.drawings.schedules.display import (
    area_cell,
    count_cell,
    mm_cell,
    percent_cell,
    plot_area_cell,
    ratio_cell,
    sqft_text,
    sqm_text,
)
from services.drawings.schedules.door_window import (
    KIND_LABELS,
    TAG_PREFIXES,
    DoorWindowSchedule,
    ScheduleGroup,
    build_schedule,
    group_key_of,
    opening_tags,
    tagged_openings,
)
from services.drawings.schedules.openings import (
    OPENING_KIND_ORDER,
    UNKNOWN_STOREY,
    ScheduleOpening,
    StoreyRef,
    normalise_openings,
    normalise_storeys,
)
from services.drawings.schedules.projection_adapter import table_to_primitives
from services.drawings.schedules.sheet_primitives import AreaStatementRow, ScheduleRow
from services.drawings.schedules.table import (
    Column,
    LineItem,
    Table,
    TableStyle,
    TextItem,
)

__all__ = [
    "CARPET_EXCLUDED_ROOM_TYPES",
    "KIND_LABELS",
    "OPENING_KIND_ORDER",
    "TAG_PREFIXES",
    "UNKNOWN_STOREY",
    "AreaStatementRow",
    "AreaStatementSheet",
    "CarpetRow",
    "Column",
    "DoorWindowSchedule",
    "LineItem",
    "ScheduleGroup",
    "ScheduleOpening",
    "ScheduleRow",
    "SetbackLine",
    "StoreyLine",
    "StoreyRef",
    "Table",
    "TableStyle",
    "TextItem",
    "area_cell",
    "build_area_statement_sheet",
    "build_schedule",
    "carpet_by_storey",
    "count_cell",
    "group_key_of",
    "mm_cell",
    "normalise_openings",
    "normalise_storeys",
    "opening_tags",
    "percent_cell",
    "plot_area_cell",
    "ratio_cell",
    "sqft_text",
    "sqm_text",
    "table_to_primitives",
    "tagged_openings",
]
