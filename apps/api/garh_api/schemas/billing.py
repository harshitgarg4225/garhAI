"""Request/response schemas for ``/billing/**`` (G-1 … G-4).

Conventions from ``schemas/__init__.py`` hold: camelCase on the wire, ``extra="forbid"``
on every request, ``StrictInt`` for numbers.

Money on the wire
-----------------
Every amount is a ``StrictInt`` in **whole rupees**, suffixed ``Inr`` so a client cannot
mistake it for paise. The one exception is ``amountPaise`` in the checkout response,
which is what the Razorpay widget must be handed verbatim; it carries its own suffix and
sits beside ``amountInr`` so the two are visibly the same money in two units.

``StrictInt`` matters more here than elsewhere: it rejects ``4999.0``. A float that
survived to the wire would mean somewhere upstream money became a float, and the point
of this package is that it never does.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import Field, StrictBool, StrictInt, StrictStr

from garh_api.schemas import CamelModel, CursorPage, ResponseModel

# ---------------------------------------------------------------------------
# Plans and entitlement
# ---------------------------------------------------------------------------


class PlanAllowanceOut(ResponseModel):
    """One metered kind on a plan. ``null`` allowance = unmetered, ``0`` = not included."""

    kind: StrictStr
    allowance: StrictInt | None = None


class PlanOut(ResponseModel):
    code: StrictStr
    name: StrictStr
    price_inr_per_month: StrictInt
    included_editor_seats: StrictInt
    extra_seat_inr_per_month: StrictInt | None = None
    summary: StrictStr
    allowances: list[PlanAllowanceOut] = Field(default_factory=list)


class PlanListOut(ResponseModel):
    plans: list[PlanOut] = Field(default_factory=list)
    #: Which of the above the caller's firm is on right now.
    current_plan_code: StrictStr


class SubscriptionOut(ResponseModel):
    plan_code: StrictStr
    plan_name: StrictStr
    #: The plan whose ALLOWANCES apply. Differs from ``planCode`` when a subscription is
    #: past due — the firm keeps its seats and drops to free-tier spend.
    effective_plan_code: StrictStr
    status: StrictStr
    current_period_start: datetime
    current_period_end: datetime
    extra_seats: StrictInt
    seats_entitled: StrictInt
    cancel_at_period_end: StrictBool
    monthly_charge_inr: StrictInt
    provider: StrictStr


class SubscriptionUpdateIn(CamelModel):
    """Change the plan and/or the number of purchased extra seats."""

    plan_code: StrictStr | None = Field(default=None, max_length=32)
    extra_seats: StrictInt | None = Field(default=None, ge=0, le=500)
    cancel_at_period_end: StrictBool | None = None


# ---------------------------------------------------------------------------
# Usage (G-2)
# ---------------------------------------------------------------------------


class UsageLineOut(ResponseModel):
    kind: StrictStr
    used: StrictInt
    #: ``null`` = unmetered on this plan.
    allowance: StrictInt | None = None
    remaining: StrictInt | None = None


class UsageOut(ResponseModel):
    plan_code: StrictStr
    effective_plan_code: StrictStr
    period_start: datetime
    period_end: datetime
    lines: list[UsageLineOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Billing account (G-3)
# ---------------------------------------------------------------------------


class BillingAccountOut(ResponseModel):
    legal_name: StrictStr
    gstin: StrictStr | None = None
    state_code: StrictStr
    state_name: StrictStr
    address_line: StrictStr
    city: StrictStr
    postal_code: StrictStr
    billing_email: StrictStr


class BillingAccountIn(CamelModel):
    """The firm's GST identity.

    ``gstin`` is optional because an unregistered practice is a real customer.
    ``stateCode`` is not: it is the place of supply, and there is no safe default.
    """

    legal_name: StrictStr = Field(min_length=1, max_length=200)
    state_code: StrictStr = Field(min_length=1, max_length=2)
    gstin: StrictStr | None = Field(default=None, max_length=20)
    address_line: StrictStr = Field(default="", max_length=300)
    city: StrictStr = Field(default="", max_length=120)
    postal_code: StrictStr = Field(default="", max_length=12)
    billing_email: StrictStr = Field(default="", max_length=254)


class GstStateOut(ResponseModel):
    code: StrictStr
    name: StrictStr


# ---------------------------------------------------------------------------
# Invoices (G-3)
# ---------------------------------------------------------------------------


class InvoiceLineOut(ResponseModel):
    description: StrictStr
    #: Rule 46 requires the HSN or SAC per line, not per invoice.
    hsn_sac: StrictStr
    quantity: StrictInt
    unit_price_inr: StrictInt
    amount_inr: StrictInt


class InvoiceOut(ResponseModel):
    """Every Rule 46 field, so the client can render a compliant document."""

    id: uuid.UUID
    invoice_number: StrictStr
    status: StrictStr
    issued_on: date
    period_start: datetime
    period_end: datetime

    supplier_legal_name: StrictStr
    supplier_gstin: StrictStr
    supplier_state_code: StrictStr
    supplier_address: StrictStr

    customer_legal_name: StrictStr
    customer_gstin: StrictStr | None = None
    customer_address: StrictStr
    place_of_supply_code: StrictStr
    place_of_supply: StrictStr

    interstate: StrictBool
    currency: StrictStr
    rate_percent_x100: StrictInt
    taxable_inr: StrictInt
    cgst_inr: StrictInt
    sgst_inr: StrictInt
    igst_inr: StrictInt
    tax_total_inr: StrictInt
    total_inr: StrictInt
    total_in_words: StrictStr

    lines: list[InvoiceLineOut] = Field(default_factory=list)
    paid_at: datetime | None = None


class InvoicePage(CursorPage[InvoiceOut]):
    """Cursor page of invoices, newest first."""


# ---------------------------------------------------------------------------
# Payments (G-1)
# ---------------------------------------------------------------------------


class CheckoutOut(ResponseModel):
    """What the browser hands to the provider's checkout widget."""

    invoice_id: uuid.UUID
    invoice_number: StrictStr
    provider: StrictStr
    order_id: StrictStr
    amount_inr: StrictInt
    #: The same money in the gateway's unit. Razorpay's widget takes paise.
    amount_paise: StrictInt
    currency: StrictStr
    #: Publishable key id. Never the key secret.
    key_id: StrictStr


class PaymentVerifyIn(CamelModel):
    """The three values the provider's checkout handler returns to the browser."""

    order_id: StrictStr = Field(min_length=1, max_length=100)
    payment_id: StrictStr = Field(min_length=1, max_length=100)
    signature: StrictStr = Field(min_length=1, max_length=256)


class PaymentOut(ResponseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    provider: StrictStr
    provider_order_id: StrictStr
    provider_payment_id: StrictStr | None = None
    status: StrictStr
    amount_inr: StrictInt
    currency: StrictStr
    signature_verified: StrictBool


class PaymentSettledOut(ResponseModel):
    payment: PaymentOut
    invoice: InvoiceOut


# ---------------------------------------------------------------------------
# Seats (G-4)
# ---------------------------------------------------------------------------


class SeatOut(ResponseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    seat_type: StrictStr
    assigned_by: uuid.UUID | None = None
    created_at: datetime


class SeatListOut(ResponseModel):
    entitled: StrictInt
    editors_used: StrictInt
    viewers_used: StrictInt
    available: StrictInt
    seats: list[SeatOut] = Field(default_factory=list)


class SeatAssignIn(CamelModel):
    user_id: uuid.UUID
    #: ``editor`` (consumes a paid seat) or ``viewer`` (free).
    seat_type: StrictStr = Field(default="editor", max_length=16)


__all__ = [
    "BillingAccountIn",
    "BillingAccountOut",
    "CheckoutOut",
    "GstStateOut",
    "InvoiceLineOut",
    "InvoiceOut",
    "InvoicePage",
    "PaymentOut",
    "PaymentSettledOut",
    "PaymentVerifyIn",
    "PlanAllowanceOut",
    "PlanListOut",
    "PlanOut",
    "SeatAssignIn",
    "SeatListOut",
    "SeatOut",
    "SubscriptionOut",
    "SubscriptionUpdateIn",
    "UsageLineOut",
    "UsageOut",
]
