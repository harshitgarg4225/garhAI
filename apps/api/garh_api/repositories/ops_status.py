"""Deployment-wide job statistics for the platform owner's ops page.

Non-tenant, and the reason is the reader: the person on ``/platform/ops`` is the
deployment's owner asking "is the solver keeping up?", and that question has no
firm. What comes out is **aggregate counts and percentiles** over the job tables —
never a job id, a project id, a firm id, an option or an error string — so a
tenant's data is not reachable through here even in principle. The allowlist in
``tests/test_no_unscoped_queries.py`` names this module with that reason; the
router that uses it is gated by the platform-owner allowlist, not a firm role.

Durations are ``updated_at - created_at`` on terminal rows: the time from enqueue
to the last lifecycle write. That includes queue wait, which is the number an
operator wants (a healthy solver with a 3-minute queue is a capacity problem the
handler's own timer would hide). The worker's heartbeat carries the handler-only
p50/p95 for the same window, so the two can be read side by side.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from garh_api import models

#: The tables that carry a job lifecycle, keyed by the name the ops page uses.
#: Export jobs live in Redis with a 24 h TTL (``garh_api.queue`` says why) and are
#: counted there, not here.
JOB_TABLES: dict[str, type[models.SolverJob] | type[models.RenderJob]] = {
    "solver": models.SolverJob,
    "render": models.RenderJob,
}


@dataclass(frozen=True, slots=True)
class DurationStats:
    """Percentiles over terminal jobs of one kind, in whole milliseconds."""

    count: int
    p50_ms: int
    p95_ms: int
    max_ms: int


class OpsStatusRepository:
    """Counts and percentiles over every firm's jobs. Aggregates only."""

    entity_name = "ops_status"

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def job_counts_since(self, since: datetime) -> dict[str, dict[str, int]]:
        """``{kind: {status: count}}`` for jobs created at or after ``since``.

        Every status in :data:`models.JOB_STATUSES` is present for every kind, zero
        included, so the page never has to guess whether a missing key means "none"
        or "not reported".
        """
        out: dict[str, dict[str, int]] = {}
        for kind, table in JOB_TABLES.items():
            counts = dict.fromkeys(models.JOB_STATUSES, 0)
            result = await self._session.execute(
                select(table.status, func.count())
                .where(table.created_at >= since)
                .group_by(table.status)
            )
            for status, count in result.all():
                if status in counts:
                    counts[str(status)] = int(count)
            out[kind] = counts
        return out

    async def job_durations_since(self, since: datetime) -> dict[str, DurationStats]:
        """Enqueue-to-terminal percentiles per kind over the window.

        ``percentile_cont`` is computed by Postgres so the page costs one aggregate
        per table, not a row scan in Python. ``cancelled`` is excluded: a job the
        user stopped after two seconds is not a two-second job.
        """
        out: dict[str, DurationStats] = {}
        for kind, table in JOB_TABLES.items():
            elapsed_ms = func.extract("epoch", table.updated_at - table.created_at) * 1000
            result = await self._session.execute(
                select(
                    func.count(),
                    func.percentile_cont(0.5).within_group(elapsed_ms),
                    func.percentile_cont(0.95).within_group(elapsed_ms),
                    func.max(elapsed_ms),
                ).where(
                    table.created_at >= since,
                    table.status.in_(("succeeded", "failed")),
                )
            )
            count, p50, p95, longest = result.one()
            out[kind] = DurationStats(
                count=int(count or 0),
                p50_ms=round(float(p50 or 0)),
                p95_ms=round(float(p95 or 0)),
                max_ms=round(float(longest or 0)),
            )
        return out

    async def alembic_revisions(self) -> list[str] | None:
        """The revision(s) the database says it is at; ``None`` when Alembic has
        never stamped it (a ``create_all`` schema, or a database migrations never
        reached). ``to_regclass`` first, so an absent table is an answer rather than
        an aborted transaction."""
        present = await self._session.execute(
            text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
        )
        if not bool(present.scalar()):
            return None
        rows = await self._session.execute(
            text("SELECT version_num FROM alembic_version ORDER BY version_num")
        )
        return [str(row[0]) for row in rows.all()]


__all__ = ["JOB_TABLES", "DurationStats", "OpsStatusRepository"]
