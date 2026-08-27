"""The guard that ``services/common/config.py``'s docstring promised.

``config.py`` says of :func:`assert_shared_env_names_match`:

    "services/common/tests/test_config.py runs it"

Until this file existed that sentence was false, and the guard was inert — nothing
in the repo called it, so a variable renamed on the API side and not on the worker
side would have shipped with the worker silently keeping its development default.
``extra="ignore"`` on both settings classes makes that failure completely silent,
which is precisely why the assertion needs a caller.

The rest of the module pins the handful of worker settings behaviours other code
already depends on:

* ``PROVIDER_LLM``/``PROVIDER_RENDER`` default to ``mock`` — the locked decision
  that the whole product runs with zero API keys and zero GPUs.
* Queue names match the ones ``garh_api.queue`` LPUSHes to. A mismatch here means
  the API enqueues into a key no worker reads, and jobs sit in ``queued`` forever
  with no error anywhere.
* The lease heartbeat is well under the visibility timeout, or a healthy worker
  loses jobs it is still processing.

No Redis, no Postgres, no network: this suite is pure configuration algebra.
"""

from __future__ import annotations

import pytest

from services.common.config import (
    SHARED_ENV_NAMES,
    WorkerConfigError,
    WorkerSettings,
    assert_shared_env_names_match,
)


def test_shared_env_names_are_all_readable() -> None:
    """The guard config.py's docstring advertises. Must not raise."""
    assert_shared_env_names_match()


def test_shared_env_names_is_not_empty() -> None:
    """A guard over an empty list is a guard that can never fire."""
    assert len(SHARED_ENV_NAMES) >= 5
    assert len(set(SHARED_ENV_NAMES)) == len(SHARED_ENV_NAMES), "duplicate name"
    for name in SHARED_ENV_NAMES:
        assert name == name.upper(), name
        assert " " not in name, name


def test_shared_env_guard_actually_detects_a_missing_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prove the guard fails when a name is unreadable, not just that it passes.

    A green assertion over a list that happens to be satisfiable proves nothing
    about the guard itself; this injects a name no field can read.
    """
    monkeypatch.setattr(
        "services.common.config.SHARED_ENV_NAMES",
        (*tuple(SHARED_ENV_NAMES), "GARH_DEFINITELY_NOT_A_FIELD"),
    )
    with pytest.raises(WorkerConfigError) as excinfo:
        assert_shared_env_names_match()
    assert "GARH_DEFINITELY_NOT_A_FIELD" in str(excinfo.value)


@pytest.fixture()
def pristine_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defaults mean *defaults* — clear anything the ambient shell or CI exported.

    Without this, a runner that happens to export PROVIDER_RENDER or a queue name
    would make these assertions pass or fail for reasons unrelated to the code.
    """
    for name in (
        "PROVIDER_LLM",
        "PROVIDER_RENDER",
        "STABILITY_API_KEY",
        "STABILITY_BASE_URL",
        "QUEUE_SOLVER",
        "QUEUE_RENDER",
        "QUEUE_DRAWINGS",
        "JOB_EVENTS_STREAM",
        "WORKER_HEARTBEAT_INTERVAL_SECONDS",
        "QUEUE_VISIBILITY_TIMEOUT_SECONDS",
        "QUEUE_RETRY_BACKOFF_SECONDS",
        "QUEUE_RETRY_BACKOFF_MAX_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_providers_default_to_mock(pristine_env: None) -> None:
    """SKILL.md locked decision: zero keys, zero GPUs, full product."""
    settings = WorkerSettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.provider_llm == "mock"
    assert settings.provider_render == "mock"


def test_stability_is_a_recognised_render_provider(pristine_env: None) -> None:
    """PROVIDER_RENDER=stability is the hosted no-GPU path; the Literal must admit it."""
    settings = WorkerSettings(_env_file=None, provider_render="stability")  # type: ignore[call-arg]
    assert settings.provider_render == "stability"
    assert settings.stability_api_key == "", "no key by default — the factory enforces it"
    assert settings.stability_base_url.startswith("https://")


def test_stability_key_never_appears_in_the_boot_config_dump(pristine_env: None) -> None:
    """The secret-field treatment ANTHROPIC_API_KEY gets, proven for the new key.

    The second assertion is the one that can actually go red: remove
    ``stability_api_key`` from ``secret_fields`` and the raw value leaks into the
    dump this scans.
    """
    settings = WorkerSettings(  # type: ignore[call-arg]
        _env_file=None, stability_api_key="sk-very-secret-value"
    )
    dump = settings.redacted()
    assert dump["stability_api_key"] == "***"
    assert "sk-very-secret-value" not in str(dump)


def test_queue_names_match_the_api_contract(pristine_env: None) -> None:
    """These exact strings are what ``garh_api.queue`` LPUSHes to.

    Drift here is invisible at boot and fatal at runtime: the API enqueues into a
    key nothing pops, and the job row stays ``queued`` with no error to show.
    """
    settings = WorkerSettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.queue_solver == "garh:queue:solver"
    assert settings.queue_render == "garh:queue:render"
    assert settings.queue_drawings == "garh:queue:drawings"
    assert settings.job_events_stream == "garh:events:jobs"


def test_heartbeat_is_well_inside_the_visibility_timeout(pristine_env: None) -> None:
    """Otherwise a healthy worker's lease expires mid-job and the job is re-run."""
    settings = WorkerSettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.worker_heartbeat_interval_seconds * 3 <= (
        settings.queue_visibility_timeout_seconds
    )


def test_retry_backoff_cap_is_not_below_the_base(pristine_env: None) -> None:
    settings = WorkerSettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.queue_retry_backoff_max_seconds >= settings.queue_retry_backoff_seconds


def test_render_model_allowlist_accepts_the_documented_comma_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """docker-compose.yml and .env.example both pass the allowlist as a comma list.

    pydantic-settings json.loads() env values for complex fields at the SOURCE
    level, before any validator — so without ``NoDecode`` the comma form crashed
    the worker at boot with a JSONDecodeError. The first `docker compose up`
    ever executed (CI run 7, 2026-08-27) found it; every environment before
    that either left the variable unset or never booted a worker at all.
    """
    monkeypatch.setenv("RENDER_MODEL_ALLOWLIST", "vendor/model-a, vendor/model-b")
    settings = WorkerSettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.render_model_allowlist == ("vendor/model-a", "vendor/model-b")


def test_render_model_allowlist_still_accepts_the_json_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RENDER_MODEL_ALLOWLIST", '["vendor/model-c"]')
    settings = WorkerSettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.render_model_allowlist == ("vendor/model-c",)
