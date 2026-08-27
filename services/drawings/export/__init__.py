"""Sheet exports (§7 / Phase 8): the four ``EXPORT_KINDS`` the API offers.

``garh_api.queue.EXPORT_KINDS`` is ``("pdf-set", "dxf", "gltf", "png-pack")``, and
:data:`EXPORTERS` below maps each to what produces it:

=============  =============================  =================================  ==========
kind           module                         needs                              runs here
=============  =============================  =================================  ==========
``pdf-set``    :mod:`.pdf`                    an SVG->PDF binary (Chromium/rsvg)  no
``dxf``        :mod:`.dxf`                    ``ezdxf``                           no
``gltf``       :mod:`.gltf`                   nothing                             **yes**
``png-pack``   :mod:`.png`                    Pillow (encode only)                partly
=============  =============================  =================================  ==========

"Runs here" means: on a bare Python 3.9 with no third-party packages, which is the build
machine this repo is being written on. That column is why the package is shaped the way
it is — every decision that can be made without a dependency has been pushed up into
:mod:`services.drawings.render.primitives` and the pure halves of these modules, so the
untestable surface is as small as it can be. The glTF exporter needs nothing at all and is
therefore tested end-to-end, byte for byte.

Each module fails loudly and specifically when its dependency is absent
(:class:`~.dxf.EzdxfMissing`, :class:`~.pdf.PdfToolMissing`) and none of them has a
fallback that writes a plausible-looking broken file. That is the rule for this package:
an export either produces the real artefact or says exactly what is missing.
"""

from __future__ import annotations

__all__ = [
    "EXPORTERS",
    "EXPORT_KINDS",
    "requirements_for",
]

#: Mirrors ``garh_api.queue.EXPORT_KINDS`` and ``services.drawings.handler.EXPORT_KINDS``.
#: Kept as a literal rather than imported, for the same reason those two are: this
#: package must not import the API, and a third copy that drifts is caught by the
#: assertion below.
EXPORT_KINDS = ("pdf-set", "dxf", "gltf", "png-pack")

#: kind -> ``(module, entry point, hard requirement or None)``.
EXPORTERS: dict[str, dict[str, object]] = {
    "pdf-set": {
        "module": "services.drawings.export.pdf",
        "entry": "svg_set_to_pdf",
        "requires": "an SVG-to-PDF converter binary (rsvg-convert, chromium or inkscape)",
        "extension": "pdf",
        "contentType": "application/pdf",
    },
    "dxf": {
        "module": "services.drawings.export.dxf",
        "entry": "write_dxf_bytes",
        "requires": "ezdxf",
        "extension": "dxf",
        "contentType": "application/dxf",
    },
    "gltf": {
        "module": "services.drawings.export.gltf",
        "entry": "write_glb_bytes",
        "requires": None,
        "extension": "glb",
        "contentType": "model/gltf-binary",
    },
    "png-pack": {
        "module": "services.drawings.export.png",
        "entry": "pack_plan",
        "requires": "Pillow (for the encode step only; sizing is dependency-free)",
        "extension": "zip",
        "contentType": "application/zip",
    },
}

# The API's mapping and this one must agree on every kind, or an export job is accepted
# and then has nowhere to go.
assert tuple(sorted(EXPORTERS)) == tuple(sorted(EXPORT_KINDS)), (
    "EXPORTERS and EXPORT_KINDS disagree: %s vs %s" % (sorted(EXPORTERS), sorted(EXPORT_KINDS))
)


def requirements_for(kind: str) -> dict[str, object]:
    """What an export kind needs, for a pre-flight check or an honest error message."""
    try:
        return dict(EXPORTERS[kind])
    except KeyError:
        raise KeyError(
            "%r is not an export kind. Expected one of: %s." % (kind, ", ".join(sorted(EXPORTERS)))
        ) from None
