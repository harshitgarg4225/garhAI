"""billing_accounts / subscriptions / invoices / payments / seats — G-1..G-4

The tables that let the product take money: a GST billing identity per firm, one
subscription, the tax invoices it produces, the gateway orders that collect them, and
the paid seats the firm assigns.

Hand-written like 0001 and 0002, and for the same reason: the constraint names must be
exactly what ``garh_api/billing/models.py`` declares.
``tests/test_billing_migration.py`` proves that by *executing* this ``upgrade()`` into a
throwaway schema and diffing the result against ``BILLING_METADATA``, column by column
and constraint by constraint — so a column added to the models and forgotten here fails
a test rather than a production deploy.

Two shapes differ from every other table in the schema, both deliberate and both
explained in ``garh_api/billing/models.py``:

* **no foreign key to ``firms``.** ``firm_id`` is NOT NULL and indexed, but there is no
  ``ON DELETE CASCADE`` — §36 of the CGST Act requires invoices to be retained for 72
  months, so they must outlive the tenant, exactly as ``audit_log`` already does.
* **money is ``integer`` whole rupees.** Not numeric, not float. See
  ``garh_api/billing/money.py``.

Revision ID: 0003_billing
Revises: 0002_project_underlays
Create Date: 2026-08-28

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_billing"
down_revision: str | None = "0002_project_underlays"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())

ACCOUNTS = "billing_accounts"
SUBSCRIPTIONS = "billing_subscriptions"
INVOICES = "billing_invoices"
PAYMENTS = "billing_payments"
SEATS = "billing_seats"

#: Newest first for the drop, dependency order for the create.
TABLES = (ACCOUNTS, SUBSCRIPTIONS, INVOICES, PAYMENTS, SEATS)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def _id_column() -> sa.Column:
    return sa.Column("id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False)


def upgrade() -> None:
    # -- billing_accounts ------------------------------------------------
    op.create_table(
        ACCOUNTS,
        _id_column(),
        sa.Column("firm_id", UUID, nullable=False),
        sa.Column("legal_name", sa.Text(), nullable=False),
        sa.Column("gstin", sa.Text(), nullable=True),
        sa.Column("state_code", sa.Text(), nullable=False),
        sa.Column("address_line", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("city", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("postal_code", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("billing_email", sa.Text(), server_default=sa.text("''"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_billing_accounts"),
        # One GST identity per firm.
        sa.UniqueConstraint("firm_id", name="uq_billing_accounts_firm_id"),
        sa.CheckConstraint("length(btrim(legal_name)) > 0", name="legal_name_not_blank"),
        sa.CheckConstraint("length(state_code) = 2", name="state_code_len"),
        sa.CheckConstraint("gstin IS NULL OR length(gstin) = 15", name="gstin_len"),
    )
    op.create_index("ix_billing_accounts_firm_id", ACCOUNTS, ["firm_id"])

    # -- billing_subscriptions -------------------------------------------
    op.create_table(
        SUBSCRIPTIONS,
        _id_column(),
        sa.Column("firm_id", UUID, nullable=False),
        sa.Column("plan_code", sa.Text(), server_default=sa.text("'free'"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("extra_seats", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), server_default=sa.text("'mock'"), nullable=False),
        sa.Column("provider_ref", sa.Text(), nullable=True),
        sa.Column("meta", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_billing_subscriptions"),
        sa.UniqueConstraint("firm_id", name="uq_billing_subscriptions_firm_id"),
        sa.CheckConstraint(
            "plan_code IN ('free', 'studio', 'practice', 'enterprise')",
            name="plan_code",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'past_due', 'cancelled')",
            name="status",
        ),
        sa.CheckConstraint("extra_seats >= 0", name="extra_seats"),
        sa.CheckConstraint(
            "current_period_end > current_period_start",
            name="period_ordered",
        ),
    )
    op.create_index("ix_billing_subscriptions_firm_id", SUBSCRIPTIONS, ["firm_id"])

    # -- billing_invoices -------------------------------------------------
    op.create_table(
        INVOICES,
        _id_column(),
        sa.Column("firm_id", UUID, nullable=False),
        sa.Column("invoice_number", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("issued_on", sa.Date(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supplier_legal_name", sa.Text(), nullable=False),
        sa.Column("supplier_gstin", sa.Text(), nullable=False),
        sa.Column("supplier_state_code", sa.Text(), nullable=False),
        sa.Column("supplier_address", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("customer_legal_name", sa.Text(), nullable=False),
        sa.Column("customer_gstin", sa.Text(), nullable=True),
        sa.Column("customer_address", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("place_of_supply_code", sa.Text(), nullable=False),
        sa.Column("interstate", sa.Boolean(), nullable=False),
        sa.Column("currency", sa.Text(), server_default=sa.text("'INR'"), nullable=False),
        sa.Column("taxable_inr", sa.Integer(), nullable=False),
        sa.Column("cgst_inr", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("sgst_inr", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("igst_inr", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("total_inr", sa.Integer(), nullable=False),
        sa.Column("rate_percent_x100", sa.Integer(), nullable=False),
        sa.Column("lines", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meta", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_billing_invoices"),
        # Rule 46(b): the serial number is unique. Globally, not per firm — the number
        # already contains a firm segment.
        sa.UniqueConstraint("invoice_number", name="uq_billing_invoices_invoice_number"),
        sa.CheckConstraint("status IN ('draft', 'issued', 'paid', 'void')", name="status"),
        sa.CheckConstraint("currency = 'INR'", name="currency"),
        # Rule 46(b): "not exceeding sixteen characters".
        sa.CheckConstraint(
            "length(invoice_number) <= 16 AND length(btrim(invoice_number)) > 0",
            name="number_len",
        ),
        # The identity a customer's accountant checks first.
        sa.CheckConstraint(
            "total_inr = taxable_inr + cgst_inr + sgst_inr + igst_inr",
            name="total_is_sum",
        ),
        # CGST+SGST or IGST, never both, and the two halves equal.
        sa.CheckConstraint(
            "(interstate AND cgst_inr = 0 AND sgst_inr = 0)"
            " OR (NOT interstate AND igst_inr = 0 AND cgst_inr = sgst_inr)",
            name="tax_regime",
        ),
        sa.CheckConstraint(
            "taxable_inr >= 0 AND cgst_inr >= 0 AND sgst_inr >= 0 AND igst_inr >= 0"
            " AND total_inr >= 0",
            name="amounts_non_negative",
        ),
        sa.CheckConstraint("period_end > period_start", name="period_ordered"),
    )
    op.create_index("ix_billing_invoices_firm_id", INVOICES, ["firm_id"])
    op.create_index("ix_billing_invoices_firm_id_created_at", INVOICES, ["firm_id", "created_at"])
    op.create_index("ix_billing_invoices_firm_id_issued_on", INVOICES, ["firm_id", "issued_on"])

    # -- billing_payments -------------------------------------------------
    op.create_table(
        PAYMENTS,
        _id_column(),
        sa.Column("firm_id", UUID, nullable=False),
        sa.Column("invoice_id", UUID, nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_order_id", sa.Text(), nullable=False),
        sa.Column("provider_payment_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'created'"), nullable=False),
        sa.Column("amount_inr", sa.Integer(), nullable=False),
        sa.Column("currency", sa.Text(), server_default=sa.text("'INR'"), nullable=False),
        sa.Column(
            "signature_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("meta", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_billing_payments"),
        # RESTRICT: a collected payment outlives any attempt to tidy up its invoice.
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["billing_invoices.id"],
            name="fk_billing_payments_invoice_id_billing_invoices",
            ondelete="RESTRICT",
        ),
        # One row per gateway order, so a replayed callback cannot open a second.
        sa.UniqueConstraint("provider_order_id", name="uq_billing_payments_provider_order_id"),
        sa.CheckConstraint(
            "status IN ('created', 'authorized', 'captured', 'failed', 'refunded')",
            name="status",
        ),
        sa.CheckConstraint("amount_inr >= 0", name="amount_non_negative"),
        sa.CheckConstraint("currency = 'INR'", name="currency"),
    )
    op.create_index("ix_billing_payments_firm_id", PAYMENTS, ["firm_id"])
    op.create_index("ix_billing_payments_firm_id_created_at", PAYMENTS, ["firm_id", "created_at"])
    op.create_index("ix_billing_payments_invoice_id", PAYMENTS, ["invoice_id"])

    # -- billing_seats ----------------------------------------------------
    op.create_table(
        SEATS,
        _id_column(),
        sa.Column("firm_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("seat_type", sa.Text(), server_default=sa.text("'editor'"), nullable=False),
        sa.Column("assigned_by", UUID, nullable=True),
        sa.Column("meta", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_billing_seats"),
        # One seat per user per firm — what makes "count the editor seats" equal
        # "count the users holding one".
        sa.UniqueConstraint("firm_id", "user_id", name="uq_billing_seats_firm_id_user_id"),
        sa.CheckConstraint("seat_type IN ('editor', 'viewer')", name="seat_type"),
    )
    op.create_index("ix_billing_seats_firm_id", SEATS, ["firm_id"])
    op.create_index("ix_billing_seats_firm_id_created_at", SEATS, ["firm_id", "created_at"])

    # The same BEFORE UPDATE trigger every 0001 table carries, so raw-SQL writers
    # maintain updated_at too. The function already exists.
    for table in TABLES:
        op.execute(
            "CREATE TRIGGER trg_%s_updated_at BEFORE UPDATE ON %s "
            "FOR EACH ROW EXECUTE FUNCTION garh_set_updated_at()" % (table, table)
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute("DROP TRIGGER IF EXISTS trg_%s_updated_at ON %s" % (table, table))
    # Payments reference invoices, so they go first.
    op.drop_table(SEATS)
    op.drop_table(PAYMENTS)
    op.drop_table(INVOICES)
    op.drop_table(SUBSCRIPTIONS)
    op.drop_table(ACCOUNTS)
