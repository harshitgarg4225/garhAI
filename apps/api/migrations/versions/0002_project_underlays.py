"""project_underlays — the tracing-underlay sidecar (Rayon's "import a plan and trace")

One row per project (unique index on ``project_id``): the storage key of an uploaded
plan image plus its calibration (mm-per-pixel, model-space origin) and view state
(opacity / locked / visible). A sidecar table rather than model ops on purpose — an
underlay is a tracing AID, not design state, so it must not require byte-identical
TS/Python model-core changes or pollute undo with opacity tweaks. See
``garh_api/models.py::ProjectUnderlay`` for the field-by-field rationale, including
why ``mm_per_px`` is the one sanctioned float.

Hand-written like 0001, and for the same reason: the constraint names must be exactly
what ``models.py`` declares, so ``alembic revision --autogenerate`` at this revision
produces an empty diff.

Revision ID: 0002_project_underlays
Revises: 0001_initial
Create Date: 2026-08-27

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_project_underlays"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)

TABLE = "project_underlays"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("firm_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("width_px", sa.Integer(), nullable=False),
        sa.Column("height_px", sa.Integer(), nullable=False),
        sa.Column("mm_per_px", sa.Float(), server_default=sa.text("1.0"), nullable=False),
        sa.Column("origin_x_mm", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("origin_y_mm", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("opacity", sa.Float(), server_default=sa.text("0.5"), nullable=False),
        sa.Column("locked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("visible", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_project_underlays"),
        sa.ForeignKeyConstraint(
            ["firm_id"],
            ["firms.id"],
            name="fk_project_underlays_firm_id_firms",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_project_underlays_project_id_projects",
            ondelete="CASCADE",
        ),
        # One underlay per project — replacing uploads overwrite the row.
        sa.UniqueConstraint("project_id", name="uq_project_underlays_project_id"),
        sa.CheckConstraint("width_px > 0", name="ck_project_underlays_width_px_positive"),
        sa.CheckConstraint("height_px > 0", name="ck_project_underlays_height_px_positive"),
        sa.CheckConstraint("mm_per_px > 0", name="ck_project_underlays_mm_per_px_positive"),
        sa.CheckConstraint(
            "opacity >= 0 AND opacity <= 1", name="ck_project_underlays_opacity_range"
        ),
        sa.CheckConstraint(
            "length(btrim(object_key)) > 0", name="ck_project_underlays_object_key_not_blank"
        ),
    )
    op.create_index("ix_project_underlays_firm_id", TABLE, ["firm_id"])

    # The same BEFORE UPDATE trigger every 0001 table carries, so raw-SQL writers
    # (seed scripts, psql) maintain updated_at too. The function already exists.
    op.execute(
        "CREATE TRIGGER trg_%s_updated_at BEFORE UPDATE ON %s "
        "FOR EACH ROW EXECUTE FUNCTION garh_set_updated_at()" % (TABLE, TABLE)
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_%s_updated_at ON %s" % (TABLE, TABLE))
    op.drop_table(TABLE)
