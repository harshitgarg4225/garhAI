"""The billing surface over real HTTP against a real Postgres (G-1 … G-4).

Everything in this file goes through the ASGI app and the real repositories: plans,
quota denial, GST invoices with their CGST/SGST vs IGST split, the gateway order and
its signature check, seats, and the cross-tenant guarantee on every route.

THREE GATES ARE NEGATIVE-TESTED HERE, because each is a gate that could plausibly
never fire:

* the **quota** — the same request succeeds at the allowance and is refused one unit
  over it, through a route that mounts ``require_quota`` exactly as a spending route
  will (:func:`test_the_quota_denies_the_unit_over_the_allowance`);
* the **seat limit** — the first seat is granted and the second refused on a one-seat
  plan (:func:`test_the_seat_gate_denies_the_seat_beyond_the_plan`);
* the **payment signature** — the settle call is refused with a tampered signature and
  the invoice stays unpaid, then accepted with the real one
  (:func:`test_a_tampered_signature_cannot_pay_an_invoice`).

MOUNTING: ``garh_api.routers.api_router`` includes the billing router, so the app these
tests drive is the production app — the ``billing_app`` fixture adds nothing but one
probe route for the quota dependency. ``test_the_billing_router_is_mounted_on_the_real_app``
asserts that from the plain ``app`` fixture, so an accidental un-mounting is a red test
rather than a silently unreachable package.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import APIRouter
from garh_api import models
from garh_api.billing.mock import MockBillingProvider
from garh_api.billing.models import BILLING_METADATA, BILLING_TABLES
from garh_api.billing.quotas import check_quota, require_quota
from garh_api.billing.repositories import PaymentRepository, SubscriptionRepository
from garh_api.billing.subscriptions import ensure_subscription, month_bounds
from garh_api.routers import billing as billing_router
from sqlalchemy import text

from tests.helpers import problem

pytestmark = pytest.mark.integration

#: Checksum-valid, fictitious registrations (see ``test_billing_core`` for the algorithm
#: they were generated with). Karnataka is where the supplier is, so a Karnataka customer
#: is an intra-state supply and a Maharashtra one is inter-state.
SUPPLIER_GSTIN = "29AABCG1234H1ZV"
KARNATAKA_GSTIN = "29AAAAA0000A1ZY"
MAHARASHTRA_GSTIN = "27AAAAA0000A1Z2"

STUDIO_PRICE_INR = 4_999
STUDIO_TOTAL_INR = 5_899  # 4999 + 450 + 450 (or + 900 IGST)

_TRUNCATE_BILLING = "TRUNCATE TABLE %s RESTART IDENTITY CASCADE" % ", ".join(
    '"%s"' % name for name in BILLING_TABLES
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def billing_schema(database: Any) -> Any:
    """The five billing tables, rebuilt from ``BILLING_METADATA``.

    Dropped first, not merely created-if-absent: these tables are not in
    ``garh_api.models.ALL_TABLES``, so the session-scoped ``database`` fixture never
    touches them, and a stale definition left by an earlier revision of this package
    would otherwise silently persist and make the suite test the wrong schema.
    ``0003_billing`` is what production runs, and ``test_billing_migration.py`` proves
    the two agree.
    """
    BILLING_METADATA.drop_all(database)
    BILLING_METADATA.create_all(database)
    return database


@pytest.fixture
def clean_billing(billing_schema: Any, clean_db: None) -> Any:
    """Empty the billing tables before each test.

    ``conftest.clean_db`` truncates ``ALL_TABLES``, which these five are not part of —
    without this, invoices would leak between tests and the invoice-serial assertions
    would depend on execution order.
    """
    with billing_schema.begin() as connection:
        connection.execute(text(_TRUNCATE_BILLING))
    return billing_schema


@pytest.fixture
def billing_app(clean_billing: Any, clean_redis: Any, settings: Any) -> Any:
    """The real app — which already includes the billing router — plus a quota probe.

    The probe route isolates ``require_quota``: it mounts the dependency exactly as
    ``routers/jobs.py`` does (``dependencies=[require_quota("render")]``) and does nothing
    else, so a 402 from it is a 402 the dependency produced and not a side effect of a job
    pipeline. The billing router itself is NOT included here — ``api_router`` mounts it —
    so these tests cannot pass against a router that production does not serve.
    """
    from garh_api.main import create_app

    app = create_app(settings)

    probe = APIRouter()

    @probe.post("/quota-probe/render", dependencies=[require_quota("render")])
    async def _render_probe() -> dict[str, bool]:
        return {"started": True}

    app.include_router(probe, prefix=settings.api_prefix)
    return app


@pytest.fixture
async def billing_client(billing_app: Any) -> Any:
    transport = httpx.ASGITransport(app=billing_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as client:
        yield client


@pytest.fixture
def supplier_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured GST registration for the deployment, so invoices can be issued."""
    monkeypatch.setenv("BILLING_SUPPLIER_LEGAL_NAME", "Garh Technologies Private Limited")
    monkeypatch.setenv("BILLING_SUPPLIER_GSTIN", SUPPLIER_GSTIN)
    monkeypatch.setenv("BILLING_SUPPLIER_ADDRESS", "4th Cross, Indiranagar, Bengaluru 560038")
    monkeypatch.delenv("BILLING_SUPPLIER_STATE_CODE", raising=False)


@pytest.fixture
def no_supplier_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "BILLING_SUPPLIER_LEGAL_NAME",
        "BILLING_SUPPLIER_GSTIN",
        "BILLING_SUPPLIER_ADDRESS",
        "BILLING_SUPPLIER_STATE_CODE",
    ):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def walk_routes(app: Any) -> list[Any]:
    """Flatten the route table, the same way ``tests/test_cross_tenant.py`` does.

    fastapi 0.141 leaves lazy ``_IncludedRouter`` wrappers in ``app.routes`` whose
    children surface only through ``effective_candidates()`` — their ``routes`` list is
    empty. Reading ``app.routes`` alone finds zero billing routes, and every assertion
    below would then pass over an empty list, which is the vacuous-green shape the
    ``>= 15`` assertions exist to catch.
    """
    found: list[Any] = []
    stack = list(app.routes)
    while stack:
        item = stack.pop()
        children = getattr(item, "routes", None)
        if children:
            stack.extend(children)
            continue
        candidates = getattr(item, "effective_candidates", None)
        if callable(candidates):
            stack.extend(candidates())
            continue
        if hasattr(item, "dependant"):
            found.append(item)
    return found


async def _set_account(
    client: httpx.AsyncClient, api: str, actor: Any, *, state_code: str, gstin: str | None
) -> httpx.Response:
    return await client.put(
        "%s/billing/account" % api,
        headers=actor.headers,
        json={
            "legalName": "%s LLP" % actor.firm_name,
            "stateCode": state_code,
            "gstin": gstin,
            "addressLine": "12 MG Road",
            "city": "Bengaluru",
            "postalCode": "560001",
            "billingEmail": "accounts@studio.test",
        },
    )


async def _set_plan(
    client: httpx.AsyncClient, api: str, actor: Any, plan_code: str, *, extra_seats: int = 0
) -> httpx.Response:
    return await client.put(
        "%s/billing/subscription" % api,
        headers=actor.headers,
        json={"planCode": plan_code, "extraSeats": extra_seats},
    )


async def _issue_invoice(
    client: httpx.AsyncClient, api: str, actor: Any, *, state_code: str, gstin: str | None
) -> dict[str, Any]:
    assert (
        await _set_account(client, api, actor, state_code=state_code, gstin=gstin)
    ).status_code == 200
    assert (await _set_plan(client, api, actor, "studio")).status_code == 200
    response = await client.post("%s/billing/invoices" % api, headers=actor.headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _record_credits(session: Any, actor: Any, kind: str, count: int) -> None:
    """Write ``count`` metered events through the real repository."""
    from garh_api.repositories import CreditEventRepository

    repo = CreditEventRepository(session, actor.ctx())
    for _ in range(count):
        await repo.record(kind=kind, qty=1)
    await session.commit()


# ---------------------------------------------------------------------------
# Plans, subscription, usage (G-2)
# ---------------------------------------------------------------------------


async def test_the_price_list_is_served_with_the_firms_current_plan(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any
) -> None:
    response = await billing_client.get("%s/billing/plans" % api, headers=firm_a.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["currentPlanCode"] == "free"
    codes = [plan["code"] for plan in body["plans"]]
    assert codes == ["free", "studio", "practice", "enterprise"]
    # Every plan quotes an allowance for every kind the meter writes.
    for plan in body["plans"]:
        kinds = {row["kind"] for row in plan["allowances"]}
        assert kinds == set(models.CREDIT_EVENT_KINDS), plan["code"]
    free = next(plan for plan in body["plans"] if plan["code"] == "free")
    # 0 is "not included", and it is the drawing export — the thing the fee is earned on.
    assert next(row for row in free["allowances"] if row["kind"] == "export")["allowance"] == 0


async def test_a_firm_that_never_subscribed_is_on_the_free_plan(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any
) -> None:
    """A missing subscription row must read as "free", never as "unmetered"."""
    response = await billing_client.get("%s/billing/subscription" % api, headers=firm_a.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["planCode"] == "free"
    assert body["effectivePlanCode"] == "free"
    assert body["seatsEntitled"] == 1
    assert body["monthlyChargeInr"] == 0
    # The period is the calendar month, so a quota resets on a date a firm can predict.
    start = datetime.fromisoformat(body["currentPeriodStart"])
    end = datetime.fromisoformat(body["currentPeriodEnd"])
    assert start.day == 1 and end.day == 1 and end > start


async def test_usage_reports_the_credit_events_the_product_already_writes(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, session: Any
) -> None:
    await _record_credits(session, firm_a, "render", 3)
    await _record_credits(session, firm_a, "solver", 1)

    response = await billing_client.get("%s/billing/usage" % api, headers=firm_a.headers)
    assert response.status_code == 200, response.text
    lines = {row["kind"]: row for row in response.json()["lines"]}
    assert lines["render"]["used"] == 3
    assert lines["render"]["allowance"] == 5 and lines["render"]["remaining"] == 2
    assert lines["solver"]["used"] == 1
    assert lines["export"]["allowance"] == 0 and lines["export"]["remaining"] == 0


async def test_the_quota_denies_the_unit_over_the_allowance(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, session: Any
) -> None:
    """THE GATE, with its negative control: 200 at the allowance, 402 one unit over.

    The free plan includes five renders. With four recorded the probe route — which
    mounts ``require_quota("render")`` exactly as a spending route will — admits the
    request. With five recorded it refuses with 402 and names the plan and the numbers.
    """
    await _record_credits(session, firm_a, "render", 4)
    allowed = await billing_client.post("%s/quota-probe/render" % api, headers=firm_a.headers)
    assert allowed.status_code == 200, allowed.text

    await _record_credits(session, firm_a, "render", 1)
    denied = await billing_client.post("%s/quota-probe/render" % api, headers=firm_a.headers)
    assert denied.status_code == 402, denied.text
    body = problem(denied)
    assert body["code"] == "quota_exceeded"
    assert body["kind"] == "render" and body["used"] == 5 and body["allowance"] == 5
    assert body["planCode"] == "free"


async def test_the_real_render_route_refuses_once_the_plan_allowance_is_spent(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, project_a: Any, session: Any
) -> None:
    """The gate on a route that actually spends money, not on the probe.

    ``require_quota`` was mounted on nothing when this package landed, so every test of
    it ran against a probe route invented by the test file — a gate proven to work in a
    place production does not have. This drives ``POST /projects/:id/renders``, the real
    route, and requires the 402 there.

    The five-render free allowance is spent with real ``credit_events`` rows. Below it
    the route answers whatever the render pipeline answers (there is no saved design
    version here, so a 409) — the assertion is only that it is **not** the quota's 402.
    At the allowance it is.
    """
    url = "%s/projects/%s/renders" % (api, project_a.id)
    body = {"mode": "explore"}

    await _record_credits(session, firm_a, "render", 4)
    under = await billing_client.post(url, headers=firm_a.headers, json=body)
    assert under.status_code != 402, (
        "the quota denied a firm that is under its allowance: %s" % under.text[:300]
    )

    await _record_credits(session, firm_a, "render", 1)
    denied = await billing_client.post(url, headers=firm_a.headers, json=body)
    assert denied.status_code == 402, denied.text
    problem_body = problem(denied)
    assert problem_body["code"] == "quota_exceeded"
    assert problem_body["kind"] == "render" and problem_body["planCode"] == "free"


async def test_a_paid_plan_lifts_the_quota_that_denied(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, session: Any
) -> None:
    """The denial is about the plan, not about the meter: upgrading admits the request."""
    await _record_credits(session, firm_a, "render", 6)
    assert (
        await billing_client.post("%s/quota-probe/render" % api, headers=firm_a.headers)
    ).status_code == 402

    assert (await _set_plan(billing_client, api, firm_a, "studio")).status_code == 200
    assert (
        await billing_client.post("%s/quota-probe/render" % api, headers=firm_a.headers)
    ).status_code == 200


async def test_the_quota_counts_only_the_current_billing_period(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, session: Any
) -> None:
    """Usage from before the period start must not count against this period.

    Written straight through the ORM because the repository (rightly) will not let a
    caller choose ``created_at`` — and a window that silently included last month would
    deny a paying firm on the first of the month.
    """
    stale = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=3
    )
    for _ in range(9):
        session.add(
            models.CreditEvent(firm_id=firm_a.firm_id, kind="render", qty=1, created_at=stale)
        )
    await session.commit()

    response = await billing_client.get("%s/billing/usage" % api, headers=firm_a.headers)
    lines = {row["kind"]: row for row in response.json()["lines"]}
    assert lines["render"]["used"] == 0, "last month's usage leaked into this period"
    assert (
        await billing_client.post("%s/quota-probe/render" % api, headers=firm_a.headers)
    ).status_code == 200


async def test_the_quota_still_denies_after_the_billing_period_rolls_over(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, session: Any
) -> None:
    """THE GATE THAT WENT INERT, and the clock that used to switch it off.

    Nothing in this product renews a subscription. The row a firm gets when it
    subscribes carries a one-month window and no scheduler ever moves it, so once the
    wall clock passed ``current_period_end`` every later ``credit_event`` fell OUTSIDE
    ``usage_by_kind(since=period_start, until=period_end)``. Usage read zero, the
    allowance was never reached, and the 402 could never be raised again — bug class 1
    from CLAUDE.md, "a gate that silently never fires", with a one-month fuse.

    So: park a real subscription row three months in the past, spend six renders *now*
    (one over the free plan's five), and require the refusal anyway. With the stored
    window returned verbatim this reads 200.
    """
    ctx = firm_a.ctx()
    subscription = await ensure_subscription(session, ctx)
    stale_start, stale_end = month_bounds(datetime.now(UTC) - timedelta(days=95))
    await SubscriptionRepository(session, ctx).update(
        subscription.id, period_start=stale_start, period_end=stale_end
    )
    await session.commit()
    assert stale_end < datetime.now(UTC), "the fixture window must really be in the past"

    await _record_credits(session, firm_a, "render", 6)

    usage = await billing_client.get("%s/billing/usage" % api, headers=firm_a.headers)
    assert usage.status_code == 200, usage.text
    body = usage.json()
    this_month_start, this_month_end = month_bounds(datetime.now(UTC))
    assert datetime.fromisoformat(body["periodStart"]) == this_month_start, (
        "the usage window is still the stale stored one — every later credit event "
        "falls outside it and the quota can never deny again"
    )
    assert datetime.fromisoformat(body["periodEnd"]) == this_month_end
    render = next(row for row in body["lines"] if row["kind"] == "render")
    assert render["used"] == 6 and render["remaining"] == 0

    denied = await billing_client.post("%s/quota-probe/render" % api, headers=firm_a.headers)
    assert denied.status_code == 402, denied.text
    assert problem(denied)["used"] == 6

    # The subscription page reports the same rolled window, so the customer is told
    # which period they are being metered against.
    shown = (
        await billing_client.get("%s/billing/subscription" % api, headers=firm_a.headers)
    ).json()
    assert datetime.fromisoformat(shown["currentPeriodStart"]) == this_month_start


async def test_an_admin_write_persists_the_rolled_period_onto_the_row(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, session: Any
) -> None:
    """There is no scheduler, so the write paths are the renewal path.

    ``entitlement`` already answers with the live window whether or not anything has
    persisted it — that is what keeps the gate honest — but the stored row must catch up
    too, because the next invoice's period comes off it and a stale one would issue a
    duplicate invoice for a period already billed.
    """
    ctx = firm_a.ctx()
    subscription = await ensure_subscription(session, ctx)
    stale_start, stale_end = month_bounds(datetime.now(UTC) - timedelta(days=95))
    await SubscriptionRepository(session, ctx).update(
        subscription.id, period_start=stale_start, period_end=stale_end
    )
    await session.commit()

    assert (await _set_plan(billing_client, api, firm_a, "studio")).status_code == 200

    session.expire_all()
    row = await SubscriptionRepository(session, ctx).get_for_firm()
    assert row is not None
    this_month_start, this_month_end = month_bounds(datetime.now(UTC))
    assert (row.current_period_start, row.current_period_end) == (
        this_month_start,
        this_month_end,
    ), "the renewal path left the stored window in the past"
    assert row.status == "active"


async def test_a_subscription_cancelled_at_period_end_really_stops_at_the_period_end(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, session: Any
) -> None:
    """Rolling the window forward must not renew a subscription the firm cancelled.

    ``cancel_at_period_end`` means "keep what you paid for until the period ends". Once
    it has, the firm is on free allowances — otherwise the roll-forward that fixed one
    permanently-open gate would have opened another, handing a cancelled firm its paid
    allowance every month for ever.
    """
    ctx = firm_a.ctx()
    assert (await _set_plan(billing_client, api, firm_a, "practice")).status_code == 200
    subscription = await ensure_subscription(session, ctx)
    stale_start, stale_end = month_bounds(datetime.now(UTC) - timedelta(days=40))
    await SubscriptionRepository(session, ctx).update(
        subscription.id,
        period_start=stale_start,
        period_end=stale_end,
        cancel_at_period_end=True,
    )
    await session.commit()

    body = (
        await billing_client.get("%s/billing/subscription" % api, headers=firm_a.headers)
    ).json()
    assert body["status"] == "cancelled"
    assert body["planCode"] == "practice" and body["effectivePlanCode"] == "free"

    # ...and the free plan's five renders are what actually applies now.
    await _record_credits(session, firm_a, "render", 5)
    denied = await billing_client.post("%s/quota-probe/render" % api, headers=firm_a.headers)
    assert denied.status_code == 402, denied.text
    assert problem(denied)["planCode"] == "free"


async def test_a_past_due_subscription_drops_to_free_allowances_but_keeps_its_seats(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, session: Any
) -> None:
    """A failed card stops the spend. It does not lock a ten-person practice out."""
    assert (
        await _set_plan(billing_client, api, firm_a, "practice", extra_seats=2)
    ).status_code == 200

    ctx = firm_a.ctx()
    subscription = await ensure_subscription(session, ctx)
    await SubscriptionRepository(session, ctx).update(subscription.id, status="past_due")
    await session.commit()

    body = (
        await billing_client.get("%s/billing/subscription" % api, headers=firm_a.headers)
    ).json()
    assert body["planCode"] == "practice" and body["effectivePlanCode"] == "free"
    # Seats follow the paid plan: 10 included + 2 bought.
    assert body["seatsEntitled"] == 12

    usage = (await billing_client.get("%s/billing/usage" % api, headers=firm_a.headers)).json()
    render = next(row for row in usage["lines"] if row["kind"] == "render")
    assert render["allowance"] == 5, "a lapsed subscription kept its paid allowance"


async def test_check_quota_refuses_a_kind_the_meter_never_writes(
    clean_billing: Any, session: Any, firm_a: Any
) -> None:
    """A typo'd kind is a programming error, never an admission.

    This is the bug-class-2 guard at the call site: "I don't recognise this kind" must
    not fall through to "no limit applies".
    """
    with pytest.raises(ValueError, match="not a metered kind"):
        await check_quota(session, firm_a.ctx(), "renders")
    with pytest.raises(ValueError, match="not a metered kind"):
        require_quota("renders")


# ---------------------------------------------------------------------------
# Billing account and GST validation (G-3)
# ---------------------------------------------------------------------------


async def test_the_billing_account_round_trips(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any
) -> None:
    response = await _set_account(
        billing_client, api, firm_a, state_code="29", gstin=KARNATAKA_GSTIN
    )
    assert response.status_code == 200, response.text
    assert response.json()["stateName"] == "Karnataka"

    fetched = await billing_client.get("%s/billing/account" % api, headers=firm_a.headers)
    assert fetched.status_code == 200
    assert fetched.json()["gstin"] == KARNATAKA_GSTIN


async def test_a_gstin_with_a_bad_check_digit_is_refused(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any
) -> None:
    """NEGATIVE CONTROL for the check digit, over HTTP.

    A wrong GSTIN on an issued invoice costs the customer their input tax credit, and an
    issued invoice cannot be edited — so the validation has to happen here.
    """
    bad = KARNATAKA_GSTIN[:-1] + ("A" if KARNATAKA_GSTIN[-1] != "A" else "B")
    response = await _set_account(billing_client, api, firm_a, state_code="29", gstin=bad)
    assert response.status_code == 422, response.text
    assert problem(response)["code"] == "invalid_gst_details"


async def test_a_gstin_from_another_state_than_the_place_of_supply_is_refused(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any
) -> None:
    """Those two disagreeing is what flips an invoice between CGST/SGST and IGST."""
    response = await _set_account(
        billing_client, api, firm_a, state_code="29", gstin=MAHARASHTRA_GSTIN
    )
    assert response.status_code == 422
    assert "Maharashtra" in problem(response)["message"]


async def test_an_unregistered_customer_may_have_no_gstin(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any
) -> None:
    """A sole practitioner under the threshold is a real customer with no registration."""
    response = await _set_account(billing_client, api, firm_a, state_code="33", gstin=None)
    assert response.status_code == 200, response.text
    assert response.json()["gstin"] is None
    assert response.json()["stateName"] == "Tamil Nadu"


# ---------------------------------------------------------------------------
# Invoices (G-3)
# ---------------------------------------------------------------------------


async def test_no_invoice_is_issued_without_our_own_gst_registration(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, no_supplier_env: None
) -> None:
    """A document that looks like a tax invoice without a supplier GSTIN is worse than none."""
    await _set_account(billing_client, api, firm_a, state_code="29", gstin=KARNATAKA_GSTIN)
    await _set_plan(billing_client, api, firm_a, "studio")
    response = await billing_client.post("%s/billing/invoices" % api, headers=firm_a.headers)
    assert response.status_code == 503, response.text
    assert problem(response)["code"] == "billing_unavailable"


async def test_no_invoice_is_issued_without_the_customers_billing_details(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, supplier_env: None
) -> None:
    await _set_plan(billing_client, api, firm_a, "studio")
    response = await billing_client.post("%s/billing/invoices" % api, headers=firm_a.headers)
    assert response.status_code == 409, response.text
    assert problem(response)["code"] == "billing_profile_incomplete"


async def test_the_free_plan_has_nothing_to_invoice(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, supplier_env: None
) -> None:
    await _set_account(billing_client, api, firm_a, state_code="29", gstin=KARNATAKA_GSTIN)
    response = await billing_client.post("%s/billing/invoices" % api, headers=firm_a.headers)
    assert response.status_code == 409, response.text
    assert problem(response)["code"] == "invoice_state"


async def test_an_intra_state_invoice_splits_into_equal_cgst_and_sgst(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, supplier_env: None
) -> None:
    """Supplier in Karnataka, customer in Karnataka → CGST 9% + SGST 9%, and Rule 46."""
    invoice = await _issue_invoice(
        billing_client, api, firm_a, state_code="29", gstin=KARNATAKA_GSTIN
    )

    assert invoice["taxableInr"] == STUDIO_PRICE_INR
    assert invoice["cgstInr"] == 450 and invoice["sgstInr"] == 450 and invoice["igstInr"] == 0
    assert invoice["taxTotalInr"] == 900
    assert invoice["totalInr"] == STUDIO_TOTAL_INR
    assert invoice["totalInr"] == (
        invoice["taxableInr"] + invoice["cgstInr"] + invoice["sgstInr"] + invoice["igstInr"]
    )
    assert invoice["interstate"] is False

    # Rule 46 fields a customer's accountant looks for.
    assert invoice["supplierGstin"] == SUPPLIER_GSTIN
    assert invoice["customerGstin"] == KARNATAKA_GSTIN
    assert invoice["placeOfSupply"] == "Karnataka"
    assert invoice["ratePercentX100"] == 1800
    assert len(invoice["invoiceNumber"]) <= 16
    assert invoice["lines"] and invoice["lines"][0]["hsnSac"] == "997331"
    assert invoice["totalInWords"] == "Rupees Five Thousand Eight Hundred Ninety Nine Only"
    assert invoice["status"] == "issued" and invoice["currency"] == "INR"


async def test_an_inter_state_invoice_is_a_single_igst_line(
    billing_client: httpx.AsyncClient, api: str, firm_b: Any, supplier_env: None
) -> None:
    """Supplier in Karnataka, customer in Maharashtra → IGST 18%, one line, no CGST."""
    invoice = await _issue_invoice(
        billing_client, api, firm_b, state_code="27", gstin=MAHARASHTRA_GSTIN
    )
    assert invoice["cgstInr"] == 0 and invoice["sgstInr"] == 0
    assert invoice["igstInr"] == 900
    assert invoice["totalInr"] == STUDIO_TOTAL_INR
    assert invoice["interstate"] is True
    assert invoice["placeOfSupply"] == "Maharashtra"


async def test_a_second_invoice_for_the_same_period_is_refused(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, supplier_env: None
) -> None:
    """Rule 46 numbering is consecutive; a period is corrected by a credit note."""
    first = await _issue_invoice(
        billing_client, api, firm_a, state_code="29", gstin=KARNATAKA_GSTIN
    )
    again = await billing_client.post("%s/billing/invoices" % api, headers=firm_a.headers)
    assert again.status_code == 409, again.text
    body = problem(again)
    assert body["code"] == "invoice_state"
    assert body["invoiceNumber"] == first["invoiceNumber"]


async def test_invoices_are_listed_newest_first_and_fetchable_by_id(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, supplier_env: None
) -> None:
    invoice = await _issue_invoice(
        billing_client, api, firm_a, state_code="29", gstin=KARNATAKA_GSTIN
    )
    listed = await billing_client.get("%s/billing/invoices" % api, headers=firm_a.headers)
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["items"]] == [invoice["id"]]

    one = await billing_client.get(
        "%s/billing/invoices/%s" % (api, invoice["id"]), headers=firm_a.headers
    )
    assert one.status_code == 200 and one.json()["invoiceNumber"] == invoice["invoiceNumber"]


# ---------------------------------------------------------------------------
# Payments (G-1)
# ---------------------------------------------------------------------------


async def test_checkout_opens_an_order_for_the_invoice_total_in_paise(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, supplier_env: None
) -> None:
    invoice = await _issue_invoice(
        billing_client, api, firm_a, state_code="29", gstin=KARNATAKA_GSTIN
    )
    response = await billing_client.post(
        "%s/billing/invoices/%s/checkout" % (api, invoice["id"]), headers=firm_a.headers
    )
    assert response.status_code == 200, response.text
    checkout = response.json()
    assert checkout["amountInr"] == STUDIO_TOTAL_INR
    # The same money in the gateway's unit, exactly — never a rounded conversion.
    assert checkout["amountPaise"] == STUDIO_TOTAL_INR * 100
    assert checkout["provider"] == "mock" and checkout["orderId"].startswith("order_")


async def test_checking_out_the_same_invoice_twice_is_idempotent_not_a_500(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, session: Any, supplier_env: None
) -> None:
    """A second click on "Pay" used to be an unhandled 500.

    ``billing_payments.provider_order_id`` is globally unique and the mock provider
    derives the order id deterministically from the invoice number, so the second insert
    violated ``uq_billing_payments_provider_order_id`` and the ``IntegrityError`` escaped
    as a server error. A customer reloading the checkout page is not an exceptional
    event, and one invoice should have one open order — so the second call returns the
    first one, byte for byte, and writes no second row.
    """
    invoice = await _issue_invoice(
        billing_client, api, firm_a, state_code="29", gstin=KARNATAKA_GSTIN
    )
    url = "%s/billing/invoices/%s/checkout" % (api, invoice["id"])

    first = await billing_client.post(url, headers=firm_a.headers)
    assert first.status_code == 200, first.text
    second = await billing_client.post(url, headers=firm_a.headers)
    assert second.status_code == 200, second.text
    assert second.json() == first.json(), "a re-checkout answered with a different order"

    third = await billing_client.post(url, headers=firm_a.headers)
    assert third.status_code == 200, third.text

    rows = await PaymentRepository(session, firm_a.ctx()).list_for_invoice(uuid.UUID(invoice["id"]))
    assert len(rows) == 1, "each checkout opened another gateway order for one invoice"
    assert rows[0].provider_order_id == first.json()["orderId"]
    assert rows[0].amount_inr == STUDIO_TOTAL_INR

    # The reused order is a real order: it still settles.
    payment_id, signature = MockBillingProvider().simulate_payment(second.json()["orderId"])
    settled = await billing_client.post(
        "%s/billing/payments/verify" % api,
        headers=firm_a.headers,
        json={"orderId": second.json()["orderId"], "paymentId": payment_id, "signature": signature},
    )
    assert settled.status_code == 200, settled.text
    assert settled.json()["invoice"]["status"] == "paid"


async def test_a_tampered_signature_cannot_pay_an_invoice(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, supplier_env: None
) -> None:
    """THE GATE, with its negative control: a wrong signature settles nothing.

    First a tampered signature is refused and the invoice is still ``issued``; then the
    provider's real signature settles it. Under the mock this is the same HMAC check the
    Razorpay adapter performs, so "the browser said it paid" is never enough.
    """
    invoice = await _issue_invoice(
        billing_client, api, firm_a, state_code="29", gstin=KARNATAKA_GSTIN
    )
    checkout = (
        await billing_client.post(
            "%s/billing/invoices/%s/checkout" % (api, invoice["id"]), headers=firm_a.headers
        )
    ).json()
    payment_id, signature = MockBillingProvider().simulate_payment(checkout["orderId"])

    tampered = ("0" if signature[0] != "0" else "1") + signature[1:]
    refused = await billing_client.post(
        "%s/billing/payments/verify" % api,
        headers=firm_a.headers,
        json={
            "orderId": checkout["orderId"],
            "paymentId": payment_id,
            "signature": tampered,
        },
    )
    assert refused.status_code == 400, refused.text
    assert problem(refused)["code"] == "payment_not_verified"

    still = await billing_client.get(
        "%s/billing/invoices/%s" % (api, invoice["id"]), headers=firm_a.headers
    )
    assert still.json()["status"] == "issued", "a bad signature moved the invoice to paid"
    assert still.json()["paidAt"] is None

    accepted = await billing_client.post(
        "%s/billing/payments/verify" % api,
        headers=firm_a.headers,
        json={
            "orderId": checkout["orderId"],
            "paymentId": payment_id,
            "signature": signature,
        },
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["invoice"]["status"] == "paid" and body["invoice"]["paidAt"] is not None
    assert body["payment"]["signatureVerified"] is True
    assert body["payment"]["status"] == "captured"
    assert body["payment"]["amountInr"] == STUDIO_TOTAL_INR


async def test_a_paid_invoice_cannot_be_checked_out_again(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, supplier_env: None
) -> None:
    invoice = await _issue_invoice(
        billing_client, api, firm_a, state_code="29", gstin=KARNATAKA_GSTIN
    )
    checkout = (
        await billing_client.post(
            "%s/billing/invoices/%s/checkout" % (api, invoice["id"]), headers=firm_a.headers
        )
    ).json()
    payment_id, signature = MockBillingProvider().simulate_payment(checkout["orderId"])
    await billing_client.post(
        "%s/billing/payments/verify" % api,
        headers=firm_a.headers,
        json={"orderId": checkout["orderId"], "paymentId": payment_id, "signature": signature},
    )
    again = await billing_client.post(
        "%s/billing/invoices/%s/checkout" % (api, invoice["id"]), headers=firm_a.headers
    )
    assert again.status_code == 409
    assert problem(again)["code"] == "invoice_state"


# ---------------------------------------------------------------------------
# Seats (G-4)
# ---------------------------------------------------------------------------


async def test_the_seat_gate_denies_the_seat_beyond_the_plan(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, member_a: Any
) -> None:
    """THE GATE, with its negative control: seat one is granted, seat two refused.

    The free plan includes exactly one editor seat, so the admin takes it and the member
    cannot have one until the plan grows.
    """
    first = await billing_client.post(
        "%s/billing/seats" % api,
        headers=firm_a.headers,
        json={"userId": str(firm_a.user_id), "seatType": "editor"},
    )
    assert first.status_code == 201, first.text

    second = await billing_client.post(
        "%s/billing/seats" % api,
        headers=firm_a.headers,
        json={"userId": str(member_a.user_id), "seatType": "editor"},
    )
    assert second.status_code == 402, second.text
    body = problem(second)
    assert body["code"] == "seat_limit_reached"
    assert body["entitled"] == 1 and body["used"] == 1

    # A viewer seat is free and unlimited — a client looking at a scheme costs nothing.
    viewer = await billing_client.post(
        "%s/billing/seats" % api,
        headers=firm_a.headers,
        json={"userId": str(member_a.user_id), "seatType": "viewer"},
    )
    assert viewer.status_code == 201, viewer.text

    summary = (await billing_client.get("%s/billing/seats" % api, headers=firm_a.headers)).json()
    assert summary["entitled"] == 1
    assert summary["editorsUsed"] == 1 and summary["viewersUsed"] == 1
    assert summary["available"] == 0


async def test_a_bigger_plan_grants_the_seat_that_was_refused(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, member_a: Any
) -> None:
    await billing_client.post(
        "%s/billing/seats" % api,
        headers=firm_a.headers,
        json={"userId": str(firm_a.user_id), "seatType": "editor"},
    )
    assert (await _set_plan(billing_client, api, firm_a, "studio")).status_code == 200
    granted = await billing_client.post(
        "%s/billing/seats" % api,
        headers=firm_a.headers,
        json={"userId": str(member_a.user_id), "seatType": "editor"},
    )
    assert granted.status_code == 201, granted.text


async def test_a_downgrade_that_would_strand_a_seat_is_refused(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, member_a: Any
) -> None:
    """Better a 409 naming the number than silently revoking somebody by row order."""
    await _set_plan(billing_client, api, firm_a, "studio")
    for user_id in (firm_a.user_id, member_a.user_id):
        created = await billing_client.post(
            "%s/billing/seats" % api,
            headers=firm_a.headers,
            json={"userId": str(user_id), "seatType": "editor"},
        )
        assert created.status_code == 201, created.text

    refused = await _set_plan(billing_client, api, firm_a, "free")
    assert refused.status_code == 409, refused.text
    body = problem(refused)
    assert body["code"] == "plan_change_refused"
    assert body["assigned"] == 2 and body["entitledAfter"] == 1

    # Release one, and the same downgrade goes through.
    seats = (await billing_client.get("%s/billing/seats" % api, headers=firm_a.headers)).json()
    released = await billing_client.delete(
        "%s/billing/seats/%s" % (api, seats["seats"][0]["id"]), headers=firm_a.headers
    )
    assert released.status_code == 204
    assert (await _set_plan(billing_client, api, firm_a, "free")).status_code == 200


async def test_a_user_cannot_hold_two_seats(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any
) -> None:
    body = {"userId": str(firm_a.user_id), "seatType": "editor"}
    assert (
        await billing_client.post("%s/billing/seats" % api, headers=firm_a.headers, json=body)
    ).status_code == 201
    duplicate = await billing_client.post(
        "%s/billing/seats" % api, headers=firm_a.headers, json=body
    )
    assert duplicate.status_code == 409
    assert problem(duplicate)["code"] == "plan_change_refused"


async def test_a_seat_cannot_be_given_to_another_firms_user(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, firm_b: Any
) -> None:
    """The seat table has no FK to ``users``; the firm-scoped lookup is the real check."""
    response = await billing_client.post(
        "%s/billing/seats" % api,
        headers=firm_a.headers,
        json={"userId": str(firm_b.user_id), "seatType": "editor"},
    )
    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# Roles and tenancy
# ---------------------------------------------------------------------------


async def test_a_member_may_read_billing_but_not_change_it(
    billing_client: httpx.AsyncClient, api: str, firm_a: Any, member_a: Any, supplier_env: None
) -> None:
    """An architect sees what is left; only an admin spends the practice's money."""
    assert (
        await billing_client.get("%s/billing/usage" % api, headers=member_a.headers)
    ).status_code == 200
    assert (
        await billing_client.get("%s/billing/plans" % api, headers=member_a.headers)
    ).status_code == 200

    for response in (
        await _set_account(billing_client, api, member_a, state_code="29", gstin=KARNATAKA_GSTIN),
        await _set_plan(billing_client, api, member_a, "studio"),
        await billing_client.post("%s/billing/invoices" % api, headers=member_a.headers),
        await billing_client.post(
            "%s/billing/seats" % api,
            headers=member_a.headers,
            json={"userId": str(member_a.user_id), "seatType": "editor"},
        ),
        await billing_client.get("%s/billing/account" % api, headers=member_a.headers),
    ):
        assert response.status_code == 403, response.request.url


async def test_firm_b_cannot_reach_firm_as_billing(
    billing_client: httpx.AsyncClient,
    api: str,
    firm_a: Any,
    firm_b: Any,
    supplier_env: None,
) -> None:
    """Firm B holds a valid token and firm A's real ids, and gets 404 on every one.

    ``tests/test_cross_tenant.py`` now lists ``invoice_id``/``seat_id`` in
    ``TENANT_SCOPED_PARAMS`` and carries a ``Case`` for each of these three routes, so
    they are covered by that guard *by default* rather than by this file remembering. The
    duplication is deliberate: this one also asserts the negative halves (firm B's own
    invoice list and seat list come back empty), which the sweep there does not.
    """
    invoice = await _issue_invoice(
        billing_client, api, firm_a, state_code="29", gstin=KARNATAKA_GSTIN
    )
    seat = (
        await billing_client.post(
            "%s/billing/seats" % api,
            headers=firm_a.headers,
            json={"userId": str(firm_a.user_id), "seatType": "editor"},
        )
    ).json()

    cases = [
        ("GET", "%s/billing/invoices/%s" % (api, invoice["id"]), None),
        ("POST", "%s/billing/invoices/%s/checkout" % (api, invoice["id"]), None),
        ("DELETE", "%s/billing/seats/%s" % (api, seat["id"]), None),
    ]
    for method, url, body in cases:
        response = await billing_client.request(method, url, headers=firm_b.headers, json=body)
        assert response.status_code == 404, "%s %s → %s" % (method, url, response.status_code)

    # Firm B sees none of firm A's invoices in its own list.
    listed = await billing_client.get("%s/billing/invoices" % api, headers=firm_b.headers)
    assert listed.json()["items"] == []
    # ...and none of its seats.
    seats = await billing_client.get("%s/billing/seats" % api, headers=firm_b.headers)
    assert seats.json()["seats"] == []


async def test_firm_b_cannot_settle_firm_as_order_even_with_a_valid_signature(
    billing_client: httpx.AsyncClient,
    api: str,
    firm_a: Any,
    firm_b: Any,
    supplier_env: None,
) -> None:
    """The sharpest tenancy case in this package.

    An order id is not a secret — it is in the checkout widget's JavaScript — and under
    the mock anyone can compute a *genuinely valid* signature for one. What stops firm B
    from marking firm A's invoice paid is that the payment lookup is firm-scoped, so the
    order simply does not exist for them.
    """
    invoice = await _issue_invoice(
        billing_client, api, firm_a, state_code="29", gstin=KARNATAKA_GSTIN
    )
    checkout = (
        await billing_client.post(
            "%s/billing/invoices/%s/checkout" % (api, invoice["id"]), headers=firm_a.headers
        )
    ).json()
    payment_id, signature = MockBillingProvider().simulate_payment(checkout["orderId"])

    stolen = await billing_client.post(
        "%s/billing/payments/verify" % api,
        headers=firm_b.headers,
        json={"orderId": checkout["orderId"], "paymentId": payment_id, "signature": signature},
    )
    assert stolen.status_code == 400
    assert problem(stolen)["code"] == "payment_not_verified"

    unpaid = await billing_client.get(
        "%s/billing/invoices/%s" % (api, invoice["id"]), headers=firm_a.headers
    )
    assert unpaid.json()["status"] == "issued"


async def test_an_unauthenticated_caller_reaches_nothing(
    billing_client: httpx.AsyncClient, api: str
) -> None:
    for path in ("/billing/plans", "/billing/usage", "/billing/invoices", "/billing/seats"):
        response = await billing_client.get(api + path)
        assert response.status_code == 401, path


#: Handlers that write a ``credit_events`` row but deliberately do NOT carry
#: ``require_quota``, each with the reason it is not a one-line mount. Anything metered
#: and not listed here must be gated, or
#: :func:`test_every_metered_route_is_gated_or_listed_as_deliberately_ungated` fails.
UNGATED_ON_PURPOSE: dict[tuple[str, str], str] = {
    ("start_export", "export"): (
        "The free plan's export allowance is 0 ON PURPOSE (plans.py: the municipal set "
        "is the one thing the free tier does not do), so mounting this flips every "
        "free-tier export to 402. That is the intended product behaviour but not a "
        "one-line change: the demo seed must move to a paid plan first. Handoff."
    ),
    ("archive_render_pack", "export"): (
        "Same export allowance as start_export, and it is the png-pack half of the same "
        "decision. Handoff, with start_export."
    ),
    ("start_client_pack", "render"): (
        "Meters qty=len(shots), so a static require_quota('render') with qty=1 would "
        "admit a twelve-shot pack against one unit of allowance — an under-check reads "
        "as a gate while letting the spend through. Wants an in-handler check_quota "
        "with the real quantity. Handoff."
    ),
}

#: Routes that must carry the gate, and the kind each must carry.
QUOTA_GATED_ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/projects/{project_id}/solve"): "solver",
    ("POST", "/projects/{project_id}/renders"): "render",
    ("POST", "/projects/{project_id}/copilot"): "llm",
    ("POST", "/projects/{project_id}/brief/parse"): "llm",
}


def _mounted_quota_kinds(route: Any) -> set[str]:
    """The kinds ``require_quota`` is mounted for on one route.

    Identified by the closure ``require_quota`` builds, and the ``kind`` it closed over —
    not by a name a router could shadow and not by reading the source.
    """
    kinds: set[str] = set()
    stack = list(route.dependant.dependencies)
    while stack:
        dependency = stack.pop()
        call = dependency.call
        if getattr(call, "__qualname__", "").startswith("require_quota."):
            frees = getattr(call, "__code__", None)
            cells = getattr(call, "__closure__", None) or ()
            if frees is not None:
                closed = dict(
                    zip(
                        frees.co_freevars,
                        (cell.cell_contents for cell in cells),
                        strict=False,
                    )
                )
                kind = closed.get("kind")
                if isinstance(kind, str):
                    kinds.add(kind)
        stack.extend(dependency.dependencies)
    return kinds


def _metered_handlers() -> dict[str, set[str]]:
    """``handler name -> {credit kinds it records}``, read off the routers' own source.

    An AST scan rather than a maintained list, so a new spending route is covered the
    moment it writes its first ``credit_events`` row.
    """
    import ast
    from pathlib import Path

    routers = Path(__file__).resolve().parent.parent / "garh_api" / "routers"
    found: dict[str, set[str]] = {}
    for path in sorted(routers.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(function):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr != "record":
                    continue
                receiver = node.func.value
                if not (
                    isinstance(receiver, ast.Call)
                    and isinstance(receiver.func, ast.Name)
                    and receiver.func.id == "CreditEventRepository"
                ):
                    continue
                for keyword in node.keywords:
                    if keyword.arg == "kind" and isinstance(keyword.value, ast.Constant):
                        found.setdefault(function.name, set()).add(str(keyword.value.value))
    return found


def test_the_quota_is_mounted_on_the_spending_routes(app_routes: Any, settings: Any) -> None:
    """A quota mounted on no route is a gate that cannot fire.

    ``require_quota`` was fully implemented, unit-tested and mounted on **nothing**: a
    solve, a render and a copilot call all ran unmetered against the plan. That is bug
    class 4 (a module that believes it is registered) and there is no compile-time signal
    for it, so this reads the live dependency graph and requires the mount.
    """
    by_route = {
        (method, route.path): _mounted_quota_kinds(route)
        for route in walk_routes(app_routes)
        for method in (getattr(route, "methods", None) or ())
        if method not in ("HEAD", "OPTIONS")
    }
    missing: list[str] = []
    for (method, path), kind in QUOTA_GATED_ROUTES.items():
        full = "%s%s" % (settings.api_prefix, path)
        mounted = by_route.get((method, full))
        if mounted is None:
            missing.append("%s %s — route not found at all" % (method, full))
        elif kind not in mounted:
            missing.append(
                "%s %s — expected require_quota(%r), found %s"
                % (method, full, kind, sorted(mounted) or "no quota dependency")
            )
    assert not missing, "\n  ".join(["the quota gate is not mounted where it must be:", *missing])


def test_every_metered_route_is_gated_or_listed_as_deliberately_ungated(
    app_routes: Any, settings: Any
) -> None:
    """Coverage by default: a new route that spends money is gated, or named here.

    The metered handlers are read out of the routers' source (every
    ``CreditEventRepository(...).record(kind=...)`` call), so this cannot be satisfied by
    remembering to update a list — only by mounting the gate or writing down why not.
    """
    gated: dict[str, set[str]] = {}
    for route in walk_routes(app_routes):
        endpoint = getattr(route, "endpoint", None)
        name = getattr(endpoint, "__name__", None)
        if name:
            gated.setdefault(name, set()).update(_mounted_quota_kinds(route))

    metered = _metered_handlers()
    assert len(metered) >= 5, metered

    ungated = sorted(
        (handler, kind)
        for handler, kinds in metered.items()
        for kind in kinds
        if kind not in gated.get(handler, set())
    )
    unexplained = [pair for pair in ungated if pair not in UNGATED_ON_PURPOSE]
    assert not unexplained, (
        "%d route(s) spend a metered kind with no quota gate and no stated reason:\n  %s\n"
        "Mount require_quota(<kind>) on the route, or add it to UNGATED_ON_PURPOSE in "
        "this file with the reason it cannot be a one-line mount."
        % (len(unexplained), "\n  ".join("%s -> %s" % pair for pair in unexplained))
    )
    # ...and the exemption list may not rot into a list of things that ARE gated: an
    # entry that no longer describes an ungated route is a stale excuse.
    stale = sorted(set(UNGATED_ON_PURPOSE) - set(ungated))
    assert not stale, "UNGATED_ON_PURPOSE names %d route(s) that are gated (or gone):\n  %s" % (
        len(stale),
        "\n  ".join("%s -> %s" % pair for pair in stale),
    )


def test_the_billing_router_is_mounted_on_the_real_app(app_routes: Any, settings: Any) -> None:
    """Bug class 4: a module that believes it is registered.

    Every other test in this file drives ``billing_app``, which is the real app plus one
    quota probe. If ``garh_api.routers.api_router`` stopped including the billing router
    they would ALL still pass while ``/api/v1/billing/**`` served 404 in production —
    which is exactly the state this package shipped in. So this reads the plain ``app``
    fixture (no billing-specific wiring at all) and requires every route the router
    declares to be there, under the real prefix, with the real methods.
    """
    live = {
        (method, route.path)
        for route in walk_routes(app_routes)
        for method in (getattr(route, "methods", None) or ())
        if method not in ("HEAD", "OPTIONS")
    }
    declared = {
        (method, "%s%s" % (settings.api_prefix, route.path))
        for route in billing_router.router.routes
        for method in (getattr(route, "methods", None) or ())
        if method not in ("HEAD", "OPTIONS")
    }
    assert len(declared) >= 15, declared
    missing = sorted(declared - live)
    assert not missing, (
        "%d billing route(s) are declared but not reachable on the app:\n  %s\n"
        "Mount the router in garh_api/routers/__init__.py::api_router."
        % (len(missing), "\n  ".join("%s %s" % pair for pair in missing))
    )


async def test_every_billing_route_requires_a_tenant(billing_app: Any, settings: Any) -> None:
    """No route on this router is anonymous, and none is reachable by a share viewer.

    Read off the live route table rather than from a list somebody maintains, so a route
    added without a tenant dependency fails here.
    """
    from garh_api.deps import require_admin, require_share_viewer, require_tenant

    def dependency_calls(route: Any) -> set[Any]:
        """Every dependency callable on the route, one level of nesting included."""
        calls: set[Any] = set()
        stack = list(route.dependant.dependencies)
        while stack:
            dependency = stack.pop()
            calls.add(dependency.call)
            stack.extend(dependency.dependencies)
        return calls

    billing_routes = [
        route
        for route in walk_routes(billing_app)
        if getattr(route, "path", "").startswith("%s/billing" % settings.api_prefix)
    ]
    assert len(billing_routes) >= 15, len(billing_routes)
    for route in billing_routes:
        calls = dependency_calls(route)
        assert require_tenant in calls or require_admin in calls, route.path
        assert require_share_viewer not in calls, route.path


def test_the_billing_repositories_all_require_a_tenant_context() -> None:
    """Every repository in this package is ``(session, ctx)`` — no ctx-less variant.

    The same structural check ``test_no_unscoped_queries`` applies to
    ``garh_api.repositories``, applied to this package's own repositories, because that
    test only walks ``garh_api.repositories.__all__``.
    """
    import inspect

    from garh_api.billing import repositories as billing_repositories
    from garh_api.tenancy import Repository

    checked = 0
    for name in billing_repositories.__all__:
        candidate = getattr(billing_repositories, name)
        if not inspect.isclass(candidate) or not issubclass(candidate, Repository):
            continue
        parameters = list(inspect.signature(candidate.__init__).parameters)
        assert parameters[:3] == ["self", "session", "ctx"], (name, parameters)
        assert candidate.row_type.__tenant_owned__ is True, name
        checked += 1
    assert checked == 5, checked


def test_no_billing_module_builds_its_own_sql() -> None:
    """The package composes the tenancy layer's helpers; it never opens a query.

    ``tests/test_no_unscoped_queries.py`` enforces this for ``garh_api/`` as a whole and
    would already fail on a ``session.execute`` here — this asserts the same rule from
    inside the package's own suite, so the reason is written down where the code is.
    """
    import ast
    import os
    from pathlib import Path

    package = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "garh_api", "billing"
    )
    receivers = {"session", "db", "_session", "conn", "connection"}
    methods = {"query", "execute", "scalars", "scalar", "add", "add_all", "delete", "merge", "get"}
    offenders: list[str] = []
    for path in sorted(Path(package).glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in methods:
                continue
            receiver = node.func.value
            name = (
                receiver.id
                if isinstance(receiver, ast.Name)
                else receiver.attr
                if isinstance(receiver, ast.Attribute)
                else None
            )
            if name in receivers:
                offenders.append(
                    "%s:%d %s.%s(...)" % (path.name, node.lineno, name, node.func.attr)
                )
    assert not offenders, offenders
