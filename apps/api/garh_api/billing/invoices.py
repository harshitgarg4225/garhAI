"""Issuing a GST tax invoice for a billing period (G-3).

The output of this module is the document an Indian practice puts through its books.
Everything it needs is assembled here and then frozen onto the row: supplier identity,
customer identity, place of supply, per-line HSN/SAC, taxable value, the CGST+SGST or
IGST split, and a Rule 46(b) serial number.

FOUR REFUSALS, EACH DELIBERATE
------------------------------
An invoice is not issued when:

1. **this deployment has no GST registration** — issuing a document that looks like a
   tax invoice without a supplier GSTIN would be worse than issuing nothing, because
   the customer would file against it (503);
2. **the firm has no billing profile** — no legal name and no state means no place of
   supply, and place of supply is what decides CGST/SGST vs IGST (409);
3. **the plan is free** — there is nothing to invoice, and a ₹0 tax invoice is not a
   thing (409);
4. **the period already has an invoice** — Rule 46 numbering is consecutive and an
   invoice is corrected by a credit note, never by issuing a second one (409).

Every amount is a whole rupee (:mod:`garh_api.billing.money`); the arithmetic identity
``total = taxable + cgst + sgst + igst`` is enforced by :func:`~garh_api.billing.gst.
compute_tax`, again by the CHECK constraint on the table, and asserted a third time by
the tests. Three times because it is the one number a customer's accountant checks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from garh_api.billing.errors import (
    BillingProfileIncompleteError,
    BillingUnavailableError,
    InvoiceStateError,
)
from garh_api.billing.gst import (
    GST_RATE_X100,
    SUBSCRIPTION_SAC,
    GstError,
    SupplierIdentity,
    compute_tax,
    invoice_number,
    supplier_identity,
)
from garh_api.billing.repositories import (
    BillingAccountRepository,
    Invoice,
    InvoiceRepository,
)
from garh_api.billing.subscriptions import Entitlement, ensure_subscription, entitlement
from garh_api.logging import get_logger
from garh_api.tenancy import TenantCtx

_log = get_logger(__name__)


def subscription_lines(ent: Entitlement) -> list[dict[str, Any]]:
    """The invoice lines for one period of a subscription.

    Two at most: the plan, and the extra seats if any were bought. Seats are a separate
    line rather than folded into the plan price because that is how the customer bought
    them and how they will query the bill ("why is this ₹3,000 more than last month").

    Keys are camelCase — the wire casing this codebase uses everywhere — so the response
    schema maps them without a translation table.
    """
    plan = ent.plan
    lines: list[dict[str, Any]] = [
        {
            "description": "Garh AI — %s plan (%s to %s)"
            % (
                plan.name,
                ent.period_start.date().isoformat(),
                ent.period_end.date().isoformat(),
            ),
            "hsnSac": SUBSCRIPTION_SAC,
            "quantity": 1,
            "unitPriceInr": plan.price_inr_per_month,
            "amountInr": plan.price_inr_per_month,
        }
    ]
    seat_price = plan.extra_seat_inr_per_month
    if seat_price and ent.extra_seats > 0:
        lines.append(
            {
                "description": "Additional editor seats",
                "hsnSac": SUBSCRIPTION_SAC,
                "quantity": ent.extra_seats,
                "unitPriceInr": seat_price,
                "amountInr": seat_price * ent.extra_seats,
            }
        )
    return lines


def _require_supplier() -> SupplierIdentity:
    try:
        return supplier_identity().require()
    except GstError as exc:
        # A configuration gap, not a customer error: 503 with the operator's message
        # kept out of the customer-facing text.
        _log.error("billing.supplier_not_configured", reason=str(exc))
        raise BillingUnavailableError("Invoices can't be issued from this deployment yet.") from exc


async def issue_for_current_period(
    session: AsyncSession,
    ctx: TenantCtx,
    *,
    provider_name: str = "mock",
    now: datetime | None = None,
) -> Invoice:
    """Issue (or refuse to issue) the invoice covering the firm's current period."""
    ctx.require_admin("issuing an invoice")
    moment = now or datetime.now(UTC)
    supplier = _require_supplier()

    account = await BillingAccountRepository(session, ctx).get_for_firm()
    if account is None:
        raise BillingProfileIncompleteError()

    # ``ensure_subscription`` rather than ``entitlement`` alone: issuing an invoice is a
    # write path, and the row it creates is what the payment will later be attached to.
    await ensure_subscription(session, ctx, provider_name=provider_name, now=moment)
    ent = await entitlement(session, ctx, now=moment)

    charge_inr = ent.monthly_charge_inr
    if charge_inr <= 0:
        raise InvoiceStateError(
            "There's nothing to invoice on the %s plan." % ent.plan.name,
            action="Move to a paid plan first.",
        )

    repo = InvoiceRepository(session, ctx)
    existing = await repo.find_for_period(ent.period_start)
    if existing is not None:
        raise InvoiceStateError(
            "Invoice %s already covers this period." % existing.invoice_number,
            extra={"invoiceId": str(existing.id), "invoiceNumber": existing.invoice_number},
        )

    lines = subscription_lines(ent)
    taxable = sum(int(line["amountInr"]) for line in lines)
    if taxable != charge_inr:
        # The lines and the plan arithmetic are two views of one number; if they ever
        # disagree the invoice is wrong in a way nobody would notice downstream.
        raise InvoiceStateError(
            "The invoice lines do not add up to the plan charge — refusing to issue."
        )

    tax = compute_tax(
        taxable,
        supplier_state_code=supplier.state_code,
        place_of_supply_code=account.state_code,
    )

    issued_on = moment.astimezone(UTC).date()
    sequence = await repo.count_in_fiscal_year(issued_on) + 1
    number = invoice_number(firm_id=ctx.firm_id, issued_on=issued_on, sequence=sequence)

    invoice = await repo.create(
        invoice_number=number,
        status="issued",
        issued_on=issued_on,
        period_start=ent.period_start,
        period_end=ent.period_end,
        supplier_legal_name=supplier.legal_name,
        supplier_gstin=supplier.gstin,
        supplier_state_code=supplier.state_code,
        supplier_address=supplier.address,
        customer_legal_name=account.legal_name,
        customer_gstin=account.gstin,
        customer_address=account.postal_address,
        place_of_supply_code=tax.place_of_supply_code,
        interstate=tax.interstate,
        currency="INR",
        taxable_inr=tax.taxable_inr,
        cgst_inr=tax.cgst_inr,
        sgst_inr=tax.sgst_inr,
        igst_inr=tax.igst_inr,
        total_inr=tax.total_inr,
        rate_percent_x100=GST_RATE_X100,
        lines=lines,
        meta={"planCode": ent.plan.code, "extraSeats": ent.extra_seats},
    )
    _log.info(
        "billing.invoice_issued",
        invoice_number=number,
        total_inr=invoice.total_inr,
        interstate=tax.interstate,
    )
    return invoice


__all__ = ["issue_for_current_period", "subscription_lines"]
