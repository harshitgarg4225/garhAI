"""Rupees on the screen, dollars in the ledger — and the one dated rate between them.

Three promises:

* the rate is validated at boot (a bad ``BILLING_USD_INR_RATE`` refuses to start);
* ``micros_to_paise`` is exact integer arithmetic rounded half-up ONCE, and the web's
  ``money.ts`` agrees with it to the paisa — :data:`SHARED_CASES` is the same table
  ``money.test.ts`` pins, so the two implementations cannot drift apart silently;
* the usage response carries the rate and its date, so every rupee the web shows has
  its dollar source one hover away.
"""

from __future__ import annotations

from typing import Any

import pytest
from garh_api.billing.fx import (
    RateValueError,
    format_inr,
    micros_to_paise,
    validate_rate,
    validate_rate_date,
)

#: (micro-USD, rate, paise). Mirrored verbatim in apps/web/src/features/billing/money.test.ts.
SHARED_CASES: tuple[tuple[int, str, int], ...] = (
    (0, "84.00", 0),
    (1, "84.00", 0),  # 0.0084 paise
    (59, "84.00", 0),  # 0.4956 → 0
    (60, "84.00", 1),  # 0.504 → 1
    (100, "50.00", 1),  # exactly 0.5 → 1: half-up, never banker's
    (300, "50.00", 2),  # exactly 1.5 → 2
    (40_000, "84.00", 336),  # $0.04 → ₹3.36
    (38_095, "84.00", 320),  # 319.998 → 320
    (1_000_000, "84.00", 8_400),  # $1 → ₹84.00
    (5_000_000, "84.00", 42_000),  # the $5 trial budget → ₹420.00
    (1_050_000, "83.1234", 8_728),  # 8727.957 → 8728, four-decimal rate
    (123_456_789, "84.5", 1_043_210),  # 1043209.87 → 1043210
)


@pytest.fixture(autouse=True)
def _billing_tables(request: Any) -> None:
    """``GET /billing/usage`` reads ``billing_subscriptions``; see ``test_markup.py``."""
    if request.node.get_closest_marker("integration"):
        from garh_api.billing.models import BILLING_METADATA

        BILLING_METADATA.create_all(request.getfixturevalue("database"))


# ---------------------------------------------------------------------------
# 1. The rate is a number with a date, or it is refused
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "normalised"),
    [("84.00", "84.00"), (" 84 ", "84"), ("83.1234", "83.1234"), ("0.5", "0.5"), (84, "84")],
)
def test_a_rate_is_accepted_and_normalised(raw: object, normalised: str) -> None:
    assert validate_rate(raw) == normalised


@pytest.mark.parametrize("bad", ["", "abc", "0", "-84", "84.12345", "8.4e1", "nan", "inf", True])
def test_what_is_not_a_rate_is_refused(bad: object) -> None:
    with pytest.raises(RateValueError):
        validate_rate(bad)


def test_the_date_is_iso_or_refused() -> None:
    assert validate_rate_date(" 2026-09-01 ") == "2026-09-01"
    for bad in ("", "01-09-2026", "2026-13-01", "yesterday"):
        with pytest.raises(RateValueError):
            validate_rate_date(bad)


def test_settings_refuse_to_boot_on_a_bad_rate(monkeypatch: Any) -> None:
    """The boot gate, not just the helper: a wrong env value never reaches a card."""
    from garh_api.config import Settings

    monkeypatch.setenv("BILLING_USD_INR_RATE", "84.00")
    monkeypatch.setenv("BILLING_USD_INR_RATE_AS_OF", "2026-09-01")
    ok = Settings()
    assert (ok.billing_usd_inr_rate, ok.billing_usd_inr_rate_as_of) == ("84.00", "2026-09-01")

    monkeypatch.setenv("BILLING_USD_INR_RATE", "eighty-four")
    with pytest.raises(ValueError, match="BILLING_USD_INR_RATE"):
        Settings()

    monkeypatch.setenv("BILLING_USD_INR_RATE", "84.00")
    monkeypatch.setenv("BILLING_USD_INR_RATE_AS_OF", "last tuesday")
    with pytest.raises(ValueError, match="BILLING_USD_INR_RATE_AS_OF"):
        Settings()


# ---------------------------------------------------------------------------
# 2. The arithmetic the web must agree with
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("micros", "rate", "paise"), SHARED_CASES)
def test_micros_to_paise_matches_the_shared_table(micros: int, rate: str, paise: int) -> None:
    assert micros_to_paise(micros, rate) == paise


def test_negative_micros_are_nothing_not_a_credit() -> None:
    assert micros_to_paise(-5, "84.00") == 0


def test_rupees_are_grouped_the_indian_way() -> None:
    assert format_inr(0) == "₹0.00"
    assert format_inr(5) == "₹0.05"
    assert format_inr(42_000) == "₹420.00"
    assert format_inr(100_000) == "₹1,000.00"
    assert format_inr(12_345_678) == "₹1,23,456.78"
    assert format_inr(1_234_567_890) == "₹1,23,45,678.90"


# ---------------------------------------------------------------------------
# 3. The usage response names the rate beside the dollars
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_usage_carries_the_display_rate_and_its_date(client, api, firm_a) -> None:
    from garh_api.config import get_settings

    settings = get_settings()
    response = await client.get("%s/billing/usage" % api, headers=firm_a.headers)
    assert response.status_code == 200, response.text
    spend = response.json()["spend"]
    assert spend is not None, "the test settings configure a budget"
    assert spend["usdInrRate"] == settings.billing_usd_inr_rate
    assert spend["usdInrRateAsOf"] == settings.billing_usd_inr_rate_as_of
    assert validate_rate(spend["usdInrRate"]) and validate_rate_date(spend["usdInrRateAsOf"])
    # The ledger numbers stay micro-USD: nothing on the wire is a rupee figure derived
    # server-side, so there is exactly one place (the web) that converts.
    assert {"capMicros", "spentMicros", "remainingMicros", "providerCostMicros"} <= set(spend)
    assert not any(key.endswith("Inr") or key.endswith("Paise") for key in spend)
