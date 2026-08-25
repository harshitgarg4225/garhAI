"""The section — §7's one cut, through the staircase. **Real, no dependencies.**

    Section (through stair): section line auto-chosen through stair flight + one wet area
    if possible; show storey heights chain, sill/lintel heights, plinth, parapet, mumty,
    foundation indicative line (900mm below plinth, dashed, labeled
    "INDICATIVE — REFER STRUCTURAL").

=======================  ==================================================  ==========
``choose``               candidate cuts, scored — *why* this line             **real**
``project``              ``(HouseModel[, CutLine]) → primitives``             **real**
``stair``                flight geometry, and what the model cannot say       **real**
``smoke``                ``python "services/drawings/sections/smoke.py"``     **real**
=======================  ==================================================  ==========

Two things about this package are deliberate and worth knowing before changing it.

**The cut line is chosen, not configured.** :func:`~services.drawings.sections.choose.choose_section_line`
scores candidates against the rules §7 states — through the stair flight, along it rather
than across it, reaching a wet area if one is reachable — and carries the breakdown on the
result so the sheet can explain itself. A hardcoded line would be right for the demo plan
and quietly wrong for every real one.

**The stair is drawn only as far as the model describes it.** ``Stair`` stores one origin,
one direction and one landing, so a dogleg's return flight does not exist in the document;
:mod:`services.drawings.sections.stair` draws the first flight and the landing and puts the
limitation in the drawing's notes. Inventing the rest would put geometry on a municipal
sheet that the plan and the 3D view do not agree with.

Levels, level markers, the height chain and the ``u = ẑ × n̂`` axis convention are shared
with the elevations (:mod:`services.drawings.elevations.vertical`), on purpose: the section
and the elevations must agree about every floor level, and the only way to guarantee that
is for them to read it from the same code.
"""

from __future__ import annotations

from services.drawings.sections.choose import (
    CutLine,
    SectionCandidate,
    SectionChoice,
    choose_section_line,
    score_candidate,
)
from services.drawings.sections.project import (
    FOUNDATION_DEPTH_BELOW_PLINTH_MM,
    FOUNDATION_LABEL,
    MUMTY_CLEAR_HEIGHT_MM,
    SectionOptions,
    SectionResult,
    build_section,
)
from services.drawings.sections.stair import StairGeometry, stair_geometry

__all__ = [
    "FOUNDATION_DEPTH_BELOW_PLINTH_MM",
    "FOUNDATION_LABEL",
    "MUMTY_CLEAR_HEIGHT_MM",
    "CutLine",
    "SectionCandidate",
    "SectionChoice",
    "SectionOptions",
    "SectionResult",
    "StairGeometry",
    "build_section",
    "choose_section_line",
    "score_candidate",
    "stair_geometry",
]
