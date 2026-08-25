"""MockRenderProvider — the DEFAULT render provider (playbook §9, §14).

    "MockProvider (default): composites viewport PNG + preset-tinted gradient +
     watermark text; instant. Deterministic by seed. Keeps the whole product testable
     without GPUs."

This is a real implementation, not a placeholder that returns the input unchanged:

* the viewport is fitted to the requested size (cover + centre crop, LANCZOS);
* a preset-tinted gradient is graded over it, more strongly in Explore than in
  Precise — so the two modes *look* different, which is what an e2e test of the mode
  toggle needs to assert;
* the edge map, when supplied, is multiplied back in, so the mock visibly respects the
  geometry the client sent;
* a vignette and a watermark bar go on last. The watermark says MOCK RENDER, because
  §15's tone rule cuts both ways: the UI must never imply this is a real render.

**Determinism** is the contract: identical request bytes ⇒ identical output bytes. All
randomness comes from ``random.Random(...)`` seeded with
:meth:`~services.render.types.RenderRequest.grade_seed_material` — derived from the
request (seed, preset, mode, size) and nothing else. No clock, no ``os.urandom``, no
global ``random`` state.

Where that is checked, and how far each check reaches:

* ``scripts/render_mirrors.py`` (in ``make bare``) executes the *seed material* half on
  a bare interpreter — no Pillow needed. It is why the derivation lives on
  ``RenderRequest`` instead of inline here; do not inline it back.
* ``apps/api/tests/test_render_jobs.py::test_mock_provider_is_deterministic_by_seed_and_under_budget``
  asserts *byte* equality and the §14 <1s budget. It needs Pillow and skips cleanly
  without it, so on a machine without an image library the pixels are unproven — see
  ``docs/phase-6-7-verification.md``.

Budget: §14 says mock render <1s. At 2048×1152 this is a handful of resizes and
blends — comfortably inside it on a laptop CPU.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFont

from services.common.errors import PermanentError
from services.common.logging import get_logger
from services.render.imaging import encode_png, fit_cover, open_image
from services.render.prompts import assert_templates_cover_presets, sanitise_prompt_extras
from services.render.types import RenderRequest, RenderResult

log = get_logger("render.mock")

#: Grading strength per mode. Precise stays close to the model view; Explore departs.
_MODE_ALPHA = {"precise": 0.20, "explore": 0.44}
#: Extra jitter, deterministic per seed, so two seeds of the same preset differ.
_ALPHA_JITTER = 0.05
_WATERMARK_TEXT = "GARH AI · MOCK RENDER"


@dataclass(frozen=True)
class _Grade:
    """Deterministic look parameters derived from the request."""

    alpha: float
    direction: int  # 0 = top→bottom, 1 = bottom→top, 2 = left→right, 3 = right→left
    vignette: float
    edge_strength: float


class MockRenderProvider:
    """Instant, deterministic, GPU-free renders."""

    name = "mock"

    def __init__(self, *, watermark: bool = True) -> None:
        assert_templates_cover_presets()
        self.watermark = watermark

    def render(self, req: RenderRequest) -> RenderResult:
        """Composite one image. Synchronous by design — callers use ``to_thread``."""
        req.validate()
        started = time.monotonic()

        base = open_image(req.viewport_png, what="viewport")
        grade = _grade_for(req)

        canvas = fit_cover(base, req.width, req.height)
        canvas = _apply_gradient(canvas, req, grade)
        if req.edges_png:
            canvas = _apply_edges(canvas, req.edges_png, grade.edge_strength)
        canvas = _apply_vignette(canvas, grade.vignette)
        if self.watermark:
            canvas = _apply_watermark(canvas, req)

        payload = encode_png(canvas)

        duration_ms = int((time.monotonic() - started) * 1000)
        log.info(
            "render.mock.done",
            preset=req.preset,
            render_mode=req.mode,
            seed=req.seed,
            width=req.width,
            height=req.height,
            duration_ms=duration_ms,
            bytes=len(payload),
        )
        return RenderResult(
            image_png=payload,
            provider=self.name,
            mode=req.mode,
            preset=req.preset,
            seed=req.seed,
            width=req.width,
            height=req.height,
            duration_ms=duration_ms,
            model_id="mock",
            is_mock=True,
            safety_flagged=False,
            metadata={
                "grade": {
                    "alpha": round(grade.alpha, 4),
                    "direction": grade.direction,
                    "vignette": round(grade.vignette, 4),
                },
                "usedEdges": bool(req.edges_png),
                "usedDepth": bool(req.depth_png),
                "promptExtrasChars": len(sanitise_prompt_extras(req.prompt_extras)),
            },
        )


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------
def _grade_for(req: RenderRequest) -> _Grade:
    # The seed material lives on RenderRequest (Pillow-free) so `make bare` can execute
    # the determinism claim without an image library. Do not inline it back here.
    rng = random.Random(req.grade_seed_material())
    base_alpha = _MODE_ALPHA[req.mode]
    alpha = base_alpha + rng.uniform(-_ALPHA_JITTER, _ALPHA_JITTER)
    return _Grade(
        alpha=max(0.05, min(0.75, alpha)),
        direction=rng.randrange(4),
        vignette=rng.uniform(0.10, 0.24),
        edge_strength=0.25 if req.mode == "precise" else 0.12,
    )


def _gradient(width: int, height: int, direction: int) -> Image.Image:
    """A 0→255 ramp of the requested orientation, as an ``L`` image."""
    ramp = Image.linear_gradient("L")  # 256x256, black at top
    if direction == 1:
        ramp = ramp.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    elif direction == 2:
        ramp = ramp.transpose(Image.Transpose.ROTATE_90)
    elif direction == 3:
        ramp = ramp.transpose(Image.Transpose.ROTATE_270)
    return ramp.resize((width, height), Image.Resampling.BILINEAR)


def _apply_gradient(canvas: Image.Image, req: RenderRequest, grade: _Grade) -> Image.Image:
    preset = req.preset_def()
    top = Image.new("RGB", canvas.size, preset.tint_rgb)
    bottom = Image.new("RGB", canvas.size, preset.tint_rgb_secondary)
    mask = _gradient(canvas.width, canvas.height, grade.direction)
    tint = Image.composite(bottom, top, mask)
    # Soft-light keeps the building's own shading readable; a straight blend flattens
    # it, and the whole point of Precise is that the geometry still reads.
    graded = ImageChops.soft_light(canvas, tint)
    return Image.blend(canvas, graded, grade.alpha)


def _apply_edges(canvas: Image.Image, edges_png: bytes, strength: float) -> Image.Image:
    """Multiply the client's edge map back in so lines stay crisp."""
    try:
        edges = open_image(edges_png, what="edge map").convert("L")
    except PermanentError:
        # A bad edge map degrades the look; it must not fail the render.
        log.warning("render.mock.bad_edges")
        return canvas
    if edges.size != canvas.size:
        edges = edges.resize(canvas.size, Image.Resampling.BILINEAR)
    # Client convention: white background, dark lines. Multiply darkens only the lines.
    lines = Image.merge("RGB", (edges, edges, edges))
    multiplied = ImageChops.multiply(canvas, lines)
    return Image.blend(canvas, multiplied, strength)


def _apply_vignette(canvas: Image.Image, amount: float) -> Image.Image:
    radial = Image.radial_gradient("L").resize(canvas.size, Image.Resampling.BILINEAR)
    dark = Image.new("RGB", canvas.size, (0, 0, 0))
    vignetted = Image.composite(dark, canvas, radial)
    return Image.blend(canvas, vignetted, max(0.0, min(0.6, amount)))


def _apply_watermark(canvas: Image.Image, req: RenderRequest) -> Image.Image:
    """A legible "this is not a real render" bar along the bottom."""
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    bar_height = max(28, canvas.height // 22)
    draw.rectangle(
        [(0, canvas.height - bar_height), (canvas.width, canvas.height)],
        fill=(17, 17, 20, 168),
    )
    label = "%s · %s · %s · seed %d" % (
        _WATERMARK_TEXT,
        req.preset,
        req.mode,
        req.seed,
    )
    font = _load_font(max(12, bar_height // 2))
    text_y = canvas.height - bar_height + (bar_height - _text_height(draw, label, font)) // 2
    draw.text((max(8, canvas.width // 96), text_y), label, fill=(236, 236, 240, 255), font=font)
    return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")


def _load_font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Pillow's bundled default font at ``size``, with a pre-10.1 fallback.

    No font file is read from disk: the image must render identically in CI, in a
    container, and on a laptop.
    """
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - Pillow < 10.1
        return ImageFont.load_default()


def _text_height(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont | ImageFont.FreeTypeFont
) -> int:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    del left, right
    return max(1, bottom - top)


__all__ = ["MockRenderProvider"]
