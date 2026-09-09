"""The platform fee — what the owner adds on top of provider cost.

The owner's rule, verbatim: "per dollar of credit spent my markup will be 5% of total
credits; this percentage can be changed by me tomorrow." So:

* the fee is a PERCENTAGE OF COST, stored in basis points (``500`` = 5 %) so a value
  like 7.25 % is exact and no float ever reaches a ledger;
* it is read at CHARGE time from :class:`PlatformSettingRepository` (the DB row the
  owner edits through ``PUT /admin/billing/markup``), falling back to
  ``Settings.billing_markup_percent`` when no row exists yet — so a fresh deployment
  charges the configured default and a running one follows the owner without a redeploy;
* it is RECORDED on the credit event it was applied to. Changing the percentage changes
  the next charge and never touches an old row (``test_markup`` keeps that promise).

Units follow :mod:`garh_api.billing.spend`: micro-dollars, integers, one rounding at the
end, half away from zero — a fee that rounds toward zero on every small call would
quietly be a smaller fee than the owner set.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

#: The ``platform_settings`` key the owner's percentage lives under.
MARKUP_KEY: Final = "billing.markup_bps"
#: The sign-in email of the owner who last set it, stored beside the value. The fee page
#: says "set by <who> on <when>", and the owner may belong to any firm — a tenant-scoped
#: user lookup could not resolve them, and ``platform_settings`` is non-tenant anyway.
MARKUP_SET_BY_KEY: Final = "billing.markup_set_by"

#: Basis points per whole percent, and the ceiling the endpoint accepts (100 %).
BPS_PER_PERCENT: Final = 100
MAX_MARKUP_BPS: Final = 100 * BPS_PER_PERCENT
BPS_DENOMINATOR: Final = 10_000


class MarkupValueError(ValueError):
    """The percentage is not a number between 0 and 100 with at most two decimals."""


def percent_to_bps(percent: object) -> int:
    """``5`` → ``500``; ``"7.25"`` → ``725``. Refuses negatives, >100, and >2 decimals.

    Accepts ``int``, ``str`` and :class:`~decimal.Decimal`; ``float`` is converted
    through its shortest repr so ``0.1`` means ``0.1``, not ``0.1000000000000000055``.
    Booleans are refused — ``True`` is not a fee.
    """
    if isinstance(percent, bool):
        raise MarkupValueError("percent must be a number, not a boolean.")
    try:
        value = Decimal(repr(percent)) if isinstance(percent, float) else Decimal(str(percent))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise MarkupValueError("percent must be a number.") from exc
    if not value.is_finite():
        raise MarkupValueError("percent must be a finite number.")
    if value < 0 or value > 100:
        raise MarkupValueError("percent must be between 0 and 100.")
    scaled = value * BPS_PER_PERCENT
    if scaled != scaled.to_integral_value():
        raise MarkupValueError("percent may carry at most two decimals (whole basis points).")
    return int(scaled)


def bps_to_percent(bps: int) -> str:
    """``500`` → ``"5"``; ``725`` → ``"7.25"``. A string, so a UI never parses a float."""
    value = (Decimal(int(bps)) / BPS_PER_PERCENT).normalize()
    text = format(value, "f")
    return text if text != "-0" else "0"


def markup_micros(cost_micros: int, markup_bps: int) -> int:
    """The fee on one charge, in µUSD, rounded half away from zero once at the end."""
    if cost_micros <= 0 or markup_bps <= 0:
        return 0
    numerator = Decimal(cost_micros) * Decimal(markup_bps)
    return int((numerator / BPS_DENOMINATOR).to_integral_value(rounding=ROUND_HALF_UP))


def charged_micros(cost_micros: int, markup_bps: int) -> int:
    """What the architect is charged for work that cost ``cost_micros``."""
    return max(0, cost_micros) + markup_micros(cost_micros, markup_bps)


async def current_markup_bps(session: AsyncSession) -> int:
    """The fee in force right now: the owner's DB row, else the configured default."""
    from garh_api.config import get_settings
    from garh_api.repositories.platform_settings import PlatformSettingRepository

    stored = await PlatformSettingRepository(session).get(MARKUP_KEY)
    if stored is not None:
        try:
            value = int(stored)
        except ValueError:
            value = -1
        if 0 <= value <= MAX_MARKUP_BPS:
            return value
        # A corrupt row must not silently zero the fee or explode a charge; the
        # configured default is the honest fallback, and the endpoint can overwrite it.
    return percent_to_bps(get_settings().billing_markup_percent)


@dataclass(frozen=True, slots=True)
class MarkupState:
    """The fee as it stands, for the endpoint and the usage card."""

    bps: int
    percent: str
    source: str  # "setting" | "default"
    updated_at: datetime | None
    updated_by: uuid.UUID | None
    #: Who set it, as the email they signed in with; ``None`` until an owner has.
    updated_by_email: str | None = None


async def describe_markup(session: AsyncSession) -> MarkupState:
    """The fee in force plus where it came from — the owner's row or the boot default."""
    from garh_api.repositories.platform_settings import PlatformSettingRepository

    repo = PlatformSettingRepository(session)
    bps = await current_markup_bps(session)
    stored = await repo.describe(MARKUP_KEY)
    set_by = await repo.get(MARKUP_SET_BY_KEY) if stored is not None else None
    return MarkupState(
        bps=bps,
        percent=bps_to_percent(bps),
        source="setting" if stored is not None else "default",
        updated_at=stored.updated_at if stored is not None else None,
        updated_by=stored.updated_by if stored is not None else None,
        updated_by_email=(set_by or None) if stored is not None else None,
    )


async def set_markup_bps(
    session: AsyncSession,
    bps: int,
    *,
    updated_by: uuid.UUID | None,
    updated_by_email: str | None = None,
) -> int:
    """Persist a new fee for every charge from now on. Old rows keep theirs.

    ``updated_by_email`` is what the fee page shows as "set by"; it is written in the
    same transaction as the value so the two can never name different changes.
    """
    from garh_api.repositories.platform_settings import PlatformSettingRepository

    if isinstance(bps, bool) or not isinstance(bps, int) or bps < 0 or bps > MAX_MARKUP_BPS:
        raise MarkupValueError("markup must be between 0 and %d basis points." % MAX_MARKUP_BPS)
    repo = PlatformSettingRepository(session)
    await repo.set(MARKUP_KEY, str(bps), updated_by=updated_by)
    await repo.set(
        MARKUP_SET_BY_KEY, (updated_by_email or "").strip().lower(), updated_by=updated_by
    )
    return bps


__all__ = [
    "BPS_PER_PERCENT",
    "MARKUP_KEY",
    "MARKUP_SET_BY_KEY",
    "MAX_MARKUP_BPS",
    "MarkupState",
    "MarkupValueError",
    "bps_to_percent",
    "charged_micros",
    "current_markup_bps",
    "describe_markup",
    "markup_micros",
    "percent_to_bps",
    "set_markup_bps",
]
