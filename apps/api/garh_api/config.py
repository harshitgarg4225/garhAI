"""Typed application configuration (engineering playbook §18).

12-factor: everything comes from the environment, everything has a local default so
``docker compose up`` works with an empty ``.env`` and zero API keys / zero GPUs.

Fail-fast contract: in any non-``dev`` environment, missing secrets raise
:class:`ConfigError` at import/boot time with a message that names every missing
variable and what to set it to. Never at first-request time.

Usage::

    from garh_api.config import get_settings
    settings = get_settings()          # cached singleton
"""

from __future__ import annotations

import functools
from typing import Any, Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "test", "staging", "prod"]
LlmProvider = Literal["mock", "anthropic"]
# "stability" is served by the render worker, not this process, but the value
# must validate here too: PROVIDER_RENDER is a shared env name, and a
# project-wide setting must not brick the API at boot.
RenderProvider = Literal["mock", "diffusers", "stability"]
BillingProvider = Literal["mock", "razorpay"]
RenderDevice = Literal["cpu", "cuda"]
LogFormat = Literal["json", "console"]

#: Local development defaults. Kept as constants so the fail-fast validator can
#: detect "still on the local default in production" and refuse to boot.
DEV_DATABASE_URL = "postgresql+psycopg://garh:garh@localhost:5432/garh"
DEV_REDIS_URL = "redis://localhost:6379/0"
DEV_S3_ENDPOINT_URL = "http://localhost:9000"
DEV_S3_ACCESS_KEY_ID = "garh-minio"
DEV_S3_SECRET_ACCESS_KEY = "garh-minio-secret"
DEV_APP_URL = "http://localhost:5173"


class ConfigError(RuntimeError):
    """Configuration is unusable. Raised at boot, never mid-request."""


class Settings(BaseSettings):
    """All of §18's environment surface, typed.

    Naming: the env var is the UPPER_SNAKE of the field name unless an alias says
    otherwise (``S3_*`` fields keep their §18 names).
    """

    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- environment ------------------------------------------------------
    env: Environment = Field(default="dev", validation_alias=AliasChoices("ENV", "APP_ENV"))
    app_name: str = "garh-api"
    app_url: str = DEV_APP_URL
    api_prefix: str = "/api/v1"
    debug: bool = False

    # -- datastores -------------------------------------------------------
    database_url: str = DEV_DATABASE_URL
    redis_url: str = DEV_REDIS_URL
    db_pool_size: int = Field(default=10, ge=1, le=200)
    db_max_overflow: int = Field(default=10, ge=0, le=200)
    db_pool_timeout_seconds: int = Field(default=30, ge=1, le=300)
    db_pool_recycle_seconds: int = Field(default=1800, ge=60)
    db_statement_timeout_ms: int = Field(default=15_000, ge=0)
    sql_echo: bool = False

    # -- auth (§13: email OTP, JWT RS256, refresh rotation) ---------------
    jwt_private_key: str = ""
    jwt_public_key: str = ""
    jwt_algorithm: str = "RS256"
    jwt_issuer: str = "garh-ai"
    access_token_ttl_seconds: int = Field(default=900, ge=60)  # 15 min (§11)
    refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 30, ge=3600)
    otp_ttl_seconds: int = Field(default=600, ge=60)  # 10 min (§13)
    otp_max_attempts: int = Field(default=5, ge=1, le=10)  # 5 attempts (§13)
    otp_code_length: int = Field(default=6, ge=4, le=10)
    share_token_bytes: int = Field(default=32, ge=32)  # 256-bit (§13)

    # -- transactional mail (SMTP; delivers the §13 OTP sign-in codes) -----
    #: Empty host = mail off. The dev echo is then the only delivery channel, and a
    #: non-dev OTP request fails loudly instead of pretending a mail was sent — see
    #: ``garh_api.auth._deliver_code`` and ``garh_api.mailer``.
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)  # 587 = submission, STARTTLS
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""  # sender address, e.g. no-reply@garh.ai
    smtp_starttls: bool = True

    # -- object storage (minio in compose) --------------------------------
    s3_endpoint_url: str = Field(
        default=DEV_S3_ENDPOINT_URL, validation_alias=AliasChoices("S3_ENDPOINT_URL")
    )
    s3_region: str = Field(default="ap-south-1", validation_alias=AliasChoices("S3_REGION"))
    s3_bucket: str = Field(default="garh-dev", validation_alias=AliasChoices("S3_BUCKET"))
    s3_access_key_id: str = Field(
        default=DEV_S3_ACCESS_KEY_ID, validation_alias=AliasChoices("S3_ACCESS_KEY_ID")
    )
    s3_secret_access_key: str = Field(
        default=DEV_S3_SECRET_ACCESS_KEY, validation_alias=AliasChoices("S3_SECRET_ACCESS_KEY")
    )
    s3_force_path_style: bool = True
    #: §13: signed download URLs ≤10 min.
    s3_signed_url_ttl_seconds: int = Field(default=600, ge=30, le=600)

    # -- providers (all mockable: the app runs with no keys, no GPUs) ------
    provider_llm: LlmProvider = "mock"
    provider_render: RenderProvider = "mock"
    provider_billing: BillingProvider = "mock"
    render_device: RenderDevice = "cpu"
    anthropic_api_key: str = ""
    # Current-generation default. The provider degrades gracefully on parameter
    # drift (services/llm/anthropic_provider.py), but the default should not
    # trail the model family the prompts were written against.
    anthropic_model: str = "claude-opus-5"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""

    # -- queues & limits (§13 rate limits, §9 concurrency) ----------------
    queue_solver: str = "garh:queue:solver"
    queue_render: str = "garh:queue:render"
    queue_drawings: str = "garh:queue:drawings"
    rate_limit_ops_per_second: int = Field(default=60, ge=1)
    rate_limit_solver_jobs_per_hour: int = Field(default=10, ge=1)
    rate_limit_auth_per_hour: int = Field(default=20, ge=1)
    #: §13 rate limits, applied to the LLM routes (``POST /projects/:id/brief/parse``
    #: today, the copilot in Phase 6). These are the only endpoints that spend money at
    #: a third party per request, so an authenticated user with a loop is a billing
    #: incident rather than a load problem. Generous for a human filling in a brief —
    #: the form has one "parse" button — and cheap to exhaust for a script.
    rate_limit_llm_per_hour: int = Field(default=60, ge=1)
    render_concurrency_per_firm: int = Field(default=4, ge=1)
    max_dxf_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)
    max_image_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    #: §13: ceiling on ANY request body this API will read, upload routes included.
    #: Every current endpoint takes JSON; the largest legitimate body is an op
    #: append (``max_ops_per_append`` ops, each a small object), which is orders of
    #: magnitude under this. It exists so an unauthenticated caller cannot make the
    #: process buffer an arbitrary number of bytes before a single validator runs.
    #: A route that genuinely needs more (Phase 2's DXF import, ≤20MB) must be
    #: listed in ``garh_api.main.LARGE_BODY_PATH_SUFFIXES`` and enforce its own
    #: per-format limit — see ``max_dxf_upload_bytes``.
    max_request_body_bytes: int = Field(default=8 * 1024 * 1024, ge=64 * 1024)

    # -- model core -------------------------------------------------------
    #: §2: fold a snapshot into design_versions every N ops.
    op_snapshot_interval: int = Field(default=200, ge=1)
    #: §11: how many ops a single append call may carry.
    max_ops_per_append: int = Field(default=500, ge=1)

    # -- observability (§18) ----------------------------------------------
    log_level: str = "INFO"
    log_format: LogFormat = "json"
    sentry_dsn: str = ""
    #: Fraction of requests traced when SENTRY_DSN is set (inert otherwise —
    #: error tracking is OFF by default, like every provider). Small on purpose:
    #: 10% is enough to see p95 latency shape without paying quota for every
    #: request. Override via SENTRY_TRACES_SAMPLE_RATE.
    sentry_traces_sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)

    # -- web --------------------------------------------------------------
    cors_allow_origins: tuple[str, ...] = ("http://localhost:5173",)

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

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("jwt_private_key", "jwt_public_key", mode="before")
    @classmethod
    def _normalise_pem(cls, value: Any) -> Any:
        """Accept PEM keys with literal ``\\n`` escapes (common in .env files)."""
        if isinstance(value, str) and "\\n" in value:
            return value.replace("\\n", "\n")
        return value

    @model_validator(mode="after")
    def _fail_fast_on_missing_secrets(self) -> Settings:
        # "test" is exempt alongside "dev": a test environment is by definition
        # pointed at a local stack (CI's service containers, a dev box's
        # postgres), so demanding non-local URLs and real S3 credentials made
        # APP_ENV=test unusable — the suite ran as "dev" and CI's alembic step
        # died at import. Staging and prod keep the full check.
        if self.env in ("dev", "test"):
            return self
        missing: list[str] = []

        def need(present: bool, var: str, hint: str) -> None:
            if not present:
                missing.append("%s — %s" % (var, hint))

        need(bool(self.jwt_private_key), "JWT_PRIVATE_KEY", "RS256 private key (PEM)")
        need(bool(self.jwt_public_key), "JWT_PUBLIC_KEY", "RS256 public key (PEM)")
        need(
            self.database_url != DEV_DATABASE_URL,
            "DATABASE_URL",
            "still pointing at the local dev database",
        )
        need(self.redis_url != DEV_REDIS_URL, "REDIS_URL", "still pointing at local redis")
        need(
            self.s3_access_key_id not in ("", DEV_S3_ACCESS_KEY_ID),
            "S3_ACCESS_KEY_ID",
            "still the local minio credential",
        )
        need(
            self.s3_secret_access_key not in ("", DEV_S3_SECRET_ACCESS_KEY),
            "S3_SECRET_ACCESS_KEY",
            "still the local minio credential",
        )
        need(self.app_url != DEV_APP_URL, "APP_URL", "public URL of the web app")
        if self.provider_llm == "anthropic":
            need(
                bool(self.anthropic_api_key),
                "ANTHROPIC_API_KEY",
                "required because PROVIDER_LLM=anthropic",
            )
        if self.provider_billing == "razorpay":
            need(
                bool(self.razorpay_key_id),
                "RAZORPAY_KEY_ID",
                "required because PROVIDER_BILLING=razorpay",
            )
            need(
                bool(self.razorpay_key_secret),
                "RAZORPAY_KEY_SECRET",
                "required because PROVIDER_BILLING=razorpay",
            )

        if missing:
            raise ConfigError(
                "Refusing to start in ENV=%s: %d configuration value(s) missing or still on a "
                "local default.\n  - %s\nSet them in the environment (never in the client "
                "bundle) and restart." % (self.env, len(missing), "\n  - ".join(missing))
            )
        return self

    # ------------------------------------------------------------------
    # convenience
    # ------------------------------------------------------------------
    @property
    def is_dev(self) -> bool:
        return self.env == "dev"

    @property
    def is_test(self) -> bool:
        return self.env == "test"

    @property
    def is_production(self) -> bool:
        return self.env in ("staging", "prod")

    @property
    def jwt_keys_configured(self) -> bool:
        """False in dev when no keypair was supplied.

        The auth layer is expected to mint an ephemeral in-process dev keypair in that
        case (and log a warning). Non-dev boots can never reach here — the validator
        above refuses to start.
        """
        return bool(self.jwt_private_key and self.jwt_public_key)

    @property
    def smtp_configured(self) -> bool:
        """True when real mail can go out: a relay (SMTP_HOST) and a sender (SMTP_FROM).

        ``SMTP_USER``/``SMTP_PASSWORD`` are deliberately not part of the gate — an
        IP-allowlisted internal relay legitimately needs neither.
        """
        return bool(self.smtp_host and self.smtp_from)

    @property
    def sentry_enabled(self) -> bool:
        return bool(self.sentry_dsn)

    def redacted(self) -> dict[str, Any]:
        """Config dump safe to log at boot: secrets replaced with ``***``."""
        secret_fields = {
            "jwt_private_key",
            "jwt_public_key",
            "anthropic_api_key",
            "razorpay_key_id",
            "razorpay_key_secret",
            "s3_secret_access_key",
            "s3_access_key_id",
            "sentry_dsn",
            "smtp_password",
        }
        out: dict[str, Any] = {}
        for name, value in self.model_dump().items():
            if name in secret_fields:
                out[name] = "***" if value else ""
            elif name in ("database_url", "redis_url"):
                out[name] = _redact_url_password(str(value))
            else:
                out[name] = value
        return out


def _redact_url_password(url: str) -> str:
    """``postgresql://u:p@h/db`` → ``postgresql://u:***@h/db``."""
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


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton. Raises :class:`ConfigError` on a bad non-dev env."""
    return Settings()


def reset_settings_cache() -> None:
    """Test helper: drop the cached singleton so monkeypatched env vars take effect."""
    get_settings.cache_clear()


__all__ = [
    "BillingProvider",
    "ConfigError",
    "Environment",
    "LlmProvider",
    "LogFormat",
    "RenderDevice",
    "RenderProvider",
    "Settings",
    "get_settings",
    "reset_settings_cache",
]
