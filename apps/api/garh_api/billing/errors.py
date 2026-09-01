"""Billing failures, as problem+json (golden rule 9: errors say what to do next).

Every class here derives from :class:`garh_api.errors.ApiError`, which is what
``install_error_handlers`` dispatches on — a separate base would fall through to the
catch-all and turn a deliberate "you are out of renders" into a 500.

Status codes are chosen, not defaulted:

* **402 Payment Required** for a quota or seat limit. It is the one status whose plain
  meaning is "this is a billing decision, not a bug and not a permission problem", and
  the client can branch on it to show the upgrade path rather than an error toast.
  429 would be wrong (nothing is rate limited — waiting does not help) and 403 would be
  wrong (the caller has every right, the firm has not bought the capacity).
* **409 Conflict** when the request contradicts durable state — issuing an invoice for
  a period that already has one, paying an invoice that is already paid.
* **422** for a GSTIN or state code that cannot be what the caller says it is.
* **503** when the deployment itself is not configured to issue tax invoices.
"""

from __future__ import annotations

from garh_api.errors import ApiError


class SpendCapExceededError(ApiError):
    """This architect has spent their whole generation budget.

    402, like :class:`QuotaExceededError`, and for the same reason — it is a billing
    answer, not a fault. Separate from it because the two fail for different reasons
    and an architect can hit either while the other has room: the quota counts CALLS
    against a plan, this counts DOLLARS against a one-off budget.
    """

    http_status = 402
    code = "spend_cap_exceeded"
    default_message = "You've used your generation budget."
    action = "Ask your administrator to raise the budget."

    @classmethod
    def for_spend(cls, *, spent_micros: int, cap_micros: int, kind: str) -> SpendCapExceededError:
        from garh_api.billing.spend import format_usd

        return cls(
            "Generating uses a budget of %s, and %s of it is spent."
            % (format_usd(cap_micros), format_usd(spent_micros)),
            extra={
                "kind": kind,
                "spentUsd": format_usd(spent_micros),
                "capUsd": format_usd(cap_micros),
                "spentMicros": spent_micros,
                "capMicros": cap_micros,
            },
        )


class QuotaExceededError(ApiError):
    """The firm has used its monthly allowance for this metered kind."""

    http_status = 402
    code = "quota_exceeded"
    default_message = "This month's allowance for that is used up."
    action = "Upgrade the plan, or wait for the next billing period."

    @classmethod
    def for_kind(
        cls, *, kind: str, used: int, allowance: int, plan_code: str
    ) -> QuotaExceededError:
        return cls(
            "Your %s plan includes %d %s per billing period and %d have been used."
            % (plan_code, allowance, kind, used),
            extra={
                "kind": kind,
                "used": used,
                "allowance": allowance,
                "planCode": plan_code,
            },
        )


class SeatLimitError(ApiError):
    """Every editor seat the firm pays for is already assigned."""

    http_status = 402
    code = "seat_limit_reached"
    default_message = "Every editor seat on this plan is taken."
    action = "Release a seat, buy an extra one, or move to a larger plan."


class PlanChangeError(ApiError):
    """The requested plan cannot be applied to the firm as it stands."""

    http_status = 409
    code = "plan_change_refused"
    default_message = "That plan change can't be applied right now."
    action = "Release the seats above the new plan's limit, then try again."


class BillingProfileIncompleteError(ApiError):
    """No billing account, or one missing something an invoice must carry."""

    http_status = 409
    code = "billing_profile_incomplete"
    default_message = "Your billing details are incomplete."
    action = "Fill in the firm's legal name and state under Billing, then try again."


class InvalidGstDetailsError(ApiError):
    """A GSTIN or state code that is not one."""

    http_status = 422
    code = "invalid_gst_details"
    default_message = "Those GST details aren't valid."
    action = "Check the GSTIN against the certificate of registration and re-enter it."


class InvoiceStateError(ApiError):
    """The invoice is not in a state where this makes sense."""

    http_status = 409
    code = "invoice_state"
    default_message = "That invoice can't be changed like that."
    action = "Reload the invoice to see its current state."


class PaymentVerificationError(ApiError):
    """The payment signature did not verify. Never say more than that.

    Deliberately terse: the difference between "unknown order" and "bad signature" is
    exactly the oracle someone forging a callback wants, and neither answer helps a
    legitimate customer, whose checkout widget produces the right signature or none.
    """

    http_status = 400
    code = "payment_not_verified"
    default_message = "That payment could not be verified."
    action = "Refresh the invoice — if the money left your account, contact support."


class BillingUnavailableError(ApiError):
    """The gateway is unreachable, or this deployment cannot issue tax invoices."""

    http_status = 503
    code = "billing_unavailable"
    default_message = "Billing is unavailable right now."
    action = "Try again in a few minutes. Nothing has been charged."


__all__ = [
    "BillingProfileIncompleteError",
    "BillingUnavailableError",
    "InvalidGstDetailsError",
    "InvoiceStateError",
    "PaymentVerificationError",
    "PlanChangeError",
    "QuotaExceededError",
    "SeatLimitError",
    "SpendCapExceededError",
]
