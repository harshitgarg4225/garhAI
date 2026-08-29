"""Billing arithmetic, the plan catalogue, GST, and both payment providers.

No Postgres, no Redis, no network — everything here is pure functions and an
``httpx.MockTransport``, so the most consequential assertions in the billing package
(the tax split, the check digit, the signature verification) run on a laptop with
nothing installed.

Every gate in this file is negative-tested: the check that would deny is shown denying,
and where a gate could plausibly be inert it is broken on purpose and shown failing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction

import httpx
import pytest
from garh_api.billing import gst, money, plans, subscriptions
from garh_api.billing.mock import MockBillingProvider
from garh_api.billing.provider import get_billing_provider
from garh_api.billing.razorpay_provider import (
    WEBHOOK_SECRET_ENV,
    RazorpayBillingProvider,
)
from garh_api.billing.types import BillingProviderError, OrderRequest
from garh_api.config import Settings
from garh_api.models import CREDIT_EVENT_KINDS
from pydantic import ValidationError as PydanticValidationError

#: Obviously-fake credentials for the transport double. Not a Razorpay key and not
#: shaped like one on purpose — no test in this repository invents a credential that
#: could be mistaken for real.
FAKE_KEY_ID = "not-a-real-razorpay-key-id"
FAKE_KEY_SECRET = "not-a-real-razorpay-key-secret"

#: The published example GSTIN from the GST documentation — a real, valid check digit,
#: which is what makes it worth pinning.
VALID_EXAMPLE_GSTIN = "27AAPFU0939F1ZV"
#: Fictitious but checksum-valid registrations, generated with the algorithm under test
#: (Karnataka and Maharashtra). Used as customer identities in the API suite.
KARNATAKA_GSTIN = "29AAAAA0000A1ZY"
MAHARASHTRA_GSTIN = "27AAAAA0000A1Z2"


def _razorpay(transport: httpx.MockTransport) -> RazorpayBillingProvider:
    settings = Settings(razorpay_key_id=FAKE_KEY_ID, razorpay_key_secret=FAKE_KEY_SECRET)
    return RazorpayBillingProvider(settings, transport=transport)


# ---------------------------------------------------------------------------
# money — whole rupees, exact, no floats
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Fraction(1, 2), 1),
        (Fraction(3, 2), 2),
        (Fraction(5, 2), 3),
        (Fraction(-1, 2), -1),
        (Fraction(-5, 2), -3),
        (Fraction(449910, 1000), 450),
        (Fraction(0), 0),
        (7, 7),
    ],
)
def test_rounding_is_half_away_from_zero(value: Fraction | int, expected: int) -> None:
    assert money.round_half_away_from_zero(value) == expected


def test_rounding_is_not_pythons_banker_rounding() -> None:
    """The rule is half-AWAY-from-zero, and Python's ``round`` is not it.

    ``round(0.5) == 0`` and ``round(2.5) == 2``. On a tax column that understates the
    government's share about half the time, which is why this module has its own
    rounder rather than calling the builtin.
    """
    assert round(0.5) == 0 and money.round_half_away_from_zero(Fraction(1, 2)) == 1
    assert round(2.5) == 2 and money.round_half_away_from_zero(Fraction(5, 2)) == 3


def test_floats_and_bools_are_refused_as_money() -> None:
    with pytest.raises(money.MoneyError):
        money.round_half_away_from_zero(4999.5)  # type: ignore[arg-type]
    with pytest.raises(money.MoneyError):
        money.percent_of(True, 1800)  # type: ignore[arg-type]
    with pytest.raises(money.MoneyError):
        money.rupees_to_paise(49.99)  # type: ignore[arg-type]


def test_percent_of_is_exact_where_a_float_would_not_be() -> None:
    """9% of ₹4,999 is ₹449.91 → ₹450. The float route gets ₹449.

    ``int(4999 * 0.09)`` is 449 because 4999*0.09 is 449.90999999999997 in binary
    floating point. One rupee, every month, on every intra-state invoice — and the
    invoice would no longer add up.
    """
    assert money.percent_of(4999, 900) == 450
    assert int(4999 * 0.09) == 449


def test_rupees_to_paise_is_the_only_unit_conversion() -> None:
    assert money.rupees_to_paise(4999) == 499_900
    assert money.rupees_to_paise(0) == 0
    with pytest.raises(money.MoneyError):
        money.rupees_to_paise(-1)


@pytest.mark.parametrize(
    ("amount", "words"),
    [
        (0, "Rupees Zero Only"),
        (1, "Rupees One Only"),
        (5899, "Rupees Five Thousand Eight Hundred Ninety Nine Only"),
        (100000, "Rupees One Lakh Only"),
        (10000000, "Rupees One Crore Only"),
    ],
)
def test_amount_in_words_uses_indian_place_values(amount: int, words: str) -> None:
    assert money.amount_in_words(amount) == words


# ---------------------------------------------------------------------------
# plans — the catalogue gate that stops a quota going inert
# ---------------------------------------------------------------------------


def test_every_plan_meters_exactly_the_kinds_the_meter_writes() -> None:
    """The bug-class-2 guard: allowance keys ≡ ``credit_events.kind``.

    A plan keyed ``"renders"`` while the meter writes ``"render"`` produces a quota that
    can never be reached and a compliance-green dashboard, which is exactly how 83 rules
    in this repository went silently inert.
    """
    for plan in plans.PLANS:
        assert set(plan.allowances) == set(CREDIT_EVENT_KINDS), plan.code


def test_the_catalogue_gate_actually_fires(monkeypatch: pytest.MonkeyPatch) -> None:
    """NEGATIVE CONTROL: break a plan's allowance key and the import-time check raises."""
    broken = plans.Plan(
        code="broken",
        name="Broken",
        price_inr_per_month=1,
        included_editor_seats=1,
        extra_seat_inr_per_month=None,
        # "renders" is not a member of CREDIT_EVENT_KINDS — the exact typo the gate exists for.
        allowances={"renders": 5, "solver": 5, "llm": 5, "export": 5},
        summary="",
    )
    monkeypatch.setattr(plans, "PLANS", (broken,))
    monkeypatch.setattr(plans, "_BY_CODE", {"broken": broken})
    with pytest.raises(plans.PlanCatalogueError, match="can never deny"):
        plans._validate_catalogue()


def test_an_unknown_plan_code_raises_and_never_falls_back() -> None:
    with pytest.raises(plans.UnknownPlanError):
        plans.plan_for("gold")
    with pytest.raises(plans.UnknownPlanError):
        plans.plan_for("")


def test_allowance_refuses_a_kind_the_meter_does_not_write() -> None:
    """ "I don't recognise this" must never read as "no limit"."""
    with pytest.raises(plans.UnknownPlanError):
        plans.plan_for("studio").allowance("renders")


def test_zero_and_none_allowances_mean_different_things() -> None:
    free = plans.plan_for("free")
    enterprise = plans.plan_for("enterprise")
    # 0 = not included at all: the free tier does not export a municipal drawing set.
    assert free.allowance("export") == 0
    # None = unmetered.
    assert enterprise.allowance("export") is None


def test_seat_entitlement_and_charge_follow_the_plan() -> None:
    studio = plans.plan_for("studio")
    free = plans.plan_for("free")
    assert plans.seat_entitlement(studio, 0) == 3
    assert plans.seat_entitlement(studio, 2) == 5
    # The free plan sells no extra seats, so a stored number cannot conjure one.
    assert plans.seat_entitlement(free, 4) == 1
    assert plans.monthly_charge_inr(free, 4) == 0
    assert plans.monthly_charge_inr(studio, 2) == 4_999 + 2 * 1_499


# ---------------------------------------------------------------------------
# The billing period — the arithmetic that keeps the quota gate alive
# ---------------------------------------------------------------------------


def test_a_period_that_still_contains_now_is_returned_untouched() -> None:
    """The common case must not move the window under a firm mid-month."""
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = datetime(2026, 9, 1, tzinfo=UTC)
    assert subscriptions.current_period(start, end, datetime(2026, 8, 29, tzinfo=UTC)) == (
        start,
        end,
    )
    # The bounds are half-open: the instant before the end is still inside.
    assert subscriptions.current_period(start, end, end - timedelta(microseconds=1)) == (start, end)


def test_a_period_that_has_ended_rolls_forward_to_the_one_containing_now() -> None:
    """THE FIX for the inert quota, as arithmetic.

    Nothing renews a subscription on a timer, so the stored window is a month in the
    past the moment a firm's first period ends. Returning it verbatim is what made
    ``usage_by_kind(since=..., until=...)`` sum an empty window forever — usage zero,
    allowance never reached, gate permanently open.
    """
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 2, 1, tzinfo=UTC)

    # One period later.
    assert subscriptions.current_period(start, end, datetime(2026, 2, 14, tzinfo=UTC)) == (
        datetime(2026, 2, 1, tzinfo=UTC),
        datetime(2026, 3, 1, tzinfo=UTC),
    )
    # Seven periods later, across a year boundary — still a window containing `now`.
    assert subscriptions.current_period(start, end, datetime(2026, 12, 20, tzinfo=UTC)) == (
        datetime(2026, 12, 1, tzinfo=UTC),
        datetime(2027, 1, 1, tzinfo=UTC),
    )
    # Exactly at the boundary: `end` belongs to the NEXT window, not the one that ended.
    assert subscriptions.current_period(start, end, end) == (
        datetime(2026, 2, 1, tzinfo=UTC),
        datetime(2026, 3, 1, tzinfo=UTC),
    )


@pytest.mark.parametrize("months", [1, 2, 3, 6, 11, 13, 25, 60, 121])
def test_the_rolled_window_always_contains_now_and_stays_contiguous(months: int) -> None:
    """Property: whatever the gap, `now` lands inside the answer and no month is skipped.

    A window that does not contain `now` is a window the meter sums nothing over, which
    is the inert gate again by another route.
    """
    start = datetime(2025, 3, 1, tzinfo=UTC)
    end = datetime(2025, 4, 1, tzinfo=UTC)
    now = subscriptions.add_months(end, months) + timedelta(days=3)
    rolled_start, rolled_end = subscriptions.current_period(start, end, now)
    assert rolled_start <= now < rolled_end
    assert rolled_start == subscriptions.add_months(end, months)
    assert rolled_end == subscriptions.add_months(rolled_start, 1)


def test_a_month_end_anchor_does_not_drift_month_by_month() -> None:
    """Jan 31 → Feb 28 → **Mar 31**, not Mar 28.

    Rolling one month at a time and clamping each time would walk a 31st anchor down to
    the 28th and leave it there, silently shortening every later period by three days.
    """
    assert subscriptions.add_months(datetime(2026, 1, 31, tzinfo=UTC), 1) == datetime(
        2026, 2, 28, tzinfo=UTC
    )
    assert subscriptions.add_months(datetime(2026, 1, 31, tzinfo=UTC), 2) == datetime(
        2026, 3, 31, tzinfo=UTC
    )
    # 2028 is a leap year.
    assert subscriptions.add_months(datetime(2028, 1, 31, tzinfo=UTC), 1) == datetime(
        2028, 2, 29, tzinfo=UTC
    )
    start = datetime(2025, 12, 31, tzinfo=UTC)
    end = datetime(2026, 1, 31, tzinfo=UTC)
    assert subscriptions.current_period(start, end, datetime(2026, 3, 15, tzinfo=UTC)) == (
        datetime(2026, 2, 28, tzinfo=UTC),
        datetime(2026, 3, 31, tzinfo=UTC),
    )


def test_an_incoherent_window_falls_back_to_the_calendar_month() -> None:
    """An inverted or empty anchor cannot be rolled, so it answers like "no subscription".

    Never an unbounded loop and never the stale window: both would be a gate that stops
    denying, which is the failure this whole function exists to prevent.
    """
    now = datetime(2026, 8, 29, 11, 30, tzinfo=UTC)
    same = datetime(2026, 1, 1, tzinfo=UTC)
    assert subscriptions.current_period(same, same, now) == subscriptions.month_bounds(now)
    inverted = subscriptions.current_period(
        datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 4, 1, tzinfo=UTC), now
    )
    assert inverted == subscriptions.month_bounds(now)
    # ...and an anchor so far in the past that the roll hits its ceiling.
    ancient = subscriptions.current_period(
        datetime(1800, 1, 1, tzinfo=UTC), datetime(1800, 2, 1, tzinfo=UTC), now
    )
    assert ancient == subscriptions.month_bounds(now)
    assert ancient[0] <= now < ancient[1]


# ---------------------------------------------------------------------------
# GST — the check digit and the split
# ---------------------------------------------------------------------------


def test_the_published_example_gstin_validates() -> None:
    assert gst.validate_gstin(VALID_EXAMPLE_GSTIN) == VALID_EXAMPLE_GSTIN
    assert gst.validate_gstin(" 27aapfu0939f1zv ") == VALID_EXAMPLE_GSTIN
    assert gst.gstin_state_code(VALID_EXAMPLE_GSTIN) == "27"


def test_a_single_character_typo_fails_the_check_digit() -> None:
    """NEGATIVE CONTROL for the checksum: the shape is right, the digit is not."""
    typo = VALID_EXAMPLE_GSTIN[:5] + "V" + VALID_EXAMPLE_GSTIN[6:]
    assert len(typo) == 15
    with pytest.raises(gst.GstError, match="check digit"):
        gst.validate_gstin(typo)


def test_gstin_shape_and_state_are_checked_before_the_digit() -> None:
    with pytest.raises(gst.GstError, match="15 characters"):
        gst.validate_gstin("27AAPFU0939F1Z")
    with pytest.raises(gst.GstError, match="not shaped like"):
        gst.validate_gstin("27AAPFU0939F1AV")
    # 25 and 28 are retired state codes; a current registration cannot carry one.
    retired = "25" + VALID_EXAMPLE_GSTIN[2:14]
    with pytest.raises(gst.GstError):
        gst.validate_gstin(retired + gst.gstin_checksum(retired))


def test_intra_state_supply_splits_into_equal_cgst_and_sgst() -> None:
    tax = gst.compute_tax(4_999, supplier_state_code="29", place_of_supply_code="29")
    assert (tax.cgst_inr, tax.sgst_inr, tax.igst_inr) == (450, 450, 0)
    assert tax.interstate is False
    assert tax.total_inr == 5_899
    assert tax.place_of_supply == "Karnataka"


def test_inter_state_supply_is_one_igst_line() -> None:
    tax = gst.compute_tax(4_999, supplier_state_code="29", place_of_supply_code="27")
    assert (tax.cgst_inr, tax.sgst_inr, tax.igst_inr) == (0, 0, 900)
    assert tax.interstate is True
    assert tax.total_inr == 5_899
    assert tax.place_of_supply == "Maharashtra"


@pytest.mark.parametrize("taxable", [0, 1, 7, 99, 4_999, 14_999, 49_999, 123_457])
def test_the_invoice_identity_holds_for_every_amount(taxable: int) -> None:
    """``total == taxable + cgst + sgst + igst``, and the halves are equal.

    The one number a customer's chartered accountant checks. Asserted here, enforced
    again by a CHECK constraint on the table.
    """
    for place in ("29", "27"):
        tax = gst.compute_tax(taxable, supplier_state_code="29", place_of_supply_code=place)
        assert tax.total_inr == taxable + tax.cgst_inr + tax.sgst_inr + tax.igst_inr
        assert tax.cgst_inr == tax.sgst_inr
        # And the split is exact against the Fraction ground truth, not a float.
        expected = money.round_half_away_from_zero(Fraction(taxable * 18, 100))
        if tax.interstate:
            assert tax.igst_inr == expected


def test_an_unknown_state_code_is_refused() -> None:
    with pytest.raises(gst.GstError):
        gst.compute_tax(100, supplier_state_code="29", place_of_supply_code="99")
    with pytest.raises(gst.GstError):
        gst.validate_state_code("KA")


def test_invoice_numbers_obey_rule_46b() -> None:
    firm_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
    number = gst.invoice_number(firm_id=firm_id, issued_on=date(2026, 8, 28), sequence=1)
    assert len(number) <= gst.MAX_INVOICE_NUMBER_LENGTH
    assert number.isalnum() and number.isupper()
    assert number.startswith("G2627")
    # Same firm, same year, next serial — consecutive, and only the tail moves.
    later = gst.invoice_number(firm_id=firm_id, issued_on=date(2026, 8, 28), sequence=2)
    assert later[:-4] == number[:-4] and later.endswith("0002")
    # A different firm gets a different series.
    other = gst.invoice_number(firm_id=uuid.uuid4(), issued_on=date(2026, 8, 28), sequence=1)
    assert other != number


def test_the_financial_year_is_indian_not_calendar() -> None:
    assert gst.fiscal_year_code(date(2026, 4, 1)) == "2627"
    assert gst.fiscal_year_code(date(2027, 3, 31)) == "2627"
    assert gst.fiscal_year_code(date(2027, 4, 1)) == "2728"
    assert gst.fiscal_year_bounds(date(2027, 3, 31)) == (date(2026, 4, 1), date(2027, 4, 1))


def test_a_sequence_that_would_overflow_the_series_is_refused() -> None:
    with pytest.raises(gst.GstError):
        gst.invoice_number(firm_id=uuid.uuid4(), issued_on=date(2026, 8, 28), sequence=0)
    with pytest.raises(gst.GstError):
        gst.invoice_number(firm_id=uuid.uuid4(), issued_on=date(2026, 8, 28), sequence=10_000)


def test_supplier_identity_is_read_from_the_environment_and_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BILLING_SUPPLIER_GSTIN", raising=False)
    monkeypatch.delenv("BILLING_SUPPLIER_LEGAL_NAME", raising=False)
    monkeypatch.delenv("BILLING_SUPPLIER_STATE_CODE", raising=False)
    assert gst.supplier_identity().configured is False
    with pytest.raises(gst.GstError, match="no GST registration"):
        gst.supplier_identity().require()

    monkeypatch.setenv("BILLING_SUPPLIER_LEGAL_NAME", "Garh Technologies Private Limited")
    monkeypatch.setenv("BILLING_SUPPLIER_GSTIN", KARNATAKA_GSTIN)
    supplier = gst.supplier_identity().require()
    # The state code defaults out of the GSTIN, so the two can never disagree.
    assert supplier.state_code == "29" and supplier.state == "Karnataka"


# ---------------------------------------------------------------------------
# The provider seam
# ---------------------------------------------------------------------------


def test_the_factory_defaults_to_the_mock_and_refuses_a_typo() -> None:
    """Two layers refuse ``PROVIDER_BILLING=razorpayy``, and neither serves the mock.

    The typed ``Literal`` in ``garh_api.config`` is the first: the process cannot even
    build a ``Settings``. The factory's own check is the second, proven here by
    ``model_construct``, which bypasses validation exactly as a future untyped caller
    would. A typo silently downgrading production to fake orders would mean customers
    "paying" and nothing arriving.
    """
    assert get_billing_provider(Settings(provider_billing="mock")).name == "mock"

    with pytest.raises(PydanticValidationError):
        Settings(provider_billing="razorpayy")  # type: ignore[arg-type]

    smuggled = Settings().model_copy(update={"provider_billing": "razorpayy"})
    with pytest.raises(ValueError, match="Unknown PROVIDER_BILLING"):
        get_billing_provider(smuggled)


def test_razorpay_without_credentials_refuses_to_be_built() -> None:
    """A half-configured gateway must stop the process, not take an unsettleable order."""
    with pytest.raises(ValueError, match="RAZORPAY_KEY_ID"):
        get_billing_provider(
            Settings(provider_billing="razorpay", razorpay_key_id="", razorpay_key_secret="")
        )


async def test_the_mock_signs_and_verifies_for_real() -> None:
    provider = MockBillingProvider()
    order = await provider.create_order(OrderRequest(amount_inr=5_899, receipt="G26270000001"))
    assert order.amount_paise == 589_900
    payment_id, signature = provider.simulate_payment(order.order_id)
    assert provider.verify_payment_signature(
        order_id=order.order_id, payment_id=payment_id, signature=signature
    )


async def test_the_mock_rejects_a_tampered_signature() -> None:
    """NEGATIVE CONTROL: the demo path is a real HMAC check, not a rubber stamp."""
    provider = MockBillingProvider()
    order = await provider.create_order(OrderRequest(amount_inr=100, receipt="G26270000002"))
    payment_id, signature = provider.simulate_payment(order.order_id)
    tampered = ("0" if signature[0] != "0" else "1") + signature[1:]
    assert not provider.verify_payment_signature(
        order_id=order.order_id, payment_id=payment_id, signature=tampered
    )
    # ...and a signature for one order does not settle another.
    assert not provider.verify_payment_signature(
        order_id="order_somebody_else", payment_id=payment_id, signature=signature
    )


async def test_the_mock_refuses_a_zero_amount_order() -> None:
    with pytest.raises(BillingProviderError):
        await MockBillingProvider().create_order(OrderRequest(amount_inr=0, receipt="G0"))


# ---------------------------------------------------------------------------
# Razorpay adapter, against a strict transport double
# ---------------------------------------------------------------------------


async def test_razorpay_create_order_sends_paise_and_our_invoice_number() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization", "")
        body = json.loads(request.content.decode())
        seen["body"] = body
        return httpx.Response(
            200,
            json={
                "id": "order_ABC123",
                "entity": "order",
                "amount": body["amount"],
                "currency": "INR",
                "receipt": body["receipt"],
                "status": "created",
            },
        )

    provider = _razorpay(httpx.MockTransport(handler))
    order = await provider.create_order(
        OrderRequest(
            amount_inr=5_899, receipt="G26270000001", notes={"firmId": "f", "invoiceId": "i"}
        )
    )
    await provider.aclose()

    assert seen["method"] == "POST"
    assert seen["url"] == "https://api.razorpay.com/v1/orders"
    # HTTP Basic over the key pair, exactly as Razorpay documents. Decoded rather than
    # merely "starts with Basic ", so a header built from the wrong pair fails here.
    expected = base64.b64encode(("%s:%s" % (FAKE_KEY_ID, FAKE_KEY_SECRET)).encode()).decode()
    assert seen["auth"] == "Basic " + expected
    body = seen["body"]
    assert isinstance(body, dict)
    # PAISE on the wire, rupees in our model. 5899 → 589900.
    assert body["amount"] == 589_900
    assert body["currency"] == "INR"
    assert body["receipt"] == "G26270000001"
    # Ids only in notes — no name, no email, no address leaves for a third party.
    assert set(body["notes"]) == {"firmId", "invoiceId"}
    assert order.order_id == "order_ABC123" and order.amount_paise == 589_900


async def test_razorpay_refuses_an_order_opened_for_a_different_amount() -> None:
    """NEGATIVE CONTROL: the gateway echoing a different amount must not be papered over."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "order_X",
                "amount": 1,
                "currency": "INR",
                "receipt": "G1",
                "status": "created",
            },
        )

    provider = _razorpay(httpx.MockTransport(handler))
    with pytest.raises(BillingProviderError, match="different amount"):
        await provider.create_order(OrderRequest(amount_inr=5_899, receipt="G1"))
    await provider.aclose()


async def test_razorpay_error_envelope_becomes_a_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "BAD_REQUEST_ERROR",
                    "description": "Order amount less than minimum amount allowed.",
                }
            },
        )

    provider = _razorpay(httpx.MockTransport(handler))
    with pytest.raises(BillingProviderError, match="minimum amount") as caught:
        await provider.create_order(OrderRequest(amount_inr=1, receipt="G1"))
    assert caught.value.status == 400 and caught.value.code == "BAD_REQUEST_ERROR"
    await provider.aclose()


async def test_razorpay_only_calls_a_payment_captured_when_it_really_is() -> None:
    """``authorized`` money is held, not ours. Only the pair (captured, status) settles."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == "https://api.razorpay.com/v1/payments/pay_1"
        return httpx.Response(
            200,
            json={
                "id": "pay_1",
                "order_id": "order_1",
                "amount": 589_900,
                "currency": "INR",
                "status": "authorized",
                "captured": False,
                "method": "upi",
            },
        )

    provider = _razorpay(httpx.MockTransport(handler))
    snapshot = await provider.fetch_payment("pay_1")
    await provider.aclose()
    assert snapshot.status == "authorized" and snapshot.captured is False
    assert snapshot.amount_paise == 589_900 and snapshot.method == "upi"


async def test_razorpay_capture_posts_paise() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "id": "pay_1",
                "order_id": "order_1",
                "amount": 589_900,
                "currency": "INR",
                "status": "captured",
                "captured": True,
            },
        )

    provider = _razorpay(httpx.MockTransport(handler))
    snapshot = await provider.capture_payment("pay_1", amount_inr=5_899)
    await provider.aclose()
    assert seen["url"] == "https://api.razorpay.com/v1/payments/pay_1/capture"
    assert seen["body"] == {"amount": 589_900, "currency": "INR"}
    assert snapshot.captured is True


def test_razorpay_payment_signature_is_the_documented_hmac() -> None:
    provider = _razorpay(httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    expected = hmac.new(FAKE_KEY_SECRET.encode(), b"order_1|pay_1", hashlib.sha256).hexdigest()
    assert provider.verify_payment_signature(
        order_id="order_1", payment_id="pay_1", signature=expected
    )
    # NEGATIVE CONTROL: one character off, and any empty field, must fail.
    assert not provider.verify_payment_signature(
        order_id="order_1", payment_id="pay_1", signature=expected[:-1] + "0"
    )
    assert not provider.verify_payment_signature(
        order_id="order_1", payment_id="pay_1", signature=""
    )
    # A signature minted for a different order does not settle this one.
    other = hmac.new(FAKE_KEY_SECRET.encode(), b"order_2|pay_1", hashlib.sha256).hexdigest()
    assert not provider.verify_payment_signature(
        order_id="order_1", payment_id="pay_1", signature=other
    )


def test_webhook_verification_fails_closed_without_its_own_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unconfigured webhook secret means "trust nothing", never "trust everything"."""
    provider = _razorpay(httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    body = b'{"event":"payment.captured"}'

    monkeypatch.delenv(WEBHOOK_SECRET_ENV, raising=False)
    # Even a signature computed with the KEY secret must not be accepted here.
    key_signed = hmac.new(FAKE_KEY_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert provider.verify_webhook_signature(body=body, signature=key_signed) is False

    monkeypatch.setenv(WEBHOOK_SECRET_ENV, "not-a-real-webhook-secret")
    correct = hmac.new(b"not-a-real-webhook-secret", body, hashlib.sha256).hexdigest()
    assert provider.verify_webhook_signature(body=body, signature=correct) is True
    assert provider.verify_webhook_signature(body=body + b" ", signature=correct) is False


# ---------------------------------------------------------------------------
# Documentation that is executable, because a wrong instruction is a defect
# ---------------------------------------------------------------------------


def test_require_quota_is_mounted_bare_and_the_documented_form_is_the_working_one() -> None:
    """``require_quota`` already returns ``Depends(...)``; wrapping it again raises.

    Both ``quotas.py``'s module docstring and ``require_quota``'s own used to show
    ``dependencies=[Depends(require_quota("render"))]``, which is not a slow-burn
    correctness issue — it is an instruction that fails at import of any router that
    follows it. This asserts the working form works, the documented-wrong form does not,
    and that neither docstring has drifted back to it.
    """
    from fastapi import APIRouter, Depends, params
    from garh_api.billing.quotas import require_quota

    assert isinstance(require_quota("render"), params.Depends)

    good = APIRouter()

    @good.post("/probe", dependencies=[require_quota("render")])
    async def _probe() -> dict[str, bool]:
        return {"ok": True}

    assert good.routes, "the documented form did not even register a route"

    bad = APIRouter()
    with pytest.raises(AssertionError, match="callable dependency"):

        @bad.post("/probe", dependencies=[Depends(require_quota("render"))])
        async def _bad_probe() -> dict[str, bool]:
            return {"ok": True}

    from garh_api.billing import quotas

    for text in (quotas.__doc__ or "", require_quota.__doc__ or ""):
        assert (
            "dependencies=[Depends(require_quota" not in text
        ), "a docstring is telling the next reader to write the form that raises"


def test_every_module_in_the_package_points_at_a_test_file_that_exists() -> None:
    """A docstring naming ``tests/test_billing_provider.py`` — which does not exist — is
    a dead pointer, and the reader who follows it concludes the code is untested.

    Cheap to check and impossible to keep true by diligence, so it is checked: every
    ``tests/test_*.py`` path mentioned anywhere in ``garh_api/billing`` must be a real
    file.
    """
    import re
    from pathlib import Path

    package = Path(__file__).resolve().parent.parent / "garh_api" / "billing"
    tests_dir = Path(__file__).resolve().parent
    pattern = re.compile(r"tests/(test_[A-Za-z0-9_]+\.py)")

    dangling: list[str] = []
    checked = 0
    for path in sorted(package.glob("*.py")):
        for name in pattern.findall(path.read_text(encoding="utf-8")):
            checked += 1
            if not (tests_dir / name).is_file():
                dangling.append("%s -> tests/%s" % (path.name, name))
    assert checked, "no test-file references found at all — this guard would be vacuous"
    assert not dangling, dangling
