"""The one exchange rate: US dollars to rupees, for DISPLAY.

The ledger is micro-USD (:mod:`garh_api.billing.spend`) because that is what the
providers bill in, and the plan price is whole rupees (:mod:`garh_api.billing.money`)
because that is what a firm is invoiced. An architect reading a budget wants rupees,
so the web derives them — and it derives them from THIS number, which is:

* a dated, hand-set value (``BILLING_USD_INR_RATE`` / ``BILLING_USD_INR_RATE_AS_OF``),
  never a live fetch — a budget that moved with the market would be a different
  number every time the page loaded, and a number nobody could reconcile later;
* validated at boot: a rate that is not a positive decimal with at most four places,
  or a date that is not ``YYYY-MM-DD``, refuses to start rather than rendering
  ``₹NaN`` on every card;
* served beside every rupee it produced (``usdInrRate`` / ``usdInrRateAsOf`` on the
  usage response), so the dollar source is always one hover away.

:func:`micros_to_paise` is the reference arithmetic. The web's ``features/billing/
money.ts`` must agree with it to the paisa; ``tests/test_inr_display.py`` and
``money.test.ts`` share one table of cases so the two cannot drift apart silently.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from garh_api.config import (
    USD_INR_RATE_MAX_DECIMALS as RATE_MAX_DECIMALS,
)
from garh_api.config import (
    RateValueError,
    validate_usd_inr_rate,
    validate_usd_inr_rate_date,
)

#: Paise per rupee, micro-dollars per dollar.
PAISE_PER_INR: Final = 100
MICROS_PER_USD: Final = 1_000_000

#: The validators live in :mod:`garh_api.config` (a ``Settings`` validator must not
#: import this package); re-exported here so billing code has one place to look.
validate_rate = validate_usd_inr_rate
validate_rate_date = validate_usd_inr_rate_date


def micros_to_paise(micros: int, rate: str) -> int:
    """Micro-dollars → paise at ``rate`` rupees per dollar, rounded half-up once.

    Integer in, integer out: ``Decimal`` throughout, one rounding at the very end, so
    ``₹0.01`` of drift cannot appear between a card and the row beneath it.
    """
    if micros <= 0:
        return 0
    rupees = Decimal(int(micros)) * Decimal(validate_rate(rate)) / MICROS_PER_USD
    return int((rupees * PAISE_PER_INR).to_integral_value(rounding=ROUND_HALF_UP))


def format_inr(paise: int) -> str:
    """``12_345_678`` → ``"₹1,23,456.78"`` — Indian grouping, always two decimals."""
    safe = max(0, int(paise))
    whole, frac = divmod(safe, PAISE_PER_INR)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join([*groups, tail])
    return "₹%s.%02d" % (digits, frac)


__all__ = [
    "MICROS_PER_USD",
    "PAISE_PER_INR",
    "RATE_MAX_DECIMALS",
    "RateValueError",
    "format_inr",
    "micros_to_paise",
    "validate_rate",
    "validate_rate_date",
]
