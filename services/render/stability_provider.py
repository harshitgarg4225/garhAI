"""StabilityRenderProvider — real AI renders from a hosted API, key-only (§9).

The third answer to "how does a render get made", between the mock (no key, no GPU,
watermarked) and diffusers (a whole GPU, multi-GB weights): Stability AI's hosted
stable-image REST API. ``PROVIDER_RENDER=stability`` + ``STABILITY_API_KEY`` is the
entire setup — no CUDA, no model downloads, no ``[ml]`` extra.

**Endpoint choice: ``control/structure``, deliberately.** The product's edge is that a
render matches the drawing set — a render that invents a window is worse than a plain
one. ``/v2beta/stable-image/control/structure`` conditions generation on the structure
of a supplied image, which is exactly the composited model view the worker sends us.
The plain ``generate/sd3`` img2img call was considered and rejected: its only
geometry control is denoise strength, which in Explore mode (strength 0.8) would let
the model redraw the building freely. Structure control is the hosted analogue of the
diffusers path's ControlNet, so Precise/Explore map onto ``control_strength`` using
the SAME §9 numbers (0.9 / 0.35) from ``services/render/prompts.MODE_PARAMS`` — one
source for the mode promise, not two.

Contracts kept, and one honestly weakened:

* **Lazy cost.** httpx is a base dependency, but this module is still only imported
  inside the factory's ``stability`` branch, same as diffusers — importing the
  registry never pays for a provider it will not use.
* **Sync ``render``.** Same as the other providers; the worker calls it through
  ``asyncio.to_thread``. One ``httpx.Client`` per provider instance, built lazily.
* **§13 logging.** Prompt text never appears in a log line — sizes and parameter
  values only, matching the Anthropic provider.
* **Fail closed on safety.** A 200 whose ``finish-reason`` header says
  ``CONTENT_FILTERED`` is Stability returning a *blurred* image. We refuse it with
  the same ``render_safety_blocked`` code diffusers uses, rather than storing a
  censored frame as if it were the render.
* **Determinism (weakened).** ``seed`` is forwarded, but a hosted API does not
  promise bit-identical output across time the way the mock does. ``RenderResult``
  is honest about the provider, and the §14 byte-equality test applies to the mock
  only.

Resolution: the endpoint returns an image at (approximately) the control image's
resolution and caps inputs at ~9.4 MP total, so the viewport is fitted to a capped
frame before upload and the response is LANCZOS-fitted to the requested size after —
the same render-small-then-resize shape as the diffusers path.

Not run against the live API on this machine (no key here — keys are a launch gate).
The wire format is pinned by ``services/render/tests/test_stability_provider.py``
against a strict ``httpx.MockTransport`` double; the one manual smoke path is the
``__main__`` guard at the bottom, which needs a real ``STABILITY_API_KEY``.
"""

from __future__ import annotations

import threading
import time

import httpx

from services.common.config import WorkerSettings, get_worker_settings
from services.common.errors import ProviderError
from services.common.logging import get_logger
from services.render.imaging import encode_png, fit_cover, open_image
from services.render.prompts import MODE_PARAMS, assert_templates_cover_presets, build_prompt
from services.render.types import RenderRequest, RenderResult

log = get_logger("render.stability")

#: The structure-control endpoint, relative to ``STABILITY_BASE_URL``.
STRUCTURE_PATH = "/v2beta/stable-image/control/structure"
#: Shown in render details as the model id (the endpoint IS the model choice here).
MODEL_ID = "stabilityai/stable-image-control-structure"
#: Stability's documented input cap: total pixels ≤ 9,437,184, each side ≥ 64.
MAX_API_PIXELS = 9_437_184
MIN_API_SIDE = 64
#: Stability's seed range is [0, 4294967294]. Ours is any non-negative int.
MAX_SEED = 4_294_967_294


class StabilityRenderProvider:
    """Geometry-preserving renders via Stability's hosted structure control."""

    name = "stability"

    def __init__(
        self,
        settings: WorkerSettings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        assert_templates_cover_presets()
        self.settings = settings or get_worker_settings()
        #: Test seam: an ``httpx.MockTransport`` here makes the suite hermetic
        #: without monkeypatching httpx internals. ``None`` = the real network.
        self._transport = transport
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # public interface
    # ------------------------------------------------------------------
    def render(self, req: RenderRequest) -> RenderResult:
        """One API call, one image. Synchronous — callers use ``to_thread``."""
        req.validate()
        started = time.monotonic()
        prompt = build_prompt(req)

        upload_w, upload_h = _upload_resolution(req.width, req.height)
        control_png = encode_png(
            fit_cover(open_image(req.viewport_png, what="viewport"), upload_w, upload_h)
        )
        #: §9's Precise/Explore numbers, from the one module that owns them.
        control_strength = MODE_PARAMS[req.mode].controlnet_conditioning_scale
        seed = req.seed % (MAX_SEED + 1)

        # §13: parameters and sizes only. The prompt carries user text (prompt_extras)
        # and MUST NOT appear in a log line.
        log.info(
            "render.stability.request",
            preset=req.preset,
            render_mode=req.mode,
            seed=seed,
            upload_width=upload_w,
            upload_height=upload_h,
            control_strength=control_strength,
            positive_chars=len(prompt.positive),
        )
        try:
            response = self._http().post(
                STRUCTURE_PATH,
                data={
                    "prompt": prompt.positive,
                    "negative_prompt": prompt.negative,
                    "control_strength": "%.2f" % control_strength,
                    "seed": str(seed),
                    "output_format": "png",
                },
                files={"image": ("viewport.png", control_png, "image/png")},
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "The render service took too long to answer.",
                provider=self.name,
                retryable=True,
                action="Try again in a moment.",
                detail="timeout after %ss calling %s: %r"
                % (self.settings.stability_timeout_seconds, STRUCTURE_PATH, exc),
            ) from exc
        except httpx.HTTPError as exc:
            # DNS, TLS, connection reset — transient infrastructure until proven
            # otherwise, same optimism as services/common/errors.is_retryable.
            raise ProviderError(
                "We could not reach the render service.",
                provider=self.name,
                retryable=True,
                action="Try again in a moment.",
                detail="transport error calling %s: %r" % (STRUCTURE_PATH, exc),
            ) from exc

        if response.status_code != 200:
            raise self._error_for(response)

        finish_reason = response.headers.get("finish-reason", "").upper()
        if finish_reason == "CONTENT_FILTERED":
            # The API returns a *blurred* image with a 200 in this case. Storing it
            # as the render would be lying to the architect; refuse like diffusers.
            raise ProviderError(
                "We could not produce this render.",
                provider=self.name,
                retryable=False,
                action="Try a different preset or prompt.",
                code="render_safety_blocked",
                detail="stability finish-reason=CONTENT_FILTERED (seed %s)" % seed,
            )

        # The API answers at roughly the upload resolution; the job promised
        # req.size. open_image also re-validates the payload is a real image.
        image = fit_cover(
            open_image(response.content, what="stability render"), req.width, req.height
        )
        payload = encode_png(image)
        duration_ms = int((time.monotonic() - started) * 1000)
        log.info(
            "render.stability.done",
            preset=req.preset,
            render_mode=req.mode,
            seed=seed,
            duration_ms=duration_ms,
            bytes=len(payload),
            finish_reason=finish_reason or "SUCCESS",
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
            model_id=MODEL_ID,
            is_mock=False,
            safety_flagged=False,
            metadata={
                "endpoint": "control/structure",
                "controlStrength": control_strength,
                "uploadResolution": [upload_w, upload_h],
                "responseSeed": response.headers.get("seed"),
                **prompt.summary(),
            },
        )

    def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            client.close()

    # ------------------------------------------------------------------
    # plumbing
    # ------------------------------------------------------------------
    def _http(self) -> httpx.Client:
        """One client per provider instance, built lazily (connection reuse)."""
        if self._client is None:
            with self._lock:
                if self._client is None:
                    self._client = httpx.Client(
                        base_url=self.settings.stability_base_url,
                        headers={
                            "authorization": "Bearer %s" % self.settings.stability_api_key,
                            "accept": "image/*",
                        },
                        timeout=httpx.Timeout(
                            float(self.settings.stability_timeout_seconds), connect=10.0
                        ),
                        transport=self._transport,
                    )
        return self._client

    def _error_for(self, response: httpx.Response) -> ProviderError:
        """Map a non-200 onto the worker taxonomy. Detail is operator-only (§13)."""
        status = response.status_code
        detail = "HTTP %d from %s: %s" % (status, STRUCTURE_PATH, _body_snippet(response))

        if status == 401:
            # Key wrong, revoked, or for another account. Retrying cannot fix a
            # configuration problem, and the detail must name the variable.
            return ProviderError(
                "Photoreal rendering is not configured correctly on this deployment.",
                provider=self.name,
                retryable=False,
                action="Ask an administrator to check the render worker.",
                code="render_provider_auth",
                detail="STABILITY_API_KEY was rejected — %s" % detail,
                status=status,
            )
        if status == 403:
            # Stability's request-side content moderation. Same refusal shape as a
            # filtered response: not our outage, not retryable, user can rephrase.
            return ProviderError(
                "We could not produce this render.",
                provider=self.name,
                retryable=False,
                action="Try a different preset or prompt.",
                code="render_safety_blocked",
                detail=detail,
                status=status,
            )
        if status in (402, 429):
            # Out of credits / rate limited. Retryable so the queue's backoff gets a
            # chance (a top-up or a quieter minute fixes both without a code change).
            return ProviderError(
                "The render service is busy right now.",
                provider=self.name,
                retryable=True,
                action="Try again in a few minutes."
                if status == 429
                else "Try again — if it keeps happening, ask an administrator to top "
                "up the render provider credits.",
                detail=detail,
                status=status,
            )
        if 400 <= status < 500:
            # 400/413/422: this exact request will fail identically every time.
            return ProviderError(
                "The render service could not process this image.",
                provider=self.name,
                retryable=False,
                action="Try a different preset or prompt.",
                detail=detail,
                status=status,
            )
        return ProviderError(
            "The render service is temporarily unavailable.",
            provider=self.name,
            retryable=True,
            action="Try again in a moment.",
            detail=detail,
            status=status,
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _upload_resolution(target_w: int, target_h: int) -> tuple[int, int]:
    """Fit the requested frame under the API's ~9.4 MP cap, aspect preserved.

    Snapped to a multiple of 8 like the diffusers ``_base_resolution`` — not an API
    requirement, but it keeps the two real providers' sizing behaviour comparable.
    ``RenderRequest.validate`` guarantees sides >= 256, so after the worst-case scale
    (8192x8192, factor 0.375) no side can approach the 64 px floor.
    """
    total = target_w * target_h
    if total <= MAX_API_PIXELS:
        return target_w, target_h
    scale = (MAX_API_PIXELS / total) ** 0.5
    width = max(MIN_API_SIDE, int(target_w * scale) // 8 * 8)
    height = max(MIN_API_SIDE, int(target_h * scale) // 8 * 8)
    return width, height


def _body_snippet(response: httpx.Response) -> str:
    """First 300 chars of an error body, for operator-facing detail only."""
    try:
        text = response.text
    except Exception:
        return "<unreadable body>"
    return " ".join(text.split())[:300] or "<empty body>"


if __name__ == "__main__":  # pragma: no cover - manual smoke path, real network + key
    # The ONE non-hermetic path, run by hand: builds a synthetic viewport, calls the
    # live API with STABILITY_API_KEY from the environment, writes /tmp output.
    import os
    import sys

    if not os.environ.get("STABILITY_API_KEY"):
        sys.exit("Set STABILITY_API_KEY to run this smoke test (it spends credits).")

    from PIL import Image, ImageDraw

    canvas = Image.new("RGB", (1024, 576), (204, 214, 224))
    sketch = ImageDraw.Draw(canvas)
    sketch.rectangle([200, 180, 820, 500], fill=(228, 224, 216), outline=(60, 60, 64), width=4)
    sketch.rectangle([260, 240, 380, 360], fill=(140, 170, 200), outline=(60, 60, 64), width=3)
    sketch.rectangle([640, 240, 760, 360], fill=(140, 170, 200), outline=(60, 60, 64), width=3)
    sketch.rectangle([470, 340, 560, 500], fill=(120, 96, 80), outline=(60, 60, 64), width=3)

    provider = StabilityRenderProvider(WorkerSettings())
    result = provider.render(
        RenderRequest(
            viewport_png=encode_png(canvas),
            mode="explore",
            preset="exterior-street-day",
            seed=7,
            size=(1024, 576),
        )
    )
    out_path = "/tmp/garh-stability-smoke.png"
    with open(out_path, "wb") as handle:
        handle.write(result.image_png)
    print("wrote %s — %s" % (out_path, result.summary()))


__all__ = ["MAX_API_PIXELS", "MODEL_ID", "STRUCTURE_PATH", "StabilityRenderProvider"]
