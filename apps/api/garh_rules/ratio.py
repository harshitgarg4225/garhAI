"""Exact integer arithmetic. There is no float in a compliance verdict.

Every derived limit in the DSL is an exact rational (`{num, den}`) applied to an
integer base, and ``rulepacks/README.md`` fixes the rounding direction for each
one — always toward the stricter reading:

===========================  ==================================================
``far_max`` / ``coverage_max``  ``floor(num * plotAreaMm2 / den)``
``ventilation_ratio_min``       ``max(ceil(num * roomAreaMm2 / den), minAreaMm2)``
``parking_min``                 ``max(ceil(rate * basis), minSpaces)``
Vastu score                     ``round_half_up(100 * Sigma(w*sat) / Sigma(w))``
===========================  ==================================================

``floor`` on an allowance and ``ceil`` on a requirement both move the boundary
against the design, so a value that lands exactly on a limit passes and nothing
sneaks through on a rounding artefact.

Only the Vastu score and ``brahmasthan_open`` need :class:`fractions.Fraction`;
everything else is plain ``int`` division, which is what keeps a full run inside
the 100 ms budget (§14).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .errors import ContextError

__all__ = [
    "Ratio",
    "ONE",
    "ZERO",
    "ceil_div",
    "floor_div",
    "round_half_up",
    "require_int",
]


def floor_div(num: int, den: int) -> int:
    """``floor(num / den)`` for any sign. Python's ``//`` already floors."""
    if den == 0:
        raise ZeroDivisionError("ratio denominator is zero")
    return num // den


def ceil_div(num: int, den: int) -> int:
    """``ceil(num / den)`` for any sign, in integers only."""
    if den == 0:
        raise ZeroDivisionError("ratio denominator is zero")
    return -((-num) // den)


def round_half_up(value: Fraction) -> int:
    """Round half **away from zero on the .5** upward, i.e. 2.5 -> 3, -2.5 -> -2.

    This is the DSL's declared ``rounding: half-up``, applied once to a final
    score and never to an intermediate. It is *not* banker's rounding: the
    Vastu score must be reproducible from the published formula by hand.
    """
    return math.floor(value + Fraction(1, 2))


def require_int(value: Any, what: str) -> int:
    """Accept only a true integer. A float length is a bug, not a rounding job.

    ``bool`` is rejected explicitly — ``isinstance(True, int)`` is ``True`` in
    Python, and a boolean silently standing in for 0/1 millimetres would be a
    very quiet wrong answer.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContextError(
            "%s must be an integer (geometry is integer millimetres), got %r" % (what, value),
            field=what,
        )
    return value


@dataclass(frozen=True)
class Ratio:
    """An exact rational from the DSL: FAR 1.75 is ``{num: 175, den: 100}``.

    Never reduced on construction: the pack's own numerator and denominator are
    what a reviewer reads, and a compliance report has to be re-explainable from
    the pack text years later.
    """

    num: int
    den: int

    def __post_init__(self) -> None:
        if self.den <= 0:
            raise ContextError("ratio denominator must be >= 1, got %r" % (self.den,))

    @classmethod
    def from_json(cls, data: Mapping[str, Any], what: str = "ratio") -> Ratio:
        return cls(
            num=require_int(data.get("num"), "%s.num" % what),
            den=require_int(data.get("den"), "%s.den" % what),
        )

    def to_json(self) -> dict[str, int]:
        return {"num": self.num, "den": self.den}

    def floor_of(self, base: int) -> int:
        """The allowance form: ``floor(num * base / den)``."""
        return floor_div(self.num * base, self.den)

    def ceil_of(self, base: int) -> int:
        """The requirement form: ``ceil(num * base / den)``."""
        return ceil_div(self.num * base, self.den)

    def as_fraction(self) -> Fraction:
        return Fraction(self.num, self.den)

    def __str__(self) -> str:  # pragma: no cover - display only
        return "%d/%d" % (self.num, self.den)


ONE = Ratio(1, 1)
ZERO = Ratio(0, 1)
