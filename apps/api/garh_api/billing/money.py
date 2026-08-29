"""Money arithmetic for the billing package. **Whole rupees, integers, no floats.**

THE UNIT
--------
Every rupee figure in this package — plan price, line amount, CGST, SGST, IGST,
invoice total, seat charge — is an ``int`` number of **whole rupees**. Never paise,
never ``Decimal``, never ``float``.

Why whole rupees and not paise, given Razorpay's API is paise-denominated: the rest of
this repository already speaks whole rupees (``garh_api.estimator`` prices a fee scale
in them, ``routers/catalog.py`` publishes ``priceInrPerSqm``), and a second money unit
in the same codebase is how a 100× billing error gets shipped. The conversion happens
in exactly one place — :func:`rupees_to_paise`, called only at the Razorpay boundary —
and it is an exact integer multiply, so nothing is lost going out and nothing has to be
rounded coming back.

Percentages are integers ×100 (18% is ``1800``), the same convention
``garh_api.estimator`` uses, so a GST rate is exact and a half-percent rate would still
be representable.

ROUNDING
--------
:func:`round_half_away_from_zero` — the repository-wide rule (CLAUDE.md: "Rounding is
half-away-from-zero, *not* ``Math.round``"), applied here to :class:`fractions.Fraction`
so the whole chain amount → rate → tax is exact until the single rounding step at the
end. ``round()`` is banker's rounding in Python 3 (``round(0.5) == 0``), which on a tax
column understates the government's share about half the time; on money that is not a
rounding preference, it is a wrong number.

There is no ``float`` anywhere in this module and there must never be: a tax component
computed as ``4999 * 0.09`` is ``449.90999999999997``, and an invoice whose components
do not add up to its total is one a chartered accountant will reject.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Final

#: A whole-rupee amount. An alias, not a new type — it documents intent at call sites
#: without pretending the type checker will catch a paise value passed as rupees.
Rupees = int

#: Percentages are carried as integers ×100 (18% → 1800).
PERCENT_SCALE: Final = 100

#: One rupee in paise. Used only by :func:`rupees_to_paise`.
PAISE_PER_RUPEE: Final = 100


class MoneyError(ValueError):
    """A money value that cannot be honoured (negative amount, float input)."""


def _as_fraction(value: Fraction | int) -> Fraction:
    """Accept only exact numeric types. A ``float`` here is a bug, so it raises.

    ``bool`` is excluded explicitly: ``isinstance(True, int)`` is True in Python, and
    ``True`` reaching a money function means a caller passed a flag where an amount
    belongs.
    """
    if isinstance(value, bool):
        raise MoneyError("A boolean is not an amount.")
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    raise MoneyError(
        "Money must be an int or a Fraction, got %r (%s). Floats are banned in this "
        "package — see the module docstring." % (value, type(value).__name__)
    )


def round_half_away_from_zero(value: Fraction | int) -> int:
    """Round an exact rational to a whole rupee, halves going away from zero.

    ``0.5 → 1``, ``1.5 → 2``, ``-0.5 → -1``. The repository-wide rule, and the rule
    Indian invoicing practice follows (CGST Act §170 rounds tax to the nearest rupee).
    """
    exact = _as_fraction(value)
    negative = exact < 0
    magnitude = -exact if negative else exact
    whole = magnitude.numerator // magnitude.denominator
    remainder = magnitude - whole
    if remainder * 2 >= 1:
        whole += 1
    return -whole if negative else whole


def percent_of(amount_inr: int, rate_percent_x100: int) -> int:
    """``rate`` percent of ``amount``, in whole rupees.

    Both arguments are integers and the intermediate is a :class:`Fraction`, so
    ``percent_of(4999, 900)`` is exactly ``Fraction(449910, 1000)`` before it is
    rounded to ``450`` — never ``449.90999999999997``.
    """
    if not isinstance(amount_inr, int) or isinstance(amount_inr, bool):
        raise MoneyError("An amount must be an int number of whole rupees.")
    if not isinstance(rate_percent_x100, int) or isinstance(rate_percent_x100, bool):
        raise MoneyError("A rate must be an int percentage ×100 (18%% → 1800).")
    if rate_percent_x100 < 0:
        raise MoneyError("A tax rate cannot be negative.")
    return round_half_away_from_zero(Fraction(amount_inr * rate_percent_x100, PERCENT_SCALE * 100))


def rupees_to_paise(amount_inr: int) -> int:
    """The ONE conversion to the payment gateway's unit. Exact, integer, one-way.

    Razorpay's REST API takes ``amount`` in the smallest currency unit (paise for INR).
    Because every amount we hold is already a whole rupee, this multiply is exact and
    there is no rounding decision to get wrong. Nothing in this package converts back:
    a gateway amount is compared against ``rupees_to_paise(ours)``, never divided.
    """
    if not isinstance(amount_inr, int) or isinstance(amount_inr, bool):
        raise MoneyError("An amount must be an int number of whole rupees.")
    if amount_inr < 0:
        raise MoneyError("Cannot charge a negative amount.")
    return amount_inr * PAISE_PER_RUPEE


#: Indian place-value names, largest first, with the divisor each introduces.
_INDIAN_SCALES: Final[tuple[tuple[int, str], ...]] = (
    (10_000_000, "Crore"),
    (100_000, "Lakh"),
    (1_000, "Thousand"),
    (100, "Hundred"),
)

_ONES: Final[tuple[str, ...]] = (
    "Zero",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
)
_TENS: Final[tuple[str, ...]] = (
    "",
    "",
    "Twenty",
    "Thirty",
    "Forty",
    "Fifty",
    "Sixty",
    "Seventy",
    "Eighty",
    "Ninety",
)


def _words_below_hundred(value: int) -> list[str]:
    if value < 20:
        return [_ONES[value]]
    tens, ones = divmod(value, 10)
    return [_TENS[tens]] + ([_ONES[ones]] if ones else [])


def _spell(value: int) -> str:
    """The number alone, in Indian place values. ``0`` is the caller's problem."""
    remaining = value
    words: list[str] = []
    for divisor, label in _INDIAN_SCALES:
        count, remaining = divmod(remaining, divisor)
        if count:
            # Crore is the top scale and is itself unbounded, so its count can exceed
            # 99 (a ₹1,000 crore invoice reads "One Thousand Crore"). Recursing keeps
            # that correct instead of silently truncating.
            head = _spell(count) if count > 99 else " ".join(_words_below_hundred(count))
            words.extend([head, label])
    if remaining:
        words.extend(_words_below_hundred(remaining))
    return " ".join(words)


def amount_in_words(amount_inr: int) -> str:
    """``5899`` → ``"Rupees Five Thousand Eight Hundred Ninety Nine Only"``.

    Indian place values — crore and lakh, not million and billion — and the
    "Rupees … Only" wrapper every invoice an Indian accountant has ever seen carries.
    Not required by Rule 46; its real function is that a figure cannot be altered by
    adding a digit without the words disagreeing.

    Whole rupees only: there are no paise anywhere in this package, so there is no
    "and fifty paise" tail to get wrong.
    """
    if not isinstance(amount_inr, int) or isinstance(amount_inr, bool):
        raise MoneyError("An amount must be an int number of whole rupees.")
    if amount_inr < 0:
        return "Rupees Minus %s Only" % _spell(-amount_inr)
    if amount_inr == 0:
        return "Rupees Zero Only"
    return "Rupees %s Only" % _spell(amount_inr)


__all__ = [
    "PAISE_PER_RUPEE",
    "PERCENT_SCALE",
    "MoneyError",
    "Rupees",
    "amount_in_words",
    "percent_of",
    "round_half_away_from_zero",
    "rupees_to_paise",
]
