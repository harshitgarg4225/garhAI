"""Engine + session plumbing (psycopg 3).

Two engines, one URL:

* **async** — everything request-facing. ``postgresql+psycopg://`` is a dual-mode
  dialect, so the async engine and the sync engine share one ``DATABASE_URL``.
* **sync** — Alembic migrations, the seed script, and Redis-queue workers that have
  no event loop of their own.

Transaction discipline (important — repositories deliberately never commit):

* Route handlers get a session from :func:`get_db_session` (a FastAPI dependency)
  and it commits once, at the end, if the handler returned normally.
* Workers and scripts use :func:`session_scope` / :func:`sync_session_scope`.
* Repositories only ``flush()``. A repository that commits breaks multi-repo
  units of work (e.g. append ops + write a snapshot + record a credit event).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from garh_api.config import Settings, get_settings

#: The one driver we ship. psycopg 3 serves both sync and async.
_DRIVER = "psycopg"

_async_engine: AsyncEngine | None = None
_async_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_sync_engine: Engine | None = None
_sync_sessionmaker: sessionmaker[Session] | None = None


# ---------------------------------------------------------------------------
# URL normalisation
# ---------------------------------------------------------------------------


def normalise_database_url(url: str) -> str:
    """Force any Postgres URL onto the psycopg 3 dialect.

    Accepts ``postgres://``, ``postgresql://``, ``postgresql+asyncpg://``,
    ``postgresql+psycopg2://`` and returns ``postgresql+psycopg://``. Anything
    non-Postgres is returned untouched so tests can point at another backend.
    """
    parsed = make_url(url)
    backend = parsed.get_backend_name()
    if backend != "postgresql":
        return url
    # render_as_string(hide_password=False), NOT str(): SQLAlchemy 2's
    # URL.__str__ masks the password as ``***`` — so every engine built from
    # this function was literally authenticating with the three-character
    # password ``***``. Locally the dev stack's trust auth hid it; the first
    # real deployment surfaced it as an unexplainable auth failure.
    return parsed.set(drivername="postgresql+%s" % _DRIVER).render_as_string(hide_password=False)


def build_async_url(settings: Settings | None = None) -> str:
    """URL for the request-facing async engine."""
    cfg = settings or get_settings()
    return normalise_database_url(cfg.database_url)


def build_sync_url(settings: Settings | None = None) -> str:
    """URL for Alembic / workers / the seed script."""
    cfg = settings or get_settings()
    return normalise_database_url(cfg.database_url)


def _connect_args(settings: Settings) -> dict[str, object]:
    args: dict[str, object] = {"application_name": settings.app_name}
    if settings.db_statement_timeout_ms > 0:
        # Belt-and-braces against a runaway query holding a pool slot.
        args["options"] = "-c statement_timeout=%d" % settings.db_statement_timeout_ms
    return args


# ---------------------------------------------------------------------------
# Async engine (API)
# ---------------------------------------------------------------------------


def get_async_engine(settings: Settings | None = None) -> AsyncEngine:
    """Process-wide async engine (created on first use)."""
    global _async_engine
    if _async_engine is None:
        cfg = settings or get_settings()
        _async_engine = create_async_engine(
            build_async_url(cfg),
            echo=cfg.sql_echo,
            pool_pre_ping=True,
            pool_size=cfg.db_pool_size,
            max_overflow=cfg.db_max_overflow,
            pool_timeout=cfg.db_pool_timeout_seconds,
            pool_recycle=cfg.db_pool_recycle_seconds,
            connect_args=_connect_args(cfg),
        )
    return _async_engine


def get_sessionmaker(settings: Settings | None = None) -> async_sessionmaker[AsyncSession]:
    """Process-wide async session factory.

    ``expire_on_commit=False`` so domain objects built from rows survive the commit
    that a route dependency performs after the handler returns.
    """
    global _async_sessionmaker
    if _async_sessionmaker is None:
        _async_sessionmaker = async_sessionmaker(
            bind=get_async_engine(settings),
            expire_on_commit=False,
            autoflush=False,
            class_=AsyncSession,
        )
    return _async_sessionmaker


@asynccontextmanager
async def session_scope(settings: Settings | None = None) -> AsyncIterator[AsyncSession]:
    """Unit of work: commit on success, roll back on any exception."""
    factory = get_sessionmaker(settings)
    session = factory()
    try:
        yield session
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. One session (and one transaction) per request.

    ::

        @router.get("/projects")
        async def list_projects(
            session: AsyncSession = Depends(get_db_session),
            ctx: TenantCtx = Depends(require_tenant),
        ) -> ...:
            return await ProjectRepository(session, ctx).list()
    """
    async with session_scope() as session:
        yield session


async def dispose_async_engine() -> None:
    """Close the async pool (call from the FastAPI lifespan shutdown hook)."""
    global _async_engine, _async_sessionmaker
    if _async_engine is not None:
        await _async_engine.dispose()
    _async_engine = None
    _async_sessionmaker = None


async def healthcheck(settings: Settings | None = None) -> bool:
    """``SELECT 1`` — backs ``/healthz`` (§18). Never raises."""
    try:
        engine = get_async_engine(settings)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Sync engine (Alembic, workers, seed script)
# ---------------------------------------------------------------------------


def get_sync_engine(settings: Settings | None = None, *, pooled: bool = True) -> Engine:
    """Process-wide sync engine.

    ``pooled=False`` (``NullPool``) is the right choice inside short-lived worker
    subprocesses and inside Alembic, where a pool outliving its usefulness just
    holds connections open.
    """
    global _sync_engine
    if not pooled:
        cfg = settings or get_settings()
        from sqlalchemy import create_engine

        return create_engine(
            build_sync_url(cfg),
            echo=cfg.sql_echo,
            poolclass=NullPool,
            future=True,
            connect_args=_connect_args(cfg),
        )
    if _sync_engine is None:
        cfg = settings or get_settings()
        from sqlalchemy import create_engine

        _sync_engine = create_engine(
            build_sync_url(cfg),
            echo=cfg.sql_echo,
            pool_pre_ping=True,
            pool_size=cfg.db_pool_size,
            max_overflow=cfg.db_max_overflow,
            pool_timeout=cfg.db_pool_timeout_seconds,
            pool_recycle=cfg.db_pool_recycle_seconds,
            future=True,
            connect_args=_connect_args(cfg),
        )
    return _sync_engine


def get_sync_sessionmaker(settings: Settings | None = None) -> sessionmaker[Session]:
    global _sync_sessionmaker
    if _sync_sessionmaker is None:
        _sync_sessionmaker = sessionmaker(
            bind=get_sync_engine(settings), expire_on_commit=False, autoflush=False
        )
    return _sync_sessionmaker


@contextmanager
def sync_session_scope(settings: Settings | None = None) -> Iterator[Session]:
    """Sync unit of work for workers and the seed script."""
    session = get_sync_sessionmaker(settings)()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_sync_engine() -> None:
    global _sync_engine, _sync_sessionmaker
    if _sync_engine is not None:
        _sync_engine.dispose()
    _sync_engine = None
    _sync_sessionmaker = None


__all__ = [
    "build_async_url",
    "build_sync_url",
    "dispose_async_engine",
    "dispose_sync_engine",
    "get_async_engine",
    "get_db_session",
    "get_sessionmaker",
    "get_sync_engine",
    "get_sync_sessionmaker",
    "healthcheck",
    "normalise_database_url",
    "session_scope",
    "sync_session_scope",
]
