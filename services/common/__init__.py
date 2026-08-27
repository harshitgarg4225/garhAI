"""Shared worker runtime: queue transport, progress, config, logging, health (§18).

The public surface a worker package needs is re-exported here::

    from services.common import BaseJobHandler, JobContext, JobResult, run_worker

Everything else (``queue``, ``progress``, ``blobs``, ``checkpoint``) is importable by
module when a handler needs the detail.
"""

from __future__ import annotations

from services.common.blobs import BlobClient
from services.common.checkpoint import JobCheckpoint, NullCheckpoint, inputs_hash
from services.common.config import (
    WorkerConfigError,
    WorkerSettings,
    get_worker_settings,
    reset_worker_settings_cache,
)
from services.common.envelope import (
    ENVELOPE_SCHEMA_VERSION,
    JOB_KINDS,
    JOB_KINDS_BY_WORKER,
    BlobRef,
    JobEnvelope,
    new_job_id,
    now_ms,
)
from services.common.errors import (
    BlobError,
    InvalidJobError,
    JobCancelledError,
    JobTimeoutError,
    LicenseError,
    PermanentError,
    ProviderError,
    RetryableError,
    WorkerError,
    is_retryable,
    user_facing,
)
from services.common.jobstore import JobResult, JobStatusSink, NullJobStatusSink
from services.common.jsonschema_lite import (
    SchemaError,
    SchemaValidator,
    UnsupportedSchemaError,
    ValidationFailure,
    format_errors,
)
from services.common.logging import (
    bind_job_context,
    clear_job_context,
    configure_worker_logging,
    get_logger,
)
from services.common.metrics import WorkerMetrics
from services.common.progress import NullProgressReporter, ProgressEvent, ProgressReporter
from services.common.queue import QueueDepth, RedisJobQueue, RedisLike, Reservation, connect
from services.common.runtime import BaseJobHandler, JobContext, JobHandler, Worker, run_worker

__all__ = [
    "ENVELOPE_SCHEMA_VERSION",
    "JOB_KINDS",
    "JOB_KINDS_BY_WORKER",
    "BaseJobHandler",
    "BlobClient",
    "BlobError",
    "BlobRef",
    "InvalidJobError",
    "JobCancelledError",
    "JobCheckpoint",
    "JobContext",
    "JobEnvelope",
    "JobHandler",
    "JobResult",
    "JobStatusSink",
    "JobTimeoutError",
    "LicenseError",
    "NullCheckpoint",
    "NullJobStatusSink",
    "NullProgressReporter",
    "PermanentError",
    "ProgressEvent",
    "ProgressReporter",
    "ProviderError",
    "QueueDepth",
    "RedisJobQueue",
    "RedisLike",
    "Reservation",
    "RetryableError",
    "SchemaError",
    "SchemaValidator",
    "UnsupportedSchemaError",
    "ValidationFailure",
    "Worker",
    "run_worker",
    "WorkerConfigError",
    "WorkerError",
    "WorkerMetrics",
    "WorkerSettings",
    "bind_job_context",
    "clear_job_context",
    "configure_worker_logging",
    "connect",
    "format_errors",
    "get_logger",
    "get_worker_settings",
    "inputs_hash",
    "is_retryable",
    "new_job_id",
    "now_ms",
    "reset_worker_settings_cache",
    "user_facing",
]
