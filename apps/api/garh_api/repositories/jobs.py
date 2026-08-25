"""Worker-job repositories: solver jobs (§5) and render jobs (§9).

Both follow the same lifecycle — ``queued → running → succeeded | failed |
cancelled`` — and both are progressed by a worker holding a
``TenantCtx.for_system(firm_id)`` context, so worker writes are scoped exactly like
user writes. Golden rule 9: "the UI shows job state honestly", which means a job never
sits in ``running`` after its worker dies; workers heartbeat progress and the API can
fail a stalled job explicitly.
"""

from __future__ import annotations

import uuid
from typing import Any

from garh_api import models
from garh_api.repositories._guards import (
    require_design_version_in_firm,
    require_project_in_firm,
)
from garh_api.repositories.domain import RenderJob, SolverJob
from garh_api.tenancy import Page, ProjectScopedRepository, RepositoryUsageError


def _validate_status(status: str) -> None:
    if status not in models.JOB_STATUSES:
        raise RepositoryUsageError(
            "status must be one of %s." % ", ".join(models.JOB_STATUSES)
        )


def _clamp_progress(progress: int) -> int:
    return max(0, min(int(progress), 100))


class SolverJobRepository(ProjectScopedRepository[models.SolverJob, SolverJob]):
    """CP-SAT layout jobs.

    ``options`` holds the presentable ``PlanOption[]`` only — §5.6's gates run in the
    worker, and golden rule 2 says a hard-fail plan is never shown, so a plan that
    failed the gates must not be written here.
    """

    row_type = models.SolverJob
    entity_name = "solver_job"

    def to_domain(self, row: models.SolverJob) -> SolverJob:
        return SolverJob.from_row(row)

    # -- reads ---------------------------------------------------------
    async def list_for_firm(
        self, *, limit: int | None = None, cursor: str | None = None, status: str | None = None
    ) -> Page[SolverJob]:
        stmt = self._scoped_select()
        if status is not None:
            _validate_status(status)
            stmt = stmt.where(models.SolverJob.status == status)
        return await self._page(stmt, limit=limit, cursor=cursor, newest_first=True)

    async def latest_for_project(self, project_id: uuid.UUID) -> SolverJob | None:
        stmt = (
            self._project_scoped_select(project_id)
            .order_by(models.SolverJob.created_at.desc())
            .limit(1)
        )
        row = await self._first(stmt)
        return None if row is None else self.to_domain(row)

    async def count_active(self) -> int:
        """Queued+running jobs for this firm — backs the free-tier rate limit (§13)."""
        stmt = self._scoped_select().where(
            models.SolverJob.status.in_(("queued", "running"))
        )
        return await self._count(stmt)

    async def count_since(self, since: Any) -> int:
        """Jobs created since a timestamp — 10 solver jobs/hr on the free tier (§13)."""
        stmt = self._scoped_select().where(models.SolverJob.created_at >= since)
        return await self._count(stmt)

    # -- writes --------------------------------------------------------
    async def enqueue(
        self, project_id: uuid.UUID, *, params: dict[str, Any] | None = None
    ) -> SolverJob:
        self.ctx.require_write("generating plans")
        await require_project_in_firm(self._session, self.firm_id, project_id)
        row = self._new_row(
            project_id=project_id,
            params=params or {},
            status="queued",
            progress=0,
        )
        await self._insert(row)
        self._log.info("solver_job.enqueued", entity_id=str(row.id), project_id=str(project_id))
        return self.to_domain(row)

    async def mark_running(self, job_id: uuid.UUID) -> SolverJob:
        row = await self._require_row(job_id)
        row.status = "running"
        await self.flush()
        return self.to_domain(row)

    async def set_progress(self, job_id: uuid.UUID, progress: int) -> SolverJob:
        """Progress from real worker events (§15: "never a fake bar")."""
        row = await self._require_row(job_id)
        row.progress = _clamp_progress(progress)
        if row.status == "queued":
            row.status = "running"
        await self.flush()
        return self.to_domain(row)

    async def succeed(self, job_id: uuid.UUID, options: list[Any]) -> SolverJob:
        if not isinstance(options, list):
            raise RepositoryUsageError("options must be a list of PlanOption objects.")
        row = await self._require_row(job_id)
        row.status = "succeeded"
        row.progress = 100
        row.options = options
        row.error = None
        await self.flush()
        self._log.info("solver_job.succeeded", entity_id=str(job_id), options=len(options))
        return self.to_domain(row)

    async def fail(self, job_id: uuid.UUID, error: str) -> SolverJob:
        """Fail a job. ``error`` is user-facing copy (golden rule 9), not a traceback."""
        row = await self._require_row(job_id)
        row.status = "failed"
        row.error = error[:2000]
        await self.flush()
        self._log.warning("solver_job.failed", entity_id=str(job_id))
        return self.to_domain(row)

    async def cancel(self, job_id: uuid.UUID) -> SolverJob:
        row = await self._require_row(job_id)
        if row.status in models.JOB_TERMINAL_STATUSES:
            return self.to_domain(row)
        row.status = "cancelled"
        await self.flush()
        return self.to_domain(row)


class RenderJobRepository(ProjectScopedRepository[models.RenderJob, RenderJob]):
    """Render jobs, pinned to a design version.

    Per-firm concurrency of 4 (§9) is enforced by the caller checking
    :meth:`count_active` before enqueuing. Any model edit calls
    :meth:`mark_stale_for_project`, which is what puts the "Design changed since this
    render" banner on old images instead of silently showing stale ones.
    """

    row_type = models.RenderJob
    entity_name = "render_job"

    def to_domain(self, row: models.RenderJob) -> RenderJob:
        return RenderJob.from_row(row)

    # -- reads ---------------------------------------------------------
    async def list_for_version(
        self, project_id: uuid.UUID, design_version_id: uuid.UUID
    ) -> list[RenderJob]:
        stmt = (
            self._project_scoped_select(project_id)
            .where(models.RenderJob.design_version_id == design_version_id)
            .order_by(models.RenderJob.created_at.desc())
        )
        return [self.to_domain(row) for row in await self._all(stmt)]

    async def list_gallery(
        self, project_id: uuid.UUID, *, limit: int | None = None, cursor: str | None = None
    ) -> Page[RenderJob]:
        """Render history, newest first (only successful ones carry an image)."""
        return await self.list_for_project(project_id, limit=limit, cursor=cursor)

    async def count_active(self) -> int:
        """Queued+running renders for this firm — the §9 concurrency-4 gate."""
        stmt = self._scoped_select().where(
            models.RenderJob.status.in_(("queued", "running"))
        )
        return await self._count(stmt)

    # -- writes --------------------------------------------------------
    async def enqueue(
        self,
        project_id: uuid.UUID,
        *,
        mode: str,
        view: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        design_version_id: uuid.UUID | None = None,
        provider: str = "mock",
    ) -> RenderJob:
        self.ctx.require_write("starting a render")
        if mode not in models.RENDER_MODES:
            raise RepositoryUsageError(
                "mode must be one of %s." % ", ".join(models.RENDER_MODES)
            )
        await require_project_in_firm(self._session, self.firm_id, project_id)
        if design_version_id is not None:
            await require_design_version_in_firm(
                self._session, self.firm_id, design_version_id
            )
        row = self._new_row(
            project_id=project_id,
            design_version_id=design_version_id,
            view=view or {},
            params=params or {},
            mode=mode,
            provider=provider,
            status="queued",
            progress=0,
            stale=False,
        )
        await self._insert(row)
        self._log.info(
            "render_job.enqueued",
            entity_id=str(row.id),
            project_id=str(project_id),
            render_mode=mode,
            provider=provider,
        )
        return self.to_domain(row)

    async def set_progress(self, job_id: uuid.UUID, progress: int) -> RenderJob:
        row = await self._require_row(job_id)
        row.progress = _clamp_progress(progress)
        if row.status == "queued":
            row.status = "running"
        await self.flush()
        return self.to_domain(row)

    async def succeed(self, job_id: uuid.UUID, output_url: str) -> RenderJob:
        row = await self._require_row(job_id)
        row.status = "succeeded"
        row.progress = 100
        row.output_url = output_url
        row.error = None
        await self.flush()
        self._log.info("render_job.succeeded", entity_id=str(job_id))
        return self.to_domain(row)

    async def fail(self, job_id: uuid.UUID, error: str) -> RenderJob:
        row = await self._require_row(job_id)
        row.status = "failed"
        row.error = error[:2000]
        await self.flush()
        self._log.warning("render_job.failed", entity_id=str(job_id))
        return self.to_domain(row)

    async def cancel(self, job_id: uuid.UUID) -> RenderJob:
        row = await self._require_row(job_id)
        if row.status in models.JOB_TERMINAL_STATUSES:
            return self.to_domain(row)
        row.status = "cancelled"
        await self.flush()
        return self.to_domain(row)

    async def mark_stale_for_project(
        self, project_id: uuid.UUID, *, except_design_version_id: uuid.UUID | None = None
    ) -> int:
        """Flag existing renders stale after a model edit. Returns rows affected.

        **Queued and running jobs are marked too, not only succeeded ones.** A render
        that is mid-flight when the design moves was pinned, at enqueue, to the design
        as it was *before* this edit — whatever image it produces depicts the old plan.
        Marking only ``succeeded`` rows left that window open: the job finished after
        the edit, ``succeed()`` never touches ``stale``, and the gallery showed a
        pre-edit image with no banner. Since ops append holds the branch lock, every
        row that exists at this moment predates the edit; a render enqueued after it
        is not in the table yet and stays correctly fresh.

        ``failed``/``cancelled`` rows are left alone — they carry no image, so a
        staleness banner on them would be noise about nothing.
        """
        stmt = (
            self._project_scoped_select(project_id)
            .where(models.RenderJob.stale.is_(False))
            .where(models.RenderJob.status.in_(("queued", "running", "succeeded")))
        )
        if except_design_version_id is not None:
            stmt = stmt.where(
                models.RenderJob.design_version_id != except_design_version_id
            )
        rows = await self._all(stmt)
        for row in rows:
            row.stale = True
        if rows:
            await self.flush()
            self._log.info(
                "render_job.marked_stale", project_id=str(project_id), count=len(rows)
            )
        return len(rows)


__all__ = ["RenderJobRepository", "SolverJobRepository"]
