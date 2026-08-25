"""Render service (playbook §9): provider interface, deterministic mock, diffusers.

``PROVIDER_RENDER=mock`` is the default and produces a real composited image in well
under a second with no GPU and no model weights — that is what makes the whole product
e2e-testable on a laptop.

Importing this package does **not** import torch or diffusers. The ML path is reached
only through :func:`services.render.provider.get_render_provider` when
``PROVIDER_RENDER=diffusers``.

``MockRenderProvider`` is re-exported **lazily** (PEP 562): the mock needs Pillow, and
the pure-data modules (``types``, ``licenses``, ``pack``, ``prompts``) are deliberately
runnable on a bare interpreter via the ``services/dev_stubs.py`` pattern — Phase 7's
local verification depends on that (see the toolchain-gap row in ``DECISIONS.md``).
"""

from __future__ import annotations

from typing import Any

from services.render.licenses import assert_component_allowed, assert_weights_allowed
from services.render.pack import CLIENT_PACK_SHOTS, PackShot, pack_filenames, shot_seed
from services.render.prompts import MODE_PARAMS, PromptSpec, build_prompt
from services.render.provider import PROVIDER_NAMES, RenderProvider, get_render_provider
from services.render.types import (
    DEFAULT_PRESET,
    PRESETS,
    RENDER_MODES,
    RenderMode,
    RenderPreset,
    RenderRequest,
    RenderResult,
)


def __getattr__(name: str) -> Any:  # PEP 562 — keeps Pillow out of the import path
    if name == "MockRenderProvider":
        from services.render.mock import MockRenderProvider

        return MockRenderProvider
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


__all__ = [
    "CLIENT_PACK_SHOTS",
    "DEFAULT_PRESET",
    "MODE_PARAMS",
    "PRESETS",
    "PROVIDER_NAMES",
    "RENDER_MODES",
    "MockRenderProvider",
    "PackShot",
    "pack_filenames",
    "shot_seed",
    "PromptSpec",
    "RenderMode",
    "RenderPreset",
    "RenderProvider",
    "RenderRequest",
    "RenderResult",
    "assert_component_allowed",
    "assert_weights_allowed",
    "build_prompt",
    "get_render_provider",
]
