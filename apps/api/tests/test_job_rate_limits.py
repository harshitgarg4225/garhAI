"""F-7 — per-firm ceilings on the three expensive enqueue paths.

Before F-7 the solver was the only job route with an hourly budget. The render route,
the client pack, ``/export`` and ``/sheets/generate`` had none: one authenticated firm
could queue unbounded GPU-equivalent work, unbounded headless-Chromium work and
unbounded writes to our object store, and the only thing standing in the way was the
§9 render *concurrency* cap — which limits how many run at once, not how many a firm may
start in an hour.

What this file is actually guarding against
-------------------------------------------

Two of the four failure classes in ``CLAUDE.md`` are live hazards for a rate limit:

* **A gate that silently never fires.** A limit mounted on the wrong route, keyed on the
  wrong identity, or charged after the work is already queued looks identical in review
  to one that works. So nothing here asserts "the rule object has limit=30". Every wiring
  test drives the real HTTP route and reads the real Redis bucket back through
  ``peek_rate_limit``: if the route is not charging that exact rule for that exact firm,
  the peek is 0 and the test fails.
* **A module that believes it is registered.** ``test_every_new_rule_is_in_rule_factories``
  is that check for the rule table the security checklist audits.

The per-shot charge on the client pack gets its own test because a flat charge of 1 is
the most natural-looking way to write it and it is a hole: eight renders for the price
of one, on the route the UI puts a single button in front of.
"""

from __future__ import annotations

import base64
import importlib
import inspect
import pkgutil
import uuid
from typing import Any

import pytest
from garh_api import ratelimit
from garh_api.config import Settings
from garh_api.errors import ServiceUnavailableError
from garh_api.ratelimit import (
    RULE_FACTORIES,
    check_rate_limit,
    export_jobs_per_firm_rule,
    peek_rate_limit,
    render_jobs_per_firm_rule,
    sheet_jobs_per_firm_rule,
)
from garh_api.routers import renders as renders_router

from tests import factories
from tests.helpers import problem

pytestmark = pytest.mark.integration

#: A valid 1×1 white PNG — the render routes require a captured viewport, and a body
#: that fails validation would 422 before the limiter ran (the "test that cannot fail"
#: shape). Same constant as ``test_render_jobs.py``, inlined so this file stands alone.
TINY_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4//8/AwAI/AL+p5qgoAAAAABJRU5ErkJggg=="
assert base64.b64decode(TINY_PNG_B64).startswith(b"\x89PNG"), "the viewport fixture must be a PNG"


def _render_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "mode": "explore",
        "preset": "exterior-street-day",
        "seed": 42,
        "width": 512,
        "height": 512,
        "view": {"preset": "exterior-street-day", "fovDeg": 45},
        "inputs": {"viewportPng": TINY_PNG_B64},
    }
    body.update(overrides)
    return body


def _pack_body(shot_count: int = 3, **overrides: Any) -> dict[str, Any]:
    shots = [
        {
            "slug": slug,
            "preset": preset,
            "mode": mode,
            "view": {"preset": preset, "fovDeg": 45},
            "inputs": {"viewportPng": TINY_PNG_B64},
        }
        for slug, preset, mode in renders_router.CLIENT_PACK_SHOTS[:shot_count]
    ]
    body: dict[str, Any] = {"seed": 7, "width": 512, "height": 512, "shots": shots}
    body.update(overrides)
    return body


def _identity(firm: Any) -> str:
    """The bucket key the route dependency uses. Spelled out rather than imported so a
    change to ``FirmRateLimit``'s keying breaks these tests instead of hiding in them."""
    return "firm:%s" % firm.firm_id


async def _spend(rule: Any, firm: Any, cost: int) -> None:
    decision = await check_rate_limit(rule, _identity(firm), cost=cost)
    assert decision.allowed, "the fixture could not pre-spend %d slots" % cost


# ---------------------------------------------------------------------------
# The rules themselves
# ---------------------------------------------------------------------------


def test_new_rules_carry_their_numbers_and_their_failure_policy(settings: Settings) -> None:
    render = render_jobs_per_firm_rule(settings)
    assert (render.name, render.window_seconds, render.scope) == (
        "render_jobs.per_firm",
        3600,
        "firm",
    )
    assert render.limit == settings.rate_limit_render_jobs_per_hour

    export = export_jobs_per_firm_rule(settings)
    assert (export.name, export.window_seconds, export.scope) == (
        "export_jobs.per_firm",
        3600,
        "firm",
    )
    assert export.limit == settings.rate_limit_export_jobs_per_hour

    # The deliberate asymmetry, and the reason for it, asserted rather than assumed.
    # A render is a metered third-party call once PROVIDER_RENDER leaves 'mock', so an
    # uncounted one is a bill; an export burns our own CPU and cannot even be queued
    # without the Redis the limiter just failed to reach.
    assert render.fail_closed is True, (
        "render_jobs_per_firm_rule must fail closed — with a hosted render provider an "
        "uncounted job is a billing incident, exactly as for llm_per_firm_rule."
    )
    assert export.fail_closed is False, (
        "export_jobs_per_firm_rule must fail open — refusing here would replace an "
        "honest 'the job queue is unreachable' with a vaguer 503 for no gain."
    )

    for rule in (render, export, sheet_jobs_per_firm_rule(settings)):
        assert rule.message and rule.action, "%s cannot explain its own 429" % rule.name
        assert rule.describe()


def test_sheets_and_export_share_one_bucket_but_not_one_sentence(settings: Settings) -> None:
    """One drawings-worker budget, two feature-appropriate 429s — the LLM rule's shape."""
    export = export_jobs_per_firm_rule(settings, feature="export")
    sheets = sheet_jobs_per_firm_rule(settings)

    assert (
        sheets.name == export.name == "export_jobs.per_firm"
    ), "a split bucket lets a firm double its drawings budget by alternating routes"
    assert sheets.limit == export.limit
    assert sheets.message != export.message
    assert "drawing" in sheets.message.lower()
    assert "export" in export.message.lower()
    # An unknown feature must fall back to real copy, never raise mid-429.
    assert export_jobs_per_firm_rule(settings, feature="nope").message


#: Modules under ``garh_api`` that legitimately cannot be imported by this test run.
#: ``copilot_loop`` imports the sibling ``services.*`` package, which the API's own test
#: environment does not put on the path. The scan asserts failures stay a SUBSET of
#: this: a new module that stops importing fails the test rather than quietly shrinking
#: what the audit can see.
_UNIMPORTABLE_MODULES = frozenset({"garh_api.copilot_loop"})

#: Rule factories that are knowingly absent from ``RULE_FACTORIES``, and why.
#:
#: ``share.comments_per_ip`` is declared privately inside ``routers/share.py``. Moving
#: or registering it means editing that module, which is not this lane's to edit — it is
#: in the handoff. It is listed here so the gap is a recorded exemption rather than an
#: invisible hole, and the test below fails if the exemption stops being needed, so it
#: cannot rot.
_UNREGISTERED_BY_DESIGN = frozenset({"share.comments_per_ip"})


def _discover_rule_factories() -> dict[str, tuple[str, str]]:
    """Every ``-> RateLimitRule`` factory in ``garh_api``: rule name -> (module, func).

    Derived from the source tree, not typed out. The hazard is bug pattern 4 — a rule
    that believes it is registered — and the previous version of this test named the
    three rules it already knew about, so it could not see ``2fa_attempt``, which was
    mounted on every second-factor verification and in no audit at all. A hand-written
    list cannot detect an omission from a hand-written list.
    """
    import garh_api

    discovered: dict[str, tuple[str, str]] = {}
    failures: set[str] = set()
    for info in pkgutil.walk_packages(garh_api.__path__, prefix="garh_api."):
        try:
            module = importlib.import_module(info.name)
        except Exception:
            failures.add(info.name)
            continue
        for attribute, value in vars(module).items():
            if not inspect.isfunction(value) or value.__module__ != info.name:
                continue
            annotation = inspect.signature(value).return_annotation
            if annotation in ("RateLimitRule", ratelimit.RateLimitRule):
                discovered[value().name] = (info.name, attribute)
    assert failures <= _UNIMPORTABLE_MODULES, (
        "a garh_api module stopped importing, so this scan is blind to its rules: %s"
        % (failures - _UNIMPORTABLE_MODULES)
    )
    return discovered


def test_every_shipped_rule_is_registered_in_rule_factories(settings: Settings) -> None:
    """Bug pattern 4: a rule that believes it is registered.

    ``RULE_FACTORIES`` is what the security checklist audits — the fail-open/fail-closed
    sweep in ``test_rate_limits.py`` iterates it, and a rule outside it is unexamined
    however carefully it was written. So this walks the package for anything that
    returns a ``RateLimitRule`` and insists the registry knows its name.
    """
    discovered = _discover_rule_factories()
    registered = {factory(settings).name for factory in RULE_FACTORIES}

    assert "2fa_attempt" in discovered, "the scan lost sight of the second-factor rule"
    missing = {
        name: where
        for name, where in discovered.items()
        if name not in registered and name not in _UNREGISTERED_BY_DESIGN
    }
    assert not missing, "rate-limit rules the security audit cannot see: %s" % missing

    # An exemption that is no longer needed must not survive as cover for the next gap.
    stale = _UNREGISTERED_BY_DESIGN - set(discovered)
    assert not stale, "stale exemptions in _UNREGISTERED_BY_DESIGN: %s" % stale
    assert _UNREGISTERED_BY_DESIGN.isdisjoint(
        registered
    ), "an exempted rule is registered after all — delete the exemption"


async def test_failure_policy_is_executed_not_just_declared(
    settings: Settings, clean_redis: Any
) -> None:
    """Point the limiter at a dead Redis and watch the two policies actually diverge.

    ``fail_closed`` is a boolean anybody can flip; this is the only test in the suite
    that proves the boolean reaches ``_degraded`` and changes what a caller sees.
    """
    dead = settings.model_copy(update={"redis_url": "redis://127.0.0.1:6399/0"})
    await ratelimit.close_redis()  # drop the cached healthy client
    try:
        with pytest.raises(ServiceUnavailableError):
            await check_rate_limit(render_jobs_per_firm_rule(settings), "firm:x", settings=dead)

        admitted = await check_rate_limit(
            export_jobs_per_firm_rule(settings), "firm:x", settings=dead
        )
        assert admitted.allowed is True
        assert admitted.degraded is True, "a fail-open admission must be flagged degraded"
    finally:
        await ratelimit.close_redis()  # so the next test gets a client on the real URL


# ---------------------------------------------------------------------------
# Wiring, through real HTTP
# ---------------------------------------------------------------------------


async def test_render_route_charges_the_render_budget(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any, settings: Settings
) -> None:
    """One POST /renders spends exactly one slot of ``render_jobs.per_firm``."""
    await factories.create_version(session, firm_a, project_a.id)
    rule = render_jobs_per_firm_rule(settings)

    assert await peek_rate_limit(rule, _identity(firm_a)) == 0
    response = await client.post(
        "%s/projects/%s/renders" % (api, project_a.id),
        json=_render_body(),
        headers=firm_a.headers,
    )
    assert response.status_code == 202, response.text
    assert (
        await peek_rate_limit(rule, _identity(firm_a)) == 1
    ), "POST /renders is not charging render_jobs.per_firm for this firm"
    # A client cannot back off politely if it cannot see the budget (§13).
    assert response.headers["x-ratelimit-limit"] == str(rule.limit)
    assert int(response.headers["x-ratelimit-remaining"]) == rule.limit - 1


async def test_render_route_refuses_once_the_budget_is_spent(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any, settings: Settings
) -> None:
    await factories.create_version(session, firm_a, project_a.id)
    rule = render_jobs_per_firm_rule(settings)
    await _spend(rule, firm_a, rule.limit)

    response = await client.post(
        "%s/projects/%s/renders" % (api, project_a.id),
        json=_render_body(),
        headers=firm_a.headers,
    )
    assert response.status_code == 429, response.text
    body = problem(response)
    assert body["code"] == "rate_limited", body
    assert body["rule"] == "render_jobs.per_firm", body
    assert body["limit"] == rule.limit, body
    assert body["action"], "a 429 with no next step is unactionable (golden rule 9)"
    assert int(response.headers["retry-after"]) >= 1


async def test_client_pack_charges_one_slot_per_shot(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any, settings: Settings
) -> None:
    """The hole a flat charge would leave: eight renders for the price of one.

    Three shots must cost three. If this ever reads 1 the pack route has become the
    cheap way round the per-render budget, and the budget is decorative.
    """
    await factories.create_version(session, firm_a, project_a.id)
    rule = render_jobs_per_firm_rule(settings)

    response = await client.post(
        "%s/projects/%s/renders/client-pack" % (api, project_a.id),
        json=_pack_body(shot_count=3),
        headers=firm_a.headers,
    )
    assert response.status_code == 202, response.text
    assert len(response.json()["jobs"]) == 3
    assert (
        await peek_rate_limit(rule, _identity(firm_a)) == 3
    ), "a 3-shot pack must cost 3 slots, not 1"


async def test_a_pack_larger_than_the_remaining_budget_is_refused_whole(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any, settings: Settings
) -> None:
    """Two slots left, three shots asked for: refuse the pack rather than queue part of it."""
    await factories.create_version(session, firm_a, project_a.id)
    rule = render_jobs_per_firm_rule(settings)
    await _spend(rule, firm_a, rule.limit - 2)

    response = await client.post(
        "%s/projects/%s/renders/client-pack" % (api, project_a.id),
        json=_pack_body(shot_count=3),
        headers=firm_a.headers,
    )
    assert response.status_code == 429, response.text
    assert problem(response)["rule"] == "render_jobs.per_firm", response.text
    # Nothing partial: the two remaining slots are still there, and no job was queued.
    assert await peek_rate_limit(rule, _identity(firm_a)) == rule.limit - 2


async def test_export_and_sheets_draw_on_the_same_budget(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any, settings: Settings
) -> None:
    """One drawings-worker budget across two routes, proven through the bucket itself.

    Both requests answer 409 (this project has no saved version to draw), which is the
    point: the ceiling is charged by the route dependency before the handler runs, so
    this test needs no object storage and still proves the wiring.
    """
    rule = export_jobs_per_firm_rule(settings)
    identity = _identity(firm_a)
    assert await peek_rate_limit(rule, identity) == 0

    sheets = await client.post(
        "%s/projects/%s/sheets/generate" % (api, project_a.id),
        json={"sheetSize": "A2"},
        headers=firm_a.headers,
    )
    assert sheets.status_code == 409, sheets.text
    assert await peek_rate_limit(rule, identity) == 1, "/sheets/generate charged nothing"

    export = await client.post(
        "%s/projects/%s/export" % (api, project_a.id),
        json={"kind": "dxf"},
        headers=firm_a.headers,
    )
    assert export.status_code == 409, export.text
    assert await peek_rate_limit(rule, identity) == 2, (
        "/export charged a DIFFERENT bucket from /sheets/generate — a firm can double "
        "its drawings budget by alternating routes"
    )


async def test_sheets_and_export_both_refuse_once_the_shared_budget_is_spent(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any, settings: Settings
) -> None:
    rule = export_jobs_per_firm_rule(settings)
    await _spend(rule, firm_a, rule.limit)

    sheets = await client.post(
        "%s/projects/%s/sheets/generate" % (api, project_a.id),
        json={"sheetSize": "A2"},
        headers=firm_a.headers,
    )
    assert sheets.status_code == 429, sheets.text
    sheets_body = problem(sheets)
    assert sheets_body["rule"] == "export_jobs.per_firm", sheets_body
    assert (
        "drawing" in sheets_body["message"].lower()
    ), "a drawing-set user told they are out of 'exports' reads it as a bug"

    export = await client.post(
        "%s/projects/%s/export" % (api, project_a.id),
        json={"kind": "dxf"},
        headers=firm_a.headers,
    )
    assert export.status_code == 429, export.text
    assert problem(export)["rule"] == "export_jobs.per_firm", export.text


async def test_the_three_job_budgets_are_independent(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any, settings: Settings
) -> None:
    """Spending the render budget must not stop a solve or an export.

    A copy-pasted ``name=`` on a new rule would merge two budgets silently; nothing else
    in the suite would notice.
    """
    await factories.seed_plot_and_brief(session, firm_a, project_a.id)
    await _spend(
        render_jobs_per_firm_rule(settings), firm_a, settings.rate_limit_render_jobs_per_hour
    )

    solve = await client.post(
        "%s/projects/%s/solve" % (api, project_a.id),
        json={"optionCount": 3},
        headers=firm_a.headers,
    )
    assert solve.status_code == 202, solve.text

    export = await client.post(
        "%s/projects/%s/export" % (api, project_a.id),
        json={"kind": "dxf"},
        headers=firm_a.headers,
    )
    assert export.status_code != 429, export.text


async def test_the_render_budget_is_per_firm_not_global(
    client: Any,
    api: str,
    session: Any,
    firm_a: Any,
    firm_b: Any,
    project_a: Any,
    settings: Settings,
) -> None:
    """One firm exhausting its renders must not throttle another firm's."""
    project_b = await factories.create_project(session, firm_b, name="Firm B project")
    await factories.create_version(session, firm_b, project_b.id)
    await _spend(
        render_jobs_per_firm_rule(settings), firm_a, settings.rate_limit_render_jobs_per_hour
    )

    response = await client.post(
        "%s/projects/%s/renders" % (api, project_b.id),
        json=_render_body(),
        headers=firm_b.headers,
    )
    assert response.status_code == 202, response.text


async def test_buckets_are_keyed_by_firm_id(
    session: Any, firm_a: Any, firm_b: Any, settings: Settings, clean_redis: Any
) -> None:
    """The identity string the route builds is the firm id and nothing else.

    Guards the other direction of the same wire: a limit keyed on, say, the project id
    would still pass every test above while letting one firm buy more budget by making
    more projects.
    """
    rule = render_jobs_per_firm_rule(settings)
    await _spend(rule, firm_a, 2)

    assert await peek_rate_limit(rule, _identity(firm_a)) == 2
    assert await peek_rate_limit(rule, _identity(firm_b)) == 0
    assert await peek_rate_limit(rule, "firm:%s" % uuid.uuid4()) == 0


# ---------------------------------------------------------------------------
# Negative control
# ---------------------------------------------------------------------------
#
# Removing ``two_factor_attempt_rule`` from ``RULE_FACTORIES`` — the state it shipped
# in — makes ``test_every_shipped_rule_is_registered_in_rule_factories`` fail naming
# both the rule and the module that declares it, and takes the fail-closed sweep in
# ``test_rate_limits.py`` down with it (``KeyError: 'two_factor_attempt_rule'``).
