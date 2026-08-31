"""The ``render.image`` job handler (playbook §9).

Flow, and the real event behind each progress step (§15: no fake bars):

======  ==================================  ==============================================
%       stage                               what actually happened
======  ==================================  ==============================================
0       ``started``                         the job was leased by this worker
10      ``fetching-view``                   viewport / depth / edges downloaded
20      ``rendering``                       the provider call began
90      ``storing``                         the provider returned an image
100     ``succeeded``                       the image is in object storage
======  ==================================  ==============================================

Two §9 rules are enforced elsewhere on purpose, and this docstring is where that is
written down so nobody re-implements them here:

* **Per-firm concurrency 4** is checked by the API before enqueuing
  (``RenderJobRepository.count_active``). A worker cannot see other firms' jobs, so it
  cannot enforce a per-firm limit; ``WORKER_RENDER_CONCURRENCY`` bounds this process.
* **Stale flagging** on model change is ``RenderJobRepository.mark_stale_for_project``,
  triggered by the op pipeline. The worker only guarantees the other half: a result is
  always pinned to the ``designVersionId`` it was rendered from, and refuses to run
  without one.
"""

from __future__ import annotations

import asyncio
from typing import Any

from services.common.envelope import BlobRef
from services.common.errors import InvalidJobError
from services.common.jobstore import JobResult
from services.common.logging import get_logger
from services.common.runtime import BaseJobHandler, JobContext
from services.render.prompts import build_prompt
from services.render.provider import RenderProvider, get_render_provider
from services.render.references import INTENTS, SCOPES, Reference
from services.render.types import DEFAULT_PRESET, PRESETS, RENDER_MODES, RenderRequest

log = get_logger("render.handler")

#: Asset names in the envelope. The API must use exactly these keys.
ASSET_VIEWPORT = "viewport_png"
ASSET_DEPTH = "depth_png"
ASSET_EDGES = "edges_png"
#: Output name in the envelope.
OUTPUT_IMAGE = "image"


class RenderJobHandler(BaseJobHandler):
    """Turns a captured viewport into a rendered image."""

    kinds = ("render.image",)

    def __init__(self, provider: RenderProvider | None = None) -> None:
        self._provider = provider
        self.timeout_seconds: int | None = None

    def timeout_for(self, ctx: JobContext) -> int | None:
        """Settings-driven budget, resolved by the runner BEFORE the job starts
        (an assignment inside ``handle`` would apply one job late)."""
        return ctx.settings.render_timeout_seconds

    def provider(self, ctx: JobContext) -> RenderProvider:
        """Resolve the provider once per process (model load is expensive)."""
        if self._provider is None:
            self._provider = get_render_provider(ctx.settings)
        return self._provider

    async def handle(self, ctx: JobContext) -> JobResult:
        envelope = ctx.envelope
        if not envelope.design_version_id:
            raise InvalidJobError(
                "This render is not linked to a saved version of your design.",
                action="Save the design and render again.",
                detail="render jobs must carry designVersionId (§9: results pinned to it)",
            )

        request = await self._build_request(ctx)
        provider = self.provider(ctx)
        ctx.raise_if_cancelled()

        await ctx.progress.stage(
            "rendering",
            "Rendering %s…" % PRESETS[request.preset].label.lower(),
            percent=20,
            provider=provider.name,
            renderMode=request.mode,
        )
        # Providers are synchronous and CPU/GPU-bound: off the event loop they go, or
        # this worker's heartbeat and cancellation polling would stall behind them.
        result = await asyncio.to_thread(provider.render, request)
        ctx.raise_if_cancelled()

        await ctx.progress.stage("storing", "Saving your render…", percent=90)
        output_ref = envelope.require_output(OUTPUT_IMAGE)
        stored = await ctx.blobs.put(
            output_ref, result.image_png, content_type="image/png", what="render"
        )

        data: dict[str, Any] = {
            "designVersionId": envelope.design_version_id,
            "outputUrl": stored.get_url or stored.path or "",
            "outputKey": stored.key,
            **result.summary(),
        }
        # §11: name the board references this render actually followed, so "did it use
        # my reference?" has an answer on the render itself rather than a shrug. Taken
        # from `build_prompt`, the same function the provider used, so this credit list
        # cannot drift from the instruction the model received; only ids and the
        # architect's own labels travel, never prompt text (§13).
        credited = build_prompt(request).references_used
        if credited:
            data["references"] = [dict(entry) for entry in credited]
        log.info("render.job.done", **result.summary())
        return JobResult(
            data=data,
            outputs={OUTPUT_IMAGE: stored},
            message="Render ready." if not result.is_mock else "Preview render ready.",
        )

    # ------------------------------------------------------------------
    async def _build_request(self, ctx: JobContext) -> RenderRequest:
        payload = ctx.payload
        envelope = ctx.envelope

        mode = str(payload.get("mode", "explore"))
        if mode not in RENDER_MODES:
            raise InvalidJobError(
                "We do not recognise that render mode.",
                detail="mode=%r, expected one of %s" % (mode, ", ".join(RENDER_MODES)),
            )
        preset = str(payload.get("preset", DEFAULT_PRESET))
        if preset not in PRESETS:
            raise InvalidJobError(
                "We do not recognise that render style.",
                action="Pick a style from the list and try again.",
                detail="preset=%r, known: %s" % (preset, ", ".join(sorted(PRESETS))),
            )
        seed = payload.get("seed", 0)
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            raise InvalidJobError(
                "This render's settings could not be read.",
                detail="seed must be a non-negative integer, got %r" % (seed,),
            )
        width = _int_or(payload.get("width"), ctx.settings.render_output_width)
        height = _int_or(payload.get("height"), ctx.settings.render_output_height)

        await ctx.progress.stage("fetching-view", "Reading your model view…", percent=5)
        viewport = await ctx.blobs.fetch(envelope.require_asset(ASSET_VIEWPORT), what="model view")
        depth = await self._optional(ctx, ASSET_DEPTH, "depth map")
        edges = await self._optional(ctx, ASSET_EDGES, "edge map")
        await ctx.progress.stage(
            "fetching-view",
            "Model view ready.",
            percent=10,
            hasDepth=bool(depth),
            hasEdges=bool(edges),
        )

        request = RenderRequest(
            references=_references_from(payload.get("references")),
            viewport_png=viewport,
            depth_png=depth,
            edges_png=edges,
            mode=mode,  # type: ignore[arg-type]  # checked against RENDER_MODES above
            preset=preset,
            seed=seed,
            size=(width, height),
            prompt_extras=str(payload.get("promptExtras", "")),
            options=payload.get("options") if isinstance(payload.get("options"), dict) else {},
        )
        try:
            request.validate()
        except ValueError as exc:
            raise InvalidJobError(
                "These render settings do not go together.",
                action="Adjust the style or mode and try again.",
                detail=str(exc),
            ) from exc
        return request

    async def _optional(self, ctx: JobContext, name: str, what: str) -> bytes | None:
        ref: BlobRef | None = ctx.envelope.assets.get(name)
        if ref is None or not ref.readable:
            return None
        return await ctx.blobs.fetch(ref, what=what)


def _references_from(raw: Any) -> tuple[Reference, ...]:
    """Rebuild the inspiration board the API snapshotted into this job's payload (§11).

    Skips anything malformed rather than failing the render. The board is additive: a
    reference the worker cannot read must cost the architect a reference, never the
    picture they were waiting for. Every skip is logged, because a board that silently
    contributes nothing is the failure this feature exists to prevent.

    Unknown scopes and intents are dropped for the same reason a bad scope is refused at
    the API boundary — ``applies_to`` would answer ``True`` for anything it does not
    recognise, so an unreadable scope would leak into every view.
    """
    if not isinstance(raw, list):
        return ()
    out: list[Reference] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        scope = str(entry.get("scope", ""))
        intent = str(entry.get("intent", "guide"))
        # An id-less entry cannot be credited on the finished render, and §11's claim
        # is that an architect can see which references a render followed. A reference
        # that can never be named is one that would show as a blank chip.
        if not str(entry.get("id", "")).strip():
            log.warning("render.reference.unidentified", scope=scope)
            continue
        if scope not in SCOPES or intent not in INTENTS:
            log.warning(
                "render.reference.unreadable",
                reference_id=str(entry.get("id", "")),
                scope=scope,
                intent=intent,
            )
            continue
        out.append(
            Reference(
                id=str(entry.get("id", "")),
                label=str(entry.get("label", "")),
                scope=scope,  # type: ignore[arg-type]  # checked against SCOPES above
                why=str(entry.get("why", "")),
                ignore=str(entry.get("ignore", "")),
                intent=intent,  # type: ignore[arg-type]  # checked against INTENTS above
            )
        )
    return tuple(out)


def _int_or(value: Any, fallback: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return fallback


__all__ = [
    "ASSET_DEPTH",
    "ASSET_EDGES",
    "ASSET_VIEWPORT",
    "OUTPUT_IMAGE",
    "RenderJobHandler",
]
