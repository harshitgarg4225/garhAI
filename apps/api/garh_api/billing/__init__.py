"""Billing: the part of the product that takes money (G-1 … G-4).

Before this package, the meter ran and nothing billed against it — ``credit_events`` had
a row for every solve, render, export and LLM call since the first commit, and
``PROVIDER_BILLING=razorpay`` was a config enum value with no implementation behind it.

What is here, and where:

=========================  ====================================================
:mod:`~garh_api.billing.money`          whole-rupee integer arithmetic; no floats
:mod:`~garh_api.billing.plans`          the plan catalogue and its allowances (G-2)
:mod:`~garh_api.billing.gst`            GSTIN, place of supply, CGST/SGST vs IGST (G-3)
:mod:`~garh_api.billing.models`         the five tables, on their own ``MetaData``
:mod:`~garh_api.billing.repositories`   firm-scoped access, built only from the
                                        tenancy layer's own helpers
:mod:`~garh_api.billing.subscriptions`  the ONE answer to "what may this firm do"
:mod:`~garh_api.billing.quotas`         usage vs allowance, and the deny (G-2)
:mod:`~garh_api.billing.seats`          who holds a paid seat (G-4)
:mod:`~garh_api.billing.invoices`       issuing a Rule-46 tax invoice (G-3)
:mod:`~garh_api.billing.payments`       opening and settling a gateway order (G-1)
:mod:`~garh_api.billing.provider`       the mock/razorpay seam, same shape as LLM/render
=========================  ====================================================

Three properties hold across all of it:

* **money is whole rupees, as ``int``.** The only conversion is
  :func:`~garh_api.billing.money.rupees_to_paise`, at the gateway boundary, and it is an
  exact multiply. No float touches an amount anywhere in this package.
* **the mock is the default.** ``PROVIDER_BILLING=mock`` runs the whole flow — order,
  signature, settle — with zero keys, so the pay-an-invoice journey is e2e-testable and
  demoable exactly like the render and LLM paths.
* **nothing here queries a table directly.** Every read and write goes through a
  :class:`~garh_api.tenancy.Repository` subclass built from the tenancy layer's own
  firm-scoped helpers, so a cross-tenant read is not something this package can express.

Imports are kept shallow on purpose: this module re-exports names but pulls in no
provider SDK and no router, so ``import garh_api.billing`` stays cheap.
"""

from __future__ import annotations

from garh_api.billing.errors import (
    BillingProfileIncompleteError,
    BillingUnavailableError,
    InvalidGstDetailsError,
    InvoiceStateError,
    PaymentVerificationError,
    PlanChangeError,
    QuotaExceededError,
    SeatLimitError,
)
from garh_api.billing.models import BILLING_METADATA, BILLING_TABLES
from garh_api.billing.plans import PLAN_CODES, PLANS, Plan, plan_for
from garh_api.billing.provider import BillingProvider, get_billing_provider
from garh_api.billing.quotas import QuotaLine, check_quota, require_quota, usage_lines
from garh_api.billing.subscriptions import Entitlement, entitlement

__all__ = [
    "BILLING_METADATA",
    "BILLING_TABLES",
    "PLANS",
    "PLAN_CODES",
    "BillingProfileIncompleteError",
    "BillingProvider",
    "BillingUnavailableError",
    "Entitlement",
    "InvalidGstDetailsError",
    "InvoiceStateError",
    "PaymentVerificationError",
    "Plan",
    "PlanChangeError",
    "QuotaExceededError",
    "QuotaLine",
    "SeatLimitError",
    "check_quota",
    "entitlement",
    "get_billing_provider",
    "plan_for",
    "require_quota",
    "usage_lines",
]
