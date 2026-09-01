"""Price every metered event, and attribute it to the architect who spent it.

Revision ID: 0011_credit_event_cost
Revises: 0010_solver_job_banner
Create Date: 2026-09-01

``credit_events`` counted units — one solve, one render, one LLM call — which is what a
COUNT quota needs. A money cap needs what each of those actually cost, and a per-
architect cap needs to know who ran it.

``cost_micros`` is micro-dollars (see ``billing/spend.py``), NOT rupees: it records what
a provider charged us, in the currency they charge in, and it never reaches an invoice.
BIGINT because µUSD is a millionth — a firm that spends $10,000 is 10^10, past int4.

``user_id`` is nullable and has no FK cascade of its own: it is an attribution label,
and losing it must never take the billing row with it. Rows written before this
migration keep NULL, which reads as "this firm, architect unknown".
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision = "0011_credit_event_cost"
down_revision = "0010_solver_job_banner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "credit_events",
        sa.Column("cost_micros", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("credit_events", sa.Column("user_id", PgUUID(as_uuid=True), nullable=True))
    op.create_check_constraint(
        "ck_credit_events_cost_non_negative", "credit_events", "cost_micros >= 0"
    )
    # The cap's read is "everything this architect has ever spent", so it is exactly
    # this index. Without it the check is a full scan of the firm's history on every
    # metered call.
    op.create_index(
        "ix_credit_events_user_id_cost", "credit_events", ["user_id"], postgresql_where=None
    )


def downgrade() -> None:
    op.drop_index("ix_credit_events_user_id_cost", table_name="credit_events")
    op.drop_constraint("ck_credit_events_cost_non_negative", "credit_events", type_="check")
    op.drop_column("credit_events", "user_id")
    op.drop_column("credit_events", "cost_micros")
