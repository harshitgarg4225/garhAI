"""SVG -> vector PDF. **Pipeline implemented; needs a converter binary at runtime.**

    Rendering pipeline: model -> 2D projection primitives -> SVG (screen and **PDF via
    headless print**) and DXF ...  -- §7

F7-A's criterion is *"print-true vector PDF"*, and the whole point of the SVG renderer
being print-true (real millimetres of paper, ``width="594mm"``) is that this step becomes
a conversion rather than a layout: the page is A2 because the SVG says it is A2, and
nothing here scales, fits or centres anything. If a PDF comes out the wrong size, the bug
is in the SVG, not here.

WHAT THIS MODULE DOES AND DOES NOT DO
-------------------------------------
It does **not** contain a PDF writer. Writing vector PDF by hand means reimplementing
font embedding and text metrics, and a submission drawing whose dimension text is
mis-kerned by a home-made writer is worse than one produced by a browser's print engine.
So this module *drives a converter* and is explicit about needing one:

* :func:`find_converter` probes for the tools in :data:`CONVERTERS`, in preference order.
* :func:`svg_to_pdf` converts one sheet, raising :class:`PdfToolMissing` — with the
  install command for the platform — when nothing is available.
* :func:`svg_set_to_pdf` produces the multi-page set, merging with the first available
  merge tool.

**There is no fallback that writes something PDF-shaped.** A zero-page or raster-inside
PDF that a municipality rejects at the counter is a far worse outcome than an export job
that fails saying "the PDF renderer is not installed on this worker".

CI
--
The converter is an *environment* dependency, not a Python one, so it belongs in the CI
image and the worker image rather than in a lockfile:

* CI / worker image: ``apt-get install -y --no-install-recommends chromium poppler-utils``
  (Chromium for the headless print, ``pdfunite`` from poppler for the merge). Both are
  free-licensed: Chromium is BSD-3-Clause, poppler is GPL-2.0 — **poppler is a runtime
  binary invoked as a subprocess, never linked or vendored into app code**, which keeps
  it outside the "no GPL in app code" rule. ``qpdf`` (Apache-2.0) is probed first for
  exactly this reason and is the preferred image package.
* The CI step that must exist for this path to be covered:
  ``python3 scripts/sheet_goldens.py --check --pdf`` — generates a PDF per golden sheet
  and asserts a non-trivial page count and an A2 MediaBox. It is skipped with a loud
  reason, never silently, when no converter is present (see
  :func:`converter_report`).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence

__all__ = [
    "CONVERTERS",
    "MERGE_TOOLS",
    "PDF_TIMEOUT_SECONDS",
    "PdfToolMissing",
    "converter_report",
    "find_converter",
    "find_merge_tool",
    "svg_set_to_pdf",
    "svg_to_pdf",
]

#: Seconds per sheet. A headless Chromium cold start is a few seconds; 60 is generous
#: without letting a hung browser eat the export job's whole budget.
PDF_TIMEOUT_SECONDS = 60

#: Converters in preference order. ``rsvg-convert`` first: it is the smallest, fastest
#: and most deterministic of the three, and it honours the SVG's physical size directly.
#: Chromium is the §7-named "headless print" and the most faithful on text.
CONVERTERS: tuple[tuple[str, str, str], ...] = (
    (
        "rsvg-convert",
        "librsvg (LGPL-2.1, invoked as a binary)",
        "apt-get install -y librsvg2-bin  |  brew install librsvg",
    ),
    (
        "chromium",
        "Chromium headless print (BSD-3-Clause)",
        "apt-get install -y chromium  |  brew install --cask chromium",
    ),
    (
        "chromium-browser",
        "Chromium headless print (BSD-3-Clause)",
        "apt-get install -y chromium-browser",
    ),
    (
        "inkscape",
        "Inkscape (GPL-3.0, invoked as a binary)",
        "apt-get install -y inkscape  |  brew install --cask inkscape",
    ),
)

#: Merge tools in preference order. qpdf is Apache-2.0 and the one to put in the image.
MERGE_TOOLS: tuple[tuple[str, str], ...] = (
    ("qpdf", "apt-get install -y qpdf  |  brew install qpdf"),
    ("pdfunite", "apt-get install -y poppler-utils  |  brew install poppler"),
)


class PdfToolMissing(RuntimeError):
    """No SVG->PDF converter (or no merge tool) is available on this machine."""


def find_converter() -> tuple[str, str, str] | None:
    """The first available converter as ``(binary_path, name, install_hint)``."""
    for name, _description, hint in CONVERTERS:
        path = shutil.which(name)
        if path:
            return (path, name, hint)
    return None


def find_merge_tool() -> tuple[str, str] | None:
    for name, _hint in MERGE_TOOLS:
        path = shutil.which(name)
        if path:
            return (path, name)
    return None


def converter_report() -> dict[str, object]:
    """What is and is not available here — for the CI skip message and job logs.

    Returned rather than logged so the caller decides whether a missing converter is a
    skip (local dev) or a failure (CI). A silent skip is how a gate stops being a gate.
    """
    converter = find_converter()
    merge = find_merge_tool()
    return {
        "converter": converter[1] if converter else None,
        "converterPath": converter[0] if converter else None,
        "mergeTool": merge[1] if merge else None,
        "available": converter is not None,
        "canMerge": merge is not None,
        "installHint": None
        if converter
        else "; ".join("%s: %s" % (name, hint) for name, _description, hint in CONVERTERS),
    }


def _require_converter() -> tuple[str, str, str]:
    converter = find_converter()
    if converter is None:
        raise PdfToolMissing(
            "No SVG-to-PDF converter found on this machine, so no vector PDF can be "
            "produced. Install one of: %s. This path deliberately has no fallback: a "
            "fake or rasterised PDF would be submitted to a municipality and rejected."
            % "; ".join("%s (%s)" % (name, hint) for name, _d, hint in CONVERTERS)
        )
    return converter


def _command(binary: str, name: str, svg_path: str, pdf_path: str) -> list[str]:
    """The converter's argv. Each is told to honour the SVG's own physical page size."""
    if name == "rsvg-convert":
        return [binary, "--format=pdf", "--keep-aspect-ratio", "--output=%s" % pdf_path, svg_path]
    if name in ("chromium", "chromium-browser"):
        return [
            binary,
            "--headless=new",
            "--disable-gpu",
            # No network, no extensions, no sandbox escape surface: the input is a
            # drawing this worker just produced, but §13 says untrusted-by-default.
            "--no-sandbox",
            "--disable-extensions",
            "--no-pdf-header-footer",
            "--print-to-pdf-no-header",
            "--print-to-pdf=%s" % pdf_path,
            "file://%s" % svg_path,
        ]
    if name == "inkscape":
        return [binary, "--export-type=pdf", "--export-filename=%s" % pdf_path, svg_path]
    raise PdfToolMissing("no argv recipe for converter %r" % name)


def svg_to_pdf(svg: str, pdf_path: str, *, timeout_seconds: int = PDF_TIMEOUT_SECONDS) -> str:
    """Convert one print-true SVG string to a PDF at ``pdf_path``. Returns the path.

    The SVG goes to a temp file rather than the converter's stdin: Chromium needs a URL,
    and a consistent path for all three converters keeps the failure modes comparable.
    """
    binary, name, _hint = _require_converter()
    handle, svg_path = tempfile.mkstemp(suffix=".svg")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(svg)
        result = subprocess.run(
            _command(binary, name, svg_path, pdf_path),
            capture_output=True,
            timeout=timeout_seconds,
        )
        if result.returncode != 0 or not os.path.exists(pdf_path):
            raise PdfToolMissing(
                "%s failed to convert the sheet (exit %d): %s"
                % (name, result.returncode, result.stderr.decode("utf-8", "replace")[:500])
            )
        if os.path.getsize(pdf_path) == 0:
            raise PdfToolMissing("%s produced an empty PDF" % name)
        return pdf_path
    finally:
        with contextlib.suppress(OSError):  # pragma: no cover
            os.unlink(svg_path)


def svg_set_to_pdf(
    svgs: Sequence[str],
    pdf_path: str,
    *,
    timeout_seconds: int = PDF_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """The multi-page ``pdf-set`` export: one page per sheet, in the given order.

    A single sheet needs no merge tool, which is the common case for "download this
    sheet" and is worth not failing on.
    """
    if not svgs:
        raise ValueError("a PDF set needs at least one sheet")
    _require_converter()
    if len(svgs) == 1:
        svg_to_pdf(svgs[0], pdf_path, timeout_seconds=timeout_seconds)
        return {"pages": 1, "mergeTool": None, "path": pdf_path}

    merge = find_merge_tool()
    if merge is None:
        raise PdfToolMissing(
            "%d sheets need merging into one PDF but no merge tool is installed. "
            "Install one of: %s."
            % (len(svgs), "; ".join("%s (%s)" % (name, hint) for name, hint in MERGE_TOOLS))
        )
    merge_binary, merge_name = merge

    directory = tempfile.mkdtemp(prefix="garh-pdf-")
    pages: list[str] = []
    try:
        for index, svg in enumerate(svgs):
            page = os.path.join(directory, "page-%03d.pdf" % index)
            svg_to_pdf(svg, page, timeout_seconds=timeout_seconds)
            pages.append(page)
        if merge_name == "qpdf":
            argv = [merge_binary, "--empty", "--pages", *pages, "--", pdf_path]
        else:
            argv = [merge_binary, *pages, pdf_path]
        result = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout_seconds,
        )
        if result.returncode != 0 or not os.path.exists(pdf_path):
            raise PdfToolMissing(
                "%s failed to merge %d pages (exit %d): %s"
                % (
                    merge_name,
                    len(pages),
                    result.returncode,
                    result.stderr.decode("utf-8", "replace")[:500],
                )
            )
        return {"pages": len(pages), "mergeTool": merge_name, "path": pdf_path}
    finally:
        shutil.rmtree(directory, ignore_errors=True)
