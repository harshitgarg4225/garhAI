"""Shared types for the §10 LLM layer.

Every call in this package is a **structured-output** call: a JSON Schema goes in, a
schema-valid JSON object comes out or the call fails. There is no "parse the prose"
path, because §13's prompt-injection containment depends on it — *LLM output only ever
becomes validated ops, never executed text*.

The provider interface is deliberately narrower than the Messages API. A caller cannot
ask for free text and cannot pass tools. If a future feature needs one of those, add a
second method rather than widening this one — the narrowness is what makes "mock and
real behave identically" checkable.

Streaming took exactly that route: :mod:`services.llm.streaming` adds a second
protocol (``stream_json``) and leaves :class:`~services.llm.provider.LlmProvider`
untouched, so a provider that cannot stream needs no changes and the streamed prose
stays structurally separate from the object the schema gate runs on.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

#: Named units of LLM work. Fixture files and per-task tuning are keyed by these.
TaskName = Literal["brief.parse", "copilot.ops", "rationale.write", "compliance.explain"]

TASK_NAMES: tuple[TaskName, ...] = (
    "brief.parse",
    "copilot.ops",
    "rationale.write",
    "compliance.explain",
)

#: Reasoning depth hint. Mapped to the provider's own control (Anthropic: effort).
Effort = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class LlmUsage:
    """Token accounting, forwarded to ``credit_events`` by the caller."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_json(self) -> dict[str, int]:
        return {
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "cacheReadTokens": self.cache_read_tokens,
            "cacheWriteTokens": self.cache_write_tokens,
        }

    def plus(self, other: LlmUsage) -> LlmUsage:
        return LlmUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass(frozen=True)
class LlmTask:
    """One structured-output request.

    ``fixture_key`` is what the mock provider looks up. It is derived from the caller's
    stable inputs (the command text, the brief text) — never from anything random — so
    the mock is deterministic across runs and machines.
    """

    name: TaskName
    system: str
    user: str
    #: JSON Schema the response must satisfy. Enforced on BOTH provider paths.
    schema: Mapping[str, Any]
    schema_name: str
    fixture_key: str = ""
    max_output_tokens: int = 4_096
    effort: Effort = "low"
    #: Extra user-turn content appended on a self-correction retry (§10's "feed the
    #: reasons back ONCE"). Kept separate so the cached prefix is not disturbed.
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LlmResult:
    """A schema-valid response plus the provenance the UI is entitled to show."""

    data: dict[str, Any]
    provider: str
    model: str
    usage: LlmUsage = field(default_factory=LlmUsage)
    #: Total provider calls made, including schema-violation repairs (§10: max 2).
    attempts: int = 1
    #: True when the first response violated the schema and had to be repaired.
    repaired: bool = False
    #: True when the answer came from a fixture rather than a model.
    is_mock: bool = False
    duration_ms: int = 0

    def summary(self) -> dict[str, Any]:
        """Log-safe description. Never includes prompt or response text."""
        return {
            "provider": self.provider,
            "model": self.model,
            "attempts": self.attempts,
            "repaired": self.repaired,
            "isMock": self.is_mock,
            "durationMs": self.duration_ms,
            **self.usage.to_json(),
        }


class LlmError(Exception):
    """Base for LLM-layer failures that carry user-facing copy (golden rule 9)."""

    code = "llm_error"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        action: str | None = None,
        detail: str | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.action = action
        self.detail = detail
        if code is not None:
            self.code = code

    def as_problem(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.action:
            body["action"] = self.action
        return body


class SchemaViolationError(LlmError):
    """The model could not produce a schema-valid answer within the retry budget."""

    code = "llm_schema_violation"

    def __init__(self, message: str, *, failures: str = "", attempts: int = 0) -> None:
        super().__init__(
            message,
            action="Try rephrasing your request.",
            detail="schema violations after %d attempt(s):\n%s" % (attempts, failures),
        )
        self.failures = failures
        self.attempts = attempts


class LlmUnavailableError(LlmError):
    """The provider is unreachable or refused the request. Retryable."""

    code = "llm_unavailable"
    retryable = True

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message, action="Try again in a moment.", detail=detail)


class LlmRefusalError(LlmError):
    """The model's safety classifiers declined the request.

    Distinct from a failure: nothing is wrong with the system, and retrying the same
    text will not help. Surfaced honestly rather than dressed up as an outage.
    """

    code = "llm_refused"

    def __init__(self, message: str, *, category: str | None = None) -> None:
        super().__init__(
            message,
            action="Rephrase the request and try again.",
            detail="refusal category=%s" % (category or "unknown"),
        )
        self.category = category


__all__ = [
    "TASK_NAMES",
    "Effort",
    "LlmError",
    "LlmRefusalError",
    "LlmResult",
    "LlmTask",
    "LlmUnavailableError",
    "LlmUsage",
    "SchemaViolationError",
    "TaskName",
]
