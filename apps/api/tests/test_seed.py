"""The seed script (playbook §17) — idempotency, honesty, and the production guard.

Three things must hold, and all three are the kind that only a test keeps true:

1. **Re-running changes nothing.** ``make seed`` is documented as safe to re-run and CI's
   e2e job runs it against a stack that may already be seeded. A seeder that appends the
   plot boundary a second time produces a demo project with two boundaries and a state hash
   nobody can reproduce.
2. **The unfinished parts stay unfinished.** §17 asks for a solved plan, a facade, two
   renders and a sheet set; none of those subsystems exist before Phases 3/5/7/8. The seed
   deliberately does not fake them, and :func:`test_pending_phases_are_declared_not_faked`
   asserts that the extension points are still empty *and* still reported — so the report an
   operator reads matches reality, and so filling one in is a visible change.
3. **Production is refused.** The demo firm is a real tenant whose mailbox nobody owns.

The demo project is also the universal fixture (tours, goldens, perf budgets, screenshots),
so its identity — firm name, user email, plot, brief — is asserted rather than assumed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select

from garh_api import models
from garh_api.seed import demo as demo_data
from garh_api.seed.runner import CREATED, REUSED, SKIPPED, SeedError, SeedOptions, seed

pytestmark = pytest.mark.integration


async def _count(session: Any, model: Any) -> int:
    return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


# ---------------------------------------------------------------------------
# What §17 asks for that exists today
# ---------------------------------------------------------------------------


async def test_seed_creates_the_demo_firm_user_and_project(session: Any) -> None:
    result = await seed(session, SeedOptions())
    await session.commit()

    assert result.steps["firm"] == CREATED
    assert result.steps["user"] == CREATED
    assert result.steps["demoProject"] == CREATED
    assert result.steps["opLog"] == CREATED
    assert result.steps["version"] == CREATED

    firm = (await session.execute(select(models.Firm))).scalars().one()
    assert firm.name == demo_data.DEMO_FIRM_NAME == "Studio Demo"

    user = (await session.execute(select(models.User))).scalars().one()
    assert user.email == demo_data.DEMO_USER_EMAIL == "demo@garh.ai"
    assert user.role == "admin"
    assert user.firm_id == firm.id

    project = (await session.execute(select(models.Project))).scalars().one()
    assert project.demo is True, "every empty state offers the demo project (delight rule 8)"
    assert project.city_pack == "blr"
    assert project.units == "ft-in"
    assert project.firm_id == firm.id


async def test_seed_writes_the_plot_and_brief_as_ops_not_just_rows(session: Any) -> None:
    """Golden rule 1: the op log is the truth, ``plots``/``briefs`` are a projection.

    Seeding the tables directly would give the demo project an empty folded document and a
    populated plot form — the exact inconsistency the sequencer exists to prevent.
    """
    result = await seed(session, SeedOptions())
    await session.commit()

    ops = (await session.execute(select(models.Op).order_by(models.Op.idx))).scalars().all()
    types = [op.type for op in ops]
    assert types[:4] == [
        "plot.set_boundary",
        "plot.set_north",
        "plot.set_road",
        "plot.set_reg_profile",
    ], types
    assert "brief.update" in types
    assert types.count("storey.add") == 2, "G+1 means two storeys (§17)"
    assert all(op.source == "system" for op in ops), "the seeder is not a user"
    assert result.head_idx == len(ops) - 1

    plot = (await session.execute(select(models.Plot))).scalars().one()
    assert plot.north_deg == 0, "north up (§17)"
    assert plot.roads[0]["widthMm"] == 9000, "9 m road (§17)"
    assert plot.roads[0]["edgeIndex"] == 0, "on the south edge"
    assert plot.source == "seed"
    # 30 x 40 ft, in integer millimetres, straight from the shared model fixture.
    assert plot.boundary[1] == {"x": 9144, "y": 0}
    assert plot.boundary[2] == {"x": 9144, "y": 12192}

    brief = (await session.execute(select(models.Brief))).scalars().one()
    assert brief.data["bedrooms"] == 3, "3BHK (§17)"
    assert brief.data["floorsAboveGround"] == 1, "G+1 (§17)"
    assert brief.vastu_mode in models.VASTU_MODES


async def test_seeded_version_has_a_folded_snapshot(session: Any) -> None:
    """The named version must carry a real snapshot, or "open project" replays every op."""
    result = await seed(session, SeedOptions())
    await session.commit()

    version = (await session.execute(select(models.DesignVersion))).scalars().one()
    assert version.id == result.version_id
    assert version.kind == "named"
    assert version.name == demo_data.DEMO_VERSION_NAME
    assert version.snapshot is not None
    assert version.snapshot["doc"], "the snapshot envelope has no document"
    assert version.snapshot["atIdx"] == result.head_idx
    assert version.snapshot_hash
    assert len(version.snapshot["stateHash"]) == 64


async def test_seed_records_the_rulepacks_and_catalogue_on_the_firm(session: Any) -> None:
    """§17 "3 rule packs + nbc-core + vastu", recorded so a report can be traced to them."""
    result = await seed(session, SeedOptions())
    await session.commit()

    assert set(result.rulepacks) == {"nbc-core", "blr", "ncr", "hyd", "vastu"}

    firm = (await session.execute(select(models.Firm))).scalars().one()
    settings = firm.settings
    assert settings["defaultCityPack"] == "blr"
    assert set(settings["rulePacks"]["versions"]) == set(result.rulepacks)
    assert settings["catalog"]["counts"]["furniture"] >= 30, "§17 minimum"
    assert settings["catalog"]["counts"]["materials"] >= 20, "§17 minimum"
    assert settings["catalog"]["facadeKitIds"] == ["contemporary", "modern-minimal"]
    assert settings["catalog"]["digest"], "no catalogue digest — drift becomes a mystery"
    assert settings["titleBlock"]["firmName"] == demo_data.DEMO_FIRM_NAME


async def test_seed_writes_an_audit_row(session: Any) -> None:
    """§13 audits privileged operations, and seeding creates a tenant."""
    await seed(session, SeedOptions())
    await session.commit()

    entries = (await session.execute(select(models.AuditLog))).scalars().all()
    actions = {entry.action for entry in entries}
    assert "seed.completed" in actions, actions


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_seeding_twice_changes_nothing(session: Any) -> None:
    """Re-running must reuse, not duplicate — across transactions, as ``make seed`` does."""
    first = await seed(session, SeedOptions())
    await session.commit()

    counts_before = {
        model.__name__: await _count(session, model)
        for model in (models.Firm, models.User, models.Project, models.Plot, models.Brief, models.Op, models.DesignVersion)
    }

    second = await seed(session, SeedOptions())
    await session.commit()

    assert second.firm_id == first.firm_id
    assert second.user_id == first.user_id
    assert second.project_id == first.project_id
    assert second.version_id == first.version_id
    assert second.head_idx == first.head_idx
    assert second.ops_appended == 0, "the second run appended ops"

    for step in ("firm", "user", "demoProject", "opLog", "version"):
        assert second.steps[step] == REUSED, (step, second.steps)

    counts_after = {
        model.__name__: await _count(session, model)
        for model in (models.Firm, models.User, models.Project, models.Plot, models.Brief, models.Op, models.DesignVersion)
    }
    assert counts_after == counts_before, "a re-seed changed the row counts"


async def test_seeding_three_times_is_still_stable(session: Any) -> None:
    """Once is luck, twice is a pattern, three times is idempotent."""
    hashes = []
    for _ in range(3):
        result = await seed(session, SeedOptions())
        await session.commit()
        hashes.append((result.firm_id, result.project_id, result.head_idx))
    assert len(set(hashes)) == 1, hashes
    assert await _count(session, models.Op) > 0


async def test_dry_run_writes_nothing(session: Any) -> None:
    """``--dry-run`` validates the input files and touches no table."""
    result = await seed(session, SeedOptions(dry_run=True))
    await session.commit()

    assert result.dry_run is True
    assert set(result.steps.values()) == {SKIPPED}, result.steps
    assert result.firm_id is None
    assert result.project_id is None
    for model in (models.Firm, models.User, models.Project, models.Op):
        assert await _count(session, model) == 0, model.__name__

    # It still validated the data, which is the point of a dry run.
    assert result.catalog["counts"]["furniture"] >= 30
    assert set(result.rulepacks) == {"nbc-core", "blr", "ncr", "hyd", "vastu"}
    assert result.brief_source


async def test_reset_demo_rebuilds_the_project(session: Any) -> None:
    """``--reset-demo`` is the escape hatch for a demo project someone edited."""
    first = await seed(session, SeedOptions())
    await session.commit()

    reset = await seed(session, SeedOptions(reset_demo=True))
    await session.commit()

    assert reset.project_id != first.project_id, "reset-demo reused the old project"
    assert reset.firm_id == first.firm_id, "reset-demo must not recreate the firm"
    assert await _count(session, models.Project) == 1
    assert reset.ops_appended == first.ops_appended


# ---------------------------------------------------------------------------
# Honesty about the unfinished parts
# ---------------------------------------------------------------------------


def test_pending_phases_are_declared_not_faked() -> None:
    """The four §17 rows that need later phases are empty extension points, with reasons.

    If a phase lands and fills one in, this test tells whoever did it to move the entry out
    of ``PENDING_PHASES`` — so the seed report can never claim a solved plan it does not have,
    and can never stay silent about one it does.
    """
    pending = {item["item"]: item for item in demo_data.PENDING_PHASES}
    assert set(pending) == {"solvedPlan", "facade", "renders", "sheets"}

    for item in pending.values():
        assert item["phase"] in {"3", "5", "7", "8"}, item
        assert item["extensionPoint"].startswith("garh_api.seed.demo."), item
        assert len(item["why"]) > 40, "every deferral needs a real reason: %r" % item

    storeys = list(demo_data.demo_storey_ids())
    assert demo_data.solved_plan_ops(storey_ids=storeys) == [], (
        "solved_plan_ops returns geometry. If Phase 3 has landed, that geometry must come "
        "from a real solver run captured as a golden file — and this entry must move out of "
        "PENDING_PHASES."
    )
    assert demo_data.facade_ops(storey_ids=storeys) == []
    assert demo_data.demo_render_requests() == []
    assert demo_data.demo_sheet_kinds() == []


async def test_seed_report_names_what_is_missing(session: Any) -> None:
    """An operator reading the report must be told what was not seeded, and why."""
    result = await seed(session, SeedOptions(dry_run=True))
    rendered = result.render()

    assert "Not seeded yet" in rendered
    for item in ("solvedPlan", "facade", "renders", "sheets"):
        assert item in rendered, rendered
    assert demo_data.DEMO_USER_EMAIL in rendered
    assert [entry["item"] for entry in result.pending] == [
        "solvedPlan",
        "facade",
        "renders",
        "sheets",
    ]

    payload = result.to_json()
    assert payload["dryRun"] is True
    assert [entry["item"] for entry in payload["pending"]] == [
        "solvedPlan",
        "facade",
        "renders",
        "sheets",
    ]


async def test_demo_project_status_matches_what_was_actually_seeded(session: Any) -> None:
    """"brief", not "design": the project is at the stage the data really reaches."""
    await seed(session, SeedOptions())
    await session.commit()
    project = (await session.execute(select(models.Project))).scalars().one()
    assert project.status == "brief", (
        "the demo project claims a stage it has no data for; it becomes 'options' the "
        "first time the solver runs"
    )
    assert await _count(session, models.SolverJob) == 0
    assert await _count(session, models.RenderJob) == 0
    assert await _count(session, models.Sheet) == 0


# ---------------------------------------------------------------------------
# The production guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_seed_refuses_production_by_default(env: str) -> None:
    """The demo firm in production is a mistake, not a convenience."""
    production = SimpleNamespace(is_production=True, env=env)
    with pytest.raises(SeedError) as excinfo:
        SeedOptions().assert_allowed(production)  # type: ignore[arg-type]
    message = str(excinfo.value)
    assert env in message
    assert "--allow-production" in message, "the refusal must name the escape hatch"


def test_explicit_flag_permits_production() -> None:
    production = SimpleNamespace(is_production=True, env="staging")
    SeedOptions(allow_production=True).assert_allowed(production)  # type: ignore[arg-type]


def test_env_var_permits_production(monkeypatch: Any) -> None:
    from garh_api.seed.runner import ALLOW_PROD_ENV

    monkeypatch.setenv(ALLOW_PROD_ENV, "1")
    production = SimpleNamespace(is_production=True, env="prod")
    SeedOptions().assert_allowed(production)  # type: ignore[arg-type]


def test_dev_needs_no_flag(settings: Any) -> None:
    SeedOptions().assert_allowed(settings)
    assert settings.is_production is False


# ---------------------------------------------------------------------------
# The seeded user can actually sign in — the Phase 0 DoD sentence, literally
# ---------------------------------------------------------------------------


async def test_the_seeded_user_can_sign_in_and_see_the_demo_project(
    session: Any, client: Any, api: str
) -> None:
    """``docker compose up`` → login → the demo project is there.

    This is the DoD walked through HTTP with nothing but the seed: request a code as
    ``demo@garh.ai``, exchange it, list projects.
    """
    await seed(session, SeedOptions())
    await session.commit()

    issued = await client.post("%s/auth/otp" % api, json={"email": demo_data.DEMO_USER_EMAIL})
    assert issued.status_code == 202, issued.text
    code = issued.json()["devCode"]
    assert code, "the seeded user cannot sign in without a mail provider or the dev echo"

    verified = await client.post(
        "%s/auth/verify" % api, json={"email": demo_data.DEMO_USER_EMAIL, "code": code}
    )
    assert verified.status_code == 200, verified.text
    headers = {"Authorization": "Bearer %s" % verified.json()["accessToken"]}
    assert verified.json()["firm"]["name"] == demo_data.DEMO_FIRM_NAME

    listed = await client.get("%s/projects" % api, headers=headers)
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert len(items) == 1
    assert items[0]["demo"] is True

    shell = await client.get("%s/projects/%s" % (api, items[0]["id"]), headers=headers)
    assert shell.status_code == 200, shell.text
    body = shell.json()
    assert body["plot"] is not None, "the demo project must open with its plot filled in"
    assert body["brief"]["data"]["bedrooms"] == 3
    assert body["headIdx"] >= 6
