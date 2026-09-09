"""``compliance_reports.summary`` — freeze the area statement with the rows.

A frozen report stored only the rule rows and the pack versions. The area statement
(FAR consumed vs allowed, coverage, per-storey built-up, setbacks), the scores, the
engine's warnings, the disclaimers and the projection notes were computed in the same
evaluation and then dropped, so ``GET /compliance`` could state them for a LIVE run and
not for the frozen report a version quotes — the one the sheet prints. Storing them
beside the rows keeps "one source of compliance numbers" literal (§7): a stored report
can be read back years later without re-running an engine whose packs have moved on.

Nullable: rows frozen before this revision have no summary, and the API says so rather
than re-evaluating them under newer packs.

Revision ID: 0016_compliance_summary (alembic_version is varchar(32) — keep ids short)
Revises: 0015_team_invites
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0016_compliance_summary"
down_revision = "0015_team_invites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "compliance_reports",
        sa.Column("summary", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("compliance_reports", "summary")
