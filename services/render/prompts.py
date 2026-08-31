"""Prompt templates and per-mode diffusion parameters (playbook §9).

Two things live here and nowhere else:

1. **Per-preset prompt templates.** Written for Indian residential architecture — the
   defaults an SDXL checkpoint reaches for are American suburbia, and the render has
   to look like the building the architect actually drew.
2. **Precise vs Explore.** §9 fixes the numbers: precise = ControlNet scale 0.9 /
   denoise 0.45, explore = 0.35 / 0.8. Precise keeps the geometry; Explore is allowed
   to reinterpret it. They are constants, not tunables, because "Precise" is a promise
   made in the UI.

Containment (§13): ``prompt_extras`` is user text. It is sanitised here — length
capped, control characters stripped — and it can only ever end up inside a prompt
string. It never reaches an op, a file path, a shell, or a model id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from services.render.types import RenderMode, RenderRequest

#: §9. Do not "tune" these without changing what the UI promises.
MODE_PARAMS: dict[RenderMode, ModeParams] = {}

#: Users type freely; models do not need an essay, and neither does our log budget.
MAX_PROMPT_EXTRAS_CHARS = 400

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class ModeParams:
    """Diffusion parameters that differ between Precise and Explore."""

    controlnet_conditioning_scale: float
    #: img2img denoising strength. Lower = closer to the viewport.
    strength: float
    guidance_scale: float
    num_inference_steps: int


MODE_PARAMS.update(
    {
        "precise": ModeParams(
            controlnet_conditioning_scale=0.9,
            strength=0.45,
            guidance_scale=6.0,
            num_inference_steps=30,
        ),
        "explore": ModeParams(
            controlnet_conditioning_scale=0.35,
            strength=0.8,
            guidance_scale=7.5,
            num_inference_steps=28,
        ),
    }
)

#: Shared quality tail. Kept short: long tails mostly add drift.
_QUALITY = (
    "architectural visualisation, photorealistic, physically based materials, "
    "correct perspective, high detail, sharp focus"
)

#: Negative prompt. The geometry terms matter most — a render that invents a window
#: is worse than a plain one, because the drawing set has to match it.
NEGATIVE_PROMPT = (
    "distorted geometry, warped walls, extra floors, extra windows, missing windows, "
    "fisheye, tilted horizon, blurry, lowres, watermark, signature, text, logo, "
    "cartoon, illustration, painting, cgi artifacts, oversaturated, hdr halo, "
    "people with deformed faces, duplicated railings"
)

PROMPT_TEMPLATES: dict[str, str] = {
    "exterior-street-day": (
        "Street-level photograph of a contemporary Indian residential house, "
        "{scene_extra}, midday sun, clear sky, compound wall and gate, paved road in "
        "front, tropical planting, parked car for scale, " + _QUALITY
    ),
    "exterior-34-day": (
        "Three-quarter exterior photograph of a contemporary Indian residential house, "
        "{scene_extra}, bright daylight, soft shadows, landscaped setback, "
        "eye-level camera, 35mm lens, " + _QUALITY
    ),
    "exterior-34-dusk": (
        "Three-quarter exterior photograph of a contemporary Indian residential house at "
        "dusk, {scene_extra}, warm interior lights glowing through windows, deep blue "
        "sky, wet-look paving reflections, 35mm lens, " + _QUALITY
    ),
    "exterior-night": (
        "Night photograph of a contemporary Indian residential house, {scene_extra}, "
        "warm facade lighting, glowing windows, dark sky, subtle street lighting, "
        "long exposure, " + _QUALITY
    ),
    "interior-living": (
        "Interior photograph of an Indian family living room, {scene_extra}, natural "
        "daylight from large windows, contemporary furniture at Indian proportions, "
        "vitrified tile flooring, ceiling fan, 24mm lens, " + _QUALITY
    ),
    "interior-bedroom": (
        "Interior photograph of an Indian master bedroom, {scene_extra}, queen bed with "
        "wardrobe wall, soft morning daylight, curtains, ceiling fan, 24mm lens, " + _QUALITY
    ),
    "interior-kitchen": (
        "Interior photograph of a modern Indian kitchen, {scene_extra}, L-shaped counter "
        "with granite top, chimney over hob, upper and lower cabinets, daylight from a "
        "window over the sink, 24mm lens, " + _QUALITY
    ),
}


@dataclass(frozen=True)
class PromptSpec:
    """The fully resolved instruction set for one diffusion call."""

    positive: str
    negative: str
    params: ModeParams

    def summary(self) -> dict[str, object]:
        """Log-safe: parameters only. Prompt text is never logged (§13)."""
        return {
            "controlnetScale": self.params.controlnet_conditioning_scale,
            "strength": self.params.strength,
            "guidanceScale": self.params.guidance_scale,
            "steps": self.params.num_inference_steps,
            "positiveChars": len(self.positive),
        }


def sanitise_prompt_extras(text: str) -> str:
    """Make user text safe to concatenate into a prompt.

    Strips control characters, collapses whitespace, caps length. It does NOT try to
    detect "jailbreak" phrasing: for an image model the containment that matters is
    structural — this string can only ever be a prompt fragment.
    """
    cleaned = _CONTROL_CHARS.sub(" ", text or "")
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    if len(cleaned) > MAX_PROMPT_EXTRAS_CHARS:
        cleaned = cleaned[:MAX_PROMPT_EXTRAS_CHARS].rstrip()
    return cleaned


#: The elevation presets share ONE template, because their difference is not
#: wording — it is the light, which is computed per project from where north
#: actually is. Sixteen near-identical strings would be sixteen places for the
#: east elevation to end up describing the west one's sun.
ELEVATION_TEMPLATE = (
    "Straight-on architectural photograph of the {facade} elevation of a contemporary "
    "Indian residential house, {light}, {scene_extra}, camera perpendicular to the "
    "facade at eye level, minimal perspective distortion, " + _QUALITY
)


def build_prompt(request: RenderRequest) -> PromptSpec:
    """Resolve preset + mode + user extras into one :class:`PromptSpec`.

    An ELEVATION preset takes the computed branch: its sun is derived from the
    project's own north rotation (``request.north_deg``) rather than written into a
    template, because "north elevation" points a different way on every plot. A
    render that paints warm afternoon sun onto a north wall is a convincing picture
    of a building that does not exist, and it is the architect who would have to
    explain it to the client.
    """
    preset = request.preset_def()

    if preset.is_elevation:
        from services.render.orientation import describe_light

        light = describe_light(
            preset.facade,  # type: ignore[arg-type]  # is_elevation proves both are set
            preset.time_of_day,  # type: ignore[arg-type]
            north_deg=request.north_deg,
        )
        extras = sanitise_prompt_extras(request.prompt_extras)
        positive = ELEVATION_TEMPLATE.format(
            facade=preset.facade,
            light=light.phrase,
            scene_extra=extras or "flat chajjas and a vertical cladding band at the stair bay",
        )
        return _with_references(positive, request, preset)

    template = PROMPT_TEMPLATES.get(preset.id)
    if template is None:  # pragma: no cover - PRESETS and templates are asserted equal
        raise ValueError("No prompt template for preset %r" % preset.id)

    extras = sanitise_prompt_extras(request.prompt_extras)
    scene_extra = extras or (
        "flat chajjas and a vertical cladding band at the stair bay"
        if preset.scene == "exterior"
        else "calm neutral palette with a wood accent"
    )
    positive = template.format(scene_extra=scene_extra)
    return _with_references(positive, request, preset)


def _with_references(positive: str, request: RenderRequest, preset: Any) -> PromptSpec:
    """Fold the project's inspiration board into the prompt.

    The architect said what to take from each picture and what to leave; the first
    becomes something to draw, the second something not to. Only references this view
    can use are read — a bathroom picture contributes nothing to a street elevation —
    and a project with no board produces byte-identically the prompt it produced before
    the board existed, which is what keeps every existing render reproducible.
    """
    from services.render.references import reference_prompt

    extra_positive, extra_negative = reference_prompt(tuple(request.references), preset)
    if extra_positive:
        positive = "%s. Reference: %s" % (positive, extra_positive)
    negative = NEGATIVE_PROMPT
    if extra_negative:
        negative = "%s, %s" % (negative, extra_negative)
    return PromptSpec(positive=positive, negative=negative, params=MODE_PARAMS[request.mode])


def assert_templates_cover_presets() -> None:
    """Every preset must have a template. Called at provider construction."""
    from services.render.types import PRESETS

    # Elevation presets are covered by ELEVATION_TEMPLATE, which is computed rather
    # than looked up — so they are legitimately absent from PROMPT_TEMPLATES, and
    # excluding them here is the difference between a real gate and one that fails
    # on its own design.
    free_camera = {key for key, preset in PRESETS.items() if not preset.is_elevation}
    missing = sorted(free_camera - set(PROMPT_TEMPLATES))
    extra = sorted(set(PROMPT_TEMPLATES) - free_camera)
    if missing or extra:
        raise ValueError(
            "Prompt templates and presets disagree — missing: %s; orphaned: %s"
            % (missing or "none", extra or "none")
        )


__all__ = [
    "MAX_PROMPT_EXTRAS_CHARS",
    "MODE_PARAMS",
    "NEGATIVE_PROMPT",
    "PROMPT_TEMPLATES",
    "ModeParams",
    "PromptSpec",
    "assert_templates_cover_presets",
    "build_prompt",
    "sanitise_prompt_extras",
]
