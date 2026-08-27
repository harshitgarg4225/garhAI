"""Raster export: sheet PNGs and the WhatsApp preset. **Sizing pure; encode via Pillow.**

Phase 8's export list ends with *"PNG/WhatsApp preset"*, and §15 says every render and
share link carries **"Share on WhatsApp"**. That is not a decoration in India — a client
review happens in a WhatsApp thread, and a 4 MB A2 sheet that WhatsApp re-compresses to
mud is a real product failure.

The split in this module mirrors the one in the package: the *decisions* (target pixel
size at a DPI, long-edge caps, what fits in WhatsApp's limits, filenames in the pack) are
pure integer arithmetic and run and are tested anywhere; the *encode* needs Pillow and is
imported lazily inside the two functions that need it. Pillow is a base dependency of the
render worker (``services/render/imaging.py`` uses it) but is not installed on every
build machine, so the same lazy-import discipline as ``ezdxf`` applies.

Reuse, not reimplementation: :func:`encode` calls
:func:`services.render.imaging.encode_png`, which already fixes the compression level so
identical inputs give identical bytes. Two PNG encoders in one repo drift.

WHY WHATSAPP GETS ITS OWN PRESET
--------------------------------
WhatsApp re-encodes images it considers large, capping the long edge around 1600 px and
applying its own JPEG quality. Sending it a 7000 px sheet means *WhatsApp* chooses the
downscale, and it chooses badly for line drawings — dimension text turns to grey mush. So
the preset pre-scales to a long edge WhatsApp will pass through, at a DPI chosen so 2.5 mm
dimension text lands on enough pixels to stay readable. That arithmetic is in
:func:`text_legible_at`, which is the honest way to answer "will the architect's client be
able to read this".
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

__all__ = [
    "MAX_PIXELS",
    "PRESETS",
    "PngPreset",
    "dpi_for_long_edge",
    "encode",
    "pack_plan",
    "preset",
    "size_for_dpi",
    "text_legible_at",
]

#: Decompression / memory guard, matching services/render/imaging.py's own cap.
MAX_PIXELS = 40_000_000

#: A pixel needs to be about this many across for 2.5 mm text to stay readable.
#: Below ~6 px of cap height, antialiased line-drawing text stops being legible on a
#: phone screen; 2.5 mm at 96 dpi is 9.4 px, at 72 dpi it is 7.1 px.
_MIN_TEXT_PIXELS = 6


class PngPreset(tuple):
    """``(name, dpi, max_long_edge_px, background, description)``.

    ``dpi`` is the *target*; when it would exceed ``max_long_edge_px`` the effective DPI
    is reduced by :func:`size_for_dpi`, so a preset is a ceiling pair rather than a
    promise that both hold.
    """

    __slots__ = ()

    def __new__(
        cls,
        name: str,
        dpi: int,
        max_long_edge_px: int,
        background: str = "#ffffff",
        description: str = "",
    ) -> PngPreset:
        if dpi <= 0 or max_long_edge_px <= 0:
            raise ValueError("preset dpi and long edge must be positive")
        return super().__new__(
            cls, (name, int(dpi), int(max_long_edge_px), background, description)
        )

    @property
    def name(self) -> str:
        return self[0]

    @property
    def dpi(self) -> int:
        return self[1]

    @property
    def max_long_edge_px(self) -> int:
        return self[2]

    @property
    def background(self) -> str:
        return self[3]

    @property
    def description(self) -> str:
        return self[4]


#: The presets. Ordered smallest to largest so a UI list reads sensibly.
PRESETS: dict[str, PngPreset] = {
    preset_.name: preset_
    for preset_ in (
        PngPreset(
            "thumbnail",
            72,
            480,
            description="Sheet card in the Sheets tab. Not meant to be read.",
        ),
        PngPreset(
            "whatsapp",
            150,
            1600,
            description="§15 'Share on WhatsApp'. 1600 px long edge is what WhatsApp "
            "passes through without re-compressing a line drawing to mush.",
        ),
        PngPreset(
            "review",
            150,
            2400,
            description="On-screen review / email attachment. Dimension text readable " "at 100%.",
        ),
        PngPreset(
            "print",
            300,
            9000,
            description="Raster fallback for a print shop that cannot take vector. "
            "300 dpi on A2 is 7016x4961.",
        ),
    )
}
DEFAULT_PRESET = "review"


def preset(name: str) -> PngPreset:
    try:
        return PRESETS[name]
    except KeyError:
        raise KeyError(
            "%r is not a PNG preset. Expected one of: %s." % (name, ", ".join(sorted(PRESETS)))
        ) from None


def size_for_dpi(
    paper_width_mm: int, paper_height_mm: int, spec: PngPreset
) -> tuple[int, int, int]:
    """``(width_px, height_px, effective_dpi)`` for a paper size under a preset.

    Integer arithmetic with an explicit round-half-up, not ``round()``: pixel counts
    appear in filenames and test assertions, and banker's rounding on a .5 is a
    surprise nobody needs there.

    The effective DPI is reported, not hidden, because the caller often needs it —
    :func:`text_legible_at` reads it to answer whether the result is worth sending.
    """
    if paper_width_mm <= 0 or paper_height_mm <= 0:
        raise ValueError("paper size must be positive")

    def to_px(mm: int, dpi: int) -> int:
        # px = mm * dpi / 25.4, i.e. mm * dpi * 10 / 254, with halves rounded away from
        # zero. The *10 is not decoration: 25.4 has one decimal place, and dropping it
        # makes every raster come out a factor of ten too small — which is exactly what
        # this line did before test_size_for_dpi_is_exact_integer_arithmetic caught it.
        return (mm * dpi * 20 + 254) // 508

    dpi = spec.dpi
    width = to_px(paper_width_mm, dpi)
    height = to_px(paper_height_mm, dpi)
    long_edge = max(width, height)
    if long_edge > spec.max_long_edge_px:
        # Scale the DPI down rather than resampling afterwards: rendering straight to
        # the final size keeps line-drawing edges crisp, and a second resample of a
        # 1 px dimension line is what loses it.
        dpi = max(1, (spec.max_long_edge_px * 254) // (max(paper_width_mm, paper_height_mm) * 10))
        width = to_px(paper_width_mm, dpi)
        height = to_px(paper_height_mm, dpi)
    if width * height > MAX_PIXELS:
        raise ValueError(
            "preset %r on %dx%d mm paper would be %d pixels, over the %d cap"
            % (spec.name, paper_width_mm, paper_height_mm, width * height, MAX_PIXELS)
        )
    return (width, height, dpi)


def dpi_for_long_edge(paper_width_mm: int, paper_height_mm: int, long_edge_px: int) -> int:
    """The DPI that lands a paper size on exactly ``long_edge_px``, rounded down."""
    long_mm = max(paper_width_mm, paper_height_mm)
    if long_mm <= 0:
        raise ValueError("paper size must be positive")
    return max(1, (long_edge_px * 254) // (long_mm * 10))


def text_legible_at(dpi: int, text_height_paper_um: int = 2_500) -> bool:
    """Will text of this paper height survive rasterisation at this DPI?

    ``text_height_paper_um / 1000 mm`` at ``dpi`` gives a pixel height; below
    :data:`_MIN_TEXT_PIXELS` the answer is no. The Sheets UI uses this to decide whether
    to warn before a WhatsApp share of a dense sheet, which is much better than the
    client squinting and asking for it again.
    """
    pixels = (text_height_paper_um * dpi) // 25_400
    return pixels >= _MIN_TEXT_PIXELS


def pack_plan(
    sheets: Sequence[Any], *, preset_name: str = DEFAULT_PRESET
) -> tuple[dict[str, Any], ...]:
    """The ``png-pack`` contents: one entry per sheet, with filename and pixel size.

    Filenames are ``01-A-01-site-plan.png`` — a numeric prefix so an unzip lists them in
    submission order whatever the file manager's sort is, then the sheet number, then the
    kind. Slugs are ASCII and lower-case; a filename with a space or a Devanagari
    character is a support ticket on a Windows machine.
    """
    spec = preset(preset_name)
    entries: list[dict[str, Any]] = []
    for index, sheet in enumerate(sheets, start=1):
        frame = getattr(sheet, "frame", None)
        paper = getattr(frame, "paper", None)
        if paper is None:
            raise ValueError("sheet %r carries no frame.paper" % getattr(sheet, "id", "?"))
        width, height, dpi = size_for_dpi(int(paper.width_mm), int(paper.height_mm), spec)
        number = _slug(str(getattr(sheet, "number", "") or "sheet"))
        kind = _slug(str(getattr(sheet, "kind", "") or "sheet"))
        entries.append(
            {
                "filename": "%02d-%s-%s.png" % (index, number, kind),
                "sheetId": str(getattr(sheet, "id", "")),
                "widthPx": width,
                "heightPx": height,
                "dpi": dpi,
                "preset": spec.name,
                "textLegible": text_legible_at(dpi),
            }
        )
    return tuple(entries)


def _slug(value: str) -> str:
    out = []
    for char in value.lower():
        if char.isalnum():
            out.append(char)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-") or "sheet"


# ---------------------------------------------------------------------------
# The Pillow boundary
# ---------------------------------------------------------------------------
def encode(image: Any) -> bytes:
    """Deterministic PNG bytes, via the render service's shared encoder.

    Lazy import so the pure functions above stay usable without Pillow installed.
    """
    from services.render.imaging import encode_png

    return encode_png(image)


def blank_sheet_image(width_px: int, height_px: int, background: str = "#ffffff") -> Any:
    """A blank sheet-sized canvas, for compositing a rasterised sheet onto.

    The rasterisation itself is the PDF converter's job (:mod:`services.drawings.export.pdf`
    produces the vector, and ``pdftoppm``/``rsvg-convert`` can raster it). This helper
    exists so the pack builder can produce a correctly sized, correctly backed image
    even for a sheet whose raster step failed — with the failure recorded in the pack
    manifest rather than an absent file the client silently never sees.
    """
    from PIL import Image

    if width_px * height_px > MAX_PIXELS:
        raise ValueError("%dx%d exceeds the %d pixel cap" % (width_px, height_px, MAX_PIXELS))
    return Image.new("RGB", (int(width_px), int(height_px)), background)
