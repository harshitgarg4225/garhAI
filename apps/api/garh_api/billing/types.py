"""The data a billing provider takes and returns. No provider imports this from outside.

Deliberately frozen dataclasses rather than Pydantic models: these never cross the HTTP
boundary in either direction (``schemas/billing.py`` owns that), and a dataclass keeps
the provider layer importable without dragging validation machinery into a worker.

**Units.** Everything we hold is whole rupees. Everything a gateway speaks is paise.
The boundary is :class:`OrderRequest`, which carries ``amount_inr``, and :class:`Order`,
which carries ``amount_paise`` because that is what the gateway echoed back. Conversion
happens once, in :func:`garh_api.billing.money.rupees_to_paise`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


class BillingProviderError(RuntimeError):
    """The payment provider could not be reached, or refused the request.

    Carries no key material and no request body — a provider error is logged, and a
    logged secret is a leaked secret.
    """

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class OrderRequest:
    """Ask the provider to open an order the customer can pay against.

    ``receipt`` is our invoice number — the gateway echoes it back on every webhook and
    in the dashboard, which is what makes a payment reconcilable to an invoice by hand.

    ``notes`` are provider-side key/value metadata. They must carry **ids only**: the
    firm id and the invoice id. Never a name, an email or an address — those would be
    PII shipped to a third party for no operational gain, which is the §13 rule the LLM
    summariser already lives under.
    """

    amount_inr: int
    receipt: str
    notes: Mapping[str, str] = field(default_factory=dict)
    currency: str = "INR"


@dataclass(frozen=True)
class Order:
    """An open order at the provider. ``order_id`` is what the checkout widget needs."""

    provider: str
    order_id: str
    amount_paise: int
    currency: str
    status: str
    receipt: str


@dataclass(frozen=True)
class PaymentSnapshot:
    """What the provider says about one payment, right now."""

    provider: str
    payment_id: str
    order_id: str
    amount_paise: int
    currency: str
    status: str
    #: ``captured`` at Razorpay means the money is ours; ``authorized`` means it is
    #: only held. The distinction is the difference between an invoice that is paid and
    #: one that is not.
    captured: bool
    method: str | None = None


__all__ = ["BillingProviderError", "Order", "OrderRequest", "PaymentSnapshot"]
