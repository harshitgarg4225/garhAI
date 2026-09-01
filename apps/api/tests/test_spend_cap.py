"""The $5-per-architect generation budget: pricing, the ledger, and the gate.

Three separate things get tested, because three separate things can silently fail:

1. **The arithmetic.** A published rate typed in wrong is a number nobody can see is
   wrong. The cases below assert against the rates as PUBLISHED (dollars per million
   tokens), so a typo in `LLM_PRICES` shows up as an off-by-5x answer rather than as a
   plausible-looking total.
2. **The ledger.** A cost that is computed and not stored is CLAUDE.md's recurring
   failure — the render credit and the solver banner both shipped that way this week.
3. **The gate.** A cap that never refuses is worse than no cap, because it reads as
   protection. Every "refuses" case here has a matching "still allows" case, so a
   guard hard-wired to `raise` cannot pass either.
"""

from __future__ import annotations

import pytest
from garh_api.billing.spend import (
    FREE_PROVIDERS,
    LLM_PRICES,
    MICROS_PER_USD,
    assert_prices_cover_configured_model,
    cost_micros_for,
    format_usd,
    llm_cost_micros,
)

# ---------------------------------------------------------------------------
# 1. The arithmetic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "input_usd_per_mtok", "output_usd_per_mtok"),
    [
        ("claude-opus-5", 5, 25),
        ("claude-sonnet-5", 2, 10),
        ("claude-haiku-4-5", 1, 5),
        ("claude-fable-5", 10, 50),
    ],
)
def test_a_million_tokens_costs_the_published_rate(
    model: str, input_usd_per_mtok: int, output_usd_per_mtok: int
) -> None:
    """The whole point of micro-dollars: exactly the published number, no rounding."""
    assert llm_cost_micros(model=model, input_tokens=1_000_000) == (
        input_usd_per_mtok * MICROS_PER_USD
    )
    assert llm_cost_micros(model=model, output_tokens=1_000_000) == (
        output_usd_per_mtok * MICROS_PER_USD
    )


def test_a_small_call_does_not_round_away_to_nothing() -> None:
    """In cents this would be 0, and a cap counting zeroes never trips.

    1k in + 1k out on Haiku is $0.001 + $0.005 = $0.006 — six thousand micro-dollars,
    and under one cent.
    """
    cost = llm_cost_micros(model="claude-haiku-4-5", input_tokens=1_000, output_tokens=1_000)
    assert cost == 6_000
    assert cost * 100 // MICROS_PER_USD == 0, "under a cent — which is the danger"


def test_cache_tokens_bill_at_their_multipliers() -> None:
    # Opus 5 input is $5/MTok: reads at 0.1x = $0.50/MTok, writes at 1.25x = $6.25/MTok.
    assert llm_cost_micros(model="claude-opus-5", cache_read_tokens=1_000_000) == 500_000
    assert llm_cost_micros(model="claude-opus-5", cache_write_tokens=1_000_000) == 6_250_000


def test_an_unpriced_model_is_charged_the_most_expensive_rate() -> None:
    """An unlisted model must never be the CHEAPEST way to spend.

    Otherwise the cap is something a config change walks around.
    """
    unknown = llm_cost_micros(model="some-model-we-never-heard-of", input_tokens=1_000_000)
    assert unknown >= max(
        llm_cost_micros(model=name, input_tokens=1_000_000) for name in LLM_PRICES
    )


def test_the_configured_model_must_have_a_published_price() -> None:
    from garh_api.config import get_settings

    assert_prices_cover_configured_model(get_settings().anthropic_model)
    with pytest.raises(ValueError, match="No price row"):
        assert_prices_cover_configured_model("claude-not-a-real-model")


@pytest.mark.parametrize("provider", sorted(FREE_PROVIDERS - {""}))
def test_a_mock_provider_costs_nothing(provider: str) -> None:
    """A stack running on fixtures must not burn a real budget.

    Every test in this repository, the whole dev stack and most of a trial run under
    `PROVIDER_LLM=mock` — charging for those would empty the $5 without a rupee
    leaving anyone's account.
    """
    assert (
        cost_micros_for(
            "llm",
            meta={"provider": provider, "model": "claude-opus-5", "inputTokens": 5_000_000},
        )
        == 0
    )


def test_a_real_provider_with_the_same_tokens_does_cost() -> None:
    """NEGATIVE CONTROL for the mock case: identical input, real provider, real cost.

    Without it, `cost_micros_for` returning 0 unconditionally would pass every
    free-provider assertion above.
    """
    assert (
        cost_micros_for(
            "llm",
            meta={"provider": "anthropic", "model": "claude-opus-5", "inputTokens": 1_000_000},
        )
        == 5 * MICROS_PER_USD
    )
    assert cost_micros_for("render", qty=2, meta={"provider": "stability"}) > 0


def test_format_is_the_money_an_architect_reads() -> None:
    assert format_usd(5 * MICROS_PER_USD) == "$5.00"
    assert format_usd(4_250_000) == "$4.25"
    assert format_usd(6_000) == "$0.00", "sub-cent rounds for DISPLAY only, never in the ledger"
    assert format_usd(-1) == "$0.00"


# ---------------------------------------------------------------------------
# 2. The ledger — a cost that is computed and not stored is not a cost
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_recording_prices_the_event_from_the_meta_already_written(session, firm_a) -> None:
    """The routes already record provider/model/tokens. Pricing reads those.

    So metering a new call site is a keyword argument, not a second bookkeeping path
    that can drift from the first.
    """
    from garh_api.repositories import CreditEventRepository

    repo = CreditEventRepository(session, firm_a.ctx())
    event = await repo.record(
        kind="llm",
        meta={
            "provider": "anthropic",
            "model": "claude-opus-5",
            "inputTokens": 200_000,
            "outputTokens": 20_000,
        },
    )
    # 200k in at $5/MTok = $1.00; 20k out at $25/MTok = $0.50.
    assert event.cost_micros == 1_500_000
    assert event.user_id == firm_a.user_id, "the spend must name the architect who spent it"


@pytest.mark.integration
async def test_spent_micros_is_the_architects_lifetime_total(session, firm_a) -> None:
    from garh_api.repositories import CreditEventRepository

    repo = CreditEventRepository(session, firm_a.ctx())
    assert await repo.spent_micros() == 0
    await repo.record(kind="render", meta={"provider": "stability"})
    await repo.record(kind="render", meta={"provider": "stability"})
    await repo.record(kind="llm", meta={"provider": "mock", "inputTokens": 10_000_000})
    total = await repo.spent_micros()
    assert total == 2 * cost_micros_for("render", meta={"provider": "stability"})
    assert total > 0, "the mock row contributed nothing, the two real ones did"


@pytest.mark.integration
async def test_one_architects_spend_does_not_close_anothers_door(session, firm_a, firm_b) -> None:
    """Keyed on the USER, not the firm.

    NEGATIVE CONTROL for the aggregate: a firm-wide SUM would make this fail, and it
    is the difference between "your budget" and "somebody else's".
    """
    from garh_api.repositories import CreditEventRepository

    await CreditEventRepository(session, firm_a.ctx()).record(
        kind="render", qty=50, meta={"provider": "stability"}
    )
    assert await CreditEventRepository(session, firm_a.ctx()).spent_micros() > 0
    assert await CreditEventRepository(session, firm_b.ctx()).spent_micros() == 0


# ---------------------------------------------------------------------------
# 3. The gate
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_the_budget_refuses_once_it_is_spent(session, firm_a, monkeypatch) -> None:
    from garh_api.billing.errors import SpendCapExceededError
    from garh_api.billing.quotas import check_spend_budget
    from garh_api.config import get_settings
    from garh_api.repositories import CreditEventRepository

    settings = get_settings()
    monkeypatch.setattr(settings, "spend_cap_usd", 5, raising=False)

    ctx = firm_a.ctx()
    # Under budget: allowed.
    await CreditEventRepository(session, ctx).record(
        kind="llm",
        meta={"provider": "anthropic", "model": "claude-opus-5", "inputTokens": 100_000},
    )
    await check_spend_budget(session, ctx, "llm")  # $0.50 of $5 — fine

    # Now spend the rest: 1M output tokens on Opus is $25, well past $5.
    await CreditEventRepository(session, ctx).record(
        kind="llm",
        meta={"provider": "anthropic", "model": "claude-opus-5", "outputTokens": 1_000_000},
    )
    with pytest.raises(SpendCapExceededError) as caught:
        await check_spend_budget(session, ctx, "llm")
    # The message has to be one an architect can act on, with real numbers in it.
    assert "$5.00" in str(caught.value)


@pytest.mark.integration
async def test_a_zero_cap_disables_the_budget_entirely(session, firm_a, monkeypatch) -> None:
    """NEGATIVE CONTROL for the case above.

    An unmetered deployment, and every test that is not about spending, must be able
    to turn this off — and a guard hard-wired to raise would fail here.
    """
    from garh_api.billing.quotas import check_spend_budget
    from garh_api.config import get_settings
    from garh_api.repositories import CreditEventRepository

    monkeypatch.setattr(get_settings(), "spend_cap_usd", 0, raising=False)
    ctx = firm_a.ctx()
    await CreditEventRepository(session, ctx).record(
        kind="llm",
        meta={"provider": "anthropic", "model": "claude-opus-5", "outputTokens": 10_000_000},
    )
    await check_spend_budget(session, ctx, "llm")  # $250 spent, no cap, no refusal


@pytest.mark.integration
async def test_generate_is_refused_over_HTTP_once_the_budget_is_gone(
    client, api, session, firm_a, project_a, clean_redis, monkeypatch
) -> None:
    """End to end: the architect gets a 402 with the numbers, not a 500 or a blank."""
    from garh_api.config import get_settings
    from garh_api.repositories import CreditEventRepository

    monkeypatch.setattr(get_settings(), "spend_cap_usd", 5, raising=False)
    await CreditEventRepository(session, firm_a.ctx()).record(
        kind="llm",
        meta={"provider": "anthropic", "model": "claude-opus-5", "outputTokens": 1_000_000},
    )
    await session.commit()

    response = await client.post(
        "%s/projects/%s/solve" % (api, project_a.id),
        json={"optionCount": 3},
        headers=firm_a.headers,
    )
    assert response.status_code == 402, response.text
    body = response.json()
    assert body["code"] == "spend_cap_exceeded"
    assert body["capUsd"] == "$5.00"
    assert body["action"], "a refusal an architect cannot act on is a dead end"


# ---------------------------------------------------------------------------
# 4. The coverage gate — the reason this cannot rot
# ---------------------------------------------------------------------------


def _api_routes(app):
    """Every APIRoute, flattened.

    This FastAPI version mounts included routers lazily (`_IncludedRouter`), so
    `app.routes` is a tree rather than a flat list — walking only the top level finds
    almost nothing and a gate built on it would pass by finding no routes at all.
    """
    from fastapi.routing import APIRoute

    found, seen = [], set()

    def walk(routes):
        for route in routes:
            if id(route) in seen:
                continue
            seen.add(id(route))
            if isinstance(route, APIRoute):
                found.append(route)
            inner = getattr(route, "original_router", None)
            if inner is not None:
                walk(inner.routes)
            walk(getattr(route, "routes", []) or [])

    walk(app.routes)
    return found


def _guards_the_budget(route) -> bool:
    """Is `require_spend_budget`'s dependency anywhere in this route's tree?

    Checked against the real dependency graph, not against the source text or the
    OpenAPI document: a dependency raises at request time and generates no schema, so
    documentation is not evidence that the gate is mounted.
    """

    def walk(dependant) -> bool:
        call = getattr(dependant, "call", None)
        if getattr(call, "__qualname__", "").startswith("require_spend_budget"):
            return True
        return any(walk(child) for child in getattr(dependant, "dependencies", []))

    return walk(route.dependant)


def test_every_route_that_meters_also_guards_the_budget() -> None:
    """A metered route without a budget guard is an uncapped way to spend.

    Coverage that depends on somebody remembering is not coverage — the same argument
    `test_cross_tenant.py` makes for tenancy. This walks the live route table, so a new
    metered endpoint merged without a guard turns this red instead of quietly becoming
    the cheapest hole in the budget.
    """
    from garh_api.main import create_app

    routes = _api_routes(create_app())
    assert len(routes) > 50, "the walker found almost no routes, so it proves nothing"

    # Paths WITHOUT the /api/v1 prefix: an included router's own routes carry the
    # path as declared, and the prefix is applied when the app mounts it.
    must_guard = {
        "/projects/{project_id}/solve",
        "/projects/{project_id}/renders",
        "/projects/{project_id}/renders/client-pack",
        "/projects/{project_id}/render-packs/{pack_id}/archive",
        "/projects/{project_id}/export",
        "/projects/{project_id}/copilot",
    }
    by_path = {r.path: r for r in routes if "POST" in (r.methods or set())}

    stale = sorted(must_guard - set(by_path))
    assert not stale, "these metered routes are gone or renamed, so this list is stale: %s" % stale

    unguarded = sorted(path for path in must_guard if not _guards_the_budget(by_path[path]))
    assert not unguarded, "these routes can spend money with no budget guard: %s" % unguarded


def test_the_guard_walker_can_tell_a_guarded_route_from_an_unguarded_one() -> None:
    """NEGATIVE CONTROL for the gate above.

    If `_guards_the_budget` returned True for everything — the easy way for that test
    to look green forever — this fails. `GET /projects` spends nothing and must not be
    reported as guarded.
    """
    from garh_api.main import create_app

    routes = _api_routes(create_app())
    unmetered = next(r for r in routes if r.path == "/projects" and "GET" in (r.methods or set()))
    assert not _guards_the_budget(unmetered)


@pytest.mark.integration
async def test_the_budget_shown_is_the_budget_enforced(
    client, api, session, firm_a, monkeypatch
) -> None:
    """One source. A meter that disagrees with the gate is worse than no meter.

    An architect who reads "$4.50 left" and is then refused has been lied to, and the
    only way that cannot happen is for both numbers to come from the same query.
    """
    from garh_api.billing.errors import SpendCapExceededError
    from garh_api.billing.quotas import check_spend_budget
    from garh_api.config import get_settings
    from garh_api.repositories import CreditEventRepository

    monkeypatch.setattr(get_settings(), "spend_cap_usd", 5, raising=False)

    # $1.00 of $5 spent: the page says so, and the gate lets the next call through.
    await CreditEventRepository(session, firm_a.ctx()).record(
        kind="llm",
        meta={"provider": "anthropic", "model": "claude-opus-5", "inputTokens": 200_000},
    )
    await session.commit()

    shown = (await client.get("%s/billing/usage" % api, headers=firm_a.headers)).json()["spend"]
    assert shown["capUsd"] == "$5.00"
    assert shown["spentUsd"] == "$1.00"
    assert shown["remainingUsd"] == "$4.00"
    assert shown["enforced"] is True
    await check_spend_budget(session, firm_a.ctx(), "llm")  # agrees: still allowed

    # Now overspend, and both must flip together.
    await CreditEventRepository(session, firm_a.ctx()).record(
        kind="llm",
        meta={"provider": "anthropic", "model": "claude-opus-5", "outputTokens": 1_000_000},
    )
    await session.commit()

    shown = (await client.get("%s/billing/usage" % api, headers=firm_a.headers)).json()["spend"]
    assert shown["remainingUsd"] == "$0.00"
    with pytest.raises(SpendCapExceededError):
        await check_spend_budget(session, firm_a.ctx(), "llm")
