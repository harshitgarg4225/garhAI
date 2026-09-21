"""Which saved version a piece of work is *of* — and minting one when there is none.

A render, a sheet set and an export are all statements about ONE state of a design:
"this is what the house looked like when I made this". §7 and §9 both pin their
results to a ``design_versions`` row for exactly that reason — so an image or a
drawing can be shown honestly against the design it came from, and marked stale when
the design moves on.

The product has no "save a version" button, though, and never will: an architect who
presses Render means *the building on their screen*. So the pin cannot be a
precondition the caller has to satisfy. This module is the one place that resolves it,
minting a checkpoint of the head when the design has moved past the last one.

WHY IT LIVES HERE rather than in ``routers/jobs.py`` where it was written. The sheet
routes called the minting resolver; the render route called a look-only one that
returned ``None`` for a project with no checkpoint — and ``None`` is exactly the value
the render worker rejects (``services/render/handler.py``: "render jobs must carry
designVersionId"). The API answered 201, the UI said "Render started", a credit was
spent, and the job died in the worker where no part of the web app was looking. On a
project started from a ready-made plan — the front door of the product, and a path
that appends ops without ever cutting a version — that was EVERY render. Found in a
browser, timing a new user's first render, which never arrived.

One importable helper, imported by both routers, is the structural answer: there is no
longer a second resolver for a caller to pick by mistake.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from garh_api.errors import ApiError
from garh_api.logging import get_logger
from garh_api.repositories import DesignVersionRepository, OpRepository, TenantCtx
from garh_api.routers import active_branch

__all__ = ["NoDesignVersionError", "design_version_at_head"]

_log = get_logger(__name__)


class NoDesignVersionError(ApiError):
    """Nothing to pin to, because there is no design yet.

    The ONLY project this is now true of is one with no ops at all — every project
    that has a plan gets a checkpoint minted for it above. The action says so: it
    would be cruel (and, before this module, was actually wrong) to answer "save the
    design first" in a product with no save-version button.
    """

    http_status = 409
    code = "no_design_version"
    action = "Generate plan options or draw the plan first."


async def design_version_at_head(
    session: AsyncSession,
    ctx: TenantCtx,
    project_id: uuid.UUID,
    supplied: uuid.UUID | None,
    *,
    what: str = "drawing",
) -> uuid.UUID | None:
    """The version this work is *of*, minted from the head when the head has none.

    A supplied id is honoured as-is (that is an architect asking for an older version
    on purpose). Otherwise: the latest version on the branch when the design has not
    moved past it, else a fresh checkpoint of the head — snapshot plus frozen
    compliance report, exactly what a "save a version" button would store.

    ``None`` means there is genuinely nothing to pin: no ops and no version. Callers
    must turn that into a 4xx rather than queue work that cannot succeed.
    """
    if supplied is not None:
        await DesignVersionRepository(session, ctx).require(supplied)
        return supplied

    branch = await active_branch(session, ctx, project_id)
    op_repo = OpRepository(session, ctx)
    dv_repo = DesignVersionRepository(session, ctx)
    head_seq = await op_repo.head_seq(project_id, branch)
    latest = await dv_repo.latest(project_id, branch)
    if latest is None and head_seq is None:
        return None
    if latest is not None and (
        head_seq is None or (latest.op_seq_end is not None and latest.op_seq_end >= head_seq)
    ):
        return latest.id

    # Imported here, not at module top: these helpers live in router modules, which the
    # routers package loads after this one.
    from garh_api.routers.ops import get_model_engine, load_project_state, wrap_snapshot
    from garh_api.routers.projects import freeze_compliance_report

    await op_repo.acquire_branch_write_lock(project_id, branch)
    state = await load_project_state(session, ctx, project_id, branch)
    head_seq = await op_repo.head_seq(project_id, branch)
    if (
        latest is not None
        and latest.snapshot is not None
        and state.state_hash is not None
        and latest.snapshot.get("stateHash") == state.state_hash
    ):
        # A version with no recorded op range (older rows) that is nonetheless the head.
        return latest.id

    engine = get_model_engine()
    version = await dv_repo.create_checkpoint(
        project_id,
        version_branch=branch,
        snapshot=wrap_snapshot(
            state.document,
            version_branch=branch,
            at_idx=state.head_idx,
            at_seq=head_seq,
            state_hash=state.state_hash,
            schema_version=engine.schema_version,
        ),
        op_seq_start=None,
        op_seq_end=head_seq,
    )
    # §7: the area statement on the sheet and the compliance annexure quote ONE set of
    # numbers, frozen with the snapshot — the same rule POST /versions follows.
    await freeze_compliance_report(session, ctx, project_id, state.document, version.id)
    _log.info(
        "design_version.minted",
        project_id=str(project_id),
        version_id=str(version.id),
        at_idx=state.head_idx,
        behind=None if latest is None else str(latest.id),
        what=what,
    )
    return version.id
