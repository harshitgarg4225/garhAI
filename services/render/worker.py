"""``python -m services.render.worker`` — the render queue consumer (§9).

Consumes ``QUEUE_RENDER`` (default ``garh:queue:render``). Provider selection is
``PROVIDER_RENDER``: ``mock`` (default, instant, no GPU) or ``diffusers``.

The provider is constructed at boot rather than on the first job, so a licence
refusal or a missing ML extra kills the process immediately with a clear message
instead of failing the first user's render minutes later.
"""

from __future__ import annotations

import sys

from services.common.config import get_worker_settings
from services.common.errors import WorkerError
from services.common.logging import configure_worker_logging, get_logger
from services.common.runtime import run_worker
from services.render.handler import RenderJobHandler
from services.render.provider import get_render_provider

log = get_logger("render.worker")


def main() -> int:
    settings = get_worker_settings()
    configure_worker_logging(settings)
    try:
        provider = get_render_provider(settings)
    except WorkerError as exc:
        # A licence refusal or a missing backend is fatal and must be unmistakable.
        log.error(
            "render.worker.provider_unavailable",
            code=exc.code,
            reason=exc.message,
            detail=exc.detail,
        )
        return 2
    except ValueError as exc:
        log.error("render.worker.bad_config", error=str(exc))
        return 2

    log.info(
        "render.worker.boot",
        provider=provider.name,
        device=settings.render_device,
        safety_checker=settings.render_safety_checker,
        output=[settings.render_output_width, settings.render_output_height],
    )
    return run_worker(name="render", handler=RenderJobHandler(provider))


if __name__ == "__main__":
    sys.exit(main())
