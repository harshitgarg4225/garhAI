"""``0003_billing`` is EXECUTED here, and its result diffed against the models.

Why this exists at all
----------------------
``garh_api/billing/models.py`` and ``migrations/versions/0003_billing.py`` describe the
same five tables twice. The test suite builds its schema with ``metadata.create_all``;
production builds it with Alembic. So a column added to the models and forgotten in the
migration passes every other test in this repository and fails on deploy — the classic
shape of a check that cannot go red.

This test closes that by running the real ``upgrade()`` into a throwaway schema inside a
transaction that is always rolled back, then comparing what Postgres actually created
against ``BILLING_METADATA``: table names, column names, nullability, types, server
defaults, and every check/unique/foreign-key/primary-key/index name.

Two details make it faithful rather than approximate:

* the ``MigrationContext`` is configured with ``target_metadata`` carrying the same
  naming convention ``migrations/env.py`` passes — Alembic applies that convention to
  constraint names inside ``op.create_table`` (``alembic.operations.schemaobj``), so a
  test that omitted it would compare against names production never produces;
* the whole thing runs in ONE transaction that ends in ``ROLLBACK``. Postgres DDL is
  transactional, so the scratch schema and every object in it disappear even if an
  assertion fails.
"""

from __future__ import annotations

import importlib.util
import os
from types import ModuleType
from typing import Any

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from garh_api.billing.models import BILLING_METADATA, BILLING_TABLES
from garh_api.models import Base
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql

pytestmark = pytest.mark.integration

API_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION_PATH = os.path.join(API_ROOT, "migrations", "versions", "0003_billing.py")

#: Scratch schema the migration is executed into. Dropped by the ROLLBACK, not by a
#: DROP — so a failing assertion cannot leave it behind.
SCRATCH_SCHEMA = "billing_migration_check"


def _load_migration() -> ModuleType:
    """Import the migration file by path (``migrations/versions`` is not a package)."""
    spec = importlib.util.spec_from_file_location("_billing_migration_0003", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normalise_default(value: Any) -> str | None:
    """``"'INR'::text"`` → ``"'inr'"`` — comparable across reflection and metadata.

    Postgres reflects a default with its cast and its own spacing; the metadata holds
    the SQL text we wrote. Normalising both sides keeps the comparison about *whether
    the default is the same value*, which is what drifts, rather than about punctuation.
    """
    if value is None:
        return None
    text_value = str(getattr(value, "arg", value))
    text_value = text_value.strip().lower()
    for cast in ("::text", "::jsonb", "::character varying", "::integer", "::uuid"):
        text_value = text_value.replace(cast, "")
    while text_value.startswith("(") and text_value.endswith(")"):
        text_value = text_value[1:-1].strip()
    return text_value.replace(" ", "")


def _pg_type(type_: Any) -> str:
    """The type as Postgres spells it, for both a reflected and a declared column."""
    return str(type_.compile(dialect=postgresql.dialect())).upper()


def _constraint_names(inspector: Any, table: str, kind: str) -> set[str]:
    if kind == "check":
        return {
            item["name"] for item in inspector.get_check_constraints(table, schema=SCRATCH_SCHEMA)
        }
    if kind == "unique":
        return {
            item["name"] for item in inspector.get_unique_constraints(table, schema=SCRATCH_SCHEMA)
        }
    if kind == "foreign":
        return {item["name"] for item in inspector.get_foreign_keys(table, schema=SCRATCH_SCHEMA)}
    if kind == "index":
        return {item["name"] for item in inspector.get_indexes(table, schema=SCRATCH_SCHEMA)}
    raise AssertionError(kind)


def _metadata_constraint_names(table: Any, kind: str) -> set[str]:
    from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

    wanted = {
        "check": CheckConstraint,
        "unique": UniqueConstraint,
        "foreign": ForeignKeyConstraint,
    }[kind]
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, wanted) and constraint.name
    }


def _ensure_trigger_function(connection: Any) -> None:
    """Provide ``garh_set_updated_at()`` if this database has never run 0001.

    In a real chain 0001 installs it and 0003 only attaches triggers. A database the
    conftest built with ``metadata.create_all`` has the tables but not the function, so
    the precondition is stated here rather than making the migration defensive — it
    lands in the scratch schema and disappears with the ROLLBACK.
    """
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


def test_the_migration_creates_exactly_what_the_models_declare(database: Any) -> None:
    """Run ``0003_billing.upgrade()`` for real, then diff Postgres against the models."""
    migration = _load_migration()
    connection = database.connect()
    transaction = connection.begin()
    try:
        connection.execute(text('CREATE SCHEMA "%s"' % SCRATCH_SCHEMA))
        # public stays on the path so gen_random_uuid() (pgcrypto) and the
        # garh_set_updated_at() trigger function 0001 installs both resolve.
        connection.execute(text('SET LOCAL search_path TO "%s", public' % SCRATCH_SCHEMA))
        _ensure_trigger_function(connection)
        # ``target_metadata`` carries the naming convention, exactly as migrations/env.py
        # configures it. Without it Alembic would name constraints differently here than
        # it does in production, and this test would compare against a fiction.
        context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
        with Operations.context(context):
            migration.upgrade()

        inspector = inspect(connection)
        created = set(inspector.get_table_names(schema=SCRATCH_SCHEMA))
        assert created == set(BILLING_TABLES), (
            "the migration created %s; BILLING_TABLES says %s"
            % (sorted(created), sorted(BILLING_TABLES))
        )

        problems: list[str] = []
        for name in BILLING_TABLES:
            model_table = BILLING_METADATA.tables[name]
            columns = {
                column["name"]: column
                for column in inspector.get_columns(name, schema=SCRATCH_SCHEMA)
            }

            missing = set(model_table.columns.keys()) - set(columns)
            extra = set(columns) - set(model_table.columns.keys())
            if missing:
                problems.append("%s: migration is missing column(s) %s" % (name, sorted(missing)))
            if extra:
                problems.append("%s: migration has extra column(s) %s" % (name, sorted(extra)))

            for column_name, model_column in model_table.columns.items():
                created_column = columns.get(column_name)
                if created_column is None:
                    continue
                if bool(created_column["nullable"]) != bool(model_column.nullable):
                    problems.append(
                        "%s.%s: nullable %s in the migration, %s in the models"
                        % (name, column_name, created_column["nullable"], model_column.nullable)
                    )
                # Compiled against the postgresql dialect on BOTH sides: ``str()`` on a
                # generic ``DateTime(timezone=True)`` is "DATETIME" and on the reflected
                # type is "TIMESTAMP", so comparing the raw strings would report drift
                # on every timestamp column and hide any real one in the noise.
                created_type = _pg_type(created_column["type"])
                model_type = _pg_type(model_column.type)
                if created_type != model_type:
                    problems.append(
                        "%s.%s: type %s in the migration, %s in the models"
                        % (name, column_name, created_type, model_type)
                    )
                created_default = _normalise_default(created_column.get("default"))
                model_default = _normalise_default(model_column.server_default)
                if created_default != model_default:
                    problems.append(
                        "%s.%s: server default %r in the migration, %r in the models"
                        % (name, column_name, created_default, model_default)
                    )

            for kind in ("check", "unique", "foreign"):
                created_names = _constraint_names(inspector, name, kind)
                model_names = _metadata_constraint_names(model_table, kind)
                if created_names != model_names:
                    problems.append(
                        "%s: %s constraint names differ — migration %s, models %s"
                        % (name, kind, sorted(created_names), sorted(model_names))
                    )

            # Postgres implements a UNIQUE constraint with an index of the same name,
            # and reflection reports both. Those are already compared as constraints.
            created_indexes = _constraint_names(inspector, name, "index") - _constraint_names(
                inspector, name, "unique"
            )
            model_indexes = {index.name for index in model_table.indexes}
            if created_indexes != model_indexes:
                problems.append(
                    "%s: index names differ — migration %s, models %s"
                    % (name, sorted(created_indexes), sorted(model_indexes))
                )

            pk = inspector.get_pk_constraint(name, schema=SCRATCH_SCHEMA)
            assert pk["constrained_columns"] == ["id"], name
            assert pk["name"] == "pk_%s" % name, name

        assert not problems, "0003_billing and billing/models.py disagree:\n  " + "\n  ".join(
            problems
        )
    finally:
        transaction.rollback()
        connection.close()


def test_the_migration_installs_the_updated_at_trigger(database: Any) -> None:
    """Every table in the schema carries the ``garh_set_updated_at`` BEFORE UPDATE trigger.

    Not decoration: the ORM maintains ``updated_at`` on its own writes, but the seed
    script, psql and any future worker writing raw SQL do not — which is why 0001 puts
    a trigger on every table and why these five must have one too.
    """
    migration = _load_migration()
    connection = database.connect()
    transaction = connection.begin()
    try:
        connection.execute(text('CREATE SCHEMA "%s"' % SCRATCH_SCHEMA))
        connection.execute(text('SET LOCAL search_path TO "%s", public' % SCRATCH_SCHEMA))
        _ensure_trigger_function(connection)
        context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
        with Operations.context(context):
            migration.upgrade()

        rows = connection.execute(
            text(
                "SELECT c.relname FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :schema AND NOT t.tgisinternal"
            ),
            {"schema": SCRATCH_SCHEMA},
        )
        triggered = {row[0] for row in rows}
        assert triggered == set(BILLING_TABLES), sorted(triggered)
    finally:
        transaction.rollback()
        connection.close()
