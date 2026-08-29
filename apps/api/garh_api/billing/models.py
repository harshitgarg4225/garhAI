"""The five billing tables (G-1..G-4), on their own ``MetaData``.

WHY A SEPARATE ``MetaData`` AND NOT ``garh_api.models.Base``
------------------------------------------------------------
Two reasons, one legal and one structural.

**Legal.** A tax invoice is a statutory record: §36 of the CGST Act requires a
registered person to retain invoices and the accounts behind them for 72 months from the
due date of the annual return. Every other tenant-owned table in this product hangs off
``firms.id`` with ``ON DELETE CASCADE``, so deleting a firm deletes its rows — which is
right for projects and catastrophic for invoices. These tables therefore declare **no
foreign key to firms**, exactly as ``audit_log`` already does and for the same class of
reason ("the audit trail must survive deletion of the firm it describes"). ``firm_id``
is still ``NOT NULL`` and still indexed, and every one of these tables still carries
:class:`~garh_api.models.TenantOwned`, so they can only be reached through a
firm-scoped :class:`~garh_api.tenancy.Repository`. Foreign keys *within* the package —
a payment to its invoice — are declared normally, because both sides live here.

**Structural.** Keeping them off ``Base.metadata`` means this package lands without
editing ``garh_api/models.py``: ``ALL_TABLES`` there drives the test suite's per-test
TRUNCATE, and a table appearing in the metadata but not in that tuple would silently
un-isolate other people's tests. Folding these five names in is a one-line follow-up
(see the handoff note); until then :data:`BILLING_TABLES` is the list, and
``tests/test_billing_api.py`` truncates from it.

CONVENTIONS INHERITED, NOT REINVENTED
-------------------------------------
Same naming convention (so constraint names are deterministic and the hand-written
migration matches), same ``UuidPk``/``Timestamps`` mixins, same "enum-ish CHECK
constraint mirrored by a module-level tuple" rule, same ``updated_at`` trigger installed
by the migration. Money columns are ``Integer`` **whole rupees** (see
:mod:`garh_api.billing.money`), never numeric, never float.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from garh_api.billing.plans import PLAN_CODES
from garh_api.models import (
    JSON_ARR,
    JSON_OBJ,
    NAMING_CONVENTION,
    TenantOwned,
    Timestamps,
    UuidPk,
    _in_check,
)

# ---------------------------------------------------------------------------
# Controlled vocabularies (mirror the CHECK constraints below, 1:1)
# ---------------------------------------------------------------------------

#: billing_subscriptions.status.
#:
#: Three states, no "trialing": a trial is the free plan with a different marketing
#: word, and a status whose entitlements are identical to another status is a branch
#: waiting to be got wrong. Anything that is not ``active`` is entitled to the FREE
#: plan's allowances, never the subscribed plan's — see ``subscriptions.entitlement``.
SUBSCRIPTION_STATUSES: tuple[str, ...] = ("active", "past_due", "cancelled")

#: billing_invoices.status. ``draft`` never leaves the API; an issued invoice is
#: immutable except for its status and ``paid_at`` (Rule 46: a tax invoice is corrected
#: by a credit note, not by editing it).
INVOICE_STATUSES: tuple[str, ...] = ("draft", "issued", "paid", "void")

#: billing_payments.status — the subset of the gateway's lifecycle we act on.
PAYMENT_STATUSES: tuple[str, ...] = ("created", "authorized", "captured", "failed", "refunded")

#: billing_seats.seat_type. Only ``editor`` seats consume the plan's paid entitlement;
#: ``viewer`` seats are free and unlimited, because a client looking at a scheme costs
#: us nothing and charging the architect for their own client is a way to lose the
#: architect.
SEAT_TYPES: tuple[str, ...] = ("editor", "viewer")

#: Currency. One value, and a CHECK constraint, so a row can never claim to be USD
#: while carrying rupees.
CURRENCY_INR: str = "INR"

#: Every table in this module, in dependency order. Drives the migration and the
#: per-test truncation in ``tests/test_billing_api.py``.
BILLING_TABLES: tuple[str, ...] = (
    "billing_accounts",
    "billing_subscriptions",
    "billing_invoices",
    "billing_payments",
    "billing_seats",
)


class BillingBase(DeclarativeBase):
    """Declarative base for the billing tables. Its own ``MetaData`` — see the docstring."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


#: Exported so tests and the migration check can create/inspect the schema.
BILLING_METADATA = BillingBase.metadata


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


class BillingAccount(UuidPk, Timestamps, TenantOwned, BillingBase):
    """Who the invoice is made out to: the firm's legal identity for GST.

    Separate from ``firms`` on purpose. ``firms.name`` is the display name an architect
    types ("Rao & Associates"), and ``firms.settings`` is a free-form JSONB blob for
    preferences; neither is a place to keep a statutory identity that has to be exact,
    validated, and unchanged on every invoice already issued.

    ``gstin`` is nullable because an unregistered customer is a real customer — a sole
    practitioner under the ₹20 lakh threshold has no GSTIN, and their invoice is still a
    valid tax invoice (it simply carries no recipient GSTIN and they claim no credit).
    ``state_code`` is NOT nullable, because the place of supply decides CGST/SGST vs
    IGST and there is no safe default for it.
    """

    __tablename__ = "billing_accounts"

    firm_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    #: The name that appears on the invoice — the registered entity, not the studio name.
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    #: 15-character GSTIN, validated by ``billing.gst.validate_gstin`` before it lands.
    gstin: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Two-digit GST state code — the place of supply (IGST Act §12(2)(a)).
    state_code: Mapped[str] = mapped_column(Text, nullable=False)
    address_line: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    city: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    postal_code: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    #: Where invoices are emailed. Separate from the signing-in user's address: accounts
    #: payable is rarely the architect.
    billing_email: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))

    __table_args__ = (
        UniqueConstraint("firm_id", name="uq_billing_accounts_firm_id"),
        CheckConstraint("length(btrim(legal_name)) > 0", name="legal_name_not_blank"),
        CheckConstraint("length(state_code) = 2", name="state_code_len"),
        CheckConstraint("gstin IS NULL OR length(gstin) = 15", name="gstin_len"),
        Index("ix_billing_accounts_firm_id", "firm_id"),
    )


class BillingSubscription(UuidPk, Timestamps, TenantOwned, BillingBase):
    """What the firm is on, and until when. One row per firm.

    ``extra_seats`` is stored here rather than derived from seat rows: it is what the
    firm *bought*, and seat assignments are what it *used*. Conflating them would mean
    releasing a seat silently cancels the purchase mid-period.
    """

    __tablename__ = "billing_subscriptions"

    firm_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    plan_code: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'free'"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    #: Editor seats purchased beyond the plan's included count.
    extra_seats: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    #: The window the quota counts over and the invoice covers.
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: A downgrade requested mid-period takes effect at the period end; the firm keeps
    #: what it paid for until then.
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    #: Which provider holds the money side (``mock`` | ``razorpay``), and its id for
    #: this firm if it has one. Never a secret — an order/customer id only.
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'mock'"))
    provider_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_OBJ)

    __table_args__ = (
        UniqueConstraint("firm_id", name="uq_billing_subscriptions_firm_id"),
        CheckConstraint(_in_check("plan_code", PLAN_CODES), name="plan_code"),
        CheckConstraint(_in_check("status", SUBSCRIPTION_STATUSES), name="status"),
        CheckConstraint("extra_seats >= 0", name="extra_seats"),
        CheckConstraint(
            "current_period_end > current_period_start",
            name="period_ordered",
        ),
        Index("ix_billing_subscriptions_firm_id", "firm_id"),
    )


class BillingInvoice(UuidPk, Timestamps, TenantOwned, BillingBase):
    """A GST tax invoice, with every Rule 46 field it has to carry.

    The supplier and customer identities are **snapshotted** onto the row rather than
    joined at read time. An invoice is a statement of what was true on the day it was
    issued: if the firm later corrects its GSTIN, last quarter's invoice must not
    silently start showing the new one, because the customer has already filed a return
    quoting the old.

    ``lines`` is JSONB rather than a child table for the same reason the rest of this
    codebase keeps documents in JSONB: the lines are immutable once issued, are always
    read whole with their invoice, and are never queried across invoices. Each line
    carries its own HSN/SAC, which Rule 46 requires per line, not per invoice.
    """

    __tablename__ = "billing_invoices"

    firm_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    #: Rule 46(b): ≤16 characters, consecutive within a financial year, unique. The
    #: uniqueness is global, not per firm — the number already contains a firm segment.
    invoice_number: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    issued_on: Mapped[date] = mapped_column(Date, nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # -- the supply -----------------------------------------------------
    supplier_legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    supplier_gstin: Mapped[str] = mapped_column(Text, nullable=False)
    supplier_state_code: Mapped[str] = mapped_column(Text, nullable=False)
    supplier_address: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    customer_legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    customer_gstin: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_address: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    #: Place of supply — the recipient's state code. Printed on the invoice verbatim.
    place_of_supply_code: Mapped[str] = mapped_column(Text, nullable=False)
    #: True → IGST, False → CGST+SGST. Stored rather than recomputed so the row is
    #: self-describing to anyone reading the table without this code.
    interstate: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # -- money (whole rupees) -------------------------------------------
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'INR'"))
    taxable_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    cgst_inr: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    sgst_inr: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    igst_inr: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_percent_x100: Mapped[int] = mapped_column(Integer, nullable=False)

    #: ``[{description, hsnSac, quantity, unitPriceInr, amountInr}]``.
    lines: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=JSON_ARR
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_OBJ)

    __table_args__ = (
        UniqueConstraint("invoice_number", name="uq_billing_invoices_invoice_number"),
        CheckConstraint(_in_check("status", INVOICE_STATUSES), name="status"),
        CheckConstraint("currency = 'INR'", name="currency"),
        CheckConstraint(
            "length(invoice_number) <= 16 AND length(btrim(invoice_number)) > 0",
            name="number_len",
        ),
        # The arithmetic identity, enforced by the database as well as by the code that
        # writes it. A row that fails this is an invoice a chartered accountant rejects.
        CheckConstraint(
            "total_inr = taxable_inr + cgst_inr + sgst_inr + igst_inr",
            name="total_is_sum",
        ),
        # Exactly one tax regime per invoice: CGST+SGST or IGST, never both.
        CheckConstraint(
            "(interstate AND cgst_inr = 0 AND sgst_inr = 0)"
            " OR (NOT interstate AND igst_inr = 0 AND cgst_inr = sgst_inr)",
            name="tax_regime",
        ),
        CheckConstraint(
            "taxable_inr >= 0 AND cgst_inr >= 0 AND sgst_inr >= 0 AND igst_inr >= 0"
            " AND total_inr >= 0",
            name="amounts_non_negative",
        ),
        CheckConstraint("period_end > period_start", name="period_ordered"),
        Index("ix_billing_invoices_firm_id", "firm_id"),
        Index("ix_billing_invoices_firm_id_created_at", "firm_id", "created_at"),
        Index("ix_billing_invoices_firm_id_issued_on", "firm_id", "issued_on"),
    )


class BillingPayment(UuidPk, Timestamps, TenantOwned, BillingBase):
    """One attempt to collect one invoice through a payment provider.

    ``signature_verified`` is a stored fact, not a transient check: a payment is only
    allowed to move an invoice to ``paid`` when the provider's HMAC over
    ``order_id|payment_id`` verified against our key secret, and the row records that
    it did. A reviewer asking "how do we know this money arrived" gets an answer from
    the table.
    """

    __tablename__ = "billing_payments"

    firm_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        # RESTRICT, not CASCADE: a collected payment must outlive any attempt to tidy
        # up the invoice it paid.
        ForeignKey(
            "billing_invoices.id",
            ondelete="RESTRICT",
            name="fk_billing_payments_invoice_id_billing_invoices",
        ),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    #: The gateway's order id (Razorpay ``order_...``). Not a secret.
    provider_order_id: Mapped[str] = mapped_column(Text, nullable=False)
    #: The gateway's payment id (Razorpay ``pay_...``), once the customer has paid.
    provider_payment_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'created'"))
    amount_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'INR'"))
    signature_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_OBJ)

    __table_args__ = (
        UniqueConstraint("provider_order_id", name="uq_billing_payments_provider_order_id"),
        CheckConstraint(_in_check("status", PAYMENT_STATUSES), name="status"),
        CheckConstraint("amount_inr >= 0", name="amount_non_negative"),
        CheckConstraint("currency = 'INR'", name="currency"),
        Index("ix_billing_payments_firm_id", "firm_id"),
        Index("ix_billing_payments_firm_id_created_at", "firm_id", "created_at"),
        Index("ix_billing_payments_invoice_id", "invoice_id"),
    )


class BillingSeat(UuidPk, Timestamps, TenantOwned, BillingBase):
    """One firm user holding one seat (G-4).

    Not a duplicate of ``users.role``. ``users.role`` is *authority* inside the firm
    (admin may delete a project); a seat is *entitlement* the firm has paid for. They
    move independently: a practice can promote a member to admin without buying a seat,
    and can let a seat lapse without demoting anyone.

    ``user_id`` carries no foreign key (see the module docstring on why these tables
    stand alone), so the repository validates that the user exists **inside the same
    firm** before assigning — which is the check that actually matters, and one a
    foreign key to ``users`` would not have made.
    """

    __tablename__ = "billing_seats"

    firm_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    seat_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'editor'"))
    #: Who granted it — the admin's user id, for a "who added this seat" answer.
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=JSON_OBJ)

    __table_args__ = (
        # One seat per user per firm. The uniqueness is what makes "count the editor
        # seats" equal "count the users holding one".
        UniqueConstraint("firm_id", "user_id", name="uq_billing_seats_firm_id_user_id"),
        CheckConstraint(_in_check("seat_type", SEAT_TYPES), name="seat_type"),
        Index("ix_billing_seats_firm_id", "firm_id"),
        Index("ix_billing_seats_firm_id_created_at", "firm_id", "created_at"),
    )


__all__ = [
    "BILLING_METADATA",
    "BILLING_TABLES",
    "CURRENCY_INR",
    "INVOICE_STATUSES",
    "PAYMENT_STATUSES",
    "SEAT_TYPES",
    "SUBSCRIPTION_STATUSES",
    "BillingAccount",
    "BillingBase",
    "BillingInvoice",
    "BillingPayment",
    "BillingSeat",
    "BillingSubscription",
]
