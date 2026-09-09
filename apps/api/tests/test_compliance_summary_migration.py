"""``0016_compliance_summary`` is EXECUTED here against the real test database.

The suite builds its schema with ``metadata.create_all``, so a column added to the
model and mistyped in the migration would pass every other test and fail on deploy —
the shape of a check that cannot go red (see ``test_billing_migration.py``, whose
approach this copies). This runs the migration's ``upgrade()`` and ``downgrade()``
for real into a scratch schema inside one transaction that always rolls back.

The migration's ``down_revision`` names ``0015_team_invites``, which lives on another
branch; nothing here walks the Alembic chain, so the file is exercised on its own
body exactly as production will apply it once both land.
"""

from __future__ import annotations

import importlib.util
import os
from types import ModuleType
from typing import Any

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from garh_api.models import Base
from sqlalchemy import inspect, text

pytestmark = pytest.mark.integration

API_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION_PATH = os.path.join(API_ROOT, "migrations", "versions", "0016_compliance_summary.py")
SCRATCH_SCHEMA = "compliance_summary_migration_check"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_compliance_summary_0016", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _columns(connection: Any) -> dict[str, dict[str, Any]]:
    inspector = inspect(connection)
    return {
        column["name"]: column
        for column in inspector.get_columns("compliance_reports", schema=SCRATCH_SCHEMA)
    }


def test_the_revision_chain_is_what_the_coordinator_assigned() -> None:
    migration = _load_migration()
    assert migration.revision == "0016_compliance_summary"
    assert migration.down_revision == "0015_team_invites"
    assert len(migration.revision) <= 32, "alembic_version is varchar(32)"


def test_upgrade_adds_the_nullable_jsonb_column_and_downgrade_removes_it(database: Any) -> None:
    migration = _load_migration()
    connection = database.connect()
    transaction = connection.begin()
    try:
        connection.execute(text('CREATE SCHEMA "%s"' % SCRATCH_SCHEMA))
        connection.execute(text('SET LOCAL search_path TO "%s", public' % SCRATCH_SCHEMA))
        # The table as it stood BEFORE this revision: every model column except the
        # one the migration adds. Built from the model so the pre-state cannot drift.
        before = Base.metadata.tables["compliance_reports"]
        columns_before = [c.name for c in before.columns if c.name != "summary"]
        connection.execute(
            text(
                "CREATE TABLE compliance_reports (%s)"
                % ", ".join(
                    "%s %s"
                    % (
                        name,
                        "uuid"
                        if name.endswith("id")
                        else "jsonb"
                        if name in ("pack_versions", "results")
                        else "timestamptz",
                    )
                    for name in columns_before
                )
            )
        )
        assert "summary" not in _columns(connection)

        context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
        with Operations.context(context):
            migration.upgrade()
        after = _columns(connection)
        assert "summary" in after, "upgrade() did not add compliance_reports.summary"
        assert after["summary"]["nullable"] is True, "rows frozen earlier have no summary"
        assert str(after["summary"]["type"]).upper() == "JSONB"
        # Every column the model declares now exists — the migration and the model agree.
        model_columns = {c.name for c in Base.metadata.tables["compliance_reports"].columns}
        assert model_columns <= set(after)

        with Operations.context(context):
            migration.downgrade()
        assert "summary" not in _columns(connection), "downgrade() must remove the column"
    finally:
        transaction.rollback()
        connection.close()
