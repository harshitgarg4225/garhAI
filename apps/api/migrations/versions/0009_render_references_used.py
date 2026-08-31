"""Record which board references a finished render actually followed.

Revision ID: 0009_render_references_used
Revises: 0008_project_references
Create Date: 2026-08-31

The board reaches the worker inside the job payload, and the prompt builder records
what it consumed — but ``render_jobs`` kept only ``output_url`` from a worker's result,
so the credit list was computed, logged, and dropped one layer before the architect.
The board looked wired end to end and "did this render use my reference?" still had no
answer on the render.

Its own column rather than a key in ``params``: ``params`` is the REQUEST (the recipe
the architect asked for, including the board as it stood at enqueue), and this is what
the prompt actually used. Two different facts — a render can carry a reference it could
not apply — and putting them in one place is how they end up disagreeing.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009_render_references_used"
down_revision = "0008_project_references"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "render_jobs",
        sa.Column(
            "references_used",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    # Safe to drop unconditionally, unlike 0008's table: this column is derived from
    # the render's own payload and holds nothing an architect authored.
    op.drop_column("render_jobs", "references_used")
