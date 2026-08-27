"""A minimal SVG serialiser for the ``A-DIM`` stream — goldens and eyeballs.

This is **not** the sheet renderer. The plan/elevation/section projectors own the real
SVG output (frame, title block, walls, hatches, room labels); this module draws nothing
but the dimension primitives this package produced, and exists for two reasons:

1. **A golden a human can open.** §16 wants SVG goldens byte-diffed in CI. The primitive
   JSON golden catches every numeric change, but a reviewer cannot see a layout in it. An
   SVG of the same run can be dropped into a browser, and "the north chain's labels are
   stacked" becomes obvious in a second.
2. **It is pure string output**, so unlike DXF it can be generated and committed on a
   machine with no drawing libraries installed at all.

§13 compliance is structural, not reviewed: this writer can emit ``<line>``, ``<text>``,
``<g>`` and a static ``<style>`` block and literally nothing else. No script, no
``foreignObject``, no external reference, no ``href`` of any kind. Text is escaped on the
way out even though dimension labels are digits, because "it is always digits" is the
kind of assumption that stops being true.

Coordinates: plan millimetres, y flipped once per point (``max_y - y``) rather than by a
group transform, so every number in the file is an integer and the text needs no
counter-transform. Determinism follows from the primitive order, which
:func:`services.drawings.autodim.render.render_primitives` fixes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from services.drawings.autodim.config import DEFAULT_CONFIG, AutoDimConfig
from services.drawings.autodim.primitives import (
    KIND_DIM,
    KIND_LEADER,
    KIND_TICK,
    KIND_WITNESS,
    Line,
    Primitive,
    Text,
)

#: Stroke widths in model mm, from the ``A-DIM`` lineweight (0.13mm) at 1:100.
_STROKE_MM: dict[str, int] = {
    KIND_DIM: 13,
    KIND_WITNESS: 10,
    KIND_TICK: 13,
    KIND_LEADER: 10,
}

#: ``kind`` → CSS class. A renderer may style from ``kind``; it may never derive
#: geometry from it (the projection module's rule).
_CLASS: dict[str, str] = {
    KIND_DIM: "dim",
    KIND_WITNESS: "witness",
    KIND_TICK: "tick",
    KIND_LEADER: "leader",
}

_STYLE_BLOCK = """
  .dim    { stroke: #c0392b; stroke-width: %(dim)d; }
  .witness{ stroke: #c0392b; stroke-width: %(witness)d; }
  .tick   { stroke: #c0392b; stroke-width: %(tick)d; }
  .leader { stroke: #c0392b; stroke-width: %(leader)d; }
  text    { fill: #c0392b; font-family: monospace; text-anchor: middle;
            dominant-baseline: central; }
"""


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _bounds(primitives: Sequence[Primitive], padding_mm: int) -> tuple[int, int, int, int]:
    xs: list[int] = []
    ys: list[int] = []
    for primitive in primitives:
        if isinstance(primitive, Line):
            xs.extend((primitive.a[0], primitive.b[0]))
            ys.extend((primitive.a[1], primitive.b[1]))
        else:
            half_w = primitive.height_mm * len(primitive.text)
            xs.extend((primitive.position[0] - half_w, primitive.position[0] + half_w))
            ys.extend(
                (
                    primitive.position[1] - primitive.height_mm,
                    primitive.position[1] + primitive.height_mm,
                )
            )
    if not xs:
        return (0, 0, 1, 1)
    return (
        min(xs) - padding_mm,
        min(ys) - padding_mm,
        max(xs) + padding_mm,
        max(ys) + padding_mm,
    )


def render_svg(
    result: Any,
    *,
    config: AutoDimConfig = DEFAULT_CONFIG,
    padding_mm: int = 600,
) -> str:
    """The dimension stream of one :class:`DimensionResult` as an SVG document.

    Ends with a newline, uses ``\\n`` throughout and contains no timestamp, id counter or
    other run-varying content — the three things that make an SVG golden flap.
    """
    primitives = result.primitives
    min_x, min_y, max_x, max_y = _bounds(primitives, padding_mm)
    width, height = max_x - min_x, max_y - min_y

    def sx(value: int) -> int:
        return value - min_x

    def sy(value: int) -> int:
        return max_y - value

    lines: list[str] = []
    lines.append(
        '<svg xmlns="http://www.w3.org/2000/svg" width="%dmm" height="%dmm" '
        'viewBox="0 0 %d %d">'
        % (width // config.scale_denominator, height // config.scale_denominator, width, height)
    )
    lines.append(
        "  <style>%s  </style>"
        % (
            _STYLE_BLOCK
            % {
                "dim": _STROKE_MM[KIND_DIM],
                "witness": _STROKE_MM[KIND_WITNESS],
                "tick": _STROKE_MM[KIND_TICK],
                "leader": _STROKE_MM[KIND_LEADER],
            }
        )
    )
    lines.append('  <g id="A-DIM">')
    for primitive in primitives:
        if isinstance(primitive, Line):
            lines.append(
                '    <line class="%s" x1="%d" y1="%d" x2="%d" y2="%d"/>'
                % (
                    _CLASS.get(primitive.kind, "dim"),
                    sx(primitive.a[0]),
                    sy(primitive.a[1]),
                    sx(primitive.b[0]),
                    sy(primitive.b[1]),
                )
            )
        elif isinstance(primitive, Text):
            x, y = sx(primitive.position[0]), sy(primitive.position[1])
            rotate = (
                "" if primitive.rotation_deg == 0 else ' transform="rotate(-90 %d %d)"' % (x, y)
            )
            lines.append(
                '    <text x="%d" y="%d" font-size="%d"%s>%s</text>'
                % (x, y, primitive.height_mm, rotate, _escape(primitive.text))
            )
        else:  # pragma: no cover - the stream is lines and text by construction
            raise TypeError("unrenderable primitive %r" % type(primitive))
    lines.append("  </g>")
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


__all__ = ["render_svg"]
