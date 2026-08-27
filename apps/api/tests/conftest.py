"""Shared fixtures: a real Postgres, a real Redis, and one app per test.

## Why real datastores

Phase 0's DoD is *"a cross-tenant access attempt test proves 404/403"*. That claim is only
worth something against the real thing: the tenancy guarantee lives in SQL ``WHERE``
clauses and in a partial unique index, and a mocked session would let a broken filter pass.
The op sequencer's 409 is likewise produced by a Postgres advisory lock and a unique
constraint, and the auth layer's reuse detection by a Lua script inside Redis. So: no
mocks of either datastore, anywhere in this suite.

Everything comes from the environment, with the same defaults ``docker compose`` uses::

    DATABASE_URL=postgresql+psycopg://garh:garh@localhost:5432/garh
    REDIS_URL=redis://localhost:6379/0

## Why the suite runs with ``APP_ENV=dev`` and not ``test``

Since 2026-08-27 the ``_fail_fast_on_missing_secrets`` validator exempts ``test``
alongside ``dev`` (its old refusal is pinned, now as a passing test, in
``test_config_env.py::test_test_env_can_point_at_the_local_stack``), so ``APP_ENV=test``
*boots* against the local stack — that is what CI's ``alembic upgrade head`` step needs.
This module still forces ``APP_ENV=dev`` for the suite itself, for one concrete reason:
``refresh_cookie_secure`` is ``not is_dev``, so under ``test`` the rotating refresh
cookie is ``Secure`` — and this suite's ASGI client talks ``http://``, where a
cookie-jar honouring ``Secure`` would silently drop it and fail every refresh round
trip for the wrong reason. The ``Secure`` attribute itself is asserted directly against
a non-dev ``Settings`` in ``test_auth_flow.py``, so nothing is lost.

Nothing else the tests care about differs between ``dev`` and ``test``:
``is_production`` is False in both, ``dev_echo_otp_enabled`` allows both, and the
flags/provider defaults are identical. The two behavioural differences are covered
another way:

* the refresh cookie is not ``Secure`` in dev (so the ASGI client can carry it over
  ``http://``) — ``test_auth_flow.py`` asserts ``Secure`` directly against a non-dev
  ``Settings`` instead of inferring it from a response;
* HSTS is only emitted in production — ``test_problem_json.py`` asserts that
  conditionality rather than the header.

## Skip vs fail

On a developer machine with nothing running, the datastore-backed tests **skip** with a
message telling you what to start. In CI they **fail**, because a green build that silently
ran zero tenancy tests is worse than a red one: ``CI=true`` (GitHub sets it) or
``GARH_REQUIRE_INTEGRATION=1`` turns every skip into an error. The file-only tests
(`test_catalog_fixtures`, `test_brief_corpus`, `test_no_unscoped_queries`,
`test_config_env`) need neither datastore and always run.

## Isolation

* ``clean_db`` truncates every table before each test — one schema, no per-test database,
  and ``RESTART IDENTITY`` so ``ops.seq`` starts at 1 in every test that looks at it.
* ``clean_redis`` deletes every ``garh:*`` key, so rate-limit windows, refresh families and
  logout-all generations do not leak between tests.
* ``_reset_process_state`` disposes the async engine and both Redis pools **after every
  test**. This is not tidiness: pytest-asyncio gives each test a fresh event loop, and a
  connection pool created on a closed loop is the classic "Event loop is closed" failure
  ten tests later. A new pool per test costs milliseconds against localhost.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Environment — set before anything imports garh_api.config (its settings are cached)
# ---------------------------------------------------------------------------

#: What CI's alembic step runs as, and what this suite deliberately does not. See the
#: docstring; ``test_config_env.py`` is the executable version of this comment.
INTENDED_APP_ENV = "test"


def _force_dev_env() -> str | None:
    """Pin ``APP_ENV=dev``. Returns the value we overrode, for the warning below.

    Deliberately ``environ[...] = ...`` and not ``setdefault``: CI exports
    ``APP_ENV: test``, and honouring it would flip the refresh cookie to ``Secure``
    under the suite's http:// ASGI client (module docstring).
    """
    inherited = os.environ.get("APP_ENV") or os.environ.get("ENV")
    os.environ["APP_ENV"] = "dev"
    os.environ["ENV"] = "dev"
    return inherited if inherited not in (None, "dev") else None


_OVERRODE_APP_ENV = _force_dev_env()

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://garh:garh@localhost:5432/garh")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("PROVIDER_LLM", "mock")
os.environ.setdefault("PROVIDER_RENDER", "mock")
os.environ.setdefault("PROVIDER_BILLING", "mock")
os.environ.setdefault("LOG_FORMAT", "console")
# The OTP echo is what lets the auth tests read a code without a mail provider. It is
# double-gated in garh_api.auth and unreachable outside dev/test.
os.environ.setdefault("DEV_ECHO_OTP", "1")
# Read at import time by garh_api.deps. 1 hop lets a test vary the client IP with
# X-Forwarded-For, which is the only way to exercise a per-IP limit through ASGI.
os.environ.setdefault("TRUSTED_PROXY_HOPS", "1")

import httpx  # noqa: E402
from garh_api import db as db_module  # noqa: E402
from garh_api.config import Settings, get_settings, reset_settings_cache  # noqa: E402
from garh_api.models import ALL_TABLES, Base  # noqa: E402
from sqlalchemy import text  # noqa: E402

#: Truthy here means "a missing Postgres/Redis is a failure, not a skip".
REQUIRE_INTEGRATION = os.environ.get("GARH_REQUIRE_INTEGRATION", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
) or os.environ.get("CI", "").strip().lower() in ("1", "true", "yes", "on")

_TRUNCATE_SQL = "TRUNCATE TABLE %s RESTART IDENTITY CASCADE" % ", ".join(
    '"%s"' % name for name in ALL_TABLES
)


def pytest_configure(config: Any) -> None:
    """Say out loud that we overrode ``APP_ENV``, so the choice stays visible."""
    if _OVERRODE_APP_ENV is None:
        return
    config.issue_config_time_warning(
        pytest.PytestConfigWarning(
            "Overrode APP_ENV=%s with 'dev': the suite's ASGI client runs over "
            "http://, and outside dev the refresh cookie is Secure and would be "
            "dropped by the cookie jar. See the module docstring in tests/conftest.py."
            % (_OVERRODE_APP_ENV,)
        ),
        stacklevel=1,
    )


def _unavailable(what: str, detail: str) -> None:
    """Skip locally, fail in CI. Never silently pass."""
    message = (
        "%s is not available: %s\n"
        "Start it with `docker compose up -d postgres redis`, or point DATABASE_URL / "
        "REDIS_URL at your own." % (what, detail)
    )
    if REQUIRE_INTEGRATION:
        pytest.fail(message, pytrace=False)
    pytest.skip(message, allow_module_level=True)


# ---------------------------------------------------------------------------
# Session-scoped, synchronous: schema + connectivity
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def settings() -> Settings:
    reset_settings_cache()
    return get_settings()


@pytest.fixture(scope="session")
def database(settings: Settings) -> Iterator[Any]:
    """A live Postgres with the schema applied.

    Synchronous on purpose — a session-scoped *async* fixture would be pinned to one
    event loop while every test gets its own. The schema is applied with
    ``metadata.create_all`` when the tables are absent, so the suite runs on a bare
    database; CI applies the real Alembic migration first, which is what actually gets
    tested for correctness (see ``docs/testing.md``).
    """
    engine = db_module.get_sync_engine(settings, pooled=False)
    try:
        with engine.begin() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        _unavailable("Postgres", "%s: %s" % (type(exc).__name__, exc))
    with engine.begin() as conn:
        present = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = current_schema()"
                )
            )
        }
        missing = [name for name in ALL_TABLES if name not in present]
        if missing:
            # gen_random_uuid() lives in pgcrypto; the Alembic migration creates the
            # extension, so create_all needs it too.
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    if missing:
        Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def redis_available(settings: Settings) -> Iterator[Any]:
    """A live Redis. Uses the *synchronous* client so the check is loop-independent."""
    from redis import Redis as SyncRedis

    client = SyncRedis.from_url(settings.redis_url, socket_timeout=2, decode_responses=True)
    try:
        client.ping()
    except Exception as exc:
        _unavailable("Redis", "%s: %s" % (type(exc).__name__, exc))
    yield client
    client.close()


# ---------------------------------------------------------------------------
# Per-test isolation
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_db(database: Any) -> Iterator[None]:
    """Empty every table before the test. Truncate, not drop — the schema is expensive."""
    with database.begin() as conn:
        conn.execute(text(_TRUNCATE_SQL))
    yield


@pytest.fixture
def clean_redis(redis_available: Any) -> Iterator[Any]:
    """Delete every ``garh:*`` key before the test.

    Scoped by prefix rather than ``FLUSHDB`` so pointing ``REDIS_URL`` at a shared server
    cannot wipe something that is not ours.
    """

    def _purge() -> None:
        cursor = 0
        while True:
            cursor, keys = redis_available.scan(cursor=cursor, match="garh:*", count=500)
            if keys:
                redis_available.delete(*keys)
            if cursor == 0:
                break

    _purge()
    yield redis_available
    _purge()


@pytest.fixture(autouse=True)
async def _reset_process_state() -> AsyncIterator[None]:
    """Drop every loop-bound singleton after each test. See the module docstring."""
    yield
    from garh_api import queue as queue_module
    from garh_api import ratelimit as ratelimit_module
    from garh_api.auth import reset_session_scripts

    await ratelimit_module.close_redis()
    await queue_module.close_redis()
    reset_session_scripts()
    await db_module.dispose_async_engine()


@pytest.fixture
async def session(clean_db: None, settings: Settings) -> AsyncIterator[Any]:
    """A plain :class:`AsyncSession` for repository-level tests and test data setup.

    Committing is the caller's job — helpers in ``tests.factories`` commit, because the
    rows have to be visible to the separate session an HTTP request opens.
    """
    factory = db_module.get_sessionmaker(settings)
    async with factory() as async_session:
        yield async_session
        await async_session.rollback()


# ---------------------------------------------------------------------------
# The application
# ---------------------------------------------------------------------------


@pytest.fixture
def app(clean_db: None, clean_redis: Any, settings: Settings) -> Any:
    """A fresh FastAPI app.

    ``create_app`` rather than ``garh_api.main.app`` so each test gets its own handlers and
    middleware stack. The lifespan hook is deliberately **not** run: it starts the job-event
    consumer, which is a background task nothing in this suite needs and which would keep a
    Redis connection open past the end of the test.
    """
    from garh_api.main import create_app

    return create_app(settings)


@pytest.fixture
def app_routes(settings: Settings) -> Any:
    """The app, built without touching Postgres or Redis.

    For tests that only read the route table (the cross-tenant coverage guard). `create_app`
    opens no connection — the lifespan hook does, and it is not run here — so the most
    structurally important test in the suite stays runnable on a laptop with nothing
    installed.
    """
    from garh_api.main import create_app

    return create_app(settings)


@pytest.fixture
async def client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client speaking ASGI directly to the app — no socket, no server."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as http_client:
        yield http_client


@pytest.fixture
def api(settings: Settings) -> str:
    """The ``/api/v1`` prefix, so no test hard-codes it."""
    return settings.api_prefix


# ---------------------------------------------------------------------------
# Two tenants — the fixtures the cross-tenant suite is built on
# ---------------------------------------------------------------------------


@pytest.fixture
async def firm_a(session: Any) -> Any:
    """An admin of firm A ("Studio One"). The tenant whose data the tests own."""
    from tests.factories import create_firm

    return await create_firm(session, firm_name="Studio One", name="Asha Rao")


@pytest.fixture
async def firm_b(session: Any) -> Any:
    """An admin of firm B ("Studio Two"). The attacker in every tenancy test.

    A separate firm created through the same signup path, so there is nothing special
    about it — the isolation being tested is structural, not a property of this fixture.
    """
    from tests.factories import create_firm

    return await create_firm(session, firm_name="Studio Two", name="Meera Iyer")


@pytest.fixture
async def member_a(session: Any, firm_a: Any) -> Any:
    """A non-admin seat inside firm A, for the admin-only route checks."""
    from tests.factories import add_member

    return await add_member(session, firm_a, name="Rahul Verma", role="member")


@pytest.fixture
async def project_a(session: Any, firm_a: Any) -> Any:
    """A project owned by firm A."""
    from tests.factories import create_project

    return await create_project(session, firm_a, name="Sharma Residence")


# ---------------------------------------------------------------------------
# Helpers exposed as fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def unique_email() -> Any:
    """Factory for addresses that cannot collide across tests (``users.email`` is unique)."""

    def _make(prefix: str = "user") -> str:
        return "%s-%s@studio.test" % (prefix, uuid.uuid4().hex[:12])

    return _make
