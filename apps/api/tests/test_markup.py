"""The platform fee: a percentage the owner sets, applied to every charge from then on.

Two promises, each with a negative control:

* changing the percentage changes the NEXT charge — and never an old row;
* the provider-cost ledger (``cost_micros``) stays exactly what the work cost, so
  reconciliation against Anthropic's or Stability's bill still adds up.
"""

from __future__ import annotations

import pytest
from garh_api.billing.markup import (
    MarkupValueError,
    bps_to_percent,
    charged_micros,
    markup_micros,
    percent_to_bps,
)

# ---------------------------------------------------------------------------
# 1. Arithmetic — integers in, integers out, one rounding at the end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("percent", "bps"),
    [(5, 500), ("5", 500), (7.5, 750), ("7.25", 725), (0, 0), (100, 10_000), ("0.01", 1)],
)
def test_percent_to_bps_is_exact(percent: object, bps: int) -> None:
    assert percent_to_bps(percent) == bps
    assert percent_to_bps(bps_to_percent(bps)) == bps, "round-trips through the display string"


@pytest.mark.parametrize("bad", [-1, 101, "5.005", "abc", True, float("nan"), float("inf")])
def test_percent_to_bps_refuses_what_is_not_a_fee(bad: object) -> None:
    with pytest.raises(MarkupValueError):
        percent_to_bps(bad)


def test_five_percent_of_a_dollar_is_five_cents() -> None:
    assert markup_micros(1_000_000, 500) == 50_000
    assert charged_micros(1_000_000, 500) == 1_050_000


def test_the_fee_rounds_half_up_once_and_never_to_less_than_set() -> None:
    # 5 % of 10 µUSD is 0.5 µUSD → 1, not 0: a fee that rounds away on every small
    # call would quietly be a smaller fee than the owner set.
    assert markup_micros(10, 500) == 1
    assert markup_micros(9, 500) == 0  # 0.45 → 0: half-up, not ceil
    assert markup_micros(3, 1) == 0
    assert charged_micros(0, 500) == 0, "nothing cost nothing, fee or not"
    assert charged_micros(1_000_000, 0) == 1_000_000, "a 0 % fee charges cost"


def test_display_strings_have_no_float_noise() -> None:
    assert bps_to_percent(500) == "5"
    assert bps_to_percent(725) == "7.25"
    assert bps_to_percent(0) == "0"
    assert bps_to_percent(10_000) == "100"


# ---------------------------------------------------------------------------
# 2. The ledger — recorded on the row, read at charge time, never rewritten
# ---------------------------------------------------------------------------

LLM_META = {"provider": "anthropic", "model": "claude-opus-5", "inputTokens": 200_000}  # $1.00


@pytest.mark.integration
async def test_a_charge_carries_the_default_fee_and_the_honest_cost(session, firm_a) -> None:
    from garh_api.repositories import CreditEventRepository

    event = await CreditEventRepository(session, firm_a.ctx()).record(kind="llm", meta=LLM_META)
    assert event.cost_micros == 1_000_000, "the provider ledger is untouched by the fee"
    assert event.markup_bps == 500, "the boot default is 5 %"
    assert event.charged_micros == 1_050_000


@pytest.mark.integration
async def test_changing_the_fee_changes_the_next_charge_and_not_the_last(session, firm_a) -> None:
    from garh_api.billing.markup import current_markup_bps, set_markup_bps
    from garh_api.repositories import CreditEventRepository

    repo = CreditEventRepository(session, firm_a.ctx())
    before = await repo.record(kind="llm", meta=LLM_META)
    assert before.charged_micros == 1_050_000

    await set_markup_bps(session, 750, updated_by=firm_a.user_id)  # the owner: 7.5 % from now
    assert await current_markup_bps(session) == 750

    after = await repo.record(kind="llm", meta=LLM_META)
    assert after.markup_bps == 750 and after.charged_micros == 1_075_000

    # NEGATIVE CONTROL: the earlier row keeps the fee it was charged at.
    rows = await repo.list_recent(limit=10) if hasattr(repo, "list_recent") else None
    if rows is None:
        from garh_api import models
        from sqlalchemy import select

        result = await session.execute(
            select(models.CreditEvent.markup_bps, models.CreditEvent.charged_micros).where(
                models.CreditEvent.id == before.id
            )
        )
        rows = [result.one()]
        assert tuple(rows[0]) == (500, 1_050_000)
    assert await repo.spent_micros() == 1_050_000 + 1_075_000, "a budget is spent in charges"
    assert await repo.cost_micros_total() == 2_000_000, "and the cost ledger stays cost"


@pytest.mark.integration
async def test_a_mock_provider_is_free_fee_included(session, firm_a) -> None:
    from garh_api.repositories import CreditEventRepository

    event = await CreditEventRepository(session, firm_a.ctx()).record(
        kind="render", meta={"provider": "mock"}
    )
    assert (event.cost_micros, event.charged_micros) == (0, 0)


@pytest.mark.integration
async def test_a_corrupt_setting_row_falls_back_to_the_configured_default(session, firm_a) -> None:
    from garh_api.billing.markup import MARKUP_KEY, current_markup_bps
    from garh_api.repositories import PlatformSettingRepository

    await PlatformSettingRepository(session).set(MARKUP_KEY, "not-a-number", updated_by=None)
    assert await current_markup_bps(session) == 500


# ---------------------------------------------------------------------------
# 3. Over HTTP — anyone may read the fee, only a platform owner may set it
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_anyone_signed_in_can_read_the_fee(client, api, firm_a) -> None:
    response = await client.get("%s/admin/billing/markup" % api, headers=firm_a.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["percent"] == "5" and body["bps"] == 500 and body["source"] == "default"


@pytest.mark.integration
async def test_a_firm_admin_who_is_not_the_owner_cannot_set_it(
    client, api, firm_a, monkeypatch
) -> None:
    from garh_api.config import get_settings

    monkeypatch.setattr(
        get_settings(), "platform_owner_emails", "owner@garh.example", raising=False
    )
    response = await client.put(
        "%s/admin/billing/markup" % api, json={"percent": 9}, headers=firm_a.headers
    )
    assert response.status_code == 403, response.text


@pytest.mark.integration
async def test_an_empty_owner_list_refuses_everyone(client, api, firm_a, monkeypatch) -> None:
    from garh_api.config import get_settings

    monkeypatch.setattr(get_settings(), "platform_owner_emails", "", raising=False)
    response = await client.put(
        "%s/admin/billing/markup" % api, json={"percent": 9}, headers=firm_a.headers
    )
    assert response.status_code == 403, response.text


@pytest.mark.integration
async def test_the_owner_sets_the_fee_and_the_usage_card_shows_it(
    client, api, session, firm_a, monkeypatch
) -> None:
    from garh_api import models
    from garh_api.config import get_settings
    from sqlalchemy import select

    result = await session.execute(
        select(models.User.email).where(models.User.id == firm_a.user_id)
    )
    owner_email = result.scalar_one()
    monkeypatch.setattr(
        get_settings(), "platform_owner_emails", " %s " % owner_email.upper(), raising=False
    )

    response = await client.put(
        "%s/admin/billing/markup" % api, json={"percent": "7.25"}, headers=firm_a.headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["percent"] == "7.25" and body["bps"] == 725 and body["source"] == "setting"
    assert body["updatedBy"] == str(firm_a.user_id)

    usage = await client.get("%s/billing/usage" % api, headers=firm_a.headers)
    assert usage.status_code == 200, usage.text
    assert usage.json()["spend"]["markupPercent"] == "7.25"

    for bad in (-1, 101, "5.005", "abc"):
        refused = await client.put(
            "%s/admin/billing/markup" % api, json={"percent": bad}, headers=firm_a.headers
        )
        assert refused.status_code in (400, 422), (bad, refused.text)
