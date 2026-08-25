"""DXF document setup (§7). **Units, layers and DIMSTYLE are real; export is Phase 8.**

The setup helper is short, correct and worth having now because it encodes three things
that are easy to get wrong and expensive to discover late in a golden-file diff:

1. **Units.** ``$INSUNITS = 4`` (millimetres) and ``$MEASUREMENT = 1`` (metric). Without
   these a DXF opens at 1/25.4 scale in some viewers and correctly in others, which is
   the worst kind of bug — it looks fine on the developer's machine.
2. **The nine §7 layers**, created from :mod:`services.drawings.layers` so the DXF and
   the SVG cannot disagree about what a layer is called.
3. **A DIMSTYLE that produces the drawing an Indian municipal reviewer expects**:
   dimension text in whole millimetres, no unit suffix, architectural tick marks rather
   than arrowheads, and ``dimlfac``/``dimscale`` set so a 1:100 sheet prints text at a
   readable size.

``ezdxf`` is imported lazily inside the functions. It is a base dependency, but keeping
the import local means importing this module for its constants costs nothing.
"""

from __future__ import annotations

from typing import Any

from services.common.logging import get_logger
from services.drawings.layers import LAYERS, REQUIRED_LINETYPES

log = get_logger("drawings.dxf")

PHASE = "Phase 8 (Drawings & exports)"

#: ezdxf/DXF header value for millimetres.
INSUNITS_MILLIMETRES = 4
#: 1 = metric.
MEASUREMENT_METRIC = 1
#: R2010 handles everything §7 needs (true colour, lineweights, modern DIMSTYLE) and is
#: old enough that LibreCAD and the free ODA viewer both read it cleanly.
DXF_VERSION = "R2010"

#: Our DIMSTYLE name. "GARH-100" reads as "Garh, for 1:100".
DIMSTYLE_NAME = "GARH-100"

#: Text height in **paper mm**. 2.5mm is the ISO 3098 standard and stays legible on an
#: A2 print; ``dimscale`` multiplies it up into model space.
DIM_TEXT_HEIGHT_PAPER_MM = 2.5


def new_document(*, scale_denominator: int = 100) -> Any:
    """Create a DXF document with §7 units, layers and DIMSTYLE already set up.

    ``scale_denominator`` is the sheet scale (100 for 1:100). It drives ``dimscale``, so
    dimension text and ticks come out the same physical size on paper at any scale.
    """
    import ezdxf

    document = ezdxf.new(DXF_VERSION, setup=True)
    setup_units(document)
    setup_layers(document)
    setup_dimstyle(document, scale_denominator=scale_denominator)
    return document


def setup_units(document: Any) -> None:
    """Millimetres, metric. The single most important header setting in the file."""
    document.header["$INSUNITS"] = INSUNITS_MILLIMETRES
    document.header["$MEASUREMENT"] = MEASUREMENT_METRIC
    # Decimal units, 0 decimal places: geometry is integer mm, so a ".0" would be noise.
    document.header["$LUNITS"] = 2
    document.header["$LUPREC"] = 0
    document.header["$AUNITS"] = 0
    document.header["$AUPREC"] = 0


def setup_layers(document: Any) -> None:
    """Create the nine §7 layers, plus the linetypes they reference."""
    for linetype in REQUIRED_LINETYPES:
        if linetype not in document.linetypes:
            # `setup=True` on ezdxf.new() loads the standard patterns; anything still
            # missing is a continuous fallback rather than a hard failure.
            document.linetypes.add(linetype, pattern=[1.0], description=linetype)

    for spec in LAYERS:
        if spec.name in document.layers:
            continue
        # description is set on the created layer, not passed to add():
        # ezdxf's LayerTable.add() takes no description kwarg (TypeError),
        # while the Layer object exposes it as a property.
        layer = document.layers.add(
            name=spec.name,
            color=spec.color,
            linetype=spec.linetype,
            lineweight=spec.lineweight,
        )
        layer.description = spec.description
    log.info("drawings.dxf.layers_ready", count=len(LAYERS))


def setup_dimstyle(document: Any, *, scale_denominator: int = 100) -> Any:
    """Create the §7 dimension style and return it.

    Every value here is a decision about what the printed sheet looks like:

    * ``dimscale`` — multiplies every paper-space size (text, ticks, gaps) into model
      space, so 2.5mm text on paper is 250mm in a 1:100 model.
    * ``dimlfac = 1`` — measurements are already in millimetres; no conversion.
    * ``dimdec = 0`` and ``dimzin = 8`` — whole millimetres, no trailing zeros. §7:
      "All dim text in mm on drawings regardless of display units."
    * ``dimtsz`` non-zero — architectural oblique ticks instead of arrowheads, which is
      what a building drawing uses.
    * ``dimtih/dimtoh = 0`` — text stays aligned with the dimension line.
    """
    if DIMSTYLE_NAME in document.dimstyles:
        return document.dimstyles.get(DIMSTYLE_NAME)

    style = document.dimstyles.add(DIMSTYLE_NAME)
    style.dxf.dimscale = float(scale_denominator)
    style.dxf.dimlfac = 1.0
    style.dxf.dimtxt = DIM_TEXT_HEIGHT_PAPER_MM
    style.dxf.dimdec = 0
    style.dxf.dimzin = 8
    style.dxf.dimtsz = 1.0            # oblique ticks, size in paper mm
    style.dxf.dimasz = 2.5            # arrow size (used by leaders)
    style.dxf.dimexe = 1.25           # extension line beyond the dimension line
    style.dxf.dimexo = 1.0            # gap between the object and its extension line
    style.dxf.dimgap = 1.0            # gap around the text
    style.dxf.dimtih = 0              # text aligned with the dimension line (inside)
    style.dxf.dimtoh = 0              # ... and outside
    style.dxf.dimtad = 1              # text above the dimension line
    style.dxf.dimclrd = 1             # dimension line: ACI red, matching A-DIM
    style.dxf.dimclre = 1
    style.dxf.dimclrt = 1
    log.info("drawings.dxf.dimstyle_ready", name=DIMSTYLE_NAME, scale=scale_denominator)
    return style


def audit(document: Any) -> tuple[bool, tuple[str, ...]]:
    """Run ``ezdxf.audit`` and return ``(clean, messages)``.

    §16 requires "``ezdxf.audit()`` clean" and a CI check that the DXF opens without
    errors. Wrapping it here means the export job and the test suite run the same
    check.
    """
    from ezdxf import audit as ezdxf_audit

    auditor = ezdxf_audit.Auditor(document)
    auditor.run()
    messages = tuple(
        str(entry) for entry in list(auditor.errors) + list(auditor.fixes)
    )
    return (not auditor.errors, messages)


def write_sheet(document: Any, sheet: Any, primitives: Any) -> None:
    """Write one sheet's projection primitives into a DXF modelspace.

    Deferred to %s: it consumes the 2D primitive stream that the plan/elevation/section
    projectors produce, and those projectors are Phase 8's main body of work. The
    document this writes into is already correct — units, layers and DIMSTYLE above are
    real today.
    """ % PHASE
    raise NotImplementedError(
        "write_sheet is implemented in %s, once the §7 projection pipeline "
        "(model → 2D primitives with layer tags) exists. new_document(), setup_units(), "
        "setup_layers(), setup_dimstyle() and audit() are already usable." % PHASE
    )


__all__ = [
    "DIMSTYLE_NAME",
    "DIM_TEXT_HEIGHT_PAPER_MM",
    "DXF_VERSION",
    "INSUNITS_MILLIMETRES",
    "MEASUREMENT_METRIC",
    "PHASE",
    "audit",
    "new_document",
    "setup_dimstyle",
    "setup_layers",
    "setup_units",
    "write_sheet",
]
