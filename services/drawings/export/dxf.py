"""Primitives -> DXF. **The only module in this package that touches ezdxf.**

    ... and DXF (ezdxf, mm units, layers A-WALL, A-WALL-PART, A-DOOR, A-WIND, A-STAIR,
    A-DIM, A-TEXT, A-AREA, A-TITL).  -- §7

F7-A's acceptance criterion: *"DXF opens clean in AutoCAD 2018+ (A-WALL/A-DOOR/A-DIM
layer convention)"*.

THIS MODULE CANNOT BE EXECUTED ON THE BUILD MACHINE
---------------------------------------------------
``ezdxf`` is pinned but not installed here (see the toolchain-gap row in
``DECISIONS.md``), so nothing below has ever run. That fact shaped the design more than
any style preference:

* **It is thin on purpose.** Every geometric decision — where a door arc starts, where a
  dimension's witness lines go, how a wall breaks around an opening — is made upstream
  in :mod:`services.drawings.render.primitives`, which is pure and fully tested. This
  module translates primitives into entities and does no geometry of its own beyond
  degrees-to-radians and a Y-axis note. Untested code that only translates is survivable;
  untested code that computes is not.
* **The import is lazy and the failure is loud.** ``ezdxf`` is imported inside
  :func:`write_dxf`, so importing this module for its constants costs nothing, and its
  absence raises :class:`EzdxfMissing` with the install command. There is deliberately
  no fallback path that writes a partial or plain-text file: a DXF that opens with
  missing walls is worse than a job that fails with a clear message, because the first
  one gets submitted.
* **Every entity goes on one of the nine §7 layers**, taken from
  :mod:`services.drawings.layers`, and every dimension uses the ``GARH-100`` DIMSTYLE
  from :mod:`services.drawings.dxf`. Those two modules are real today.

DXF coordinates are **model millimetres, Y up** — the model's own frame, not paper. A
DXF is a model, not a print: the recipient sets their own plot scale, and a DXF pre-
scaled to paper is the classic way to hand someone a drawing they cannot measure. The
sheet's scale reaches the file through the DIMSTYLE's ``dimscale`` (so dimension text
prints at 2.5 mm) and through paperspace, not by scaling the geometry.
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

from services.drawings.layers import LAYER_NAMES
from services.drawings.render.primitives import (
    HATCH_CROSS,
    HATCH_DIAGONAL,
    HATCH_EARTH,
    HATCH_SOLID,
    Arc,
    Circle,
    Dim,
    DrawingGroup,
    Hatch,
    Line,
    Polyline,
    Pt2,
    SheetDrawing,
    Text,
    dim_geometry,
)

__all__ = [
    "DXF_HATCH_PATTERNS",
    "EZDXF_INSTALL_HINT",
    "SHEET_GUTTER_MM",
    "TEXT_STYLE_NAME",
    "EzdxfMissing",
    "block_name",
    "dxf_structure",
    "sheet_extent_width_mm",
    "write_dxf",
    "write_dxf_bytes",
]

EZDXF_INSTALL_HINT = (
    "ezdxf is a base dependency of garh-services but is not installed in this "
    "interpreter. Install it with `pip install -e services/` (ezdxf is MIT licensed, "
    "pure Python, no compiled extensions)."
)

#: §7 hatch patterns mapped to the ANSI/ISO names ezdxf ships. ``SOLID`` is a real
#: DXF solid fill; the others are standard pattern names AutoCAD recognises, so a
#: reviewer's hatch dialog shows a name they know rather than an unresolved custom one.
DXF_HATCH_PATTERNS = {
    HATCH_SOLID: "SOLID",
    HATCH_DIAGONAL: "ANSI31",
    HATCH_CROSS: "ANSI37",
    HATCH_EARTH: "EARTH",
}

#: Text style created in the document for §7 text. ``STANDARD`` exists in every DXF;
#: naming ours keeps the height/width factor ours to set.
TEXT_STYLE_NAME = "GARH"


#: Model mm between sheet blocks in modelspace. 20 m clears any residential plot.
SHEET_GUTTER_MM = 20_000


def block_name(drawing: SheetDrawing, index: int) -> str:
    """A DXF block name for a sheet: ``SHEET-A-02A``. Deterministic, no counter.

    DXF block names cannot contain spaces or most punctuation, so the sheet number is
    upper-cased and reduced to ``A-Z0-9-``. ``index`` is only a fallback for a sheet with
    no number, and it makes the name unique without depending on content that might
    collide.
    """
    raw = str(getattr(drawing.sheet, "number", "") or "").upper()
    cleaned = "".join(ch if (ch.isalnum() or ch == "-") else "-" for ch in raw).strip("-")
    return "SHEET-%s" % (cleaned or "%02d" % (index + 1))


def sheet_extent_width_mm(drawing: SheetDrawing) -> int:
    """Model-space width of everything on a sheet, for the block layout gutter."""
    lo: Optional[int] = None
    hi: Optional[int] = None
    for group in drawing.groups:
        extent = group.extent_model_mm()
        if extent is None:
            continue
        lo = extent[0] if lo is None else min(lo, extent[0])
        hi = extent[2] if hi is None else max(hi, extent[2])
    if lo is None or hi is None:
        return 0
    return max(0, hi - lo)


class EzdxfMissing(RuntimeError):
    """``ezdxf`` is not importable. Carries the install command, never a fallback."""

    def __init__(self, cause: Optional[BaseException] = None) -> None:
        super().__init__(EZDXF_INSTALL_HINT)
        self.cause = cause


def _require_ezdxf() -> Any:
    try:
        import ezdxf
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise EzdxfMissing(exc) from exc
    return ezdxf


# ---------------------------------------------------------------------------
# A structural description of what the DXF will contain.
# ---------------------------------------------------------------------------
def dxf_structure(drawing: SheetDrawing) -> dict:
    """What :func:`write_dxf` will emit, as plain data. **Runs without ezdxf.**

    This exists so the DXF path is not completely unverifiable on a machine with no
    ``ezdxf``: the entity counts per layer, the layer set and the dimension count are all
    decided by pure code, and :mod:`services.drawings.tests.test_export` asserts them.
    It is also exactly the shape ``fixtures/sheets/README.md`` says DXF goldens are
    compared as ("compared structurally (entity/layer/DIMSTYLE table), never byte-wise —
    ezdxf writes a timestamp").

    Keys are sorted and values are counts, so this is stable enough to be a golden.
    """
    counts: dict = {}
    dimensions = 0
    for group in drawing.groups:
        for primitive in group.primitives:
            if isinstance(primitive, Dim):
                dimensions += len(primitive.chain.segments)
                entity = "DIMENSION"
            elif isinstance(primitive, Line):
                entity = "LINE"
            elif isinstance(primitive, Polyline):
                entity = "LWPOLYLINE"
            elif isinstance(primitive, Arc):
                entity = "ARC"
            elif isinstance(primitive, Circle):
                entity = "CIRCLE"
            elif isinstance(primitive, Text):
                entity = "TEXT"
            elif isinstance(primitive, Hatch):
                entity = "HATCH"
            else:  # pragma: no cover - Primitive is a closed union
                raise TypeError("no DXF entity for %s" % type(primitive).__name__)
            key = "%s/%s" % (primitive.layer, entity)
            counts[key] = counts.get(key, 0) + 1
    return {
        "sheetNumber": str(getattr(drawing.sheet, "number", "")),
        "sheetKind": str(getattr(drawing.sheet, "kind", "")),
        "scale": int(getattr(getattr(drawing.sheet, "scale", None), "denominator", 0) or 0),
        "layers": list(LAYER_NAMES),
        "layersUsed": list(drawing.layers_used()),
        "entities": {key: counts[key] for key in sorted(counts)},
        "dimensionSegments": dimensions,
        "chains": len(drawing.chains),
    }


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def _model_point(point: Pt2) -> Tuple[float, float]:
    """Model mm -> DXF coordinates. Identity, with the Y sign preserved.

    Written as a named function rather than inlined because it is the one place the
    "DXF is model space, Y up, no paper scaling" decision is expressed. If a future
    change ever needs paperspace geometry, it changes here and nowhere else.
    """
    return (float(point[0]), float(point[1]))


def _add_line(msp: Any, prim: Line) -> None:
    msp.add_line(
        _model_point(prim.a),
        _model_point(prim.b),
        dxfattribs={"layer": prim.layer, "linetype": _linetype(prim.style)},
    )


def _add_polyline(msp: Any, prim: Polyline) -> None:
    msp.add_lwpolyline(
        [_model_point(vertex) for vertex in prim.vertices],
        close=prim.closed,
        dxfattribs={"layer": prim.layer, "linetype": _linetype(prim.style)},
    )


def _add_arc(msp: Any, prim: Arc) -> None:
    # ezdxf angles are degrees CCW from +X, which is the same convention Arc uses, so
    # no conversion — and no chance of an inverted door swing.
    msp.add_arc(
        center=_model_point(prim.centre),
        radius=float(prim.radius_mm),
        start_angle=float(prim.start_deg),
        end_angle=float(prim.end_deg),
        dxfattribs={"layer": prim.layer, "linetype": _linetype(prim.style)},
    )


def _add_circle(msp: Any, prim: Circle) -> None:
    msp.add_circle(
        center=_model_point(prim.centre),
        radius=float(prim.radius_mm),
        dxfattribs={"layer": prim.layer, "linetype": _linetype(prim.style)},
    )


_DXF_ALIGN = {
    ("start", "baseline"): "LEFT",
    ("middle", "baseline"): "CENTER",
    ("end", "baseline"): "RIGHT",
    ("start", "middle"): "MIDDLE_LEFT",
    ("middle", "middle"): "MIDDLE_CENTER",
    ("end", "middle"): "MIDDLE_RIGHT",
    ("start", "hanging"): "TOP_LEFT",
    ("middle", "hanging"): "TOP_CENTER",
    ("end", "hanging"): "TOP_RIGHT",
}


def _add_text(msp: Any, prim: Text, *, scale_denominator: int) -> None:
    # Text height in DXF is model units. A 2.5 mm paper height at 1:100 is 250 mm of
    # model, which is why the scale has to reach this function.
    height = prim.height_paper_um * scale_denominator / 1000.0
    entity = msp.add_text(
        prim.text,
        dxfattribs={
            "layer": prim.layer,
            "style": TEXT_STYLE_NAME,
            "height": height,
            "rotation": float(prim.rotation_deg),
        },
    )
    # ezdxf 1.x rejects a string here (set_placement asserts the enum), so the
    # name from _DXF_ALIGN is looked up on TextEntityAlignment at call time —
    # imported lazily like everything else ezdxf in this module.
    from ezdxf.enums import TextEntityAlignment

    entity.set_placement(
        _model_point(prim.at),
        align=TextEntityAlignment[_DXF_ALIGN[(prim.anchor, prim.baseline)]],
    )


def _add_hatch(msp: Any, prim: Hatch) -> None:
    pattern = DXF_HATCH_PATTERNS[prim.pattern]
    hatch = msp.add_hatch(dxfattribs={"layer": prim.layer})
    if prim.pattern == HATCH_SOLID:
        hatch.set_solid_fill()
    else:
        hatch.set_pattern_fill(
            pattern,
            scale=float(prim.spacing_mm) / 100.0,
            angle=float(prim.angle_deg),
        )
    hatch.paths.add_polyline_path(
        [_model_point(vertex) for vertex in prim.outline], is_closed=True
    )
    for hole in prim.holes:
        # flags=0 makes the path a hole rather than an external boundary; without it a
        # stair well would come out filled.
        hatch.paths.add_polyline_path(
            [_model_point(vertex) for vertex in hole], is_closed=True, flags=0
        )


def _add_dim(msp: Any, prim: Dim, *, scale_denominator: int, dimstyle: str) -> None:
    """One native ``DIMENSION`` per chain segment.

    Per segment, not per chain: DXF linear dimensions measure between two points, and a
    chain of five segments is five dimensions sharing a dimension line. That is also how
    a CAD user expects to be able to grab and edit one of them.

    Base points come from the shared :func:`dim_geometry`, so the DXF dimension line
    lands exactly where the SVG drew it.
    """
    geometry = dim_geometry(prim, scale_denominator=scale_denominator)
    chain = prim.chain
    horizontal = chain.orientation == "horizontal"
    angle = 0.0 if horizontal else 90.0
    for segment in chain.segments:
        start = chain.origin_mm + segment.start_mm
        end = chain.origin_mm + segment.end_mm
        if horizontal:
            p1: Pt2 = (start, chain.offset_mm)
            p2: Pt2 = (end, chain.offset_mm)
        else:
            p1 = (chain.offset_mm, start)
            p2 = (chain.offset_mm, end)
        dimension = msp.add_linear_dim(
            base=_model_point(geometry.line_a),
            p1=_model_point(p1),
            p2=_model_point(p2),
            angle=angle,
            dimstyle=dimstyle,
            # §7: dim text is millimetres regardless of display units. The measurement
            # IS millimetres (dimlfac=1 in the DIMSTYLE), and an explicit override would
            # break the association that lets a CAD user re-measure.
            dxfattribs={"layer": prim.layer},
        )
        dimension.render()


_LINETYPES = {
    "solid": "CONTINUOUS",
    "dashed": "DASHED",
    "hidden": "HIDDEN",
    "centre": "CENTER",
}


def _linetype(style: str) -> str:
    return _LINETYPES.get(style, "CONTINUOUS")


def _write_group(msp: Any, group: DrawingGroup, *, dimstyle: str) -> None:
    from services.drawings.render.primitives import sort_by_layer

    scale = group.placement.scale_denominator
    for primitive in sort_by_layer(group.primitives):
        if isinstance(primitive, Hatch):
            _add_hatch(msp, primitive)
        elif isinstance(primitive, Line):
            _add_line(msp, primitive)
        elif isinstance(primitive, Polyline):
            _add_polyline(msp, primitive)
        elif isinstance(primitive, Arc):
            _add_arc(msp, primitive)
        elif isinstance(primitive, Circle):
            _add_circle(msp, primitive)
        elif isinstance(primitive, Text):
            _add_text(msp, primitive, scale_denominator=scale)
        elif isinstance(primitive, Dim):
            _add_dim(msp, primitive, scale_denominator=scale, dimstyle=dimstyle)
        else:  # pragma: no cover - Primitive is a closed union
            raise TypeError(
                "No DXF writer for primitive %s. A silently skipped primitive is a "
                "missing wall on a submission drawing." % type(primitive).__name__
            )


def write_dxf(drawings: Sequence[SheetDrawing], path: str) -> dict:
    """Write one DXF containing every sheet's geometry. Returns :func:`dxf_structure`-ish info.

    One file per project, layers intact, sheets laid out side by side — that is what an
    architect imports; a zip of nine DXFs is a worse deliverable and a worse thing to
    audit.

    Each sheet becomes a **BLOCK** named after its sheet number, inserted into
    modelspace at an offset. The offset therefore lives on the ``INSERT`` entity and
    never on the geometry, which keeps this module free of coordinate arithmetic (the
    whole point — see the module docstring) and gives the recipient nine named blocks
    they can explode, xref or re-place individually.

    Raises :class:`EzdxfMissing` when ``ezdxf`` is absent. Never writes a partial file.
    """
    _require_ezdxf()  # fail here, with the install command, before anything else
    from services.drawings.dxf import DIMSTYLE_NAME, audit, new_document

    scale = 100
    if drawings:
        first = getattr(getattr(drawings[0], "sheet", None), "scale", None)
        scale = int(getattr(first, "denominator", 100) or 100)

    document = new_document(scale_denominator=scale)
    if TEXT_STYLE_NAME not in document.styles:
        # width=0.85 is the condensed proportion architectural lettering uses; the
        # default 1.0 makes dimension text overrun short segments.
        document.styles.add(TEXT_STYLE_NAME, font="isocpeur.ttf", dxfattribs={"width": 0.85})
    msp = document.modelspace()

    # Sheets are laid out left to right with a generous gutter, so nothing overlaps
    # whatever the buildings' extents are.
    cursor_x = 0
    written: List[dict] = []
    for index, drawing in enumerate(drawings):
        structure = dxf_structure(drawing)
        written.append(structure)
        name = block_name(drawing, index)
        block = document.blocks.new(name=name)
        for group in drawing.groups:
            _write_group(block, group, dimstyle=DIMSTYLE_NAME)
        msp.add_blockref(name, insert=(float(cursor_x), 0.0), dxfattribs={"layer": "A-TITL"})
        cursor_x += sheet_extent_width_mm(drawing) + SHEET_GUTTER_MM

    clean, messages = audit(document)
    if not clean:
        raise RuntimeError(
            "The DXF failed ezdxf's audit and was not written: %s. §16 requires a clean "
            "audit — an unopenable DXF must fail here, not in the reviewer's AutoCAD."
            % "; ".join(messages[:5])
        )
    document.saveas(path)
    return {
        "path": path,
        "sheets": written,
        "auditMessages": list(messages),
        "dxfVersion": document.dxfversion,
    }


def write_dxf_bytes(drawings: Sequence[SheetDrawing]) -> bytes:
    """The same DXF as bytes, for the export job's blob upload.

    ezdxf writes text, so this goes through a temporary file rather than a string
    buffer: ``saveas`` is the only writer path ezdxf guarantees to produce a file a
    reviewer's CAD will open (it handles the encoding header), and reproducing it by
    hand is exactly the kind of cleverness this module avoids.
    """
    import os
    import tempfile

    handle, path = tempfile.mkstemp(suffix=".dxf")
    os.close(handle)
    try:
        write_dxf(drawings, path)
        with open(path, "rb") as stream:
            return stream.read()
    finally:
        try:
            os.unlink(path)
        except OSError:  # pragma: no cover
            pass
