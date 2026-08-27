"""Worker error taxonomy.

Golden rule 9: "Errors say what to do next. No raw exceptions to the UI." Every
failure a worker records therefore carries three things:

``code``
    machine-readable, stable, used by the client to pick an icon / next action.
``message``
    one sentence of plain user-facing copy. Never a traceback, never a stack frame,
    never the word "exception". It goes straight into ``solver_jobs.error`` /
    ``render_jobs.error``, which the UI shows verbatim.
``action``
    the one thing the user can do about it, or ``None`` when it is genuinely on us.

The retryable/permanent split drives the queue: :class:`RetryableError` goes back on
the queue with exponential backoff, :class:`PermanentError` fails the job on the first
attempt (retrying a malformed payload three times just wastes 40 seconds of the user's
patience). An *unexpected* exception is treated as retryable — the optimistic reading
is the safe one for transient infrastructure — but its detail is logged, never shown.
"""

from __future__ import annotations

from typing import Any


class WorkerError(Exception):
    """Base class: an error with user-facing copy attached."""

    #: Stable machine code. Overridden per subclass.
    code = "worker_error"
    #: Whether the runtime should retry this job.
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        action: str | None = None,
        code: str | None = None,
        detail: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.action = action
        if code is not None:
            self.code = code
        #: Operator-facing detail. Logged, never returned to the client.
        self.detail = detail
        self.context: dict[str, Any] = dict(context or {})

    def as_problem(self) -> dict[str, Any]:
        """problem+json shaped body (§11 convention ``{code, message, action}``)."""
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.action:
            body["action"] = self.action
        if self.context:
            body["context"] = dict(self.context)
        return body

    def __str__(self) -> str:
        return self.message


class RetryableError(WorkerError):
    """Transient: object storage hiccup, provider 503, Redis blip. Try again."""

    code = "worker_retryable"
    retryable = True


class PermanentError(WorkerError):
    """Deterministic: this job will fail identically every time. Fail it now."""

    code = "worker_permanent"
    retryable = False


class InvalidJobError(PermanentError):
    """The envelope is malformed or missing required inputs."""

    code = "invalid_job"


class JobCancelledError(WorkerError):
    """The job was cancelled while running. Not a failure — a user choice."""

    code = "job_cancelled"
    retryable = False

    def __init__(self, message: str = "This job was cancelled.") -> None:
        super().__init__(message, action=None)


class JobTimeoutError(RetryableError):
    """The handler blew its time budget (§14)."""

    code = "job_timeout"


class ProviderError(WorkerError):
    """An external provider (LLM/render) failed. Retryability is decided per case."""

    code = "provider_error"

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        retryable: bool = True,
        action: str | None = None,
        code: str | None = None,
        detail: str | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(
            message,
            action=action,
            code=code,
            detail=detail,
            context={"provider": provider} if provider else None,
        )
        self.provider = provider
        self.retryable = retryable
        self.status = status


class BlobError(RetryableError):
    """Fetching or storing a job asset failed."""

    code = "blob_error"


class LicenseError(PermanentError):
    """A model/weights licence guard refused the request (§9).

    Never retryable, never downgradeable to a warning: this is a legal boundary, and
    the only fix is a configuration change by an operator.
    """

    code = "license_refused"


def is_retryable(exc: BaseException) -> bool:
    """Should the runtime put this job back on the queue?

    Unknown exceptions are optimistically retryable: the common cause of a surprise is
    infrastructure, and the retry budget bounds the damage.
    """
    if isinstance(exc, JobCancelledError):
        return False
    if isinstance(exc, WorkerError):
        return exc.retryable
    return not isinstance(exc, MemoryError | KeyboardInterrupt | SystemExit)


#: Copy used when an unexpected exception reaches the runtime. §15: "error copy never
#: blames the user"; §13: never leak internals.
GENERIC_FAILURE_MESSAGE = "Something went wrong on our side while running this job."
GENERIC_FAILURE_ACTION = "Try again — if it keeps happening, contact support."


def user_facing(exc: BaseException) -> dict[str, Any]:
    """Turn any exception into the ``{code, message, action}`` the UI may see."""
    if isinstance(exc, WorkerError):
        return exc.as_problem()
    return {
        "code": "internal_error",
        "message": GENERIC_FAILURE_MESSAGE,
        "action": GENERIC_FAILURE_ACTION,
    }


__all__ = [
    "GENERIC_FAILURE_ACTION",
    "GENERIC_FAILURE_MESSAGE",
    "BlobError",
    "InvalidJobError",
    "JobCancelledError",
    "JobTimeoutError",
    "LicenseError",
    "PermanentError",
    "ProviderError",
    "RetryableError",
    "WorkerError",
    "is_retryable",
    "user_facing",
]
