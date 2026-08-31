"""The per-project inspiration board: reference images and what to do with them.

An architect collects pictures — a kitchen the client loves, a facade from a magazine.
Before this the product stored one picture's FILENAME and never read it, so nothing
could say which part of the house a picture was for, what to take from it, or what to
leave.

Many rows per project, unlike ``project_underlays`` which is one: a board with one
picture on it is not a board.

Revision ID: 0008_project_references
Revises: 0007_project_submission
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision = "0008_project_references"
down_revision = "0007_project_submission"
branch_labels = None
depends_on = None

SCOPES = (
    "whole-house",
    "facade",
    "interior",
    "kitchen",
    "living",
    "bedroom",
    "bathroom",
    "landscape",
    "material",
)
INTENTS = ("match", "guide", "avoid")


def _in_check(column: str, values: tuple[str, ...]) -> str:
    return "%s IN (%s)" % (column, ", ".join("'%s'" % v for v in values))


def upgrade() -> None:
    op.create_table(
        "project_references",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("firm_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("project_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("width_px", sa.Integer(), nullable=False),
        sa.Column("height_px", sa.Integer(), nullable=False),
        # -- the architect's four answers ------------------------------------
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("why", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("ignore_note", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("intent", sa.Text(), nullable=False, server_default=sa.text("'guide'")),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
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
        sa.ForeignKeyConstraint(
            ["firm_id"],
            ["firms.id"],
            ondelete="CASCADE",
            name="fk_project_references_firm_id_firms",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
            name="fk_project_references_project_id_projects",
        ),
        sa.CheckConstraint(_in_check("scope", SCOPES), name="ck_project_references_scope"),
        sa.CheckConstraint(_in_check("intent", INTENTS), name="ck_project_references_intent"),
        sa.CheckConstraint("width_px > 0", name="ck_project_references_width_px_positive"),
        sa.CheckConstraint("height_px > 0", name="ck_project_references_height_px_positive"),
        sa.CheckConstraint(
            "length(btrim(label)) > 0", name="ck_project_references_label_not_blank"
        ),
        sa.CheckConstraint(
            "length(btrim(object_key)) > 0", name="ck_project_references_object_key_not_blank"
        ),
    )
    op.create_index("ix_project_references_firm_id", "project_references", ["firm_id"])
    op.create_index(
        "ix_project_references_project_id_position",
        "project_references",
        ["project_id", "position"],
    )


def downgrade() -> None:
    """Refuses rather than deleting what an architect collected and annotated.

    Same shape as 0005 through 0007: the images live in object storage and the
    annotations live only here, so dropping the table silently loses the half nobody
    can reconstruct.
    """
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM project_references")).scalar()
    if count:
        raise RuntimeError(
            "%d reference image(s) are pinned to project boards, with the notes saying "
            "what to take from each. Dropping this table deletes those notes. Clear them "
            "deliberately first: DELETE FROM project_references;" % count
        )
    op.drop_table("project_references")
