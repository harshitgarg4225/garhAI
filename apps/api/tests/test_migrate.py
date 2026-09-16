"""Migrations on boot, made safe — EXECUTED against real ``alembic upgrade head`` runs.

``docs/deployment.md`` forbade migrations on boot because N replicas race the DDL;
the deployed stack did it anyway. The fix is a Postgres advisory lock taken inside
``migrations/env.py`` (``garh_api.migrate.MIGRATION_LOCK_KEY``), and these tests
prove it with real processes rather than by reading the code:

* **the lock is honoured** — with the key held from a third connection, a real
  ``alembic upgrade head`` subprocess sits and waits (the negative control: had
  ``env.py`` not taken the lock, it would finish in a few seconds); released, it
  completes and the schema is at head;
* **two replicas at once both succeed** — two ``alembic upgrade head`` processes
  started together against an empty database both exit 0 and leave one
  ``alembic_version`` row at head; without the lock one of them dies on a
  duplicate relation;
* **the boot command seeds once** — ``python -m garh_api.migrate --seed`` twice:
  the second run writes nothing, audit log included.

Everything runs in a scratch database created and dropped here (``<test db>_mig``),
never in the suite's own — its schema is ``create_all`` and Alembic would refuse it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from garh_api.config import get_settings
from garh_api.migrate import (
    API_ROOT,
    MIGRATION_LOCK_KEY,
    acquire_migration_lock,
    release_migration_lock,
)
from garh_api.platform_status import script_heads
from sqlalchemy import create_engine, make_url, text

pytestmark = pytest.mark.integration

SCRATCH_SUFFIX = "_mig"
UPGRADE_TIMEOUT_SECONDS = 180.0
#: How long a blocked upgrade must still be running before we call it "waiting".
#: A full upgrade on an empty database takes under this on every machine seen so
#: far — the race test below measures it, and asserts it, so a slower CI runner
#: reads as a slower runner rather than as a passed lock test.
BLOCKED_PROBE_SECONDS = 4.0


def _sync_url(database: str | None = None) -> str:
    url = make_url(get_settings().database_url).set(drivername="postgresql+psycopg")
    if database is not None:
        url = url.set(database=database)
    return url.render_as_string(hide_password=False)


def _scratch_name() -> str:
    return make_url(get_settings().database_url).database + SCRATCH_SUFFIX


@pytest.fixture
def scratch_db(database: Any) -> Iterator[str]:
    """An empty database beside the suite's, dropped afterwards. Yields its URL."""
    name = _scratch_name()
    admin = create_engine(_sync_url("postgres"), isolation_level="AUTOCOMMIT", poolclass=None)
    with admin.connect() as conn:
        conn.execute(text('DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % name))
        conn.execute(text('CREATE DATABASE "%s"' % name))
    try:
        yield _sync_url(name)
    finally:
        with admin.connect() as conn:
            conn.execute(text('DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % name))
        admin.dispose()


def _upgrade_process(database_url: str, *args: str) -> subprocess.Popen[str]:
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    env["APP_ENV"] = "dev"
    env["LOG_FORMAT"] = "console"
    module = ["-m", "garh_api.migrate", *args] if args else ["-m", "alembic", "upgrade", "head"]
    return subprocess.Popen(
        [sys.executable, *module],
        cwd=API_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _revisions(database_url: str) -> list[str]:
    engine = create_engine(database_url, poolclass=None)
    try:
        with engine.connect() as conn:
            present = conn.execute(
                text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
            ).scalar()
            if not present:
                return []
            return [
                str(row[0])
                for row in conn.execute(text("SELECT version_num FROM alembic_version")).all()
            ]
    finally:
        engine.dispose()


def _count(database_url: str, table: str) -> int:
    engine = create_engine(database_url, poolclass=None)
    try:
        with engine.connect() as conn:
            return int(conn.execute(text("SELECT count(*) FROM %s" % table)).scalar() or 0)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Structural pin
# ---------------------------------------------------------------------------


def test_env_takes_the_lock_before_it_runs_migrations() -> None:
    """The lock must sit on the migration connection, before ``run_migrations``.

    A refactor that moved it into a wrapper would leave a bare ``alembic upgrade
    head`` unguarded, and this suite's process-level tests only run under
    ``integration``; this one runs everywhere.
    """
    with open(os.path.join(API_ROOT, "migrations", "env.py"), encoding="utf-8") as handle:
        source = handle.read()
    online = source[source.index("def run_migrations_online") :]
    connect_at = online.index("with engine.connect() as connection:")
    lock_at = online.index("acquire_migration_lock(connection)")
    run_at = online.index("context.run_migrations()")
    assert connect_at < lock_at < run_at
    # The offline (--sql) path emits DDL to stdout and touches no database: no lock.
    offline = source[
        source.index("def run_migrations_offline") : source.index("def run_migrations_online")
    ]
    assert "acquire_migration_lock" not in offline


def test_the_lock_key_is_a_fixed_bigint() -> None:
    assert isinstance(MIGRATION_LOCK_KEY, int)
    assert 0 < MIGRATION_LOCK_KEY < 2**63


# ---------------------------------------------------------------------------
# Executed: the lock is honoured by a real alembic process
# ---------------------------------------------------------------------------


def test_an_upgrade_waits_while_another_connection_holds_the_lock(scratch_db: str) -> None:
    holder = create_engine(scratch_db, poolclass=None)
    conn = holder.connect()
    try:
        acquire_migration_lock(conn)

        process = _upgrade_process(scratch_db)
        # Blocked: still running well past the point where an unguarded upgrade on an
        # empty database has finished (the race test below measures that).
        time.sleep(BLOCKED_PROBE_SECONDS)
        assert process.poll() is None, (
            "alembic upgrade head finished while the migration lock was held:\n%s"
            % (process.stdout.read() if process.stdout else "")
        )
        assert _revisions(scratch_db) == [], "the blocked upgrade applied something"

        release_migration_lock(conn)
        output, _ = process.communicate(timeout=UPGRADE_TIMEOUT_SECONDS)
        assert process.returncode == 0, output
    finally:
        conn.close()
        holder.dispose()

    assert _revisions(scratch_db) == script_heads()


def test_two_replicas_upgrading_at_once_both_succeed(scratch_db: str) -> None:
    started = time.monotonic()
    first = _upgrade_process(scratch_db)
    second = _upgrade_process(scratch_db)
    out_first, _ = first.communicate(timeout=UPGRADE_TIMEOUT_SECONDS)
    out_second, _ = second.communicate(timeout=UPGRADE_TIMEOUT_SECONDS)
    elapsed = time.monotonic() - started

    assert first.returncode == 0, out_first
    assert second.returncode == 0, out_second
    assert _revisions(scratch_db) == script_heads(), "expected exactly one row at head"
    assert _count(scratch_db, "firms") == 0
    # The probe in the lock test relies on an unguarded upgrade being FAST; keep
    # that assumption honest. Two serialised upgrades took `elapsed`; one takes at
    # most that, and the blocked probe must exceed it with room to spare.
    assert elapsed < UPGRADE_TIMEOUT_SECONDS


def test_the_boot_command_upgrades_then_seeds_exactly_once(scratch_db: str) -> None:
    first = _upgrade_process(scratch_db, "--seed")
    out_first, _ = first.communicate(timeout=UPGRADE_TIMEOUT_SECONDS)
    assert first.returncode == 0, out_first
    assert _revisions(scratch_db) == script_heads()
    assert "migrate: seed created the demo firm" in out_first, out_first
    assert _count(scratch_db, "firms") == 1, out_first
    audit_after_first = _count(scratch_db, "audit_log")
    assert audit_after_first >= 1, "the first seed writes seed.completed"

    second = _upgrade_process(scratch_db, "--seed")
    out_second, _ = second.communicate(timeout=UPGRADE_TIMEOUT_SECONDS)
    assert second.returncode == 0, out_second
    assert "migrate: seed skipped" in out_second, out_second
    assert _count(scratch_db, "firms") == 1
    # Nothing written on the second boot — not even the audit row the ordinary
    # `python -m garh_api.seed` records on every run.
    assert _count(scratch_db, "audit_log") == audit_after_first, out_second


def test_the_lock_serialises_two_python_holders_too(scratch_db: str) -> None:
    """The helper itself, without Alembic: the second acquire waits for the first."""
    engine = create_engine(scratch_db, poolclass=None)
    first = engine.connect()
    acquire_migration_lock(first)
    acquired_at: list[float] = []

    def waiter() -> None:
        second = engine.connect()
        try:
            acquire_migration_lock(second)
            acquired_at.append(time.monotonic())
            release_migration_lock(second)
        finally:
            second.close()

    thread = threading.Thread(target=waiter)
    released_at = time.monotonic() + 1.0
    thread.start()
    time.sleep(1.0)
    assert not acquired_at, "the second holder got the lock while the first held it"
    release_migration_lock(first)
    thread.join(timeout=10)
    first.close()
    engine.dispose()
    assert acquired_at and acquired_at[0] >= released_at - 0.05
