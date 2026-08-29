"""Per-project submission details: which authority, and its statutory identifiers.

D-4. A khata number is a fact about a *plot*, not about a firm and not about a drawing,
so it cannot live in the firm's title-block template — two projects in the same practice
have different khata numbers, and one of them would end up on the other's sanction set.

Nullable with no default, because most projects are not being submitted to anything.

Revision ID: 0007_project_submission
Revises: 0006_structural_grid_sheet_kind
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007_project_submission"
down_revision = "0006_structural_grid_sheet_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("submission", JSONB(), nullable=True))


def downgrade() -> None:
    """Refuses rather than silently discarding what an architect typed.

    Same shape as 0005 and 0006: a downgrade that drops statutory identifiers takes work
    with it that nobody can reconstruct, and a migration is the wrong place to make that
    call on someone's behalf.
    """
    count = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM projects WHERE submission IS NOT NULL"))
        .scalar()
    )
    if count:
        raise RuntimeError(
            "%d project(s) carry submission details (authority and statutory "
            "identifiers). Dropping this column deletes them. Clear them deliberately "
            "first: UPDATE projects SET submission = NULL;" % count
        )
    op.drop_column("projects", "submission")
