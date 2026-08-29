"""RazorpayBillingProvider — taking money in India, key-only (G-1).

Razorpay rather than Stripe because the customer is an Indian architecture practice:
UPI, RuPay, netbanking from 50-odd banks and card EMI are what an Indian firm actually
pays with, and Stripe India onboarding is not open to most of them. ``PROVIDER_BILLING=
razorpay`` plus ``RAZORPAY_KEY_ID``/``RAZORPAY_KEY_SECRET`` is the entire setup — the
same "a config flip, not a rewrite" contract the Stability render provider has.

WHAT THIS CALLS, AND WHY THOSE THREE
------------------------------------
The Orders API, not the Payment Links or Subscriptions API:

* ``POST /v1/orders`` — open an order for one invoice. Our invoice number goes in
  ``receipt``, so a payment in the Razorpay dashboard is reconcilable to a GST invoice
  by eye, and the firm/invoice ids go in ``notes``.
* ``GET /v1/payments/{id}`` — the server-side truth about a payment. The browser's
  claim that it paid is never enough on its own.
* ``POST /v1/payments/{id}/capture`` — for the case where auto-capture is off and the
  payment comes back merely ``authorized``.

Razorpay Subscriptions was considered and rejected for now: it would move the plan
catalogue and the invoice numbering to their side, and this product must
issue its own **GST-compliant** invoice with our HSN/SAC and place-of-supply split
either way (see :mod:`garh_api.billing.gst`). One authoritative invoice, generated here,
with the gateway used only to collect — not two half-invoices.

VERIFICATION IS THE SECURITY BOUNDARY
-------------------------------------
``verify_payment_signature`` is HMAC-SHA256 over ``"{order_id}|{payment_id}"`` keyed
with the **key secret**, exactly as documented for the checkout handler. It is compared
with :func:`hmac.compare_digest`, so a wrong signature leaks no timing information.
``verify_webhook_signature`` is the same construction over the raw request body keyed
with the separate **webhook secret**, and it **fails closed when that secret is unset** —
an unconfigured webhook secret must mean "no callback is trusted", never "every callback
is trusted".

NOT RUN AGAINST THE LIVE API
----------------------------
There is no Razorpay key on this machine and none is invented here. The wire format is
pinned by ``tests/test_billing_core.py`` ("Razorpay adapter, against a strict transport
double") against a strict ``httpx.MockTransport``
double that asserts the method, path, auth header, JSON body and units of every call —
the same way ``services/render/stability_provider.py`` was built and for the same
reason. A live key remains a launch gate.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

import httpx

from garh_api.billing.money import rupees_to_paise
from garh_api.billing.types import BillingProviderError, Order, OrderRequest, PaymentSnapshot
from garh_api.config import Settings, get_settings
from garh_api.logging import get_logger

log = get_logger(__name__)

#: Razorpay's API root. Overridable for a proxy or a sandbox, never for a test — tests
#: inject a transport instead.
DEFAULT_BASE_URL = "https://api.razorpay.com"

#: Razorpay caps ``receipt`` at 40 characters. Our invoice numbers are 16 (Rule 46(b)),
#: so this is a guard against a caller passing something else, not a real constraint.
MAX_RECEIPT_LENGTH = 40

#: Seconds. A payment call that has not answered in this long has failed as far as the
#: architect clicking "Pay" is concerned.
TIMEOUT_SECONDS = 20.0

#: The webhook secret is configured **separately** from the API key pair in the Razorpay
#: dashboard, so it is its own environment variable. Read here rather than added to
#: ``garh_api.config`` because that module is owned elsewhere (see the handoff note);
#: ``garh_api.deps`` sets the precedent for a module-level ``os.environ`` read.
WEBHOOK_SECRET_ENV = "RAZORPAY_WEBHOOK_SECRET"


class RazorpayBillingProvider:
    """Orders, payments and signatures against Razorpay's documented REST API."""

    name = "razorpay"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str | None = None,
    ) -> None:
        cfg = settings or get_settings()
        if not cfg.razorpay_key_id or not cfg.razorpay_key_secret:
            raise ValueError(
                "RazorpayBillingProvider needs RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET."
            )
        self._key_id = cfg.razorpay_key_id
        self._key_secret = cfg.razorpay_key_secret
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        #: Test seam: an ``httpx.MockTransport`` here makes the suite hermetic without
        #: monkeypatching httpx internals. ``None`` = the real network.
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------
    def _http(self) -> httpx.AsyncClient:
        """One client per provider instance, built lazily.

        Basic auth with the key pair is what Razorpay documents; httpx builds the
        header, so the secret is never formatted into a string in this module.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                auth=(self._key_id, self._key_secret),
                timeout=TIMEOUT_SECONDS,
                transport=self._transport,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None) -> Any:
        try:
            response = await self._http().request(method, path, json=payload)
        except httpx.HTTPError as exc:
            # The exception text can contain the URL but never the auth header; still,
            # only the class name is logged and the message is ours.
            log.warning("billing.razorpay.transport_error", error=type(exc).__name__, path=path)
            raise BillingProviderError("The payment gateway could not be reached.") from exc
        if response.status_code >= 400:
            raise self._error_for(response)
        try:
            return response.json()
        except ValueError as exc:
            raise BillingProviderError(
                "The payment gateway returned something that was not JSON."
            ) from exc

    def _error_for(self, response: httpx.Response) -> BillingProviderError:
        """Razorpay's error envelope → our error, with the description preserved.

        The description is the part a support ticket needs ("Order amount less than
        minimum amount allowed"), and it carries no credential.
        """
        description = "The payment gateway refused the request."
        code: str | None = None
        try:
            body = response.json()
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                code = str(error.get("code") or "") or None
                description = str(error.get("description") or description)
        except ValueError:
            pass
        log.warning(
            "billing.razorpay.error", status=response.status_code, code=code, path=response.url.path
        )
        return BillingProviderError(description, status=response.status_code, code=code)

    # ------------------------------------------------------------------
    # orders and payments
    # ------------------------------------------------------------------
    async def create_order(self, req: OrderRequest) -> Order:
        if req.amount_inr <= 0:
            raise BillingProviderError("An order must be for a positive amount.", code="bad_amount")
        if len(req.receipt) > MAX_RECEIPT_LENGTH:
            raise BillingProviderError(
                "The receipt reference is longer than the gateway allows.", code="bad_receipt"
            )
        payload: dict[str, Any] = {
            # Paise. The one conversion, from the one helper.
            "amount": rupees_to_paise(req.amount_inr),
            "currency": req.currency,
            "receipt": req.receipt,
            "notes": dict(req.notes),
        }
        data = await self._request("POST", "/v1/orders", payload)
        order_id = str(data.get("id") or "")
        if not order_id:
            raise BillingProviderError("The gateway created an order with no id.")
        amount_paise = int(data.get("amount") or 0)
        if amount_paise != payload["amount"]:
            # A gateway that echoes a different amount than we asked for is the one
            # failure that must never be papered over: it would mean charging the
            # customer something we did not invoice.
            raise BillingProviderError(
                "The gateway opened an order for a different amount than requested.",
                code="amount_mismatch",
            )
        return Order(
            provider=self.name,
            order_id=order_id,
            amount_paise=amount_paise,
            currency=str(data.get("currency") or req.currency),
            status=str(data.get("status") or "created"),
            receipt=str(data.get("receipt") or req.receipt),
        )

    async def fetch_payment(self, payment_id: str) -> PaymentSnapshot:
        clean = (payment_id or "").strip()
        if not clean:
            raise BillingProviderError("A payment id is required.", code="bad_payment_id")
        data = await self._request("GET", "/v1/payments/%s" % clean, None)
        return self._snapshot(data)

    async def capture_payment(self, payment_id: str, *, amount_inr: int) -> PaymentSnapshot:
        """Capture an authorised payment.

        Only needed when auto-capture is off on the account. Capturing an already
        captured payment is an error at Razorpay, so callers check ``captured`` first.
        """
        data = await self._request(
            "POST",
            "/v1/payments/%s/capture" % (payment_id or "").strip(),
            {"amount": rupees_to_paise(amount_inr), "currency": "INR"},
        )
        return self._snapshot(data)

    def _snapshot(self, data: Any) -> PaymentSnapshot:
        if not isinstance(data, dict):
            raise BillingProviderError("The gateway returned an unexpected payment shape.")
        status = str(data.get("status") or "")
        return PaymentSnapshot(
            provider=self.name,
            payment_id=str(data.get("id") or ""),
            order_id=str(data.get("order_id") or ""),
            amount_paise=int(data.get("amount") or 0),
            currency=str(data.get("currency") or "INR"),
            status=status,
            # Razorpay reports both a boolean and a status; trust the pair, and only
            # call it captured when they agree. "authorized" money is not ours yet.
            captured=bool(data.get("captured")) and status == "captured",
            method=str(data["method"]) if data.get("method") else None,
        )

    # ------------------------------------------------------------------
    # signatures
    # ------------------------------------------------------------------
    def verify_payment_signature(self, *, order_id: str, payment_id: str, signature: str) -> bool:
        """HMAC-SHA256(``order_id|payment_id``) under the key secret, constant-time."""
        if not order_id or not payment_id or not signature:
            return False
        expected = hmac.new(
            self._key_secret.encode("utf-8"),
            ("%s|%s" % (order_id, payment_id)).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature.strip())

    def verify_webhook_signature(self, *, body: bytes, signature: str) -> bool:
        """HMAC-SHA256(raw body) under the **webhook** secret. Fails closed if unset."""
        secret = (os.environ.get(WEBHOOK_SECRET_ENV, "") or "").strip()
        if not secret:
            log.warning("billing.razorpay.webhook_secret_missing", env=WEBHOOK_SECRET_ENV)
            return False
        if not signature:
            return False
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature.strip())


__all__ = ["DEFAULT_BASE_URL", "WEBHOOK_SECRET_ENV", "RazorpayBillingProvider"]
