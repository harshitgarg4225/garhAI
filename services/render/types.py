"""Render request/result types and the preset catalogue (playbook §9).

The playbook fixes the request shape::

    RenderRequest: { viewport_png, depth_png, edges_png, mode: 'precise'|'explore',
                     preset: str, prompt_extras: str, seed: int, size: (w, h) }

Everything here is provider-agnostic: the mock and the diffusers implementation see
the identical request, which is what makes "e2e-testable with zero GPUs" true rather
than aspirational.

Geometry note: ``size`` is in **pixels**, not millimetres. It is the only place in the
system where a non-mm length is legitimate, because it describes an image, not a
building.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RenderMode = Literal["precise", "explore"]

#: MVP cut line (spec F6): exterior gets Precise + Explore, interior is Explore only.
RENDER_MODES: tuple[RenderMode, ...] = ("precise", "explore")


@dataclass(frozen=True)
class RenderPreset:
    """One named look. Prompt text lives in ``services/render/prompts.py``."""

    id: str
    label: str
    scene: Literal["exterior", "interior"]
    #: Which modes this preset may be requested in (§ spec F6).
    modes: tuple[RenderMode, ...]
    #: sRGB tint the mock provider grades towards — also the UI swatch.
    tint_rgb: tuple[int, int, int]
    #: Second tint; the mock builds its gradient between the two.
    tint_rgb_secondary: tuple[int, int, int]

    def allows(self, mode: str) -> bool:
        return mode in self.modes


#: The MVP preset catalogue. Ids are API values — changing one is a breaking change.
PRESETS: dict[str, RenderPreset] = {
    "exterior-street-day": RenderPreset(
        id="exterior-street-day",
        label="Street view, daylight",
        scene="exterior",
        modes=("precise", "explore"),
        tint_rgb=(196, 214, 235),
        tint_rgb_secondary=(246, 240, 226),
    ),
    "exterior-34-dusk": RenderPreset(
        id="exterior-34-dusk",
        label="Three-quarter view, dusk",
        scene="exterior",
        modes=("precise", "explore"),
        tint_rgb=(72, 84, 128),
        tint_rgb_secondary=(238, 156, 106),
    ),
    "exterior-34-day": RenderPreset(
        id="exterior-34-day",
        label="Three-quarter view, daylight",
        scene="exterior",
        modes=("precise", "explore"),
        tint_rgb=(186, 208, 232),
        tint_rgb_secondary=(250, 246, 232),
    ),
    "exterior-night": RenderPreset(
        id="exterior-night",
        label="Night, warm interior glow",
        scene="exterior",
        modes=("precise", "explore"),
        tint_rgb=(24, 32, 58),
        tint_rgb_secondary=(226, 172, 96),
    ),
    "interior-living": RenderPreset(
        id="interior-living",
        label="Living room",
        scene="interior",
        modes=("explore",),
        tint_rgb=(226, 214, 198),
        tint_rgb_secondary=(250, 246, 238),
    ),
    "interior-bedroom": RenderPreset(
        id="interior-bedroom",
        label="Bedroom",
        scene="interior",
        modes=("explore",),
        tint_rgb=(216, 206, 200),
        tint_rgb_secondary=(246, 242, 236),
    ),
    "interior-kitchen": RenderPreset(
        id="interior-kitchen",
        label="Kitchen",
        scene="interior",
        modes=("explore",),
        tint_rgb=(214, 218, 214),
        tint_rgb_secondary=(248, 248, 244),
    ),
}

DEFAULT_PRESET = "exterior-street-day"


@dataclass(frozen=True)
class RenderRequest:
    """Everything a provider needs for one image.

    ``depth_png`` and ``edges_png`` come from the client (§9: "Client captures viewport
    + depth (R3F depth pass) + edges (Sobel on normals/depth)"). They are optional
    here because the mock does not need them and an Explore render degrades gracefully
    without them — but ``precise`` without a depth map is a contradiction, so
    :meth:`validate` rejects it.
    """

    viewport_png: bytes
    mode: RenderMode
    preset: str
    seed: int
    size: tuple[int, int]
    depth_png: bytes | None = None
    edges_png: bytes | None = None
    prompt_extras: str = ""
    #: Free-form provider hints (negative prompt overrides, LoRA ids in future).
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def width(self) -> int:
        return self.size[0]

    @property
    def height(self) -> int:
        return self.size[1]

    def preset_def(self) -> RenderPreset:
        preset = PRESETS.get(self.preset)
        if preset is None:
            raise ValueError(
                "Unknown render preset %r. Known presets: %s"
                % (self.preset, ", ".join(sorted(PRESETS)))
            )
        return preset

    def grade_seed_material(self) -> str:
        """The seed material every provider derives its randomness from (§9, §14).

        "Deterministic by seed" is a product promise — re-rendering the same shot of the
        same design has to return the same image, or the client pack is not a pack and
        "re-render this one" is a lie. The promise reduces entirely to this string:
        derived from the request and nothing else — no clock, no ``os.urandom``, no
        global ``random`` state, no object id.

        It lives here, on the Pillow-free request, rather than inside
        ``services/render/mock.py``, so the determinism claim can be *executed* on a
        bare interpreter (``scripts/render_mirrors.py``) instead of only asserted in a
        test that needs Pillow and a GPU-free image pipeline.
        """
        return "garh-mock|%d|%s|%s|%dx%d" % (
            self.seed,
            self.preset,
            self.mode,
            self.width,
            self.height,
        )

    def validate(self) -> None:
        """Raise ``ValueError`` on a request no provider could honour."""
        if not self.viewport_png:
            raise ValueError("viewport_png is required — a render is graded from the model view")
        if self.mode not in RENDER_MODES:
            raise ValueError("mode must be one of %s" % ", ".join(RENDER_MODES))
        preset = self.preset_def()
        if not preset.allows(self.mode):
            raise ValueError(
                "Preset %r supports %s only (spec F6: interior is Explore-only at MVP)"
                % (preset.id, " and ".join(preset.modes))
            )
        if self.width < 256 or self.height < 256:
            raise ValueError("size must be at least 256x256 px")
        if self.width > 8192 or self.height > 8192:
            raise ValueError("size must be at most 8192x8192 px")
        if not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError("seed must be a non-negative integer (determinism depends on it)")
        if self.mode == "precise" and not self.depth_png:
            raise ValueError(
                "precise mode needs depth_png — geometry lock is the whole point of Precise"
            )


@dataclass(frozen=True)
class RenderResult:
    """One rendered image plus the provenance a user is entitled to see."""

    image_png: bytes
    provider: str
    mode: RenderMode
    preset: str
    seed: int
    width: int
    height: int
    duration_ms: int
    #: Model id for the diffusers path; ``"mock"`` otherwise. Shown in render details.
    model_id: str = "mock"
    #: True when the image is a stand-in, so the UI can badge it honestly (§15 tone).
    is_mock: bool = False
    #: True when the safety checker replaced or flagged the output.
    safety_flagged: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        """Log-safe / API-safe description (no image bytes, no prompt text)."""
        return {
            "provider": self.provider,
            "modelId": self.model_id,
            "mode": self.mode,
            "preset": self.preset,
            "seed": self.seed,
            "width": self.width,
            "height": self.height,
            "durationMs": self.duration_ms,
            "isMock": self.is_mock,
            "safetyFlagged": self.safety_flagged,
            "bytes": len(self.image_png),
        }


__all__ = [
    "DEFAULT_PRESET",
    "PRESETS",
    "RENDER_MODES",
    "RenderMode",
    "RenderPreset",
    "RenderRequest",
    "RenderResult",
]
