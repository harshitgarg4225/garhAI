"""What a firm is entitled to, right now (G-2).

One function matters here: :func:`entitlement`. Everything that gates on money —
quotas, seats, the plan card in the UI — reads it, so there is exactly one answer to
"what is this firm allowed to do" and no second implementation to drift.

TWO RULES THAT ARE EASY TO GET WRONG
------------------------------------
**A firm with no subscription row is on the free plan**, not "unlimited". A missing row
is the normal state for a firm that signed up five minutes ago, and the failure mode of
treating it as unmetered is that the entire quota system is inert for exactly the
population most likely to abuse it.

**A subscription that is not ``active`` is entitled to the FREE plan's allowances**, not
its own plan's. A card that stopped working must stop the spend; it must not stop the
architect from opening last week's drawings. So a ``past_due`` firm on Practice keeps
its projects, its sheets and its exports of things already made, and drops to free-tier
allowances for anything that costs us money to produce. ``effective_plan`` is that
distinction made explicit — code that wants "what they pay for" reads ``plan``, code
that wants "what they may spend" reads ``effective_plan``, and neither can be mistaken
for the other.

**THE PERIOD ROLLS FORWARD ON READ, NOT ONLY ON WRITE.** The stored
``current_period_start``/``current_period_end`` are an *anchor*, not the answer. Nothing
in this product renews a subscription on a timer, so if :func:`entitlement` returned the
stored window verbatim, then one month after a firm subscribed every ``credit_event``
would fall outside ``[period_start, period_end)`` — usage would read zero forever and the
quota could never deny again. That is bug class 1 from CLAUDE.md ("a gate that silently
never fires") in its exact shape, and it is why :func:`current_period` exists: the window
a gate counts over is always the one containing *now*, computed from the anchor, whether
or not any writer has been along to persist it. :func:`ensure_subscription` persists the
rolled window when an admin write path passes through, so the row stays honest for
invoicing — but correctness of the gate does not depend on that ever happening.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from garh_api.billing.plans import (
    DEFAULT_PLAN_CODE,
    Plan,
    default_plan,
    monthly_charge_inr,
    plan_for,
    seat_entitlement,
)
from garh_api.billing.repositories import Subscription, SubscriptionRepository
from garh_api.tenancy import TenantCtx


def month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """The calendar month containing ``now``, in UTC, half-open.

    The billing period for a firm that has never subscribed. Calendar months (rather
    than 30-day windows from signup) so a firm's quota resets on a date it can predict
    and an invoice covers a month an accountant recognises.
    """
    moment = now.astimezone(UTC)
    start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


#: A hard stop on the roll-forward loop below. 1,200 monthly periods is a century, which
#: no subscription row can honestly need; hitting it means the anchor is nonsense (a
#: corrupted row, a clock a long way out) and the honest answer is the calendar month
#: containing ``now`` rather than an unbounded loop.
_MAX_PERIOD_ROLLS: Final[int] = 1_200


def add_months(moment: datetime, months: int) -> datetime:
    """``moment`` shifted by whole calendar months, clamping the day to month length.

    Jan 31 + 1 month is Feb 28 (or 29). Callers below always add from the *original*
    anchor rather than iterating one month at a time, so that clamp cannot accumulate
    into drift — a period anchored on the 31st is still on the 31st in March.
    """
    total = (moment.year * 12 + (moment.month - 1)) + months
    year, month = divmod(total, 12)
    month += 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def current_period(start: datetime, end: datetime, now: datetime) -> tuple[datetime, datetime]:
    """The billing window containing ``now``, rolled forward from a stored anchor.

    Pure, and the reason the quota gate cannot go inert. ``(start, end)`` is what the
    subscription row says; the answer is that same window while it still contains
    ``now``, and otherwise the *n*-th monthly window after it that does. Nothing in this
    product renews a subscription on a schedule, so without this a firm's quota window
    would be a month in the past forever and every later ``credit_event`` would fall
    outside it — usage zero, allowance never reached, gate permanently open.

    A window that is empty or inverted (``end <= start``) cannot be rolled coherently, so
    it falls back to the calendar month containing ``now`` — the same window a firm with
    no subscription row at all gets.
    """
    if end <= start:
        return month_bounds(now)
    if now < end:
        return start, end
    # Roll from ``end``: the first rolled window is contiguous with the stored one, and
    # every later one is computed from that same anchor, so no clamping drift.
    for index in range(_MAX_PERIOD_ROLLS):
        window_end = add_months(end, index + 1)
        if now < window_end:
            return add_months(end, index), window_end
    return month_bounds(now)


@dataclass(frozen=True)
class Entitlement:
    """Everything a gate needs, resolved once."""

    #: The plan the firm is paying for (or would be, once it pays).
    plan: Plan
    #: The plan whose ALLOWANCES apply right now. Equals ``plan`` while active; the
    #: free plan when the subscription is past due or cancelled.
    effective_plan: Plan
    status: str
    period_start: datetime
    period_end: datetime
    extra_seats: int
    #: ``None`` when the firm has never subscribed — there is no row to update.
    subscription: Subscription | None

    @property
    def seats_entitled(self) -> int:
        """Editor seats the firm may assign.

        Seats follow the *paid* plan, not the effective one: a card that failed must
        not lock a ten-person practice down to one seat overnight. The spend gates
        (quotas) are what tighten; access does not.
        """
        return seat_entitlement(self.plan, self.extra_seats)

    @property
    def monthly_charge_inr(self) -> int:
        return monthly_charge_inr(self.plan, self.extra_seats)

    @property
    def is_active(self) -> bool:
        return self.status == "active"


async def entitlement(
    session: AsyncSession, ctx: TenantCtx, *, now: datetime | None = None
) -> Entitlement:
    """Resolve the firm's current entitlement. The single source for every money gate."""
    moment = now or datetime.now(UTC)
    row = await SubscriptionRepository(session, ctx).get_for_firm()
    if row is None:
        start, end = month_bounds(moment)
        free = default_plan()
        return Entitlement(
            plan=free,
            effective_plan=free,
            status="active",
            period_start=start,
            period_end=end,
            extra_seats=0,
            subscription=None,
        )
    # ``plan_for`` raises on an unknown code rather than defaulting — a plan_code the
    # catalogue does not know must be a loud failure, not a silent downgrade or a
    # silent upgrade. The CHECK constraint on the column makes this unreachable from
    # SQL, which is exactly why it must stay unreachable from code too.
    plan = plan_for(row.plan_code)
    period_start, period_end = current_period(
        row.current_period_start, row.current_period_end, moment
    )
    rolled = period_start != row.current_period_start
    # A subscription set to stop at the period end really has stopped once that period
    # is behind us. Rolling its window forward without this would hand a cancelled firm
    # its paid allowances for every month after it cancelled — the same "gate that
    # cannot deny" defect the roll-forward exists to fix, moved one step along.
    status = "cancelled" if (rolled and row.cancel_at_period_end) else row.status
    return Entitlement(
        plan=plan,
        effective_plan=plan if status == "active" else default_plan(),
        status=status,
        period_start=period_start,
        period_end=period_end,
        extra_seats=row.extra_seats,
        subscription=row,
    )


async def ensure_subscription(
    session: AsyncSession,
    ctx: TenantCtx,
    *,
    provider_name: str = "mock",
    now: datetime | None = None,
) -> Subscription:
    """Return the firm's subscription row, creating a free one if it has none.

    Called by the routes that need a row to update (plan change, invoice issue). Not
    called on read paths: a GET must not write, and :func:`entitlement` already answers
    correctly without a row.

    This is also the **renewal path**. There is no scheduler in this product, so the
    period rolls forward the next time an admin write path touches the subscription: the
    stored window is brought up to the one containing ``now``, and a subscription that
    asked to stop at the period end is marked ``cancelled`` on the way past. Persisting
    it here is what makes the row match what :func:`entitlement` already reports and what
    the next invoice will cover; the gate itself does not wait for it.
    """
    moment = now or datetime.now(UTC)
    repo = SubscriptionRepository(session, ctx)
    existing = await repo.get_for_firm()
    if existing is not None:
        period_start, period_end = current_period(
            existing.current_period_start, existing.current_period_end, moment
        )
        if period_start == existing.current_period_start:
            return existing
        return await repo.update(
            existing.id,
            period_start=period_start,
            period_end=period_end,
            status="cancelled" if existing.cancel_at_period_end else None,
        )
    start, end = month_bounds(moment)
    return await repo.create(
        plan_code=DEFAULT_PLAN_CODE,
        status="active",
        period_start=start,
        period_end=end,
        provider=provider_name,
    )


__all__ = [
    "Entitlement",
    "add_months",
    "current_period",
    "ensure_subscription",
    "entitlement",
    "month_bounds",
]
