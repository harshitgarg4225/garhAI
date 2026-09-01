"""What a piece of work COSTS, and the per-architect ceiling on it.

THE UNIT: micro-dollars (µUSD), integer
----------------------------------------
One millionth of one US dollar. ``$5.00`` is ``5_000_000``.

This is deliberately NOT the unit :mod:`garh_api.billing.money` uses, and the two must
never be added together. They measure different things in different currencies:

* ``money.py`` counts **whole rupees** — what a firm is INVOICED. Its docstring
  explains why a second unit there would ship a 100× error, and that still holds.
* this module counts **micro-dollars** — what a provider CHARGES US. Anthropic and
  Stability bill in USD, and their prices are quoted per million tokens, so µUSD is the
  unit their arithmetic is already in.

Converting between them needs an exchange rate, which is a business decision with a
date on it, so this module does not do it. Nothing here reaches an invoice.

WHY MICRO-DOLLARS AND NOT CENTS
-------------------------------
A single Haiku call can cost well under one cent. In cents it would round to zero, and
a cap counting zeroes never trips — the meter would look like it worked. In µUSD the
published prices are exact integers: ``$5.00 / 1M tokens`` is exactly ``5 µUSD`` per
token, so the common case has no rounding at all. Where a rate is fractional (cache
reads at 0.1×, cache writes at 1.25×) the arithmetic stays integer and floors once, at
the end, in :func:`_per_token`.

WHAT IS AUTHORITATIVE HERE AND WHAT IS NOT
------------------------------------------
:data:`LLM_PRICES` are Anthropic's published first-party API rates. They are real
numbers and they change; :func:`assert_prices_cover_configured_model` fails the boot if
the configured model has no row, so an unpriced model is a startup error rather than a
silently free one.

:data:`FLAT_PRICES` are NOT published prices. A render's true cost depends on the
Stability plan the operator bought, and solver and export runs burn our own CPU rather
than anyone's API. They are operator-set figures with defaults that are deliberately
generous, and they are labelled as estimates everywhere they surface.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

#: One US dollar in micro-dollars.
MICROS_PER_USD: Final = 1_000_000

#: Tokens per unit of the published price ("per million tokens").
TOKENS_PER_PRICE_UNIT: Final = 1_000_000


class TokenPrice:
    """One model's rates, in µUSD per million tokens.

    Cache rates follow Anthropic's published multipliers rather than being typed out
    separately, so a model whose base rate changes cannot end up with a stale cache
    rate beside a fresh input rate.
    """

    __slots__ = ("input_micros", "output_micros")

    def __init__(self, input_usd_per_mtok: int, output_usd_per_mtok: int) -> None:
        # Prices are given in whole dollars per million tokens (all current models are
        # whole dollars); µUSD keeps the multiply exact.
        self.input_micros = input_usd_per_mtok * MICROS_PER_USD
        self.output_micros = output_usd_per_mtok * MICROS_PER_USD

    @property
    def cache_read_micros(self) -> int:
        """Cache reads bill at ~0.1× input."""
        return self.input_micros // 10

    @property
    def cache_write_micros(self) -> int:
        """Cache writes bill at ~1.25× input."""
        return (self.input_micros * 5) // 4


#: Anthropic first-party API rates, USD per million tokens (input, output).
#: Source: the Claude API model table. Partner platforms (Bedrock, Vertex) price
#: separately and are not represented here.
LLM_PRICES: Final[Mapping[str, TokenPrice]] = {
    "claude-fable-5": TokenPrice(10, 50),
    "claude-mythos-5": TokenPrice(10, 50),
    "claude-opus-5": TokenPrice(5, 25),
    "claude-opus-4-8": TokenPrice(5, 25),
    "claude-opus-4-7": TokenPrice(5, 25),
    "claude-opus-4-6": TokenPrice(5, 25),
    "claude-sonnet-5": TokenPrice(2, 10),
    "claude-sonnet-4-6": TokenPrice(3, 15),
    "claude-haiku-4-5": TokenPrice(1, 5),
}

#: The rate used for a model with no row above.
#:
#: The most expensive one known, on purpose. An unpriced model must never be the
#: CHEAPEST way to spend — that would make the cap something a config change could walk
#: around. Boot-time validation should catch this first; this is the belt.
_UNKNOWN_MODEL_PRICE: Final = TokenPrice(10, 50)

#: µUSD per unit of work for the kinds that are not token-priced.
#:
#: NOT published prices — see the module docstring. ``render`` is a stand-in for a
#: Stability image on a mid-tier plan; ``solver`` and ``export`` are our own compute,
#: priced so that a heavy CPU job is not free in the ledger. An operator who knows
#: their real numbers should override these.
FLAT_PRICES: Final[Mapping[str, int]] = {
    "render": 40_000,  # $0.040 per image
    "solver": 6_000,  # $0.006 per generate — CPU seconds, not an API charge
    "export": 2_000,  # $0.002 per drawing set
}

#: What a metered call costs when nothing real was spent. A mock provider does not
#: touch anyone's API, and charging for it would make the whole trial budget vanish
#: into a stack that runs on fixtures.
FREE_PROVIDERS: Final[frozenset[str]] = frozenset({"mock", "stub", "none", ""})


def _per_token(tokens: int, micros_per_mtok: int) -> int:
    """``tokens × rate``, floored once. Integer throughout — no float ever."""
    if tokens <= 0:
        return 0
    return (tokens * micros_per_mtok) // TOKENS_PER_PRICE_UNIT


def llm_cost_micros(
    *,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> int:
    """What one Anthropic call cost, from the token counts it reported.

    The adapter already returns these (``LlmUsage``) and the routes already write them
    into ``credit_events.meta``, so this is arithmetic on numbers we have — not an
    estimate.
    """
    price = LLM_PRICES.get(model, _UNKNOWN_MODEL_PRICE)
    return (
        _per_token(input_tokens, price.input_micros)
        + _per_token(output_tokens, price.output_micros)
        + _per_token(cache_read_tokens, price.cache_read_micros)
        + _per_token(cache_write_tokens, price.cache_write_micros)
    )


def cost_micros_for(kind: str, *, qty: int = 1, meta: Mapping[str, object] | None = None) -> int:
    """The cost of one metered event, in µUSD.

    Reads the same ``meta`` the routes already record, so metering a new call site is
    one keyword argument rather than a second bookkeeping path.
    """
    facts = meta or {}
    provider = str(facts.get("provider", "")).lower()
    if provider in FREE_PROVIDERS:
        return 0

    if kind == "llm":
        return llm_cost_micros(
            model=str(facts.get("model", "")),
            input_tokens=_int(facts.get("inputTokens")),
            output_tokens=_int(facts.get("outputTokens")),
            cache_read_tokens=_int(facts.get("cacheReadTokens")),
            cache_write_tokens=_int(facts.get("cacheWriteTokens")),
        )
    return FLAT_PRICES.get(kind, 0) * max(0, qty)


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def format_usd(micros: int) -> str:
    """``4_250_000`` → ``"$4.25"``. For messages an architect reads."""
    whole, frac = divmod(max(0, micros), MICROS_PER_USD)
    return "$%d.%02d" % (whole, (frac * 100) // MICROS_PER_USD)


def assert_prices_cover_configured_model(model: str) -> None:
    """Fail loudly at boot when the configured model has no published rate.

    An unpriced model would meter at the fallback rate, which is defensible but is not
    the truth, and nothing would say so. This is the same posture as the rest of the
    billing package: a gate that cannot go quiet.
    """
    if model and model not in LLM_PRICES:
        raise ValueError(
            "No price row for LLM model %r. Add it to LLM_PRICES (rates are published "
            "per million tokens) — an unpriced model meters at the most expensive "
            "known rate, which is a guess, not a bill." % model
        )


__all__ = [
    "FLAT_PRICES",
    "FREE_PROVIDERS",
    "LLM_PRICES",
    "MICROS_PER_USD",
    "TokenPrice",
    "assert_prices_cover_configured_model",
    "cost_micros_for",
    "format_usd",
    "llm_cost_micros",
]
