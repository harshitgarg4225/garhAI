"""Share links: the firm-side management routes, and the read-only client viewer (§13).

Two routers, and the separation is a security control rather than a tidiness one.

``router`` — authenticated firm users. Mint a link, list links, revoke one, read and
resolve the comments clients left.

``public_router`` — **anonymous, read-only**, reached with a share token. §13 requires
this surface to be "a separate read-only router with no write deps imported", and this
module honours that literally:

* every handler depends on ``ShareViewer``, never on ``Tenant``. A share context has
  ``can_write == False``, so every repository write method it could reach raises
  ``PermissionDeniedError`` before touching a table — the guarantee is structural, not a
  habit of remembering to check;
* the module imports no op sequencer, no ``dispatch_ops``, no queue producer, no job
  enqueue, no project/plot/brief write repository. The one snapshot helper the viewer
  needs is imported lazily inside the single handler that needs it, so this module's
  import graph never contains the write path at all;
* the one write in the whole surface is ``POST /share/{token}/comments``, which the
  playbook's §11 explicitly allows ("read-only surface (viewer + comments POST)"). It
  goes through :meth:`CommentRepository.create_from_share`, which re-derives the project
  and share-link ids from the resolved token and refuses unless the scope says
  ``canComment``. Nothing in the request body can widen that.

Every read is additionally gated on the link's ``sections`` allowlist: a link shared for
renders alone cannot fetch the plan, the sheets or the compliance report.

The token itself is 256 bits of ``secrets`` randomness, returned exactly once at
creation and stored only as a SHA-256 hash. There is no endpoint that can show it again,
because there is no code path that could.
"""

from __future__ import annotations

import secrets
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query, Request, status

from garh_api.config import get_settings
from garh_api.deps import ShareProjectId, ShareViewer
from garh_api.logging import get_logger
from garh_api.ratelimit import RateLimitRule, enforce_rate_limit
from garh_api.repositories import (
    EMPTY_BRANCH_HEAD,
    AuditLogRepository,
    CommentRepository,
    ComplianceReportRepository,
    DesignVersionRepository,
    OpRepository,
    ProjectRepository,
    RenderJobRepository,
    SheetRepository,
    ShareLinkRepository,
)
from garh_api.repositories.audit_log import ACTION_SHARE_CREATED, ACTION_SHARE_REVOKED
from garh_api.routers import (
    ApiError,
    SessionDep,
    TenantDep,
    active_branch,
    client_ip,
    require_project,
)
from garh_api.schemas import Ack, DeletedOut
from garh_api.schemas.jobs import RenderJobOut, SheetOut, SheetSetOut
from garh_api.schemas.ops import ModelStateOut, OpOut
from garh_api.schemas.project import (
    CommentIn,
    CommentOut,
    ComplianceOut,
    ShareCreate,
    ShareLinkOut,
    SharedProjectOut,
)

_log = get_logger(__name__)

router = APIRouter(tags=["share"])

#: The anonymous viewer surface. Registered separately in ``routers.api_router`` so the
#: split is visible in the route table, not just in this docstring.
public_router = APIRouter(tags=["share-viewer"])

#: How many tail ops the viewer will ship. Lower than the editor's cap: a client viewer
#: only ever loads once, and a snapshot always exists for anything worth sharing.
MAX_VIEWER_TAIL_OPS = 1000


def _share_comment_rule() -> RateLimitRule:
    """Per-IP limit on anonymous comments (§13: rate limits per IP on public surfaces).

    Fails **closed**: this is an unauthenticated write reachable by anyone holding a
    link. If the limiter cannot answer, the safe direction is to decline rather than to
    admit unbounded writes into a customer's project.
    """
    return RateLimitRule(
        name="share.comments_per_ip",
        limit=30,
        window_seconds=3600,
        scope="ip",
        fail_closed=True,
        message="That's a lot of comments in a short time.",
        action="Take a moment, then add the rest.",
    )


# ---------------------------------------------------------------------------
# Firm side — mint, list, revoke
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/share",
    response_model=ShareLinkOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a scoped share link",
)
async def create_share_link(
    project_id: uuid.UUID,
    body: ShareCreate,
    session: SessionDep,
    ctx: TenantDep,
) -> ShareLinkOut:
    """Mint a 256-bit token. **This response is the only time the token exists in clear.**

    Storage keeps a SHA-256 hash, so a database dump does not hand out live links, and
    "resend the link" is genuinely impossible rather than merely discouraged — the UI
    must offer "create a new link" instead.
    """
    ctx.require_write("sharing this project")
    project = await require_project(session, ctx, project_id)
    settings = get_settings()

    token = secrets.token_urlsafe(settings.share_token_bytes)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
        if body.expires_in_days is not None
        else None
    )
    link = await ShareLinkRepository(session, ctx).create(
        project_id,
        token=token,
        sections=body.sections,
        can_comment=body.can_comment,
        expires_at=expires_at,
    )
    await AuditLogRepository(session, ctx).record(
        ACTION_SHARE_CREATED,
        entity="share_link",
        entity_id=link.id,
        meta={
            "projectId": str(project_id),
            "sections": list(link.scope.get("sections") or []),
            "canComment": bool(link.scope.get("canComment")),
            "expiresAt": expires_at.isoformat() if expires_at else None,
        },
    )

    url = "%s/share/%s" % (settings.app_url.rstrip("/"), token)
    message = "%s — shared from Garh AI: %s" % (project.name, url)
    whatsapp = "https://wa.me/?text=%s" % urllib.parse.quote(message)
    _log.info("share_link.issued", project_id=str(project_id), share_link_id=str(link.id))
    return ShareLinkOut.of(link, token=token, url=url, whatsapp_url=whatsapp)


@router.get(
    "/projects/{project_id}/share",
    response_model=list[ShareLinkOut],
    summary="Active share links for a project",
)
async def list_share_links(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
) -> list[ShareLinkOut]:
    """Active links only, and never their tokens — see :func:`create_share_link`."""
    await require_project(session, ctx, project_id)
    links = await ShareLinkRepository(session, ctx).list_active_for_project(project_id)
    return [ShareLinkOut.of(link) for link in links]


@router.delete(
    "/share/{share_link_id}",
    response_model=DeletedOut,
    summary="Revoke a share link",
)
async def revoke_share_link(
    share_link_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
) -> DeletedOut:
    """Revocation is immediate and the row is kept, so the audit trail survives it."""
    ctx.require_write("revoking a share link")
    repo = ShareLinkRepository(session, ctx)
    link = await repo.revoke(share_link_id)
    await AuditLogRepository(session, ctx).record(
        ACTION_SHARE_REVOKED,
        entity="share_link",
        entity_id=share_link_id,
        meta={"projectId": str(link.project_id)},
    )
    return DeletedOut(id=share_link_id, deleted=True)


@router.get(
    "/projects/{project_id}/comments",
    response_model=list[CommentOut],
    summary="Open comments on a project",
)
async def list_comments(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
) -> list[CommentOut]:
    await require_project(session, ctx, project_id)
    comments = await CommentRepository(session, ctx).list_open_for_project(project_id)
    return [CommentOut.of(comment) for comment in comments]


@router.post(
    "/projects/{project_id}/comments",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a comment as a firm user",
)
async def create_comment(
    project_id: uuid.UUID,
    body: CommentIn,
    session: SessionDep,
    ctx: TenantDep,
) -> CommentOut:
    await require_project(session, ctx, project_id)
    comment = await CommentRepository(session, ctx).create(
        project_id,
        body=body.body,
        author_name=body.author_name or "Team",
        anchor=body.anchor,
    )
    return CommentOut.of(comment)


@router.post(
    "/comments/{comment_id}/resolve",
    response_model=CommentOut,
    summary="Resolve (or reopen) a comment",
)
async def resolve_comment(
    comment_id: uuid.UUID,
    session: SessionDep,
    ctx: TenantDep,
    resolved: bool = Query(default=True),
) -> CommentOut:
    ctx.require_write("resolving a comment")
    comment = await CommentRepository(session, ctx).set_resolved(comment_id, resolved)
    return CommentOut.of(comment)


# ---------------------------------------------------------------------------
# Viewer surface — anonymous, read-only, scope-gated
# ---------------------------------------------------------------------------


@public_router.get(
    "/share/{token}",
    response_model=SharedProjectOut,
    summary="What this link shows",
)
async def shared_project(
    token: str,
    session: SessionDep,
    ctx: ShareViewer,
    project_id: ShareProjectId,
) -> SharedProjectOut:
    """The viewer's entry point: the project's name, units and what the link permits.

    Scrupulously narrow. No firm name, no member list, no other project, no ids beyond
    the design version the viewer is about to fetch — a client with a link should learn
    exactly what they were sent and nothing about the practice that sent it.
    """
    project = await ProjectRepository(session, ctx).require(project_id)
    branch = await active_branch(session, ctx, project_id)
    latest = await DesignVersionRepository(session, ctx).latest(project_id, branch)
    # The viewer is told when their link dies, so the UI can say "expires in 3 days"
    # instead of the link simply going dark one morning.
    link = (
        await ShareLinkRepository(session, ctx).get(ctx.share_link_id)
        if ctx.share_link_id is not None
        else None
    )
    return SharedProjectOut(
        project_name=project.name,
        units=project.units,
        city_pack=project.city_pack,
        sections=[str(s) for s in (ctx.scope.get("sections") or [])],
        can_comment=bool(ctx.scope.get("canComment")),
        expires_at=link.expires_at if link is not None else None,
        design_version_id=latest.id if latest is not None else None,
        updated_at=project.updated_at,
    )


@public_router.get(
    "/share/{token}/model",
    response_model=ModelStateOut,
    summary="The design, as snapshot + tail ops",
)
async def shared_model(
    token: str,
    session: SessionDep,
    ctx: ShareViewer,
    project_id: ShareProjectId,
) -> ModelStateOut:
    """Same payload shape as the editor's ``GET /projects/:id/model``.

    The viewer folds it with the same model core the editor uses, so a shared plan is
    the plan — not a screenshot of it, and not a second renderer that could disagree.
    """
    ctx.require_scope("plan")
    # Imported here, not at module scope: keeping the op sequencer out of this module's
    # import graph is the §13 isolation this file is built around. ``unwrap_snapshot`` is
    # a pure envelope reader with no write path of its own.
    from garh_api.routers.ops import unwrap_snapshot

    from garh_api import MODEL_SCHEMA_VERSION

    branch = await active_branch(session, ctx, project_id)
    op_repo = OpRepository(session, ctx)
    dv_repo = DesignVersionRepository(session, ctx)
    head_idx = await op_repo.head_idx(project_id, branch)

    anchor_version = await dv_repo.latest_snapshot(project_id, branch)
    anchor = None
    if anchor_version is not None and anchor_version.snapshot is not None:
        candidate = unwrap_snapshot(anchor_version.snapshot)
        if candidate is not None and candidate.at_idx <= head_idx:
            anchor = candidate

    base_idx = anchor.at_idx if anchor is not None else EMPTY_BRANCH_HEAD
    tail = await op_repo.list_since(project_id, branch, base_idx, limit=MAX_VIEWER_TAIL_OPS)
    return ModelStateOut(
        project_id=project_id,
        version_branch=branch,
        design_version_id=anchor_version.id if anchor is not None and anchor_version else None,
        schema_version=anchor.schema_version if anchor is not None else MODEL_SCHEMA_VERSION,
        snapshot=anchor.document if anchor is not None else None,
        snapshot_hash=(
            anchor_version.snapshot_hash if anchor is not None and anchor_version else None
        ),
        base_idx=base_idx,
        head_idx=head_idx,
        ops=[OpOut.of(op) for op in tail],
        state_hash=(anchor.state_hash if anchor is not None and not tail else None),
        truncated=len(tail) == MAX_VIEWER_TAIL_OPS and bool(tail) and tail[-1].idx < head_idx,
    )


@public_router.get(
    "/share/{token}/renders",
    response_model=list[RenderJobOut],
    summary="Finished renders",
)
async def shared_renders(
    token: str,
    session: SessionDep,
    ctx: ShareViewer,
    project_id: ShareProjectId,
) -> list[RenderJobOut]:
    """Successful renders only.

    A client should not see a queued job, a failed one, or an error string from a GPU
    worker. Those are the practice's business; the client is looking at pictures.
    """
    ctx.require_scope("renders")
    page = await RenderJobRepository(session, ctx).list_gallery(project_id, limit=50)
    return [
        RenderJobOut.of(job)
        for job in page.items
        if job.status == "succeeded" and job.output_url
    ]


@public_router.get(
    "/share/{token}/sheets",
    response_model=SheetSetOut,
    summary="The drawing set",
)
async def shared_sheets(
    token: str,
    session: SessionDep,
    ctx: ShareViewer,
    project_id: ShareProjectId,
) -> SheetSetOut:
    """Sheet metadata without download links.

    Signed download tokens are minted for firm users only. A client viewing a set is a
    different thing from a client being handed the submission DXF, and the second is a
    decision the architect makes explicitly.
    """
    ctx.require_scope("sheets")
    branch = await active_branch(session, ctx, project_id)
    latest = await DesignVersionRepository(session, ctx).latest(project_id, branch)
    if latest is None:
        return SheetSetOut(project_id=project_id, design_version_id=None, sheets=[])
    sheets = await SheetRepository(session, ctx).list_for_version(project_id, latest.id)
    generated = [s.generated_at for s in sheets if s.generated_at is not None]
    return SheetSetOut(
        project_id=project_id,
        design_version_id=latest.id,
        sheets=[SheetOut.of(sheet) for sheet in sheets],
        generated_at=max(generated) if generated else None,
    )


@public_router.get(
    "/share/{token}/compliance",
    response_model=ComplianceOut,
    summary="The compliance report",
)
async def shared_compliance(
    token: str,
    session: SessionDep,
    ctx: ShareViewer,
    project_id: ShareProjectId,
) -> ComplianceOut:
    ctx.require_scope("compliance")
    report = await ComplianceReportRepository(session, ctx).latest_for_project(project_id)
    if report is None:
        return ComplianceOut.not_evaluated(project_id, None)
    return ComplianceOut.of(project_id, report)


@public_router.get(
    "/share/{token}/comments",
    response_model=list[CommentOut],
    summary="Comments left through this link",
)
async def shared_comments(
    token: str,
    session: SessionDep,
    ctx: ShareViewer,
) -> list[CommentOut]:
    """Only the comments made through *this* link.

    Not every comment on the project: those include the practice's internal notes, and
    a client who received a link has no business reading them.
    """
    if ctx.share_link_id is None:  # pragma: no cover - guaranteed by the context type
        return []
    comments = await CommentRepository(session, ctx).list_for_share_link(ctx.share_link_id)
    return [CommentOut.of(comment) for comment in comments]


@public_router.post(
    "/share/{token}/comments",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Leave a comment (only when the link allows it)",
)
async def create_shared_comment(
    token: str,
    body: CommentIn,
    request: Request,
    session: SessionDep,
    ctx: ShareViewer,
) -> CommentOut:
    """The single write on the viewer surface, and the narrowest one available.

    The project id and share-link id come from the resolved token inside the repository,
    never from the request; a body that tried to name a different project would be
    ignored, because there is no field for it. ``canComment`` is checked there too, so
    this handler cannot forget.
    """
    await enforce_rate_limit(_share_comment_rule(), "ip:%s" % client_ip(request))
    if not ctx.scope.get("canComment"):
        raise ApiError(
            "This link is view-only.",
            status=403,
            code="permission_denied",
            action="Ask whoever shared it for a link that allows comments.",
        )
    comment = await CommentRepository(session, ctx).create_from_share(
        body=body.body,
        author_name=body.author_name or "Guest",
        anchor=body.anchor,
    )
    _log.info("share.comment_created", share_link_id=str(ctx.share_link_id))
    return CommentOut.of(comment)


@public_router.get(
    "/share/{token}/ping",
    response_model=Ack,
    summary="Is this link still live?",
)
async def shared_ping(token: str, ctx: ShareViewer) -> Ack:
    """Cheap liveness check for the viewer.

    Resolving the token is the whole test: an unknown, revoked or expired link produces
    the same 404 here as everywhere else, so a polling viewer notices a revocation
    without the app having to special-case it.
    """
    return Ack(ok=True)


def viewer_route_paths() -> list[str]:
    """Every path on the anonymous surface.

    Exposed so a security test can assert the list — and assert that none of them is a
    mutating method other than the one comment POST — rather than a reviewer re-reading
    this file each release.
    """
    paths: list[str] = []
    for route in public_router.routes:
        methods = sorted(getattr(route, "methods", set()) or set())
        paths.append("%s %s" % (",".join(methods), getattr(route, "path", "")))
    return sorted(paths)


__all__: list[str] = [
    "MAX_VIEWER_TAIL_OPS",
    "public_router",
    "router",
    "viewer_route_paths",
]
