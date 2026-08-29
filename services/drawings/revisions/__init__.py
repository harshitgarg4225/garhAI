"""Revision management for a municipal drawing set (D-1).

    Title block: firm logo/fields template; sheet numbering; auto revision table. (F7-A)

A drawing set is not a printout. It is issued, queried, corrected and re-issued, and every
print in circulation has to say which issue it is and what changed in it. Three parts,
three modules:

:mod:`record`     what a revision is — number, date, description, author, and the model
                  state hash it was issued at — plus :class:`RevisionHistory`, the
                  register, which refuses a reused number or a backwards date.
:mod:`diff`       the geometric difference between two model states: which walls,
                  openings, rooms, stairs, columns and balconies changed, and the box on
                  the plan each one occupies. This is what makes clouds *derived* rather
                  than remembered.
:mod:`cloud`      the clouds and delta tags themselves — exact integer arc chains on the
                  existing primitive vocabulary and the existing nine layers.
:mod:`register`   the register table, in the same two renderings the schedule and area
                  statement already use.

The seam with the rest of §7 is deliberately narrow: nothing here imports the sheet
builder, and the sheet builder calls exactly two functions —
:func:`~services.drawings.revisions.cloud.revision_marks` for a plan and
:func:`~services.drawings.revisions.register.revision_register_group` for the first sheet.
"""

from __future__ import annotations

from services.drawings.revisions.cloud import (
    CLOUD_BULGE_PAPER_MM,
    CLOUD_MARGIN_PAPER_MM,
    CLUSTER_GAP_PAPER_MM,
    TAG_SIDE_PAPER_MM,
    cloud_regions,
    revision_cloud,
    revision_marks,
    revision_tag,
)
from services.drawings.revisions.diff import (
    COMPARE_KINDS,
    COMPARED_KINDS,
    EXCLUDED_KINDS,
    Box,
    ChangedElement,
    ModelDiff,
    cluster_boxes,
    diff_models,
    merge_boxes,
)
from services.drawings.revisions.record import (
    DATE_PATTERN,
    Revision,
    RevisionHistory,
    parse_date,
)
from services.drawings.revisions.register import (
    REGISTER_TITLE,
    revision_register_group,
    revision_register_primitives,
    revision_register_table,
)

__all__ = [
    "CLOUD_BULGE_PAPER_MM",
    "CLOUD_MARGIN_PAPER_MM",
    "CLUSTER_GAP_PAPER_MM",
    "COMPARED_KINDS",
    "COMPARE_KINDS",
    "DATE_PATTERN",
    "EXCLUDED_KINDS",
    "REGISTER_TITLE",
    "TAG_SIDE_PAPER_MM",
    "Box",
    "ChangedElement",
    "ModelDiff",
    "Revision",
    "RevisionHistory",
    "cloud_regions",
    "cluster_boxes",
    "diff_models",
    "merge_boxes",
    "parse_date",
    "revision_cloud",
    "revision_marks",
    "revision_register_group",
    "revision_register_primitives",
    "revision_register_table",
    "revision_tag",
]
