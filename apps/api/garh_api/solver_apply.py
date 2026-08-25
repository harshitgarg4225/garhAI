"""``solver.apply_option`` expansion (§4 op 31) — server-side, no side door.

The client clicks "Use this plan" and sends the smallest possible op::

    { "type": "solver.apply_option",
      "payload": { "solverJobId": "…", "optionIndex": 0 }, "clientOpId": "…" }

This module turns that into the op the model core actually folds: the same type,
with ``payload.ops`` filled from the **stored** solver job's chosen option — the
storey/wall/opening/stair/room ops, in dependency order, plus ``lockedRoomIds`` when
the job was a §5.7 partial re-solve. Three properties are load-bearing:

* **Same validate+fold path as any op batch.** The expanded op goes through the
  ordinary sequencer; ``garh_model.fold.fold`` intercepts ``solver.apply_option`` and
  applies the inner ops through ``apply_group``, validating each against the
  intermediate state. Nothing here folds, and nothing here can skip validation.
* **The server's ops, never the client's.** Whatever the request carried in
  ``payload.ops`` is discarded; the expansion is rebuilt from ``solver_jobs.options``
  — the row §5.6's gates already vetted. A client cannot smuggle geometry through
  op 31 that it could not send as ordinary ops.
* **Idempotent via clientOpId.** The expanded op keeps the client's ``clientOpId``;
  when the client sent none, a **derived** one (job id + option index) fills in, so
  re-applying the same option is the sequencer's ordinary replay no-op either way.

Cross-tenant scoping is the repository layer's: ``SolverJobRepository.require`` is
firm-scoped (a foreign firm's job id → 404), and a job that belongs to a *different
project* of the same firm is rejected with the same 404 — op 31 must not let one
project's geometry be replayed into another.

``snapshot_after_solver_apply`` writes the §2 ``kind='option'`` design version after
the append — accepting a plan is a named moment on the timeline, and pinning it makes
"open the plan I accepted" a snapshot read instead of a replay.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from garh_api.logging import get_logger
from garh_api.repositories import (
    DesignVersionRepository,
    OpRepository,
    SolverJobRepository,
    TenantCtx,
)
from garh_api.routers import ApiError
from garh_api.schemas.ops import OpIn, OpsAppendOut
from garh_api.tenancy import EntityNotFoundError

_log = get_logger(__name__)

SOLVER_APPLY_OP_TYPE = "solver.apply_option"

#: Fold order for the expansion (§4 "storey/wall/opening/room/stair ops in dependency
#: order"). A storey must exist before its walls, a wall before its openings, and the
#: room ops last because rooms are *derived* from walls — ``room.assign`` needs the
#: detection pass that folding the walls triggers. The sort is stable, so the
#: solver's own ordering survives within each band.
_DEPENDENCY_PRECEDENCE: dict[str, int] = {
    "storey.add": 0,
    "storey.set_height": 1,
    "levels.set": 2,
    "wall.add": 3,
    "wall.set_thickness": 4,
    "opening.add": 5,
    "stair.add": 6,
    "balcony.add": 7,
    "column.add": 7,
    "room.assign": 8,
    "room.set_target": 9,
    "furniture.place": 10,
    "material.assign": 11,
}
_DEPENDENCY_DEFAULT = 12

#: Namespace for deriving deterministic group ids from (job, option).
_GROUP_NAMESPACE = uuid.UUID("a3a5a1f0-31c5-4bd5-9d3e-6f1b6a1c7e02")


class SolverOptionNotApplicableError(ApiError):
    """The job/option named by op 31 cannot be applied right now. 409, with a way out."""

    http_status = 409
    code = "solver_option_not_applicable"
    action = "Generate plans again, then apply one of the new options."


def derived_client_op_id(job_id: uuid.UUID, option_index: int) -> str:
    """Deterministic idempotency key when the client did not send one.

    Applying the same option of the same job twice IS the same operation, so the
    derived key makes the retry/double-click case a replay no-op even for clients
    that forgot ``clientOpId``. Fits the 64-char column: ``sapply-`` + 32 + index.
    """
    return "sapply-%s-%d" % (job_id.hex, option_index)


def _derived_group_id(job_id: uuid.UUID, option_index: int) -> uuid.UUID:
    return uuid.uuid5(_GROUP_NAMESPACE, "%s:%d" % (job_id, option_index))


def dependency_order(ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable sort into fold-dependency bands. Same input ⇒ same output, always."""
    return sorted(
        ops,
        key=lambda item: _DEPENDENCY_PRECEDENCE.get(
            str(item.get("type")), _DEPENDENCY_DEFAULT
        ),
    )


async def expand_solver_apply_ops(
    session: AsyncSession,
    ctx: TenantCtx,
    project_id: uuid.UUID,
    incoming: list[OpIn],
) -> list[OpIn]:
    """Replace every ``solver.apply_option`` op with its server-built expansion.

    Ops of any other type pass through untouched, and a batch with no op-31 in it
    costs zero queries. Called by the sequencer BEFORE its replay check, so the
    (possibly derived) ``clientOpId`` participates in idempotent replay.
    """
    if not any(op_in.type == SOLVER_APPLY_OP_TYPE for op_in in incoming):
        return incoming
    repo = SolverJobRepository(session, ctx)
    expanded: list[OpIn] = []
    for op_in in incoming:
        if op_in.type == SOLVER_APPLY_OP_TYPE:
            expanded.append(await _expand_one(repo, project_id, op_in))
        else:
            expanded.append(op_in)
    return expanded


async def _expand_one(
    repo: SolverJobRepository, project_id: uuid.UUID, op_in: OpIn
) -> OpIn:
    payload = op_in.payload or {}
    job_id = _job_uuid(payload.get("solverJobId"))

    # Firm scoping is the repository's (foreign firm → 404); project scoping is ours.
    job = await repo.require(job_id)
    if job.project_id != project_id:
        # Same shape as cross-tenant: the job does not exist *for this project*.
        raise EntityNotFoundError("solver_job", job_id)

    if job.status != "succeeded" or not job.options:
        raise SolverOptionNotApplicableError(
            "That plan generation hasn't finished successfully, so there is no "
            "option to apply yet.",
            extra={"solverJobId": str(job_id), "status": job.status},
        )

    option_index = payload.get("optionIndex")
    if isinstance(option_index, bool) or not isinstance(option_index, int):
        raise SolverOptionNotApplicableError(
            "That request didn't say which plan option to apply.",
            extra={"solverJobId": str(job_id), "optionIndex": option_index},
        )
    if not 0 <= option_index < len(job.options):
        raise SolverOptionNotApplicableError(
            "That plan generation produced %d option(s); option %d doesn't exist."
            % (len(job.options), option_index + 1),
            extra={"solverJobId": str(job_id), "optionIndex": option_index},
        )

    option = job.options[option_index]
    if not isinstance(option, dict):
        raise SolverOptionNotApplicableError(
            "That plan option is in a format this server cannot apply.",
            extra={"solverJobId": str(job_id), "optionIndex": option_index},
        )
    ops_raw = option.get("ops")
    if not isinstance(ops_raw, list) or not ops_raw:
        # §5.6 means a stored option always carries its ops; an empty one is a
        # worker bug surfaced honestly, not applied as a silent no-op.
        raise SolverOptionNotApplicableError(
            "That plan option carries no geometry to apply.",
            extra={"solverJobId": str(job_id), "optionIndex": option_index},
        )
    inner_ops: list[dict[str, Any]] = []
    for index, item in enumerate(ops_raw):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("type"), str)
            or not isinstance(item.get("payload"), dict)
        ):
            raise SolverOptionNotApplicableError(
                "That plan option contains a step this server cannot apply.",
                extra={
                    "solverJobId": str(job_id),
                    "optionIndex": option_index,
                    "opIndex": index,
                },
            )
        inner_ops.append({"type": item["type"], "payload": item["payload"]})

    locked_ids = option.get("lockedRoomIds")
    if not isinstance(locked_ids, list):
        locked_ids = (job.params or {}).get("lockedRoomIds")

    # The payload is rebuilt whole: whatever `ops` the CLIENT sent is discarded.
    new_payload: dict[str, Any] = {
        "solverJobId": str(job.id),
        "optionIndex": option_index,
        "ops": dependency_order(inner_ops),
    }
    if isinstance(locked_ids, list) and locked_ids:
        new_payload["lockedRoomIds"] = [str(item) for item in locked_ids]

    _log.info(
        "solver_apply.expanded",
        solver_job_id=str(job.id),
        option_index=option_index,
        op_count=len(inner_ops),
        derived_client_op_id=op_in.client_op_id is None,
    )
    return OpIn(
        type=SOLVER_APPLY_OP_TYPE,
        payload=new_payload,
        client_op_id=op_in.client_op_id or derived_client_op_id(job.id, option_index),
        # One group for the whole application (§4: "solver.apply_option … is a
        # single group"); derived, so a retry regroups identically.
        group_id=op_in.group_id or _derived_group_id(job.id, option_index),
    )


def _job_uuid(raw: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        # An unparseable id is indistinguishable from a nonexistent one: 404, not 500.
        raise EntityNotFoundError("solver_job", raw)


async def snapshot_after_solver_apply(
    session: AsyncSession,
    ctx: TenantCtx,
    *,
    project_id: uuid.UUID,
    branch: uuid.UUID,
    incoming: list[OpIn],
    result: OpsAppendOut,
) -> OpsAppendOut:
    """After an op-31 append lands, pin it as a ``kind='option'`` design version.

    A no-op for batches without op 31 and for idempotent replays (the original
    request already snapshotted). The snapshot re-folds "snapshot + tail" up to the
    new head through the same loader the sequencer itself uses — one folding
    implementation, one truth.
    """
    if result.already_applied:
        return result
    applied = [op_in for op_in in incoming if op_in.type == SOLVER_APPLY_OP_TYPE]
    if not applied:
        return result

    # Imported here, not at module top: routers/ops.py imports this module, and the
    # snapshot helpers live there. By the time this coroutine runs, ops.py is loaded.
    from garh_api.routers import ops as ops_router

    engine = ops_router.get_model_engine()
    state = await ops_router.load_project_state(
        session, ctx, project_id, branch, upto_idx=result.head_idx
    )
    head_seq = await OpRepository(session, ctx).head_seq(project_id, branch)
    option_index = applied[0].payload.get("optionIndex")
    name = (
        "Solver option %d" % (option_index + 1)
        if isinstance(option_index, int) and not isinstance(option_index, bool)
        else "Solver option"
    )
    version = await DesignVersionRepository(session, ctx).create_option(
        project_id,
        name=name,
        version_branch=branch,
        snapshot=ops_router.wrap_snapshot(
            state.document,
            version_branch=branch,
            at_idx=result.head_idx,
            at_seq=head_seq,
            state_hash=state.state_hash,
            schema_version=engine.schema_version,
        ),
        op_seq_end=head_seq,
    )
    _log.info(
        "solver_apply.option_version",
        project_id=str(project_id),
        version_id=str(version.id),
        at_idx=result.head_idx,
    )

    # §7 / decision D13, the regeneration contract: "Solver re-run: all annotations
    # whose anchors didn't survive id-matching → orphaned=true → Review Tray UI".
    # This is where a solver re-run becomes visible to the tray, so an architect finds
    # their notes waiting rather than discovering them missing at print time. Failure
    # is logged, never raised: the option has already been applied, and refusing the
    # append now would lose the design change to fix a bookkeeping problem.
    try:
        from garh_api.routers.sheets import reconcile_annotations

        stats = await reconcile_annotations(session, ctx, project_id, state.document)
        if stats.get("orphaned"):
            _log.info(
                "solver_apply.annotations_orphaned",
                project_id=str(project_id),
                version_id=str(version.id),
                orphaned=stats["orphaned"],
                attached=stats.get("attached", 0),
            )
    except Exception as exc:  # noqa: BLE001 - the applied option stands regardless
        _log.error(
            "solver_apply.annotation_reconcile_failed",
            project_id=str(project_id),
            error="%s: %s" % (type(exc).__name__, exc),
        )

    if result.snapshot_version_id is None:
        return result.model_copy(update={"snapshot_version_id": version.id})
    return result


__all__ = [
    "SOLVER_APPLY_OP_TYPE",
    "SolverOptionNotApplicableError",
    "dependency_order",
    "derived_client_op_id",
    "expand_solver_apply_ops",
    "snapshot_after_solver_apply",
]
