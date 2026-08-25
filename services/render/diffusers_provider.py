"""DiffusersRenderProvider — the real GPU path (playbook §9, Phase 7).

    "SDXL or FLUX.1-schnell via diffusers; ControlNet depth + MLSD from the supplied
     maps; precise = ControlNet scale 0.9 / denoise 0.45; explore = scale 0.35 /
     denoise 0.8; prompt templates per preset; Real-ESRGAN 2x; NSFW/safety checker on.
     Weights license guard: assert model id in allowlist (no FLUX.1-dev)."

Structure of this module, and the promises it keeps:

* **Every ML import is lazy.** ``import torch`` happens inside :meth:`_pipeline`, which
  is only reached when ``PROVIDER_RENDER=diffusers``. Importing this file costs
  nothing, which is why ``services/render/provider.py`` can reference it safely.
* **The licence guard runs first**, in ``__init__``, before any weight is fetched —
  a refusal must happen at boot, not after a 6 GB download. It runs again in
  :meth:`_pipeline` for every component (ControlNet, VAE, upscaler).
* **Fail closed on safety.** If ``RENDER_SAFETY_CHECKER=true`` and the checker cannot
  be loaded, the provider refuses to render. Silently disabling a safety control
  because a dependency is missing is not an option.
* **Pipeline held per process, built once**, guarded by a lock: model load is tens of
  seconds and ``WORKER_RENDER_CONCURRENCY`` defaults to 1 because "real diffusers wants
  a whole GPU".

**Phase 7 verification note (honest open item).** The class/kwarg names below follow
the ``diffusers==0.30.3`` API pinned in ``services/pyproject.toml`` — in particular
``StableDiffusionXLControlNetImg2ImgPipeline`` with a ``MultiControlNetModel`` for the
depth+MLSD pair. They have not been executed here (no GPU, no torch on this machine).
Phase 7 must run ``pytest -m gpu`` against a real device and correct any drift; the
seams that would move are confined to :meth:`_pipeline` and :meth:`_run_pipeline`.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from services.common.config import WorkerSettings
from services.common.errors import LicenseError, PermanentError, ProviderError
from services.common.logging import get_logger
from services.render.imaging import encode_png, fit_cover, open_image
from services.render.licenses import assert_component_allowed, assert_weights_allowed
from services.render.prompts import PromptSpec, assert_templates_cover_presets, build_prompt
from services.render.types import RenderRequest, RenderResult

log = get_logger("render.diffusers")

#: ControlNets paired with each supported base checkpoint. Both are allowlisted in
#: services/render/licenses.py; adding one requires adding it there too.
CONTROLNET_DEPTH_SDXL = "diffusers/controlnet-depth-sdxl-1.0"
CONTROLNET_MLSD = "lllyasviel/control_v11p_sd15_mlsd"


class DiffusersRenderProvider:
    """Geometry-locked renders via diffusers + ControlNet."""

    name = "diffusers"

    def __init__(self, settings: WorkerSettings) -> None:
        assert_templates_cover_presets()
        self.settings = settings
        self.model_id = settings.render_model_id
        self.device = settings.render_device

        # LEGAL GUARD FIRST. Before any download, any import, any GPU allocation.
        assert_weights_allowed(self.model_id, settings.render_model_allowlist)

        self._pipe: Any | None = None
        self._safety: Any | None = None
        self._upscaler: Any | None = None
        self._upscaler_kind = "none"
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # public interface
    # ------------------------------------------------------------------
    def render(self, req: RenderRequest) -> RenderResult:
        req.validate()
        started = time.monotonic()
        prompt = build_prompt(req)

        pipe = self._pipeline()
        # SDXL is trained at ~1 megapixel; render there and upscale 2x rather than
        # asking the UNet for 2048px directly (slower and visibly worse).
        base_w, base_h = _base_resolution(req.width, req.height)

        try:
            image = self._run_pipeline(pipe, req, prompt, base_w, base_h)
        except LicenseError:
            raise
        except Exception as exc:  # noqa: BLE001 - torch/diffusers raise a wide family
            raise ProviderError(
                "The render engine could not finish this image.",
                provider=self.name,
                retryable=True,
                action="Try again in a moment.",
                detail="pipeline call failed: %r" % exc,
            ) from exc

        flagged = self._check_safety(image)
        if flagged:
            log.warning("render.diffusers.safety_flagged", preset=req.preset, seed=req.seed)
            raise ProviderError(
                "We could not produce this render.",
                provider=self.name,
                retryable=False,
                action="Try a different preset or prompt.",
                code="render_safety_blocked",
                detail="safety checker flagged the output",
            )

        image = self._upscale(image, req.width, req.height)
        payload = encode_png(image)
        duration_ms = int((time.monotonic() - started) * 1000)
        log.info(
            "render.diffusers.done",
            preset=req.preset,
            render_mode=req.mode,
            seed=req.seed,
            duration_ms=duration_ms,
            bytes=len(payload),
            upscaler=self._upscaler_kind,
            **prompt.summary(),
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
            model_id=self.model_id,
            is_mock=False,
            safety_flagged=False,
            metadata={
                "device": self.device,
                "baseResolution": [base_w, base_h],
                "upscaler": self._upscaler_kind,
                **prompt.summary(),
            },
        )

    # ------------------------------------------------------------------
    # pipeline construction (lazy, once per process)
    # ------------------------------------------------------------------
    def _pipeline(self) -> Any:
        if self._pipe is not None:
            return self._pipe
        with self._lock:
            if self._pipe is not None:
                return self._pipe
            self._pipe = self._build_pipeline()
            return self._pipe

    def _build_pipeline(self) -> Any:
        try:
            import torch
            from diffusers import (
                ControlNetModel,
                StableDiffusionXLControlNetImg2ImgPipeline,
            )
            from diffusers.pipelines.controlnet import MultiControlNetModel
        except ImportError as exc:
            raise PermanentError(
                "Photoreal rendering is not available on this server.",
                action="Switch to the preview renderer, or ask an administrator to "
                "install the render extras.",
                code="render_backend_missing",
                detail=(
                    "PROVIDER_RENDER=diffusers but the ML extra is not installed. "
                    'Install with: pip install "garh-services[ml]" — %s' % exc
                ),
            ) from exc

        # Re-assert on every component: a ControlNet is weights too.
        assert_weights_allowed(self.model_id, self.settings.render_model_allowlist)
        assert_component_allowed(CONTROLNET_DEPTH_SDXL)
        assert_component_allowed(CONTROLNET_MLSD)

        dtype = torch.float16 if self.device == "cuda" else torch.float32
        log.info(
            "render.diffusers.loading",
            model_id=self.model_id,
            device=self.device,
            dtype=str(dtype),
        )
        depth_net = ControlNetModel.from_pretrained(CONTROLNET_DEPTH_SDXL, torch_dtype=dtype)
        mlsd_net = ControlNetModel.from_pretrained(CONTROLNET_MLSD, torch_dtype=dtype)
        controlnet = MultiControlNetModel([depth_net, mlsd_net])

        pipe = StableDiffusionXLControlNetImg2ImgPipeline.from_pretrained(
            self.model_id,
            controlnet=controlnet,
            torch_dtype=dtype,
            variant="fp16" if self.device == "cuda" else None,
            use_safetensors=True,
        )
        pipe = pipe.to(self.device)
        if self.device == "cuda":
            pipe.enable_vae_slicing()
            pipe.enable_attention_slicing()
        else:
            # CPU inference is a debugging path only — minutes, not seconds.
            log.warning("render.diffusers.cpu", hint="set RENDER_DEVICE=cuda for real use")

        if self.settings.render_safety_checker:
            self._safety = self._build_safety_checker(dtype)
        return pipe

    def _build_safety_checker(self, dtype: Any) -> Any:
        """Load the NSFW checker, or refuse to render at all (fail closed)."""
        try:
            from diffusers.pipelines.stable_diffusion.safety_checker import (
                StableDiffusionSafetyChecker,
            )
            from transformers import CLIPImageProcessor

            checker = StableDiffusionSafetyChecker.from_pretrained(
                "CompVis/stable-diffusion-safety-checker", torch_dtype=dtype
            ).to(self.device)
            processor = CLIPImageProcessor.from_pretrained(
                "openai/clip-vit-base-patch32"
            )
        except Exception as exc:  # noqa: BLE001
            raise PermanentError(
                "Rendering is temporarily unavailable.",
                action="Ask an administrator to check the render worker.",
                code="render_safety_unavailable",
                detail=(
                    "RENDER_SAFETY_CHECKER=true but the checker could not be loaded, so "
                    "the provider refuses to render (fail closed): %r" % exc
                ),
            ) from exc
        return (checker, processor)

    # ------------------------------------------------------------------
    # inference
    # ------------------------------------------------------------------
    def _run_pipeline(
        self, pipe: Any, req: RenderRequest, prompt: PromptSpec, width: int, height: int
    ) -> Any:
        import torch

        init_image = fit_cover(open_image(req.viewport_png, what="viewport"), width, height)
        depth_image = (
            fit_cover(open_image(req.depth_png, what="depth map"), width, height)
            if req.depth_png
            else init_image
        )
        edges_image = (
            fit_cover(open_image(req.edges_png, what="edge map"), width, height)
            if req.edges_png
            else depth_image
        )

        generator = torch.Generator(device=self.device).manual_seed(req.seed)
        params = prompt.params
        output = pipe(
            prompt=prompt.positive,
            negative_prompt=prompt.negative,
            image=init_image,
            control_image=[depth_image, edges_image],
            controlnet_conditioning_scale=[
                params.controlnet_conditioning_scale,
                params.controlnet_conditioning_scale * 0.6,
            ],
            strength=params.strength,
            guidance_scale=params.guidance_scale,
            num_inference_steps=params.num_inference_steps,
            generator=generator,
        )
        return output.images[0]

    def _check_safety(self, image: Any) -> bool:
        if self._safety is None:
            return False
        import numpy as np  # noqa: PLC0415 - part of the torch stack, ml extra only
        import torch

        checker, processor = self._safety
        inputs = processor(images=image, return_tensors="pt").to(self.device)
        with torch.no_grad():
            _checked, has_nsfw = checker(
                images=[np.array(image)], clip_input=inputs.pixel_values
            )
        return bool(has_nsfw and has_nsfw[0])

    # ------------------------------------------------------------------
    # upscaling
    # ------------------------------------------------------------------
    def _upscale(self, image: Any, target_w: int, target_h: int) -> Any:
        """Real-ESRGAN 2x, with an honest LANCZOS fallback recorded in metadata."""
        from PIL import Image

        if image.width >= target_w and image.height >= target_h:
            self._upscaler_kind = "none"
            return image.resize((target_w, target_h), Image.Resampling.LANCZOS)

        upscaler = self._real_esrgan()
        if upscaler is not None:
            try:
                import numpy as np

                output, _ = upscaler.enhance(np.array(image), outscale=2)
                self._upscaler_kind = self.settings.render_upscaler
                return Image.fromarray(output).resize(
                    (target_w, target_h), Image.Resampling.LANCZOS
                )
            except Exception as exc:  # noqa: BLE001 - never fail a good image on polish
                log.warning("render.diffusers.upscale_failed", error=str(exc))

        self._upscaler_kind = "lanczos-fallback"
        return image.resize((target_w, target_h), Image.Resampling.LANCZOS)

    def _real_esrgan(self) -> Any | None:
        if self._upscaler is not None:
            return self._upscaler
        assert_component_allowed(self.settings.render_upscaler)
        try:
            from basicsr.archs.rrdbnet_arch import RRDBNet
            from realesrgan import RealESRGANer

            model = RRDBNet(
                num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=2
            )
            self._upscaler = RealESRGANer(
                scale=2,
                model_path=(
                    "https://github.com/xinntao/Real-ESRGAN/releases/download/"
                    "v0.2.1/RealESRGAN_x2plus.pth"
                ),
                model=model,
                half=self.device == "cuda",
                device=self.device,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("render.diffusers.upscaler_unavailable", error=str(exc))
            return None
        return self._upscaler


def _base_resolution(target_w: int, target_h: int) -> tuple[int, int]:
    """Halve the target, snapped to a multiple of 8 (UNet requirement), min 512."""
    width = max(512, (target_w // 2) // 8 * 8)
    height = max(512, (target_h // 2) // 8 * 8)
    return width, height


__all__ = ["CONTROLNET_DEPTH_SDXL", "CONTROLNET_MLSD", "DiffusersRenderProvider"]
