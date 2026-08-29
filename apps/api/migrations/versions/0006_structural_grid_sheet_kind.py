"""Allow the structural-grid working drawing as a sheet kind.

D-7, and the same shape as 0005: ``sheets.kind`` is guarded by a CHECK constraint
rendered from ``garh_api.models.SHEET_KINDS``, so a new kind has to be taught to the
database or every insert is rejected while the tests that never touch Postgres stay
green.

Revision ID: 0006_structural_grid_sheet_kind
Revises: 0005_setting_out_sheet_kind
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006_structural_grid_sheet_kind"
down_revision: str | None = "0005_setting_out_sheet_kind"
branch_labels: None = None
depends_on: None = None

_SUBMISSION = ("site", "floor", "elevation", "section", "schedule", "area-statement")
_BEFORE = (*_SUBMISSION, "setting-out")
_AFTER = (*_BEFORE, "structural-grid")


def _check(values: tuple[str, ...]) -> str:
    return "kind IN (%s)" % ", ".join("'%s'" % v for v in values)


def upgrade() -> None:
    op.drop_constraint("ck_sheets_kind", "sheets", type_="check")
    op.create_check_constraint("ck_sheets_kind", "sheets", _check(_AFTER))


def downgrade() -> None:
    # Refuse rather than strand rows outside their own constraint. Same reasoning as
    # 0005: a downgrade that leaves illegal rows behind is a corruption nobody notices
    # until the next constraint validation.
    stranded = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM sheets WHERE kind = 'structural-grid'"))
        .scalar_one()
    )
    if stranded:
        raise RuntimeError(
            "%d sheet row(s) still use kind 'structural-grid'. Delete or re-kind them "
            "before downgrading." % stranded
        )
    op.drop_constraint("ck_sheets_kind", "sheets", type_="check")
    op.create_check_constraint("ck_sheets_kind", "sheets", _check(_BEFORE))
