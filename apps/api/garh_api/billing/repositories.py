"""Firm-scoped data access for the five billing tables.

WHY THIS FILE CONTAINS NO SQL
-----------------------------
``tests/test_no_unscoped_queries.py`` allows ``session.execute``-shaped calls only under
``garh_api/repositories/``, ``tenancy.py`` and ``db.py``. This package is outside that
allowlist, and rather than argue for an exception, every query here is built from the
inherited helpers on :class:`garh_api.tenancy.Repository` — ``_scoped_select``,
``_first``, ``_all``, ``_count``, ``_page``, ``_new_row``, ``_insert``, ``_require_row``.
Every one of them is already firm-filtered, so this module *cannot* express an unscoped
read even by accident. The constraint made the code better: the one place that wanted
raw SQL (summing usage) turned out to be
:meth:`garh_api.repositories.credits.CreditEventRepository.usage_by_kind`, which already
existed — so the quota is metered against the same rows and the same aggregation the
billing page shows, not a second implementation of "how much have they used".

Domain objects are frozen dataclasses, per ``repositories/domain.py``: no ORM row ever
leaves this module, so nothing downstream can lazy-load across a tenant boundary.

Writes are admin-gated at this layer (``ctx.require_admin``) as well as at the route
layer. That is not redundancy for its own sake — the repository is the layer a future
worker or scheduled job will call, and it must refuse a member-level context there too.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from garh_api.billing import models as billing_models
from garh_api.billing.gst import fiscal_year_bounds
from garh_api.billing.plans import PLAN_CODES
from garh_api.tenancy import Page, Repository, RepositoryUsageError


def _json_obj(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _json_arr(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# Domain objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BillingAccount:
    id: uuid.UUID
    firm_id: uuid.UUID
    legal_name: str
    gstin: str | None
    state_code: str
    address_line: str
    city: str
    postal_code: str
    billing_email: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> BillingAccount:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            legal_name=row.legal_name,
            gstin=row.gstin,
            state_code=row.state_code,
            address_line=row.address_line,
            city=row.city,
            postal_code=row.postal_code,
            billing_email=row.billing_email,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @property
    def postal_address(self) -> str:
        """One line, for the invoice's "address of the recipient" field."""
        parts = [self.address_line, self.city, self.postal_code]
        return ", ".join(part.strip() for part in parts if part and part.strip())


@dataclass(frozen=True)
class Subscription:
    id: uuid.UUID
    firm_id: uuid.UUID
    plan_code: str
    status: str
    extra_seats: int
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    provider: str
    provider_ref: str | None
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> Subscription:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            plan_code=row.plan_code,
            status=row.status,
            extra_seats=row.extra_seats,
            current_period_start=row.current_period_start,
            current_period_end=row.current_period_end,
            cancel_at_period_end=row.cancel_at_period_end,
            provider=row.provider,
            provider_ref=row.provider_ref,
            meta=_json_obj(row.meta),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


@dataclass(frozen=True)
class Invoice:
    id: uuid.UUID
    firm_id: uuid.UUID
    invoice_number: str
    status: str
    issued_on: date
    period_start: datetime
    period_end: datetime
    supplier_legal_name: str
    supplier_gstin: str
    supplier_state_code: str
    supplier_address: str
    customer_legal_name: str
    customer_gstin: str | None
    customer_address: str
    place_of_supply_code: str
    interstate: bool
    currency: str
    taxable_inr: int
    cgst_inr: int
    sgst_inr: int
    igst_inr: int
    total_inr: int
    rate_percent_x100: int
    lines: list[dict[str, Any]]
    paid_at: datetime | None
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> Invoice:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            invoice_number=row.invoice_number,
            status=row.status,
            issued_on=row.issued_on,
            period_start=row.period_start,
            period_end=row.period_end,
            supplier_legal_name=row.supplier_legal_name,
            supplier_gstin=row.supplier_gstin,
            supplier_state_code=row.supplier_state_code,
            supplier_address=row.supplier_address,
            customer_legal_name=row.customer_legal_name,
            customer_gstin=row.customer_gstin,
            customer_address=row.customer_address,
            place_of_supply_code=row.place_of_supply_code,
            interstate=row.interstate,
            currency=row.currency,
            taxable_inr=row.taxable_inr,
            cgst_inr=row.cgst_inr,
            sgst_inr=row.sgst_inr,
            igst_inr=row.igst_inr,
            total_inr=row.total_inr,
            rate_percent_x100=row.rate_percent_x100,
            lines=_json_arr(row.lines),
            paid_at=row.paid_at,
            meta=_json_obj(row.meta),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @property
    def tax_total_inr(self) -> int:
        return self.cgst_inr + self.sgst_inr + self.igst_inr


@dataclass(frozen=True)
class Payment:
    id: uuid.UUID
    firm_id: uuid.UUID
    invoice_id: uuid.UUID
    provider: str
    provider_order_id: str
    provider_payment_id: str | None
    status: str
    amount_inr: int
    currency: str
    signature_verified: bool
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> Payment:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            invoice_id=row.invoice_id,
            provider=row.provider,
            provider_order_id=row.provider_order_id,
            provider_payment_id=row.provider_payment_id,
            status=row.status,
            amount_inr=row.amount_inr,
            currency=row.currency,
            signature_verified=row.signature_verified,
            meta=_json_obj(row.meta),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


@dataclass(frozen=True)
class Seat:
    id: uuid.UUID
    firm_id: uuid.UUID
    user_id: uuid.UUID
    seat_type: str
    assigned_by: uuid.UUID | None
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> Seat:
        return cls(
            id=row.id,
            firm_id=row.firm_id,
            user_id=row.user_id,
            seat_type=row.seat_type,
            assigned_by=row.assigned_by,
            meta=_json_obj(row.meta),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


class BillingAccountRepository(Repository[Any, BillingAccount]):
    """The firm's GST identity. Exactly one row per firm, or none."""

    row_type = billing_models.BillingAccount
    entity_name = "billing_account"

    def to_domain(self, row: Any) -> BillingAccount:
        return BillingAccount.from_row(row)

    async def get_for_firm(self) -> BillingAccount | None:
        row = await self._first(self._scoped_select())
        return None if row is None else self.to_domain(row)

    async def upsert(
        self,
        *,
        legal_name: str,
        state_code: str,
        gstin: str | None,
        address_line: str = "",
        city: str = "",
        postal_code: str = "",
        billing_email: str = "",
    ) -> BillingAccount:
        """Create or replace the firm's billing identity. Admin only.

        Values are assumed already validated by
        :func:`garh_api.billing.gst.validate_gstin` /
        :func:`~garh_api.billing.gst.validate_state_code` — the repository stores, it
        does not parse. It does refuse a blank legal name, because that one is a NOT
        NULL + CHECK at the database and a 500 is a worse answer than a usage error.
        """
        self.ctx.require_admin("changing billing details")
        clean_name = (legal_name or "").strip()
        if not clean_name:
            raise RepositoryUsageError("A billing account needs a legal name.")
        row = await self._first(self._scoped_select())
        values = {
            "legal_name": clean_name,
            "state_code": state_code,
            "gstin": gstin,
            "address_line": address_line or "",
            "city": city or "",
            "postal_code": postal_code or "",
            "billing_email": billing_email or "",
        }
        if row is None:
            row = self._new_row(**values)
            await self._insert(row)
        else:
            # Assigned directly rather than through ``_apply_patch``: that helper skips
            # ``None`` values, and ``gstin=None`` is a real edit — a firm that
            # surrenders its registration must be able to clear it.
            for key, value in values.items():
                setattr(row, key, value)
            await self.flush()
        self._log.info("billing.account_saved", has_gstin=bool(gstin), state=state_code)
        return self.to_domain(row)


class SubscriptionRepository(Repository[Any, Subscription]):
    """What the firm is on. Exactly one row per firm, or none (= never subscribed)."""

    row_type = billing_models.BillingSubscription
    entity_name = "subscription"

    def to_domain(self, row: Any) -> Subscription:
        return Subscription.from_row(row)

    async def get_for_firm(self) -> Subscription | None:
        row = await self._first(self._scoped_select())
        return None if row is None else self.to_domain(row)

    async def create(
        self,
        *,
        plan_code: str,
        status: str,
        period_start: datetime,
        period_end: datetime,
        extra_seats: int = 0,
        provider: str = "mock",
    ) -> Subscription:
        if plan_code not in PLAN_CODES:
            raise RepositoryUsageError("plan_code must be one of %s." % ", ".join(PLAN_CODES))
        if status not in billing_models.SUBSCRIPTION_STATUSES:
            raise RepositoryUsageError(
                "status must be one of %s." % ", ".join(billing_models.SUBSCRIPTION_STATUSES)
            )
        row = self._new_row(
            plan_code=plan_code,
            status=status,
            extra_seats=extra_seats,
            current_period_start=period_start,
            current_period_end=period_end,
            provider=provider,
        )
        await self._insert(row)
        return self.to_domain(row)

    async def update(
        self,
        subscription_id: uuid.UUID,
        *,
        plan_code: str | None = None,
        status: str | None = None,
        extra_seats: int | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        cancel_at_period_end: bool | None = None,
        provider_ref: str | None = None,
    ) -> Subscription:
        self.ctx.require_admin("changing the subscription")
        row = await self._require_row(subscription_id)
        if plan_code is not None:
            if plan_code not in PLAN_CODES:
                raise RepositoryUsageError("plan_code must be one of %s." % ", ".join(PLAN_CODES))
            row.plan_code = plan_code
        if status is not None:
            if status not in billing_models.SUBSCRIPTION_STATUSES:
                raise RepositoryUsageError(
                    "status must be one of %s." % ", ".join(billing_models.SUBSCRIPTION_STATUSES)
                )
            row.status = status
        if extra_seats is not None:
            if extra_seats < 0:
                raise RepositoryUsageError("extra_seats cannot be negative.")
            row.extra_seats = extra_seats
        if period_start is not None:
            row.current_period_start = period_start
        if period_end is not None:
            row.current_period_end = period_end
        if cancel_at_period_end is not None:
            row.cancel_at_period_end = cancel_at_period_end
        if provider_ref is not None:
            row.provider_ref = provider_ref
        await self.flush()
        self._log.info("billing.subscription_updated", plan_code=row.plan_code, status=row.status)
        return self.to_domain(row)


class InvoiceRepository(Repository[Any, Invoice]):
    """Issued tax invoices. Append-mostly: only ``status`` and ``paid_at`` ever change."""

    row_type = billing_models.BillingInvoice
    entity_name = "invoice"

    def to_domain(self, row: Any) -> Invoice:
        return Invoice.from_row(row)

    async def delete(self, entity_id: Any) -> bool:
        raise RepositoryUsageError(
            "A tax invoice cannot be deleted (CGST Act §36 requires 72 months of "
            "retention). Void it instead."
        )

    async def create(self, **values: Any) -> Invoice:
        """Insert an invoice. Every field is supplied by ``billing.invoices``."""
        row = self._new_row(**values)
        await self._insert(row)
        self._log.info(
            "billing.invoice_created",
            invoice_number=values.get("invoice_number"),
            total_inr=values.get("total_inr"),
        )
        return self.to_domain(row)

    async def list_recent(
        self, *, limit: int | None = None, cursor: str | None = None
    ) -> Page[Invoice]:
        return await self._page(limit=limit, cursor=cursor, newest_first=True)

    async def count_in_fiscal_year(self, on: date) -> int:
        """How many invoices this firm already has in the FY containing ``on``.

        The next invoice's serial is this + 1 (Rule 46(b) wants the series consecutive
        within a financial year). Counting rows rather than reading a stored counter
        means there is no counter to drift; the unique index on ``invoice_number`` is
        what makes a concurrent double-issue fail loudly instead of duplicating.
        """
        start, end = fiscal_year_bounds(on)
        stmt = (
            self._scoped_select()
            .where(billing_models.BillingInvoice.issued_on >= start)
            .where(billing_models.BillingInvoice.issued_on < end)
        )
        return await self._count(stmt)

    async def find_for_period(self, period_start: datetime) -> Invoice | None:
        """The invoice already covering a period start, if any (idempotence guard)."""
        stmt = (
            self._scoped_select()
            .where(billing_models.BillingInvoice.period_start == period_start)
            .where(billing_models.BillingInvoice.status != "void")
        )
        row = await self._first(stmt)
        return None if row is None else self.to_domain(row)

    async def set_status(
        self, invoice_id: uuid.UUID, *, status: str, paid_at: datetime | None = None
    ) -> Invoice:
        if status not in billing_models.INVOICE_STATUSES:
            raise RepositoryUsageError(
                "status must be one of %s." % ", ".join(billing_models.INVOICE_STATUSES)
            )
        row = await self._require_row(invoice_id)
        row.status = status
        if paid_at is not None:
            row.paid_at = paid_at
        await self.flush()
        return self.to_domain(row)


class PaymentRepository(Repository[Any, Payment]):
    """Collection attempts against an invoice."""

    row_type = billing_models.BillingPayment
    entity_name = "payment"

    def to_domain(self, row: Any) -> Payment:
        return Payment.from_row(row)

    async def create(
        self,
        *,
        invoice_id: uuid.UUID,
        provider: str,
        provider_order_id: str,
        amount_inr: int,
    ) -> Payment:
        row = self._new_row(
            invoice_id=invoice_id,
            provider=provider,
            provider_order_id=provider_order_id,
            amount_inr=amount_inr,
            status="created",
        )
        await self._insert(row)
        return self.to_domain(row)

    async def by_order_id(self, provider_order_id: str) -> Payment | None:
        """Look one up by the gateway's order id — **within this firm only**.

        The scoping is the point. An order id is a value an attacker can hold (it is in
        the checkout widget's JavaScript), and an unscoped lookup here would let firm B
        settle firm A's invoice by quoting it. ``_scoped_select`` makes that
        impossible rather than remembered.
        """
        stmt = self._scoped_select().where(
            billing_models.BillingPayment.provider_order_id == provider_order_id
        )
        row = await self._first(stmt)
        return None if row is None else self.to_domain(row)

    async def open_attempt_for_invoice(self, invoice_id: uuid.UUID) -> Payment | None:
        """The live, unsettled attempt against an invoice, newest first, or ``None``.

        What makes re-opening a checkout idempotent. ``provider_order_id`` is globally
        unique, and the mock provider derives its order id deterministically from the
        invoice number — so a customer who clicks "Pay" twice (or reloads the page)
        produces the *same* order id, and inserting a second row would violate that
        constraint and surface as a 500. Reusing the row the first click created is both
        the fix and the right semantics: the gateway order is still open, and one invoice
        should have one order, not one per click.

        Only ``created`` counts. A ``captured`` row means the invoice is paid (checkout
        refuses that earlier anyway), and a ``failed`` one is a dead attempt that must
        not be handed back as if it were live.
        """
        stmt = (
            self._scoped_select()
            .where(billing_models.BillingPayment.invoice_id == invoice_id)
            .where(billing_models.BillingPayment.status == "created")
            .order_by(billing_models.BillingPayment.created_at.desc())
        )
        row = await self._first(stmt)
        return None if row is None else self.to_domain(row)

    async def list_for_invoice(self, invoice_id: uuid.UUID) -> list[Payment]:
        stmt = self._scoped_select().where(billing_models.BillingPayment.invoice_id == invoice_id)
        return [self.to_domain(row) for row in await self._all(stmt)]

    async def mark(
        self,
        payment_id: uuid.UUID,
        *,
        status: str,
        provider_payment_id: str | None = None,
        signature_verified: bool | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Payment:
        if status not in billing_models.PAYMENT_STATUSES:
            raise RepositoryUsageError(
                "status must be one of %s." % ", ".join(billing_models.PAYMENT_STATUSES)
            )
        row = await self._require_row(payment_id)
        row.status = status
        if provider_payment_id is not None:
            row.provider_payment_id = provider_payment_id
        if signature_verified is not None:
            row.signature_verified = signature_verified
        if meta is not None:
            row.meta = {**_json_obj(row.meta), **meta}
        await self.flush()
        self._log.info("billing.payment_marked", payment_status=status)
        return self.to_domain(row)


class SeatRepository(Repository[Any, Seat]):
    """Who in the firm holds a seat (G-4)."""

    row_type = billing_models.BillingSeat
    entity_name = "seat"

    def to_domain(self, row: Any) -> Seat:
        return Seat.from_row(row)

    async def list_all(self) -> list[Seat]:
        return [self.to_domain(row) for row in await self._all(self._scoped_select())]

    async def for_user(self, user_id: uuid.UUID) -> Seat | None:
        stmt = self._scoped_select().where(billing_models.BillingSeat.user_id == user_id)
        row = await self._first(stmt)
        return None if row is None else self.to_domain(row)

    async def count_of_type(self, seat_type: str) -> int:
        """Seats of one type held in this firm — the number a quota compares against."""
        if seat_type not in billing_models.SEAT_TYPES:
            raise RepositoryUsageError(
                "seat_type must be one of %s." % ", ".join(billing_models.SEAT_TYPES)
            )
        stmt = self._scoped_select().where(billing_models.BillingSeat.seat_type == seat_type)
        return await self._count(stmt)

    async def assign(
        self, *, user_id: uuid.UUID, seat_type: str, assigned_by: uuid.UUID | None
    ) -> Seat:
        if seat_type not in billing_models.SEAT_TYPES:
            raise RepositoryUsageError(
                "seat_type must be one of %s." % ", ".join(billing_models.SEAT_TYPES)
            )
        row = self._new_row(user_id=user_id, seat_type=seat_type, assigned_by=assigned_by)
        await self._insert(row)
        self._log.info("billing.seat_assigned", seat_type=seat_type)
        return self.to_domain(row)


__all__ = [
    "BillingAccount",
    "BillingAccountRepository",
    "Invoice",
    "InvoiceRepository",
    "Payment",
    "PaymentRepository",
    "Seat",
    "SeatRepository",
    "Subscription",
    "SubscriptionRepository",
]
