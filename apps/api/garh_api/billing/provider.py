"""The billing provider interface and its factory (G-1).

The same seam ``services/llm/provider.py`` and ``services/render/provider.py`` use, for
the same reasons and with the same properties:

* selection is one environment variable — ``PROVIDER_BILLING`` (``mock`` | ``razorpay``),
  already typed in :mod:`garh_api.config`;
* **the mock is the default**, so the whole product runs, seeds and is e2e-testable with
  zero keys — including the pay-an-invoice loop;
* the real implementation is imported **inside its factory branch**, so a checkout with
  no Razorpay credentials never pays the import cost or the dependency;
* an unknown name raises at boot instead of quietly serving the mock. A typo in
  ``PROVIDER_BILLING`` that silently downgraded a production deployment to fake orders
  would mean customers "paying" and nothing arriving.

The interface is ``async`` (like the LLM provider, unlike the render one) because these
are network calls on the request path, not CPU work.

**Signature verification is synchronous and offline.** Both ``verify_*_signature``
methods are pure HMAC over strings we already hold — no network, no await. They are on
the provider because the secret is the provider's, and they are the reason a mock is
still a real test of the payment path: the mock signs with a mock secret and the same
verification code either accepts it or does not.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from garh_api.billing.types import Order, OrderRequest, PaymentSnapshot
from garh_api.config import Settings, get_settings
from garh_api.logging import get_logger

log = get_logger(__name__)

PROVIDER_NAMES: tuple[str, ...] = ("mock", "razorpay")


@runtime_checkable
class BillingProvider(Protocol):
    """Open an order, look a payment up, and prove a callback really came from us."""

    #: Stable identifier stored on ``billing_payments.provider``.
    name: str

    async def create_order(self, req: OrderRequest) -> Order:
        """Open an order for ``req.amount_inr``. Raises ``BillingProviderError``."""
        ...

    async def fetch_payment(self, payment_id: str) -> PaymentSnapshot:
        """What the provider currently says about a payment."""
        ...

    def verify_payment_signature(self, *, order_id: str, payment_id: str, signature: str) -> bool:
        """True when ``signature`` is the provider's HMAC over ``order_id|payment_id``.

        This is the check that stands between "the browser told us it paid" and "the
        money is ours". It must be constant-time and it must fail closed.
        """
        ...

    def verify_webhook_signature(self, *, body: bytes, signature: str) -> bool:
        """True when ``signature`` is the provider's HMAC over the raw webhook body."""
        ...

    async def aclose(self) -> None:
        """Release any transport. Safe to call twice."""
        ...


def get_billing_provider(settings: Settings | None = None) -> BillingProvider:
    """Build the provider named by ``PROVIDER_BILLING``.

    Raises ``ValueError`` on an unknown name, and on ``razorpay`` with no credentials —
    a half-configured payment provider must stop the process, not take an order it
    cannot settle.
    """
    cfg = settings or get_settings()
    provider_name = cfg.provider_billing

    if provider_name == "mock":
        from garh_api.billing.mock import MockBillingProvider

        return MockBillingProvider()

    if provider_name == "razorpay":
        # Imported here so the mock path never loads it.
        from garh_api.billing.razorpay_provider import RazorpayBillingProvider

        if not cfg.razorpay_key_id or not cfg.razorpay_key_secret:
            raise ValueError(
                "PROVIDER_BILLING=razorpay but RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET are "
                "empty. Set them, or use PROVIDER_BILLING=mock (the default) to run "
                "without a payment gateway."
            )
        log.info(
            "billing.provider.selected", provider="razorpay", key_id_len=len(cfg.razorpay_key_id)
        )
        return RazorpayBillingProvider(cfg)

    raise ValueError(
        "Unknown PROVIDER_BILLING=%r. Expected one of: %s."
        % (provider_name, ", ".join(PROVIDER_NAMES))
    )


__all__ = ["PROVIDER_NAMES", "BillingProvider", "get_billing_provider"]
