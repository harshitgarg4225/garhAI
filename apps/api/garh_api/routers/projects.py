"""Projects, plots, briefs, versions and compliance (§11).

Two rules shape this file:

**Everything that changes the design goes through the op log.** ``PUT /plot`` and
``PUT /brief`` look like ordinary REST upserts, but they dispatch ops
(``plot.set_boundary``, ``plot.set_north``, ``plot.set_road``,
``plot.set_reg_profile``, ``brief.update``) through the same sequencer the canvas uses,
and *then* mirror the result into the ``plots`` / ``briefs`` tables. Those tables are a
projection that the solver and rules engine read cheaply; the log is the truth. Skipping
the op would mean the folded document and the projection could disagree, and nothing
downstream would know which one to believe (golden rule 1).

**Nothing here touches a table.** Every read and write goes through a repository holding
a :class:`~garh_api.tenancy.TenantCtx`, so a project id from another firm returns 404 by
construction rather than by remembering to add a filter (§13).
"""

from __future__ import annotations

import inspect
import json
import os
import re
import uuid
from typing import Any

from fastapi import APIRouter, Query, Response, status

from garh_api.compliance import (
    ComplianceUnavailable,
    cannot_evaluate_reason,
    evaluate_document,
)
from garh_api.config import get_settings
from garh_api.logging import get_logger
from garh_api.ratelimit import enforce_rate_limit, llm_per_firm_rule
from garh_api.repositories import (
    AuditLogRepository,
    BriefRepository,
    CommentRepository,
    ComplianceReportRepository,
    CreditEventRepository,
    DesignVersionRepository,
    OpRepository,
    PlotRepository,
    ProjectPatch,
    ProjectRepository,
)
from garh_api.repositories.audit_log import (
    ACTION_PROJECT_ARCHIVED,
    ACTION_PROJECT_DELETED,
    ACTION_REG_PROFILE_OVERRIDDEN,
)
from garh_api.routers import (
    ApiError,
    PageDep,
    SessionDep,
    TenantDep,
    active_branch,
    main_branch_id,
    repo_root,
    require_project,
)
from garh_api.routers.ops import (
    dispatch_ops,
    get_model_engine,
    load_project_state,
    wrap_snapshot,
)
from garh_api.schemas import CursorPage, DeletedOut
from garh_api.schemas.ops import OpIn
from garh_api.schemas.project import (
    BriefAssumption,
    BriefIn,
    BriefOut,
    BriefParseIn,
    BriefParseOut,
    ComplianceOut,
    PlotIn,
    PlotOut,
    ProjectCreate,
    ProjectDetailOut,
    ProjectOut,
    ProjectUpdate,
    VersionCreate,
    VersionOut,
    VersionRestoreOut,
)

_log = get_logger(__name__)

router = APIRouter(tags=["projects"])

#: Timeline pages are a scrubber, not an infinite list — one bounded request loads it.
MAX_VERSIONS_PER_PAGE = 200


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@router.get("/projects", response_model=CursorPage[ProjectOut], summary="List projects")
async def list_projects(
    session: SessionDep,
    ctx: TenantDep,
    page: PageDep,
    include_archived: bool = Query(default=False),
    project_status: str | None = Query(
        default=None, alias="status", description="Filter by dashboard status chip."
    ),
) -> CursorPage[ProjectOut]:
    """Newest first, keyset-paginated on ``(created_at, id)``."""
    result = await ProjectRepository(session, ctx).list(
        limit=page.limit,
        cursor=page.cursor,
        include_archived=include_archived,
        status=project_status,
    )
    return CursorPage[ProjectOut](
        items=[ProjectOut.of(p) for p in result.items],
        next_cursor=result.next_cursor,
        has_more=result.has_more,
    )


@router.post(
    "/projects",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
)
async def create_project(
    body: ProjectCreate,
    session: SessionDep,
    ctx: TenantDep,
) -> ProjectOut:
    """One required field, by design — Phase 0's "login → create project → fetch it"."""
    ctx.require_write("creating a project")
    project = await ProjectRepository(session, ctx).create(
        name=body.name,
        status=body.status,
        units=body.units,
        city_pack=body.city_pack,
        architect_of_record=body.architect_of_record,
    )
    _log.info("project.created", project_id=str(project.id))
    return ProjectOut.of(project)


@router.get(
    "/projects/{project_id}",
    response_model=ProjectDetailOut,
    summary="Project shell: project + plot + brief + head index",
)
async def get_project(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
) -> ProjectDetailOut:
    """One round trip to render the project screen (§15 micro-speed).

    The folded model is deliberately not included — that is ``GET /projects/:id/model``,
    which the canvas fetches in parallel and can cache by ``stateHash``.
    """
    project = await require_project(session, ctx, project_id)
    plot = await PlotRepository(session, ctx).get_for_project(project_id)
    brief = await BriefRepository(session, ctx).get_for_project(project_id)
    branch = await active_branch(session, ctx, project_id)
    head_idx = await OpRepository(session, ctx).head_idx(project_id, branch)
    latest = await DesignVersionRepository(session, ctx).latest(project_id, branch)
    open_comments = await CommentRepository(session, ctx).count_open(project_id)
    return ProjectDetailOut(
        project=ProjectOut.of(project),
        plot=PlotOut.of(plot) if plot is not None else None,
        brief=BriefOut.of(brief) if brief is not None else None,
        version_branch=branch,
        head_idx=head_idx,
        latest_version=VersionOut.of(latest) if latest is not None else None,
        open_comment_count=open_comments,
    )


@router.patch("/projects/{project_id}", response_model=ProjectOut, summary="Update a project")
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    session: SessionDep,
    ctx: TenantDep,
) -> ProjectOut:
    ctx.require_write("editing this project")
    repo = ProjectRepository(session, ctx)
    project = await repo.update(
        project_id,
        ProjectPatch(
            name=body.name,
            status=body.status,
            units=body.units,
            city_pack=body.city_pack,
            architect_of_record=body.architect_of_record,
        ),
    )
    if body.clear_architect_of_record:
        project = await repo.clear_architect_of_record(project_id)
    if body.status == "archived":
        await AuditLogRepository(session, ctx).record(
            ACTION_PROJECT_ARCHIVED, entity="project", entity_id=project_id
        )
    return ProjectOut.of(project)


@router.delete(
    "/projects/{project_id}",
    response_model=DeletedOut,
    summary="Delete a project (admin only, audited)",
)
async def delete_project(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
) -> DeletedOut:
    """Hard delete, cascading to plot/brief/ops/versions/jobs/sheets/shares.

    Admin-only and audited (§13). Archiving (``PATCH`` with ``status: archived``) is the
    reversible option the UI should offer first — a deleted op log cannot be recovered.
    """
    project = await require_project(session, ctx, project_id)
    audit = AuditLogRepository(session, ctx)
    await audit.record(
        ACTION_PROJECT_DELETED,
        entity="project",
        entity_id=project_id,
        meta={"name": project.name, "demo": project.demo},
    )
    deleted = await ProjectRepository(session, ctx).delete(project_id)
    if not deleted:
        raise ApiError(
            "That project no longer exists.",
            status=404,
            code="not_found",
            action="Go back to your dashboard.",
        )
    return DeletedOut(id=project_id, deleted=True)


# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/plot", response_model=PlotOut | None, summary="Get the plot")
async def get_plot(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
) -> PlotOut | None:
    """Null when the project has no plot yet — an empty state, not an error."""
    await require_project(session, ctx, project_id)
    plot = await PlotRepository(session, ctx).get_for_project(project_id)
    return PlotOut.of(plot) if plot is not None else None


@router.put("/projects/{project_id}/plot", response_model=PlotOut, summary="Set the plot")
async def put_plot(
    project_id: uuid.UUID,
    body: PlotIn,
    session: SessionDep,
    ctx: TenantDep,
) -> PlotOut:
    """Upsert the plot as ops, then mirror into ``plots``.

    Last-writer-wins: a plot form has no optimistic queue to rebase, so it appends at
    whatever HEAD is under the project's write lock. Conflict-safe editing is
    ``POST /ops`` with an explicit ``baseIdx``.
    """
    ctx.require_write("editing the plot")
    project = await require_project(session, ctx, project_id)

    ops: list[OpIn] = []
    if body.boundary is not None:
        ops.append(
            OpIn(
                type="plot.set_boundary",
                payload={"polygon": [{"x": p.x, "y": p.y} for p in body.boundary]},
            )
        )
    if body.north_deg is not None:
        ops.append(OpIn(type="plot.set_north", payload={"deg": body.north_deg}))
    if body.roads is not None:
        for road in body.roads:
            ops.append(
                OpIn(
                    type="plot.set_road",
                    payload={"edgeIndex": road.edge_index, "widthMm": road.width_mm},
                )
            )
    if body.reg_profile is not None:
        city_pack = body.reg_profile.get("cityPack") or project.city_pack
        overrides = body.reg_profile.get("overrides")
        if not isinstance(overrides, dict):
            # A profile posted without the {cityPack, overrides} shape is treated as
            # overrides wholesale rather than silently dropped.
            overrides = {k: v for k, v in body.reg_profile.items() if k != "cityPack"}
        ops.append(
            OpIn(
                type="plot.set_reg_profile",
                payload={"cityPack": city_pack, "overrides": overrides},
            )
        )
        await AuditLogRepository(session, ctx).record(
            ACTION_REG_PROFILE_OVERRIDDEN,
            entity="plot",
            entity_id=project_id,
            meta={"cityPack": city_pack, "overrideKeys": sorted(overrides.keys())},
        )

    if ops:
        group_id = uuid.uuid4()
        await dispatch_ops(session, ctx, project_id, ops, source="manual", group_id=group_id)

    plot = await PlotRepository(session, ctx).upsert(
        project_id,
        boundary=(
            [{"x": p.x, "y": p.y} for p in body.boundary] if body.boundary is not None else None
        ),
        north_deg=body.north_deg,
        roads=(
            [{"edgeIndex": r.edge_index, "widthMm": r.width_mm} for r in body.roads]
            if body.roads is not None
            else None
        ),
        reg_profile=body.reg_profile,
        source=body.source,
    )
    return PlotOut.of(plot)


# ---------------------------------------------------------------------------
# Brief
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/brief", response_model=BriefOut | None, summary="Get the brief")
async def get_brief(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
) -> BriefOut | None:
    await require_project(session, ctx, project_id)
    brief = await BriefRepository(session, ctx).get_for_project(project_id)
    return BriefOut.of(brief) if brief is not None else None


@router.put("/projects/{project_id}/brief", response_model=BriefOut, summary="Set the brief")
async def put_brief(
    project_id: uuid.UUID,
    body: BriefIn,
    session: SessionDep,
    ctx: TenantDep,
) -> BriefOut:
    """Upsert the brief as a ``brief.update`` op, then mirror into ``briefs``.

    ``merge=true`` gives RFC 7386 semantics (null deletes a key) — the same semantics as
    the copilot's op 5, so the form and the copilot share one write path rather than two
    that drift.
    """
    ctx.require_write("editing the brief")
    await require_project(session, ctx, project_id)
    repo = BriefRepository(session, ctx)

    patch: dict[str, Any] = dict(body.data or {})
    if body.vastu_mode is not None:
        patch["vastuMode"] = body.vastu_mode

    if patch:
        await dispatch_ops(
            session,
            ctx,
            project_id,
            [OpIn(type="brief.update", payload={"patch": patch})],
            source="manual",
        )

    if body.data is not None and not body.merge:
        brief = await repo.upsert(
            project_id,
            data=body.data,
            vastu_mode=body.vastu_mode,
            completeness=body.completeness,
        )
    elif patch:
        brief = await repo.merge_patch(project_id, patch, completeness=body.completeness)
        if body.vastu_mode is not None:
            brief = await repo.set_vastu_mode(project_id, body.vastu_mode)
    else:
        brief = await repo.upsert(
            project_id, vastu_mode=body.vastu_mode, completeness=body.completeness
        )
    return BriefOut.of(brief)


@router.post(
    "/projects/{project_id}/brief/parse",
    response_model=BriefParseOut,
    summary="Parse free-text into a structured brief (LLM provider interface)",
)
async def parse_brief(
    project_id: uuid.UUID,
    body: BriefParseIn,
    session: SessionDep,
    ctx: TenantDep,
) -> BriefParseOut:
    """Free text → brief fields + an assumption for every value the model invented.

    Locked decision: **the LLM never emits geometry.** This endpoint returns counts,
    flags and preferences only. Room sizes come from NBC minimums and benchmarks in the
    solver, never from a language model — see ``_MockBriefParser`` for the shape the
    real provider must also produce.

    **A parse is a pure suggestion — this endpoint never writes the brief.** The UI
    shows the parse and its assumptions as editable chips first (golden rule 4); when
    the architect accepts, the *client* dispatches a ``brief.update`` op through the
    sequencer — the same undoable path as typing into the form, with the review step
    in between. ``tests/test_brief_parse.py`` pins the read-only property. The only
    rows this handler writes are metering (``credit_events``): the call spends money
    at a third party and must be billed whether or not the suggestion is accepted.

    ``body.apply`` is accepted for wire compatibility and deliberately not honoured;
    the response says so in ``warnings`` and ``applied`` is always ``false``.
    """
    ctx.require_write("parsing a brief")
    await require_project(session, ctx, project_id)
    settings = get_settings()

    # §13 rate limits. This is the ONLY route in the API that spends money at a third
    # party on every call, so it is the only one where an unbounded loop is a billing
    # incident rather than a load problem. The rule fails closed on purpose (see
    # llm_per_firm_rule): if we cannot count the call, we do not make it.
    #
    # Checked BEFORE the provider is resolved, so a limited request costs nothing.
    await enforce_rate_limit(llm_per_firm_rule(settings), "firm:%s" % ctx.firm_id)

    parser = _resolve_brief_parser()
    parsed_or_awaitable = parser.parse_brief(
        text=body.text, known=dict(body.known_fields), project_id=str(project_id)
    )
    # The real parser is a coroutine (it is a network call under
    # PROVIDER_LLM=anthropic); the offline mock is not. Accept both rather than
    # forcing one shape on every future provider.
    parsed = (
        await parsed_or_awaitable
        if inspect.isawaitable(parsed_or_awaitable)
        else parsed_or_awaitable
    )

    warnings = [str(w) for w in (parsed.get("warnings") or [])]
    if body.apply:
        warnings.append(
            "Parsing no longer applies the brief for you. Review the suggestion, then "
            "save it from the brief form — every change stays undoable."
        )

    out = BriefParseOut(
        provider=parsed.get("provider") or settings.provider_llm,
        data=dict(parsed.get("data") or {}),
        assumptions=[
            BriefAssumption(
                field=str(item.get("field") or ""),
                value=item.get("value"),
                reason=str(item.get("reason") or ""),
                cite=item.get("cite"),
            )
            for item in (parsed.get("assumptions") or [])
        ],
        completeness=int(parsed.get("completeness") or 0),
        warnings=warnings,
        applied=False,
    )

    # §2: "credit_events — render/solver/LLM metering from day one." Recorded after
    # the call returns, so a provider failure is not billed, and with the provider,
    # model and token counts in meta because reconciliation needs to know WHICH
    # provider ran and what it consumed — a `mock` row costs nothing and must be
    # distinguishable. Metering is the one write this endpoint performs.
    usage = parsed.get("usage") or {}
    await CreditEventRepository(session, ctx).record(
        kind="llm",
        qty=1,
        meta={
            "route": "brief.parse",
            "projectId": str(project_id),
            "provider": out.provider,
            "model": parsed.get("model")
            or (settings.anthropic_model if out.provider == "anthropic" else None),
            "inputTokens": int(usage.get("inputTokens") or 0),
            "outputTokens": int(usage.get("outputTokens") or 0),
        },
    )
    return out


# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/compliance",
    response_model=ComplianceOut,
    summary="Latest frozen compliance report",
)
async def get_compliance(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
    version: uuid.UUID | None = Query(default=None, description="A design version id."),
) -> ComplianceOut:
    """The frozen rules-engine result for a version (§6).

    The live editor re-runs the rules in-process on every edit (<100ms, debounced) and
    does not persist those runs. What lands in ``compliance_reports`` is what the area
    statement quotes, what the solver critic recorded and what a client saw on a share
    link — one source of numbers (§7).

    ``evaluated: false`` is an honest "nobody has run the rules against this version
    yet". It is never rendered as a pass.
    """
    await require_project(session, ctx, project_id)
    ctx.require_scope("compliance")
    repo = ComplianceReportRepository(session, ctx)
    report = (
        await repo.latest_for_version(project_id, version)
        if version is not None
        else await repo.latest_for_project(project_id)
    )
    if report is not None:
        return ComplianceOut.of(project_id, report)

    # Nothing frozen for this version. Rather than answering "not checked" forever
    # (which is what happened while garh_rules was unwired), run the engine now and
    # return the result WITHOUT persisting it: an unnamed working state is not a
    # version, and freezing every editor keystroke would fill compliance_reports with
    # rows nothing ever quotes. `live: true` tells the client which it got.
    branch = await active_branch(session, ctx, project_id)
    try:
        state = await load_project_state(session, ctx, project_id, branch)
    except ApiError:
        # No model engine available — say so honestly instead of implying a pass.
        return ComplianceOut.not_evaluated(project_id, version)

    blocked = cannot_evaluate_reason(state.document)
    if blocked is not None:
        return ComplianceOut.not_evaluated(project_id, version, reason=blocked)
    try:
        payload, pack_versions = evaluate_document(state.document)
    except ComplianceUnavailable as exc:
        _log.warning("compliance.unavailable", project_id=str(project_id), error=str(exc))
        return ComplianceOut.not_evaluated(project_id, version, reason=str(exc))
    return ComplianceOut.live_run(project_id, payload, pack_versions)


async def freeze_compliance_report(
    session: Any,
    ctx: Any,
    project_id: uuid.UUID,
    document: dict[str, Any],
    design_version_id: uuid.UUID,
) -> None:
    """Run the rules and store the result against a design version.

    Never raises into the caller: a version must still save when the rules engine
    cannot run (a plot not drawn yet is the common case). The absence of a row is the
    honest signal, and ``GET /compliance`` renders it as "not checked yet".
    """
    blocked = cannot_evaluate_reason(document)
    if blocked is not None:
        _log.info("compliance.skipped", project_id=str(project_id), reason=blocked)
        return
    try:
        payload, pack_versions = evaluate_document(document)
    except ComplianceUnavailable as exc:
        _log.warning("compliance.unavailable", project_id=str(project_id), error=str(exc))
        return
    results = payload.get("results")
    await ComplianceReportRepository(session, ctx).record(
        project_id,
        results=list(results) if isinstance(results, list) else [],
        pack_versions=pack_versions,
        design_version_id=design_version_id,
    )


# ---------------------------------------------------------------------------
# Versions (§11 POST/GET /versions, POST /versions/:vid/restore)
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/versions",
    response_model=CursorPage[VersionOut],
    summary="Version timeline",
)
async def list_versions(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
    limit: int = Query(default=100, ge=1, le=MAX_VERSIONS_PER_PAGE),
) -> CursorPage[VersionOut]:
    """Newest first, snapshot payloads excluded.

    Bounded ``limit`` rather than a cursor: this backs the header timeline scrubber,
    which wants the whole (small) list in one request. Loading snapshots here would ship
    megabytes to render a row of dots.
    """
    await require_project(session, ctx, project_id)
    summaries = await DesignVersionRepository(session, ctx).list_summaries(project_id, limit=limit)
    return CursorPage[VersionOut](
        items=[VersionOut.of(v) for v in summaries],
        next_cursor=None,
        has_more=len(summaries) == limit,
    )


@router.post(
    "/projects/{project_id}/versions",
    response_model=VersionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Save a named version",
)
async def create_version(
    project_id: uuid.UUID,
    body: VersionCreate,
    session: SessionDep,
    ctx: TenantDep,
) -> VersionOut:
    """Fold the current state and store it as a named snapshot, so restore is instant."""
    ctx.require_write("saving a version")
    await require_project(session, ctx, project_id)
    engine = get_model_engine()
    branch = await active_branch(session, ctx, project_id)
    op_repo = OpRepository(session, ctx)
    await op_repo.acquire_branch_write_lock(project_id, branch)

    state = await load_project_state(session, ctx, project_id, branch)
    head_seq = await op_repo.head_seq(project_id, branch)
    version = await DesignVersionRepository(session, ctx).create_named(
        project_id,
        name=body.name,
        version_branch=branch,
        snapshot=wrap_snapshot(
            state.document,
            version_branch=branch,
            at_idx=state.head_idx,
            at_seq=head_seq,
            state_hash=state.state_hash,
            schema_version=engine.schema_version,
        ),
        op_seq_end=head_seq,
    )
    # §7: the area statement on the sheet, the compliance annexure and the share-link
    # viewer must all quote ONE set of numbers. That is only true if the rules run
    # once, at version time, and the result is frozen with the snapshot. A live
    # re-run later would be a different design.
    await freeze_compliance_report(session, ctx, project_id, state.document, version.id)

    _log.info(
        "version.created",
        project_id=str(project_id),
        version_id=str(version.id),
        at_idx=state.head_idx,
    )
    return VersionOut.of(version)


@router.post(
    "/projects/{project_id}/versions/{version_id}/restore",
    response_model=VersionRestoreOut,
    summary="Restore a version (forks a new branch; history is never deleted)",
)
async def restore_version(
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
) -> VersionRestoreOut:
    """Restore by *forking*, not by rewinding.

    The op log is append-only (§4), so "go back to v12" copies v12's branch prefix onto
    a fresh branch and writes a new named version there. Everything after v12 still
    exists on the old branch — the user can restore forward again, and no work is ever
    destroyed by a mis-click.
    """
    ctx.require_write("restoring a version")
    await require_project(session, ctx, project_id)
    dv_repo = DesignVersionRepository(session, ctx)
    op_repo = OpRepository(session, ctx)

    source_version = await dv_repo.require(version_id)
    if source_version.snapshot is None:
        raise ApiError(
            "That version's saved state was pruned, so it cannot be restored.",
            status=409,
            code="snapshot_pruned",
            action="Pick another version from the timeline.",
        )
    from garh_api.routers.ops import unwrap_snapshot

    unwrapped = unwrap_snapshot(source_version.snapshot)
    if unwrapped is None:
        raise ApiError(
            "That version was saved in a format this server cannot restore.",
            status=409,
            code="snapshot_unreadable",
            action="Pick another version from the timeline.",
        )

    source_branch = source_version.version_branch
    target_branch = await op_repo.new_branch_id()
    await op_repo.acquire_branch_write_lock(project_id, source_branch)
    copied = await op_repo.copy_branch(
        project_id, source_branch, target_branch, through_idx=unwrapped.at_idx
    )
    head_seq = await op_repo.head_seq(project_id, target_branch)
    restored = await dv_repo.create_named(
        project_id,
        name="Restored: %s" % (source_version.name or "checkpoint"),
        version_branch=target_branch,
        snapshot=wrap_snapshot(
            unwrapped.document,
            version_branch=target_branch,
            at_idx=unwrapped.at_idx,
            at_seq=head_seq,
            state_hash=unwrapped.state_hash,
            schema_version=unwrapped.schema_version,
        ),
        op_seq_end=head_seq,
        parent_id=source_version.id,
    )
    head_idx = await op_repo.head_idx(project_id, target_branch)
    _log.info(
        "version.restored",
        project_id=str(project_id),
        from_version=str(version_id),
        to_version=str(restored.id),
        ops_copied=copied,
    )
    return VersionRestoreOut(
        version=VersionOut.of(restored),
        version_branch=target_branch,
        head_idx=head_idx,
        ops_copied=copied,
        state_hash=unwrapped.state_hash,
    )


@router.get(
    "/projects/{project_id}/branch",
    summary="The project's active op branch (debug/diagnostics)",
)
async def get_branch(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
    response: Response,
) -> dict[str, Any]:
    """Exposes the derived-branch decision so a support engineer can see it.

    Not part of §11's surface; it exists because "which branch am I on?" is otherwise
    invisible, and a derived value nobody can inspect is a value nobody can debug.
    """
    await require_project(session, ctx, project_id)
    branch = await active_branch(session, ctx, project_id)
    head_idx = await OpRepository(session, ctx).head_idx(project_id, branch)
    response.headers["cache-control"] = "no-store"
    return {
        "projectId": str(project_id),
        "versionBranch": str(branch),
        "defaultBranch": str(main_branch_id(project_id)),
        "headIdx": head_idx,
    }


# ---------------------------------------------------------------------------
# LLM provider interface (§10) — mock by default, always mockable
# ---------------------------------------------------------------------------


class _MockBriefParser:
    """Deterministic, offline brief parser. The default in dev, test and CI.

    Two sources, in order:

    1. A fixture in ``fixtures/briefs/`` whose ``match`` keywords appear in the text —
       this is what the copilot/solver golden corpora use, so a fixture change is a
       visible diff rather than a silent behaviour change.
    2. Keyword extraction over the text (BHK count, storeys, facing, budget, Vastu,
       parking, pooja). Everything it could not find becomes an *assumption*, never a
       silent default — §10: "Anything not stated → assumption, never silence."

    It emits no geometry. Not one coordinate, not one room size.
    """

    provider_name = "mock"

    _BHK = re.compile(r"(\d+)\s*bhk", re.IGNORECASE)
    _BEDROOMS = re.compile(r"(\d+)\s*(?:bed\s?rooms?|bedrooms?)", re.IGNORECASE)
    _BATHS = re.compile(r"(\d+)\s*(?:bath\s?rooms?|baths?|toilets?)", re.IGNORECASE)
    _STOREYS = re.compile(r"\bg\s*\+\s*(\d+)\b", re.IGNORECASE)
    _BUDGET_LAKH = re.compile(r"(?:₹|rs\.?|inr)?\s*(\d+(?:\.\d+)?)\s*(lakhs?|lacs?)", re.IGNORECASE)
    _BUDGET_CRORE = re.compile(
        r"(?:₹|rs\.?|inr)?\s*(\d+(?:\.\d+)?)\s*(crores?|cr)\b", re.IGNORECASE
    )
    _FACING = re.compile(
        r"\b(north[-\s]?east|south[-\s]?east|north[-\s]?west|south[-\s]?west|north|south|east|west)"
        r"[-\s]?facing\b",
        re.IGNORECASE,
    )
    _CARS = re.compile(r"(\d+)\s*(?:car|cars|parking)", re.IGNORECASE)

    def parse_brief(self, *, text: str, known: dict[str, Any], project_id: str) -> dict[str, Any]:
        fixture = self._match_fixture(text)
        if fixture is not None:
            fixture.setdefault("provider", self.provider_name)
            return fixture

        data: dict[str, Any] = {}
        assumptions: list[dict[str, Any]] = []
        lowered = text.lower()

        bedrooms = self._first_int(self._BHK, text) or self._first_int(self._BEDROOMS, text)
        if bedrooms is not None:
            data["bedrooms"] = bedrooms
        else:
            data["bedrooms"] = 3
            assumptions.append(
                {
                    "field": "bedrooms",
                    "value": 3,
                    "reason": "The brief did not say. 3BHK is the most common Indian "
                    "independent-house programme.",
                }
            )

        baths = self._first_int(self._BATHS, text)
        if baths is not None:
            data["bathrooms"] = baths
        else:
            inferred = max(2, int(data["bedrooms"]))
            data["bathrooms"] = inferred
            assumptions.append(
                {
                    "field": "bathrooms",
                    "value": inferred,
                    "reason": "Not stated — assumed one per bedroom, minimum two.",
                }
            )

        storeys = self._first_int(self._STOREYS, text)
        if storeys is not None:
            data["floorsAboveGround"] = storeys
        else:
            data["floorsAboveGround"] = 1
            assumptions.append(
                {
                    "field": "floorsAboveGround",
                    "value": 1,
                    "reason": "Not stated — assumed G+1, the usual fit for a small urban plot.",
                }
            )

        facing = self._FACING.search(text)
        if facing:
            data["plotFacing"] = facing.group(1).replace(" ", "-").lower()
        else:
            assumptions.append(
                {
                    "field": "plotFacing",
                    "value": None,
                    "reason": "Not stated — the plot's road edge will decide the entrance side.",
                }
            )

        budget = self._budget_inr(text)
        if budget is not None:
            data["budgetInr"] = budget
        else:
            assumptions.append(
                {
                    "field": "budgetInr",
                    "value": None,
                    "reason": "No budget given — costing will use the city benchmark ₹/sqft.",
                }
            )

        if "vastu" in lowered:
            mode = "strict" if "strict" in lowered else "advisory"
            data["vastuMode"] = mode
        else:
            data["vastuMode"] = "off"
            assumptions.append(
                {
                    "field": "vastuMode",
                    "value": "off",
                    "reason": "Vastu was not mentioned — left off; switch it on any time.",
                }
            )

        cars = self._first_int(self._CARS, text)
        if cars is not None:
            data["carParking"] = cars
        else:
            data["carParking"] = 1
            assumptions.append(
                {
                    "field": "carParking",
                    "value": 1,
                    "reason": "Not stated — one covered car space, which most bye-laws require.",
                }
            )

        data["poojaRoom"] = "pooja" in lowered or "puja" in lowered or "mandir" in lowered
        if "servant" in lowered or "maid" in lowered:
            data["servantRoom"] = True
        if "lift" in lowered or "elevator" in lowered:
            data["lift"] = True

        # Caller-supplied values always win over anything inferred here.
        for key, value in known.items():
            data[key] = value

        stated = len(data) - len(assumptions)
        completeness = max(0, min(100, int(round(100.0 * stated / max(1, len(data))))))
        return {
            "provider": self.provider_name,
            "data": data,
            "assumptions": assumptions,
            "completeness": completeness,
            "warnings": [],
        }

    def _match_fixture(self, text: str) -> dict[str, Any] | None:
        directory = os.path.join(
            os.environ.get("FIXTURE_DIR") or os.path.join(repo_root(), "fixtures"), "briefs"
        )
        if not os.path.isdir(directory):
            return None
        lowered = text.lower()
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(directory, name), encoding="utf-8") as handle:
                    fixture = json.load(handle)
            except (OSError, ValueError):
                continue
            if not isinstance(fixture, dict):
                continue
            keywords = fixture.get("match")
            if isinstance(keywords, list) and any(str(k).lower() in lowered for k in keywords):
                _log.info("brief_parse.fixture_hit", fixture=name)
                return {
                    "provider": self.provider_name,
                    "data": dict(fixture.get("data") or {}),
                    "assumptions": list(fixture.get("assumptions") or []),
                    "completeness": int(fixture.get("completeness") or 0),
                    "warnings": list(fixture.get("warnings") or []),
                }
        return None

    @staticmethod
    def _first_int(pattern: re.Pattern[str], text: str) -> int | None:
        match = pattern.search(text)
        if not match:
            return None
        try:
            value = int(match.group(1))
        except (TypeError, ValueError):
            return None
        return value if 0 < value <= 20 else None

    @classmethod
    def _budget_inr(cls, text: str) -> int | None:
        """Money in whole rupees — never a float (the model core rejects floats)."""
        crore = cls._BUDGET_CRORE.search(text)
        if crore:
            return int(round(float(crore.group(1)) * 10_000_000))
        lakh = cls._BUDGET_LAKH.search(text)
        if lakh:
            return int(round(float(lakh.group(1)) * 100_000))
        return None


def _resolve_brief_parser() -> Any:
    """Return the real brief parser, falling back to the in-router mock.

    The real one is ``services.llm.get_brief_parser()`` — a
    :class:`services.llm.adapters.BriefParserAdapter` wrapping
    :class:`services.llm.brief.BriefParser`, which schema-validates the model's output,
    partitions ``stated`` from ``assumptions`` and redacts PII before the prompt leaves
    the process. Its ``parse_brief`` is a coroutine; the caller awaits whatever comes
    back, so a synchronous implementation also works.

    HISTORY, so this is not re-broken: this function used to probe
    ``get_llm_provider()`` for a ``parse_brief`` attribute. ``LlmProvider`` has never
    had one (its protocol is ``complete_json``/``aclose``), so the probe always failed
    and every request silently ran :class:`_MockBriefParser` — with the entire
    ``services/llm`` package dead. If you change the name below, change it in
    ``services/llm/adapters.py`` and ``services/llm/__init__.__all__`` too.

    ``_MockBriefParser`` remains the fallback rather than an error, because
    ``services/`` is not importable from every context the API runs in (a pure-API
    image need not carry the worker tree), and an unreachable provider must degrade to
    an honest offline parser, not a 500. ``provider`` in the response always says which
    one answered.
    """
    import importlib

    for module_name in ("garh_api.llm", "services.llm"):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        factory = getattr(module, "get_brief_parser", None)
        if callable(factory):
            try:
                parser = factory()
            except Exception as exc:
                # PROVIDER_LLM=anthropic with no key raises here. Degrade loudly.
                _log.warning(
                    "brief_parse.provider_unavailable",
                    module=module_name,
                    error=str(exc),
                )
                break
            if hasattr(parser, "parse_brief"):
                return parser
        # Legacy hook, kept so an in-process provider object can still be injected.
        parser = getattr(module, "LLM_PROVIDER", None)
        if parser is not None and hasattr(parser, "parse_brief"):
            return parser
    _log.info("brief_parse.using_router_mock")
    return _MockBriefParser()


__all__ = ["router"]
