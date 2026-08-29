"""Worker jobs: solve, render, sheets, export — plus their SSE streams (§5, §7, §9, §11).

Every route here does the same four things in the same order, and the order is the
point:

1. **Authorise and validate** through a repository holding a ``TenantCtx``.
2. **Write the job row first** (``solver_jobs`` / ``render_jobs``), so the job exists in
   the one place the product treats as true.
3. **Enqueue onto Redis.** If Redis refuses, the handler raises and the request
   transaction rolls the row back. A row that says ``queued`` while nothing will ever
   pick it up is precisely the dishonest state golden rule 9 forbids.
4. **Return the row**, with the queue depth attached so the UI can say "3rd in queue"
   instead of showing a spinner that means nothing.

No progress is ever synthesised. §15: "never a fake bar". A job's ``progress`` moves
only when a worker publishes an event; if a worker is silent, the client sees the last
honest value and the stream stays open.

Every enqueue route here also carries a per-firm hourly ceiling, mounted as a route
dependency so it is charged before a row is written: solver, render and one shared
drawings budget across ``/export`` and ``/sheets/generate``. These are the four most
expensive things a request can start, and three of them had no ceiling at all before
F-7 — see :mod:`garh_api.ratelimit` for which fail open and which fail closed, and why
they differ.

Who writes job rows
-------------------

Workers hold **no database connection** (``services/common/jobstore.py`` explains why:
statelessness, one writer per row, and keeping third-party model code out of a process
that holds tenant credentials). They append every lifecycle transition to the
``garh:events:jobs`` Redis Stream instead, and :func:`consume_job_events` — started from
the app lifespan — is the API-side consumer that turns those events into repository
calls. That consumer is the *only* path from "the work finished" to "the row says so",
which is why it acknowledges a stream entry only after its transaction has committed.

SSE and database sessions
-------------------------

The streaming endpoints deliberately do **not** take the request-scoped session
dependency. A stream can stay open for the length of a solver run, and a pooled
connection pinned for that long is a connection nobody else can have — a handful of
watchers would exhaust the pool. Each stream opens a short ``session_scope()`` to read
(and tenancy-check) the job row, closes it, and only then starts streaming from Redis.

Browsers cannot set an ``Authorization`` header on ``EventSource``. The client is
expected to use a fetch-based SSE reader (``@microsoft/fetch-event-source`` or
equivalent). A token in the query string is not offered: it would put a credential in
URLs, access logs and referrers.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from garh_api import queue
from garh_api.billing.quotas import require_quota
from garh_api.config import Settings, get_settings
from garh_api.db import session_scope
from garh_api.deps import (
    rate_limit_export_jobs,
    rate_limit_render_jobs,
    rate_limit_sheet_jobs,
    rate_limit_solver_jobs,
)
from garh_api.logging import get_logger
from garh_api.repositories import (
    AuditLogRepository,
    CreditEventRepository,
    DesignVersionRepository,
    RenderJobRepository,
    SheetRepository,
    SolverJobRepository,
    TenantCtx,
)
from garh_api.repositories.audit_log import ACTION_EXPORT_CREATED, ACTION_EXPORT_DOWNLOADED
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
    verify_download_token,
)
from garh_api.schemas import CursorPage
from garh_api.schemas.jobs import (
    DownloadOut,
    ExportIn,
    ExportJobOut,
    JobEventOut,
    RenderIn,
    RenderJobOut,
    SheetOut,
    SheetSetOut,
    SheetsGenerateIn,
    SolveIn,
    SolverJobOut,
)
from garh_api.tenancy import EntityNotFoundError

_log = get_logger(__name__)

router = APIRouter(tags=["jobs"])

#: How often the SSE layer emits a keepalive comment. Below the 60s idle timeout of
#: every proxy we are likely to sit behind, so a quiet solver stage does not look like
#: a dropped connection.
SSE_PING_SECONDS = 15

#: Formats a generated sheet can be downloaded in (§11 ``/sheets/:sid.(svg|dxf|pdf)``).
SHEET_FORMATS: tuple[str, ...] = ("svg", "dxf", "pdf")

_SHEET_CONTENT_TYPES: dict[str, str] = {
    # No `image/svg+xml` for a download: §13 requires SVG output to be sanitised, and
    # serving it as a document type a browser will execute scripts inside is the exact
    # hazard that rule is about. It downloads; the app renders sheets from the model.
    "svg": "application/octet-stream",
    "dxf": "application/dxf",
    "pdf": "application/pdf",
}

_EXPORT_CONTENT_TYPES: dict[str, str] = {
    "pdf-set": "application/pdf",
    "dxf": "application/dxf",
    "gltf": "model/gltf-binary",
    "png-pack": "application/zip",
}

_EXPORT_EXTENSIONS: dict[str, str] = {
    "pdf-set": "pdf",
    "dxf": "dxf",
    "gltf": "glb",
    "png-pack": "zip",
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class QueueDownError(ApiError):
    """Redis is unreachable, so nothing can be started."""

    http_status = 503
    code = "queue_unavailable"
    action = "Try again in a moment — nothing was lost."


class ConcurrencyLimitError(ApiError):
    """§9: four concurrent renders per firm."""

    http_status = 429
    code = "render_concurrency_limit"
    action = "Wait for one of your running renders to finish."


class JobNotCancellableError(ApiError):
    http_status = 409
    code = "job_not_cancellable"
    action = "This job has already finished."


class ArtifactNotReadyError(ApiError):
    """The row exists but the file does not — an honest 409, never a 404.

    A 404 would tell the user the sheet does not exist, which is false and sends them
    to regenerate something that is merely still rendering.
    """

    http_status = 409
    code = "artifact_not_ready"
    action = "Wait for the job to finish, then download again."


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _events_url(request: Request, path: str) -> str:
    settings = get_settings()
    return str(request.base_url).rstrip("/") + settings.api_prefix + path


async def _depth(worker: str) -> int | None:
    """Queue depth, or ``None`` when Redis could not answer.

    ``None`` renders as "unknown" in the UI. Reporting 0 for an unreachable queue would
    claim the job is next when we have no idea.
    """
    depth = await queue.queue_depth(worker)
    return None if depth < 0 else depth


async def _resolve_design_version(
    session: AsyncSession,
    ctx: TenantCtx,
    project_id: uuid.UUID,
    supplied: uuid.UUID | None,
) -> uuid.UUID | None:
    """Pin work to a design version: the supplied one, else the project's latest.

    Renders and sheets are pinned so a result can be shown honestly against the design
    it was made from (§9's stale banner, §7's sheet set). ``None`` means the project has
    no version yet, which is a legitimate state for a brand-new project.
    """
    if supplied is not None:
        await DesignVersionRepository(session, ctx).require(supplied)
        return supplied
    branch = await active_branch(session, ctx, project_id)
    latest = await DesignVersionRepository(session, ctx).latest(project_id, branch)
    return latest.id if latest is not None else None


async def _enqueue_or_rollback(envelope: queue.JobEnvelope) -> int:
    """Enqueue, converting a queue outage into a 503 that rolls the row back."""
    try:
        return await queue.enqueue(envelope)
    except queue.QueueUnavailableError as exc:
        raise QueueDownError(
            "We couldn't start that just now — the job queue is unreachable."
        ) from exc


# ---------------------------------------------------------------------------
# Solver (§5)
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/solve",
    response_model=SolverJobOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate layout options (CP-SAT worker job)",
    # Two ceilings, and they answer different questions. The rate limit is "not this
    # fast" (429); the quota is "not on this plan" (402). A solve is metered into
    # ``credit_events`` at the bottom of this handler, so the gate and the meter read
    # the same rows.
    dependencies=[Depends(rate_limit_solver_jobs), require_quota("solver")],
)
async def start_solve(
    project_id: uuid.UUID,
    body: SolveIn,
    request: Request,
    session: SessionDep,
    ctx: TenantDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> SolverJobOut:
    """Queue a plan generation. Returns immediately with a job to watch.

    202, not 201: nothing is created that the client can fetch as a finished thing yet.
    The options arrive on the job row when the worker has scored them against §5.6's
    gates — golden rule 2 means a plan that failed the gates is never returned at all.
    """
    ctx.require_write("generating plans")
    await require_project(session, ctx, project_id)

    guard = IdempotencyGuard(scope="solve", key=idempotency_key, firm_id=ctx.firm_id)
    replayed = await guard.begin()
    if replayed is not None:
        return SolverJobOut.model_validate(replayed)

    try:
        branch = await active_branch(session, ctx, project_id)
        # The worker's payload contract (services/solver/handler._parse_params) needs
        # the plot, the regulatory numbers and the brief — the worker holds no
        # database, so the API assembles them from the folded document and the rules
        # engine (see garh_api.solver_enqueue). Built before the job row exists, so a
        # project that cannot honestly be solved yet gets a 4xx and leaves nothing
        # behind — never a queued row whose job is doomed at parse.
        from garh_api.solver_enqueue import build_solve_inputs

        inputs = await build_solve_inputs(
            session, ctx, project_id, branch, requested_storeys=body.storeys
        )
        params: dict[str, Any] = {
            "lockedRoomIds": list(body.locked_room_ids),
            "optionCount": body.option_count,
            "storeys": inputs.storeys,
            "versionBranch": str(branch),
        }
        if body.seed is not None:
            params["seed"] = body.seed
        params.update(body.params)

        # The row keeps the request-shaped params only (it is echoed to the UI);
        # the plot/profile/brief ride on the envelope, and they go in AFTER the
        # client's extra params so a request body cannot override the server's
        # compliance numbers — same rule as op 31 discarding client-sent ops.
        job = await SolverJobRepository(session, ctx).enqueue(project_id, params=params)
        # §5.7: a run with locked rooms is a partial re-solve, and the worker takes a
        # different path for it. The kind says so rather than the worker inferring it.
        kind = queue.JOB_SOLVER_RESOLVE if body.locked_room_ids else queue.JOB_SOLVER_GENERATE
        depth = await _enqueue_or_rollback(
            queue.JobEnvelope(
                job_id=str(job.id),
                kind=kind,
                firm_id=str(ctx.firm_id),
                project_id=str(project_id),
                actor_user_id=str(ctx.user_id) if ctx.user_id else None,
                request_id=ctx.request_id,
                idempotency_key=idempotency_key,
                payload={
                    **params,
                    "plot": inputs.plot,
                    "profile": inputs.profile,
                    "brief": inputs.brief,
                },
            )
        )
        await CreditEventRepository(session, ctx).record(
            kind="solver", qty=1, meta={"jobId": str(job.id), "projectId": str(project_id)}
        )
        out = SolverJobOut.of(
            job,
            queue_depth=depth,
            events_url=_events_url(request, "/solver-jobs/%s/events" % job.id),
        )
    except Exception:
        await guard.release()
        raise

    await guard.store(json.loads(out.model_dump_json(by_alias=True)))
    return out


@router.get(
    "/projects/{project_id}/solver-jobs",
    response_model=CursorPage[SolverJobOut],
    summary="Solver job history for a project",
)
async def list_solver_jobs(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
) -> CursorPage[SolverJobOut]:
    await require_project(session, ctx, project_id)
    page = await SolverJobRepository(session, ctx).list_for_project(
        project_id, limit=limit, cursor=cursor
    )
    return CursorPage[SolverJobOut](
        items=[SolverJobOut.of(job) for job in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


@router.get("/solver-jobs/{job_id}", response_model=SolverJobOut, summary="Solver job state")
async def get_solver_job(
    job_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    ctx: TenantDep,
) -> SolverJobOut:
    job = await SolverJobRepository(session, ctx).require(job_id)
    depth = await _depth(queue.WORKER_SOLVER) if job.status == "queued" else None
    return SolverJobOut.of(
        job,
        queue_depth=depth,
        events_url=_events_url(request, "/solver-jobs/%s/events" % job_id),
    )


@router.post(
    "/solver-jobs/{job_id}/cancel",
    response_model=SolverJobOut,
    summary="Ask the solver to stop",
)
async def cancel_solver_job(
    job_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
) -> SolverJobOut:
    """Cancellation is cooperative: the worker stops at its next checkpoint.

    The row is marked ``cancelled`` immediately because that is what the user asked for
    and what they should see; the worker's own ``cancelled`` event arrives shortly after
    and is idempotent with this write.
    """
    ctx.require_write("cancelling a job")
    repo = SolverJobRepository(session, ctx)
    job = await repo.require(job_id)
    if job.status in queue.JOB_TERMINAL_STATUSES:
        raise JobNotCancellableError("That plan generation has already finished.")
    await queue.request_cancel(job_id)
    return SolverJobOut.of(await repo.cancel(job_id))


@router.get(
    "/solver-jobs/{job_id}/events",
    summary="Live solver progress (SSE)",
    response_model=JobEventOut,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def solver_job_events(
    job_id: uuid.UUID,
    request: Request,
    ctx: TenantDep,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    """Stream the real stage messages §15's "generation theater" renders."""
    async with session_scope() as session:
        job = await SolverJobRepository(session, ctx).require(job_id)
        initial = SolverJobOut.of(job)
    return _stream_job(request, job_id, initial, last_event_id)


# ---------------------------------------------------------------------------
# Renders (§9)
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/renders",
    response_model=RenderJobOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a render",
    #: 402 before the job is queued when the plan's render allowance is spent — the
    #: same ``credit_events`` rows this handler writes are what the gate counts.
    dependencies=[Depends(rate_limit_render_jobs), require_quota("render")],
)
async def start_render(
    project_id: uuid.UUID,
    body: RenderIn,
    request: Request,
    session: SessionDep,
    ctx: TenantDep,
    settings: Settings = Depends(get_settings),
    idempotency_key: IdempotencyKeyDep = None,
) -> RenderJobOut:
    """Queue a render pinned to a design version.

    The client captures the viewport, depth and edge maps from the R3F scene (§9) and
    passes them either inline as base64 or as already-uploaded URLs. They travel as
    envelope ``assets`` — the same shape the worker's ``require_asset`` expects — so the
    render worker needs no storage credentials of its own.
    """
    ctx.require_write("starting a render")
    await require_project(session, ctx, project_id)

    # Same up-front preset/mode check the client-pack route does. Without it the pack
    # route answered 422 while a single render with the identical bad preset queued a
    # job that died in the worker — two answers to one mistake, and the worse one on
    # the path people use most. The worker still re-validates; it is authoritative.
    from garh_api.routers.renders import PRESET_MODES

    allowed_modes = PRESET_MODES.get(body.preset)
    if allowed_modes is None:
        raise ApiError(
            "There's no render preset called %r." % body.preset,
            status=422,
            code="unknown_preset",
            action="Pick one of: %s." % ", ".join(sorted(PRESET_MODES)),
        )
    if body.mode not in allowed_modes:
        raise ApiError(
            "%s supports %s only (interiors are Explore-only at MVP)."
            % (body.preset, " and ".join(allowed_modes)),
            status=422,
            code="preset_mode_mismatch",
            action="Switch to Explore and try again.",
        )

    repo = RenderJobRepository(session, ctx)
    active = await repo.count_active()
    if active >= settings.render_concurrency_per_firm:
        raise ConcurrencyLimitError(
            "Your firm already has %d renders running (the limit is %d)."
            % (active, settings.render_concurrency_per_firm),
            extra={"active": active, "limit": settings.render_concurrency_per_firm},
        )

    guard = IdempotencyGuard(scope="render", key=idempotency_key, firm_id=ctx.firm_id)
    replayed = await guard.begin()
    if replayed is not None:
        return RenderJobOut.model_validate(replayed)

    try:
        design_version_id = await _resolve_design_version(
            session, ctx, project_id, body.design_version_id
        )
        params: dict[str, Any] = {
            "preset": body.preset,
            "promptExtras": body.prompt_extras,
            "width": body.width,
            "height": body.height,
        }
        if body.seed is not None:
            params["seed"] = body.seed

        job = await repo.enqueue(
            project_id,
            mode=body.mode,
            view=body.view,
            params=params,
            design_version_id=design_version_id,
            provider=settings.provider_render,
        )

        # Phase 7: the worker stores its image through `envelope.require_output("image")`
        # — a presigned PUT the API must mint (§13: workers hold no storage credentials).
        # Without this every render died with "nowhere to save its result".
        from garh_api.routers.renders import mint_render_outputs

        depth = await _enqueue_or_rollback(
            queue.JobEnvelope(
                job_id=str(job.id),
                kind=queue.JOB_RENDER_IMAGE,
                firm_id=str(ctx.firm_id),
                project_id=str(project_id),
                design_version_id=str(design_version_id) if design_version_id else None,
                actor_user_id=str(ctx.user_id) if ctx.user_id else None,
                request_id=ctx.request_id,
                idempotency_key=idempotency_key,
                payload={"mode": body.mode, "view": body.view, **params},
                assets=_render_assets(body),
                outputs=mint_render_outputs(ctx.firm_id, job.id, settings),
            )
        )
        await CreditEventRepository(session, ctx).record(
            kind="render",
            qty=1,
            meta={
                "jobId": str(job.id),
                "projectId": str(project_id),
                "provider": settings.provider_render,
                "mode": body.mode,
                "preset": body.preset,
            },
        )
        out = RenderJobOut.of(
            job,
            queue_depth=depth,
            events_url=_events_url(request, "/render-jobs/%s/events" % job.id),
        )
    except Exception:
        await guard.release()
        raise

    await guard.store(json.loads(out.model_dump_json(by_alias=True)))
    return out


def _render_assets(body: RenderIn) -> dict[str, queue.BlobRef]:
    """Control maps → envelope assets, using the worker's own ``BlobRef`` names.

    Inline base64 is accepted for the mock provider and for tests; a real deployment
    uploads to storage first and passes URLs, which is why both paths exist here rather
    than one convenient one.
    """
    inputs = body.inputs
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


@router.get(
    "/projects/{project_id}/renders",
    response_model=CursorPage[RenderJobOut],
    summary="Render history for a project",
)
async def list_renders(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
) -> CursorPage[RenderJobOut]:
    await require_project(session, ctx, project_id)
    ctx.require_scope("renders")
    page = await RenderJobRepository(session, ctx).list_gallery(
        project_id, limit=limit, cursor=cursor
    )
    return CursorPage[RenderJobOut](
        items=[RenderJobOut.of(job) for job in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


@router.get("/render-jobs/{job_id}", response_model=RenderJobOut, summary="Render job state")
async def get_render_job(
    job_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    ctx: TenantDep,
) -> RenderJobOut:
    job = await RenderJobRepository(session, ctx).require(job_id)
    depth = await _depth(queue.WORKER_RENDER) if job.status == "queued" else None
    output_url = None
    if job.output_url:
        token, _ = sign_download_token({"k": "render", "f": str(ctx.firm_id), "j": str(job_id)})
        output_url = build_download_url(request, token)
    return RenderJobOut.of(
        job,
        queue_depth=depth,
        events_url=_events_url(request, "/render-jobs/%s/events" % job_id),
        output_url=output_url,
    )


@router.post("/render-jobs/{job_id}/cancel", response_model=RenderJobOut, summary="Cancel a render")
async def cancel_render_job(
    job_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
) -> RenderJobOut:
    ctx.require_write("cancelling a job")
    repo = RenderJobRepository(session, ctx)
    job = await repo.require(job_id)
    if job.status in queue.JOB_TERMINAL_STATUSES:
        raise JobNotCancellableError("That render has already finished.")
    await queue.request_cancel(job_id)
    return RenderJobOut.of(await repo.cancel(job_id))


@router.get(
    "/render-jobs/{job_id}/events",
    summary="Live render progress (SSE)",
    response_model=JobEventOut,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def render_job_events(
    job_id: uuid.UUID,
    request: Request,
    ctx: TenantDep,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    async with session_scope() as session:
        job = await RenderJobRepository(session, ctx).require(job_id)
        initial = RenderJobOut.of(job)
    return _stream_job(request, job_id, initial, last_event_id)


# ---------------------------------------------------------------------------
# Sheets (§7)
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/sheets/generate",
    response_model=SheetSetOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate the municipal drawing set",
    dependencies=[Depends(rate_limit_sheet_jobs)],
)
async def generate_sheets(
    project_id: uuid.UUID,
    body: SheetsGenerateIn,
    request: Request,
    session: SessionDep,
    ctx: TenantDep,
) -> SheetSetOut:
    """Queue a sheet-set generation for a design version.

    Sheets need a version to be *of*: an area statement or an elevation is a statement
    about a specific state of the design, and a set generated from "whatever is current"
    could not be reproduced or defended later. A project with no version yet gets a 409
    telling the user to save one.
    """
    ctx.require_write("generating drawings")
    await require_project(session, ctx, project_id)

    design_version_id = await _resolve_design_version(
        session, ctx, project_id, body.design_version_id
    )
    if design_version_id is None:
        raise ApiError(
            "There's no saved version of this design to draw yet.",
            status=409,
            code="no_design_version",
            action="Save a version first, then generate the drawing set.",
        )

    job_id = queue.new_job_id()
    # Everything the worker needs that only the API can produce: the folded document
    # uploaded to storage, the area statement from the rules engine, the firm's title
    # block, and one presigned PUT per sheet per format (§13: the worker holds no
    # storage credentials). See routers/sheets.build_sheets_job.
    from garh_api.routers import sheets as sheets_support

    payload, assets, outputs = await sheets_support.build_sheets_job(
        session,
        ctx,
        project_id,
        design_version_id,
        job_id=job_id,
        kinds=body.kinds,
        scale_denominator=body.scale_denominator,
        sheet_size=body.sheet_size,
        dim_to_jamb=body.dim_to_jamb,
        title_block=body.title_block,
        revisions=body.revisions,
        formats=body.formats,
    )
    record = await queue.put_export_job(
        queue.ExportJob(
            id=job_id,
            firm_id=str(ctx.firm_id),
            project_id=str(project_id),
            kind="sheets",
            status="queued",
            design_version_id=str(design_version_id),
            # The presigned URLs are deliberately NOT stored on the job record: they
            # are credentials with a one-hour life, and a job record is read by the UI.
            params={k: v for k, v in payload.items() if k != "titleBlock"},
        )
    )
    await _enqueue_or_rollback(
        queue.JobEnvelope(
            job_id=job_id,
            kind=queue.JOB_DRAWINGS_GENERATE_SHEETS,
            firm_id=str(ctx.firm_id),
            project_id=str(project_id),
            design_version_id=str(design_version_id),
            actor_user_id=str(ctx.user_id) if ctx.user_id else None,
            request_id=ctx.request_id,
            payload=payload,
            assets=assets,
            outputs=outputs,
        )
    )

    existing = await SheetRepository(session, ctx).list_for_version(project_id, design_version_id)
    return SheetSetOut(
        project_id=project_id,
        design_version_id=design_version_id,
        sheets=[
            SheetOut.of(sheet, artifacts=_sheet_artifacts(request, ctx, sheet))
            for sheet in existing
        ],
        job=ExportJobOut.of(
            record, events_url=_events_url(request, "/export-jobs/%s/events" % job_id)
        ),
        generated_at=None,
    )


@router.get(
    "/projects/{project_id}/sheets",
    response_model=SheetSetOut,
    summary="The generated sheet set",
)
async def list_sheets(
    project_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    ctx: TenantDep,
    version: uuid.UUID | None = Query(default=None, description="A design version id."),
) -> SheetSetOut:
    await require_project(session, ctx, project_id)
    ctx.require_scope("sheets")
    design_version_id = await _resolve_design_version(session, ctx, project_id, version)
    if design_version_id is None:
        return SheetSetOut(project_id=project_id, design_version_id=None, sheets=[])
    sheets = await SheetRepository(session, ctx).list_for_version(project_id, design_version_id)
    generated = [s.generated_at for s in sheets if s.generated_at is not None]
    return SheetSetOut(
        project_id=project_id,
        design_version_id=design_version_id,
        sheets=[
            SheetOut.of(sheet, artifacts=_sheet_artifacts(request, ctx, sheet)) for sheet in sheets
        ],
        generated_at=max(generated) if generated else None,
    )


def _sheet_artifacts(request: Request, ctx: TenantCtx, sheet: Any) -> dict[str, str]:
    """Signed download URLs for whatever formats this sheet actually has.

    Read from ``layout.artifacts`` (written by the drawings worker), so a format that
    has not been rendered is simply absent rather than a link that 404s.
    """
    stored = dict(sheet.layout).get("artifacts")
    available = (
        [fmt for fmt in SHEET_FORMATS if stored.get(fmt)] if isinstance(stored, dict) else []
    )
    out: dict[str, str] = {}
    for fmt in available:
        token, _ = sign_download_token(
            {"k": "sheet", "f": str(ctx.firm_id), "s": str(sheet.id), "x": fmt}
        )
        out[fmt] = build_download_url(request, token)
    return out


@router.get(
    "/projects/{project_id}/sheets/{sheet_id}.{fmt}",
    response_model=DownloadOut,
    summary="Mint a short-lived signed link for one sheet",
)
async def get_sheet_download(
    project_id: uuid.UUID,
    sheet_id: uuid.UUID,
    fmt: str,
    request: Request,
    session: SessionDep,
    ctx: TenantDep,
) -> DownloadOut:
    """Returns the signed URL rather than the bytes.

    §11 says all downloads go through short-lived signed URLs, and handing back a URL
    lets the browser fetch a possibly-large PDF outside the JSON request cycle, with a
    resumable connection and a real progress bar.
    """
    await require_project(session, ctx, project_id)
    ctx.require_scope("sheets")
    if fmt not in SHEET_FORMATS:
        raise ApiError(
            "We can't export a sheet as %r." % fmt,
            status=400,
            code="unsupported_format",
            action="Ask for one of: %s." % ", ".join(SHEET_FORMATS),
        )
    sheet = await SheetRepository(session, ctx).require(sheet_id)
    if sheet.project_id != project_id:
        raise EntityNotFoundError("sheet", sheet_id)
    stored = dict(sheet.layout).get("artifacts")
    if not isinstance(stored, dict) or not stored.get(fmt):
        raise ArtifactNotReadyError("That sheet hasn't been rendered as %s yet." % fmt.upper())
    token, expires_at = sign_download_token(
        {"k": "sheet", "f": str(ctx.firm_id), "s": str(sheet_id), "x": fmt}
    )
    return DownloadOut(
        url=build_download_url(request, token),
        expires_at=expires_at,
        filename="%s.%s" % (sheet.number or sheet.kind, fmt),
        content_type=_SHEET_CONTENT_TYPES[fmt],
    )


# ---------------------------------------------------------------------------
# Exports (§11)
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/export",
    response_model=ExportJobOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Export the project (pdf-set | dxf | gltf | png-pack)",
    dependencies=[Depends(rate_limit_export_jobs)],
)
async def start_export(
    project_id: uuid.UUID,
    body: ExportIn,
    request: Request,
    session: SessionDep,
    ctx: TenantDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> ExportJobOut:
    """Queue an export; the finished job carries a short-lived signed download URL.

    Audited (§13: "audit_log on … exports") and metered (``credit_events``), because an
    export is the artefact that leaves the building — it is what a municipal office
    receives, so who produced it and when is worth keeping.
    """
    ctx.require_write("exporting")
    await require_project(session, ctx, project_id)

    guard = IdempotencyGuard(scope="export", key=idempotency_key, firm_id=ctx.firm_id)
    replayed = await guard.begin()
    if replayed is not None:
        return ExportJobOut.model_validate(replayed)

    try:
        design_version_id = await _resolve_design_version(
            session, ctx, project_id, body.design_version_id
        )
        job_id = queue.new_job_id()
        payload: dict[str, Any] = {
            "kind": body.kind,
            "designVersionId": str(design_version_id) if design_version_id else None,
            "sheetIds": [str(sid) for sid in body.sheet_ids],
            "includeDisclaimer": body.include_disclaimer,
            "options": body.options,
        }
        # An export needs the model (every kind is derived from it) and somewhere to
        # write. Both come from the sheets support module, so the export path and the
        # sheets path build their envelopes the same way — a divergence there is how
        # "the PDF looks different from the sheet on screen" happens.
        from garh_api.routers import sheets as sheets_support

        if design_version_id is None:
            raise ApiError(
                "There's no saved version of this design to export yet.",
                status=409,
                code="no_design_version",
                action="Save a version first, then export.",
            )
        export_assets, export_outputs = await sheets_support.build_export_job(
            session, ctx, project_id, design_version_id, job_id=job_id, kind=body.kind
        )
        record = await queue.put_export_job(
            queue.ExportJob(
                id=job_id,
                firm_id=str(ctx.firm_id),
                project_id=str(project_id),
                kind=body.kind,
                status="queued",
                design_version_id=str(design_version_id) if design_version_id else None,
                params=payload,
            )
        )
        await _enqueue_or_rollback(
            queue.JobEnvelope(
                job_id=job_id,
                kind=queue.JOB_DRAWINGS_EXPORT,
                firm_id=str(ctx.firm_id),
                project_id=str(project_id),
                design_version_id=str(design_version_id) if design_version_id else None,
                actor_user_id=str(ctx.user_id) if ctx.user_id else None,
                request_id=ctx.request_id,
                idempotency_key=idempotency_key,
                payload=payload,
                assets=export_assets,
                outputs=export_outputs,
            )
        )
        await AuditLogRepository(session, ctx).record(
            ACTION_EXPORT_CREATED,
            entity="project",
            entity_id=project_id,
            meta={"kind": body.kind, "jobId": job_id},
        )
        await CreditEventRepository(session, ctx).record(
            kind="export",
            qty=1,
            meta={"jobId": job_id, "projectId": str(project_id), "exportKind": body.kind},
        )
        out = ExportJobOut.of(
            record, events_url=_events_url(request, "/export-jobs/%s/events" % job_id)
        )
    except Exception:
        await guard.release()
        raise

    await guard.store(json.loads(out.model_dump_json(by_alias=True)))
    return out


@router.get("/export-jobs/{job_id}", response_model=ExportJobOut, summary="Export job state")
async def get_export_job(
    job_id: str,
    request: Request,
    ctx: TenantDep,
) -> ExportJobOut:
    """Export jobs live in Redis (see ``garh_api.queue``), keyed by firm.

    The firm id is part of the key, so another tenant's job id resolves to nothing —
    the same 404 a nonexistent job gives, which is the §13 answer.
    """
    record = await queue.get_export_job(ctx.firm_id, job_id)
    if record is None:
        raise EntityNotFoundError("export_job", job_id)
    out = ExportJobOut.of(
        record, events_url=_events_url(request, "/export-jobs/%s/events" % job_id)
    )
    if record.status == "succeeded" and record.download_url:
        token, _ = sign_download_token(
            {"k": "export", "f": str(ctx.firm_id), "j": job_id, "x": record.kind}
        )
        out.download_url = build_download_url(request, token)
    return out


@router.get(
    "/export-jobs/{job_id}/events",
    summary="Live export progress (SSE)",
    response_model=JobEventOut,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def export_job_events(
    job_id: str,
    request: Request,
    ctx: TenantDep,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    record = await queue.get_export_job(ctx.firm_id, job_id)
    if record is None:
        raise EntityNotFoundError("export_job", job_id)
    return _stream_job(request, job_id, ExportJobOut.of(record), last_event_id)


# ---------------------------------------------------------------------------
# Signed downloads (§11, §13 "signed S3 URLs ≤10min")
# ---------------------------------------------------------------------------


@router.get(
    "/downloads/{token}",
    summary="Redeem a signed download link",
    responses={
        307: {"description": "Redirect to the stored object."},
        404: {"description": "Bad signature, or nothing there for this firm."},
        409: {"description": "The artifact has not been produced yet."},
        410: {"description": "The link expired — mint a fresh one."},
    },
)
async def redeem_download(token: str, request: Request) -> Response:
    """Verify a signed token and redirect to the artifact.

    Unauthenticated on purpose: the token *is* the credential, and it is short-lived
    (≤10 min), single-purpose and firm-scoped. That is what makes a download work in a
    new browser tab, from a WhatsApp share, or from ``curl``.

    Redirect rather than proxy. Object storage serves bytes better than a Python event
    loop does, and streaming a 40 MB PDF set through the API would hold a worker for the
    length of the transfer.
    """
    payload = verify_download_token(token)
    firm_id = str(payload.get("f") or "")
    kind = str(payload.get("k") or "")
    if not firm_id or not kind:
        raise ApiError(
            "That download link is not valid.",
            status=404,
            code="not_found",
            action="Open the project and download again.",
        )

    target: str | None = None
    filename = "download"

    if kind == "export":
        record = await queue.get_export_job(firm_id, str(payload.get("j")))
        if record is None:
            raise ApiError(
                "That export is no longer available.",
                status=404,
                code="not_found",
                action="Run the export again from the project.",
            )
        # Re-sign from the deterministic object key rather than redirecting to the
        # presigned GET the worker reported at completion: those expire in ≤10 minutes
        # (§13), so a link opened fifteen minutes later landed on an S3 "Request has
        # expired" page. Same fix, same reason, as the sheet and render branches below.
        from garh_api.routers.imports import _sigv4_presign
        from garh_api.routers.sheets import EXPORT_ARTEFACTS, export_object_key

        settings = get_settings()
        # The render client pack also lands as a `png-pack` export record, but it built
        # its own zip under `renders/{firm}/packs/{pack}.zip` — re-signing the drawings
        # key for it would 404. Its `packId` param is the discriminator.
        is_render_pack = bool(dict(record.params or {}).get("packId"))
        if record.kind in EXPORT_ARTEFACTS and not is_render_pack:
            target = _sigv4_presign(
                "GET",
                export_object_key(firm_id, record.id, record.kind),
                ttl_seconds=settings.s3_signed_url_ttl_seconds,
                settings=settings,
            )
        else:
            # Kinds minted elsewhere (the render pack builds its own zip key).
            target = record.download_url
        filename = "garh-export.%s" % _EXPORT_EXTENSIONS.get(record.kind, "bin")
    else:
        # Sheet and render artifacts are rows, so redemption re-reads them through a
        # firm-scoped repository. The token's firm id is only a lookup hint — the
        # repository is what enforces the boundary, exactly as on an authenticated path.
        ctx = TenantCtx.for_system(
            uuid.UUID(firm_id), request_id=request.headers.get("x-request-id")
        )
        async with session_scope() as session:
            if kind == "sheet":
                sheet = await SheetRepository(session, ctx).require(
                    uuid.UUID(str(payload.get("s")))
                )
                fmt = str(payload.get("x") or "pdf")
                # `layout.artifacts[fmt]` holds the OBJECT KEY, not a URL: the presigned
                # GET the worker wrote through expires in ≤10 minutes (§13), and this
                # link may be redeemed the next morning. `fresh_sheet_url` re-signs from
                # the key, and passes a `file://` developer path straight through —
                # exactly what `renders.fresh_image_url` does for the same reason.
                from garh_api.routers.sheets import fresh_sheet_url

                target = fresh_sheet_url(sheet, fmt, firm_id, get_settings())
                filename = "%s.%s" % (sheet.number or sheet.kind, fmt)
            elif kind == "render":
                job = await RenderJobRepository(session, ctx).require(
                    uuid.UUID(str(payload.get("j")))
                )
                # Re-sign from the deterministic object key rather than redirecting to
                # `job.output_url`. That column holds the presigned GET the worker
                # reported at completion, and those expire in ~10 minutes (§13) — a
                # download link redeemed the next morning would land on an S3
                # "Request has expired" page. `fresh_image_url` mints a new one and
                # passes non-storage URLs (developer golden runs) straight through.
                from garh_api.routers.renders import fresh_image_url

                target = fresh_image_url(job, firm_id, get_settings())
                filename = "render-%s.png" % job.id
            else:
                raise ApiError(
                    "That download link is not valid.",
                    status=404,
                    code="not_found",
                    action="Open the project and download again.",
                )

    if not target:
        raise ArtifactNotReadyError("That file hasn't been produced yet.")
    if not target.startswith("http://") and not target.startswith("https://"):
        # A bare object key means storage has not been wired to hand out URLs. Say so
        # instead of redirecting the browser to a path that is not a URL.
        _log.error("download.unresolvable_target", download_kind=kind, target_shape="opaque-key")
        raise ArtifactNotReadyError(
            "That file exists but this server cannot hand out a link to it yet."
        )

    _log.info("download.redeemed", download_kind=kind, filename=filename)
    return Response(
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers={
            "location": target,
            "cache-control": "no-store",
            "content-disposition": 'attachment; filename="%s"' % filename,
        },
    )


@router.post(
    "/projects/{project_id}/downloads/audit",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Record that an export was downloaded (§13 audit trail)",
)
async def record_download(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
    export_job_id: str = Query(alias="exportJobId", max_length=64),
) -> Response:
    """Called by the client once a download actually starts.

    The redirect endpoint cannot write this: it is unauthenticated by design, so it has
    no user to attribute the action to, and a redirect may be followed by a prefetcher
    that never becomes a real download.
    """
    await require_project(session, ctx, project_id)
    await AuditLogRepository(session, ctx).record(
        ACTION_EXPORT_DOWNLOADED,
        entity="project",
        entity_id=project_id,
        meta={"jobId": export_job_id},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# SSE plumbing
# ---------------------------------------------------------------------------


def _parse_last_event_id(raw: str | None) -> int:
    """``Last-Event-ID`` → a sequence number. Anything unreadable replays from zero.

    Replaying too much is a cosmetic flicker; replaying too little loses the event that
    said the job finished, and the UI waits forever.
    """
    if not raw:
        return 0
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return 0


def _stream_job(
    request: Request,
    job_id: Any,
    initial: Any,
    last_event_id: str | None,
) -> EventSourceResponse:
    """Wrap :func:`queue.progress_stream` as SSE, opening with the current row state."""
    after_seq = _parse_last_event_id(last_event_id)

    async def publisher() -> AsyncIterator[dict[str, Any]]:
        # The row state first: a client that connects after the job finished must still
        # learn the outcome, and the backlog may have expired.
        yield {
            "event": "state",
            "data": initial.model_dump_json(by_alias=True),
        }
        if getattr(initial, "status", "") in queue.JOB_TERMINAL_STATUSES:
            return
        try:
            async for event in queue.progress_stream(job_id, after_seq=after_seq):
                if await request.is_disconnected():
                    break
                yield {
                    "id": str(event.seq),
                    "event": event.sse_event_name(),
                    "data": event.encode(),
                }
        except asyncio.CancelledError:  # pragma: no cover - client went away
            raise
        except Exception as exc:
            _log.warning(
                "sse.stream_failed",
                job_id=str(job_id),
                error="%s: %s" % (type(exc).__name__, exc),
            )
            yield {
                "event": "error",
                "data": json.dumps(
                    {
                        "code": "stream_interrupted",
                        "message": "The live updates stopped.",
                        "action": "Refresh to see the job's current state.",
                    }
                ),
            }

    return EventSourceResponse(
        publisher(),
        ping=SSE_PING_SECONDS,
        headers={
            "cache-control": "no-store",
            # Nginx buffers proxied responses by default, which turns a live stream into
            # one burst at the end. This is the documented opt-out.
            "x-accel-buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Lifecycle consumer — worker events → job rows
# ---------------------------------------------------------------------------


async def apply_lifecycle_record(session: AsyncSession, record: queue.LifecycleRecord) -> bool:
    """Apply one worker lifecycle event to its job row. True if a row was written.

    Runs under ``TenantCtx.for_system(firm_id)``: the firm comes from the envelope the
    API itself minted, so the write is scoped exactly like a user's would be. A worker
    cannot widen it, because the worker never gets a context at all — it only gets to
    append to a stream.
    """
    ctx = TenantCtx.for_system(uuid.UUID(record.firm_id))
    event = record.event
    job_id = uuid.UUID(record.job_id)

    if record.worker == queue.WORKER_SOLVER:
        repo = SolverJobRepository(session, ctx)
        if record.type == "started":
            await repo.mark_running(job_id)
        elif record.type == "succeeded":
            options = event.data.get("options")
            await repo.succeed(job_id, list(options) if isinstance(options, list) else [])
        elif record.type in ("failed", "dead_lettered"):
            await repo.fail(job_id, _problem_message(event))
        elif record.type == "cancelled":
            await repo.cancel(job_id)
        else:
            return False
        return True

    if record.worker == queue.WORKER_RENDER:
        repo_r = RenderJobRepository(session, ctx)
        if record.type == "started":
            await repo_r.set_progress(job_id, 0)
        elif record.type == "succeeded":
            output_url = event.data.get("outputUrl")
            if not output_url:
                await repo_r.fail(job_id, "The render finished but produced no image. Try again.")
            else:
                await repo_r.succeed(job_id, str(output_url))
        elif record.type in ("failed", "dead_lettered"):
            await repo_r.fail(job_id, _problem_message(event))
        elif record.type == "cancelled":
            await repo_r.cancel(job_id)
        else:
            return False
        return True

    # Drawings jobs: the *job* state lives in Redis, but a sheet SET is durable data.
    # This is the only path from "the worker drew them" to "the sheets table says so",
    # for the same reason the solver's is: workers hold no database connection.
    return await _apply_drawings_lifecycle(session, record)


def _problem_message(event: queue.ProgressEvent) -> str:
    """User-facing failure copy from a worker's problem body (golden rule 9)."""
    message = event.data.get("message") or event.message
    action = event.data.get("action")
    text = str(message or "That job didn't finish.")
    return "%s %s" % (text, action) if action else text


async def _apply_drawings_lifecycle(session: AsyncSession, record: queue.LifecycleRecord) -> bool:
    """Update the Redis-backed job record, and persist a finished sheet set.

    Returns True when a database row was written, so the caller's contract ("True if a
    row was written") stays accurate.

    The sheet set is persisted **before** the job record flips to ``succeeded``: the UI
    reacts to that transition by re-reading ``GET /projects/:id/sheets``, and a client
    that got there first would see an empty set and a finished job — the dishonest
    combination golden rule 9 is about. If the persist raises, the exception propagates
    and ``consume_job_events`` leaves the stream entry unacknowledged for redelivery,
    which is the right outcome: the sheets exist in storage, so replaying the event
    lands them rather than losing them.
    """
    existing = await queue.get_export_job(record.firm_id, record.job_id)
    if existing is None:
        return False
    event = record.event
    status_value = record.status

    wrote_rows = False
    if (
        status_value == "succeeded"
        and record.kind == queue.JOB_DRAWINGS_GENERATE_SHEETS
        and existing.project_id
        and existing.design_version_id
    ):
        from garh_api.routers.sheets import persist_sheet_set

        count = await persist_sheet_set(
            session,
            uuid.UUID(record.firm_id),
            uuid.UUID(existing.project_id),
            uuid.UUID(existing.design_version_id),
            dict(event.data),
        )
        wrote_rows = count > 0

    changes: dict[str, Any] = {"status": status_value}
    if event.percent is not None:
        changes["progress"] = event.percent
    if status_value == "succeeded":
        changes["progress"] = 100
        url = event.data.get("downloadUrl") or event.data.get("outputUrl")
        if url:
            changes["download_url"] = str(url)
    if status_value == "failed":
        changes["error"] = _problem_message(event)
    await queue.put_export_job(existing.evolve(**changes))
    return wrote_rows


async def consume_job_events(consumer_name: str, *, stop: asyncio.Event | None = None) -> None:
    """Background loop: drain ``garh:events:jobs`` into job rows. Runs for the app's life.

    Started from the lifespan hook in ``main.py``. Safe to run on every API replica —
    the consumer group hands each entry to exactly one of them.

    ``XACK`` happens only after ``session_scope()`` has committed. If this process dies
    mid-transaction the entry stays pending and another replica (or this one after a
    restart) redelivers it. Repository writes are idempotent for the same terminal
    event, so at-least-once delivery is safe; at-most-once would not be, because a lost
    ``succeeded`` leaves a job running forever in the UI.
    """
    await queue.ensure_job_events_group()
    _log.info("job_events.consumer_started", consumer=consumer_name)

    while stop is None or not stop.is_set():
        try:
            records = await queue.read_job_events(consumer_name)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.error("job_events.read_failed", error="%s: %s" % (type(exc).__name__, exc))
            await asyncio.sleep(2.0)
            continue

        for record in records:
            try:
                async with session_scope() as session:
                    await apply_lifecycle_record(session, record)
                await queue.ack_job_events([record.entry_id])
                _log.info(
                    "job_events.applied",
                    job_id=record.job_id,
                    job_kind=record.kind,
                    event_type=record.type,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Deliberately NOT acknowledged: the entry stays pending so it can be
                # retried or inspected. A silently dropped `succeeded` is a job the user
                # watches spin forever.
                _log.error(
                    "job_events.apply_failed",
                    job_id=record.job_id,
                    event_type=record.type,
                    entry_id=record.entry_id,
                    error="%s: %s" % (type(exc).__name__, exc),
                )

    _log.info("job_events.consumer_stopped", consumer=consumer_name)


def start_job_event_consumer(app: Any) -> asyncio.Task[None]:
    """Spawn :func:`consume_job_events` and remember it on ``app.state``."""
    stop = asyncio.Event()
    consumer_name = "api-%s-%s" % (
        datetime.now(UTC).strftime("%H%M%S"),
        uuid.uuid4().hex[:8],
    )
    task = asyncio.create_task(consume_job_events(consumer_name, stop=stop), name="garh-job-events")
    app.state.job_event_stop = stop
    app.state.job_event_task = task
    return task


async def stop_job_event_consumer(app: Any) -> None:
    """Ask the consumer to finish its current entry, then wait briefly for it."""
    stop = getattr(app.state, "job_event_stop", None)
    task = getattr(app.state, "job_event_task", None)
    if stop is not None:
        stop.set()
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(task, timeout=5.0)


__all__ = [
    "SHEET_FORMATS",
    "SSE_PING_SECONDS",
    "apply_lifecycle_record",
    "consume_job_events",
    "router",
    "start_job_event_consumer",
    "stop_job_event_consumer",
]
