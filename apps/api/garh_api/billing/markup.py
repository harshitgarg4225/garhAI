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
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

#: The ``platform_settings`` key the owner's percentage lives under.
MARKUP_KEY: Final = "billing.markup_bps"

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


async def set_markup_bps(session: AsyncSession, bps: int, *, updated_by: uuid.UUID | None) -> int:
    """Persist a new fee for every charge from now on. Old rows keep theirs."""
    from garh_api.repositories.platform_settings import PlatformSettingRepository

    if isinstance(bps, bool) or not isinstance(bps, int) or bps < 0 or bps > MAX_MARKUP_BPS:
        raise MarkupValueError("markup must be between 0 and %d basis points." % MAX_MARKUP_BPS)
    await PlatformSettingRepository(session).set(MARKUP_KEY, str(bps), updated_by=updated_by)
    return bps


__all__ = [
    "BPS_PER_PERCENT",
    "MARKUP_KEY",
    "MAX_MARKUP_BPS",
    "MarkupValueError",
    "bps_to_percent",
    "charged_micros",
    "current_markup_bps",
    "markup_micros",
    "percent_to_bps",
    "set_markup_bps",
]
