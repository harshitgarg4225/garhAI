"""``/billing/**`` — plans, quotas, GST invoices, payments and seats (G-1 … G-4).

Shape of this module: it is thin. Every decision lives in :mod:`garh_api.billing` —
the plan catalogue, the quota arithmetic, the GST split, the seat gate — and the
handlers below resolve a tenant, call one service function, and map the result to a
schema. That is deliberate: the same gates have to hold when a scheduled job issues
invoices with no HTTP request in sight, so none of them may live in a handler.

WHO MAY DO WHAT
---------------
Reads (``plans``, ``subscription``, ``usage``, ``invoices``, ``seats``) are open to any
signed-in member of the firm — an architect should be able to see how many renders are
left without being an admin. Every *write*, and the billing account (which carries the
firm's GST identity and its address), is admin-only. Both layers enforce it: the route
takes ``AdminDep``, and the repository calls ``ctx.require_admin`` again, because the
repository is what a future worker will call.

NO SHARE-LINK SURFACE
---------------------
Nothing here is reachable by a ``share_viewer``. A client with a link to a scheme has
no business seeing what the practice pays for software, and the routes take
``TenantDep``/``AdminDep``, which a share context cannot satisfy.

MOUNTING
--------
``garh_api.routers.api_router`` includes this router, so every route below is live under
``/api/v1/billing/**`` in the real app. ``tests/test_billing_api.py`` drives that same
app, and ``tests/test_cross_tenant.py`` now sees the three routes here that carry a
tenant-owned path parameter (``invoice_id``, ``seat_id``) — being reachable is what makes
that coverage guard able to fail.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from garh_api.billing import invoices as invoice_service
from garh_api.billing import payments as payment_service
from garh_api.billing import seats as seat_service
from garh_api.billing.errors import (
    BillingProfileIncompleteError,
    InvalidGstDetailsError,
    PlanChangeError,
)
from garh_api.billing.gst import (
    GST_STATE_CODES,
    GstError,
    gstin_state_code,
    state_name,
    validate_gstin,
    validate_state_code,
)
from garh_api.billing.money import amount_in_words
from garh_api.billing.plans import PLANS, QUOTA_KINDS, plan_for, seat_entitlement
from garh_api.billing.provider import get_billing_provider
from garh_api.billing.quotas import usage_lines
from garh_api.billing.repositories import (
    BillingAccount,
    BillingAccountRepository,
    Invoice,
    InvoiceRepository,
    Payment,
    SeatRepository,
    SubscriptionRepository,
)
from garh_api.billing.subscriptions import Entitlement, ensure_subscription, entitlement
from garh_api.config import Settings, get_settings
from garh_api.routers import AdminDep, PageDep, SessionDep, TenantDep, not_found
from garh_api.schemas.billing import (
    BillingAccountIn,
    BillingAccountOut,
    CheckoutOut,
    GstStateOut,
    InvoiceOut,
    InvoicePage,
    PaymentOut,
    PaymentSettledOut,
    PaymentVerifyIn,
    PlanAllowanceOut,
    PlanListOut,
    PlanOut,
    SeatAssignIn,
    SeatListOut,
    SeatOut,
    SpendBudgetOut,
    SubscriptionOut,
    SubscriptionUpdateIn,
    UsageLineOut,
    UsageOut,
)

router = APIRouter(prefix="/billing", tags=["billing"])


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


def _plan_out(code: str) -> PlanOut:
    plan = plan_for(code)
    return PlanOut(
        code=plan.code,
        name=plan.name,
        price_inr_per_month=plan.price_inr_per_month,
        included_editor_seats=plan.included_editor_seats,
        extra_seat_inr_per_month=plan.extra_seat_inr_per_month,
        summary=plan.summary,
        allowances=[
            PlanAllowanceOut(kind=kind, allowance=plan.allowance(kind)) for kind in QUOTA_KINDS
        ],
    )


def _subscription_out(ent: Entitlement) -> SubscriptionOut:
    return SubscriptionOut(
        plan_code=ent.plan.code,
        plan_name=ent.plan.name,
        effective_plan_code=ent.effective_plan.code,
        status=ent.status,
        current_period_start=ent.period_start,
        current_period_end=ent.period_end,
        extra_seats=ent.extra_seats,
        seats_entitled=ent.seats_entitled,
        cancel_at_period_end=(ent.subscription.cancel_at_period_end if ent.subscription else False),
        monthly_charge_inr=ent.monthly_charge_inr,
        provider=ent.subscription.provider if ent.subscription else "mock",
    )


def _invoice_out(invoice: Invoice) -> InvoiceOut:
    return InvoiceOut(
        id=invoice.id,
        invoice_number=invoice.invoice_number,
        status=invoice.status,
        issued_on=invoice.issued_on,
        period_start=invoice.period_start,
        period_end=invoice.period_end,
        supplier_legal_name=invoice.supplier_legal_name,
        supplier_gstin=invoice.supplier_gstin,
        supplier_state_code=invoice.supplier_state_code,
        supplier_address=invoice.supplier_address,
        customer_legal_name=invoice.customer_legal_name,
        customer_gstin=invoice.customer_gstin,
        customer_address=invoice.customer_address,
        place_of_supply_code=invoice.place_of_supply_code,
        place_of_supply=state_name(invoice.place_of_supply_code),
        interstate=invoice.interstate,
        currency=invoice.currency,
        rate_percent_x100=invoice.rate_percent_x100,
        taxable_inr=invoice.taxable_inr,
        cgst_inr=invoice.cgst_inr,
        sgst_inr=invoice.sgst_inr,
        igst_inr=invoice.igst_inr,
        tax_total_inr=invoice.tax_total_inr,
        total_inr=invoice.total_inr,
        total_in_words=amount_in_words(invoice.total_inr),
        lines=invoice.lines,
        paid_at=invoice.paid_at,
    )


def _payment_out(payment: Payment) -> PaymentOut:
    return PaymentOut(
        id=payment.id,
        invoice_id=payment.invoice_id,
        provider=payment.provider,
        provider_order_id=payment.provider_order_id,
        provider_payment_id=payment.provider_payment_id,
        status=payment.status,
        amount_inr=payment.amount_inr,
        currency=payment.currency,
        signature_verified=payment.signature_verified,
    )


# ---------------------------------------------------------------------------
# Plans, subscription and usage (G-2)
# ---------------------------------------------------------------------------


@router.get("/plans", response_model=PlanListOut, summary="The price list")
async def list_plans(session: SessionDep, ctx: TenantDep) -> PlanListOut:
    ent = await entitlement(session, ctx)
    return PlanListOut(
        plans=[_plan_out(plan.code) for plan in PLANS],
        current_plan_code=ent.plan.code,
    )


@router.get("/states", response_model=list[GstStateOut], summary="GST state codes")
async def list_states(ctx: TenantDep) -> list[GstStateOut]:
    """The place-of-supply picker's options.

    Served from the same table :func:`garh_api.billing.gst.validate_state_code` validates
    against, so the client cannot offer a code the server will reject.
    """
    return [GstStateOut(code=code, name=name) for code, name in sorted(GST_STATE_CODES.items())]


@router.get("/subscription", response_model=SubscriptionOut, summary="The current plan")
async def get_subscription(session: SessionDep, ctx: TenantDep) -> SubscriptionOut:
    return _subscription_out(await entitlement(session, ctx))


@router.put("/subscription", response_model=SubscriptionOut, summary="Change plan or seats")
async def update_subscription(
    body: SubscriptionUpdateIn, session: SessionDep, ctx: AdminDep
) -> SubscriptionOut:
    """Move plans or buy/return extra seats.

    Refuses a change that would leave more editor seats assigned than the new plan pays
    for (409, naming the number), rather than revoking somebody's access by row order.

    KNOWN GAP, stated rather than implied: the new plan takes effect **immediately** and
    is billed on the next invoice — there is no pay-first gate, and no proration of the
    part-period already elapsed. That is fine while every customer is invoiced and
    chased by a human, and it is not fine at self-serve scale; the handoff names it.
    """
    subscription = await ensure_subscription(session, ctx)
    plan_code = body.plan_code or subscription.plan_code
    try:
        target = plan_for(plan_code)
    except LookupError as exc:
        raise PlanChangeError("%r is not a plan we sell." % plan_code) from exc

    extra_seats = subscription.extra_seats if body.extra_seats is None else body.extra_seats
    if target.extra_seat_inr_per_month is None and extra_seats:
        raise PlanChangeError(
            "The %s plan doesn't sell extra seats." % target.name,
            action="Move to Studio or Practice to add seats.",
        )

    await seat_service.assert_downgrade_fits(
        session, ctx, entitled_after=seat_entitlement(target, extra_seats)
    )

    await SubscriptionRepository(session, ctx).update(
        subscription.id,
        plan_code=plan_code,
        extra_seats=extra_seats,
        cancel_at_period_end=body.cancel_at_period_end,
    )
    return _subscription_out(await entitlement(session, ctx))


async def _spend_budget(session: SessionDep, ctx: TenantDep) -> SpendBudgetOut | None:
    """The caller's generation budget, or ``None`` when none is configured.

    Read through the same repository method the gate uses, so the number an architect
    is shown and the number that refuses their next Generate cannot disagree.
    """
    from garh_api.billing.spend import MICROS_PER_USD, format_usd
    from garh_api.repositories import CreditEventRepository

    cap_micros = int(get_settings().spend_cap_usd) * MICROS_PER_USD
    spent = await CreditEventRepository(session, ctx).spent_micros()
    if cap_micros <= 0:
        return SpendBudgetOut(
            cap_usd=format_usd(0),
            spent_usd=format_usd(spent),
            remaining_usd=format_usd(0),
            cap_micros=0,
            spent_micros=spent,
            remaining_micros=0,
            enforced=False,
        )
    remaining = max(0, cap_micros - spent)
    return SpendBudgetOut(
        cap_usd=format_usd(cap_micros),
        spent_usd=format_usd(spent),
        remaining_usd=format_usd(remaining),
        cap_micros=cap_micros,
        spent_micros=spent,
        remaining_micros=remaining,
        enforced=True,
    )


@router.get("/usage", response_model=UsageOut, summary="Usage against allowance")
async def get_usage(session: SessionDep, ctx: TenantDep) -> UsageOut:
    """What the firm has spent this period, per metered kind.

    The numbers come from ``credit_events`` — the same rows the solver, render, export
    and LLM paths have been writing since the first commit, aggregated by the same
    repository method the quota gate uses. One source, so the page and the gate can
    never disagree.
    """
    ent, lines = await usage_lines(session, ctx)
    return UsageOut(
        spend=await _spend_budget(session, ctx),
        plan_code=ent.plan.code,
        effective_plan_code=ent.effective_plan.code,
        period_start=ent.period_start,
        period_end=ent.period_end,
        lines=[
            UsageLineOut(
                kind=line.kind,
                used=line.used,
                allowance=line.allowance,
                remaining=line.remaining,
            )
            for line in lines
        ],
    )


# ---------------------------------------------------------------------------
# Billing account (G-3)
# ---------------------------------------------------------------------------


def _account_out(account: BillingAccount) -> BillingAccountOut:
    return BillingAccountOut(
        legal_name=account.legal_name,
        gstin=account.gstin,
        state_code=account.state_code,
        state_name=state_name(account.state_code),
        address_line=account.address_line,
        city=account.city,
        postal_code=account.postal_code,
        billing_email=account.billing_email,
    )


@router.get("/account", response_model=BillingAccountOut, summary="GST billing details")
async def get_account(session: SessionDep, ctx: AdminDep) -> BillingAccountOut:
    account = await BillingAccountRepository(session, ctx).get_for_firm()
    if account is None:
        raise BillingProfileIncompleteError(
            "This firm hasn't set up its billing details yet.",
            action="Add the firm's legal name, state and GSTIN under Billing.",
        )
    return _account_out(account)


@router.put("/account", response_model=BillingAccountOut, summary="Set GST billing details")
async def put_account(
    body: BillingAccountIn, session: SessionDep, ctx: AdminDep
) -> BillingAccountOut:
    """Validate and store the firm's GST identity.

    The GSTIN is check-digit validated here, before it can reach an invoice — a wrong
    GSTIN on an issued invoice costs the customer their input tax credit, and an invoice
    cannot be edited afterwards.

    A supplied GSTIN whose state code disagrees with the stated place of supply is
    rejected rather than silently preferred one way or the other: those two disagreeing
    is what flips an invoice between CGST/SGST and IGST.
    """
    try:
        state_code = validate_state_code(body.state_code)
        gstin = validate_gstin(body.gstin) if body.gstin else None
    except GstError as exc:
        raise InvalidGstDetailsError(str(exc)) from exc
    if gstin is not None and gstin_state_code(gstin) != state_code:
        raise InvalidGstDetailsError(
            "That GSTIN is registered in %s but the place of supply says %s."
            % (state_name(gstin_state_code(gstin)), state_name(state_code))
        )

    account = await BillingAccountRepository(session, ctx).upsert(
        legal_name=body.legal_name,
        state_code=state_code,
        gstin=gstin,
        address_line=body.address_line,
        city=body.city,
        postal_code=body.postal_code,
        billing_email=body.billing_email,
    )
    return _account_out(account)


# ---------------------------------------------------------------------------
# Invoices (G-3)
# ---------------------------------------------------------------------------


@router.get("/invoices", response_model=InvoicePage, summary="Invoice history")
async def list_invoices(session: SessionDep, ctx: TenantDep, page: PageDep) -> InvoicePage:
    result = await InvoiceRepository(session, ctx).list_recent(limit=page.limit, cursor=page.cursor)
    return InvoicePage(
        items=[_invoice_out(invoice) for invoice in result.items],
        next_cursor=result.next_cursor,
        has_more=result.next_cursor is not None,
    )


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut, summary="One invoice")
async def get_invoice(invoice_id: uuid.UUID, session: SessionDep, ctx: TenantDep) -> InvoiceOut:
    return _invoice_out(await InvoiceRepository(session, ctx).require(invoice_id))


@router.post(
    "/invoices",
    response_model=InvoiceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Issue this period's invoice",
)
async def issue_invoice(session: SessionDep, ctx: AdminDep) -> InvoiceOut:
    settings = get_settings()
    invoice = await invoice_service.issue_for_current_period(
        session, ctx, provider_name=settings.provider_billing
    )
    return _invoice_out(invoice)


# ---------------------------------------------------------------------------
# Payments (G-1)
# ---------------------------------------------------------------------------


@router.post(
    "/invoices/{invoice_id}/checkout", response_model=CheckoutOut, summary="Open a payment"
)
async def open_checkout(invoice_id: uuid.UUID, session: SessionDep, ctx: AdminDep) -> CheckoutOut:
    settings: Settings = get_settings()
    provider = get_billing_provider(settings)
    try:
        checkout = await payment_service.open_checkout(
            session,
            ctx,
            invoice_id=invoice_id,
            provider=provider,
            # Publishable key. Empty under the mock, which needs none.
            key_id=settings.razorpay_key_id,
        )
    finally:
        await provider.aclose()
    return CheckoutOut(
        invoice_id=checkout.invoice_id,
        invoice_number=checkout.invoice_number,
        provider=checkout.provider,
        order_id=checkout.order_id,
        amount_inr=checkout.amount_inr,
        amount_paise=checkout.amount_paise,
        currency=checkout.currency,
        key_id=checkout.key_id,
    )


@router.post("/payments/verify", response_model=PaymentSettledOut, summary="Settle a paid invoice")
async def verify_payment(
    body: PaymentVerifyIn, session: SessionDep, ctx: AdminDep
) -> PaymentSettledOut:
    """Verify the gateway's signature, then mark the invoice paid.

    Everything in the body came from a browser. The signature is what makes it
    trustworthy, and it is checked before a single row is written.
    """
    provider = get_billing_provider(get_settings())
    try:
        payment, invoice = await payment_service.settle_payment(
            session,
            ctx,
            order_id=body.order_id,
            payment_id=body.payment_id,
            signature=body.signature,
            provider=provider,
        )
    finally:
        await provider.aclose()
    return PaymentSettledOut(payment=_payment_out(payment), invoice=_invoice_out(invoice))


# ---------------------------------------------------------------------------
# Seats (G-4)
# ---------------------------------------------------------------------------


@router.get("/seats", response_model=SeatListOut, summary="Who holds a seat")
async def list_seats(session: SessionDep, ctx: TenantDep) -> SeatListOut:
    _ent, summary = await seat_service.seat_summary(session, ctx)
    seats = await SeatRepository(session, ctx).list_all()
    return SeatListOut(
        entitled=summary.entitled,
        editors_used=summary.editors_used,
        viewers_used=summary.viewers_used,
        available=summary.available,
        seats=[
            SeatOut(
                id=seat.id,
                user_id=seat.user_id,
                seat_type=seat.seat_type,
                assigned_by=seat.assigned_by,
                created_at=seat.created_at,
            )
            for seat in seats
        ],
    )


@router.post(
    "/seats",
    response_model=SeatOut,
    status_code=status.HTTP_201_CREATED,
    summary="Give a member a seat",
)
async def assign_seat(body: SeatAssignIn, session: SessionDep, ctx: AdminDep) -> SeatOut:
    seat = await seat_service.assign_seat(
        session, ctx, user_id=body.user_id, seat_type=body.seat_type
    )
    return SeatOut(
        id=seat.id,
        user_id=seat.user_id,
        seat_type=seat.seat_type,
        assigned_by=seat.assigned_by,
        created_at=seat.created_at,
    )


@router.delete("/seats/{seat_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Release a seat")
async def release_seat(seat_id: uuid.UUID, session: SessionDep, ctx: AdminDep) -> None:
    released = await seat_service.release_seat(session, ctx, seat_id)
    if not released:
        # A seat from another firm is indistinguishable from a missing one — the
        # cross-tenant guarantee, spelled the same way ``require_project`` spells it.
        raise not_found("seat", seat_id)


__all__ = ["router"]
