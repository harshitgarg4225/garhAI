"""Firm (tenant) repository — scoped by primary key rather than ``firm_id``."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from garh_api import models
from garh_api.repositories.domain import Firm
from garh_api.tenancy import Repository, RepositoryUsageError, TenantCtx


class FirmRepository(Repository[models.Firm, Firm]):
    """Read/update the caller's own firm. It can never see another one.

    Special case in the scoping model: ``firms`` has no ``firm_id`` column because
    its primary key *is* the tenant id, so :meth:`_tenant_column` is overridden to
    ``firms.id``. Every inherited query therefore resolves to ``WHERE id = :firm_id``
    — one row, always the caller's.

    Firm *creation* lives in
    :class:`~garh_api.repositories.auth_directory.AuthDirectoryRepository` (signup
    happens before any tenant context exists).
    """

    row_type = models.Firm
    entity_name = "firm"

    def __init__(self, session: AsyncSession, ctx: TenantCtx) -> None:
        super().__init__(session, ctx)

    def _tenant_column(self) -> ColumnElement[Any]:
        return models.Firm.id

    def _new_row(self, **values: Any) -> models.Firm:
        raise RepositoryUsageError(
            "Firms are created during signup by AuthDirectoryRepository."
            "create_firm_with_owner(), not through FirmRepository."
        )

    def to_domain(self, row: models.Firm) -> Firm:
        return Firm.from_row(row)

    # -- reads ---------------------------------------------------------
    async def get_current(self) -> Firm:
        """The caller's firm. Raises ``EntityNotFoundError`` if it was deleted."""
        return self.to_domain(await self._require_row(self.firm_id))

    # -- writes --------------------------------------------------------
    async def rename(self, name: str) -> Firm:
        self.ctx.require_admin("renaming the firm")
        clean = name.strip()
        if not clean:
            raise RepositoryUsageError("Firm name cannot be blank.")
        row = await self._require_row(self.firm_id)
        return self.to_domain(await self._apply_patch(row, {"name": clean}))

    async def set_logo_url(self, logo_url: str | None) -> Firm:
        self.ctx.require_admin("changing the firm logo")
        row = await self._require_row(self.firm_id)
        row.logo_url = logo_url
        await self.flush()
        return self.to_domain(row)

    async def merge_settings(self, patch: dict[str, Any]) -> Firm:
        """Shallow-merge into ``firms.settings`` (title block fields, units, flags).

        Shallow on purpose: settings is a flat namespace of small values, and a deep
        merge would make "delete this key" impossible to express.
        """
        self.ctx.require_admin("changing firm settings")
        row = await self._require_row(self.firm_id)
        merged = dict(row.settings or {})
        merged.update(patch)
        row.settings = merged
        await self.flush()
        self._log.info("firm.settings_updated", keys=sorted(patch.keys()))
        return self.to_domain(row)

    async def replace_settings(self, settings: dict[str, Any]) -> Firm:
        self.ctx.require_admin("replacing firm settings")
        row = await self._require_row(self.firm_id)
        row.settings = dict(settings)
        await self.flush()
        return self.to_domain(row)


__all__ = ["FirmRepository"]
