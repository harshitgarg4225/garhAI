"""Allow the setting-out working drawing as a sheet kind.

``sheets.kind`` is guarded by a CHECK constraint rendered from
``garh_api.models.SHEET_KINDS``. D-2 adds the setting-out plan — the GFC drawing a
site engineer works from — so the constraint has to learn the value or every insert
of one is rejected by the database while passing every test that never touched
Postgres.

Data-safe in both directions: the DOWN path removes a value rather than a column,
so it first refuses if any row is actually using it. Dropping a constraint that
existing rows violate is how a downgrade corrupts a table quietly.

Revision ID: 0005_setting_out_sheet_kind
Revises: 0004_user_two_factor
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005_setting_out_sheet_kind"
down_revision: str | None = "0004_user_two_factor"
branch_labels: None = None
depends_on: None = None

#: The six §7 submission kinds, in the order they were originally written.
_SUBMISSION = ("site", "floor", "elevation", "section", "schedule", "area-statement")
_WORKING = ("setting-out",)


def _check(values: tuple[str, ...]) -> str:
    return "kind IN (%s)" % ", ".join("'%s'" % v for v in values)


def upgrade() -> None:
    op.drop_constraint("ck_sheets_kind", "sheets", type_="check")
    op.create_check_constraint("ck_sheets_kind", "sheets", _check(_SUBMISSION + _WORKING))


def downgrade() -> None:
    # Refuse rather than corrupt: a row whose kind is about to become illegal must be
    # dealt with by a person, not silently left violating a constraint nobody checks.
    bind = op.get_bind()
    stranded = bind.execute(
        sa.text("SELECT count(*) FROM sheets WHERE kind = ANY(:kinds)"),
        {"kinds": list(_WORKING)},
    ).scalar_one()
    if stranded:
        raise RuntimeError(
            "%d sheet row(s) still use a working-drawing kind %s. Delete or re-kind them "
            "before downgrading; this migration will not strand rows outside their own "
            "CHECK constraint." % (stranded, list(_WORKING))
        )
    op.drop_constraint("ck_sheets_kind", "sheets", type_="check")
    op.create_check_constraint("ck_sheets_kind", "sheets", _check(_SUBMISSION))
