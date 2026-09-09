"""firm_invites + users.last_sign_in_at — the team half of "set up the practice" (J01)

Until this revision a firm could only ever hold the one admin its signup created:
``AuthDirectoryRepository.create_firm_with_owner`` was the sole constructor of a
``users`` row and nothing mounted ``UserRepository.create``. Presence, live cursors and
colleague comments were all built and all unreachable by any two humans.

``firm_invites`` is an admin's standing offer of a seat to an address. Acceptance is the
normal OTP sign-in — proving control of the mailbox is the credential — so the table
stores only ``sha256(token)`` for the honest pre-auth status page, the same discipline
as ``share_links``. See ``garh_api/models.py::FirmInvite`` for the pending/expired/
revoked semantics and why an address already in another firm still looks "pending".

``users.last_sign_in_at`` is what the Team page prints as "last signed in"; the auth
service stamps it on every completed sign-in. Nullable, because a member who has
never turned up is exactly the case an admin needs to see.

Hand-written like every revision before it so the constraint names match ``models.py``
exactly, and checked by ``tests/test_team_migration.py`` running this file's
``upgrade()`` into a scratch schema and diffing the result against the models.

Revision ID: 0015_team_invites  (alembic_version is varchar(32) — keep ids short)
Revises: 0014_platform_settings_id
Create Date: 2026-09-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_team_invites"
down_revision: str | None = "0014_platform_settings_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)

TABLE = "firm_invites"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("last_sign_in_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        TABLE,
        sa.Column("id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("firm_id", UUID, nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), server_default=sa.text("'member'"), nullable=False),
        sa.Column("seat_type", sa.Text(), server_default=sa.text("'editor'"), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("invited_by", UUID, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_sent_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("send_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_user_id", UUID, nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_firm_invites"),
        sa.ForeignKeyConstraint(
            ["firm_id"],
            ["firms.id"],
            name="fk_firm_invites_firm_id_firms",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"],
            ["users.id"],
            name="fk_firm_invites_invited_by_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_user_id"],
            ["users.id"],
            name="fk_firm_invites_accepted_user_id_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("token_hash", name="uq_firm_invites_token_hash"),
        sa.CheckConstraint("role IN ('admin', 'member')", name="ck_firm_invites_role"),
        sa.CheckConstraint("seat_type IN ('editor', 'viewer')", name="ck_firm_invites_seat_type"),
        sa.CheckConstraint("email = lower(email)", name="ck_firm_invites_email_lowercase"),
        sa.CheckConstraint("position('@' in email) > 1", name="ck_firm_invites_email_shape"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_firm_invites_name_not_blank"),
        sa.CheckConstraint("send_count >= 1", name="ck_firm_invites_send_count_min"),
    )
    op.create_index("ix_firm_invites_firm_id", TABLE, ["firm_id"])
    op.create_index("ix_firm_invites_firm_id_created_at", TABLE, ["firm_id", "created_at"])
    op.create_index("ix_firm_invites_email", TABLE, ["email"])
    op.create_index(
        "uq_firm_invites_pending",
        TABLE,
        ["firm_id", "email"],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )
    # Same BEFORE UPDATE trigger 0001 gave every table, so a raw-SQL writer maintains
    # updated_at too. The function already exists.
    op.execute(
        "CREATE TRIGGER trg_%s_updated_at BEFORE UPDATE ON %s "
        "FOR EACH ROW EXECUTE FUNCTION garh_set_updated_at()" % (TABLE, TABLE)
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_%s_updated_at ON %s" % (TABLE, TABLE))
    op.drop_index("uq_firm_invites_pending", table_name=TABLE)
    op.drop_index("ix_firm_invites_email", table_name=TABLE)
    op.drop_index("ix_firm_invites_firm_id_created_at", table_name=TABLE)
    op.drop_index("ix_firm_invites_firm_id", table_name=TABLE)
    op.drop_table(TABLE)
    op.drop_column("users", "last_sign_in_at")
