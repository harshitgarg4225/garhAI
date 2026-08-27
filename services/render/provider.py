"""The §9 render provider interface and its factory.

    class RenderProvider(Protocol):
        def render(self, req: RenderRequest) -> RenderResult: ...

``render`` is **synchronous** — exactly as the playbook writes it. Diffusion and image
compositing are CPU/GPU-bound, so the async worker calls providers through
``asyncio.to_thread``; making the interface async would only hide that.

Selection is by ``PROVIDER_RENDER`` (``mock`` | ``diffusers`` | ``stability``). The
mock is the default everywhere including CI, and importing this module pulls in **no**
ML dependency: the diffusers and stability implementations are imported inside the
factory branch that needs them.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from services.common.config import WorkerSettings, get_worker_settings
from services.common.logging import get_logger
from services.render.types import RenderRequest, RenderResult

log = get_logger("render.provider")

PROVIDER_NAMES: tuple[str, ...] = ("mock", "diffusers", "stability")


@runtime_checkable
class RenderProvider(Protocol):
    """Turn a viewport (+ depth/edges) into an image."""

    #: Stable identifier stored on the render job row and shown in render details.
    name: str

    def render(self, req: RenderRequest) -> RenderResult: ...


def get_render_provider(settings: WorkerSettings | None = None) -> RenderProvider:
    """Build the provider named by ``PROVIDER_RENDER``.

    Raises ``ValueError`` on an unknown name — a typo in the environment must stop the
    worker at boot, not silently downgrade a paying render to a mock.
    """
    cfg = settings or get_worker_settings()
    provider_name = cfg.provider_render

    if provider_name == "mock":
        from services.render.mock import MockRenderProvider

        log.info("render.provider.selected", provider="mock")
        return MockRenderProvider()

    if provider_name == "diffusers":
        # Imported here, never at module scope: torch alone is ~2 GB and the mock path
        # must install and start without it.
        from services.render.diffusers_provider import DiffusersRenderProvider

        log.info(
            "render.provider.selected",
            provider="diffusers",
            device=cfg.render_device,
            model_id=cfg.render_model_id,
        )
        return DiffusersRenderProvider(cfg)

    if provider_name == "stability":
        # Imported here for symmetry with diffusers. The stability path needs only
        # httpx (already a base dependency), but keeping the import inside the branch
        # means the registry's import cost and graph never grow for unused providers.
        from services.render.stability_provider import StabilityRenderProvider

        if not cfg.stability_api_key:
            raise ValueError(
                "PROVIDER_RENDER=stability but STABILITY_API_KEY is empty. Set the "
                "key, or use PROVIDER_RENDER=mock (the default) to run without one."
            )
        log.info(
            "render.provider.selected",
            provider="stability",
            base_url=cfg.stability_base_url,
        )
        return StabilityRenderProvider(cfg)

    raise ValueError(
        "Unknown PROVIDER_RENDER=%r. Expected one of: %s."
        % (provider_name, ", ".join(PROVIDER_NAMES))
    )


__all__ = [
    "PROVIDER_NAMES",
    "RenderProvider",
    "RenderRequest",
    "RenderResult",
    "get_render_provider",
]
