"""Feature flags — global config, read at boot (§18).

Not tenant data, so not a scoped repository. The table is global; per-firm overrides
live in ``firms.settings["flags"]`` (see
:meth:`garh_api.repositories.domain.Firm.flag_override`), which keeps flipping a flag
for one beta firm a single-row edit rather than a fan-out.

Boot wiring (``main.py`` lifespan)::

    from garh_api.db import session_scope
    from garh_api.repositories.flags import FLAG_REGISTRY, FlagRepository

    async with session_scope() as session:
        await FLAG_REGISTRY.refresh(FlagRepository(session))

Reads after that are in-memory. :meth:`FlagRegistry.enabled` falls back to
:data:`DEFAULT_FLAGS` (everything off), so a missing row is never a crash.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from garh_api import models
from garh_api.logging import get_logger
from garh_api.repositories.domain import Firm, Flag
from garh_api.tenancy import RepositoryUsageError

_log = get_logger(__name__)

#: Known flags and their default state. §18: "default off".
DEFAULT_FLAGS: dict[str, bool] = {
    "facade_v2": False,
    "interior_precise": False,
    "copilot_advanced_ops": False,
    "dxf_import": False,
    "billing_live": False,
    "render_diffusers": False,
}


class FlagRepository:
    """Non-tenant repository for the global ``flags`` table.

    Constructor::

        FlagRepository(session: AsyncSession)

    Deliberately not a :class:`~garh_api.tenancy.Repository` subclass: ``flags`` has no
    ``firm_id``, and pretending otherwise would mean inventing a fake tenant column.
    Reading global config is not a tenancy hole — no tenant data is reachable from here.
    """

    entity_name = "flag"

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def all_flags(self) -> dict[str, bool]:
        """Every flag row as ``{key: enabled}``, merged over :data:`DEFAULT_FLAGS`."""
        result = await self._session.execute(select(models.Flag.key, models.Flag.enabled))
        merged = dict(DEFAULT_FLAGS)
        for key, enabled in result.all():
            merged[str(key)] = bool(enabled)
        return merged

    async def list_flags(self) -> list[Flag]:
        result = await self._session.execute(
            select(models.Flag).order_by(models.Flag.key.asc())
        )
        return [Flag.from_row(row) for row in result.scalars().all()]

    async def is_enabled(self, key: str) -> bool:
        normalised = _normalise_key(key)
        result = await self._session.execute(
            select(models.Flag.enabled).where(models.Flag.key == normalised).limit(1)
        )
        row = result.first()
        if row is None:
            return DEFAULT_FLAGS.get(normalised, False)
        return bool(row[0])

    async def upsert(
        self, key: str, *, enabled: bool, description: str | None = None
    ) -> Flag:
        """Create or flip a flag. Ops/seed use only — never a tenant-facing endpoint."""
        normalised = _normalise_key(key)
        stmt = (
            pg_insert(models.Flag)
            .values(key=normalised, enabled=enabled, description=description)
            .on_conflict_do_update(
                index_elements=[models.Flag.key],
                set_={"enabled": enabled, "description": description},
            )
            .returning(models.Flag)
        )
        result = await self._session.execute(stmt)
        row = result.scalars().one()
        _log.info("flag.upserted", flag_key=normalised, enabled=enabled)
        return Flag.from_row(row)

    async def seed_defaults(self) -> int:
        """Insert any missing known flag at its default. Idempotent."""
        existing = {flag.key for flag in await self.list_flags()}
        created = 0
        for key, enabled in DEFAULT_FLAGS.items():
            if key not in existing:
                await self.upsert(key, enabled=enabled, description="seeded default")
                created += 1
        return created


class FlagRegistry:
    """In-memory flag cache, refreshed at boot (and on demand).

    Flags gate features, not security decisions — a stale read for a few seconds is
    fine, and a DB round-trip on every request is not.
    """

    def __init__(self) -> None:
        self._flags: dict[str, bool] = dict(DEFAULT_FLAGS)
        self._loaded = False

    async def refresh(self, repo: FlagRepository) -> dict[str, bool]:
        self._flags = await repo.all_flags()
        self._loaded = True
        _log.info(
            "flags.loaded",
            count=len(self._flags),
            enabled=sorted(k for k, v in self._flags.items() if v),
        )
        return dict(self._flags)

    @property
    def loaded(self) -> bool:
        return self._loaded

    def enabled(self, key: str, firm: Firm | None = None) -> bool:
        """Resolve a flag, honouring a per-firm override when a firm is supplied."""
        normalised = _normalise_key(key)
        if firm is not None:
            override = firm.flag_override(normalised)
            if override is not None:
                return override
        return self._flags.get(normalised, DEFAULT_FLAGS.get(normalised, False))

    def snapshot(self) -> dict[str, bool]:
        return dict(self._flags)

    def set_for_tests(self, key: str, enabled: bool) -> None:
        """Test-only override. Never call from application code."""
        self._flags[_normalise_key(key)] = enabled


#: Process-wide registry. Populated by the boot hook shown in the module docstring.
FLAG_REGISTRY = FlagRegistry()


def _normalise_key(key: str) -> str:
    clean = (key or "").strip().lower()
    if not clean:
        raise RepositoryUsageError("A flag key cannot be blank.")
    return clean


__all__ = ["DEFAULT_FLAGS", "FLAG_REGISTRY", "FlagRegistry", "FlagRepository"]
