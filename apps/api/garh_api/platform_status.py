"""The operator's one page: what this deployment is actually running on, right now.

``docs/deployment.md`` listed the queue-depth metric and job-duration histograms
under "still to add", and the reader's audit found the sharper problem behind
that: **observability could be silently off**. Sentry is a config flip nobody had
flipped, a worker can be absent with nothing to say so, and the only way to learn
the migration head was a shell. Everything on this page is a fact the process can
establish for itself, assembled here and served by ``routers/platform_ops.py`` to
the platform owner (the ``PLATFORM_OWNER_EMAILS`` allowlist, never a firm role).

What is here, and where each number comes from:

* **queues** — ``LLEN``/``ZCARD`` on the three work queues and their
  ``:delayed`` / ``:processing`` / ``:dead`` companions (``garh_api.queue`` names them).
* **workers** — the heartbeat keys every worker process writes on each sweep
  (``services/common/heartbeat.py``), plus which of the three expected workers has
  **no** heartbeat at all. Absence is the alarm; presence is the detail.
* **jobs** — counts by status and enqueue-to-terminal p50/p95 per kind over the
  last 24 hours, from the job tables (``repositories/ops_status.py``: aggregates
  only, never a row).
* **providers** — the LLM, render, billing and mail channels in force, by name.
* **sentry** — ``on``/``off`` for this process, and per worker from its heartbeat.
* **migrations** — the database's ``alembic_version`` against the code's heads.

Nothing here is a secret or a person: names, counts, timestamps, revision ids.
``tests/test_platform_ops.py`` asserts the document never carries the DSN, a key,
a connection string or a job id.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

import garh_api
from garh_api import __version__
from garh_api.auth import otp_delivery_channel
from garh_api.config import Settings, get_settings
from garh_api.logging import get_logger
from garh_api.queue import (
    WORKERS,
    dead_letter_queue,
    delayed_queue,
    get_redis,
    processing_queue,
    queue_name,
    worker_heartbeat_pattern,
)
from garh_api.repositories import OpsStatusRepository

_log = get_logger(__name__)

#: The names the ops page expects a heartbeat from. Mirrors
#: ``services.common.heartbeat.EXPECTED_WORKERS`` — the three queues.
EXPECTED_WORKERS: tuple[str, ...] = tuple(WORKERS)

#: How far back the job counts and percentiles look.
JOB_WINDOW = timedelta(hours=24)

#: A heartbeat older than this many of its own intervals is reported ``stale``: the
#: key has not lapsed yet, but the worker has missed beats. Two, so one slow sweep
#: is not a page-level alarm and three is.
STALE_AFTER_INTERVALS = 2

#: Where the Alembic scripts live, relative to the package — the same directory
#: ``alembic.ini``'s ``script_location`` names, resolved so this works from any cwd
#: (the Railway start command runs from ``/app/apps/api``; a test from the repo root).
MIGRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(garh_api.__file__))), "migrations"
)


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QueueStatus:
    name: str
    worker: str
    pending: int
    delayed: int
    processing: int
    dead: int


@dataclass(frozen=True, slots=True)
class WorkerStatus:
    worker: str
    instance: str
    hostname: str | None
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
    provider_llm: str | None
    provider_render: str | None
    sentry: str
    release: str | None
    env: str | None


@dataclass(frozen=True, slots=True)
class JobKindStatus:
    kind: str
    counts: dict[str, int]
    terminal_count: int
    p50_ms: int
    p95_ms: int
    max_ms: int


@dataclass(frozen=True, slots=True)
class MigrationStatus:
    #: What ``alembic_version`` holds; empty when the table is absent.
    current: list[str]
    #: What the code's migration scripts say the head(s) are.
    heads: list[str]
    up_to_date: bool
    #: Why ``up_to_date`` is false, in words an operator can act on.
    reason: str | None


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    llm: str
    render: str
    billing: str
    #: ``smtp`` | ``brevo-http`` | ``dev-echo`` | ``none``.
    mail: str


@dataclass(frozen=True, slots=True)
class Observability:
    sentry: str
    log_format: str
    workers_reporting: int
    workers_missing: list[str]
    workers_stale: list[str]
    #: Whether every check above came back. A Redis outage makes the queue and worker
    #: sections empty; this says so instead of letting empty read as idle.
    redis: str
    database: str


@dataclass(frozen=True, slots=True)
class PlatformStatus:
    generated_at: datetime
    env: str
    version: str
    release: str | None
    providers: ProviderStatus
    sentry: str
    migrations: MigrationStatus
    queues: list[QueueStatus] = field(default_factory=list)
    workers: list[WorkerStatus] = field(default_factory=list)
    jobs: list[JobKindStatus] = field(default_factory=list)
    export_jobs_live: int = 0
    job_window_hours: int = int(JOB_WINDOW.total_seconds() // 3600)
    observability: Observability = field(
        default_factory=lambda: Observability(
            sentry="off",
            log_format="json",
            workers_reporting=0,
            workers_missing=list(EXPECTED_WORKERS),
            workers_stale=[],
            redis="down",
            database="down",
        )
    )


# ---------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------


def release_tag() -> str | None:
    """``APP_VERSION`` beats ``GIT_SHA``; neither is a Settings field (deploy metadata)."""
    return os.environ.get("APP_VERSION") or os.environ.get("GIT_SHA") or None


def provider_status(settings: Settings) -> ProviderStatus:
    return ProviderStatus(
        llm=settings.provider_llm,
        render=settings.provider_render,
        billing=settings.provider_billing,
        mail=otp_delivery_channel(settings),
    )


def script_heads(migrations_dir: str = MIGRATIONS_DIR) -> list[str]:
    """The head revision(s) of the migration scripts on disk."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", migrations_dir)
    return sorted(ScriptDirectory.from_config(config).get_heads())


def migration_status(current: list[str] | None, heads: list[str]) -> MigrationStatus:
    """Compare what the database says with what the code says.

    Pure, so the verdict can be tested against every shape: never stamped, behind,
    ahead (a rollback of the code without a downgrade), and two heads (a branch the
    coordinator has not re-chained yet — which is a fact to show, not to hide).
    """
    if current is None:
        return MigrationStatus(
            current=[],
            heads=heads,
            up_to_date=False,
            reason="alembic_version is absent: migrations have never run on this database.",
        )
    if not heads:
        return MigrationStatus(
            current=current,
            heads=[],
            up_to_date=False,
            reason="no migration scripts found beside the package.",
        )
    if sorted(current) == sorted(heads):
        reason = None
        if len(heads) > 1:
            reason = (
                "the migration scripts have %d heads; merge them before the next release."
                % len(heads)
            )
        return MigrationStatus(current=sorted(current), heads=heads, up_to_date=True, reason=reason)
    return MigrationStatus(
        current=sorted(current),
        heads=heads,
        up_to_date=False,
        reason="database is at %s, code head is %s: run `alembic upgrade head`."
        % (", ".join(sorted(current)) or "nothing", ", ".join(heads)),
    )


async def queue_status(settings: Settings) -> list[QueueStatus]:
    """Depth of each work queue and its companions, straight from Redis."""
    redis = get_redis(settings)
    out: list[QueueStatus] = []
    for worker in WORKERS:
        base = queue_name(worker, settings)
        out.append(
            QueueStatus(
                name=base,
                worker=worker,
                pending=int(await redis.llen(base) or 0),
                delayed=int(await redis.zcard(delayed_queue(worker, settings)) or 0),
                processing=int(await redis.llen(processing_queue(worker, settings)) or 0),
                dead=int(await redis.llen(dead_letter_queue(worker, settings)) or 0),
            )
        )
    return out


def _parse_sent_at(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def worker_from_heartbeat(document: dict[str, Any], *, now: datetime) -> WorkerStatus | None:
    """One heartbeat document → the row the page shows. ``None`` if unreadable."""
    worker = document.get("worker")
    instance = document.get("instance")
    sent_at = _parse_sent_at(document.get("sentAt"))
    if not isinstance(worker, str) or not isinstance(instance, str) or sent_at is None:
        return None
    interval = max(1, _int(document.get("intervalSeconds"), 10))
    age = max(0, int((now - sent_at).total_seconds()))
    hostname = document.get("hostname")
    return WorkerStatus(
        worker=worker,
        instance=instance,
        hostname=hostname if isinstance(hostname, str) else None,
        sent_at=sent_at.isoformat(),
        age_seconds=age,
        interval_seconds=interval,
        stale=age > STALE_AFTER_INTERVALS * interval,
        draining=bool(document.get("draining", False)),
        uptime_seconds=_int(document.get("uptimeSeconds")),
        in_flight=_int(document.get("inFlight")),
        concurrency=_int(document.get("concurrency"), 1),
        jobs_received=_int(document.get("jobsReceived")),
        jobs_succeeded=_int(document.get("jobsSucceeded")),
        jobs_failed=_int(document.get("jobsFailed")),
        jobs_retried=_int(document.get("jobsRetried")),
        jobs_dead_lettered=_int(document.get("jobsDeadLettered")),
        p50_duration_ms=_int(document.get("p50DurationMs")),
        p95_duration_ms=_int(document.get("p95DurationMs")),
        provider_llm=str(document["providerLlm"]) if "providerLlm" in document else None,
        provider_render=str(document["providerRender"]) if "providerRender" in document else None,
        sentry="on" if document.get("sentry") == "on" else "off",
        release=str(document["release"]) if document.get("release") else None,
        env=str(document["env"]) if document.get("env") else None,
    )


async def worker_status(settings: Settings, *, now: datetime) -> list[WorkerStatus]:
    """Every heartbeat under the prefix, newest first. SCAN, never KEYS."""
    redis = get_redis(settings)
    found: list[WorkerStatus] = []
    async for key in redis.scan_iter(match=worker_heartbeat_pattern(), count=200):
        raw = await redis.get(key)
        if not raw:
            continue
        try:
            document = json.loads(raw)
        except ValueError:
            _log.warning("platform_status.heartbeat_unreadable", key=str(key))
            continue
        if not isinstance(document, dict):
            continue
        row = worker_from_heartbeat(document, now=now)
        if row is not None:
            found.append(row)
    found.sort(key=lambda row: (row.worker, row.age_seconds, row.instance))
    return found


async def export_jobs_live(settings: Settings) -> int:
    """Export jobs are Redis-only with a 24 h TTL, so a key count IS the 24 h count."""
    redis = get_redis(settings)
    count = 0
    async for _key in redis.scan_iter(match="garh:export:*", count=500):
        count += 1
    return count


async def job_status(session: AsyncSession, *, since: datetime) -> list[JobKindStatus]:
    repo = OpsStatusRepository(session)
    counts = await repo.job_counts_since(since)
    durations = await repo.job_durations_since(since)
    out: list[JobKindStatus] = []
    for kind in sorted(counts):
        stats = durations.get(kind)
        out.append(
            JobKindStatus(
                kind=kind,
                counts=counts[kind],
                terminal_count=stats.count if stats else 0,
                p50_ms=stats.p50_ms if stats else 0,
                p95_ms=stats.p95_ms if stats else 0,
                max_ms=stats.max_ms if stats else 0,
            )
        )
    return out


# ---------------------------------------------------------------------------
# The whole document
# ---------------------------------------------------------------------------


async def platform_status(
    session: AsyncSession,
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
) -> PlatformStatus:
    """Assemble the page. Each dependency is probed on its own, so one that is down
    leaves its section empty **and named** in ``observability`` rather than 500ing the
    page an operator opens precisely because something is down."""
    cfg = settings or get_settings()
    at = now or datetime.now(UTC)
    since = at - JOB_WINDOW

    redis_ok = True
    queues: list[QueueStatus] = []
    workers: list[WorkerStatus] = []
    exports = 0
    try:
        queues = await queue_status(cfg)
        workers = await worker_status(cfg, now=at)
        exports = await export_jobs_live(cfg)
    except Exception as exc:
        redis_ok = False
        _log.error("platform_status.redis_failed", error="%s: %s" % (type(exc).__name__, exc))

    database_ok = True
    jobs: list[JobKindStatus] = []
    current_revisions: list[str] | None = None
    try:
        current_revisions = await OpsStatusRepository(session).alembic_revisions()
        jobs = await job_status(session, since=since)
    except Exception as exc:
        database_ok = False
        _log.error("platform_status.database_failed", error="%s: %s" % (type(exc).__name__, exc))

    try:
        heads = script_heads()
    except Exception as exc:
        _log.error("platform_status.scripts_failed", error="%s: %s" % (type(exc).__name__, exc))
        heads = []

    seen = {row.worker for row in workers}
    return PlatformStatus(
        generated_at=at,
        env=cfg.env,
        version=__version__,
        release=release_tag(),
        providers=provider_status(cfg),
        sentry="on" if cfg.sentry_enabled else "off",
        migrations=migration_status(current_revisions, heads),
        queues=queues,
        workers=workers,
        jobs=jobs,
        export_jobs_live=exports,
        observability=Observability(
            sentry="on" if cfg.sentry_enabled else "off",
            log_format=cfg.log_format,
            workers_reporting=len(workers),
            workers_missing=[name for name in EXPECTED_WORKERS if name not in seen],
            workers_stale=sorted({row.instance for row in workers if row.stale}),
            redis="ok" if redis_ok else "down",
            database="ok" if database_ok else "down",
        ),
    )


__all__ = [
    "EXPECTED_WORKERS",
    "JOB_WINDOW",
    "MIGRATIONS_DIR",
    "STALE_AFTER_INTERVALS",
    "JobKindStatus",
    "MigrationStatus",
    "Observability",
    "PlatformStatus",
    "ProviderStatus",
    "QueueStatus",
    "WorkerStatus",
    "migration_status",
    "platform_status",
    "provider_status",
    "release_tag",
    "script_heads",
    "worker_from_heartbeat",
]
