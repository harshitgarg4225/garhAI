"""Shared ownership guards for child rows.

A foreign key proves a parent *exists*; it says nothing about who owns it. Writing a
child row (plot, brief, op, job, sheet, comment...) under a ``project_id`` therefore
needs an explicit firm-scoped check on the parent, or a caller could attach data to
another firm's project and — because the child would then carry the *attacker's*
``firm_id`` — the row would be invisible to its real owner while polluting their
project. These helpers are that check, in one place.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from garh_api import models
from garh_api.tenancy import EntityNotFoundError


async def require_project_in_firm(
    session: AsyncSession, firm_id: uuid.UUID, project_id: uuid.UUID
) -> None:
    """Raise ``EntityNotFoundError`` (→ 404) unless the project belongs to the firm."""
    stmt = (
        select(models.Project.id)
        .where(models.Project.id == project_id)
        .where(models.Project.firm_id == firm_id)
        .limit(1)
    )
    result = await session.execute(stmt)
    if result.first() is None:
        raise EntityNotFoundError("project", project_id)


async def require_design_version_in_firm(
    session: AsyncSession, firm_id: uuid.UUID, design_version_id: uuid.UUID
) -> None:
    stmt = (
        select(models.DesignVersion.id)
        .where(models.DesignVersion.id == design_version_id)
        .where(models.DesignVersion.firm_id == firm_id)
        .limit(1)
    )
    result = await session.execute(stmt)
    if result.first() is None:
        raise EntityNotFoundError("design_version", design_version_id)


async def require_sheet_in_firm(
    session: AsyncSession, firm_id: uuid.UUID, sheet_id: uuid.UUID
) -> None:
    stmt = (
        select(models.Sheet.id)
        .where(models.Sheet.id == sheet_id)
        .where(models.Sheet.firm_id == firm_id)
        .limit(1)
    )
    result = await session.execute(stmt)
    if result.first() is None:
        raise EntityNotFoundError("sheet", sheet_id)


__all__ = [
    "require_design_version_in_firm",
    "require_project_in_firm",
    "require_sheet_in_firm",
]
