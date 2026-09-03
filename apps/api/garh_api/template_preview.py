"""Picker thumbnails for the plan library — the same primitives the sheets draw.

A template's preview is rendered from its op recipe through the SAME path the
municipal drawing set uses (``garh_model.replay`` → ``plan_primitives`` →
``render_group_svg``), so the thumbnail cannot show a plan the canvas would not.
No third source of truth for geometry (golden rule: renderers consume shared
primitives).

Rendered once, at seed time, into ``fixtures/plans/<id>.svg`` and pinned by a golden
test that re-renders the recipe and compares. The API ships the stored SVG as a data
URL, so the picker draws it with an ``<img>``: an image never executes script, which
is why §13 forbids serving drawings as ``image/svg+xml`` documents but not this.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: Layers that are noise at 200 px: dimension chains, section marks, hatch, clouds,
#: and text — a 2.5 mm room name is 2 px in the picker's 112 px box, grey fuzz that
#: hides the walls (plan-library verification, 2026-09-03). The card already says what
#: the plan is; the thumbnail shows its fabric: walls, openings, stairs.
_NOISE_LAYER_MARKERS: tuple[str, ...] = (
    "DIM",
    "SECT",
    "HATCH",
    "PATT",
    "CLOUD",
    "REVC",
    "GRID",
    "TEXT",
    "AREA",
    "TITL",
)


def is_preview_layer(layer: str) -> bool:
    name = layer.upper()
    return not any(marker in name for marker in _NOISE_LAYER_MARKERS)


PREVIEW_SCALE = 100
MARGIN_UM = 15_000  # 15 mm of paper = 1.5 m of building at 1:100: a hairline of air around the plan


class PreviewUnavailable(RuntimeError):
    """The drawings renderer is not importable here (a worker-less image)."""


def recipe_document(ops: Sequence[Mapping[str, Any]]) -> Any:
    """Fold a wire-op recipe into a ProjectDoc, exactly as the sequencer would."""
    from garh_model import replay
    from garh_model.ops import Op

    return replay([Op(type=str(o["type"]), payload=dict(o.get("payload") or {})) for o in ops])


def preview_svg(ops: Sequence[Mapping[str, Any]], *, storey_index: int = 0) -> str:
    """One storey's plan as a standalone, deterministic SVG document."""
    try:
        from services.drawings.render.primitives import DrawingGroup, Placement
        from services.drawings.render.reference_sheets import plan_primitives
        from services.drawings.render.svg import render_group_svg
    except ImportError as exc:  # pragma: no cover - exercised only in a worker-less image
        raise PreviewUnavailable(str(exc)) from exc

    doc = recipe_document(ops)
    storeys = list(doc.house.storeys)
    if not storeys:
        raise ValueError("the recipe has no storeys, so there is nothing to draw")
    storey = storeys[min(storey_index, len(storeys) - 1)]
    primitives, _chains = plan_primitives(doc, storey.id, scale_denominator=PREVIEW_SCALE)
    # Hatch primitives are emitted inside a clip group whose <clipPath> the SHEET
    # defines in its <defs>; a standalone fragment has no defs, so the clip dangled
    # and every partition drew as a grey band (plan-library verification, 2026-09-03).
    kept = tuple(p for p in primitives if is_preview_layer(p.layer) and type(p).__name__ != "Hatch")
    if not kept:
        raise ValueError("storey %r has no walls to draw" % storey.name)
    probe = DrawingGroup(id="preview", placement=Placement(PREVIEW_SCALE), primitives=kept)
    extent = probe.extent_model_mm()
    assert extent is not None
    min_x, min_y, max_x, max_y = extent
    # Paper y grows downward and building y grows upward, so the paper origin maps to
    # the building's top-left corner (see Placement's transform).
    placement = Placement(
        PREVIEW_SCALE,
        origin_model_mm=(min_x, max_y),
        origin_paper_um=(MARGIN_UM, MARGIN_UM),
    )
    group = DrawingGroup(id="preview", placement=placement, primitives=kept, label=storey.name)
    width_mm = (max_x - min_x) * 1000 // PREVIEW_SCALE // 1000 + 2 * MARGIN_UM // 1000
    height_mm = (max_y - min_y) * 1000 // PREVIEW_SCALE // 1000 + 2 * MARGIN_UM // 1000
    body = render_group_svg(group)
    if "url(#" in body:
        raise ValueError("the thumbnail references a definition the fragment does not carry")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%dmm" '
        'height="%dmm" version="1.1">\n<!-- garh-template-preview %s -->\n%s\n</svg>\n'
        % (width_mm, height_mm, width_mm, height_mm, storey_index, body)
    )


def preview_data_url(svg: str) -> str:
    """The SVG as an ``<img src>`` value — never a document the browser executes."""
    from urllib.parse import quote

    return "data:image/svg+xml;charset=utf-8," + quote(svg, safe="")


__all__ = [
    "is_preview_layer",
    "PreviewUnavailable",
    "preview_data_url",
    "preview_svg",
    "recipe_document",
]
