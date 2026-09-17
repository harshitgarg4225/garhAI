"""``python -m garh_api.migrate`` — the one boot step that touches the schema.

``docs/deployment.md`` says migrations must not run on boot with more than one
replica, because N containers running ``alembic upgrade head`` at once race the
same DDL. The deployed stack ran exactly that on boot anyway (with one replica,
which is why it never bit). This module makes the race impossible rather than
forbidden:

1. **A Postgres advisory lock guards every upgrade.** ``migrations/env.py`` takes
   :data:`MIGRATION_LOCK_KEY` with ``pg_advisory_lock`` on its connection before
   ``run_migrations``; the second replica blocks until the first finishes, then
   sees ``head`` and applies nothing. Session-level, so it is released when the
   connection closes — including when the process dies mid-migration. The lock is
   in ``env.py``, not here, so a bare ``alembic upgrade head`` from a shell is
   guarded too; this module is the documented entry point, not the only safe one.
2. **The seed is a no-op once the demo firm exists.** ``--seed`` runs
   ``garh_api.seed`` with ``skip_if_seeded``: one indexed lookup, then nothing
   written — not a firm-settings merge, not an audit row. Production refuses the
   seed by design (``SeedOptions.assert_allowed``); that refusal is logged and the
   boot continues, because the demo tenant is a dev convenience, not a dependency.

The lock helpers themselves (:data:`MIGRATION_LOCK_KEY`, :func:`acquire_migration_lock`,
:func:`release_migration_lock`) are defined in :mod:`garh_api.db`, the connection-plumbing
module, and re-exported here. An advisory lock is a statement on a raw connection, not
a table query, and ``db.py`` is the one non-repository module the tenancy audit
(``tests/test_no_unscoped_queries.py``) lets run SQL; a raw ``execute`` in this module
would be a §13 offender even though it never touches a tenant row.

The Railway api service's start command is therefore::

    python -m garh_api.migrate --seed && exec uvicorn garh_api.main:app --host 0.0.0.0 --port $PORT --workers 4

and ``deploy/railway/api.json`` says so; ``tests/test_deploy_config.py`` reads that
file and the runbook's table so the three cannot drift. ``tests/test_migrate.py``
races two real ``alembic upgrade head`` processes against a scratch database and
holds the lock from a third connection to prove an upgrade actually waits.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

import garh_api
from garh_api.db import MIGRATION_LOCK_KEY, acquire_migration_lock, release_migration_lock
from garh_api.logging import get_logger

_log = get_logger(__name__)

__all__ = [
    "API_ROOT",
    "MIGRATION_LOCK_KEY",
    "acquire_migration_lock",
    "release_migration_lock",
    "alembic_config",
    "upgrade_head",
    "seed_if_missing",
    "build_parser",
    "main",
]

#: ``apps/api`` — where ``alembic.ini`` and ``migrations/`` live. Resolved from the
#: package so this works from ``/app/apps/api`` (the image) and from the repo root.
API_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(garh_api.__file__)))


def alembic_config() -> Any:
    """The Alembic config with an absolute ``script_location``, cwd-independent."""
    from alembic.config import Config

    config = Config(os.path.join(API_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(API_ROOT, "migrations"))
    return config


def upgrade_head() -> None:
    """``alembic upgrade head`` in-process. The lock is taken inside ``env.py``."""
    from alembic import command

    _log.info("migrate.upgrade_starting")
    command.upgrade(alembic_config(), "head")
    _log.info("migrate.upgrade_done")


def seed_if_missing() -> dict[str, Any]:
    """Seed the demo tenant unless it exists; a production refusal is logged, not fatal."""
    from garh_api.seed.runner import SeedError, SeedOptions, run

    try:
        result = asyncio.run(run(SeedOptions(skip_if_seeded=True)))
    except SeedError as exc:
        _log.warning("migrate.seed_refused", reason=str(exc).splitlines()[0])
        return {"seeded": False, "reason": str(exc).splitlines()[0]}
    _log.info("migrate.seed_done", steps=dict(result.steps), skipped=result.skipped_reason)
    return {"seeded": result.skipped_reason is None, "steps": dict(result.steps)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m garh_api.migrate",
        description="Apply migrations under the advisory lock, then optionally seed once.",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="after upgrading, seed the demo firm if it does not exist (dev/staging)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from garh_api.config import get_settings
    from garh_api.logging import configure_logging

    settings = get_settings()
    configure_logging(settings)
    try:
        upgrade_head()
    except Exception as exc:
        _log.error("migrate.upgrade_failed", error="%s: %s" % (type(exc).__name__, exc))
        print("migrate failed: %s" % exc, file=sys.stderr)
        return 1
    # alembic.ini's fileConfig re-routes the root logger while the upgrade runs;
    # restore ours so the seed's own lines land in the JSON stream again. The plain
    # prints below are the operator-facing summary and survive either way.
    configure_logging(settings)
    print("migrate: schema at head")
    if args.seed:
        outcome = seed_if_missing()
        if outcome.get("seeded"):
            print("migrate: seed created the demo firm")
        elif "reason" in outcome:
            print("migrate: seed refused — %s" % outcome["reason"])
        else:
            print("migrate: seed skipped — demo firm exists")
    return 0


__all__ = [
    "API_ROOT",
    "MIGRATION_LOCK_KEY",
    "acquire_migration_lock",
    "alembic_config",
    "build_parser",
    "main",
    "release_migration_lock",
    "seed_if_missing",
    "upgrade_head",
]


if __name__ == "__main__":
    sys.exit(main())
