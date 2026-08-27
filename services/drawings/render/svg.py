"""Primitives -> SVG. **Fully implemented, pure, no dependency, byte-deterministic.**

§7's screen and PDF path:

    ... -> SVG (screen + PDF via headless print) and DXF (...)

Three properties this module guarantees, each for a concrete reason:

**Print-true.** The document is sized in real millimetres of paper
(``width="594mm" height="420mm"``) with a ``viewBox`` in the same unit, so one SVG user
unit is one millimetre on the printed sheet. A 1:100 plan measures 1/100 of the building
with a ruler on the paper, which is what a municipal reviewer does. It also means the
PDF path (:mod:`services.drawings.export.pdf`) inherits correct dimensions with no
scaling step to get wrong — the PDF page *is* A2 because the SVG said so.

**Byte-deterministic.** No timestamps, no random ids, no dict iteration order, no float
formatting. Every coordinate arrives as an integer number of paper micrometres and is
formatted by :func:`_mm` as a fixed 3-decimal string, so the same model produces the
same bytes on every machine and every Python version. §7 requires it ("byte-diff, SVG
normalized") and §16 makes a failing golden a build failure. The only "normalisation"
the golden harness performs is line-ending and trailing-whitespace — because there is
nothing else to normalise away.

**Sanitary.** Every string goes through :func:`~services.drawings.render.sanitize.escape_text`
on the way in, and the finished document goes through
:func:`~services.drawings.render.sanitize.assert_sanitary` on the way out (§13).

Fonts: the only family named is a generic CSS stack ending in ``sans-serif``. No
``@font-face``, no remote font — a submission drawing must render identically on a
machine that has never heard of this project, and a missing webfont silently reflows
every dimension label.
"""

from __future__ import annotations

from collections.abc import Sequence

from services.drawings.layers import LAYER_NAMES, LAYERS_BY_NAME
from services.drawings.render.primitives import (
    DASH_PATTERNS_PAPER_UM,
    HATCH_CROSS,
    HATCH_EARTH,
    HATCH_SOLID,
    Arc,
    Circle,
    Dim,
    DrawingGroup,
    Hatch,
    Line,
    Placement,
    Polyline,
    Primitive,
    Pt2,
    SheetDrawing,
    Text,
    dim_geometry,
    div_round,
    layer_lineweight_paper_um,
    sort_by_layer,
)
from services.drawings.render.sanitize import assert_sanitary, escape_text, safe_id

__all__ = [
    "FONT_STACK",
    "MIN_STROKE_PAPER_UM",
    "SvgRenderError",
    "normalize_svg",
    "render_group_svg",
    "render_sheet_svg",
]


class SvgRenderError(ValueError):
    """The primitives could not be rendered — always a producer bug, never user input."""


#: Generic only. See the module docstring: a named webfont would be a remote dependency
#: and a silent reflow risk on any machine that lacks it.
FONT_STACK = "Helvetica, Arial, sans-serif"

#: Hairlines below this vanish on a 300 dpi print (0.08 mm is roughly one dot).
MIN_STROKE_PAPER_UM = 80

#: ACI -> print colour. Municipal sheets print black; the ACI colours in
#: :mod:`services.drawings.layers` are a CAD *screen* convention, not ink. Mapping them
#: to near-black greys keeps the layer distinction visible on screen while printing as
#: a proper monochrome drawing, which is what gets submitted.
_ACI_TO_HEX = {
    1: "#1a1a1a",  # A-DIM
    2: "#3a3a3a",  # A-AREA
    3: "#1a1a1a",  # A-DOOR
    4: "#1a1a1a",  # A-WIND
    5: "#111111",  # A-TEXT
    6: "#1a1a1a",  # A-STAIR
    7: "#000000",  # A-WALL, A-TITL
    8: "#555555",  # A-WALL-PART
}


def _mm(paper_um: int) -> str:
    """Paper micrometres -> a fixed 3-decimal millimetre string. Exact, no float.

    ``91500 -> "91.500"``. Built by integer arithmetic so there is no float repr in the
    output at all: ``str(91.5)`` is stable today but the guarantee this file needs is
    stronger than "stable today".
    """
    sign = "-" if paper_um < 0 else ""
    magnitude = -paper_um if paper_um < 0 else paper_um
    whole, fraction = divmod(magnitude, 1000)
    return "%s%d.%03d" % (sign, whole, fraction)


def _pt(placement: Placement, point: Pt2) -> tuple[str, str]:
    x_um, y_um = placement.to_paper_um(point)
    return (_mm(x_um), _mm(y_um))


def _stroke_hex(layer_name: str) -> str:
    spec = LAYERS_BY_NAME[layer_name]
    return _ACI_TO_HEX.get(spec.color, "#000000")


def _stroke_width_um(layer_name: str) -> int:
    return max(MIN_STROKE_PAPER_UM, layer_lineweight_paper_um(layer_name))


def _dash_attr(style: str) -> str:
    """``stroke-dasharray`` in paper mm, or an empty string for continuous lines.

    Dash lengths are paper-space (a dashed line looks the same at 1:50 and 1:200),
    which is why they are not divided by the scale.
    """
    pattern = DASH_PATTERNS_PAPER_UM.get(style, ())
    if not pattern:
        return ""
    return ' stroke-dasharray="%s"' % " ".join(_mm(value) for value in pattern)


# ---------------------------------------------------------------------------
# Per-primitive emission. Attribute order is fixed by these functions and is
# part of the golden contract — do not reorder without regenerating goldens.
# ---------------------------------------------------------------------------
def _emit_line(out: list[str], placement: Placement, prim: Line) -> None:
    x1, y1 = _pt(placement, prim.a)
    x2, y2 = _pt(placement, prim.b)
    out.append(
        '<line x1="%s" y1="%s" x2="%s" y2="%s"%s/>' % (x1, y1, x2, y2, _dash_attr(prim.style))
    )


def _emit_polyline(out: list[str], placement: Placement, prim: Polyline) -> None:
    coords = " ".join("%s,%s" % _pt(placement, vertex) for vertex in prim.vertices)
    tag = "polygon" if prim.closed else "polyline"
    out.append('<%s points="%s" fill="none"%s/>' % (tag, coords, _dash_attr(prim.style)))


def _arc_endpoint(centre: Pt2, radius_mm: int, degrees: int) -> Pt2:
    """A point on a circle at an integer degree, in integer model mm.

    Integer-only trig via a 90-entry sine table would be overkill; instead the sin/cos
    are computed in floating point and *immediately* rounded to integer millimetres by
    :func:`div_round`, so what leaves this function — and therefore what reaches the
    output file — is an integer. Two hosts agreeing on ``math.sin`` to 1e-15 is enough
    when the result is rounded to a whole millimetre.
    """
    import math

    radians = math.radians(degrees % 360)
    cx, cy = centre
    return (
        cx + div_round(int(round(math.cos(radians) * radius_mm * 1000)), 1000),
        cy + div_round(int(round(math.sin(radians) * radius_mm * 1000)), 1000),
    )


def _emit_arc(out: list[str], placement: Placement, prim: Arc) -> None:
    sweep_deg = (prim.end_deg - prim.start_deg) % 360
    if sweep_deg == 0:
        # A full circle cannot be expressed as one SVG arc segment.
        _emit_circle(out, placement, Circle(prim.centre, prim.radius_mm, prim.layer, prim.style))
        return
    start = _arc_endpoint(prim.centre, prim.radius_mm, prim.start_deg)
    end = _arc_endpoint(prim.centre, prim.radius_mm, prim.end_deg)
    x1, y1 = _pt(placement, start)
    x2, y2 = _pt(placement, end)
    radius = _mm(placement.length_to_paper_um(prim.radius_mm))
    large_arc = 1 if sweep_deg > 180 else 0
    # The model sweeps CCW; the Y flip makes that clockwise on paper, hence sweep-flag 0
    # when flipped. Getting this wrong mirrors every door swing on the sheet.
    sweep_flag = 0 if placement.flip_y else 1
    out.append(
        '<path d="M %s %s A %s %s 0 %d %d %s %s" fill="none"%s/>'
        % (x1, y1, radius, radius, large_arc, sweep_flag, x2, y2, _dash_attr(prim.style))
    )


def _emit_circle(out: list[str], placement: Placement, prim: Circle) -> None:
    cx, cy = _pt(placement, prim.centre)
    radius = _mm(placement.length_to_paper_um(prim.radius_mm))
    out.append(
        '<circle cx="%s" cy="%s" r="%s" fill="none"%s/>' % (cx, cy, radius, _dash_attr(prim.style))
    )


_ANCHOR_TO_SVG = {"start": "start", "middle": "middle", "end": "end"}
_BASELINE_TO_SVG = {
    "baseline": "alphabetic",
    "middle": "central",
    "hanging": "hanging",
}


def _emit_text(out: list[str], placement: Placement, prim: Text) -> None:
    x, y = _pt(placement, prim.at)
    height = _mm(prim.height_paper_um)
    attributes = [
        'x="%s"' % x,
        'y="%s"' % y,
        'font-size="%s"' % height,
        'text-anchor="%s"' % _ANCHOR_TO_SVG[prim.anchor],
        'dominant-baseline="%s"' % _BASELINE_TO_SVG[prim.baseline],
    ]
    if prim.bold:
        attributes.append('font-weight="600"')
    if prim.rotation_deg:
        # Rotate about the insertion point. Negated because the Y flip already turned
        # the coordinate system over: a 90° CCW label in the model reads bottom-to-top
        # on paper, which is -90 in SVG's Y-down frame. This is the convention every
        # vertical dimension run on the sheet depends on.
        attributes.append('transform="rotate(%d %s %s)"' % (-prim.rotation_deg, x, y))
    out.append("<text %s>%s</text>" % (" ".join(attributes), escape_text(prim.text)))


def _hatch_pattern_id(prim: Hatch, placement: Placement) -> str:
    """A pattern id derived only from the hatch's own parameters.

    Content-derived, never a counter: two identical hatches must reuse one ``<pattern>``
    and the id must not depend on how many hatches were emitted before it, or inserting
    a wall at the top of a plan renames every pattern below it and the whole golden
    diffs.
    """
    return "h-%s-%d-%d-%d" % (
        prim.pattern,
        prim.spacing_mm,
        prim.angle_deg % 360,
        placement.scale_denominator,
    )


def _emit_hatch_defs(out: list[str], groups: Sequence[DrawingGroup]) -> None:
    """Emit one ``<pattern>`` per distinct hatch, sorted by id for stable bytes."""
    seen = {}
    for group in groups:
        for primitive in group.primitives:
            if isinstance(primitive, Hatch) and primitive.pattern != HATCH_SOLID:
                seen[_hatch_pattern_id(primitive, group.placement)] = (
                    primitive,
                    group.placement,
                )
    if not seen:
        return
    out.append("<defs>")
    for pattern_id in sorted(seen):
        primitive, placement = seen[pattern_id]
        step = max(1, placement.length_to_paper_um(primitive.spacing_mm))
        size = _mm(step)
        stroke = _stroke_hex(primitive.layer)
        width = _mm(max(MIN_STROKE_PAPER_UM, 60))
        out.append(
            '<pattern id="%s" width="%s" height="%s" patternUnits="userSpaceOnUse" '
            'patternTransform="rotate(%d)">'
            % (pattern_id, size, size, -(primitive.angle_deg % 360))
        )
        out.append(
            '<line x1="0" y1="0" x2="0" y2="%s" stroke="%s" stroke-width="%s"/>'
            % (size, stroke, width)
        )
        if primitive.pattern in (HATCH_CROSS, HATCH_EARTH):
            out.append(
                '<line x1="0" y1="0" x2="%s" y2="0" stroke="%s" stroke-width="%s"/>'
                % (size, stroke, width)
            )
        out.append("</pattern>")
    out.append("</defs>")


def _ring_path(placement: Placement, ring: Sequence[Pt2]) -> str:
    parts = []
    for index, vertex in enumerate(ring):
        x, y = _pt(placement, vertex)
        parts.append("%s %s %s" % ("M" if index == 0 else "L", x, y))
    parts.append("Z")
    return " ".join(parts)


def _emit_hatch(out: list[str], placement: Placement, prim: Hatch) -> None:
    path = _ring_path(placement, prim.outline)
    for hole in prim.holes:
        path += " " + _ring_path(placement, hole)
    if prim.pattern == HATCH_SOLID:
        fill = _stroke_hex(prim.layer)
    else:
        fill = "url(#%s)" % _hatch_pattern_id(prim, placement)
    # fill-rule evenodd so `holes` actually punch through (stair wells, shafts).
    out.append('<path d="%s" fill="%s" fill-rule="evenodd" stroke="none"/>' % (path, fill))


def _emit_dim(out: list[str], placement: Placement, prim: Dim) -> None:
    """Explode a chain via the shared :func:`dim_geometry` and draw it.

    The geometry comes from the shared function, not from here, so the DXF writer's
    native ``DIMENSION`` entities line up with these lines to the millimetre.
    """
    geometry = dim_geometry(prim, scale_denominator=placement.scale_denominator)
    _emit_line(out, placement, Line(geometry.line_a, geometry.line_b, prim.layer))
    for tick in geometry.ticks:
        _emit_line(out, placement, Line(tick.witness_a, tick.witness_b, prim.layer))
    for at, label, rotation in geometry.labels:
        _emit_text(
            out,
            placement,
            Text(
                at=at,
                text=label,
                layer=prim.layer,
                height_paper_um=prim.text_height_paper_um,
                anchor="middle",
                baseline="baseline",
                rotation_deg=rotation,
            ),
        )


_EMITTERS = (
    (Hatch, _emit_hatch),
    (Line, _emit_line),
    (Polyline, _emit_polyline),
    (Arc, _emit_arc),
    (Circle, _emit_circle),
    (Text, _emit_text),
    (Dim, _emit_dim),
)


def _emit_primitive(out: list[str], placement: Placement, prim: Primitive) -> None:
    for kind, emitter in _EMITTERS:
        if isinstance(prim, kind):
            emitter(out, placement, prim)  # type: ignore[arg-type]
            return
    raise SvgRenderError(
        "No SVG emitter for primitive %s. Every kind in "
        "services.drawings.render.primitives.PRIMITIVE_KINDS needs one — a silently "
        "skipped primitive is a missing wall on a submission drawing." % type(prim).__name__
    )


# ---------------------------------------------------------------------------
# Group and sheet
# ---------------------------------------------------------------------------
def render_group_svg(group: DrawingGroup) -> str:
    """One drawing group as an SVG fragment: a ``<g>`` per layer, in LAYERS order.

    Layer grouping is not decoration — it is how the layer convention survives into
    the SVG, so the web viewer can toggle A-DIM off and the PDF inherits a structure a
    reviewer recognises. The fragment carries no ``<svg>`` wrapper: sheets place several
    groups, and each nests in the same document.
    """
    out: list[str] = []
    ordered = sort_by_layer(group.primitives)
    by_layer = {}
    for primitive in ordered:
        by_layer.setdefault(primitive.layer, []).append(primitive)

    out.append('<g id="g-%s">' % safe_id(group.id))
    for layer_name in LAYER_NAMES:
        primitives = by_layer.get(layer_name)
        if not primitives:
            continue
        out.append(
            '<g class="layer" data-layer="%s" stroke="%s" stroke-width="%s" '
            'fill="none" stroke-linecap="butt" stroke-linejoin="miter">'
            % (
                layer_name,
                _stroke_hex(layer_name),
                _mm(_stroke_width_um(layer_name)),
            )
        )
        # Text needs fill and no stroke; everything else is the reverse. Setting it on
        # a nested group keeps the per-element attribute count (and the byte count) down.
        text_primitives = [p for p in primitives if isinstance(p, Text)]
        other_primitives = [p for p in primitives if not isinstance(p, Text)]
        for primitive in other_primitives:
            _emit_primitive(out, group.placement, primitive)
        if text_primitives:
            out.append(
                '<g font-family="%s" fill="%s" stroke="none">'
                % (FONT_STACK, _stroke_hex(layer_name))
            )
            for primitive in text_primitives:
                _emit_primitive(out, group.placement, primitive)
            out.append("</g>")
        out.append("</g>")
    out.append("</g>")
    return "\n".join(out)


def render_sheet_svg(drawing: SheetDrawing) -> str:
    """A complete, print-true, sanitised SVG document for one sheet.

    The returned string ends with a newline and contains no timestamp, no generator
    version and no random id, so two runs over the same model are byte-identical. That
    is the property the §16 golden gate rests on.
    """
    sheet = drawing.sheet
    frame = getattr(sheet, "frame", None)
    paper = getattr(frame, "paper", None)
    if paper is None:
        raise SvgRenderError(
            "SheetDrawing.sheet must carry frame.paper (a services.drawings.sheets."
            "PaperSize); got %r." % type(sheet).__name__
        )
    width_mm = int(paper.width_mm)
    height_mm = int(paper.height_mm)

    out: list[str] = []
    out.append(
        '<svg xmlns="http://www.w3.org/2000/svg" width="%dmm" height="%dmm" '
        'viewBox="0 0 %d %d" version="1.1">' % (width_mm, height_mm, width_mm, height_mm)
    )
    # Provenance as a comment, deliberately containing nothing volatile: sheet id,
    # kind and scale only. No date, no version, no host.
    scale = getattr(sheet, "scale", None)
    scale_label = getattr(scale, "label", "") if scale is not None else ""
    out.append(
        "<!-- garh-sheet %s %s %s -->"
        % (
            escape_text(str(getattr(sheet, "id", ""))),
            escape_text(str(getattr(sheet, "kind", ""))),
            escape_text(str(scale_label)),
        )
    )
    out.append(
        "<title>%s</title>"
        % escape_text("%s %s" % (getattr(sheet, "number", ""), getattr(sheet, "title", "")))
    )
    # An opaque white ground: a transparent sheet prints as whatever is behind it and
    # reads as grey in a dark-mode browser, which is not what a drawing looks like.
    out.append(
        '<rect x="0" y="0" width="%d" height="%d" fill="#ffffff" stroke="none"/>'
        % (width_mm, height_mm)
    )
    _emit_hatch_defs(out, drawing.groups)
    for group in drawing.groups:
        out.append(render_group_svg(group))
        if group.label and group.label_at is not None:
            label_group = DrawingGroup(
                id=group.id + "-label",
                placement=group.placement,
                primitives=(
                    Text(
                        at=group.label_at,
                        text=group.label,
                        layer="A-TEXT",
                        height_paper_um=3_500,
                        anchor="middle",
                        baseline="hanging",
                        bold=True,
                    ),
                ),
            )
            out.append(render_group_svg(label_group))
    out.append("</svg>")

    svg = "\n".join(out) + "\n"
    assert_sanitary(svg)
    return svg


def normalize_svg(svg: str) -> str:
    """Normalise an SVG for byte comparison. **This is the whole normalisation.**

    Exactly three things happen, and they are documented here because the golden
    harness's credibility depends on nobody wondering what got quietly rewritten:

    1. CRLF and lone CR become LF (a Windows checkout must not fail the gate).
    2. Trailing whitespace is stripped from each line.
    3. The file ends with exactly one newline.

    Nothing else. No id rewriting, no timestamp stripping, no attribute sorting —
    :func:`render_sheet_svg` emits no timestamps, no generated ids and no
    order-dependent attributes in the first place, so there is nothing to launder. A
    normaliser that erased ids would also erase a real regression in which every id
    changed.
    """
    text = svg.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"
