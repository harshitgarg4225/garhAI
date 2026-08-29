"""Collecting an issued invoice through the payment provider (G-1).

The flow, which is Razorpay's documented checkout-handler flow and works identically
under the mock:

1. ``open_checkout`` — the firm's admin asks to pay invoice X. We create a provider
   order for exactly the invoice total and record a ``billing_payments`` row in
   ``created``. The response carries the order id and the *publishable* key id, which
   is what the browser widget needs. The key **secret** never leaves the server.
2. the customer pays in the widget, which hands the browser back
   ``razorpay_order_id``, ``razorpay_payment_id`` and ``razorpay_signature``;
3. ``settle_payment`` — the browser posts those three back. We verify the signature
   server-side, and only then mark the payment captured and the invoice paid.

WHY THE SIGNATURE IS THE WHOLE SECURITY STORY
---------------------------------------------
Step 3 arrives from a browser, which means every field in it is attacker-controlled.
The signature is HMAC-SHA256 over ``order_id|payment_id`` keyed with our key secret,
which only Razorpay and we hold — so a forged pair cannot be signed. Three things
follow, and all three are enforced below:

* the order id must belong to a payment row **of the caller's firm** (the repository is
  firm-scoped, so quoting another firm's order id finds nothing);
* the signature must verify, by constant-time comparison, before anything is written;
* the amount is never taken from the request. It is the invoice's own total.

No webhook endpoint is mounted. A webhook is unauthenticated by nature and would have
to resolve a firm from provider metadata *before* any tenant context exists — the one
shape this codebase's tenancy model has no safe answer for yet. The handler flow above
covers the paying customer; a webhook for out-of-band events (a payment completed after
the browser closed) is listed in the handoff as the next step, and wants its own
design for tenant resolution.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from garh_api.billing.errors import (
    BillingUnavailableError,
    InvoiceStateError,
    PaymentVerificationError,
)
from garh_api.billing.money import rupees_to_paise
from garh_api.billing.provider import BillingProvider
from garh_api.billing.repositories import (
    Invoice,
    InvoiceRepository,
    Payment,
    PaymentRepository,
)
from garh_api.billing.types import BillingProviderError, OrderRequest
from garh_api.logging import get_logger
from garh_api.tenancy import TenantCtx

_log = get_logger(__name__)


@dataclass(frozen=True)
class Checkout:
    """What the browser needs to open the provider's payment widget."""

    invoice_id: uuid.UUID
    invoice_number: str
    provider: str
    order_id: str
    amount_inr: int
    amount_paise: int
    currency: str
    #: The *publishable* key. Razorpay's checkout script needs it in the browser; it is
    #: not a secret and is safe in a response. The key secret is server-only.
    key_id: str


def _checkout_of(
    invoice: Invoice, payment: Payment, *, key_id: str, amount_paise: int | None = None
) -> Checkout:
    """The widget payload for an invoice and the payment row that holds its order.

    One constructor for both paths — the freshly opened order and the reused one — so a
    re-checkout cannot answer with a differently shaped body than the first checkout did.
    The amount is the *payment row's*, which is the amount the gateway holds against that
    order id; ``amount_paise`` is passed through from the provider when we have just
    heard from it, and derived exactly otherwise (a multiply, never a rounded conversion).
    """
    return Checkout(
        invoice_id=invoice.id,
        invoice_number=invoice.invoice_number,
        provider=payment.provider,
        order_id=payment.provider_order_id,
        amount_inr=payment.amount_inr,
        amount_paise=rupees_to_paise(payment.amount_inr) if amount_paise is None else amount_paise,
        currency=payment.currency,
        key_id=key_id,
    )


async def open_checkout(
    session: AsyncSession,
    ctx: TenantCtx,
    *,
    invoice_id: uuid.UUID,
    provider: BillingProvider,
    key_id: str,
) -> Checkout:
    """Open a provider order for an issued invoice, and record the attempt.

    **Idempotent.** Asking twice for the same unpaid invoice returns the order the first
    call opened; it does not open a second one. That is both the correct semantics (one
    invoice, one order — a customer who reloads the checkout page has not started a
    second payment) and the fix for a real 500: ``provider_order_id`` is globally unique
    and the mock provider derives it deterministically from the invoice number, so a
    second insert violated the constraint and the ``IntegrityError`` escaped as an
    unhandled server error.
    """
    ctx.require_admin("paying an invoice")
    invoices = InvoiceRepository(session, ctx)
    invoice = await invoices.require(invoice_id)
    if invoice.status == "paid":
        raise InvoiceStateError(
            "Invoice %s is already paid." % invoice.invoice_number,
            action="Nothing to do — download the receipt instead.",
        )
    if invoice.status != "issued":
        raise InvoiceStateError(
            "Invoice %s is %s, so it can't be paid." % (invoice.invoice_number, invoice.status)
        )

    payments = PaymentRepository(session, ctx)
    open_attempt = await payments.open_attempt_for_invoice(invoice.id)
    if open_attempt is not None:
        _log.info(
            "billing.checkout_reused",
            invoice_number=invoice.invoice_number,
            provider=open_attempt.provider,
        )
        return _checkout_of(invoice, open_attempt, key_id=key_id)

    try:
        order = await provider.create_order(
            OrderRequest(
                amount_inr=invoice.total_inr,
                receipt=invoice.invoice_number,
                # Ids only. Never a name, an email or an address — a third party gets
                # no PII it has no use for (§13).
                notes={"firmId": str(ctx.firm_id), "invoiceId": str(invoice.id)},
            )
        )
    except BillingProviderError as exc:
        _log.warning("billing.order_failed", invoice_number=invoice.invoice_number)
        raise BillingUnavailableError(str(exc)) from exc

    # Second line of defence on the same unique constraint: a deterministic provider can
    # hand back an order id we already hold on a row that is no longer ``created`` (a
    # dead attempt), and a concurrent first click can have inserted between the lookup
    # above and here. Either way, reuse rather than let the insert raise.
    existing = await payments.by_order_id(order.order_id)
    if existing is not None:
        _log.info(
            "billing.checkout_reused",
            invoice_number=invoice.invoice_number,
            provider=existing.provider,
        )
        return _checkout_of(invoice, existing, key_id=key_id)

    attempt = await payments.create(
        invoice_id=invoice.id,
        provider=order.provider,
        provider_order_id=order.order_id,
        amount_inr=invoice.total_inr,
    )
    _log.info(
        "billing.checkout_opened",
        invoice_number=invoice.invoice_number,
        provider=order.provider,
    )
    return _checkout_of(invoice, attempt, key_id=key_id, amount_paise=order.amount_paise)


async def settle_payment(
    session: AsyncSession,
    ctx: TenantCtx,
    *,
    order_id: str,
    payment_id: str,
    signature: str,
    provider: BillingProvider,
    now: datetime | None = None,
) -> tuple[Payment, Invoice]:
    """Verify the provider's signature, then mark the payment and invoice settled.

    Order of operations is the point: **verify first, write second**. A failed
    verification writes nothing at all — not even a "failed" status — because the row
    it would write is keyed on values an unauthenticated forger supplied.
    """
    ctx.require_admin("recording a payment")
    payments = PaymentRepository(session, ctx)

    # Firm-scoped: another firm's order id resolves to None here, which is why a
    # cross-tenant settle attempt is indistinguishable from a wrong signature.
    payment = await payments.by_order_id((order_id or "").strip())
    if payment is None:
        raise PaymentVerificationError()

    if not provider.verify_payment_signature(
        order_id=order_id, payment_id=payment_id, signature=signature
    ):
        _log.warning("billing.signature_rejected", provider=payment.provider)
        raise PaymentVerificationError()

    moment = now or datetime.now(UTC)
    settled = await payments.mark(
        payment.id,
        status="captured",
        provider_payment_id=(payment_id or "").strip(),
        signature_verified=True,
    )
    invoices = InvoiceRepository(session, ctx)
    invoice = await invoices.set_status(payment.invoice_id, status="paid", paid_at=moment)
    _log.info(
        "billing.invoice_paid",
        invoice_number=invoice.invoice_number,
        amount_inr=settled.amount_inr,
    )
    return settled, invoice


__all__ = ["Checkout", "open_checkout", "settle_payment"]
