"""Credit-event repository — usage metering from day one (§2, product-spec D11).

Metered credits go live in the M3 beta, but the *events* are recorded from the first
commit: pricing needs real COGS behaviour to look back on, and a meter switched on
later has no history. Events are append-only facts (a render happened, a solve
happened) — never a running balance. Balances are derived by summing, so a
double-charge is visible instead of baked into a counter.
"""

from __future__ import annotations

import uuid
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
        self,
        *,
        kind: str,
        qty: int = 1,
        meta: dict[str, Any] | None = None,
        cost_micros: int | None = None,
    ) -> CreditEvent:
        """Record one usage event, priced.

        ``meta`` should carry enough to reconcile a bill later: the job id, provider,
        model/preset, and for LLM calls the token counts.

        ``cost_micros`` defaults to whatever :func:`billing.spend.cost_micros_for` reads
        out of that same ``meta``, so a call site that already records honest metadata
        is priced without touching it. Pass it explicitly only when the caller knows
        something the meta does not. A mock provider prices at 0 — a stack running on
        fixtures must not burn a real budget.
        """
        if kind not in models.CREDIT_EVENT_KINDS:
            raise RepositoryUsageError(
                "kind must be one of %s." % ", ".join(models.CREDIT_EVENT_KINDS)
            )
        if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
            raise RepositoryUsageError("qty must be a positive integer.")
        facts = meta or {}
        if cost_micros is None:
            # Imported HERE, not at module scope. `garh_api.billing.__init__` pulls in
            # `quotas`, which imports this very package — a module-level import makes
            # `garh_api.repositories` un-importable. Same reason `quotas.require_quota`
            # imports `garh_api.deps` inside its factory, and documented in both places
            # so neither gets "tidied" back up to the top.
            from garh_api.billing.spend import cost_micros_for

            cost_micros = cost_micros_for(kind, qty=qty, meta=facts)
        if isinstance(cost_micros, bool) or not isinstance(cost_micros, int) or cost_micros < 0:
            raise RepositoryUsageError("cost_micros must be a non-negative integer of micro-USD.")
        row = self._new_row(
            kind=kind,
            qty=qty,
            meta=facts,
            cost_micros=cost_micros,
            user_id=self.ctx.user_id,
        )
        await self._insert(row)
        self._log.info("credit_event.recorded", credit_kind=kind, qty=qty, cost_micros=cost_micros)
        return self.to_domain(row)

    async def spent_micros(self, user_id: uuid.UUID | None = None) -> int:
        """Everything this architect has ever spent, in micro-dollars.

        Lifetime, not per period: the trial budget is a one-off allowance, so there is
        no window to filter on. ``user_id`` defaults to the caller — a firm-wide total
        would let one architect's spend close another's door.
        """
        target = user_id if user_id is not None else self.ctx.user_id
        stmt = select(func.coalesce(func.sum(models.CreditEvent.cost_micros), 0)).where(
            models.CreditEvent.firm_id == self.firm_id
        )
        if target is not None:
            stmt = stmt.where(models.CreditEvent.user_id == target)
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

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
