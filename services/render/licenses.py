"""WEIGHTS LICENCE GUARD (playbook §9) — a legal boundary, not a preference.

    "Weights license guard: assert model id in allowlist (no FLUX.1-dev)."

SKILL.md's locked decisions say: Apache/MIT/BSD/MPL only, never GPL/AGPL, and never
RPLAN-derived weights. Model weights have their own licences that are *not* the
library's licence — ``diffusers`` is Apache-2.0 while FLUX.1-dev is non-commercial.
Shipping a commercial render made with FLUX.1-dev would be a licence violation by us
on behalf of a paying architect.

Three rules, in this order:

1. **The denylist always wins.** Even if an operator adds FLUX.1-dev to
   ``RENDER_MODEL_ALLOWLIST``, it is refused. Configuration cannot grant a licence.
2. **Marker matching, not exact matching.** A local path (``/weights/flux1-dev.safetensors``)
   or a mirror (``someone/FLUX.1-dev-fp8``) is the same weights. Any known
   non-commercial marker anywhere in the identifier is a refusal.
3. **Allowlist for everything else.** Unknown weights are refused, not assumed fine —
   the same posture the repo's licence scanner takes on unknown packages.

Refusals are :class:`~services.common.errors.LicenseError`: permanent, never retried,
and logged at ERROR with a banner so it cannot be mistaken for a transient blip.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.common.errors import LicenseError
from services.common.logging import get_logger

log = get_logger("render.licenses")

#: Verified in references/market-research-and-oss-licenses.md. Keep the citation.
DEFAULT_ALLOWLIST: tuple[str, ...] = (
    "stabilityai/stable-diffusion-xl-base-1.0",  # OpenRAIL-M — commercial use permitted
    "black-forest-labs/FLUX.1-schnell",  # Apache-2.0
    "Qwen/Qwen-Image",  # Apache-2.0
)

#: Auxiliary models the pipeline loads besides the base checkpoint.
DEFAULT_COMPONENT_ALLOWLIST: tuple[str, ...] = (
    "diffusers/controlnet-depth-sdxl-1.0",  # Apache-2.0
    "diffusers/controlnet-canny-sdxl-1.0",  # Apache-2.0
    "thibaud/controlnet-openpose-sdxl-1.0",  # Apache-2.0
    "lllyasviel/control_v11p_sd15_mlsd",  # Apache-2.0 (MLSD line control)
    "madebyollin/sdxl-vae-fp16-fix",  # MIT
    "RealESRGAN_x2plus",  # BSD-3 (xinntao/Real-ESRGAN)
    "RealESRGAN_x4plus",  # BSD-3
)


@dataclass(frozen=True)
class DeniedWeights:
    """A model we refuse to load, and the sentence explaining why."""

    marker: str
    name: str
    reason: str


#: Substring markers, matched case-insensitively against the whole identifier.
DENIED_WEIGHTS: tuple[DeniedWeights, ...] = (
    DeniedWeights(
        marker="flux.1-dev",
        name="FLUX.1-dev",
        reason=(
            "FLUX.1-dev ships under the FLUX.1 [dev] Non-Commercial License. Garh AI is a "
            "commercial product, so its outputs may not be used here. Use "
            "black-forest-labs/FLUX.1-schnell (Apache-2.0) instead."
        ),
    ),
    DeniedWeights(
        marker="flux1-dev",
        name="FLUX.1-dev",
        reason="Same weights as FLUX.1-dev under a different filename — non-commercial.",
    ),
    DeniedWeights(
        marker="flux-dev",
        name="FLUX.1-dev",
        reason="Same weights as FLUX.1-dev under a different filename — non-commercial.",
    ),
    DeniedWeights(
        marker="supir",
        name="SUPIR",
        reason=(
            "SUPIR's weights are non-commercial. Use Real-ESRGAN (BSD-3) for upscaling."
        ),
    ),
    DeniedWeights(
        marker="stable-video-diffusion",
        name="Stable Video Diffusion",
        reason="SVD is released under a non-commercial research licence.",
    ),
    DeniedWeights(
        marker="rplan",
        name="RPLAN-derived weights",
        reason=(
            "SKILL.md locked decision: never RPLAN-derived model weights. The dataset is "
            "research-only and cannot be redistributed, even via a permissively licensed "
            "checkpoint."
        ),
    ),
    DeniedWeights(
        marker="cc-by-nc",
        name="a CC BY-NC model",
        reason="CC BY-NC forbids commercial use.",
    ),
    DeniedWeights(
        marker="noncommercial",
        name="a non-commercial model",
        reason="The identifier declares a non-commercial licence.",
    ),
    DeniedWeights(
        marker="non-commercial",
        name="a non-commercial model",
        reason="The identifier declares a non-commercial licence.",
    ),
)


def _normalise(model_id: str) -> str:
    """Lower-case, strip a ``@revision`` suffix and normalise path separators."""
    text = model_id.strip().lower()
    if "@" in text:
        text = text.split("@", 1)[0]
    return text.replace("\\", "/").rstrip("/")


def find_denied(model_id: str) -> DeniedWeights | None:
    """The denylist entry matching ``model_id``, if any."""
    text = _normalise(model_id)
    for denied in DENIED_WEIGHTS:
        if denied.marker in text:
            return denied
    return None


def assert_weights_allowed(
    model_id: str,
    allowlist: tuple[str, ...] | None = None,
    *,
    what: str = "model",
) -> None:
    """Refuse to load weights we are not licensed to use commercially.

    Raises :class:`LicenseError` (permanent, never retried). This function is the only
    approved gate — call it before *every* ``from_pretrained``.
    """
    if not model_id or not model_id.strip():
        raise LicenseError(
            "This render is not configured correctly.",
            action="Ask an administrator to set RENDER_MODEL_ID.",
            detail="empty %s id passed to the licence guard" % what,
        )

    denied = find_denied(model_id)
    if denied is not None:
        _shout(model_id, denied.name, denied.reason)
        raise LicenseError(
            "This render was blocked: the configured image model is not licensed for "
            "commercial use.",
            action="Ask an administrator to switch RENDER_MODEL_ID to an approved model.",
            detail="DENIED %s %r — %s" % (what, model_id, denied.reason),
        )

    permitted = tuple(allowlist) if allowlist is not None else DEFAULT_ALLOWLIST
    normalised = _normalise(model_id)
    if normalised not in {_normalise(entry) for entry in permitted}:
        _shout(
            model_id,
            "an unlisted model",
            "It is not in RENDER_MODEL_ALLOWLIST. Unknown weights are refused rather "
            "than assumed safe — verify the licence, then add it to the allowlist AND "
            "to DECISIONS.md.",
        )
        raise LicenseError(
            "This render was blocked: the configured image model has not been approved.",
            action="Ask an administrator to use an approved model.",
            detail="%s %r is not in the allowlist (%s)" % (what, model_id, ", ".join(permitted)),
        )

    log.info("render.weights_allowed", model_id=model_id, kind=what)


def assert_component_allowed(model_id: str, allowlist: tuple[str, ...] | None = None) -> None:
    """Same guard for ControlNets, VAEs and upscalers."""
    assert_weights_allowed(
        model_id,
        tuple(allowlist) if allowlist is not None else DEFAULT_COMPONENT_ALLOWLIST,
        what="component",
    )


def _shout(model_id: str, name: str, reason: str) -> None:
    """Make a licence refusal impossible to scroll past."""
    banner = "=" * 78
    log.error(
        "render.weights_refused",
        model_id=model_id,
        model_name=name,
        reason=reason,
        banner=banner,
        legal_guard=True,
        hint=(
            "This is a licence boundary, not a bug. Do not add an override; change "
            "RENDER_MODEL_ID."
        ),
    )


__all__ = [
    "DEFAULT_ALLOWLIST",
    "DEFAULT_COMPONENT_ALLOWLIST",
    "DENIED_WEIGHTS",
    "DeniedWeights",
    "assert_component_allowed",
    "assert_weights_allowed",
    "find_denied",
]
