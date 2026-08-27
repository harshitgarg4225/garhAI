"""Compose one sheet: frame + projected geometry, in a single paper-space stream. §7.

THIS IS THE ONLY MODULE THAT SCALES ANYTHING. Projectors work in model millimetres and
know nothing about paper; ``frame`` works in paper millimetres and knows nothing about the
building; the renderers receive one flat list in paper µm and make no spatial decisions at
all. Every scale multiplication in the drawing engine happens in
:func:`transform_primitive` below, so "is this sheet at the right scale?" is a question
with exactly one place to look.

    Rendering pipeline: model → 2D projection primitives → SVG (screen + PDF via headless
    print) and DXF (ezdxf, mm units, ...)

WHAT GETS SCALED, AND WHAT DOES NOT
-----------------------------------
Positions, arc radii, text heights and hatch spacings are all lengths and all scale.
Angles do not: this transform has no rotation and no mirroring, so a door swing at 90°
stays at 90° and a hatch at 45° stays at 45°. That is why a sheet cannot flip a plan —
mirroring geometry would silently produce a mirror-image building, which is the one
drawing error a reviewer cannot detect from the sheet itself.

Text heights arrive already paper-scaled from ``projection.style`` (2.5mm of paper is
250mm of model at 1:100), so scaling them back down here returns them to 2.5mm on paper.
The two conversions are inverses on purpose: the projector needs the height in model space
to lay out a label block against real walls, and the renderer needs it in paper space to
print it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from services.drawings.projection.primitives import (
    Arc,
    Hatch,
    Line,
    Polyline,
    Primitive,
    Text,
    bbox_of,
)
from services.drawings.sheets.frame import frame_primitives
from services.drawings.sheets.model import Sheet
from services.drawings.sheets.transform import Fit, PaperTransform, fit_to_frame


def transform_primitive(item: Primitive, transform: PaperTransform) -> Primitive:
    """One primitive from model mm into paper µm."""
    if isinstance(item, Line):
        return replace(item, a=transform.point_to_paper(item.a), b=transform.point_to_paper(item.b))
    if isinstance(item, Arc):
        return replace(
            item,
            centre=transform.point_to_paper(item.centre),
            radius_mm=max(1, transform.length_to_paper_um(item.radius_mm)),
        )
    if isinstance(item, Text):
        return replace(
            item,
            position=transform.point_to_paper(item.position),
            height_mm=max(1, transform.length_to_paper_um(item.height_mm)),
        )
    if isinstance(item, Hatch):
        return replace(
            item,
            boundary=tuple(transform.point_to_paper(p) for p in item.boundary),
            holes=tuple(tuple(transform.point_to_paper(p) for p in ring) for ring in item.holes),
            spacing_mm=max(1, transform.length_to_paper_um(item.spacing_mm)),
        )
    if isinstance(item, Polyline):
        return replace(item, points=tuple(transform.point_to_paper(p) for p in item.points))
    raise TypeError("not a primitive: %r" % (item,))


def transform_primitives(
    primitives: Sequence[Primitive], transform: PaperTransform
) -> tuple[Primitive, ...]:
    """A whole model-space stream into paper space."""
    return tuple(transform_primitive(item, transform) for item in primitives)


@dataclass(frozen=True)
class ComposedSheet:
    """A sheet ready to render: one stream, paper µm, frame first.

    ``warnings`` are for the job's progress feed, not for the drawing. §7 sheets are
    print-true, so a drawing that overruns its frame is reported at its honest scale
    rather than quietly shrunk to fit — an architect who measures 1:100 off a sheet that
    was secretly squeezed has a real problem.
    """

    sheet: Sheet
    transform: PaperTransform
    fit: Fit
    primitives: tuple[Primitive, ...]
    warnings: tuple[str, ...] = ()


def compose_sheet(
    sheet: Sheet,
    model_primitives: Sequence[Primitive] = (),
    *,
    paper_primitives: Sequence[Primitive] = (),
    extent_model_mm: tuple[int, int, int, int] | None = None,
    transform: PaperTransform | None = None,
    reserve_title_block: bool = True,
) -> ComposedSheet:
    """Frame + geometry for one sheet.

    ``model_primitives``
        A projector's output, in model mm. Scaled and centred by the transform.
    ``paper_primitives``
        Content that is already paper-space — a schedule table, an area statement, a
        drawing-list block. Passed through untouched, which is what makes the table
        sheets (§7's schedule and area statement) fit the same pipeline as the drawings
        instead of needing one of their own.
    ``extent_model_mm``
        What to centre on. Defaults to the extent of ``model_primitives``, which includes
        their dimension chains — the drawing has to fit *with* its dimensions, not just
        the building.
    """
    sheet.validate()
    if extent_model_mm is None and model_primitives:
        extent_model_mm = bbox_of(model_primitives)

    fit = fit_to_frame(
        extent_model_mm,
        sheet.frame,
        sheet.scale,
        reserve_title_block=reserve_title_block,
        offset_mm=sheet.viewport.offset_mm,
    )
    resolved = transform or fit.transform

    warnings: list[str] = []
    if not fit.fits:
        suggestion = fit.suggested_denominator()
        warnings.append(
            "Sheet %s: the drawing needs %.0f × %.0fmm of paper but only %.0f × %.0fmm "
            "is free at %s. Try 1:%d or a larger sheet."
            % (
                sheet.number,
                fit.required_paper_mm[0],
                fit.required_paper_mm[1],
                fit.available_paper_mm[0],
                fit.available_paper_mm[1],
                sheet.scale.label,
                suggestion,
            )
        )

    primitives: list[Primitive] = list(frame_primitives(sheet.frame))
    primitives.extend(transform_primitives(model_primitives, resolved))
    primitives.extend(paper_primitives)
    return ComposedSheet(
        sheet=sheet,
        transform=resolved,
        fit=fit,
        primitives=tuple(primitives),
        warnings=tuple(warnings),
    )


def compose_plan_sheet(
    sheet: Sheet,
    house: Any,
    *,
    plan_options: Any = None,
    reserve_title_block: bool = True,
) -> ComposedSheet:
    """Convenience: project the sheet's storey and compose it in one call.

    The sheet's own ``viewport.storey_id`` and ``scale`` drive the projection, so a plan
    sheet cannot end up showing one storey at another storey's scale.
    """
    from services.drawings.projection.plan import project_plan_detail

    storey_id = sheet.viewport.storey_id
    if storey_id is None:
        raise ValueError(
            "compose_plan_sheet needs a viewport with a storeyId; sheet %s is a %s."
            % (sheet.number, sheet.kind)
        )
    projection = project_plan_detail(house, storey_id, sheet.scale, options=plan_options)
    return compose_sheet(
        sheet,
        projection.primitives,
        reserve_title_block=reserve_title_block,
    )


__all__ = [
    "ComposedSheet",
    "compose_plan_sheet",
    "compose_sheet",
    "transform_primitive",
    "transform_primitives",
]
