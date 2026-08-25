"""Alembic environment.

Uses the **sync** engine (``postgresql+psycopg``) deliberately: migrations are a
short-lived, strictly sequential batch job, and running them through asyncio buys
nothing while making tracebacks worse.

The URL comes from :mod:`garh_api.config`, so ``alembic.ini`` holds no credentials.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

from garh_api.config import get_settings
from garh_api.db import build_sync_url, get_sync_engine
from garh_api.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

#: What ``--autogenerate`` diffs against.
target_metadata = Base.metadata


def _include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Keep autogenerate focused on our own tables."""
    if type_ == "table" and name in ("spatial_ref_sys",):
        return False
    return True


def run_migrations_offline() -> None:
    """``--sql`` mode: emit DDL to stdout without connecting."""
    context.configure(
        url=build_sync_url(get_settings()),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
        # Deterministic constraint names come from Base.metadata's naming convention.
        render_as_batch=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Normal mode: connect and run inside one transaction."""
    engine = get_sync_engine(get_settings(), pooled=False)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=_include_object,
            poolclass=pool.NullPool,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
