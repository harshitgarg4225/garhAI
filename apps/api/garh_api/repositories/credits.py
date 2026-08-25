"""Credit-event repository — usage metering from day one (§2, product-spec D11).

Metered credits go live in the M3 beta, but the *events* are recorded from the first
commit: pricing needs real COGS behaviour to look back on, and a meter switched on
later has no history. Events are append-only facts (a render happened, a solve
happened) — never a running balance. Balances are derived by summing, so a
double-charge is visible instead of baked into a counter.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from garh_api import models
from garh_api.repositories.domain import CreditEvent
from garh_api.tenancy import Page, Repository, RepositoryUsageError


class CreditEventRepository(Repository[models.CreditEvent, CreditEvent]):
    """Record and aggregate metered usage for the caller's firm."""

    row_type = models.CreditEvent
    entity_name = "credit_event"

    def to_domain(self, row: models.CreditEvent) -> CreditEvent:
        return CreditEvent.from_row(row)

    # -- writes --------------------------------------------------------
    async def record(
        self, *, kind: str, qty: int = 1, meta: dict[str, Any] | None = None
    ) -> CreditEvent:
        """Record one usage event.

        ``meta`` should carry enough to reconcile a bill later: the job id, provider,
        model/preset, and for LLM calls the token counts.
        """
        if kind not in models.CREDIT_EVENT_KINDS:
            raise RepositoryUsageError(
                "kind must be one of %s." % ", ".join(models.CREDIT_EVENT_KINDS)
            )
        if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
            raise RepositoryUsageError("qty must be a positive integer.")
        row = self._new_row(kind=kind, qty=qty, meta=meta or {})
        await self._insert(row)
        self._log.info("credit_event.recorded", credit_kind=kind, qty=qty)
        return self.to_domain(row)

    # -- reads ---------------------------------------------------------
    async def list_recent(
        self, *, limit: int | None = None, cursor: str | None = None, kind: str | None = None
    ) -> Page[CreditEvent]:
        stmt = self._scoped_select()
        if kind is not None:
            if kind not in models.CREDIT_EVENT_KINDS:
                raise RepositoryUsageError(
                    "kind must be one of %s." % ", ".join(models.CREDIT_EVENT_KINDS)
                )
            stmt = stmt.where(models.CreditEvent.kind == kind)
        return await self._page(stmt, limit=limit, cursor=cursor, newest_first=True)

    async def usage_by_kind(
        self, *, since: datetime | None = None, until: datetime | None = None
    ) -> dict[str, int]:
        """Summed quantities per kind for the period — the billing-page numbers."""
        stmt = (
            select(models.CreditEvent.kind, func.coalesce(func.sum(models.CreditEvent.qty), 0))
            .where(models.CreditEvent.firm_id == self.firm_id)
            .group_by(models.CreditEvent.kind)
        )
        if since is not None:
            stmt = stmt.where(models.CreditEvent.created_at >= since)
        if until is not None:
            stmt = stmt.where(models.CreditEvent.created_at < until)
        result = await self._session.execute(stmt)
        return {str(row[0]): int(row[1]) for row in result.all()}

    async def total_since(self, kind: str, since: datetime) -> int:
        """Total of one kind since a timestamp — backs per-firm quota checks."""
        if kind not in models.CREDIT_EVENT_KINDS:
            raise RepositoryUsageError(
                "kind must be one of %s." % ", ".join(models.CREDIT_EVENT_KINDS)
            )
        stmt = (
            select(func.coalesce(func.sum(models.CreditEvent.qty), 0))
            .where(models.CreditEvent.firm_id == self.firm_id)
            .where(models.CreditEvent.kind == kind)
            .where(models.CreditEvent.created_at >= since)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())


__all__ = ["CreditEventRepository"]
