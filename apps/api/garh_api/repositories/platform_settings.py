"""``platform_settings`` — the owner's runtime knobs, one row per key.

Non-tenant, like :mod:`garh_api.repositories.flags`: there is no ``firm_id`` because
these values belong to the deployment, not to a practice. Reading them from a tenant
request is not a tenancy hole — no tenant data is reachable from here — and writing
them is gated at the router by the platform-owner allowlist, never by a firm role.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from garh_api import models

_log = structlog.get_logger("garh_api.platform_settings")


def _normalise_key(key: str) -> str:
    normalised = key.strip().lower()
    if not normalised:
        raise ValueError("platform setting key must not be blank.")
    return normalised


@dataclass(frozen=True, slots=True)
class PlatformSetting:
    key: str
    value: str
    updated_by: uuid.UUID | None
    updated_at: datetime

    @classmethod
    def from_row(cls, row: models.PlatformSetting) -> PlatformSetting:
        return cls(
            key=row.key, value=row.value, updated_by=row.updated_by, updated_at=row.updated_at
        )


class PlatformSettingRepository:
    """Get/set one deployment-wide value. Reads are one indexed row; writes upsert."""

    entity_name = "platform_setting"

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, key: str) -> str | None:
        result = await self._session.execute(
            select(models.PlatformSetting.value)
            .where(models.PlatformSetting.key == _normalise_key(key))
            .limit(1)
        )
        row = result.first()
        return None if row is None else str(row[0])

    async def describe(self, key: str) -> PlatformSetting | None:
        result = await self._session.execute(
            select(models.PlatformSetting)
            .where(models.PlatformSetting.key == _normalise_key(key))
            .limit(1)
        )
        row = result.scalars().first()
        return None if row is None else PlatformSetting.from_row(row)

    async def set(self, key: str, value: str, *, updated_by: uuid.UUID | None) -> PlatformSetting:
        normalised = _normalise_key(key)
        stmt = (
            pg_insert(models.PlatformSetting)
            .values(key=normalised, value=value, updated_by=updated_by)
            .on_conflict_do_update(
                index_elements=[models.PlatformSetting.key],
                set_={"value": value, "updated_by": updated_by},
            )
            .returning(models.PlatformSetting)
        )
        result = await self._session.execute(stmt)
        row = result.scalars().one()
        _log.info("platform_setting.set", setting_key=normalised, value=value)
        return PlatformSetting.from_row(row)


__all__ = ["PlatformSetting", "PlatformSettingRepository"]
