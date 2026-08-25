"""Shared Pillow helpers for the render providers.

Both providers need the same three things — decode an uploaded PNG safely, fit it to
the requested frame without distorting the building, and encode deterministically — so
they live here rather than one importing the other's private functions.

Pillow only. No ML dependency, importable on the mock path.
"""

from __future__ import annotations

import io

from PIL import Image

from services.common.errors import PermanentError

#: Decompression-bomb guard (§13: uploads are type-sniffed and capped).
MAX_INPUT_PIXELS = 40_000_000
#: Fixed so identical inputs produce identical bytes on every machine.
PNG_COMPRESS_LEVEL = 6


def open_image(payload: bytes, *, what: str) -> Image.Image:
    """Decode bytes to RGB, or fail the job with copy a user can act on."""
    try:
        image = Image.open(io.BytesIO(payload))
        width, height = image.size
        if width * height > MAX_INPUT_PIXELS:
            raise PermanentError(
                "That view is too large to render.",
                action="Try again at a smaller window size.",
                detail="%s is %dx%d px (max %d total)" % (what, width, height, MAX_INPUT_PIXELS),
            )
        return image.convert("RGB")
    except PermanentError:
        raise
    except Exception as exc:  # noqa: BLE001 - Pillow raises a wide family here
        raise PermanentError(
            "We could not read the view captured from your model.",
            action="Try the render again.",
            detail="%s is not a readable image: %s" % (what, exc),
        ) from exc


def fit_cover(image: Image.Image, width: int, height: int) -> Image.Image:
    """Scale to cover the frame, then centre-crop.

    Cover-and-crop rather than stretch: a squashed elevation is worse than a cropped
    one, because an architect will read proportions off it.
    """
    src_w, src_h = image.size
    if src_w == width and src_h == height:
        return image
    scale = max(width / src_w, height / src_h)
    new_w = max(width, int(round(src_w * scale)))
    new_h = max(height, int(round(src_h * scale)))
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    return resized.crop((left, top, left + width, top + height))


def encode_png(image: Image.Image) -> bytes:
    """Deterministic PNG bytes (no timestamp chunk, fixed compression level)."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=PNG_COMPRESS_LEVEL)
    return buffer.getvalue()


__all__ = ["MAX_INPUT_PIXELS", "PNG_COMPRESS_LEVEL", "encode_png", "fit_cover", "open_image"]
