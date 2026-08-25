"""The §10 LLM provider interface and its factory.

    Provider interface with `mock` (fixture-driven, used in tests/dev) and `anthropic`
    implementations. All calls use structured outputs (JSON schema), temperature <=0.3,
    max 2 retries on schema violation.

Selection is ``PROVIDER_LLM`` (``mock`` | ``anthropic``). **The mock is the default**,
and importing this module pulls in no SDK: the Anthropic implementation is imported
inside the branch that needs it, so a checkout with no API key and no ``anthropic``
package still runs the whole product.

The interface is ``async`` (unlike the render provider, which is sync) because these
calls are network-bound, not CPU-bound — they belong on the event loop, not in a
thread.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from services.common.config import WorkerSettings, get_worker_settings
from services.common.jsonschema_lite import SchemaValidator, ValidationFailure, format_errors
from services.common.logging import get_logger
from services.llm.types import LlmResult, LlmTask, SchemaViolationError

log = get_logger("llm.provider")

PROVIDER_NAMES: tuple[str, ...] = ("mock", "anthropic")

#: §10: "max 2 retries on schema violation" — an upper bound the config cannot exceed.
MAX_SCHEMA_RETRIES = 2


@runtime_checkable
class LlmProvider(Protocol):
    """Turn a prompt + JSON Schema into a schema-valid object."""

    #: Stable identifier stored on the row that recorded this call.
    name: str
    #: Model identifier, or ``"fixtures"`` for the mock.
    model: str

    async def complete_json(self, task: LlmTask) -> LlmResult:
        """Return schema-valid data, or raise a :class:`~services.llm.types.LlmError`.

        Implementations MUST validate before returning. A caller is entitled to assume
        ``result.data`` satisfies ``task.schema`` — that assumption is what keeps
        untrusted model output from reaching the op pipeline unchecked (§13).
        """
        ...

    async def aclose(self) -> None:
        """Release any transport. Safe to call twice."""
        ...


class SchemaGate:
    """Compiled schema plus the accept/reject decision, shared by both providers."""

    def __init__(self, schema: Mapping[str, Any], name: str) -> None:
        self.name = name
        self.validator = SchemaValidator(schema)

    def check(self, data: Any) -> list[ValidationFailure]:
        return self.validator.validate(data)

    def require(self, data: Any, *, attempts: int) -> dict[str, Any]:
        failures = self.check(data)
        if failures:
            raise SchemaViolationError(
                "The assistant's answer did not come back in a usable shape.",
                failures=format_errors(failures),
                attempts=attempts,
            )
        if not isinstance(data, dict):
            raise SchemaViolationError(
                "The assistant's answer did not come back in a usable shape.",
                failures="top level must be a JSON object",
                attempts=attempts,
            )
        return data


_GATES: dict[str, SchemaGate] = {}


def gate_for(task: LlmTask) -> SchemaGate:
    """Cached :class:`SchemaGate` for a task's schema.

    Keyed by ``schema_name``: schemas are module-level constants, so one compile per
    name for the life of the process. Compiling a schema also *audits* it — an
    unsupported keyword raises here, at first use, rather than silently going
    unchecked.
    """
    gate = _GATES.get(task.schema_name)
    if gate is None:
        gate = SchemaGate(task.schema, task.schema_name)
        _GATES[task.schema_name] = gate
    return gate


def reset_schema_gate_cache() -> None:
    """Test helper for when a schema is monkeypatched."""
    _GATES.clear()


def get_llm_provider(settings: WorkerSettings | None = None) -> LlmProvider:
    """Build the provider named by ``PROVIDER_LLM``.

    Raises ``ValueError`` on an unknown name: a typo in the environment must stop the
    process at boot rather than quietly serve fixtures to a paying customer.
    """
    cfg = settings or get_worker_settings()
    provider_name = cfg.provider_llm

    if provider_name == "mock":
        from services.llm.mock import MockLlmProvider

        log.info("llm.provider.selected", provider="mock")
        return MockLlmProvider(cfg)

    if provider_name == "anthropic":
        # Imported here so the mock path needs no SDK installed.
        from services.llm.anthropic_provider import AnthropicLlmProvider

        if not cfg.anthropic_api_key:
            raise ValueError(
                "PROVIDER_LLM=anthropic but ANTHROPIC_API_KEY is empty. Set the key, or "
                "use PROVIDER_LLM=mock (the default) to run without one."
            )
        log.info("llm.provider.selected", provider="anthropic", model=cfg.anthropic_model)
        return AnthropicLlmProvider(cfg)

    raise ValueError(
        "Unknown PROVIDER_LLM=%r. Expected one of: %s."
        % (provider_name, ", ".join(PROVIDER_NAMES))
    )


__all__ = [
    "MAX_SCHEMA_RETRIES",
    "PROVIDER_NAMES",
    "LlmProvider",
    "SchemaGate",
    "gate_for",
    "get_llm_provider",
    "reset_schema_gate_cache",
]
