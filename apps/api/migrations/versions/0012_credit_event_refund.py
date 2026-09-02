"""Give a credit back when the job it paid for never delivered.

Revision ID: 0012_credit_event_refund
Revises: 0011_credit_event_cost
Create Date: 2026-09-02

``credit_events`` are written when a job is ENQUEUED — the moment the quota and the
spend cap must be checked — and were never touched again. A job that then failed,
was dead-lettered or was cancelled still counted: the first trial architect lost two
of ten free generations to a worker image that could not open its own catalogue.

``job_id`` lifts the ``meta->>'jobId'`` the charge sites already write into a real,
indexed column so the lifecycle consumer can find the row to refund without a JSON
scan. ``refunded_at`` is the refund: the row stays (it is the audit trail of what was
attempted) but every reader — the count quota, the money cap, the usage page —
excludes it. Existing rows are backfilled from their meta.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision = "0012_credit_event_refund"
down_revision = "0011_credit_event_cost"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("credit_events", sa.Column("job_id", PgUUID(as_uuid=True), nullable=True))
    op.add_column(
        "credit_events", sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Backfill from the meta the charge sites have always written. The regex guards
    # the cast: a malformed value must not fail the migration for every firm.
    op.execute(
        sa.text(
            "UPDATE credit_events SET job_id = (meta->>'jobId')::uuid "
            "WHERE job_id IS NULL AND meta ? 'jobId' "
            "AND meta->>'jobId' ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
            "-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'"
        )
    )
    # The refund's lookup is exactly (firm, job); the readers' filter is refunded_at.
    op.create_index("ix_credit_events_firm_id_job_id", "credit_events", ["firm_id", "job_id"])
    # One-time refund of every charge whose job had already failed or been cancelled
    # before refunds existed. Export jobs live in Redis, not here, so they are not
    # covered — nothing was ever charged for an export on the deployed stack.
    for table in ("solver_jobs", "render_jobs"):
        op.execute(
            sa.text(
                "UPDATE credit_events ce SET refunded_at = now(), "
                'meta = ce.meta || \'{"refund": "backfill_undelivered"}\'::jsonb '
                f"FROM {table} j WHERE ce.job_id = j.id AND ce.firm_id = j.firm_id "
                "AND j.status IN ('failed', 'cancelled') AND ce.refunded_at IS NULL"
            )
        )


def downgrade() -> None:
    op.drop_index("ix_credit_events_firm_id_job_id", table_name="credit_events")
    op.drop_column("credit_events", "refunded_at")
    op.drop_column("credit_events", "job_id")
