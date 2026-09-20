"""``GET /admin/ops`` and the ``observability`` half of ``/healthz`` — negative-tested.

The claim under test is the reader's: *observability cannot silently be off*. Each
section of the ops document has the run that would break it:

* the gate — anonymous 401, a firm admin 403, an empty allowlist 403, the owner 200;
* queue depth — zero before a push, one after, so a depth that read 0 forever
  would fail;
* worker heartbeats — a fresh key is listed, a key past two intervals is ``stale``,
  the three expected names are ``missing`` until a heartbeat arrives;
* job counts and percentiles — cover BOTH firms (the owner's view is the
  deployment's) while the body carries no job, project or firm id;
* Sentry — ``off`` on the zero-keys default, ``on`` with a DSN, and the DSN itself
  never appears in either document;
* migrations — never stamped / behind / at head / two heads, each named in words;
* a Redis outage names itself in ``observability.redis`` instead of 500ing the page.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from garh_api import platform_status as ps
from garh_api.config import get_settings
from garh_api.queue import delayed_queue, queue_name
from sqlalchemy import text

from tests.factories import create_project, create_solver_job

FAKE_DSN = "https://publickey@o0.ingest.sentry.io/0"


def _make_owner(monkeypatch: Any, actor: Any) -> None:
    monkeypatch.setattr(get_settings(), "platform_owner_emails", actor.email, raising=False)


def _heartbeat(worker: str, *, sent_at: datetime, interval: int = 10, **extra: Any) -> str:
    body: dict[str, Any] = {
        "worker": worker,
        "queue": "garh:queue:%s" % worker,
        "instance": "host-%s-1" % worker,
        "hostname": "host-%s" % worker,
        "pid": 7,
        "env": "dev",
        "release": None,
        "sentAt": sent_at.isoformat(),
        "intervalSeconds": interval,
        "uptimeSeconds": 120,
        "draining": False,
        "inFlight": 1,
        "concurrency": 2,
        "jobsReceived": 5,
        "jobsSucceeded": 4,
        "jobsFailed": 1,
        "jobsRetried": 0,
        "jobsDeadLettered": 0,
        "p50DurationMs": 800,
        "p95DurationMs": 2400,
        "queueDepth": {"pending": 0, "delayed": 0, "processing": 1, "dead": 0},
        "providerLlm": "mock",
        "providerRender": "mock",
        "sentry": "off",
    }
    body.update(extra)
    return json.dumps(body)


# ---------------------------------------------------------------------------
# Pure verdicts
# ---------------------------------------------------------------------------


def test_migration_status_names_every_shape_in_words() -> None:
    never = ps.migration_status(None, ["0016_x"])
    assert never.up_to_date is False and "never run" in (never.reason or "")

    behind = ps.migration_status(["0015_x"], ["0016_x"])
    assert behind.up_to_date is False
    assert "0015_x" in (behind.reason or "") and "alembic upgrade head" in (behind.reason or "")

    at_head = ps.migration_status(["0016_x"], ["0016_x"])
    assert at_head.up_to_date is True and at_head.reason is None

    two_heads = ps.migration_status(["0016_x", "0017_y"], ["0017_y", "0016_x"])
    assert two_heads.up_to_date is True
    assert "2 heads" in (two_heads.reason or "")

    no_scripts = ps.migration_status(["0016_x"], [])
    assert no_scripts.up_to_date is False and "no migration scripts" in (no_scripts.reason or "")


def test_script_heads_reads_the_real_migration_directory() -> None:
    heads = ps.script_heads()
    assert heads, "no head found beside the package — MIGRATIONS_DIR is wrong"
    assert all(head[:4].isdigit() for head in heads), heads


def test_a_heartbeat_past_two_intervals_is_stale_and_an_unreadable_one_is_dropped() -> None:
    now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    fresh = ps.worker_from_heartbeat(
        json.loads(_heartbeat("solver", sent_at=now - timedelta(seconds=15))), now=now
    )
    assert fresh is not None and fresh.stale is False and fresh.age_seconds == 15

    stale = ps.worker_from_heartbeat(
        json.loads(_heartbeat("solver", sent_at=now - timedelta(seconds=21))), now=now
    )
    assert stale is not None and stale.stale is True

    assert ps.worker_from_heartbeat({"worker": "solver"}, now=now) is None
    assert (
        ps.worker_from_heartbeat({"worker": "x", "instance": "y", "sentAt": "no"}, now=now) is None
    )


def test_the_expected_workers_mirror_the_worker_side_contract() -> None:
    from services.common.heartbeat import EXPECTED_WORKERS as WORKER_SIDE

    assert set(ps.EXPECTED_WORKERS) == set(WORKER_SIDE)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_anonymous_is_401(client: Any, api: str) -> None:
    response = await client.get("%s/admin/ops" % api)
    assert response.status_code == 401, response.text


@pytest.mark.integration
async def test_a_firm_admin_who_is_not_the_owner_is_403(
    client: Any, api: str, firm_a: Any, monkeypatch: Any
) -> None:
    monkeypatch.setattr(
        get_settings(), "platform_owner_emails", "owner@garh.example", raising=False
    )
    response = await client.get("%s/admin/ops" % api, headers=firm_a.headers)
    assert response.status_code == 403, response.text


@pytest.mark.integration
async def test_an_empty_allowlist_refuses_everyone(
    client: Any, api: str, firm_a: Any, monkeypatch: Any
) -> None:
    monkeypatch.setattr(get_settings(), "platform_owner_emails", "", raising=False)
    response = await client.get("%s/admin/ops" % api, headers=firm_a.headers)
    assert response.status_code == 403, response.text


def _about(actual: Any, expected: int, label: str, tolerance_ms: int = 2_000) -> None:
    """A duration in milliseconds, within a tolerance smaller than any gap that matters."""
    assert abs(int(actual) - expected) <= tolerance_ms, "%s was %s, expected about %s" % (
        label,
        actual,
        expected,
    )


async def _alembic_stamp(session: Any) -> list[str]:
    """The revisions this database is stamped at — empty when create_all built it."""
    present = await session.execute(text("SELECT to_regclass('alembic_version')"))
    if present.scalar() is None:
        return []
    rows = await session.execute(text("SELECT version_num FROM alembic_version"))
    return [str(value) for value in rows.scalars().all()]


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_the_owner_reads_the_zero_keys_default_honestly(
    client: Any, api: str, session: Any, firm_a: Any, monkeypatch: Any
) -> None:
    _make_owner(monkeypatch, firm_a)
    response = await client.get("%s/admin/ops" % api, headers=firm_a.headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["sentry"] == "off"
    assert body["observability"]["sentry"] == "off"
    assert body["providers"] == {
        "llm": "mock",
        "render": "mock",
        "billing": "mock",
        "mail": "dev-echo",
    }
    assert body["observability"]["redis"] == "ok" and body["observability"]["database"] == "ok"
    # Nothing has written a heartbeat: every expected worker is MISSING, by name.
    assert body["observability"]["workersReporting"] == 0
    assert body["observability"]["workersMissing"] == ["solver", "render", "drawings"]
    assert body["workers"] == []
    # Whether the schema is stamped depends on WHO built it, so this asserts that the
    # page tells the truth rather than assuming one environment. The suite normally
    # runs on a create_all schema with no alembic_version row (upToDate false, "never
    # run"); CI applies `alembic upgrade head` first, so the same endpoint must report
    # true there. Hard-coding false passed locally and failed CI run 90.
    stamped = await _alembic_stamp(session)
    assert body["migrations"]["heads"] == ps.script_heads()
    if not stamped:
        assert body["migrations"]["upToDate"] is False
        assert "never run" in body["migrations"]["reason"]
    else:
        assert sorted(stamped) == sorted(ps.script_heads()), (
            "the database is stamped at %s but the scripts head at %s"
            % (sorted(stamped), sorted(ps.script_heads()))
        )
        assert body["migrations"]["upToDate"] is True, body["migrations"]
    assert [q["worker"] for q in body["queues"]] == ["solver", "render", "drawings"]
    assert all(
        q["pending"] == q["delayed"] == q["processing"] == q["dead"] == 0 for q in body["queues"]
    )
    assert body["jobWindowHours"] == 24
    assert {j["kind"] for j in body["jobs"]} == {"solver", "render"}
    assert body["exportJobsLive"] == 0


@pytest.mark.integration
async def test_queue_depth_counts_what_is_actually_queued(
    client: Any, api: str, firm_a: Any, monkeypatch: Any, clean_redis: Any
) -> None:
    _make_owner(monkeypatch, firm_a)
    settings = get_settings()

    before = (await client.get("%s/admin/ops" % api, headers=firm_a.headers)).json()
    solver_before = next(q for q in before["queues"] if q["worker"] == "solver")
    assert solver_before["pending"] == 0 and solver_before["delayed"] == 0

    clean_redis.lpush(queue_name("solver", settings), '{"jobId": "x"}')
    clean_redis.lpush(queue_name("solver", settings), '{"jobId": "y"}')
    clean_redis.zadd(delayed_queue("solver", settings), {'{"jobId": "z"}': 1.0})
    clean_redis.lpush("%s:dead" % queue_name("render", settings), '{"jobId": "d"}')

    after = (await client.get("%s/admin/ops" % api, headers=firm_a.headers)).json()
    solver = next(q for q in after["queues"] if q["worker"] == "solver")
    render = next(q for q in after["queues"] if q["worker"] == "render")
    assert solver["pending"] == 2 and solver["delayed"] == 1
    assert render["dead"] == 1 and render["pending"] == 0


@pytest.mark.integration
async def test_worker_heartbeats_are_listed_missing_ones_named_and_stale_ones_flagged(
    client: Any, api: str, firm_a: Any, monkeypatch: Any, clean_redis: Any
) -> None:
    _make_owner(monkeypatch, firm_a)
    now = datetime.now(UTC)
    clean_redis.set("garh:worker:solver:host-solver-1", _heartbeat("solver", sent_at=now), ex=60)
    clean_redis.set(
        "garh:worker:render:host-render-1",
        _heartbeat("render", sent_at=now - timedelta(seconds=45), providerRender="stability"),
        ex=60,
    )
    clean_redis.set("garh:worker:drawings:garbage", "not json", ex=60)

    body = (await client.get("%s/admin/ops" % api, headers=firm_a.headers)).json()
    workers = {w["instance"]: w for w in body["workers"]}
    assert set(workers) == {"host-solver-1", "host-render-1"}
    assert workers["host-solver-1"]["stale"] is False
    assert workers["host-solver-1"]["jobsSucceeded"] == 4
    assert workers["host-solver-1"]["p95DurationMs"] == 2400
    assert workers["host-render-1"]["stale"] is True
    assert workers["host-render-1"]["providerRender"] == "stability"
    assert body["observability"]["workersReporting"] == 2
    assert body["observability"]["workersMissing"] == ["drawings"]
    assert body["observability"]["workersStale"] == ["host-render-1"]


@pytest.mark.integration
async def test_job_counts_and_percentiles_cover_every_firm_and_carry_no_ids(
    client: Any, api: str, session: Any, firm_a: Any, firm_b: Any, project_a: Any, monkeypatch: Any
) -> None:
    _make_owner(monkeypatch, firm_a)
    project_b = await create_project(session, firm_b, name="Other Practice House")

    done_a = await create_solver_job(session, firm_a, project_a.id)
    done_b = await create_solver_job(session, firm_b, project_b.id)
    failed_b = await create_solver_job(session, firm_b, project_b.id)
    queued_a = await create_solver_job(session, firm_a, project_a.id)

    # Terminal rows with known enqueue→terminal spans: 30 s, 90 s, and a failed 10 s.
    #
    # The span is created by moving created_at BACK, never by writing updated_at
    # forward. Every table the migrations build carries a BEFORE UPDATE trigger
    # (garh_set_updated_at, migration 0001) that rewrites updated_at to now(), so an
    # UPDATE that sets updated_at itself is silently discarded wherever the schema came
    # from alembic — which is CI and production. Writing it survived here only because
    # this suite's schema comes from metadata.create_all, which carries none of those
    # 26 triggers. CI run 90 measured 8 ms instead of 30 000.
    for job_id, status, seconds in (
        (done_a.id, "succeeded", 30),
        (done_b.id, "succeeded", 90),
        (failed_b.id, "failed", 10),
    ):
        await session.execute(
            text(
                "UPDATE solver_jobs SET status = :status, "
                "created_at = updated_at - make_interval(secs => :secs) WHERE id = :id"
            ),
            {"status": status, "secs": seconds, "id": job_id},
        )
    await session.commit()

    response = await client.get("%s/admin/ops" % api, headers=firm_a.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    solver = next(j for j in body["jobs"] if j["kind"] == "solver")
    assert solver["counts"] == {
        "queued": 1,
        "running": 0,
        "succeeded": 2,
        "failed": 1,
        "cancelled": 0,
    }
    assert solver["terminalCount"] == 3
    # percentile_cont over [10 000, 30 000, 90 000] ms, within a tolerance far smaller
    # than the gaps between those three numbers — so a wrong percentile (p95 reading
    # 90 000, p50 reading 10 000) or a span that was never seeded (CI run 90 measured
    # 8 ms) still fails. The tolerance is needed because on a migrated schema the
    # BEFORE UPDATE trigger rewrites updated_at to the statement's now(), which adds
    # the few milliseconds between the row's creation and this update.
    _about(solver["p50Ms"], 30_000, "p50")
    _about(solver["p95Ms"], 84_000, "p95")
    _about(solver["maxMs"], 90_000, "max")
    render = next(j for j in body["jobs"] if j["kind"] == "render")
    assert render["terminalCount"] == 0 and render["p50Ms"] == 0

    # The owner's view is aggregate: no row, id or name from either firm reaches it.
    flat = json.dumps(body)
    for leaked in (
        str(done_a.id),
        str(done_b.id),
        str(queued_a.id),
        str(project_a.id),
        str(project_b.id),
        str(firm_a.firm_id),
        str(firm_b.firm_id),
        "Other Practice House",
        "Sharma",
    ):
        assert leaked not in flat, leaked


@pytest.mark.integration
async def test_sentry_reads_on_with_a_dsn_and_the_dsn_never_appears(
    client: Any, api: str, firm_a: Any, monkeypatch: Any
) -> None:
    _make_owner(monkeypatch, firm_a)
    settings = get_settings()
    monkeypatch.setattr(settings, "sentry_dsn", FAKE_DSN, raising=False)

    ops = await client.get("%s/admin/ops" % api, headers=firm_a.headers)
    assert ops.status_code == 200, ops.text
    assert ops.json()["sentry"] == "on"
    assert ops.json()["observability"]["sentry"] == "on"
    flat = ops.text
    assert FAKE_DSN not in flat and "publickey" not in flat
    assert settings.database_url not in flat
    assert settings.redis_url not in flat
    assert "password" not in flat.lower()

    health = await client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["observability"]["sentry"] == "on"
    assert FAKE_DSN not in health.text


@pytest.mark.integration
async def test_healthz_says_sentry_is_off_on_the_zero_keys_default(client: Any) -> None:
    health = await client.get("/healthz")
    assert health.status_code == 200, health.text
    body = health.json()
    assert body["observability"] == {"sentry": "off", "logFormat": get_settings().log_format}
    # The liveness contract is untouched: still `ok`, still dependency-free.
    assert body["status"] == "ok"


@pytest.mark.integration
async def test_a_redis_outage_is_named_not_hidden(
    client: Any, api: str, firm_a: Any, monkeypatch: Any
) -> None:
    _make_owner(monkeypatch, firm_a)

    def _boom(_settings: Any = None) -> Any:
        raise ConnectionError("redis refused")

    monkeypatch.setattr(ps, "get_redis", _boom)
    response = await client.get("%s/admin/ops" % api, headers=firm_a.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["observability"]["redis"] == "down"
    assert body["queues"] == [] and body["workers"] == []
    # Empty must not read as idle: the missing list still names all three.
    assert body["observability"]["workersMissing"] == ["solver", "render", "drawings"]
    # The database half still answered.
    assert body["observability"]["database"] == "ok"
    assert {j["kind"] for j in body["jobs"]} == {"solver", "render"}


@pytest.mark.integration
async def test_migrations_read_up_to_date_when_alembic_version_matches_the_scripts(
    client: Any, api: str, session: Any, firm_a: Any, monkeypatch: Any
) -> None:
    """The suite's schema is create_all, so stamp a scratch alembic_version by hand
    and drop it again — the verdict must flip from "never run" to up to date."""
    _make_owner(monkeypatch, firm_a)
    heads = ps.script_heads()
    await session.execute(text("DROP TABLE IF EXISTS alembic_version"))
    await session.execute(text("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)"))
    for head in heads:
        await session.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": head}
        )
    await session.commit()
    try:
        body = (await client.get("%s/admin/ops" % api, headers=firm_a.headers)).json()
        assert body["migrations"]["current"] == heads
        assert body["migrations"]["upToDate"] is True

        # Behind by one: the verdict names both revisions and the command.
        await session.execute(
            text("UPDATE alembic_version SET version_num = '0001_initial_schema'")
        )
        await session.commit()
        body = (await client.get("%s/admin/ops" % api, headers=firm_a.headers)).json()
        assert body["migrations"]["upToDate"] is False
        assert "0001_initial_schema" in body["migrations"]["reason"]
        assert "alembic upgrade head" in body["migrations"]["reason"]
    finally:
        await session.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await session.commit()


@pytest.mark.integration
async def test_export_jobs_are_counted_from_their_redis_keys(
    client: Any, api: str, firm_a: Any, monkeypatch: Any, clean_redis: Any
) -> None:
    _make_owner(monkeypatch, firm_a)
    clean_redis.set("garh:export:%s:%s" % (firm_a.firm_id, uuid.uuid4()), "{}", ex=60)
    clean_redis.set("garh:export:%s:%s" % (uuid.uuid4(), uuid.uuid4()), "{}", ex=60)
    body = (await client.get("%s/admin/ops" % api, headers=firm_a.headers)).json()
    assert body["exportJobsLive"] == 2
