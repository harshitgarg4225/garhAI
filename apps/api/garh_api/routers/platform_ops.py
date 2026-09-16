"""``GET /admin/ops`` — the deployment's operational state, for the platform owner.

The document is assembled by :mod:`garh_api.platform_status`; this module is the
wire shape and the gate. The gate is the same ``PLATFORM_OWNER_EMAILS`` allowlist
that guards the platform fee (``routers/admin.py``): a firm admin runs a practice,
the owner runs the deployment, and queue depths across every firm are the owner's
to see. An empty allowlist refuses everyone, on purpose.

Why owner-only rather than "any signed-in admin": the page names hostnames,
counts every firm's jobs together and states which third-party providers are
live. None of that is a secret, but all of it is about the platform rather than
about the caller's practice, and a practice has no business knowing whether the
practice next door is generating plans right now.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import Field

from garh_api.platform_status import PlatformStatus, platform_status
from garh_api.routers import SessionDep
from garh_api.routers.admin import OwnerDep
from garh_api.schemas import ResponseModel

router = APIRouter(prefix="/admin", tags=["admin"])


class QueueOut(ResponseModel):
    name: str
    worker: str
    pending: int
    delayed: int
    processing: int
    dead: int


class WorkerOut(ResponseModel):
    worker: str
    instance: str
    hostname: str | None = None
    sent_at: str
    age_seconds: int
    interval_seconds: int
    stale: bool
    draining: bool
    uptime_seconds: int
    in_flight: int
    concurrency: int
    jobs_received: int
    jobs_succeeded: int
    jobs_failed: int
    jobs_retried: int
    jobs_dead_lettered: int
    p50_duration_ms: int
    p95_duration_ms: int
    provider_llm: str | None = None
    provider_render: str | None = None
    sentry: str
    release: str | None = None
    env: str | None = None


class JobKindOut(ResponseModel):
    kind: str
    counts: dict[str, int]
    terminal_count: int = Field(description="Succeeded + failed rows the percentiles cover.")
    p50_ms: int
    p95_ms: int
    max_ms: int


class MigrationOut(ResponseModel):
    current: list[str]
    heads: list[str]
    up_to_date: bool
    reason: str | None = None


class ProvidersOut(ResponseModel):
    llm: str
    render: str
    billing: str
    mail: str


class ObservabilityOut(ResponseModel):
    sentry: str
    log_format: str
    workers_reporting: int
    workers_missing: list[str]
    workers_stale: list[str]
    redis: str
    database: str


class PlatformStatusOut(ResponseModel):
    """Everything on ``/platform/ops``. Names, counts and timestamps — no secrets."""

    generated_at: datetime
    env: str
    version: str
    release: str | None = None
    providers: ProvidersOut
    sentry: str = Field(description="`on` when this API process has a Sentry DSN.")
    migrations: MigrationOut
    queues: list[QueueOut]
    workers: list[WorkerOut]
    jobs: list[JobKindOut]
    export_jobs_live: int
    job_window_hours: int
    observability: ObservabilityOut

    @classmethod
    def of(cls, status: PlatformStatus) -> PlatformStatusOut:
        return cls(
            generated_at=status.generated_at,
            env=status.env,
            version=status.version,
            release=status.release,
            providers=ProvidersOut.model_validate(status.providers),
            sentry=status.sentry,
            migrations=MigrationOut.model_validate(status.migrations),
            queues=[QueueOut.model_validate(q) for q in status.queues],
            workers=[WorkerOut.model_validate(w) for w in status.workers],
            jobs=[JobKindOut.model_validate(j) for j in status.jobs],
            export_jobs_live=status.export_jobs_live,
            job_window_hours=status.job_window_hours,
            observability=ObservabilityOut.model_validate(status.observability),
        )


@router.get(
    "/ops",
    response_model=PlatformStatusOut,
    summary="Queues, workers, jobs, providers and the migration head — owner only",
)
async def get_platform_status(session: SessionDep, owner: OwnerDep) -> PlatformStatusOut:
    """403 for anyone not on the owner allowlist; the document otherwise."""
    del owner  # the dependency is the authorisation
    return PlatformStatusOut.of(await platform_status(session))


__all__ = ["PlatformStatusOut", "router"]
