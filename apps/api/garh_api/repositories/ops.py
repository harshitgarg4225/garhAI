"""Op-log repository — the append path with optimistic concurrency (§4, §11).

The op log is the source of truth for model state (``state = fold(ops)``), so this is
the most safety-critical write path in the product. Three mechanisms stack up:

1. **Per-project advisory lock** (``pg_advisory_xact_lock``) — the playbook's "single
   writer per project". Concurrent appenders serialise instead of racing, so the
   common case is a clean append rather than a retry storm.
2. **``unique(project_id, version_branch, idx)``** — the actual guarantee. Even if the
   lock were skipped (a second API process, a worker, a bug), two writers cannot
   claim the same index. The loser gets an ``IntegrityError``, which this layer
   converts into :class:`~garh_api.tenancy.OpSequenceConflictError` → HTTP 409, and
   the client rebases (§11).
3. **``client_op_id`` idempotency** — a retried request (flaky network, optimistic
   queue replay) returns the ops that already landed instead of a spurious conflict.

Ops are append-only. There is no update and no delete: undo appends the inverse op.
That is why the inherited id-addressed helpers are disabled below.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, insert
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError

from garh_api import models
from garh_api.config import get_settings
from garh_api.repositories._guards import require_project_in_firm
from garh_api.repositories.domain import NewOp, Op, OpAppendResult
from garh_api.tenancy import (
    OpSequenceConflictError,
    Page,
    ProjectScopedRepository,
    RepositoryUsageError,
)

#: ``head_idx`` for a branch with no ops yet. The first op lands at idx 0, so a client
#: starting fresh sends ``baseIdx = -1``.
EMPTY_BRANCH_HEAD = -1


class OpRepository(ProjectScopedRepository[models.Op, Op]):
    """Append and read the op log for a project branch."""

    row_type = models.Op
    entity_name = "op"
    _order_columns = ("created_at", "seq")

    def to_domain(self, row: models.Op) -> Op:
        return Op.from_row(row)

    # ------------------------------------------------------------------
    # append-only: the id-addressed inherited surface is a category error here
    # ------------------------------------------------------------------
    async def _row_by_id(self, entity_id: object, *, for_update: bool = False) -> models.Op:
        raise RepositoryUsageError(
            "Ops have no uuid id — address them by (project_id, version_branch, idx) "
            "or by seq."
        )

    async def delete(self, entity_id: object) -> bool:
        raise RepositoryUsageError(
            "The op log is append-only. Undo appends the inverse op (§4); it never "
            "deletes history."
        )

    async def list_for_project(self, project_id: object, **kwargs: object) -> Page[Op]:
        raise RepositoryUsageError(
            "Ops paginate by branch index, not by an opaque cursor. Use "
            "list_since(project_id, version_branch, since_idx) — that is also what "
            "GET /projects/:id/ops?since=idx exposes."
        )

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    async def head_idx(self, project_id: uuid.UUID, version_branch: uuid.UUID) -> int:
        """Current highest ``idx`` on the branch, or ``-1`` when it is empty."""
        stmt = (
            self._project_scoped_select(project_id, models.Op.idx)
            .where(models.Op.version_branch == version_branch)
            .order_by(models.Op.idx.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.first()
        return EMPTY_BRANCH_HEAD if row is None else int(row[0])

    async def head_seq(self, project_id: uuid.UUID, version_branch: uuid.UUID) -> int | None:
        """Highest global ``seq`` on the branch — becomes ``design_versions.op_seq_end``."""
        stmt = (
            self._project_scoped_select(project_id, func.max(models.Op.seq))
            .where(models.Op.version_branch == version_branch)
        )
        result = await self._session.execute(stmt)
        value = result.scalar_one_or_none()
        return None if value is None else int(value)

    async def list_since(
        self,
        project_id: uuid.UUID,
        version_branch: uuid.UUID,
        since_idx: int = EMPTY_BRANCH_HEAD,
        *,
        limit: int = 1000,
    ) -> list[Op]:
        """Ops with ``idx > since_idx``, ascending — backs ``GET /ops?since=idx``.

        Index order, not insertion order: replay correctness depends on it.
        """
        capped = max(1, min(int(limit), 10_000))
        stmt = (
            self._project_scoped_select(project_id)
            .where(models.Op.version_branch == version_branch)
            .where(models.Op.idx > since_idx)
            .order_by(models.Op.idx.asc())
            .limit(capped)
        )
        return [self.to_domain(row) for row in await self._all(stmt)]

    async def list_range(
        self,
        project_id: uuid.UUID,
        version_branch: uuid.UUID,
        from_idx: int,
        to_idx: int,
    ) -> list[Op]:
        """Inclusive ``[from_idx, to_idx]`` slice — snapshot + tail loads use this."""
        if to_idx < from_idx:
            raise RepositoryUsageError("to_idx must be >= from_idx.")
        stmt = (
            self._project_scoped_select(project_id)
            .where(models.Op.version_branch == version_branch)
            .where(models.Op.idx >= from_idx)
            .where(models.Op.idx <= to_idx)
            .order_by(models.Op.idx.asc())
        )
        return [self.to_domain(row) for row in await self._all(stmt)]

    async def list_group(self, project_id: uuid.UUID, group_id: uuid.UUID) -> list[Op]:
        """All ops in one batch — undo/redo operates on groups (§4)."""
        stmt = (
            self._project_scoped_select(project_id)
            .where(models.Op.group_id == group_id)
            .order_by(models.Op.idx.asc())
        )
        return [self.to_domain(row) for row in await self._all(stmt)]

    async def get_by_client_op_id(self, project_id: uuid.UUID, client_op_id: str) -> Op | None:
        stmt = (
            self._project_scoped_select(project_id)
            .where(models.Op.client_op_id == client_op_id)
            .limit(1)
        )
        row = await self._first(stmt)
        return None if row is None else self.to_domain(row)

    async def count_since(
        self, project_id: uuid.UUID, version_branch: uuid.UUID, after_idx: int
    ) -> int:
        """How many ops since a snapshot — drives the N=200 snapshot cadence (§2)."""
        stmt = (
            self._project_scoped_select(project_id, models.Op.seq)
            .where(models.Op.version_branch == version_branch)
            .where(models.Op.idx > after_idx)
        )
        return await self._count(stmt)

    async def list_branches(self, project_id: uuid.UUID) -> list[uuid.UUID]:
        stmt = (
            self._project_scoped_select(project_id, models.Op.version_branch)
            .distinct()
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    # ------------------------------------------------------------------
    # locking
    # ------------------------------------------------------------------
    async def acquire_branch_write_lock(
        self, project_id: uuid.UUID, version_branch: uuid.UUID
    ) -> None:
        """Take the per-branch advisory lock for the rest of this transaction.

        Transaction-scoped (``pg_advisory_xact_lock``), so it is released on commit or
        rollback — a crashed request cannot wedge a project. Blocking rather than
        ``try_``-style on purpose: appends are sub-millisecond, and queueing behind the
        current writer gives a clean append instead of a 409 the client must repair.
        """
        key = "ops:%s:%s" % (project_id, version_branch)
        await self._session.execute(
            sql_text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        )

    # ------------------------------------------------------------------
    # the append path
    # ------------------------------------------------------------------
    async def append(
        self,
        project_id: uuid.UUID,
        version_branch: uuid.UUID,
        base_idx: int,
        ops: Sequence[NewOp],
        *,
        source: str,
        actor: uuid.UUID | None = None,
        group_id: uuid.UUID | None = None,
        lock: bool = True,
    ) -> OpAppendResult:
        """Append ops at ``base_idx + 1``, or raise on a stale base.

        Args:
            base_idx: the index the caller believes is HEAD (``-1`` for an empty
                branch). This is §11's ``baseIdx``.
            ops: validated, folded ops with their inverses already computed. This
                layer does **not** validate op payloads — the model core does, before
                calling, because it needs the pre-fold state.
            source: one of ``manual`` / ``copilot`` / ``solver`` / ``system``.
            group_id: batch id; undo/redo operates on groups. Defaults to a fresh id
                when more than one op is appended, so a multi-op apply is always
                undoable as one unit.
            lock: take the advisory lock (leave True except in tests measuring the
                unique-constraint fallback).

        Raises:
            OpSequenceConflictError: ``base_idx`` is not HEAD, or another writer won
                the race. Carries ``head_idx`` so the client can rebase.

        Returns:
            :class:`OpAppendResult`. ``already_applied=True`` means this was a retry
            of a request that already landed.
        """
        self.ctx.require_write("editing this design")
        settings = get_settings()

        if not ops:
            raise RepositoryUsageError("append() needs at least one op.")
        if len(ops) > settings.max_ops_per_append:
            raise RepositoryUsageError(
                "Too many ops in one append (%d > %d)."
                % (len(ops), settings.max_ops_per_append)
            )
        if source not in models.OP_SOURCES:
            raise RepositoryUsageError(
                "source must be one of %s." % ", ".join(models.OP_SOURCES)
            )
        if base_idx < EMPTY_BRANCH_HEAD:
            raise RepositoryUsageError("base_idx must be >= %d." % EMPTY_BRANCH_HEAD)
        for i, op in enumerate(ops):
            if not op.type or not op.type.strip():
                raise RepositoryUsageError("ops[%d].type is required." % i)
            if not isinstance(op.payload, dict):
                raise RepositoryUsageError("ops[%d].payload must be an object." % i)

        await require_project_in_firm(self._session, self.firm_id, project_id)

        if lock:
            await self.acquire_branch_write_lock(project_id, version_branch)

        # Idempotent replay: the exact same client_op_ids already on the branch.
        replay = await self._replay_result(project_id, version_branch, ops)
        if replay is not None:
            return replay

        head = await self.head_idx(project_id, version_branch)
        if head != base_idx:
            self._log.info(
                "ops.append_conflict",
                project_id=str(project_id),
                base_idx=base_idx,
                head_idx=head,
            )
            raise OpSequenceConflictError(
                project_id=project_id,
                version_branch=version_branch,
                base_idx=base_idx,
                head_idx=head,
            )

        effective_group = group_id
        if effective_group is None and len(ops) > 1:
            effective_group = uuid.uuid4()

        records = []
        for offset, op in enumerate(ops):
            records.append(
                {
                    "firm_id": self.firm_id,
                    "project_id": project_id,
                    "version_branch": version_branch,
                    "idx": base_idx + 1 + offset,
                    "type": op.type,
                    "payload": op.payload,
                    "inverse": op.inverse,
                    "actor": actor if actor is not None else self.actor_id,
                    "source": source,
                    "client_op_id": op.client_op_id,
                    "group_id": op.group_id or effective_group,
                }
            )

        try:
            async with self._session.begin_nested():
                result = await self._session.execute(
                    insert(models.Op).returning(models.Op), records
                )
                inserted = list(result.scalars().all())
        except IntegrityError as exc:
            # Either another writer took these indexes, or this is a partially
            # overlapping replay. Re-check both before deciding.
            replay = await self._replay_result(project_id, version_branch, ops)
            if replay is not None:
                return replay
            head_now = await self.head_idx(project_id, version_branch)
            self._log.warning(
                "ops.append_integrity_conflict",
                project_id=str(project_id),
                base_idx=base_idx,
                head_idx=head_now,
            )
            raise OpSequenceConflictError(
                project_id=project_id,
                version_branch=version_branch,
                base_idx=base_idx,
                head_idx=head_now,
            ) from exc

        inserted.sort(key=lambda row: row.idx)
        domain_ops = [self.to_domain(row) for row in inserted]
        first_idx = domain_ops[0].idx
        last_idx = domain_ops[-1].idx
        self._log.info(
            "ops.appended",
            project_id=str(project_id),
            op_source=source,
            count=len(domain_ops),
            first_idx=first_idx,
            last_idx=last_idx,
        )
        return OpAppendResult(
            ops=domain_ops,
            first_idx=first_idx,
            last_idx=last_idx,
            head_idx=last_idx,
            already_applied=False,
        )

    async def _replay_result(
        self,
        project_id: uuid.UUID,
        version_branch: uuid.UUID,
        ops: Sequence[NewOp],
    ) -> OpAppendResult | None:
        """Detect an idempotent retry.

        Returns a result only when *every* incoming op carries a ``client_op_id`` and
        *all* of them are already on this branch. A partial overlap is a genuine
        conflict — the caller's view of history is wrong, and silently appending the
        remainder would interleave two edit streams.
        """
        client_ids = [op.client_op_id for op in ops if op.client_op_id]
        if not client_ids or len(client_ids) != len(ops):
            return None
        stmt = (
            self._project_scoped_select(project_id)
            .where(models.Op.version_branch == version_branch)
            .where(models.Op.client_op_id.in_(client_ids))
            .order_by(models.Op.idx.asc())
        )
        existing = await self._all(stmt)
        if len(existing) != len(client_ids):
            return None
        domain_ops = [self.to_domain(row) for row in existing]
        head = await self.head_idx(project_id, version_branch)
        self._log.info(
            "ops.append_replayed",
            project_id=str(project_id),
            count=len(domain_ops),
            head_idx=head,
        )
        return OpAppendResult(
            ops=domain_ops,
            first_idx=domain_ops[0].idx,
            last_idx=domain_ops[-1].idx,
            head_idx=head,
            already_applied=True,
        )

    # ------------------------------------------------------------------
    # branching (solver options / version restore fork a branch)
    # ------------------------------------------------------------------
    async def copy_branch(
        self,
        project_id: uuid.UUID,
        source_branch: uuid.UUID,
        target_branch: uuid.UUID,
        *,
        through_idx: int | None = None,
    ) -> int:
        """Fork ``source_branch`` into ``target_branch`` up to ``through_idx``.

        Used by version restore: rather than deleting history, the restored state
        becomes a new branch that replays the prefix. Returns the number of ops copied.
        """
        self.ctx.require_write("restoring a version")
        if source_branch == target_branch:
            raise RepositoryUsageError("Source and target branch must differ.")
        head = await self.head_idx(project_id, source_branch)
        limit_idx = head if through_idx is None else min(through_idx, head)
        if limit_idx < 0:
            return 0
        if await self.head_idx(project_id, target_branch) != EMPTY_BRANCH_HEAD:
            raise RepositoryUsageError("Target branch already has ops.")

        source_ops = await self.list_range(project_id, source_branch, 0, limit_idx)
        records = [
            {
                "firm_id": self.firm_id,
                "project_id": project_id,
                "version_branch": target_branch,
                "idx": op.idx,
                "type": op.type,
                "payload": op.payload,
                "inverse": op.inverse,
                "actor": op.actor,
                "source": op.source,
                # client_op_id is intentionally dropped: it is unique per project, and
                # a copy is a different op instance.
                "client_op_id": None,
                "group_id": op.group_id,
            }
            for op in source_ops
        ]
        if not records:
            return 0
        await self._session.execute(insert(models.Op), records)
        await self.flush()
        self._log.info(
            "ops.branch_copied",
            project_id=str(project_id),
            source_branch=str(source_branch),
            target_branch=str(target_branch),
            count=len(records),
        )
        return len(records)

    async def new_branch_id(self) -> uuid.UUID:
        """Mint a branch id. A method (not a bare ``uuid4()`` at the call site) so
        tests can seed determinism in one place."""
        return uuid.uuid4()


__all__ = ["EMPTY_BRANCH_HEAD", "OpRepository"]
