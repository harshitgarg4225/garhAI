"""Keep the sentence the solver wrote when it had nothing to show.

Revision ID: 0010_solver_job_banner
Revises: 0009_render_references_used
Create Date: 2026-09-01

The solver already writes a banner for the empty case — ``shortfall_banner`` turns a
stage-A diagnosis into "the ground floor is 8 m² short" rather than "0 options" — and
the worker returns it as its ``JobResult.message``. ``render_jobs`` had the same shape
of hole: the API's lifecycle consumer read ``options`` out of the event and dropped
everything else, so a solve that produced nothing reached the architect as
``succeeded``, ``progress: 100``, zero options and NO text at all.

That is a blank screen after a two-minute wait, on a product whose first interaction is
"Generate". The reason existed the whole time, one hop away.

Its own column rather than reusing ``error``: this job did not fail, and a row that is
both ``succeeded`` and carrying an ``error`` is how a UI ends up showing a red banner
for a normal outcome.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_solver_job_banner"
down_revision = "0009_render_references_used"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("solver_jobs", sa.Column("banner", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("solver_jobs", "banner")
