"""Configuration invariants — no datastore needed.

Two jobs:

1. pin the ``APP_ENV=test`` defect that forces :mod:`tests.conftest` to run the suite as
   ``dev`` (see that module's docstring). It is a strict xfail: the day ``config.py``
   exempts ``test`` from the production-secret validator, this test **passes**, pytest
   reports XPASS as a failure, and whoever fixed it is told to delete both the marker and
   the ``APP_ENV`` override.
2. assert the numbers §13 and §11 specify are actually the numbers in ``Settings``, so a
   "harmless" default change cannot quietly widen a security window.

## Why every case goes through the environment

``Settings`` is a ``BaseSettings``, so an init keyword is only honoured when it matches the
field's **validation alias**. ``env`` answers to ``ENV``/``APP_ENV``, the ``S3_*`` fields
answer to their §18 names, and everything else answers to its snake_case field name — so
``Settings(DATABASE_URL=...)`` is silently *dropped* (``extra="ignore"``) and the value comes
from the real environment instead. That is a quiet way to write a test that passes for the
wrong reason, especially in CI, which exports a real ``JWT_PRIVATE_KEY``.

:func:`_settings_from` therefore clears every variable the validator reads and sets exactly
the ones the case is about, with ``_env_file=None`` so the developer's ``.env`` cannot join
in either.
"""

from __future__ import annotations

from typing import Any

import pytest

from garh_api.config import (
    DEV_APP_URL,
    DEV_DATABASE_URL,
    DEV_REDIS_URL,
    DEV_S3_ACCESS_KEY_ID,
    DEV_S3_SECRET_ACCESS_KEY,
    ConfigError,
    Settings,
)

#: Every variable ``_fail_fast_on_missing_secrets`` looks at, plus the env selector. Cleared
#: before each case so an inherited value cannot make an assertion pass by accident.
VALIDATED_ENV_KEYS: tuple[str, ...] = (
    "ENV",
    "APP_ENV",
    "JWT_PRIVATE_KEY",
    "JWT_PUBLIC_KEY",
    "DATABASE_URL",
    "REDIS_URL",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "APP_URL",
    "PROVIDER_LLM",
    "ANTHROPIC_API_KEY",
    "PROVIDER_BILLING",
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
)


def _settings_from(monkeypatch: Any, **env: str) -> Settings:
    """Build a ``Settings`` from exactly ``env`` and nothing else."""
    for key in VALIDATED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


#: A complete, valid production configuration. Individual cases remove one value from it.
PRODUCTION_ENV: dict[str, str] = {
    "APP_ENV": "prod",
    "JWT_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----",
    "JWT_PUBLIC_KEY": "-----BEGIN PUBLIC KEY-----\nnot-a-real-key\n-----END PUBLIC KEY-----",
    "DATABASE_URL": "postgresql+psycopg://u:p@db.internal:5432/garh",
    "REDIS_URL": "redis://cache.internal:6379/0",
    "S3_ACCESS_KEY_ID": "live-key",
    "S3_SECRET_ACCESS_KEY": "live-secret",
    "APP_URL": "https://app.garh.ai",
}


# ---------------------------------------------------------------------------
# The defect that shapes the whole suite
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    raises=ConfigError,
    reason=(
        "config.py's _fail_fast_on_missing_secrets runs for every env except 'dev', so "
        "APP_ENV=test demands real S3 credentials, a public APP_URL and a non-local "
        "DATABASE_URL/REDIS_URL. A test environment cannot satisfy that — it is by "
        "definition pointed at the local stack. Fix: guard the validator with "
        '`if self.env in ("dev", "test")`. When that lands, remove this marker AND '
        "the APP_ENV override in tests/conftest.py."
    ),
)
def test_test_env_can_point_at_the_local_stack(monkeypatch: Any) -> None:
    """``APP_ENV=test`` against compose's datastores must boot.

    This is what CI's ``unit-python`` job actually configures (``APP_ENV=test``,
    ``REDIS_URL=redis://localhost:6379/0``, no ``S3_*``, no ``APP_URL``), so as long as this
    fails, that job cannot start the app either.
    """
    settings = _settings_from(
        monkeypatch,
        APP_ENV="test",
        DATABASE_URL=DEV_DATABASE_URL,
        REDIS_URL=DEV_REDIS_URL,
    )
    assert settings.env == "test"


def test_dev_env_boots_with_no_configuration(monkeypatch: Any) -> None:
    """The counterpart: dev must need nothing at all (§18 "all defaulted for local")."""
    settings = _settings_from(monkeypatch, APP_ENV="dev")
    assert settings.is_dev
    assert not settings.is_production
    assert settings.provider_llm == "mock", "the app must run with no API keys"
    assert settings.provider_render == "mock", "and with no GPU"
    assert settings.database_url == DEV_DATABASE_URL
    assert settings.api_prefix == "/api/v1"


def test_a_complete_production_config_boots(monkeypatch: Any) -> None:
    """The positive control: without this, the cases below could pass for any reason."""
    settings = _settings_from(monkeypatch, **PRODUCTION_ENV)
    assert settings.is_production
    assert settings.jwt_keys_configured


@pytest.mark.parametrize(
    ("dropped", "replacement"),
    [
        ("JWT_PRIVATE_KEY", ""),
        ("JWT_PUBLIC_KEY", ""),
        ("DATABASE_URL", DEV_DATABASE_URL),
        ("REDIS_URL", DEV_REDIS_URL),
        ("S3_ACCESS_KEY_ID", DEV_S3_ACCESS_KEY_ID),
        ("S3_SECRET_ACCESS_KEY", DEV_S3_SECRET_ACCESS_KEY),
        ("APP_URL", DEV_APP_URL),
    ],
)
def test_production_refuses_each_missing_or_local_value(
    monkeypatch: Any, dropped: str, replacement: str
) -> None:
    """§13 secrets hygiene: a prod boot on a dev value must fail, and name the variable.

    One case per value, because a single "everything missing" test passes as soon as *any*
    one of them is checked — which is how a config validator quietly stops covering five of
    the seven things it claims to.
    """
    env = {**PRODUCTION_ENV, dropped: replacement}
    with pytest.raises(ConfigError) as excinfo:
        _settings_from(monkeypatch, **env)
    assert dropped in str(excinfo.value), str(excinfo.value)


def test_anthropic_key_is_required_only_when_selected(monkeypatch: Any) -> None:
    """Provider interfaces are mockable by design (locked decision); keys follow the choice."""
    ok = _settings_from(monkeypatch, **PRODUCTION_ENV, PROVIDER_LLM="mock")
    assert ok.provider_llm == "mock"

    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        _settings_from(monkeypatch, **PRODUCTION_ENV, PROVIDER_LLM="anthropic")


def test_razorpay_keys_are_required_only_when_selected(monkeypatch: Any) -> None:
    with pytest.raises(ConfigError, match="RAZORPAY_KEY_ID"):
        _settings_from(monkeypatch, **PRODUCTION_ENV, PROVIDER_BILLING="razorpay")


# ---------------------------------------------------------------------------
# The playbook's numbers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "expected", "clause"),
    [
        ("otp_ttl_seconds", 600, "§13: OTP expires in 10 minutes"),
        ("otp_max_attempts", 5, "§13: 5 attempts per code"),
        ("access_token_ttl_seconds", 900, "§11: 15-minute access token"),
        ("share_token_bytes", 32, "§13: 256-bit share token"),
        ("s3_signed_url_ttl_seconds", 600, "§13: signed URLs <= 10 min"),
        ("rate_limit_ops_per_second", 60, "§11: 60 ops/s per firm"),
        ("rate_limit_solver_jobs_per_hour", 10, "§11: 10 solver jobs/hr per firm"),
        ("render_concurrency_per_firm", 4, "§9: 4 concurrent renders per firm"),
        ("max_dxf_upload_bytes", 20 * 1024 * 1024, "§13: DXF <= 20MB"),
        ("max_image_upload_bytes", 10 * 1024 * 1024, "§13: images <= 10MB"),
        ("max_request_body_bytes", 8 * 1024 * 1024, "§13: ceiling on any request body"),
        ("op_snapshot_interval", 200, "§4: snapshot every 200 ops"),
        ("jwt_algorithm", "RS256", "§13: JWT RS256"),
    ],
)
def test_playbook_numbers_are_the_defaults(
    monkeypatch: Any, field: str, expected: object, clause: str
) -> None:
    settings = _settings_from(monkeypatch, APP_ENV="dev")
    assert getattr(settings, field) == expected, clause


def test_refresh_cookie_is_secure_outside_dev(monkeypatch: Any) -> None:
    """§13 "SameSite=Lax cookies for refresh" — and Secure everywhere but local http.

    Asserted against a non-dev ``Settings`` rather than a live response, because the suite
    runs as dev (conftest) where ``Secure`` is deliberately off so a plain-http test client
    can carry the cookie.
    """
    from garh_api.security import refresh_cookie_path, refresh_cookie_secure

    dev = _settings_from(monkeypatch, APP_ENV="dev")
    assert refresh_cookie_secure(dev) is False, "dev over plain http, for Safari's sake"

    staging = _settings_from(monkeypatch, **{**PRODUCTION_ENV, "APP_ENV": "staging"})
    assert refresh_cookie_secure(staging) is True
    # Path-scoped to the auth routes: the token is attached to nothing else.
    assert refresh_cookie_path(staging) == "/api/v1/auth"


def test_redacted_config_hides_every_secret(monkeypatch: Any) -> None:
    """§13: "secrets: env only, never in the client bundle" — nor in a boot log line."""
    settings = _settings_from(monkeypatch, **PRODUCTION_ENV)
    dump = settings.redacted()

    for field in ("jwt_private_key", "jwt_public_key", "s3_access_key_id", "s3_secret_access_key"):
        assert dump[field] == "***", field
    assert "p@db.internal" not in dump["database_url"], dump["database_url"]
    assert ":***@" in dump["database_url"]

    blob = repr(dump)
    assert "not-a-real-key" not in blob
    assert "live-secret" not in blob
