"""Worker configuration (playbook §18). 12-factor, everything defaulted for local.

Relationship to ``apps/api/garh_api/config.py``: that file is the authority for every
variable the API reads, and every name shared with it below is spelled identically on
purpose (``REDIS_URL``, ``QUEUE_SOLVER``, ``PROVIDER_RENDER``, ...). Workers need a
*different* slice of the environment — queue mechanics, provider tuning, ML knobs —
and nothing in ``garh_api`` is imported here, so a worker image never has to satisfy
the API's dependency set. :func:`assert_shared_env_names_match` is the guard against
the two drifting; ``services/common/tests/test_config.py`` runs it.

Fail-fast contract, same as the API: a bad value raises at boot with a message that
names the variable, never at first-job time.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["dev", "test", "staging", "prod"]
LlmProvider = Literal["mock", "anthropic"]
RenderProvider = Literal["mock", "diffusers", "stability"]
RenderDevice = Literal["cpu", "cuda"]
LogFormat = Literal["json", "console"]
WorkerName = Literal["solver", "render", "drawings"]

#: §10: "All calls use structured outputs (JSON schema), temperature ≤0.3".
MAX_LLM_TEMPERATURE = 0.3

#: Repo root, resolved from this file: services/common/config.py → ../../
REPO_ROOT = Path(__file__).resolve().parents[2]


class WorkerConfigError(RuntimeError):
    """Configuration is unusable. Raised at boot, never mid-job."""


class WorkerSettings(BaseSettings):
    """Every environment variable a worker process reads."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env", "../../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- environment -------------------------------------------------------
    env: Environment = Field(default="dev", validation_alias="APP_ENV")
    log_level: str = "INFO"
    log_format: LogFormat = "json"
    sentry_dsn: str = ""
    #: Fraction of transactions traced when SENTRY_DSN is set (inert otherwise).
    #: Same default and same env name as the API's field — one knob, both sides.
    sentry_traces_sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)

    # -- identity: which of the three workers is this process? -------------
    #: Set per service in docker-compose.yml. Also the structlog ``service`` field.
    worker_name: str = "worker"
    #: The queue this process consumes. Compose wires it from QUEUE_SOLVER etc.
    worker_queue: str = ""
    #: Jobs handled in parallel by this process.
    worker_concurrency: int = Field(default=2, ge=1, le=64)

    # -- queues (§18) -------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    queue_solver: str = "garh:queue:solver"
    queue_render: str = "garh:queue:render"
    queue_drawings: str = "garh:queue:drawings"

    #: Job lease. A job whose worker died is re-queued after this long.
    queue_visibility_timeout_seconds: int = Field(default=900, ge=10, le=86_400)
    #: Attempts BEYOND the first before dead-lettering.
    queue_max_retries: int = Field(default=3, ge=0, le=10)
    #: Base for exponential backoff: delay = backoff * 2**(attempt-1), capped.
    queue_retry_backoff_seconds: int = Field(default=5, ge=1, le=3_600)
    queue_retry_backoff_max_seconds: int = Field(default=600, ge=1, le=86_400)
    #: Dead-letter list is capped — an unbounded DLQ is a memory leak, not a record.
    queue_dead_letter_maxlen: int = Field(default=1_000, ge=1, le=100_000)
    #: Blocking reserve timeout. Also the shutdown-signal granularity.
    queue_reserve_timeout_seconds: int = Field(default=5, ge=1, le=60)
    #: How often the lease/delayed-set maintenance sweep runs.
    queue_sweep_interval_seconds: int = Field(default=10, ge=1, le=300)

    # -- worker lifecycle ---------------------------------------------------
    #: SIGTERM → stop reserving, let in-flight jobs finish for this long, then
    #: release them back to the queue (they are resumable — see checkpoint.py).
    worker_shutdown_grace_seconds: int = Field(default=30, ge=1, le=600)
    #: Lease renewal cadence; must be well under the visibility timeout.
    worker_heartbeat_interval_seconds: int = Field(default=30, ge=1, le=3_600)
    #: §18: "/healthz per service, worker queue-depth metric".
    worker_health_host: str = "0.0.0.0"
    worker_health_port: int = Field(default=8081, ge=0, le=65_535)

    # -- progress & events --------------------------------------------------
    #: Ring buffer of progress events per job, so an SSE client that connects late
    #: still sees the staged messages (§15 generation theater) instead of a gap.
    progress_log_maxlen: int = Field(default=200, ge=10, le=10_000)
    progress_ttl_seconds: int = Field(default=3_600, ge=60, le=86_400)
    #: Resumable jobs (golden rule 9): checkpoint survives a retry, not forever.
    job_checkpoint_ttl_seconds: int = Field(default=86_400, ge=60)
    #: Redis Stream carrying job lifecycle transitions for the API to persist.
    job_events_stream: str = "garh:events:jobs"
    job_events_maxlen: int = Field(default=10_000, ge=100, le=1_000_000)

    # -- blobs (§13: workers hold no S3 credentials; the API presigns) ------
    blob_http_timeout_seconds: int = Field(default=60, ge=1, le=900)
    blob_max_bytes: int = Field(default=64 * 1024 * 1024, ge=1024)

    # -- LLM provider (§10) -------------------------------------------------
    provider_llm: LlmProvider = "mock"
    anthropic_api_key: str = ""
    # Current-generation default. Kept in lockstep with garh_api.config —
    # the copilot runs in the API process, workers read this copy.
    anthropic_model: str = "claude-opus-5"
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_version: str = "2023-06-01"
    llm_max_output_tokens: int = Field(default=8_192, ge=256, le=200_000)
    llm_temperature: float = Field(default=0.2, ge=0.0, le=MAX_LLM_TEMPERATURE)
    llm_schema_retries: int = Field(default=2, ge=0, le=2)
    llm_timeout_seconds: int = Field(default=60, ge=1, le=900)
    #: Extra/override fixtures for the mock provider. The built-in corpus in
    #: services/llm/fixtures/ always loads first, so the mock works with this unset.
    llm_fixture_dir: str = "fixtures/copilot-commands"

    # -- render provider (§9) -----------------------------------------------
    provider_render: RenderProvider = "mock"
    render_device: RenderDevice = "cpu"
    render_concurrency_per_firm: int = Field(default=4, ge=1, le=64)
    render_model_id: str = "stabilityai/stable-diffusion-xl-base-1.0"
    #: LEGAL GUARD (§9). Weights outside this list are refused at load time.
    #: ``NoDecode``: without it pydantic-settings json.loads() any env value for a
    #: complex field BEFORE the comma-split validator can run — the exact crash the
    #: first `docker compose up` in CI produced, since compose's
    #: RENDER_MODEL_ALLOWLIST is the comma form .env.example documents. The
    #: validator below is the single decoder (comma list or JSON array).
    render_model_allowlist: Annotated[tuple[str, ...], NoDecode] = (
        "stabilityai/stable-diffusion-xl-base-1.0",
        "black-forest-labs/FLUX.1-schnell",
        "Qwen/Qwen-Image",
    )
    render_upscaler: str = "RealESRGAN_x2plus"
    render_output_width: int = Field(default=2_048, ge=256, le=8_192)
    render_output_height: int = Field(default=1_152, ge=256, le=8_192)
    render_safety_checker: bool = True
    render_timeout_seconds: int = Field(default=180, ge=1, le=3_600)

    # -- hosted render API (PROVIDER_RENDER=stability) ----------------------
    #: Required when PROVIDER_RENDER=stability; the factory refuses to build the
    #: provider without it. Secret — masked by :meth:`redacted`, never logged.
    stability_api_key: str = ""
    #: Overridable for a proxy, mirroring ``anthropic_base_url``. The provider posts
    #: to ``/v2beta/stable-image/...`` relative to this.
    stability_base_url: str = "https://api.stability.ai"
    #: Per-HTTP-call budget. Deliberately under ``render_timeout_seconds`` (the job
    #: budget) so a hung upstream fails as a provider error, not a job timeout.
    stability_timeout_seconds: int = Field(default=120, ge=1, le=900)

    # -- solver (§5.2) -------------------------------------------------------
    solver_num_search_workers: int = Field(default=8, ge=1, le=64)
    solver_time_budget_seconds: int = Field(default=15, ge=1, le=600)

    # -- drawings / uploads (§13) -------------------------------------------
    #: The SAME cap the API enforces at the edge — ``Settings.max_dxf_upload_bytes``,
    #: env ``MAX_DXF_UPLOAD_BYTES``, listed in :data:`SHARED_ENV_NAMES`.
    #:
    #: The worker re-checks it rather than trusting the API for two reasons: a blob
    #: can be replaced between enqueue and pop, and the envelope's ``sizeBytes`` is a
    #: claim, not a measurement. It must be the same NUMBER on both sides, though —
    #: ``services/drawings/handler.py`` used to hard-code ``20 * 1024 * 1024`` here,
    #: so raising ``MAX_DXF_UPLOAD_BYTES`` made the API accept a file the worker then
    #: refused, and the architect saw a failed job instead of a 413.
    max_dxf_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)
    dxf_parse_timeout_seconds: int = Field(default=10, ge=1, le=120)
    dxf_parse_memory_limit_mb: int = Field(default=512, ge=64, le=8_192)

    # ------------------------------------------------------------------
    # validators
    # ------------------------------------------------------------------
    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: Any) -> Any:
        if isinstance(value, str):
            level = value.strip().upper()
            allowed = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET")
            if level not in allowed:
                raise ValueError("LOG_LEVEL must be one of %s" % (", ".join(allowed),))
            return level
        return value

    @field_validator("render_model_allowlist", mode="before")
    @classmethod
    def _split_allowlist(cls, value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                # Someone fed the JSON form pydantic-settings used to demand;
                # honour it rather than splitting a JSON array on commas.
                import json

                return tuple(json.loads(text))
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("llm_temperature")
    @classmethod
    def _cap_temperature(cls, value: float) -> float:
        # Belt and braces: the Field bound already rejects >0.3, but this is the
        # §10 rule stated in one obvious place with the reason attached.
        if value > MAX_LLM_TEMPERATURE:
            raise ValueError(
                "LLM_TEMPERATURE must be <= %s (playbook §10: structured outputs need "
                "determinism, not creativity)." % MAX_LLM_TEMPERATURE
            )
        return value

    # ------------------------------------------------------------------
    # convenience
    # ------------------------------------------------------------------
    @property
    def is_dev(self) -> bool:
        return self.env == "dev"

    def queue_for(self, worker: str) -> str:
        """Default queue name for a worker, used when ``WORKER_QUEUE`` is unset."""
        table = {
            "solver": self.queue_solver,
            "render": self.queue_render,
            "drawings": self.queue_drawings,
        }
        try:
            return table[worker]
        except KeyError:
            raise WorkerConfigError(
                "Unknown worker %r. Expected one of %s, or set WORKER_QUEUE explicitly."
                % (worker, ", ".join(sorted(table)))
            ) from None

    def resolve_queue(self, worker: str) -> str:
        """``WORKER_QUEUE`` if set, else the per-worker default."""
        return self.worker_queue.strip() or self.queue_for(worker)

    def fixture_dir(self) -> Path:
        """Absolute path of ``LLM_FIXTURE_DIR`` (relative paths resolve to repo root)."""
        raw = Path(self.llm_fixture_dir)
        return raw if raw.is_absolute() else (REPO_ROOT / raw)

    def retry_delay_seconds(self, attempt: int) -> int:
        """Exponential backoff for ``attempt`` (1 = the first retry), capped.

        5s, 10s, 20s, 40s ... — deliberately int seconds so a log line and a test
        assertion can both state the exact number.
        """
        if attempt < 1:
            return 0
        shift = min(attempt - 1, 16)
        delay = self.queue_retry_backoff_seconds * (2**shift)
        return int(min(delay, self.queue_retry_backoff_max_seconds))

    def redacted(self) -> dict[str, Any]:
        """Boot-time config dump with secrets masked."""
        secret_fields = {"anthropic_api_key", "sentry_dsn", "stability_api_key"}
        out: dict[str, Any] = {}
        for name, value in self.model_dump().items():
            if name in secret_fields:
                out[name] = "***" if value else ""
            elif name == "redis_url":
                out[name] = _redact_url_password(str(value))
            else:
                out[name] = value
        return out


def _redact_url_password(url: str) -> str:
    if "://" not in url:
        return url
    scheme, _, rest = url.partition("://")
    if "@" not in rest:
        return url
    creds, _, host = rest.partition("@")
    user, sep, _password = creds.partition(":")
    if not sep:
        return url
    return "%s://%s:***@%s" % (scheme, user, host)


#: Variables that BOTH the API and the workers read. Spelled here so a test can prove
#: the two config modules agree; drift is silent otherwise (pydantic-settings uses
#: ``extra="ignore"``, so a renamed variable just keeps its default).
SHARED_ENV_NAMES: tuple[str, ...] = (
    "APP_ENV",
    "LOG_LEVEL",
    "LOG_FORMAT",
    "SENTRY_DSN",
    "SENTRY_TRACES_SAMPLE_RATE",
    "REDIS_URL",
    "QUEUE_SOLVER",
    "QUEUE_RENDER",
    "QUEUE_DRAWINGS",
    "PROVIDER_LLM",
    "PROVIDER_RENDER",
    "RENDER_DEVICE",
    "RENDER_CONCURRENCY_PER_FIRM",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    # §13 upload cap. Enforced twice on purpose (API edge + worker), which only
    # works if both sides read the same variable — see max_dxf_upload_bytes.
    "MAX_DXF_UPLOAD_BYTES",
)


def assert_shared_env_names_match() -> None:
    """Prove every name in :data:`SHARED_ENV_NAMES` is readable by this settings class.

    Raises :class:`WorkerConfigError` naming the offenders. Cheap insurance against
    the "renamed one side, the other silently kept its default" bug class.
    """
    fields = WorkerSettings.model_fields
    known: set[str] = set()
    for name, field in fields.items():
        known.add(name.upper())
        alias = field.validation_alias
        if isinstance(alias, str):
            known.add(alias.upper())
    missing = [name for name in SHARED_ENV_NAMES if name not in known]
    if missing:
        raise WorkerConfigError(
            "WorkerSettings cannot read %d variable(s) the API also uses: %s. "
            "Add the field (or a validation_alias) so the two stay in lockstep."
            % (len(missing), ", ".join(missing))
        )


@functools.lru_cache(maxsize=1)
def get_worker_settings() -> WorkerSettings:
    """Cached settings singleton."""
    return WorkerSettings()


def reset_worker_settings_cache() -> None:
    """Test helper: drop the cached singleton so monkeypatched env vars take effect."""
    get_worker_settings.cache_clear()


__all__ = [
    "MAX_LLM_TEMPERATURE",
    "REPO_ROOT",
    "SHARED_ENV_NAMES",
    "Environment",
    "LlmProvider",
    "LogFormat",
    "RenderDevice",
    "RenderProvider",
    "WorkerConfigError",
    "WorkerName",
    "WorkerSettings",
    "assert_shared_env_names_match",
    "get_worker_settings",
    "reset_worker_settings_cache",
]
