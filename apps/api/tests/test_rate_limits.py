"""Rate limits (playbook §11 numbers, §13 "per firm + per IP on auth").

Three layers, tested at each:

* the **sliding window** itself, against real Redis — because a limiter that admits ``2 x
  limit`` across a window boundary is worse than none (it looks enforced in a unit test and
  is not enforced in production);
* the **auth limits**, which fail *closed*: an unmetered OTP endpoint is a brute-force and
  mail-bombing target, so when Redis is unreachable the request is refused;
* the **product limits**, which fail *open*: a limiter outage must not stop people editing.

Every 429 is also checked for the things a client needs to behave: ``Retry-After``, a
``rule`` name, and problem+json with an ``action`` a human can follow.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from garh_api import ratelimit
from garh_api.config import Settings
from garh_api.ratelimit import (
    OTP_PER_EMAIL_PER_HOUR,
    OTP_RESEND_COOLDOWN_SECONDS,
    RULE_FACTORIES,
    VERIFY_PER_IP_PER_HOUR,
    RateLimitRule,
    check_rate_limit,
    peek_rate_limit,
    reset_rate_limit,
)

from tests.helpers import problem

pytestmark = pytest.mark.integration


def _identity() -> str:
    """A bucket nothing else in the suite can collide with."""
    return "firm:%s" % uuid.uuid4()


# ---------------------------------------------------------------------------
# The sliding window
# ---------------------------------------------------------------------------


async def test_window_admits_exactly_the_limit(clean_redis: Any) -> None:
    rule = RateLimitRule(name="test.exact", limit=3, window_seconds=60, scope="firm")
    identity = _identity()

    for expected_used in (1, 2, 3):
        decision = await check_rate_limit(rule, identity)
        assert decision.allowed is True
        assert decision.used == expected_used
        assert decision.remaining == rule.limit - expected_used
        assert decision.retry_after_seconds == 0

    denied = await check_rate_limit(rule, identity)
    assert denied.allowed is False
    assert denied.remaining == 0
    assert denied.retry_after_seconds >= 1, "a 429 with no Retry-After is unactionable"
    assert denied.headers()["Retry-After"] == str(denied.retry_after_seconds)


async def test_denials_do_not_consume_the_window(clean_redis: Any) -> None:
    """A client hammering a spent bucket must not push its own reset time out forever."""
    rule = RateLimitRule(name="test.no-consume", limit=1, window_seconds=60, scope="firm")
    identity = _identity()

    assert (await check_rate_limit(rule, identity)).allowed is True
    first_denial = await check_rate_limit(rule, identity)
    assert first_denial.allowed is False

    for _ in range(5):
        again = await check_rate_limit(rule, identity)
        assert again.allowed is False
        assert again.used == 1, "a denied request was counted, extending the lockout"
    assert again.retry_after_seconds <= first_denial.retry_after_seconds


async def test_window_expires(clean_redis: Any) -> None:
    """A one-second window really does reopen — the window is a window, not a counter."""
    rule = RateLimitRule(name="test.expiry", limit=1, window_seconds=1, scope="firm")
    identity = _identity()

    assert (await check_rate_limit(rule, identity)).allowed is True
    assert (await check_rate_limit(rule, identity)).allowed is False
    await asyncio.sleep(1.1)
    assert (await check_rate_limit(rule, identity)).allowed is True


async def test_no_double_burst_across_a_boundary(clean_redis: Any) -> None:
    """The classic fixed-window bug: ``limit`` at the end, ``limit`` again at the start.

    Sliding windows must never admit more than ``limit`` in any single window's worth of
    time, wherever that window starts.
    """
    rule = RateLimitRule(name="test.boundary", limit=2, window_seconds=2, scope="firm")
    identity = _identity()

    assert (await check_rate_limit(rule, identity)).allowed is True
    assert (await check_rate_limit(rule, identity)).allowed is True
    await asyncio.sleep(1.0)  # half a window later
    assert (
        await check_rate_limit(rule, identity)
    ).allowed is False, "admitted a third request inside one window"


async def test_buckets_are_isolated_per_identity(clean_redis: Any) -> None:
    rule = RateLimitRule(name="test.isolation", limit=1, window_seconds=60, scope="firm")
    first, second = _identity(), _identity()
    assert (await check_rate_limit(rule, first)).allowed is True
    assert (await check_rate_limit(rule, first)).allowed is False
    assert (await check_rate_limit(rule, second)).allowed is True


async def test_peek_and_reset(clean_redis: Any) -> None:
    """``peek`` renders a countdown without burning a slot; ``reset`` un-sticks support."""
    rule = RateLimitRule(name="test.peek", limit=2, window_seconds=60, scope="firm")
    identity = _identity()

    assert await peek_rate_limit(rule, identity) == 0
    await check_rate_limit(rule, identity)
    assert await peek_rate_limit(rule, identity) == 1
    assert await peek_rate_limit(rule, identity) == 1, "peek consumed a slot"

    await reset_rate_limit(rule, identity)
    assert await peek_rate_limit(rule, identity) == 0


def test_shipped_rules_carry_the_playbook_numbers(settings: Settings) -> None:
    """§11/§13's numbers, and the fail-open/fail-closed policy per rule."""
    rules = {factory.__name__: factory(settings) for factory in RULE_FACTORIES}

    ops = rules["ops_per_firm_rule"]
    assert (ops.limit, ops.window_seconds, ops.scope) == (60, 1, "firm")
    assert ops.fail_closed is False, "a limiter outage must not stop people editing"

    solver = rules["solver_jobs_per_firm_rule"]
    assert (solver.limit, solver.window_seconds, solver.scope) == (10, 3600, "firm")

    # The LLM routes are the only ones that spend money at a third party per request,
    # so this is the one PRODUCT limit that fails closed: an uncounted call to a metered
    # API is worse than a brief parse the architect retries. Opposite policy to
    # ops_per_firm_rule above, and the contrast is the point.
    llm = rules["llm_per_firm_rule"]
    assert (llm.window_seconds, llm.scope) == (3600, "firm")
    assert llm.limit == settings.rate_limit_llm_per_hour
    assert llm.fail_closed is True, (
        "llm_per_firm_rule must fail closed — if Redis cannot count the call, the call "
        "must not be made. Failing open here bills the company for a limiter outage."
    )

    # Two routes, two sentences, ONE counter. The copy has to name what the architect
    # was doing (a copilot user told they are out of "brief parses" reads it as a bug);
    # the bucket must NOT split, or a firm doubles its provider budget by alternating.
    brief_rule = ratelimit.llm_per_firm_rule(settings, feature="brief-parse")
    copilot_rule = ratelimit.llm_per_firm_rule(settings, feature="copilot")
    assert brief_rule.name == copilot_rule.name == "llm.per_firm"
    assert brief_rule.message != copilot_rule.message
    assert "brief" in brief_rule.message.lower()
    assert "editing" in copilot_rule.message.lower()
    assert copilot_rule.fail_closed is True
    # An unknown feature falls back to real copy rather than a KeyError mid-429.
    assert ratelimit.llm_per_firm_rule(settings, feature="nope").message

    for name in (
        "auth_ip_rule",
        "otp_per_email_rule",
        "otp_resend_rule",
        "verify_ip_rule",
        "llm_per_firm_rule",
    ):
        assert rules[name].fail_closed is True, "%s must fail closed (§13)" % name

    assert rules["otp_per_email_rule"].limit == OTP_PER_EMAIL_PER_HOUR == 5
    assert rules["otp_resend_rule"].window_seconds == OTP_RESEND_COOLDOWN_SECONDS == 60
    assert rules["verify_ip_rule"].limit == VERIFY_PER_IP_PER_HOUR == 30
    assert rules["auth_ip_rule"].limit == settings.rate_limit_auth_per_hour

    # Every rule must be able to explain itself in the 429 body (golden rule 9).
    for name, rule in rules.items():
        assert rule.message, name
        assert rule.describe()


# ---------------------------------------------------------------------------
# Auth limits, through HTTP
# ---------------------------------------------------------------------------


async def test_otp_resend_cooldown_is_enforced(client: Any, api: str, firm_a: Any) -> None:
    """§13's 60-second resend cooldown — the cheapest mail-bomb defence there is."""
    first = await client.post("%s/auth/otp" % api, json={"email": firm_a.email})
    assert first.status_code == 202, first.text

    second = await client.post("%s/auth/otp" % api, json={"email": firm_a.email})
    assert second.status_code == 429, second.text
    body = problem(second)
    assert body["code"] == "otp_rate_limited", body
    assert body["action"], body
    assert int(second.headers["retry-after"]) >= 1
    assert body["retryAfterSeconds"] >= 1
    assert body["rule"] == "auth.otp_resend", body
    # A 429 must not leak whether the address exists.
    assert firm_a.email not in second.text


async def test_otp_per_ip_limit_is_enforced(client: Any, api: str, settings: Settings) -> None:
    """Per-IP hourly cap on the OTP-issuing routes, using distinct unknown addresses.

    Distinct addresses on purpose: the per-address cooldown would otherwise fire first and
    the per-IP bucket would never be exercised. Unknown addresses still burn the budget —
    if they did not, the difference would itself be an enumeration oracle.
    """
    limit = settings.rate_limit_auth_per_hour
    statuses = []
    for index in range(limit + 1):
        response = await client.post(
            "%s/auth/otp" % api, json={"email": "ip-limit-%d@studio.test" % index}
        )
        statuses.append(response.status_code)

    assert statuses[:limit] == [202] * limit, statuses
    assert statuses[-1] == 429, statuses


async def test_per_ip_limits_are_per_ip(client: Any, api: str, settings: Settings) -> None:
    """A second caller behind a different address is unaffected (TRUSTED_PROXY_HOPS=1)."""
    limit = settings.rate_limit_auth_per_hour
    for index in range(limit + 1):
        response = await client.post(
            "%s/auth/otp" % api,
            json={"email": "noisy-%d@studio.test" % index},
            headers={"X-Forwarded-For": "203.0.113.7"},
        )
    assert response.status_code == 429, response.text

    other = await client.post(
        "%s/auth/otp" % api,
        json={"email": "quiet@studio.test"},
        headers={"X-Forwarded-For": "203.0.113.8"},
    )
    assert other.status_code == 202, other.text


# ---------------------------------------------------------------------------
# Product limits, through HTTP
# ---------------------------------------------------------------------------


async def test_solver_jobs_per_hour_is_enforced(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any, settings: Settings
) -> None:
    """§11: 10 solver jobs an hour per firm on the free tier.

    Enqueues real jobs — the worker is not running, so they stay ``queued``, which is
    exactly the state the UI shows while it waits. The project needs a plot and a
    brief: without them ``/solve`` refuses with a 409 before anything is enqueued
    (see ``test_solve_enqueue.py``), and a refused request is not a job.
    """
    from tests.factories import seed_plot_and_brief

    await seed_plot_and_brief(session, firm_a, project_a.id)
    limit = settings.rate_limit_solver_jobs_per_hour
    accepted = 0
    last: Any = None
    for _ in range(limit + 1):
        last = await client.post(
            "%s/projects/%s/solve" % (api, project_a.id),
            json={"optionCount": 3},
            headers=firm_a.headers,
        )
        if last.status_code == 202:
            accepted += 1

    assert accepted == limit, "admitted %d solver jobs, limit is %d" % (accepted, limit)
    assert last.status_code == 429, last.text
    body = problem(last)
    assert body["code"] == "rate_limited", body
    assert body["rule"] == "solver_jobs.per_firm", body
    assert body["limit"] == limit, body
    assert int(last.headers["retry-after"]) >= 1


async def test_rate_limit_headers_are_present_on_success(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    """A client cannot back off politely if it cannot see the budget (§13 CORS exposes these)."""
    from tests.factories import seed_plot_and_brief

    await seed_plot_and_brief(session, firm_a, project_a.id)
    response = await client.post(
        "%s/projects/%s/solve" % (api, project_a.id),
        json={"optionCount": 3},
        headers=firm_a.headers,
    )
    assert response.status_code == 202, response.text
    assert response.headers["x-ratelimit-limit"] == "10"
    assert int(response.headers["x-ratelimit-remaining"]) == 9
    assert response.headers["x-ratelimit-window"] == "3600"


async def test_solver_limit_is_per_firm_not_global(
    client: Any,
    api: str,
    session: Any,
    firm_a: Any,
    firm_b: Any,
    project_a: Any,
    settings: Settings,
) -> None:
    """One firm exhausting its budget must not throttle another firm."""
    from tests.factories import create_project, seed_plot_and_brief

    project_b = await create_project(session, firm_b, name="Firm B project")
    await seed_plot_and_brief(session, firm_a, project_a.id)
    await seed_plot_and_brief(session, firm_b, project_b.id)

    for _ in range(settings.rate_limit_solver_jobs_per_hour + 1):
        await client.post(
            "%s/projects/%s/solve" % (api, project_a.id),
            json={"optionCount": 3},
            headers=firm_a.headers,
        )

    response = await client.post(
        "%s/projects/%s/solve" % (api, project_b.id),
        json={"optionCount": 3},
        headers=firm_b.headers,
    )
    assert response.status_code == 202, response.text
