"""user_two_factor — TOTP second factor for a firm seat (F-4)

One row per user: the base32 shared secret an authenticator app scanned, when the
enrolment was proved (``confirmed_at``), the highest TOTP step already spent
(``last_counter``, the replay guard) and the ``sha256`` digests of the recovery codes
that are still unused.

Postgres and not Redis, deliberately — ``garh_api.auth`` keeps refresh families in
Redis and documents that a flush loses them. Losing a *second factor* record either
silently downgrades every account to one factor or locks everyone out, so it needs a
durable home. See ``garh_api/models.py::UserTwoFactor`` for the field-by-field
reasoning and ``garh_api/twofactor.py`` for the algorithm.

``ON DELETE CASCADE`` on ``user_id`` is the DPDP erasure path (F-6): removing the
``users`` row takes the credential with it, with no sweep to remember.

Hand-written like 0001 and 0002, and for the same reason: the constraint names must be
exactly what ``models.py`` declares, so ``alembic revision --autogenerate`` at this
revision produces an empty diff.

Revision ID: 0004_user_two_factor
Revises: 0003_billing
Create Date: 2026-08-28

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_user_two_factor"
#: Chained after ``0003_billing``, which landed in the same batch of work. Alembic
#: refuses to resolve ``head`` while two revisions share a parent, so the ordering
#: between these two is arbitrary but must exist — nothing in either migration
#: depends on the other.
down_revision: str | None = "0003_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)

TABLE = "user_two_factor"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("firm_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_counter", sa.BigInteger(), server_default=sa.text("-1"), nullable=False),
        sa.Column(
            "recovery_hashes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_user_two_factor"),
        sa.ForeignKeyConstraint(
            ["firm_id"],
            ["firms.id"],
            name="fk_user_two_factor_firm_id_firms",
            ondelete="CASCADE",
        ),
        # Erasing the seat erases the credential — see the module docstring.
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_two_factor_user_id_users",
            ondelete="CASCADE",
        ),
        # At most one enrolment per user: re-enrolling replaces the row rather than
        # leaving a second secret that would also verify.
        sa.UniqueConstraint("user_id", name="uq_user_two_factor_user_id"),
        sa.CheckConstraint("length(btrim(secret)) > 0", name="ck_user_two_factor_secret_not_blank"),
        sa.CheckConstraint("last_counter >= -1", name="ck_user_two_factor_last_counter_range"),
        sa.CheckConstraint(
            "jsonb_typeof(recovery_hashes) = 'array'",
            name="ck_user_two_factor_recovery_hashes_array",
        ),
    )
    op.create_index("ix_user_two_factor_firm_id", TABLE, ["firm_id"])

    # The same BEFORE UPDATE trigger every 0001 table carries, so raw-SQL writers
    # (seed scripts, psql) maintain updated_at too. The function already exists.
    op.execute(
        "CREATE TRIGGER trg_%s_updated_at BEFORE UPDATE ON %s "
        "FOR EACH ROW EXECUTE FUNCTION garh_set_updated_at()" % (TABLE, TABLE)
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_%s_updated_at ON %s" % (TABLE, TABLE))
    op.drop_table(TABLE)
