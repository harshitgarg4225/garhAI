"""Project repository."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from garh_api import models
from garh_api.repositories.domain import Project
from garh_api.tenancy import Page, Repository, RepositoryUsageError


@dataclass(frozen=True)
class ProjectPatch:
    """Partial update. ``None`` means "leave alone".

    ``architect_of_record`` and ``city_pack`` therefore cannot be cleared through this
    dataclass — use :meth:`ProjectRepository.clear_architect_of_record` /
    :meth:`set_city_pack` for that. Explicit beats a magic sentinel.
    """

    name: str | None = None
    status: str | None = None
    units: str | None = None
    city_pack: str | None = None
    architect_of_record: uuid.UUID | None = None


class ProjectRepository(Repository[models.Project, Project]):
    """Projects belonging to the caller's firm.

    Every read here is the cross-tenant test's target: a valid project id from another
    firm resolves to :class:`~garh_api.tenancy.EntityNotFoundError` → HTTP 404.
    """

    row_type = models.Project
    entity_name = "project"

    def to_domain(self, row: models.Project) -> Project:
        return Project.from_row(row)

    # -- reads ---------------------------------------------------------
    async def list(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        include_archived: bool = False,
        status: str | None = None,
        demo_only: bool = False,
    ) -> Page[Project]:
        stmt = self._scoped_select()
        if status is not None:
            if status not in models.PROJECT_STATUSES:
                raise RepositoryUsageError(
                    "status must be one of %s." % ", ".join(models.PROJECT_STATUSES)
                )
            stmt = stmt.where(models.Project.status == status)
        elif not include_archived:
            stmt = stmt.where(models.Project.status != "archived")
        if demo_only:
            stmt = stmt.where(models.Project.demo.is_(True))
        return await self._page(stmt, limit=limit, cursor=cursor, newest_first=True)

    async def get_demo_project(self) -> Project | None:
        """The seeded demo project — every empty state offers it (delight rule 8)."""
        stmt = (
            self._scoped_select()
            .where(models.Project.demo.is_(True))
            .order_by(models.Project.created_at.asc())
            .limit(1)
        )
        row = await self._first(stmt)
        return None if row is None else self.to_domain(row)

    async def get_by_name(self, name: str) -> Project | None:
        stmt = self._scoped_select().where(models.Project.name == name.strip()).limit(1)
        row = await self._first(stmt)
        return None if row is None else self.to_domain(row)

    # -- writes --------------------------------------------------------
    async def create(
        self,
        *,
        name: str,
        status: str = "draft",
        units: str = "ft-in",
        city_pack: str | None = None,
        architect_of_record: uuid.UUID | None = None,
        demo: bool = False,
    ) -> Project:
        self.ctx.require_write("creating a project")
        clean = name.strip()
        if not clean:
            raise RepositoryUsageError("Project name cannot be blank.")
        if status not in models.PROJECT_STATUSES:
            raise RepositoryUsageError(
                "status must be one of %s." % ", ".join(models.PROJECT_STATUSES)
            )
        if units not in models.PROJECT_UNITS:
            raise RepositoryUsageError(
                "units must be one of %s." % ", ".join(models.PROJECT_UNITS)
            )
        if architect_of_record is not None:
            await self._require_member(architect_of_record)
        row = self._new_row(
            name=clean,
            status=status,
            units=units,
            city_pack=city_pack,
            architect_of_record=architect_of_record,
            demo=demo,
        )
        await self._insert(row)
        self._log.info("project.created", entity_id=str(row.id), demo=demo)
        return self.to_domain(row)

    async def update(self, project_id: uuid.UUID, patch: ProjectPatch) -> Project:
        self.ctx.require_write("editing this project")
        row = await self._require_row(project_id)
        values: dict[str, Any] = {}
        if patch.name is not None:
            clean = patch.name.strip()
            if not clean:
                raise RepositoryUsageError("Project name cannot be blank.")
            values["name"] = clean
        if patch.status is not None:
            if patch.status not in models.PROJECT_STATUSES:
                raise RepositoryUsageError(
                    "status must be one of %s." % ", ".join(models.PROJECT_STATUSES)
                )
            values["status"] = patch.status
        if patch.units is not None:
            if patch.units not in models.PROJECT_UNITS:
                raise RepositoryUsageError(
                    "units must be one of %s." % ", ".join(models.PROJECT_UNITS)
                )
            values["units"] = patch.units
        if patch.city_pack is not None:
            values["city_pack"] = patch.city_pack
        if patch.architect_of_record is not None:
            await self._require_member(patch.architect_of_record)
            values["architect_of_record"] = patch.architect_of_record
        return self.to_domain(await self._apply_patch(row, values))

    async def set_status(self, project_id: uuid.UUID, status: str) -> Project:
        return await self.update(project_id, ProjectPatch(status=status))

    async def set_city_pack(self, project_id: uuid.UUID, city_pack: str | None) -> Project:
        """Set or clear the city rule pack (clearing falls back to ``nbc-core`` only)."""
        self.ctx.require_write("changing the rule pack")
        row = await self._require_row(project_id)
        row.city_pack = city_pack
        await self.flush()
        return self.to_domain(row)

    async def clear_architect_of_record(self, project_id: uuid.UUID) -> Project:
        self.ctx.require_write("editing this project")
        row = await self._require_row(project_id)
        row.architect_of_record = None
        await self.flush()
        return self.to_domain(row)

    async def archive(self, project_id: uuid.UUID) -> Project:
        """Soft delete. Preferred over :meth:`delete` — ops/versions/sheets survive."""
        return await self.set_status(project_id, "archived")

    async def delete(self, project_id: uuid.UUID) -> bool:
        """Hard delete. Cascades to plot, brief, ops, versions, jobs, sheets, shares.

        Admin-only, and the caller must write an ``audit_log`` entry (§13 audits
        deletions).
        """
        self.ctx.require_admin("deleting a project")
        deleted = await self._delete_by_id(project_id)
        if deleted:
            self._log.warning("project.deleted", entity_id=str(project_id))
        return deleted

    # -- helpers -------------------------------------------------------
    async def _require_member(self, user_id: uuid.UUID) -> None:
        """Architect-of-record must be a member of the same firm.

        Without this check a caller could point ``architect_of_record`` at another
        firm's user id — the FK alone would happily accept it, and that name lands on
        every municipal sheet.
        """
        stmt = (
            select(models.User.id)
            .where(models.User.id == user_id)
            .where(models.User.firm_id == self.firm_id)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        if result.first() is None:
            raise RepositoryUsageError(
                "The architect of record must be a member of your firm."
            )


__all__ = ["ProjectPatch", "ProjectRepository"]
