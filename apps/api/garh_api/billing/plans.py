"""The plan catalogue and what each plan entitles a firm to (G-2).

A plan is four things: a monthly price in whole rupees, a number of included editor
seats, a price for seats beyond that, and a monthly allowance per metered kind.

THE ONE THING THAT MUST NOT DRIFT
---------------------------------
The allowance keys are the *meter's own vocabulary* —
:data:`garh_api.models.CREDIT_EVENT_KINDS` — and :func:`_validate_catalogue` asserts
that at import time, per plan, in both directions.

This is not defensive tidiness. This repository has already shipped the exact failure it
prevents: 83 rules went inert because a context default (``buildingUse="residential"``)
was not a member of the packs' own enum, so every rule reported ``not_applicable`` while
the report still looked green (CLAUDE.md, bug 2). A quota table keyed ``"renders"`` while
``credit_events.kind`` is written as ``"render"`` fails the same way and looks the same
from outside: the meter fills up, the quota is never reached, and nobody finds out until
the third-party bill arrives. An import-time assertion turns that into a boot failure.

ALLOWANCE SEMANTICS — three distinct values, none of them interchangeable
------------------------------------------------------------------------
* ``None``  — unmetered. The gate admits without counting.
* ``0``     — not included in this plan. The gate denies the first unit. The free plan's
              ``export`` allowance is 0 on purpose: the municipal drawing set is the
              product an Indian architect's fee is actually earned on, so it is the one
              thing the free tier does not do.
* ``n > 0`` — n units per billing period, counted against ``credit_events``.

An unknown plan code raises :class:`UnknownPlanError`. It does **not** fall back to a
plan: a typo in a database column must not silently hand somebody the enterprise
allowance, and must not silently reduce a paying firm to the free one either.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from garh_api.models import CREDIT_EVENT_KINDS

#: The metered kinds a plan carries an allowance for. Bound to the meter's own enum so
#: the two cannot drift — see the module docstring.
QUOTA_KINDS: Final[tuple[str, ...]] = CREDIT_EVENT_KINDS


class UnknownPlanError(LookupError):
    """A plan code that is not in the catalogue. Never falls back to a default plan."""


class PlanCatalogueError(RuntimeError):
    """The catalogue in this module is internally inconsistent. Raised at import."""


@dataclass(frozen=True)
class Plan:
    """One row of the price list. Immutable; the catalogue is authored, not stored."""

    code: str
    name: str
    #: Whole rupees per month, exclusive of GST (B2B SaaS convention — the invoice adds
    #: CGST+SGST or IGST on top; see :mod:`garh_api.billing.gst`).
    price_inr_per_month: int
    #: Editor seats the price includes. Viewer seats are free and unlimited — a client
    #: reviewing a scheme uses a share link, and charging for that would be charging the
    #: architect for their own client.
    included_editor_seats: int
    #: Whole rupees per month for each editor seat beyond the included count. ``None``
    #: means this plan cannot buy extra seats (upgrade instead).
    extra_seat_inr_per_month: int | None
    #: kind → units per billing period. See "ALLOWANCE SEMANTICS" in the module docstring.
    allowances: Mapping[str, int | None]
    #: One line, shown on the plan card.
    summary: str

    def allowance(self, kind: str) -> int | None:
        """Allowance for one metered kind.

        Raises on a kind the meter does not know, rather than returning ``None``
        (unmetered) — "I don't recognise this" must never read as "no limit".
        """
        if kind not in QUOTA_KINDS:
            raise UnknownPlanError(
                "%r is not a metered kind. Expected one of: %s." % (kind, ", ".join(QUOTA_KINDS))
            )
        return self.allowances[kind]


#: The catalogue. Prices are seed values in the same sense the rule packs' numbers are:
#: defensible, market-shaped, and to be replaced by whoever signs the pricing page.
PLANS: Final[tuple[Plan, ...]] = (
    Plan(
        code="free",
        name="Free",
        price_inr_per_month=0,
        included_editor_seats=1,
        extra_seat_inr_per_month=None,
        allowances={"solver": 10, "render": 5, "llm": 100, "export": 0},
        summary="One architect, ten plan generations a month, no drawing exports.",
    ),
    Plan(
        code="studio",
        name="Studio",
        price_inr_per_month=4_999,
        included_editor_seats=3,
        extra_seat_inr_per_month=1_499,
        allowances={"solver": 150, "render": 100, "llm": 1_500, "export": 100},
        summary="A small practice: three architects, the full municipal drawing set.",
    ),
    Plan(
        code="practice",
        name="Practice",
        price_inr_per_month=14_999,
        included_editor_seats=10,
        extra_seat_inr_per_month=1_299,
        allowances={"solver": 600, "render": 500, "llm": 6_000, "export": 400},
        summary="Ten architects, priority solving, four hundred drawing sets a month.",
    ),
    Plan(
        code="enterprise",
        name="Enterprise",
        price_inr_per_month=49_999,
        included_editor_seats=40,
        extra_seat_inr_per_month=999,
        allowances={"solver": None, "render": None, "llm": None, "export": None},
        summary="Forty seats, unmetered generation, drawings and renders.",
    ),
)

#: Code → plan.
_BY_CODE: Final[dict[str, Plan]] = {plan.code: plan for plan in PLANS}

#: Every valid ``billing_subscriptions.plan_code``. Mirrors the CHECK constraint.
PLAN_CODES: Final[tuple[str, ...]] = tuple(plan.code for plan in PLANS)

#: What a firm gets when it has never subscribed, and what a lapsed subscription falls
#: back to. Named rather than spelled ``"free"`` at four call sites.
DEFAULT_PLAN_CODE: Final = "free"


def _validate_catalogue() -> None:
    """Fail at import if the catalogue is inconsistent. See the module docstring.

    Four checks, each guarding a way this table has a live failure mode:

    1. allowance keys ≡ ``CREDIT_EVENT_KINDS`` — a quota keyed off the meter's
       vocabulary never fires (bug class 2);
    2. no duplicate codes — a duplicate silently shadows a plan in ``_BY_CODE``;
    3. no negative prices or seat counts — a negative price is a credit note, and this
       module cannot express one;
    4. the default plan exists — otherwise every un-subscribed firm 500s.
    """
    expected = set(QUOTA_KINDS)
    for plan in PLANS:
        keys = set(plan.allowances)
        if keys != expected:
            raise PlanCatalogueError(
                "Plan %r meters %s but credit_events.kind is %s. A quota keyed off a "
                "kind the meter never writes can never deny anything."
                % (plan.code, sorted(keys), sorted(expected))
            )
        for kind, value in plan.allowances.items():
            if value is not None and (not isinstance(value, int) or value < 0):
                raise PlanCatalogueError(
                    "Plan %r allowance for %r must be a non-negative int or None (unmetered), "
                    "got %r." % (plan.code, kind, value)
                )
        if plan.price_inr_per_month < 0 or plan.included_editor_seats < 0:
            raise PlanCatalogueError("Plan %r has a negative price or seat count." % plan.code)
        if plan.extra_seat_inr_per_month is not None and plan.extra_seat_inr_per_month < 0:
            raise PlanCatalogueError("Plan %r has a negative extra-seat price." % plan.code)
    if len(_BY_CODE) != len(PLANS):
        raise PlanCatalogueError("Two plans share a code; one of them is unreachable.")
    if DEFAULT_PLAN_CODE not in _BY_CODE:
        raise PlanCatalogueError(
            "DEFAULT_PLAN_CODE=%r is not in the catalogue." % DEFAULT_PLAN_CODE
        )


_validate_catalogue()


def plan_for(code: str) -> Plan:
    """The plan with this code, or :class:`UnknownPlanError`. Never a fallback."""
    try:
        return _BY_CODE[code]
    except KeyError:
        raise UnknownPlanError(
            "%r is not a plan. Expected one of: %s." % (code, ", ".join(PLAN_CODES))
        ) from None


def default_plan() -> Plan:
    """The free plan — what an un-subscribed or lapsed firm is entitled to."""
    return _BY_CODE[DEFAULT_PLAN_CODE]


def seat_entitlement(plan: Plan, extra_seats: int) -> int:
    """Editor seats a firm may assign: the plan's included seats plus purchased extras.

    A plan that cannot sell extra seats (``extra_seat_inr_per_month is None``) ignores
    ``extra_seats`` entirely rather than honouring a number nobody could have paid for.
    """
    if extra_seats < 0:
        raise ValueError("extra_seats cannot be negative.")
    if plan.extra_seat_inr_per_month is None:
        return plan.included_editor_seats
    return plan.included_editor_seats + extra_seats


def monthly_charge_inr(plan: Plan, extra_seats: int) -> int:
    """Recurring charge before tax: the plan price plus any purchased extra seats."""
    entitled_extra = 0 if plan.extra_seat_inr_per_month is None else max(0, extra_seats)
    per_seat = plan.extra_seat_inr_per_month or 0
    return plan.price_inr_per_month + per_seat * entitled_extra


__all__ = [
    "DEFAULT_PLAN_CODE",
    "PLANS",
    "PLAN_CODES",
    "QUOTA_KINDS",
    "Plan",
    "PlanCatalogueError",
    "UnknownPlanError",
    "default_plan",
    "monthly_charge_inr",
    "plan_for",
    "seat_entitlement",
]
