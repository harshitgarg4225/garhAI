"""Render-specific routes the jobs router lacks (§9): durable render history links,
the client pack, and the output-blob minting every render job needs.

Why this module exists (Phase 7)
--------------------------------

``routers/jobs.py`` owns the §11 job surface (``POST /projects/:id/renders``, SSE,
cancel). Three render-specific needs sit outside it:

1. **Somewhere for the worker to write the image.** The render worker stores its
   result through ``envelope.require_output("image")`` — a presigned PUT the API must
   mint at enqueue time (§13: the worker holds no S3 credentials). Nothing minted it,
   so every render job died with "this job has nowhere to save its result".
   :func:`mint_render_outputs` is that mint; ``jobs.start_render`` calls it.

2. **History images that outlive a 10-minute URL.** The job row stores the presigned
   GET the worker reported, which expires (§13 caps signed URLs at 10 min). The render
   object's key is deterministic (:func:`render_object_key`), so
   ``GET /projects/:id/render-history`` re-presigns a fresh link per request instead
   of serving a dead one.

3. **The client pack** (§9, spec F6): one click → 6 exteriors + living + kitchen as
   ONE job group (a shared ``packId``), then a zip through the existing signed-URL
   export path (a ``png-pack`` export-job record + ``/downloads/{token}``).

The pack composition here is a **byte-identical mirror** of
``services/render/pack.py`` — the same mirroring convention ``garh_api.queue`` uses
for the envelope, because the API deliberately does not import ``services.*``.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from typing import Any, List, Optional

import httpx
from fastapi import APIRouter, Query, Request, status
from pydantic import Field, StrictInt, StrictStr, field_validator

from garh_api import queue
from garh_api.config import Settings, get_settings
from garh_api.logging import get_logger
from garh_api.repositories import (
    AuditLogRepository,
    CreditEventRepository,
    DesignVersionRepository,
    RenderJobRepository,
    TenantCtx,
)
from garh_api.repositories.audit_log import ACTION_EXPORT_CREATED
from garh_api.repositories.domain import RenderJob
from garh_api.routers import (
    ApiError,
    IdempotencyGuard,
    IdempotencyKeyDep,
    SessionDep,
    TenantDep,
    active_branch,
    build_download_url,
    require_project,
    sign_download_token,
)

# The one SigV4 implementation in the API. Reaching into the imports router for it is
# deliberate: a second copy of request signing is a second place for a signing bug.
from garh_api.routers.imports import _sigv4_presign
from garh_api.schemas import CamelModel, CursorPage, ResponseModel
from garh_api.schemas.jobs import ExportJobOut, RenderInputs, RenderJobOut

_log = get_logger(__name__)

router = APIRouter(tags=["renders"])

RENDER_IMAGE_CONTENT_TYPE = "image/png"

#: TTL for the worker's presigned PUT. Must outlive queue wait + the render itself
#: (§14 budgets: mock <1s, diffusers ≤180s default) with generous headroom.
PUT_URL_TTL_SECONDS = 3600

#: How long fetching one member image may take while building the pack zip.
ARCHIVE_FETCH_TIMEOUT_SECONDS = 30

#: The §9 pack is 8 shots; leave room for a future kit variant, refuse absurdity.
MAX_PACK_SHOTS = 12

#: **Byte-identical mirror of ``services/render/pack.py`` (CLIENT_PACK_SHOTS) and of
#: the preset/mode matrix in ``services/render/types.py`` (PRESETS).** The worker is
#: authoritative — it re-validates every request — but validating up front means a bad
#: pack fails as one 422 instead of eight dead jobs.
PRESET_MODES: dict[str, tuple[str, ...]] = {
    "exterior-street-day": ("precise", "explore"),
    "exterior-34-dusk": ("precise", "explore"),
    "exterior-34-day": ("precise", "explore"),
    "exterior-night": ("precise", "explore"),
    "interior-living": ("explore",),
    "interior-bedroom": ("explore",),
    "interior-kitchen": ("explore",),
}

CLIENT_PACK_SHOTS: tuple[tuple[str, str, str], ...] = (
    # (slug, preset, mode) — zip order.
    ("exterior-street-day", "exterior-street-day", "precise"),
    ("exterior-34-day", "exterior-34-day", "precise"),
    ("exterior-34-dusk", "exterior-34-dusk", "precise"),
    ("exterior-night", "exterior-night", "precise"),
    ("exterior-street-day-explore", "exterior-street-day", "explore"),
    ("exterior-34-dusk-explore", "exterior-34-dusk", "explore"),
    ("interior-living", "interior-living", "explore"),
    ("interior-kitchen", "interior-kitchen", "explore"),
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PackConcurrencyError(ApiError):
    http_status = 429
    code = "render_concurrency_limit"
    action = "Wait for your running renders to finish, then start the pack."


class PackQueueDownError(ApiError):
    http_status = 503
    code = "queue_unavailable"
    action = "Try again in a moment — nothing was lost."


class PackNotReadyError(ApiError):
    """The pack exists but not every image does — an honest 409, never a 404."""

    http_status = 409
    code = "render_pack_not_ready"
    action = "Wait for every image in the pack to finish, then download again."


class NoDesignVersionError(ApiError):
    http_status = 409
    code = "no_design_version"
    action = "Save the design first, then render."


# ---------------------------------------------------------------------------
# Output minting + durable links (§13: worker holds no storage credentials)
# ---------------------------------------------------------------------------


def render_object_key(firm_id: Any, job_id: Any) -> str:
    """Deterministic, firm-scoped object key for one render's image.

    Deterministic on purpose: history links are re-presigned from ``(firm, job)``
    alone, so nothing durable has to be parsed back out of an expired URL.
    """
    return "renders/%s/%s.png" % (firm_id, job_id)


def pack_object_key(firm_id: Any, pack_id: str) -> str:
    return "renders/%s/packs/%s.zip" % (firm_id, pack_id)


def mint_render_outputs(
    firm_id: Any, job_id: Any, settings: Optional[Settings] = None
) -> dict[str, queue.BlobRef]:
    """The envelope ``outputs`` for one render job.

    ``putUrl`` is what the worker writes through; ``getUrl`` is what it reports back as
    ``outputUrl`` so the image is viewable the moment the job succeeds. The ``key``
    travels too, purely as provenance — fresh links are minted from the deterministic
    key, never parsed out of this URL.
    """
    cfg = settings or get_settings()
    key = render_object_key(firm_id, job_id)
    return {
        "image": queue.BlobRef(
            put_url=_sigv4_presign("PUT", key, ttl_seconds=PUT_URL_TTL_SECONDS, settings=cfg),
            get_url=_sigv4_presign(
                "GET", key, ttl_seconds=cfg.s3_signed_url_ttl_seconds, settings=cfg
            ),
            key=key,
            content_type=RENDER_IMAGE_CONTENT_TYPE,
        )
    }


def fresh_image_url(job: RenderJob, firm_id: Any, settings: Settings) -> Optional[str]:
    """A viewable URL for a finished render, re-signed now.

    Only rows whose stored URL points at our object store are re-signed; a ``path``
    from a developer golden run is returned as-is (and simply will not render in a
    browser, which is the honest outcome for that mode).
    """
    if job.status != "succeeded" or not job.output_url:
        return job.output_url
    if job.output_url.startswith(settings.s3_endpoint_url):
        return _sigv4_presign(
            "GET",
            render_object_key(firm_id, job.id),
            ttl_seconds=settings.s3_signed_url_ttl_seconds,
            settings=settings,
        )
    return job.output_url


# ---------------------------------------------------------------------------
# Schemas (request models strict per §13; response models mirror RenderJobOut)
# ---------------------------------------------------------------------------


class PackShotIn(CamelModel):
    """One captured shot of the pack — the client photographs its own 3D scene (§9)."""

    slug: StrictStr = Field(max_length=64, description="Zip filename slug, e.g. exterior-34-dusk.")
    preset: StrictStr = Field(max_length=64)
    mode: StrictStr = Field(description="precise | explore")
    view: dict[str, Any] = Field(
        default_factory=dict, description="Camera state: {eyeMm, targetMm, fovDeg} — integer mm."
    )
    inputs: RenderInputs = Field(default_factory=RenderInputs)

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, value: str) -> str:
        from garh_api import models

        if value not in models.RENDER_MODES:
            raise ValueError("mode must be one of %s." % ", ".join(models.RENDER_MODES))
        return value

    @field_validator("preset")
    @classmethod
    def _check_preset(cls, value: str) -> str:
        if value not in PRESET_MODES:
            raise ValueError(
                "Unknown preset %r. Known presets: %s." % (value, ", ".join(sorted(PRESET_MODES)))
            )
        return value


class RenderPackIn(CamelModel):
    """Start the §9 client pack as one job group."""

    design_version_id: Optional[uuid.UUID] = Field(
        default=None, description="Pins every shot to a version. Omit to pin to the latest."
    )
    seed: Optional[StrictInt] = Field(
        default=None, ge=0, description="Base seed; shot i renders with seed base+i."
    )
    width: StrictInt = Field(default=1536, ge=256, le=4096)
    height: StrictInt = Field(default=1024, ge=256, le=4096)
    shots: List[PackShotIn] = Field(min_length=1, max_length=MAX_PACK_SHOTS)


class RenderPackOut(ResponseModel):
    pack_id: StrictStr
    project_id: uuid.UUID
    design_version_id: Optional[uuid.UUID] = None
    status: StrictStr = Field(description="queued | running | succeeded | failed | cancelled")
    progress: StrictInt = 0
    jobs: List[RenderJobOut] = Field(default_factory=list)

    @classmethod
    def of(cls, pack_id: str, project_id: uuid.UUID, jobs: List[RenderJobOut]) -> "RenderPackOut":
        statuses = [j.status for j in jobs]
        if statuses and all(s == "succeeded" for s in statuses):
            overall = "succeeded"
        elif any(s == "failed" for s in statuses):
            overall = "failed"
        elif any(s == "cancelled" for s in statuses):
            overall = "cancelled"
        elif all(s == "queued" for s in statuses):
            overall = "queued"
        else:
            overall = "running"
        progress = int(sum(j.progress for j in jobs) / len(jobs)) if jobs else 0
        version_ids = {j.design_version_id for j in jobs if j.design_version_id is not None}
        return cls(
            pack_id=pack_id,
            project_id=project_id,
            design_version_id=next(iter(version_ids)) if len(version_ids) == 1 else None,
            status=overall,
            progress=progress,
            jobs=jobs,
        )


class RenderUploadsIn(CamelModel):
    """Ask for presigned capture-upload slots (viewport/depth/edges × shots)."""

    count: StrictInt = Field(ge=1, le=MAX_PACK_SHOTS * 3)


class UploadSlotOut(ResponseModel):
    put_url: StrictStr
    get_url: StrictStr
    key: StrictStr


class RenderUploadsOut(ResponseModel):
    slots: List[UploadSlotOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Capture uploads — how a pack stays under the 8 MB request-body cap (§13)
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/renders/uploads",
    response_model=RenderUploadsOut,
    summary="Mint presigned upload slots for capture images",
)
async def mint_capture_uploads(
    project_id: uuid.UUID,
    body: RenderUploadsIn,
    session: SessionDep,
    ctx: TenantDep,
) -> RenderUploadsOut:
    """The §9 capture set travels to storage directly, not through the API.

    `jobs.py`'s `_render_assets` docstring promises this path ("a real deployment
    uploads to storage first and passes URLs"); this is the endpoint that keeps the
    promise. One client-pack request carrying 24 inline PNGs would blow the §13
    request-body cap — presigned PUTs move the bytes browser→storage, and the job
    body carries only URLs. Minting is pure HMAC: no storage round trip, no rows.
    """
    ctx.require_write("uploading render captures")
    await require_project(session, ctx, project_id)
    settings = get_settings()
    slots: List[UploadSlotOut] = []
    for _ in range(body.count):
        key = "renders/%s/inputs/%s.png" % (ctx.firm_id, uuid.uuid4().hex)
        slots.append(
            UploadSlotOut(
                put_url=_sigv4_presign("PUT", key, ttl_seconds=900, settings=settings),
                get_url=_sigv4_presign(
                    "GET", key, ttl_seconds=PUT_URL_TTL_SECONDS, settings=settings
                ),
                key=key,
            )
        )
    return RenderUploadsOut(slots=slots)


# ---------------------------------------------------------------------------
# Render history — the §9 gallery, with links that work (GET /render-history)
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/render-history",
    response_model=CursorPage[RenderJobOut],
    summary="Render history with fresh signed image links",
)
async def render_history(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
    limit: int = Query(default=30, ge=1, le=100),
    cursor: Optional[str] = Query(default=None, max_length=512),
) -> CursorPage[RenderJobOut]:
    """Newest first, each pinned to its ``designVersionId``, ``stale`` as the ops
    pipeline last marked it (§9 banner), and ``outputUrl`` re-signed for this request
    so a gallery opened tomorrow still shows yesterday's renders."""
    await require_project(session, ctx, project_id)
    ctx.require_scope("renders")
    settings = get_settings()
    page = await RenderJobRepository(session, ctx).list_gallery(
        project_id, limit=limit, cursor=cursor
    )
    return CursorPage[RenderJobOut](
        items=[
            RenderJobOut.of(job, output_url=fresh_image_url(job, ctx.firm_id, settings))
            for job in page.items
        ],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


# ---------------------------------------------------------------------------
# Client pack (§9: "client-pack batch", one job group)
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/renders/client-pack",
    response_model=RenderPackOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="One click: 6 exteriors + living + kitchen as one job group",
)
async def start_client_pack(
    project_id: uuid.UUID,
    body: RenderPackIn,
    request: Request,
    session: SessionDep,
    ctx: TenantDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> RenderPackOut:
    """Queue every shot of the pack under one ``packId``.

    The §9 per-firm concurrency gate is checked ONCE for the whole pack — a pack is one
    user intention, and rejecting its fifth member while accepting four would leave a
    half-pack nobody asked for. Members queue up and run as worker capacity frees.
    """
    ctx.require_write("starting renders")
    await require_project(session, ctx, project_id)
    settings = get_settings()

    for index, shot in enumerate(body.shots):
        if shot.mode not in PRESET_MODES[shot.preset]:
            raise ApiError(
                "Shot %d: %s supports %s only (interiors are Explore-only at MVP)."
                % (index + 1, shot.preset, " and ".join(PRESET_MODES[shot.preset])),
                status=422,
                code="preset_mode_mismatch",
                action="Switch that shot to Explore and try again.",
            )
        if not (shot.inputs.viewport_png or shot.inputs.viewport_url):
            raise ApiError(
                "Shot %d has no captured view — a render is graded from the model view."
                % (index + 1),
                status=422,
                code="missing_viewport",
                action="Capture the pack again from the 3D view.",
            )

    repo = RenderJobRepository(session, ctx)
    active = await repo.count_active()
    if active >= settings.render_concurrency_per_firm:
        raise PackConcurrencyError(
            "Your firm already has %d renders queued or running (the limit is %d)."
            % (active, settings.render_concurrency_per_firm),
            extra={"active": active, "limit": settings.render_concurrency_per_firm},
        )

    guard = IdempotencyGuard(scope="render-pack", key=idempotency_key, firm_id=ctx.firm_id)
    replayed = await guard.begin()
    if replayed is not None:
        return RenderPackOut.model_validate(replayed)

    try:
        design_version_id = body.design_version_id
        if design_version_id is not None:
            await DesignVersionRepository(session, ctx).require(design_version_id)
        else:
            branch = await active_branch(session, ctx, project_id)
            latest = await DesignVersionRepository(session, ctx).latest(project_id, branch)
            design_version_id = latest.id if latest is not None else None
        if design_version_id is None:
            raise NoDesignVersionError(
                "There's no saved version of this design to render yet."
            )

        pack_id = uuid.uuid4().hex
        base_seed = body.seed if body.seed is not None else _derive_seed(pack_id)

        rows = []
        for index, shot in enumerate(body.shots):
            params: dict[str, Any] = {
                "preset": shot.preset,
                "promptExtras": "",
                "seed": base_seed + index,  # mirrors services/render/pack.shot_seed
                "width": body.width,
                "height": body.height,
                "packId": pack_id,
                "packIndex": index,
                "packSlug": shot.slug,
            }
            job = await repo.enqueue(
                project_id,
                mode=shot.mode,
                view=shot.view,
                params=params,
                design_version_id=design_version_id,
                provider=settings.provider_render,
            )
            rows.append((job, shot, params))

        # All rows exist before the first Redis push: if a push fails the transaction
        # rolls every row back. (A mid-loop Redis death can strand already-pushed
        # envelopes whose rows rolled back; their lifecycle events fail to apply and
        # stay pending for inspection rather than corrupting state — see
        # consume_job_events in routers/jobs.py.)
        for job, shot, params in rows:
            try:
                await queue.enqueue(
                    queue.JobEnvelope(
                        job_id=str(job.id),
                        kind=queue.JOB_RENDER_IMAGE,
                        firm_id=str(ctx.firm_id),
                        project_id=str(project_id),
                        design_version_id=str(design_version_id),
                        actor_user_id=str(ctx.user_id) if ctx.user_id else None,
                        request_id=ctx.request_id,
                        idempotency_key=idempotency_key,
                        payload={"mode": shot.mode, "view": shot.view, **params},
                        assets=_shot_assets(shot.inputs),
                        outputs=mint_render_outputs(ctx.firm_id, job.id, settings),
                    )
                )
            except queue.QueueUnavailableError as exc:
                raise PackQueueDownError(
                    "We couldn't start the pack just now — the job queue is unreachable."
                ) from exc

        await CreditEventRepository(session, ctx).record(
            kind="render",
            qty=len(rows),
            meta={
                "packId": pack_id,
                "projectId": str(project_id),
                "provider": settings.provider_render,
            },
        )
        out = RenderPackOut.of(
            pack_id,
            project_id,
            [
                RenderJobOut.of(
                    job,
                    events_url=_events_url(request, "/render-jobs/%s/events" % job.id),
                )
                for job, _shot, _params in rows
            ],
        )
    except Exception:
        await guard.release()
        raise

    await guard.store(json.loads(out.model_dump_json(by_alias=True)))
    _log.info(
        "render_pack.enqueued",
        pack_id=pack_id,
        project_id=str(project_id),
        shots=len(rows),
    )
    return out


@router.get(
    "/projects/{project_id}/render-packs/{pack_id}",
    response_model=RenderPackOut,
    summary="Client pack state (aggregated over its member jobs)",
)
async def get_render_pack(
    project_id: uuid.UUID,
    pack_id: str,
    request: Request,
    session: SessionDep,
    ctx: TenantDep,
) -> RenderPackOut:
    await require_project(session, ctx, project_id)
    ctx.require_scope("renders")
    settings = get_settings()
    members = await _pack_members(session, ctx, project_id, pack_id)
    if not members:
        raise ApiError(
            "That render pack doesn't exist.",
            status=404,
            code="not_found",
            action="Start the client pack again from the Renders tab.",
        )
    return RenderPackOut.of(
        pack_id,
        project_id,
        [
            RenderJobOut.of(
                job,
                events_url=_events_url(request, "/render-jobs/%s/events" % job.id),
                output_url=fresh_image_url(job, ctx.firm_id, settings),
            )
            for job in members
        ],
    )


@router.post(
    "/projects/{project_id}/render-packs/{pack_id}/archive",
    response_model=ExportJobOut,
    summary="Zip the finished pack and hand back a signed download",
)
async def archive_render_pack(
    project_id: uuid.UUID,
    pack_id: str,
    request: Request,
    session: SessionDep,
    ctx: TenantDep,
) -> ExportJobOut:
    """Build ``renders/{firm}/packs/{pack}.zip`` and return it through the EXISTING
    signed-URL export path: a ``png-pack`` export-job record redeemed at
    ``/downloads/{token}`` — exactly where every other artefact leaves the building
    (§11, §13). Idempotent by construction: calling again rebuilds the same zip from
    the same deterministic member keys and re-signs a fresh link.
    """
    ctx.require_write("exporting renders")
    await require_project(session, ctx, project_id)
    settings = get_settings()

    members = await _pack_members(session, ctx, project_id, pack_id)
    if not members:
        raise ApiError(
            "That render pack doesn't exist.",
            status=404,
            code="not_found",
            action="Start the client pack again from the Renders tab.",
        )
    done = [j for j in members if j.status == "succeeded"]
    if len(done) != len(members):
        raise PackNotReadyError(
            "%d of %d images are ready — the pack zips when all of them are."
            % (len(done), len(members)),
            extra={"ready": len(done), "total": len(members)},
        )

    # Fetch every member image through a fresh presigned GET and zip in order.
    ordered = sorted(members, key=lambda j: int(dict(j.params).get("packIndex") or 0))
    archive = io.BytesIO()
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(float(ARCHIVE_FETCH_TIMEOUT_SECONDS)), follow_redirects=False
    ) as client:
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
            for index, job in enumerate(ordered):
                url = fresh_image_url(job, ctx.firm_id, settings)
                if not url or not url.startswith(("http://", "https://")):
                    raise PackNotReadyError(
                        "One image in this pack is not reachable from storage."
                    )
                response = await client.get(url)
                if response.status_code >= 400:
                    _log.error(
                        "render_pack.member_fetch_failed",
                        pack_id=pack_id,
                        job_id=str(job.id),
                        status_code=response.status_code,
                    )
                    raise PackNotReadyError(
                        "One image in this pack could not be read back from storage."
                    )
                params = dict(job.params)
                slug = str(params.get("packSlug") or params.get("preset") or "render")
                bundle.writestr("%02d-%s.png" % (index + 1, slug), response.content)
        zip_bytes = archive.getvalue()

        put_url = _sigv4_presign(
            "PUT",
            pack_object_key(ctx.firm_id, pack_id),
            ttl_seconds=PUT_URL_TTL_SECONDS,
            settings=settings,
        )
        stored = await client.put(
            put_url, content=zip_bytes, headers={"content-type": "application/zip"}
        )
        if stored.status_code >= 400:
            raise ApiError(
                "We couldn't store the pack zip just now.",
                status=503,
                code="storage_unavailable",
                action="Try the download again in a moment.",
            )

    # The existing export path: a Redis export-job record + a signed download token.
    export_job_id = "renderpack-%s" % pack_id
    record = await queue.put_export_job(
        queue.ExportJob(
            id=export_job_id,
            firm_id=str(ctx.firm_id),
            project_id=str(project_id),
            kind="png-pack",
            status="succeeded",
            progress=100,
            design_version_id=(
                str(ordered[0].design_version_id) if ordered[0].design_version_id else None
            ),
            download_url=_sigv4_presign(
                "GET",
                pack_object_key(ctx.firm_id, pack_id),
                ttl_seconds=settings.s3_signed_url_ttl_seconds,
                settings=settings,
            ),
            params={"packId": pack_id, "images": len(ordered), "bytes": len(zip_bytes)},
        )
    )
    await AuditLogRepository(session, ctx).record(
        ACTION_EXPORT_CREATED,
        entity="project",
        entity_id=project_id,
        meta={"kind": "png-pack", "jobId": export_job_id, "packId": pack_id},
    )
    await CreditEventRepository(session, ctx).record(
        kind="export",
        qty=1,
        meta={"jobId": export_job_id, "projectId": str(project_id), "exportKind": "png-pack"},
    )

    out = ExportJobOut.of(record)
    token, _expires = sign_download_token(
        {"k": "export", "f": str(ctx.firm_id), "j": export_job_id, "x": "png-pack"}
    )
    out.download_url = build_download_url(request, token)
    _log.info(
        "render_pack.archived", pack_id=pack_id, images=len(ordered), bytes=len(zip_bytes)
    )
    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _events_url(request: Request, path: str) -> str:
    settings = get_settings()
    return str(request.base_url).rstrip("/") + settings.api_prefix + path


def _derive_seed(pack_id: str) -> int:
    """A stable base seed from the pack id — reproducible, no global RNG state."""
    return int(pack_id[:8], 16) % 1_000_000


def _shot_assets(inputs: RenderInputs) -> dict[str, queue.BlobRef]:
    """Identical shape to ``jobs._render_assets`` — the worker's own asset names."""
    assets: dict[str, queue.BlobRef] = {}
    for name, inline, url in (
        ("viewport_png", inputs.viewport_png, inputs.viewport_url),
        ("depth_png", inputs.depth_png, inputs.depth_url),
        ("edges_png", inputs.edges_png, inputs.edges_url),
    ):
        if url:
            assets[name] = queue.BlobRef(get_url=url, content_type="image/png")
        elif inline:
            assets[name] = queue.BlobRef(inline_base64=inline, content_type="image/png")
    return assets


async def _pack_members(
    session: Any, ctx: TenantCtx, project_id: uuid.UUID, pack_id: str
) -> list[RenderJob]:
    """Member jobs of a pack, via the firm-scoped repository.

    Packs are recent by construction (they are started and consumed in one sitting),
    so one 100-row page of the project's newest renders is the honest window; a pack
    that has scrolled past it has also scrolled past being downloadable UI.
    """
    page = await RenderJobRepository(session, ctx).list_gallery(project_id, limit=100)
    return [job for job in page.items if dict(job.params).get("packId") == pack_id]


__all__ = [
    "CLIENT_PACK_SHOTS",
    "PRESET_MODES",
    "fresh_image_url",
    "mint_render_outputs",
    "pack_object_key",
    "render_object_key",
    "router",
]
