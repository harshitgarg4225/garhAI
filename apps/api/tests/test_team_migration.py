"""``0015_team_invites`` is EXECUTED here and diffed against the models.

Same shape and same reason as ``test_billing_migration.py``: the suite builds its
schema with ``metadata.create_all`` while production runs Alembic, so a column that
exists in ``models.py`` and not in the migration is green everywhere except the
deploy. The migration runs for real into a scratch schema — seeded with the two
tables it depends on, ``firms`` and ``users`` in their pre-0015 shape — inside one
transaction that always rolls back.
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
MIGRATION_PATH = os.path.join(API_ROOT, "migrations", "versions", "0015_team_invites.py")
SCRATCH_SCHEMA = "team_migration_check"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_team_migration_0015", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pre_0015_tables(connection: Any) -> None:
    """``firms`` and ``users`` as they stood before this revision, in the scratch schema."""
    for name in ("firms", "users"):
        Base.metadata.tables[name].create(connection)
    connection.execute(text("ALTER TABLE users DROP COLUMN last_sign_in_at"))
    exists = connection.execute(
        text("SELECT to_regprocedure('garh_set_updated_at()') IS NOT NULL")
    ).scalar()
    if not exists:
        connection.execute(
            text(
                "CREATE FUNCTION garh_set_updated_at() RETURNS trigger AS $$ "
                "BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql"
            )
        )


def _names(items: list[dict[str, Any]]) -> set[str]:
    return {str(item["name"]) for item in items}


def test_the_migration_creates_exactly_what_the_models_declare(database: Any) -> None:
    migration = _load_migration()
    connection = database.connect()
    transaction = connection.begin()
    try:
        connection.execute(text('CREATE SCHEMA "%s"' % SCRATCH_SCHEMA))
        connection.execute(text('SET LOCAL search_path TO "%s", public' % SCRATCH_SCHEMA))
        _pre_0015_tables(connection)
        context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
        with Operations.context(context):
            migration.upgrade()

        inspector = inspect(connection)
        model = Base.metadata.tables["firm_invites"]

        # users.last_sign_in_at arrived, nullable, timestamptz.
        users = {c["name"]: c for c in inspector.get_columns("users", schema=SCRATCH_SCHEMA)}
        assert "last_sign_in_at" in users
        assert users["last_sign_in_at"]["nullable"] is True
        assert "TIMESTAMP" in str(users["last_sign_in_at"]["type"]).upper()

        # firm_invites: every column, with the model's nullability and defaults.
        created = {
            c["name"]: c for c in inspector.get_columns("firm_invites", schema=SCRATCH_SCHEMA)
        }
        assert set(created) == set(model.columns.keys()), sorted(set(model.columns) ^ set(created))
        for name, column in model.columns.items():
            assert created[name]["nullable"] == column.nullable, name
            has_default = created[name]["default"] is not None
            assert has_default == (column.server_default is not None), (
                name,
                created[name]["default"],
            )

        # Every named constraint and index the model declares, by the model's name.
        checks = _names(inspector.get_check_constraints("firm_invites", schema=SCRATCH_SCHEMA))
        uniques = _names(inspector.get_unique_constraints("firm_invites", schema=SCRATCH_SCHEMA))
        foreign = _names(inspector.get_foreign_keys("firm_invites", schema=SCRATCH_SCHEMA))
        indexes = {
            i["name"]: i for i in inspector.get_indexes("firm_invites", schema=SCRATCH_SCHEMA)
        }
        from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

        def declared(kind: type) -> set[str]:
            return {c.name for c in model.constraints if isinstance(c, kind) and c.name}

        assert declared(CheckConstraint) <= checks, sorted(declared(CheckConstraint) - checks)
        assert declared(UniqueConstraint) <= uniques, sorted(declared(UniqueConstraint) - uniques)
        assert declared(ForeignKeyConstraint) <= foreign, sorted(
            declared(ForeignKeyConstraint) - foreign
        )
        assert {i.name for i in model.indexes} <= set(indexes), sorted(
            {str(i.name) for i in model.indexes} - set(indexes)
        )
        pending = indexes["uq_firm_invites_pending"]
        assert pending["unique"] is True
        assert "accepted_at IS NULL" in str(
            pending.get("dialect_options", {}).get("postgresql_where", "")
        )

        # FK actions: the firm cascades, people are SET NULL — the audit-friendly shape.
        actions = {
            fk["name"]: fk["options"].get("ondelete")
            for fk in inspector.get_foreign_keys("firm_invites", schema=SCRATCH_SCHEMA)
        }
        assert actions["fk_firm_invites_firm_id_firms"] == "CASCADE"
        assert actions["fk_firm_invites_invited_by_users"] == "SET NULL"
        assert actions["fk_firm_invites_accepted_user_id_users"] == "SET NULL"

        # The updated_at trigger rides along, as on every other table.
        triggered = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :schema AND NOT t.tgisinternal"
                ),
                {"schema": SCRATCH_SCHEMA},
            )
        }
        assert "firm_invites" in triggered

        # And the way back is clean.
        with Operations.context(context):
            migration.downgrade()
        inspector = inspect(connection)
        assert "firm_invites" not in inspector.get_table_names(schema=SCRATCH_SCHEMA)
        assert "last_sign_in_at" not in {
            c["name"] for c in inspector.get_columns("users", schema=SCRATCH_SCHEMA)
        }
    finally:
        transaction.rollback()
        connection.close()


def test_the_migration_is_chained_after_0014() -> None:
    """A second head would make ``alembic upgrade head`` refuse to run at all."""
    migration = _load_migration()
    assert migration.revision == "0015_team_invites"
    assert len(migration.revision) <= 32, "alembic_version is varchar(32)"
    assert migration.down_revision == "0014_platform_settings_id"
