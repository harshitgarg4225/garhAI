"""Team seats (G-4): a practice buys capacity, then decides who holds it.

WHY A SEAT IS NOT A USER
------------------------
``users`` already exists and already carries ``role`` (admin/member). A seat is a
different fact: ``users.role`` is *authority* inside the firm, a seat is *entitlement
the firm has paid for*. They move independently and must be able to — a practice
promotes a member to admin without buying anything, and lets a seat lapse at renewal
without demoting anybody. Merging them would mean "cancel a seat" silently changes who
can delete a project, which is a security change dressed as a billing one.

Two seat types, and only one costs money:

* ``editor`` — consumes the plan's paid seat entitlement. An architect drawing.
* ``viewer`` — free and unlimited. A client, a consultant, a junior looking. Charging an
  architect for their own client is a good way to lose the architect, and the share-link
  surface already lets a client in without an account at all.

TENANCY
-------
A seat names a ``user_id`` and the table carries no foreign key (see
``billing/models.py`` for why these tables stand alone), so :func:`assign_seat` resolves
the user through :class:`~garh_api.repositories.users.UserRepository` — which is
firm-scoped — and gets ``None`` for a user in another firm. That is a stronger check
than a foreign key would have been: an FK to ``users`` would happily accept another
firm's user id.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from garh_api.billing.errors import PlanChangeError, SeatLimitError
from garh_api.billing.models import SEAT_TYPES
from garh_api.billing.repositories import Seat, SeatRepository
from garh_api.billing.subscriptions import Entitlement, entitlement
from garh_api.logging import get_logger
from garh_api.repositories import UserRepository
from garh_api.tenancy import EntityNotFoundError, TenantCtx

_log = get_logger(__name__)

#: The seat type that consumes paid entitlement.
PAID_SEAT_TYPE = "editor"


@dataclass(frozen=True)
class SeatSummary:
    """What the billing page and the assign gate both read."""

    entitled: int
    editors_used: int
    viewers_used: int

    @property
    def available(self) -> int:
        return max(0, self.entitled - self.editors_used)

    @property
    def over_entitlement(self) -> int:
        """Editor seats held beyond what is paid for. Non-zero only after a downgrade."""
        return max(0, self.editors_used - self.entitled)


async def seat_summary(
    session: AsyncSession, ctx: TenantCtx, *, ent: Entitlement | None = None
) -> tuple[Entitlement, SeatSummary]:
    resolved = ent or await entitlement(session, ctx)
    repo = SeatRepository(session, ctx)
    editors = await repo.count_of_type("editor")
    viewers = await repo.count_of_type("viewer")
    return resolved, SeatSummary(
        entitled=resolved.seats_entitled, editors_used=editors, viewers_used=viewers
    )


async def assign_seat(
    session: AsyncSession,
    ctx: TenantCtx,
    *,
    user_id: uuid.UUID,
    seat_type: str = PAID_SEAT_TYPE,
) -> Seat:
    """Give one firm user a seat. Denies with 402 when the paid seats are all taken.

    Idempotent-ish by constraint rather than by silence: a user who already holds a seat
    gets :class:`~garh_api.billing.errors.PlanChangeError` (409) rather than a second
    row, because the unique index would reject the insert anyway and a 409 that says
    which user is more useful than an IntegrityError.
    """
    ctx.require_admin("assigning a seat")
    if seat_type not in SEAT_TYPES:
        raise PlanChangeError(
            "A seat is either an editor seat or a viewer seat.",
            extra={"seatType": seat_type},
        )

    # Firm-scoped: another firm's user id resolves to None, so a cross-tenant assign is
    # a 404 on the user, not a seat granted to a stranger.
    user = await UserRepository(session, ctx).get(user_id)
    if user is None:
        raise EntityNotFoundError("user", user_id)

    repo = SeatRepository(session, ctx)
    if await repo.for_user(user_id) is not None:
        raise PlanChangeError(
            "%s already holds a seat." % user.name,
            extra={"userId": str(user_id)},
        )

    if seat_type == PAID_SEAT_TYPE:
        ent, summary = await seat_summary(session, ctx)
        if summary.available < 1:
            _log.info(
                "billing.seat_denied",
                entitled=summary.entitled,
                used=summary.editors_used,
                plan_code=ent.plan.code,
            )
            raise SeatLimitError(
                "Your %s plan includes %d editor seat(s) and all of them are assigned."
                % (ent.plan.name, summary.entitled),
                extra={
                    "planCode": ent.plan.code,
                    "entitled": summary.entitled,
                    "used": summary.editors_used,
                },
            )

    return await repo.assign(user_id=user_id, seat_type=seat_type, assigned_by=ctx.user_id)


async def release_seat(session: AsyncSession, ctx: TenantCtx, seat_id: uuid.UUID) -> bool:
    """Take a seat back. Returns False if it was not this firm's seat to take."""
    ctx.require_admin("releasing a seat")
    return await SeatRepository(session, ctx).delete(seat_id)


async def assert_downgrade_fits(
    session: AsyncSession, ctx: TenantCtx, *, entitled_after: int
) -> None:
    """Refuse a plan change that would leave more editor seats assigned than paid for.

    The alternative — silently revoking whichever seats do not fit — would pick people
    to lock out by row order. Making the admin release seats first is the honest
    version, and it is why the error names the number.
    """
    editors = await SeatRepository(session, ctx).count_of_type(PAID_SEAT_TYPE)
    if editors > entitled_after:
        raise PlanChangeError(
            "That plan includes %d editor seat(s) and %d are assigned. Release %d "
            "first." % (entitled_after, editors, editors - entitled_after),
            extra={"assigned": editors, "entitledAfter": entitled_after},
        )


__all__ = [
    "PAID_SEAT_TYPE",
    "SeatSummary",
    "assert_downgrade_fits",
    "assign_seat",
    "release_seat",
    "seat_summary",
]
