"""The zero-key billing provider. The default everywhere, including CI and the demo seed.

What it is honest about: no money moves. What it is *not* a fake of — and this is the
point — is the security-relevant half of the payment path. The mock signs its orders
with a real HMAC-SHA256 under :data:`MOCK_KEY_SECRET`, using byte-for-byte the same
message construction Razorpay documents (``order_id|payment_id``), and
``verify_payment_signature`` is the same constant-time comparison the real adapter
performs. So the branch that decides "this invoice is paid" is exercised for real by
every test and every demo, and a tampered signature is rejected under the mock exactly
as it would be in production.

Determinism: order and payment ids are derived from the receipt (our invoice number) by
SHA-256, so the same invoice always yields the same ids. A seeded demo therefore looks
the same on every machine, and a test can assert an id without reaching for a fixture.
"""

from __future__ import annotations

import hashlib
import hmac

from garh_api.billing.money import rupees_to_paise
from garh_api.billing.types import BillingProviderError, Order, OrderRequest, PaymentSnapshot

#: The mock's "key secret". Not a credential: it protects nothing, it is in the source,
#: and it exists so signature verification is a real code path under the mock. A real
#: secret never appears in this repository.
MOCK_KEY_SECRET = "garh-mock-billing-secret"

#: Prefixes mirror Razorpay's, so anything that pattern-matches an id in a log or a
#: dashboard behaves the same under both providers.
ORDER_PREFIX = "order_"
PAYMENT_PREFIX = "pay_"


def _digest(*parts: str) -> str:
    """A stable 14-character id body from the parts, base-16."""
    joined = "|".join(parts).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()[:14]


class MockBillingProvider:
    """Deterministic orders and real signatures, with no gateway behind them."""

    name = "mock"

    def __init__(self, key_secret: str = MOCK_KEY_SECRET) -> None:
        self._key_secret = key_secret

    # -- orders --------------------------------------------------------
    async def create_order(self, req: OrderRequest) -> Order:
        if req.amount_inr <= 0:
            # Same refusal the real gateway gives: Razorpay rejects a zero-amount order.
            # A free plan must not reach a checkout at all, and this is the backstop
            # that makes "it can't" true rather than assumed.
            raise BillingProviderError("An order must be for a positive amount.", code="bad_amount")
        return Order(
            provider=self.name,
            order_id=ORDER_PREFIX + _digest("order", req.receipt),
            amount_paise=rupees_to_paise(req.amount_inr),
            currency=req.currency,
            status="created",
            receipt=req.receipt,
        )

    async def fetch_payment(self, payment_id: str) -> PaymentSnapshot:
        """A mock payment is always captured — there is nothing to fail.

        The order id cannot be recovered from a hash, so it comes back empty; callers
        reconcile through the ``billing_payments`` row they already hold, which is what
        the real path does too (the gateway's answer is a cross-check, not the source
        of truth).
        """
        if not payment_id.startswith(PAYMENT_PREFIX):
            raise BillingProviderError("Unknown payment %r." % payment_id, code="not_found")
        return PaymentSnapshot(
            provider=self.name,
            payment_id=payment_id,
            order_id="",
            amount_paise=0,
            currency="INR",
            status="captured",
            captured=True,
            method="mock",
        )

    # -- signatures ----------------------------------------------------
    def _sign(self, message: str) -> str:
        return hmac.new(
            self._key_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def verify_payment_signature(self, *, order_id: str, payment_id: str, signature: str) -> bool:
        expected = self._sign("%s|%s" % (order_id, payment_id))
        return hmac.compare_digest(expected, (signature or "").strip())

    def verify_webhook_signature(self, *, body: bytes, signature: str) -> bool:
        expected = hmac.new(self._key_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, (signature or "").strip())

    # -- the mock-only affordance --------------------------------------
    def simulate_payment(self, order_id: str) -> tuple[str, str]:
        """``(payment_id, signature)`` as the checkout widget would hand them back.

        MOCK ONLY, and deliberately not on the :class:`~garh_api.billing.provider.
        BillingProvider` protocol: nothing in ``garh_api`` outside tests and the demo
        seed may call it, because a production caller of "pretend this was paid" is a
        fraud path. It exists so the pay-an-invoice journey is walkable end to end with
        no keys — the same reason the render mock draws a watermarked image.
        """
        payment_id = PAYMENT_PREFIX + _digest("payment", order_id)
        return payment_id, self._sign("%s|%s" % (order_id, payment_id))

    async def aclose(self) -> None:
        """Nothing to release."""


__all__ = ["MOCK_KEY_SECRET", "MockBillingProvider"]
