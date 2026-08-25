"""Brief repository — one brief per project (``unique(project_id)``)."""

from __future__ import annotations

import uuid
from typing import Any

from garh_api import models
from garh_api.repositories._guards import require_project_in_firm
from garh_api.repositories.domain import Brief
from garh_api.tenancy import EntityNotFoundError, ProjectScopedRepository, RepositoryUsageError


class BriefRepository(ProjectScopedRepository[models.Brief, Brief]):
    """The client brief for a project.

    ``data`` is the Brief document (rooms with target areas in mm², adjacency wishes,
    facing, budget, style, plus ``assumptions[]`` — golden rule 4: every AI default is
    a visible chip). This layer stores it as-is; parsing and completeness scoring live
    above it. :meth:`merge_patch` implements the RFC 7386 semantics of op 5
    (``brief.update``) so the copilot and the form share one write path.
    """

    row_type = models.Brief
    entity_name = "brief"

    def to_domain(self, row: models.Brief) -> Brief:
        return Brief.from_row(row)

    # -- reads ---------------------------------------------------------
    async def get_for_project(self, project_id: uuid.UUID) -> Brief | None:
        row = await self._first(self._project_scoped_select(project_id).limit(1))
        return None if row is None else self.to_domain(row)

    async def require_for_project(self, project_id: uuid.UUID) -> Brief:
        brief = await self.get_for_project(project_id)
        if brief is None:
            raise EntityNotFoundError("brief", project_id)
        return brief

    # -- writes --------------------------------------------------------
    async def upsert(
        self,
        project_id: uuid.UUID,
        *,
        data: dict[str, Any] | None = None,
        vastu_mode: str | None = None,
        completeness: int | None = None,
    ) -> Brief:
        """Create or replace brief fields. Only supplied fields change."""
        self.ctx.require_write("editing the brief")
        await require_project_in_firm(self._session, self.firm_id, project_id)
        if vastu_mode is not None and vastu_mode not in models.VASTU_MODES:
            raise RepositoryUsageError(
                "vastu_mode must be one of %s." % ", ".join(models.VASTU_MODES)
            )
        if completeness is not None:
            _validate_completeness(completeness)
        if data is not None and not isinstance(data, dict):
            raise RepositoryUsageError("Brief data must be an object.")

        row = await self._first(self._project_scoped_select(project_id).limit(1))
        if row is None:
            row = self._new_row(
                project_id=project_id,
                data=data if data is not None else {},
                vastu_mode=vastu_mode or "off",
                completeness=completeness if completeness is not None else 0,
            )
            await self._insert(row)
            self._log.info("brief.created", project_id=str(project_id))
            return self.to_domain(row)

        if data is not None:
            row.data = data
        if vastu_mode is not None:
            row.vastu_mode = vastu_mode
        if completeness is not None:
            row.completeness = completeness
        await self.flush()
        self._log.info("brief.updated", project_id=str(project_id))
        return self.to_domain(row)

    async def merge_patch(
        self,
        project_id: uuid.UUID,
        patch: dict[str, Any],
        *,
        completeness: int | None = None,
    ) -> Brief:
        """Apply an RFC 7386 JSON merge-patch to ``data`` (op 5 ``brief.update``).

        Merge-patch semantics: a ``null`` value **deletes** the key, nested objects
        merge recursively, arrays replace wholesale.
        """
        self.ctx.require_write("editing the brief")
        if not isinstance(patch, dict):
            raise RepositoryUsageError("A merge patch must be an object.")
        current = await self.get_for_project(project_id)
        base = dict(current.data) if current is not None else {}
        merged = apply_merge_patch(base, patch)
        return await self.upsert(project_id, data=merged, completeness=completeness)

    async def set_vastu_mode(self, project_id: uuid.UUID, vastu_mode: str) -> Brief:
        """Switch Vastu off/advisory/strict — changes solver constraints (§5.2)."""
        return await self.upsert(project_id, vastu_mode=vastu_mode)

    async def set_completeness(self, project_id: uuid.UUID, completeness: int) -> Brief:
        return await self.upsert(project_id, completeness=completeness)


def apply_merge_patch(target: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """RFC 7386 JSON merge-patch, returning a new dict (never mutates ``target``)."""
    result = dict(target)
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict):
            existing = result.get(key)
            base = existing if isinstance(existing, dict) else {}
            result[key] = apply_merge_patch(base, value)
        else:
            result[key] = value
    return result


def _validate_completeness(completeness: Any) -> None:
    if isinstance(completeness, bool) or not isinstance(completeness, int):
        raise RepositoryUsageError("completeness must be an integer 0–100.")
    if not 0 <= completeness <= 100:
        raise RepositoryUsageError("completeness must be between 0 and 100.")


__all__ = ["BriefRepository", "apply_merge_patch"]
