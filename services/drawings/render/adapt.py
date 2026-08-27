"""Adapter: ``services.drawings.projection`` primitives -> renderer primitives.

**Fully implemented, pure, and the piece that makes the two halves of Phase 8 meet.**

Phase 8 was built as two pieces in parallel:

* ``services.drawings.projection`` + ``services.drawings.autodim`` — the §7 projection and
  auto-dimensioning engine. It emits a model-space primitive stream:
  ``Line | Arc | Text | Hatch | Polyline``.
* ``services.drawings.render`` + ``services.drawings.export`` — the renderers and
  exporters. They consume the vocabulary in
  :mod:`services.drawings.render.primitives`.

The two vocabularies were designed independently and are, happily, the *same five shapes*
with the same conventions — including the one that would have hurt most to get wrong:
arcs sweep counter-clockwise from ``start_deg`` to ``end_deg`` in both, so no door swing
gets mirrored in translation. Four differences remain, all mechanical, and this module is
where each is resolved exactly once:

======================  ==============================  ==============================
concept                 projection                      render
======================  ==============================  ==============================
text height             ``height_mm`` (**model** mm)     ``height_paper_um`` (**paper** µm)
line style              ``dashed: bool``                ``style: "solid" | "dashed" | …``
hatch pattern           DXF names (``ANSI31``…)         semantic names (``diagonal``…)
text alignment          ``h_align`` / ``v_align``       ``anchor`` / ``baseline``
element provenance      ``owner_id``                    ``element_id``
======================  ==============================  ==============================

The text-height row is the one worth reading twice. The projection stream carries model
millimetres (a 2.5 mm paper letter is 250 mm at 1:100); the renderers carry paper
micrometres, because ISO 3098 text is a property of the print and must not rescale.
Converting needs the sheet's scale, which is why :func:`from_projection` requires
``scale_denominator`` and refuses to guess. A wrong scale here produces text that is
plausible-looking and the wrong size on every sheet — so the conversion is asserted in
:mod:`services.drawings.tests.test_render` against a known 1:100 case.

Why an adapter rather than one side adopting the other: neither vocabulary is wrong, both
are tested against their own goldens, and rewriting either would invalidate a golden
corpus to save a 150-line pure function. The integrator can collapse them later; until
then this keeps the seam explicit and covered.

Nothing here makes a geometry decision. Every coordinate passes through unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from services.drawings.render.primitives import (
    HATCH_CROSS,
    HATCH_DIAGONAL,
    HATCH_EARTH,
    HATCH_SOLID,
    STYLE_DASHED,
    STYLE_SOLID,
    Arc,
    Circle,
    Hatch,
    Line,
    Polyline,
    Primitive,
    Text,
    div_round,
)

__all__ = [
    "ALIGN_TO_ANCHOR",
    "PATTERN_TO_HATCH",
    "VALIGN_TO_BASELINE",
    "AdaptError",
    "from_projection",
    "from_projection_one",
    "model_mm_to_paper_um",
]


class AdaptError(TypeError):
    """A primitive kind this adapter does not know. Never silently dropped."""


#: DXF pattern name -> the renderer's semantic pattern. The projection stream names
#: patterns the way AutoCAD does (it is heading for a DXF); the renderers name them by
#: what they mean (they also draw SVG, which has no ANSI31).
PATTERN_TO_HATCH = {
    "SOLID": HATCH_SOLID,
    "ANSI31": HATCH_DIAGONAL,
    "ANSI37": HATCH_CROSS,
    "EARTH": HATCH_EARTH,
    # Lower-case forms in case a caller passes the renderer's own names through.
    HATCH_SOLID: HATCH_SOLID,
    HATCH_DIAGONAL: HATCH_DIAGONAL,
    HATCH_CROSS: HATCH_CROSS,
    HATCH_EARTH: HATCH_EARTH,
}

#: ``h_align`` -> ``anchor``. "center" (US) and "middle" (SVG) mean the same thing.
ALIGN_TO_ANCHOR = {
    "left": "start",
    "center": "middle",
    "centre": "middle",
    "middle": "middle",
    "right": "end",
    "start": "start",
    "end": "end",
}

#: ``v_align`` -> ``baseline``. "bottom" maps to the alphabetic baseline: for a single
#: line of drawing text the difference is a descender, and a dimension label sitting a
#: descender high is invisible next to being on the wrong side of the dimension line.
VALIGN_TO_BASELINE = {
    "top": "hanging",
    "hanging": "hanging",
    "middle": "middle",
    "bottom": "baseline",
    "baseline": "baseline",
}


def model_mm_to_paper_um(height_mm: int, scale_denominator: int) -> int:
    """Model mm -> paper µm. ``250 mm`` at 1:100 is ``2500 µm`` (2.5 mm on paper)."""
    if scale_denominator <= 0:
        raise ValueError("scale denominator must be positive, got %d" % scale_denominator)
    return div_round(height_mm * 1000, scale_denominator)


def _style(dashed: bool) -> str:
    return STYLE_DASHED if dashed else STYLE_SOLID


def _pattern(name: str) -> str:
    try:
        return PATTERN_TO_HATCH[name]
    except KeyError:
        raise AdaptError(
            "hatch pattern %r has no renderer equivalent. Known: %s. A silently "
            "substituted pattern means a section's concrete prints as brick."
            % (name, ", ".join(sorted(set(PATTERN_TO_HATCH))))
        ) from None


def from_projection_one(item: Any, *, scale_denominator: int) -> Primitive:
    """Convert one projection primitive. Raises :class:`AdaptError` on an unknown kind.

    Duck-typed rather than ``isinstance``-checked against the projection classes: the
    projection package imports ``services.common`` transitively in some paths, and this
    module must stay importable on a bare interpreter. The field names are the contract.
    """
    kind = getattr(item, "kind", "") or ""
    owner = getattr(item, "owner_id", None)

    if hasattr(item, "a") and hasattr(item, "b"):
        return Line(
            a=tuple(item.a),  # type: ignore[arg-type]
            b=tuple(item.b),  # type: ignore[arg-type]
            layer=item.layer,
            style=_style(bool(getattr(item, "dashed", False))),
            element_id=owner,
        )

    if hasattr(item, "radius_mm"):
        start = int(item.start_deg) % 360
        end = int(item.end_deg)
        # A full circle in the projection stream is 0->360; the renderer has a Circle for
        # that, which becomes a real DXF CIRCLE and one SVG element instead of two arcs.
        if int(item.start_deg) == 0 and end == 360:
            return Circle(
                centre=tuple(item.centre),  # type: ignore[arg-type]
                radius_mm=int(item.radius_mm),
                layer=item.layer,
                style=_style(bool(getattr(item, "dashed", False))),
                element_id=owner,
            )
        return Arc(
            centre=tuple(item.centre),  # type: ignore[arg-type]
            radius_mm=int(item.radius_mm),
            start_deg=start,
            end_deg=end % 360,
            layer=item.layer,
            style=_style(bool(getattr(item, "dashed", False))),
            element_id=owner,
        )

    if hasattr(item, "text") and hasattr(item, "height_mm"):
        return Text(
            at=tuple(item.position),  # type: ignore[arg-type]
            text=item.text,
            layer=item.layer,
            height_paper_um=model_mm_to_paper_um(int(item.height_mm), scale_denominator),
            anchor=ALIGN_TO_ANCHOR.get(getattr(item, "h_align", "center"), "middle"),
            baseline=VALIGN_TO_BASELINE.get(getattr(item, "v_align", "middle"), "middle"),
            rotation_deg=int(getattr(item, "rotation_deg", 0)),
            element_id=owner,
            bold=kind in ("room-name", "title-block-value", "section-label"),
        )

    if hasattr(item, "boundary"):
        return Hatch(
            outline=tuple(tuple(p) for p in item.boundary),  # type: ignore[arg-type]
            layer=item.layer,
            pattern=_pattern(getattr(item, "pattern", HATCH_SOLID)),
            spacing_mm=int(getattr(item, "spacing_mm", 250)),
            angle_deg=int(getattr(item, "angle_deg", 0)),
            holes=tuple(tuple(tuple(p) for p in ring) for ring in getattr(item, "holes", ())),
            element_id=owner,
        )

    if hasattr(item, "points"):
        return Polyline(
            vertices=tuple(tuple(p) for p in item.points),  # type: ignore[arg-type]
            layer=item.layer,
            closed=bool(getattr(item, "closed", False)),
            style=_style(bool(getattr(item, "dashed", False))),
            element_id=owner,
        )

    raise AdaptError(
        "%s is not a projection primitive this adapter knows (expected one of Line, Arc, "
        "Text, Hatch, Polyline by field shape). Dropping a primitive silently means a "
        "missing wall on a submission drawing." % type(item).__name__
    )


def from_projection(primitives: Sequence[Any], *, scale_denominator: int) -> tuple[Primitive, ...]:
    """Convert a whole projection primitive stream. Order is preserved.

    Order is preserved rather than re-sorted: the projection engine already emits in a
    deliberate draw order, and :func:`services.drawings.render.primitives.sort_by_layer`
    is a stable sort, so the renderer's layer grouping keeps that order within each layer.
    """
    out: list[Primitive] = []
    for item in primitives:
        out.append(from_projection_one(item, scale_denominator=scale_denominator))
    return tuple(out)
