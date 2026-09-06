"""The platform fee: an owner-set markup on every metered charge.

``platform_settings`` is a tiny non-tenant key/value table for the handful of numbers
the OWNER of the deployment changes at runtime without a redeploy. Its first key is
``billing.markup_bps`` — the percentage, in basis points, added on top of provider
cost before a charge is counted against an architect's budget.

``credit_events`` gains what the architect was CHARGED beside what the work COST. The
two are kept apart on purpose: ``cost_micros`` is the honest provider ledger and never
moves; ``charged_micros`` is cost plus the markup in force when the row was written,
and ``markup_bps`` records that markup so a later change never rewrites history.
Existing rows were charged at cost (no markup existed), and the backfill says so.

Revision ID: 0013_platform_fee
Revises: 0012_credit_event_refund
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision = "0013_platform_fee"
down_revision = "0012_credit_event_refund"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_settings",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_by", PgUUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("key", name="uq_platform_settings_key"),
        sa.CheckConstraint("key = lower(key)", name="ck_platform_settings_key_lowercase"),
        sa.CheckConstraint("length(btrim(key)) > 0", name="ck_platform_settings_key_not_blank"),
    )
    op.add_column(
        "credit_events",
        sa.Column("markup_bps", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "credit_events",
        sa.Column("charged_micros", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    # Every row written before the fee existed was charged exactly what it cost.
    op.execute(sa.text("UPDATE credit_events SET charged_micros = cost_micros"))
    op.create_check_constraint(
        "ck_credit_events_markup_non_negative", "credit_events", "markup_bps >= 0"
    )
    op.create_check_constraint(
        "ck_credit_events_charged_covers_cost", "credit_events", "charged_micros >= cost_micros"
    )


def downgrade() -> None:
    op.drop_constraint("ck_credit_events_charged_covers_cost", "credit_events", type_="check")
    op.drop_constraint("ck_credit_events_markup_non_negative", "credit_events", type_="check")
    op.drop_column("credit_events", "charged_micros")
    op.drop_column("credit_events", "markup_bps")
    op.drop_table("platform_settings")
