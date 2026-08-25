"""Elevations — §7's four facade projections. **Real, and runnable with no dependencies.**

    Elevations: project facade sub-model + openings per direction; dims: floor lines
    (plinth/FFL/lintel/parapet levels as level markers, not chains), overall height
    chain; material callout leaders from facade kit metadata.

=======================  ==================================================  ==========
``project``              ``(HouseModel, "N"|"E"|"S"|"W") → primitives``       **real**
``facade``               front-facing faces, visible openings, balconies      **real**
``vertical``             axes, levels, level markers, the height chain         **real**
``callouts``             material callouts from facade component metadata      **real**
``demo_house``           a folded G+1 fixture, for the tests and the smoke     test aid
``smoke``                ``python "services/drawings/elevations/smoke.py"``    **real**
=======================  ==================================================  ==========

The section is the sibling projector (:mod:`services.drawings.sections`) and shares
:mod:`~services.drawings.elevations.vertical` with this package — same axes, same levels,
same chain, so an elevation and the section can never disagree about a floor level.

WHAT MAKES THIS TESTABLE ANYWHERE
---------------------------------
Nothing here imports ezdxf, the model core, or ``services.common``. A house is read by
attribute access, output is
:mod:`services.drawings.projection.primitives`, and every number is an integer
millimetre — so the whole projector runs on a bare interpreter and the DXF boundary stays
one module away in :mod:`services.drawings.dxf`. That was the point of the split: the part
of §7 that has to be *correct* is the part that needs no dependency to prove.

THE ONE RULE WORTH RESTATING
----------------------------
An opening on the far face of the building must not appear. It is structural here rather
than a special case: :func:`~services.drawings.elevations.facade.facade_faces` only yields
walls whose outward normal is the drawing's, and
:func:`~services.drawings.elevations.facade.visible_openings` can only place an opening on
one of those faces. A nearer face that fully covers a further one hides its openings too.
"""

from __future__ import annotations

from services.drawings.elevations.callouts import Callout, build_callouts, callout_text
from services.drawings.elevations.facade import (
    FacadeFace,
    FacadeOpening,
    ProjectedBalcony,
    facade_faces,
    footprint_of,
    footprint_rings,
    outward_normal_of,
    visible_balconies,
    visible_openings,
)
from services.drawings.elevations.project import (
    ElevationOptions,
    build_all_elevations,
    build_elevation,
    elevation_title,
    true_azimuth_deg,
)
from services.drawings.elevations.vertical import (
    DIRECTIONS_4,
    U_AXES,
    LevelMarker,
    LevelSet,
    StoreyLevels,
    VerticalDrawing,
    VerticalStyle,
    build_levels,
    height_chain,
    normals_of,
)

__all__ = [
    "DIRECTIONS_4",
    "U_AXES",
    "Callout",
    "ElevationOptions",
    "FacadeFace",
    "FacadeOpening",
    "LevelMarker",
    "LevelSet",
    "ProjectedBalcony",
    "StoreyLevels",
    "VerticalDrawing",
    "VerticalStyle",
    "build_all_elevations",
    "build_callouts",
    "build_elevation",
    "build_levels",
    "callout_text",
    "elevation_title",
    "facade_faces",
    "footprint_of",
    "footprint_rings",
    "height_chain",
    "normals_of",
    "outward_normal_of",
    "true_azimuth_deg",
    "visible_balconies",
    "visible_openings",
]
