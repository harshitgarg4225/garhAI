"""GST: what an Indian practice needs on a receipt before it can expense it (G-3).

An architecture practice in India cannot put a software subscription through its books
from a card statement. It needs a tax invoice carrying, at minimum (CGST Rules, Rule
46): the supplier's name, address and **GSTIN**; a consecutive serial number unique for
the financial year, ≤16 characters; the date; the recipient's name, address and GSTIN;
the **HSN or SAC** of what was supplied; the taxable value; the **rate and amount of
tax split into CGST + SGST or IGST**; and the **place of supply**. Miss the GSTIN or the
place of supply and the customer cannot claim input tax credit, which for a ₹15,000/month
subscription is ₹2,700 a month of the customer's money.

THE SPLIT, WHICH IS THE WHOLE POINT
-----------------------------------
GST on a service is 18%, but *which* 18% depends on where the customer is:

* customer's place of supply is in the **same state** as ours → intra-state supply →
  **CGST 9% + SGST 9%**, two separate lines;
* any other state or union territory → inter-state supply → **IGST 18%**, one line.

For a subscription (a service supplied to a registered person) the place of supply is
the recipient's location — §12(2)(a) of the IGST Act — which is why
:class:`BillingAccount` stores a state code rather than deriving one from an address
string. Getting this wrong is not cosmetic: an invoice that charges CGST+SGST to a
customer in another state charges tax to the wrong government, and the customer's credit
claim fails.

WHAT IS NOT DONE HERE
---------------------
No e-invoicing / IRN registration (mandatory only above a turnover threshold this
product is nowhere near), no reverse charge, no export-of-service zero rating, no TCS.
Each would be a new field on the invoice row and a new branch here; none is needed to
issue a valid domestic tax invoice today.

Every amount is a whole rupee integer — see :mod:`garh_api.billing.money`.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Final

from garh_api.billing.money import percent_of

# ---------------------------------------------------------------------------
# Rates and codes
# ---------------------------------------------------------------------------

#: GST on this supply, as an integer percentage ×100. 18% is the standard rate for
#: "information technology software" services (SAC 9973), and the rate the SAC below
#: attracts.
GST_RATE_X100: Final = 1800
#: The intra-state halves. Two components, each half the standard rate, and the law
#: requires them to be shown separately and equally.
CGST_RATE_X100: Final = GST_RATE_X100 // 2
SGST_RATE_X100: Final = GST_RATE_X100 // 2

#: SAC (Services Accounting Code) for what we sell: 997331, "licensing services for the
#: right to use computer software and databases". A subscription to hosted design
#: software is a licence to use software, not a sale of goods, so it carries a SAC and
#: not an HSN. The invoice column is labelled "HSN/SAC" because that is what the format
#: prescribes and what a customer's accountant looks for.
SUBSCRIPTION_SAC: Final = "997331"

#: GST state codes → the name printed as "Place of supply". The first two digits of
#: every GSTIN are one of these.
#:
#: 25 (Daman and Diu) and 28 (undivided Andhra Pradesh) are deliberately absent: both
#: were retired — 25 merged into 26 in 2020, 28 split into 37/36 in 2017 — so neither
#: can be a valid place of supply for an invoice issued today. A historic GSTIN bearing
#: one is therefore rejected by :func:`validate_gstin`, which is the correct answer for
#: a customer typing their current registration in.
GST_STATE_CODES: Final[dict[str, str]] = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
    "97": "Other Territory",
}


class GstError(ValueError):
    """A GST value the invoice cannot legally carry (bad GSTIN, unknown state)."""


# ---------------------------------------------------------------------------
# GSTIN
# ---------------------------------------------------------------------------

#: 2-digit state code, 10-character PAN, 1 entity digit/letter, 'Z', 1 checksum.
_GSTIN_SHAPE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")

#: The checksum alphabet: 0-9 then A-Z, valued 0..35.
_CHECKSUM_ALPHABET: Final = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_checksum(first_fourteen: str) -> str:
    """The 15th character of a GSTIN, from the first fourteen.

    The published algorithm: value each character in base 36, multiply by an alternating
    factor of 1 and 2, sum ``quotient + remainder`` of each product over 36, and take
    ``(36 - total mod 36) mod 36`` back to a character. It is a real check digit, not a
    format rule — it catches the single-character typos and transpositions that make up
    almost every wrong GSTIN a customer types, before that GSTIN reaches an invoice
    where the mistake costs them their input credit.
    """
    if len(first_fourteen) != 14:
        raise GstError("A GSTIN checksum is computed from exactly 14 characters.")
    total = 0
    for index, char in enumerate(first_fourteen):
        value = _CHECKSUM_ALPHABET.find(char)
        if value < 0:
            raise GstError("%r is not a valid GSTIN character." % char)
        product = value * (2 if index % 2 else 1)
        total += product // 36 + product % 36
    return _CHECKSUM_ALPHABET[(36 - total % 36) % 36]


def normalise_gstin(value: str) -> str:
    """Upper-cased, whitespace-stripped. Does not validate."""
    return re.sub(r"\s+", "", value or "").upper()


def validate_gstin(value: str) -> str:
    """Return the normalised GSTIN, or raise :class:`GstError` saying what is wrong.

    Three gates, in the order a wrong value usually fails them: shape, state code, then
    check digit. The error names which one failed — "that GSTIN's check digit doesn't
    match" is actionable, "invalid GSTIN" is not.
    """
    clean = normalise_gstin(value)
    if len(clean) != 15:
        raise GstError("A GSTIN is 15 characters; %r is %d." % (clean, len(clean)))
    if not _GSTIN_SHAPE.match(clean):
        raise GstError(
            "%r is not shaped like a GSTIN (2-digit state code, 10-character PAN, "
            "entity code, 'Z', check digit)." % clean
        )
    state = clean[:2]
    if state not in GST_STATE_CODES:
        raise GstError("%r starts with state code %r, which is not a GST state." % (clean, state))
    expected = gstin_checksum(clean[:14])
    if clean[14] != expected:
        raise GstError(
            "%r fails its check digit (expected %r, got %r) — usually a typo in the "
            "PAN portion." % (clean, expected, clean[14])
        )
    return clean


def gstin_state_code(gstin: str) -> str:
    """The state code embedded in a valid GSTIN."""
    return validate_gstin(gstin)[:2]


def validate_state_code(value: str) -> str:
    """A place-of-supply state code, normalised to two digits."""
    clean = (value or "").strip()
    if len(clean) == 1 and clean.isdigit():
        clean = "0" + clean
    if clean not in GST_STATE_CODES:
        raise GstError(
            "%r is not a GST state code. It must be one of the two-digit codes in "
            "GST_STATE_CODES (e.g. '29' for Karnataka)." % value
        )
    return clean


def state_name(code: str) -> str:
    return GST_STATE_CODES[validate_state_code(code)]


# ---------------------------------------------------------------------------
# The supplier — us
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SupplierIdentity:
    """Our own registration, as it must appear on every invoice we issue.

    Read from the environment rather than hard-coded because it is a real legal
    identity that differs between the company that ships this code and anyone running
    it themselves. It is deliberately **not** in :mod:`garh_api.config`: that module is
    owned elsewhere and adding fields to it is a separate change (see the handoff
    note), and reading a small number of process-level names directly has precedent in
    ``garh_api.deps`` (``TRUSTED_PROXY_HOPS``).

    ``configured`` is the gate. Issuing an invoice with a blank or placeholder GSTIN
    would produce a document that looks like a tax invoice and is not one, which is
    worse than refusing — so :meth:`require` raises and the issue route 503s until the
    environment carries a real registration.
    """

    legal_name: str
    gstin: str
    state_code: str
    address: str

    @property
    def configured(self) -> bool:
        return bool(self.legal_name and self.gstin and self.state_code)

    @property
    def state(self) -> str:
        return state_name(self.state_code)

    def require(self) -> SupplierIdentity:
        if not self.configured:
            raise GstError(
                "This deployment has no GST registration configured, so it cannot issue "
                "a tax invoice. Set BILLING_SUPPLIER_LEGAL_NAME, BILLING_SUPPLIER_GSTIN "
                "and BILLING_SUPPLIER_ADDRESS in the environment."
            )
        validate_gstin(self.gstin)
        return self


def supplier_identity() -> SupplierIdentity:
    """The configured supplier, read fresh from the environment.

    Not cached: a cached read would make the value unmonkeypatchable in tests and would
    survive a config change across a reload, and it is four ``os.environ`` lookups.

    ``BILLING_SUPPLIER_STATE_CODE`` defaults to the state code inside the configured
    GSTIN, because those two disagreeing is not a configuration option — it is a
    mistake that would flip every invoice between CGST/SGST and IGST.
    """
    gstin = normalise_gstin(os.environ.get("BILLING_SUPPLIER_GSTIN", ""))
    state = (os.environ.get("BILLING_SUPPLIER_STATE_CODE", "") or "").strip()
    if not state and len(gstin) >= 2:
        state = gstin[:2]
    return SupplierIdentity(
        legal_name=(os.environ.get("BILLING_SUPPLIER_LEGAL_NAME", "") or "").strip(),
        gstin=gstin,
        state_code=state,
        address=(os.environ.get("BILLING_SUPPLIER_ADDRESS", "") or "").strip(),
    )


# ---------------------------------------------------------------------------
# The split
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaxBreakdown:
    """The tax block of an invoice. Every field a whole rupee integer.

    Exactly one of (``cgst_inr`` + ``sgst_inr``) or ``igst_inr`` is non-zero, and
    ``total_inr == taxable_inr + cgst_inr + sgst_inr + igst_inr`` always — asserted in
    :func:`compute_tax` rather than trusted, because "the components don't add up to
    the total" is the one invoice defect a customer's accountant always catches and we
    never would.
    """

    taxable_inr: int
    cgst_inr: int
    sgst_inr: int
    igst_inr: int
    total_inr: int
    interstate: bool
    place_of_supply_code: str
    rate_percent_x100: int = GST_RATE_X100

    @property
    def tax_total_inr(self) -> int:
        return self.cgst_inr + self.sgst_inr + self.igst_inr

    @property
    def place_of_supply(self) -> str:
        return state_name(self.place_of_supply_code)


def compute_tax(
    taxable_inr: int, *, supplier_state_code: str, place_of_supply_code: str
) -> TaxBreakdown:
    """Split ``taxable_inr`` into CGST+SGST (same state) or IGST (any other state).

    Each component is rounded independently, half away from zero. Rounding CGST and
    SGST separately (rather than halving a rounded total) is what keeps them *equal*,
    which the law requires — 9% of ₹4,999 is ₹449.91, so both halves show ₹450 and the
    invoice totals ₹5,899. Halving a rounded ₹900 total would give the same answer
    here and a ₹1 discrepancy on the next odd amount.
    """
    if not isinstance(taxable_inr, int) or isinstance(taxable_inr, bool):
        raise GstError("A taxable value must be an int number of whole rupees.")
    if taxable_inr < 0:
        raise GstError("A taxable value cannot be negative.")
    supplier = validate_state_code(supplier_state_code)
    recipient = validate_state_code(place_of_supply_code)
    interstate = supplier != recipient

    if interstate:
        cgst = sgst = 0
        igst = percent_of(taxable_inr, GST_RATE_X100)
    else:
        cgst = percent_of(taxable_inr, CGST_RATE_X100)
        sgst = percent_of(taxable_inr, SGST_RATE_X100)
        igst = 0

    total = taxable_inr + cgst + sgst + igst
    breakdown = TaxBreakdown(
        taxable_inr=taxable_inr,
        cgst_inr=cgst,
        sgst_inr=sgst,
        igst_inr=igst,
        total_inr=total,
        interstate=interstate,
        place_of_supply_code=recipient,
    )
    # Not defensive: this is the invariant the whole document rests on, and it is
    # cheaper to assert it here than to discover it on a customer's ledger.
    if breakdown.total_inr != breakdown.taxable_inr + breakdown.tax_total_inr:
        raise GstError("Tax components do not sum to the invoice total.")
    if not interstate and breakdown.cgst_inr != breakdown.sgst_inr:
        raise GstError("CGST and SGST must be equal on an intra-state supply.")
    return breakdown


# ---------------------------------------------------------------------------
# Invoice numbering (Rule 46(b))
# ---------------------------------------------------------------------------

#: Rule 46(b): "a consecutive serial number not exceeding sixteen characters".
MAX_INVOICE_NUMBER_LENGTH: Final = 16
#: Rule 46(b) permits alphabets, numerals, '-' and '/'.
_INVOICE_NUMBER_SHAPE = re.compile(r"^[A-Z0-9/-]{1,16}$")

_BASE36 = _CHECKSUM_ALPHABET
#: Characters of the firm segment. 36**7 ≈ 7.8e10 — with ten thousand firms the chance
#: that any two share a segment is under one in a million, and a collision is caught by
#: the unique index rather than producing a duplicate number.
_FIRM_SEGMENT_LENGTH: Final = 7
#: Digits of the per-firm, per-financial-year sequence.
_SEQUENCE_DIGITS: Final = 4
#: Leading letter so an invoice number is recognisably ours at a glance.
_INVOICE_PREFIX: Final = "G"


def fiscal_year_code(on: date) -> str:
    """``"2627"`` for the Indian financial year 2026-27 (1 April – 31 March).

    The serial number must be unique *for a financial year*, and the Indian financial
    year is not the calendar year — an invoice issued in February 2027 belongs to
    FY 2026-27, and one issued that April starts a new series at 0001.
    """
    start_year = on.year if on.month >= 4 else on.year - 1
    return "%02d%02d" % (start_year % 100, (start_year + 1) % 100)


def fiscal_year_bounds(on: date) -> tuple[date, date]:
    """``(1 April, next 1 April)`` for the financial year containing ``on``.

    Half-open, so a ``issued_on >= start AND issued_on < end`` filter counts a year's
    invoices with no off-by-one on 31 March.
    """
    start_year = on.year if on.month >= 4 else on.year - 1
    return date(start_year, 4, 1), date(start_year + 1, 4, 1)


def _firm_segment(firm_id: uuid.UUID) -> str:
    """The firm's stable base-36 segment of the invoice number."""
    remaining = firm_id.int % (36**_FIRM_SEGMENT_LENGTH)
    out: list[str] = []
    for _ in range(_FIRM_SEGMENT_LENGTH):
        remaining, digit = divmod(remaining, 36)
        out.append(_BASE36[digit])
    return "".join(reversed(out))


def invoice_number(*, firm_id: uuid.UUID, issued_on: date, sequence: int) -> str:
    """``G`` + financial year + firm segment + sequence, exactly 16 characters.

    A per-firm series, which Rule 46(b) explicitly permits ("in one or multiple
    series"). One global consecutive series would be the other lawful option and is not
    used here for a structural reason: allocating from it means reading rows across
    every tenant, and this codebase has exactly one audited escape hatch out of
    firm-scoped queries (``garh_api.tenancy``). Trading a legally equivalent numbering
    scheme for keeping every billing query inside the tenancy layer is the right side
    of that trade.
    """
    if sequence < 1 or sequence >= 10**_SEQUENCE_DIGITS:
        raise GstError(
            "Invoice sequence %d is outside 1..%d for one firm in one financial year."
            % (sequence, 10**_SEQUENCE_DIGITS - 1)
        )
    number = "%s%s%s%0*d" % (
        _INVOICE_PREFIX,
        fiscal_year_code(issued_on),
        _firm_segment(firm_id),
        _SEQUENCE_DIGITS,
        sequence,
    )
    if len(number) > MAX_INVOICE_NUMBER_LENGTH or not _INVOICE_NUMBER_SHAPE.match(number):
        raise GstError(
            "Generated invoice number %r violates Rule 46(b) (≤16 characters, "
            "alphanumerics with - and /)." % number
        )
    return number


__all__ = [
    "CGST_RATE_X100",
    "GST_RATE_X100",
    "GST_STATE_CODES",
    "MAX_INVOICE_NUMBER_LENGTH",
    "SGST_RATE_X100",
    "SUBSCRIPTION_SAC",
    "GstError",
    "SupplierIdentity",
    "TaxBreakdown",
    "compute_tax",
    "fiscal_year_bounds",
    "fiscal_year_code",
    "gstin_checksum",
    "gstin_state_code",
    "invoice_number",
    "normalise_gstin",
    "state_name",
    "supplier_identity",
    "validate_gstin",
    "validate_state_code",
]
