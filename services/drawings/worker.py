"""``python -m services.drawings.worker`` — the drawings queue consumer (§7).

Consumes ``QUEUE_DRAWINGS`` (default ``garh:queue:drawings``) and handles
``drawings.generate_sheets``, ``drawings.export`` and ``drawings.import_dxf``.

Optional dependencies are probed at boot and reported, not enforced
--------------------------------------------------------------------
This used to exit(2) when ``ezdxf`` was missing. That was right when DXF *was* the
drawings pipeline, and wrong now: sheet generation, the dimension chains, every SVG,
the schedules, the area statement and the glTF export are pure Python and work
perfectly without it. Refusing to start meant an architect got no drawings at all
because one download format was unavailable.

So the boot log now states plainly what this process can and cannot do, and each
missing capability turns into an actionable per-format error at the moment someone
asks for that format (``handler._publish`` records it as a note; the export path
answers with "install X, or download Y instead"). ``ezdxf`` is still a declared
dependency of ``garh-services`` — a production image without it is misconfigured, and
the ``error``-level line below is meant to be alertable. It just is not fatal.

The one genuinely fatal dependency is the model core: without ``garh_model`` there is
nothing to draw, and starting would only produce a queue of failed jobs.
"""

from __future__ import annotations

import shutil
import sys

from services.common.config import get_worker_settings
from services.common.logging import configure_worker_logging, get_logger
from services.common.runtime import run_worker
from services.drawings.handler import DrawingsJobHandler
from services.drawings.layers import LAYER_NAMES

log = get_logger("drawings.worker")

#: Capability name → what it costs when absent. Ordered by how much it hurts.
OPTIONAL_CAPABILITIES = (
    ("dxf", "ezdxf", "DXF export and per-sheet DXF downloads"),
    ("pdf", None, "vector PDF export (needs rsvg-convert, chromium or inkscape)"),
    ("png", None, "the PNG/WhatsApp pack (same rasteriser as PDF)"),
)


def probe_capabilities() -> dict[str, bool]:
    """What this process can actually produce. Logged at boot, per §18 observability."""
    capabilities: dict[str, bool] = {"svg": True, "gltf": True, "sheets": True}
    try:
        import ezdxf  # noqa: F401

        capabilities["dxf"] = True
    except ImportError:
        capabilities["dxf"] = False

    from services.drawings.export.pdf import CONVERTERS

    rasteriser = next((name for name, _d, _h in CONVERTERS if shutil.which(name)), None)
    capabilities["pdf"] = rasteriser is not None
    capabilities["png"] = rasteriser is not None
    capabilities["rasteriser"] = rasteriser  # type: ignore[assignment]
    return capabilities


def main() -> int:
    settings = get_worker_settings()
    configure_worker_logging(settings)

    try:
        import garh_model  # noqa: F401 - import is the check
    except ImportError as exc:
        log.error(
            "drawings.worker.missing_dependency",
            error=str(exc),
            hint="garh_model must be importable (PYTHONPATH=/app:/app/apps/api in the "
            "worker image). Without the model core there is nothing to draw.",
        )
        return 2

    capabilities = probe_capabilities()
    if not capabilities["dxf"]:
        log.error(
            "drawings.worker.degraded",
            missing="ezdxf",
            unavailable="DXF export and per-sheet DXF downloads",
            hint="ezdxf is a base dependency of garh-services; run "
            "`pip install -e services/` (MIT licensed, pure Python)",
        )
    if not capabilities["pdf"]:
        log.warning(
            "drawings.worker.degraded",
            missing="svg rasteriser",
            unavailable="vector PDF export and the PNG pack",
            hint="apt-get install -y librsvg2-bin qpdf (or chromium)",
        )

    log.info(
        "drawings.worker.boot",
        layers=list(LAYER_NAMES),
        dxf_parse_timeout_seconds=settings.dxf_parse_timeout_seconds,
        can_svg=capabilities["svg"],
        can_dxf=capabilities["dxf"],
        can_pdf=capabilities["pdf"],
        can_gltf=capabilities["gltf"],
        rasteriser=capabilities.get("rasteriser"),
    )
    return run_worker(name="drawings", handler=DrawingsJobHandler())


if __name__ == "__main__":
    sys.exit(main())
