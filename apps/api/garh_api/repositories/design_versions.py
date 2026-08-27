"""Design-version repository — snapshots, named versions, solver options (§2, §3).

Load path this exists to serve: "open project → interactive canvas <2s" (§15
micro-speed). That is *latest snapshot + tail ops*, never a full replay of the log.
So: a snapshot is folded every ``OP_SNAPSHOT_INTERVAL`` (200) ops and at every named
version and every applied solver option, and :meth:`latest_snapshot` finds the newest
one to start from.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from garh_api import models
from garh_api.config import get_settings
from garh_api.repositories._guards import require_project_in_firm
from garh_api.repositories.domain import DesignVersion, DesignVersionSummary
from garh_api.tenancy import Page, ProjectScopedRepository, RepositoryUsageError

#: Columns needed for the version timeline — everything except the heavy snapshot.
_SUMMARY_COLUMNS = (
    models.DesignVersion.id,
    models.DesignVersion.project_id,
    models.DesignVersion.name,
    models.DesignVersion.parent_id,
    models.DesignVersion.version_branch,
    models.DesignVersion.op_seq_start,
    models.DesignVersion.op_seq_end,
    models.DesignVersion.snapshot_hash,
    models.DesignVersion.kind,
    models.DesignVersion.created_at,
)


def canonical_json(document: dict[str, Any]) -> str:
    """Canonical JSON form used for hashing — ``garh-canonical-json/v1``.

    DELEGATES to ``garh_model.fold``, which owns the definition. It used to keep a
    second, local ``json.dumps(sort_keys=True, separators=(",", ":"))``, and the
    two were not equivalent: the model core's canonicaliser *asserts every number
    is an integer* (a float length raises rather than silently hashing) and runs
    ``to_jsonable`` first so it accepts the dataclass form. A document that hashed
    one way here and another way in the model core would break ``snapshot_hash``
    equality against ``fixtures/model/golden-states.json`` and against the
    TypeScript client — silently, because both sides produce 64 plausible hex
    characters either way.

    The fallback exists only so this module stays importable in a context where
    ``garh_model`` is not on the path (the mypy/ruff-only jobs do not add it). It
    logs nothing and is byte-identical for integer-only documents; if it is ever
    reached with a float, the divergence is the caller's bug, not this function's.
    """
    try:
        from garh_model.fold import canonical_json as _model_canonical_json
    except ImportError:  # pragma: no cover - garh_model absent from sys.path
        return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _model_canonical_json(document)


def compute_snapshot_hash(snapshot: dict[str, Any]) -> str:
    """``sha256`` of the canonical JSON (§2) — i.e. ``garh_model.fold.state_hash``.

    Equal by construction to the ``expectedStateHash`` column of
    ``fixtures/model/golden-states.json`` for the same document, which is what
    makes ``design_versions.snapshot_hash`` comparable to what the browser
    computed before it posted the ops.
    """
    try:
        from garh_model.fold import state_hash as _model_state_hash
    except ImportError:  # pragma: no cover - garh_model absent from sys.path
        return hashlib.sha256(canonical_json(snapshot).encode("utf-8")).hexdigest()
    return _model_state_hash(snapshot)


def snapshot_due(ops_since_snapshot: int, interval: int | None = None) -> bool:
    """True when enough ops have accumulated to justify folding a new snapshot."""
    step = interval if interval is not None else get_settings().op_snapshot_interval
    return ops_since_snapshot >= step


class DesignVersionRepository(ProjectScopedRepository[models.DesignVersion, DesignVersion]):
    """Versions and snapshots for a project."""

    row_type = models.DesignVersion
    entity_name = "design_version"

    def to_domain(self, row: models.DesignVersion) -> DesignVersion:
        return DesignVersion.from_row(row)

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    async def latest(
        self, project_id: uuid.UUID, version_branch: uuid.UUID | None = None
    ) -> DesignVersion | None:
        stmt = self._project_scoped_select(project_id)
        if version_branch is not None:
            stmt = stmt.where(models.DesignVersion.version_branch == version_branch)
        stmt = stmt.order_by(models.DesignVersion.created_at.desc()).limit(1)
        row = await self._first(stmt)
        return None if row is None else self.to_domain(row)

    async def latest_snapshot(
        self, project_id: uuid.UUID, version_branch: uuid.UUID | None = None
    ) -> DesignVersion | None:
        """Newest version that actually carries a snapshot — the fast-load anchor.

        Ordered by ``op_seq_end`` (nulls last) rather than ``created_at``: position in
        the op log is what makes a snapshot usable as a replay base, and a version row
        can be written slightly out of wall-clock order by a worker.
        """
        stmt = self._project_scoped_select(project_id).where(
            models.DesignVersion.snapshot.is_not(None)
        )
        if version_branch is not None:
            stmt = stmt.where(models.DesignVersion.version_branch == version_branch)
        stmt = stmt.order_by(
            models.DesignVersion.op_seq_end.desc().nullslast(),
            models.DesignVersion.created_at.desc(),
        ).limit(1)
        row = await self._first(stmt)
        return None if row is None else self.to_domain(row)

    async def list_timeline(
        self,
        project_id: uuid.UUID,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        kind: str | None = None,
    ) -> Page[DesignVersion]:
        """Version timeline (header scrubber, §15). Includes snapshots — use
        :meth:`list_summaries` when the payload size matters."""
        stmt = self._project_scoped_select(project_id)
        if kind is not None:
            _validate_kind(kind)
            stmt = stmt.where(models.DesignVersion.kind == kind)
        return await self._page(stmt, limit=limit, cursor=cursor, newest_first=True)

    async def list_summaries(
        self, project_id: uuid.UUID, *, limit: int = 100
    ) -> list[DesignVersionSummary]:
        """Timeline entries without snapshot payloads (cheap enough for every page load)."""
        stmt = (
            self._project_scoped_select(project_id, *_SUMMARY_COLUMNS)
            .order_by(models.DesignVersion.created_at.desc())
            .limit(max(1, min(limit, 500)))
        )
        result = await self._session.execute(stmt)
        return [DesignVersionSummary.from_row(row) for row in result.all()]

    async def list_options(self, project_id: uuid.UUID) -> list[DesignVersion]:
        """Solver-option versions, oldest first (option lineage in the UI)."""
        stmt = (
            self._project_scoped_select(project_id)
            .where(models.DesignVersion.kind == "option")
            .order_by(models.DesignVersion.created_at.asc())
        )
        return [self.to_domain(row) for row in await self._all(stmt)]

    async def get_by_hash(self, project_id: uuid.UUID, snapshot_hash: str) -> DesignVersion | None:
        stmt = (
            self._project_scoped_select(project_id)
            .where(models.DesignVersion.snapshot_hash == snapshot_hash)
            .limit(1)
        )
        row = await self._first(stmt)
        return None if row is None else self.to_domain(row)

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------
    async def create(
        self,
        project_id: uuid.UUID,
        *,
        version_branch: uuid.UUID,
        kind: str = "auto",
        name: str | None = None,
        parent_id: uuid.UUID | None = None,
        op_seq_start: int | None = None,
        op_seq_end: int | None = None,
        snapshot: dict[str, Any] | None = None,
        snapshot_hash: str | None = None,
    ) -> DesignVersion:
        """Create a version row.

        ``snapshot_hash`` is computed from ``snapshot`` when omitted; supplying it
        lets the model core pass the hash it already has (and lets tests assert the
        two agree).
        """
        self.ctx.require_write("saving a version")
        _validate_kind(kind)
        clean_name = (name or "").strip() or None
        if kind == "named" and not clean_name:
            raise RepositoryUsageError("A named version needs a name.")
        if snapshot is not None:
            if not isinstance(snapshot, dict):
                raise RepositoryUsageError("snapshot must be an object.")
            snapshot_hash = snapshot_hash or compute_snapshot_hash(snapshot)
        elif snapshot_hash is not None:
            raise RepositoryUsageError("snapshot_hash without a snapshot is meaningless.")

        await require_project_in_firm(self._session, self.firm_id, project_id)
        if parent_id is not None:
            await self._require_row(parent_id)  # 404s if it belongs to another firm

        row = self._new_row(
            project_id=project_id,
            version_branch=version_branch,
            kind=kind,
            name=clean_name,
            parent_id=parent_id,
            op_seq_start=op_seq_start,
            op_seq_end=op_seq_end,
            snapshot=snapshot,
            snapshot_hash=snapshot_hash,
        )
        await self._insert(row)
        self._log.info(
            "design_version.created",
            entity_id=str(row.id),
            project_id=str(project_id),
            version_kind=kind,
            has_snapshot=snapshot is not None,
        )
        return self.to_domain(row)

    async def create_checkpoint(
        self,
        project_id: uuid.UUID,
        *,
        version_branch: uuid.UUID,
        snapshot: dict[str, Any],
        op_seq_start: int | None,
        op_seq_end: int | None,
        parent_id: uuid.UUID | None = None,
    ) -> DesignVersion:
        """Automatic snapshot every N ops (§2). ``kind='auto'``, no name."""
        return await self.create(
            project_id,
            version_branch=version_branch,
            kind="auto",
            parent_id=parent_id,
            op_seq_start=op_seq_start,
            op_seq_end=op_seq_end,
            snapshot=snapshot,
        )

    async def create_named(
        self,
        project_id: uuid.UUID,
        *,
        name: str,
        version_branch: uuid.UUID,
        snapshot: dict[str, Any],
        op_seq_start: int | None = None,
        op_seq_end: int | None = None,
        parent_id: uuid.UUID | None = None,
    ) -> DesignVersion:
        """User-named version — always snapshotted, so restore is instant."""
        return await self.create(
            project_id,
            version_branch=version_branch,
            kind="named",
            name=name,
            parent_id=parent_id,
            op_seq_start=op_seq_start,
            op_seq_end=op_seq_end,
            snapshot=snapshot,
        )

    async def create_option(
        self,
        project_id: uuid.UUID,
        *,
        name: str | None,
        version_branch: uuid.UUID,
        snapshot: dict[str, Any],
        op_seq_start: int | None = None,
        op_seq_end: int | None = None,
        parent_id: uuid.UUID | None = None,
    ) -> DesignVersion:
        """A solver option the user accepted (``solver.apply_option``, op 31)."""
        return await self.create(
            project_id,
            version_branch=version_branch,
            kind="option",
            name=name,
            parent_id=parent_id,
            op_seq_start=op_seq_start,
            op_seq_end=op_seq_end,
            snapshot=snapshot,
        )

    async def attach_snapshot(
        self,
        version_id: uuid.UUID,
        snapshot: dict[str, Any],
        *,
        snapshot_hash: str | None = None,
        op_seq_end: int | None = None,
    ) -> DesignVersion:
        """Backfill a snapshot onto an existing version (compaction worker)."""
        self.ctx.require_write("writing a snapshot")
        if not isinstance(snapshot, dict):
            raise RepositoryUsageError("snapshot must be an object.")
        row = await self._require_row(version_id)
        row.snapshot = snapshot
        row.snapshot_hash = snapshot_hash or compute_snapshot_hash(snapshot)
        if op_seq_end is not None:
            row.op_seq_end = op_seq_end
        await self.flush()
        return self.to_domain(row)

    async def rename(self, version_id: uuid.UUID, name: str) -> DesignVersion:
        self.ctx.require_write("renaming a version")
        clean = name.strip()
        if not clean:
            raise RepositoryUsageError("Version name cannot be blank.")
        row = await self._require_row(version_id)
        row.name = clean
        if row.kind == "auto":
            # Naming an auto-checkpoint promotes it: users expect named versions to
            # stop being pruning candidates.
            row.kind = "named"
        await self.flush()
        return self.to_domain(row)

    async def set_op_range(
        self,
        version_id: uuid.UUID,
        *,
        op_seq_start: int | None = None,
        op_seq_end: int | None = None,
    ) -> DesignVersion:
        self.ctx.require_write("updating a version")
        row = await self._require_row(version_id)
        if op_seq_start is not None:
            row.op_seq_start = op_seq_start
        if op_seq_end is not None:
            row.op_seq_end = op_seq_end
        await self.flush()
        return self.to_domain(row)

    async def prune_auto_snapshots(self, project_id: uuid.UUID, *, keep_latest: int = 5) -> int:
        """Drop snapshot payloads from old ``auto`` checkpoints, keeping the rows.

        Storage hygiene: a G+2 house snapshot is not small and every 200 ops writes
        another. The row survives, so the timeline keeps its shape and its op range;
        only the folded payload goes, and it can be re-derived from the op log at any
        time. ``snapshot_hash`` clears with it — the DDL's
        ``(snapshot IS NULL) = (snapshot_hash IS NULL)`` check forbids a dangling hash,
        and a hash whose payload is gone would be a liability in a sync check anyway.

        Named versions and solver options are never pruned.
        """
        self.ctx.require_write("pruning snapshots")
        stmt = (
            self._project_scoped_select(project_id)
            .where(models.DesignVersion.kind == "auto")
            .where(models.DesignVersion.snapshot.is_not(None))
            .order_by(models.DesignVersion.created_at.desc())
        )
        rows = await self._all(stmt)
        cleared = 0
        for row in rows[max(0, keep_latest) :]:
            row.snapshot = None
            row.snapshot_hash = None
            cleared += 1
        if cleared:
            await self.flush()
            self._log.info(
                "design_version.snapshots_pruned",
                project_id=str(project_id),
                cleared=cleared,
            )
        return cleared


def _validate_kind(kind: str) -> None:
    if kind not in models.DESIGN_VERSION_KINDS:
        raise RepositoryUsageError(
            "kind must be one of %s." % ", ".join(models.DESIGN_VERSION_KINDS)
        )


__all__ = [
    "DesignVersionRepository",
    "canonical_json",
    "compute_snapshot_hash",
    "snapshot_due",
]
