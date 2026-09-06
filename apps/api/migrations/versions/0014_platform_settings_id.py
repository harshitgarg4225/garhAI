"""``platform_settings.id`` gets the ``gen_random_uuid()`` default every other table has.

``0013_platform_fee`` created the table without it, so an upsert that lets the database
mint the id (the same shape ``flags`` uses) failed on a schema built by Alembic while
passing on one built by ``metadata.create_all`` — CI's first run of the fee caught it.

Revision ID: 0014_platform_settings_id (alembic_version is varchar(32) — keep ids short)
Revises: 0013_platform_fee
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_platform_settings_id"
down_revision = "0013_platform_fee"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "platform_settings",
        "id",
        server_default=sa.text("gen_random_uuid()"),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column("platform_settings", "id", server_default=None, existing_nullable=False)
